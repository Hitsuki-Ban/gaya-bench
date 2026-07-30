from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from gaya_pipeline.adapters.supertonic3 import EXPECTED_VOICE_STYLES
from gaya_pipeline.adapters.voice_assignments import (
    CLONE_REFERENCE_ASSIGNMENTS,
    SUPERTONIC3_VOICE_ASSIGNMENTS,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_CLONE_CANARY_ASSIGNMENTS = {
    ("tavern-night", "drunkard"): "hadou-emotion-11",
    ("tavern-night", "old-regular"): "hadou-emotion-11",
    ("market-day", "fruit-vendor"): "hadou-emotion-11",
    ("market-day", "shopper"): "lux-emotion-76",
    ("market-day", "street-kid"): "tsukuyomi-corpus-94",
}
EXPECTED_SUPERTONIC_CANARY_ASSIGNMENTS = {
    ("tavern-night", "barmaid"): "F2",
    ("tavern-night", "drunkard"): "M1",
    ("tavern-night", "old-regular"): "M5",
    ("market-day", "fruit-vendor"): "M1",
    ("market-day", "shopper"): "F1",
    ("market-day", "street-kid"): "F2",
}


def _characters() -> dict[tuple[str, str], dict[str, Any]]:
    characters: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted((REPOSITORY_ROOT / "scenarios").glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        scenario_id = document["id"]
        for character in document["characters"]:
            key = (scenario_id, character["id"])
            assert key not in characters
            characters[key] = character
    return characters


def test_clone_assignments_exactly_cover_null_reference_characters() -> None:
    characters = _characters()
    null_reference_keys = {
        key
        for key, character in characters.items()
        if character["reference_voice"] is None
    }
    registered_voices = {
        voice["id"]
        for voice in yaml.safe_load(
            (
                REPOSITORY_ROOT / "assets" / "voices" / "metadata.yaml"
            ).read_text(encoding="utf-8"),
        )["voices"]
    }

    assert len(characters) == 58
    assert len(null_reference_keys) == 53
    assert set(CLONE_REFERENCE_ASSIGNMENTS) == null_reference_keys
    assert set(CLONE_REFERENCE_ASSIGNMENTS.values()) <= registered_voices


def test_supertonic_assignments_exactly_cover_current_characters() -> None:
    characters = _characters()

    assert len(characters) == 58
    assert set(SUPERTONIC3_VOICE_ASSIGNMENTS) == set(characters)
    assert set(SUPERTONIC3_VOICE_ASSIGNMENTS.values()) == set(
        EXPECTED_VOICE_STYLES,
    )


def test_supertonic_presets_match_declared_gender() -> None:
    for key, character in _characters().items():
        if character["gender"] == "neutral":
            continue
        expected_prefix = "M" if character["gender"] == "male" else "F"
        assert SUPERTONIC3_VOICE_ASSIGNMENTS[key].startswith(expected_prefix), key


def test_existing_canary_assignments_remain_exact() -> None:
    assert {
        key: CLONE_REFERENCE_ASSIGNMENTS[key]
        for key in EXPECTED_CLONE_CANARY_ASSIGNMENTS
    } == EXPECTED_CLONE_CANARY_ASSIGNMENTS
    assert {
        key: SUPERTONIC3_VOICE_ASSIGNMENTS[key]
        for key in EXPECTED_SUPERTONIC_CANARY_ASSIGNMENTS
    } == EXPECTED_SUPERTONIC_CANARY_ASSIGNMENTS
