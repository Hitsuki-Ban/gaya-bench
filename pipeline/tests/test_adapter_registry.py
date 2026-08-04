from __future__ import annotations

from pathlib import Path

import pytest

from gaya_pipeline.adapters import (
    UnknownAdapterError,
    create_adapter,
    get_model_profile,
)
from gaya_pipeline.adapters.dummy import DummyAdapter


def test_model_profile参照はadapterを初期化しない(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_init(_self: DummyAdapter) -> None:
        raise AssertionError("adapter must not be instantiated")

    monkeypatch.setattr(DummyAdapter, "__init__", fail_init)

    assert get_model_profile("dummy") is DummyAdapter.profile
    with pytest.raises(AssertionError, match="must not be instantiated"):
        create_adapter("dummy")


def test_role_anchor_selectionは対象外modelへ渡せない() -> None:
    with pytest.raises(UnknownAdapterError, match="Qwen3-TTS/Irodori-TTS"):
        create_adapter(
            "dummy",
            role_anchor_selection_path=Path("C:/anchors/selection.json"),
        )


def test_irodori_v4_is_an_independent_registered_model() -> None:
    v3 = get_model_profile("irodori-tts-600m-v3-voicedesign")
    v4 = get_model_profile("irodori-tts-v4-small")

    assert v4.id == "irodori-tts-v4-small"
    assert v4.name == "Irodori-TTS v4-Small"
    assert v4.version != v3.version
    assert v4.capabilities.voice_prompt is True
    assert v4.capabilities.clone is True
    assert v4.capabilities.reading is False
