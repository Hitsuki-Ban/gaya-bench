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


def _decision(
    candidate_count: int = 3,
    *,
    minimum_eligible_candidates: int = 3,
) -> dict[str, Any]:
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
            "rank": index,
            "pasqa_score": 5.0 - index,
            "duration_sec": 1.0 + index / 10,
        }
        for index in range(1, candidate_count + 1)
    ]
    return {
        "format_version": 1,
        "protocol": "role-baseline-decision-v1",
        "plan_sha256": "b" * 64,
        "anchor_selection_sha256": "c" * 64,
        "candidate_set_sha256": "a" * 64,
        "ranking_report_sha256": "f" * 64,
        "groups": [
            {
                "model": "model",
                "scenario": "scene",
                "line": "line",
                "variant": "dry",
                "role_epoch_sha256": "d" * 64,
                "group_sha256": "e" * 64,
                "authority": {
                    "type": "auto-selected",
                    "policy_version": "phase-b-auto-selection-v1",
                    "minimum_eligible_candidates": minimum_eligible_candidates,
                    "gate_policy_version": "take-gates-v2",
                },
                "candidates": candidates,
                "decision": {
                    "type": "selected",
                    "take_id": candidates[0]["take_id"],
                },
                "screening": {
                    "protocol": "role-gender-f0-soft-v1",
                    "expected_gender": "female",
                    "median_f0_hz": 210.0,
                    "status": "pass",
                    "signal": None,
                    "qc_report_sha256": "9" * 64,
                },
            },
        ],
    }


def test_published_base_selectionを固定SHAへexact再構築する() -> None:
    manifest = json.loads(
        (
            ROOT
            / "docs"
            / "research"
            / "full-baseline-completion"
            / "base-manifest-v4.json"
        ).read_bytes(),
    )
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


def test_auto_selectedはrankingとF0soft_signalを正直に保持する() -> None:
    decision = _decision()

    normalized = validate_completion_decision(decision)

    selected = next(
        candidate
        for candidate in normalized["groups"][0]["candidates"]
        if candidate["rank"] == 1
    )
    assert selected["rank"] == 1
    assert selected["pasqa_score"] == 4.0
    assert normalized["groups"][0]["screening"]["status"] == "pass"
    assert canonical_completion_decision_bytes(decision)


def test_auto_selectedはmechanical_passが3件未満なら拒否する() -> None:
    with pytest.raises(CurationError, match="3件以上"):
        validate_completion_decision(_decision(candidate_count=2))


def test_auto_selectedはgroupが明示した最低1件を受理する() -> None:
    normalized = validate_completion_decision(
        _decision(candidate_count=1, minimum_eligible_candidates=1),
    )

    authority = normalized["groups"][0]["authority"]
    assert authority["minimum_eligible_candidates"] == 1


@pytest.mark.parametrize("minimum", [0, -1, True])
def test_auto_selectedは正でない最低候補数を拒否する(minimum: Any) -> None:
    with pytest.raises(CurationError, match="auto-selected 契約"):
        validate_completion_decision(
            _decision(minimum_eligible_candidates=minimum),
        )


def test_auto_selectedはrankを省略できない() -> None:
    decision = _decision()
    del decision["groups"][0]["candidates"][0]["rank"]

    with pytest.raises(CurationError, match="exact contract"):
        validate_completion_decision(decision)


def test_auto_selectedはmechanical_rejectを候補にできない() -> None:
    decision = _decision()
    decision["groups"][0]["candidates"][1]["gate"]["mechanical"] = "reject"

    with pytest.raises(CurationError, match="mechanical は pass"):
        validate_completion_decision(decision)


def test_auto_selectedはrank1以外を選べない() -> None:
    decision = _decision()
    decision["groups"][0]["decision"]["take_id"] = decision["groups"][0][
        "candidates"
    ][1]["take_id"]

    with pytest.raises(CurationError, match="rank 1"):
        validate_completion_decision(decision)


def test_F0screeningはpolicyと矛盾する結果を拒否する() -> None:
    decision = _decision()
    decision["groups"][0]["screening"].update(
        {
            "expected_gender": "female",
            "median_f0_hz": 120.0,
            "status": "pass",
        }
    )

    with pytest.raises(CurationError, match="F0判定結果"):
        validate_completion_decision(decision)


def test_decisionはplan_anchor_role_epochの旧値を再生できない() -> None:
    decision = _decision()
    del decision["groups"][0]["role_epoch_sha256"]

    with pytest.raises(CurationError, match="exact contract"):
        validate_completion_decision(decision)

    decision = _decision()
    del decision["groups"][0]["group_sha256"]
    with pytest.raises(CurationError, match="exact contract"):
        validate_completion_decision(decision)

    decision = _decision()
    del decision["anchor_selection_sha256"]
    with pytest.raises(CurationError, match="exact contract"):
        validate_completion_decision(decision)
