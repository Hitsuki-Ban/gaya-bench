from __future__ import annotations

from copy import deepcopy

import pytest

from gaya_pipeline.completion_review import (
    CompletionReviewError,
    EXPECTED_REVIEW_COUNT,
    validate_completion_review_bundle,
)


def _document() -> dict[str, object]:
    return {
        "format_version": 1,
        "protocol": "role-quality-review-bundle-v1",
        "plan_sha256": "a" * 64,
        "decision_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
        "quality_signals_sha256": "d" * 64,
        "groups": [
            {
                "model": "model",
                "scenario": "scenario",
                "line": f"line-{index:03d}",
                "variant": "dry",
                "scenario_title": "場面",
                "text": "台詞",
                "delivery": "自然に",
                "role": {
                    "name": "役",
                    "kind": "human",
                    "gender": "male",
                    "age": "adult",
                    "archetype": "guard",
                    "voice": "low",
                    "personality": "calm",
                },
                "take_id": f"{index + 1:064x}",
                "audio_path": f"audio/{index + 1:064x}.opus",
                "audio_sha256": f"{index + 1000:064x}",
                "expected_gender": "male",
                "median_f0_hz": 200.0,
                "signal": "gender_f0_above_expected",
            }
            for index in range(EXPECTED_REVIEW_COUNT)
        ],
    }


def test_quality_review_bundleは145件のsoft_signalだけを受理する() -> None:
    normalized = validate_completion_review_bundle(_document())
    assert len(normalized["groups"]) == EXPECTED_REVIEW_COUNT


def test_quality_review_bundleは件数とF0_signal改ざんを拒否する() -> None:
    missing = _document()
    groups = missing["groups"]
    assert isinstance(groups, list)
    groups.pop()
    with pytest.raises(CompletionReviewError, match="exact 145"):
        validate_completion_review_bundle(missing)

    tampered = deepcopy(_document())
    tampered_groups = tampered["groups"]
    assert isinstance(tampered_groups, list)
    assert isinstance(tampered_groups[0], dict)
    tampered_groups[0]["signal"] = "gender_f0_below_expected"
    with pytest.raises(CompletionReviewError, match="soft F0 policy"):
        validate_completion_review_bundle(tampered)
