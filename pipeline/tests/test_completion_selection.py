from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline.completion_selection import (
    BASE_SELECTION_SHA256,
    canonical_completion_decision_bytes,
    reconstruct_base_selection,
    validate_completion_decision,
)
from gaya_pipeline.curation import CurationError
from gaya_pipeline.selection import canonical_selection_bytes


ROOT = Path(__file__).resolve().parents[2]


def _rubric(**overrides: Any) -> dict[str, Any]:
    result = {
        "content_correct": False,
        "prompt_leakage": True,
        "reading_correct": False,
        "accent_naturalness": 1,
        "role_match": 2,
        "delivery_match": 1,
        "audio_quality": 2,
        "adoptable": False,
        "notes": "全候補に問題があるため、その中で最良のものを選択",
    }
    result.update(overrides)
    return result


def _decision(candidate_count: int = 3) -> dict[str, Any]:
    candidates = [
        {
            "take_id": hashlib.sha256(f"take:{index}".encode()).hexdigest(),
            "path": f"audio/takes/model/scene/line/dry/take-{index:04d}-"
            f"{hashlib.sha256(f'audio:{index}'.encode()).hexdigest()}.opus",
            "audio_sha256": hashlib.sha256(f"audio:{index}".encode()).hexdigest(),
            "gate": {
                "mechanical": "pass",
                "content": "review_required",
                "policy_version": "take-gates-v2",
            },
            "rubric": _rubric(),
        }
        for index in range(1, candidate_count + 1)
    ]
    return {
        "format_version": 1,
        "protocol": "baseline-completion-decision-v1",
        "candidate_set_sha256": "a" * 64,
        "groups": [
            {
                "model": "model",
                "scenario": "scene",
                "line": "line",
                "variant": "dry",
                "authority": {
                    "type": "best_available",
                    "policy_version": "missing-slot-best-of-n-v1",
                    "reviewer": "owner",
                    "minimum_eligible_candidates": 3,
                },
                "candidates": candidates,
                "decision": {
                    "type": "selected",
                    "take_id": candidates[0]["take_id"],
                },
            },
        ],
    }


def test_published_base_selectionを固定SHAへexact再構築する() -> None:
    manifest = json.loads((ROOT / "data" / "manifest.json").read_bytes())
    qwen_path = next(
        (ROOT / "docs" / "research" / "baseline-v4" / "release" / "curation").glob(
            "*.json",
        ),
    )
    qwen = json.loads(qwen_path.read_bytes())

    selection = reconstruct_base_selection(
        base_manifest=manifest,
        qwen_curation=qwen,
    )

    assert len(selection["groups"]) == 1282
    assert (
        hashlib.sha256(canonical_selection_bytes(selection)).hexdigest()
        == BASE_SELECTION_SHA256
    )


def test_best_availableは低品質の真実なrubricでも選択できる() -> None:
    decision = _decision()

    normalized = validate_completion_decision(decision)

    selected = normalized["groups"][0]["candidates"][0]
    assert selected["rubric"]["adoptable"] is False
    assert selected["rubric"]["content_correct"] is False
    assert selected["rubric"]["prompt_leakage"] is True
    assert canonical_completion_decision_bytes(decision)


def test_best_availableはmechanical_passが3件未満なら拒否する() -> None:
    with pytest.raises(CurationError, match="3件以上"):
        validate_completion_decision(_decision(candidate_count=2))


def test_best_availableはrubricを省略できない() -> None:
    decision = _decision()
    del decision["groups"][0]["candidates"][0]["rubric"]["reading_correct"]

    with pytest.raises(CurationError, match="exact contract"):
        validate_completion_decision(decision)


def test_best_availableはmechanical_rejectを候補にできない() -> None:
    decision = _decision()
    decision["groups"][0]["candidates"][1]["gate"]["mechanical"] = "reject"

    with pytest.raises(CurationError, match="mechanical は pass"):
        validate_completion_decision(decision)
