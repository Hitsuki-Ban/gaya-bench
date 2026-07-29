from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from gaya_pipeline.adapters import get_model_profile
from gaya_pipeline.audio import (
    AudioProcessingError,
    AudioProbe,
    AudioTools,
    EncodedLoudnessReport,
    PostprocessProfile,
    find_audio_tools,
    measure_encoded_opus,
    probe_audio,
)
from gaya_pipeline.curation import (
    CurationError,
    build_candidate_set,
    canonical_candidate_set_bytes,
    load_authoritative_candidate_lines,
    validate_snapshot_bundle,
)
from gaya_pipeline.japanese_reading import (
    JapaneseReadingError,
    find_ambiguous_japanese_readings,
    normalize_japanese_reading,
    resolve_japanese_reading,
)
from gaya_pipeline.qc_report import QCReportError, validate_qc_report
from gaya_pipeline.run_lock import RunLockError, exclusive_run_lock
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.take_ledger import (
    TERMINAL_STATUSES,
    TakeLedgerError,
    read_ledger,
    transition_attempt,
    write_ledger_atomic,
)
from gaya_pipeline.take_manifest_v4 import (
    TakeManifestError,
    candidate_from_attempt,
    validate_manifest_v4,
)
from gaya_pipeline.take_sidecar import TakeSidecarError, validate_take_sidecar
from gaya_pipeline.validation import validate_scenario_ids


class QCError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeInspection:
    transcript: str
    average_log_probability: float | None
    prosody: Mapping[str, Any]


class QCRuntime(Protocol):
    def prepare(self) -> None: ...

    def describe(self) -> Mapping[str, Any]: ...

    def inspect(
        self,
        audio_path: Path,
        *,
        mora_count: int,
    ) -> RuntimeInspection: ...


@dataclass(frozen=True)
class QCSummary:
    ledger_path: Path
    report_path: Path
    snapshot_path: Path | None
    candidate_set_path: Path | None
    candidate_set_marker_path: Path | None
    attempt_count: int
    eligible_count: int
    hard_rejected_count: int
    blocked_count: int
    generation_failed_count: int
    pending_count: int
    content_review_required_count: int


@dataclass(frozen=True)
class _ScenarioInput:
    line: Mapping[str, Any]
    expected_reading: Mapping[str, Any]


@dataclass(frozen=True)
class _MechanicalPass:
    wav_probe: AudioProbe
    opus_probe: AudioProbe
    loudness: EncodedLoudnessReport
    sidecar: Mapping[str, Any]


@dataclass(frozen=True)
class _PendingInspection:
    slot: tuple[str, str, str, str, int]
    attempt: dict[str, Any]
    scenario: _ScenarioInput
    opus_path: Path
    mechanical: _MechanicalPass


class _ProvenanceError(RuntimeError):
    pass


REPORT_FORMAT_VERSION = 2
GATE_POLICY_VERSION = "take-gates-v2"
VARIANT = "dry"


def _invalidate_existing_snapshot(
    *,
    snapshot_path: Path,
    candidate_set_path: Path,
    marker_path: Path,
) -> None:
    paths = (snapshot_path, marker_path, candidate_set_path)
    if not any(path.exists() for path in paths):
        return
    try:
        bundle = validate_snapshot_bundle(
            snapshot_path=snapshot_path,
            candidate_set_path=candidate_set_path,
            marker_path=marker_path,
        )
    except CurationError as error:
        raise QCError(
            "既存 v4 snapshot bundle が不正なため無効化を拒否しました: "
            f"{error}",
        ) from error
    if bundle.manifest["curations"]:
        raise QCError(
            "既存 v4 snapshot には curation があるため、新しい generation run が"
            "必要です。",
        )
    for path in paths:
        try:
            path.unlink()
        except OSError as error:
            raise QCError(f"既存 v4 snapshot を無効化できません: {path}") from error


def run_qc(
    *,
    run_id: str,
    scenarios_dir: Path,
    artifacts_dir: Path,
    runtime: QCRuntime,
) -> QCSummary:
    _require_path_segment(run_id, "run_id")
    artifacts_dir = artifacts_dir.resolve()
    takes_root = (artifacts_dir / "takes").resolve()
    run_root = (takes_root / run_id).resolve()
    if not run_root.is_relative_to(takes_root):
        raise QCError("run root が artifacts/takes の外を参照しています。")
    try:
        with exclusive_run_lock(run_root):
            return _run_qc_transaction(
                run_id=run_id,
                scenarios_dir=scenarios_dir,
                runtime=runtime,
                run_root=run_root,
            )
    except RunLockError as error:
        raise QCError(f"run lock に失敗しました: {error}") from error


def _run_qc_transaction(
    *,
    run_id: str,
    scenarios_dir: Path,
    runtime: QCRuntime,
    run_root: Path,
) -> QCSummary:
    ledger_path = run_root / "ledger.json"
    report_path = run_root / "qc-report.json"
    snapshot_path = run_root / "manifest-v4.json"
    candidate_set_path = run_root / "candidate-set.json"
    candidate_set_marker_path = run_root / "candidate-set.sha256"

    try:
        ledger = read_ledger(ledger_path)
    except (OSError, json.JSONDecodeError, TakeLedgerError) as error:
        raise QCError(f"run ledger を読み込めません: {ledger_path}: {error}") from error
    if ledger["run_id"] != run_id:
        raise QCError("run_id と ledger.run_id が一致しません。")

    existing_snapshot_state = any(
        path.exists()
        for path in (
            snapshot_path,
            candidate_set_path,
            candidate_set_marker_path,
        )
    )
    ledger_terminal = all(
        attempt["status"] in TERMINAL_STATUSES for attempt in ledger["attempts"]
    )
    prior_authority = None
    prior_report: Mapping[str, Any] | None = None
    if (existing_snapshot_state and ledger_terminal) or any(
        attempt["status"] in {"eligible", "hard_rejected"}
        for attempt in ledger["attempts"]
    ):
        try:
            prior_document = json.loads(report_path.read_text(encoding="utf-8"))
            prior_authority = validate_qc_report(
                prior_document,
                ledger_path=ledger_path,
                ledger=ledger,
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            QCReportError,
        ) as error:
            raise QCError(
                "terminal run の再実行には完全な v2 QC report が必要です。"
                "新しい generation run を開始してください: "
                f"{report_path}: {error}",
            ) from error
        prior_report = prior_document

    _invalidate_existing_snapshot(
        snapshot_path=snapshot_path,
        candidate_set_path=candidate_set_path,
        marker_path=candidate_set_marker_path,
    )

    generated_at = _utc_now()
    attempt_reports: dict[tuple[str, str, str, str, int], dict[str, Any]] = (
        {
            slot: dict(report)
            for slot, report in prior_authority.attempts_by_slot.items()
        }
        if prior_authority is not None
        else {}
    )
    pending: list[_PendingInspection] = []
    runtime_description: dict[str, Any] = (
        dict(prior_report["runtime"])
        if prior_report is not None
        else {"status": "not_required"}
    )

    try:
        scenarios = _load_scenarios(
            scenarios_dir,
            ledger_source=ledger["source"],
        )
        tools = (
            find_audio_tools()
            if any(
                attempt["status"] not in {"planned", "generation_failed"}
                for attempt in ledger["attempts"]
            )
            else None
        )
    except (AudioProcessingError, _ProvenanceError) as error:
        ledger = _block_mutable_attempts(
            ledger,
            ledger_path=ledger_path,
            reason=str(error),
            reports=attempt_reports,
        )
        if not any(
            attempt["status"] in {"generated", "blocked"}
            for attempt in ledger["attempts"]
        ):
            raise QCError(f"terminal run の provenance 検証に失敗しました: {error}") from error
        runtime_description = {
            "status": "blocked",
            "error": str(error),
        }
        return _finish(
            ledger=ledger,
            ledger_path=ledger_path,
            report_path=report_path,
            snapshot_path=snapshot_path,
            candidate_set_path=candidate_set_path,
            candidate_set_marker_path=candidate_set_marker_path,
            run_id=run_id,
            run_root=run_root,
            scenarios_dir=scenarios_dir,
            tools=None,
            profile=PostprocessProfile(),
            attempt_reports=attempt_reports,
            runtime_description=runtime_description,
            generated_at=generated_at,
            model_profile=None,
        )

    try:
        model_profile = get_model_profile(str(ledger["source"]["model"]))
    except Exception as error:
        ledger = _block_mutable_attempts(
            ledger,
            ledger_path=ledger_path,
            reason=f"model profile を解決できません: {error}",
            reports=attempt_reports,
        )
        runtime_description = {
            "status": "blocked",
            "error": f"model profile を解決できません: {error}",
        }
        return _finish(
            ledger=ledger,
            ledger_path=ledger_path,
            report_path=report_path,
            snapshot_path=snapshot_path,
            candidate_set_path=candidate_set_path,
            candidate_set_marker_path=candidate_set_marker_path,
            run_id=run_id,
            run_root=run_root,
            scenarios_dir=scenarios_dir,
            tools=tools,
            profile=PostprocessProfile(),
            attempt_reports=attempt_reports,
            runtime_description=runtime_description,
            generated_at=generated_at,
            model_profile=None,
        )

    profile = PostprocessProfile()
    for attempt in list(ledger["attempts"]):
        slot = _attempt_slot(attempt)
        report = attempt_reports.get(slot, _attempt_identity(attempt))
        attempt_reports[slot] = report
        status = str(attempt["status"])
        if status in {"planned", "generation_failed"}:
            report.update(
                {
                    "status": status,
                    "gates": None,
                    "mechanical": {"status": "not_run"},
                    "content": {"status": "not_run"},
                },
            )
            continue
        if tools is None:
            raise AssertionError("audio attempt に音声 toolchain がありません。")

        scenario = scenarios.get((str(attempt["scenario"]), str(attempt["line"])))
        if scenario is None:
            if status in TERMINAL_STATUSES:
                raise QCError(
                    "terminal attempt に対応する scenario line がありません: "
                    f"{_slot_text(slot)}",
                )
            ledger = _record_blocked(
                ledger,
                ledger_path=ledger_path,
                attempt=attempt,
                reason="ledger attempt に対応する scenario line がありません。",
                report=report,
            )
            continue

        try:
            mechanical = _mechanical_gate(
                attempt=attempt,
                run_id=run_id,
                run_root=run_root,
                ledger_source=ledger["source"],
                tools=tools,
                profile=profile,
            )
        except _ProvenanceError as error:
            if status in TERMINAL_STATUSES:
                raise QCError(
                    f"terminal attempt の provenance 検証に失敗しました: "
                    f"{_slot_text(slot)}: {error}",
                ) from error
            ledger = _record_blocked(
                ledger,
                ledger_path=ledger_path,
                attempt=attempt,
                reason=str(error),
                report=report,
            )
            continue
        except AudioProcessingError as error:
            if status == "eligible":
                raise QCError(
                    f"eligible attempt の mechanical 再検証に失敗しました: "
                    f"{_slot_text(slot)}: {error}",
                ) from error
            if status in TERMINAL_STATUSES:
                continue
            ledger = _record_hard_rejected(
                ledger,
                ledger_path=ledger_path,
                attempt=attempt,
                reason=str(error),
                report=report,
            )
            continue

        if status in TERMINAL_STATUSES:
            continue

        pending.append(
            _PendingInspection(
                slot=slot,
                attempt=attempt,
                scenario=scenario,
                opus_path=run_root / attempt["audio"]["opus_path"],
                mechanical=mechanical,
            ),
        )

    if pending:
        try:
            runtime.prepare()
            runtime_description = {
                "status": "ready",
                **dict(runtime.describe()),
            }
        except Exception as error:
            reason = f"QC runtime の準備に失敗しました: {error}"
            runtime_description = {"status": "blocked", "error": reason}
            for item in pending:
                report = attempt_reports[item.slot]
                current = _find_attempt(ledger, item.slot)
                ledger = _record_blocked(
                    ledger,
                    ledger_path=ledger_path,
                    attempt=current,
                    reason=reason,
                    report=report,
                    mechanical=item.mechanical,
                    content_blocked=True,
                )
        else:
            for item in pending:
                report = attempt_reports[item.slot]
                current = _find_attempt(ledger, item.slot)
                try:
                    inspection = runtime.inspect(
                        item.opus_path,
                        mora_count=count_japanese_mora(
                            str(item.scenario.expected_reading["normalized"]),
                        ),
                    )
                except Exception as error:
                    ledger = _record_blocked(
                        ledger,
                        ledger_path=ledger_path,
                        attempt=current,
                        reason=f"QC runtime inspection に失敗しました: {error}",
                        report=report,
                        mechanical=item.mechanical,
                        content_blocked=True,
                    )
                    continue

                try:
                    _verify_inputs_unchanged(
                        item,
                        run_root=run_root,
                        scenarios_dir=scenarios_dir,
                        ledger_source=ledger["source"],
                    )
                except _ProvenanceError as error:
                    ledger = _record_blocked(
                        ledger,
                        ledger_path=ledger_path,
                        attempt=current,
                        reason=str(error),
                        report=report,
                    )
                    continue

                active_speech = inspection.prosody.get("active_speech_sec")
                if not _finite_number(active_speech):
                    ledger = _record_blocked(
                        ledger,
                        ledger_path=ledger_path,
                        attempt=current,
                        reason="QC runtime の active_speech_sec が不正です。",
                        report=report,
                        mechanical=item.mechanical,
                        content_blocked=True,
                    )
                    continue
                if active_speech <= 0:
                    ledger = _record_hard_rejected(
                        ledger,
                        ledger_path=ledger_path,
                        attempt=current,
                        reason="active_speech_sec が 0 または不正です。",
                        report=report,
                        mechanical=item.mechanical,
                    )
                    continue
                if not inspection.transcript.strip():
                    ledger = _record_blocked(
                        ledger,
                        ledger_path=ledger_path,
                        attempt=current,
                        reason="Kana ASR transcript が空です。",
                        report=report,
                        mechanical=item.mechanical,
                        content_blocked=True,
                    )
                    continue

                content_status, content_report = _content_gate(
                    expected=item.scenario.expected_reading,
                    inspection=inspection,
                )
                ledger = _record_eligible(
                    ledger,
                    ledger_path=ledger_path,
                    attempt=current,
                    content_status=content_status,
                    report=report,
                    mechanical=item.mechanical,
                    content=content_report,
                )

    return _finish(
        ledger=ledger,
        ledger_path=ledger_path,
        report_path=report_path,
        snapshot_path=snapshot_path,
        candidate_set_path=candidate_set_path,
        candidate_set_marker_path=candidate_set_marker_path,
        run_id=run_id,
        run_root=run_root,
        scenarios_dir=scenarios_dir,
        tools=tools,
        profile=profile,
        attempt_reports=attempt_reports,
        runtime_description=runtime_description,
        generated_at=generated_at,
        model_profile=model_profile.as_manifest_entry(),
    )


def count_japanese_mora(reading: str) -> int:
    normalized = normalize_japanese_reading(reading)
    non_mora = frozenset("ァィゥェォャュョヮヵヶ")
    return sum(
        character not in non_mora
        for character in normalized
        if "\u30a1" <= character <= "\u30ff"
    )


def _load_scenarios(
    scenarios_dir: Path,
    *,
    ledger_source: Mapping[str, Any],
) -> dict[tuple[str, str], _ScenarioInput]:
    scenarios_dir = scenarios_dir.resolve()
    scenario_ids = _source_scenario_ids(ledger_source)
    validation = validate_scenario_ids(scenarios_dir, scenario_ids)
    if validation.problems:
        details = "\n".join(str(problem) for problem in validation.problems)
        raise _ProvenanceError(f"scenario 検証に失敗しました:\n{details}")

    catalog: dict[tuple[str, str], _ScenarioInput] = {}
    for scenario_id in scenario_ids:
        scenario_path = scenarios_dir / f"{scenario_id}.yaml"
        try:
            source_bytes = scenario_path.read_bytes()
            document = yaml.safe_load(source_bytes.decode("utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise _ProvenanceError(
                f"scenario source を読み込めません: {scenario_path}",
            ) from error
        if not isinstance(document, dict) or document.get("id") != scenario_id:
            raise _ProvenanceError(
                f"scenario source id が一致しません: {scenario_path}",
            )
        for line in document["lines"]:
            try:
                expected = _expected_reading(line)
            except (JapaneseReadingError, TypeError, ValueError) as error:
                raise _ProvenanceError(
                    f"scenario reading を解決できません: "
                    f"{scenario_id}/{line.get('id')}: {error}",
                ) from error
            catalog[(scenario_id, str(line["id"]))] = _ScenarioInput(
                line=line,
                expected_reading=expected,
            )

    actual_source_sha = _current_scenario_source_sha(
        scenarios_dir,
        scenario_ids=scenario_ids,
    )
    if actual_source_sha != ledger_source["scenario_sha256"]:
        raise _ProvenanceError("scenario source SHA-256 が ledger と一致しません。")
    return catalog


def _expected_reading(line: Mapping[str, Any]) -> dict[str, Any]:
    explicit = line.get("reading")
    authoritative = isinstance(explicit, str) and bool(explicit.strip())
    reading = resolve_japanese_reading(
        text=line["text"],
        reading=explicit,
    )
    ambiguous = find_ambiguous_japanese_readings(line["text"])
    return {
        "text": reading.text,
        "source": reading.source,
        "normalized": normalize_japanese_reading(reading.text),
        "authoritative": authoritative,
        "ambiguous_terms": [
            {
                "surface": item.surface,
                "candidates": list(item.candidates),
            }
            for item in ambiguous
        ],
    }


def _mechanical_gate(
    *,
    attempt: Mapping[str, Any],
    run_id: str,
    run_root: Path,
    ledger_source: Mapping[str, Any],
    tools: AudioTools,
    profile: PostprocessProfile,
) -> _MechanicalPass:
    audio = attempt["audio"]
    wav_path = _resolve_run_artifact(run_root, str(audio["wav_path"]))
    opus_path = _resolve_run_artifact(run_root, str(audio["opus_path"]))
    sidecar_path = opus_path.with_suffix(".json")
    for name, path in (
        ("WAV", wav_path),
        ("Opus", opus_path),
        ("sidecar", sidecar_path),
    ):
        if not path.is_file():
            raise _ProvenanceError(f"take {name} がありません: {path}")
    if _file_sha256(sidecar_path) != audio["sidecar_sha256"]:
        raise _ProvenanceError("take sidecar SHA-256 が ledger と一致しません。")
    try:
        sidecar = validate_take_sidecar(
            json.loads(sidecar_path.read_text(encoding="utf-8")),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TakeSidecarError) as error:
        raise _ProvenanceError(f"take sidecar が不正です: {sidecar_path}: {error}") from error

    expected_identity = (
        run_id,
        attempt["model"],
        attempt["scenario"],
        attempt["line"],
        attempt["variant"],
        attempt["take_index"],
    )
    actual_identity = tuple(
        sidecar[key]
        for key in ("run_id", "model", "scenario", "line", "variant", "take_index")
    )
    if actual_identity != expected_identity:
        raise _ProvenanceError("ledger と sidecar の run/slot identity が一致しません。")
    if attempt["variant"] != VARIANT:
        raise _ProvenanceError(f"未対応の take variant です: {attempt['variant']}")
    expected_root = (
        f"audio/{attempt['model']}/{attempt['scenario']}/{attempt['line']}/"
        f"{attempt['variant']}/take-{attempt['take_index']:04d}"
    )
    if audio["wav_path"] != f"{expected_root}.wav":
        raise _ProvenanceError("ledger WAV path が slot と一致しません。")
    if audio["opus_path"] != f"{expected_root}.opus":
        raise _ProvenanceError("ledger Opus path が slot と一致しません。")
    for key in ("take_id", "generation_input_sha256"):
        if attempt[key] != sidecar[key]:
            raise _ProvenanceError(f"ledger と sidecar の {key} が一致しません。")
    for kind, path in (("wav", wav_path), ("opus", opus_path)):
        actual_sha = _file_sha256(path)
        if actual_sha != audio[f"{kind}_sha256"]:
            raise _ProvenanceError(f"take {kind.upper()} SHA-256 が ledger と一致しません。")
        if actual_sha != sidecar[f"{kind}_sha256"]:
            raise _ProvenanceError(f"take {kind.upper()} SHA-256 が sidecar と一致しません。")
    expected_generation = {
        "status": "succeeded",
        "seed": sidecar["take"]["seed"],
        "sampling": sidecar["take"]["sampling"],
        "rtf": sidecar["rtf"],
    }
    if attempt["generation"] != expected_generation:
        raise _ProvenanceError("ledger と sidecar の generation provenance が一致しません。")
    if sidecar["take"]["recipe_version"] != ledger_source["recipe_version"]:
        raise _ProvenanceError("sidecar recipe version が ledger と一致しません。")
    if sidecar["postprocess"] != profile.as_dict():
        raise _ProvenanceError("sidecar postprocess profile が現行契約と一致しません。")
    if sidecar["toolchain"] != tools.as_identity():
        raise _ProvenanceError("sidecar toolchain が現在の音声 toolchain と一致しません。")

    wav_probe = probe_audio(tools, wav_path)
    if (
        not wav_probe.codec_name.startswith("pcm_")
        or wav_probe.sample_rate_hz != profile.sample_rate_hz
        or wav_probe.channels != profile.channels
    ):
        raise AudioProcessingError("WAV の形式が QC profile と一致しません。")
    opus_probe = probe_audio(tools, opus_path)
    if (
        opus_probe.codec_name != "opus"
        or opus_probe.sample_rate_hz != profile.sample_rate_hz
        or opus_probe.channels != profile.channels
    ):
        raise AudioProcessingError("Opus の形式が QC profile と一致しません。")
    if round(opus_probe.duration_sec, 6) != sidecar["duration_sec"]:
        raise _ProvenanceError("Opus duration が sidecar と一致しません。")
    loudness = measure_encoded_opus(tools, opus_path, profile)
    return _MechanicalPass(
        wav_probe=wav_probe,
        opus_probe=opus_probe,
        loudness=loudness,
        sidecar=sidecar,
    )


def _verify_inputs_unchanged(
    pending: _PendingInspection,
    *,
    run_root: Path,
    scenarios_dir: Path,
    ledger_source: Mapping[str, Any],
) -> None:
    attempt = pending.attempt
    for kind in ("wav", "opus"):
        path = _resolve_run_artifact(
            run_root,
            str(attempt["audio"][f"{kind}_path"]),
        )
        if _file_sha256(path) != attempt["audio"][f"{kind}_sha256"]:
            raise _ProvenanceError(
                f"QC 実行中に take {kind.upper()} が変更されました。",
            )
    sidecar_path = _resolve_run_artifact(
        run_root,
        str(attempt["audio"]["opus_path"]),
    ).with_suffix(".json")
    try:
        current_sidecar = validate_take_sidecar(
            json.loads(sidecar_path.read_text(encoding="utf-8")),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TakeSidecarError) as error:
        raise _ProvenanceError(
            "QC 実行中に take sidecar を再検証できません。",
        ) from error
    if current_sidecar != pending.mechanical.sidecar:
        raise _ProvenanceError("QC 実行中に take sidecar が変更されました。")
    actual_scenario_sha = _current_scenario_source_sha(
        scenarios_dir.resolve(),
        scenario_ids=_source_scenario_ids(ledger_source),
    )
    if actual_scenario_sha != ledger_source["scenario_sha256"]:
        raise _ProvenanceError("QC 実行中に scenario source が変更されました。")


def _revalidate_snapshot_inputs(
    *,
    ledger: Mapping[str, Any],
    run_id: str,
    run_root: Path,
    scenarios_dir: Path,
    tools: AudioTools | None,
    profile: PostprocessProfile,
    attempt_reports: dict[
        tuple[str, str, str, str, int],
        dict[str, Any],
    ],
) -> None:
    scenario_sha = _current_scenario_source_sha(
        scenarios_dir.resolve(),
        scenario_ids=_source_scenario_ids(ledger["source"]),
    )
    if scenario_sha != ledger["source"]["scenario_sha256"]:
        raise QCError("v4 snapshot 確定前に scenario source が変更されました。")
    eligible = [
        attempt
        for attempt in ledger["attempts"]
        if attempt["status"] == "eligible"
    ]
    if eligible and tools is None:
        raise QCError("eligible snapshot の音声 toolchain がありません。")
    for attempt in eligible:
        try:
            mechanical = _mechanical_gate(
                attempt=attempt,
                run_id=run_id,
                run_root=run_root,
                ledger_source=ledger["source"],
                tools=tools,
                profile=profile,
            )
        except (AudioProcessingError, _ProvenanceError) as error:
            raise QCError(
                f"eligible attempt の snapshot 再検証に失敗しました: "
                f"{_slot_text(_attempt_slot(attempt))}: {error}",
            ) from error
        report = attempt_reports[_attempt_slot(attempt)]
        report["mechanical"] = _mechanical_report(mechanical)


def _verify_snapshot_material_identity(
    *,
    ledger: Mapping[str, Any],
    ledger_path: Path,
    run_root: Path,
    scenarios_dir: Path,
) -> None:
    try:
        current_ledger = read_ledger(ledger_path)
    except (OSError, json.JSONDecodeError, TakeLedgerError) as error:
        raise QCError(
            "final report 書込後の snapshot 再検証で ledger を読めません。",
        ) from error
    if current_ledger != ledger:
        raise QCError(
            "final report 書込後の snapshot 再検証で ledger が変更されました。",
        )
    scenario_sha = _current_scenario_source_sha(
        scenarios_dir.resolve(),
        scenario_ids=_source_scenario_ids(ledger["source"]),
    )
    if scenario_sha != ledger["source"]["scenario_sha256"]:
        raise QCError(
            "final report 書込後の snapshot 再検証で scenario source が"
            "変更されました。",
        )
    for attempt in ledger["attempts"]:
        if attempt["status"] != "eligible":
            continue
        audio = attempt["audio"]
        try:
            for kind in ("wav", "opus"):
                path = _resolve_run_artifact(
                    run_root,
                    str(audio[f"{kind}_path"]),
                )
                if _file_sha256(path) != audio[f"{kind}_sha256"]:
                    raise QCError(
                        "final report 書込後の snapshot 再検証で "
                        f"take {kind.upper()} が変更されました。",
                    )
            sidecar_path = _resolve_run_artifact(
                run_root,
                str(audio["opus_path"]),
            ).with_suffix(".json")
            if _file_sha256(sidecar_path) != audio["sidecar_sha256"]:
                raise QCError(
                    "final report 書込後の snapshot 再検証で take sidecar が"
                    "変更されました。",
                )
        except (OSError, _ProvenanceError) as error:
            raise QCError(
                "final report 書込後の snapshot 再検証で take material を"
                "確認できません。",
            ) from error


def _content_gate(
    *,
    expected: Mapping[str, Any],
    inspection: RuntimeInspection,
) -> tuple[str, dict[str, Any]]:
    transcript = inspection.transcript.strip()
    actual = normalize_japanese_reading(transcript)
    expected_normalized = str(expected["normalized"])
    matches = actual == expected_normalized
    authoritative = bool(expected["authoritative"])
    status = "pass" if authoritative and matches else "review_required"
    review_reason = (
        None
        if status == "pass"
        else "explicit_reading_mismatch"
        if authoritative
        else "non_authoritative_expected_reading"
    )
    return status, {
        "status": status,
        "review_reason": review_reason,
        "expected_reading": dict(expected),
        "asr": {
            "text": inspection.transcript,
            "normalized_reading": actual,
            "average_log_probability": inspection.average_log_probability,
        },
        "reading": {
            "character_error_rate": _character_error_rate(
                expected_normalized,
                actual,
            ),
            "reading_mismatch": (
                not matches if authoritative else None
            ),
        },
        "prosody": dict(inspection.prosody),
    }


def _record_blocked(
    ledger: dict[str, Any],
    *,
    ledger_path: Path,
    attempt: Mapping[str, Any],
    reason: str,
    report: dict[str, Any],
    mechanical: _MechanicalPass | None = None,
    content_blocked: bool = False,
) -> dict[str, Any]:
    mechanical_status = "pass" if mechanical is not None else "blocked"
    content_status = "blocked" if content_blocked else "not_run"
    current_status = str(attempt["status"])
    if current_status == "generated":
        replacement = {
            **attempt,
            "gates": {
                "mechanical": mechanical_status,
                "content": content_status,
            },
            "features": {"status": "unscored"},
            "status": "blocked",
        }
        ledger = _checkpoint(
            ledger,
            ledger_path=ledger_path,
            slot=_attempt_slot(attempt),
            replacement=replacement,
        )
    report.update(
        {
            "status": "blocked",
            "gates": {
                "mechanical": mechanical_status,
                "content": content_status,
            },
            "mechanical": (
                _mechanical_report(mechanical)
                if mechanical is not None
                else {"status": "blocked", "reason": reason}
            ),
            "content": {
                "status": content_status,
                "reason": reason,
            },
        },
    )
    return ledger


def _record_hard_rejected(
    ledger: dict[str, Any],
    *,
    ledger_path: Path,
    attempt: Mapping[str, Any],
    reason: str,
    report: dict[str, Any],
    mechanical: _MechanicalPass | None = None,
) -> dict[str, Any]:
    replacement = {
        **attempt,
        "gates": {"mechanical": "reject", "content": "not_run"},
        "features": {"status": "unscored"},
        "status": "hard_rejected",
    }
    ledger = _checkpoint(
        ledger,
        ledger_path=ledger_path,
        slot=_attempt_slot(attempt),
        replacement=replacement,
    )
    mechanical_report = (
        _mechanical_report(mechanical)
        if mechanical is not None
        else {"status": "reject"}
    )
    mechanical_report.update({"status": "reject", "reason": reason})
    report.update(
        {
            "status": "hard_rejected",
            "gates": dict(replacement["gates"]),
            "mechanical": mechanical_report,
            "content": {"status": "not_run"},
        },
    )
    return ledger


def _record_eligible(
    ledger: dict[str, Any],
    *,
    ledger_path: Path,
    attempt: Mapping[str, Any],
    content_status: str,
    report: dict[str, Any],
    mechanical: _MechanicalPass,
    content: Mapping[str, Any],
) -> dict[str, Any]:
    replacement = {
        **attempt,
        "gates": {"mechanical": "pass", "content": content_status},
        "features": {"status": "unscored"},
        "status": "eligible",
    }
    ledger = _checkpoint(
        ledger,
        ledger_path=ledger_path,
        slot=_attempt_slot(attempt),
        replacement=replacement,
    )
    report.update(
        {
            "status": "eligible",
            "gates": dict(replacement["gates"]),
            "mechanical": _mechanical_report(mechanical),
            "content": dict(content),
        },
    )
    return ledger


def _block_mutable_attempts(
    ledger: dict[str, Any],
    *,
    ledger_path: Path,
    reason: str,
    reports: dict[tuple[str, str, str, str, int], dict[str, Any]],
) -> dict[str, Any]:
    for attempt in list(ledger["attempts"]):
        slot = _attempt_slot(attempt)
        report = reports.setdefault(slot, _attempt_identity(attempt))
        if attempt["status"] == "generated":
            ledger = _record_blocked(
                ledger,
                ledger_path=ledger_path,
                attempt=attempt,
                reason=reason,
                report=report,
            )
        elif attempt["status"] == "blocked":
            _record_blocked(
                ledger,
                ledger_path=ledger_path,
                attempt=attempt,
                reason=reason,
                report=report,
            )
        else:
            if (
                attempt["status"] in TERMINAL_STATUSES
                and "mechanical" in report
                and "content" in report
            ):
                continue
            report.update(
                {
                    "status": attempt["status"],
                    "gates": attempt.get("gates"),
                    "mechanical": {"status": "not_run", "reason": reason},
                    "content": {"status": "not_run"},
                },
            )
    return ledger


def _checkpoint(
    ledger: dict[str, Any],
    *,
    ledger_path: Path,
    slot: tuple[str, str, str, str, int],
    replacement: dict[str, Any],
) -> dict[str, Any]:
    try:
        next_ledger = transition_attempt(
            ledger,
            slot=slot,
            replacement=replacement,
        )
        write_ledger_atomic(ledger_path, next_ledger)
    except (OSError, TakeLedgerError) as error:
        raise QCError(
            f"QC ledger checkpoint に失敗しました: {_slot_text(slot)}: {error}",
        ) from error
    return next_ledger


def _finish(
    *,
    ledger: dict[str, Any],
    ledger_path: Path,
    report_path: Path,
    snapshot_path: Path,
    candidate_set_path: Path,
    candidate_set_marker_path: Path,
    run_id: str,
    run_root: Path,
    scenarios_dir: Path,
    tools: AudioTools | None,
    profile: PostprocessProfile,
    attempt_reports: dict[
        tuple[str, str, str, str, int],
        dict[str, Any],
    ],
    runtime_description: Mapping[str, Any],
    generated_at: str,
    model_profile: Mapping[str, Any] | None,
) -> QCSummary:
    counts = {
        status: sum(
            attempt["status"] == status for attempt in ledger["attempts"]
        )
        for status in (
            "eligible",
            "hard_rejected",
            "blocked",
            "generation_failed",
            "planned",
            "generated",
        )
    }
    pending_count = counts["planned"] + counts["generated"]
    content_review_required_count = sum(
        attempt["status"] == "eligible"
        and attempt.get("gates", {}).get("content") == "review_required"
        for attempt in ledger["attempts"]
    )
    terminal = all(
        attempt["status"] in TERMINAL_STATUSES
        for attempt in ledger["attempts"]
    )
    if terminal:
        try:
            current_ledger = read_ledger(ledger_path)
        except (OSError, json.JSONDecodeError, TakeLedgerError) as error:
            raise QCError("v4 snapshot 確定前に ledger を再検証できません。") from error
        if current_ledger != ledger:
            raise QCError("v4 snapshot 確定前に ledger が変更されました。")
        _revalidate_snapshot_inputs(
            ledger=ledger,
            run_id=run_id,
            run_root=run_root,
            scenarios_dir=scenarios_dir,
            tools=tools,
            profile=profile,
            attempt_reports=attempt_reports,
        )
    reports = [
        dict(
            attempt_reports.get(
                _attempt_slot(attempt),
                {
                    **_attempt_identity(attempt),
                    "status": attempt["status"],
                    "gates": attempt.get("gates"),
                    "mechanical": {"status": "not_run"},
                    "content": {"status": "not_run"},
                },
            ),
        )
        for attempt in ledger["attempts"]
    ]
    report_document = {
        "format_version": REPORT_FORMAT_VERSION,
        "generated_at": generated_at,
        "gate_policy_version": GATE_POLICY_VERSION,
        "run_id": ledger["run_id"],
        "source": {
            "ledger": ledger_path.as_posix(),
            "scenario_sha256": ledger["source"]["scenario_sha256"],
            "model": ledger["source"]["model"],
            "recipe_version": ledger["source"]["recipe_version"],
        },
        "runtime": dict(runtime_description),
        "summary": {
            "attempt_count": len(ledger["attempts"]),
            **counts,
            "pending": pending_count,
            "content_review_required": content_review_required_count,
        },
        "attempts": reports,
    }
    try:
        validate_qc_report(
            report_document,
            ledger_path=ledger_path,
            ledger=ledger,
        )
    except QCReportError as error:
        raise QCError(f"QC report contract の生成に失敗しました: {error}") from error
    _atomic_write_json(report_path, report_document)

    written_snapshot: Path | None = None
    written_candidate_set: Path | None = None
    written_candidate_set_marker: Path | None = None
    if terminal:
        _verify_snapshot_material_identity(
            ledger=ledger,
            ledger_path=ledger_path,
            run_root=run_root,
            scenarios_dir=scenarios_dir,
        )
        if model_profile is None:
            raise QCError("v4 snapshot に必要な model profile がありません。")
        candidates = []
        reports_by_slot = {
            _attempt_slot(report): report
            for report in attempt_reports.values()
        }
        for attempt in ledger["attempts"]:
            if attempt["status"] != "eligible":
                continue
            report = reports_by_slot[_attempt_slot(attempt)]
            mechanical = report.get("mechanical")
            if (
                not isinstance(mechanical, Mapping)
                or "duration_sec" not in mechanical
                or "loudness" not in mechanical
                or "generation_params" not in mechanical
            ):
                raise QCError(
                    "eligible attempt の snapshot provenance が report にありません: "
                    f"{_slot_text(_attempt_slot(attempt))}",
                )
            candidates.append(
                candidate_from_attempt(
                    attempt,
                    duration_sec=float(mechanical["duration_sec"]),
                    loudness=dict(mechanical["loudness"]),
                    gate_policy_version=GATE_POLICY_VERSION,
                    recipe_version=str(ledger["source"]["recipe_version"]),
                    requested_params=dict(
                        mechanical["generation_params"]["requested"],
                    ),
                    realized_params=dict(
                        mechanical["generation_params"]["realized"],
                    ),
                ),
            )
        candidate_groups = {
            tuple(candidate[key] for key in ("model", "scenario", "line", "variant"))
            for candidate in candidates
        }
        failures = [
            {**dict(group), "reason": "no_eligible_take"}
            for group in ledger["source"]["groups"]
            if tuple(
                group[key] for key in ("model", "scenario", "line", "variant")
            )
            not in candidate_groups
        ]
        try:
            scenario_sha256, lines = load_authoritative_candidate_lines(
                scenarios_dir=scenarios_dir.resolve(),
                ledger_source=ledger["source"],
            )
            candidate_set = build_candidate_set(
                scenario_sha256=scenario_sha256,
                lines=lines,
                models=[dict(model_profile)],
                candidates=candidates,
                failures=failures,
            )
            candidate_set_payload = canonical_candidate_set_bytes(candidate_set)
        except CurationError as error:
            raise QCError(f"candidate set の構築に失敗しました: {error}") from error
        candidate_set_sha256 = hashlib.sha256(candidate_set_payload).hexdigest()
        manifest = {
            "format_version": 4,
            "generated_at": generated_at,
            "candidate_set_sha256": candidate_set_sha256,
            "models": [dict(model_profile)],
            "candidates": candidates,
            "curations": [],
            "failures": failures,
        }
        try:
            validate_manifest_v4(manifest)
        except (TakeManifestError, TakeLedgerError) as error:
            raise QCError(f"v4 snapshot の構築に失敗しました: {error}") from error
        _atomic_write_bytes(candidate_set_path, candidate_set_payload)
        written_candidate_set = candidate_set_path
        _atomic_write_bytes(
            candidate_set_marker_path,
            candidate_set_sha256.encode("ascii"),
        )
        written_candidate_set_marker = candidate_set_marker_path
        _atomic_write_json(snapshot_path, manifest)
        written_snapshot = snapshot_path

    return QCSummary(
        ledger_path=ledger_path,
        report_path=report_path,
        snapshot_path=written_snapshot,
        candidate_set_path=written_candidate_set,
        candidate_set_marker_path=written_candidate_set_marker,
        attempt_count=len(ledger["attempts"]),
        eligible_count=counts["eligible"],
        hard_rejected_count=counts["hard_rejected"],
        blocked_count=counts["blocked"],
        generation_failed_count=counts["generation_failed"],
        pending_count=pending_count,
        content_review_required_count=content_review_required_count,
    )


def _mechanical_report(mechanical: _MechanicalPass) -> dict[str, Any]:
    return {
        "status": "pass",
        "duration_sec": round(mechanical.opus_probe.duration_sec, 6),
        "wav": {
            "codec": mechanical.wav_probe.codec_name,
            "sample_rate_hz": mechanical.wav_probe.sample_rate_hz,
            "channels": mechanical.wav_probe.channels,
        },
        "opus": {
            "codec": mechanical.opus_probe.codec_name,
            "sample_rate_hz": mechanical.opus_probe.sample_rate_hz,
            "channels": mechanical.opus_probe.channels,
        },
        "loudness": mechanical.loudness.as_manifest_dict(PostprocessProfile()),
        "generation_params": {
            "requested": dict(mechanical.sidecar["gen_params"]["requested"]),
            "realized": dict(mechanical.sidecar["gen_params"]["realized"]),
        },
        "sidecar_provenance": {
            "generation_seconds": mechanical.sidecar["generation_seconds"],
            "postprocess": dict(mechanical.sidecar["postprocess"]),
            "toolchain": dict(mechanical.sidecar["toolchain"]),
            "loudness": dict(mechanical.sidecar["loudness"]),
        },
    }


def _resolve_run_artifact(run_root: Path, relative: str) -> Path:
    path = (run_root / relative).resolve()
    if not path.is_relative_to(run_root.resolve()):
        raise _ProvenanceError(f"take artifact が run root 外を参照しています: {relative}")
    return path


def _source_scenario_ids(
    ledger_source: Mapping[str, Any],
) -> list[str]:
    return sorted(
        {str(group["scenario"]) for group in ledger_source["groups"]},
    )


def _current_scenario_source_sha(
    scenarios_dir: Path,
    *,
    scenario_ids: list[str],
) -> str:
    source_files: list[dict[str, str]] = []
    for scenario_id in scenario_ids:
        path = scenarios_dir / f"{scenario_id}.yaml"
        try:
            source_bytes = path.read_bytes()
        except OSError as error:
            raise _ProvenanceError(
                f"scenario source を再検証できません: {path}",
            ) from error
        source_files.append(
            {
                "path": path.name,
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
            },
        )
    return hashlib.sha256(
        canonical_json(source_files).encode("utf-8"),
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise _ProvenanceError(f"take artifact を読み込めません: {path}") from error
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        encoded = (
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise QCError(f"QC JSON を原子的に書き込めません: {path}: {error}") from error
    _atomic_write_bytes(path, encoded)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, pending_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".pending",
        )
    except OSError as error:
        raise QCError(f"QC JSON を原子的に書き込めません: {path}: {error}") from error

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
        raise QCError(f"QC JSON を原子的に書き込めません: {path}: {error}") from error
    try:
        pending.unlink(missing_ok=True)
    except OSError:
        pass


def _finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
    )


def _attempt_slot(
    attempt: Mapping[str, Any],
) -> tuple[str, str, str, str, int]:
    return (
        str(attempt["model"]),
        str(attempt["scenario"]),
        str(attempt["line"]),
        str(attempt["variant"]),
        int(attempt["take_index"]),
    )


def _find_attempt(
    ledger: Mapping[str, Any],
    slot: tuple[str, str, str, str, int],
) -> dict[str, Any]:
    for attempt in ledger["attempts"]:
        if _attempt_slot(attempt) == slot:
            return attempt
    raise QCError(f"ledger attempt が見つかりません: {_slot_text(slot)}")


def _attempt_identity(attempt: Mapping[str, Any]) -> dict[str, Any]:
    identity = {
        key: attempt[key]
        for key in ("model", "scenario", "line", "variant", "take_index")
    }
    if "take_id" in attempt:
        identity["take_id"] = attempt["take_id"]
    return identity


def _slot_text(slot: tuple[str, str, str, str, int]) -> str:
    return "/".join((*slot[:4], str(slot[4])))


def _require_path_segment(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise QCError(f"{field} は安全な path segment が必要です。")
    return value


def _character_error_rate(expected: str, actual: str) -> float:
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for expected_index, expected_character in enumerate(expected, start=1):
        current = [expected_index]
        for actual_index, actual_character in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[actual_index] + 1,
                    previous[actual_index - 1]
                    + (expected_character != actual_character),
                ),
            )
        previous = current
    return round(previous[-1] / len(expected), 6)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
