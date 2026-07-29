from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class QCReportError(ValueError):
    pass


@dataclass(frozen=True)
class QCAuthority:
    gate_policy_version: str
    attempts_by_slot: dict[
        tuple[str, str, str, str, int],
        dict[str, Any],
    ]


GROUP_KEYS = ("model", "scenario", "line", "variant")
STATUSES = (
    "eligible",
    "hard_rejected",
    "blocked",
    "generation_failed",
    "planned",
    "generated",
)


def validate_qc_report(
    document: Any,
    *,
    ledger_path: Path,
    ledger: Mapping[str, Any],
) -> QCAuthority:
    report = _exact(
        document,
        {
            "format_version",
            "generated_at",
            "gate_policy_version",
            "run_id",
            "source",
            "runtime",
            "summary",
            "attempts",
        },
        "QC report",
    )
    if report["format_version"] != 1:
        raise QCReportError("QC report format_version は 1 が必要です。")
    _text(report["generated_at"], "QC report generated_at")
    gate_policy_version = _text(
        report["gate_policy_version"],
        "QC report gate_policy_version",
    )
    if report["run_id"] != ledger["run_id"]:
        raise QCReportError("QC report run_id が ledger と一致しません。")
    source = _exact(
        report["source"],
        {"ledger", "scenario_sha256", "model", "recipe_version"},
        "QC report source",
    )
    expected_source = {
        "ledger": ledger_path.as_posix(),
        "scenario_sha256": ledger["source"]["scenario_sha256"],
        "model": ledger["source"]["model"],
        "recipe_version": ledger["source"]["recipe_version"],
    }
    if source != expected_source:
        raise QCReportError("QC report source が ledger と一致しません。")
    if not isinstance(report["runtime"], dict):
        raise QCReportError("QC report runtime は object が必要です。")

    counts = {
        status: sum(attempt["status"] == status for attempt in ledger["attempts"])
        for status in STATUSES
    }
    expected_summary = {
        "attempt_count": len(ledger["attempts"]),
        **counts,
        "pending": counts["planned"] + counts["generated"],
    }
    if _exact(report["summary"], set(expected_summary), "QC report summary") != (
        expected_summary
    ):
        raise QCReportError("QC report summary が ledger と一致しません。")
    if not isinstance(report["attempts"], list):
        raise QCReportError("QC report attempts は配列が必要です。")

    ledger_by_slot = {
        tuple(attempt[key] for key in GROUP_KEYS) + (attempt["take_index"],): attempt
        for attempt in ledger["attempts"]
    }
    report_by_slot: dict[
        tuple[str, str, str, str, int],
        dict[str, Any],
    ] = {}
    for index, value in enumerate(report["attempts"]):
        field = f"QC report attempts[{index}]"
        if not isinstance(value, dict):
            raise QCReportError(f"{field} は object が必要です。")
        try:
            slot = tuple(value[key] for key in GROUP_KEYS) + (value["take_index"],)
        except KeyError as error:
            raise QCReportError(f"{field} の identity が不足しています。") from error
        ledger_attempt = ledger_by_slot.get(slot)
        if ledger_attempt is None:
            raise QCReportError("QC report attempt が ledger slot の範囲外です。")
        expected_keys = {
            *GROUP_KEYS,
            "take_index",
            "status",
            "gates",
            "mechanical",
            "content",
        }
        if "take_id" in ledger_attempt:
            expected_keys.add("take_id")
        report_attempt = _exact(value, expected_keys, field)
        if slot in report_by_slot:
            raise QCReportError("QC report attempt slot が重複しています。")
        for key in (*GROUP_KEYS, "take_index"):
            if report_attempt[key] != ledger_attempt[key]:
                raise QCReportError(
                    "QC report attempt identity が ledger と一致しません。",
                )
        if (
            "take_id" in ledger_attempt
            and report_attempt["take_id"] != ledger_attempt["take_id"]
        ):
            raise QCReportError("QC report attempt take_id が ledger と一致しません。")
        if (
            report_attempt["status"] != ledger_attempt["status"]
            or report_attempt["gates"] != ledger_attempt.get("gates")
        ):
            raise QCReportError(
                "QC report attempt status/gates が ledger と一致しません。",
            )
        _validate_attempt_payload(
            report_attempt,
            ledger_attempt=ledger_attempt,
            field=field,
        )
        report_by_slot[slot] = report_attempt
    if set(report_by_slot) != set(ledger_by_slot):
        raise QCReportError(
            "QC report attempts が ledger attempts を完全に被覆していません。",
        )
    return QCAuthority(
        gate_policy_version=gate_policy_version,
        attempts_by_slot=report_by_slot,
    )


def _validate_attempt_payload(
    report: Mapping[str, Any],
    *,
    ledger_attempt: Mapping[str, Any],
    field: str,
) -> None:
    status = str(ledger_attempt["status"])
    gates = ledger_attempt.get("gates")
    mechanical = report["mechanical"]
    content = report["content"]

    if status in {"planned", "generation_failed"}:
        _not_run_or_reason(mechanical, f"{field}.mechanical")
        _not_run(content, f"{field}.content")
        return

    if status == "generated":
        _not_run(mechanical, f"{field}.mechanical")
        _not_run(content, f"{field}.content")
        return

    if not isinstance(gates, dict):
        raise QCReportError(f"{field}.gates は object が必要です。")
    mechanical_gate = gates["mechanical"]
    content_gate = gates["content"]

    if status == "eligible":
        _mechanical_pass(mechanical, f"{field}.mechanical")
        _content_result(
            content,
            expected_status=content_gate,
            field=f"{field}.content",
        )
        return

    if status == "blocked":
        if (mechanical_gate, content_gate) == ("blocked", "not_run"):
            _status_reason(
                mechanical,
                expected_status="blocked",
                field=f"{field}.mechanical",
            )
            _status_reason(
                content,
                expected_status="not_run",
                field=f"{field}.content",
            )
            return
        if (mechanical_gate, content_gate) == ("pass", "blocked"):
            _mechanical_pass(mechanical, f"{field}.mechanical")
            _status_reason(
                content,
                expected_status="blocked",
                field=f"{field}.content",
            )
            return
        raise QCReportError(f"{field} の blocked gates が不正です。")

    if status == "hard_rejected":
        if (mechanical_gate, content_gate) == ("pass", "reject"):
            _mechanical_pass_or_terminal_not_rechecked(
                mechanical,
                f"{field}.mechanical",
            )
            _content_result(
                content,
                expected_status="reject",
                field=f"{field}.content",
            )
            return
        if (mechanical_gate, content_gate) == ("reject", "not_run"):
            _mechanical_rejection(mechanical, f"{field}.mechanical")
            _content_not_run(
                content,
                field=f"{field}.content",
            )
            return
        raise QCReportError(f"{field} の hard_rejected gates が不正です。")

    raise QCReportError(f"{field}.status が未対応です: {status}")


def _mechanical_pass(value: Any, field: str) -> None:
    mechanical = _mechanical_common(
        value,
        {
            "status",
            "duration_sec",
            "wav",
            "opus",
            "loudness",
            "generation_params",
            "sidecar_provenance",
        },
        field,
    )
    if mechanical["status"] != "pass":
        raise QCReportError(f"{field}.status は pass が必要です。")


def _mechanical_rejection(value: Any, field: str) -> None:
    if isinstance(value, dict) and set(value) == {
        "status",
        "duration_sec",
        "wav",
        "opus",
        "loudness",
        "generation_params",
        "sidecar_provenance",
    }:
        _mechanical_pass(value, field)
        return
    if isinstance(value, dict) and set(value) == {"status", "reason"}:
        _status_reason(value, expected_status="reject", field=field)
        return
    mechanical = _mechanical_common(
        value,
        {
            "status",
            "reason",
            "duration_sec",
            "wav",
            "opus",
            "loudness",
            "generation_params",
            "sidecar_provenance",
        },
        field,
    )
    if mechanical["status"] != "reject":
        raise QCReportError(f"{field}.status は reject が必要です。")
    _text(mechanical["reason"], f"{field}.reason")


def _mechanical_pass_or_terminal_not_rechecked(value: Any, field: str) -> None:
    if isinstance(value, dict) and set(value) == {"status", "reason"}:
        document = _exact(value, {"status", "reason"}, field)
        if (
            document["status"] != "pass"
            or document["reason"] != "terminal_not_rechecked"
        ):
            raise QCReportError(f"{field} の terminal recheck report が不正です。")
        return
    _mechanical_pass(value, field)


def _mechanical_common(
    value: Any,
    keys: set[str],
    field: str,
) -> dict[str, Any]:
    mechanical = _exact(value, keys, field)
    _finite_nonnegative(mechanical["duration_sec"], f"{field}.duration_sec")
    wav = _exact(
        mechanical["wav"],
        {"codec", "sample_rate_hz", "channels"},
        f"{field}.wav",
    )
    if wav != {
        "codec": "pcm_s16le",
        "sample_rate_hz": 48_000,
        "channels": 1,
    }:
        raise QCReportError(f"{field}.wav が固定 PCM16/48kHz/mono と一致しません。")
    opus = _exact(
        mechanical["opus"],
        {"codec", "sample_rate_hz", "channels"},
        f"{field}.opus",
    )
    if opus != {
        "codec": "opus",
        "sample_rate_hz": 48_000,
        "channels": 1,
    }:
        raise QCReportError(f"{field}.opus が固定 Opus/48kHz/mono と一致しません。")
    loudness = _exact(
        mechanical["loudness"],
        {"source", "i_lufs", "tp_dbtp", "shortfall"},
        f"{field}.loudness",
    )
    if loudness["source"] != "encoded_opus":
        raise QCReportError(f"{field}.loudness.source が不正です。")
    _finite_number(loudness["i_lufs"], f"{field}.loudness.i_lufs")
    _finite_number(loudness["tp_dbtp"], f"{field}.loudness.tp_dbtp")
    if not isinstance(loudness["shortfall"], bool):
        raise QCReportError(f"{field}.loudness.shortfall は boolean が必要です。")
    generation_params = _exact(
        mechanical["generation_params"],
        {"requested", "realized"},
        f"{field}.generation_params",
    )
    if not isinstance(generation_params["requested"], dict) or not isinstance(
        generation_params["realized"],
        dict,
    ):
        raise QCReportError(
            f"{field}.generation_params requested/realized は object が必要です。",
        )
    provenance = _exact(
        mechanical["sidecar_provenance"],
        {"generation_seconds", "postprocess", "toolchain", "loudness"},
        f"{field}.sidecar_provenance",
    )
    _finite_nonnegative(
        provenance["generation_seconds"],
        f"{field}.sidecar_provenance.generation_seconds",
    )
    for key in ("postprocess", "toolchain", "loudness"):
        if not isinstance(provenance[key], dict):
            raise QCReportError(
                f"{field}.sidecar_provenance.{key} は object が必要です。",
            )
    return mechanical


def _content_result(
    value: Any,
    *,
    expected_status: Any,
    field: str,
) -> None:
    if not isinstance(expected_status, str):
        raise QCReportError(f"{field} の expected status が不正です。")
    if isinstance(value, dict) and set(value) == {"status", "inspection"}:
        content = _exact(value, {"status", "inspection"}, field)
        if content["inspection"] != "terminal_not_repeated":
            raise QCReportError(f"{field}.inspection が不正です。")
    else:
        content = _exact(
            value,
            {"status", "expected_reading", "asr", "reading", "prosody"},
            field,
        )
        expected = _exact(
            content["expected_reading"],
            {"text", "source", "normalized", "authoritative", "ambiguous_terms"},
            f"{field}.expected_reading",
        )
        for key in ("text", "source", "normalized"):
            _text(expected[key], f"{field}.expected_reading.{key}")
        if not isinstance(expected["authoritative"], bool):
            raise QCReportError(
                f"{field}.expected_reading.authoritative は boolean が必要です。",
            )
        if not isinstance(expected["ambiguous_terms"], list):
            raise QCReportError(
                f"{field}.expected_reading.ambiguous_terms は配列が必要です。",
            )
        for index, item in enumerate(expected["ambiguous_terms"]):
            ambiguous = _exact(
                item,
                {"surface", "candidates"},
                f"{field}.expected_reading.ambiguous_terms[{index}]",
            )
            _text(
                ambiguous["surface"],
                f"{field}.expected_reading.ambiguous_terms[{index}].surface",
            )
            if not isinstance(ambiguous["candidates"], list) or not all(
                isinstance(candidate, str) and candidate
                for candidate in ambiguous["candidates"]
            ):
                raise QCReportError(
                    f"{field}.expected_reading.ambiguous_terms[{index}]"
                    ".candidates が不正です。",
                )
        asr = _exact(
            content["asr"],
            {"text", "normalized_reading", "average_log_probability"},
            f"{field}.asr",
        )
        for key in ("text", "normalized_reading"):
            _text(asr[key], f"{field}.asr.{key}")
        average = asr["average_log_probability"]
        if average is not None:
            _finite_number(average, f"{field}.asr.average_log_probability")
        reading = _exact(
            content["reading"],
            {"character_error_rate", "reading_mismatch"},
            f"{field}.reading",
        )
        _finite_nonnegative(
            reading["character_error_rate"],
            f"{field}.reading.character_error_rate",
        )
        if reading["reading_mismatch"] is not None and not isinstance(
            reading["reading_mismatch"],
            bool,
        ):
            raise QCReportError(
                f"{field}.reading.reading_mismatch は boolean/null が必要です。",
            )
        if not isinstance(content["prosody"], dict):
            raise QCReportError(f"{field}.prosody は object が必要です。")
    if content["status"] != expected_status:
        raise QCReportError(
            f"{field}.status が ledger gates.content と一致しません。",
        )


def _content_not_run(value: Any, *, field: str) -> None:
    if isinstance(value, dict) and set(value) == {"status"}:
        _not_run(value, field)
        return
    _content_result(value, expected_status="not_run", field=field)


def _not_run(value: Any, field: str) -> None:
    document = _exact(value, {"status"}, field)
    if document["status"] != "not_run":
        raise QCReportError(f"{field}.status は not_run が必要です。")


def _not_run_or_reason(value: Any, field: str) -> None:
    if isinstance(value, dict) and set(value) == {"status"}:
        _not_run(value, field)
        return
    _status_reason(value, expected_status="not_run", field=field)


def _status_reason(
    value: Any,
    *,
    expected_status: str,
    field: str,
) -> None:
    document = _exact(value, {"status", "reason"}, field)
    if document["status"] != expected_status:
        raise QCReportError(f"{field}.status は {expected_status} が必要です。")
    _text(document["reason"], f"{field}.reason")


def _exact(value: Any, fields: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise QCReportError(f"{field} の項目が exact contract と一致しません。")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise QCReportError(f"{field} は空でない文字列が必要です。")
    return value


def _finite_number(value: Any, field: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
    ):
        raise QCReportError(f"{field} は有限数が必要です。")


def _finite_nonnegative(value: Any, field: str) -> None:
    _finite_number(value, field)
    if value < 0:
        raise QCReportError(f"{field} は非負数が必要です。")
