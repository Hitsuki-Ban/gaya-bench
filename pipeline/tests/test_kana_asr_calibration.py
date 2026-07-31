from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline.japanese_reading import character_error_rate
from gaya_pipeline.kana_asr_calibration import (
    KanaAsrCalibrationError,
    build_calibration_report,
    load_calibration_cases,
    validate_calibration_cases,
    write_calibration_report,
)


REAL_CASES_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "research"
    / "kana-asr-calibration"
    / "cases.json"
)


def test_real_cases_fixtureは7_2_5_1の校正結果を再現(tmp_path: Path) -> None:
    document = load_calibration_cases(REAL_CASES_PATH)
    report = build_calibration_report(document)

    assert report["summary"] == {
        "case_count": 7,
        "word_reading_incorrect_count": 7,
        "current_detected_count": 2,
        "human_asr_missed_count": 5,
        "upstream_meaning_changed_count": 1,
        "semantic_target_count": 1,
        "current_decision_consistent_count": 7,
        "current_decision_inconsistent_count": 0,
    }
    assert report["production_policy_decision"] == {
        "normalized_exact_match": "retained",
        "reading_mismatch": "retained_soft_review",
        "character_error_rate": "report_only",
        "threshold_change": "none",
        "hard_reject_change": "none",
        "alternate_asr_or_fallback": "none",
    }
    output = tmp_path / "report.json"
    write_calibration_report(input_path=REAL_CASES_PATH, output_path=output)
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_reportは7件中2件検出5件漏検と明示的意味変更1件を集計() -> None:
    report = build_calibration_report(_fixture())

    assert report["summary"] == {
        "case_count": 7,
        "word_reading_incorrect_count": 7,
        "current_detected_count": 2,
        "human_asr_missed_count": 5,
        "upstream_meaning_changed_count": 1,
        "semantic_target_count": 1,
        "current_decision_consistent_count": 7,
        "current_decision_inconsistent_count": 0,
    }
    messenger = next(
        case for case in report["cases"] if case["identity"]["line"] == "messenger-003"
    )
    assert messenger["expected_reading"] == {
        "text": "サガレ",
        "normalized": "サガレ",
    }
    assert messenger["upstream_g2p"]["text"] == "タイガレ"
    assert messenger["upstream_g2p"]["assessment"] == "meaning_changed"
    assert messenger["upstream_g2p"]["semantic_targets"] == [
        {
            "surface": "退がれ",
            "expected": "サガレ",
            "upstream": "タイガレ",
            "assessment": "meaning_changed",
        },
    ]
    assert "human_decision" not in messenger
    assert "human_asr_miss" not in messenger


def test_Kana差分だけではupstream意味変更を推論しない() -> None:
    report = build_calibration_report(_fixture())
    equivalent = next(
        case for case in report["cases"] if case["identity"]["line"] == "merchant-001"
    )

    assert equivalent["upstream_g2p"]["normalized"] != equivalent["expected_reading"][
        "normalized"
    ]
    assert equivalent["upstream_g2p"]["character_error_rate"] > 0
    assert equivalent["upstream_g2p"]["assessment"] == "equivalent"
    assert equivalent["upstream_g2p"]["semantic_targets"] == []


def test_reportは入力case順に依存しない() -> None:
    fixture = _fixture()
    reversed_fixture = deepcopy(fixture)
    reversed_fixture["cases"].reverse()

    assert build_calibration_report(fixture) == build_calibration_report(reversed_fixture)


def test_current_decisionと再計算結果の矛盾を拒否() -> None:
    fixture = _fixture()
    fixture["cases"][0]["current_decision"] = _mismatch_decision()

    with pytest.raises(KanaAsrCalibrationError, match="expected_reading / asr_transcript"):
        build_calibration_report(fixture)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("surface", "存在しない表記"),
        ("expected", "チガウヨミ"),
        ("upstream", "チガウヨミ"),
    ],
)
def test_semantic_targetはcase内の実在範囲だけを許可(field: str, value: str) -> None:
    fixture = _fixture()
    fixture["cases"][2]["upstream_g2p"]["semantic_targets"][0][field] = value

    with pytest.raises(KanaAsrCalibrationError, match=f"{field}.*存在しません"):
        validate_calibration_cases(fixture)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.update(extra=True),
        lambda document: document["source"].update(extra=True),
        lambda document: document["source"]["human_evidence"].update(extra=True),
        lambda document: document["source"]["upstream"].update(extra=True),
        lambda document: document["cases"][0].update(extra=True),
        lambda document: document["cases"][0]["identity"].update(extra=True),
        lambda document: document["cases"][2]["upstream_g2p"]["semantic_targets"][0].update(
            extra=True,
        ),
    ],
)
def test_unknown_fieldを全階層で拒否(mutation: Any) -> None:
    fixture = _fixture()
    mutation(fixture)

    with pytest.raises(KanaAsrCalibrationError, match="契約と一致"):
        validate_calibration_cases(fixture)


def test_duplicate_identityを拒否() -> None:
    fixture = _fixture()
    fixture["cases"][1]["identity"] = deepcopy(fixture["cases"][0]["identity"])

    with pytest.raises(KanaAsrCalibrationError, match="identity slot.*重複"):
        validate_calibration_cases(fixture)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document["cases"][0]["current_decision"].update(status="unknown"),
        lambda document: document["source"]["human_evidence"].update(scope="pitch_accent"),
        lambda document: document["cases"][0]["upstream_g2p"].update(assessment="guessed"),
        lambda document: document["cases"][2]["upstream_g2p"]["semantic_targets"][0].update(
            assessment="equivalent",
        ),
    ],
)
def test_unknown_enumを拒否(mutation: Any) -> None:
    fixture = _fixture()
    mutation(fixture)

    with pytest.raises(KanaAsrCalibrationError):
        validate_calibration_cases(fixture)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document["source"].update(candidate_manifest_sha256="A" * 64),
        lambda document: document["source"]["upstream"].update(revision="a" * 39),
        lambda document: document["source"]["asr"].update(revision="not-a-commit"),
        lambda document: document["cases"][0]["identity"].update(take_id="1" * 63),
        lambda document: document["cases"][0]["identity"].update(
            generation_input_sha256="g" * 64,
        ),
        lambda document: document["cases"][0].update(audio_sha256="0" * 65),
    ],
)
def test_invalid_hashを拒否(mutation: Any) -> None:
    fixture = _fixture()
    mutation(fixture)

    with pytest.raises(KanaAsrCalibrationError, match="hash|SHA-256"):
        validate_calibration_cases(fixture)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("word_reading_incorrect_count", True),
        ("current_detected_count", 0),
        ("human_asr_missed_count", "5"),
        ("per_case_decision_exported", 0),
        ("per_case_decision_exported", True),
    ],
)
def test_human_evidenceのcountとbooleanを厳格検証(field: str, value: Any) -> None:
    fixture = _fixture()
    fixture["source"]["human_evidence"][field] = value

    with pytest.raises(KanaAsrCalibrationError):
        validate_calibration_cases(fixture)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("word_reading_incorrect_count", 6),
        ("current_detected_count", 3),
        ("human_asr_missed_count", 4),
    ],
)
def test_human_evidence集計とcase派生値の矛盾を拒否(field: str, value: int) -> None:
    fixture = _fixture()
    fixture["source"]["human_evidence"][field] = value

    with pytest.raises(KanaAsrCalibrationError, match="human_evidence"):
        validate_calibration_cases(fixture)


@pytest.mark.parametrize(
    ("expected", "actual", "rate"),
    [
        ("", "", 0.0),
        ("", "ア", 1.0),
        ("サガレ", "タイガレ", 0.666667),
        ("コーヒー", "コーヒ", 0.25),
    ],
)
def test_shared_character_error_rateは既存QC計算を維持(
    expected: str,
    actual: str,
    rate: float,
) -> None:
    assert character_error_rate(expected, actual) == rate


def test_python_module_entrypointは決定的reportを書き既存outputを拒否(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "cases.json"
    output_path = tmp_path / "report.json"
    input_path.write_text(
        json.dumps(_fixture(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gaya_pipeline.kana_asr_calibration",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    first_bytes = output_path.read_bytes()
    assert json.loads(first_bytes)["summary"]["human_asr_missed_count"] == 5

    second = subprocess.run(
        [
            sys.executable,
            "-m",
            "gaya_pipeline.kana_asr_calibration",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert second.returncode == 1
    assert "既に存在" in second.stderr
    assert output_path.read_bytes() == first_bytes


def test_existing_outputはinput読込より先にfail_fast(tmp_path: Path) -> None:
    output_path = tmp_path / "report.json"
    output_path.write_bytes(b"stable\n")

    with pytest.raises(KanaAsrCalibrationError, match="既に存在"):
        write_calibration_report(
            input_path=tmp_path / "missing-cases.json",
            output_path=output_path,
        )

    assert output_path.read_bytes() == b"stable\n"


def _fixture() -> dict[str, Any]:
    cases = [
        _case(
            index=0,
            line="merchant-001",
            surface_text="珈琲をどうぞ。",
            expected="コーヒー",
            upstream="コーヒ",
            asr="コーヒー",
        ),
        _case(
            index=1,
            line="guard-001",
            surface_text="門を閉めろ。",
            expected="モンヲシメロ",
            upstream="モンヲシメロ",
            asr="モンヲシメロ",
        ),
        _case(
            index=2,
            line="messenger-003",
            surface_text="退がれ！",
            expected="サガレ",
            upstream="タイガレ",
            asr="サガレ",
            meaning_changed=True,
        ),
        _case(
            index=3,
            line="innkeeper-001",
            surface_text="部屋は空いてるよ。",
            expected="ヘヤワアイテルヨ",
            upstream="ヘヤワアイテルヨ",
            asr="ヘヤワアイテルヨ",
        ),
        _case(
            index=4,
            line="traveler-001",
            surface_text="次の街まで頼む。",
            expected="ツギノマチマデタノム",
            upstream="ツギノマチマデタノム",
            asr="ツギノマチマデタノム",
        ),
        _case(
            index=5,
            line="cook-001",
            surface_text="火を弱めて。",
            expected="ヒヲヨワメテ",
            upstream="ヒヲヨワメテ",
            asr="ヒオヨワメテ",
            detected=True,
        ),
        _case(
            index=6,
            line="vendor-001",
            surface_text="今なら安いよ。",
            expected="イマナラヤスイヨ",
            upstream="イマナラヤスイヨ",
            asr="イマナラヤスイゾ",
            detected=True,
        ),
    ]
    return {
        "format_version": 1,
        "source": {
            "issue_url": "https://github.com/Hitsuki-Ban/gaya-bench/issues/158",
            "human_evidence": {
                "url": "https://github.com/Hitsuki-Ban/gaya-bench/issues/158#issuecomment-1",
                "scope": "first_seven_batch",
                "word_reading_incorrect_count": 7,
                "current_detected_count": 2,
                "human_asr_missed_count": 5,
                "per_case_decision_exported": False,
            },
            "model": "chatterbox-multilingual-v3",
            "candidate_manifest_sha256": "a" * 64,
            "upstream": {
                "repository": "resemble-ai/chatterbox",
                "revision": "65b18437192794391a0308a8f705b1e33e633948",
                "component": "chatterbox.mtl_tts.tokenizers.multilingual",
                "pykakasi_version": "2.3.0",
            },
            "asr": {
                "model": "sbintuitions/kana-whisper",
                "revision": "88ecb3d79c5846cb4fcf76f4107b84c8fa2acd82",
            },
        },
        "cases": cases,
    }


def _case(
    *,
    index: int,
    line: str,
    surface_text: str,
    expected: str,
    upstream: str,
    asr: str,
    detected: bool = False,
    meaning_changed: bool = False,
) -> dict[str, Any]:
    semantic_targets = (
        [
            {
                "surface": "退がれ",
                "expected": "サガレ",
                "upstream": "タイガレ",
                "assessment": "meaning_changed",
            },
        ]
        if meaning_changed
        else []
    )
    return {
        "identity": {
            "scenario": "castle-gate",
            "line": line,
            "variant": "dry",
            "take_index": 1,
            "take_id": f"{index + 1:064x}",
            "generation_input_sha256": f"{index + 101:064x}",
        },
        "surface_text": surface_text,
        "expected_reading": expected,
        "upstream_g2p": {
            "text": upstream,
            "assessment": "meaning_changed" if meaning_changed else "equivalent",
            "semantic_targets": semantic_targets,
        },
        "asr_transcript": asr,
        "current_decision": _mismatch_decision() if detected else _pass_decision(),
        "audio_sha256": f"{index + 201:064x}",
    }


def _pass_decision() -> dict[str, Any]:
    return {
        "status": "pass",
        "review_reason": None,
        "reading_mismatch": False,
    }


def _mismatch_decision() -> dict[str, Any]:
    return {
        "status": "review_required",
        "review_reason": "explicit_reading_mismatch",
        "reading_mismatch": True,
    }
