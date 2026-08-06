from __future__ import annotations

import pytest

from gaya_pipeline import conditioning_variants as cv
from gaya_pipeline.adapters import UnknownAdapterError, get_model_profile
from gaya_pipeline.adapters.conditioning import variant_profile
from gaya_pipeline.adapters.voice_assignments import CLONE_REFERENCE_ASSIGNMENTS
from gaya_pipeline.take_manifest_v4 import TakeManifestError, validate_manifest_v4
from gaya_pipeline.take_identity import derive_seed
from gaya_pipeline.variant_plan import VARIANT_PRIMARY_SEED_BASE


BASE_ENTRY = {
    "id": "irodori-tts-v4-small",
    "name": "Irodori-TTS v4-Small",
    "version": "rev-1",
    "license_note": "MIT",
    "capabilities": {
        "emotion": True,
        "voice_prompt": True,
        "clone": True,
        "nonverbal": True,
        "reading": False,
    },
}


def test_variant_ids_and_columns() -> None:
    assert cv.variant_model_id("voxcpm2", cv.MODE_HUMAN_REFERENCE) == "voxcpm2--ref"
    assert cv.variant_model_id("voxcpm2", cv.MODE_TEXT_ONLY) == "voxcpm2--text"
    assert cv.split_variant_model_id("voxcpm2--text") == (
        "voxcpm2",
        cv.MODE_TEXT_ONLY,
    )
    assert cv.split_variant_model_id("voxcpm2") is None
    assert cv.split_variant_model_id("aivisspeech-kohaku") is None
    assert cv.split_variant_model_id("unknown-model--ref") is None
    assert len(cv.variant_columns()) == 8
    assert len(set(cv.variant_model_ids())) == 8
    assert cv.FINAL_MODEL_COUNT == 13
    assert cv.FINAL_SELECTED_COUNT == 2_093


def test_variant_entry_inherits_base_fields() -> None:
    entry = cv.variant_model_entry(BASE_ENTRY, cv.MODE_HUMAN_REFERENCE)
    assert entry["id"] == "irodori-tts-v4-small--ref"
    assert entry["name"] == "Irodori-TTS v4-Small（見本あり）"
    assert entry["version"] == BASE_ENTRY["version"]
    assert entry["license_note"] == BASE_ENTRY["license_note"]
    assert entry["capabilities"] == BASE_ENTRY["capabilities"]
    assert entry["conditioning"] == {
        "base_model": "irodori-tts-v4-small",
        "mode": "human-reference",
    }
    text = cv.variant_model_entry(BASE_ENTRY, cv.MODE_TEXT_ONLY)
    assert text["name"] == "Irodori-TTS v4-Small（見本なし）"
    assert text["id"] == "irodori-tts-v4-small--text"


def test_variant_entry_rejects_non_variant_base() -> None:
    with pytest.raises(cv.ConditioningVariantError):
        cv.variant_model_entry({**BASE_ENTRY, "id": "supertonic-3"}, cv.MODE_TEXT_ONLY)


def test_validate_conditioning_binds_model_id() -> None:
    assert cv.validate_conditioning(
        {"base_model": "voxcpm2", "mode": "text-only"},
        model_id="voxcpm2--text",
    ) == {"base_model": "voxcpm2", "mode": "text-only"}
    with pytest.raises(cv.ConditioningVariantError):
        cv.validate_conditioning(
            {"base_model": "voxcpm2", "mode": "text-only"},
            model_id="voxcpm2--ref",
        )
    with pytest.raises(cv.ConditioningVariantError):
        cv.validate_conditioning({"base_model": "voxcpm2"}, model_id="voxcpm2--text")


def test_requires_anchor_authority_table() -> None:
    assert cv.requires_anchor_authority("irodori-tts-v4-small") is True
    assert cv.requires_anchor_authority("irodori-tts-v4-small--text") is True
    assert cv.requires_anchor_authority("irodori-tts-v4-small--ref") is False
    assert cv.requires_anchor_authority("qwen3-tts-12hz-1.7b--text") is True
    # VoxCPM2 の text-only は adapter 内蔵 voice design なので anchor 不要。
    assert cv.requires_anchor_authority("voxcpm2--text") is False
    assert cv.requires_anchor_authority("voxcpm2--ref") is False
    assert cv.requires_anchor_authority("supertonic-3") is False


def test_effective_reference_voice_modes() -> None:
    scenario, character = "tavern-night", "drunkard"
    assert (scenario, character) in CLONE_REFERENCE_ASSIGNMENTS
    assert (
        cv.effective_reference_voice(
            mode=None,
            scenario=scenario,
            character=character,
            explicit=None,
        )
        is None
    )
    assert cv.effective_reference_voice(
        mode=cv.MODE_HUMAN_REFERENCE,
        scenario=scenario,
        character=character,
        explicit=None,
    ) == CLONE_REFERENCE_ASSIGNMENTS[(scenario, character)]
    assert (
        cv.effective_reference_voice(
            mode=cv.MODE_HUMAN_REFERENCE,
            scenario="tavern-night",
            character="barmaid",
            explicit="amitaro-countdown",
        )
        == "amitaro-countdown"
    )
    assert (
        cv.effective_reference_voice(
            mode=cv.MODE_TEXT_ONLY,
            scenario="tavern-night",
            character="barmaid",
            explicit="amitaro-countdown",
        )
        is None
    )


def test_effective_reference_voice_fails_without_assignment() -> None:
    with pytest.raises(cv.ConditioningVariantError):
        cv.effective_reference_voice(
            mode=cv.MODE_HUMAN_REFERENCE,
            scenario="nowhere",
            character="nobody",
            explicit=None,
        )


def test_reference_selection_source_labels() -> None:
    assert cv.reference_selection_source(
        mode=cv.MODE_HUMAN_REFERENCE,
        scenario="tavern-night",
        character="drunkard",
        explicit=None,
    ) == "adapter.assignment:tavern-night/drunkard"
    assert (
        cv.reference_selection_source(
            mode=cv.MODE_HUMAN_REFERENCE,
            scenario="tavern-night",
            character="barmaid",
            explicit="amitaro-countdown",
        )
        == "character.reference_voice"
    )
    assert (
        cv.reference_selection_source(
            mode=cv.MODE_TEXT_ONLY,
            scenario="tavern-night",
            character="barmaid",
            explicit="amitaro-countdown",
        )
        is None
    )


@pytest.mark.parametrize(
    ("base_model", "human", "text"),
    [
        (
            "irodori-tts-600m-v3-voicedesign",
            {"reference_source": "voice-asset"},
            {"reference_source": "selected-role-anchor"},
        ),
        (
            "irodori-tts-v4-small",
            {"reference_source": "voice-asset"},
            {"reference_source": "selected-role-anchor"},
        ),
        (
            "qwen3-tts-12hz-1.7b",
            {"reference_control": "voice_asset"},
            {"reference_control": "selected_voice_design_anchor"},
        ),
        (
            "voxcpm2",
            {"reference_kind": "asset"},
            {"reference_kind": "voice_design"},
        ),
    ],
)
def test_realized_conditioning_mode(
    base_model: str,
    human: dict[str, str],
    text: dict[str, str],
) -> None:
    assert (
        cv.realized_conditioning_mode(base_model=base_model, realized=human)
        == cv.MODE_HUMAN_REFERENCE
    )
    assert (
        cv.realized_conditioning_mode(base_model=base_model, realized=text)
        == cv.MODE_TEXT_ONLY
    )
    with pytest.raises(cv.ConditioningVariantError):
        cv.realized_conditioning_mode(base_model=base_model, realized={})


def test_get_model_profile_for_variant_ids() -> None:
    for base_model in cv.VARIANT_BASE_MODELS:
        base = get_model_profile(base_model)
        for mode in cv.CONDITIONING_MODES:
            profile = get_model_profile(cv.variant_model_id(base_model, mode))
            assert profile.id == cv.variant_model_id(base_model, mode)
            assert profile.version == base.version
            assert profile.license_note == base.license_note
            assert profile.capabilities == base.capabilities
            assert profile.conditioning == {
                "base_model": base_model,
                "mode": mode,
            }
            assert profile.name == cv.variant_model_name(base.name, mode)
    with pytest.raises(UnknownAdapterError):
        get_model_profile("supertonic-3--ref")


def test_variant_profile_passthrough_without_mode() -> None:
    base = get_model_profile("voxcpm2")
    assert variant_profile(base, None) is base
    assert base.as_manifest_entry().keys() == {
        "id",
        "name",
        "version",
        "license_note",
        "capabilities",
    }


def _manifest(models: list[dict[str, object]]) -> dict[str, object]:
    return {
        "format_version": 4,
        "generated_at": "2026-08-06T00:00:00Z",
        "candidate_set_sha256": "0" * 64,
        "models": models,
        "candidates": [],
        "curations": [],
        "failures": [],
    }


def test_manifest_v4_accepts_optional_conditioning() -> None:
    entry = cv.variant_model_entry(BASE_ENTRY, cv.MODE_TEXT_ONLY)
    document = validate_manifest_v4(_manifest([dict(entry)]))
    assert document["models"][0]["conditioning"]["mode"] == "text-only"
    # 既存9モデルの entry は field 不在のまま受理される (canonical bytes 不変)。
    validate_manifest_v4(_manifest([dict(BASE_ENTRY)]))


def test_manifest_v4_rejects_inconsistent_conditioning() -> None:
    entry = cv.variant_model_entry(BASE_ENTRY, cv.MODE_TEXT_ONLY)
    broken = {**entry, "conditioning": {"base_model": "voxcpm2", "mode": "text-only"}}
    with pytest.raises(TakeManifestError):
        validate_manifest_v4(_manifest([broken]))
    with pytest.raises(TakeManifestError):
        validate_manifest_v4(_manifest([{**entry, "conditioning": {"mode": "x"}}]))
    with pytest.raises(TakeManifestError):
        validate_manifest_v4(_manifest([{**entry, "unexpected": 1}]))


def test_variant_seed_base_is_isolated_and_deterministic() -> None:
    assert VARIANT_PRIMARY_SEED_BASE == 201
    assert VARIANT_PRIMARY_SEED_BASE not in {104, 194}

    def seed(model: str, index: int, seed_base: int = VARIANT_PRIMARY_SEED_BASE) -> int:
        return derive_seed(
            policy_version="derived-sha256-v1",
            seed_base=seed_base,
            model=model,
            scenario="tavern-night",
            line="drunkard-001",
            variant="dry",
            index=index,
            seed_min=0,
            seed_max=2**32 - 1,
        )

    ref = seed("irodori-tts-v4-small--ref", 1)
    text = seed("irodori-tts-v4-small--text", 1)
    base = seed("irodori-tts-v4-small", 1)
    # 列ごと・take ごと・seed_base ごとに決定論的に分離する。
    assert len({ref, text, base}) == 3
    assert ref == seed("irodori-tts-v4-small--ref", 1)
    assert ref != seed("irodori-tts-v4-small--ref", 2)
    assert ref != seed("irodori-tts-v4-small--ref", 1, seed_base=194)
