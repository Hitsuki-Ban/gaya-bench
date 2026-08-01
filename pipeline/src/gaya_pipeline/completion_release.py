from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from gaya_pipeline.completion_listen import (
    CompletionListeningError,
    CompletionSourceResolution,
    PRIMARY_MODELS,
    _load_target_lines,
    _local_audio_path,
    resolve_completion_sources,
)
from gaya_pipeline.completion_plan import (
    BASE_MANIFEST_GIT_BLOB,
    BASE_MANIFEST_SHA256,
    PRODUCTION_PLAN_SHA256,
    CompletionPlan,
    require_production_completion_plan,
)
from gaya_pipeline.completion_selection import (
    BASE_CANDIDATE_SET_SHA256,
    BASE_SELECTION_SHA256,
    FORMAT_VERSION as SELECTION_FORMAT_VERSION,
    PROTOCOL as SELECTION_PROTOCOL,
    canonical_completion_decision_bytes,
    canonical_completion_selection_bytes,
    reconstruct_base_selection,
    validate_completion_decision,
    validate_completion_selection,
)
from gaya_pipeline.curation import (
    CurationError,
    build_candidate_set,
    canonical_candidate_set_bytes,
)
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.take_manifest_v4 import TakeManifestError, validate_manifest_v4


class CompletionReleaseError(RuntimeError):
    pass


RELEASE_PROTOCOL = "role-baseline-release-v1"
SOURCE_AUDIT_SHA256 = (
    "0456aca9a1d8c9a57049ce07f9106c16452067e8fa3ebdc88111beae70f2544c"
)
EXPECTED_REPLACEMENT_COUNT = 597
EXPECTED_INHERITED_COUNT = 691
EXPECTED_MATCHED_COUNT = 691
EXPECTED_SELECTED_COUNT = 1_288


@dataclass(frozen=True)
class CompletionReleaseSummary:
    output_dir: Path
    manifest_sha256: str
    candidate_set_sha256: str
    selection_sha256: str
    candidate_count: int
    selected_count: int
    replacement_candidate_count: int


@dataclass(frozen=True)
class CompletionReleaseBundle:
    root: Path
    manifest: dict[str, Any]
    candidate_set: dict[str, Any]
    selection: dict[str, Any]
    provenance: dict[str, Any]


def finalize_completion_release(
    *,
    plan: CompletionPlan,
    base_manifest_path: Path,
    qwen_curation_path: Path,
    source_audit_path: Path,
    decision_path: Path,
    primary_run_ids: Sequence[str],
    topup_run_ids: Sequence[str],
    anchor_selection_path: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    output_dir: Path,
) -> CompletionReleaseSummary:
    require_production_completion_plan(plan)
    try:
        return _finalize_completion_release(
            plan=plan,
            base_manifest_path=base_manifest_path,
            qwen_curation_path=qwen_curation_path,
            source_audit_path=source_audit_path,
            decision_path=decision_path,
            primary_run_ids=primary_run_ids,
            topup_run_ids=topup_run_ids,
            anchor_selection_path=anchor_selection_path,
            artifacts_dir=artifacts_dir,
            scenarios_dir=scenarios_dir,
            voices_dir=voices_dir,
            output_dir=output_dir,
        )
    except CompletionReleaseError:
        raise
    except (
        CompletionListeningError,
        CurationError,
        TakeManifestError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise CompletionReleaseError(f"completion release入力契約が不正です: {error}") from error


def _finalize_completion_release(
    *,
    plan: CompletionPlan,
    base_manifest_path: Path,
    qwen_curation_path: Path,
    source_audit_path: Path,
    decision_path: Path,
    primary_run_ids: Sequence[str],
    topup_run_ids: Sequence[str],
    anchor_selection_path: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    output_dir: Path,
) -> CompletionReleaseSummary:
    if output_dir.exists():
        raise CompletionReleaseError(f"release outputは既存pathを拒否します: {output_dir}")
    if not output_dir.resolve().parent.is_dir():
        raise CompletionReleaseError("release outputの親directoryが存在しません。")
    resolution = resolve_completion_sources(
        plan=plan,
        primary_run_ids=primary_run_ids,
        topup_run_ids=topup_run_ids,
        anchor_selection_path=anchor_selection_path,
        artifacts_dir=artifacts_dir,
        scenarios_dir=scenarios_dir,
    )
    base_raw = _read_bytes(base_manifest_path, "base manifest")
    if hashlib.sha256(base_raw).hexdigest() != plan.base_manifest_sha256:
        raise CompletionReleaseError("base manifest SHAがfrozen planと一致しません。")
    base_manifest = validate_manifest_v4(_decode_json(base_raw, base_manifest_path))
    qwen_curation = _read_json(qwen_curation_path, "Qwen curation")
    base_selection = reconstruct_base_selection(
        base_manifest=base_manifest,
        qwen_curation=qwen_curation,
    )
    if (
        base_manifest["candidate_set_sha256"] != plan.base_candidate_set_sha256
        or plan.base_candidate_set_sha256 != BASE_CANDIDATE_SET_SHA256
        or plan.base_selection_sha256 != BASE_SELECTION_SHA256
    ):
        raise CompletionReleaseError("published base bindingが固定値と一致しません。")

    replacement = {target.identity for target in plan.targets}
    if len(replacement) != EXPECTED_REPLACEMENT_COUNT:
        raise CompletionReleaseError("frozen plan replacementはexact 597 groupが必要です。")
    base_groups = {_group_key(group): group for group in base_selection["groups"]}
    selected_base = {
        identity: group
        for identity, group in base_groups.items()
        if group["decision"]["type"] == "selected"
    }
    inherited = {
        identity: group
        for identity, group in selected_base.items()
        if identity not in replacement
    }
    if len(inherited) != EXPECTED_INHERITED_COUNT:
        raise CompletionReleaseError(
            "inheritedはpublished selected - replacement exactの691 groupが必要です。",
        )

    audit = _load_source_audit(source_audit_path)
    audit_partition = _validate_audit_partition(
        audit=audit,
        replacement=replacement,
        inherited=inherited,
    )
    replacement_candidates = _replacement_candidates(plan, resolution)
    replacement_candidate_set = _build_candidate_set(
        candidates=replacement_candidates,
        models=[
            run.manifest["models"][0]
            for run in resolution.runs
        ],
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        plan=plan,
    )
    replacement_candidate_bytes = canonical_candidate_set_bytes(
        replacement_candidate_set,
    )
    replacement_candidate_sha = hashlib.sha256(
        replacement_candidate_bytes,
    ).hexdigest()
    decision_raw = _read_bytes(decision_path, "completion decision")
    decision = validate_completion_decision(
        _decode_json(decision_raw, decision_path),
    )
    if decision_raw != canonical_completion_decision_bytes(decision):
        raise CompletionReleaseError("completion decisionはcanonical bytesが必要です。")
    if (
        decision["plan_sha256"] != plan.plan_id
        or decision["anchor_selection_sha256"]
        != resolution.anchor_selection_sha256
        or decision["candidate_set_sha256"] != replacement_candidate_sha
    ):
        raise CompletionReleaseError(
            "completion decisionのplan/anchor/candidate-set bindingが不一致です。",
        )
    decision_groups = {_group_key(group): group for group in decision["groups"]}
    if set(decision_groups) != replacement:
        raise CompletionReleaseError("completion decisionはexact 597 groupが必要です。")
    _validate_decision_against_sources(
        decision_groups=decision_groups,
        plan=plan,
        resolution=resolution,
        candidate_set=replacement_candidate_set,
    )

    base_candidates_by_take = {
        candidate["take_id"]: candidate for candidate in base_manifest["candidates"]
    }
    inherited_candidates: list[dict[str, Any]] = []
    selection_groups: list[dict[str, Any]] = []
    for identity, group in sorted(inherited.items()):
        take_id = group["decision"]["take_id"]
        candidate = base_candidates_by_take.get(take_id)
        if candidate is None or _group_key(candidate) != identity:
            raise CompletionReleaseError("inherited selected candidateがbaseにありません。")
        inherited_candidates.append(dict(candidate))
        selection_groups.append(
            {
                **{key: group[key] for key in ("model", "scenario", "line", "variant")},
                "role_epoch_sha256": _inherited_role_epoch(plan, identity),
                "authority": dict(group["authority"]),
                "candidates": [dict(item) for item in group["candidates"]],
                "decision": dict(group["decision"]),
            },
        )
    for identity in sorted(replacement):
        selection_groups.append(
            {
                key: value
                for key, value in decision_groups[identity].items()
                if key != "group_sha256"
            },
        )

    final_candidates = sorted(
        [*inherited_candidates, *replacement_candidates],
        key=lambda item: (_group_key(item), int(item["take_index"])),
    )
    final_candidate_set = _build_candidate_set(
        candidates=final_candidates,
        models=base_manifest["models"],
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        plan=plan,
    )
    candidate_set_bytes = canonical_candidate_set_bytes(final_candidate_set)
    candidate_set_sha256 = hashlib.sha256(candidate_set_bytes).hexdigest()
    selection = validate_completion_selection(
        {
            "format_version": SELECTION_FORMAT_VERSION,
            "protocol": SELECTION_PROTOCOL,
            "plan_sha256": plan.plan_id,
            "anchor_selection_sha256": resolution.anchor_selection_sha256,
            "candidate_set_sha256": candidate_set_sha256,
            "groups": selection_groups,
        },
    )
    if len(selection["groups"]) != EXPECTED_SELECTED_COUNT:
        raise CompletionReleaseError("final selectionはexact 1,288 groupが必要です。")
    selection_bytes = canonical_completion_selection_bytes(selection)
    selection_sha256 = hashlib.sha256(selection_bytes).hexdigest()
    curations = [
        {
            "model": group["model"],
            "scenario": group["scenario"],
            "line": group["line"],
            "variant": group["variant"],
            "decision": "selected",
            "take_id": group["decision"]["take_id"],
            "curation_sha256": selection_sha256,
        }
        for group in selection["groups"]
    ]
    manifest = validate_manifest_v4(
        {
            "format_version": 4,
            "generated_at": max(
                [str(base_manifest["generated_at"])]
                + [str(run.manifest["generated_at"]) for run in resolution.runs],
            ),
            "candidate_set_sha256": candidate_set_sha256,
            "models": final_candidate_set["models"],
            "candidates": final_candidates,
            "curations": curations,
            "failures": [],
        },
    )
    manifest_bytes = canonical_json(manifest).encode("utf-8")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    provenance = _build_provenance(
        plan=plan,
        resolution=resolution,
        audit_partition=audit_partition,
        manifest_sha256=manifest_sha256,
        candidate_set_sha256=candidate_set_sha256,
        selection_sha256=selection_sha256,
    )
    provenance_bytes = canonical_json(provenance).encode("utf-8")
    _write_release(
        output_dir=output_dir,
        manifest_bytes=manifest_bytes,
        candidate_set_bytes=candidate_set_bytes,
        selection_bytes=selection_bytes,
        provenance_bytes=provenance_bytes,
    )
    validate_completion_release(
        release_dir=output_dir,
        artifacts_dir=artifacts_dir,
        source_audit_path=source_audit_path,
    )
    return CompletionReleaseSummary(
        output_dir=output_dir,
        manifest_sha256=manifest_sha256,
        candidate_set_sha256=candidate_set_sha256,
        selection_sha256=selection_sha256,
        candidate_count=len(final_candidates),
        selected_count=len(selection["groups"]),
        replacement_candidate_count=len(replacement_candidates),
    )


def validate_completion_release(
    *,
    release_dir: Path,
    source_audit_path: Path,
    artifacts_dir: Path | None = None,
) -> CompletionReleaseBundle:
    try:
        if not source_audit_path.is_absolute():
            raise CompletionReleaseError(
                "source auditは絶対pathの明示入力が必要です。",
            )
        root = release_dir.resolve(strict=True)
        if not root.is_dir():
            raise CompletionReleaseError("release_dirはdirectoryが必要です。")
        manifest, manifest_sha = _read_marker_bound(
            root / "manifest-v4.json",
            root / "manifest-v4.sha256",
            "manifest",
        )
        candidate_set, candidate_sha = _read_marker_bound(
            root / "candidate-set.json",
            root / "candidate-set.sha256",
            "candidate set",
        )
        selection_doc, selection_sha = _read_marker_bound(
            root / "selection.json",
            root / "selection.sha256",
            "selection",
        )
        provenance, _provenance_sha = _read_marker_bound(
            root / "release-provenance.json",
            root / "release-provenance.sha256",
            "release provenance",
        )
        normalized_manifest = validate_manifest_v4(manifest)
        normalized_selection = validate_completion_selection(selection_doc)
        audit = _load_source_audit(source_audit_path)
        if canonical_json(normalized_manifest).encode("utf-8") != (
            root / "manifest-v4.json"
        ).read_bytes():
            raise CompletionReleaseError("manifestはcanonical bytesが必要です。")
        if canonical_candidate_set_bytes(candidate_set) != (
            root / "candidate-set.json"
        ).read_bytes():
            raise CompletionReleaseError("candidate setはcanonical bytesが必要です。")
        if canonical_completion_selection_bytes(normalized_selection) != (
            root / "selection.json"
        ).read_bytes():
            raise CompletionReleaseError("selectionはcanonical bytesが必要です。")
        _validate_provenance_document(
            provenance,
            manifest_sha=manifest_sha,
            candidate_sha=candidate_sha,
            selection_sha=selection_sha,
        )
        if (
            provenance["plan_sha256"] != normalized_selection["plan_sha256"]
            or provenance["anchor_selection_sha256"]
            != normalized_selection["anchor_selection_sha256"]
        ):
            raise CompletionReleaseError(
                "release provenanceとselectionのplan/anchor bindingが不一致です。",
            )
        if (
            normalized_manifest["candidate_set_sha256"] != candidate_sha
            or normalized_selection["candidate_set_sha256"] != candidate_sha
            or len(normalized_selection["groups"]) != EXPECTED_SELECTED_COUNT
            or normalized_manifest["failures"]
        ):
            raise CompletionReleaseError("release digest/count/failure contractが不正です。")
        _validate_candidate_set_manifest_join(
            candidate_set=candidate_set,
            manifest=normalized_manifest,
        )
        _validate_selection_manifest_join(normalized_selection, normalized_manifest)
        _validate_provenance_selection_join(
            provenance=provenance,
            selection=normalized_selection,
            manifest=normalized_manifest,
        )
        if artifacts_dir is not None:
            _verify_local_replacement_audio(
                provenance=provenance,
                manifest=normalized_manifest,
                artifacts_dir=artifacts_dir,
            )
        return CompletionReleaseBundle(
            root=root,
            manifest=normalized_manifest,
            candidate_set=candidate_set,
            selection=normalized_selection,
            provenance=provenance,
        )
    except CompletionReleaseError:
        raise
    except (
        CurationError,
        TakeManifestError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise CompletionReleaseError(f"completion release bundleが不正です: {error}") from error


def _replacement_candidates(
    plan: CompletionPlan,
    resolution: CompletionSourceResolution,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for identity, run in sorted(resolution.group_sources.items()):
        group_candidates = [
            dict(candidate)
            for candidate in run.manifest["candidates"]
            if _group_key(candidate) == identity
        ]
        minimum = plan.policy_for_model(identity[0]).minimum_eligible_candidates
        if len(group_candidates) < minimum:
            raise CompletionReleaseError(
                "replacement groupはeligible candidateがmodel policyの"
                f"{minimum}件以上必要です: {identity}",
            )
        result.extend(group_candidates)
    return result


def _build_candidate_set(
    *,
    candidates: Sequence[Mapping[str, Any]],
    models: Sequence[Mapping[str, Any]],
    scenarios_dir: Path,
    voices_dir: Path,
    plan: CompletionPlan,
) -> dict[str, Any]:
    scenario_sha, lines = _load_target_lines(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        targets={(target.scenario, target.line) for target in plan.targets}
        | {
            (_group_key(candidate)[1], _group_key(candidate)[2])
            for candidate in candidates
        },
    )
    by_id = {str(model["id"]): dict(model) for model in models}
    used_models = sorted({_group_key(candidate)[0] for candidate in candidates})
    return build_candidate_set(
        scenario_sha256=scenario_sha,
        lines=lines,
        models=[by_id[model] for model in used_models],
        candidates=[dict(candidate) for candidate in candidates],
        failures=[],
    )


def _validate_decision_against_sources(
    *,
    decision_groups: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
    plan: CompletionPlan,
    resolution: CompletionSourceResolution,
    candidate_set: Mapping[str, Any],
) -> None:
    lines = {
        (str(line["scenario"]), str(line["line"])): line
        for line in candidate_set["lines"]
    }
    for identity, group in decision_groups.items():
        minimum = plan.policy_for_model(identity[0]).minimum_eligible_candidates
        if group["authority"]["minimum_eligible_candidates"] != minimum:
            raise CompletionReleaseError(
                f"decision minimumがmodel policyと不一致です: {identity}",
            )
        if group["role_epoch_sha256"] != resolution.expected_role_epochs[identity]:
            raise CompletionReleaseError(
                f"旧role epochのdecisionは再生できません: {identity}",
            )
        source = resolution.group_sources[identity]
        line = lines.get((identity[1], identity[2]))
        if line is None:
            raise CompletionReleaseError(
                f"decision groupのcandidate catalog lineがありません: {identity}",
            )
        manifest_candidates = {
            candidate["take_id"]: candidate
            for candidate in source.manifest["candidates"]
            if _group_key(candidate) == identity
        }
        expected_group_sha256 = _decision_group_sha256(
            identity=identity,
            line=line,
            role_epoch_sha256=resolution.expected_role_epochs[identity],
            source_run_id=source.run_id,
            minimum_eligible_candidates=minimum,
            candidates=[
                candidate
                for candidate in candidate_set["candidates"]
                if _group_key(candidate) == identity
            ],
        )
        if group["group_sha256"] != expected_group_sha256:
            raise CompletionReleaseError(
                f"decision group_sha256がcandidate catalogと不一致です: {identity}",
            )
        if set(manifest_candidates) != {
            candidate["take_id"] for candidate in group["candidates"]
        }:
            raise CompletionReleaseError(
                f"decision candidatesが最終有効sourceとexact一致しません: {identity}",
            )
        for candidate in group["candidates"]:
            manifest_candidate = manifest_candidates[candidate["take_id"]]
            if (
                candidate["path"] != manifest_candidate["path"]
                or candidate["audio_sha256"] != manifest_candidate["sha256"]
                or candidate["gate"] != manifest_candidate["gate"]
            ):
                raise CompletionReleaseError(
                    f"decision candidate path/SHA/gateがsourceと不一致です: {identity}",
                )


def _decision_group_sha256(
    *,
    identity: tuple[str, str, str, str],
    line: Mapping[str, Any],
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
            for candidate in candidates
        ],
    }
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def _load_source_audit(path: Path) -> dict[str, Any]:
    raw = _read_bytes(path, "source audit")
    if hashlib.sha256(raw).hexdigest() != SOURCE_AUDIT_SHA256:
        raise CompletionReleaseError("source audit SHAが固定snapshotと一致しません。")
    value = _decode_json(raw, path)
    if not isinstance(value, dict):
        raise CompletionReleaseError("source audit rootはobjectが必要です。")
    return value


def _validate_audit_partition(
    *,
    audit: Mapping[str, Any],
    replacement: set[tuple[str, str, str, str]],
    inherited: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    receipts = audit.get("conditioning_receipts")
    if not isinstance(receipts, list):
        raise CompletionReleaseError("source audit conditioning_receiptsが不正です。")
    expected_identities = replacement | set(inherited)
    seen: set[tuple[str, str, str, str]] = set()
    matched: list[dict[str, Any]] = []
    replacement_status_counts = {
        "mismatch": 0,
        "unverifiable": 0,
        "match": 0,
        "failure": 0,
    }
    for receipt in receipts:
        identity = (
            str(receipt["model"]),
            str(receipt["scenario"]),
            str(receipt["line"]),
            "dry",
        )
        if identity in seen:
            raise CompletionReleaseError(
                f"source audit identityが重複しています: {identity}",
            )
        seen.add(identity)
        status = receipt["published_comparison"]["status"]
        if identity in replacement:
            if status not in replacement_status_counts:
                raise CompletionReleaseError(
                    f"source audit replacement statusが不正です: {identity}",
                )
            replacement_status_counts[status] += 1
            continue
        if identity not in inherited or status != "match":
            raise CompletionReleaseError(
                f"source audit inheritedはmatchが必要です: {identity}",
            )
        provenance = receipt["published_provenance"]
        matched.append(
            {
                "model": identity[0],
                "scenario": identity[1],
                "line": identity[2],
                "variant": identity[3],
                "take_id": str(provenance["take_id"]),
                "reason": str(receipt["published_comparison"]["reason"]),
            },
        )
    if seen != expected_identities or len(seen) != EXPECTED_SELECTED_COUNT:
        raise CompletionReleaseError(
            "source audit identity集合が597 replacement + 691 inheritedと不一致です。",
        )
    if replacement_status_counts != {
        "mismatch": 357,
        "unverifiable": 145,
        "match": 89,
        "failure": 6,
    } or len(matched) != EXPECTED_MATCHED_COUNT:
        raise CompletionReleaseError(
            "source audit status分布はreplacement 357/145/89/6、"
            "inherited match 691が必要です。",
        )
    for item in matched:
        identity = (item["model"], item["scenario"], item["line"], item["variant"])
        if inherited[identity]["decision"]["take_id"] != item["take_id"]:
            raise CompletionReleaseError(
                f"source audit inherited take_idがbase selectionと不一致です: {identity}",
            )
    return {
        "source_audit_sha256": SOURCE_AUDIT_SHA256,
        "matched_candidate_count": len(matched),
        "inherited_identity_unverifiable": [],
    }


def _build_provenance(
    *,
    plan: CompletionPlan,
    resolution: CompletionSourceResolution,
    audit_partition: Mapping[str, Any],
    manifest_sha256: str,
    candidate_set_sha256: str,
    selection_sha256: str,
) -> dict[str, Any]:
    source_runs: list[dict[str, Any]] = []
    for run in resolution.runs:
        candidates: list[dict[str, Any]] = []
        effective_groups = {
            identity
            for identity, source in resolution.group_sources.items()
            if source is run
        }
        for candidate in run.manifest["candidates"]:
            if _group_key(candidate) not in effective_groups:
                continue
            local_path = _local_audio_path(run.root, candidate)
            candidates.append(
                {
                    "take_id": candidate["take_id"],
                    "path": candidate["path"],
                    "audio_sha256": candidate["sha256"],
                    "run_relative_path": local_path.relative_to(run.root).as_posix(),
                    "size_bytes": local_path.stat().st_size,
                },
            )
        source_runs.append(
            {
                "model": run.model,
                "run_id": run.run_id,
                "kind": run.kind,
                "supersedes_run_id": run.supersedes_run_id,
                "ledger_sha256": run.ledger_sha256,
                "qc_report_sha256": run.qc_report_sha256,
                "manifest_sha256": run.manifest_sha256,
                "candidate_set_sha256": run.candidate_set_sha256,
                "effective_groups": [
                    {
                        "model": identity[0],
                        "scenario": identity[1],
                        "line": identity[2],
                        "variant": identity[3],
                        "role_epoch_sha256": resolution.expected_role_epochs[identity],
                    }
                    for identity in sorted(effective_groups)
                ],
                "candidates": sorted(candidates, key=lambda item: item["take_id"]),
            },
        )
    return {
        "format_version": 1,
        "protocol": RELEASE_PROTOCOL,
        "plan_sha256": plan.plan_id,
        "anchor_selection_sha256": resolution.anchor_selection_sha256,
        "manifest_sha256": manifest_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "selection_sha256": selection_sha256,
        "counts": {
            "replacement_groups": EXPECTED_REPLACEMENT_COUNT,
            "inherited_groups": EXPECTED_INHERITED_COUNT,
            "selected_groups": EXPECTED_SELECTED_COUNT,
            "failures": 0,
        },
        "base": {
            "manifest_sha256": plan.base_manifest_sha256,
            "git_blob": plan.base_manifest_git_blob,
            "candidate_set_sha256": plan.base_candidate_set_sha256,
            "selection_sha256": plan.base_selection_sha256,
            **dict(audit_partition),
        },
        "source_runs": sorted(source_runs, key=lambda item: item["run_id"]),
    }


def _validate_provenance_document(
    value: Any,
    *,
    manifest_sha: str,
    candidate_sha: str,
    selection_sha: str,
) -> None:
    if not isinstance(value, dict) or set(value) != {
        "format_version",
        "protocol",
        "plan_sha256",
        "anchor_selection_sha256",
        "manifest_sha256",
        "candidate_set_sha256",
        "selection_sha256",
        "counts",
        "base",
        "source_runs",
    }:
        raise CompletionReleaseError("release provenance exact rootが不正です。")
    if (
        value["format_version"] != 1
        or value["protocol"] != RELEASE_PROTOCOL
        or value["plan_sha256"] != PRODUCTION_PLAN_SHA256
        or value["manifest_sha256"] != manifest_sha
        or value["candidate_set_sha256"] != candidate_sha
        or value["selection_sha256"] != selection_sha
        or value["counts"]
        != {
            "replacement_groups": 597,
            "inherited_groups": 691,
            "selected_groups": 1288,
            "failures": 0,
        }
    ):
        raise CompletionReleaseError("release provenance digest/countが不正です。")
    base = value["base"]
    if not isinstance(base, dict) or set(base) != {
        "manifest_sha256",
        "git_blob",
        "candidate_set_sha256",
        "selection_sha256",
        "source_audit_sha256",
        "matched_candidate_count",
        "inherited_identity_unverifiable",
    }:
        raise CompletionReleaseError("release provenance base exact contractが不正です。")
    unverifiable = base.get("inherited_identity_unverifiable")
    expected_unverifiable: list[dict[str, Any]] = []
    if (
        base.get("manifest_sha256") != BASE_MANIFEST_SHA256
        or base.get("git_blob") != BASE_MANIFEST_GIT_BLOB
        or base.get("candidate_set_sha256") != BASE_CANDIDATE_SET_SHA256
        or base.get("selection_sha256") != BASE_SELECTION_SHA256
        or base.get("source_audit_sha256") != SOURCE_AUDIT_SHA256
        or base.get("matched_candidate_count") != EXPECTED_MATCHED_COUNT
        or not isinstance(unverifiable, list)
        or unverifiable != expected_unverifiable
    ):
        raise CompletionReleaseError(
            "release provenanceは691 matchedと0 unverifiableが必要です。",
        )
    runs = value["source_runs"]
    if not isinstance(runs, list):
        raise CompletionReleaseError("release provenance source_runsが不正です。")
    run_ids: set[str] = set()
    effective_groups: set[tuple[str, str, str, str]] = set()
    primary_models: set[str] = set()
    primary_count = 0
    runs_by_id: dict[str, Mapping[str, Any]] = {}
    for run in runs:
        if not isinstance(run, dict) or set(run) != {
            "model",
            "run_id",
            "kind",
            "supersedes_run_id",
            "ledger_sha256",
            "qc_report_sha256",
            "manifest_sha256",
            "candidate_set_sha256",
            "effective_groups",
            "candidates",
        }:
            raise CompletionReleaseError("source run provenance exact contractが不正です。")
        run_id = run["run_id"]
        if not isinstance(run_id, str) or run_id in run_ids:
            raise CompletionReleaseError("source run IDが不正または重複しています。")
        run_ids.add(run_id)
        runs_by_id[run_id] = run
        kind = run["kind"]
        if kind == "primary":
            if run["model"] not in PRIMARY_MODELS or run["supersedes_run_id"] is not None:
                raise CompletionReleaseError("primary source run provenanceが不正です。")
            primary_models.add(run["model"])
            primary_count += 1
        elif kind == "topup":
            if not isinstance(run["supersedes_run_id"], str):
                raise CompletionReleaseError("topup source runにsupersedesが必要です。")
        else:
            raise CompletionReleaseError("source run kindが不正です。")
        for field in (
            "ledger_sha256",
            "qc_report_sha256",
            "manifest_sha256",
            "candidate_set_sha256",
        ):
            _require_sha256(run[field], f"source run.{field}")
        groups = run["effective_groups"]
        candidates = run["candidates"]
        if not isinstance(groups, list) or not isinstance(candidates, list):
            raise CompletionReleaseError("source run groups/candidatesが不正です。")
        for group in groups:
            if not isinstance(group, dict) or set(group) != {
                "model",
                "scenario",
                "line",
                "variant",
                "role_epoch_sha256",
            }:
                raise CompletionReleaseError("effective group exact contractが不正です。")
            identity = _group_key(group)
            _require_sha256(group["role_epoch_sha256"], "effective role epoch")
            if identity in effective_groups:
                raise CompletionReleaseError("effective group sourceが一意ではありません。")
            effective_groups.add(identity)
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) != {
                "take_id",
                "path",
                "audio_sha256",
                "run_relative_path",
                "size_bytes",
            }:
                raise CompletionReleaseError("source candidate provenanceが不正です。")
            _require_sha256(candidate["take_id"], "source candidate.take_id")
            _require_sha256(candidate["audio_sha256"], "source candidate.audio_sha256")
            if (
                not isinstance(candidate["size_bytes"], int)
                or isinstance(candidate["size_bytes"], bool)
                or candidate["size_bytes"] < 1
            ):
                raise CompletionReleaseError("source candidate.size_bytesが不正です。")
    if (
        primary_models != PRIMARY_MODELS
        or primary_count != len(PRIMARY_MODELS)
        or len(effective_groups) != 597
    ):
        raise CompletionReleaseError(
            "source provenanceは8 primaryとexact 597 effective groupが必要です。",
        )
    for run in runs:
        if run["kind"] != "topup":
            continue
        predecessor = runs_by_id.get(run["supersedes_run_id"])
        if predecessor is None or predecessor["model"] != run["model"]:
            raise CompletionReleaseError(
                "topup provenanceのsupersedes runが同一model sourceにありません。",
            )
def _validate_candidate_set_manifest_join(
    *,
    candidate_set: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    for field in ("models", "candidates", "failures"):
        if candidate_set[field] != manifest[field]:
            raise CompletionReleaseError(
                f"candidate set {field}がmanifestとexact一致しません。",
            )


def _validate_selection_manifest_join(
    selection: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    candidates = {
        candidate["take_id"]: candidate for candidate in manifest["candidates"]
    }
    if len(candidates) != len(manifest["candidates"]):
        raise CompletionReleaseError("manifest take_idが重複しています。")
    groups = {_group_key(group): group for group in selection["groups"]}
    if len(groups) != EXPECTED_SELECTED_COUNT:
        raise CompletionReleaseError("selection groupが重複しています。")
    for group in selection["groups"]:
        selected = candidates.get(group["decision"]["take_id"])
        if selected is None or _group_key(selected) != _group_key(group):
            raise CompletionReleaseError("selection selected takeがmanifestにありません。")
        for member in group["candidates"]:
            candidate = candidates.get(member["take_id"])
            if (
                candidate is None
                or member["path"] != candidate["path"]
                or member["audio_sha256"] != candidate["sha256"]
            ):
                raise CompletionReleaseError(
                    "selection candidate path/SHAがmanifestと不一致です。",
                )


def _validate_provenance_selection_join(
    *,
    provenance: Mapping[str, Any],
    selection: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    effective: dict[tuple[str, str, str, str], str] = {}
    provenance_candidates: dict[str, Mapping[str, Any]] = {}
    for run in provenance["source_runs"]:
        run_groups = {_group_key(group) for group in run["effective_groups"]}
        for identity in run_groups:
            effective[identity] = run["run_id"]
        for candidate in run["candidates"]:
            provenance_candidates[candidate["take_id"]] = candidate
    replacement_groups = {
        _group_key(group)
        for group in selection["groups"]
        if group["authority"]["type"] == "best_available"
    }
    if set(effective) != replacement_groups or len(replacement_groups) != 597:
        raise CompletionReleaseError(
            "provenance effective sourceとselection replacementがexact一致しません。",
        )
    manifest_replacements = {
        candidate["take_id"]: candidate
        for candidate in manifest["candidates"]
        if _group_key(candidate) in replacement_groups
    }
    if set(provenance_candidates) != set(manifest_replacements):
        raise CompletionReleaseError(
            "provenance candidateとreplacement manifest candidateがexact一致しません。",
        )
    for take_id, item in provenance_candidates.items():
        candidate = manifest_replacements[take_id]
        identity = _group_key(candidate)
        if (
            identity not in effective
            or item["path"] != candidate["path"]
            or item["audio_sha256"] != candidate["sha256"]
        ):
            raise CompletionReleaseError(
                "provenance candidate path/SHA/groupがmanifestと不一致です。",
            )


def _verify_local_replacement_audio(
    *,
    provenance: Mapping[str, Any],
    manifest: Mapping[str, Any],
    artifacts_dir: Path,
) -> None:
    manifest_by_take = {
        candidate["take_id"]: candidate for candidate in manifest["candidates"]
    }
    takes_root = artifacts_dir.resolve() / "takes"
    for run in provenance["source_runs"]:
        run_root = (takes_root / run["run_id"]).resolve(strict=True)
        if run_root.parent != takes_root:
            raise CompletionReleaseError("source run pathがtakes root直下ではありません。")
        for field, filename in (
            ("ledger_sha256", "ledger.json"),
            ("qc_report_sha256", "qc-report.json"),
            ("manifest_sha256", "manifest-v4.json"),
            ("candidate_set_sha256", "candidate-set.json"),
        ):
            actual_digest = hashlib.sha256(
                run_root.joinpath(filename).read_bytes(),
            ).hexdigest()
            if actual_digest != run[field]:
                raise CompletionReleaseError(
                    f"source run {filename} digestがrelease provenanceから漂移しました。",
                )
        for item in run["candidates"]:
            path = (run_root / Path(item["run_relative_path"])).resolve(strict=True)
            if not path.is_relative_to(run_root) or not path.is_file():
                raise CompletionReleaseError("replacement audio pathがrun外です。")
            candidate = manifest_by_take[item["take_id"]]
            payload = path.read_bytes()
            if (
                len(payload) != item["size_bytes"]
                or hashlib.sha256(payload).hexdigest() != item["audio_sha256"]
                or candidate["path"] != item["path"]
                or candidate["sha256"] != item["audio_sha256"]
            ):
                raise CompletionReleaseError("replacement audio provenanceが漂移しました。")


def _write_release(
    *,
    output_dir: Path,
    manifest_bytes: bytes,
    candidate_set_bytes: bytes,
    selection_bytes: bytes,
    provenance_bytes: bytes,
) -> None:
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.",
            dir=output_dir.resolve().parent,
        ),
    )
    try:
        payloads = {
            "manifest-v4.json": manifest_bytes,
            "candidate-set.json": candidate_set_bytes,
            "selection.json": selection_bytes,
            "release-provenance.json": provenance_bytes,
        }
        for name, payload in payloads.items():
            (temporary / name).write_bytes(payload)
            (temporary / name.replace(".json", ".sha256")).write_text(
                hashlib.sha256(payload).hexdigest(),
                encoding="ascii",
            )
        temporary.replace(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _read_marker_bound(
    path: Path,
    marker: Path,
    label: str,
) -> tuple[Any, str]:
    raw = _read_bytes(path, label)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        marker_value = marker.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise CompletionReleaseError(f"{label} markerを読めません: {marker}") from error
    if marker_value != digest:
        raise CompletionReleaseError(f"{label} markerがbytesと不一致です。")
    value = _decode_json(raw, path)
    if raw != canonical_json(value).encode("utf-8"):
        raise CompletionReleaseError(f"{label}はcanonical bytesが必要です。")
    return value, digest


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise CompletionReleaseError(f"{label}を読めません: {path}") from error


def _read_json(path: Path, label: str) -> Any:
    return _decode_json(_read_bytes(path, label), path)


def _decode_json(raw: bytes, path: Path) -> Any:
    try:
        return json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CompletionReleaseError(f"JSONを読めません: {path}: {error}") from error


def _group_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value["model"]),
        str(value["scenario"]),
        str(value["line"]),
        str(value["variant"]),
    )


def _inherited_role_epoch(
    plan: CompletionPlan,
    identity: tuple[str, str, str, str],
) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "protocol": "inherited-role-epoch-v1",
                "plan_sha256": plan.plan_id,
                "base_selection_sha256": plan.base_selection_sha256,
                "model": identity[0],
                "scenario": identity[1],
                "line": identity[2],
                "variant": identity[3],
            },
        ).encode("utf-8"),
    ).hexdigest()


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CompletionReleaseError(f"{field}は完全な小文字SHA-256が必要です。")
    return value
