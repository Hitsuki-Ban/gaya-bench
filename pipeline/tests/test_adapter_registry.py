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
