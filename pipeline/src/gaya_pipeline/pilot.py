from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from gaya_pipeline.curation import (
    CurationError,
    _validate_manifest_against_terminal_ledger,
    build_candidate_set,
    canonical_candidate_set_bytes,
    load_authoritative_candidate_lines,
    validate_snapshot_bundle,
)
from gaya_pipeline.qc_report import QCReportError, validate_qc_report
from gaya_pipeline.take_identity import canonical_json, sha256_canonical
from gaya_pipeline.take_ledger import TakeLedgerError, read_ledger
from gaya_pipeline.take_sidecar import TakeSidecarError, validate_take_sidecar
from gaya_pipeline.validation import validate_scenario_ids


class PilotError(RuntimeError):
    pass


@dataclass(frozen=True)
class PilotBuildSummary:
    bundle_dir: Path
    pilot_set_path: Path
    pilot_set_sha256: str
    group_count: int
    candidate_count: int


@dataclass(frozen=True)
class PilotAnalysisSummary:
    report_json_path: Path
    report_markdown_path: Path
    pilot_set_sha256: str
    decision_sha256: str


FORMAT_VERSION = 1
PROTOCOL = "n3-pilot-v1"
RUBRIC_VERSION = "n3-pilot-human-v1"
VARIANT = "dry"
MODELS = (
    "qwen3-tts-12hz-1.7b",
    "irodori-tts-600m-v3-voicedesign",
    "voxcpm2",
)
SCENARIOS = ("battlefield-camp", "dungeon-entrance")
TAKES_PER_GROUP = 3
SEED_BASE = 103
LINE_COUNT = 24
PRIMARY_REJECT_RULES = (
    "mechanical_audio",
    "active_speech_nonpositive",
    "explicit_reading_mismatch",
)
ACTIVE_SPEECH_REASON = "active_speech_sec が 0 または不正です。"
FEATURE_SPECS = (
    ("duration_sec", "content.prosody.duration_sec"),
    ("mora_per_second", "content.prosody.active_mora_per_sec"),
    ("pause_sec", "content.prosody.pause.internal_total_sec"),
    ("voiced_ratio", "content.prosody.f0.voiced_ratio"),
    ("f0_semitone_std", "content.prosody.f0.semitone_std"),
    ("energy_median_dbfs", "content.prosody.energy.median_dbfs"),
)
FEATURE_NAMES = tuple(name for name, _source in FEATURE_SPECS)
HEX = frozenset("0123456789abcdef")
GROUP_IDENTITY_KEYS = ("model", "scenario", "line", "variant")
PILOT_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "generated_at",
    "design",
    "lines",
    "groups",
    "candidates",
}
DESIGN_FIELDS = {
    "models",
    "scenarios",
    "line_count",
    "takes_per_group",
    "seed_base",
    "feature_specs",
}
LINE_FIELDS = {
    "scenario",
    "line",
    "scenario_title",
    "text",
    "reading",
    "delivery",
}
GROUP_FIELDS = {
    "group_id",
    *GROUP_IDENTITY_KEYS,
    "candidate_ids",
}
CANDIDATE_FIELDS = {
    "candidate_id",
    *GROUP_IDENTITY_KEYS,
    "take_index",
    "take_id",
    "status",
    "gates",
    "features",
    "audio",
}
GATE_FIELDS = {
    "mechanical",
    "content",
    "policy_version",
    "primary_reject_rule",
    "reject_reason",
}
DECISION_ROOT_FIELDS = {
    "format_version",
    "rubric_version",
    "pilot_set_sha256",
    "groups",
}
DECISION_GROUP_FIELDS = {"group_id", "candidates", "decision"}
DECISION_CANDIDATE_FIELDS = {"candidate_id", "rubric"}
RUBRIC_FIELDS = {
    "content_correct",
    "intent_match",
    "character_naturalness",
    "adoptable",
}


def build_pilot_bundle(
    *,
    run_ids: list[str],
    output_dir: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
) -> PilotBuildSummary:
    if len(run_ids) != len(MODELS) * len(SCENARIOS):
        raise PilotError("pilot build には 6 個の run-id が必要です。")
    if len(set(run_ids)) != len(run_ids):
        raise PilotError("pilot build run-id が重複しています。")
    for run_id in run_ids:
        _path_segment(run_id, "run-id")

    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise PilotError(f"pilot bundle 出力先は存在してはいけません: {output_dir}")
    output_parent = output_dir.parent
    if not output_parent.is_dir():
        raise PilotError(f"pilot bundle 出力先の親 directory がありません: {output_parent}")

    scenarios = _load_pilot_scenarios(scenarios_dir.resolve())
    expected_line_ids = {
        scenario: tuple(line["line"] for line in scenarios if line["scenario"] == scenario)
        for scenario in SCENARIOS
    }
    run_materials: dict[tuple[str, str], dict[str, Any]] = {}
    generated_at_values: list[str] = []
    candidates: list[dict[str, Any]] = []
    candidate_sources: dict[str, Path] = {}

    for run_id in run_ids:
        material = _load_run_material(
            run_id=run_id,
            artifacts_dir=artifacts_dir.resolve(),
            scenarios_dir=scenarios_dir.resolve(),
            expected_line_ids=expected_line_ids,
        )
        key = (material["model"], material["scenario"])
        if key in run_materials:
            raise PilotError(f"model/scenario run が重複しています: {key[0]}/{key[1]}")
        run_materials[key] = material
        generated_at_values.append(material["generated_at"])
        for candidate, source_path in material["candidates"]:
            candidate_id = candidate["candidate_id"]
            if candidate_id in candidate_sources:
                raise PilotError(f"pilot candidate_id が重複しています: {candidate_id}")
            candidates.append(candidate)
            candidate_sources[candidate_id] = source_path

    expected_runs = {(model, scenario) for model in MODELS for scenario in SCENARIOS}
    if set(run_materials) != expected_runs:
        missing = sorted(expected_runs - set(run_materials))
        extra = sorted(set(run_materials) - expected_runs)
        raise PilotError(f"pilot run coverage が不正です: missing={missing}, extra={extra}")

    candidates.sort(
        key=lambda candidate: (
            candidate["model"],
            candidate["scenario"],
            candidate["line"],
            candidate["variant"],
            candidate["take_index"],
        ),
    )
    groups = _build_groups(candidates)
    document = {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "generated_at": max(generated_at_values),
        "design": {
            "models": list(MODELS),
            "scenarios": list(SCENARIOS),
            "line_count": LINE_COUNT,
            "takes_per_group": TAKES_PER_GROUP,
            "seed_base": SEED_BASE,
            "feature_specs": [
                {"name": name, "source": source} for name, source in FEATURE_SPECS
            ],
        },
        "lines": scenarios,
        "groups": groups,
        "candidates": candidates,
    }
    payload = canonical_pilot_set_bytes(document)
    pilot_set_sha256 = hashlib.sha256(payload).hexdigest()

    pending_dir = Path(
        tempfile.mkdtemp(
            dir=output_parent,
            prefix=f".{output_dir.name}.",
            suffix=".pending",
        ),
    )
    try:
        audio_dir = pending_dir / "audio"
        audio_dir.mkdir()
        for candidate in candidates:
            source = candidate_sources[candidate["candidate_id"]]
            target = pending_dir / candidate["audio"]["path"]
            shutil.copyfile(source, target)
            if _file_sha256(target) != candidate["audio"]["sha256"]:
                raise PilotError(f"pilot Opus copy SHA-256 が一致しません: {target}")
        _write_bytes_atomic(pending_dir / "pilot-set.json", payload)
        pending_dir.replace(output_dir)
    except (OSError, PilotError) as error:
        shutil.rmtree(pending_dir, ignore_errors=True)
        if isinstance(error, PilotError):
            raise
        raise PilotError(f"pilot bundle の書込みに失敗しました: {error}") from error

    return PilotBuildSummary(
        bundle_dir=output_dir,
        pilot_set_path=output_dir / "pilot-set.json",
        pilot_set_sha256=pilot_set_sha256,
        group_count=len(groups),
        candidate_count=len(candidates),
    )


def analyze_pilot_bundle(
    *,
    bundle_dir: Path,
    decision_path: Path,
    output_dir: Path,
) -> PilotAnalysisSummary:
    bundle_dir = bundle_dir.resolve()
    pilot_set_path = bundle_dir / "pilot-set.json"
    pilot_raw = _read_bytes(pilot_set_path, "pilot-set.json")
    pilot = _read_json_bytes(pilot_raw, pilot_set_path, "pilot-set.json")
    canonical = canonical_pilot_set_bytes(pilot)
    if pilot_raw != canonical:
        raise PilotError("pilot-set.json は canonical bytes である必要があります。")
    pilot_sha = hashlib.sha256(pilot_raw).hexdigest()
    _validate_bundle_audio(bundle_dir=bundle_dir, pilot=pilot)

    decision_path = decision_path.resolve()
    decision_raw = _read_bytes(decision_path, "pilot decision")
    decision = validate_pilot_decision(
        _read_json_bytes(decision_raw, decision_path, "pilot decision"),
        pilot=pilot,
        pilot_set_sha256=pilot_sha,
    )
    decision_sha = hashlib.sha256(decision_raw).hexdigest()

    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise PilotError(f"pilot report 出力先は存在してはいけません: {output_dir}")
    if not output_dir.parent.is_dir():
        raise PilotError(f"pilot report 出力先の親 directory がありません: {output_dir.parent}")

    report = _build_analysis_report(
        pilot=pilot,
        decision=decision,
        pilot_set_sha256=pilot_sha,
        decision_sha256=decision_sha,
    )
    report_json = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
        "utf-8",
    )
    report_markdown = _render_report_markdown(report).encode("utf-8")
    pending_dir = Path(
        tempfile.mkdtemp(
            dir=output_dir.parent,
            prefix=f".{output_dir.name}.",
            suffix=".pending",
        ),
    )
    try:
        _write_bytes_atomic(pending_dir / "pilot-report.json", report_json)
        _write_bytes_atomic(pending_dir / "pilot-report.md", report_markdown)
        pending_dir.replace(output_dir)
    except OSError as error:
        shutil.rmtree(pending_dir, ignore_errors=True)
        raise PilotError(f"pilot report の書込みに失敗しました: {error}") from error
    return PilotAnalysisSummary(
        report_json_path=output_dir / "pilot-report.json",
        report_markdown_path=output_dir / "pilot-report.md",
        pilot_set_sha256=pilot_sha,
        decision_sha256=decision_sha,
    )


def validate_pilot_set(document: Any) -> dict[str, Any]:
    root = _exact(document, PILOT_ROOT_FIELDS, "pilot set")
    if root["format_version"] != FORMAT_VERSION or root["protocol"] != PROTOCOL:
        raise PilotError("pilot set format/protocol が v1 契約と一致しません。")
    _text(root["generated_at"], "pilot set generated_at")
    design = _exact(root["design"], DESIGN_FIELDS, "pilot set design")
    expected_design = {
        "models": list(MODELS),
        "scenarios": list(SCENARIOS),
        "line_count": LINE_COUNT,
        "takes_per_group": TAKES_PER_GROUP,
        "seed_base": SEED_BASE,
        "feature_specs": [
            {"name": name, "source": source} for name, source in FEATURE_SPECS
        ],
    }
    if design != expected_design:
        raise PilotError("pilot set design が固定 N3 protocol と一致しません。")
    for key in ("lines", "groups", "candidates"):
        if not isinstance(root[key], list):
            raise PilotError(f"pilot set {key} は配列が必要です。")

    lines: list[dict[str, Any]] = []
    line_ids: set[tuple[str, str]] = set()
    for index, value in enumerate(root["lines"]):
        field = f"pilot set lines[{index}]"
        line = _exact(value, LINE_FIELDS, field)
        scenario = _path_segment(line["scenario"], f"{field}.scenario")
        line_id = _path_segment(line["line"], f"{field}.line")
        for key in ("scenario_title", "text", "reading", "delivery"):
            _text(line[key], f"{field}.{key}")
        identity = (scenario, line_id)
        if identity in line_ids:
            raise PilotError("pilot set line が重複しています。")
        line_ids.add(identity)
        lines.append(line)
    if len(lines) != LINE_COUNT:
        raise PilotError(f"pilot set lines は {LINE_COUNT} 件が必要です。")
    if {scenario for scenario, _line in line_ids} != set(SCENARIOS):
        raise PilotError("pilot set line scenario coverage が不正です。")
    if lines != sorted(lines, key=lambda line: (line["scenario"], line["line"])):
        raise PilotError("pilot set lines は scenario/line 順が必要です。")

    candidates_by_id: dict[str, dict[str, Any]] = {}
    candidate_slots: set[tuple[str, str, str, str, int]] = set()
    for index, value in enumerate(root["candidates"]):
        candidate = _validate_pilot_candidate(
            value,
            field=f"pilot set candidates[{index}]",
        )
        if (candidate["scenario"], candidate["line"]) not in line_ids:
            raise PilotError("pilot candidate が未知の line を参照しています。")
        candidate_id = candidate["candidate_id"]
        if candidate_id in candidates_by_id:
            raise PilotError("pilot candidate_id が重複しています。")
        slot = tuple(candidate[key] for key in GROUP_IDENTITY_KEYS) + (
            candidate["take_index"],
        )
        if slot in candidate_slots:
            raise PilotError("pilot candidate slot が重複しています。")
        candidate_slots.add(slot)
        candidates_by_id[candidate_id] = candidate
    expected_candidate_count = len(MODELS) * LINE_COUNT * TAKES_PER_GROUP
    if len(candidates_by_id) != expected_candidate_count:
        raise PilotError(f"pilot candidates は {expected_candidate_count} 件が必要です。")
    expected_candidate_order = sorted(
        root["candidates"],
        key=lambda candidate: (
            candidate["model"],
            candidate["scenario"],
            candidate["line"],
            candidate["variant"],
            candidate["take_index"],
        ),
    )
    if root["candidates"] != expected_candidate_order:
        raise PilotError("pilot candidates の順序が不正です。")

    seen_groups: set[str] = set()
    covered_candidates: set[str] = set()
    expected_identities = {
        (model, scenario, line)
        for model in MODELS
        for scenario, line in line_ids
    }
    actual_identities: set[tuple[str, str, str]] = set()
    for index, value in enumerate(root["groups"]):
        field = f"pilot set groups[{index}]"
        group = _exact(value, GROUP_FIELDS, field)
        identity = tuple(
            _path_segment(group[key], f"{field}.{key}") for key in GROUP_IDENTITY_KEYS
        )
        if identity[3] != VARIANT:
            raise PilotError(f"{field}.variant が dry ではありません。")
        group_id = _sha(group["group_id"], f"{field}.group_id")
        if group_id != _make_group_id(*identity):
            raise PilotError(f"{field}.group_id が identity と一致しません。")
        if group_id in seen_groups:
            raise PilotError("pilot group_id が重複しています。")
        seen_groups.add(group_id)
        actual_identities.add(identity[:3])
        candidate_ids = group["candidate_ids"]
        if not isinstance(candidate_ids, list) or len(candidate_ids) != TAKES_PER_GROUP:
            raise PilotError(f"{field}.candidate_ids は 3 件が必要です。")
        expected_candidates = sorted(
            [
            candidate
            for candidate in root["candidates"]
            if tuple(candidate[key] for key in GROUP_IDENTITY_KEYS) == identity
            ],
            key=lambda candidate: candidate["candidate_id"],
        )
        expected_ids = [candidate["candidate_id"] for candidate in expected_candidates]
        if candidate_ids != expected_ids:
            raise PilotError(f"{field}.candidate_ids が group candidates と一致しません。")
        overlap = covered_candidates.intersection(candidate_ids)
        if overlap:
            raise PilotError(f"pilot candidate が複数 group にあります: {sorted(overlap)}")
        covered_candidates.update(candidate_ids)
    if actual_identities != expected_identities:
        raise PilotError("pilot groups が 3 model × 24 line を被覆していません。")
    if covered_candidates != set(candidates_by_id):
        raise PilotError("pilot groups が candidates を完全被覆していません。")
    if root["groups"] != sorted(root["groups"], key=lambda group: group["group_id"]):
        raise PilotError("pilot groups は opaque group_id 順が必要です。")
    return root


def canonical_pilot_set_bytes(document: Any) -> bytes:
    return canonical_json(validate_pilot_set(document)).encode("utf-8")


def validate_pilot_decision(
    document: Any,
    *,
    pilot: Mapping[str, Any],
    pilot_set_sha256: str,
) -> dict[str, Any]:
    root = _exact(document, DECISION_ROOT_FIELDS, "pilot decision")
    if root["format_version"] != FORMAT_VERSION:
        raise PilotError("pilot decision format_version は 1 が必要です。")
    if root["rubric_version"] != RUBRIC_VERSION:
        raise PilotError("pilot decision rubric_version が不正です。")
    if _sha(root["pilot_set_sha256"], "pilot decision pilot_set_sha256") != pilot_set_sha256:
        raise PilotError("pilot decision が pilot-set.json raw bytes と一致しません。")
    if not isinstance(root["groups"], list):
        raise PilotError("pilot decision groups は配列が必要です。")

    pilot_groups = {group["group_id"]: group for group in pilot["groups"]}
    seen_groups: set[str] = set()
    for index, value in enumerate(root["groups"]):
        field = f"pilot decision groups[{index}]"
        group = _exact(value, DECISION_GROUP_FIELDS, field)
        group_id = _sha(group["group_id"], f"{field}.group_id")
        pilot_group = pilot_groups.get(group_id)
        if pilot_group is None or group_id in seen_groups:
            raise PilotError(f"{field}.group_id が未知または重複です。")
        seen_groups.add(group_id)
        if not isinstance(group["candidates"], list):
            raise PilotError(f"{field}.candidates は配列が必要です。")
        actual_ids: list[str] = []
        for candidate_index, candidate_value in enumerate(group["candidates"]):
            candidate_field = f"{field}.candidates[{candidate_index}]"
            candidate = _exact(
                candidate_value,
                DECISION_CANDIDATE_FIELDS,
                candidate_field,
            )
            candidate_id = _sha(candidate["candidate_id"], f"{candidate_field}.candidate_id")
            _validate_rubric(candidate["rubric"], f"{candidate_field}.rubric")
            actual_ids.append(candidate_id)
        if actual_ids != pilot_group["candidate_ids"]:
            raise PilotError(f"{field}.candidates が pilot group と一致しません。")
        decision = group["decision"]
        if not isinstance(decision, dict) or "type" not in decision:
            raise PilotError(f"{field}.decision は discriminated object が必要です。")
        expected_fields = (
            {"type", "candidate_id"} if decision["type"] == "selected" else {"type"}
        )
        decision = _exact(decision, expected_fields, f"{field}.decision")
        if decision["type"] == "selected":
            selected = _sha(decision["candidate_id"], f"{field}.decision.candidate_id")
            if selected not in actual_ids:
                raise PilotError(f"{field}.decision が同一 group 外を参照しています。")
        elif decision["type"] != "skipped":
            raise PilotError(f"{field}.decision.type が不正です。")
    if seen_groups != set(pilot_groups):
        raise PilotError("pilot decision が全 group を被覆していません。")
    expected_order = [group["group_id"] for group in pilot["groups"]]
    if [group["group_id"] for group in root["groups"]] != expected_order:
        raise PilotError("pilot decision groups の順序が pilot set と一致しません。")
    return root


def _load_pilot_scenarios(scenarios_dir: Path) -> list[dict[str, Any]]:
    validation = validate_scenario_ids(scenarios_dir, list(SCENARIOS))
    if validation.problems:
        details = "\n".join(str(problem) for problem in validation.problems)
        raise PilotError(f"pilot scenario 検証に失敗しました:\n{details}")
    result: list[dict[str, Any]] = []
    for scenario_id in SCENARIOS:
        path = scenarios_dir / f"{scenario_id}.yaml"
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise PilotError(f"pilot scenario を読めません: {path}: {error}") from error
        if not isinstance(document, dict) or document.get("id") != scenario_id:
            raise PilotError(f"pilot scenario id が不正です: {path}")
        lines = document.get("lines")
        if not isinstance(lines, list) or len(lines) != LINE_COUNT // len(SCENARIOS):
            raise PilotError(f"pilot scenario は 12 line が必要です: {scenario_id}")
        for line in lines:
            reading = line.get("reading")
            if not isinstance(reading, str) or not reading:
                raise PilotError(f"pilot line に explicit reading がありません: {scenario_id}/{line.get('id')}")
            result.append(
                {
                    "scenario": scenario_id,
                    "line": str(line["id"]),
                    "scenario_title": str(document["title"]),
                    "text": str(line["text"]),
                    "reading": reading,
                    "delivery": str(line["delivery"]),
                },
            )
    return sorted(result, key=lambda line: (line["scenario"], line["line"]))


def _load_run_material(
    *,
    run_id: str,
    artifacts_dir: Path,
    scenarios_dir: Path,
    expected_line_ids: Mapping[str, tuple[str, ...]],
) -> dict[str, Any]:
    takes_root = (artifacts_dir / "takes").resolve()
    run_root = (takes_root / run_id).resolve()
    if not run_root.is_relative_to(takes_root) or not run_root.is_dir():
        raise PilotError(f"pilot run root がありません: {run_id}")
    ledger_path = run_root / "ledger.json"
    report_path = run_root / "qc-report.json"
    try:
        ledger = read_ledger(ledger_path)
    except (OSError, UnicodeError, json.JSONDecodeError, TakeLedgerError) as error:
        raise PilotError(f"pilot ledger が不正です: {run_id}: {error}") from error
    if ledger["run_id"] != run_id:
        raise PilotError(f"pilot ledger run_id が一致しません: {run_id}")
    source = ledger["source"]
    model = source["model"]
    if model not in MODELS:
        raise PilotError(f"pilot model が固定 protocol 外です: {model}")
    if source["takes"] != TAKES_PER_GROUP or source["seed_base"] != SEED_BASE:
        raise PilotError(f"pilot run の takes/seed-base が 3/103 ではありません: {run_id}")
    scenario_ids = {group["scenario"] for group in source["groups"]}
    if len(scenario_ids) != 1:
        raise PilotError(f"pilot run は単一 scenario が必要です: {run_id}")
    scenario = next(iter(scenario_ids))
    if scenario not in SCENARIOS:
        raise PilotError(f"pilot scenario が固定 protocol 外です: {scenario}")
    expected_groups = {
        (model, scenario, line_id, VARIANT) for line_id in expected_line_ids[scenario]
    }
    actual_groups = {
        tuple(group[key] for key in GROUP_IDENTITY_KEYS) for group in source["groups"]
    }
    if actual_groups != expected_groups:
        raise PilotError(f"pilot run group coverage が 12 line と一致しません: {run_id}")
    invalid_statuses = sorted(
        {
            attempt["status"]
            for attempt in ledger["attempts"]
            if attempt["status"] not in {"eligible", "hard_rejected"}
        },
    )
    if invalid_statuses:
        raise PilotError(
            f"pilot build は blocked/failure/pending run を拒否します: "
            f"{run_id}: {invalid_statuses}",
        )

    try:
        current_scenario_sha, authoritative_lines = (
            load_authoritative_candidate_lines(
                scenarios_dir=scenarios_dir,
                ledger_source=source,
            )
        )
    except CurationError as error:
        raise PilotError(
            f"pilot current scenario source が ledger と一致しません: "
            f"{run_id}: {error}",
        ) from error

    report_raw = _read_bytes(report_path, f"{run_id} QC report")
    report = _read_json_bytes(report_raw, report_path, f"{run_id} QC report")
    try:
        authority = validate_qc_report(report, ledger_path=ledger_path, ledger=ledger)
    except QCReportError as error:
        raise PilotError(f"pilot QC report が不正です: {run_id}: {error}") from error
    generated_at = _text(report["generated_at"], f"{run_id} QC generated_at")

    try:
        snapshot = validate_snapshot_bundle(
            snapshot_path=run_root / "manifest-v4.json",
            candidate_set_path=run_root / "candidate-set.json",
            marker_path=run_root / "candidate-set.sha256",
        )
    except CurationError as error:
        raise PilotError(f"pilot v4 snapshot bundle が不正です: {run_id}: {error}") from error
    if snapshot.candidate_set["scenario_sha256"] != source["scenario_sha256"]:
        raise PilotError(f"pilot candidate set scenario SHA が ledger と一致しません: {run_id}")
    if snapshot.candidate_set["scenario_sha256"] != current_scenario_sha:
        raise PilotError(
            f"pilot candidate set scenario SHA が current source と一致しません: {run_id}",
        )
    manifest_models = [entry["id"] for entry in snapshot.manifest["models"]]
    if manifest_models != [model]:
        raise PilotError(f"pilot manifest model profile が run と一致しません: {run_id}")
    if snapshot.manifest["generated_at"] != generated_at:
        raise PilotError(
            f"pilot manifest generated_at が QC report と一致しません: {run_id}",
        )
    try:
        _validate_manifest_against_terminal_ledger(
            manifest=snapshot.manifest,
            ledger=ledger,
            run_root=run_root,
            qc_authority=authority,
        )
        rebuilt_candidate_set = build_candidate_set(
            scenario_sha256=current_scenario_sha,
            lines=authoritative_lines,
            models=snapshot.manifest["models"],
            candidates=snapshot.manifest["candidates"],
            failures=snapshot.manifest["failures"],
        )
        if (
            canonical_candidate_set_bytes(rebuilt_candidate_set)
            != snapshot.candidate_set_bytes
        ):
            raise CurationError(
                "candidate-set.json が current scenario/manifest からの"
                "再構築結果と一致しません。",
            )
    except CurationError as error:
        raise PilotError(
            f"pilot snapshot provenance が ledger/QC と一致しません: "
            f"{run_id}: {error}",
        ) from error

    eligible_slots = {
        tuple(attempt[key] for key in GROUP_IDENTITY_KEYS) + (attempt["take_index"],)
        for attempt in ledger["attempts"]
        if attempt["status"] == "eligible"
    }
    snapshot_slots = {
        tuple(candidate[key] for key in GROUP_IDENTITY_KEYS) + (candidate["take_index"],)
        for candidate in snapshot.candidate_set["candidates"]
    }
    if snapshot_slots != eligible_slots:
        raise PilotError(f"pilot candidate set が eligible ledger slots と一致しません: {run_id}")
    eligible_groups = {slot[:4] for slot in eligible_slots}
    expected_failure_groups = expected_groups - eligible_groups
    actual_failure_groups = {
        tuple(failure[key] for key in GROUP_IDENTITY_KEYS)
        for failure in snapshot.candidate_set["failures"]
    }
    if actual_failure_groups != expected_failure_groups:
        raise PilotError(f"pilot candidate set failures が ledger と一致しません: {run_id}")

    candidates: list[tuple[dict[str, Any], Path]] = []
    for attempt in ledger["attempts"]:
        slot = tuple(attempt[key] for key in GROUP_IDENTITY_KEYS) + (
            attempt["take_index"],
        )
        qc_attempt = authority.attempts_by_slot[slot]
        opus_path, sidecar = _validate_attempt_provenance(
            run_root=run_root,
            run_id=run_id,
            source=source,
            attempt=attempt,
        )
        features = _extract_features(qc_attempt)
        candidate_id = _make_candidate_id(attempt["take_id"])
        gate = _pilot_gate(
            attempt=attempt,
            qc_attempt=qc_attempt,
            policy_version=authority.gate_policy_version,
        )
        candidate = {
            "candidate_id": candidate_id,
            **{key: attempt[key] for key in GROUP_IDENTITY_KEYS},
            "take_index": attempt["take_index"],
            "take_id": attempt["take_id"],
            "status": attempt["status"],
            "gates": gate,
            "features": features,
            "audio": {
                "path": f"audio/{candidate_id}.opus",
                "sha256": sidecar["opus_sha256"],
            },
        }
        _validate_pilot_candidate(candidate, field="built pilot candidate")
        candidates.append((candidate, opus_path))
    return {
        "model": model,
        "scenario": scenario,
        "generated_at": generated_at,
        "candidates": candidates,
    }


def _validate_attempt_provenance(
    *,
    run_root: Path,
    run_id: str,
    source: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    audio = attempt["audio"]
    wav_path = _resolve_run_path(run_root, audio["wav_path"])
    opus_path = _resolve_run_path(run_root, audio["opus_path"])
    sidecar_path = opus_path.with_suffix(".json")
    for label, path in (("WAV", wav_path), ("Opus", opus_path), ("sidecar", sidecar_path)):
        if not path.is_file():
            raise PilotError(f"pilot take {label} がありません: {path}")
    if _file_sha256(wav_path) != audio["wav_sha256"]:
        raise PilotError(f"pilot WAV SHA が ledger と一致しません: {wav_path}")
    if _file_sha256(opus_path) != audio["opus_sha256"]:
        raise PilotError(f"pilot Opus SHA が ledger と一致しません: {opus_path}")
    if _file_sha256(sidecar_path) != audio["sidecar_sha256"]:
        raise PilotError(f"pilot sidecar SHA が ledger と一致しません: {sidecar_path}")
    try:
        sidecar = validate_take_sidecar(
            json.loads(sidecar_path.read_text(encoding="utf-8")),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TakeSidecarError) as error:
        raise PilotError(f"pilot sidecar が不正です: {sidecar_path}: {error}") from error
    expected_identity = (
        run_id,
        *(attempt[key] for key in GROUP_IDENTITY_KEYS),
        attempt["take_index"],
    )
    actual_identity = tuple(
        sidecar[key]
        for key in ("run_id", *GROUP_IDENTITY_KEYS, "take_index")
    )
    if actual_identity != expected_identity:
        raise PilotError(f"pilot sidecar identity が ledger と一致しません: {sidecar_path}")
    for key in ("take_id", "generation_input_sha256"):
        if sidecar[key] != attempt[key]:
            raise PilotError(f"pilot sidecar {key} が ledger と一致しません: {sidecar_path}")
    if sidecar["wav_sha256"] != audio["wav_sha256"] or sidecar["opus_sha256"] != audio["opus_sha256"]:
        raise PilotError(f"pilot sidecar audio SHA が ledger と一致しません: {sidecar_path}")
    if sidecar["take"]["recipe_version"] != source["recipe_version"]:
        raise PilotError(f"pilot sidecar recipe が ledger と一致しません: {sidecar_path}")
    expected_generation = {
        "status": "succeeded",
        "seed": sidecar["take"]["seed"],
        "sampling": sidecar["take"]["sampling"],
        "rtf": sidecar["rtf"],
    }
    if attempt["generation"] != expected_generation:
        raise PilotError(f"pilot sidecar generation が ledger と一致しません: {sidecar_path}")
    return opus_path, sidecar


def _pilot_gate(
    *,
    attempt: Mapping[str, Any],
    qc_attempt: Mapping[str, Any],
    policy_version: str,
) -> dict[str, Any]:
    gates = attempt["gates"]
    status = attempt["status"]
    primary_rule: str | None = None
    reason: str | None = None
    if status == "hard_rejected":
        if gates == {"mechanical": "pass", "content": "reject"}:
            reading = qc_attempt["content"].get("reading")
            terminal_inspection = qc_attempt["content"] == {
                "status": "reject",
                "inspection": "terminal_not_repeated",
            }
            if (
                not terminal_inspection
                and (
                    not isinstance(reading, dict)
                    or reading.get("reading_mismatch") is not True
                )
            ):
                raise PilotError("content reject に explicit reading mismatch 証拠がありません。")
            primary_rule = "explicit_reading_mismatch"
        elif gates == {"mechanical": "reject", "content": "not_run"}:
            mechanical = qc_attempt["mechanical"]
            reason_value = mechanical.get("reason") if isinstance(mechanical, dict) else None
            if reason_value is not None and (
                not isinstance(reason_value, str) or not reason_value
            ):
                raise PilotError("mechanical reject reason が不正です。")
            reason = reason_value
            primary_rule = (
                "active_speech_nonpositive"
                if reason == ACTIVE_SPEECH_REASON
                else "mechanical_audio"
            )
        else:
            raise PilotError("hard_rejected pilot candidate の gates が不正です。")
    return {
        "mechanical": gates["mechanical"],
        "content": gates["content"],
        "policy_version": policy_version,
        "primary_reject_rule": primary_rule,
        "reject_reason": reason,
    }


def _extract_features(qc_attempt: Mapping[str, Any]) -> dict[str, float | None]:
    content = qc_attempt.get("content")
    prosody = content.get("prosody") if isinstance(content, dict) else None
    mechanical = qc_attempt.get("mechanical")
    duration = _nested_number(prosody, ("duration_sec",))
    if duration is None:
        duration = _nested_number(mechanical, ("duration_sec",))
    return {
        "duration_sec": duration,
        "mora_per_second": _nested_number(prosody, ("active_mora_per_sec",)),
        "pause_sec": _nested_number(prosody, ("pause", "internal_total_sec")),
        "voiced_ratio": _nested_number(prosody, ("f0", "voiced_ratio")),
        "f0_semitone_std": _nested_number(prosody, ("f0", "semitone_std")),
        "energy_median_dbfs": _nested_number(prosody, ("energy", "median_dbfs")),
    }


def _nested_number(value: Any, path: tuple[str, ...]) -> float | None:
    current = value
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    if current is None:
        return None
    if isinstance(current, bool) or not isinstance(current, int | float) or not math.isfinite(current):
        raise PilotError(f"pilot feature {'.'.join(path)} は有限数または null が必要です。")
    return float(current)


def _build_groups(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        identity = tuple(candidate[key] for key in GROUP_IDENTITY_KEYS)
        grouped.setdefault(identity, []).append(candidate)
    groups: list[dict[str, Any]] = []
    for identity, items in grouped.items():
        items.sort(key=lambda candidate: candidate["candidate_id"])
        if len(items) != TAKES_PER_GROUP:
            raise PilotError(f"pilot group candidate count が 3 ではありません: {identity}")
        groups.append(
            {
                "group_id": _make_group_id(*identity),
                **dict(zip(GROUP_IDENTITY_KEYS, identity, strict=True)),
                "candidate_ids": [item["candidate_id"] for item in items],
            },
        )
    return sorted(groups, key=lambda group: group["group_id"])


def _validate_pilot_candidate(value: Any, *, field: str) -> dict[str, Any]:
    candidate = _exact(value, CANDIDATE_FIELDS, field)
    identity = tuple(
        _path_segment(candidate[key], f"{field}.{key}") for key in GROUP_IDENTITY_KEYS
    )
    if identity[0] not in MODELS or identity[1] not in SCENARIOS or identity[3] != VARIANT:
        raise PilotError(f"{field} identity が固定 protocol 外です。")
    take_index = candidate["take_index"]
    if isinstance(take_index, bool) or not isinstance(take_index, int) or not 1 <= take_index <= 3:
        raise PilotError(f"{field}.take_index は 1..3 が必要です。")
    take_id = _sha(candidate["take_id"], f"{field}.take_id")
    candidate_id = _sha(candidate["candidate_id"], f"{field}.candidate_id")
    if candidate_id != _make_candidate_id(take_id):
        raise PilotError(f"{field}.candidate_id が take_id と一致しません。")
    if candidate["status"] not in {"eligible", "hard_rejected"}:
        raise PilotError(f"{field}.status が不正です。")
    gates = _exact(candidate["gates"], GATE_FIELDS, f"{field}.gates")
    _text(gates["policy_version"], f"{field}.gates.policy_version")
    rule = gates["primary_reject_rule"]
    reason = gates["reject_reason"]
    if candidate["status"] == "eligible":
        if (
            gates["mechanical"] != "pass"
            or gates["content"] not in {"pass", "review_required"}
            or rule is not None
            or reason is not None
        ):
            raise PilotError(f"{field}.gates が eligible と一致しません。")
    else:
        if rule not in PRIMARY_REJECT_RULES:
            raise PilotError(f"{field}.gates.primary_reject_rule が不正です。")
        if rule == "explicit_reading_mismatch":
            if (
                gates["mechanical"] != "pass"
                or gates["content"] != "reject"
                or reason is not None
            ):
                raise PilotError(f"{field}.gates が explicit reading reject と一致しません。")
        else:
            if (
                gates["mechanical"] != "reject"
                or gates["content"] != "not_run"
            ):
                raise PilotError(f"{field}.gates が mechanical reject と一致しません。")
            if (rule == "active_speech_nonpositive") != (reason == ACTIVE_SPEECH_REASON):
                raise PilotError(f"{field}.gates active speech rule/reason が一致しません。")
            if rule == "mechanical_audio" and reason is not None and (
                not isinstance(reason, str) or not reason
            ):
                raise PilotError(f"{field}.gates.reject_reason が不正です。")
    features = _exact(candidate["features"], set(FEATURE_NAMES), f"{field}.features")
    for name in FEATURE_NAMES:
        value = features[name]
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise PilotError(f"{field}.features.{name} は有限数または null が必要です。")
    audio = _exact(candidate["audio"], {"path", "sha256"}, f"{field}.audio")
    if audio["path"] != f"audio/{candidate_id}.opus":
        raise PilotError(f"{field}.audio.path が blind path 契約と一致しません。")
    _sha(audio["sha256"], f"{field}.audio.sha256")
    return candidate


def _validate_bundle_audio(*, bundle_dir: Path, pilot: Mapping[str, Any]) -> None:
    expected_files = {Path("pilot-set.json")}
    for candidate in pilot["candidates"]:
        relative = Path(candidate["audio"]["path"])
        path = (bundle_dir / relative).resolve()
        if not path.is_relative_to(bundle_dir) or not path.is_file():
            raise PilotError(f"pilot bundle audio がありません: {relative.as_posix()}")
        if _file_sha256(path) != candidate["audio"]["sha256"]:
            raise PilotError(f"pilot bundle audio SHA が一致しません: {relative.as_posix()}")
        expected_files.add(relative)
    actual_files = {
        path.relative_to(bundle_dir)
        for path in bundle_dir.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        extra = sorted(path.as_posix() for path in actual_files - expected_files)
        missing = sorted(path.as_posix() for path in expected_files - actual_files)
        raise PilotError(f"pilot bundle file coverage が不正です: extra={extra}, missing={missing}")


def _build_analysis_report(
    *,
    pilot: Mapping[str, Any],
    decision: Mapping[str, Any],
    pilot_set_sha256: str,
    decision_sha256: str,
) -> dict[str, Any]:
    candidates = {candidate["candidate_id"]: candidate for candidate in pilot["candidates"]}
    decisions_by_group = {group["group_id"]: group for group in decision["groups"]}
    rubrics: dict[str, dict[str, Any]] = {}
    selected_ids: set[str] = set()
    skipped_groups = 0
    for group in decision["groups"]:
        for candidate in group["candidates"]:
            rubrics[candidate["candidate_id"]] = candidate["rubric"]
        if group["decision"]["type"] == "selected":
            selected_ids.add(group["decision"]["candidate_id"])
        else:
            skipped_groups += 1

    adoptable_confusion = {
        "automated_eligible_human_adoptable": 0,
        "automated_eligible_human_not_adoptable": 0,
        "automated_rejected_human_adoptable": 0,
        "automated_rejected_human_not_adoptable": 0,
    }
    content_confusion = {
        "automated_eligible_content_correct": 0,
        "automated_eligible_content_incorrect": 0,
        "automated_rejected_content_correct": 0,
        "automated_rejected_content_incorrect": 0,
    }
    for candidate_id, candidate in candidates.items():
        eligible = candidate["status"] == "eligible"
        adoptable = rubrics[candidate_id]["adoptable"]
        adoptable_key = (
            f"automated_{'eligible' if eligible else 'rejected'}_"
            f"human_{'adoptable' if adoptable else 'not_adoptable'}"
        )
        adoptable_confusion[adoptable_key] += 1
        content_correct = rubrics[candidate_id]["content_correct"]
        content_key = (
            f"automated_{'eligible' if eligible else 'rejected'}_"
            f"content_{'correct' if content_correct else 'incorrect'}"
        )
        content_confusion[content_key] += 1
    adoptable_confusion["total"] = len(candidates)
    content_confusion["total"] = len(candidates)
    rejected_content_correct = content_confusion["automated_rejected_content_correct"]
    rejected_content_incorrect = content_confusion["automated_rejected_content_incorrect"]
    eligible_content_correct = content_confusion["automated_eligible_content_correct"]
    eligible_content_incorrect = content_confusion["automated_eligible_content_incorrect"]
    rejected_adoptable = adoptable_confusion["automated_rejected_human_adoptable"]
    eligible_adoptable = adoptable_confusion["automated_eligible_human_adoptable"]
    total_content_correct = rejected_content_correct + eligible_content_correct
    total_adoptable = rejected_adoptable + eligible_adoptable

    rule_false_rejects = []
    for rule in PRIMARY_REJECT_RULES:
        rejected = [
            candidate
            for candidate in candidates.values()
            if candidate["gates"]["primary_reject_rule"] == rule
        ]
        adoptable_count = sum(
            rubrics[candidate["candidate_id"]]["adoptable"] for candidate in rejected
        )
        content_correct_count = sum(
            rubrics[candidate["candidate_id"]]["content_correct"] for candidate in rejected
        )
        rule_false_rejects.append(
            {
                "rule": rule,
                "rejected_count": len(rejected),
                "content_correct_false_reject_count": content_correct_count,
                "content_correct_false_reject_rate": _rate(
                    content_correct_count,
                    total_content_correct,
                ),
                "content_correct_share_of_rule_rejects": _rate(
                    content_correct_count,
                    len(rejected),
                ),
                "adoptable_false_reject_count": adoptable_count,
                "adoptable_false_reject_rate": _rate(
                    adoptable_count,
                    total_adoptable,
                ),
                "adoptable_share_of_rule_rejects": _rate(
                    adoptable_count,
                    len(rejected),
                ),
            },
        )

    model_rule_rejects = []
    for model in MODELS:
        model_candidates = [
            candidate
            for candidate in candidates.values()
            if candidate["model"] == model
        ]
        model_content_correct = sum(
            rubrics[candidate["candidate_id"]]["content_correct"]
            for candidate in model_candidates
        )
        model_adoptable = sum(
            rubrics[candidate["candidate_id"]]["adoptable"]
            for candidate in model_candidates
        )
        for rule in PRIMARY_REJECT_RULES:
            rejected = [
                candidate
                for candidate in model_candidates
                if candidate["model"] == model
                and candidate["gates"]["primary_reject_rule"] == rule
            ]
            adoptable_count = sum(
                rubrics[candidate["candidate_id"]]["adoptable"] for candidate in rejected
            )
            content_correct_count = sum(
                rubrics[candidate["candidate_id"]]["content_correct"]
                for candidate in rejected
            )
            model_rule_rejects.append(
                {
                    "model": model,
                    "rule": rule,
                    "rejected_count": len(rejected),
                    "content_correct_false_reject_count": content_correct_count,
                    "content_correct_false_reject_rate": _rate(
                        content_correct_count,
                        model_content_correct,
                    ),
                    "content_correct_share_of_rule_rejects": _rate(
                        content_correct_count,
                        len(rejected),
                    ),
                    "adoptable_false_reject_count": adoptable_count,
                    "adoptable_false_reject_rate": _rate(
                        adoptable_count,
                        model_adoptable,
                    ),
                    "adoptable_share_of_rule_rejects": _rate(
                        adoptable_count,
                        len(rejected),
                    ),
                },
            )

    human_by_model = [
        _human_metrics(
            model=model,
            candidates=candidates,
            rubrics=rubrics,
            selected_ids=selected_ids,
            pilot_groups=pilot["groups"],
            decision_groups=decisions_by_group,
        )
        for model in MODELS
    ]
    human_overall = {
        "group_count": len(pilot["groups"]),
        "skipped_group_count": skipped_groups,
        "candidate_count": len(candidates),
        "adoptable_candidate_count": sum(rubric["adoptable"] for rubric in rubrics.values()),
        "selected_candidate_count": len(selected_ids),
        "selected_adoptable_count": sum(rubrics[candidate_id]["adoptable"] for candidate_id in selected_ids),
    }
    lost_winner_count = sum(
        candidates[candidate_id]["status"] == "hard_rejected"
        for candidate_id in selected_ids
    )

    return {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "source": {
            "pilot_set_sha256": pilot_set_sha256,
            "decision_sha256": decision_sha256,
        },
        "scope": {
            "line_count": LINE_COUNT,
            "model_count": len(MODELS),
            "takes_per_group": TAKES_PER_GROUP,
            "candidate_count": len(candidates),
            "group_count": len(pilot["groups"]),
        },
        "raw_confusion_matrices": {
            "adoptable": adoptable_confusion,
            "content_correct": content_confusion,
        },
        "gate_metrics": {
            "false_reject_rate_content_correct": {
                "numerator": rejected_content_correct,
                "denominator": rejected_content_correct + eligible_content_correct,
                "rate": _rate(
                    rejected_content_correct,
                    rejected_content_correct + eligible_content_correct,
                ),
            },
            "false_reject_rate_adoptable": {
                "numerator": rejected_adoptable,
                "denominator": rejected_adoptable + eligible_adoptable,
                "rate": _rate(
                    rejected_adoptable,
                    rejected_adoptable + eligible_adoptable,
                ),
            },
            "bad_content_recall": {
                "numerator": rejected_content_incorrect,
                "denominator": rejected_content_incorrect + eligible_content_incorrect,
                "rate": _rate(
                    rejected_content_incorrect,
                    rejected_content_incorrect + eligible_content_incorrect,
                ),
            },
            "reject_precision": {
                "numerator": rejected_content_incorrect,
                "denominator": rejected_content_incorrect + rejected_content_correct,
                "rate": _rate(
                    rejected_content_incorrect,
                    rejected_content_incorrect + rejected_content_correct,
                ),
            },
            "lost_winner": {
                "count": lost_winner_count,
                "selected_group_count": len(selected_ids),
                "rate": _rate(lost_winner_count, len(selected_ids)),
            },
        },
        "rule_false_rejects": rule_false_rejects,
        "model_rule_rejects": model_rule_rejects,
        "human_selected_adoptable": {
            "overall": human_overall,
            "by_model": human_by_model,
        },
        "eligible_only_feature_lolo": [
            _feature_lolo(
                feature=feature,
                pilot=pilot,
                candidates=candidates,
                decisions=decisions_by_group,
            )
            for feature in FEATURE_NAMES
        ],
        "conclusions": {
            "exploratory_scope": "24-line exploratory",
            "production_scorer": "no-go without independent confirmation",
            "n5_policy": [
                {
                    "model": model,
                    "decision": "maintain_n3",
                    "reason": "no paired take4/5 data",
                }
                for model in MODELS
            ],
            "asr_used_for_ranking": False,
        },
    }


def _human_metrics(
    *,
    model: str,
    candidates: Mapping[str, Mapping[str, Any]],
    rubrics: Mapping[str, Mapping[str, Any]],
    selected_ids: set[str],
    pilot_groups: list[dict[str, Any]],
    decision_groups: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    model_candidates = [
        candidate for candidate in candidates.values() if candidate["model"] == model
    ]
    model_ids = {candidate["candidate_id"] for candidate in model_candidates}
    model_groups = [group for group in pilot_groups if group["model"] == model]
    return {
        "model": model,
        "group_count": len(model_groups),
        "skipped_group_count": sum(
            decision_groups[group["group_id"]]["decision"]["type"] == "skipped"
            for group in model_groups
        ),
        "candidate_count": len(model_candidates),
        "adoptable_candidate_count": sum(
            rubrics[candidate_id]["adoptable"] for candidate_id in model_ids
        ),
        "selected_candidate_count": len(selected_ids.intersection(model_ids)),
        "selected_adoptable_count": sum(
            rubrics[candidate_id]["adoptable"]
            for candidate_id in selected_ids.intersection(model_ids)
        ),
    }


def _feature_lolo(
    *,
    feature: str,
    pilot: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
    decisions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    eligible_groups: list[dict[str, Any]] = []
    excluded_group_count = 0
    excluded_missing_feature = 0
    for group in pilot["groups"]:
        decision = decisions[group["group_id"]]["decision"]
        if decision["type"] != "selected":
            excluded_group_count += 1
            continue
        selected_id = decision["candidate_id"]
        eligible = [
            candidates[candidate_id]
            for candidate_id in group["candidate_ids"]
            if candidates[candidate_id]["status"] == "eligible"
        ]
        if (
            selected_id not in {candidate["candidate_id"] for candidate in eligible}
            or not eligible
        ):
            excluded_group_count += 1
            continue
        if any(candidate["features"][feature] is None for candidate in eligible):
            excluded_group_count += 1
            excluded_missing_feature += 1
            continue
        eligible_groups.append(
            {
                "line_key": f"{group['scenario']}/{group['line']}",
                "selected_id": selected_id,
                "candidates": eligible,
            },
        )

    folds: list[dict[str, Any]] = []
    totals = {"hit1": 0, "hit2": 0, "count": 0, "random1": 0.0, "random2": 0.0}
    line_keys = sorted({group["line_key"] for group in eligible_groups})
    for line_key in line_keys:
        train = [group for group in eligible_groups if group["line_key"] != line_key]
        test = [group for group in eligible_groups if group["line_key"] == line_key]
        if not train or not test:
            continue
        direction = _select_direction(train, feature)
        hit1 = 0
        hit2 = 0
        random1 = 0.0
        random2 = 0.0
        for group in test:
            rank = _selected_rank(group, feature=feature, direction=direction)
            hit1 += rank <= 1
            hit2 += rank <= 2
            size = len(group["candidates"])
            random1 += min(1, size) / size
            random2 += min(2, size) / size
        count = len(test)
        totals["hit1"] += hit1
        totals["hit2"] += hit2
        totals["count"] += count
        totals["random1"] += random1
        totals["random2"] += random2
        folds.append(
            {
                "held_out_line": line_key,
                "direction": direction,
                "test_group_count": count,
                "hit_at_1": _rate(hit1, count),
                "hit_at_2": _rate(hit2, count),
                "random_baseline_hit_at_1": round(random1 / count, 6),
                "random_baseline_hit_at_2": round(random2 / count, 6),
            },
        )
    count = int(totals["count"])
    return {
        "feature": feature,
        "eligible_group_count": len(eligible_groups),
        "excluded_group_count": excluded_group_count,
        "excluded_missing_feature": excluded_missing_feature,
        "fold_count": len(folds),
        "hit_at_1": {
            "hits": int(totals["hit1"]),
            "total": count,
            "rate": _rate(int(totals["hit1"]), count),
            "random_baseline": (
                round(float(totals["random1"]) / count, 6) if count else None
            ),
        },
        "hit_at_2": {
            "hits": int(totals["hit2"]),
            "total": count,
            "rate": _rate(int(totals["hit2"]), count),
            "random_baseline": (
                round(float(totals["random2"]) / count, 6) if count else None
            ),
        },
        "folds": folds,
    }


def _select_direction(groups: list[dict[str, Any]], feature: str) -> str:
    scores: dict[str, tuple[int, int, float]] = {}
    for direction in ("ascending", "descending"):
        ranks = [
            _selected_rank(group, feature=feature, direction=direction)
            for group in groups
        ]
        scores[direction] = (
            sum(rank <= 1 for rank in ranks),
            sum(rank <= 2 for rank in ranks),
            sum(1.0 / rank for rank in ranks),
        )
    return "descending" if scores["descending"] > scores["ascending"] else "ascending"


def _selected_rank(group: Mapping[str, Any], *, feature: str, direction: str) -> int:
    reverse = direction == "descending"
    ranked = sorted(
        group["candidates"],
        key=lambda candidate: (
            (
                -float(candidate["features"][feature])
                if reverse
                else float(candidate["features"][feature])
            ),
            candidate["candidate_id"],
        ),
    )
    return next(
        index
        for index, candidate in enumerate(ranked, start=1)
        if candidate["candidate_id"] == group["selected_id"]
    )


def _render_report_markdown(report: Mapping[str, Any]) -> str:
    adoptable_confusion = report["raw_confusion_matrices"]["adoptable"]
    content_confusion = report["raw_confusion_matrices"]["content_correct"]
    human = report["human_selected_adoptable"]["overall"]
    lines = [
        "# N3 pilot 校正レポート",
        "",
        f"- pilot set SHA-256: `{report['source']['pilot_set_sha256']}`",
        f"- decision SHA-256: `{report['source']['decision_sha256']}`",
        f"- 適用範囲: {report['conclusions']['exploratory_scope']}",
        f"- production scorer: **{report['conclusions']['production_scorer']}**",
        "- ASR は feature ranking に使用していない。",
        "- N5 方針:",
        *[
            f"  - `{item['model']}`: {item['decision']} — {item['reason']}"
            for item in report["conclusions"]["n5_policy"]
        ],
        "",
        "## 自動 gate × 人評 adoptable の raw 集計",
        "",
        "| 自動 gate | 人評 adoptable | 人評 not adoptable |",
        "| --- | ---: | ---: |",
        (
            "| eligible | "
            f"{adoptable_confusion['automated_eligible_human_adoptable']} | "
            f"{adoptable_confusion['automated_eligible_human_not_adoptable']} |"
        ),
        (
            "| rejected | "
            f"{adoptable_confusion['automated_rejected_human_adoptable']} | "
            f"{adoptable_confusion['automated_rejected_human_not_adoptable']} |"
        ),
        "",
        "## 自動 gate × content correct の raw 集計",
        "",
        "| 自動 gate | content correct | content incorrect |",
        "| --- | ---: | ---: |",
        (
            "| eligible | "
            f"{content_confusion['automated_eligible_content_correct']} | "
            f"{content_confusion['automated_eligible_content_incorrect']} |"
        ),
        (
            "| rejected | "
            f"{content_confusion['automated_rejected_content_correct']} | "
            f"{content_confusion['automated_rejected_content_incorrect']} |"
        ),
        "",
        "## 人評の選択結果",
        "",
        f"- group: {human['group_count']} (skip {human['skipped_group_count']})",
        (
            f"- 選択: {human['selected_candidate_count']} "
            f"(adoptable {human['selected_adoptable_count']})"
        ),
        f"- adoptable candidate: {human['adoptable_candidate_count']}",
        (
            f"- gate が失った winner: {report['gate_metrics']['lost_winner']['count']} / "
            f"{report['gate_metrics']['lost_winner']['selected_group_count']} "
            f"({_display_rate(report['gate_metrics']['lost_winner']['rate'])})"
        ),
        "",
        "## rule 別 false reject",
        "",
        "| rule | reject | content FRR | content 比率 | adoptable FRR | adoptable 比率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report["rule_false_rejects"]:
        lines.append(
            f"| {item['rule']} | {item['rejected_count']} | "
            f"{item['content_correct_false_reject_count']} "
            f"({_display_rate(item['content_correct_false_reject_rate'])}) | "
            f"{_display_rate(item['content_correct_share_of_rule_rejects'])} | "
            f"{item['adoptable_false_reject_count']} "
            f"({_display_rate(item['adoptable_false_reject_rate'])}) | "
            f"{_display_rate(item['adoptable_share_of_rule_rejects'])} |",
        )
    lines.extend(
        [
        "",
        "## model × rule reject",
        "",
        "| model | rule | reject | content FRR | content 比率 | adoptable FRR | adoptable 比率 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ],
    )
    for item in report["model_rule_rejects"]:
        lines.append(
            f"| {item['model']} | {item['rule']} | {item['rejected_count']} | "
            f"{item['content_correct_false_reject_count']} "
            f"({_display_rate(item['content_correct_false_reject_rate'])}) | "
            f"{_display_rate(item['content_correct_share_of_rule_rejects'])} | "
            f"{item['adoptable_false_reject_count']} "
            f"({_display_rate(item['adoptable_false_reject_rate'])}) | "
            f"{_display_rate(item['adoptable_share_of_rule_rejects'])} |",
        )
    lines.extend(
        [
        "",
        "## eligible-only 単一 feature LOLO",
        "",
        "| feature | Hit@1 | random@1 | Hit@2 | random@2 | group |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        ],
    )
    for feature in report["eligible_only_feature_lolo"]:
        hit1 = feature["hit_at_1"]
        hit2 = feature["hit_at_2"]
        lines.append(
            f"| {feature['feature']} | {_display_rate(hit1['rate'])} | "
            f"{_display_rate(hit1['random_baseline'])} | {_display_rate(hit2['rate'])} | "
            f"{_display_rate(hit2['random_baseline'])} | {hit1['total']} |",
        )
    lines.extend(
        [
            "",
            "方向は各 leave-one-line-out fold の training lines だけで選択した。",
            "同率時は ascending を事前規定の tie-break とした。",
            "",
        ],
    )
    return "\n".join(lines)


def _display_rate(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def _validate_rubric(value: Any, field: str) -> dict[str, Any]:
    rubric = _exact(value, RUBRIC_FIELDS, field)
    for key in ("content_correct", "adoptable"):
        if not isinstance(rubric[key], bool):
            raise PilotError(f"{field}.{key} は bool が必要です。")
    for key in ("intent_match", "character_naturalness"):
        score = rubric[key]
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
            raise PilotError(f"{field}.{key} は 1..5 の整数が必要です。")
    return rubric


def _make_candidate_id(take_id: str) -> str:
    return sha256_canonical({"protocol": PROTOCOL, "take_id": take_id})


def _make_group_id(model: str, scenario: str, line: str, variant: str) -> str:
    return sha256_canonical(
        {
            "protocol": PROTOCOL,
            "model": model,
            "scenario": scenario,
            "line": line,
            "variant": variant,
        },
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _resolve_run_path(run_root: Path, relative: str) -> Path:
    path = (run_root / relative).resolve()
    if not path.is_relative_to(run_root):
        raise PilotError(f"pilot run artifact が run root 外を参照しています: {relative}")
    return path


def _path_segment(value: Any, field: str) -> str:
    text = _text(value, field)
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise PilotError(f"{field} は安全な path segment が必要です。")
    return text


def _sha(value: Any, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(character not in HEX for character in text):
        raise PilotError(f"{field} は完全な小文字 SHA-256 が必要です。")
    return text


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PilotError(f"{field} は空でない文字列が必要です。")
    return value


def _exact(value: Any, fields: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PilotError(f"{field} の項目が exact contract と一致しません。")
    return value


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise PilotError(f"{label} を読めません: {path}: {error}") from error


def _read_json_bytes(raw: bytes, path: Path, label: str) -> Any:
    try:
        return json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PilotError(f"{label} JSON が不正です: {path}: {error}") from error


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise PilotError(f"SHA-256 を計算できません: {path}: {error}") from error
    return digest.hexdigest()


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, pending_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".pending",
    )
    pending = Path(pending_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)
