from __future__ import annotations

from gaya_pipeline.adapters.base import Adapter
from gaya_pipeline.adapters.dummy import DummyAdapter


class UnknownAdapterError(ValueError):
    pass


def create_adapter(model_id: str) -> Adapter:
    if model_id == "dummy":
        return DummyAdapter()
    raise UnknownAdapterError(f"未知の model id です: {model_id}")
