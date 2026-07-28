from __future__ import annotations

import hashlib
import importlib
import os
import re
import subprocess
import sys
import wave
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
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
from gaya_pipeline.voice_assets import validate_voice_metadata

MODEL_ID = "gpt-sovits-v2-pro-plus"
UPSTREAM_REPOSITORY = "RVC-Boss/GPT-SoVITS"
UPSTREAM_REVISION = "d523079fc05d9a8028d6085bffe4a2757c32abb6"
WEIGHTS_REPOSITORY = "lj1995/GPT-SoVITS"
WEIGHTS_REVISION = "336b2ec4e8d4ac74740798dd40af44e74659ecaf"
UPSTREAM_ROOT_ENV = "GAYA_GPT_SOVITS_ROOT"
TORCH_VERSION = "2.7.0"
TORCHAUDIO_VERSION = "2.7.0"
CUDA_WHEEL_VERSION = "12.8"
DEVICE = "cuda:0"
PRECISION = "fp16"
NATIVE_SAMPLE_RATE_HZ = 32_000
REFERENCE_SAMPLE_RATE_HZ = 48_000
REFERENCE_SECONDS = 5
REFERENCE_FRAME_COUNT = REFERENCE_SAMPLE_RATE_HZ * REFERENCE_SECONDS
SEED = 42
TOP_K = 5
TOP_P = 1.0
TEMPERATURE = 1.0
REPETITION_PENALTY = 1.35

PROFILE_VERSION = (
    f"GPT-SoVITS v2ProPlus; {UPSTREAM_REPOSITORY}@{UPSTREAM_REVISION}; "
    f"{WEIGHTS_REPOSITORY}@{WEIGHTS_REVISION}"
)

REQUIRED_DISTRIBUTIONS: Mapping[str, str] = {
    "cn2an": "0.5.24",
    "fast-langdetect": "1.0.1",
    "ffmpeg-python": "0.2.0",
    "jieba": "0.42.1",
    "jieba-fast": "0.53",
    "librosa": "0.10.2",
    "matplotlib": "3.10.3",
    "numpy": "1.26.4",
    "peft": "0.17.1",
    "pypinyin": "0.55.0",
    "pytorch-lightning": "2.6.5",
    "split-lang": "2.1.1",
    "torch": TORCH_VERSION,
    "torchaudio": TORCHAUDIO_VERSION,
    "torchmetrics": "1.5.2",
    "transformers": "4.50.3",
    "x-transformers": "2.24.0",
}

WEIGHT_FILES: Mapping[str, str] = {
    "s1v3.ckpt": "87133414860ea14ff6620c483a3db5ed07b44be42e2c3fcdad65523a729a745a",
    "v2Pro/s2Gv2ProPlus.pth": (
        "d42a22bbbf65fb2bbdd45ad6a66841156977db45c7aabe0a6992ff378d9c7d3b"
    ),
    "sv/pretrained_eres2netv2w24s4ep4.ckpt": (
        "4f5a0bf73c61eb41b174e1bb54e7ee3c83233892be8e0af1f187024e8e581a35"
    ),
    "chinese-hubert-base/config.json": (
        "c3e5060a1277e0f078cc6be9da4528a605dba6ece93018981fe2c820e5c7b103"
    ),
    "chinese-hubert-base/preprocessor_config.json": (
        "dcd684124d06722947939d41ea6ae58dbf10968c60a11a29f23ddc602c64a29b"
    ),
    "chinese-hubert-base/pytorch_model.bin": (
        "24164f129c66499d1346e2aa55f183250c223161ec2770c0da3d3b08cf432d3c"
    ),
    "chinese-roberta-wwm-ext-large/config.json": (
        "3d57de2fd7e80d0e5c8ff194f0bbb6baa10df7e43fc262a0cc71298a78b0a3e5"
    ),
    "chinese-roberta-wwm-ext-large/pytorch_model.bin": (
        "e53a693acc59ace251d143d068096ae0d7b79e4b1b503fa84c9dcf576448c1d8"
    ),
    "chinese-roberta-wwm-ext-large/tokenizer.json": (
        "173796956820ea27bd14f76bf28162607ff4254807e2948253eb5b46f5bb643b"
    ),
    "fast_langdetect/lid.176.bin": (
        "7e69ec5451bc261cc7844e49e4792a85d7f09c06789ec800fc4a44aec362764e"
    ),
}

ALLOWED_UPSTREAM_UNTRACKED = frozenset(
    {
        "GPT_SoVITS/text/ja_userdic/user.dict",
        "GPT_SoVITS/text/ja_userdic/userdict.md5",
    },
)
ALLOWED_UPSTREAM_IGNORED = frozenset(
    {
        *(f"GPT_SoVITS/pretrained_models/{relative}" for relative in WEIGHT_FILES),
        "GPT_SoVITS/pretrained_models/.cache/huggingface/.gitignore",
        "GPT_SoVITS/pretrained_models/.cache/huggingface/CACHEDIR.TAG",
        (
            "GPT_SoVITS/pretrained_models/.cache/huggingface/download/"
            "chinese-hubert-base/config.json.metadata"
        ),
        (
            "GPT_SoVITS/pretrained_models/.cache/huggingface/download/"
            "chinese-hubert-base/preprocessor_config.json.metadata"
        ),
        (
            "GPT_SoVITS/pretrained_models/.cache/huggingface/download/"
            "chinese-hubert-base/pytorch_model.bin.metadata"
        ),
        (
            "GPT_SoVITS/pretrained_models/.cache/huggingface/download/"
            "chinese-roberta-wwm-ext-large/config.json.metadata"
        ),
        (
            "GPT_SoVITS/pretrained_models/.cache/huggingface/download/"
            "chinese-roberta-wwm-ext-large/pytorch_model.bin.metadata"
        ),
        (
            "GPT_SoVITS/pretrained_models/.cache/huggingface/download/"
            "chinese-roberta-wwm-ext-large/tokenizer.json.metadata"
        ),
        ("GPT_SoVITS/pretrained_models/.cache/huggingface/download/s1v3.ckpt.metadata"),
        (
            "GPT_SoVITS/pretrained_models/.cache/huggingface/download/"
            "sv/pretrained_eres2netv2w24s4ep4.ckpt.metadata"
        ),
        (
            "GPT_SoVITS/pretrained_models/.cache/huggingface/download/"
            "v2Pro/s2Gv2ProPlus.pth.metadata"
        ),
        (
            "GPT_SoVITS/pretrained_models/.cache/huggingface/trees/"
            f"{WEIGHTS_REVISION}.json"
        ),
    },
)
USER_DICTIONARY_CSV_MD5 = "878b3caf4d1cd7c2927c26e85072a2f5"
USER_DICTIONARY_SHA256 = (
    "b44817ce96e24be7bcfdd009d834b5237fe044dc9ed5f2f9709f71da9d506fed"
)

REFERENCE_ASSIGNMENTS: Mapping[tuple[str, str], str] = {
    ("tavern-night", "drunkard"): "hadou-emotion-11",
    ("tavern-night", "old-regular"): "hadou-emotion-11",
    ("market-day", "fruit-vendor"): "hadou-emotion-11",
    ("market-day", "shopper"): "lux-emotion-76",
    ("market-day", "street-kid"): "tsukuyomi-corpus-94",
}

REFERENCE_START_FRAMES: Mapping[str, int] = {
    "amitaro-countdown": 0,
    "hadou-emotion-11": 0,
    "lux-emotion-76": 0,
    "sayoko-emotion-75": 0,
    "tsukuyomi-corpus-94": 0,
}

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MIB = 1024 * 1024
_T = TypeVar("_T")


class GPTSoVITSAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class _ReferenceClip:
    voice_id: str
    source_sha256: str
    path: Path
    sha256: str
    start_frame: int
    frame_count: int


@dataclass(frozen=True)
class _PreparedInput:
    text: str
    reading_source: str
    reference_selection_source: str
    reference: _ReferenceClip

    def as_generation_input(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "reading_source": self.reading_source,
            "reference_selection_source": self.reference_selection_source,
            "reference_voice": self.reference.voice_id,
            "reference_source_sha256": self.reference.source_sha256,
            "reference_clip_sha256": self.reference.sha256,
            "reference_clip_start_frame": self.reference.start_frame,
            "reference_clip_frame_count": self.reference.frame_count,
            "prompt_text_mode": "reference-free",
        }


class _Runtime(Protocol):
    def prepare(
        self,
        *,
        upstream_root: Path,
        runtime_config_path: Path,
    ) -> Mapping[str, float]: ...

    def synthesize(
        self,
        *,
        text: str,
        reference_wav: Path,
        output_wav: Path,
    ) -> Mapping[str, Any]: ...

    def is_out_of_memory(self, error: BaseException) -> bool: ...


class _NativeRuntime:
    def __init__(self) -> None:
        self._torch: Any | None = None
        self._numpy: Any | None = None
        self._tts: Any | None = None
        self._upstream_root: Path | None = None

    def prepare(
        self,
        *,
        upstream_root: Path,
        runtime_config_path: Path,
    ) -> Mapping[str, float]:
        if self._tts is not None:
            raise GPTSoVITSAdapterError("runtime はすでに prepare 済みです。")
        if sys.platform != "win32":
            raise GPTSoVITSAdapterError(
                "GPT-SoVITS は Windows native CUDA:0 だけをサポートします。",
            )

        for distribution, version in REQUIRED_DISTRIBUTIONS.items():
            _require_distribution(
                distribution,
                version,
                allow_local_suffix=distribution in {"torch", "torchaudio"},
            )

        root = _validate_upstream(upstream_root)
        model_paths = _validate_weight_files(root)

        try:
            torch = importlib.import_module("torch")
            numpy = importlib.import_module("numpy")
        except (ImportError, ModuleNotFoundError) as error:
            raise GPTSoVITSAdapterError(
                f"GPT-SoVITS の必須依存を import できません: {error}",
            ) from error

        if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
            raise GPTSoVITSAdapterError("CUDA:0 を利用できません。")
        if str(torch.version.cuda) != CUDA_WHEEL_VERSION:
            raise GPTSoVITSAdapterError(
                "PyTorch CUDA wheel が一致しません: "
                f"expected={CUDA_WHEEL_VERSION}, actual={torch.version.cuda}",
            )

        runtime_config_path = runtime_config_path.resolve()
        runtime_config_path.parent.mkdir(parents=True, exist_ok=True)
        custom_config = {
            "device": DEVICE,
            "is_half": True,
            "version": "v2ProPlus",
            "t2s_weights_path": str(model_paths["s1v3.ckpt"]),
            "vits_weights_path": str(model_paths["v2Pro/s2Gv2ProPlus.pth"]),
            "cnhuhbert_base_path": str(
                root / "GPT_SoVITS" / "pretrained_models" / "chinese-hubert-base"
            ),
            "bert_base_path": str(
                root
                / "GPT_SoVITS"
                / "pretrained_models"
                / "chinese-roberta-wwm-ext-large"
            ),
        }

        torch.cuda.set_device(0)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats(0)
        with _upstream_context(root):
            try:
                tts_module = importlib.import_module("TTS_infer_pack.TTS")
                tts_config = tts_module.TTS_Config({"custom": custom_config})
                tts_config.configs_path = str(runtime_config_path)
                tts = tts_module.TTS(tts_config)
            except Exception as error:
                raise GPTSoVITSAdapterError(
                    f"固定 GPT-SoVITS runtime の load に失敗しました: {error}",
                ) from error

        _validate_japanese_dictionary_cache(root, required=True)
        _assert_runtime_identity(tts, model_paths)
        torch.cuda.synchronize(0)
        self._torch = torch
        self._numpy = numpy
        self._tts = tts
        self._upstream_root = root
        return _peak(torch)

    def synthesize(
        self,
        *,
        text: str,
        reference_wav: Path,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        if (
            self._tts is None
            or self._torch is None
            or self._numpy is None
            or self._upstream_root is None
        ):
            raise GPTSoVITSAdapterError("runtime の prepare() が完了していません。")

        torch = self._torch
        torch.cuda.reset_peak_memory_stats(0)
        inputs = {
            "text": text,
            "text_lang": "all_ja",
            "ref_audio_path": str(reference_wav.resolve()),
            "aux_ref_audio_paths": [],
            "prompt_text": "",
            "prompt_lang": "all_ja",
            "top_k": TOP_K,
            "top_p": TOP_P,
            "temperature": TEMPERATURE,
            "text_split_method": "cut0",
            "batch_size": 1,
            "batch_threshold": 0.75,
            "split_bucket": False,
            "speed_factor": 1.0,
            "fragment_interval": 0.3,
            "seed": SEED,
            "return_fragment": False,
            "streaming_mode": False,
            "parallel_infer": False,
            "repetition_penalty": REPETITION_PENALTY,
            "sample_steps": 32,
            "super_sampling": False,
        }
        with _upstream_context(self._upstream_root):
            result = _consume_single_result(self._tts.run(inputs))
        sample_rate, audio = result
        if not isinstance(sample_rate, int) or sample_rate != NATIVE_SAMPLE_RATE_HZ:
            raise GPTSoVITSAdapterError(
                f"native sample rate が不正です: {sample_rate!r}",
            )
        if (
            not isinstance(audio, self._numpy.ndarray)
            or audio.dtype != self._numpy.int16
            or audio.ndim != 1
            or audio.size == 0
            or not self._numpy.any(audio)
        ):
            raise GPTSoVITSAdapterError(
                "GPT-SoVITS が有効な mono int16 音声を返しませんでした。",
            )

        output_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_wav), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio.tobytes())
        torch.cuda.synchronize(0)
        return {
            "seed": SEED,
            "sample_rate_hz": sample_rate,
            "prompt_text_mode": "reference-free",
            "phase_peak_vram_mib": {"generation": _peak(torch)},
        }

    def is_out_of_memory(self, error: BaseException) -> bool:
        if self._torch is not None:
            classes = tuple(
                error_class
                for error_class in (
                    getattr(self._torch, "OutOfMemoryError", None),
                    getattr(self._torch.cuda, "OutOfMemoryError", None),
                )
                if isinstance(error_class, type)
            )
            if classes and isinstance(error, classes):
                return True
        return "out of memory" in str(error).lower()


class GPTSoVITSAdapter:
    profile = ModelProfile(
        id=MODEL_ID,
        name="GPT-SoVITS v2ProPlus",
        version=PROFILE_VERSION,
        license_note=(
            "GPT-SoVITS コードと lj1995/GPT-SoVITS 公式重みは MIT。"
            "言語識別用 fastText lid.176.bin は CC BY-SA 3.0 "
            "(Meta AI Research)。"
            "透かしなし。生成時は各 reference_voice 素材の利用規約・"
            "クレジット・再配布条件にも従う。無断の声真似や誤認を招く利用を禁止。"
        ),
        capabilities=Capabilities(
            emotion=False,
            voice_prompt=False,
            clone=True,
            nonverbal=False,
            reading=True,
        ),
    )

    def take_recipe(self) -> TakeRecipe:
        return TakeRecipe(
            version="fixed-single-v1",
            seed_policy="fixed",
            single_take_seed=SEED,
            seed_range=(0, 2**32 - 1),
            sampling=(
                ("repetition_penalty", REPETITION_PENALTY),
                ("temperature", TEMPERATURE),
                ("top_k", TOP_K),
                ("top_p", TOP_P),
            ),
            supports_multiple=False,
        )

    def __init__(
        self,
        *,
        runtime: _Runtime | None = None,
        upstream_root: Path | None = None,
    ) -> None:
        self._runtime = runtime if runtime is not None else _NativeRuntime()
        self._upstream_root = upstream_root
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
        self._load_peak = None

        entries = _load_reference_entries(voices_dir)
        selections: list[tuple[LineJob, str, str]] = []
        for job in jobs:
            key = _job_key(job)
            if key in self._prepared_inputs or any(
                _job_key(candidate) == key for candidate, _, _ in selections
            ):
                raise GPTSoVITSAdapterError(
                    f"同じ line job が重複しています: {key[0]}/{key[1]}",
                )
            voice_id, source = _select_reference_voice(job)
            selections.append((job, voice_id, source))

        clips_dir = artifacts_dir.resolve() / "voices" / MODEL_ID
        clips: dict[str, _ReferenceClip] = {}
        for _, voice_id, _ in selections:
            if voice_id not in clips:
                try:
                    entry = entries[voice_id]
                except KeyError as error:
                    raise GPTSoVITSAdapterError(
                        f"未登録の reference_voice です: {voice_id}",
                    ) from error
                clips[voice_id] = _derive_reference_clip(
                    voices_dir,
                    entry,
                    clips_dir,
                )

        for job, voice_id, source in selections:
            text, reading_source = _target_text(job)
            self._prepared_inputs[_job_key(job)] = _PreparedInput(
                text=text,
                reading_source=reading_source,
                reference_selection_source=source,
                reference=clips[voice_id],
            )

        upstream_root = (
            self._upstream_root
            if self._upstream_root is not None
            else _upstream_root_from_environment()
        )
        try:
            peak = self._runtime.prepare(
                upstream_root=upstream_root,
                runtime_config_path=(
                    artifacts_dir / "runtime" / MODEL_ID / "tts-infer.yaml"
                ),
            )
        except Exception as error:
            self._raise_runtime_error("GPT-SoVITS runtime load", error)
        self._load_peak = _copy_peak(peak)
        self._prepared = True

    def generation_params(self) -> Mapping[str, Any]:
        return {
            "upstream_repository": UPSTREAM_REPOSITORY,
            "upstream_revision": UPSTREAM_REVISION,
            "weights_repository": WEIGHTS_REPOSITORY,
            "weights_revision": WEIGHTS_REVISION,
            "weight_sha256": dict(WEIGHT_FILES),
            "torch_version": TORCH_VERSION,
            "torchaudio_version": TORCHAUDIO_VERSION,
            "cuda_wheel_version": CUDA_WHEEL_VERSION,
            "device": DEVICE,
            "precision": PRECISION,
            "native_sample_rate_hz": NATIVE_SAMPLE_RATE_HZ,
            "language": "all_ja",
            "seed": SEED,
            "top_k": TOP_K,
            "top_p": TOP_P,
            "temperature": TEMPERATURE,
            "repetition_penalty": REPETITION_PENALTY,
            "text_split_method": "cut0",
            "batch_size": 1,
            "split_bucket": False,
            "parallel_infer": False,
            "reference_seconds": REFERENCE_SECONDS,
            "reference_start_frames": dict(REFERENCE_START_FRAMES),
            "reference_assignments": {
                f"{scenario}/{character}": voice
                for (scenario, character), voice in REFERENCE_ASSIGNMENTS.items()
            },
            "prompt_text_mode": "reference-free",
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
        prepared = self._prepared_input(job)
        try:
            realized = self._runtime.synthesize(
                text=prepared.text,
                reference_wav=prepared.reference.path,
                output_wav=output_wav,
            )
        except Exception as error:
            self._raise_runtime_error(
                f"GPT-SoVITS generation ({job.scenario_id}/{job.line_id})",
                error,
            )
        if not output_wav.is_file():
            raise GPTSoVITSAdapterError(
                f"adapter 出力がありません: {output_wav}",
            )
        if self._load_peak is None:
            raise GPTSoVITSAdapterError("runtime load peak がありません。")
        result = dict(realized)
        peaks = result.get("phase_peak_vram_mib")
        if not isinstance(peaks, Mapping):
            raise GPTSoVITSAdapterError(
                "runtime の phase_peak_vram_mib が不正です。",
            )
        result["phase_peak_vram_mib"] = {
            "runtime_load": _copy_peak(self._load_peak),
            **{str(name): _copy_peak(peak) for name, peak in peaks.items()},
        }
        result.update(
            {
                "reading_source": prepared.reading_source,
                "reference_selection_source": prepared.reference_selection_source,
                "reference_voice": prepared.reference.voice_id,
                "reference_source_sha256": prepared.reference.source_sha256,
                "reference_clip_sha256": prepared.reference.sha256,
                "reference_clip_start_frame": prepared.reference.start_frame,
                "reference_clip_frame_count": prepared.reference.frame_count,
            },
        )
        return result

    def _prepared_input(self, job: LineJob) -> _PreparedInput:
        if not self._prepared:
            raise GPTSoVITSAdapterError("prepare() が完了していません。")
        key = _job_key(job)
        try:
            return self._prepared_inputs[key]
        except KeyError as error:
            raise GPTSoVITSAdapterError(
                f"prepare 済み input がありません: {key[0]}/{key[1]}",
            ) from error

    def _raise_runtime_error(self, phase: str, error: BaseException) -> None:
        if self._runtime.is_out_of_memory(error):
            raise GPTSoVITSAdapterError(
                f"{phase} で CUDA out of memory が発生しました。",
            ) from error
        if isinstance(error, GPTSoVITSAdapterError):
            raise error
        raise GPTSoVITSAdapterError(f"{phase} に失敗しました: {error}") from error


def _select_reference_voice(job: LineJob) -> tuple[str, str]:
    scenario_id = _required_identifier(job.scene, "id", "scene")
    character_id = _required_identifier(job.character, "id", "character")
    value = job.character.get("reference_voice")
    if value is not None:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise GPTSoVITSAdapterError(
                "character.reference_voice は voice id または null が必要です。",
            )
        return value, "character.reference_voice"
    key = (scenario_id, character_id)
    try:
        return REFERENCE_ASSIGNMENTS[
            key
        ], f"adapter.assignment:{scenario_id}/{character_id}"
    except KeyError as error:
        raise GPTSoVITSAdapterError(
            "reference_voice が null で固定 assignment がありません: "
            f"{scenario_id}/{character_id}",
        ) from error


def _target_text(job: LineJob) -> tuple[str, str]:
    text = _required_string(job.line, "text", "line")
    reading = job.line.get("reading")
    if reading is None:
        return text, "line.text"
    if not isinstance(reading, str) or not reading.strip():
        raise GPTSoVITSAdapterError(
            "line.reading は null または空でない文字列が必要です。",
        )
    return reading, "line.reading"


def _derive_reference_clip(
    voices_dir: Path,
    entry: Mapping[str, Any],
    clips_dir: Path,
) -> _ReferenceClip:
    voice_id = str(entry["id"])
    source_path, source_sha256 = _resolve_reference_wav(voices_dir, entry)
    try:
        start_frame = REFERENCE_START_FRAMES[voice_id]
    except KeyError as error:
        raise GPTSoVITSAdapterError(
            f"reference clip window が未定義です: {voice_id}",
        ) from error

    with wave.open(str(source_path), "rb") as source:
        if (
            source.getnchannels() != 1
            or source.getsampwidth() != 2
            or source.getframerate() != REFERENCE_SAMPLE_RATE_HZ
            or source.getcomptype() != "NONE"
        ):
            raise GPTSoVITSAdapterError(
                f"reference voice は PCM16/48kHz/mono が必要です: {voice_id}",
            )
        if start_frame < 0 or source.getnframes() < start_frame + REFERENCE_FRAME_COUNT:
            raise GPTSoVITSAdapterError(
                f"reference voice は固定5秒 window を満たしません: {voice_id}",
            )
        source.setpos(start_frame)
        frames = source.readframes(REFERENCE_FRAME_COUNT)

    clip_dir = clips_dir / voice_id
    clip_dir.mkdir(parents=True, exist_ok=True)
    clip_path = clip_dir / "reference-5s.wav"
    pending_path = clip_dir / ".reference-5s.pending.wav"
    pending_path.unlink(missing_ok=True)
    try:
        with wave.open(str(pending_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(REFERENCE_SAMPLE_RATE_HZ)
            output.writeframes(frames)
        pending_path.replace(clip_path)
    finally:
        pending_path.unlink(missing_ok=True)

    return _ReferenceClip(
        voice_id=voice_id,
        source_sha256=source_sha256,
        path=clip_path,
        sha256=_sha256_file(clip_path),
        start_frame=start_frame,
        frame_count=REFERENCE_FRAME_COUNT,
    )


def _load_reference_entries(
    voices_dir: Path,
) -> dict[str, Mapping[str, Any]]:
    result = validate_voice_metadata(voices_dir)
    if result.problems:
        raise GPTSoVITSAdapterError(
            f"参照音声 metadata が不正です: {result.problems[0]}",
        )
    metadata_path = voices_dir / "metadata.yaml"
    try:
        document = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise GPTSoVITSAdapterError(
            f"参照音声 metadata を読めません: {metadata_path}: {error}",
        ) from error
    if not isinstance(document, Mapping) or not isinstance(
        document.get("voices"),
        list,
    ):
        raise GPTSoVITSAdapterError(
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
        raise GPTSoVITSAdapterError(
            f"reference voice path が不正です: {voice_id}",
        )
    expected_sha256 = entry.get("sha256")
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}",
        expected_sha256,
    ):
        raise GPTSoVITSAdapterError(
            f"reference voice SHA-256 が不正です: {voice_id}",
        )
    root = voices_dir.resolve()
    source = root / expected_file
    resolved = source.resolve()
    if source.is_symlink() or not resolved.is_relative_to(root):
        raise GPTSoVITSAdapterError(
            f"reference voice は voices 内の通常ファイルが必要です: {source}",
        )
    if not source.is_file():
        raise GPTSoVITSAdapterError(
            f"reference voice WAV がありません: {source}",
        )
    actual_sha256 = _sha256_file(source)
    if actual_sha256 != expected_sha256:
        raise GPTSoVITSAdapterError(
            "reference voice SHA-256 が一致しません: "
            f"expected={expected_sha256}, actual={actual_sha256}",
        )
    return source, expected_sha256


def _upstream_root_from_environment() -> Path:
    value = os.environ.get(UPSTREAM_ROOT_ENV)
    if value is None or not value.strip():
        raise GPTSoVITSAdapterError(
            f"{UPSTREAM_ROOT_ENV} に固定 upstream checkout を指定してください。",
        )
    return Path(value)


def _validate_upstream(root: Path) -> Path:
    root = root.resolve()
    if not root.is_dir():
        raise GPTSoVITSAdapterError(
            f"GPT-SoVITS upstream directory がありません: {root}",
        )
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        tracked_changes = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        untracked_files = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--others",
                "--exclude-standard",
                "--full-name",
                "-z",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
        ignored_files = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "--full-name",
                "-z",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise GPTSoVITSAdapterError(
            f"upstream git identity を検証できません: {error}",
        ) from error
    if revision != UPSTREAM_REVISION:
        raise GPTSoVITSAdapterError(
            "upstream revision が一致しません: "
            f"expected={UPSTREAM_REVISION}, actual={revision}",
        )
    if tracked_changes:
        raise GPTSoVITSAdapterError(
            "upstream tracked files に変更があります。",
        )
    unexpected_untracked = sorted(
        path
        for path in untracked_files.split("\0")
        if path and path not in ALLOWED_UPSTREAM_UNTRACKED
    )
    if unexpected_untracked:
        raise GPTSoVITSAdapterError(
            "upstream に許可されていない untracked file があります: "
            f"{unexpected_untracked[0]}",
        )
    unexpected_ignored = sorted(
        path
        for path in ignored_files.split("\0")
        if path and path not in ALLOWED_UPSTREAM_IGNORED
    )
    if unexpected_ignored:
        raise GPTSoVITSAdapterError(
            "upstream に許可されていない ignored file があります: "
            f"{unexpected_ignored[0]}",
        )
    _validate_japanese_dictionary_cache(root, required=False)
    return root


def _validate_japanese_dictionary_cache(
    root: Path,
    *,
    required: bool,
) -> None:
    directory = root / "GPT_SoVITS" / "text" / "ja_userdic"
    csv_path = directory / "userdict.csv"
    dictionary_path = directory / "user.dict"
    md5_path = directory / "userdict.md5"
    if not dictionary_path.exists() and not md5_path.exists() and not required:
        return
    if (
        not csv_path.is_file()
        or not dictionary_path.is_file()
        or not md5_path.is_file()
    ):
        raise GPTSoVITSAdapterError(
            "Japanese user dictionary cache が完全ではありません。",
        )
    csv_md5 = hashlib.md5(csv_path.read_bytes()).hexdigest()
    recorded_md5 = md5_path.read_text(encoding="utf-8").strip()
    if csv_md5 != USER_DICTIONARY_CSV_MD5 or recorded_md5 != csv_md5:
        raise GPTSoVITSAdapterError(
            "Japanese user dictionary source identity が一致しません。",
        )
    dictionary_sha256 = _sha256_file(dictionary_path)
    if dictionary_sha256 != USER_DICTIONARY_SHA256:
        raise GPTSoVITSAdapterError(
            "Japanese user dictionary SHA-256 が一致しません: "
            f"expected={USER_DICTIONARY_SHA256}, actual={dictionary_sha256}",
        )


def _validate_weight_files(root: Path) -> dict[str, Path]:
    model_root = root / "GPT_SoVITS" / "pretrained_models"
    paths: dict[str, Path] = {}
    for relative, expected_sha256 in WEIGHT_FILES.items():
        path = model_root.joinpath(*relative.split("/"))
        if not path.is_file():
            raise GPTSoVITSAdapterError(
                f"固定 weight file がありません: {path}",
            )
        actual_sha256 = _sha256_file(path)
        if actual_sha256 != expected_sha256:
            raise GPTSoVITSAdapterError(
                "weight SHA-256 が一致しません: "
                f"{relative}: expected={expected_sha256}, actual={actual_sha256}",
            )
        paths[relative] = path.resolve()
    return paths


def _assert_runtime_identity(
    tts: Any,
    model_paths: Mapping[str, Path],
) -> None:
    configs = getattr(tts, "configs", None)
    if configs is None:
        raise GPTSoVITSAdapterError("TTS.configs がありません。")
    expected = {
        "device": DEVICE,
        "is_half": True,
        "version": "v2ProPlus",
        "t2s_weights_path": model_paths["s1v3.ckpt"],
        "vits_weights_path": model_paths["v2Pro/s2Gv2ProPlus.pth"],
    }
    if str(getattr(configs, "device", "")) != expected["device"]:
        raise GPTSoVITSAdapterError("upstream が CUDA:0 以外へ切り替わりました。")
    if getattr(configs, "is_half", None) is not True:
        raise GPTSoVITSAdapterError("upstream が FP16 以外へ切り替わりました。")
    if getattr(configs, "version", None) != expected["version"]:
        raise GPTSoVITSAdapterError(
            "upstream model version が v2ProPlus ではありません。",
        )
    for attribute in ("t2s_weights_path", "vits_weights_path"):
        actual = Path(str(getattr(configs, attribute, ""))).resolve()
        if actual != expected[attribute]:
            raise GPTSoVITSAdapterError(
                f"upstream が別の {attribute} へ切り替わりました。",
            )


def _consume_single_result(
    results: Generator[tuple[Any, Any], None, None],
) -> tuple[Any, Any]:
    consumed = list(results)
    if len(consumed) != 1:
        raise GPTSoVITSAdapterError(
            f"GPT-SoVITS の非stream出力数が不正です: {len(consumed)}",
        )
    result = consumed[0]
    if not isinstance(result, tuple) or len(result) != 2:
        raise GPTSoVITSAdapterError("GPT-SoVITS の出力構造が不正です。")
    return result


@contextmanager
def _upstream_context(root: Path) -> Generator[None, None, None]:
    original_cwd = Path.cwd()
    original_dont_write_bytecode = sys.dont_write_bytecode
    additions = [str(root / "GPT_SoVITS"), str(root)]
    sys.path[:0] = additions
    sys.dont_write_bytecode = True
    os.chdir(root)
    try:
        yield
    finally:
        os.chdir(original_cwd)
        sys.dont_write_bytecode = original_dont_write_bytecode
        for addition in additions:
            try:
                sys.path.remove(addition)
            except ValueError:
                pass


def _require_distribution(
    distribution: str,
    expected: str,
    *,
    allow_local_suffix: bool = False,
) -> None:
    try:
        actual = metadata.version(distribution)
    except metadata.PackageNotFoundError as error:
        raise GPTSoVITSAdapterError(
            f"必須 package がありません: {distribution}=={expected}",
        ) from error
    matches = actual == expected or (
        allow_local_suffix and actual.startswith(f"{expected}+")
    )
    if not matches:
        raise GPTSoVITSAdapterError(
            "package version が一致しません: "
            f"{distribution}: expected={expected}, actual={actual}",
        )


def _peak(torch: Any) -> dict[str, float]:
    return {
        "allocated_mib": round(torch.cuda.max_memory_allocated(0) / _MIB, 3),
        "reserved_mib": round(torch.cuda.max_memory_reserved(0) / _MIB, 3),
    }


def _copy_peak(value: Mapping[str, Any]) -> dict[str, float]:
    try:
        allocated = float(value["allocated_mib"])
        reserved = float(value["reserved_mib"])
    except (KeyError, TypeError, ValueError) as error:
        raise GPTSoVITSAdapterError(
            "VRAM peak は allocated_mib/reserved_mib が必要です。",
        ) from error
    if allocated < 0 or reserved < 0:
        raise GPTSoVITSAdapterError("VRAM peak は非負である必要があります。")
    return {
        "allocated_mib": allocated,
        "reserved_mib": reserved,
    }


def _job_key(job: LineJob) -> tuple[str, str]:
    if job.locale != "ja":
        raise GPTSoVITSAdapterError(
            f"GPT-SoVITS adapter の language は Japanese 固定です: {job.locale}",
        )
    return (
        _required_identifier(job.scene, "id", "scene"),
        _required_identifier(job.line, "id", "line"),
    )


def _required_identifier(
    document: Mapping[str, Any],
    key: str,
    label: str,
) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise GPTSoVITSAdapterError(
            f"{label}.{key} は identifier が必要です: {value!r}",
        )
    return value


def _required_string(
    document: Mapping[str, Any],
    key: str,
    label: str,
) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GPTSoVITSAdapterError(
            f"{label}.{key} は空でない文字列が必要です。",
        )
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
