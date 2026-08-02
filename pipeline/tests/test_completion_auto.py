from __future__ import annotations

import pytest

from gaya_pipeline.completion_auto import (
    BATCH_OUTPUT_KIND,
    BATCH_POLICY,
    CompletionAutoDecisionError,
    _gender_screening,
    _tokenize_mora,
    _validate_ranking_report,
)


def test_PASQA_moraは発音上のヲと小書き母音だけを明示正規化する() -> None:
    vocab = frozenset({"ク", "ソ", "オ", "ヨ", "ミ", "イ"})

    assert _tokenize_mora("クソヲヨォミィ", vocab) == [
        "ク",
        "ソ",
        "オ",
        "ヨ",
        "オ",
        "ミ",
        "イ",
    ]


def test_PASQA_moraは未知文字を推測しない() -> None:
    with pytest.raises(CompletionAutoDecisionError, match="明示正規化できません"):
        _tokenize_mora("未", frozenset({"ミ"}))


@pytest.mark.parametrize(
    ("gender", "median", "status", "signal"),
    [
        ("female", 210.0, "pass", None),
        ("female", 120.0, "review_required", "gender_f0_below_expected"),
        ("male", 120.0, "pass", None),
        ("male", 230.0, "review_required", "gender_f0_above_expected"),
        ("male", None, "review_required", "gender_f0_unavailable"),
        ("neutral", 230.0, "not_applicable", None),
    ],
)
def test_F0gender判定はreleaseを拒否せずsoft_signalだけを返す(
    gender: str,
    median: float | None,
    status: str,
    signal: str | None,
) -> None:
    attempt = {"content": {"prosody": {"f0": {"median_hz": median}}}}

    result = _gender_screening(
        expected_gender=gender,
        attempt=attempt,
        qc_report_sha256="a" * 64,
    )

    assert result["status"] == status
    assert result["signal"] == signal


def test_ranking_reportはcandidate_audioとrankをexact照合する() -> None:
    identity = ("model", "scene", "line", "dry")
    candidate = {
        "take_id": "a" * 64,
        "sha256": "b" * 64,
        "duration_sec": 1.5,
        "gate": {
            "mechanical": "pass",
            "content": "pass",
            "policy_version": "take-gates-v2",
        },
    }
    report = {
        "format_version": 1,
        "kind": BATCH_OUTPUT_KIND,
        "usage": "same-group-ranking-only_no_release_rejection",
        "ranking_policy": BATCH_POLICY,
        "groups": [
            {
                "group": {
                    "model_id": "model",
                    "scenario_id": "scene",
                    "line_id": "line",
                    "variant": "dry",
                },
                "mora_tokens": ["ト"],
                "ranking_policy": BATCH_POLICY,
                "rankings": [
                    {
                        "rank": 1,
                        "take_id": "a" * 64,
                        "source_audio_sha256": "b" * 64,
                        "gate_content": "pass",
                        "duration_seconds": 1.5,
                        "pasqa": {
                            "score": 4.0,
                            "audio_sha256": "c" * 64,
                            "frame_length": 8,
                        },
                    }
                ],
            }
        ],
        "provenance": {"input_sha256": "d" * 64},
    }

    normalized = _validate_ranking_report(
        report,
        batch_input_sha256="d" * 64,
        candidates_by_group={identity: [candidate]},
    )

    assert normalized["groups"][identity]["rankings"][0]["rank"] == 1
    report["groups"][0]["rankings"][0]["source_audio_sha256"] = "e" * 64
    with pytest.raises(CompletionAutoDecisionError, match="binding"):
        _validate_ranking_report(
            report,
            batch_input_sha256="d" * 64,
            candidates_by_group={identity: [candidate]},
        )
