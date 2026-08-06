from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from gaya_pipeline.completion_anchor import (
    CompletionAnchorError,
    validate_anchor_selection,
)
from gaya_pipeline.completion_plan import (
    IRODORI_MODEL,
    MODEL_REVISIONS,
    QWEN_MODEL,
    CompletionPlan,
    require_production_completion_plan,
)
from gaya_pipeline.curation import (
    CurationError,
    build_candidate_set,
    canonical_candidate_set_bytes,
    validate_snapshot_bundle,
)
from gaya_pipeline.qc_report import QCAuthority, QCReportError, validate_qc_report
from gaya_pipeline.take_identity import canonical_json, derive_seed
from gaya_pipeline.take_ledger import TakeLedgerError, read_ledger
from gaya_pipeline.take_manifest_v4 import TakeManifestError, validate_manifest_v4


class CompletionListeningError(RuntimeError):
    pass


PHASE_B_PROTOCOL = "phase-b-generation-v2"
PHASE_B_SEED_MIN = 0
PHASE_B_SEED_MAX = 2**32 - 1
SOURCE_MAP_PROTOCOL = "phase-b-source-map-v1"
PRIMARY_MODELS = frozenset(
    MODEL_REVISIONS,
)
ANCHOR_BOUND_MODELS = frozenset({QWEN_MODEL, IRODORI_MODEL})
EXPECTED_SOURCE_GROUP_COUNT = 597

AnchorLoader = Callable[
    [Path, Any],
    "tuple[str, Mapping[tuple[str, str, str], str]]",
]


@dataclass(frozen=True)
class CompletionListeningSummary:
    output_dir: Path
    candidate_set_sha256: str
    source_map_sha256: str
    model_count: int
    group_count: int
    candidate_count: int


@dataclass(frozen=True)
class CompletionScenarioAuthority:
    scenario_sha256: str
    lines: tuple[dict[str, Any], ...]
    contexts: Mapping[tuple[str, str], Mapping[str, Any]]
    line_characters: Mapping[tuple[str, str], str]


@dataclass(frozen=True)
class CompletionSourceRun:
    run_id: str
    model: str
    kind: str
    supersedes_run_id: str | None
    root: Path
    ledger_sha256: str
    qc_report_sha256: str
    manifest_sha256: str
    candidate_set_sha256: str
    manifest: dict[str, Any]
    groups: frozenset[tuple[str, str, str, str]]
    role_epochs: Mapping[tuple[str, str, str, str], str]
    seed_base: int | None
    attempt_seeds: Mapping[tuple[str, str, str, str], frozenset[int]]


@dataclass(frozen=True)
class CompletionSourceResolution:
    runs: tuple[CompletionSourceRun, ...]
    group_sources: Mapping[tuple[str, str, str, str], CompletionSourceRun]
    anchor_selection_sha256: str
    expected_role_epochs: Mapping[tuple[str, str, str, str], str]


def completion_group_sha256(
    *,
    identity: tuple[str, str, str, str],
    line: Mapping[str, Any],
    context: Mapping[str, Any],
    role_epoch_sha256: str,
    source_run_id: str,
    minimum_eligible_candidates: int,
    candidates: Sequence[Mapping[str, Any]],
) -> str:
    document = {
        "model": identity[0],
        "scenario": identity[1],
        "line": identity[2],
        "variant": identity[3],
        "character": context["character"],
        "role_identity_sha256": context["role_identity_sha256"],
        "reference_voice": context["reference_voice"],
        "role": context["role"],
        "scene_setting": context["scene_setting"],
        "reading": context["reading"],
        "situation": context["situation"],
        "emotion": context["emotion"],
        "intensity": context["intensity"],
        "scenario_title": line["scenario_title"],
        "text": line["text"],
        "delivery": line["delivery"],
        "role_epoch_sha256": role_epoch_sha256,
        "source_run_id": source_run_id,
        "minimum_eligible_candidates": minimum_eligible_candidates,
        "candidates": [
            {
                "take_id": candidate["take_id"],
                "path": candidate["path"],
                "audio_sha256": candidate["sha256"],
                "gate": candidate["gate"],
            }
            for candidate in sorted(
                candidates,
                key=lambda candidate: str(candidate["take_id"]),
            )
        ],
    }
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def phase_b_generation_binding(
    *,
    plan: CompletionPlan,
    model: str,
    scenarios_dir: Path,
    voices_dir: Path,
    anchor_selection_path: Path,
) -> tuple[str, dict[tuple[str, str], str]]:
    """Resolve the exact anchor digest and per-line role epochs for one run."""

    require_production_completion_plan(plan)
    return generation_binding(
        plan=plan,
        model=model,
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        anchor_selection_path=anchor_selection_path,
    )


def generation_binding(
    *,
    plan: Any,
    model: str,
    scenarios_dir: Path,
    voices_dir: Path,
    anchor_selection_path: Path,
    anchor_loader: AnchorLoader | None = None,
) -> tuple[str, dict[tuple[str, str], str]]:
    """Bind one generation run to its anchor digest without production gating."""

    load_anchor = _load_anchor_selection if anchor_loader is None else anchor_loader
    anchor_sha, anchor_epochs = load_anchor(
        anchor_selection_path,
        plan,
    )
    scenario_authority = _load_completion_scenario_authority(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        plan=plan,
    )
    expected = expected_phase_b_role_epochs(
        plan=plan,
        line_characters=scenario_authority.line_characters,
        anchor_selection_sha256=anchor_sha,
        selected_anchor_epochs=anchor_epochs,
    )
    model_targets = {
        target.identity for target in plan.targets_for_model(model)
    }
    if not model_targets:
        raise CompletionListeningError(f"Phase B生成対象外modelです: {model}")
    return anchor_sha, {
        (identity[1], identity[2]): expected[identity]
        for identity in sorted(model_targets)
    }


def build_completion_listening_bundle(
    *,
    plan: CompletionPlan,
    plan_path: Path,
    primary_run_ids: Sequence[str],
    topup_run_ids: Sequence[str],
    anchor_selection_path: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    output_dir: Path,
) -> CompletionListeningSummary:
    require_production_completion_plan(plan)
    plan_bytes = _read_canonical_authority_bytes(
        plan_path,
        label="completion plan",
        expected_sha256=plan.plan_id,
    )
    if hashlib.sha256(plan_bytes).hexdigest() != plan.raw_sha256:
        raise CompletionListeningError(
            "completion plan bytes SHAがloaded planと一致しません。",
        )
    if output_dir.exists():
        raise CompletionListeningError(
            f"listening output は既存 path を拒否します: {output_dir}",
        )
    parent = output_dir.resolve().parent
    if not parent.is_dir():
        raise CompletionListeningError(
            f"listening output の親 directory が存在しません: {parent}",
        )
    scenario_authority = _load_completion_scenario_authority(
        scenarios_dir=scenarios_dir.resolve(),
        voices_dir=voices_dir.resolve(),
        plan=plan,
    )
    resolution = resolve_completion_sources(
        plan=plan,
        primary_run_ids=primary_run_ids,
        topup_run_ids=topup_run_ids,
        anchor_selection_path=anchor_selection_path,
        artifacts_dir=artifacts_dir,
        scenario_authority=scenario_authority,
    )
    anchor_selection_bytes = _read_canonical_authority_bytes(
        anchor_selection_path,
        label="anchor selection",
        expected_sha256=resolution.anchor_selection_sha256,
        marker_path=anchor_selection_path.with_suffix(".sha256"),
    )

    candidates: list[dict[str, Any]] = []
    source_audio: dict[str, Path] = {}
    models: dict[str, dict[str, Any]] = {}
    generated_at: list[str] = []
    for identity, run in sorted(resolution.group_sources.items()):
        group_candidates = [
            dict(candidate)
            for candidate in run.manifest["candidates"]
            if _group_key(candidate) == identity
        ]
        minimum = plan.policy_for_model(identity[0]).minimum_eligible_candidates
        if len(group_candidates) < minimum:
            raise CompletionListeningError(
                "最終有効 source の mechanical-pass candidate が"
                f"{minimum}件未満です: {identity}: {run.run_id}",
            )
        models[run.model] = dict(run.manifest["models"][0])
        generated_at.append(str(run.manifest["generated_at"]))
        for candidate in group_candidates:
            local_path = _local_audio_path(run.root, candidate)
            _verify_audio_sha(local_path, str(candidate["sha256"]))
            path = str(candidate["path"])
            if path in source_audio:
                raise CompletionListeningError(
                    f"最終候補 path が重複しています: {path}",
                )
            source_audio[path] = local_path
            candidates.append(candidate)

    candidates.sort(key=lambda item: (_group_key(item), int(item["take_index"])))
    candidate_set = build_candidate_set(
        scenario_sha256=scenario_authority.scenario_sha256,
        lines=list(scenario_authority.lines),
        models=[models[model] for model in sorted(models)],
        candidates=candidates,
        failures=[],
    )
    candidate_set_bytes = canonical_candidate_set_bytes(candidate_set)
    candidate_set_sha256 = hashlib.sha256(candidate_set_bytes).hexdigest()
    try:
        manifest = validate_manifest_v4(
            {
                "format_version": 4,
                "generated_at": max(generated_at),
                "candidate_set_sha256": candidate_set_sha256,
                "models": candidate_set["models"],
                "candidates": candidates,
                "curations": [],
                "failures": [],
            },
        )
    except (TakeManifestError, TakeLedgerError) as error:
        raise CompletionListeningError(
            f"combined listening manifest が不正です: {error}",
        ) from error

    source_map = {
        "format_version": 1,
        "protocol": SOURCE_MAP_PROTOCOL,
        "plan_sha256": plan.plan_id,
        "anchor_selection_sha256": resolution.anchor_selection_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "groups": [
            {
                "model": identity[0],
                "scenario": identity[1],
                "line": identity[2],
                "variant": identity[3],
                **scenario_authority.contexts[(identity[1], identity[2])],
                "role_epoch_sha256": resolution.expected_role_epochs[identity],
                "source_run_id": run.run_id,
                "minimum_eligible_candidates": plan.policy_for_model(
                    identity[0],
                ).minimum_eligible_candidates,
            }
            for identity, run in sorted(resolution.group_sources.items())
        ],
    }
    source_map_bytes = canonical_json(source_map).encode("utf-8")
    source_map_sha256 = hashlib.sha256(source_map_bytes).hexdigest()

    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=parent))
    try:
        _write_new_file(
            temporary / "manifest-v4.json",
            canonical_json(manifest).encode("utf-8"),
        )
        _write_new_file(temporary / "candidate-set.json", candidate_set_bytes)
        _write_new_file(
            temporary / "candidate-set.sha256",
            candidate_set_sha256.encode("ascii"),
        )
        for name, payload in _listening_authority_files(
            plan_bytes=plan_bytes,
            plan_sha256=plan.plan_id,
            anchor_selection_bytes=anchor_selection_bytes,
            anchor_selection_sha256=resolution.anchor_selection_sha256,
        ).items():
            _write_new_file(temporary / name, payload)
        _write_new_file(
            temporary / "phase-b-source-map-v1.json",
            source_map_bytes,
        )
        _write_new_file(
            temporary / "phase-b-source-map-v1.sha256",
            source_map_sha256.encode("ascii"),
        )
        for candidate in candidates:
            destination = temporary / _listening_audio_relative(candidate)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_audio[str(candidate["path"])], destination)
            _verify_audio_sha(destination, str(candidate["sha256"]))
        temporary.replace(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return CompletionListeningSummary(
        output_dir=output_dir,
        candidate_set_sha256=candidate_set_sha256,
        source_map_sha256=source_map_sha256,
        model_count=len(models),
        group_count=len(resolution.group_sources),
        candidate_count=len(candidates),
    )


def resolve_completion_sources(
    *,
    plan: CompletionPlan,
    primary_run_ids: Sequence[str],
    topup_run_ids: Sequence[str],
    anchor_selection_path: Path,
    artifacts_dir: Path,
    scenario_authority: CompletionScenarioAuthority,
    primary_models: frozenset[str] | None = None,
    anchor_loader: AnchorLoader | None = None,
    anchor_bound_models: frozenset[str] | None = None,
    expected_group_count: int = EXPECTED_SOURCE_GROUP_COUNT,
) -> CompletionSourceResolution:
    models = PRIMARY_MODELS if primary_models is None else primary_models
    bound_models = (
        ANCHOR_BOUND_MODELS if anchor_bound_models is None else anchor_bound_models
    )
    load_anchor = _load_anchor_selection if anchor_loader is None else anchor_loader
    all_ids = [*primary_run_ids, *topup_run_ids]
    if len(all_ids) != len(set(all_ids)):
        raise CompletionListeningError("Phase B source run-id が重複しています。")
    if len(primary_run_ids) != len(models):
        raise CompletionListeningError(
            f"Phase B primary run は{len(models)}件必要です。",
        )
    takes_root = _require_directory(artifacts_dir / "takes", "takes root")
    anchor_sha, anchor_epochs = load_anchor(
        anchor_selection_path,
        plan,
    )
    expected_epochs = expected_phase_b_role_epochs(
        plan=plan,
        line_characters=scenario_authority.line_characters,
        anchor_selection_sha256=anchor_sha,
        selected_anchor_epochs=anchor_epochs,
    )
    expected_targets = {target.identity for target in plan.targets}

    primary_runs = [
        _load_phase_b_run(
            takes_root=takes_root,
            run_id=run_id,
            plan=plan,
            expected_epochs=expected_epochs,
            expected_anchor_sha256=anchor_sha,
            required_kind="primary",
            primary_models=models,
            anchor_bound_models=bound_models,
        )
        for run_id in primary_run_ids
    ]
    primary_by_model = {run.model: run for run in primary_runs}
    if set(primary_by_model) != models or len(primary_by_model) != len(
        primary_runs
    ):
        raise CompletionListeningError(
            f"primary run はplanの{len(models)} modelを各1件必要です。",
        )
    for model, run in primary_by_model.items():
        expected_model_groups = {
            target.identity
            for target in plan.targets_for_model(model)
        }
        if run.groups != expected_model_groups:
            raise CompletionListeningError(
                f"primary run coverage がplanと一致しません: {run.run_id}",
            )

    runs_by_id = {run.run_id: run for run in primary_runs}
    group_sources: dict[
        tuple[str, str, str, str],
        CompletionSourceRun,
    ] = {
        identity: run for run in primary_runs for identity in run.groups
    }
    topup_runs: list[CompletionSourceRun] = []
    for run_id in topup_run_ids:
        run = _load_phase_b_run(
            takes_root=takes_root,
            run_id=run_id,
            plan=plan,
            expected_epochs=expected_epochs,
            expected_anchor_sha256=anchor_sha,
            required_kind="topup",
            primary_models=models,
            anchor_bound_models=bound_models,
        )
        predecessor = runs_by_id.get(str(run.supersedes_run_id))
        if predecessor is None or predecessor.model != run.model:
            raise CompletionListeningError(
                f"topup supersedes_run_id が明示済み同一model runではありません: {run_id}",
            )
        if not run.groups or not run.groups.issubset(predecessor.groups):
            raise CompletionListeningError(
                f"topup groups がsuperseded runのexact subsetではありません: {run_id}",
            )
        if plan.policy_for_model(run.model).seed_policy == "none":
            raise CompletionListeningError(
                f"seedを持たないmodelはtopupできません: {run.model}",
            )
        if run.seed_base == predecessor.seed_base:
            raise CompletionListeningError(
                f"topup seed_baseはsuperseded runと異なる値が必要です: {run_id}",
            )
        overlapping_seeds = {
            identity: sorted(
                run.attempt_seeds[identity]
                & predecessor.attempt_seeds[identity],
            )
            for identity in run.groups
            if run.attempt_seeds[identity]
            & predecessor.attempt_seeds[identity]
        }
        if overlapping_seeds:
            raise CompletionListeningError(
                "topup derived seedがsuperseded runと重複しています: "
                f"{run_id}: {overlapping_seeds}",
            )
        stale = sorted(
            identity
            for identity in run.groups
            if group_sources.get(identity) is not predecessor
        )
        if stale:
            raise CompletionListeningError(
                f"topup supersedes chain が現在のsourceと一致しません: {run_id}: {stale}",
            )
        for identity in run.groups:
            group_sources[identity] = run
        runs_by_id[run.run_id] = run
        topup_runs.append(run)

    if (
        set(group_sources) != expected_targets
        or len(group_sources) != expected_group_count
    ):
        missing = sorted(expected_targets - set(group_sources))
        extra = sorted(set(group_sources) - expected_targets)
        raise CompletionListeningError(
            f"最終source mapはplan exact {expected_group_count} groupが必要です: "
            f"missing={missing}, extra={extra}",
        )
    return CompletionSourceResolution(
        runs=tuple([*primary_runs, *topup_runs]),
        group_sources=group_sources,
        anchor_selection_sha256=anchor_sha,
        expected_role_epochs=expected_epochs,
    )


def expected_phase_b_role_epochs(
    *,
    plan: CompletionPlan,
    line_characters: Mapping[tuple[str, str], str],
    anchor_selection_sha256: str,
    selected_anchor_epochs: Mapping[tuple[str, str, str], str],
) -> dict[tuple[str, str, str, str], str]:
    result: dict[tuple[str, str, str, str], str] = {}
    for target in plan.targets:
        identity = target.identity
        character = line_characters[(target.scenario, target.line)]
        role = plan.role(target.scenario, character)
        anchor_key = (target.model, target.scenario, character)
        if anchor_key in selected_anchor_epochs:
            epoch = selected_anchor_epochs[anchor_key]
        else:
            epoch_document = {
                "protocol": "phase-b-role-epoch-v1",
                "plan_sha256": plan.plan_id,
                "model": target.model,
                "model_revision": plan.models[target.model],
                "scenario": target.scenario,
                "character": character,
                "role_identity_sha256": role.role_identity_sha256,
                "reference_voice": role.reference_voice,
                "anchor_selection_sha256": (
                    anchor_selection_sha256
                    if target.model in {QWEN_MODEL, IRODORI_MODEL}
                    else None
                ),
            }
            epoch = hashlib.sha256(
                canonical_json(epoch_document).encode("utf-8"),
            ).hexdigest()
        result[identity] = epoch
    return result


def _load_phase_b_run(
    *,
    takes_root: Path,
    run_id: str,
    plan: CompletionPlan,
    expected_epochs: Mapping[tuple[str, str, str, str], str],
    expected_anchor_sha256: str,
    required_kind: str,
    primary_models: frozenset[str] | None = None,
    anchor_bound_models: frozenset[str] | None = None,
) -> CompletionSourceRun:
    models = PRIMARY_MODELS if primary_models is None else primary_models
    bound_models = (
        ANCHOR_BOUND_MODELS if anchor_bound_models is None else anchor_bound_models
    )
    root = _resolve_direct_child(takes_root, run_id, f"source run {run_id}")
    try:
        ledger = read_ledger(root / "ledger.json")
        bundle = validate_snapshot_bundle(
            snapshot_path=root / "manifest-v4.json",
            candidate_set_path=root / "candidate-set.json",
            marker_path=root / "candidate-set.sha256",
        )
        qc_raw = _read_json(root / "qc-report.json", "QC report")
        qc_authority = validate_qc_report(
            qc_raw,
            ledger_path=root / "ledger.json",
            ledger=ledger,
        )
    except (
        CurationError,
        OSError,
        QCReportError,
        TakeLedgerError,
        UnicodeError,
        json.JSONDecodeError,
    ) as error:
        raise CompletionListeningError(
            f"Phase B run bundle が不正です: {run_id}: {error}",
        ) from error
    if ledger["run_id"] != run_id:
        raise CompletionListeningError(f"ledger.run_id がpathと不一致です: {run_id}")
    phase_b = ledger["source"].get("phase_b")
    if not isinstance(phase_b, Mapping):
        raise CompletionListeningError(f"ledger.source.phase_b がありません: {run_id}")
    if qc_raw["source"].get("phase_b") != phase_b:
        raise CompletionListeningError(
            f"QC source.phase_b がledgerと一致しません: {run_id}",
        )
    expected_phase_fields = {
        "protocol",
        "plan_sha256",
        "run_kind",
        "supersedes_run_id",
        "anchor_selection_sha256",
        "anchor_plan_sha256",
        "target_groups",
    }
    if set(phase_b) != expected_phase_fields:
        raise CompletionListeningError(
            f"ledger.source.phase_b exact contract が不正です: {run_id}",
        )
    if (
        phase_b["protocol"] != PHASE_B_PROTOCOL
        or phase_b["plan_sha256"] != plan.plan_id
        or phase_b["run_kind"] != required_kind
    ):
        raise CompletionListeningError(
            f"Phase B protocol/plan/run kind が不一致です: {run_id}",
        )
    if required_kind == "primary" and phase_b["supersedes_run_id"] is not None:
        raise CompletionListeningError(
            f"primary runにsupersedes_run_idは指定できません: {run_id}",
        )
    if required_kind == "topup" and not isinstance(
        phase_b["supersedes_run_id"],
        str,
    ):
        raise CompletionListeningError(
            f"topup runはsupersedes_run_idが必要です: {run_id}",
        )
    run_models = bundle.manifest["models"]
    if len(run_models) != 1:
        raise CompletionListeningError(f"Phase B runは単一modelが必要です: {run_id}")
    model = str(run_models[0]["id"])
    if model not in models or run_models[0]["version"] != plan.models[model]:
        raise CompletionListeningError(
            f"Phase B model/revision がplanと一致しません: {run_id}",
        )
    policy = plan.policy_for_model(model)
    if ledger["source"]["takes"] != policy.takes:
        raise CompletionListeningError(
            f"Phase B source.takesがmodel policyと一致しません: {run_id}",
        )
    if (
        required_kind == "primary"
        and ledger["source"]["seed_base"] != policy.primary_seed_base
    ):
        raise CompletionListeningError(
            f"primary seed_baseがmodel policyと一致しません: {run_id}",
        )
    if required_kind == "topup" and policy.seed_policy == "none":
        raise CompletionListeningError(
            f"seedを持たないmodelはtopupできません: {model}",
        )
    anchor_sha = phase_b["anchor_selection_sha256"]
    anchor_plan_sha = phase_b["anchor_plan_sha256"]
    expected_run_anchor = (
        expected_anchor_sha256 if model in bound_models else None
    )
    expected_anchor_plan = (
        plan.anchor_source_plan_sha256
        if model in bound_models
        else None
    )
    if (
        anchor_sha != expected_run_anchor
        or anchor_plan_sha != expected_anchor_plan
    ):
        raise CompletionListeningError(
            f"Phase B anchor authority が不一致です: {run_id}",
        )
    target_groups = phase_b["target_groups"]
    if not isinstance(target_groups, list) or not target_groups:
        raise CompletionListeningError(f"target_groupsが不正です: {run_id}")
    role_epochs: dict[tuple[str, str, str, str], str] = {}
    normalized_groups: list[dict[str, str]] = []
    for index, item in enumerate(target_groups):
        if not isinstance(item, Mapping) or set(item) != {
            "model",
            "scenario",
            "line",
            "variant",
            "role_epoch_sha256",
        }:
            raise CompletionListeningError(
                f"target_groups[{index}] exact contractが不正です: {run_id}",
            )
        identity = _group_key(item)
        epoch = str(item["role_epoch_sha256"])
        if identity in role_epochs or expected_epochs.get(identity) != epoch:
            raise CompletionListeningError(
                f"target group role epoch がplan/anchorと不一致です: {run_id}: {identity}",
            )
        role_epochs[identity] = epoch
        normalized_groups.append(dict(item))
    if normalized_groups != sorted(
        normalized_groups,
        key=lambda item: _group_key(item),
    ):
        raise CompletionListeningError(f"target_groupsはcanonical順が必要です: {run_id}")
    ledger_groups = {_group_key(item) for item in ledger["source"]["groups"]}
    artifact_groups = {
        _group_key(item)
        for item in [
            *bundle.manifest["candidates"],
            *bundle.manifest["failures"],
        ]
    }
    if set(role_epochs) != ledger_groups or artifact_groups != ledger_groups:
        raise CompletionListeningError(
            f"run ledger/phase_b/manifest group集合が一致しません: {run_id}",
        )
    _validate_manifest_candidate_authority(
        run_id=run_id,
        ledger=ledger,
        manifest=bundle.manifest,
        qc_authority=qc_authority,
        phase_b=phase_b,
        seed_policy=policy.seed_policy,
    )
    attempt_seeds = _attempt_seeds_by_group(ledger)
    return CompletionSourceRun(
        run_id=run_id,
        model=model,
        kind=required_kind,
        supersedes_run_id=phase_b["supersedes_run_id"],
        root=root,
        ledger_sha256=_file_sha256(root / "ledger.json"),
        qc_report_sha256=_file_sha256(root / "qc-report.json"),
        manifest_sha256=_file_sha256(root / "manifest-v4.json"),
        candidate_set_sha256=bundle.candidate_set_sha256,
        manifest=bundle.manifest,
        groups=frozenset(role_epochs),
        role_epochs=role_epochs,
        seed_base=ledger["source"]["seed_base"],
        attempt_seeds=attempt_seeds,
    )


def _validate_manifest_candidate_authority(
    *,
    run_id: str,
    ledger: Mapping[str, Any],
    manifest: Mapping[str, Any],
    qc_authority: QCAuthority,
    phase_b: Mapping[str, Any],
    seed_policy: str,
) -> None:
    ledger_by_slot = {
        _attempt_slot(attempt): attempt for attempt in ledger["attempts"]
    }
    candidates_by_slot = {
        _attempt_slot(candidate): candidate
        for candidate in manifest["candidates"]
    }
    eligible_slots = {
        slot
        for slot, attempt in ledger_by_slot.items()
        if attempt["status"] == "eligible"
    }
    if set(candidates_by_slot) != eligible_slots:
        missing = sorted(eligible_slots - set(candidates_by_slot))
        extra = sorted(set(candidates_by_slot) - eligible_slots)
        raise CompletionListeningError(
            "manifest candidatesがledger eligible terminal attemptと"
            f"exact一致しません: {run_id}: missing={missing}, extra={extra}",
        )
    if seed_policy == "none":
        if ledger["source"]["seed_base"] is not None or any(
            attempt["generation"]["seed"] is not None
            for attempt in ledger_by_slot.values()
        ):
            raise CompletionListeningError(
                f"seedなしmodelのseed contractが不正です: {run_id}",
            )
    elif seed_policy == "derived-sha256-v1":
        for slot, attempt in ledger_by_slot.items():
            expected_seed = derive_seed(
                policy_version=seed_policy,
                seed_base=ledger["source"]["seed_base"],
                model=slot[0],
                scenario=slot[1],
                line=slot[2],
                variant=slot[3],
                index=slot[4],
                seed_min=PHASE_B_SEED_MIN,
                seed_max=PHASE_B_SEED_MAX,
            )
            if attempt["generation"]["seed"] != expected_seed:
                raise CompletionListeningError(
                    "ledger attempt seedがsource seed_baseからのcanonical"
                    f" derive値と一致しません: {run_id}: {slot}",
                )
    else:
        raise CompletionListeningError(
            f"Phase B seed policyが不正です: {run_id}",
        )
    recipe_version = ledger["source"]["recipe_version"]
    for slot, candidate in candidates_by_slot.items():
        attempt = ledger_by_slot[slot]
        qc_attempt = qc_authority.attempts_by_slot.get(slot)
        if (
            qc_attempt is None
            or qc_attempt["status"] != "eligible"
            or qc_attempt["gates"] != attempt["gates"]
        ):
            raise CompletionListeningError(
                f"candidateのQC slot authorityが不正です: {run_id}: {slot}",
            )
        audio = attempt["audio"]
        generation = attempt["generation"]
        params = candidate["gen_params"]
        expected_public_path = (
            f"audio/takes/{slot[0]}/{slot[1]}/{slot[2]}/{slot[3]}/"
            f"take-{slot[4]:04d}-{audio['opus_sha256']}.opus"
        )
        if (
            candidate["take_id"] != attempt["take_id"]
            or candidate["generation_input_sha256"]
            != attempt["generation_input_sha256"]
            or candidate["sha256"] != audio["opus_sha256"]
            or candidate["path"] != expected_public_path
            or params["seed"] != generation["seed"]
            or params["sampling"] != generation["sampling"]
            or params["recipe_version"] != recipe_version
            or candidate["rtf"] != generation["rtf"]
            or candidate["gate"]
            != {
                "mechanical": attempt["gates"]["mechanical"],
                "content": attempt["gates"]["content"],
                "policy_version": qc_authority.gate_policy_version,
            }
        ):
            raise CompletionListeningError(
                "manifest candidateがledger audio/generation/gate authorityと"
                f"一致しません: {run_id}: {slot}",
            )
        mechanical = qc_attempt["mechanical"]
        generation_params = (
            mechanical.get("generation_params")
            if isinstance(mechanical, Mapping)
            else None
        )
        if not isinstance(generation_params, Mapping) or any(
            params[kind] != generation_params[kind]
            for kind in ("requested", "realized")
        ):
            raise CompletionListeningError(
                "manifest candidate generation provenanceがQC authorityと"
                f"一致しません: {run_id}: {slot}",
            )
        if (
            not isinstance(mechanical, Mapping)
            or canonical_json(candidate["duration_sec"])
            != canonical_json(mechanical.get("duration_sec"))
            or canonical_json(candidate["loudness"])
            != canonical_json(mechanical.get("loudness"))
        ):
            raise CompletionListeningError(
                "manifest candidate duration/loudnessがQC mechanical authorityと"
                f"exact一致しません: {run_id}: {slot}",
            )
        target_group = next(
            item
            for item in phase_b["target_groups"]
            if _group_key(item) == slot[:4]
        )
        provenance = {
            "protocol": phase_b["protocol"],
            "plan_sha256": phase_b["plan_sha256"],
            "run_kind": phase_b["run_kind"],
            "supersedes_run_id": phase_b["supersedes_run_id"],
            "anchor_selection_sha256": phase_b[
                "anchor_selection_sha256"
            ],
            "anchor_plan_sha256": phase_b["anchor_plan_sha256"],
            "target_group": target_group,
        }
        provenance_sha = hashlib.sha256(
            canonical_json(provenance).encode("utf-8"),
        ).hexdigest()
        if (
            attempt["phase_b_provenance_sha256"] != provenance_sha
            or params["requested"].get("phase_b_provenance") != provenance
            or params["realized"].get("phase_b_provenance") != provenance
        ):
            raise CompletionListeningError(
                "candidate Phase B provenanceがsource/ledgerと"
                f"一致しません: {run_id}: {slot}",
            )
def _attempt_slot(value: Mapping[str, Any]) -> tuple[str, str, str, str, int]:
    return (
        str(value["model"]),
        str(value["scenario"]),
        str(value["line"]),
        str(value["variant"]),
        int(value["take_index"]),
    )


def _attempt_seeds_by_group(
    ledger: Mapping[str, Any],
) -> dict[tuple[str, str, str, str], frozenset[int]]:
    result: dict[tuple[str, str, str, str], set[int]] = {
        _group_key(group): set() for group in ledger["source"]["groups"]
    }
    for attempt in ledger["attempts"]:
        seed = attempt["generation"]["seed"]
        if isinstance(seed, int) and not isinstance(seed, bool):
            result[_group_key(attempt)].add(seed)
    return {identity: frozenset(seeds) for identity, seeds in result.items()}


def _load_anchor_selection(
    path: Path,
    plan: CompletionPlan,
) -> tuple[str, dict[tuple[str, str, str], str]]:
    if not path.is_absolute():
        raise CompletionListeningError("anchor selectionは絶対pathが必要です。")
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
        selection = validate_anchor_selection(document)
    except (
        CompletionAnchorError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as error:
        raise CompletionListeningError(f"anchor selectionが不正です: {error}") from error
    if raw != canonical_json(selection).encode("utf-8"):
        raise CompletionListeningError("anchor selectionはcanonical bytesが必要です。")
    digest = hashlib.sha256(raw).hexdigest()
    marker = path.with_suffix(".sha256")
    try:
        marker_value = marker.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise CompletionListeningError(
            f"anchor selection SHA markerを読めません: {marker}",
        ) from error
    if (
        marker_value != digest
        or digest != plan.anchor_selection_sha256
        or selection["plan_sha256"] != plan.anchor_source_plan_sha256
        or selection["candidate_set_sha256"]
        != plan.anchor_candidate_set_sha256
    ):
        raise CompletionListeningError(
            "anchor selection marker/authority SHAがfrozen planと一致しません。",
        )
    if len(selection["groups"]) != 106:
        raise CompletionListeningError(
            "anchor selectionはexact 106 groupが必要です。",
        )
    expected_anchor_groups = {
        (model, role.scenario, role.character)
        for model in {QWEN_MODEL, IRODORI_MODEL}
        for role in plan.roles
        if role.reference_voice is None
    }
    actual_anchor_groups = {
        (group["model"], group["scenario"], group["character"])
        for group in selection["groups"]
    }
    if actual_anchor_groups != expected_anchor_groups:
        raise CompletionListeningError(
            "anchor selection group集合が58 roleのno-reference対象と一致しません。",
        )
    for group in selection["groups"]:
        role = plan.role(group["scenario"], group["character"])
        if (
            group["model_revision"] != plan.models[group["model"]]
            or group["role_identity_sha256"] != role.role_identity_sha256
        ):
            raise CompletionListeningError(
                "anchor selection model revision/role identityがplanと一致しません。",
            )
    epochs = {
        (group["model"], group["scenario"], group["character"]): group[
            "role_epoch_sha256"
        ]
        for group in selection["groups"]
    }
    return digest, epochs


def _load_completion_scenario_authority(
    *,
    scenarios_dir: Path,
    voices_dir: Path,
    plan: CompletionPlan,
) -> CompletionScenarioAuthority:
    scenarios_dir = scenarios_dir.resolve()
    voices_dir = voices_dir.resolve()
    # 条件バリアント列 (#201) は Phase B target が161行の部分集合になるが、
    # release は全161行を覆う必要があるため authority だけ広い集合を使う。
    # 凍結planはこの属性を持たないので従来どおり plan.targets が使われる。
    authority_targets = getattr(plan, "scenario_authority_targets", None)
    expected = {
        (target.scenario, target.line)
        for target in (plan.targets if authority_targets is None else authority_targets)
    }
    declared_sources = [
        {
            "scenario": source.scenario,
            "path": source.path,
            "sha256": source.sha256,
        }
        for source in plan.scenario_files
    ]
    declared_registry_sha = hashlib.sha256(
        canonical_json(declared_sources).encode("utf-8"),
    ).hexdigest()
    if declared_registry_sha != plan.scenario_registry_sha256:
        raise CompletionListeningError(
            "frozen plan scenario registry SHAがsource snapshotと一致しません。",
        )
    if {source["scenario"] for source in declared_sources} != {
        scenario for scenario, _line in expected
    }:
        raise CompletionListeningError(
            "frozen plan scenario source集合がPhase B targetと一致しません。",
        )
    if plan.voice_registry_path != "assets/voices/metadata.yaml":
        raise CompletionListeningError(
            "frozen plan voice registry pathが不正です。",
        )
    voice_path = voices_dir / "metadata.yaml"
    try:
        resolved_voice = voice_path.resolve(strict=True)
        voice_raw = resolved_voice.read_bytes()
    except OSError as error:
        raise CompletionListeningError(
            f"frozen voice registryを読めません: {voice_path}: {error}",
        ) from error
    if (
        resolved_voice.parent != voices_dir
        or hashlib.sha256(voice_raw).hexdigest() != plan.voice_registry_sha256
    ):
        raise CompletionListeningError(
            "voice registry bytesがfrozen plan source snapshotと一致しません。",
        )

    candidate_sources: list[dict[str, str]] = []
    lines: list[dict[str, Any]] = []
    contexts: dict[tuple[str, str], dict[str, Any]] = {}
    found: set[tuple[str, str]] = set()
    for source in declared_sources:
        scenario_id = source["scenario"]
        path = scenarios_dir / f"{scenario_id}.yaml"
        try:
            resolved = path.resolve(strict=True)
            raw = resolved.read_bytes()
            document = yaml.safe_load(raw.decode("utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise CompletionListeningError(
                f"frozen scenario authorityを読めません: {path}: {error}",
            ) from error
        if (
            resolved.parent != scenarios_dir
            or source["path"] != f"scenarios/{scenario_id}.yaml"
            or hashlib.sha256(raw).hexdigest() != source["sha256"]
            or not isinstance(document, Mapping)
            or document.get("id") != scenario_id
        ):
            raise CompletionListeningError(
                "scenario bytesがfrozen plan source snapshotと一致しません: "
                f"{scenario_id}",
            )
        candidate_sources.append(
            {"path": path.name, "sha256": source["sha256"]},
        )
        for line in document["lines"]:
            identity = (scenario_id, str(line["id"]))
            if identity not in expected:
                continue
            found.add(identity)
            lines.append(
                {
                    "scenario": scenario_id,
                    "line": str(line["id"]),
                    "scenario_title": str(document["title"]),
                    "text": str(line["text"]),
                    "delivery": str(line["delivery"]),
                },
            )
            character = str(line["character"])
            role = plan.role(scenario_id, character)
            reading = line.get("reading")
            if reading is not None:
                reading = str(reading)
            intensity = line["intensity"]
            if not isinstance(intensity, int) or isinstance(intensity, bool):
                raise CompletionListeningError(
                    f"source map intensityが不正です: {scenario_id}/{line['id']}",
                )
            contexts[identity] = {
                "character": character,
                "role_identity_sha256": role.role_identity_sha256,
                "reference_voice": role.reference_voice,
                "role": dict(role.role),
                "scene_setting": role.scene_setting,
                "reading": reading,
                "situation": str(line["situation"]),
                "emotion": str(line["emotion"]),
                "intensity": intensity,
            }
    if found != expected or set(contexts) != expected:
        raise CompletionListeningError(
            "scenario authorityはplan target lineのexact集合が必要です: "
            f"missing={sorted(expected - found)}, extra={sorted(found - expected)}",
        )
    return CompletionScenarioAuthority(
        scenario_sha256=hashlib.sha256(
            canonical_json(candidate_sources).encode("utf-8"),
        ).hexdigest(),
        lines=tuple(
            sorted(lines, key=lambda line: (line["scenario"], line["line"])),
        ),
        contexts=contexts,
        line_characters={
            identity: str(context["character"])
            for identity, context in contexts.items()
        },
    )


def _local_audio_path(run_root: Path, candidate: Mapping[str, Any]) -> Path:
    relative = Path(
        "audio",
        str(candidate["model"]),
        str(candidate["scenario"]),
        str(candidate["line"]),
        str(candidate["variant"]),
        f"take-{int(candidate['take_index']):04d}.opus",
    )
    path = run_root / relative
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise CompletionListeningError(f"Opus が存在しません: {path}") from error
    if not resolved.is_relative_to(run_root) or not resolved.is_file():
        raise CompletionListeningError(f"Opus は run root 内が必要です: {path}")
    return resolved


def _listening_audio_relative(candidate: Mapping[str, Any]) -> Path:
    return Path(
        "audio",
        str(candidate["model"]),
        str(candidate["scenario"]),
        str(candidate["line"]),
        str(candidate["variant"]),
        f"take-{int(candidate['take_index']):04d}.opus",
    )


def _verify_audio_sha(path: Path, expected: str) -> None:
    if _file_sha256(path) != expected:
        raise CompletionListeningError(f"Opus SHA-256 がmanifestと不一致です: {path}")


def _group_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value["model"]),
        str(value["scenario"]),
        str(value["line"]),
        str(value["variant"]),
    )


def _resolve_direct_child(root: Path, name: str, label: str) -> Path:
    candidate = root / name
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise CompletionListeningError(f"{label} が存在しません: {candidate}") from error
    if resolved.parent != root or not resolved.is_dir():
        raise CompletionListeningError(f"{label} はroot直下が必要です: {candidate}")
    return resolved


def _require_directory(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise CompletionListeningError(f"{label} が存在しません: {path}") from error
    if not resolved.is_dir():
        raise CompletionListeningError(f"{label} はdirectoryが必要です: {path}")
    return resolved


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise CompletionListeningError(f"file SHAを計算できません: {path}") from error


def _read_canonical_authority_bytes(
    path: Path,
    *,
    label: str,
    expected_sha256: str,
    marker_path: Path | None = None,
) -> bytes:
    if not path.is_absolute():
        raise CompletionListeningError(f"{label}は絶対pathが必要です。")
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CompletionListeningError(f"{label}を読めません: {path}: {error}") from error
    if raw != canonical_json(document).encode("utf-8"):
        raise CompletionListeningError(f"{label}はcanonical bytesが必要です。")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise CompletionListeningError(
            f"{label} bytes SHAがloaded authorityと一致しません。",
        )
    if marker_path is not None:
        try:
            marker = marker_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as error:
            raise CompletionListeningError(
                f"{label} SHA markerを読めません: {marker_path}",
            ) from error
        if marker != digest:
            raise CompletionListeningError(
                f"{label} SHA markerが入力bytesと一致しません。",
            )
    return raw


def _listening_authority_files(
    *,
    plan_bytes: bytes,
    plan_sha256: str,
    anchor_selection_bytes: bytes,
    anchor_selection_sha256: str,
) -> dict[str, bytes]:
    return {
        "completion-plan.json": plan_bytes,
        "completion-plan.sha256": plan_sha256.encode("ascii"),
        "role-anchor-selection-v1.json": anchor_selection_bytes,
        "role-anchor-selection-v1.sha256": anchor_selection_sha256.encode(
            "ascii",
        ),
    }


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CompletionListeningError(f"{label}を読めません: {path}: {error}") from error


def _write_new_file(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
    except FileExistsError as error:
        raise CompletionListeningError(f"file が既に存在します: {path}") from error
