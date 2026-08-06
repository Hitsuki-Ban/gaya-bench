from __future__ import annotations

from pathlib import Path

from gaya_pipeline.adapters.base import Adapter, ModelProfile
from gaya_pipeline.adapters.dummy import DummyAdapter
from gaya_pipeline.conditioning_variants import (
    ConditioningVariantError,
    split_variant_model_id,
    variant_model_entry,
)


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
    if model_id == "irodori-tts-v4-small":
        from gaya_pipeline.adapters.irodori_tts_v4 import IrodoriTTSV4Adapter

        return IrodoriTTSV4Adapter
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


_ANCHOR_ADAPTERS = frozenset(
    {
        "qwen3-tts-12hz-1.7b",
        "irodori-tts-600m-v3-voicedesign",
        "irodori-tts-v4-small",
    },
)


def create_adapter(
    model_id: str,
    *,
    role_anchor_selection_path: Path | None = None,
    role_anchor_plan_sha256: str | None = None,
) -> Adapter:
    """model id から adapter を作る。

    `<base>--ref` / `<base>--text` の条件バリアント id は base adapter を
    強制modeで構築し、`profile` だけ variant entry に差し替える (#201)。
    """

    split = split_variant_model_id(model_id)
    base_id = model_id if split is None else split[0]
    conditioning_mode = None if split is None else split[1]
    adapter_class = _adapter_class(base_id)
    if base_id in _ANCHOR_ADAPTERS:
        return adapter_class(
            role_anchor_selection_path=role_anchor_selection_path,
            role_anchor_plan_sha256=role_anchor_plan_sha256,
            conditioning_mode=conditioning_mode,
        )
    if (
        role_anchor_selection_path is not None
        or role_anchor_plan_sha256 is not None
    ):
        raise UnknownAdapterError(
            "role anchor selection/plan SHAはQwen3-TTS/Irodori-TTSだけに指定できます: "
            f"{model_id}",
        )
    if conditioning_mode is None:
        return adapter_class()
    return adapter_class(conditioning_mode=conditioning_mode)


def get_model_profile(model_id: str) -> ModelProfile:
    split = split_variant_model_id(model_id)
    if split is None:
        return _adapter_class(model_id).profile
    base_id, mode = split
    base_profile = _adapter_class(base_id).profile
    try:
        entry = variant_model_entry(base_profile.as_manifest_entry(), mode)
    except ConditioningVariantError as error:
        raise UnknownAdapterError(str(error)) from error
    return ModelProfile(
        id=entry["id"],
        name=entry["name"],
        version=base_profile.version,
        license_note=base_profile.license_note,
        capabilities=base_profile.capabilities,
        conditioning=entry["conditioning"],
    )
