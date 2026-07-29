from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from gaya_pipeline.adapters import UnknownAdapterError, get_model_profile
from gaya_pipeline.curation import (
    CurationError,
    _validate_manifest_against_terminal_ledger,
    build_candidate_set,
    canonical_candidate_set_bytes,
    load_authoritative_candidate_lines,
    validate_curation,
    validate_snapshot_bundle,
)
from gaya_pipeline.manifest import ManifestError, load_manifest
from gaya_pipeline.qc_report import QCReportError, validate_qc_report
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.take_ledger import (
    TERMINAL_STATUSES,
    TakeLedgerError,
    read_ledger,
)
from gaya_pipeline.take_manifest_v4 import TakeManifestError, validate_manifest_v4
from gaya_pipeline.take_sidecar import TakeSidecarError, validate_take_sidecar
from gaya_pipeline.validation import validate_scenario_ids


class BaselineError(RuntimeError):
    pass


@dataclass(frozen=True)
class BaselinePlanSummary:
    plan_path: Path
    plan_sha256: str
    group_count: int
    model_count: int
    excluded_failure_count: int


@dataclass(frozen=True)
class BaselineAssembleSummary:
    bundle_dir: Path
    candidate_set_sha256: str
    baseline_reference_sha256: str
    group_count: int
    candidate_count: int
    failure_count: int


@dataclass(frozen=True)
class BaselineFinalizeSummary:
    output_dir: Path
    decision_sha256: str
    release_manifest_sha256: str
    audit_sha256: str
    candidate_zero_count: int
    selected_count: int
    skipped_count: int


PLAN_FORMAT_VERSION = 1
PLAN_VERSION = "baseline-plan-v1"
REFERENCE_FORMAT_VERSION = 1
CURATION_FORMAT_VERSION = 1
CURATION_VERSION = "baseline-curation-v1"
PROVENANCE_FORMAT_VERSION = 1
AUDIT_FORMAT_VERSION = 1
BUNDLE_INVENTORY_FORMAT_VERSION = 1
EXPECTED_GROUP_COUNT = 381
EXPECTED_MODEL_COUNT = 7
GROUP_KEYS = ("model", "scenario", "line", "variant")
BUNDLE_INVENTORY_PATH = "baseline-bundle-inventory.json"
BUNDLE_INVENTORY_MARKER_PATH = "baseline-bundle-inventory.sha256"
PLAN_ROOT_FIELDS = {
    "format_version",
    "plan_version",
    "source",
    "models",
    "groups",
    "excluded_failures",
}
PLAN_SOURCE_FIELDS = {
    "manifest_path",
    "manifest_sha256",
    "scenario_sha256",
}
PLAN_GROUP_FIELDS = {*GROUP_KEYS, "legacy"}
PLAN_LEGACY_FIELDS = {"path", "sha256"}
FAILURE_FIELDS = {*GROUP_KEYS, "reason"}
REFERENCE_ROOT_FIELDS = {
    "format_version",
    "source_manifest_sha256",
    "candidate_set_sha256",
    "references",
}
REFERENCE_FIELDS = {
    *GROUP_KEYS,
    "public_path",
    "legacy_sha256",
    "local_path",
    "candidate_sha256",
    "comparison",
}
HEX = frozenset("0123456789abcdef")


def plan_baseline(
    *,
    manifest_path: Path,
    output_path: Path,
) -> BaselinePlanSummary:
    if output_path.exists():
        raise BaselineError(f"baseline plan output は既存 path を拒否します: {output_path}")
    if not manifest_path.is_file():
        raise BaselineError(f"source manifest がありません: {manifest_path}")
    try:
        raw = manifest_path.read_bytes()
        manifest = load_manifest(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ManifestError) as error:
        raise BaselineError(f"source manifest v3 を読み込めません: {error}") from error

    repository_root = _repository_root_for_manifest(manifest_path)
    manifest_relative = manifest_path.resolve().relative_to(repository_root).as_posix()
    groups = [
        {
            **{key: clip[key] for key in GROUP_KEYS},
            "legacy": {
                "path": clip["path"],
                "sha256": clip["sha256"],
            },
        }
        for clip in manifest["clips"]
    ]
    groups.sort(key=_group_key)
    failures = [dict(failure) for failure in manifest["failures"]]
    failures.sort(key=_group_key)
    models = _authoritative_model_entries(
        [str(model["id"]) for model in manifest["models"]],
    )
    scenario_sha256, _lines = _authoritative_plan_lines(
        scenarios_dir=repository_root / "scenarios",
        groups=groups,
    )
    document = validate_baseline_plan(
        {
            "format_version": PLAN_FORMAT_VERSION,
            "plan_version": PLAN_VERSION,
            "source": {
                "manifest_path": manifest_relative,
                "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                "scenario_sha256": scenario_sha256,
            },
            "models": models,
            "groups": groups,
            "excluded_failures": failures,
        },
    )
    payload = canonical_json(document).encode("utf-8")
    _write_new_file(output_path, payload)
    return BaselinePlanSummary(
        plan_path=output_path,
        plan_sha256=hashlib.sha256(payload).hexdigest(),
        group_count=len(groups),
        model_count=len(models),
        excluded_failure_count=len(failures),
    )


def validate_baseline_plan(document: Any) -> dict[str, Any]:
    root = _exact(document, PLAN_ROOT_FIELDS, "baseline plan")
    if root["format_version"] != PLAN_FORMAT_VERSION:
        raise BaselineError("baseline plan format_version は 1 が必要です。")
    if root["plan_version"] != PLAN_VERSION:
        raise BaselineError("baseline plan plan_version は baseline-plan-v1 が必要です。")
    source = _exact(root["source"], PLAN_SOURCE_FIELDS, "baseline plan source")
    manifest_path = _relative_path(
        source["manifest_path"],
        "baseline plan source.manifest_path",
    )
    manifest_sha256 = _sha(
        source["manifest_sha256"],
        "baseline plan source.manifest_sha256",
    )
    scenario_sha256 = _sha(
        source["scenario_sha256"],
        "baseline plan source.scenario_sha256",
    )
    if not isinstance(root["models"], list):
        raise BaselineError("baseline plan models は配列が必要です。")
    models = [_model(value, f"baseline plan models[{index}]") for index, value in enumerate(root["models"])]
    if len(models) != EXPECTED_MODEL_COUNT:
        raise BaselineError(
            f"baseline plan は model {EXPECTED_MODEL_COUNT} 件が必要です。",
        )
    if models != sorted(models, key=lambda model: model["id"]):
        raise BaselineError("baseline plan models は model id 順が必要です。")
    model_ids = [model["id"] for model in models]
    if len(model_ids) != len(set(model_ids)):
        raise BaselineError("baseline plan model id が重複しています。")

    if not isinstance(root["groups"], list):
        raise BaselineError("baseline plan groups は配列が必要です。")
    groups: list[dict[str, Any]] = []
    seen_groups: set[tuple[str, str, str, str]] = set()
    for index, value in enumerate(root["groups"]):
        field = f"baseline plan groups[{index}]"
        group = _exact(value, PLAN_GROUP_FIELDS, field)
        normalized = {
            **{
                key: _path_segment(group[key], f"{field}.{key}")
                for key in GROUP_KEYS
            },
            "legacy": {
                "path": _relative_path(
                    _exact(group["legacy"], PLAN_LEGACY_FIELDS, f"{field}.legacy")[
                        "path"
                    ],
                    f"{field}.legacy.path",
                ),
                "sha256": _sha(
                    group["legacy"]["sha256"],
                    f"{field}.legacy.sha256",
                ),
            },
        }
        identity = _group_key(normalized)
        if identity in seen_groups:
            raise BaselineError("baseline plan group が重複しています。")
        if identity[0] not in set(model_ids):
            raise BaselineError("baseline plan group が未知の model を参照しています。")
        seen_groups.add(identity)
        groups.append(normalized)
    if len(groups) != EXPECTED_GROUP_COUNT:
        raise BaselineError(
            f"baseline plan は公開 clip group {EXPECTED_GROUP_COUNT} 件が必要です。",
        )
    if groups != sorted(groups, key=_group_key):
        raise BaselineError("baseline plan groups は group 順が必要です。")

    if not isinstance(root["excluded_failures"], list):
        raise BaselineError("baseline plan excluded_failures は配列が必要です。")
    failures: list[dict[str, Any]] = []
    seen_failures: set[tuple[str, str, str, str]] = set()
    for index, value in enumerate(root["excluded_failures"]):
        field = f"baseline plan excluded_failures[{index}]"
        failure = _exact(value, FAILURE_FIELDS, field)
        normalized_failure = {
            **{
                key: _path_segment(failure[key], f"{field}.{key}")
                for key in GROUP_KEYS
            },
            "reason": _text(failure["reason"], f"{field}.reason"),
        }
        identity = _group_key(normalized_failure)
        if identity in seen_groups or identity in seen_failures:
            raise BaselineError(
                "baseline plan group と excluded failure が重複または競合しています。",
            )
        if identity[0] not in set(model_ids):
            raise BaselineError("excluded failure が未知の model を参照しています。")
        seen_failures.add(identity)
        failures.append(normalized_failure)
    if failures != sorted(failures, key=_group_key):
        raise BaselineError("excluded_failures は group 順が必要です。")

    normalized = {
        "format_version": PLAN_FORMAT_VERSION,
        "plan_version": PLAN_VERSION,
        "source": {
            "manifest_path": manifest_path,
            "manifest_sha256": manifest_sha256,
            "scenario_sha256": scenario_sha256,
        },
        "models": models,
        "groups": groups,
        "excluded_failures": failures,
    }
    canonical_json(normalized)
    return normalized


def load_baseline_plan(
    plan_path: Path,
    *,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    raw, document = _read_canonical_json(plan_path, "baseline plan")
    plan = validate_baseline_plan(document)
    if raw != canonical_json(plan).encode("utf-8"):
        raise BaselineError("baseline plan は canonical bytes が必要です。")
    if repository_root is not None:
        _validate_plan_sources(plan, repository_root=repository_root)
    return plan


def generation_selection(
    *,
    plan_path: Path,
    model_id: str,
    scenarios_dir: Path,
) -> set[tuple[str, str]]:
    repository_root = scenarios_dir.resolve().parent
    plan = load_baseline_plan(plan_path, repository_root=repository_root)
    expected_model = next(
        (model for model in plan["models"] if model["id"] == model_id),
        None,
    )
    if expected_model is None:
        raise BaselineError(f"baseline plan に model がありません: {model_id}")
    selected = {
        (group["scenario"], group["line"])
        for group in plan["groups"]
        if group["model"] == model_id
    }
    if not selected:
        raise BaselineError(f"baseline plan に generation group がありません: {model_id}")
    return selected


def assemble_baseline(
    *,
    plan_path: Path,
    run_ids: Sequence[str],
    output_dir: Path,
    artifacts_dir: Path,
    legacy_root: Path,
    scenarios_dir: Path,
) -> BaselineAssembleSummary:
    if output_dir.exists():
        raise BaselineError(f"baseline bundle output は既存 path を拒否します: {output_dir}")
    repository_root = scenarios_dir.resolve().parent
    plan = load_baseline_plan(plan_path, repository_root=repository_root)
    if len(run_ids) != EXPECTED_MODEL_COUNT or len(set(run_ids)) != len(run_ids):
        raise BaselineError(
            f"assemble は重複しない run id {EXPECTED_MODEL_COUNT} 件が必要です。",
        )
    for run_id in run_ids:
        _path_segment(run_id, "run_id")

    plan_groups_by_model = _plan_groups_by_model(plan)
    run_material: dict[str, tuple[Path, dict[str, Any], Any]] = {}
    all_candidates: list[dict[str, Any]] = []
    all_failures: list[dict[str, Any]] = []
    provenance_runs: list[dict[str, Any]] = []
    artifacts_root = artifacts_dir.resolve()
    takes_root = (artifacts_root / "takes").resolve()
    for run_id in run_ids:
        run_root = (takes_root / run_id).resolve()
        if not run_root.is_relative_to(takes_root) or run_root.parent != takes_root:
            raise BaselineError("run root が artifacts/takes 直下ではありません。")
        ledger, bundle = _validate_source_run(
            run_id=run_id,
            run_root=run_root,
            scenarios_dir=scenarios_dir,
            qc_ledger_path=run_root / "ledger.json",
        )
        model_id = ledger["source"]["model"]
        if model_id in run_material:
            raise BaselineError(f"同じ model の run が重複しています: {model_id}")
        _validate_model_run_selection(
            plan=plan,
            ledger=ledger,
            run_models=bundle.manifest["models"],
        )
        run_material[model_id] = (run_root, ledger, bundle)
        all_candidates.extend(dict(candidate) for candidate in bundle.manifest["candidates"])
        all_failures.extend(dict(failure) for failure in bundle.manifest["failures"])
        provenance_runs.append(
            {
                "model": model_id,
                "run_id": run_id,
                "ledger_path": (run_root / "ledger.json").as_posix(),
                "ledger_sha256": _file_sha256(run_root / "ledger.json"),
                "qc_report_sha256": _file_sha256(run_root / "qc-report.json"),
                "manifest_sha256": _file_sha256(run_root / "manifest-v4.json"),
                "candidate_set_sha256": bundle.candidate_set_sha256,
            },
        )
    if set(run_material) != set(plan_groups_by_model):
        missing = sorted(set(plan_groups_by_model) - set(run_material))
        raise BaselineError(f"assemble の model run が不足しています: {missing}")

    all_candidates.sort(key=lambda candidate: (_group_key(candidate), candidate["take_index"]))
    all_failures.sort(key=_group_key)
    _validate_aggregate_plan_coverage(
        plan=plan,
        candidates=all_candidates,
        failures=all_failures,
    )

    scenario_sha256, lines = _authoritative_plan_lines(
        scenarios_dir=scenarios_dir,
        groups=plan["groups"],
    )
    if scenario_sha256 != plan["source"]["scenario_sha256"]:
        raise BaselineError("aggregate current scenario SHA が plan と一致しません。")
    try:
        candidate_set = build_candidate_set(
            scenario_sha256=scenario_sha256,
            lines=lines,
            models=plan["models"],
            candidates=all_candidates,
            failures=all_failures,
        )
        candidate_set_bytes = canonical_candidate_set_bytes(candidate_set)
        candidate_set_sha256 = hashlib.sha256(candidate_set_bytes).hexdigest()
        manifest = validate_manifest_v4(
            {
                "format_version": 4,
                "generated_at": "baseline-assemble-v1",
                "candidate_set_sha256": candidate_set_sha256,
                "models": plan["models"],
                "candidates": all_candidates,
                "curations": [],
                "failures": all_failures,
            },
        )
    except (CurationError, TakeManifestError, TakeLedgerError) as error:
        raise BaselineError(f"aggregate v4 contract を構築できません: {error}") from error

    candidates_by_group = {_group_key(candidate): candidate for candidate in all_candidates}
    if len(candidates_by_group) != len(all_candidates):
        raise BaselineError("takes=1 baseline で同じ group に複数 candidate があります。")
    references = []
    legacy_root = legacy_root.resolve()
    for group in plan["groups"]:
        identity = _group_key(group)
        candidate = candidates_by_group.get(identity)
        candidate_sha = candidate["sha256"] if candidate is not None else None
        comparison = (
            "no_candidate"
            if candidate_sha is None
            else "identical"
            if candidate_sha == group["legacy"]["sha256"]
            else "different"
        )
        references.append(
            {
                **{key: group[key] for key in GROUP_KEYS},
                "public_path": group["legacy"]["path"],
                "legacy_sha256": group["legacy"]["sha256"],
                "local_path": (
                    "reference/"
                    f"{group['model']}/{group['scenario']}/{group['line']}/"
                    f"{group['variant']}.opus"
                ),
                "candidate_sha256": candidate_sha,
                "comparison": comparison,
            },
        )
    reference_document = validate_baseline_reference(
        {
            "format_version": REFERENCE_FORMAT_VERSION,
            "source_manifest_sha256": plan["source"]["manifest_sha256"],
            "candidate_set_sha256": candidate_set_sha256,
            "references": references,
        },
        expected_plan=plan,
        candidates=all_candidates,
    )
    reference_bytes = canonical_json(reference_document).encode("utf-8")
    reference_sha256 = hashlib.sha256(reference_bytes).hexdigest()
    plan_bytes = canonical_json(plan).encode("utf-8")
    provenance = {
        "format_version": PROVENANCE_FORMAT_VERSION,
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "runs": sorted(provenance_runs, key=lambda run: run["model"]),
    }
    provenance = _validate_provenance_document(provenance, plan=plan)
    provenance_bytes = canonical_json(provenance).encode("utf-8")
    provenance_by_model = {run["model"]: run for run in provenance["runs"]}

    stage = _new_output_stage(output_dir)
    try:
        _write_file(stage / "baseline-plan.json", plan_bytes)
        _write_file(
            stage / "baseline-plan.sha256",
            hashlib.sha256(plan_bytes).hexdigest().encode("ascii"),
        )
        _write_file(stage / "candidate-set.json", candidate_set_bytes)
        _write_file(stage / "candidate-set.sha256", candidate_set_sha256.encode("ascii"))
        _write_file(stage / "baseline-reference.json", reference_bytes)
        _write_file(stage / "baseline-reference.sha256", reference_sha256.encode("ascii"))
        _write_file(stage / "baseline-provenance.json", provenance_bytes)
        _write_file(
            stage / "baseline-provenance.sha256",
            hashlib.sha256(provenance_bytes).hexdigest().encode("ascii"),
        )
        _write_file(stage / "manifest-v4.json", canonical_json(manifest).encode("utf-8"))
        for model_id, (run_root, ledger, source_bundle) in run_material.items():
            copied_run = stage / "source-runs" / model_id
            _copy_source_run(
                source=run_root,
                target=copied_run,
                ledger=ledger,
                provenance_record=provenance_by_model[model_id],
                candidate_set_sha256=source_bundle.candidate_set_sha256,
            )
            attempts_by_take_id = {
                attempt["take_id"]: attempt
                for attempt in ledger["attempts"]
                if attempt["status"] == "eligible"
            }
            for candidate in (
                item for item in all_candidates if item["model"] == model_id
            ):
                attempt = attempts_by_take_id[candidate["take_id"]]
                source = run_root / attempt["audio"]["opus_path"]
                target = _resolve_relative(stage, candidate["path"], "candidate path")
                _copy_verified(
                    source,
                    target,
                    expected_sha256=candidate["sha256"],
                    label="candidate Opus",
                )
        for reference in references:
            source = _resolve_relative(
                legacy_root,
                reference["public_path"],
                "legacy public path",
            )
            target = _resolve_relative(
                stage,
                reference["local_path"],
                "reference local path",
            )
            _copy_verified(
                source,
                target,
                expected_sha256=reference["legacy_sha256"],
                label="legacy reference Opus",
            )
        _write_bundle_inventory(stage)
        _load_assembled_bundle(
            bundle_dir=stage,
            scenarios_dir=scenarios_dir,
            expected_manifest=None,
        )
        _commit_output_stage(stage, output_dir)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return BaselineAssembleSummary(
        bundle_dir=output_dir,
        candidate_set_sha256=candidate_set_sha256,
        baseline_reference_sha256=reference_sha256,
        group_count=EXPECTED_GROUP_COUNT,
        candidate_count=len(all_candidates),
        failure_count=len(all_failures),
    )


def validate_baseline_reference(
    document: Any,
    *,
    expected_plan: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    root = _exact(document, REFERENCE_ROOT_FIELDS, "baseline reference")
    if root["format_version"] != REFERENCE_FORMAT_VERSION:
        raise BaselineError("baseline reference format_version は 1 が必要です。")
    source_sha = _sha(
        root["source_manifest_sha256"],
        "baseline reference source_manifest_sha256",
    )
    candidate_set_sha = _sha(
        root["candidate_set_sha256"],
        "baseline reference candidate_set_sha256",
    )
    if source_sha != expected_plan["source"]["manifest_sha256"]:
        raise BaselineError("baseline reference source manifest SHA が plan と一致しません。")
    if not isinstance(root["references"], list):
        raise BaselineError("baseline reference references は配列が必要です。")
    plan_by_group = {_group_key(group): group for group in expected_plan["groups"]}
    candidate_groups = [_group_key(candidate) for candidate in candidates]
    if len(candidate_groups) != len(set(candidate_groups)):
        raise BaselineError("baseline reference candidate group が重複しています。")
    if not set(candidate_groups).issubset(plan_by_group):
        extra = sorted(set(candidate_groups) - set(plan_by_group))
        raise BaselineError(
            f"baseline reference candidate が plan 外 group を参照しています: {extra}",
        )
    candidate_by_group = {
        _group_key(candidate): candidate for candidate in candidates
    }
    references: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, value in enumerate(root["references"]):
        field = f"baseline reference references[{index}]"
        reference = _exact(value, REFERENCE_FIELDS, field)
        normalized = {
            **{
                key: _path_segment(reference[key], f"{field}.{key}")
                for key in GROUP_KEYS
            },
            "public_path": _relative_path(
                reference["public_path"],
                f"{field}.public_path",
            ),
            "legacy_sha256": _sha(
                reference["legacy_sha256"],
                f"{field}.legacy_sha256",
            ),
            "local_path": _relative_path(
                reference["local_path"],
                f"{field}.local_path",
            ),
            "candidate_sha256": (
                None
                if reference["candidate_sha256"] is None
                else _sha(reference["candidate_sha256"], f"{field}.candidate_sha256")
            ),
            "comparison": reference["comparison"],
        }
        identity = _group_key(normalized)
        if identity in seen or identity not in plan_by_group:
            raise BaselineError("baseline reference group が重複または plan 外です。")
        seen.add(identity)
        plan_group = plan_by_group[identity]
        expected_candidate = candidate_by_group.get(identity)
        expected_candidate_sha = (
            expected_candidate["sha256"] if expected_candidate is not None else None
        )
        expected_comparison = (
            "no_candidate"
            if expected_candidate_sha is None
            else "identical"
            if expected_candidate_sha == plan_group["legacy"]["sha256"]
            else "different"
        )
        expected_local_path = (
            f"reference/{identity[0]}/{identity[1]}/{identity[2]}/{identity[3]}.opus"
        )
        if (
            normalized["public_path"] != plan_group["legacy"]["path"]
            or normalized["legacy_sha256"] != plan_group["legacy"]["sha256"]
            or normalized["local_path"] != expected_local_path
            or normalized["candidate_sha256"] != expected_candidate_sha
            or normalized["comparison"] != expected_comparison
        ):
            raise BaselineError("baseline reference item が plan/candidate と一致しません。")
        references.append(normalized)
    if seen != set(plan_by_group) or len(references) != EXPECTED_GROUP_COUNT:
        raise BaselineError("baseline reference は plan 381 group の完全被覆が必要です。")
    if references != sorted(references, key=_group_key):
        raise BaselineError("baseline reference references は group 順が必要です。")
    return {
        "format_version": REFERENCE_FORMAT_VERSION,
        "source_manifest_sha256": source_sha,
        "candidate_set_sha256": candidate_set_sha,
        "references": references,
    }


def finalize_baseline(
    *,
    bundle_dir: Path,
    input_path: Path,
    output_dir: Path,
    scenarios_dir: Path,
) -> BaselineFinalizeSummary:
    if output_dir.exists():
        raise BaselineError(f"baseline finalize output は既存 path を拒否します: {output_dir}")
    material = _load_assembled_bundle(
        bundle_dir=bundle_dir,
        scenarios_dir=scenarios_dir,
        expected_manifest=None,
    )
    plan = material["plan"]
    candidate_set = material["candidate_set"]
    manifest = material["manifest"]
    reference = material["reference"]
    reference_sha256 = material["reference_sha256"]
    normalized = _validate_baseline_curation(
        _read_json(input_path, "baseline curation"),
        candidate_set_sha256=manifest["candidate_set_sha256"],
        baseline_reference_sha256=reference_sha256,
        candidates=manifest["candidates"],
        failures=manifest["failures"],
    )
    decision_bytes = canonical_json(normalized).encode("utf-8")
    decision_sha256 = hashlib.sha256(decision_bytes).hexdigest()
    curations = []
    selected_count = 0
    skipped_count = 0
    for group in normalized["groups"]:
        projection = {
            **{key: group[key] for key in GROUP_KEYS},
            "decision": group["decision"]["type"],
            "curation_sha256": decision_sha256,
        }
        if group["decision"]["type"] == "selected":
            projection["take_id"] = group["decision"]["take_id"]
            selected_count += 1
        else:
            skipped_count += 1
        curations.append(projection)
    try:
        release_manifest = validate_manifest_v4(
            {
                **manifest,
                "curations": sorted(curations, key=_group_key),
            },
        )
    except (TakeManifestError, TakeLedgerError) as error:
        raise BaselineError(f"release manifest v4 が不正です: {error}") from error
    candidate_zero_count = len(manifest["failures"])
    total = candidate_zero_count + selected_count + skipped_count
    if total != EXPECTED_GROUP_COUNT:
        raise BaselineError(
            "baseline finalize count は 381 = candidate_zero + selected + skipped "
            "が必要です。",
        )
    release_manifest_bytes = canonical_json(release_manifest).encode("utf-8")
    release_manifest_sha256 = hashlib.sha256(release_manifest_bytes).hexdigest()
    audit = {
        "format_version": AUDIT_FORMAT_VERSION,
        "source_manifest_sha256": plan["source"]["manifest_sha256"],
        "candidate_set_sha256": manifest["candidate_set_sha256"],
        "baseline_reference_sha256": reference_sha256,
        "decision_sha256": decision_sha256,
        "release_manifest_sha256": release_manifest_sha256,
        "counts": {
            "total": EXPECTED_GROUP_COUNT,
            "candidate_zero": candidate_zero_count,
            "selected": selected_count,
            "skipped": skipped_count,
            "uncurated": 0,
        },
    }
    audit_bytes = canonical_json(audit).encode("utf-8")
    audit_sha256 = hashlib.sha256(audit_bytes).hexdigest()

    stage = _new_output_stage(output_dir)
    try:
        plan_bytes = canonical_json(plan).encode("utf-8")
        provenance = material["provenance"]
        provenance_bytes = canonical_json(provenance).encode("utf-8")
        _write_file(stage / "baseline-plan.json", plan_bytes)
        _write_file(
            stage / "baseline-plan.sha256",
            hashlib.sha256(plan_bytes).hexdigest().encode("ascii"),
        )
        _write_file(stage / "baseline-provenance.json", provenance_bytes)
        _write_file(
            stage / "baseline-provenance.sha256",
            hashlib.sha256(provenance_bytes).hexdigest().encode("ascii"),
        )
        candidate_bytes = canonical_candidate_set_bytes(candidate_set)
        reference_bytes = canonical_json(reference).encode("utf-8")
        _write_file(stage / "candidate-set.json", candidate_bytes)
        _write_file(
            stage / "candidate-set.sha256",
            manifest["candidate_set_sha256"].encode("ascii"),
        )
        _write_file(stage / "baseline-reference.json", reference_bytes)
        _write_file(
            stage / "baseline-reference.sha256",
            reference_sha256.encode("ascii"),
        )
        for model_id, source_run in material["source_runs"].items():
            _copy_source_run(
                source=source_run["root"],
                target=stage / "source-runs" / model_id,
                ledger=source_run["ledger"],
                provenance_record=source_run["record"],
                candidate_set_sha256=source_run["bundle"].candidate_set_sha256,
            )
        for candidate in manifest["candidates"]:
            source = _resolve_relative(bundle_dir, candidate["path"], "candidate path")
            target = _resolve_relative(stage, candidate["path"], "candidate path")
            _copy_verified(
                source,
                target,
                expected_sha256=candidate["sha256"],
                label="candidate Opus",
            )
        for item in reference["references"]:
            source = _resolve_relative(bundle_dir, item["local_path"], "reference path")
            target = _resolve_relative(stage, item["local_path"], "reference path")
            _copy_verified(
                source,
                target,
                expected_sha256=item["legacy_sha256"],
                label="legacy reference Opus",
            )
        _write_file(stage / "manifest-v4.json", canonical_json(manifest).encode("utf-8"))
        _write_bundle_inventory(stage)
        _load_assembled_bundle(
            bundle_dir=stage,
            scenarios_dir=scenarios_dir,
            expected_manifest=None,
        )
        _write_file(
            stage / "data" / "curation" / f"{decision_sha256}.json",
            decision_bytes,
        )
        _write_file(stage / "manifest-v4.json", release_manifest_bytes)
        _write_file(
            stage / "manifest-v4.sha256",
            release_manifest_sha256.encode("ascii"),
        )
        _write_file(stage / "baseline-audit.json", audit_bytes)
        _write_file(stage / "baseline-audit.sha256", audit_sha256.encode("ascii"))
        _write_bundle_inventory(stage)
        _validate_bundle_inventory(stage)
        _validate_release_bundle(
            bundle_dir=stage,
            scenarios_dir=scenarios_dir,
            expected_manifest=release_manifest,
            expected_decision=normalized,
            expected_decision_sha256=decision_sha256,
            expected_audit=audit,
            expected_audit_sha256=audit_sha256,
            expected_manifest_sha256=release_manifest_sha256,
        )
        _validate_bundle_inventory(stage)
        _commit_output_stage(stage, output_dir)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return BaselineFinalizeSummary(
        output_dir=output_dir,
        decision_sha256=decision_sha256,
        release_manifest_sha256=release_manifest_sha256,
        audit_sha256=audit_sha256,
        candidate_zero_count=candidate_zero_count,
        selected_count=selected_count,
        skipped_count=skipped_count,
    )


def _plan_groups_by_model(
    plan: Mapping[str, Any],
) -> dict[str, set[tuple[str, str, str, str]]]:
    return {
        model["id"]: {
            _group_key(group)
            for group in plan["groups"]
            if group["model"] == model["id"]
        }
        for model in plan["models"]
    }


def _validate_model_run_selection(
    *,
    plan: Mapping[str, Any],
    ledger: Mapping[str, Any],
    run_models: Any,
) -> str:
    model_id = ledger["source"]["model"]
    expected_model = next(
        (model for model in plan["models"] if model["id"] == model_id),
        None,
    )
    if expected_model is None:
        raise BaselineError(f"run が plan 外の model を参照しています: {model_id}")
    if run_models != [expected_model]:
        raise BaselineError(f"run model metadata が plan と一致しません: {model_id}")
    actual_group_list = [
        tuple(group[key] for key in GROUP_KEYS)
        for group in ledger["source"]["groups"]
    ]
    if len(actual_group_list) != len(set(actual_group_list)):
        raise BaselineError(f"run groups が重複しています: {model_id}")
    expected_groups = _plan_groups_by_model(plan)[model_id]
    if set(actual_group_list) != expected_groups:
        raise BaselineError(
            f"run groups が plan の model selection と exact に一致しません: "
            f"{model_id}",
        )
    if ledger["source"]["takes"] != 1:
        raise BaselineError(f"baseline run は takes=1 が必要です: {model_id}")
    return model_id


def _validate_aggregate_plan_coverage(
    *,
    plan: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
) -> None:
    expected_groups = {_group_key(group) for group in plan["groups"]}
    candidate_group_list = [_group_key(candidate) for candidate in candidates]
    failure_group_list = [_group_key(failure) for failure in failures]
    if len(candidate_group_list) != len(set(candidate_group_list)):
        raise BaselineError("aggregate candidate group が重複しています。")
    if len(failure_group_list) != len(set(failure_group_list)):
        raise BaselineError("aggregate failure group が重複しています。")
    candidate_groups = set(candidate_group_list)
    failure_groups = set(failure_group_list)
    extra = (candidate_groups | failure_groups) - expected_groups
    if extra:
        raise BaselineError(f"aggregate に plan 外 group があります: {sorted(extra)}")
    if candidate_groups & failure_groups:
        raise BaselineError("aggregate candidate と failure group が競合しています。")
    if candidate_groups | failure_groups != expected_groups:
        missing = sorted(expected_groups - candidate_groups - failure_groups)
        raise BaselineError(
            f"aggregate candidate/failure が plan groups を完全被覆しません: "
            f"{missing}",
        )


def _validate_source_run(
    *,
    run_id: str,
    run_root: Path,
    scenarios_dir: Path,
    qc_ledger_path: Path,
) -> tuple[dict[str, Any], Any]:
    ledger_path = run_root / "ledger.json"
    try:
        ledger = read_ledger(ledger_path)
    except (OSError, UnicodeError, json.JSONDecodeError, TakeLedgerError) as error:
        raise BaselineError(f"source run ledger を検証できません: {ledger_path}: {error}") from error
    if ledger["run_id"] != run_id:
        raise BaselineError("source run id が ledger と一致しません。")
    if any(attempt["status"] not in TERMINAL_STATUSES for attempt in ledger["attempts"]):
        raise BaselineError("assemble は全 attempt terminal の run が必要です。")
    try:
        bundle = validate_snapshot_bundle(
            snapshot_path=run_root / "manifest-v4.json",
            candidate_set_path=run_root / "candidate-set.json",
            marker_path=run_root / "candidate-set.sha256",
        )
    except CurationError as error:
        raise BaselineError(f"source run snapshot bundle が不正です: {error}") from error
    if bundle.manifest["curations"]:
        raise BaselineError("assemble source run manifest は未策展 snapshot が必要です。")
    report = _read_json(run_root / "qc-report.json", "source run QC report")
    try:
        qc_authority = validate_qc_report(
            report,
            ledger_path=qc_ledger_path,
            ledger=ledger,
        )
    except QCReportError as error:
        raise BaselineError(f"source run QC report が不正です: {error}") from error
    if report["generated_at"] != bundle.manifest["generated_at"]:
        raise BaselineError("source run QC generated_at が manifest と一致しません。")
    try:
        _validate_manifest_against_terminal_ledger(
            manifest=bundle.manifest,
            ledger=ledger,
            run_root=run_root,
            qc_authority=qc_authority,
        )
        _validate_all_terminal_audio_evidence(
            ledger=ledger,
            run_root=run_root,
        )
        scenario_sha256, lines = load_authoritative_candidate_lines(
            scenarios_dir=scenarios_dir.resolve(),
            ledger_source=ledger["source"],
        )
        rebuilt = build_candidate_set(
            scenario_sha256=scenario_sha256,
            lines=lines,
            models=bundle.manifest["models"],
            candidates=bundle.manifest["candidates"],
            failures=bundle.manifest["failures"],
        )
        if canonical_candidate_set_bytes(rebuilt) != bundle.candidate_set_bytes:
            raise BaselineError(
                "source run candidate set が current scenario/ledger からの再構築と"
                "一致しません。",
            )
    except (CurationError, TakeManifestError, TakeLedgerError) as error:
        raise BaselineError(f"source run provenance が不正です: {error}") from error
    return ledger, bundle


def _validate_all_terminal_audio_evidence(
    *,
    ledger: Mapping[str, Any],
    run_root: Path,
) -> None:
    for attempt in ledger["attempts"]:
        slot = "/".join(str(attempt[key]) for key in GROUP_KEYS)
        take_stem = f"audio/{slot}/take-{attempt['take_index']:04d}"
        expected_paths = (
            _resolve_relative(run_root, f"{take_stem}.wav", "terminal WAV path"),
            _resolve_relative(run_root, f"{take_stem}.opus", "terminal Opus path"),
            _resolve_relative(run_root, f"{take_stem}.json", "terminal sidecar path"),
        )
        if attempt["status"] == "generation_failed":
            if any(path.exists() for path in expected_paths):
                raise BaselineError(
                    "generation_failed attempt に audio evidence が存在します: "
                    f"{take_stem}",
                )
            continue

        audio = attempt["audio"]
        wav_path = _resolve_relative(run_root, audio["wav_path"], "ledger WAV path")
        opus_path = _resolve_relative(run_root, audio["opus_path"], "ledger Opus path")
        sidecar_path = opus_path.with_suffix(".json")
        if (wav_path, opus_path, sidecar_path) != expected_paths:
            raise BaselineError("terminal audio path が ledger slot と一致しません。")
        for label, path, expected_sha256 in (
            ("WAV", wav_path, audio["wav_sha256"]),
            ("Opus", opus_path, audio["opus_sha256"]),
            ("sidecar", sidecar_path, audio["sidecar_sha256"]),
        ):
            if _file_sha256(path) != expected_sha256:
                raise BaselineError(
                    f"terminal {label} SHA-256 が ledger と一致しません: {path}",
                )
        try:
            sidecar = validate_take_sidecar(
                _read_json(sidecar_path, "terminal take sidecar"),
            )
        except TakeSidecarError as error:
            raise BaselineError(f"terminal take sidecar が不正です: {error}") from error
        identity_keys = (*GROUP_KEYS, "take_index")
        if sidecar["run_id"] != ledger["run_id"] or any(
            sidecar[key] != attempt[key] for key in identity_keys
        ):
            raise BaselineError("terminal take sidecar identity が ledger と一致しません。")
        if (
            sidecar["take_id"] != attempt["take_id"]
            or sidecar["generation_input_sha256"]
            != attempt["generation_input_sha256"]
            or sidecar["wav_sha256"] != audio["wav_sha256"]
            or sidecar["opus_sha256"] != audio["opus_sha256"]
        ):
            raise BaselineError(
                "terminal take sidecar artifact provenance が ledger と一致しません。",
            )
        generation = attempt["generation"]
        if (
            sidecar["take"]["seed"] != generation["seed"]
            or sidecar["take"]["sampling"] != generation["sampling"]
            or sidecar["take"]["recipe_version"]
            != ledger["source"]["recipe_version"]
            or sidecar["rtf"] != generation["rtf"]
        ):
            raise BaselineError(
                "terminal take sidecar generation provenance が ledger と一致しません。",
            )


def _copy_source_run(
    *,
    source: Path,
    target: Path,
    ledger: Mapping[str, Any],
    provenance_record: Mapping[str, Any],
    candidate_set_sha256: str,
) -> None:
    target.mkdir(parents=True)
    for name, expected_sha256 in (
        ("ledger.json", provenance_record["ledger_sha256"]),
        ("qc-report.json", provenance_record["qc_report_sha256"]),
        ("manifest-v4.json", provenance_record["manifest_sha256"]),
        ("candidate-set.json", candidate_set_sha256),
    ):
        _copy_verified(
            source / name,
            target / name,
            expected_sha256=expected_sha256,
            label=f"source run {name}",
        )
    marker = source / "candidate-set.sha256"
    _verify_marker(marker, candidate_set_sha256, "source run candidate set")
    _copy_exact(marker, target / "candidate-set.sha256")
    _verify_marker(
        target / "candidate-set.sha256",
        candidate_set_sha256,
        "copied source run candidate set",
    )
    copied_paths: set[str] = set()
    for attempt in ledger["attempts"]:
        if attempt["status"] == "generation_failed":
            continue
        audio = attempt["audio"]
        for relative_path, expected_sha256, label in (
            (audio["wav_path"], audio["wav_sha256"], "source run WAV"),
            (audio["opus_path"], audio["opus_sha256"], "source run Opus"),
            (
                str(Path(audio["opus_path"]).with_suffix(".json")).replace("\\", "/"),
                audio["sidecar_sha256"],
                "source run sidecar",
            ),
        ):
            if relative_path in copied_paths:
                raise BaselineError(
                    f"source run audio inventory が重複しています: {relative_path}",
                )
            copied_paths.add(relative_path)
            _copy_verified(
                _resolve_relative(source, relative_path, f"{label} path"),
                _resolve_relative(target, relative_path, f"{label} path"),
                expected_sha256=expected_sha256,
                label=label,
            )


def _load_assembled_bundle(
    *,
    bundle_dir: Path,
    scenarios_dir: Path,
    expected_manifest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not bundle_dir.is_dir():
        raise BaselineError(f"baseline bundle directory がありません: {bundle_dir}")
    _validate_bundle_inventory(bundle_dir)
    repository_root = scenarios_dir.resolve().parent
    plan_path = bundle_dir / "baseline-plan.json"
    plan = load_baseline_plan(plan_path, repository_root=repository_root)
    plan_bytes = canonical_json(plan).encode("utf-8")
    _verify_marker(
        bundle_dir / "baseline-plan.sha256",
        hashlib.sha256(plan_bytes).hexdigest(),
        "baseline plan",
    )
    try:
        bundle = validate_snapshot_bundle(
            snapshot_path=bundle_dir / "manifest-v4.json",
            candidate_set_path=bundle_dir / "candidate-set.json",
            marker_path=bundle_dir / "candidate-set.sha256",
        )
    except CurationError as error:
        raise BaselineError(f"aggregate snapshot bundle が不正です: {error}") from error
    if expected_manifest is None:
        if bundle.manifest["curations"]:
            raise BaselineError("finalize input manifest は未策展である必要があります。")
    elif bundle.manifest != expected_manifest:
        raise BaselineError("release manifest が expected curated manifest と一致しません。")
    if bundle.manifest["models"] != plan["models"]:
        raise BaselineError("aggregate manifest models が plan と一致しません。")

    reference_raw, reference_document = _read_canonical_json(
        bundle_dir / "baseline-reference.json",
        "baseline reference",
    )
    reference = validate_baseline_reference(
        reference_document,
        expected_plan=plan,
        candidates=bundle.manifest["candidates"],
    )
    expected_reference_bytes = canonical_json(reference).encode("utf-8")
    if reference_raw != expected_reference_bytes:
        raise BaselineError("baseline-reference.json は canonical bytes が必要です。")
    reference_sha256 = hashlib.sha256(reference_raw).hexdigest()
    _verify_marker(
        bundle_dir / "baseline-reference.sha256",
        reference_sha256,
        "baseline reference",
    )
    if reference["candidate_set_sha256"] != bundle.candidate_set_sha256:
        raise BaselineError("baseline reference candidate set binding が不正です。")

    provenance_raw, provenance = _read_canonical_json(
        bundle_dir / "baseline-provenance.json",
        "baseline provenance",
    )
    provenance = _validate_provenance_document(provenance, plan=plan)
    if provenance_raw != canonical_json(provenance).encode("utf-8"):
        raise BaselineError("baseline-provenance.json は canonical bytes が必要です。")
    _verify_marker(
        bundle_dir / "baseline-provenance.sha256",
        hashlib.sha256(provenance_raw).hexdigest(),
        "baseline provenance",
    )
    aggregate_candidates: list[dict[str, Any]] = []
    aggregate_failures: list[dict[str, Any]] = []
    source_runs: dict[str, dict[str, Any]] = {}
    for record in provenance["runs"]:
        source_run = bundle_dir / "source-runs" / record["model"]
        if _file_sha256(source_run / "ledger.json") != record["ledger_sha256"]:
            raise BaselineError("copied source run ledger SHA が provenance と一致しません。")
        if _file_sha256(source_run / "qc-report.json") != record["qc_report_sha256"]:
            raise BaselineError("copied source run QC SHA が provenance と一致しません。")
        if _file_sha256(source_run / "manifest-v4.json") != record["manifest_sha256"]:
            raise BaselineError("copied source run manifest SHA が provenance と一致しません。")
        ledger, source_bundle = _validate_source_run(
            run_id=record["run_id"],
            run_root=source_run,
            scenarios_dir=scenarios_dir,
            qc_ledger_path=Path(record["ledger_path"]),
        )
        if ledger["source"]["model"] != record["model"]:
            raise BaselineError("copied source run model が provenance と一致しません。")
        _validate_model_run_selection(
            plan=plan,
            ledger=ledger,
            run_models=source_bundle.manifest["models"],
        )
        if source_bundle.candidate_set_sha256 != record["candidate_set_sha256"]:
            raise BaselineError("copied source run candidate set SHA が一致しません。")
        source_runs[record["model"]] = {
            "root": source_run,
            "ledger": ledger,
            "bundle": source_bundle,
            "record": record,
        }
        aggregate_candidates.extend(source_bundle.manifest["candidates"])
        aggregate_failures.extend(source_bundle.manifest["failures"])
    aggregate_candidates.sort(key=lambda item: (_group_key(item), item["take_index"]))
    aggregate_failures.sort(key=_group_key)
    _validate_aggregate_plan_coverage(
        plan=plan,
        candidates=aggregate_candidates,
        failures=aggregate_failures,
    )
    if (
        aggregate_candidates != bundle.manifest["candidates"]
        or aggregate_failures != bundle.manifest["failures"]
    ):
        raise BaselineError("aggregate manifest が copied source runs と一致しません。")

    scenario_sha256, lines = _authoritative_plan_lines(
        scenarios_dir=scenarios_dir,
        groups=plan["groups"],
    )
    rebuilt = build_candidate_set(
        scenario_sha256=scenario_sha256,
        lines=lines,
        models=plan["models"],
        candidates=bundle.manifest["candidates"],
        failures=bundle.manifest["failures"],
    )
    if canonical_candidate_set_bytes(rebuilt) != bundle.candidate_set_bytes:
        raise BaselineError("aggregate candidate set が current scenario と一致しません。")
    for candidate in bundle.manifest["candidates"]:
        audio_path = _resolve_relative(bundle_dir, candidate["path"], "candidate path")
        if _file_sha256(audio_path) != candidate["sha256"]:
            raise BaselineError("bundle candidate Opus SHA が manifest と一致しません。")
    for item in reference["references"]:
        audio_path = _resolve_relative(bundle_dir, item["local_path"], "reference path")
        if _file_sha256(audio_path) != item["legacy_sha256"]:
            raise BaselineError("bundle legacy reference SHA が inventory と一致しません。")
    _validate_bundle_inventory(bundle_dir)
    return {
        "plan": plan,
        "manifest": bundle.manifest,
        "candidate_set": bundle.candidate_set,
        "reference": reference,
        "reference_sha256": reference_sha256,
        "provenance": provenance,
        "source_runs": source_runs,
    }


def _validate_baseline_curation(
    document: Any,
    *,
    candidate_set_sha256: str,
    baseline_reference_sha256: str,
    candidates: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    root = _exact(
        document,
        {
            "format_version",
            "rubric_version",
            "candidate_set_sha256",
            "baseline_reference_sha256",
            "groups",
        },
        "baseline curation",
    )
    if root["format_version"] != CURATION_FORMAT_VERSION:
        raise BaselineError("baseline curation format_version は 1 が必要です。")
    if root["rubric_version"] != CURATION_VERSION:
        raise BaselineError(
            "baseline curation rubric_version は baseline-curation-v1 が必要です。",
        )
    if _sha(root["candidate_set_sha256"], "baseline curation candidate set SHA") != (
        candidate_set_sha256
    ):
        raise BaselineError("baseline curation は stale candidate set を参照しています。")
    if _sha(
        root["baseline_reference_sha256"],
        "baseline curation reference SHA",
    ) != baseline_reference_sha256:
        raise BaselineError("baseline curation は stale reference inventory を参照しています。")
    try:
        ordinary = validate_curation(
            {
                "format_version": 1,
                "rubric_version": "take-curation-v1",
                "candidate_set_sha256": candidate_set_sha256,
                "groups": root["groups"],
            },
        )
    except CurationError as error:
        raise BaselineError(f"baseline curation group/rubric が不正です: {error}") from error
    candidates_by_group: dict[
        tuple[str, str, str, str],
        dict[str, Mapping[str, Any]],
    ] = {}
    for candidate in candidates:
        candidates_by_group.setdefault(_group_key(candidate), {})[
            candidate["take_id"]
        ] = candidate
    normalized_groups = ordinary["groups"]
    actual_groups = {_group_key(group) for group in normalized_groups}
    if actual_groups != set(candidates_by_group):
        missing = sorted(set(candidates_by_group) - actual_groups)
        extra = sorted(actual_groups - set(candidates_by_group))
        raise BaselineError(
            f"baseline curation は全 candidate group の decision が必要です: "
            f"missing={missing}, extra={extra}",
        )
    failure_groups = {_group_key(failure) for failure in failures}
    if actual_groups & failure_groups:
        raise BaselineError("candidate_zero failure group は curation artifact に入れません。")
    for group in normalized_groups:
        identity = _group_key(group)
        expected = candidates_by_group[identity]
        exported = {candidate["take_id"]: candidate for candidate in group["candidates"]}
        if set(exported) != set(expected):
            raise BaselineError("curation candidates は group candidate を完全被覆します。")
        for take_id, exported_candidate in exported.items():
            candidate = expected[take_id]
            if (
                exported_candidate["path"] != candidate["path"]
                or exported_candidate["audio_sha256"] != candidate["sha256"]
            ):
                raise BaselineError("curation candidate path/SHA が candidate set と一致しません。")
        decision = group["decision"]
        if decision["type"] == "selected":
            selected = exported[decision["take_id"]]
            rubric = selected["rubric"]
            if not rubric["content_correct"] or not rubric["adoptable"]:
                raise BaselineError(
                    "selected candidate は content_correct=true かつ adoptable=true "
                    "が必要です。",
                )
    normalized = {
        "format_version": CURATION_FORMAT_VERSION,
        "rubric_version": CURATION_VERSION,
        "candidate_set_sha256": candidate_set_sha256,
        "baseline_reference_sha256": baseline_reference_sha256,
        "groups": normalized_groups,
    }
    canonical_json(normalized)
    return normalized


def _validate_provenance_document(
    document: Any,
    *,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    root = _exact(
        document,
        {"format_version", "plan_sha256", "runs"},
        "baseline provenance",
    )
    if root["format_version"] != PROVENANCE_FORMAT_VERSION:
        raise BaselineError("baseline provenance format_version は 1 が必要です。")
    expected_plan_sha = hashlib.sha256(
        canonical_json(plan).encode("utf-8"),
    ).hexdigest()
    if _sha(root["plan_sha256"], "baseline provenance plan_sha256") != expected_plan_sha:
        raise BaselineError("baseline provenance plan SHA が baseline plan と一致しません。")
    if not isinstance(root["runs"], list):
        raise BaselineError("baseline provenance runs は配列が必要です。")
    runs = []
    for index, value in enumerate(root["runs"]):
        field = f"baseline provenance runs[{index}]"
        run = _exact(
            value,
            {
                "model",
                "run_id",
                "ledger_path",
                "ledger_sha256",
                "qc_report_sha256",
                "manifest_sha256",
                "candidate_set_sha256",
            },
            field,
        )
        runs.append(
            {
                "model": _path_segment(run["model"], f"{field}.model"),
                "run_id": _path_segment(run["run_id"], f"{field}.run_id"),
                "ledger_path": _absolute_posix_path(
                    run["ledger_path"],
                    f"{field}.ledger_path",
                ),
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
            },
        )
    if len(runs) != EXPECTED_MODEL_COUNT:
        raise BaselineError("baseline provenance は 7 model run が必要です。")
    if runs != sorted(runs, key=lambda run: run["model"]):
        raise BaselineError("baseline provenance runs は model 順が必要です。")
    if {run["model"] for run in runs} != {model["id"] for model in plan["models"]}:
        raise BaselineError("baseline provenance model coverage が plan と一致しません。")
    if len({run["run_id"] for run in runs}) != len(runs):
        raise BaselineError("baseline provenance run_id が重複しています。")
    return {
        "format_version": PROVENANCE_FORMAT_VERSION,
        "plan_sha256": root["plan_sha256"],
        "runs": runs,
    }


def _validate_release_bundle(
    *,
    bundle_dir: Path,
    scenarios_dir: Path,
    expected_manifest: Mapping[str, Any],
    expected_decision: Mapping[str, Any],
    expected_decision_sha256: str,
    expected_audit: Mapping[str, Any],
    expected_audit_sha256: str,
    expected_manifest_sha256: str,
) -> None:
    material = _load_assembled_bundle(
        bundle_dir=bundle_dir,
        scenarios_dir=scenarios_dir,
        expected_manifest=expected_manifest,
    )
    _validate_release_physical_audio(
        bundle_dir=bundle_dir,
        manifest=material["manifest"],
        reference=material["reference"],
    )
    _validate_release_curation_authority(
        bundle_dir=bundle_dir,
        expected_decision=expected_decision,
        expected_decision_sha256=expected_decision_sha256,
        expected_manifest=expected_manifest,
    )
    audit_raw, audit_document = _read_canonical_json(
        bundle_dir / "baseline-audit.json",
        "baseline audit",
    )
    expected_audit_bytes = canonical_json(expected_audit).encode("utf-8")
    if audit_document != expected_audit or audit_raw != expected_audit_bytes:
        raise BaselineError("baseline audit が expected canonical bytes と一致しません。")
    if hashlib.sha256(audit_raw).hexdigest() != expected_audit_sha256:
        raise BaselineError("baseline audit SHA-256 が expected audit と一致しません。")
    _verify_marker(
        bundle_dir / "baseline-audit.sha256",
        expected_audit_sha256,
        "baseline audit",
    )
    _verify_marker(
        bundle_dir / "manifest-v4.sha256",
        expected_manifest_sha256,
        "release manifest v4",
    )


def _validate_release_physical_audio(
    *,
    bundle_dir: Path,
    manifest: Mapping[str, Any],
    reference: Mapping[str, Any],
) -> None:
    for candidate in manifest["candidates"]:
        path = _resolve_relative(bundle_dir, candidate["path"], "release candidate path")
        if _file_sha256(path) != candidate["sha256"]:
            raise BaselineError("release candidate Opus SHA が manifest と一致しません。")
    for item in reference["references"]:
        path = _resolve_relative(bundle_dir, item["local_path"], "release reference path")
        if _file_sha256(path) != item["legacy_sha256"]:
            raise BaselineError("release reference Opus SHA が inventory と一致しません。")


def _validate_release_curation_authority(
    *,
    bundle_dir: Path,
    expected_decision: Mapping[str, Any],
    expected_decision_sha256: str,
    expected_manifest: Mapping[str, Any],
) -> None:
    manifest_raw, manifest_document = _read_canonical_json(
        bundle_dir / "manifest-v4.json",
        "release manifest v4",
    )
    try:
        manifest = validate_manifest_v4(manifest_document)
    except (TakeManifestError, TakeLedgerError) as error:
        raise BaselineError(f"release manifest v4 が不正です: {error}") from error
    if (
        manifest_raw != canonical_json(manifest).encode("utf-8")
        or manifest != expected_manifest
    ):
        raise BaselineError("release manifest v4 が expected canonical bytes と一致しません。")

    curation_path = (
        bundle_dir
        / "data"
        / "curation"
        / f"{expected_decision_sha256}.json"
    )
    decision_raw, decision_document = _read_canonical_json(
        curation_path,
        "release curation authority",
    )
    if hashlib.sha256(decision_raw).hexdigest() != expected_decision_sha256:
        raise BaselineError(
            "release curation authority SHA-256 が filename/projection と一致しません。",
        )
    decision = _validate_baseline_curation(
        decision_document,
        candidate_set_sha256=manifest["candidate_set_sha256"],
        baseline_reference_sha256=expected_decision["baseline_reference_sha256"],
        candidates=manifest["candidates"],
        failures=manifest["failures"],
    )
    if (
        decision_raw != canonical_json(decision).encode("utf-8")
        or decision != expected_decision
    ):
        raise BaselineError(
            "release curation authority が expected canonical decision と一致しません。",
        )
    expected_projections = []
    for group in decision["groups"]:
        projection = {
            **{key: group[key] for key in GROUP_KEYS},
            "decision": group["decision"]["type"],
            "curation_sha256": expected_decision_sha256,
        }
        if group["decision"]["type"] == "selected":
            projection["take_id"] = group["decision"]["take_id"]
        expected_projections.append(projection)
    expected_projections.sort(key=_group_key)
    if manifest["curations"] != expected_projections:
        raise BaselineError(
            "release manifest projection が canonical curation authority と"
            "一致しません。",
        )


def _write_bundle_inventory(bundle_dir: Path) -> None:
    inventory_path = bundle_dir / BUNDLE_INVENTORY_PATH
    marker_path = bundle_dir / BUNDLE_INVENTORY_MARKER_PATH
    document = {
        "format_version": BUNDLE_INVENTORY_FORMAT_VERSION,
        "files": [
            {
                "path": path.relative_to(bundle_dir).as_posix(),
                "sha256": _file_sha256(path),
            }
            for path in _bundle_files(bundle_dir)
        ],
    }
    payload = canonical_json(document).encode("utf-8")
    _write_file(inventory_path, payload)
    _write_file(
        marker_path,
        hashlib.sha256(payload).hexdigest().encode("ascii") + b"\n",
    )


def _validate_bundle_inventory(bundle_dir: Path) -> dict[str, Any]:
    raw, document = _read_canonical_json(
        bundle_dir / BUNDLE_INVENTORY_PATH,
        "baseline bundle inventory",
    )
    root = _exact(
        document,
        {"format_version", "files"},
        "baseline bundle inventory",
    )
    if root["format_version"] != BUNDLE_INVENTORY_FORMAT_VERSION:
        raise BaselineError("baseline bundle inventory format_version は 1 が必要です。")
    if not isinstance(root["files"], list):
        raise BaselineError("baseline bundle inventory files は配列が必要です。")
    files: list[dict[str, str]] = []
    for index, value in enumerate(root["files"]):
        field = f"baseline bundle inventory files[{index}]"
        item = _exact(value, {"path", "sha256"}, field)
        path = _relative_path(item["path"], f"{field}.path")
        if path in {BUNDLE_INVENTORY_PATH, BUNDLE_INVENTORY_MARKER_PATH}:
            raise BaselineError("baseline bundle inventory は自身を列挙しません。")
        files.append(
            {
                "path": path,
                "sha256": _sha(item["sha256"], f"{field}.sha256"),
            },
        )
    paths = [item["path"] for item in files]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise BaselineError("baseline bundle inventory files は path 順かつ一意が必要です。")
    casefolded_paths = [path.casefold() for path in paths]
    if len(casefolded_paths) != len(set(casefolded_paths)):
        raise BaselineError(
            "baseline bundle inventory path は大小文字を無視して一意が必要です。",
        )
    normalized = {
        "format_version": BUNDLE_INVENTORY_FORMAT_VERSION,
        "files": files,
    }
    if raw != canonical_json(normalized).encode("utf-8"):
        raise BaselineError("baseline-bundle-inventory.json は canonical bytes が必要です。")
    marker_path = bundle_dir / BUNDLE_INVENTORY_MARKER_PATH
    if not marker_path.is_file():
        raise BaselineError(
            f"baseline bundle inventory SHA marker がありません: {marker_path}",
        )
    try:
        marker = marker_path.read_bytes()
    except OSError as error:
        raise BaselineError(
            f"baseline bundle inventory SHA marker を読めません: {error}",
        ) from error
    expected_marker = hashlib.sha256(raw).hexdigest().encode("ascii") + b"\n"
    if marker != expected_marker:
        raise BaselineError(
            "baseline bundle inventory SHA marker が payload と一致しません。",
        )
    actual_paths = [
        path.relative_to(bundle_dir).as_posix()
        for path in _bundle_files(bundle_dir)
    ]
    if actual_paths != paths:
        missing = sorted(set(paths) - set(actual_paths))
        extra = sorted(set(actual_paths) - set(paths))
        raise BaselineError(
            "baseline bundle inventory の file coverage が一致しません: "
            f"missing={missing}, extra={extra}",
        )
    for item in files:
        path = _resolve_relative(bundle_dir, item["path"], "bundle inventory path")
        if _file_sha256(path) != item["sha256"]:
            raise BaselineError(
                f"baseline bundle inventory SHA-256 が一致しません: {item['path']}",
            )
    if [
        path.relative_to(bundle_dir).as_posix()
        for path in _bundle_files(bundle_dir)
    ] != paths:
        raise BaselineError("baseline bundle inventory 検証中に file set が変化しました。")
    return normalized


def _bundle_files(bundle_dir: Path) -> list[Path]:
    excluded = {BUNDLE_INVENTORY_PATH, BUNDLE_INVENTORY_MARKER_PATH}
    files: list[Path] = []
    for path in bundle_dir.rglob("*"):
        if path.is_symlink():
            raise BaselineError(f"baseline bundle は symlink を許可しません: {path}")
        if path.is_file():
            relative = path.relative_to(bundle_dir).as_posix()
            if relative not in excluded:
                files.append(path)
    return sorted(files, key=lambda path: path.relative_to(bundle_dir).as_posix())


def _new_output_stage(output_dir: Path) -> Path:
    if output_dir.exists():
        raise BaselineError(f"output は既存 path を拒否します: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    return Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.",
            suffix=".tmp",
            dir=output_dir.parent,
        ),
    )


def _commit_output_stage(stage: Path, output_dir: Path) -> None:
    if output_dir.exists():
        raise BaselineError(f"output は既存 path を拒否します: {output_dir}")
    try:
        stage.replace(output_dir)
    except OSError as error:
        raise BaselineError(f"baseline output を確定できません: {error}") from error


def _write_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(payload)
    except OSError as error:
        raise BaselineError(f"bundle file を書き込めません: {path}: {error}") from error


def _copy_exact(source: Path, target: Path) -> None:
    if not source.is_file():
        raise BaselineError(f"copy source file がありません: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, target)
    except OSError as error:
        raise BaselineError(f"file copy に失敗しました: {source}: {error}") from error


def _copy_verified(
    source: Path,
    target: Path,
    *,
    expected_sha256: str,
    label: str,
) -> None:
    if _file_sha256(source) != expected_sha256:
        raise BaselineError(f"{label} SHA-256 が authority と一致しません: {source}")
    _copy_exact(source, target)
    if _file_sha256(target) != expected_sha256:
        raise BaselineError(f"copied {label} SHA-256 が一致しません: {target}")


def _resolve_relative(root: Path, relative: str, field: str) -> Path:
    normalized = _relative_path(relative, field)
    root = root.resolve()
    path = (root / normalized).resolve()
    if not path.is_relative_to(root):
        raise BaselineError(f"{field} が root 外を参照しています。")
    return path


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise BaselineError(f"SHA-256 source file がありません: {path}")
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError as error:
        raise BaselineError(f"file SHA-256 を計算できません: {path}: {error}") from error


def _verify_marker(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise BaselineError(f"{label} SHA marker がありません: {path}")
    try:
        marker = path.read_bytes()
    except OSError as error:
        raise BaselineError(f"{label} SHA marker を読めません: {error}") from error
    if marker != expected.encode("ascii"):
        raise BaselineError(f"{label} SHA marker が payload と一致しません。")


def _read_json(path: Path, label: str) -> Any:
    _raw, document = _read_canonical_json(path, label)
    return document


def _authoritative_model_entries(
    model_ids: Sequence[str],
) -> list[dict[str, Any]]:
    if len(model_ids) != len(set(model_ids)):
        raise BaselineError("legacy manifest model ID が重複しています。")
    entries: list[dict[str, Any]] = []
    for model_id in sorted(model_ids):
        try:
            profile = get_model_profile(model_id)
            entry = profile.as_manifest_entry()
        except (UnknownAdapterError, ImportError, AttributeError) as error:
            raise BaselineError(
                "legacy manifest model ID の current adapter profile を"
                f"解決できません: {model_id}",
            ) from error
        if entry["id"] != model_id:
            raise BaselineError(
                f"current adapter profile ID が requested model と一致しません: {model_id}",
            )
        entries.append(entry)
    return entries


def _validate_plan_sources(
    plan: Mapping[str, Any],
    *,
    repository_root: Path,
) -> None:
    repository_root = repository_root.resolve()
    manifest_path = (repository_root / plan["source"]["manifest_path"]).resolve()
    if not manifest_path.is_relative_to(repository_root):
        raise BaselineError("baseline plan source manifest が repository 外です。")
    try:
        raw = manifest_path.read_bytes()
        manifest = load_manifest(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ManifestError) as error:
        raise BaselineError(f"current source manifest を検証できません: {error}") from error
    if hashlib.sha256(raw).hexdigest() != plan["source"]["manifest_sha256"]:
        raise BaselineError("current source manifest SHA-256 が baseline plan と一致しません。")
    legacy_model_ids = sorted(str(model["id"]) for model in manifest["models"])
    plan_model_ids = sorted(str(model["id"]) for model in plan["models"])
    if legacy_model_ids != plan_model_ids:
        raise BaselineError(
            "current source manifest model ID set が baseline plan と一致しません。",
        )
    expected_models = _authoritative_model_entries(legacy_model_ids)
    if expected_models != plan["models"]:
        raise BaselineError("current adapter profiles が baseline plan と一致しません。")
    expected_groups = sorted(
        (
            {
                **{key: clip[key] for key in GROUP_KEYS},
                "legacy": {"path": clip["path"], "sha256": clip["sha256"]},
            }
            for clip in manifest["clips"]
        ),
        key=_group_key,
    )
    expected_failures = sorted(
        (dict(failure) for failure in manifest["failures"]),
        key=_group_key,
    )
    if expected_groups != plan["groups"] or expected_failures != plan["excluded_failures"]:
        raise BaselineError("current source manifest results が baseline plan と一致しません。")
    scenario_sha256, _lines = _authoritative_plan_lines(
        scenarios_dir=repository_root / "scenarios",
        groups=plan["groups"],
    )
    if scenario_sha256 != plan["source"]["scenario_sha256"]:
        raise BaselineError("current scenario SHA-256 が baseline plan と一致しません。")


def _authoritative_plan_lines(
    *,
    scenarios_dir: Path,
    groups: Sequence[Mapping[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    requested = {(str(group["scenario"]), str(group["line"])) for group in groups}
    scenario_ids = sorted({scenario for scenario, _line in requested})
    validation = validate_scenario_ids(scenarios_dir, scenario_ids)
    if validation.problems:
        details = "\n".join(str(problem) for problem in validation.problems)
        raise BaselineError(f"current scenario 検証に失敗しました:\n{details}")
    source_files: list[dict[str, str]] = []
    lines: list[dict[str, Any]] = []
    found: set[tuple[str, str]] = set()
    for scenario_id in scenario_ids:
        path = scenarios_dir / f"{scenario_id}.yaml"
        raw = path.read_bytes()
        document = yaml.safe_load(raw.decode("utf-8"))
        source_files.append(
            {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()},
        )
        for line in document["lines"]:
            identity = (scenario_id, str(line["id"]))
            if identity not in requested:
                continue
            if identity in found:
                raise BaselineError("current scenario line が重複しています。")
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
        raise BaselineError(
            f"baseline plan line が current scenario にありません: {sorted(requested - found)}",
        )
    return (
        hashlib.sha256(canonical_json(source_files).encode("utf-8")).hexdigest(),
        sorted(lines, key=lambda line: (line["scenario"], line["line"])),
    )


def _repository_root_for_manifest(manifest_path: Path) -> Path:
    resolved = manifest_path.resolve()
    if resolved.parent.name != "data":
        raise BaselineError("source manifest は repository の data directory 直下が必要です。")
    repository_root = resolved.parent.parent
    if not (repository_root / "scenarios").is_dir():
        raise BaselineError("source manifest に対応する scenarios directory がありません。")
    return repository_root


def _model(value: Any, field: str) -> dict[str, Any]:
    model = _exact(
        value,
        {"id", "name", "version", "license_note", "capabilities"},
        field,
    )
    capabilities = _exact(
        model["capabilities"],
        {"emotion", "voice_prompt", "clone", "nonverbal", "reading"},
        f"{field}.capabilities",
    )
    if not all(isinstance(value, bool) for value in capabilities.values()):
        raise BaselineError(f"{field}.capabilities は boolean が必要です。")
    return {
        "id": _path_segment(model["id"], f"{field}.id"),
        "name": _text(model["name"], f"{field}.name"),
        "version": _text(model["version"], f"{field}.version"),
        "license_note": _text(model["license_note"], f"{field}.license_note"),
        "capabilities": dict(capabilities),
    }


def _exact(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise BaselineError(f"{field} が exact contract と一致しません。")
    return value


def _path_segment(value: Any, field: str) -> str:
    text = _text(value, field)
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise BaselineError(f"{field} は安全な path segment が必要です。")
    return text


def _relative_path(value: Any, field: str) -> str:
    text = _text(value, field)
    path = Path(text)
    if path.is_absolute() or text != path.as_posix() or ".." in path.parts:
        raise BaselineError(f"{field} は repository-relative POSIX path が必要です。")
    return text


def _absolute_posix_path(value: Any, field: str) -> str:
    text = _text(value, field)
    path = Path(text)
    if not path.is_absolute() or text != path.as_posix():
        raise BaselineError(f"{field} は absolute POSIX path が必要です。")
    return text


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise BaselineError(f"{field} は空でない文字列が必要です。")
    return value


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in HEX for character in value)
    ):
        raise BaselineError(f"{field} は小文字 SHA-256 が必要です。")
    return value


def _group_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(value[key]) for key in GROUP_KEYS)  # type: ignore[return-value]


def _read_canonical_json(path: Path, label: str) -> tuple[bytes, Any]:
    if not path.is_file():
        raise BaselineError(f"{label} がありません: {path}")
    try:
        raw = path.read_bytes()
        return raw, json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BaselineError(f"{label} を読み込めません: {path}: {error}") from error


def _write_new_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise BaselineError(f"output は既存 path を拒否します: {path}") from error
    except OSError as error:
        raise BaselineError(f"output を書き込めません: {path}: {error}") from error
