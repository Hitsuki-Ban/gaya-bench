from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from gaya_pipeline.curation import (
    GROUP_KEYS,
    CurationError,
    SnapshotBundle,
    _projection,
    _validate_existing_projections,
    _validate_manifest_against_terminal_ledger,
    build_candidate_set,
    canonical_candidate_set_bytes,
    canonical_curation_bytes,
    load_authoritative_candidate_lines,
    validate_curation,
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
from gaya_pipeline.take_manifest_v4 import (
    TakeManifestError,
    validate_manifest_v4,
)


class ReleaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseFinalizeSummary:
    output_dir: Path
    manifest_sha256: str
    candidate_set_sha256: str
    curation_sha256: str
    model_count: int
    candidate_count: int
    selected_count: int
    skipped_count: int
    failure_count: int


@dataclass(frozen=True)
class FinalizedRelease:
    root: Path
    manifest: dict[str, Any]
    candidate_set: dict[str, Any]
    provenance: dict[str, Any]
    curation: dict[str, Any]
    run_roots: dict[str, Path]
    source_manifests: dict[str, dict[str, Any]]
    projection_plan: dict[str, Any] | None


@dataclass(frozen=True)
class _SourceRun:
    run_id: str
    model: str
    root: Path
    ledger: dict[str, Any]
    bundle: SnapshotBundle
    ledger_sha256: str
    qc_report_sha256: str
    manifest_sha256: str
    curation_groups: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _ProjectionSource:
    source: _SourceRun
    plan: dict[str, Any]
    plan_bytes: bytes
    plan_sha256: str
    source_release: FinalizedRelease
    source_release_path: str
    source_release_manifest_sha256: str
    source_release_provenance_sha256: str
    source_release_curation_sha256: str
    source_curation_groups_sha256: str
    target_failures: tuple[dict[str, Any], ...]


PROVENANCE_FORMAT_VERSION = 1
PROJECTED_PROVENANCE_FORMAT_VERSION = 2
PROVENANCE_V1_ROOT_FIELDS = {
    "format_version",
    "candidate_set_sha256",
    "manifest_sha256",
    "runs",
}
PROVENANCE_V2_ROOT_FIELDS = {
    *PROVENANCE_V1_ROOT_FIELDS,
    "projection_plan_sha256",
    "projection",
}
PROVENANCE_RUN_FIELDS = {
    "model",
    "run_id",
    "ledger_sha256",
    "qc_report_sha256",
    "manifest_sha256",
    "candidate_set_sha256",
}
PROJECTION_PLAN_ROOT_FIELDS = {
    "format_version",
    "target_run_id",
    "source_release",
    "target_failures",
}
PROJECTION_PLAN_SOURCE_FIELDS = {
    "path",
    "model",
    "manifest_sha256",
    "candidate_set_sha256",
    "provenance_sha256",
    "curation_sha256",
}
PROJECTION_PROVENANCE_FIELDS = {
    "model",
    "source_release_path",
    "source_release_manifest_sha256",
    "source_release_candidate_set_sha256",
    "source_release_provenance_sha256",
    "source_release_curation_sha256",
    "source_curation_groups_sha256",
    "source_scenario_sha256",
    "target_scenario_sha256",
    "target_failures",
}
FAILURE_FIELDS = {*GROUP_KEYS, "reason"}
HEX = frozenset("0123456789abcdef")
PATH_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def finalize_release(
    *,
    run_ids: Sequence[str],
    artifacts_dir: Path,
    data_dir: Path,
    scenarios_dir: Path,
    output_dir: Path,
    projection_plan_path: Path | None = None,
) -> ReleaseFinalizeSummary:
    ordered_run_ids = sorted(_path_segment(run_id, "run_id") for run_id in run_ids)
    if not ordered_run_ids:
        raise ReleaseError("release finalize には1件以上の run-id が必要です。")
    if len(ordered_run_ids) != len(set(ordered_run_ids)):
        raise ReleaseError("release finalize run-id が重複しています。")
    if output_dir.exists():
        raise ReleaseError(
            f"release finalize output は既存 path を拒否します: {output_dir}",
        )
    output_parent = output_dir.resolve().parent
    if not output_parent.is_dir():
        raise ReleaseError(
            f"release finalize output の親 directory が存在しません: {output_parent}",
        )

    takes_root = _require_directory(artifacts_dir / "takes", "takes root")
    scenarios_root = _require_directory(scenarios_dir, "scenarios directory")
    artifact_root = _require_directory(data_dir / "curation", "curation directory")
    repository_root = data_dir.resolve().parent
    if projection_plan_path is not None:
        layout_root = _projected_repository_root(takes_root)
        if layout_root != repository_root:
            raise ReleaseError(
                "projected finalize は data/ と artifacts/takes が同じ"
                "repository root 配下にある必要があります。",
            )
    run_roots = [
        _resolve_direct_child(takes_root, run_id, f"source run {run_id}")
        for run_id in ordered_run_ids
    ]
    projection = (
        _load_projection_source(
            projection_plan_path=projection_plan_path,
            repository_root=repository_root,
            takes_root=takes_root,
        )
        if projection_plan_path is not None
        else None
    )
    lock_roots = {
        run_root.resolve(): run_root for run_root in run_roots
    }
    if projection is not None:
        lock_roots.setdefault(projection.source.root.resolve(), projection.source.root)

    try:
        with ExitStack() as locks:
            for run_root in lock_roots.values():
                locks.enter_context(exclusive_run_lock(run_root))
            sources = [
                _load_curated_source_run(
                    run_id=run_id,
                    run_root=run_root,
                    scenarios_dir=scenarios_root,
                    artifact_root=artifact_root,
                )
                for run_id, run_root in zip(
                    ordered_run_ids,
                    run_roots,
                    strict=True,
                )
            ]
            if projection_plan_path is not None:
                projection = _load_projection_source(
                    projection_plan_path=projection_plan_path,
                    repository_root=repository_root,
                    takes_root=takes_root,
                )
            material = _build_release_material(
                sources,
                projection=projection,
            )
            return _write_release(
                output_dir=output_dir,
                takes_root=takes_root,
                material=material,
            )
    except RunLockError as error:
        raise ReleaseError(f"source run lock に失敗しました: {error}") from error


def validate_finalized_release(
    *,
    release_dir: Path,
    takes_root: Path,
) -> FinalizedRelease:
    release_root = _require_directory(release_dir, "release directory")
    takes_root_resolved = _require_directory(takes_root, "takes root")
    try:
        bundle = validate_snapshot_bundle(
            snapshot_path=release_root / "manifest-v4.json",
            candidate_set_path=release_root / "candidate-set.json",
            marker_path=release_root / "candidate-set.sha256",
        )
    except CurationError as error:
        raise ReleaseError(f"release snapshot bundle が不正です: {error}") from error

    manifest_raw = _read_bytes(
        release_root / "manifest-v4.json",
        "release manifest",
    )
    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    if manifest_raw != canonical_json(bundle.manifest).encode("utf-8"):
        raise ReleaseError("release manifest は canonical bytes が必要です。")
    _verify_marker(
        release_root / "manifest-v4.sha256",
        manifest_sha256,
        "release manifest",
    )
    provenance = _load_provenance(release_root)
    if provenance["candidate_set_sha256"] != bundle.candidate_set_sha256:
        raise ReleaseError(
            "release provenance candidate_set_sha256 が release と一致しません。",
        )
    if provenance["manifest_sha256"] != manifest_sha256:
        raise ReleaseError(
            "release provenance manifest_sha256 が release と一致しません。",
        )
    projection_plan = _load_release_projection_plan(
        release_root=release_root,
        provenance=provenance,
    )
    projection_source = (
        _load_projection_source(
            projection_plan_path=release_root / "projection-plan.json",
            repository_root=_projected_repository_root(takes_root_resolved),
            takes_root=takes_root_resolved,
        )
        if projection_plan is not None
        else None
    )

    models = {model["id"]: model for model in bundle.manifest["models"]}
    run_by_model = {run["model"]: run for run in provenance["runs"]}
    if set(run_by_model) != set(models):
        raise ReleaseError(
            "release provenance model coverage が release manifest と一致しません。",
        )

    curation = _load_release_curation(
        release_root=release_root,
        manifest=bundle.manifest,
        candidate_set_sha256=bundle.candidate_set_sha256,
    )
    run_roots: dict[str, Path] = {}
    source_manifests: dict[str, dict[str, Any]] = {}
    source_bundles: dict[str, SnapshotBundle] = {}
    release_candidates_by_model = _items_by_model(bundle.manifest["candidates"])
    release_failures_by_model = _items_by_model(bundle.manifest["failures"])
    for model, record in run_by_model.items():
        run_root = _resolve_direct_child(
            takes_root_resolved,
            record["run_id"],
            f"source run {record['run_id']}",
        )
        _verify_source_record_files(run_root=run_root, record=record)
        try:
            source_bundle = validate_snapshot_bundle(
                snapshot_path=run_root / "manifest-v4.json",
                candidate_set_path=run_root / "candidate-set.json",
                marker_path=run_root / "candidate-set.sha256",
            )
        except CurationError as error:
            raise ReleaseError(
                f"source run snapshot bundle が不正です: {record['run_id']}: {error}",
            ) from error
        if source_bundle.candidate_set_sha256 != record["candidate_set_sha256"]:
            raise ReleaseError(
                f"source candidate set SHA が provenance と一致しません: "
                f"{record['run_id']}",
            )
        if source_bundle.manifest["models"] != [models[model]]:
            raise ReleaseError(
                f"source model metadata が release と一致しません: {model}",
            )
        release_candidates = release_candidates_by_model.get(model, [])
        _validate_release_failures_against_source(
            model=model,
            release_candidates=release_candidates,
            release_failures=release_failures_by_model.get(model, []),
            source_manifest=source_bundle.manifest,
            projected_failures=(
                projection_source.target_failures
                if projection_source is not None
                and projection_source.source.model == model
                else ()
            ),
        )
        run_roots[model] = run_root
        source_manifests[model] = source_bundle.manifest
        source_bundles[model] = source_bundle

    if projection_source is not None:
        _validate_final_projection(
            bundle=bundle,
            curation=curation,
            provenance=provenance,
            run_by_model=run_by_model,
            source_bundles=source_bundles,
            projection=projection_source,
        )

    return FinalizedRelease(
        root=release_root,
        manifest=bundle.manifest,
        candidate_set=bundle.candidate_set,
        provenance=provenance,
        curation=curation,
        run_roots=run_roots,
        source_manifests=source_manifests,
        projection_plan=projection_plan,
    )


def _load_curated_source_run(
    *,
    run_id: str,
    run_root: Path,
    scenarios_dir: Path,
    artifact_root: Path,
) -> _SourceRun:
    ledger_path = run_root / "ledger.json"
    qc_report_path = run_root / "qc-report.json"
    manifest_path = run_root / "manifest-v4.json"
    try:
        ledger = read_ledger(ledger_path)
    except (OSError, UnicodeError, json.JSONDecodeError, TakeLedgerError) as error:
        raise ReleaseError(
            f"source run ledger を検証できません: {ledger_path}: {error}",
        ) from error
    if ledger["run_id"] != run_id:
        raise ReleaseError("source run id が ledger と一致しません。")
    if any(
        attempt["status"] not in TERMINAL_STATUSES
        for attempt in ledger["attempts"]
    ):
        raise ReleaseError("release finalize は全 attempt terminal の run が必要です。")
    try:
        bundle = validate_snapshot_bundle(
            snapshot_path=manifest_path,
            candidate_set_path=run_root / "candidate-set.json",
            marker_path=run_root / "candidate-set.sha256",
        )
    except CurationError as error:
        raise ReleaseError(f"source run snapshot bundle が不正です: {error}") from error
    model = str(ledger["source"]["model"])
    if model == "dummy":
        raise ReleaseError(
            "dummy run は production release finalize の入力にできません。",
        )
    if len(bundle.manifest["models"]) != 1:
        raise ReleaseError("source run manifest は1 modelだけを含む必要があります。")
    if bundle.manifest["models"][0]["id"] != model:
        raise ReleaseError("source run model metadata が ledger と一致しません。")

    qc_document = _read_json(qc_report_path, "source run QC report")
    try:
        qc_authority = validate_qc_report(
            qc_document,
            ledger_path=ledger_path,
            ledger=ledger,
        )
    except QCReportError as error:
        raise ReleaseError(f"source run QC report が不正です: {error}") from error
    if qc_document["generated_at"] != bundle.manifest["generated_at"]:
        raise ReleaseError("source run QC generated_at が manifest と一致しません。")
    try:
        _validate_manifest_against_terminal_ledger(
            manifest=bundle.manifest,
            ledger=ledger,
            run_root=run_root,
            qc_authority=qc_authority,
        )
        scenario_sha256, lines = load_authoritative_candidate_lines(
            scenarios_dir=scenarios_dir,
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
            raise ReleaseError(
                "source run candidate set が current scenario/ledger からの再構築と"
                "一致しません。",
            )
    except (CurationError, TakeManifestError, TakeLedgerError) as error:
        raise ReleaseError(f"source run provenance が不正です: {error}") from error

    candidates_by_group: dict[
        tuple[str, str, str, str],
        dict[str, dict[str, Any]],
    ] = {}
    for candidate in bundle.manifest["candidates"]:
        identity = _group_key(candidate)
        candidates_by_group.setdefault(identity, {})[candidate["take_id"]] = candidate
    try:
        authorities = _validate_existing_projections(
            manifest=bundle.manifest,
            artifact_root=artifact_root,
            candidate_set_sha256=bundle.candidate_set_sha256,
            candidates_by_group=candidates_by_group,
            run_root=run_root,
        )
    except CurationError as error:
        raise ReleaseError(f"source run curation が不正です: {error}") from error
    if set(authorities) != set(candidates_by_group):
        missing = sorted(set(candidates_by_group) - set(authorities))
        raise ReleaseError(
            f"source run に未策展 candidate group があります: {missing}",
        )
    return _SourceRun(
        run_id=run_id,
        model=model,
        root=run_root,
        ledger=ledger,
        bundle=bundle,
        ledger_sha256=_file_sha256(ledger_path),
        qc_report_sha256=_file_sha256(qc_report_path),
        manifest_sha256=_file_sha256(manifest_path),
        curation_groups=tuple(
            authorities[identity]["group"] for identity in sorted(authorities)
        ),
    )


def validate_projection_plan(document: Any) -> dict[str, Any]:
    root = _exact(document, PROJECTION_PLAN_ROOT_FIELDS, "projection plan")
    if root["format_version"] != 1:
        raise ReleaseError("projection plan format_version は1が必要です。")
    target_run_id = _path_segment(
        root["target_run_id"],
        "projection plan target_run_id",
    )
    source = _exact(
        root["source_release"],
        PROJECTION_PLAN_SOURCE_FIELDS,
        "projection plan source_release",
    )
    source_path = _repository_relative_path(
        source["path"],
        "projection plan source_release.path",
    )
    model = _path_segment(
        source["model"],
        "projection plan source_release.model",
    )
    normalized_source = {
        "path": source_path,
        "model": model,
        "manifest_sha256": _sha(
            source["manifest_sha256"],
            "projection plan source_release.manifest_sha256",
        ),
        "candidate_set_sha256": _sha(
            source["candidate_set_sha256"],
            "projection plan source_release.candidate_set_sha256",
        ),
        "provenance_sha256": _sha(
            source["provenance_sha256"],
            "projection plan source_release.provenance_sha256",
        ),
        "curation_sha256": _sha(
            source["curation_sha256"],
            "projection plan source_release.curation_sha256",
        ),
    }
    failures = _validate_projected_failures(
        root["target_failures"],
        model=model,
        field="projection plan target_failures",
    )
    return {
        "format_version": 1,
        "target_run_id": target_run_id,
        "source_release": normalized_source,
        "target_failures": failures,
    }


def _load_projection_source(
    *,
    projection_plan_path: Path,
    repository_root: Path,
    takes_root: Path,
) -> _ProjectionSource:
    plan_path = projection_plan_path.resolve()
    plan_bytes = _read_bytes(plan_path, "projection plan")
    plan = validate_projection_plan(
        _read_json_bytes(plan_bytes, plan_path, "projection plan"),
    )
    canonical_plan = canonical_json(plan).encode("utf-8")
    if plan_bytes != canonical_plan:
        raise ReleaseError("projection plan は canonical bytes が必要です。")
    plan_sha256 = hashlib.sha256(plan_bytes).hexdigest()

    source_record = plan["source_release"]
    source_release_root = _resolve_repository_child(
        repository_root=repository_root,
        relative_path=source_record["path"],
        label="projection source release",
    )
    source_release = validate_finalized_release(
        release_dir=source_release_root,
        takes_root=takes_root,
    )
    if source_release.provenance["format_version"] != PROVENANCE_FORMAT_VERSION:
        raise ReleaseError(
            "projection source release は format_version=1 の確定releaseが必要です。",
        )
    if source_release.projection_plan is not None:
        raise ReleaseError("projection source release の連鎖投影は受理しません。")

    manifest_sha256 = _file_sha256(source_release_root / "manifest-v4.json")
    provenance_sha256 = _file_sha256(
        source_release_root / "release-provenance.json",
    )
    curation_hashes = {
        item["curation_sha256"]
        for item in source_release.manifest["curations"]
    }
    if len(curation_hashes) != 1:
        raise ReleaseError(
            "projection source release の curation SHA が一意ではありません。",
        )
    source_curation_sha256 = next(iter(curation_hashes))
    actual_pins = {
        "manifest_sha256": manifest_sha256,
        "candidate_set_sha256": source_release.manifest["candidate_set_sha256"],
        "provenance_sha256": provenance_sha256,
        "curation_sha256": source_curation_sha256,
    }
    for key, actual in actual_pins.items():
        if source_record[key] != actual:
            raise ReleaseError(
                f"projection source release {key} が plan と一致しません。",
            )

    model = source_record["model"]
    models = {
        item["id"]: item for item in source_release.manifest["models"]
    }
    if model not in models:
        raise ReleaseError(
            f"projection source release に model がありません: {model}",
        )
    if model == "dummy":
        raise ReleaseError("dummy model は preserved projection に使用できません。")
    record_by_model = {
        item["model"]: item for item in source_release.provenance["runs"]
    }
    run_record = record_by_model[model]
    run_root = source_release.run_roots[model]
    try:
        source_bundle = validate_snapshot_bundle(
            snapshot_path=run_root / "manifest-v4.json",
            candidate_set_path=run_root / "candidate-set.json",
            marker_path=run_root / "candidate-set.sha256",
        )
        ledger = read_ledger(run_root / "ledger.json")
    except (CurationError, TakeLedgerError, OSError, json.JSONDecodeError) as error:
        raise ReleaseError(
            f"projection source run を検証できません: {run_record['run_id']}: {error}",
        ) from error
    if ledger["run_id"] != run_record["run_id"]:
        raise ReleaseError("projection source run id が ledger と一致しません。")
    if ledger["source"]["model"] != model:
        raise ReleaseError("projection source model が ledger と一致しません。")
    qc_document = _read_json(
        run_root / "qc-report.json",
        "projection source run QC report",
    )
    try:
        qc_authority = validate_qc_report(
            qc_document,
            ledger_path=run_root / "ledger.json",
            ledger=ledger,
        )
        if qc_document["generated_at"] != source_bundle.manifest["generated_at"]:
            raise ReleaseError(
                "projection source run QC generated_at が manifest と"
                "一致しません。",
            )
        _validate_manifest_against_terminal_ledger(
            manifest=source_bundle.manifest,
            ledger=ledger,
            run_root=run_root,
            qc_authority=qc_authority,
        )
    except (
        CurationError,
        QCReportError,
        TakeManifestError,
        TakeLedgerError,
    ) as error:
        raise ReleaseError(
            f"projection source run の物理artifact provenanceが不正です: {error}",
        ) from error

    release_candidates = [
        item
        for item in source_release.manifest["candidates"]
        if item["model"] == model
    ]
    release_failures = [
        item
        for item in source_release.manifest["failures"]
        if item["model"] == model
    ]
    _validate_release_failures_against_source(
        model=model,
        release_candidates=release_candidates,
        release_failures=release_failures,
        source_manifest=source_bundle.manifest,
    )
    curation_groups = tuple(
        sorted(
            (
                dict(group)
                for group in source_release.curation["groups"]
                if group["model"] == model
            ),
            key=_group_key,
        ),
    )
    candidate_groups = {
        _group_key(candidate) for candidate in release_candidates
    }
    if {_group_key(group) for group in curation_groups} != candidate_groups:
        raise ReleaseError(
            "projection source release の model curation coverage が不正です。",
        )
    source_group_bytes = canonical_json(list(curation_groups)).encode("utf-8")
    source_groups_sha256 = hashlib.sha256(source_group_bytes).hexdigest()
    source = _SourceRun(
        run_id=run_record["run_id"],
        model=model,
        root=run_root,
        ledger=ledger,
        bundle=source_bundle,
        ledger_sha256=run_record["ledger_sha256"],
        qc_report_sha256=run_record["qc_report_sha256"],
        manifest_sha256=run_record["manifest_sha256"],
        curation_groups=curation_groups,
    )
    return _ProjectionSource(
        source=source,
        plan=plan,
        plan_bytes=plan_bytes,
        plan_sha256=plan_sha256,
        source_release=source_release,
        source_release_path=source_record["path"],
        source_release_manifest_sha256=manifest_sha256,
        source_release_provenance_sha256=provenance_sha256,
        source_release_curation_sha256=source_curation_sha256,
        source_curation_groups_sha256=source_groups_sha256,
        target_failures=tuple(plan["target_failures"]),
    )


def _validate_projection_against_target(
    *,
    projection: _ProjectionSource,
    target_source: _SourceRun,
) -> None:
    source_items = [
        *projection.source.bundle.manifest["candidates"],
        *projection.source.bundle.manifest["failures"],
    ]
    target_items = [
        *target_source.bundle.manifest["candidates"],
        *target_source.bundle.manifest["failures"],
    ]
    source_shapes = {_group_shape(item) for item in source_items}
    target_shapes = {_group_shape(item) for item in target_items}
    failure_shapes = {
        _group_shape(item) for item in projection.target_failures
    }
    if source_shapes & failure_shapes:
        raise ReleaseError(
            "projection target failure が preserved source group と競合しています。",
        )
    if source_shapes | failure_shapes != target_shapes:
        missing = sorted(target_shapes - source_shapes - failure_shapes)
        extra = sorted((source_shapes | failure_shapes) - target_shapes)
        raise ReleaseError(
            "projection group coverage が target run と一致しません: "
            f"missing={missing}, extra={extra}",
        )

    target_lines = {
        (item["scenario"], item["line"]): item
        for item in target_source.bundle.candidate_set["lines"]
    }
    for source_line in projection.source.bundle.candidate_set["lines"]:
        identity = (source_line["scenario"], source_line["line"])
        if target_lines.get(identity) != source_line:
            raise ReleaseError(
                "projection source/target line snapshot が一致しません: "
                f"{identity}",
            )


def _build_release_material(
    sources: Sequence[_SourceRun],
    *,
    projection: _ProjectionSource | None = None,
) -> dict[str, Any]:
    by_model = {source.model: source for source in sources}
    if len(by_model) != len(sources):
        raise ReleaseError("release finalize は model ごとに1 runだけを受理します。")
    if projection is not None:
        if projection.source.model in by_model:
            raise ReleaseError(
                "preserved projection model が通常 source run と重複しています。",
            )
        if projection.source.run_id in {source.run_id for source in sources}:
            raise ReleaseError(
                "preserved projection run が通常 source run と重複しています。",
            )
        target_run_id = projection.plan["target_run_id"]
        target_sources = [
            source for source in sources if source.run_id == target_run_id
        ]
        if len(target_sources) != 1:
            raise ReleaseError(
                "projection plan target_run_id は通常 source run を一意に"
                "参照する必要があります。",
            )
        target_source = target_sources[0]
    else:
        target_source = None

    ordered_regular_sources = [by_model[model] for model in sorted(by_model)]
    scenario_sha256s = {
        source.bundle.candidate_set["scenario_sha256"]
        for source in ordered_regular_sources
    }
    if len(scenario_sha256s) != 1:
        raise ReleaseError(
            "source run の scenario source selection が一致しません。",
        )
    line_sets = [
        source.bundle.candidate_set["lines"] for source in ordered_regular_sources
    ]
    if any(lines != line_sets[0] for lines in line_sets[1:]):
        raise ReleaseError("source run の candidate line snapshot が一致しません。")
    target_scenario_sha256 = next(iter(scenario_sha256s))
    target_lines = line_sets[0]

    if projection is not None:
        assert target_source is not None
        _validate_projection_against_target(
            projection=projection,
            target_source=target_source,
        )
        by_model[projection.source.model] = projection.source
    ordered_sources = [by_model[model] for model in sorted(by_model)]

    models = [source.bundle.manifest["models"][0] for source in ordered_sources]
    candidates = sorted(
        (
            dict(candidate)
            for source in ordered_sources
            for candidate in source.bundle.manifest["candidates"]
        ),
        key=lambda candidate: (_group_key(candidate), candidate["take_index"]),
    )
    failures = sorted(
        (
            dict(failure)
            for source in ordered_sources
            for failure in source.bundle.manifest["failures"]
        ),
        key=_group_key,
    )
    if projection is not None:
        failures = sorted(
            [*failures, *(dict(item) for item in projection.target_failures)],
            key=_group_key,
        )
    _require_unique_groups(candidates=candidates, failures=failures)
    candidate_set = build_candidate_set(
        scenario_sha256=target_scenario_sha256,
        lines=target_lines,
        models=models,
        candidates=candidates,
        failures=failures,
    )
    candidate_set_bytes = canonical_candidate_set_bytes(candidate_set)
    candidate_set_sha256 = hashlib.sha256(candidate_set_bytes).hexdigest()

    source_groups = sorted(
        (
            dict(group)
            for source in ordered_sources
            for group in source.curation_groups
        ),
        key=_group_key,
    )
    curation_document = {
        "format_version": 1,
        "rubric_version": "take-curation-v1",
        "candidate_set_sha256": candidate_set_sha256,
        "groups": source_groups,
    }
    curation_bytes = canonical_curation_bytes(curation_document)
    curation_sha256 = hashlib.sha256(curation_bytes).hexdigest()
    normalized_curation = validate_curation(
        json.loads(curation_bytes.decode("utf-8")),
    )
    curations = sorted(
        (
            _projection(group, curation_sha256=curation_sha256)
            for group in normalized_curation["groups"]
        ),
        key=_group_key,
    )
    manifest = validate_manifest_v4(
        {
            "format_version": 4,
            "generated_at": max(
                source.bundle.manifest["generated_at"] for source in ordered_sources
            ),
            "candidate_set_sha256": candidate_set_sha256,
            "models": models,
            "candidates": candidates,
            "curations": curations,
            "failures": failures,
        },
    )
    manifest_bytes = canonical_json(manifest).encode("utf-8")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    provenance_document: dict[str, Any] = {
        "format_version": (
            PROJECTED_PROVENANCE_FORMAT_VERSION
            if projection is not None
            else PROVENANCE_FORMAT_VERSION
        ),
        "candidate_set_sha256": candidate_set_sha256,
        "manifest_sha256": manifest_sha256,
        "runs": [
            {
                "model": source.model,
                "run_id": source.run_id,
                "ledger_sha256": source.ledger_sha256,
                "qc_report_sha256": source.qc_report_sha256,
                "manifest_sha256": source.manifest_sha256,
                "candidate_set_sha256": source.bundle.candidate_set_sha256,
            }
            for source in ordered_sources
        ],
    }
    if projection is not None:
        provenance_document.update(
            projection_plan_sha256=projection.plan_sha256,
            projection={
                "model": projection.source.model,
                "source_release_path": projection.source_release_path,
                "source_release_manifest_sha256": (
                    projection.source_release_manifest_sha256
                ),
                "source_release_candidate_set_sha256": (
                    projection.source_release.manifest["candidate_set_sha256"]
                ),
                "source_release_provenance_sha256": (
                    projection.source_release_provenance_sha256
                ),
                "source_release_curation_sha256": (
                    projection.source_release_curation_sha256
                ),
                "source_curation_groups_sha256": (
                    projection.source_curation_groups_sha256
                ),
                "source_scenario_sha256": (
                    projection.source.bundle.candidate_set["scenario_sha256"]
                ),
                "target_scenario_sha256": target_scenario_sha256,
                "target_failures": [
                    dict(item) for item in projection.target_failures
                ],
            },
        )
    provenance = validate_release_provenance(
        provenance_document,
    )
    provenance_bytes = canonical_json(provenance).encode("utf-8")
    return {
        "candidate_set": candidate_set,
        "candidate_set_bytes": candidate_set_bytes,
        "candidate_set_sha256": candidate_set_sha256,
        "curation": normalized_curation,
        "curation_bytes": curation_bytes,
        "curation_sha256": curation_sha256,
        "manifest": manifest,
        "manifest_bytes": manifest_bytes,
        "manifest_sha256": manifest_sha256,
        "provenance": provenance,
        "provenance_bytes": provenance_bytes,
        "projection_plan": projection.plan if projection is not None else None,
        "projection_plan_bytes": (
            projection.plan_bytes if projection is not None else None
        ),
    }


def _write_release(
    *,
    output_dir: Path,
    takes_root: Path,
    material: Mapping[str, Any],
) -> ReleaseFinalizeSummary:
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.",
            dir=output_dir.resolve().parent,
        ),
    )
    try:
        _write_new_file(stage / "candidate-set.json", material["candidate_set_bytes"])
        _write_new_file(
            stage / "candidate-set.sha256",
            material["candidate_set_sha256"].encode("ascii"),
        )
        _write_new_file(
            stage / "curation" / f"{material['curation_sha256']}.json",
            material["curation_bytes"],
        )
        _write_new_file(stage / "manifest-v4.json", material["manifest_bytes"])
        _write_new_file(
            stage / "manifest-v4.sha256",
            material["manifest_sha256"].encode("ascii"),
        )
        _write_new_file(
            stage / "release-provenance.json",
            material["provenance_bytes"],
        )
        _write_new_file(
            stage / "release-provenance.sha256",
            hashlib.sha256(material["provenance_bytes"]).hexdigest().encode("ascii"),
        )
        projection_plan_bytes = material["projection_plan_bytes"]
        if projection_plan_bytes is not None:
            _write_new_file(stage / "projection-plan.json", projection_plan_bytes)
            _write_new_file(
                stage / "projection-plan.sha256",
                hashlib.sha256(projection_plan_bytes).hexdigest().encode("ascii"),
            )
        validated = validate_finalized_release(
            release_dir=stage,
            takes_root=takes_root,
        )
        if (
            validated.manifest != material["manifest"]
            or validated.candidate_set != material["candidate_set"]
            or validated.provenance != material["provenance"]
            or validated.curation != material["curation"]
            or validated.projection_plan != material["projection_plan"]
        ):
            raise ReleaseError(
                "書き込み後の release 検証結果が入力 material と一致しません。",
            )
        os.replace(stage, output_dir)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    selected_count = sum(
        projection["decision"] == "selected"
        for projection in material["manifest"]["curations"]
    )
    skipped_count = sum(
        projection["decision"] == "skipped"
        for projection in material["manifest"]["curations"]
    )
    return ReleaseFinalizeSummary(
        output_dir=output_dir,
        manifest_sha256=material["manifest_sha256"],
        candidate_set_sha256=material["candidate_set_sha256"],
        curation_sha256=material["curation_sha256"],
        model_count=len(material["manifest"]["models"]),
        candidate_count=len(material["manifest"]["candidates"]),
        selected_count=selected_count,
        skipped_count=skipped_count,
        failure_count=len(material["manifest"]["failures"]),
    )


def validate_release_provenance(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ReleaseError("release provenance は object が必要です。")
    format_version = document.get("format_version")
    if format_version == PROVENANCE_FORMAT_VERSION:
        root = _exact(
            document,
            PROVENANCE_V1_ROOT_FIELDS,
            "release provenance",
        )
    elif format_version == PROJECTED_PROVENANCE_FORMAT_VERSION:
        root = _exact(
            document,
            PROVENANCE_V2_ROOT_FIELDS,
            "release provenance",
        )
    else:
        raise ReleaseError("release provenance format_version は1または2が必要です。")
    candidate_set_sha256 = _sha(
        root["candidate_set_sha256"],
        "release provenance candidate_set_sha256",
    )
    manifest_sha256 = _sha(
        root["manifest_sha256"],
        "release provenance manifest_sha256",
    )
    if not isinstance(root["runs"], list) or not root["runs"]:
        raise ReleaseError("release provenance runs は非空の配列が必要です。")
    runs: list[dict[str, str]] = []
    for index, value in enumerate(root["runs"]):
        field = f"release provenance runs[{index}]"
        run = _exact(value, PROVENANCE_RUN_FIELDS, field)
        runs.append(
            {
                "model": _path_segment(run["model"], f"{field}.model"),
                "run_id": _path_segment(run["run_id"], f"{field}.run_id"),
                "ledger_sha256": _sha(
                    run["ledger_sha256"],
                    f"{field}.ledger_sha256",
                ),
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
    if runs != sorted(runs, key=lambda run: run["model"]):
        raise ReleaseError("release provenance runs は model 順が必要です。")
    if len({run["model"] for run in runs}) != len(runs):
        raise ReleaseError("release provenance model が重複しています。")
    if len({run["run_id"] for run in runs}) != len(runs):
        raise ReleaseError("release provenance run_id が重複しています。")
    normalized: dict[str, Any] = {
        "format_version": format_version,
        "candidate_set_sha256": candidate_set_sha256,
        "manifest_sha256": manifest_sha256,
        "runs": runs,
    }
    if format_version == PROJECTED_PROVENANCE_FORMAT_VERSION:
        projection = _exact(
            root["projection"],
            PROJECTION_PROVENANCE_FIELDS,
            "release provenance projection",
        )
        model = _path_segment(
            projection["model"],
            "release provenance projection.model",
        )
        failures = _validate_projected_failures(
            projection["target_failures"],
            model=model,
            field="release provenance projection.target_failures",
        )
        normalized.update(
            projection_plan_sha256=_sha(
                root["projection_plan_sha256"],
                "release provenance projection_plan_sha256",
            ),
            projection={
                "model": model,
                "source_release_path": _repository_relative_path(
                    projection["source_release_path"],
                    "release provenance projection.source_release_path",
                ),
                "source_release_manifest_sha256": _sha(
                    projection["source_release_manifest_sha256"],
                    "release provenance projection."
                    "source_release_manifest_sha256",
                ),
                "source_release_candidate_set_sha256": _sha(
                    projection["source_release_candidate_set_sha256"],
                    "release provenance projection."
                    "source_release_candidate_set_sha256",
                ),
                "source_release_provenance_sha256": _sha(
                    projection["source_release_provenance_sha256"],
                    "release provenance projection."
                    "source_release_provenance_sha256",
                ),
                "source_release_curation_sha256": _sha(
                    projection["source_release_curation_sha256"],
                    "release provenance projection."
                    "source_release_curation_sha256",
                ),
                "source_curation_groups_sha256": _sha(
                    projection["source_curation_groups_sha256"],
                    "release provenance projection."
                    "source_curation_groups_sha256",
                ),
                "source_scenario_sha256": _sha(
                    projection["source_scenario_sha256"],
                    "release provenance projection.source_scenario_sha256",
                ),
                "target_scenario_sha256": _sha(
                    projection["target_scenario_sha256"],
                    "release provenance projection.target_scenario_sha256",
                ),
                "target_failures": failures,
            },
        )
    return normalized


def _load_provenance(release_root: Path) -> dict[str, Any]:
    path = release_root / "release-provenance.json"
    raw = _read_bytes(path, "release provenance")
    document = _read_json_bytes(raw, path, "release provenance")
    provenance = validate_release_provenance(document)
    if raw != canonical_json(provenance).encode("utf-8"):
        raise ReleaseError("release provenance は canonical bytes が必要です。")
    _verify_marker(
        release_root / "release-provenance.sha256",
        hashlib.sha256(raw).hexdigest(),
        "release provenance",
    )
    return provenance


def _load_release_projection_plan(
    *,
    release_root: Path,
    provenance: Mapping[str, Any],
) -> dict[str, Any] | None:
    plan_path = release_root / "projection-plan.json"
    marker_path = release_root / "projection-plan.sha256"
    if provenance["format_version"] == PROVENANCE_FORMAT_VERSION:
        if plan_path.exists() or marker_path.exists():
            raise ReleaseError(
                "format_version=1 release に projection plan は置けません。",
            )
        return None
    if not plan_path.is_file() or not marker_path.is_file():
        raise ReleaseError(
            "projected release に projection plan と SHA marker が必要です。",
        )
    raw = _read_bytes(plan_path, "release projection plan")
    plan = validate_projection_plan(
        _read_json_bytes(raw, plan_path, "release projection plan"),
    )
    if raw != canonical_json(plan).encode("utf-8"):
        raise ReleaseError("release projection plan は canonical bytes が必要です。")
    plan_sha256 = hashlib.sha256(raw).hexdigest()
    _verify_marker(marker_path, plan_sha256, "release projection plan")
    if provenance["projection_plan_sha256"] != plan_sha256:
        raise ReleaseError(
            "release provenance projection_plan_sha256 が plan と一致しません。",
        )
    return plan


def _validate_final_projection(
    *,
    bundle: SnapshotBundle,
    curation: Mapping[str, Any],
    provenance: Mapping[str, Any],
    run_by_model: Mapping[str, Mapping[str, str]],
    source_bundles: Mapping[str, SnapshotBundle],
    projection: _ProjectionSource,
) -> None:
    model = projection.source.model
    source_record_by_model = {
        item["model"]: item
        for item in projection.source_release.provenance["runs"]
    }
    if run_by_model.get(model) != source_record_by_model[model]:
        raise ReleaseError(
            "projected model の source run provenance が preserved release と"
            "一致しません。",
        )
    target_run_id = projection.plan["target_run_id"]
    target_models = [
        item["model"]
        for item in provenance["runs"]
        if item["run_id"] == target_run_id
    ]
    if len(target_models) != 1 or target_models[0] == model:
        raise ReleaseError(
            "projection target_run_id が通常 source run を参照していません。",
        )
    target_model = target_models[0]
    target_bundle = source_bundles[target_model]
    if (
        target_bundle.candidate_set["scenario_sha256"]
        != bundle.candidate_set["scenario_sha256"]
        or target_bundle.candidate_set["lines"]
        != bundle.candidate_set["lines"]
    ):
        raise ReleaseError(
            "projected release の target scenario/line snapshot が target run と"
            "一致しません。",
        )
    target_record = run_by_model[target_model]
    target_source = _SourceRun(
        run_id=target_record["run_id"],
        model=target_model,
        root=Path(),
        ledger={},
        bundle=target_bundle,
        ledger_sha256=target_record["ledger_sha256"],
        qc_report_sha256=target_record["qc_report_sha256"],
        manifest_sha256=target_record["manifest_sha256"],
        curation_groups=(),
    )
    _validate_projection_against_target(
        projection=projection,
        target_source=target_source,
    )

    projected_groups = sorted(
        (
            dict(group)
            for group in projection.source_release.curation["groups"]
            if group["model"] == model
        ),
        key=_group_key,
    )
    final_groups = sorted(
        (
            dict(group)
            for group in curation["groups"]
            if group["model"] == model
        ),
        key=_group_key,
    )
    if final_groups != projected_groups:
        raise ReleaseError(
            "projected model の curation groups が preserved release と"
            "一致しません。",
        )

    expected_projection = {
        "model": model,
        "source_release_path": projection.source_release_path,
        "source_release_manifest_sha256": (
            projection.source_release_manifest_sha256
        ),
        "source_release_candidate_set_sha256": (
            projection.source_release.manifest["candidate_set_sha256"]
        ),
        "source_release_provenance_sha256": (
            projection.source_release_provenance_sha256
        ),
        "source_release_curation_sha256": (
            projection.source_release_curation_sha256
        ),
        "source_curation_groups_sha256": (
            projection.source_curation_groups_sha256
        ),
        "source_scenario_sha256": (
            projection.source.bundle.candidate_set["scenario_sha256"]
        ),
        "target_scenario_sha256": bundle.candidate_set["scenario_sha256"],
        "target_failures": [
            dict(item) for item in projection.target_failures
        ],
    }
    if provenance["projection"] != expected_projection:
        raise ReleaseError(
            "release projection provenance が入力materialと一致しません。",
        )


def _load_release_curation(
    *,
    release_root: Path,
    manifest: Mapping[str, Any],
    candidate_set_sha256: str,
) -> dict[str, Any]:
    curation_hashes = {
        projection["curation_sha256"] for projection in manifest["curations"]
    }
    if len(curation_hashes) != 1:
        raise ReleaseError("release manifest は単一の aggregate curation が必要です。")
    curation_sha256 = next(iter(curation_hashes))
    curation_root = _require_directory(
        release_root / "curation",
        "release curation directory",
    )
    expected_name = f"{curation_sha256}.json"
    inventory = sorted(
        path.name for path in curation_root.iterdir() if path.is_file()
    )
    if inventory != [expected_name]:
        raise ReleaseError("release curation directory の inventory が不正です。")
    path = curation_root / expected_name
    raw = _read_bytes(path, "release curation")
    if hashlib.sha256(raw).hexdigest() != curation_sha256:
        raise ReleaseError("release curation SHA が filename と一致しません。")
    document = validate_curation(
        _read_json_bytes(raw, path, "release curation"),
    )
    if raw != canonical_json(document).encode("utf-8"):
        raise ReleaseError("release curation は canonical bytes が必要です。")
    if document["candidate_set_sha256"] != candidate_set_sha256:
        raise ReleaseError("release curation は stale candidate set を参照しています。")

    candidates_by_group: dict[
        tuple[str, str, str, str],
        dict[str, Mapping[str, Any]],
    ] = {}
    for candidate in manifest["candidates"]:
        candidates_by_group.setdefault(_group_key(candidate), {})[
            candidate["take_id"]
        ] = candidate
    groups_by_identity = {
        _group_key(group): group for group in document["groups"]
    }
    if set(groups_by_identity) != set(candidates_by_group):
        raise ReleaseError(
            "release curation は全 candidate group を完全に被覆する必要があります。",
        )
    projections_by_identity = {
        _group_key(projection): projection for projection in manifest["curations"]
    }
    for identity, group in groups_by_identity.items():
        exported = {
            candidate["take_id"]: candidate for candidate in group["candidates"]
        }
        expected = candidates_by_group[identity]
        if set(exported) != set(expected):
            raise ReleaseError(
                f"release curation candidate coverage が不正です: {identity}",
            )
        for take_id, item in exported.items():
            candidate = expected[take_id]
            if (
                item["path"] != candidate["path"]
                or item["audio_sha256"] != candidate["sha256"]
            ):
                raise ReleaseError(
                    f"release curation candidate が manifest と一致しません: {take_id}",
                )
        expected_projection = _projection(
            group,
            curation_sha256=curation_sha256,
        )
        if projections_by_identity.get(identity) != expected_projection:
            raise ReleaseError(
                f"release curation projection が manifest と一致しません: {identity}",
            )
        decision = group["decision"]
        if decision["type"] == "selected":
            selected = exported[decision["take_id"]]
            if (
                not selected["rubric"]["adoptable"]
                or not selected["rubric"]["content_correct"]
            ):
                raise ReleaseError(
                    "selected candidate は adoptable/content_correct が必要です。",
                )
    return document


def _validate_release_failures_against_source(
    *,
    model: str,
    release_candidates: Sequence[Mapping[str, Any]],
    release_failures: Sequence[Mapping[str, Any]],
    source_manifest: Mapping[str, Any],
    projected_failures: Sequence[Mapping[str, Any]] = (),
) -> None:
    release_candidate_groups = {_group_key(item) for item in release_candidates}
    release_failure_by_group = {
        _group_key(item): item for item in release_failures
    }
    source_candidate_groups = {
        _group_key(item) for item in source_manifest["candidates"]
    }
    source_failure_by_group = {
        _group_key(item): item for item in source_manifest["failures"]
    }
    projected_failure_by_group = {
        _group_key(item): item for item in projected_failures
    }
    if len(projected_failure_by_group) != len(projected_failures):
        raise ReleaseError("projected failure が重複しています。")
    if set(projected_failure_by_group) & (
        source_candidate_groups | set(source_failure_by_group)
    ):
        raise ReleaseError("projected failure が source group と競合しています。")
    for identity, failure in release_failure_by_group.items():
        if identity in projected_failure_by_group:
            if projected_failure_by_group[identity] != failure:
                raise ReleaseError(
                    f"projected failure が plan と一致しません: {identity}",
                )
            continue
        reason = failure["reason"]
        if reason == "no_eligible_take":
            if source_failure_by_group.get(identity) != failure:
                raise ReleaseError(
                    f"release failure が source run と一致しません: {identity}",
                )
        elif reason == "test_only_adapter":
            if model != "dummy" or identity not in source_candidate_groups:
                raise ReleaseError(
                    f"test_only_adapter failure の source candidate がありません: "
                    f"{identity}",
                )
        else:
            raise ReleaseError(f"未知の release failure reason です: {reason}")
    omitted_source_candidates = source_candidate_groups - release_candidate_groups
    test_only_groups = {
        identity
        for identity, failure in release_failure_by_group.items()
        if failure["reason"] == "test_only_adapter"
    }
    if omitted_source_candidates != test_only_groups:
        raise ReleaseError(
            f"source candidate の release 投影が不完全です: {model}",
        )
    expected_candidates = {
        canonical_json(candidate)
        for candidate in source_manifest["candidates"]
        if _group_key(candidate) not in test_only_groups
    }
    actual_candidates = {
        canonical_json(candidate) for candidate in release_candidates
    }
    if actual_candidates != expected_candidates:
        raise ReleaseError(
            f"source candidate の release 投影が exact ではありません: {model}",
        )
    if set(source_failure_by_group) != {
        identity
        for identity, failure in release_failure_by_group.items()
        if failure["reason"] == "no_eligible_take"
        and identity not in projected_failure_by_group
    }:
        raise ReleaseError(f"source failure の release 投影が不完全です: {model}")
    actual_projected = {
        identity
        for identity in release_failure_by_group
        if identity in projected_failure_by_group
    }
    if actual_projected != set(projected_failure_by_group):
        raise ReleaseError(f"projected failure の release 投影が不完全です: {model}")


def _verify_source_record_files(
    *,
    run_root: Path,
    record: Mapping[str, str],
) -> None:
    for name, key, label in (
        ("ledger.json", "ledger_sha256", "source ledger"),
        ("qc-report.json", "qc_report_sha256", "source QC report"),
        ("manifest-v4.json", "manifest_sha256", "source manifest"),
    ):
        if _file_sha256(run_root / name) != record[key]:
            raise ReleaseError(
                f"{label} SHA が release provenance と一致しません: "
                f"{record['run_id']}",
            )


def _require_unique_groups(
    *,
    candidates: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
) -> None:
    candidate_groups = [_group_key(item) for item in candidates]
    failure_groups = [_group_key(item) for item in failures]
    if len(candidate_groups) != len(set(candidate_groups)):
        raise ReleaseError("release candidate group が複数 run で競合しています。")
    if len(failure_groups) != len(set(failure_groups)):
        raise ReleaseError("release failure group が複数 run で競合しています。")
    if set(candidate_groups) & set(failure_groups):
        raise ReleaseError("release candidate/failure group が競合しています。")


def _items_by_model(
    items: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        result.setdefault(str(item["model"]), []).append(dict(item))
    return result


def _group_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(value[key]) for key in GROUP_KEYS)  # type: ignore[return-value]


def _group_shape(value: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(value["scenario"]),
        str(value["line"]),
        str(value["variant"]),
    )


def _validate_projected_failures(
    value: Any,
    *,
    model: str,
    field: str,
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ReleaseError(f"{field} は非空の配列が必要です。")
    failures: list[dict[str, str]] = []
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        failure = _exact(item, FAILURE_FIELDS, item_field)
        normalized = {
            key: _path_segment(failure[key], f"{item_field}.{key}")
            for key in GROUP_KEYS
        }
        if normalized["model"] != model:
            raise ReleaseError(
                f"{item_field}.model が preserved model と一致しません。",
            )
        if failure["reason"] != "no_eligible_take":
            raise ReleaseError(
                f"{item_field}.reason は no_eligible_take が必要です。",
            )
        failures.append({**normalized, "reason": "no_eligible_take"})
    if failures != sorted(failures, key=_group_key):
        raise ReleaseError(f"{field} は group 順が必要です。")
    if len({_group_key(item) for item in failures}) != len(failures):
        raise ReleaseError(f"{field} が重複しています。")
    return failures


def _repository_relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ReleaseError(f"{field} は repository-relative path が必要です。")
    segments = value.split("/")
    if any(PATH_SEGMENT.fullmatch(segment) is None for segment in segments):
        raise ReleaseError(f"{field} は安全な repository-relative path が必要です。")
    return "/".join(segments)


def _resolve_repository_child(
    *,
    repository_root: Path,
    relative_path: str,
    label: str,
) -> Path:
    root = _require_directory(repository_root, "repository root")
    candidate = root.joinpath(*relative_path.split("/"))
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise ReleaseError(f"{label} が存在しません: {candidate}") from error
    if not resolved.is_relative_to(root) or not resolved.is_dir():
        raise ReleaseError(
            f"{label} は repository 内の directory が必要です: {candidate}",
        )
    return resolved


def _projected_repository_root(takes_root: Path) -> Path:
    resolved = _require_directory(takes_root, "takes root")
    if resolved.name != "takes" or resolved.parent.name != "artifacts":
        raise ReleaseError(
            "projected release は <repository>/artifacts/takes を"
            "takes root として使用する必要があります。",
        )
    return resolved.parent.parent


def _resolve_direct_child(root: Path, name: str, label: str) -> Path:
    candidate = root / name
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise ReleaseError(f"{label} が存在しません: {candidate}") from error
    if resolved.parent != root or not resolved.is_dir():
        raise ReleaseError(f"{label} は root 直下の directory が必要です: {candidate}")
    return resolved


def _require_directory(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise ReleaseError(f"{label} が存在しません: {path}") from error
    if not resolved.is_dir():
        raise ReleaseError(f"{label} は directory が必要です: {path}")
    return resolved


def _write_new_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise ReleaseError(f"release file が既に存在します: {path}") from error


def _verify_marker(path: Path, expected: str, label: str) -> None:
    raw = _read_bytes(path, f"{label} SHA marker")
    try:
        marker = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise ReleaseError(f"{label} SHA marker が ASCII ではありません。") from error
    if marker != expected:
        raise ReleaseError(f"{label} SHA marker が一致しません。")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(_read_bytes(path, str(path))).hexdigest()


def _read_json(path: Path, label: str) -> Any:
    raw = _read_bytes(path, label)
    return _read_json_bytes(raw, path, label)


def _read_json_bytes(raw: bytes, path: Path, label: str) -> Any:
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseError(f"{label} をJSONとして読めません: {path}") from error


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReleaseError(f"{label} が存在しません: {path}")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ReleaseError(f"{label} を読み込めません: {path}") from error


def _exact(value: Any, fields: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ReleaseError(f"{field} の項目が不正です。")
    return value


def _path_segment(value: Any, field: str) -> str:
    if not isinstance(value, str) or PATH_SEGMENT.fullmatch(value) is None:
        raise ReleaseError(f"{field} は path segment が必要です。")
    return value


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in HEX for character in value)
    ):
        raise ReleaseError(f"{field} は完全な SHA-256 が必要です。")
    return value
