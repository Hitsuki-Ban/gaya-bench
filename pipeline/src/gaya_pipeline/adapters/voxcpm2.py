from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
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
from gaya_pipeline.voice_assets import validate_voice_metadata

MODEL_ID = "voxcpm2"
UPSTREAM_REPOSITORY = "OpenBMB/VoxCPM"
UPSTREAM_REVISION = "616d3d3e630a9c96c2853250eef91b0f39dcd5fa"
WEIGHTS_REPOSITORY = "openbmb/VoxCPM2"
WEIGHTS_REVISION = "bffb3df5a29440629464e5e839f4d214c8714c3d"
MODEL_ROOT_ENV = "GAYA_VOXCPM2_ROOT"
PROFILE_VERSION = (
    f"VoxCPM {UPSTREAM_REVISION}; {WEIGHTS_REPOSITORY} {WEIGHTS_REVISION}"
)

DEVICE = "cuda:0"
DTYPE = "bfloat16"
ARCHITECTURE = "voxcpm2"
SAMPLE_RATE_HZ = 48_000
VOXCPM_VERSION = "2.0.3.post22+g616d3d3e6"
TORCH_VERSION = "2.10.0+cu130"
TORCHAUDIO_VERSION = "2.10.0+cu130"
CUDA_VERSION = "13.0"
TRANSFORMERS_VERSION = "5.3.0"
SEED = 42
CFG_VALUE = 2.0
INFERENCE_TIMESTEPS = 10
REFERENCE_TEXT = "こんにちは。今日はとても良い天気ですね。"

EMOTION_INSTRUCTIONS: dict[str, str] = {
    "neutral": "neutral and natural",
    "cheerful": "cheerful and upbeat",
    "angry": "angry and forceful",
    "sad": "sad and subdued",
    "fearful": "fearful and tense",
    "surprised": "surprised and startled",
    "tired": "tired and low-energy",
    "drunk": "drunken and unsteady",
    "whisper": "whispering and quiet",
    "shout": "shouting and loud",
    "laughing": "laughing and amused",
    "pain": "in pain and strained",
}
INTENSITY_INSTRUCTIONS: dict[int, str] = {
    1: "slightly",
    2: "clearly",
    3: "strongly",
}

MODEL_FILE_SPECS: dict[str, tuple[int, str]] = {
    ".gitattributes": (
        1_519,
        "11ad7efa24975ee4b0c3c3a38ed18737f0658a5f75a0a96787b576a78a023361",
    ),
    "README.md": (
        7_776,
        "7384fad93ce2d98f47d5c3170597f3b31d414c12c92e7fdf3121fa90f19fe29d",
    ),
    "audiovae.pth": (
        376_951_122,
        "94b5d51e107e0507d4acc976cfdadb64edd6fd06d1f751dadbf2fd1594274bf1",
    ),
    "config.json": (
        4_336,
        "405f0dcd92f7feba6011ed4eac5c8d4f74cba9712f07fd5cfa3063bbdd95402c",
    ),
    "model.safetensors": (
        4_580_080_592,
        "f7f964cfa9da23653baec6e6f7750719977ad944ed9f95fe52fe3a620506891d",
    ),
    "special_tokens_map.json": (
        1_632,
        "068594063e37662c02b21acf42ebb334ef6a74fb810e68a2368f88f08351de76",
    ),
    "tokenization_voxcpm2.py": (
        2_895,
        "84489ea32b6ee0cae22ed5480cacb6df85c46624c3119be9a2021c3649a12729",
    ),
    "tokenizer.json": (
        3_676_772,
        "f8984687e4a92a3503d521396d454b7d68e9fdaab2a0288eb3536c7c1aa4bc20",
    ),
    "tokenizer_config.json": (
        5_059,
        "e78a3ebb48a0b9437efd1823b6b726c823da89e49dd8bcc90c02419d9baa772b",
    ),
}

_CACHE_FORMAT_VERSION = 1
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MIB = 1024 * 1024
_PEAK_KEYS = {"allocated_mib", "reserved_mib"}
_DESIGN_CACHE_PEAK_KEYS = {"voice_design_generate"}
_GENDER_LABELS = {
    "female": "female",
    "male": "male",
    "neutral": "gender-neutral",
}
_AGE_LABELS = {
    "child": "child",
    "teen": "teenage",
    "young_adult": "young adult",
    "adult": "adult",
    "middle_aged": "middle-aged",
    "elderly": "elderly",
}


class VoxCPM2AdapterError(RuntimeError):
    pass


class _Runtime(Protocol):
    def load_model(self, snapshot_path: Path) -> Any: ...

    def model_identity(self, model: Any) -> Mapping[str, Any]: ...

    def generate(
        self,
        model: Any,
        *,
        text: str,
        reference_wav_path: Path | None,
        seed: int,
    ) -> Any: ...

    def write_pcm16(self, path: Path, samples: Any, sample_rate: int) -> None: ...

    def reset_peak_memory_stats(self) -> None: ...

    def peak_memory_mib(self) -> Mapping[str, float]: ...

    def is_out_of_memory(self, error: BaseException) -> bool: ...


class _NativeRuntime:
    def __init__(self) -> None:
        self._torch: Any | None = None
        self._soundfile: Any | None = None
        self._model_class: Any | None = None

    def _load_dependencies(self) -> None:
        if self._torch is not None:
            return
        if sys.platform != "win32":
            raise VoxCPM2AdapterError(
                "VoxCPM2 は Windows native CUDA:0 だけをサポートします。",
            )
        try:
            torch = importlib.import_module("torch")
            torchaudio = importlib.import_module("torchaudio")
            soundfile = importlib.import_module("soundfile")
            voxcpm = importlib.import_module("voxcpm")
        except (ImportError, ModuleNotFoundError) as error:
            raise VoxCPM2AdapterError(
                f"VoxCPM2 の必須依存を import できません: {error}",
            ) from error

        versions = {
            "torch": str(torch.__version__),
            "torchaudio": str(torchaudio.__version__),
            "voxcpm": _distribution_version("voxcpm"),
            "transformers": _distribution_version("transformers"),
        }
        expected_versions = {
            "torch": TORCH_VERSION,
            "torchaudio": TORCHAUDIO_VERSION,
            "voxcpm": VOXCPM_VERSION,
            "transformers": TRANSFORMERS_VERSION,
        }
        if versions != expected_versions:
            raise VoxCPM2AdapterError(
                "VoxCPM2 runtime dependency version が一致しません: "
                f"expected={expected_versions}, actual={versions}",
            )
        actual_cuda = str(torch.version.cuda)
        if actual_cuda != CUDA_VERSION:
            raise VoxCPM2AdapterError(
                "PyTorch CUDA runtime version が一致しません: "
                f"expected={CUDA_VERSION}, actual={actual_cuda}",
            )
        if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
            raise VoxCPM2AdapterError("CUDA:0 を利用できません。")
        if not torch.cuda.is_bf16_supported():
            raise VoxCPM2AdapterError(
                "CUDA:0 は native BF16 をサポートしていません。",
            )
        try:
            model_class = voxcpm.VoxCPM
        except AttributeError as error:
            raise VoxCPM2AdapterError(
                f"VoxCPM2 の必須 API がありません: {error}",
            ) from error

        self._torch = torch
        self._soundfile = soundfile
        self._model_class = model_class

    def load_model(self, snapshot_path: Path) -> Any:
        self._load_dependencies()
        assert self._model_class is not None
        return self._model_class.from_pretrained(
            str(snapshot_path),
            load_denoiser=False,
            local_files_only=True,
            optimize=False,
            device=DEVICE,
        )

    def model_identity(self, model: Any) -> Mapping[str, Any]:
        try:
            tts_model = model.tts_model
            return {
                "architecture": (
                    ARCHITECTURE
                    if type(tts_model).__name__ == "VoxCPM2Model"
                    else type(tts_model).__name__
                ),
                "sample_rate_hz": tts_model.sample_rate,
                "device": str(tts_model.device),
                "dtype": str(tts_model.config.dtype),
            }
        except AttributeError as error:
            raise VoxCPM2AdapterError(
                f"VoxCPM2 runtime identity を取得できません: {error}",
            ) from error

    def generate(
        self,
        model: Any,
        *,
        text: str,
        reference_wav_path: Path | None,
        seed: int,
    ) -> Any:
        waveform = model.generate(
            text=text,
            reference_wav_path=(
                None if reference_wav_path is None else str(reference_wav_path)
            ),
            cfg_value=CFG_VALUE,
            inference_timesteps=INFERENCE_TIMESTEPS,
            normalize=False,
            denoise=False,
            retry_badcase=False,
            seed=seed,
        )
        actual_seed = getattr(model.tts_model, "last_successful_seed", None)
        if actual_seed != seed:
            raise VoxCPM2AdapterError(
                "VoxCPM2 realized seed が一致しません: "
                f"expected={seed}, actual={actual_seed!r}",
            )
        return waveform

    def write_pcm16(self, path: Path, samples: Any, sample_rate: int) -> None:
        self._load_dependencies()
        assert self._soundfile is not None
        self._soundfile.write(
            str(path),
            samples,
            samplerate=sample_rate,
            format="WAV",
            subtype="PCM_16",
        )

    def reset_peak_memory_stats(self) -> None:
        self._load_dependencies()
        assert self._torch is not None
        self._torch.cuda.synchronize(0)
        self._torch.cuda.reset_peak_memory_stats(0)

    def peak_memory_mib(self) -> Mapping[str, float]:
        self._load_dependencies()
        assert self._torch is not None
        self._torch.cuda.synchronize(0)
        return {
            "allocated_mib": round(
                self._torch.cuda.max_memory_allocated(0) / _MIB,
                3,
            ),
            "reserved_mib": round(
                self._torch.cuda.max_memory_reserved(0) / _MIB,
                3,
            ),
        }

    def is_out_of_memory(self, error: BaseException) -> bool:
        if self._torch is None:
            return False
        candidates = (
            getattr(self._torch, "OutOfMemoryError", None),
            getattr(self._torch.cuda, "OutOfMemoryError", None),
        )
        classes = tuple(candidate for candidate in candidates if isinstance(candidate, type))
        return bool(classes) and isinstance(error, classes)


@dataclass(frozen=True)
class _VoiceReference:
    wav_path: Path
    sha256: str
    selection_source: str
    kind: str
    voice_id: str | None
    provenance: Mapping[str, Any]
    phase_peak_vram_mib: Mapping[str, Mapping[str, float]]


@dataclass(frozen=True)
class _PreparedInput:
    source_text: str
    text: str
    reading_source: str
    emotion: str
    intensity: int
    delivery: str
    control: str
    model_text: str
    reference: _VoiceReference

    def as_generation_input(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "text": self.text,
            "reading_source": self.reading_source,
            "emotion": self.emotion,
            "intensity": self.intensity,
            "delivery": self.delivery,
            "control": self.control,
            "model_text": self.model_text,
            "reference_kind": self.reference.kind,
            "reference_selection_source": self.reference.selection_source,
            "reference_voice": self.reference.voice_id,
            "reference_sha256": self.reference.sha256,
            "reference_provenance": dict(self.reference.provenance),
        }


_T = TypeVar("_T")


class VoxCPM2Adapter:
    profile = ModelProfile(
        id=MODEL_ID,
        name="VoxCPM 2",
        version=PROFILE_VERSION,
        license_note=(
            "コード・重みは Apache-2.0。内蔵透かしなし。生成音声はAI生成と明示し、"
            "clone素材のライセンス・クレジット・再配布条件に従う。"
        ),
        capabilities=Capabilities(
            emotion=True,
            voice_prompt=True,
            clone=True,
            nonverbal=False,
            reading=False,
        ),
    )

    def take_recipe(self) -> TakeRecipe:
        return TakeRecipe(
            version="seed-only-v1",
            seed_policy="derived-sha256-v1",
            single_take_seed=SEED,
            seed_range=(0, 2**32 - 1),
            sampling=(
                ("cfg_value", CFG_VALUE),
                ("inference_timesteps", INFERENCE_TIMESTEPS),
            ),
            supports_multiple=True,
        )

    def __init__(
        self,
        *,
        runtime: _Runtime | None = None,
        model_root: Path | None = None,
    ) -> None:
        self._runtime = _NativeRuntime() if runtime is None else runtime
        self._model_root = model_root
        self._model: Any | None = None
        self._runtime_load_peak: dict[str, float] | None = None
        self._prepared_inputs: dict[tuple[str, str], _PreparedInput] = {}
        self._references: dict[tuple[str, str], _VoiceReference] = {}
        self._prepared = False

    def prepare(
        self,
        jobs: Sequence[LineJob],
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        self._prepared = False
        self._prepared_inputs.clear()
        self._references.clear()

        prepared_lines: dict[tuple[str, str], dict[str, Any]] = {}
        character_jobs: dict[tuple[str, str], LineJob] = {}
        character_references: dict[tuple[str, str], str | None] = {}
        character_identities: dict[tuple[str, str], dict[str, Any]] = {}
        has_explicit_reference = False

        for job in jobs:
            line_key = _line_key(job)
            if line_key in prepared_lines:
                raise VoxCPM2AdapterError(
                    f"同じ line job が重複しています: {line_key[0]}/{line_key[1]}",
                )
            character_key = _character_key(job)
            reference_value = _reference_voice_value(job)
            if (
                character_key in character_references
                and character_references[character_key] != reference_value
            ):
                raise VoxCPM2AdapterError(
                    "同じ scenario/character に異なる reference_voice があります: "
                    f"{character_key[0]}/{character_key[1]}",
                )
            character_references[character_key] = reference_value
            if reference_value is None:
                design_identity = _voice_design_identity(job)
                previous_identity = character_identities.get(character_key)
                if previous_identity is not None and previous_identity != design_identity:
                    raise VoxCPM2AdapterError(
                        "同じ scenario/character に異なる character 入力があります: "
                        f"{character_key[0]}/{character_key[1]}",
                    )
                character_identities[character_key] = design_identity
            character_jobs.setdefault(character_key, job)
            has_explicit_reference = has_explicit_reference or reference_value is not None
            prepared_lines[line_key] = _line_input(job)

        reference_entries = (
            _load_reference_entries(voices_dir) if has_explicit_reference else {}
        )
        missing_designs: list[tuple[str, str]] = []
        cache_paths: dict[tuple[str, str], tuple[Path, Path]] = {}
        for character_key, job in character_jobs.items():
            reference_voice = _reference_voice_value(job)
            if reference_voice is not None:
                self._references[character_key] = _explicit_reference(
                    voice_id=reference_voice,
                    voices_dir=voices_dir,
                    entries=reference_entries,
                )
                continue

            cache_dir = (
                artifacts_dir
                / "voices"
                / MODEL_ID
                / character_key[0]
                / character_key[1]
            )
            wav_path = cache_dir / "reference.wav"
            metadata_path = cache_dir / "reference.json"
            cache_paths[character_key] = (wav_path, metadata_path)
            cached = _read_voice_design_cache(
                wav_path=wav_path,
                metadata_path=metadata_path,
                identity=character_identities[character_key],
            )
            if cached is None:
                missing_designs.append(character_key)
            else:
                self._references[character_key] = cached

        for character_key in missing_designs:
            identity = character_identities[character_key]
            model = self._ensure_model()
            self._runtime.reset_peak_memory_stats()
            waveform = self._run_phase(
                f"VoiceDesign generation ({character_key[0]}/{character_key[1]})",
                lambda identity=identity: self._runtime.generate(
                    model,
                    text=str(identity["model_text"]),
                    reference_wav_path=None,
                    seed=SEED,
                ),
            )
            _validate_waveform(waveform, "VoiceDesign generation")
            generation_peak = _copy_peak(self._runtime.peak_memory_mib())
            wav_path, metadata_path = cache_paths[character_key]
            self._references[character_key] = self._write_voice_design_cache(
                wav_path=wav_path,
                metadata_path=metadata_path,
                identity=identity,
                waveform=waveform,
                phase_peaks={"voice_design_generate": generation_peak},
            )

        for job in jobs:
            line_key = _line_key(job)
            character_key = _character_key(job)
            line_input = prepared_lines[line_key]
            reference = self._references[character_key]
            self._prepared_inputs[line_key] = _PreparedInput(
                source_text=str(line_input["source_text"]),
                text=str(line_input["text"]),
                reading_source=str(line_input["reading_source"]),
                emotion=str(line_input["emotion"]),
                intensity=int(line_input["intensity"]),
                delivery=str(line_input["delivery"]),
                control=str(line_input["control"]),
                model_text=str(line_input["model_text"]),
                reference=reference,
            )

        self._prepared = True

    def generation_params(self) -> Mapping[str, Any]:
        return {
            "upstream_repository": UPSTREAM_REPOSITORY,
            "upstream_revision": UPSTREAM_REVISION,
            "weights_repository": WEIGHTS_REPOSITORY,
            "weights_revision": WEIGHTS_REVISION,
            "model_root_environment": MODEL_ROOT_ENV,
            "model_files": {
                name: {"size": size, "sha256": sha256}
                for name, (size, sha256) in MODEL_FILE_SPECS.items()
            },
            "architecture": ARCHITECTURE,
            "device": DEVICE,
            "dtype": DTYPE,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "voxcpm_version": VOXCPM_VERSION,
            "torch_version": TORCH_VERSION,
            "torchaudio_version": TORCHAUDIO_VERSION,
            "cuda_version": CUDA_VERSION,
            "transformers_version": TRANSFORMERS_VERSION,
            "load_denoiser": False,
            "optimize": False,
            "normalize": False,
            "denoise": False,
            "retry_badcase": False,
            "cfg_value": CFG_VALUE,
            "inference_timesteps": INFERENCE_TIMESTEPS,
            "reference_text": REFERENCE_TEXT,
            "emotion_instructions": dict(EMOTION_INSTRUCTIONS),
            "intensity_instructions": {
                str(intensity): instruction
                for intensity, instruction in INTENSITY_INSTRUCTIONS.items()
            },
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
        model = self._ensure_model()
        self._runtime.reset_peak_memory_stats()
        waveform = self._run_phase(
            f"Controllable Cloning generation ({job.scenario_id}/{job.line_id})",
            lambda: self._runtime.generate(
                model,
                text=prepared.model_text,
                reference_wav_path=prepared.reference.wav_path,
                seed=seed,
            ),
        )
        _validate_waveform(waveform, "Controllable Cloning generation")
        generation_peak = _copy_peak(self._runtime.peak_memory_mib())

        output_wav.parent.mkdir(parents=True, exist_ok=True)
        self._run_phase(
            "PCM16 WAV write",
            lambda: self._runtime.write_pcm16(
                output_wav,
                waveform,
                SAMPLE_RATE_HZ,
            ),
        )
        _validate_pcm16_wav(output_wav, expected_duration=None)
        if self._runtime_load_peak is None:
            raise VoxCPM2AdapterError("runtime load の CUDA peak profile がありません。")

        phase_peaks = {
            "runtime_load": _copy_peak(self._runtime_load_peak),
            **{
                phase: _copy_peak(peak)
                for phase, peak in prepared.reference.phase_peak_vram_mib.items()
            },
            "controllable_clone_generate": generation_peak,
        }
        return {
            "phase_peak_vram_mib": phase_peaks,
            "seed": seed,
            "sampling": take_context.sampling_dict(),
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "reading_source": prepared.reading_source,
            "reference_kind": prepared.reference.kind,
            "reference_selection_source": prepared.reference.selection_source,
            "reference_voice": prepared.reference.voice_id,
            "reference_sha256": prepared.reference.sha256,
            "control": prepared.control,
        }

    def _prepared_input(self, job: LineJob) -> _PreparedInput:
        if not self._prepared:
            raise VoxCPM2AdapterError("prepare() が完了していません。")
        key = _line_key(job)
        try:
            return self._prepared_inputs[key]
        except KeyError as error:
            raise VoxCPM2AdapterError(
                f"prepare 済み input がありません: {key[0]}/{key[1]}",
            ) from error

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        snapshot = (
            _model_root_from_environment()
            if self._model_root is None
            else self._model_root.resolve()
        )
        _validate_model_snapshot(snapshot)
        self._runtime.reset_peak_memory_stats()
        model = self._run_phase(
            "VoxCPM2 runtime load",
            lambda: self._runtime.load_model(snapshot),
        )
        _validate_runtime_identity(self._runtime.model_identity(model))
        self._runtime_load_peak = _copy_peak(self._runtime.peak_memory_mib())
        self._model = model
        return model

    def _run_phase(self, phase: str, action: Callable[[], _T]) -> _T:
        try:
            return action()
        except Exception as error:
            if self._runtime.is_out_of_memory(error):
                raise VoxCPM2AdapterError(
                    f"{phase} で CUDA out of memory が発生しました。",
                ) from error
            if isinstance(error, VoxCPM2AdapterError):
                raise
            raise VoxCPM2AdapterError(f"{phase} に失敗しました: {error}") from error

    def _write_voice_design_cache(
        self,
        *,
        wav_path: Path,
        metadata_path: Path,
        identity: Mapping[str, Any],
        waveform: Any,
        phase_peaks: Mapping[str, Mapping[str, float]],
    ) -> _VoiceReference:
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        pending_wav = wav_path.with_name(".reference.pending.wav")
        pending_metadata = metadata_path.with_name(".reference.pending.json")
        if pending_wav.exists() or pending_metadata.exists():
            raise VoxCPM2AdapterError(
                f"VoiceDesign cache の pending file が残っています: {wav_path.parent}",
            )
        try:
            self._runtime.write_pcm16(pending_wav, waveform, SAMPLE_RATE_HZ)
            _validate_pcm16_wav(pending_wav, expected_duration=None)
            wav_sha256 = _sha256_file(pending_wav)
            metadata = {
                "format_version": _CACHE_FORMAT_VERSION,
                "identity": dict(identity),
                "phase_peak_vram_mib": {
                    phase: _copy_peak(peak) for phase, peak in phase_peaks.items()
                },
                "wav_sha256": wav_sha256,
            }
            pending_metadata.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            pending_wav.replace(wav_path)
            pending_metadata.replace(metadata_path)
        finally:
            pending_wav.unlink(missing_ok=True)
            pending_metadata.unlink(missing_ok=True)

        return _designed_reference(
            wav_path=wav_path,
            sha256=wav_sha256,
            identity=identity,
            phase_peaks=phase_peaks,
        )


def _line_input(job: LineJob) -> dict[str, Any]:
    _line_key(job)
    source_text = _required_string(job.line, "text", "line")
    emotion = _required_string(job.line, "emotion", "line")
    try:
        emotion_instruction = EMOTION_INSTRUCTIONS[emotion]
    except KeyError as error:
        raise VoxCPM2AdapterError(
            f"未対応の line.emotion です: {emotion}",
        ) from error
    intensity = job.line.get("intensity", 2)
    if (
        not isinstance(intensity, int)
        or isinstance(intensity, bool)
        or intensity not in INTENSITY_INSTRUCTIONS
    ):
        raise VoxCPM2AdapterError(
            f"line.intensity は 1〜3 の integer が必要です: {intensity!r}",
        )
    delivery = _required_string(job.line, "delivery", "line")
    control = (
        "Speak "
        f"{INTENSITY_INSTRUCTIONS[intensity]} {emotion_instruction}. "
        f"Delivery: {delivery}"
    )
    return {
        "source_text": source_text,
        "text": source_text,
        "reading_source": "line.text",
        "emotion": emotion,
        "intensity": intensity,
        "delivery": delivery,
        "control": control,
        "model_text": f"({control}){source_text}",
    }


def _voice_design_identity(job: LineJob) -> dict[str, Any]:
    scenario_id, character_id = _character_key(job)
    gender = _required_string(job.character, "gender", "character")
    age = _required_string(job.character, "age", "character")
    try:
        gender_label = _GENDER_LABELS[gender]
    except KeyError as error:
        raise VoxCPM2AdapterError(
            f"未対応の character.gender です: {gender}",
        ) from error
    try:
        age_label = _AGE_LABELS[age]
    except KeyError as error:
        raise VoxCPM2AdapterError(
            f"未対応の character.age です: {age}",
        ) from error
    archetype = _optional_string(job.character, "archetype", "character")
    voice = _required_string(job.character, "voice", "character")
    personality = _optional_string(job.character, "personality", "character")
    instruction_parts = [
        f"Create a fictional {age_label} {gender_label} voice.",
    ]
    if archetype is not None:
        instruction_parts.append(f"Role: {archetype}.")
    instruction_parts.append(f"Voice qualities: {voice}.")
    if personality is not None:
        instruction_parts.append(f"Personality: {personality}.")
    instruction = " ".join(instruction_parts)
    return {
        "scenario": scenario_id,
        "character": character_id,
        "upstream_revision": UPSTREAM_REVISION,
        "weights_revision": WEIGHTS_REVISION,
        "gender": gender,
        "age": age,
        "archetype": archetype,
        "voice": voice,
        "personality": personality,
        "instruction": instruction,
        "reference_text": REFERENCE_TEXT,
        "model_text": f"({instruction}){REFERENCE_TEXT}",
        "seed": SEED,
        "cfg_value": CFG_VALUE,
        "inference_timesteps": INFERENCE_TIMESTEPS,
    }


def _reference_voice_value(job: LineJob) -> str | None:
    value = job.character.get("reference_voice")
    if value is None:
        return None
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise VoxCPM2AdapterError(
            "character.reference_voice は voice id または null が必要です。",
        )
    return value


def _load_reference_entries(voices_dir: Path) -> dict[str, Mapping[str, Any]]:
    validation = validate_voice_metadata(voices_dir)
    if validation.problems:
        raise VoxCPM2AdapterError(
            "参照音声 metadata が不正です: "
            + "; ".join(str(problem) for problem in validation.problems),
        )
    metadata_path = voices_dir.resolve() / "metadata.yaml"
    if metadata_path.is_symlink():
        raise VoxCPM2AdapterError(
            f"metadata.yaml は通常ファイルである必要があります: {metadata_path}",
        )
    try:
        document = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise VoxCPM2AdapterError(
            f"参照音声 metadata を読めません: {metadata_path}: {error}",
        ) from error
    if not isinstance(document, Mapping) or not isinstance(document.get("voices"), list):
        raise VoxCPM2AdapterError(
            f"参照音声 metadata の構造が不正です: {metadata_path}",
        )
    entries: dict[str, Mapping[str, Any]] = {}
    for entry in document["voices"]:
        if not isinstance(entry, Mapping):
            raise VoxCPM2AdapterError(
                f"参照音声 metadata entry が object ではありません: {metadata_path}",
            )
        voice_id = entry.get("id")
        if not isinstance(voice_id, str):
            raise VoxCPM2AdapterError(
                f"参照音声 metadata id が不正です: {metadata_path}",
            )
        if voice_id in entries:
            raise VoxCPM2AdapterError(
                f"参照音声 metadata id が重複しています: {voice_id}",
            )
        entries[voice_id] = entry
    return entries


def _explicit_reference(
    *,
    voice_id: str,
    voices_dir: Path,
    entries: Mapping[str, Mapping[str, Any]],
) -> _VoiceReference:
    try:
        entry = entries[voice_id]
    except KeyError as error:
        raise VoxCPM2AdapterError(
            f"character.reference_voice が metadata にありません: {voice_id}",
        ) from error
    expected_file = f"{voice_id}/reference.wav"
    if entry.get("file") != expected_file:
        raise VoxCPM2AdapterError(
            f"参照音声 file は {expected_file} である必要があります。",
        )
    expected_sha256 = entry.get("sha256")
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        raise VoxCPM2AdapterError(
            f"参照音声 sha256 が不正です: {voice_id}",
        )
    duration = entry.get("duration_sec")
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(float(duration))
    ):
        raise VoxCPM2AdapterError(
            f"参照音声 duration_sec が不正です: {voice_id}",
        )

    root = voices_dir.resolve()
    wav_path = root / voice_id / "reference.wav"
    resolved = wav_path.resolve()
    if wav_path.is_symlink() or not resolved.is_relative_to(root):
        raise VoxCPM2AdapterError(
            "参照音声は voices directory 内の通常ファイルである必要があります。",
        )
    _validate_pcm16_wav(wav_path, expected_duration=float(duration))
    actual_sha256 = _sha256_file(wav_path)
    if actual_sha256 != expected_sha256:
        raise VoxCPM2AdapterError(
            "参照音声 SHA-256 が一致しません: "
            f"expected={expected_sha256}, actual={actual_sha256}",
        )
    entry_sha256 = hashlib.sha256(
        json.dumps(
            entry,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
    ).hexdigest()
    return _VoiceReference(
        wav_path=wav_path,
        sha256=actual_sha256,
        selection_source="character.reference_voice",
        kind="asset",
        voice_id=voice_id,
        provenance={
            "voice_id": voice_id,
            "file": expected_file,
            "metadata_entry_sha256": entry_sha256,
            "source_wav_sha256": actual_sha256,
        },
        phase_peak_vram_mib={},
    )


def _read_voice_design_cache(
    *,
    wav_path: Path,
    metadata_path: Path,
    identity: Mapping[str, Any],
) -> _VoiceReference | None:
    pending_wav = wav_path.with_name(".reference.pending.wav")
    pending_metadata = metadata_path.with_name(".reference.pending.json")
    if pending_wav.exists() or pending_metadata.exists():
        raise VoxCPM2AdapterError(
            f"VoiceDesign cache の pending file が残っています: {wav_path.parent}",
        )
    wav_exists = wav_path.exists()
    metadata_exists = metadata_path.exists()
    if not wav_exists and not metadata_exists:
        return None
    if wav_exists != metadata_exists:
        raise VoxCPM2AdapterError(
            f"VoiceDesign cache の WAV/metadata pair が壊れています: {wav_path.parent}",
        )
    if (
        wav_path.is_symlink()
        or metadata_path.is_symlink()
        or not wav_path.is_file()
        or not metadata_path.is_file()
    ):
        raise VoxCPM2AdapterError(
            f"VoiceDesign cache は通常ファイルである必要があります: {wav_path.parent}",
        )
    try:
        cached = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VoxCPM2AdapterError(
            f"VoiceDesign cache metadata が不正です: {metadata_path}",
        ) from error
    if not isinstance(cached, dict) or set(cached) != {
        "format_version",
        "identity",
        "phase_peak_vram_mib",
        "wav_sha256",
    }:
        raise VoxCPM2AdapterError(
            f"VoiceDesign cache metadata の構造が不正です: {metadata_path}",
        )
    if cached["format_version"] != _CACHE_FORMAT_VERSION:
        raise VoxCPM2AdapterError(
            f"VoiceDesign cache format_version が不正です: {metadata_path}",
        )
    cached_identity = cached["identity"]
    if not isinstance(cached_identity, dict):
        raise VoxCPM2AdapterError(
            f"VoiceDesign cache identity が不正です: {metadata_path}",
        )
    peaks = cached["phase_peak_vram_mib"]
    if (
        not isinstance(peaks, dict)
        or set(peaks) != _DESIGN_CACHE_PEAK_KEYS
        or not all(_valid_peak(peak) for peak in peaks.values())
    ):
        raise VoxCPM2AdapterError(
            f"VoiceDesign cache CUDA peak profile が不正です: {metadata_path}",
        )
    wav_sha256 = cached["wav_sha256"]
    if not isinstance(wav_sha256, str) or _SHA256.fullmatch(wav_sha256) is None:
        raise VoxCPM2AdapterError(
            f"VoiceDesign cache wav_sha256 が不正です: {metadata_path}",
        )
    _validate_pcm16_wav(wav_path, expected_duration=None)
    actual_sha256 = _sha256_file(wav_path)
    if actual_sha256 != wav_sha256:
        raise VoxCPM2AdapterError(
            f"VoiceDesign cache WAV SHA-256 が一致しません: {wav_path}",
        )
    if cached_identity != dict(identity):
        return None
    return _designed_reference(
        wav_path=wav_path,
        sha256=wav_sha256,
        identity=identity,
        phase_peaks=peaks,
    )


def _designed_reference(
    *,
    wav_path: Path,
    sha256: str,
    identity: Mapping[str, Any],
    phase_peaks: Mapping[str, Mapping[str, float]],
) -> _VoiceReference:
    return _VoiceReference(
        wav_path=wav_path,
        sha256=sha256,
        selection_source="adapter.voice_design",
        kind="voice_design",
        voice_id=None,
        provenance={
            "cache_format_version": _CACHE_FORMAT_VERSION,
            "identity": dict(identity),
            "reference_wav_sha256": sha256,
        },
        phase_peak_vram_mib={
            phase: _copy_peak(peak) for phase, peak in phase_peaks.items()
        },
    )


def _validate_model_snapshot(snapshot_path: Path) -> None:
    if snapshot_path.is_symlink() or not snapshot_path.is_dir():
        raise VoxCPM2AdapterError(
            f"model snapshot は通常 directory である必要があります: {snapshot_path}",
        )
    entries = list(snapshot_path.iterdir())
    cache_dir = snapshot_path / ".cache"
    if cache_dir.exists() and (cache_dir.is_symlink() or not cache_dir.is_dir()):
        raise VoxCPM2AdapterError(
            f"model snapshot .cache は通常 directory である必要があります: {cache_dir}",
        )
    unexpected_directories = sorted(
        entry.name
        for entry in entries
        if entry.name != ".cache" and entry.is_dir()
    )
    if unexpected_directories:
        raise VoxCPM2AdapterError(
            "model snapshot に未許可 directory があります: "
            f"{unexpected_directories}",
        )
    file_entries = [entry for entry in entries if entry.name != ".cache"]
    actual_names = {entry.name for entry in file_entries}
    expected_names = set(MODEL_FILE_SPECS)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        unexpected = sorted(actual_names - expected_names)
        raise VoxCPM2AdapterError(
            "model snapshot allowlist が一致しません: "
            f"missing={missing}, unexpected={unexpected}",
        )
    for entry in file_entries:
        if entry.is_symlink() or not entry.is_file():
            raise VoxCPM2AdapterError(
                f"model snapshot entry は通常ファイルである必要があります: {entry}",
            )
        expected_size, expected_sha256 = MODEL_FILE_SPECS[entry.name]
        actual_size = entry.stat().st_size
        if actual_size != expected_size:
            raise VoxCPM2AdapterError(
                f"model file size が一致しません: {entry.name}: "
                f"expected={expected_size}, actual={actual_size}",
            )
        actual_sha256 = _sha256_file(entry)
        if actual_sha256 != expected_sha256:
            raise VoxCPM2AdapterError(
                f"model file SHA-256 が一致しません: {entry.name}: "
                f"expected={expected_sha256}, actual={actual_sha256}",
            )

    try:
        config = json.loads((snapshot_path / "config.json").read_text(encoding="utf-8"))
        architecture = config["architecture"]
        audio_vae = config["audio_vae_config"]
        device = config["device"]
        dtype = config["dtype"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise VoxCPM2AdapterError(
            f"model config.json の構造が不正です: {error}",
        ) from error
    if architecture != ARCHITECTURE:
        raise VoxCPM2AdapterError(
            f"model architecture が一致しません: {architecture}",
        )
    if (
        not isinstance(audio_vae, Mapping)
        or audio_vae.get("sample_rate") != 16_000
        or audio_vae.get("out_sample_rate") != SAMPLE_RATE_HZ
    ):
        raise VoxCPM2AdapterError("model AudioVAE sample rate が一致しません。")
    if device != "cuda" or dtype != DTYPE:
        raise VoxCPM2AdapterError(
            f"model runtime config が一致しません: device={device}, dtype={dtype}",
        )


def _model_root_from_environment() -> Path:
    value = os.environ.get(MODEL_ROOT_ENV)
    if value is None or not value.strip():
        raise VoxCPM2AdapterError(
            f"環境変数 {MODEL_ROOT_ENV} に固定 model snapshot directory が必要です。",
        )
    root = Path(value).resolve()
    if not root.is_dir():
        raise VoxCPM2AdapterError(
            f"{MODEL_ROOT_ENV} の directory が存在しません: {root}",
        )
    return root


def _distribution_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError as error:
        raise VoxCPM2AdapterError(
            f"必須 distribution がインストールされていません: {name}",
        ) from error


def _validate_runtime_identity(identity: Mapping[str, Any]) -> None:
    expected = {
        "architecture": ARCHITECTURE,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "device": DEVICE,
        "dtype": DTYPE,
    }
    if not isinstance(identity, Mapping) or dict(identity) != expected:
        raise VoxCPM2AdapterError(
            f"VoxCPM2 runtime identity が一致しません: expected={expected}, "
            f"actual={dict(identity) if isinstance(identity, Mapping) else identity}",
        )


def _validate_waveform(waveform: Any, phase: str) -> None:
    shape = getattr(waveform, "shape", None)
    is_float_array = shape is not None
    if shape is not None:
        try:
            dimensions = tuple(int(value) for value in shape)
        except (TypeError, ValueError) as error:
            raise VoxCPM2AdapterError(
                f"{phase} の waveform shape が不正です。",
            ) from error
        if len(dimensions) != 1 or dimensions[0] <= 0:
            raise VoxCPM2AdapterError(
                f"{phase} は非空の単一 waveform を返す必要があります: {dimensions}",
            )
        dtype = getattr(waveform, "dtype", None)
        if getattr(dtype, "kind", None) != "f":
            raise VoxCPM2AdapterError(
                f"{phase} の waveform は float である必要があります: {dtype}",
            )
    if isinstance(waveform, (str, bytes, bytearray)) or not hasattr(
        waveform,
        "__iter__",
    ):
        raise VoxCPM2AdapterError(
            f"{phase} の waveform は float sequence である必要があります。",
        )
    count = 0
    for sample in waveform:
        if is_float_array:
            try:
                finite = math.isfinite(float(sample))
            except (TypeError, ValueError):
                finite = False
        else:
            finite = isinstance(sample, float) and math.isfinite(sample)
        if not finite:
            raise VoxCPM2AdapterError(
                f"{phase} の waveform に有限でない float sample があります。",
            )
        count += 1
    if count == 0:
        raise VoxCPM2AdapterError(
            f"{phase} の waveform は空にできません。",
        )


def _validate_pcm16_wav(path: Path, *, expected_duration: float | None) -> None:
    if path.is_symlink() or not path.is_file():
        raise VoxCPM2AdapterError(
            f"PCM16 WAV が通常ファイルではありません: {path}",
        )
    try:
        with wave.open(str(path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            compression = wav_file.getcomptype()
    except (OSError, EOFError, wave.Error) as error:
        raise VoxCPM2AdapterError(f"WAV を検証できません: {path}: {error}") from error
    if (
        channels != 1
        or sample_width != 2
        or sample_rate != SAMPLE_RATE_HZ
        or frame_count <= 0
        or compression != "NONE"
    ):
        raise VoxCPM2AdapterError(
            "WAV は mono / 48 kHz / PCM16 / non-empty である必要があります: "
            f"{path}",
        )
    if expected_duration is not None:
        actual_duration = frame_count / sample_rate
        if not 10.0 <= actual_duration <= 20.0:
            raise VoxCPM2AdapterError(
                f"参照音声 duration は 10〜20 秒である必要があります: {actual_duration}",
            )
        if abs(actual_duration - expected_duration) > 0.001:
            raise VoxCPM2AdapterError(
                "参照音声 duration が metadata と一致しません: "
                f"expected={expected_duration}, actual={actual_duration}",
            )


def _valid_peak(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != _PEAK_KEYS:
        return False
    return all(
        isinstance(measurement, (int, float))
        and not isinstance(measurement, bool)
        and math.isfinite(float(measurement))
        and measurement >= 0
        for measurement in value.values()
    )


def _copy_peak(value: Mapping[str, Any]) -> dict[str, float]:
    if not _valid_peak(value):
        raise VoxCPM2AdapterError(f"不正な CUDA peak profile です: {value}")
    return {
        "allocated_mib": float(value["allocated_mib"]),
        "reserved_mib": float(value["reserved_mib"]),
    }


def _line_key(job: LineJob) -> tuple[str, str]:
    if job.locale != "ja":
        raise VoxCPM2AdapterError(
            f"VoxCPM2 adapter の locale は ja 固定です: {job.locale}",
        )
    return (
        _required_identifier(job.scene, "id", "scene"),
        _required_identifier(job.line, "id", "line"),
    )


def _character_key(job: LineJob) -> tuple[str, str]:
    if job.locale != "ja":
        raise VoxCPM2AdapterError(
            f"VoxCPM2 adapter の locale は ja 固定です: {job.locale}",
        )
    return (
        _required_identifier(job.scene, "id", "scene"),
        _required_identifier(job.character, "id", "character"),
    )


def _required_string(
    value: Mapping[str, Any],
    key: str,
    owner: str,
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise VoxCPM2AdapterError(
            f"{owner}.{key} は non-empty string が必要です。",
        )
    return item


def _required_identifier(
    value: Mapping[str, Any],
    key: str,
    owner: str,
) -> str:
    identifier = _required_string(value, key, owner)
    if _IDENTIFIER.fullmatch(identifier) is None:
        raise VoxCPM2AdapterError(
            f"{owner}.{key} が identifier 形式ではありません: {identifier}",
        )
    return identifier


def _optional_string(
    value: Mapping[str, Any],
    key: str,
    owner: str,
) -> str | None:
    if key not in value:
        return None
    item = value[key]
    if not isinstance(item, str):
        raise VoxCPM2AdapterError(
            f"{owner}.{key} は string が必要です。",
        )
    return item if item.strip() else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
