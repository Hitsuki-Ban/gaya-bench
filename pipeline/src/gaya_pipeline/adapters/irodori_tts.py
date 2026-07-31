from __future__ import annotations

import hashlib
import importlib
import re
import sys
import wave
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol, TypeVar

import yaml

from gaya_pipeline.adapters.base import (
    Capabilities,
    LineJob,
    ModelProfile,
    TakeContext,
    TakeRecipe,
    require_take_context,
)
from gaya_pipeline.completion_anchor import (
    CompletionAnchorError,
    resolve_selected_anchor,
)
from gaya_pipeline.completion_plan import (
    CompletionPlanError,
    RoleSnapshot,
    build_role_snapshot,
)
from gaya_pipeline.japanese_reading import resolve_japanese_reading
from gaya_pipeline.voice_assets import validate_voice_metadata

MODEL_ID = "irodori-tts-600m-v3-voicedesign"
IRODORI_TTS_VERSION = "0.1.0"
UPSTREAM_REVISION = "eaf74d6a19138f743acb5b71a445fd25a57db987"
CHECKPOINT_ID = "Aratako/Irodori-TTS-600M-v3-VoiceDesign"
CHECKPOINT_REVISION = "e863a3a93e652e09afeff3e84823a206a0a60314"
CODEC_ID = "Aratako/Semantic-DACVAE-Japanese-32dim"
CODEC_REVISION = "47376ee24834d7a05a48ebabfe3cde29b3c5e214"
DACVAE_REVISION = "414c20785fc3a28373073ea8ef7a1316eeeaca6e"
TOKENIZER_ID = "llm-jp/llm-jp-3-150m"
TOKENIZER_REVISION = "b112feef602fff752e4dac4c30af6a2c2fa41c7a"
SILENTCIPHER_MODEL_ID = "sony/silentcipher"
SILENTCIPHER_MODEL_REVISION = "a1c4d021905e0dc5b24be5f68db5fc4dba410ee1"
SILENTCIPHER_VERSION = "1.0.5"
PYOPENJTALK_VERSION = "0.4.1.post8"
TORCH_VERSION = "2.10.0"
TORCHAUDIO_VERSION = "2.10.0"
TORCHCODEC_VERSION = "0.10.0"
CUDA_WHEEL_VERSION = "12.8"
DEVICE = "cuda:0"
MODEL_PRECISION = "bf16"
CODEC_PRECISION = "fp32"
SEED = 0
SAMPLE_RATE_HZ = 48_000
NUM_STEPS = 40
CFG_SCALE_TEXT = 3.0
CFG_SCALE_CAPTION = 3.0
CFG_SCALE_SPEAKER = 5.0
CFG_GUIDANCE_MODE = "independent"
CFG_MIN_T = 0.5
CFG_MAX_T = 1.0
DURATION_SCALE = 1.0
MIN_SECONDS = 0.5
MAX_SECONDS = 30.0
MAX_REF_SECONDS = 30.0
REF_NORMALIZE_DB = -16.0
WATERMARK_PAYLOAD = "IRDTS"
ROLE_ANCHOR_TEXT = "そらにはくもがうかび、とおくでかぜのおとがきこえます。"
REFERENCE_CONTROL = "character-stable-reference-audio-v1"

PROFILE_VERSION = (
    f"Irodori-TTS {IRODORI_TTS_VERSION}@{UPSTREAM_REVISION}; "
    f"{CHECKPOINT_ID}@{CHECKPOINT_REVISION}; "
    f"{CODEC_ID}@{CODEC_REVISION}; "
    f"DACVAE@{DACVAE_REVISION}; "
    f"{TOKENIZER_ID}@{TOKENIZER_REVISION}; "
    f"{SILENTCIPHER_MODEL_ID}@{SILENTCIPHER_MODEL_REVISION}"
)

EMOTION_EMOJI: dict[str, str | None] = {
    "neutral": None,
    "cheerful": "😊",
    "angry": "😠",
    "sad": "😭",
    "fearful": "😰",
    "surprised": "😲",
    "tired": "😪",
    "drunk": "🥴",
    "whisper": "👂",
    "shout": "😱",
    "laughing": "🤭",
    "pain": "😖",
}

_EMOTION_LABEL: dict[str, str] = {
    "neutral": "中立",
    "cheerful": "明るく楽しげ",
    "angry": "怒り",
    "sad": "悲しみ",
    "fearful": "恐れと緊張",
    "surprised": "驚き",
    "tired": "疲労と気だるさ",
    "drunk": "酔い",
    "whisper": "囁き",
    "shout": "叫び",
    "laughing": "笑い",
    "pain": "苦痛",
}

_KIND_LABEL: dict[str, str] = {
    "human": "人間",
    "machine": "機械",
    "creature": "生物",
    "spirit": "霊的存在",
}

_GENDER_LABEL: dict[str, str] = {
    "female": "女性",
    "male": "男性",
    "neutral": "中性的",
}

_AGE_LABEL: dict[str, str] = {
    "child": "子ども",
    "teen": "10代",
    "young_adult": "若い成人",
    "adult": "成人",
    "middle_aged": "中年",
    "elderly": "高齢者",
}

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MIB = 1024 * 1024
_T = TypeVar("_T")


class IrodoriTTSAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class _RoleReference:
    source: str
    voice_id: str | None
    wav_path: Path
    sha256: str
    caption: str | None
    text: str | None
    phase_peak_vram_mib: Mapping[str, Mapping[str, float]]
    selected_anchor_receipt: Mapping[str, Any] | None


@dataclass(frozen=True)
class _PreparedInput:
    text: str
    reading_source: str
    emotion: str
    emoji: str | None
    caption: str
    role_identity: Mapping[str, str]
    reference_source: str
    reference_voice: str | None
    reference_wav: Path
    reference_sha256: str
    reference_caption: str | None
    reference_text: str | None
    reference_phase_peak_vram_mib: Mapping[str, Mapping[str, float]]
    selected_anchor_receipt: Mapping[str, Any] | None

    def as_generation_input(self) -> dict[str, Any]:
        result = {
            "text": self.text,
            "reading_source": self.reading_source,
            "emotion": self.emotion,
            "emotion_emoji": self.emoji,
            "caption": self.caption,
            "role_identity": dict(self.role_identity),
            "reference_control": REFERENCE_CONTROL,
            "reference_source": self.reference_source,
            "reference_voice": self.reference_voice,
            "reference_sha256": self.reference_sha256,
            "reference_caption": self.reference_caption,
            "reference_text": self.reference_text,
        }
        if self.selected_anchor_receipt is not None:
            result["selected_anchor"] = dict(self.selected_anchor_receipt)
        return result


class _Runtime(Protocol):
    def prepare(self) -> Mapping[str, float]: ...

    def synthesize(
        self,
        *,
        text: str,
        caption: str,
        reference_wav: Path | None,
        output_wav: Path,
        seed: int,
    ) -> Mapping[str, Any]: ...

    def is_out_of_memory(self, error: BaseException) -> bool: ...


class _NativeRuntime:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise IrodoriTTSAdapterError(
                "Irodori-TTS は Windows native CUDA:0 だけをサポートします。",
            )

        _require_distribution("irodori-tts", IRODORI_TTS_VERSION)
        _require_distribution("torch", TORCH_VERSION, allow_local_suffix=True)
        _require_distribution("torchaudio", TORCHAUDIO_VERSION, allow_local_suffix=True)
        _require_distribution("torchcodec", TORCHCODEC_VERSION)
        _require_distribution("silentcipher", SILENTCIPHER_VERSION)

        try:
            self.torchcodec = importlib.import_module("torchcodec")
        except (ImportError, ModuleNotFoundError, OSError, RuntimeError) as error:
            raise IrodoriTTSAdapterError(
                "TorchCodec を読み込めません。Windows では対応する "
                "FFmpeg full-shared build を PATH の先頭に置いてください: "
                f"{error}",
            ) from error

        try:
            self.torch = importlib.import_module("torch")
            self.soundfile = importlib.import_module("soundfile")
            self.huggingface_hub = importlib.import_module("huggingface_hub")
            self.silentcipher = importlib.import_module("silentcipher")
            self.inference_runtime = importlib.import_module(
                "irodori_tts.inference_runtime",
            )
            self.tokenizer_module = importlib.import_module("irodori_tts.tokenizer")
        except (ImportError, ModuleNotFoundError) as error:
            raise IrodoriTTSAdapterError(
                f"Irodori-TTS の必須依存を import できません: {error}",
            ) from error

        if not self.torch.cuda.is_available():
            raise IrodoriTTSAdapterError("CUDA:0 を利用できません。")
        if self.torch.cuda.device_count() < 1:
            raise IrodoriTTSAdapterError("CUDA:0 が存在しません。")
        if not self.torch.cuda.is_bf16_supported():
            raise IrodoriTTSAdapterError(
                "CUDA:0 は native BF16 をサポートしていません。",
            )
        actual_cuda = str(self.torch.version.cuda)
        if actual_cuda != CUDA_WHEEL_VERSION:
            raise IrodoriTTSAdapterError(
                "PyTorch CUDA wheel が一致しません: "
                f"expected={CUDA_WHEEL_VERSION}, actual={actual_cuda}",
            )

        required_runtime_api = ("InferenceRuntime", "RuntimeKey", "SamplingRequest")
        missing = [
            name
            for name in required_runtime_api
            if not hasattr(self.inference_runtime, name)
        ]
        if missing:
            raise IrodoriTTSAdapterError(
                "固定 Irodori revision の runtime API がありません: "
                + ", ".join(missing),
            )

        self._runtime: Any | None = None

    def prepare(self) -> Mapping[str, float]:
        if self._runtime is not None:
            raise IrodoriTTSAdapterError("runtime はすでに prepare 済みです。")

        model_path = Path(
            self.huggingface_hub.hf_hub_download(
                repo_id=CHECKPOINT_ID,
                filename="model.safetensors",
                revision=CHECKPOINT_REVISION,
            ),
        )
        codec_path = Path(
            self.huggingface_hub.hf_hub_download(
                repo_id=CODEC_ID,
                filename="weights.pth",
                revision=CODEC_REVISION,
            ),
        )
        tokenizer_path = Path(
            self.huggingface_hub.snapshot_download(
                repo_id=TOKENIZER_ID,
                revision=TOKENIZER_REVISION,
                allow_patterns=(
                    "special_tokens_map.json",
                    "tokenizer.json",
                    "tokenizer_config.json",
                ),
            ),
        )
        silentcipher_path = Path(
            self.huggingface_hub.snapshot_download(
                repo_id=SILENTCIPHER_MODEL_ID,
                revision=SILENTCIPHER_MODEL_REVISION,
                allow_patterns=("44_1_khz/73999_iteration/*",),
            ),
        )
        silentcipher_checkpoint = silentcipher_path / "44_1_khz" / "73999_iteration"
        silentcipher_config = silentcipher_checkpoint / "hparams.yaml"
        for path, label in (
            (model_path, "model.safetensors"),
            (codec_path, "weights.pth"),
            (tokenizer_path, "tokenizer snapshot"),
            (silentcipher_checkpoint, "SilentCipher checkpoint"),
            (silentcipher_config, "SilentCipher config"),
        ):
            if not path.exists():
                raise IrodoriTTSAdapterError(
                    f"固定 revision の {label} がありません: {path}",
                )

        tokenizer_class = self.tokenizer_module.PretrainedTextTokenizer
        original_descriptor = tokenizer_class.__dict__["from_pretrained"]
        original_silentcipher_get_model = self.silentcipher.get_model

        @classmethod
        def pinned_from_pretrained(
            cls: type[Any],
            repo_id: str,
            add_bos: bool = True,
            local_files_only: bool = False,
        ) -> Any:
            del local_files_only
            if repo_id != TOKENIZER_ID:
                raise IrodoriTTSAdapterError(
                    "checkpoint の tokenizer id が一致しません: "
                    f"expected={TOKENIZER_ID}, actual={repo_id}",
                )
            return original_descriptor.__func__(
                cls,
                str(tokenizer_path),
                add_bos=add_bos,
                local_files_only=True,
            )

        self.reset_peak_memory_stats()
        setattr(tokenizer_class, "from_pretrained", pinned_from_pretrained)
        self.silentcipher.get_model = _pinned_silentcipher_loader(
            original_silentcipher_get_model,
            checkpoint_path=silentcipher_checkpoint,
            config_path=silentcipher_config,
        )
        try:
            runtime_key = self.inference_runtime.RuntimeKey(
                checkpoint=str(model_path),
                model_device=DEVICE,
                codec_repo=str(codec_path),
                model_precision=MODEL_PRECISION,
                codec_device=DEVICE,
                codec_precision=CODEC_PRECISION,
                codec_deterministic_encode=True,
                codec_deterministic_decode=True,
                compile_model=False,
                compile_dynamic=False,
            )
            runtime = self.inference_runtime.InferenceRuntime.from_key(runtime_key)
        finally:
            setattr(tokenizer_class, "from_pretrained", original_descriptor)
            self.silentcipher.get_model = original_silentcipher_get_model

        config = runtime.model_cfg
        if not config.use_caption_condition:
            raise IrodoriTTSAdapterError(
                "checkpoint は caption conditioning をサポートしていません。",
            )
        if not config.use_speaker_condition_resolved:
            raise IrodoriTTSAdapterError(
                "checkpoint は speaker conditioning をサポートしていません。",
            )
        if not config.use_duration_predictor:
            raise IrodoriTTSAdapterError(
                "checkpoint は duration predictor をサポートしていません。",
            )
        if not runtime.watermarker.ready:
            raise IrodoriTTSAdapterError(
                "SilentCipher watermark model を読み込めません。",
            )

        self._runtime = runtime
        return self.peak_memory_mib()

    def synthesize(
        self,
        *,
        text: str,
        caption: str,
        reference_wav: Path | None,
        output_wav: Path,
        seed: int,
    ) -> Mapping[str, Any]:
        runtime = self._runtime
        if runtime is None:
            raise IrodoriTTSAdapterError("runtime が prepare されていません。")
        if not runtime.watermarker.ready:
            raise IrodoriTTSAdapterError(
                "SilentCipher watermark model が利用できません。",
            )

        self.reset_peak_memory_stats()
        request = self.inference_runtime.SamplingRequest(
            text=text,
            caption=caption,
            ref_wav=None if reference_wav is None else str(reference_wav),
            no_ref=reference_wav is None,
            ref_normalize_db=REF_NORMALIZE_DB,
            ref_ensure_max=True,
            num_candidates=1,
            decode_mode="sequential",
            seconds=None,
            duration_scale=DURATION_SCALE,
            min_seconds=MIN_SECONDS,
            max_seconds=MAX_SECONDS,
            max_ref_seconds=MAX_REF_SECONDS,
            num_steps=NUM_STEPS,
            cfg_scale_text=CFG_SCALE_TEXT,
            cfg_scale_caption=CFG_SCALE_CAPTION,
            cfg_scale_speaker=CFG_SCALE_SPEAKER,
            cfg_guidance_mode=CFG_GUIDANCE_MODE,
            cfg_min_t=CFG_MIN_T,
            cfg_max_t=CFG_MAX_T,
            context_kv_cache=True,
            speaker_uncond_mode="mask",
            seed=seed,
            t_schedule_mode="linear",
            sway_coeff=-1.0,
            trim_tail=True,
            tail_window_size=20,
            tail_std_threshold=0.05,
            tail_mean_threshold=0.1,
        )
        result = runtime.synthesize(
            request,
            log_fn=lambda message: print(
                f"[irodori] {message}",
                file=sys.stderr,
                flush=True,
            ),
        )
        generation_peak = self.peak_memory_mib()

        if result.used_seed != seed:
            raise IrodoriTTSAdapterError(
                f"seed が一致しません: expected={seed}, actual={result.used_seed}",
            )
        if result.sample_rate != SAMPLE_RATE_HZ:
            raise IrodoriTTSAdapterError(
                "sample rate が一致しません: "
                f"expected={SAMPLE_RATE_HZ}, actual={result.sample_rate}",
            )
        audio = result.audio.detach().float().cpu().squeeze()
        if audio.ndim != 1 or audio.numel() == 0:
            raise IrodoriTTSAdapterError(
                f"runtime 出力の waveform shape が不正です: {tuple(audio.shape)}",
            )

        output_wav.parent.mkdir(parents=True, exist_ok=True)
        self.soundfile.write(
            str(output_wav),
            audio.numpy(),
            samplerate=result.sample_rate,
            format="WAV",
            subtype="PCM_16",
        )
        if not output_wav.is_file():
            raise IrodoriTTSAdapterError(
                f"PCM WAV が書き込まれませんでした: {output_wav}",
            )

        timings = {
            str(name): round(float(seconds), 6)
            for name, seconds in result.stage_timings
        }
        if "silentcipher_watermark" not in timings:
            raise IrodoriTTSAdapterError(
                "SilentCipher watermark stage が実行されませんでした。",
            )
        return {
            "phase_peak_vram_mib": {
                "generation": generation_peak,
            },
            "seed": result.used_seed,
            "sample_rate_hz": result.sample_rate,
            "silentcipher_watermark_stage_executed": True,
            "stage_timings_sec": timings,
            "total_to_decode_sec": round(float(result.total_to_decode), 6),
        }

    def reset_peak_memory_stats(self) -> None:
        self.torch.cuda.synchronize(0)
        self.torch.cuda.reset_peak_memory_stats(0)

    def peak_memory_mib(self) -> dict[str, float]:
        self.torch.cuda.synchronize(0)
        return {
            "allocated_mib": round(
                self.torch.cuda.max_memory_allocated(0) / _MIB,
                3,
            ),
            "reserved_mib": round(
                self.torch.cuda.max_memory_reserved(0) / _MIB,
                3,
            ),
        }

    def is_out_of_memory(self, error: BaseException) -> bool:
        out_of_memory_error = getattr(self.torch, "OutOfMemoryError", None)
        cuda_out_of_memory = getattr(self.torch.cuda, "OutOfMemoryError", None)
        classes = tuple(
            error_class
            for error_class in (out_of_memory_error, cuda_out_of_memory)
            if isinstance(error_class, type)
        )
        return bool(classes and isinstance(error, classes)) or (
            "out of memory" in str(error).lower()
        )


class IrodoriTTSAdapter:
    profile = ModelProfile(
        id=MODEL_ID,
        name="Irodori-TTS 600M-v3-VoiceDesign",
        version=PROFILE_VERSION,
        license_note=(
            "コード・重み・codec は MIT。SilentCipher payload IRDTS の埋め込み"
            "処理を必須実行するが、後処理後の検出可能性は保証しない。"
            "学習データ詳細と生成物の独立ライセンスは非開示。"
            "無断の声真似・誤認を招く deepfake を禁止。"
        ),
        capabilities=Capabilities(
            emotion=True,
            voice_prompt=True,
            clone=True,
            nonverbal=True,
            reading=True,
        ),
    )

    def take_recipe(self) -> TakeRecipe:
        return TakeRecipe(
            version="seed-only-v1",
            seed_policy="derived-sha256-v1",
            single_take_seed=SEED,
            seed_range=(0, 2**32 - 1),
            sampling=(
                ("cfg_guidance_mode", CFG_GUIDANCE_MODE),
                ("cfg_max_t", CFG_MAX_T),
                ("cfg_min_t", CFG_MIN_T),
                ("cfg_scale_caption", CFG_SCALE_CAPTION),
                ("cfg_scale_speaker", CFG_SCALE_SPEAKER),
                ("cfg_scale_text", CFG_SCALE_TEXT),
                ("num_steps", NUM_STEPS),
            ),
            supports_multiple=True,
        )

    def __init__(
        self,
        *,
        runtime: _Runtime | None = None,
        reading_converter: Callable[[str], str] | None = None,
        role_anchor_selection_path: Path | None = None,
        role_anchor_plan_sha256: str | None = None,
    ) -> None:
        if (role_anchor_selection_path is None) != (
            role_anchor_plan_sha256 is None
        ):
            raise IrodoriTTSAdapterError(
                "role anchor selection pathとfrozen plan SHAは同時に指定してください。",
            )
        if (
            role_anchor_selection_path is not None
            and not role_anchor_selection_path.is_absolute()
        ):
            raise IrodoriTTSAdapterError(
                "role anchor selectionは絶対pathが必要です。",
            )
        if (
            role_anchor_plan_sha256 is not None
            and re.fullmatch(r"[0-9a-f]{64}", role_anchor_plan_sha256) is None
        ):
            raise IrodoriTTSAdapterError(
                "role anchor frozen plan SHAは完全な小文字SHA-256が必要です。",
            )
        self._runtime = runtime if runtime is not None else _NativeRuntime()
        self._reading_converter = reading_converter
        self._role_anchor_selection_path = role_anchor_selection_path
        self._role_anchor_plan_sha256 = role_anchor_plan_sha256
        self._prepared_inputs: dict[tuple[str, str], _PreparedInput] = {}
        self._load_peak: dict[str, float] | None = None
        self._prepared = False

    def prepare(
        self,
        jobs: Sequence[LineJob],
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        self._prepared = False
        self._prepared_inputs.clear()

        jobs_by_character: dict[tuple[str, str], list[LineJob]] = {}
        role_identities: dict[tuple[str, str], dict[str, str]] = {}
        role_snapshots: dict[tuple[str, str], RoleSnapshot] = {}
        reference_voice_by_character: dict[tuple[str, str], str | None] = {}
        line_keys: set[tuple[str, str]] = set()
        for job in jobs:
            line_key = _job_key(job)
            if line_key in line_keys:
                raise IrodoriTTSAdapterError(
                    f"同じ line job が重複しています: {line_key[0]}/{line_key[1]}",
                )
            line_keys.add(line_key)
            character_key = _character_key(job)
            identity = _role_identity(job)
            previous_identity = role_identities.get(character_key)
            if previous_identity is not None and previous_identity != identity:
                raise IrodoriTTSAdapterError(
                    "同じ scenario/character に異なる役柄情報があります: "
                    f"{character_key[0]}/{character_key[1]}",
                )
            role_identities[character_key] = identity
            snapshot = _role_snapshot(job)
            previous_snapshot = role_snapshots.get(character_key)
            if previous_snapshot is not None and previous_snapshot != snapshot:
                raise IrodoriTTSAdapterError(
                    "同じscenario/characterに異なるrole snapshotがあります: "
                    f"{character_key[0]}/{character_key[1]}",
                )
            role_snapshots[character_key] = snapshot
            reference_voice = _reference_voice_value(job)
            if (
                character_key in reference_voice_by_character
                and reference_voice_by_character[character_key] != reference_voice
            ):
                raise IrodoriTTSAdapterError(
                    "同じ scenario/character に異なる reference_voice があります: "
                    f"{character_key[0]}/{character_key[1]}",
                )
            reference_voice_by_character[character_key] = reference_voice
            jobs_by_character.setdefault(character_key, []).append(job)

        reference_entries = (
            _load_reference_entries(voices_dir)
            if any(
                value is not None
                for value in reference_voice_by_character.values()
            )
            else {}
        )
        references: dict[tuple[str, str], _RoleReference] = {}
        for character_key in sorted(jobs_by_character):
            voice_id = reference_voice_by_character[character_key]
            if voice_id is not None:
                try:
                    entry = reference_entries[voice_id]
                except KeyError as error:
                    raise IrodoriTTSAdapterError(
                        f"未登録の reference_voice です: {voice_id}",
                    ) from error
                wav_path, sha256 = _resolve_reference_wav(voices_dir, entry)
                references[character_key] = _RoleReference(
                    source="voice-asset",
                    voice_id=voice_id,
                    wav_path=wav_path,
                    sha256=sha256,
                    caption=None,
                    text=None,
                    phase_peak_vram_mib={},
                    selected_anchor_receipt=None,
                )
                continue

            if (
                self._role_anchor_selection_path is None
                or self._role_anchor_plan_sha256 is None
            ):
                raise IrodoriTTSAdapterError(
                    "reference_voice=nullのPhase B準備には"
                    "role anchor selectionの絶対pathとfrozen plan SHAが必要です。",
                )
            try:
                selected = resolve_selected_anchor(
                    selection_path=self._role_anchor_selection_path,
                    plan_sha256=self._role_anchor_plan_sha256,
                    model=MODEL_ID,
                    model_revision=PROFILE_VERSION,
                    role=role_snapshots[character_key],
                )
            except CompletionAnchorError as error:
                raise IrodoriTTSAdapterError(
                    f"selected role anchorが不正です: {error}",
                ) from error
            if selected.anchor_text != ROLE_ANCHOR_TEXT:
                raise IrodoriTTSAdapterError(
                    "selected role anchor textがIrodori固定値と一致しません。",
                )
            identity = role_identities[character_key]
            references[character_key] = _RoleReference(
                source="selected-role-anchor",
                voice_id=None,
                wav_path=selected.audio_path,
                sha256=selected.audio_sha256,
                caption=_role_anchor_caption(identity),
                text=selected.anchor_text,
                phase_peak_vram_mib={},
                selected_anchor_receipt=selected.receipt(),
            )
        del artifacts_dir

        for character_key, character_jobs in jobs_by_character.items():
            reference = references[character_key]
            for job in character_jobs:
                self._prepared_inputs[_job_key(job)] = _prepare_input(
                    job,
                    reference=reference,
                    reading_converter=self._reading_converter,
                )

        self._prepared = True

    def role_anchor_generation_input(
        self,
        role: RoleSnapshot,
    ) -> Mapping[str, Any]:
        identity = _identity_from_role(role)
        return {
            "model": MODEL_ID,
            "model_revision": PROFILE_VERSION,
            "text": ROLE_ANCHOR_TEXT,
            "caption": _role_anchor_caption(identity),
            "reference_wav": None,
            "sampling": _role_anchor_sampling(),
            "watermark_payload": WATERMARK_PAYLOAD,
        }

    def generate_role_anchor(
        self,
        role: RoleSnapshot,
        *,
        seed: int,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        generation_input = self.role_anchor_generation_input(role)
        self._ensure_runtime_loaded()
        realized = self._run_phase(
            f"Irodori role anchor generation ({role.scenario}/{role.character})",
            lambda: self._runtime.synthesize(
                text=ROLE_ANCHOR_TEXT,
                caption=str(generation_input["caption"]),
                reference_wav=None,
                output_wav=output_wav,
                seed=seed,
            ),
        )
        if not output_wav.is_file():
            raise IrodoriTTSAdapterError(
                f"role anchor WAVが書き込まれませんでした: {output_wav}",
            )
        _validate_pcm16_wav(output_wav)
        peaks = _validate_role_anchor_receipt(realized, expected_seed=seed)
        if self._load_peak is None:
            raise IrodoriTTSAdapterError("role anchor runtime load profileがありません。")
        result = dict(realized)
        result["phase_peak_vram_mib"] = {
            "role_anchor_runtime_load": _copy_peak(self._load_peak),
            "role_anchor_generate": _copy_peak(peaks["generation"]),
        }
        result["role_identity_sha256"] = role.role_identity_sha256
        return result

    def close_role_anchor_generation(self) -> None:
        # Irodori runtime owns one process-local model bundle and has no release API.
        return

    def generation_params(self) -> Mapping[str, Any]:
        return {
            "irodori_tts_version": IRODORI_TTS_VERSION,
            "upstream_revision": UPSTREAM_REVISION,
            "checkpoint": CHECKPOINT_ID,
            "checkpoint_revision": CHECKPOINT_REVISION,
            "codec": CODEC_ID,
            "codec_revision": CODEC_REVISION,
            "dacvae_revision": DACVAE_REVISION,
            "tokenizer": TOKENIZER_ID,
            "tokenizer_revision": TOKENIZER_REVISION,
            "silentcipher_model": SILENTCIPHER_MODEL_ID,
            "silentcipher_model_revision": SILENTCIPHER_MODEL_REVISION,
            "silentcipher_version": SILENTCIPHER_VERSION,
            "pyopenjtalk_plus_version": PYOPENJTALK_VERSION,
            "torch_version": TORCH_VERSION,
            "torchaudio_version": TORCHAUDIO_VERSION,
            "torchcodec_version": TORCHCODEC_VERSION,
            "cuda_wheel_version": CUDA_WHEEL_VERSION,
            "device": DEVICE,
            "model_precision": MODEL_PRECISION,
            "codec_precision": CODEC_PRECISION,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "num_steps": NUM_STEPS,
            "cfg": {
                "guidance_mode": CFG_GUIDANCE_MODE,
                "text": CFG_SCALE_TEXT,
                "caption": CFG_SCALE_CAPTION,
                "speaker": CFG_SCALE_SPEAKER,
                "min_t": CFG_MIN_T,
                "max_t": CFG_MAX_T,
            },
            "duration": {
                "automatic": True,
                "scale": DURATION_SCALE,
                "min_seconds": MIN_SECONDS,
                "max_seconds": MAX_SECONDS,
            },
            "reference": {
                "normalize_db": REF_NORMALIZE_DB,
                "ensure_max": True,
                "max_seconds": MAX_REF_SECONDS,
            },
            "role_reference": {
                "control": REFERENCE_CONTROL,
                "key": ["scenario", "character"],
                "anchor_text": ROLE_ANCHOR_TEXT,
                "anchor_caption_policy": "complete-role-identity-neutral-performance",
                "anchor_sampling": _role_anchor_sampling(),
                "selection_protocol": "role-anchor-selection-v1",
                "selection_required_for_null_reference": True,
                "explicit_asset_policy": "strict-metadata-wav-sha256",
            },
            "decode_mode": "sequential",
            "codec_deterministic_encode": True,
            "codec_deterministic_decode": True,
            "context_kv_cache": True,
            "compile_model": False,
            "emotion_emoji": dict(EMOTION_EMOJI),
            "silentcipher_watermark_stage_required": True,
            "silentcipher_payload": WATERMARK_PAYLOAD,
        }

    def generation_input(
        self,
        job: LineJob,
        take_context: TakeContext,
    ) -> Mapping[str, Any]:
        require_take_context(take_context, self.take_recipe())
        return self._prepared_input(job).as_generation_input()

    def generate(
        self,
        job: LineJob,
        take_context: TakeContext,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        require_take_context(take_context, self.take_recipe())
        seed = take_context.seed
        assert seed is not None
        prepared = self._prepared_input(job)
        self._ensure_runtime_loaded()
        _verify_reference_unchanged(prepared)
        realized = self._run_phase(
            f"Irodori generation ({job.scenario_id}/{job.line_id})",
            lambda: self._runtime.synthesize(
                text=prepared.text,
                caption=prepared.caption,
                reference_wav=prepared.reference_wav,
                output_wav=output_wav,
                seed=seed,
            ),
        )
        if not output_wav.is_file():
            raise IrodoriTTSAdapterError(
                f"adapter 出力がありません: {output_wav}",
            )
        assert self._load_peak is not None

        result = dict(realized)
        peaks = _validate_target_receipt(realized, seed=seed)
        result["phase_peak_vram_mib"] = {
            "runtime_load": _copy_peak(self._load_peak),
            **{
                str(name): _copy_peak(peak)
                for name, peak in prepared.reference_phase_peak_vram_mib.items()
            },
            **{str(name): _copy_peak(peak) for name, peak in peaks.items()},
        }
        result["seed"] = seed
        result["sampling"] = take_context.sampling_dict()
        result["role_identity"] = dict(prepared.role_identity)
        result["caption"] = prepared.caption
        result["reference_control"] = REFERENCE_CONTROL
        result["reference_source"] = prepared.reference_source
        result["reference_voice"] = prepared.reference_voice
        result["reference_sha256"] = prepared.reference_sha256
        result["reference_caption"] = prepared.reference_caption
        result["reference_text"] = prepared.reference_text
        if prepared.selected_anchor_receipt is not None:
            result["selected_anchor"] = dict(prepared.selected_anchor_receipt)
        return result

    def _ensure_runtime_loaded(self) -> None:
        if self._load_peak is not None:
            return
        peak = self._run_phase("Irodori runtime load", self._runtime.prepare)
        self._load_peak = _copy_peak(peak)

    def _prepared_input(self, job: LineJob) -> _PreparedInput:
        if not self._prepared:
            raise IrodoriTTSAdapterError("prepare() が完了していません。")
        key = _job_key(job)
        try:
            return self._prepared_inputs[key]
        except KeyError as error:
            raise IrodoriTTSAdapterError(
                f"prepare 済み input がありません: {key[0]}/{key[1]}",
            ) from error

    def _run_phase(self, phase: str, action: Callable[[], _T]) -> _T:
        try:
            return action()
        except Exception as error:
            if self._runtime.is_out_of_memory(error):
                raise IrodoriTTSAdapterError(
                    f"{phase} で CUDA out of memory が発生しました。",
                ) from error
            if isinstance(error, IrodoriTTSAdapterError):
                raise
            raise IrodoriTTSAdapterError(f"{phase} に失敗しました: {error}") from error


def _prepare_input(
    job: LineJob,
    *,
    reference: _RoleReference,
    reading_converter: Callable[[str], str] | None,
) -> _PreparedInput:
    _job_key(job)
    text = _required_string(job.line, "text", "line")
    reading_value = job.line.get("reading")
    reading = resolve_japanese_reading(
        text=text,
        reading=reading_value,
        converter=reading_converter,
    )
    emotion = _required_string(job.line, "emotion", "line")
    try:
        emoji = EMOTION_EMOJI[emotion]
        emotion_label = _EMOTION_LABEL[emotion]
    except KeyError as error:
        raise IrodoriTTSAdapterError(
            f"未対応の line.emotion です: {emotion}",
        ) from error

    role_identity = _role_identity(job)
    setting = _required_string(job.scene, "setting", "scene")
    delivery = _required_string(job.line, "delivery", "line")
    intensity = job.line.get("intensity")
    if (
        not isinstance(intensity, int)
        or isinstance(intensity, bool)
        or not 1 <= intensity <= 3
    ):
        raise IrodoriTTSAdapterError(
            f"line.intensity は 1〜3 の integer が必要です: {intensity!r}",
        )
    caption = "\n".join(
        (
            _role_caption(role_identity),
            f"場面: {setting}",
            f"感情: {emotion_label}（強度 {intensity}/3）",
            f"演技: {delivery}",
        ),
    )

    spoken_text = f"{emoji}{reading.text}" if emoji is not None else reading.text
    return _PreparedInput(
        text=spoken_text,
        reading_source=reading.source,
        emotion=emotion,
        emoji=emoji,
        caption=caption,
        role_identity=role_identity,
        reference_source=reference.source,
        reference_voice=reference.voice_id,
        reference_wav=reference.wav_path,
        reference_sha256=reference.sha256,
        reference_caption=reference.caption,
        reference_text=reference.text,
        reference_phase_peak_vram_mib=reference.phase_peak_vram_mib,
        selected_anchor_receipt=reference.selected_anchor_receipt,
    )


def _character_key(job: LineJob) -> tuple[str, str]:
    scenario_id, _line_id = _job_key(job)
    character_id = _required_identifier(job.character, "id", "character")
    return scenario_id, character_id


def _role_identity(job: LineJob) -> dict[str, str]:
    scenario_id, character_id = _character_key(job)
    name = _required_string(job.character, "name", "character")
    # scenario schema が省略時 human と定義している optional field。
    kind_value = job.character["kind"] if "kind" in job.character else "human"
    if not isinstance(kind_value, str) or kind_value not in _KIND_LABEL:
        raise IrodoriTTSAdapterError(
            f"未対応の character.kind です: {kind_value!r}",
        )
    gender = _required_string(job.character, "gender", "character")
    if gender not in _GENDER_LABEL:
        raise IrodoriTTSAdapterError(
            f"未対応の character.gender です: {gender}",
        )
    age = _required_string(job.character, "age", "character")
    if age not in _AGE_LABEL:
        raise IrodoriTTSAdapterError(
            f"未対応の character.age です: {age}",
        )
    return {
        "scenario": scenario_id,
        "character": character_id,
        "name": name,
        "kind": kind_value,
        "gender": gender,
        "age": age,
        "archetype": _required_string(job.character, "archetype", "character"),
        "voice": _required_string(job.character, "voice", "character"),
        "personality": _required_string(job.character, "personality", "character"),
    }


def _role_snapshot(job: LineJob) -> RoleSnapshot:
    scenario_id, character_id = _character_key(job)
    try:
        return build_role_snapshot(
            scenario=scenario_id,
            character=character_id,
            character_document=job.character,
            scene_setting=_required_string(job.scene, "setting", "scene"),
        )
    except CompletionPlanError as error:
        raise IrodoriTTSAdapterError(f"role snapshotが不正です: {error}") from error


def _identity_from_role(role: RoleSnapshot) -> dict[str, str]:
    if role.reference_voice is not None:
        raise IrodoriTTSAdapterError(
            "明示reference roleはIrodori anchor対象にできません。",
        )
    identity = {
        "scenario": role.scenario,
        "character": role.character,
        **dict(role.role),
    }
    for key, labels in (
        ("kind", _KIND_LABEL),
        ("gender", _GENDER_LABEL),
        ("age", _AGE_LABEL),
    ):
        if identity[key] not in labels:
            raise IrodoriTTSAdapterError(
                f"anchor roleの{key}が未対応です: {identity[key]}",
            )
    return identity


def _role_caption(identity: Mapping[str, str]) -> str:
    return "\n".join(
        (
            "架空のキャラクターとして、実在の人物や声優を模倣せずに話す。",
            f"名前: {identity['name']}",
            f"種別: {_KIND_LABEL[identity['kind']]}（{identity['kind']}）",
            f"性別: {_GENDER_LABEL[identity['gender']]}（{identity['gender']}）",
            f"年齢: {_AGE_LABEL[identity['age']]}（{identity['age']}）",
            f"役柄: {identity['archetype']}",
            f"声質: {identity['voice']}",
            f"性格: {identity['personality']}",
        ),
    )


def _role_anchor_caption(identity: Mapping[str, str]) -> str:
    return "\n".join(
        (
            _role_caption(identity),
            "発声: この役柄の声の同一性を定める中立な読み。"
            "場面固有の感情や演技を加えない。",
        ),
    )


def _role_anchor_sampling() -> dict[str, Any]:
    return {
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "no_ref": True,
        "ref_normalize_db": REF_NORMALIZE_DB,
        "ref_ensure_max": True,
        "num_candidates": 1,
        "decode_mode": "sequential",
        "seconds": None,
        "num_steps": NUM_STEPS,
        "cfg_scale_text": CFG_SCALE_TEXT,
        "cfg_scale_caption": CFG_SCALE_CAPTION,
        "cfg_scale_speaker": CFG_SCALE_SPEAKER,
        "cfg_guidance_mode": CFG_GUIDANCE_MODE,
        "cfg_min_t": CFG_MIN_T,
        "cfg_max_t": CFG_MAX_T,
        "duration_scale": DURATION_SCALE,
        "min_seconds": MIN_SECONDS,
        "max_seconds": MAX_SECONDS,
        "max_ref_seconds": MAX_REF_SECONDS,
        "context_kv_cache": True,
        "speaker_uncond_mode": "mask",
        "t_schedule_mode": "linear",
        "sway_coeff": -1.0,
        "trim_tail": True,
        "tail_window_size": 20,
        "tail_std_threshold": 0.05,
        "tail_mean_threshold": 0.1,
    }


def _reference_voice_value(job: LineJob) -> str | None:
    if "reference_voice" not in job.character:
        raise IrodoriTTSAdapterError(
            "character.reference_voice は voice id または null が必要です。",
        )
    value = job.character["reference_voice"]
    if value is None:
        return None
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise IrodoriTTSAdapterError(
            "character.reference_voice は voice id または null が必要です。",
        )
    return value


def _load_reference_entries(
    voices_dir: Path,
) -> dict[str, Mapping[str, Any]]:
    validation = validate_voice_metadata(voices_dir)
    if validation.problems:
        raise IrodoriTTSAdapterError(
            f"参照音声 metadata が不正です: {validation.problems[0]}",
        )
    metadata_path = voices_dir / "metadata.yaml"
    try:
        document = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise IrodoriTTSAdapterError(
            f"参照音声 metadata を読めません: {metadata_path}: {error}",
        ) from error
    if not isinstance(document, Mapping) or not isinstance(
        document.get("voices"), list
    ):
        raise IrodoriTTSAdapterError(
            f"参照音声 metadata の構造が不正です: {metadata_path}",
        )
    return {
        str(entry["id"]): entry
        for entry in document["voices"]
        if isinstance(entry, Mapping)
    }


def _resolve_reference_wav(
    voices_dir: Path,
    entry: Mapping[str, Any],
) -> tuple[Path, str]:
    voice_id = str(entry["id"])
    expected_file = f"{voice_id}/reference.wav"
    if entry.get("file") != expected_file:
        raise IrodoriTTSAdapterError(
            f"reference voice path が不正です: {voice_id}",
        )
    expected_sha256 = entry.get("sha256")
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}",
        expected_sha256,
    ):
        raise IrodoriTTSAdapterError(
            f"reference voice SHA-256 が不正です: {voice_id}",
        )

    voices_dir = voices_dir.resolve()
    audio_path = voices_dir / expected_file
    resolved_audio_path = audio_path.resolve()
    if audio_path.is_symlink() or not resolved_audio_path.is_relative_to(voices_dir):
        raise IrodoriTTSAdapterError(
            f"reference voice は voices 内の通常ファイルが必要です: {audio_path}",
        )
    if not audio_path.is_file():
        raise IrodoriTTSAdapterError(
            f"reference voice WAV がありません: {audio_path}",
        )
    actual_sha256 = _sha256_file(audio_path)
    if actual_sha256 != expected_sha256:
        raise IrodoriTTSAdapterError(
            "reference voice SHA-256 が一致しません: "
            f"expected={expected_sha256}, actual={actual_sha256}",
        )
    return audio_path, expected_sha256


def _validate_role_anchor_receipt(
    realized: Mapping[str, Any],
    *,
    expected_seed: int,
) -> dict[str, dict[str, float]]:
    if realized.get("silentcipher_watermark_stage_executed") is not True:
        raise IrodoriTTSAdapterError(
            "role anchor で SilentCipher watermark stage が確認できません。",
        )
    if realized.get("seed") != expected_seed:
        raise IrodoriTTSAdapterError(
            "role anchor の realized seed が一致しません。",
        )
    if realized.get("sample_rate_hz") != SAMPLE_RATE_HZ:
        raise IrodoriTTSAdapterError(
            "role anchor の realized sample rate が一致しません。",
        )
    peaks = realized.get("phase_peak_vram_mib")
    if not isinstance(peaks, Mapping) or set(peaks) != {"generation"}:
        raise IrodoriTTSAdapterError(
            "role anchor の phase_peak_vram_mib が不正です。",
        )
    generation_peak = peaks["generation"]
    if not isinstance(generation_peak, Mapping):
        raise IrodoriTTSAdapterError(
            "role anchor generation peak が不正です。",
        )
    return {"generation": _copy_peak(generation_peak)}


def _validate_target_receipt(
    realized: Mapping[str, Any],
    *,
    seed: int,
) -> dict[str, dict[str, float]]:
    if realized.get("silentcipher_watermark_stage_executed") is not True:
        raise IrodoriTTSAdapterError(
            "target generation で SilentCipher watermark stage が確認できません。",
        )
    if realized.get("seed") != seed:
        raise IrodoriTTSAdapterError(
            "target generation の realized seed が一致しません。",
        )
    if realized.get("sample_rate_hz") != SAMPLE_RATE_HZ:
        raise IrodoriTTSAdapterError(
            "target generation の realized sample rate が一致しません。",
        )
    peaks = realized.get("phase_peak_vram_mib")
    if not isinstance(peaks, Mapping) or set(peaks) != {"generation"}:
        raise IrodoriTTSAdapterError(
            "target generation の phase_peak_vram_mib が不正です。",
        )
    generation_peak = peaks["generation"]
    if not isinstance(generation_peak, Mapping):
        raise IrodoriTTSAdapterError(
            "target generation peak が不正です。",
        )
    return {"generation": _copy_peak(generation_peak)}


def _validate_pcm16_wav(path: Path) -> None:
    try:
        with wave.open(str(path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
    except (OSError, EOFError, wave.Error) as error:
        raise IrodoriTTSAdapterError(
            f"role anchor WAV が不正です: {path}: {error}",
        ) from error
    if (
        channels != 1
        or sample_width != 2
        or sample_rate != SAMPLE_RATE_HZ
        or frame_count < 1
    ):
        raise IrodoriTTSAdapterError(
            "role anchor WAV format が不正です: "
            f"channels={channels}, sample_width={sample_width}, "
            f"sample_rate={sample_rate}, frames={frame_count}",
        )


def _job_key(job: LineJob) -> tuple[str, str]:
    if job.locale != "ja":
        raise IrodoriTTSAdapterError(
            f"Irodori-TTS adapter の language は Japanese 固定です: {job.locale}",
        )
    scenario_id = _required_identifier(job.scene, "id", "scene")
    line_id = _required_identifier(job.line, "id", "line")
    return scenario_id, line_id


def _required_identifier(
    value: Mapping[str, Any],
    key: str,
    owner: str,
) -> str:
    identifier = _required_string(value, key, owner)
    if not _IDENTIFIER.fullmatch(identifier):
        raise IrodoriTTSAdapterError(
            f"{owner}.{key} は slug identifier が必要です: {identifier!r}",
        )
    return identifier


def _required_string(
    value: Mapping[str, Any],
    key: str,
    owner: str,
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or item.strip() == "":
        raise IrodoriTTSAdapterError(
            f"{owner}.{key} は non-empty string が必要です。",
        )
    return item


def _require_distribution(
    name: str,
    expected: str,
    *,
    allow_local_suffix: bool = False,
) -> None:
    try:
        actual = metadata.version(name)
    except metadata.PackageNotFoundError as error:
        raise IrodoriTTSAdapterError(
            f"依存 {name}=={expected} がインストールされていません。",
        ) from error
    matches = actual == expected or (
        allow_local_suffix and actual.startswith(f"{expected}+")
    )
    if not matches:
        raise IrodoriTTSAdapterError(
            f"{name} の version が一致しません: expected={expected}, actual={actual}",
        )


def _pinned_silentcipher_loader(
    get_model: Callable[..., Any],
    *,
    checkpoint_path: Path,
    config_path: Path,
) -> Callable[..., Any]:
    fixed_config_path = config_path

    def load(
        model_type: str = "44.1k",
        ckpt_path: str = "../Models/44_1_khz/73999_iteration",
        config_path: str = "../Models/44_1_khz/73999_iteration/hparams.yaml",
        device: str = "cpu",
        **kwargs: Any,
    ) -> Any:
        del ckpt_path, config_path
        if kwargs:
            raise IrodoriTTSAdapterError(
                f"SilentCipher の未対応引数です: {sorted(kwargs)}",
            )
        if model_type != "44.1k":
            raise IrodoriTTSAdapterError(
                f"SilentCipher model type が一致しません: {model_type}",
            )
        return get_model(
            model_type=model_type,
            ckpt_path=str(checkpoint_path),
            config_path=str(fixed_config_path),
            device=device,
        )

    return load


def _copy_peak(value: Mapping[str, Any]) -> dict[str, float]:
    if set(value) != {"allocated_mib", "reserved_mib"}:
        raise IrodoriTTSAdapterError(
            f"CUDA peak profile の項目が不正です: {sorted(value)}",
        )
    copied: dict[str, float] = {}
    for key in ("allocated_mib", "reserved_mib"):
        item = value[key]
        if not isinstance(item, (int, float)) or isinstance(item, bool) or item < 0:
            raise IrodoriTTSAdapterError(
                f"CUDA peak profile の値が不正です: {key}={item!r}",
            )
        copied[key] = float(item)
    return copied


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_reference_unchanged(prepared: _PreparedInput) -> None:
    path = prepared.reference_wav
    if path.is_symlink() or not path.is_file():
        raise IrodoriTTSAdapterError(
            f"参照音声 WAV が通常ファイルではありません: {path}",
        )
    try:
        actual_sha256 = _sha256_file(path)
    except OSError as error:
        raise IrodoriTTSAdapterError(
            f"参照音声 WAV を再検証できません: {path}: {error}",
        ) from error
    if actual_sha256 != prepared.reference_sha256:
        raise IrodoriTTSAdapterError(
            "参照音声 WAV がprepare後に変更されました: "
            f"expected={prepared.reference_sha256}, actual={actual_sha256}",
        )
