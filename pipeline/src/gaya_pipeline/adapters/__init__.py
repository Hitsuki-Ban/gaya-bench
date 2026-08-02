from __future__ import annotations

from pathlib import Path

from gaya_pipeline.adapters.base import Adapter, ModelProfile
from gaya_pipeline.adapters.dummy import DummyAdapter


class UnknownAdapterError(ValueError):
    pass


def _adapter_class(model_id: str) -> type[Adapter]:
    if model_id == "dummy":
        return DummyAdapter
    if model_id == "qwen3-tts-12hz-1.7b":
        from gaya_pipeline.adapters.qwen3_tts import Qwen3TTSAdapter

        return Qwen3TTSAdapter
    if model_id == "irodori-tts-600m-v3-voicedesign":
        from gaya_pipeline.adapters.irodori_tts import IrodoriTTSAdapter

        return IrodoriTTSAdapter
    if model_id == "aivisspeech-kohaku":
        from gaya_pipeline.adapters.aivisspeech import AivisSpeechAdapter

        return AivisSpeechAdapter
    if model_id == "gpt-sovits-v2-pro-plus":
        from gaya_pipeline.adapters.gpt_sovits import GPTSoVITSAdapter

        return GPTSoVITSAdapter
    if model_id == "voxcpm2":
        from gaya_pipeline.adapters.voxcpm2 import VoxCPM2Adapter

        return VoxCPM2Adapter
    if model_id == "chatterbox-multilingual-v3":
        from gaya_pipeline.adapters.chatterbox import ChatterboxAdapter

        return ChatterboxAdapter
    if model_id == "cosyvoice3-0.5b-2512":
        from gaya_pipeline.adapters.cosyvoice3 import CosyVoice3Adapter

        return CosyVoice3Adapter
    if model_id == "supertonic-3":
        from gaya_pipeline.adapters.supertonic3 import Supertonic3Adapter

        return Supertonic3Adapter
    raise UnknownAdapterError(f"未知の model id です: {model_id}")


def create_adapter(
    model_id: str,
    *,
    role_anchor_selection_path: Path | None = None,
    role_anchor_plan_sha256: str | None = None,
) -> Adapter:
    adapter_class = _adapter_class(model_id)
    if model_id in {
        "qwen3-tts-12hz-1.7b",
        "irodori-tts-600m-v3-voicedesign",
    }:
        return adapter_class(
            role_anchor_selection_path=role_anchor_selection_path,
            role_anchor_plan_sha256=role_anchor_plan_sha256,
        )
    if (
        role_anchor_selection_path is not None
        or role_anchor_plan_sha256 is not None
    ):
        raise UnknownAdapterError(
            "role anchor selection/plan SHAはQwen3-TTS/Irodori-TTSだけに指定できます: "
            f"{model_id}",
        )
    return adapter_class()


def get_model_profile(model_id: str) -> ModelProfile:
    return _adapter_class(model_id).profile
