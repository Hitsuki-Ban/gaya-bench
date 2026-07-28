from __future__ import annotations

import pytest

from gaya_pipeline.adapters import create_adapter, get_model_profile
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
