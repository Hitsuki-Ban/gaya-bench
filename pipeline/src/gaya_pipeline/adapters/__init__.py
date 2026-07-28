from __future__ import annotations

from gaya_pipeline.adapters.base import Adapter
from gaya_pipeline.adapters.dummy import DummyAdapter


class UnknownAdapterError(ValueError):
    pass


def create_adapter(model_id: str) -> Adapter:
    if model_id == "dummy":
        return DummyAdapter()
    if model_id == "qwen3-tts-12hz-1.7b":
        from gaya_pipeline.adapters.qwen3_tts import Qwen3TTSAdapter

        return Qwen3TTSAdapter()
    if model_id == "irodori-tts-600m-v3-voicedesign":
        from gaya_pipeline.adapters.irodori_tts import IrodoriTTSAdapter

        return IrodoriTTSAdapter()
    if model_id == "aivisspeech-kohaku":
        from gaya_pipeline.adapters.aivisspeech import AivisSpeechAdapter

        return AivisSpeechAdapter()
    if model_id == "gpt-sovits-v2-pro-plus":
        from gaya_pipeline.adapters.gpt_sovits import GPTSoVITSAdapter

        return GPTSoVITSAdapter()
    if model_id == "voxcpm2":
        from gaya_pipeline.adapters.voxcpm2 import VoxCPM2Adapter

        return VoxCPM2Adapter()
    if model_id == "chatterbox-multilingual-v3":
        from gaya_pipeline.adapters.chatterbox import ChatterboxAdapter

        return ChatterboxAdapter()
    raise UnknownAdapterError(f"未知の model id です: {model_id}")
