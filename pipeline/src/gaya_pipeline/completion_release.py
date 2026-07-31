from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import yaml

from gaya_pipeline.completion_plan import (
    BASE_MANIFEST_GIT_BLOB,
    BASE_MANIFEST_SHA256,
    CompletionPlanError,
    SEED_BASE,
    TAKES,
    load_completion_plan,
)
from gaya_pipeline.completion_selection import (
    BASE_CANDIDATE_SET_SHA256,
    BASE_SELECTION_SHA256,
    FORMAT_VERSION as SELECTION_FORMAT_VERSION,
    PROTOCOL as SELECTION_PROTOCOL,
    canonical_completion_selection_bytes,
    reconstruct_base_selection,
    validate_completion_decision,
    validate_completion_selection,
)
from gaya_pipeline.curation import (
    GROUP_KEYS,
    CurationError,
    _validate_manifest_against_terminal_ledger,
    build_candidate_set,
    canonical_candidate_set_bytes,
    validate_snapshot_bundle,
)
from gaya_pipeline.qc_report import QCReportError, validate_qc_report
from gaya_pipeline.run_lock import RunLockError, exclusive_run_lock
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.take_ledger import (
    TERMINAL_STATUSES,
    TakeLedgerError,
    read_ledger,
)
from gaya_pipeline.take_manifest_v4 import TakeManifestError, validate_manifest_v4
from gaya_pipeline.validation import validate_scenario_ids


class CompletionReleaseError(RuntimeError):
    pass


_PUBLIC_CONTRACT_ERRORS = (
    CompletionPlanError,
    CurationError,
    QCReportError,
    TakeLedgerError,
    TakeManifestError,
    json.JSONDecodeError,
    OSError,
    UnicodeError,
    TypeError,
    ValueError,
    KeyError,
)


@dataclass(frozen=True)
class CompletionReleaseSummary:
    output_dir: Path
    manifest_sha256: str
    candidate_set_sha256: str
    selection_sha256: str
    candidate_count: int
    selected_count: int
    supplement_candidate_count: int


@dataclass(frozen=True)
class CompletionReleaseBundle:
    root: Path
    manifest: dict[str, Any]
    candidate_set: dict[str, Any]
    selection: dict[str, Any]
    provenance: dict[str, Any]


PLAN_PROTOCOL = "baseline-completion-plan-v1"
RELEASE_PROTOCOL = "baseline-completion-release-v1"
PLAN_FIELDS = {
    "format_version",
    "protocol",
    "base",
    "takes",
    "seed_base",
    "minimum_eligible_candidates",
    "targets",
}
PLAN_BASE_FIELDS = {
    "manifest_sha256",
    "git_blob",
    "candidate_set_sha256",
    "selection_sha256",
}
PLAN_TARGET_FIELDS = {*GROUP_KEYS, "prior_outcome"}
SKIPPED_OUTCOME_FIELDS = {"decision", "curation_sha256"}
FAILURE_OUTCOME_FIELDS = {"reason"}
PROVENANCE_FIELDS = {
    "format_version",
    "protocol",
    "manifest_sha256",
    "candidate_set_sha256",
    "selection_sha256",
    "base",
    "supplement_runs",
}
PROVENANCE_BASE_FIELDS = {
    "kind",
    "manifest_sha256",
    "git_blob",
    "candidate_set_sha256",
    "selection_sha256",
    "candidate_count",
}
PROVENANCE_RUN_FIELDS = {
    "model",
    "run_id",
    "ledger_sha256",
    "qc_report_sha256",
    "manifest_sha256",
    "candidate_set_sha256",
    "candidates",
}
PROVENANCE_CANDIDATE_FIELDS = {
    "take_id",
    "path",
    "audio_sha256",
    "run_relative_path",
    "size_bytes",
}
HEX = frozenset("0123456789abcdef")
PATH_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
EXPECTED_TARGET_COUNT = 45
EXPECTED_INHERITED_COUNT = 1243
EXPECTED_SELECTED_COUNT = 1288


@dataclass(frozen=True)
class _SupplementRun:
    run_id: str
    model: str
    root: Path
    manifest: dict[str, Any]
    candidate_set_sha256: str
    ledger_sha256: str
    qc_report_sha256: str
    manifest_sha256: str
    groups: frozenset[tuple[str, str, str, str]]
    provenance_candidates: tuple[dict[str, Any], ...]


def validate_completion_plan(document: Any) -> dict[str, Any]:
    root = _exact(document, PLAN_FIELDS, "completion plan")
    if root["format_version"] != 1:
        raise CompletionReleaseError("completion plan format_version は1が必要です。")
    if root["protocol"] != PLAN_PROTOCOL:
        raise CompletionReleaseError(
            f"completion plan protocol は {PLAN_PROTOCOL} が必要です。",
        )
    base = _exact(root["base"], PLAN_BASE_FIELDS, "completion plan base")
    normalized_base = {
        "manifest_sha256": _sha(
            base["manifest_sha256"],
            "completion plan base.manifest_sha256",
        ),
        "git_blob": _git_blob(base["git_blob"], "completion plan base.git_blob"),
        "candidate_set_sha256": _sha(
            base["candidate_set_sha256"],
            "completion plan base.candidate_set_sha256",
        ),
        "selection_sha256": _sha(
            base["selection_sha256"],
            "completion plan base.selection_sha256",
        ),
    }
    if normalized_base["manifest_sha256"] != BASE_MANIFEST_SHA256:
        raise CompletionReleaseError("completion plan base manifest SHA が不正です。")
    if normalized_base["git_blob"] != BASE_MANIFEST_GIT_BLOB:
        raise CompletionReleaseError("completion plan base git blob が不正です。")
    if normalized_base["candidate_set_sha256"] != BASE_CANDIDATE_SET_SHA256:
        raise CompletionReleaseError("completion plan base candidate-set が不正です。")
    if normalized_base["selection_sha256"] != BASE_SELECTION_SHA256:
        raise CompletionReleaseError("completion plan base selection が不正です。")
    if root["takes"] != TAKES:
        raise CompletionReleaseError(f"completion plan takes は{TAKES}が必要です。")
    if root["seed_base"] != SEED_BASE:
        raise CompletionReleaseError(
            f"completion plan seed_base は{SEED_BASE}が必要です。",
        )
    if root["minimum_eligible_candidates"] != 3:
        raise CompletionReleaseError(
            "completion plan minimum_eligible_candidates は3が必要です。",
        )
    if not isinstance(root["targets"], list):
        raise CompletionReleaseError("completion plan targets は配列が必要です。")
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, value in enumerate(root["targets"]):
        field = f"completion plan targets[{index}]"
        target = _exact(value, PLAN_TARGET_FIELDS, field)
        identity = tuple(
            _path_segment(target[key], f"{field}.{key}") for key in GROUP_KEYS
        )
        if identity in seen:
            raise CompletionReleaseError("completion plan target が重複しています。")
        seen.add(identity)
        outcome = _validate_prior_outcome(
            target["prior_outcome"],
            f"{field}.prior_outcome",
        )
        targets.append(
            {
                **dict(zip(GROUP_KEYS, identity, strict=True)),
                "prior_outcome": outcome,
            },
        )
    if len(targets) != EXPECTED_TARGET_COUNT:
        raise CompletionReleaseError(
            f"completion plan targets は{EXPECTED_TARGET_COUNT}件が必要です。",
        )
    if targets != sorted(targets, key=_group_key):
        raise CompletionReleaseError("completion plan targets は canonical tuple 順が必要です。")
    return {
        "format_version": 1,
        "protocol": PLAN_PROTOCOL,
        "base": normalized_base,
        "takes": TAKES,
        "seed_base": SEED_BASE,
        "minimum_eligible_candidates": 3,
        "targets": targets,
    }


def finalize_completion_release(
    *,
    base_manifest_path: Path,
    qwen_curation_path: Path,
    completion_plan_path: Path,
    decision_path: Path,
    supplement_run_ids: Sequence[str],
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    output_dir: Path,
) -> CompletionReleaseSummary:
    try:
        return _finalize_completion_release(
            base_manifest_path=base_manifest_path,
            qwen_curation_path=qwen_curation_path,
            completion_plan_path=completion_plan_path,
            decision_path=decision_path,
            supplement_run_ids=supplement_run_ids,
            artifacts_dir=artifacts_dir,
            scenarios_dir=scenarios_dir,
            voices_dir=voices_dir,
            output_dir=output_dir,
        )
    except CompletionReleaseError:
        raise
    except _PUBLIC_CONTRACT_ERRORS as error:
        raise CompletionReleaseError(
            f"completion finalize 入力契約が不正です: {error}",
        ) from error


def _finalize_completion_release(
    *,
    base_manifest_path: Path,
    qwen_curation_path: Path,
    completion_plan_path: Path,
    decision_path: Path,
    supplement_run_ids: Sequence[str],
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    output_dir: Path,
) -> CompletionReleaseSummary:
    """Finalize a complete release from a published base and explicit supplements."""

    if output_dir.exists():
        raise CompletionReleaseError(f"output は既存 path を拒否します: {output_dir}")
    if not output_dir.resolve().parent.is_dir():
        raise CompletionReleaseError("output の親 directory が存在しません。")
    run_ids = [_path_segment(value, "supplement run_id") for value in supplement_run_ids]
    if not run_ids or len(run_ids) != len(set(run_ids)):
        raise CompletionReleaseError("supplement run_ids は空でなく重複不可です。")

    try:
        load_completion_plan(
            completion_plan_path,
            base_manifest_path=base_manifest_path,
        )
    except CompletionPlanError as error:
        raise CompletionReleaseError(
            f"completion plan の固定 published-base 検証に失敗しました: {error}",
        ) from error
    plan = validate_completion_plan(
        _read_canonical_json(completion_plan_path, "completion plan"),
    )
    decision = validate_completion_decision(
        _read_canonical_json(decision_path, "completion decision"),
    )
    base_manifest_raw = _read_bytes(base_manifest_path, "base manifest")
    base_manifest = _validate_canonical_manifest(base_manifest_raw)
    _validate_base_binding(base_manifest_raw, base_manifest, plan)
    qwen_curation = _read_canonical_json(qwen_curation_path, "Qwen curation")
    legacy_selection = reconstruct_base_selection(
        base_manifest=base_manifest,
        qwen_curation=qwen_curation,
    )
    base_candidate_set = _rebuild_base_candidate_set(
        base_manifest=base_manifest,
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
    )
    base_candidate_set_bytes = canonical_candidate_set_bytes(base_candidate_set)
    if hashlib.sha256(base_candidate_set_bytes).hexdigest() != BASE_CANDIDATE_SET_SHA256:
        raise CompletionReleaseError("current scenarios からの base candidate-set が漂移しています。")
    target_map = _validate_plan_against_base(plan, base_manifest)
    supplement_scenario_sha256, supplement_lines = _load_current_lines(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        groups=set(target_map),
    )

    takes_root = _require_directory(artifacts_dir / "takes", "takes root")
    run_roots = {
        run_id: _resolve_direct_child(takes_root, run_id, f"run {run_id}")
        for run_id in run_ids
    }
    try:
        with ExitStack() as stack:
            for run_id in sorted(run_roots):
                stack.enter_context(exclusive_run_lock(run_roots[run_id]))
            supplement_runs = [
                _load_supplement_run(
                    run_id=run_id,
                    run_root=run_roots[run_id],
                    scenarios_dir=scenarios_dir,
                    voices_dir=voices_dir,
                )
                for run_id in sorted(run_roots)
            ]
            material = _build_completion_material(
                base_manifest=base_manifest,
                base_candidate_set=base_candidate_set,
                legacy_selection=legacy_selection,
                plan=plan,
                target_map=target_map,
                supplement_scenario_sha256=supplement_scenario_sha256,
                supplement_lines=supplement_lines,
                decision=decision,
                supplement_runs=supplement_runs,
            )
            summary = _write_release(output_dir=output_dir, material=material)
    except RunLockError as error:
        raise CompletionReleaseError(f"supplement run lock に失敗しました: {error}") from error
    return summary


def validate_completion_release(
    *,
    release_dir: Path,
    artifacts_dir: Path | None = None,
) -> CompletionReleaseBundle:
    try:
        return _validate_completion_release(
            release_dir=release_dir,
            artifacts_dir=artifacts_dir,
        )
    except CompletionReleaseError:
        raise
    except _PUBLIC_CONTRACT_ERRORS as error:
        raise CompletionReleaseError(
            f"completion release bundle が不正です: {error}",
        ) from error


def _validate_completion_release(
    *,
    release_dir: Path,
    artifacts_dir: Path | None = None,
) -> CompletionReleaseBundle:
    root = _require_directory(release_dir, "completion release")
    candidate_set = _read_marker_bound_canonical(
        root,
        "candidate-set.json",
        "candidate-set.sha256",
        canonical_candidate_set_bytes,
        "candidate set",
    )
    selection = _read_marker_bound_canonical(
        root,
        "selection.json",
        "selection.sha256",
        canonical_completion_selection_bytes,
        "selection",
    )
    manifest_raw = _read_bytes(root / "manifest-v4.json", "manifest")
    manifest = _validate_canonical_manifest(manifest_raw)
    _verify_marker(
        root / "manifest-v4.sha256",
        hashlib.sha256(manifest_raw).hexdigest(),
        "manifest",
    )
    provenance_raw = _read_bytes(root / "release-provenance.json", "provenance")
    provenance = validate_completion_provenance(
        _decode_json(provenance_raw, root / "release-provenance.json", "provenance"),
    )
    if canonical_json(provenance).encode("utf-8") != provenance_raw:
        raise CompletionReleaseError("provenance は canonical bytes が必要です。")
    _verify_marker(
        root / "release-provenance.sha256",
        hashlib.sha256(provenance_raw).hexdigest(),
        "provenance",
    )

    candidate_set_sha = hashlib.sha256(
        canonical_candidate_set_bytes(candidate_set),
    ).hexdigest()
    selection_sha = hashlib.sha256(
        canonical_completion_selection_bytes(selection),
    ).hexdigest()
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    if (
        candidate_set_sha != manifest["candidate_set_sha256"]
        or candidate_set_sha != selection["candidate_set_sha256"]
        or candidate_set_sha != provenance["candidate_set_sha256"]
    ):
        raise CompletionReleaseError("release candidate-set binding が一致しません。")
    if selection_sha != provenance["selection_sha256"]:
        raise CompletionReleaseError("release selection binding が一致しません。")
    if manifest_sha != provenance["manifest_sha256"]:
        raise CompletionReleaseError("release manifest binding が一致しません。")
    for key in ("models", "candidates", "failures"):
        if candidate_set[key] != manifest[key]:
            raise CompletionReleaseError(f"candidate set {key} が manifest と不一致です。")
    _validate_final_selection_projection(manifest, selection, selection_sha)
    _validate_provenance_partition(manifest, provenance)
    if artifacts_dir is not None:
        _validate_supplement_audio(provenance, artifacts_dir)
    return CompletionReleaseBundle(
        root=root,
        manifest=manifest,
        candidate_set=candidate_set,
        selection=selection,
        provenance=provenance,
    )


def validate_completion_provenance(document: Any) -> dict[str, Any]:
    root = _exact(document, PROVENANCE_FIELDS, "completion provenance")
    if root["format_version"] != 1 or root["protocol"] != RELEASE_PROTOCOL:
        raise CompletionReleaseError("completion provenance root contract が不正です。")
    base = _exact(root["base"], PROVENANCE_BASE_FIELDS, "completion provenance base")
    normalized_base = {
        "kind": base["kind"],
        "manifest_sha256": _sha(base["manifest_sha256"], "provenance base manifest"),
        "git_blob": _git_blob(base["git_blob"], "provenance base git_blob"),
        "candidate_set_sha256": _sha(
            base["candidate_set_sha256"],
            "provenance base candidate_set",
        ),
        "selection_sha256": _sha(
            base["selection_sha256"],
            "provenance base selection",
        ),
        "candidate_count": base["candidate_count"],
    }
    if normalized_base["kind"] != "published_manifest":
        raise CompletionReleaseError("provenance base.kind は published_manifest が必要です。")
    if (
        normalized_base["manifest_sha256"] != BASE_MANIFEST_SHA256
        or normalized_base["git_blob"] != BASE_MANIFEST_GIT_BLOB
        or normalized_base["candidate_set_sha256"] != BASE_CANDIDATE_SET_SHA256
        or normalized_base["selection_sha256"] != BASE_SELECTION_SHA256
        or normalized_base["candidate_count"] != EXPECTED_INHERITED_COUNT
    ):
        raise CompletionReleaseError("provenance base 公開基準が不正です。")
    if not isinstance(root["supplement_runs"], list) or not root["supplement_runs"]:
        raise CompletionReleaseError("provenance supplement_runs が必要です。")
    runs: list[dict[str, Any]] = []
    seen_runs: set[str] = set()
    seen_candidates: set[str] = set()
    seen_paths: set[str] = set()
    for index, value in enumerate(root["supplement_runs"]):
        field = f"completion provenance supplement_runs[{index}]"
        run = _exact(value, PROVENANCE_RUN_FIELDS, field)
        run_id = _path_segment(run["run_id"], f"{field}.run_id")
        model = _path_segment(run["model"], f"{field}.model")
        if run_id in seen_runs:
            raise CompletionReleaseError("provenance run_id が重複しています。")
        seen_runs.add(run_id)
        if not isinstance(run["candidates"], list):
            raise CompletionReleaseError(f"{field}.candidates は配列が必要です。")
        candidates: list[dict[str, Any]] = []
        for candidate_index, candidate_value in enumerate(run["candidates"]):
            candidate_field = f"{field}.candidates[{candidate_index}]"
            candidate = _exact(
                candidate_value,
                PROVENANCE_CANDIDATE_FIELDS,
                candidate_field,
            )
            take_id = _sha(candidate["take_id"], f"{candidate_field}.take_id")
            path = _relative_posix(candidate["path"], f"{candidate_field}.path")
            audio_sha = _sha(
                candidate["audio_sha256"],
                f"{candidate_field}.audio_sha256",
            )
            run_relative = _relative_posix(
                candidate["run_relative_path"],
                f"{candidate_field}.run_relative_path",
            )
            size = candidate["size_bytes"]
            if isinstance(size, bool) or not isinstance(size, int) or size < 1:
                raise CompletionReleaseError(f"{candidate_field}.size_bytes が不正です。")
            if take_id in seen_candidates or path in seen_paths:
                raise CompletionReleaseError("provenance supplement candidate が重複しています。")
            seen_candidates.add(take_id)
            seen_paths.add(path)
            candidates.append(
                {
                    "take_id": take_id,
                    "path": path,
                    "audio_sha256": audio_sha,
                    "run_relative_path": run_relative,
                    "size_bytes": size,
                },
            )
        runs.append(
            {
                "model": model,
                "run_id": run_id,
                "ledger_sha256": _sha(run["ledger_sha256"], f"{field}.ledger_sha256"),
                "qc_report_sha256": _sha(
                    run["qc_report_sha256"],
                    f"{field}.qc_report_sha256",
                ),
                "manifest_sha256": _sha(
                    run["manifest_sha256"],
                    f"{field}.manifest_sha256",
                ),
                "candidate_set_sha256": _sha(
                    run["candidate_set_sha256"],
                    f"{field}.candidate_set_sha256",
                ),
                "candidates": sorted(candidates, key=lambda item: item["take_id"]),
            },
        )
    runs.sort(key=lambda item: (item["model"], item["run_id"]))
    return {
        "format_version": 1,
        "protocol": RELEASE_PROTOCOL,
        "manifest_sha256": _sha(root["manifest_sha256"], "provenance manifest"),
        "candidate_set_sha256": _sha(
            root["candidate_set_sha256"],
            "provenance candidate_set",
        ),
        "selection_sha256": _sha(root["selection_sha256"], "provenance selection"),
        "base": normalized_base,
        "supplement_runs": runs,
    }


def _build_completion_material(
    *,
    base_manifest: Mapping[str, Any],
    base_candidate_set: Mapping[str, Any],
    legacy_selection: Mapping[str, Any],
    plan: Mapping[str, Any],
    target_map: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
    supplement_scenario_sha256: str,
    supplement_lines: Sequence[Mapping[str, Any]],
    decision: Mapping[str, Any],
    supplement_runs: Sequence[_SupplementRun],
) -> dict[str, Any]:
    source_groups = set().union(*(run.groups for run in supplement_runs))
    if source_groups != set(target_map):
        missing = sorted(set(target_map) - source_groups)
        extra = sorted(source_groups - set(target_map))
        raise CompletionReleaseError(
            f"supplement source groups が plan と exact ではありません: "
            f"missing={missing}, extra={extra}",
        )
    new_candidates = sorted(
        (
            dict(candidate)
            for run in supplement_runs
            for candidate in run.manifest["candidates"]
        ),
        key=lambda item: (_group_key(item), item["take_index"], item["take_id"]),
    )
    _require_unique_candidates(new_candidates, "supplement")
    new_by_group = _candidates_by_group(new_candidates)
    for identity in sorted(target_map):
        if len(new_by_group.get(identity, [])) < plan["minimum_eligible_candidates"]:
            raise CompletionReleaseError(
                f"supplement mechanical-pass candidate が3件未満です: {identity}",
            )

    models_by_id: dict[str, dict[str, Any]] = {}
    for run in supplement_runs:
        model = run.manifest["models"][0]
        previous = models_by_id.setdefault(model["id"], model)
        if previous != model:
            raise CompletionReleaseError("supplement model metadata が run 間で不一致です。")
    base_models_by_id = {item["id"]: item for item in base_manifest["models"]}
    if set(models_by_id) != {identity[0] for identity in target_map}:
        raise CompletionReleaseError("supplement models が target model 集合と不一致です。")
    if any(base_models_by_id.get(model) != metadata for model, metadata in models_by_id.items()):
        raise CompletionReleaseError("supplement model metadata が published base と不一致です。")

    supplement_candidate_set = build_candidate_set(
        scenario_sha256=supplement_scenario_sha256,
        lines=[dict(line) for line in supplement_lines],
        models=[models_by_id[model] for model in sorted(models_by_id)],
        candidates=new_candidates,
        failures=[],
    )
    supplement_candidate_set_sha = hashlib.sha256(
        canonical_candidate_set_bytes(supplement_candidate_set),
    ).hexdigest()
    if decision["candidate_set_sha256"] != supplement_candidate_set_sha:
        raise CompletionReleaseError(
            "completion decision が supplement listening candidate-set と"
            "一致しません。",
        )
    _validate_decision_exact(decision, new_by_group, set(target_map))

    legacy_selected_groups = [
        dict(group)
        for group in legacy_selection["groups"]
        if group["decision"]["type"] == "selected"
    ]
    if len(legacy_selected_groups) != EXPECTED_INHERITED_COUNT:
        raise CompletionReleaseError("published base selected group 数が不正です。")
    inherited_take_ids = {
        group["decision"]["take_id"] for group in legacy_selected_groups
    }
    inherited_candidates = [
        dict(candidate)
        for candidate in base_manifest["candidates"]
        if candidate["take_id"] in inherited_take_ids
    ]
    if len(inherited_candidates) != EXPECTED_INHERITED_COUNT:
        raise CompletionReleaseError("published base candidate 投影が exact ではありません。")
    if {_group_key(item) for item in inherited_candidates} & set(target_map):
        raise CompletionReleaseError("published selected group と completion target が競合します。")

    final_candidates = sorted(
        [*inherited_candidates, *new_candidates],
        key=lambda item: (_group_key(item), item["take_index"], item["take_id"]),
    )
    _require_unique_candidates(final_candidates, "final")
    final_candidate_set = build_candidate_set(
        scenario_sha256=base_candidate_set["scenario_sha256"],
        lines=[dict(line) for line in base_candidate_set["lines"]],
        models=[dict(model) for model in base_manifest["models"]],
        candidates=final_candidates,
        failures=[],
    )
    candidate_set_bytes = canonical_candidate_set_bytes(final_candidate_set)
    candidate_set_sha = hashlib.sha256(candidate_set_bytes).hexdigest()
    final_selection = validate_completion_selection(
        {
            "format_version": SELECTION_FORMAT_VERSION,
            "protocol": SELECTION_PROTOCOL,
            "candidate_set_sha256": candidate_set_sha,
            "groups": [*legacy_selected_groups, *decision["groups"]],
        },
    )
    selection_bytes = canonical_completion_selection_bytes(final_selection)
    selection_sha = hashlib.sha256(selection_bytes).hexdigest()

    generated_at_values = {
        run.manifest["generated_at"] for run in supplement_runs
    }
    generated_at = max(generated_at_values)
    manifest = validate_manifest_v4(
        {
            "format_version": 4,
            "generated_at": generated_at,
            "candidate_set_sha256": candidate_set_sha,
            "models": [dict(model) for model in base_manifest["models"]],
            "candidates": final_candidates,
            "curations": [
                {
                    **{key: group[key] for key in GROUP_KEYS},
                    "decision": "selected",
                    "take_id": group["decision"]["take_id"],
                    "curation_sha256": selection_sha,
                }
                for group in final_selection["groups"]
            ],
            "failures": [],
        },
    )
    manifest_bytes = canonical_json(manifest).encode("utf-8")
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if (
        len(manifest["curations"]) != EXPECTED_SELECTED_COUNT
        or len(manifest["failures"]) != 0
    ):
        raise CompletionReleaseError("final manifest は1288 selected/0 failureが必要です。")

    provenance = validate_completion_provenance(
        {
            "format_version": 1,
            "protocol": RELEASE_PROTOCOL,
            "manifest_sha256": manifest_sha,
            "candidate_set_sha256": candidate_set_sha,
            "selection_sha256": selection_sha,
            "base": {
                "kind": "published_manifest",
                **dict(plan["base"]),
                "candidate_count": len(inherited_candidates),
            },
            "supplement_runs": [
                {
                    "model": run.model,
                    "run_id": run.run_id,
                    "ledger_sha256": run.ledger_sha256,
                    "qc_report_sha256": run.qc_report_sha256,
                    "manifest_sha256": run.manifest_sha256,
                    "candidate_set_sha256": run.candidate_set_sha256,
                    "candidates": [dict(item) for item in run.provenance_candidates],
                }
                for run in supplement_runs
            ],
        },
    )
    return {
        "candidate_set": final_candidate_set,
        "candidate_set_bytes": candidate_set_bytes,
        "candidate_set_sha256": candidate_set_sha,
        "selection": final_selection,
        "selection_bytes": selection_bytes,
        "selection_sha256": selection_sha,
        "manifest": manifest,
        "manifest_bytes": manifest_bytes,
        "manifest_sha256": manifest_sha,
        "provenance": provenance,
        "provenance_bytes": canonical_json(provenance).encode("utf-8"),
        "supplement_candidate_count": len(new_candidates),
    }


def _load_supplement_run(
    *,
    run_id: str,
    run_root: Path,
    scenarios_dir: Path,
    voices_dir: Path,
) -> _SupplementRun:
    ledger_path = run_root / "ledger.json"
    qc_path = run_root / "qc-report.json"
    manifest_path = run_root / "manifest-v4.json"
    try:
        ledger = read_ledger(ledger_path)
    except (OSError, UnicodeError, json.JSONDecodeError, TakeLedgerError) as error:
        raise CompletionReleaseError(f"run ledger が不正です: {run_id}: {error}") from error
    if ledger["run_id"] != run_id:
        raise CompletionReleaseError("run_id が ledger と一致しません。")
    _validate_supplement_ledger_contract(ledger)
    if any(item["status"] not in TERMINAL_STATUSES for item in ledger["attempts"]):
        raise CompletionReleaseError("supplement run は全attempt terminalが必要です。")
    try:
        bundle = validate_snapshot_bundle(
            snapshot_path=manifest_path,
            candidate_set_path=run_root / "candidate-set.json",
            marker_path=run_root / "candidate-set.sha256",
        )
    except CurationError as error:
        raise CompletionReleaseError(f"run snapshot が不正です: {run_id}: {error}") from error
    if bundle.manifest["curations"]:
        raise CompletionReleaseError("supplement source manifest は curation を許可しません。")
    if len(bundle.manifest["models"]) != 1:
        raise CompletionReleaseError("supplement run は1 modelが必要です。")
    model = ledger["source"]["model"]
    if bundle.manifest["models"][0]["id"] != model:
        raise CompletionReleaseError("supplement run model metadata が ledger と不一致です。")
    qc_document = _read_json_document(qc_path, "supplement QC report")
    try:
        qc_authority = validate_qc_report(
            qc_document,
            ledger_path=ledger_path,
            ledger=ledger,
        )
        if qc_document["generated_at"] != bundle.manifest["generated_at"]:
            raise CompletionReleaseError(
                "supplement QC generated_at が manifest と不一致です。",
            )
        _validate_manifest_against_terminal_ledger(
            manifest=bundle.manifest,
            ledger=ledger,
            run_root=run_root,
            qc_authority=qc_authority,
        )
        scenario_sha, lines = _load_current_lines(
            scenarios_dir=scenarios_dir,
            voices_dir=voices_dir,
            groups={
                _group_key(group) for group in ledger["source"]["groups"]
            },
        )
        if scenario_sha != ledger["source"]["scenario_sha256"]:
            raise CompletionReleaseError(
                "current scenario source SHA が supplement ledger と不一致です。",
            )
        rebuilt = build_candidate_set(
            scenario_sha256=scenario_sha,
            lines=lines,
            models=bundle.manifest["models"],
            candidates=bundle.manifest["candidates"],
            failures=bundle.manifest["failures"],
        )
        if canonical_candidate_set_bytes(rebuilt) != bundle.candidate_set_bytes:
            raise CompletionReleaseError("supplement candidate-set current rebuild と不一致です。")
    except (CurationError, QCReportError, TakeManifestError, TakeLedgerError) as error:
        raise CompletionReleaseError(f"supplement provenance が不正です: {run_id}: {error}") from error

    attempts_by_take = {
        attempt["take_id"]: attempt
        for attempt in ledger["attempts"]
        if attempt["status"] == "eligible"
    }
    provenance_candidates: list[dict[str, Any]] = []
    for candidate in bundle.manifest["candidates"]:
        attempt = attempts_by_take[candidate["take_id"]]
        relative = _relative_posix(
            attempt["audio"]["opus_path"],
            f"{run_id} opus path",
        )
        audio_path = _resolve_child(run_root, relative, "supplement Opus")
        size = audio_path.stat().st_size
        if size < 1:
            raise CompletionReleaseError("supplement Opus が空です。")
        provenance_candidates.append(
            {
                "take_id": candidate["take_id"],
                "path": candidate["path"],
                "audio_sha256": candidate["sha256"],
                "run_relative_path": relative,
                "size_bytes": size,
            },
        )
    groups = frozenset(
        _group_key(group) for group in ledger["source"]["groups"]
    )
    return _SupplementRun(
        run_id=run_id,
        model=model,
        root=run_root,
        manifest=bundle.manifest,
        candidate_set_sha256=bundle.candidate_set_sha256,
        ledger_sha256=_file_sha256(ledger_path),
        qc_report_sha256=_file_sha256(qc_path),
        manifest_sha256=_file_sha256(manifest_path),
        groups=groups,
        provenance_candidates=tuple(provenance_candidates),
    )


def _validate_base_binding(
    raw: bytes,
    manifest: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> None:
    actual_sha = hashlib.sha256(raw).hexdigest()
    actual_blob = _git_blob_sha1(raw)
    if actual_sha != BASE_MANIFEST_SHA256 or actual_blob != BASE_MANIFEST_GIT_BLOB:
        raise CompletionReleaseError("base manifest が固定 published baseline ではありません。")
    if actual_sha != plan["base"]["manifest_sha256"]:
        raise CompletionReleaseError("base manifest SHA が completion plan と不一致です。")
    if actual_blob != plan["base"]["git_blob"]:
        raise CompletionReleaseError("base manifest git blob が completion plan と不一致です。")
    if manifest["candidate_set_sha256"] != plan["base"]["candidate_set_sha256"]:
        raise CompletionReleaseError("base manifest candidate-set binding が不一致です。")


def _rebuild_base_candidate_set(
    *,
    base_manifest: Mapping[str, Any],
    scenarios_dir: Path,
    voices_dir: Path,
) -> dict[str, Any]:
    groups = {
        _group_key(item)
        for key in ("curations", "failures")
        for item in base_manifest[key]
    }
    scenario_sha, lines = _load_current_lines(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        groups=groups,
    )
    return build_candidate_set(
        scenario_sha256=scenario_sha,
        lines=lines,
        models=[dict(item) for item in base_manifest["models"]],
        candidates=[dict(item) for item in base_manifest["candidates"]],
        failures=[dict(item) for item in base_manifest["failures"]],
    )


def _load_current_lines(
    scenarios_dir: Path,
    voices_dir: Path,
    groups: set[tuple[str, str, str, str]],
) -> tuple[str, list[dict[str, Any]]]:
    requested = {_line_key(identity) for identity in groups}
    scenario_ids = sorted({scenario for scenario, _line in requested})
    validation = validate_scenario_ids(
        scenarios_dir,
        scenario_ids,
        voices_dir=voices_dir,
    )
    if validation.problems:
        raise CompletionReleaseError(
            "scenario 検証に失敗しました:\n"
            + "\n".join(str(problem) for problem in validation.problems),
        )
    source_files: list[dict[str, str]] = []
    lines: list[dict[str, Any]] = []
    found: set[tuple[str, str]] = set()
    for scenario_id in scenario_ids:
        path = scenarios_dir / f"{scenario_id}.yaml"
        raw = _read_bytes(path, "scenario")
        try:
            document = yaml.safe_load(raw.decode("utf-8"))
        except (UnicodeError, yaml.YAMLError) as error:
            raise CompletionReleaseError(f"scenario を読めません: {path}") from error
        source_files.append({"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
        for line in document["lines"]:
            identity = (scenario_id, str(line["id"]))
            if identity not in requested:
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
    if found != requested:
        raise CompletionReleaseError("base scenario lines が manifest を exact に被覆しません。")
    return (
        hashlib.sha256(canonical_json(source_files).encode("utf-8")).hexdigest(),
        sorted(lines, key=lambda item: (item["scenario"], item["line"])),
    )


def _validate_plan_against_base(
    plan: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[tuple[str, str, str, str], Mapping[str, Any]]:
    actual: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in manifest["curations"]:
        if item["decision"] == "skipped":
            actual[_group_key(item)] = {
                "decision": "skipped",
                "curation_sha256": item["curation_sha256"],
            }
    for item in manifest["failures"]:
        actual[_group_key(item)] = {"reason": item["reason"]}
    targets = {_group_key(item): item["prior_outcome"] for item in plan["targets"]}
    if targets != actual:
        raise CompletionReleaseError("completion plan targets が base 未公開slotと exact ではありません。")
    return targets


def _validate_decision_exact(
    decision: Mapping[str, Any],
    candidates_by_group: Mapping[
        tuple[str, str, str, str],
        Sequence[Mapping[str, Any]],
    ],
    targets: set[tuple[str, str, str, str]],
) -> None:
    decision_groups = {_group_key(item): item for item in decision["groups"]}
    if set(decision_groups) != targets:
        raise CompletionReleaseError("completion decision target groups が exact ではありません。")
    for identity, group in decision_groups.items():
        expected = {
            (
                candidate["take_id"],
                candidate["path"],
                candidate["sha256"],
                canonical_json(candidate["gate"]),
            )
            for candidate in candidates_by_group[identity]
        }
        actual = {
            (
                candidate["take_id"],
                candidate["path"],
                candidate["audio_sha256"],
                canonical_json(candidate["gate"]),
            )
            for candidate in group["candidates"]
        }
        if actual != expected:
            raise CompletionReleaseError(
                f"completion decision candidates が source と exact ではありません: {identity}",
            )


def _validate_final_selection_projection(
    manifest: Mapping[str, Any],
    selection: Mapping[str, Any],
    selection_sha: str,
) -> None:
    groups = {_group_key(item): item for item in selection["groups"]}
    projections = {_group_key(item): item for item in manifest["curations"]}
    manifest_candidates: dict[
        tuple[str, str, str, str],
        dict[str, Mapping[str, Any]],
    ] = {}
    for candidate in manifest["candidates"]:
        manifest_candidates.setdefault(_group_key(candidate), {})[
            candidate["take_id"]
        ] = candidate
    if len(groups) != EXPECTED_SELECTED_COUNT or set(groups) != set(projections):
        raise CompletionReleaseError("final selection coverage が1288 groups exactではありません。")
    if set(groups) != set(manifest_candidates):
        raise CompletionReleaseError(
            "final selection groups が manifest candidate groups と exact ではありません。",
        )
    for identity, group in groups.items():
        projection = projections[identity]
        if (
            group["decision"]["type"] != "selected"
            or projection["decision"] != "selected"
            or projection["take_id"] != group["decision"]["take_id"]
            or projection["curation_sha256"] != selection_sha
        ):
            raise CompletionReleaseError("final selection projection が manifest と不一致です。")
        expected = manifest_candidates[identity]
        selected_candidates = {
            candidate["take_id"]: candidate for candidate in group["candidates"]
        }
        if set(selected_candidates) != set(expected):
            raise CompletionReleaseError(
                "final selection candidates が manifest group と exact ではありません。",
            )
        for take_id, candidate in selected_candidates.items():
            manifest_candidate = expected[take_id]
            if (
                candidate["path"] != manifest_candidate["path"]
                or candidate["audio_sha256"] != manifest_candidate["sha256"]
            ):
                raise CompletionReleaseError(
                    "final selection candidate path/SHA が manifest と不一致です。",
                )
            if "gate" in candidate and candidate["gate"] != manifest_candidate["gate"]:
                raise CompletionReleaseError(
                    "final selection candidate gate が manifest と不一致です。",
                )


def _validate_supplement_ledger_contract(ledger: Mapping[str, Any]) -> None:
    source = ledger.get("source")
    if not isinstance(source, Mapping):
        raise CompletionReleaseError("supplement ledger.source が不正です。")
    if source.get("takes") != TAKES:
        raise CompletionReleaseError(
            f"supplement ledger.source.takes は{TAKES}が必要です。",
        )
    if source.get("seed_base") != SEED_BASE:
        raise CompletionReleaseError(
            f"supplement ledger.source.seed_base は{SEED_BASE}が必要です。",
        )


def _validate_provenance_partition(
    manifest: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> None:
    candidates_by_take = {
        item["take_id"]: item for item in manifest["candidates"]
    }
    supplement = {
        item["take_id"]: item
        for run in provenance["supplement_runs"]
        for item in run["candidates"]
    }
    if len(supplement) != sum(
        len(run["candidates"]) for run in provenance["supplement_runs"]
    ):
        raise CompletionReleaseError("supplement provenance take_id が重複しています。")
    if not set(supplement) <= set(candidates_by_take):
        raise CompletionReleaseError("supplement provenance が manifest 外candidateを参照します。")
    for take_id, item in supplement.items():
        candidate = candidates_by_take[take_id]
        if (
            item["path"] != candidate["path"]
            or item["audio_sha256"] != candidate["sha256"]
        ):
            raise CompletionReleaseError("supplement provenance candidate が manifest と不一致です。")
    inherited_count = len(candidates_by_take) - len(supplement)
    if inherited_count != provenance["base"]["candidate_count"]:
        raise CompletionReleaseError("published/inherited candidate partition が不正です。")


def _validate_supplement_audio(
    provenance: Mapping[str, Any],
    artifacts_dir: Path,
) -> None:
    takes_root = _require_directory(artifacts_dir / "takes", "takes root")
    for run in provenance["supplement_runs"]:
        run_root = _resolve_direct_child(takes_root, run["run_id"], "supplement run")
        for item in run["candidates"]:
            path = _resolve_child(run_root, item["run_relative_path"], "supplement Opus")
            if path.stat().st_size != item["size_bytes"]:
                raise CompletionReleaseError("supplement Opus size が provenance と不一致です。")
            if _file_sha256(path) != item["audio_sha256"]:
                raise CompletionReleaseError("supplement Opus SHA が provenance と不一致です。")


def _write_release(
    *,
    output_dir: Path,
    material: Mapping[str, Any],
) -> CompletionReleaseSummary:
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.",
            dir=output_dir.resolve().parent,
        ),
    )
    try:
        _write_new(stage / "candidate-set.json", material["candidate_set_bytes"])
        _write_new(
            stage / "candidate-set.sha256",
            material["candidate_set_sha256"].encode("ascii"),
        )
        _write_new(stage / "selection.json", material["selection_bytes"])
        _write_new(
            stage / "selection.sha256",
            material["selection_sha256"].encode("ascii"),
        )
        _write_new(stage / "manifest-v4.json", material["manifest_bytes"])
        _write_new(
            stage / "manifest-v4.sha256",
            material["manifest_sha256"].encode("ascii"),
        )
        _write_new(stage / "release-provenance.json", material["provenance_bytes"])
        _write_new(
            stage / "release-provenance.sha256",
            hashlib.sha256(material["provenance_bytes"]).hexdigest().encode("ascii"),
        )
        validated = validate_completion_release(release_dir=stage)
        if (
            validated.manifest != material["manifest"]
            or validated.candidate_set != material["candidate_set"]
            or validated.selection != material["selection"]
            or validated.provenance != material["provenance"]
        ):
            raise CompletionReleaseError("release write後の再検証が material と不一致です。")
        os.replace(stage, output_dir)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return CompletionReleaseSummary(
        output_dir=output_dir,
        manifest_sha256=material["manifest_sha256"],
        candidate_set_sha256=material["candidate_set_sha256"],
        selection_sha256=material["selection_sha256"],
        candidate_count=len(material["manifest"]["candidates"]),
        selected_count=len(material["manifest"]["curations"]),
        supplement_candidate_count=material["supplement_candidate_count"],
    )


def _validate_prior_outcome(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise CompletionReleaseError(f"{field} が不正です。")
    if set(value) == SKIPPED_OUTCOME_FIELDS:
        outcome = _exact(value, SKIPPED_OUTCOME_FIELDS, field)
        if outcome["decision"] != "skipped":
            raise CompletionReleaseError(f"{field}.decision が不正です。")
        curation_sha = _sha(outcome["curation_sha256"], f"{field}.curation_sha256")
        if curation_sha != BASE_SELECTION_SHA256:
            raise CompletionReleaseError(f"{field}.curation_sha256 が不正です。")
        return {"decision": "skipped", "curation_sha256": curation_sha}
    if set(value) == FAILURE_OUTCOME_FIELDS:
        outcome = _exact(value, FAILURE_OUTCOME_FIELDS, field)
        if outcome["reason"] != "no_eligible_take":
            raise CompletionReleaseError(f"{field}.reason が不正です。")
        return {"reason": "no_eligible_take"}
    raise CompletionReleaseError(f"{field} の項目が契約と一致しません。")


def _read_marker_bound_canonical(
    root: Path,
    filename: str,
    marker_name: str,
    canonicalizer: Any,
    label: str,
) -> dict[str, Any]:
    path = root / filename
    raw = _read_bytes(path, label)
    document = _decode_json(raw, path, label)
    canonical = canonicalizer(document)
    if raw != canonical:
        raise CompletionReleaseError(f"{label} は canonical bytes が必要です。")
    _verify_marker(
        root / marker_name,
        hashlib.sha256(raw).hexdigest(),
        label,
    )
    return document


def _validate_canonical_manifest(raw: bytes) -> dict[str, Any]:
    try:
        manifest = validate_manifest_v4(json.loads(raw.decode("utf-8")))
    except (UnicodeError, json.JSONDecodeError, TakeManifestError) as error:
        raise CompletionReleaseError(f"manifest が不正です: {error}") from error
    if canonical_json(manifest).encode("utf-8") != raw:
        raise CompletionReleaseError("manifest は canonical bytes が必要です。")
    return manifest


def _read_canonical_json(path: Path, label: str) -> Any:
    raw = _read_bytes(path, label)
    document = _decode_json(raw, path, label)
    try:
        canonical = canonical_json(document).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CompletionReleaseError(f"{label} が canonical JSON 契約外です。") from error
    if raw != canonical:
        raise CompletionReleaseError(f"{label} は canonical bytes が必要です。")
    return document


def _read_json_document(path: Path, label: str) -> Any:
    raw = _read_bytes(path, label)
    return _decode_json(raw, path, label)


def _decode_json(raw: bytes, path: Path, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CompletionReleaseError(f"{label} を読めません: {path}") from error


def _require_unique_candidates(candidates: Sequence[Mapping[str, Any]], label: str) -> None:
    take_ids: set[str] = set()
    paths: set[str] = set()
    slots: set[tuple[tuple[str, str, str, str], int]] = set()
    for candidate in candidates:
        slot = (_group_key(candidate), candidate["take_index"])
        if (
            candidate["take_id"] in take_ids
            or candidate["path"] in paths
            or slot in slots
        ):
            raise CompletionReleaseError(f"{label} candidate identity/slot が重複しています。")
        take_ids.add(candidate["take_id"])
        paths.add(candidate["path"])
        slots.add(slot)


def _candidates_by_group(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str, str], list[Mapping[str, Any]]]:
    result: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
    for candidate in candidates:
        result.setdefault(_group_key(candidate), []).append(candidate)
    return result


def _group_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(value[key]) for key in GROUP_KEYS)  # type: ignore[return-value]


def _line_key(identity: tuple[str, str, str, str]) -> tuple[str, str]:
    return identity[1], identity[2]


def _path_segment(value: Any, field: str) -> str:
    if not isinstance(value, str) or PATH_SEGMENT.fullmatch(value) is None:
        raise CompletionReleaseError(f"{field} は安全な path segment が必要です。")
    return value


def _relative_posix(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CompletionReleaseError(f"{field} は相対POSIX pathが必要です。")
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise CompletionReleaseError(f"{field} は安全な相対POSIX pathが必要です。")
    return value


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in HEX for character in value)
    ):
        raise CompletionReleaseError(f"{field} は小文字SHA-256が必要です。")
    return value


def _git_blob(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in HEX for character in value)
    ):
        raise CompletionReleaseError(f"{field} は小文字git blob SHA-1が必要です。")
    return value


def _exact(value: Any, fields: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise CompletionReleaseError(f"{field} の項目が契約と一致しません。")
    return value


def _git_blob_sha1(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()  # noqa: S324


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise CompletionReleaseError(f"{label} が通常ファイルではありません: {path}")
    try:
        return path.read_bytes()
    except OSError as error:
        raise CompletionReleaseError(f"{label} を読めません: {path}") from error


def _verify_marker(path: Path, expected: str, label: str) -> None:
    try:
        marker = path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as error:
        raise CompletionReleaseError(f"{label} marker を読めません: {path}") from error
    if marker != expected:
        raise CompletionReleaseError(f"{label} marker が内容と一致しません。")


def _require_directory(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise CompletionReleaseError(f"{label} が存在しません: {path}") from error
    if not resolved.is_dir():
        raise CompletionReleaseError(f"{label} がdirectoryではありません: {path}")
    return resolved


def _resolve_direct_child(root: Path, name: str, label: str) -> Path:
    path = root / name
    resolved = _require_directory(path, label)
    if resolved.parent != root:
        raise CompletionReleaseError(f"{label} がroot直下ではありません。")
    return resolved


def _resolve_child(root: Path, relative: str, label: str) -> Path:
    path = root / Path(*PurePosixPath(relative).parts)
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise CompletionReleaseError(f"{label} が存在しません: {path}") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise CompletionReleaseError(f"{label} がroot内通常ファイルではありません。")
    return resolved


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except OSError as error:
        raise CompletionReleaseError(f"release fileを書けません: {path}") from error
