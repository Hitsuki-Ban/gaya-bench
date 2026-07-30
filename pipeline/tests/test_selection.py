from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from gaya_pipeline.curation import CurationError
from gaya_pipeline.selection import (
    automatic_selection_group,
    canonical_selection_bytes,
    human_selection_group,
    selection_group_to_human_curation,
    validate_selection,
)


def test_selectionは人評と自動gateのauthorityを混同せずcanonical化する() -> None:
    human = _human_group()
    automatic = automatic_selection_group(_manifest_candidate())
    document = _selection([automatic, human_selection_group(human)])

    normalized = validate_selection(document)
    assert [group["authority"]["type"] for group in normalized["groups"]] == [
        "automatic_gate",
        "human",
    ]
    automatic_candidate = normalized["groups"][0]["candidates"][0]
    assert automatic_candidate["gate"] == {
        "mechanical": "pass",
        "content": "review_required",
        "policy_version": "take-gates-v2",
    }
    assert "rubric" not in automatic_candidate
    assert selection_group_to_human_curation(normalized["groups"][1]) == human
    assert canonical_selection_bytes(normalized) == canonical_selection_bytes(document)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda group: group["candidates"][0]["gate"].update(
                mechanical="fail",
            ),
            "mechanical",
        ),
        (
            lambda group: group["candidates"][0]["gate"].update(
                content="fail",
            ),
            "content",
        ),
        (
            lambda group: group["candidates"][0]["gate"].update(
                content="unknown",
            ),
            "content",
        ),
        (
            lambda group: group["candidates"].append(
                {
                    **group["candidates"][0],
                    "take_id": "f" * 64,
                },
            ),
            "candidate が1件",
        ),
        (
            lambda group: group.update(decision={"type": "skipped"}),
            "唯一のcandidate",
        ),
    ],
)
def test_selectionは自動gateのexact境界を拒否する(
    mutation: Any,
    message: str,
) -> None:
    group = automatic_selection_group(_manifest_candidate())
    mutation(group)

    with pytest.raises(CurationError, match=message):
        validate_selection(_selection([group]))


def test_selectionは人評selectedのrubric条件を維持する() -> None:
    group = human_selection_group(_human_group())
    group["candidates"][0]["rubric"]["content_correct"] = False

    with pytest.raises(CurationError, match="content_correct"):
        validate_selection(_selection([group]))


def test_selectionの人評再構成はauthority改変を拒否する() -> None:
    group = human_selection_group(_human_group())
    tampered = deepcopy(group)
    tampered["authority"]["type"] = "automatic_gate"

    with pytest.raises(CurationError, match="人評authority"):
        selection_group_to_human_curation(tampered)


def _selection(groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format_version": 2,
        "protocol": "take-selection-v1",
        "candidate_set_sha256": "a" * 64,
        "groups": groups,
    }


def _human_group() -> dict[str, Any]:
    return {
        "model": "model-human",
        "scenario": "tavern-night",
        "line": "barmaid-002",
        "variant": "dry",
        "candidates": [
            {
                "take_id": "b" * 64,
                "path": "audio/takes/model-human/tavern-night/barmaid-002/dry/take.opus",
                "audio_sha256": "c" * 64,
                "rubric": {
                    "content_correct": True,
                    "intent_match": 4,
                    "character_naturalness": 5,
                    "adoptable": True,
                },
            },
        ],
        "decision": {
            "type": "selected",
            "take_id": "b" * 64,
        },
    }


def _manifest_candidate() -> dict[str, Any]:
    return {
        "model": "model-auto",
        "scenario": "tavern-night",
        "line": "barmaid-001",
        "variant": "dry",
        "take_id": "d" * 64,
        "path": "audio/takes/model-auto/tavern-night/barmaid-001/dry/take.opus",
        "sha256": "e" * 64,
        "gate": {
            "mechanical": "pass",
            "content": "review_required",
            "policy_version": "take-gates-v2",
        },
    }
