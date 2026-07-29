from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from gaya_pipeline.audio import PostprocessProfile
from gaya_pipeline.qc_report import (
    QCAuthority,
    QCReportError,
    validate_qc_report,
)
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
from gaya_pipeline.take_sidecar import TakeSidecarError, validate_take_sidecar
from gaya_pipeline.validation import validate_scenario_ids


class CurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CurationSummary:
    curation_sha256: str
    artifact_path: Path
    snapshot_path: Path
    candidate_set_path: Path
    candidate_set_marker_path: Path
    group_count: int
    added_projection_count: int


@dataclass(frozen=True)
class SnapshotBundle:
    manifest: dict[str, Any]
    candidate_set: dict[str, Any]
    candidate_set_bytes: bytes
    candidate_set_sha256: str


FORMAT_VERSION = 1
RUBRIC_VERSION = "take-curation-v1"
GROUP_KEYS = ("model", "scenario", "line", "variant")
CURATION_ROOT_FIELDS = {
    "format_version",
    "rubric_version",
    "candidate_set_sha256",
    "groups",
}
GROUP_FIELDS = {*GROUP_KEYS, "candidates", "decision"}
CANDIDATE_FIELDS = {"take_id", "path", "audio_sha256", "rubric"}
RUBRIC_FIELDS = {
    "content_correct",
    "intent_match",
    "character_naturalness",
    "adoptable",
}
CANDIDATE_SET_ROOT_FIELDS = {
    "format_version",
    "scenario_sha256",
    "lines",
    "models",
    "candidates",
    "failures",
}
LINE_FIELDS = {"scenario", "line", "scenario_title", "text", "delivery"}
HEX = frozenset("0123456789abcdef")


def validate_candidate_set(document: Any) -> dict[str, Any]:
    root = _exact(document, CANDIDATE_SET_ROOT_FIELDS, "candidate set")
    if root["format_version"] != 4:
        raise CurationError("candidate set format_version は 4 が必要です。")
    _sha(root["scenario_sha256"], "candidate set scenario_sha256")
    for key in ("lines", "models", "candidates", "failures"):
        if not isinstance(root[key], list):
            raise CurationError(f"candidate set {key} は配列が必要です。")

    lines: list[dict[str, Any]] = []
    seen_lines: set[tuple[str, str]] = set()
    scenario_titles: dict[str, str] = {}
    for index, value in enumerate(root["lines"]):
        field = f"candidate set lines[{index}]"
        line = _exact(value, LINE_FIELDS, field)
        scenario = _path_segment(line["scenario"], f"{field}.scenario")
        line_id = _path_segment(line["line"], f"{field}.line")
        normalized = {
            "scenario": scenario,
            "line": line_id,
            "scenario_title": _text(
                line["scenario_title"],
                f"{field}.scenario_title",
            ),
            "text": _text(line["text"], f"{field}.text"),
            "delivery": _text(line["delivery"], f"{field}.delivery"),
        }
        identity = (scenario, line_id)
        if identity in seen_lines:
            raise CurationError("candidate set line が重複しています。")
        seen_lines.add(identity)
        previous_title = scenario_titles.setdefault(
            scenario,
            normalized["scenario_title"],
        )
        if previous_title != normalized["scenario_title"]:
            raise CurationError("同じ scenario の title が一致しません。")
        lines.append(normalized)
    if lines != sorted(lines, key=lambda line: (line["scenario"], line["line"])):
        raise CurationError("candidate set lines は scenario/line 順が必要です。")

    pseudo_manifest = {
        "format_version": 4,
        "generated_at": "candidate-set-validation",
        "candidate_set_sha256": "0" * 64,
        "models": root["models"],
        "candidates": root["candidates"],
        "curations": [],
        "failures": root["failures"],
    }
    try:
        validate_manifest_v4(pseudo_manifest)
    except (TakeManifestError, TakeLedgerError) as error:
        raise CurationError(f"candidate set の v4 内容が不正です: {error}") from error

    covered_lines = {
        (item["scenario"], item["line"])
        for key in ("candidates", "failures")
        for item in root[key]
    }
    if seen_lines != covered_lines:
        raise CurationError(
            "candidate set lines は candidates/failures の scenario/line 集合を"
            "完全に被覆する必要があります。",
        )
    try:
        canonical_json(root)
    except (TypeError, ValueError) as error:
        raise CurationError("candidate set が canonical JSON 契約を満たしません。") from error
    return root


def canonical_candidate_set_bytes(document: Any) -> bytes:
    return canonical_json(validate_candidate_set(document)).encode("utf-8")


def build_candidate_set(
    *,
    scenario_sha256: str,
    lines: list[dict[str, Any]],
    models: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    document = {
        "format_version": 4,
        "scenario_sha256": scenario_sha256,
        "lines": sorted(
            lines,
            key=lambda line: (line["scenario"], line["line"]),
        ),
        "models": models,
        "candidates": candidates,
        "failures": failures,
    }
    return validate_candidate_set(document)


def load_authoritative_candidate_lines(
    *,
    scenarios_dir: Path,
    ledger_source: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    groups = ledger_source["groups"]
    requested_lines = {
        (str(group["scenario"]), str(group["line"])) for group in groups
    }
    scenario_ids = sorted({scenario for scenario, _line in requested_lines})
    validation = validate_scenario_ids(scenarios_dir, scenario_ids)
    if validation.problems:
        details = "\n".join(str(problem) for problem in validation.problems)
        raise CurationError(f"scenario 検証に失敗しました:\n{details}")

    source_files: list[dict[str, str]] = []
    lines: list[dict[str, Any]] = []
    found: set[tuple[str, str]] = set()
    for scenario_id in scenario_ids:
        scenario_path = scenarios_dir / f"{scenario_id}.yaml"
        try:
            source_bytes = scenario_path.read_bytes()
            document = yaml.safe_load(source_bytes.decode("utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise CurationError(
                f"scenario source を読み込めません: {scenario_path}: {error}",
            ) from error
        if not isinstance(document, dict) or document.get("id") != scenario_id:
            raise CurationError(
                f"scenario source id が一致しません: {scenario_path}",
            )
        source_files.append(
            {
                "path": scenario_path.name,
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
            },
        )
        for line in document["lines"]:
            identity = (scenario_id, str(line["id"]))
            if identity not in requested_lines:
                continue
            if identity in found:
                raise CurationError("ledger source line が scenario 内で重複しています。")
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
    if found != requested_lines:
        missing = sorted(requested_lines - found)
        raise CurationError(f"ledger source line が current scenario にありません: {missing}")
    scenario_sha256 = hashlib.sha256(
        canonical_json(source_files).encode("utf-8"),
    ).hexdigest()
    if scenario_sha256 != ledger_source["scenario_sha256"]:
        raise CurationError("current scenario source SHA-256 が ledger と一致しません。")
    return scenario_sha256, sorted(
        lines,
        key=lambda line: (line["scenario"], line["line"]),
    )


def validate_snapshot_bundle(
    *,
    snapshot_path: Path,
    candidate_set_path: Path,
    marker_path: Path,
) -> SnapshotBundle:
    manifest = _read_manifest(snapshot_path)
    candidate_set_raw = _read_bytes(candidate_set_path, "authoritative candidate set")
    candidate_set = _read_json_bytes(
        candidate_set_raw,
        candidate_set_path,
        "authoritative candidate set",
    )
    canonical_bytes = canonical_candidate_set_bytes(candidate_set)
    if candidate_set_raw != canonical_bytes:
        raise CurationError("candidate-set.json は canonical bytes である必要があります。")
    marker = _read_marker(marker_path)
    candidate_set_sha256 = hashlib.sha256(candidate_set_raw).hexdigest()
    if marker != candidate_set_sha256:
        raise CurationError("candidate-set.sha256 が candidate-set.json と一致しません。")
    if manifest["candidate_set_sha256"] != candidate_set_sha256:
        raise CurationError(
            "manifest.candidate_set_sha256 が candidate-set.json と一致しません。",
        )
    for key in ("models", "candidates", "failures"):
        if candidate_set[key] != manifest[key]:
            raise CurationError(
                f"candidate-set.json の {key} が manifest と一致しません。",
            )
    return SnapshotBundle(
        manifest=manifest,
        candidate_set=candidate_set,
        candidate_set_bytes=candidate_set_raw,
        candidate_set_sha256=candidate_set_sha256,
    )


def validate_curation(document: Any) -> dict[str, Any]:
    root = _exact(document, CURATION_ROOT_FIELDS, "curation")
    if root["format_version"] != FORMAT_VERSION:
        raise CurationError("curation.format_version は 1 が必要です。")
    if root["rubric_version"] != RUBRIC_VERSION:
        raise CurationError(
            "curation.rubric_version は take-curation-v1 が必要です。",
        )
    candidate_set_sha256 = _sha(
        root["candidate_set_sha256"],
        "curation.candidate_set_sha256",
    )
    groups_value = root["groups"]
    if not isinstance(groups_value, list) or not groups_value:
        raise CurationError("curation.groups は 1 件以上の配列が必要です。")

    groups: list[dict[str, Any]] = []
    seen_groups: set[tuple[str, str, str, str]] = set()
    seen_candidates: set[str] = set()
    for index, value in enumerate(groups_value):
        field = f"curation.groups[{index}]"
        group = _exact(value, GROUP_FIELDS, field)
        identity = tuple(
            _path_segment(group[key], f"{field}.{key}") for key in GROUP_KEYS
        )
        if identity in seen_groups:
            raise CurationError("curation group が重複しています。")
        seen_groups.add(identity)

        candidates_value = group["candidates"]
        if not isinstance(candidates_value, list) or not candidates_value:
            raise CurationError(f"{field}.candidates は 1 件以上の配列が必要です。")
        candidates: list[dict[str, Any]] = []
        group_candidate_ids: set[str] = set()
        for candidate_index, candidate_value in enumerate(candidates_value):
            candidate_field = f"{field}.candidates[{candidate_index}]"
            candidate = _exact(
                candidate_value,
                CANDIDATE_FIELDS,
                candidate_field,
            )
            take_id = _sha(candidate["take_id"], f"{candidate_field}.take_id")
            if take_id in group_candidate_ids or take_id in seen_candidates:
                raise CurationError("curation candidate が重複しています。")
            group_candidate_ids.add(take_id)
            seen_candidates.add(take_id)
            candidates.append(
                {
                    "take_id": take_id,
                    "path": _text(candidate["path"], f"{candidate_field}.path"),
                    "audio_sha256": _sha(
                        candidate["audio_sha256"],
                        f"{candidate_field}.audio_sha256",
                    ),
                    "rubric": _validate_rubric(
                        candidate["rubric"],
                        f"{candidate_field}.rubric",
                    ),
                },
            )

        groups.append(
            {
                **dict(zip(GROUP_KEYS, identity, strict=True)),
                "candidates": sorted(
                    candidates,
                    key=lambda candidate: candidate["take_id"],
                ),
                "decision": _validate_decision(
                    group["decision"],
                    f"{field}.decision",
                ),
            },
        )

    groups.sort(key=lambda group: tuple(group[key] for key in GROUP_KEYS))
    return {
        "format_version": FORMAT_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "candidate_set_sha256": candidate_set_sha256,
        "groups": groups,
    }


def canonical_curation_bytes(document: Any) -> bytes:
    return canonical_json(validate_curation(document)).encode("utf-8")


def apply_curation(
    *,
    run_id: str,
    input_path: Path,
    artifacts_dir: Path,
    data_dir: Path,
    scenarios_dir: Path,
) -> CurationSummary:
    _path_segment(run_id, "run_id")
    artifacts_root = artifacts_dir.resolve()
    takes_root = (artifacts_root / "takes").resolve()
    run_root = (takes_root / run_id).resolve()
    if not run_root.is_relative_to(takes_root) or run_root.parent != takes_root:
        raise CurationError("run root が artifacts/takes の直下ではありません。")
    try:
        with exclusive_run_lock(run_root):
            return _apply_curation_transaction(
                run_id=run_id,
                input_path=input_path,
                data_dir=data_dir,
                scenarios_dir=scenarios_dir,
                run_root=run_root,
            )
    except RunLockError as error:
        raise CurationError(f"run lock に失敗しました: {error}") from error


def _apply_curation_transaction(
    *,
    run_id: str,
    input_path: Path,
    data_dir: Path,
    scenarios_dir: Path,
    run_root: Path,
) -> CurationSummary:
    ledger_path = run_root / "ledger.json"
    snapshot_path = run_root / "manifest-v4.json"
    candidate_set_path = run_root / "candidate-set.json"
    marker_path = run_root / "candidate-set.sha256"
    report_path = run_root / "qc-report.json"
    try:
        ledger = read_ledger(ledger_path)
    except (OSError, UnicodeError, json.JSONDecodeError, TakeLedgerError) as error:
        raise CurationError(f"run ledger を読み込めません: {ledger_path}: {error}") from error
    if ledger["run_id"] != run_id:
        raise CurationError("run_id と ledger.run_id が一致しません。")

    bundle = validate_snapshot_bundle(
        snapshot_path=snapshot_path,
        candidate_set_path=candidate_set_path,
        marker_path=marker_path,
    )
    if any(
        attempt["status"] not in TERMINAL_STATUSES
        for attempt in ledger["attempts"]
    ):
        raise CurationError(
            "curation apply には全 attempt が terminal の ledger が必要です。",
        )
    qc_authority = _load_qc_authority(
        report_path=report_path,
        ledger_path=ledger_path,
        ledger=ledger,
        manifest=bundle.manifest,
    )
    _validate_manifest_against_terminal_ledger(
        manifest=bundle.manifest,
        ledger=ledger,
        run_root=run_root,
        qc_authority=qc_authority,
    )
    scenario_sha256, lines = load_authoritative_candidate_lines(
        scenarios_dir=scenarios_dir.resolve(),
        ledger_source=ledger["source"],
    )
    if bundle.candidate_set["scenario_sha256"] != scenario_sha256:
        raise CurationError(
            "candidate-set.json の scenario SHA-256 が ledger/current source と"
            "一致しません。",
        )
    rebuilt = build_candidate_set(
        scenario_sha256=scenario_sha256,
        lines=lines,
        models=bundle.manifest["models"],
        candidates=bundle.manifest["candidates"],
        failures=bundle.manifest["failures"],
    )
    if canonical_candidate_set_bytes(rebuilt) != bundle.candidate_set_bytes:
        raise CurationError(
            "candidate-set.json が current scenario/manifest からの再構築結果と"
            "一致しません。",
        )

    normalized = validate_curation(_read_json(input_path, "curation artifact"))
    canonical_bytes = canonical_json(normalized).encode("utf-8")
    if normalized["candidate_set_sha256"] != bundle.candidate_set_sha256:
        raise CurationError("curation artifact は stale candidate set を参照しています。")

    candidates_by_group: dict[
        tuple[str, str, str, str],
        dict[str, dict[str, Any]],
    ] = {}
    for candidate in bundle.manifest["candidates"]:
        group = tuple(candidate[key] for key in GROUP_KEYS)
        candidates_by_group.setdefault(group, {})[candidate["take_id"]] = candidate

    curation_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
    artifact_root = data_dir.resolve() / "curation"
    artifact_path = artifact_root / f"{curation_sha256}.json"
    existing_by_group = _validate_existing_projections(
        manifest=bundle.manifest,
        artifact_root=artifact_root,
        candidate_set_sha256=bundle.candidate_set_sha256,
        candidates_by_group=candidates_by_group,
        run_root=run_root,
    )

    added_projection_count = 0
    for group in normalized["groups"]:
        identity = tuple(group[key] for key in GROUP_KEYS)
        snapshot_candidates = candidates_by_group.get(identity)
        if snapshot_candidates is None:
            raise CurationError(
                f"curation group が candidate set にありません: {_group_text(identity)}",
            )
        _validate_exported_candidates(
            group=group,
            snapshot_candidates=snapshot_candidates,
            run_root=run_root,
            identity=identity,
        )
        projection = _projection(group, curation_sha256=curation_sha256)
        existing = existing_by_group.get(identity)
        if existing is not None:
            if existing["group"] != group:
                raise CurationError(
                    f"curation projection は既存判断と競合しています: "
                    f"{_group_text(identity)}",
                )
            continue
        existing_by_group[identity] = {"projection": projection, "group": group}
        added_projection_count += 1

    updated_manifest = {
        **bundle.manifest,
        "curations": [
            existing_by_group[key]["projection"] for key in sorted(existing_by_group)
        ],
    }
    try:
        validate_manifest_v4(updated_manifest)
    except TakeManifestError as error:
        raise CurationError(f"curation projection が v4 契約を満たしません: {error}") from error

    if artifact_path.exists():
        existing_bytes = _read_bytes(artifact_path, "immutable curation artifact")
        if existing_bytes != canonical_bytes:
            raise CurationError(
                f"immutable curation artifact の bytes が競合しています: {artifact_path}",
            )
        _validate_immutable_artifact(
            artifact_path,
            expected_sha256=curation_sha256,
            candidate_set_sha256=bundle.candidate_set_sha256,
        )
    elif not added_projection_count:
        raise CurationError(
            "新規 projection がない curation artifact は未参照になるため書き込めません。",
        )

    _write_immutable_bytes(artifact_path, canonical_bytes)
    if added_projection_count:
        manifest_bytes = (
            json.dumps(
                updated_manifest,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        _write_bytes_atomic(snapshot_path, manifest_bytes)

    return CurationSummary(
        curation_sha256=curation_sha256,
        artifact_path=artifact_path,
        snapshot_path=snapshot_path,
        candidate_set_path=candidate_set_path,
        candidate_set_marker_path=marker_path,
        group_count=len(normalized["groups"]),
        added_projection_count=added_projection_count,
    )


def _load_qc_authority(
    *,
    report_path: Path,
    ledger_path: Path,
    ledger: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> QCAuthority:
    report = _read_json(report_path, "QC report")
    try:
        authority = validate_qc_report(
            report,
            ledger_path=ledger_path,
            ledger=ledger,
        )
    except QCReportError as error:
        raise CurationError(f"QC report が不正です: {error}") from error
    if report["generated_at"] != manifest["generated_at"]:
        raise CurationError("QC report generated_at が manifest と一致しません。")
    return authority


def _validate_manifest_against_terminal_ledger(
    *,
    manifest: Mapping[str, Any],
    ledger: Mapping[str, Any],
    run_root: Path,
    qc_authority: QCAuthority,
) -> None:
    if any(
        failure["reason"] != "no_eligible_take"
        for failure in manifest["failures"]
    ):
        raise CurationError(
            "source run manifest failure reason は no_eligible_take が必要です。",
        )
    attempts_by_slot: dict[
        tuple[str, str, str, str, int],
        Mapping[str, Any],
    ] = {}
    for attempt in ledger["attempts"]:
        if attempt["status"] not in TERMINAL_STATUSES:
            raise CurationError(
                "curation apply には全 attempt が terminal の ledger が必要です。",
            )
        slot = tuple(attempt[key] for key in GROUP_KEYS) + (attempt["take_index"],)
        attempts_by_slot[slot] = attempt

    eligible_by_slot = {
        slot: attempt
        for slot, attempt in attempts_by_slot.items()
        if attempt["status"] == "eligible"
    }
    candidates_by_slot = {
        tuple(candidate[key] for key in GROUP_KEYS) + (candidate["take_index"],): candidate
        for candidate in manifest["candidates"]
    }
    if set(candidates_by_slot) != set(eligible_by_slot):
        raise CurationError(
            "manifest candidate slot が eligible ledger attempt と exact に"
            "一致しません。",
        )

    source_groups = {
        tuple(group[key] for key in GROUP_KEYS)
        for group in ledger["source"]["groups"]
    }
    eligible_groups = {slot[:4] for slot in eligible_by_slot}
    candidate_groups = {slot[:4] for slot in candidates_by_slot}
    failure_groups = {
        tuple(failure[key] for key in GROUP_KEYS)
        for failure in manifest["failures"]
    }
    expected_failure_groups = source_groups - eligible_groups
    if failure_groups != expected_failure_groups:
        raise CurationError(
            "manifest failure group が eligible take のない ledger source group と"
            "exact に一致しません。",
        )
    if source_groups != candidate_groups | failure_groups:
        raise CurationError(
            "ledger source groups が candidate/failure groups で完全に被覆されて"
            "いません。",
        )

    for slot, candidate in candidates_by_slot.items():
        _validate_candidate_against_ledger_attempt(
            candidate=candidate,
            attempt=eligible_by_slot[slot],
            ledger=ledger,
            run_root=run_root,
            qc_attempt=qc_authority.attempts_by_slot[slot],
            gate_policy_version=qc_authority.gate_policy_version,
        )


def _validate_candidate_against_ledger_attempt(
    *,
    candidate: Mapping[str, Any],
    attempt: Mapping[str, Any],
    ledger: Mapping[str, Any],
    run_root: Path,
    qc_attempt: Mapping[str, Any],
    gate_policy_version: str,
) -> None:
    slot = tuple(attempt[key] for key in GROUP_KEYS) + (attempt["take_index"],)
    audio = attempt["audio"]
    expected_candidate = {
        "take_id": attempt["take_id"],
        "generation_input_sha256": attempt["generation_input_sha256"],
        "sha256": audio["opus_sha256"],
        "path": (
            f"audio/takes/{'/'.join(str(value) for value in slot[:4])}/"
            f"take-{slot[4]:04d}-{audio['opus_sha256']}.opus"
        ),
    }
    for key, expected in expected_candidate.items():
        if candidate[key] != expected:
            raise CurationError(
                f"manifest candidate {key} が eligible ledger attempt と"
                f"一致しません: {_slot_text(slot)}",
            )

    wav_path = _resolve_ledger_artifact(run_root, str(audio["wav_path"]))
    opus_path = _resolve_ledger_artifact(run_root, str(audio["opus_path"]))
    sidecar_path = opus_path.with_suffix(".json")
    for label, path in (
        ("WAV", wav_path),
        ("Opus", opus_path),
        ("sidecar", sidecar_path),
    ):
        if not path.is_file():
            raise CurationError(f"eligible ledger take {label} がありません: {path}")
    if _file_sha256(wav_path) != audio["wav_sha256"]:
        raise CurationError("eligible take WAV SHA が ledger と一致しません。")
    if _file_sha256(opus_path) != audio["opus_sha256"]:
        raise CurationError("eligible take Opus SHA が ledger と一致しません。")
    if _file_sha256(sidecar_path) != audio["sidecar_sha256"]:
        raise CurationError("eligible take sidecar SHA が ledger と一致しません。")
    try:
        sidecar = validate_take_sidecar(
            _read_json(sidecar_path, "eligible take sidecar"),
        )
    except TakeSidecarError as error:
        raise CurationError(f"eligible take sidecar が不正です: {error}") from error

    identity_keys = ("model", "scenario", "line", "variant", "take_index")
    if sidecar["run_id"] != ledger["run_id"] or any(
        sidecar[key] != attempt[key] for key in identity_keys
    ):
        raise CurationError("eligible take sidecar identity が ledger と一致しません。")
    for key in ("take_id", "generation_input_sha256"):
        if sidecar[key] != attempt[key]:
            raise CurationError(
                f"eligible take sidecar {key} が ledger と一致しません。",
            )
    if (
        sidecar["wav_sha256"] != audio["wav_sha256"]
        or sidecar["opus_sha256"] != audio["opus_sha256"]
    ):
        raise CurationError("eligible take sidecar audio SHA が ledger と一致しません。")

    generation = attempt["generation"]
    if (
        sidecar["take"]["seed"] != generation["seed"]
        or sidecar["take"]["sampling"] != generation["sampling"]
        or sidecar["rtf"] != generation["rtf"]
    ):
        raise CurationError(
            "eligible take sidecar generation provenance が ledger と一致しません。",
        )
    if sidecar["take"]["recipe_version"] != ledger["source"]["recipe_version"]:
        raise CurationError("eligible take sidecar recipe が ledger と一致しません。")
    mechanical = qc_attempt["mechanical"]
    sidecar_provenance = mechanical["sidecar_provenance"]
    expected_sidecar_provenance = {
        "generation_seconds": sidecar["generation_seconds"],
        "postprocess": sidecar["postprocess"],
        "toolchain": sidecar["toolchain"],
        "loudness": sidecar["loudness"],
    }
    if sidecar_provenance != expected_sidecar_provenance:
        raise CurationError(
            "eligible take sidecar provenance が QC report と一致しません。",
        )
    if sidecar["postprocess"] != PostprocessProfile().as_dict():
        raise CurationError(
            "eligible take sidecar postprocess が固定 profile と一致しません。",
        )
    expected_gen_params = {
        "seed": generation["seed"],
        "recipe_version": ledger["source"]["recipe_version"],
        "sampling": generation["sampling"],
        "requested": sidecar["gen_params"]["requested"],
        "realized": sidecar["gen_params"]["realized"],
    }
    if candidate["gen_params"] != expected_gen_params:
        raise CurationError(
            "manifest candidate gen_params が ledger/sidecar と一致しません。",
        )
    if (
        candidate["duration_sec"] != sidecar["duration_sec"]
        or candidate["duration_sec"] != mechanical["duration_sec"]
    ):
        raise CurationError(
            "manifest candidate duration が sidecar/QC report と一致しません。",
        )
    if candidate["rtf"] != generation["rtf"]:
        raise CurationError("manifest candidate rtf が ledger と一致しません。")
    if (
        candidate["gate"]["mechanical"] != attempt["gates"]["mechanical"]
        or candidate["gate"]["content"] != attempt["gates"]["content"]
    ):
        raise CurationError("manifest candidate gate が ledger と一致しません。")
    if candidate["gate"]["policy_version"] != gate_policy_version:
        raise CurationError(
            "manifest candidate gate policy が QC report と一致しません。",
        )
    if candidate["loudness"] != mechanical["loudness"]:
        raise CurationError(
            "manifest candidate loudness が QC report と一致しません。",
        )
    if candidate["gen_params"]["requested"] != mechanical["generation_params"][
        "requested"
    ] or candidate["gen_params"]["realized"] != mechanical["generation_params"][
        "realized"
    ]:
        raise CurationError(
            "manifest candidate generation params が QC report と一致しません。",
        )


def _resolve_ledger_artifact(run_root: Path, relative: str) -> Path:
    path = (run_root / relative).resolve()
    if not path.is_relative_to(run_root):
        raise CurationError("ledger artifact が run root の外を参照しています。")
    return path


def _validate_existing_projections(
    *,
    manifest: Mapping[str, Any],
    artifact_root: Path,
    candidate_set_sha256: str,
    candidates_by_group: Mapping[
        tuple[str, str, str, str],
        Mapping[str, Mapping[str, Any]],
    ],
    run_root: Path,
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for projection in manifest["curations"]:
        sha256 = projection["curation_sha256"]
        if sha256 in loaded:
            continue
        artifact_path = artifact_root / f"{sha256}.json"
        artifact = _validate_immutable_artifact(
            artifact_path,
            expected_sha256=sha256,
            candidate_set_sha256=candidate_set_sha256,
        )
        for group in artifact["groups"]:
            artifact_identity = tuple(group[key] for key in GROUP_KEYS)
            snapshot_candidates = candidates_by_group.get(artifact_identity)
            if snapshot_candidates is None:
                raise CurationError(
                    "immutable curation artifact に未知の group があります: "
                    f"{_group_text(artifact_identity)}",
                )
            _validate_exported_candidates(
                group=group,
                snapshot_candidates=snapshot_candidates,
                run_root=run_root,
                identity=artifact_identity,
            )
        loaded[sha256] = artifact

    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for projection in manifest["curations"]:
        sha256 = projection["curation_sha256"]
        artifact = loaded[sha256]
        identity = tuple(projection[key] for key in GROUP_KEYS)
        matches = [
            group
            for group in artifact["groups"]
            if tuple(group[key] for key in GROUP_KEYS) == identity
        ]
        if len(matches) != 1:
            raise CurationError(
                f"既存 projection の group が immutable artifact で一意ではありません: "
                f"{_group_text(identity)}",
            )
        group = matches[0]
        if _projection(group, curation_sha256=sha256) != projection:
            raise CurationError(
                f"既存 projection が immutable artifact の decision と一致しません: "
                f"{_group_text(identity)}",
            )
        result[identity] = {"projection": projection, "group": group}

    for artifact in loaded.values():
        for group in artifact["groups"]:
            identity = tuple(group[key] for key in GROUP_KEYS)
            authority = result.get(identity)
            if authority is None:
                raise CurationError(
                    "referenced immutable artifact の group に対応する manifest "
                    f"projection がありません: {_group_text(identity)}",
                )
            if authority["group"] != group:
                raise CurationError(
                    "referenced immutable artifact の group が権威 projection "
                    f"artifact と一致しません: {_group_text(identity)}",
                )
    return result


def _validate_immutable_artifact(
    path: Path,
    *,
    expected_sha256: str,
    candidate_set_sha256: str,
) -> dict[str, Any]:
    if path.name != f"{expected_sha256}.json":
        raise CurationError("immutable curation artifact filename SHA が不正です。")
    raw = _read_bytes(path, "immutable curation artifact")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise CurationError(
            f"immutable curation artifact SHA が filename と一致しません: {path}",
        )
    document = validate_curation(
        _read_json_bytes(raw, path, "immutable curation artifact"),
    )
    if canonical_json(document).encode("utf-8") != raw:
        raise CurationError(
            f"immutable curation artifact は canonical bytes ではありません: {path}",
        )
    if document["candidate_set_sha256"] != candidate_set_sha256:
        raise CurationError(
            f"immutable curation artifact は stale candidate set を参照しています: {path}",
        )
    return document


def _validate_exported_candidates(
    *,
    group: Mapping[str, Any],
    snapshot_candidates: Mapping[str, Mapping[str, Any]],
    run_root: Path,
    identity: tuple[str, str, str, str],
) -> None:
    exported_candidates = {
        candidate["take_id"]: candidate for candidate in group["candidates"]
    }
    if set(exported_candidates) != set(snapshot_candidates):
        raise CurationError(
            f"curation candidate が group を完全に被覆していません: "
            f"{_group_text(identity)}",
        )
    for take_id, exported in exported_candidates.items():
        snapshot_candidate = snapshot_candidates[take_id]
        if exported["path"] != snapshot_candidate["path"]:
            raise CurationError(f"candidate path が snapshot と一致しません: {take_id}")
        if exported["audio_sha256"] != snapshot_candidate["sha256"]:
            raise CurationError(
                f"candidate audio SHA が snapshot と一致しません: {take_id}",
            )
        local_audio = (
            run_root
            / "audio"
            / identity[0]
            / identity[1]
            / identity[2]
            / identity[3]
            / f"take-{snapshot_candidate['take_index']:04d}.opus"
        ).resolve()
        if not local_audio.is_relative_to(run_root):
            raise CurationError("local candidate audio が run root の外を参照しています。")
        if _file_sha256(local_audio) != snapshot_candidate["sha256"]:
            raise CurationError(
                f"local candidate audio SHA が snapshot と一致しません: {take_id}",
            )
    decision = group["decision"]
    if decision["type"] == "selected":
        selected_id = decision["take_id"]
        selected = exported_candidates.get(selected_id)
        if selected is None:
            raise CurationError(f"selected take が同一 group にありません: {selected_id}")
        rubric = selected["rubric"]
        if not rubric["adoptable"] or not rubric["content_correct"]:
            raise CurationError(
                "selected take は adoptable=true かつ content_correct=true が必要です。",
            )


def _projection(
    group: Mapping[str, Any],
    *,
    curation_sha256: str,
) -> dict[str, Any]:
    decision = group["decision"]
    projection = {
        **{key: group[key] for key in GROUP_KEYS},
        "decision": decision["type"],
    }
    if decision["type"] == "selected":
        projection["take_id"] = decision["take_id"]
    projection["curation_sha256"] = curation_sha256
    return projection


def _validate_rubric(value: Any, field: str) -> dict[str, Any]:
    rubric = _exact(value, RUBRIC_FIELDS, field)
    for key in ("content_correct", "adoptable"):
        if not isinstance(rubric[key], bool):
            raise CurationError(f"{field}.{key} は bool が必要です。")
    for key in ("intent_match", "character_naturalness"):
        score = rubric[key]
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
            raise CurationError(f"{field}.{key} は 1..5 の整数が必要です。")
    return {
        "content_correct": rubric["content_correct"],
        "intent_match": rubric["intent_match"],
        "character_naturalness": rubric["character_naturalness"],
        "adoptable": rubric["adoptable"],
    }


def _validate_decision(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or "type" not in value:
        raise CurationError(f"{field} は decision object が必要です。")
    decision_type = value["type"]
    expected = {"type", "take_id"} if decision_type == "selected" else {"type"}
    decision = _exact(value, expected, field)
    if decision_type == "selected":
        return {
            "type": "selected",
            "take_id": _sha(decision["take_id"], f"{field}.take_id"),
        }
    if decision_type != "skipped":
        raise CurationError(f"{field}.type が不正です。")
    return {"type": "skipped"}


def _exact(value: Any, fields: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise CurationError(f"{field} の項目が exact contract と一致しません。")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CurationError(f"{field} は空でない文字列が必要です。")
    return value


def _path_segment(value: Any, field: str) -> str:
    text = _text(value, field)
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise CurationError(f"{field} は安全な path segment が必要です。")
    return text


def _sha(value: Any, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(character not in HEX for character in text):
        raise CurationError(f"{field} は完全な小文字 SHA-256 が必要です。")
    return text


def _read_manifest(path: Path) -> dict[str, Any]:
    document = _read_json(path, "v4 snapshot")
    try:
        return validate_manifest_v4(document)
    except TakeManifestError as error:
        raise CurationError(f"v4 snapshot が不正です: {path}: {error}") from error


def _read_json(path: Path, label: str) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CurationError(f"{label} を読み込めません: {path}: {error}") from error
    return _read_json_bytes(raw, path, label)


def _read_json_bytes(raw: bytes, path: Path, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CurationError(f"{label} を読み込めません: {path}: {error}") from error


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise CurationError(f"{label} を読み込めません: {path}: {error}") from error


def _read_marker(path: Path) -> str:
    raw = _read_bytes(path, "candidate set SHA marker")
    if len(raw) != 64 or any(byte not in b"0123456789abcdef" for byte in raw):
        raise CurationError(
            "candidate-set.sha256 は改行なし 64 ASCII lowercase hex が必要です。",
        )
    return raw.decode("ascii")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise CurationError(f"local candidate audio を読み込めません: {path}") from error
    return digest.hexdigest()


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, pending_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".pending",
        )
    except OSError as error:
        raise CurationError(f"atomic write を準備できません: {path}: {error}") from error

    pending = Path(pending_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        pending.replace(path)
    except OSError as error:
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            pass
        raise CurationError(f"atomic write に失敗しました: {path}: {error}") from error
    try:
        pending.unlink(missing_ok=True)
    except OSError:
        pass


def _write_immutable_bytes(path: Path, payload: bytes) -> None:
    if path.exists():
        if _read_bytes(path, "immutable curation artifact") != payload:
            raise CurationError(
                f"immutable curation artifact の bytes が競合しています: {path}",
            )
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, pending_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".pending",
        )
    except OSError as error:
        raise CurationError(f"immutable write を準備できません: {path}: {error}") from error

    pending = Path(pending_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(pending, path)
        except FileExistsError:
            if _read_bytes(path, "immutable curation artifact") != payload:
                raise CurationError(
                    f"immutable curation artifact の bytes が競合しています: {path}",
                )
        except OSError as error:
            raise CurationError(f"immutable write に失敗しました: {path}: {error}") from error
    finally:
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            pass


def _group_text(identity: tuple[str, str, str, str]) -> str:
    return "/".join(identity)


def _slot_text(identity: tuple[str, str, str, str, int]) -> str:
    return f"{_group_text(identity[:4])}/take-{identity[4]:04d}"
