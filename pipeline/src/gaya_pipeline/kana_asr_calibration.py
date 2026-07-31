from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from gaya_pipeline.japanese_reading import (
    character_error_rate,
    normalize_japanese_reading,
)


class KanaAsrCalibrationError(ValueError):
    pass


ROOT_KEYS = {"format_version", "source", "cases"}
SOURCE_KEYS = {
    "issue_url",
    "human_evidence",
    "model",
    "candidate_manifest_sha256",
    "upstream",
    "asr",
}
HUMAN_EVIDENCE_KEYS = {
    "url",
    "scope",
    "word_reading_incorrect_count",
    "current_detected_count",
    "human_asr_missed_count",
    "per_case_decision_exported",
}
UPSTREAM_SOURCE_KEYS = {
    "repository",
    "revision",
    "component",
    "pykakasi_version",
}
ASR_SOURCE_KEYS = {"model", "revision"}
CASE_KEYS = {
    "identity",
    "surface_text",
    "expected_reading",
    "upstream_g2p",
    "asr_transcript",
    "current_decision",
    "audio_sha256",
}
IDENTITY_KEYS = {
    "scenario",
    "line",
    "variant",
    "take_index",
    "take_id",
    "generation_input_sha256",
}
CURRENT_DECISION_KEYS = {"status", "review_reason", "reading_mismatch"}
UPSTREAM_G2P_KEYS = {"text", "assessment", "semantic_targets"}
SEMANTIC_TARGET_KEYS = {"surface", "expected", "upstream", "assessment"}
CURRENT_STATUSES = {"pass", "review_required"}
UPSTREAM_ASSESSMENTS = {"equivalent", "meaning_changed"}
PRODUCTION_POLICY_DECISION = {
    "normalized_exact_match": "retained",
    "reading_mismatch": "retained_soft_review",
    "character_error_rate": "report_only",
    "threshold_change": "none",
    "hard_reject_change": "none",
    "alternate_asr_or_fallback": "none",
}


def load_calibration_cases(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise KanaAsrCalibrationError(
            f"calibration cases を読み込めません: {path}: {error}",
        ) from error
    return validate_calibration_cases(document)


def validate_calibration_cases(document: Any) -> dict[str, Any]:
    root = _exact(document, ROOT_KEYS, "cases")
    if root["format_version"] != 1:
        raise KanaAsrCalibrationError("cases.format_version は 1 が必要です。")
    human_evidence = _validate_source(root["source"])
    cases = root["cases"]
    if not isinstance(cases, list) or not cases:
        raise KanaAsrCalibrationError("cases.cases は空でない配列が必要です。")

    slots: set[tuple[str, str, str, int]] = set()
    take_ids: set[str] = set()
    current_detected_count = 0
    for index, value in enumerate(cases):
        identity = _validate_case(value, f"cases.cases[{index}]")
        slot = identity[:4]
        take_id = identity[4]
        if slot in slots:
            raise KanaAsrCalibrationError("case identity slot が重複しています。")
        if take_id in take_ids:
            raise KanaAsrCalibrationError("case identity take_id が重複しています。")
        slots.add(slot)
        take_ids.add(take_id)
        current_detected_count += value["current_decision"]["status"] == "review_required"
    _validate_human_evidence_totals(
        human_evidence,
        case_count=len(cases),
        current_detected_count=current_detected_count,
    )
    return root


def build_calibration_report(document: Any) -> dict[str, Any]:
    cases_document = validate_calibration_cases(document)
    report_cases = [_report_case(case) for case in cases_document["cases"]]
    report_cases.sort(key=_report_identity_key)

    human_evidence = cases_document["source"]["human_evidence"]
    upstream_meaning_changed_count = sum(
        case["upstream_g2p"]["assessment"] == "meaning_changed"
        for case in report_cases
    )
    current_decision_consistent_count = sum(
        case["current_decision"]["consistent"] for case in report_cases
    )
    semantic_target_count = sum(
        len(case["upstream_g2p"]["semantic_targets"])
        for case in report_cases
    )
    return {
        "format_version": 1,
        "source": deepcopy(cases_document["source"]),
        "production_policy_decision": dict(PRODUCTION_POLICY_DECISION),
        "summary": {
            "case_count": len(report_cases),
            "word_reading_incorrect_count": human_evidence[
                "word_reading_incorrect_count"
            ],
            "current_detected_count": human_evidence["current_detected_count"],
            "human_asr_missed_count": human_evidence["human_asr_missed_count"],
            "upstream_meaning_changed_count": upstream_meaning_changed_count,
            "semantic_target_count": semantic_target_count,
            "current_decision_consistent_count": current_decision_consistent_count,
            "current_decision_inconsistent_count": (
                len(report_cases) - current_decision_consistent_count
            ),
        },
        "cases": report_cases,
    }


def write_calibration_report(*, input_path: Path, output_path: Path) -> None:
    if output_path.exists():
        raise KanaAsrCalibrationError(
            f"calibration report output は既に存在します: {output_path}",
        )
    report = build_calibration_report(load_calibration_cases(input_path))
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    try:
        with output_path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(payload)
    except FileExistsError as error:
        raise KanaAsrCalibrationError(
            f"calibration report output は既に存在します: {output_path}",
        ) from error
    except OSError as error:
        raise KanaAsrCalibrationError(
            f"calibration report を書き込めません: {output_path}: {error}",
        ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gaya_pipeline.kana_asr_calibration")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        write_calibration_report(
            input_path=arguments.input,
            output_path=arguments.output,
        )
    except KanaAsrCalibrationError as error:
        parser.exit(1, f"error: {error}\n")
    return 0


def _validate_source(value: Any) -> dict[str, Any]:
    source = _exact(value, SOURCE_KEYS, "cases.source")
    _https_url(source["issue_url"], "cases.source.issue_url")
    human_evidence = _exact(
        source["human_evidence"],
        HUMAN_EVIDENCE_KEYS,
        "cases.source.human_evidence",
    )
    _https_url(human_evidence["url"], "cases.source.human_evidence.url")
    if human_evidence["scope"] != "first_seven_batch":
        raise KanaAsrCalibrationError(
            "cases.source.human_evidence.scope は first_seven_batch が必要です。",
        )
    for key in (
        "word_reading_incorrect_count",
        "current_detected_count",
        "human_asr_missed_count",
    ):
        _positive_int(human_evidence[key], f"cases.source.human_evidence.{key}")
    if not isinstance(human_evidence["per_case_decision_exported"], bool):
        raise KanaAsrCalibrationError(
            "cases.source.human_evidence.per_case_decision_exported は boolean が必要です。",
        )
    if human_evidence["per_case_decision_exported"] is not False:
        raise KanaAsrCalibrationError(
            "cases.source.human_evidence.per_case_decision_exported は false が必要です。",
        )
    _text(source["model"], "cases.source.model")
    _sha256(
        source["candidate_manifest_sha256"],
        "cases.source.candidate_manifest_sha256",
    )
    upstream = _exact(source["upstream"], UPSTREAM_SOURCE_KEYS, "cases.source.upstream")
    _text(upstream["repository"], "cases.source.upstream.repository")
    _git_revision(upstream["revision"], "cases.source.upstream.revision")
    _text(upstream["component"], "cases.source.upstream.component")
    _text(upstream["pykakasi_version"], "cases.source.upstream.pykakasi_version")
    asr = _exact(source["asr"], ASR_SOURCE_KEYS, "cases.source.asr")
    _text(asr["model"], "cases.source.asr.model")
    _git_revision(asr["revision"], "cases.source.asr.revision")
    return human_evidence


def _validate_case(
    value: Any,
    field: str,
) -> tuple[str, str, str, int, str, str]:
    case = _exact(value, CASE_KEYS, field)
    identity_value = _exact(case["identity"], IDENTITY_KEYS, f"{field}.identity")
    identity = (
        _path_segment(identity_value["scenario"], f"{field}.identity.scenario"),
        _path_segment(identity_value["line"], f"{field}.identity.line"),
        _path_segment(identity_value["variant"], f"{field}.identity.variant"),
        _positive_int(identity_value["take_index"], f"{field}.identity.take_index"),
        _sha256(identity_value["take_id"], f"{field}.identity.take_id"),
        _sha256(
            identity_value["generation_input_sha256"],
            f"{field}.identity.generation_input_sha256",
        ),
    )
    surface_text = _text(case["surface_text"], f"{field}.surface_text")
    expected = _reading(case["expected_reading"], f"{field}.expected_reading")
    upstream = _validate_upstream_g2p(case["upstream_g2p"], f"{field}.upstream_g2p")
    actual = _reading(case["asr_transcript"], f"{field}.asr_transcript")
    current_decision = _validate_current_decision(
        case["current_decision"],
        f"{field}.current_decision",
    )
    _sha256(case["audio_sha256"], f"{field}.audio_sha256")
    expected_normalized = normalize_japanese_reading(expected)
    upstream_normalized = normalize_japanese_reading(upstream["text"])
    actual_normalized = normalize_japanese_reading(actual)
    if not upstream_normalized:
        raise KanaAsrCalibrationError(
            f"{field}.upstream_g2p.text の正規化結果が空です。",
        )
    if not expected_normalized or not actual_normalized:
        raise KanaAsrCalibrationError(f"{field} の reading 正規化結果が空です。")
    _validate_semantic_target_alignment(
        upstream,
        surface_text=surface_text,
        expected_normalized=expected_normalized,
        upstream_normalized=upstream_normalized,
        field=f"{field}.upstream_g2p",
    )
    if current_decision != _recompute_current_decision(
        expected_normalized,
        actual_normalized,
    ):
        raise KanaAsrCalibrationError(
            f"{field}.current_decision が expected_reading / asr_transcript と一致しません。",
        )
    return identity


def _validate_upstream_g2p(value: Any, field: str) -> dict[str, Any]:
    upstream = _exact(value, UPSTREAM_G2P_KEYS, field)
    _reading(upstream["text"], f"{field}.text")
    assessment = upstream["assessment"]
    if assessment not in UPSTREAM_ASSESSMENTS:
        raise KanaAsrCalibrationError(f"{field}.assessment が不正です。")
    targets = upstream["semantic_targets"]
    if not isinstance(targets, list):
        raise KanaAsrCalibrationError(f"{field}.semantic_targets は配列が必要です。")
    seen: set[tuple[str, str, str]] = set()
    for index, value in enumerate(targets):
        target_field = f"{field}.semantic_targets[{index}]"
        target = _exact(value, SEMANTIC_TARGET_KEYS, target_field)
        identity = (
            _text(target["surface"], f"{target_field}.surface"),
            _reading(target["expected"], f"{target_field}.expected"),
            _reading(target["upstream"], f"{target_field}.upstream"),
        )
        if target["assessment"] != "meaning_changed":
            raise KanaAsrCalibrationError(
                f"{target_field}.assessment は meaning_changed が必要です。",
            )
        if identity in seen:
            raise KanaAsrCalibrationError(f"{field}.semantic_targets が重複しています。")
        seen.add(identity)
    if assessment == "meaning_changed" and not targets:
        raise KanaAsrCalibrationError(
            f"{field}.assessment=meaning_changed には semantic target が必要です。",
        )
    if assessment == "equivalent" and targets:
        raise KanaAsrCalibrationError(
            f"{field}.assessment=equivalent に semantic target は指定できません。",
        )
    return upstream


def _validate_current_decision(value: Any, field: str) -> dict[str, Any]:
    decision = _exact(value, CURRENT_DECISION_KEYS, field)
    if decision["status"] not in CURRENT_STATUSES:
        raise KanaAsrCalibrationError(f"{field}.status が不正です。")
    expected = (
        {"status": "pass", "review_reason": None, "reading_mismatch": False}
        if decision["status"] == "pass"
        else {
            "status": "review_required",
            "review_reason": "explicit_reading_mismatch",
            "reading_mismatch": True,
        }
    )
    if decision != expected:
        raise KanaAsrCalibrationError(f"{field} の項目が status と一致しません。")
    return decision


def _report_case(case: dict[str, Any]) -> dict[str, Any]:
    expected = normalize_japanese_reading(case["expected_reading"])
    upstream = normalize_japanese_reading(case["upstream_g2p"]["text"])
    actual = normalize_japanese_reading(case["asr_transcript"])
    recomputed_decision = _recompute_current_decision(expected, actual)
    recorded_decision = deepcopy(case["current_decision"])
    return {
        "identity": deepcopy(case["identity"]),
        "audio_sha256": case["audio_sha256"],
        "surface_text": case["surface_text"],
        "expected_reading": {
            "text": case["expected_reading"],
            "normalized": expected,
        },
        "upstream_g2p": {
            "text": case["upstream_g2p"]["text"],
            "normalized": upstream,
            "character_error_rate": character_error_rate(expected, upstream),
            "assessment": case["upstream_g2p"]["assessment"],
            "semantic_targets": deepcopy(case["upstream_g2p"]["semantic_targets"]),
        },
        "asr": {
            "text": case["asr_transcript"],
            "normalized": actual,
            "character_error_rate": character_error_rate(expected, actual),
        },
        "current_decision": {
            "recorded": recorded_decision,
            "recomputed": recomputed_decision,
            "consistent": recorded_decision == recomputed_decision,
        },
    }


def _validate_human_evidence_totals(
    evidence: dict[str, Any],
    *,
    case_count: int,
    current_detected_count: int,
) -> None:
    incorrect_count = evidence["word_reading_incorrect_count"]
    if incorrect_count != case_count:
        raise KanaAsrCalibrationError(
            "cases.source.human_evidence.word_reading_incorrect_count は case 件数と"
            "一致する必要があります。",
        )
    if evidence["current_detected_count"] != current_detected_count:
        raise KanaAsrCalibrationError(
            "cases.source.human_evidence.current_detected_count は case の現行判定集計と"
            "一致する必要があります。",
        )
    if evidence["human_asr_missed_count"] != incorrect_count - current_detected_count:
        raise KanaAsrCalibrationError(
            "cases.source.human_evidence.human_asr_missed_count は incorrect - detected と"
            "一致する必要があります。",
        )


def _validate_semantic_target_alignment(
    upstream: dict[str, Any],
    *,
    surface_text: str,
    expected_normalized: str,
    upstream_normalized: str,
    field: str,
) -> None:
    for index, target in enumerate(upstream["semantic_targets"]):
        target_field = f"{field}.semantic_targets[{index}]"
        if target["surface"] not in surface_text:
            raise KanaAsrCalibrationError(
                f"{target_field}.surface が case.surface_text に存在しません。",
            )
        target_expected = normalize_japanese_reading(target["expected"])
        if not target_expected or target_expected not in expected_normalized:
            raise KanaAsrCalibrationError(
                f"{target_field}.expected が expected_reading に存在しません。",
            )
        target_upstream = normalize_japanese_reading(target["upstream"])
        if not target_upstream or target_upstream not in upstream_normalized:
            raise KanaAsrCalibrationError(
                f"{target_field}.upstream が upstream_g2p.text に存在しません。",
            )


def _recompute_current_decision(expected: str, actual: str) -> dict[str, Any]:
    if expected == actual:
        return {"status": "pass", "review_reason": None, "reading_mismatch": False}
    return {
        "status": "review_required",
        "review_reason": "explicit_reading_mismatch",
        "reading_mismatch": True,
    }


def _report_identity_key(case: dict[str, Any]) -> tuple[str, str, str, int]:
    identity = case["identity"]
    return (
        identity["scenario"],
        identity["line"],
        identity["variant"],
        identity["take_index"],
    )


def _exact(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise KanaAsrCalibrationError(f"{field} の項目が契約と一致しません。")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KanaAsrCalibrationError(f"{field} は空でない文字列が必要です。")
    return value


def _reading(value: Any, field: str) -> str:
    return _text(value, field)


def _path_segment(value: Any, field: str) -> str:
    text = _text(value, field)
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise KanaAsrCalibrationError(f"{field} は安全な path segment が必要です。")
    return text


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise KanaAsrCalibrationError(f"{field} は 1 以上の整数が必要です。")
    return value


def _sha256(value: Any, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise KanaAsrCalibrationError(f"{field} は完全な小文字 SHA-256 が必要です。")
    return text


def _git_revision(value: Any, field: str) -> str:
    text = _text(value, field)
    if len(text) != 40 or any(character not in "0123456789abcdef" for character in text):
        raise KanaAsrCalibrationError(f"{field} は完全な小文字 git commit hash が必要です。")
    return text


def _https_url(value: Any, field: str) -> str:
    text = _text(value, field)
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.netloc:
        raise KanaAsrCalibrationError(f"{field} は完全な HTTPS URL が必要です。")
    return text


if __name__ == "__main__":
    raise SystemExit(main())
