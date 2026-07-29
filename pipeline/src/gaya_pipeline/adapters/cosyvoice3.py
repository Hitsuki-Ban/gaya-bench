from __future__ import annotations

import hashlib
import importlib
import math
import os
import random
import re
import subprocess
import sys
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

import yaml

from gaya_pipeline.adapters.base import (
    Capabilities,
    LineJob,
    ModelProfile,
    TakeContext,
    TakeRecipe,
    require_take_context,
)
from gaya_pipeline.japanese_reading import (
    JapaneseReadingError,
    resolve_japanese_reading,
)
from gaya_pipeline.voice_assets import validate_voice_metadata

MODEL_ID = "cosyvoice3-0.5b-2512"
MODEL_NAME = "CosyVoice 3 0.5B 2512"
UPSTREAM_REPOSITORY = "QwenAudio/CosyVoice"
UPSTREAM_REVISION = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
MATCHA_REPOSITORY = "shivammehta25/Matcha-TTS"
MATCHA_REVISION = "dd9105b34bf2be2230f4aa1e4769fb586a3c824e"
WEIGHTS_REPOSITORY = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
WEIGHTS_REVISION = "29e01c4e8d000f4bcd70751be16fa94bf3d85a18"
CODE_ROOT_ENV = "GAYA_COSYVOICE_CODE_ROOT"
MODEL_ROOT_ENV = "GAYA_COSYVOICE3_MODEL_ROOT"
PROFILE_VERSION = (
    f"CosyVoice {UPSTREAM_REVISION}; "
    f"Matcha-TTS {MATCHA_REVISION}; "
    f"{WEIGHTS_REPOSITORY} {WEIGHTS_REVISION}"
)

ARCHITECTURE = "CosyVoice3"
MODEL_ARCHITECTURE = "CosyVoice3Model"
DEVICE = "cuda:0"
SAMPLE_RATE_HZ = 24_000
REFERENCE_SAMPLE_RATE_HZ = 48_000
REFERENCE_MAX_SECONDS = 30
SEED = 1986
FP16 = True
LOAD_TRT = False
LOAD_VLLM = False
STREAM = False
SPEED = 1.0
TEXT_FRONTEND = False
ZERO_SHOT_SPEAKER_ID = ""

TORCH_VERSION = "2.3.1+cu121"
TORCHAUDIO_VERSION = "2.3.1+cu121"
CUDA_VERSION = "12.1"
ONNXRUNTIME_GPU_VERSION = "1.18.0"
TRANSFORMERS_VERSION = "4.51.3"
NUMPY_VERSION = "1.26.4"
SOUNDFILE_VERSION = "0.12.1"
MODELSCOPE_VERSION = "1.20.0"

OFFLINE_ENVIRONMENT: Mapping[str, str] = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "MODELSCOPE_OFFLINE": "1",
}

MODEL_FILE_SPECS: Mapping[str, tuple[int, str]] = {
    "CosyVoice-BlankEN/config.json": (
        659,
        "168aa1bd401abc3bc262ba15ba4e499627a8b4e006e9d050b47c22de20660185",
    ),
    "CosyVoice-BlankEN/generation_config.json": (
        242,
        "e558847a8b4402616f1273797b015104dc266fe4b520056fca88823ba8f8ebe6",
    ),
    "CosyVoice-BlankEN/merges.txt": (
        1_402_109,
        "ac8ff86a72bee70828fbc1119bc4398c6f3a9a6e490d7b0dbe917be025478bd0",
    ),
    "CosyVoice-BlankEN/model.safetensors": (
        988_097_824,
        "130282af0dfa9fe5840737cc49a0d339d06075f83c5a315c3372c9a0740d0b96",
    ),
    "CosyVoice-BlankEN/tokenizer_config.json": (
        1_287,
        "482bd979881423375ca5414e4e0d94cd7c5349dbb17fffd46b4d36d71e62a1bc",
    ),
    "CosyVoice-BlankEN/vocab.json": (
        2_776_833,
        "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
    ),
    "campplus.onnx": (
        28_303_423,
        "a6ac6a63997761ae2997373e2ee1c47040854b4b759ea41ec48e4e42df0f4d73",
    ),
    "cosyvoice3.yaml": (
        6_934,
        "f5a6b2c6f05139d0f18861a1fe506f751e787026b77c05f7e8fef9f8a4405965",
    ),
    "flow.pt": (
        1_329_116_148,
        "a6fab32a7825e5b0bc855ddd948f8db9370b0a786fbc249caa4595e95b608e4b",
    ),
    "hift.pt": (
        83_202_622,
        "b279d7641eb97ae55b3b540cfba4f953c26492a2df758328a89a4d007ab87a65",
    ),
    "llm.pt": (
        2_024_669_519,
        "69f43bd545131c30e98947fb360ea8b4dc9916d8e83dded7757c7ea4f5a24970",
    ),
    "speech_tokenizer_v3.onnx": (
        969_451_503,
        "23236a74175dbdda47afc66dbadd5bcb41303c467a57c261cb8539ad9db9208d",
    ),
}

REFERENCE_ASSIGNMENTS: Mapping[tuple[str, str], str] = {
    ("tavern-night", "drunkard"): "hadou-emotion-11",
    ("tavern-night", "old-regular"): "hadou-emotion-11",
    ("market-day", "fruit-vendor"): "hadou-emotion-11",
    ("market-day", "shopper"): "lux-emotion-76",
    ("market-day", "street-kid"): "tsukuyomi-corpus-94",
}

INSTRUCTION_END = "<|endofprompt|>"
INSTRUCTION_POLICY_VERSION = "fixed-emotion-template-v1"
EMOTION_INSTRUCTION_TEMPLATES: Mapping[str, str] = {
    "neutral": f"You are a helpful assistant.{INSTRUCTION_END}",
    "cheerful": (
        "You are a helpful assistant. "
        f"请非常开心地说一句话。{INSTRUCTION_END}"
    ),
    "angry": (
        "You are a helpful assistant. "
        f"请非常生气地说一句话。{INSTRUCTION_END}"
    ),
    "sad": (
        "You are a helpful assistant. "
        f"请非常伤心地说一句话。{INSTRUCTION_END}"
    ),
    "fearful": (
        "You are a helpful assistant. "
        f"请害怕地说一句话。{INSTRUCTION_END}"
    ),
    "surprised": (
        "You are a helpful assistant. "
        f"请惊讶地说一句话。{INSTRUCTION_END}"
    ),
    "tired": (
        "You are a helpful assistant. "
        f"请用疲惫的语气说一句话。{INSTRUCTION_END}"
    ),
    "drunk": (
        "You are a helpful assistant. "
        f"请用醉酒的语气说一句话。{INSTRUCTION_END}"
    ),
    "whisper": (
        "You are a helpful assistant. "
        f"Please say a sentence in a very soft voice.{INSTRUCTION_END}"
    ),
    "shout": (
        "You are a helpful assistant. "
        f"Please say a sentence as loudly as possible.{INSTRUCTION_END}"
    ),
    "laughing": (
        "You are a helpful assistant. "
        f"请笑着说一句话。{INSTRUCTION_END}"
    ),
    "pain": (
        "You are a helpful assistant. "
        f"请用痛苦的语气说一句话。{INSTRUCTION_END}"
    ),
}

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MIB = 1024 * 1024


class CosyVoice3AdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Reference:
    voice_id: str
    selection_source: str
    wav_path: Path
    sha256: str
    samples: int
    duration_sec: float


@dataclass(frozen=True)
class _PreparedInput:
    source_text: str
    tts_text: str
    reading_source: str
    emotion: str
    intensity: int
    delivery: str
    instruction_template_id: str
    instruction: str
    reference: _Reference

    def as_generation_input(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "tts_text": self.tts_text,
            "reading_source": self.reading_source,
            "instruction": self.instruction,
            "emotion": self.emotion,
            "intensity": self.intensity,
            "delivery": self.delivery,
            "instruction_policy_version": INSTRUCTION_POLICY_VERSION,
            "instruction_template_id": self.instruction_template_id,
            "reference_selection_source": self.reference.selection_source,
            "reference_voice": self.reference.voice_id,
            "reference_sha256": self.reference.sha256,
        }


class _Runtime(Protocol):
    def load_model(self, code_root: Path, model_root: Path) -> Any: ...

    def model_identity(self, model: Any) -> Mapping[str, Any]: ...

    def synthesize(
        self,
        model: Any,
        *,
        tts_text: str,
        instruction: str,
        reference_wav: Path,
        seed: int,
    ) -> Sequence[Mapping[str, Any]]: ...

    def concatenate_waveforms(self, waveforms: Sequence[Any]) -> Any: ...

    def write_pcm16(self, path: Path, waveform: Any, sample_rate: int) -> None: ...

    def reset_peak_memory_stats(self) -> None: ...

    def peak_memory_mib(self) -> Mapping[str, float]: ...

    def is_out_of_memory(self, error: BaseException) -> bool: ...


class _NativeRuntime:
    def __init__(self) -> None:
        self._torch: Any | None = None
        self._numpy: Any | None = None
        self._soundfile: Any | None = None
        self._auto_model: Any | None = None
        self._cosyvoice_module: Any | None = None

    def _load_dependencies(self, code_root: Path) -> None:
        if self._torch is not None:
            return
        if sys.version_info[:2] != (3, 12):
            raise CosyVoice3AdapterError(
                "CosyVoice 3 は Python 3.12 だけをサポートします。",
            )
        if sys.platform != "win32":
            raise CosyVoice3AdapterError(
                "CosyVoice 3 は Windows native CUDA:0 だけをサポートします。",
            )

        _set_offline_environment()
        try:
            torch = importlib.import_module("torch")
            torchaudio = importlib.import_module("torchaudio")
        except (ImportError, ModuleNotFoundError) as error:
            raise CosyVoice3AdapterError(
                f"PyTorch runtime を import できません: {error}",
            ) from error

        actual_versions = {
            "torch": str(torch.__version__),
            "torchaudio": str(torchaudio.__version__),
        }
        expected_versions = {
            "torch": TORCH_VERSION,
            "torchaudio": TORCHAUDIO_VERSION,
        }
        for distribution, expected in expected_versions.items():
            actual = actual_versions[distribution]
            if actual != expected:
                raise CosyVoice3AdapterError(
                    "package version が一致しません: "
                    f"{distribution}={actual}, expected={expected}",
                )
        if str(torch.version.cuda) != CUDA_VERSION:
            raise CosyVoice3AdapterError(
                "PyTorch CUDA version が一致しません: "
                f"{torch.version.cuda}, expected={CUDA_VERSION}",
            )
        _preload_cuda_zero(torch)
        _configure_deterministic_cuda(torch)

        try:
            onnxruntime = importlib.import_module("onnxruntime")
            numpy = importlib.import_module("numpy")
            soundfile = importlib.import_module("soundfile")
            transformers = importlib.import_module("transformers")
        except (ImportError, ModuleNotFoundError) as error:
            raise CosyVoice3AdapterError(
                f"CosyVoice runtime の必須依存を import できません: {error}",
            ) from error

        dependency_versions = {
            "onnxruntime-gpu": _distribution_version("onnxruntime-gpu"),
            "transformers": str(transformers.__version__),
            "numpy": str(numpy.__version__),
            "soundfile": _distribution_version("soundfile"),
            "modelscope": _distribution_version("modelscope"),
        }
        expected_dependency_versions = {
            "onnxruntime-gpu": ONNXRUNTIME_GPU_VERSION,
            "transformers": TRANSFORMERS_VERSION,
            "numpy": NUMPY_VERSION,
            "soundfile": SOUNDFILE_VERSION,
            "modelscope": MODELSCOPE_VERSION,
        }
        for distribution, expected in expected_dependency_versions.items():
            actual = dependency_versions[distribution]
            if actual != expected:
                raise CosyVoice3AdapterError(
                    "package version が一致しません: "
                    f"{distribution}={actual}, expected={expected}",
                )
        _validate_onnxruntime_installation(onnxruntime)

        matcha_root = code_root / "third_party" / "Matcha-TTS"
        for path in (matcha_root, code_root):
            text = str(path)
            if text not in sys.path:
                sys.path.insert(0, text)

        try:
            cosyvoice_module = importlib.import_module("cosyvoice.cli.cosyvoice")
            matcha_module = importlib.import_module("matcha")
        except (ImportError, ModuleNotFoundError) as error:
            raise CosyVoice3AdapterError(
                f"固定 CosyVoice source を import できません: {error}",
            ) from error
        _validate_module_origin(
            cosyvoice_module,
            code_root,
            "cosyvoice.cli.cosyvoice",
        )
        _validate_module_origin(matcha_module, matcha_root, "matcha")

        self._torch = torch
        self._numpy = numpy
        self._soundfile = soundfile
        self._auto_model = cosyvoice_module.AutoModel
        self._cosyvoice_module = cosyvoice_module

    def load_model(self, code_root: Path, model_root: Path) -> Any:
        self._load_dependencies(code_root)
        if self._auto_model is None:
            raise CosyVoice3AdapterError("CosyVoice runtime が初期化されていません。")
        _set_offline_environment()
        self.reset_peak_memory_stats()
        return self._auto_model(
            model_dir=str(model_root),
            load_trt=LOAD_TRT,
            load_vllm=LOAD_VLLM,
            fp16=FP16,
        )

    def model_identity(self, model: Any) -> Mapping[str, Any]:
        frontend = model.frontend
        engine = model.model
        return {
            "architecture": type(model).__name__,
            "model_architecture": type(engine).__name__,
            "sample_rate_hz": int(model.sample_rate),
            "fp16": bool(model.fp16),
            "frontend_text_frontend": str(frontend.text_frontend),
            "frontend_device": str(frontend.device),
            "llm_device": str(next(engine.llm.parameters()).device),
            "flow_device": str(next(engine.flow.parameters()).device),
            "hift_device": str(next(engine.hift.parameters()).device),
            "speech_tokenizer_providers": list(
                frontend.speech_tokenizer_session.get_providers(),
            ),
            "campplus_providers": list(
                frontend.campplus_session.get_providers(),
            ),
        }

    def synthesize(
        self,
        model: Any,
        *,
        tts_text: str,
        instruction: str,
        reference_wav: Path,
        seed: int,
    ) -> Sequence[Mapping[str, Any]]:
        if self._torch is None or self._numpy is None:
            raise CosyVoice3AdapterError("CosyVoice runtime が初期化されていません。")
        _seed_every_line(self._torch, self._numpy, seed)
        return list(
            model.inference_instruct2(
                tts_text,
                instruction,
                str(reference_wav),
                zero_shot_spk_id=ZERO_SHOT_SPEAKER_ID,
                stream=STREAM,
                speed=SPEED,
                text_frontend=TEXT_FRONTEND,
            ),
        )

    def concatenate_waveforms(self, waveforms: Sequence[Any]) -> Any:
        if self._torch is None:
            raise CosyVoice3AdapterError("CosyVoice runtime が初期化されていません。")
        return self._torch.cat(list(waveforms), dim=1)

    def write_pcm16(self, path: Path, waveform: Any, sample_rate: int) -> None:
        if self._soundfile is None:
            raise CosyVoice3AdapterError("CosyVoice runtime が初期化されていません。")
        values = waveform.detach().cpu().float().reshape(-1).numpy()
        self._soundfile.write(
            str(path),
            values,
            sample_rate,
            subtype="PCM_16",
            format="WAV",
        )

    def reset_peak_memory_stats(self) -> None:
        if self._torch is None:
            raise CosyVoice3AdapterError(
                "CUDA peak 計測前に runtime が初期化されていません。",
            )
        self._torch.cuda.reset_peak_memory_stats()

    def peak_memory_mib(self) -> Mapping[str, float]:
        if self._torch is None:
            raise CosyVoice3AdapterError(
                "CUDA peak 計測前に runtime が初期化されていません。",
            )
        return {
            "allocated_mib": (
                float(self._torch.cuda.max_memory_allocated()) / _MIB
            ),
            "reserved_mib": (
                float(self._torch.cuda.max_memory_reserved()) / _MIB
            ),
        }

    def is_out_of_memory(self, error: BaseException) -> bool:
        if self._torch is not None:
            classes = tuple(
                error_type
                for error_type in (
                    getattr(self._torch, "OutOfMemoryError", None),
                    getattr(self._torch.cuda, "OutOfMemoryError", None),
                )
                if isinstance(error_type, type)
            )
            if classes and isinstance(error, classes):
                return True
        return "out of memory" in str(error).lower()


class CosyVoice3Adapter:
    profile = ModelProfile(
        id=MODEL_ID,
        name=MODEL_NAME,
        version=PROFILE_VERSION,
        license_note=(
            "CosyVoice コードと Fun-CosyVoice3-0.5B-2512 公式重みは "
            "Apache-2.0。生成音声の権利条件は公式資料に明記されていない。"
            "公式ソースは電子透かしを開示していない。参照音声の利用規約・"
            "クレジット・再配布条件にも従い、無断の声真似や誤認を招く利用を"
            "禁止する。"
        ),
        capabilities=Capabilities(
            emotion=True,
            voice_prompt=False,
            clone=True,
            nonverbal=False,
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
                ("speed", SPEED),
                ("stream", STREAM),
            ),
            supports_multiple=True,
        )

    def __init__(self, *, runtime: _Runtime | None = None) -> None:
        self._runtime = runtime if runtime is not None else _NativeRuntime()
        self._model: Any | None = None
        self._model_identity: dict[str, Any] | None = None
        self._runtime_load_peak: dict[str, float] | None = None
        self._prepared_inputs: dict[tuple[str, str], _PreparedInput] = {}
        self._prepared = False

    def prepare(
        self,
        jobs: Sequence[LineJob],
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        del artifacts_dir
        self._prepared = False
        self._prepared_inputs.clear()

        entries = _load_reference_entries(voices_dir)
        references: dict[tuple[str, str], _Reference] = {}
        for job in jobs:
            key = _job_key(job)
            if key in self._prepared_inputs:
                raise CosyVoice3AdapterError(
                    f"同じ line job が重複しています: {key[0]}/{key[1]}",
                )
            line_input = _line_input(job)
            voice_id, selection_source = _select_reference_voice(job)
            reference_key = (voice_id, selection_source)
            reference = references.get(reference_key)
            if reference is None:
                try:
                    entry = entries[voice_id]
                except KeyError as error:
                    raise CosyVoice3AdapterError(
                        f"未登録の reference_voice です: {voice_id}",
                    ) from error
                reference = _explicit_reference(
                    voice_id=voice_id,
                    selection_source=selection_source,
                    voices_dir=voices_dir,
                    entry=entry,
                )
                references[reference_key] = reference
            self._prepared_inputs[key] = _PreparedInput(
                source_text=line_input["source_text"],
                tts_text=line_input["tts_text"],
                reading_source=line_input["reading_source"],
                emotion=line_input["emotion"],
                intensity=line_input["intensity"],
                delivery=line_input["delivery"],
                instruction_template_id=line_input["instruction_template_id"],
                instruction=line_input["instruction"],
                reference=reference,
            )
        self._prepared = True

    def generation_params(self) -> Mapping[str, Any]:
        return {
            "upstream_repository": UPSTREAM_REPOSITORY,
            "upstream_revision": UPSTREAM_REVISION,
            "matcha_repository": MATCHA_REPOSITORY,
            "matcha_revision": MATCHA_REVISION,
            "weights_repository": WEIGHTS_REPOSITORY,
            "weights_revision": WEIGHTS_REVISION,
            "code_root_environment": CODE_ROOT_ENV,
            "model_root_environment": MODEL_ROOT_ENV,
            "model_files": {
                name: {"size": size, "sha256": sha256}
                for name, (size, sha256) in MODEL_FILE_SPECS.items()
            },
            "architecture": ARCHITECTURE,
            "model_architecture": MODEL_ARCHITECTURE,
            "device": DEVICE,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "torch_version": TORCH_VERSION,
            "torchaudio_version": TORCHAUDIO_VERSION,
            "cuda_version": CUDA_VERSION,
            "onnxruntime_gpu_version": ONNXRUNTIME_GPU_VERSION,
            "transformers_version": TRANSFORMERS_VERSION,
            "numpy_version": NUMPY_VERSION,
            "soundfile_version": SOUNDFILE_VERSION,
            "modelscope_version": MODELSCOPE_VERSION,
            "fp16": FP16,
            "load_trt": LOAD_TRT,
            "load_vllm": LOAD_VLLM,
            "stream": STREAM,
            "speed": SPEED,
            "text_frontend": TEXT_FRONTEND,
            "zero_shot_speaker_id": ZERO_SHOT_SPEAKER_ID,
            "offline_environment": dict(OFFLINE_ENVIRONMENT),
            "instruction_policy_version": INSTRUCTION_POLICY_VERSION,
            "emotion_instruction_templates": dict(
                EMOTION_INSTRUCTION_TEMPLATES,
            ),
            "instruction_end": INSTRUCTION_END,
            "reference_assignments": {
                f"{scenario_id}/{character_id}": voice_id
                for (scenario_id, character_id), voice_id in (
                    REFERENCE_ASSIGNMENTS.items()
                )
            },
            "watermark_disclosed_by_official_source": False,
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
        chunks = self._run_phase(
            f"CosyVoice generation ({job.scenario_id}/{job.line_id})",
            lambda: self._runtime.synthesize(
                model,
                tts_text=prepared.tts_text,
                instruction=prepared.instruction,
                reference_wav=prepared.reference.wav_path,
                seed=seed,
            ),
        )
        waveforms, expected_samples = _validated_chunks(chunks)
        waveform = self._run_phase(
            "CosyVoice chunk concatenation",
            lambda: self._runtime.concatenate_waveforms(waveforms),
        )
        actual_samples = _validate_waveform(waveform)
        if actual_samples != expected_samples:
            raise CosyVoice3AdapterError(
                "CosyVoice chunk 結合後の sample 数が一致しません: "
                f"{actual_samples}, expected={expected_samples}",
            )
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
        _validate_pcm16_wav(output_wav, expected_samples=actual_samples)
        if self._runtime_load_peak is None or self._model_identity is None:
            raise CosyVoice3AdapterError(
                "runtime load の監査情報がありません。",
            )
        return {
            "phase_peak_vram_mib": {
                "runtime_load": _copy_peak(self._runtime_load_peak),
                "generation": generation_peak,
            },
            "providers": {
                "speech_tokenizer": list(
                    self._model_identity["speech_tokenizer_providers"],
                ),
                "campplus": list(
                    self._model_identity["campplus_providers"],
                ),
            },
            "seed": seed,
            "sampling": take_context.sampling_dict(),
            "fp16": FP16,
            "stream": STREAM,
            "speed": SPEED,
            "text_frontend": TEXT_FRONTEND,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "samples": actual_samples,
            "duration_sec": actual_samples / SAMPLE_RATE_HZ,
            "source_text": prepared.source_text,
            "tts_text": prepared.tts_text,
            "reading_source": prepared.reading_source,
            "instruction": prepared.instruction,
            "emotion": prepared.emotion,
            "intensity": prepared.intensity,
            "delivery": prepared.delivery,
            "instruction_policy_version": INSTRUCTION_POLICY_VERSION,
            "instruction_template_id": prepared.instruction_template_id,
            "reference_selection_source": (
                prepared.reference.selection_source
            ),
            "reference_voice": prepared.reference.voice_id,
            "reference_sha256": prepared.reference.sha256,
            "reference_samples": prepared.reference.samples,
            "reference_duration_sec": prepared.reference.duration_sec,
            "watermark_disclosed_by_official_source": False,
        }

    def _prepared_input(self, job: LineJob) -> _PreparedInput:
        if not self._prepared:
            raise CosyVoice3AdapterError("prepare() が完了していません。")
        key = _job_key(job)
        try:
            return self._prepared_inputs[key]
        except KeyError as error:
            raise CosyVoice3AdapterError(
                f"prepare 済み input がありません: {key[0]}/{key[1]}",
            ) from error

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        code_root = _required_absolute_environment_path(
            CODE_ROOT_ENV,
            "CosyVoice source checkout",
        )
        model_root = _required_absolute_environment_path(
            MODEL_ROOT_ENV,
            "CosyVoice model weights",
        )
        _validate_source_checkout(code_root)
        _validate_model_root(model_root)
        model = self._run_phase(
            "CosyVoice runtime load",
            lambda: self._runtime.load_model(code_root, model_root),
        )
        identity = dict(self._runtime.model_identity(model))
        _validate_model_identity(identity)
        self._runtime_load_peak = _copy_peak(self._runtime.peak_memory_mib())
        self._model_identity = identity
        self._model = model
        return model

    def _run_phase(self, phase: str, action: Any) -> Any:
        try:
            return action()
        except Exception as error:
            if isinstance(error, CosyVoice3AdapterError):
                raise
            if self._runtime.is_out_of_memory(error):
                raise CosyVoice3AdapterError(
                    f"{phase} で CUDA out of memory になりました。",
                ) from error
            raise CosyVoice3AdapterError(
                f"{phase} に失敗しました: {error}",
            ) from error


def _line_input(job: LineJob) -> dict[str, Any]:
    if job.locale != "ja":
        raise CosyVoice3AdapterError(
            f"CosyVoice 3 adapter は Japanese 固定です: locale={job.locale}",
        )
    source_text = _required_string(job.line, "text", "line")
    try:
        reading = resolve_japanese_reading(
            text=source_text,
            reading=job.line.get("reading"),
        )
    except JapaneseReadingError as error:
        raise CosyVoice3AdapterError(
            f"CosyVoice 3 の日本語読みを解決できません: {error}",
        ) from error

    emotion = _required_string(job.line, "emotion", "line")
    try:
        instruction = EMOTION_INSTRUCTION_TEMPLATES[emotion]
    except KeyError as error:
        raise CosyVoice3AdapterError(
            f"未対応の line.emotion です: {emotion}",
        ) from error

    if "intensity" not in job.line:
        raise CosyVoice3AdapterError("line.intensity がありません。")
    intensity = job.line["intensity"]
    if isinstance(intensity, bool) or not isinstance(intensity, int):
        raise CosyVoice3AdapterError("line.intensity は 1〜3 の整数が必要です。")
    if intensity not in {1, 2, 3}:
        raise CosyVoice3AdapterError(
            f"line.intensity は 1〜3 が必要です: {intensity}",
        )

    delivery = _required_string(job.line, "delivery", "line")
    if (
        instruction.count(INSTRUCTION_END) != 1
        or not instruction.endswith(INSTRUCTION_END)
    ):
        raise CosyVoice3AdapterError(
            "CosyVoice instruction の終端 token が不正です。",
        )
    return {
        "source_text": source_text,
        "tts_text": reading.text,
        "reading_source": reading.source,
        "emotion": emotion,
        "intensity": intensity,
        "delivery": delivery,
        "instruction_template_id": emotion,
        "instruction": instruction,
    }


def _select_reference_voice(job: LineJob) -> tuple[str, str]:
    scenario_id = _required_identifier(job.scene, "id", "scene")
    character_id = _required_identifier(job.character, "id", "character")
    value = job.character.get("reference_voice")
    if value is not None:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise CosyVoice3AdapterError(
                "character.reference_voice は voice id または null が必要です。",
            )
        return value, "character.reference_voice"
    key = (scenario_id, character_id)
    try:
        return (
            REFERENCE_ASSIGNMENTS[key],
            f"adapter.assignment:{scenario_id}/{character_id}",
        )
    except KeyError as error:
        raise CosyVoice3AdapterError(
            "reference_voice が null で固定 assignment がありません: "
            f"{scenario_id}/{character_id}",
        ) from error


def _explicit_reference(
    *,
    voice_id: str,
    selection_source: str,
    voices_dir: Path,
    entry: Mapping[str, Any],
) -> _Reference:
    if not voices_dir.is_dir() or voices_dir.is_symlink():
        raise CosyVoice3AdapterError(
            f"voices root は通常ディレクトリが必要です: {voices_dir}",
        )
    expected_file = f"{voice_id}/reference.wav"
    if entry.get("file") != expected_file:
        raise CosyVoice3AdapterError(
            f"reference voice path が不正です: {voice_id}",
        )
    expected_sha256 = entry.get("sha256")
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(
        expected_sha256,
    ):
        raise CosyVoice3AdapterError(
            f"reference voice SHA-256 が不正です: {voice_id}",
        )
    expected_duration = entry.get("duration_sec")
    if (
        isinstance(expected_duration, bool)
        or not isinstance(expected_duration, (int, float))
        or not math.isfinite(float(expected_duration))
        or float(expected_duration) <= 0
        or float(expected_duration) > REFERENCE_MAX_SECONDS
    ):
        raise CosyVoice3AdapterError(
            f"reference voice duration metadata が不正です: {voice_id}",
        )

    root = voices_dir.resolve()
    source = root / expected_file
    resolved = source.resolve()
    if (
        source.parent.is_symlink()
        or source.is_symlink()
        or not resolved.is_relative_to(root)
    ):
        raise CosyVoice3AdapterError(
            f"reference voice は voices 内の通常ファイルが必要です: {source}",
        )
    _validate_file(
        source,
        expected_size=None,
        expected_sha256=expected_sha256,
        label=f"reference voice {voice_id}",
    )
    try:
        with wave.open(str(source), "rb") as wav_file:
            if (
                wav_file.getnchannels() != 1
                or wav_file.getsampwidth() != 2
                or wav_file.getframerate() != REFERENCE_SAMPLE_RATE_HZ
                or wav_file.getcomptype() != "NONE"
            ):
                raise CosyVoice3AdapterError(
                    "reference voice は PCM16/48kHz/mono が必要です: "
                    f"{voice_id}",
                )
            samples = wav_file.getnframes()
    except (EOFError, OSError, wave.Error) as error:
        raise CosyVoice3AdapterError(
            f"reference voice WAV を読めません: {voice_id}: {error}",
        ) from error
    duration_sec = samples / REFERENCE_SAMPLE_RATE_HZ
    if samples <= 0 or duration_sec > REFERENCE_MAX_SECONDS:
        raise CosyVoice3AdapterError(
            "reference voice は 0 秒超 30 秒以下が必要です: "
            f"{voice_id}",
        )
    if abs(duration_sec - float(expected_duration)) > 0.001:
        raise CosyVoice3AdapterError(
            "reference voice duration が metadata と一致しません: "
            f"{voice_id}",
        )
    return _Reference(
        voice_id=voice_id,
        selection_source=selection_source,
        wav_path=resolved,
        sha256=expected_sha256,
        samples=samples,
        duration_sec=duration_sec,
    )


def _load_reference_entries(
    voices_dir: Path,
) -> dict[str, Mapping[str, Any]]:
    result = validate_voice_metadata(voices_dir)
    if result.problems:
        raise CosyVoice3AdapterError(
            f"参照音声 metadata が不正です: {result.problems[0]}",
        )
    metadata_path = voices_dir / "metadata.yaml"
    try:
        document = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise CosyVoice3AdapterError(
            f"参照音声 metadata を読めません: {metadata_path}: {error}",
        ) from error
    if not isinstance(document, Mapping) or not isinstance(
        document.get("voices"),
        list,
    ):
        raise CosyVoice3AdapterError(
            f"参照音声 metadata の構造が不正です: {metadata_path}",
        )
    return {
        str(entry["id"]): entry
        for entry in document["voices"]
        if isinstance(entry, Mapping)
    }


def _validate_source_checkout(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        raise CosyVoice3AdapterError(
            f"固定 CosyVoice source checkout がありません: {root}",
        )
    top_level = Path(_git_output(root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != root:
        raise CosyVoice3AdapterError(
            f"CosyVoice source checkout root が一致しません: {top_level}",
        )
    revision = _git_output(root, "rev-parse", "HEAD")
    if revision != UPSTREAM_REVISION:
        raise CosyVoice3AdapterError(
            "CosyVoice source revision が一致しません: "
            f"{revision}, expected={UPSTREAM_REVISION}",
        )
    status = _git_output(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if status:
        raise CosyVoice3AdapterError(
            "CosyVoice source checkout に未コミット変更があります。",
        )

    matcha_root = root / "third_party" / "Matcha-TTS"
    if not matcha_root.is_dir() or matcha_root.is_symlink():
        raise CosyVoice3AdapterError(
            f"固定 Matcha-TTS submodule がありません: {matcha_root}",
        )
    matcha_top_level = Path(
        _git_output(matcha_root, "rev-parse", "--show-toplevel"),
    ).resolve()
    if matcha_top_level != matcha_root:
        raise CosyVoice3AdapterError(
            "Matcha-TTS submodule root が一致しません: "
            f"{matcha_top_level}",
        )
    matcha_revision = _git_output(matcha_root, "rev-parse", "HEAD")
    if matcha_revision != MATCHA_REVISION:
        raise CosyVoice3AdapterError(
            "Matcha-TTS submodule revision が一致しません: "
            f"{matcha_revision}, expected={MATCHA_REVISION}",
        )
    matcha_status = _git_output(
        matcha_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if matcha_status:
        raise CosyVoice3AdapterError(
            "Matcha-TTS submodule に未コミット変更があります。",
        )


def _git_output(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as error:
        raise CosyVoice3AdapterError(
            f"git を実行できません: {error}",
        ) from error
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise CosyVoice3AdapterError(
            f"git {' '.join(arguments)} に失敗しました: {message}",
        )
    return result.stdout.strip()


def _validate_model_root(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        raise CosyVoice3AdapterError(
            f"固定 CosyVoice model root がありません: {root}",
        )
    actual_files: set[str] = set()
    actual_dirs: set[str] = set()
    for current_root, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_root)
        for name in directory_names:
            directory = current / name
            if directory.is_symlink():
                raise CosyVoice3AdapterError(
                    f"CosyVoice model directory に symlink があります: {directory}",
                )
            relative = directory.relative_to(root).as_posix()
            if relative != ".cache" and not relative.startswith(".cache/"):
                actual_dirs.add(relative)
        for name in file_names:
            path = current / name
            if path.is_symlink():
                raise CosyVoice3AdapterError(
                    f"CosyVoice model directory に symlink があります: {path}",
                )
            relative = path.relative_to(root).as_posix()
            if relative != ".cache" and not relative.startswith(".cache/"):
                actual_files.add(relative)
    if actual_dirs != {"CosyVoice-BlankEN"}:
        raise CosyVoice3AdapterError(
            "CosyVoice model root の directory inventory が一致しません: "
            f"{sorted(actual_dirs)}",
        )
    if actual_files != set(MODEL_FILE_SPECS):
        raise CosyVoice3AdapterError(
            "CosyVoice model root の file inventory が一致しません: "
            f"{sorted(actual_files)}",
        )
    for name, (size, sha256) in MODEL_FILE_SPECS.items():
        _validate_file(
            root / Path(name),
            expected_size=size,
            expected_sha256=sha256,
            label=f"model file {name}",
        )


def _required_absolute_environment_path(
    variable: str,
    label: str,
) -> Path:
    raw = os.getenv(variable)
    if raw is None or not raw.strip():
        raise CosyVoice3AdapterError(
            f"{variable} に固定 {label} を設定してください。",
        )
    path = Path(raw)
    if not path.is_absolute():
        raise CosyVoice3AdapterError(f"{variable} は絶対パスが必要です。")
    if path.is_symlink():
        raise CosyVoice3AdapterError(
            f"{variable} は symlink を指定できません。",
        )
    return path.resolve()


def _validate_model_identity(identity: Mapping[str, Any]) -> None:
    expected_keys = {
        "architecture",
        "model_architecture",
        "sample_rate_hz",
        "fp16",
        "frontend_text_frontend",
        "frontend_device",
        "llm_device",
        "flow_device",
        "hift_device",
        "speech_tokenizer_providers",
        "campplus_providers",
    }
    if set(identity) != expected_keys:
        raise CosyVoice3AdapterError(
            f"model identity の項目が一致しません: {sorted(identity)}",
        )
    expected_scalar = {
        "architecture": ARCHITECTURE,
        "model_architecture": MODEL_ARCHITECTURE,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "fp16": FP16,
        "frontend_text_frontend": "",
        "frontend_device": "cuda",
        "llm_device": DEVICE,
        "flow_device": DEVICE,
        "hift_device": DEVICE,
    }
    for key, expected in expected_scalar.items():
        if identity[key] != expected:
            raise CosyVoice3AdapterError(
                f"model identity が一致しません: "
                f"{key}={identity[key]}, expected={expected}",
            )
    speech_providers = identity["speech_tokenizer_providers"]
    if speech_providers != [
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]:
        raise CosyVoice3AdapterError(
            "speech tokenizer provider が固定構成と一致しません: "
            f"{speech_providers}",
        )
    campplus_providers = identity["campplus_providers"]
    if campplus_providers != ["CPUExecutionProvider"]:
        raise CosyVoice3AdapterError(
            "CampPlus provider は CPUExecutionProvider 固定が必要です: "
            f"{campplus_providers}",
        )


def _validated_chunks(
    chunks: Sequence[Mapping[str, Any]],
) -> tuple[list[Any], int]:
    if not isinstance(chunks, Sequence) or isinstance(
        chunks,
        (str, bytes, bytearray),
    ):
        raise CosyVoice3AdapterError(
            "CosyVoice generator output は sequence が必要です。",
        )
    if not chunks:
        raise CosyVoice3AdapterError("CosyVoice generator output が空です。")
    waveforms: list[Any] = []
    samples = 0
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, Mapping) or set(chunk) != {"tts_speech"}:
            raise CosyVoice3AdapterError(
                f"CosyVoice generator chunk {index} の項目が不正です。",
            )
        waveform = chunk["tts_speech"]
        samples += _validate_waveform(waveform)
        waveforms.append(waveform)
    return waveforms, samples


def _validate_waveform(waveform: Any) -> int:
    if hasattr(waveform, "detach"):
        values = waveform.detach().cpu()
        shape = tuple(values.shape)
        if len(shape) != 2 or shape[0] != 1 or shape[1] <= 0:
            raise CosyVoice3AdapterError(
                f"CosyVoice waveform shape が不正です: {shape}",
            )
        try:
            finite = bool(values.isfinite().all().item())
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            raise CosyVoice3AdapterError(
                f"CosyVoice waveform を検証できません: {error}",
            ) from error
        if not finite:
            raise CosyVoice3AdapterError(
                "CosyVoice waveform に非有限値があります。",
            )
        return int(shape[1])

    if (
        not isinstance(waveform, (list, tuple))
        or len(waveform) != 1
        or not isinstance(waveform[0], (list, tuple))
        or not waveform[0]
    ):
        raise CosyVoice3AdapterError(
            "CosyVoice waveform は shape [1, samples] が必要です。",
        )
    for value in waveform[0]:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise CosyVoice3AdapterError(
                "CosyVoice waveform に非有限値があります。",
            )
    return len(waveform[0])


def _validate_pcm16_wav(path: Path, *, expected_samples: int) -> None:
    if not path.is_file():
        raise CosyVoice3AdapterError(f"adapter 出力がありません: {path}")
    try:
        with wave.open(str(path), "rb") as wav_file:
            valid = (
                wav_file.getnchannels() == 1
                and wav_file.getsampwidth() == 2
                and wav_file.getframerate() == SAMPLE_RATE_HZ
                and wav_file.getcomptype() == "NONE"
                and wav_file.getnframes() == expected_samples
            )
    except (EOFError, OSError, wave.Error) as error:
        raise CosyVoice3AdapterError(
            f"adapter 出力 WAV を読めません: {path}: {error}",
        ) from error
    if not valid:
        raise CosyVoice3AdapterError(
            "adapter 出力は PCM16/24kHz/mono かつ sample 数一致が必要です。",
        )


def _validate_file(
    path: Path,
    *,
    expected_size: int | None,
    expected_sha256: str,
    label: str,
) -> None:
    if not path.is_file() or path.is_symlink():
        raise CosyVoice3AdapterError(f"{label} がありません: {path}")
    if expected_size is not None and path.stat().st_size != expected_size:
        raise CosyVoice3AdapterError(f"{label} の file size が一致しません。")
    if _sha256_file(path) != expected_sha256:
        raise CosyVoice3AdapterError(f"{label} の SHA-256 が一致しません。")


def _set_offline_environment() -> None:
    for key, value in OFFLINE_ENVIRONMENT.items():
        os.environ[key] = value


def _configure_deterministic_cuda(torch: Any) -> None:
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_tf32 = False


def _preload_cuda_zero(torch: Any) -> None:
    if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
        raise CosyVoice3AdapterError("CUDA:0 を利用できません。")
    torch.cuda.init()
    current_device = torch.cuda.current_device()
    if current_device != 0:
        raise CosyVoice3AdapterError(
            f"current CUDA device は 0 が必要です: {current_device}",
        )


def _validate_onnxruntime_installation(onnxruntime: Any) -> None:
    try:
        cpu_distribution_version = metadata.version("onnxruntime")
    except metadata.PackageNotFoundError:
        pass
    else:
        raise CosyVoice3AdapterError(
            "onnxruntime CPU distribution を同居できません: "
            f"onnxruntime={cpu_distribution_version}",
        )
    if str(onnxruntime.__version__) != ONNXRUNTIME_GPU_VERSION:
        raise CosyVoice3AdapterError(
            "onnxruntime module version が一致しません: "
            f"{onnxruntime.__version__}, expected={ONNXRUNTIME_GPU_VERSION}",
        )
    providers = list(onnxruntime.get_available_providers())
    if "CUDAExecutionProvider" not in providers:
        raise CosyVoice3AdapterError(
            "onnxruntime CUDAExecutionProvider を利用できません: "
            f"{providers}",
        )


def _seed_every_line(torch: Any, numpy: Any, seed: int) -> None:
    random.seed(seed)
    numpy.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    _configure_deterministic_cuda(torch)


def _validate_module_origin(
    module: Any,
    root: Path,
    module_name: str,
) -> None:
    raw_origin = getattr(module, "__file__", None)
    if not isinstance(raw_origin, str):
        raise CosyVoice3AdapterError(
            f"{module_name} の module origin がありません。",
        )
    origin = Path(raw_origin).resolve()
    if not origin.is_relative_to(root):
        raise CosyVoice3AdapterError(
            f"{module_name} が固定 source 外から import されました: {origin}",
        )


def _copy_peak(value: Mapping[str, Any]) -> dict[str, float]:
    if set(value) != {"allocated_mib", "reserved_mib"}:
        raise CosyVoice3AdapterError("CUDA peak profile の項目が不正です。")
    result: dict[str, float] = {}
    for key in ("allocated_mib", "reserved_mib"):
        raw = value[key]
        if (
            isinstance(raw, bool)
            or not isinstance(raw, (int, float))
            or not math.isfinite(float(raw))
            or float(raw) < 0
        ):
            raise CosyVoice3AdapterError("CUDA peak profile の値が不正です。")
        result[key] = float(raw)
    return result


def _job_key(job: LineJob) -> tuple[str, str]:
    return (
        _required_identifier(job.scene, "id", "scene"),
        _required_identifier(job.line, "id", "line"),
    )


def _required_identifier(
    value: Mapping[str, Any],
    key: str,
    section: str,
) -> str:
    result = _required_string(value, key, section)
    if not _IDENTIFIER.fullmatch(result):
        raise CosyVoice3AdapterError(
            f"{section}.{key} は正規化済み識別子が必要です。",
        )
    return result


def _required_string(
    value: Mapping[str, Any],
    key: str,
    section: str,
) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise CosyVoice3AdapterError(
            f"{section}.{key} は空でない文字列が必要です。",
        )
    return result


def _distribution_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError as error:
        raise CosyVoice3AdapterError(
            f"必須 package がありません: {distribution}",
        ) from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
