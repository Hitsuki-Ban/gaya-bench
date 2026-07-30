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


PROVENANCE_FORMAT_VERSION = 1
PROVENANCE_ROOT_FIELDS = {
    "format_version",
    "candidate_set_sha256",
    "manifest_sha256",
    "runs",
}
PROVENANCE_RUN_FIELDS = {
    "model",
    "run_id",
    "ledger_sha256",
    "qc_report_sha256",
    "manifest_sha256",
    "candidate_set_sha256",
}
HEX = frozenset("0123456789abcdef")
PATH_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def finalize_release(
    *,
    run_ids: Sequence[str],
    artifacts_dir: Path,
    data_dir: Path,
    scenarios_dir: Path,
    output_dir: Path,
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
    run_roots = [
        _resolve_direct_child(takes_root, run_id, f"source run {run_id}")
        for run_id in ordered_run_ids
    ]

    try:
        with ExitStack() as locks:
            for run_root in run_roots:
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
            material = _build_release_material(sources)
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
        )
        run_roots[model] = run_root
        source_manifests[model] = source_bundle.manifest

    return FinalizedRelease(
        root=release_root,
        manifest=bundle.manifest,
        candidate_set=bundle.candidate_set,
        provenance=provenance,
        curation=curation,
        run_roots=run_roots,
        source_manifests=source_manifests,
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


def _build_release_material(sources: Sequence[_SourceRun]) -> dict[str, Any]:
    by_model = {source.model: source for source in sources}
    if len(by_model) != len(sources):
        raise ReleaseError("release finalize は model ごとに1 runだけを受理します。")
    ordered_sources = [by_model[model] for model in sorted(by_model)]
    scenario_sha256s = {
        source.bundle.candidate_set["scenario_sha256"]
        for source in ordered_sources
    }
    if len(scenario_sha256s) != 1:
        raise ReleaseError(
            "source run の scenario source selection が一致しません。",
        )
    line_sets = [
        source.bundle.candidate_set["lines"] for source in ordered_sources
    ]
    if any(lines != line_sets[0] for lines in line_sets[1:]):
        raise ReleaseError("source run の candidate line snapshot が一致しません。")

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
    _require_unique_groups(candidates=candidates, failures=failures)
    candidate_set = build_candidate_set(
        scenario_sha256=next(iter(scenario_sha256s)),
        lines=line_sets[0],
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
    provenance = validate_release_provenance(
        {
            "format_version": PROVENANCE_FORMAT_VERSION,
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
        },
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
        validated = validate_finalized_release(
            release_dir=stage,
            takes_root=takes_root,
        )
        if (
            validated.manifest != material["manifest"]
            or validated.candidate_set != material["candidate_set"]
            or validated.provenance != material["provenance"]
            or validated.curation != material["curation"]
        ):
            raise ReleaseError("書き込み後の release 検証結果が入力 material と一致しません。")
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
    root = _exact(document, PROVENANCE_ROOT_FIELDS, "release provenance")
    if root["format_version"] != PROVENANCE_FORMAT_VERSION:
        raise ReleaseError("release provenance format_version は1が必要です。")
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
    return {
        "format_version": PROVENANCE_FORMAT_VERSION,
        "candidate_set_sha256": candidate_set_sha256,
        "manifest_sha256": manifest_sha256,
        "runs": runs,
    }


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
    for identity, failure in release_failure_by_group.items():
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
    }:
        raise ReleaseError(f"source failure の release 投影が不完全です: {model}")


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
