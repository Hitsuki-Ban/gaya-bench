from __future__ import annotations

import hashlib
import importlib
import math
import os
import re
import sys
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

import yaml

from gaya_pipeline.adapters.base import Capabilities, LineJob, ModelProfile
from gaya_pipeline.voice_assets import validate_voice_metadata

MODEL_ID = "chatterbox-multilingual-v3"
UPSTREAM_REPOSITORY = "resemble-ai/chatterbox"
UPSTREAM_REVISION = "65b18437192794391a0308a8f705b1e33e633948"
WEIGHTS_REPOSITORY = "ResembleAI/chatterbox"
WEIGHTS_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
PERTH_REPOSITORY = "resemble-ai/Perth"
PERTH_REVISION = "ce86c49d029f42272c1902eccb675556b9ed2330"
MODEL_ROOT_ENV = "GAYA_CHATTERBOX_ROOT"
PROFILE_VERSION = (
    f"Chatterbox {UPSTREAM_REVISION}; "
    f"{WEIGHTS_REPOSITORY} {WEIGHTS_REVISION}; PerTh {PERTH_REVISION}"
)

ARCHITECTURE = "chatterbox-multilingual-v3"
T3_MODEL = "v3"
LANGUAGE_ID = "ja"
DEVICE = "cuda:0"
DTYPE = "float32"
SAMPLE_RATE_HZ = 24_000
REFERENCE_SAMPLE_RATE_HZ = 48_000
REFERENCE_MIN_SECONDS = 10

CHATTERBOX_VERSION = "0.1.7"
PERTH_VERSION = "1.0.1"
TORCH_VERSION = "2.6.0+cu126"
TORCHAUDIO_VERSION = "2.6.0+cu126"
CUDA_VERSION = "12.6"
TRANSFORMERS_VERSION = "5.2.0"
DIFFUSERS_VERSION = "0.29.0"
PYKAKASI_VERSION = "2.3.0"
SAFETENSORS_VERSION = "0.5.3"
S3TOKENIZER_VERSION = "0.3.0"

SEED = 42
CFG_WEIGHT = 0.5
TEMPERATURE = 0.8
REPETITION_PENALTY = 1.2
MIN_P = 0.05
TOP_P = 1.0
EXAGGERATION_BY_INTENSITY = {
    1: 0.3,
    2: 0.5,
    3: 0.8,
}

MODEL_FILE_SPECS: dict[str, tuple[int, str]] = {
    "Cangjie5_TC.json": (
        1_920_163,
        "7073fd9de919443ae88e0bd2449917a65fe54898a4413ed1edcc4b67f28bce8c",
    ),
    "grapheme_mtl_merged_expanded_v1.json": (
        69_989,
        "69632f47220a788a52ce2661d096453c5655e9bf25289d89a8d832c46ee07dbf",
    ),
    "s3gen.pt": (
        1_057_165_844,
        "9b9ff07e60b20c136e2b1b3d7563a24604e8d2c4c267888d1ee929dd0151d2a3",
    ),
    "t3_mtl23ls_v3.safetensors": (
        2_143_989_928,
        "5abca8321ede76f8e61f1cc0d19aea6c946b28871017ce8726f8a69203f05953",
    ),
    "ve.pt": (
        5_698_626,
        "4b16d836bc598509860f6fa068165a8bb5e9ac84f05582dfcf278a5a372879f1",
    ),
}

PERTH_CHECKPOINT_RELATIVE_PATH = (
    "perth_net/pretrained/implicit/perth_net_250000.pth.tar"
)
PERTH_CHECKPOINT_SIZE = 37_429_684
PERTH_CHECKPOINT_SHA256 = (
    "a15bce457ebc53ce5e6c9c3f11df78cf7ee2bf9cdab0a798902135b4c4027670"
)
PERTH_PRETRAINED_RELATIVE_DIR = "perth_net/pretrained/implicit"
PERTH_BUNDLE_FILE_SPECS: dict[str, tuple[int, str]] = {
    "hparams.yaml": (
        271,
        "6e4deab0716a5b647eba52b4df97d93f37e57e283ff67c265fb6fee025f8e2cf",
    ),
    "id.txt": (
        22,
        "f4129d0cce1fcd76a01c778dd46aeecc84130e38d83c98402abf2e1b9c49770d",
    ),
    "perth_net_250000.pth.tar": (
        PERTH_CHECKPOINT_SIZE,
        PERTH_CHECKPOINT_SHA256,
    ),
}

REFERENCE_ASSIGNMENTS: Mapping[tuple[str, str], str] = {
    ("tavern-night", "drunkard"): "hadou-emotion-11",
    ("tavern-night", "old-regular"): "hadou-emotion-11",
    ("market-day", "fruit-vendor"): "hadou-emotion-11",
    ("market-day", "shopper"): "lux-emotion-76",
    ("market-day", "street-kid"): "tsukuyomi-corpus-94",
}

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MIB = 1024 * 1024


class ChatterboxAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Reference:
    voice_id: str
    selection_source: str
    wav_path: Path
    sha256: str


@dataclass(frozen=True)
class _PreparedInput:
    text: str
    emotion: str
    intensity: int
    exaggeration: float
    reference: _Reference

    def as_generation_input(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language_id": LANGUAGE_ID,
            "intensity": self.intensity,
            "exaggeration": self.exaggeration,
            "reference_selection_source": self.reference.selection_source,
            "reference_voice": self.reference.voice_id,
            "reference_sha256": self.reference.sha256,
        }


class _Runtime(Protocol):
    def load_model(self, snapshot_path: Path) -> Any: ...

    def model_identity(self, model: Any) -> Mapping[str, Any]: ...

    def synthesize(
        self,
        model: Any,
        *,
        text: str,
        reference_wav: Path,
        exaggeration: float,
    ) -> Any: ...

    def write_pcm16(self, path: Path, waveform: Any, sample_rate: int) -> None: ...

    def reset_peak_memory_stats(self) -> None: ...

    def peak_memory_mib(self) -> Mapping[str, float]: ...

    def is_out_of_memory(self, error: BaseException) -> bool: ...


class _NativeRuntime:
    def __init__(self) -> None:
        self._torch: Any | None = None
        self._soundfile: Any | None = None
        self._model_class: Any | None = None
        self._tokenizer_module: Any | None = None

    def _load_dependencies(self) -> None:
        if self._torch is not None:
            return
        if sys.version_info[:2] != (3, 12):
            raise ChatterboxAdapterError(
                "Chatterbox Multilingual V3 は Python 3.12 "
                "だけをサポートします。",
            )
        if sys.platform != "win32":
            raise ChatterboxAdapterError(
                "Chatterbox Multilingual V3 は Windows native CUDA:0 "
                "だけをサポートします。",
            )
        try:
            torch = importlib.import_module("torch")
            torchaudio = importlib.import_module("torchaudio")
            soundfile = importlib.import_module("soundfile")
            mtl_tts = importlib.import_module("chatterbox.mtl_tts")
            tokenizer_module = importlib.import_module(
                "chatterbox.models.tokenizers.tokenizer",
            )
            perth = importlib.import_module("perth")
        except (ImportError, ModuleNotFoundError) as error:
            raise ChatterboxAdapterError(
                f"Chatterbox の必須依存を import できません: {error}",
            ) from error

        expected_versions = {
            "chatterbox-tts": CHATTERBOX_VERSION,
            "resemble-perth": PERTH_VERSION,
            "transformers": TRANSFORMERS_VERSION,
            "diffusers": DIFFUSERS_VERSION,
            "pykakasi": PYKAKASI_VERSION,
            "safetensors": SAFETENSORS_VERSION,
            "s3tokenizer": S3TOKENIZER_VERSION,
        }
        actual_versions = {
            distribution: _distribution_version(distribution)
            for distribution in expected_versions
        }
        actual_versions.update(
            {
                "torch": str(torch.__version__),
                "torchaudio": str(torchaudio.__version__),
            },
        )
        expected_versions.update(
            {
                "torch": TORCH_VERSION,
                "torchaudio": TORCHAUDIO_VERSION,
            },
        )
        for distribution, expected in expected_versions.items():
            actual = actual_versions[distribution]
            if actual != expected:
                raise ChatterboxAdapterError(
                    f"package version が一致しません: "
                    f"{distribution}={actual}, expected={expected}",
                )
        if str(torch.version.cuda) != CUDA_VERSION:
            raise ChatterboxAdapterError(
                f"PyTorch CUDA version が一致しません: "
                f"{torch.version.cuda}, expected={CUDA_VERSION}",
            )
        if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
            raise ChatterboxAdapterError("CUDA:0 を利用できません。")

        _validate_perth_bundle(Path(perth.__file__).resolve().parent)

        self._torch = torch
        self._soundfile = soundfile
        self._model_class = mtl_tts.ChatterboxMultilingualTTS
        self._tokenizer_module = tokenizer_module

    def load_model(self, snapshot_path: Path) -> Any:
        self._load_dependencies()
        if self._model_class is None or self._tokenizer_module is None:
            raise ChatterboxAdapterError("Chatterbox runtime が初期化されていません。")

        cangjie_path = snapshot_path / "Cangjie5_TC.json"
        original_download = self._tokenizer_module.hf_hub_download
        converter_class = self._tokenizer_module.ChineseCangjieConverter
        original_segmenter_init = converter_class._init_segmenter
        download_calls = 0
        segmenter_calls = 0

        def local_cangjie_download(*args: Any, **kwargs: Any) -> str:
            nonlocal download_calls
            download_calls += 1
            repo_id = kwargs.get("repo_id")
            filename = kwargs.get("filename")
            cache_dir = kwargs.get("cache_dir")
            if args or repo_id != WEIGHTS_REPOSITORY or filename != cangjie_path.name:
                raise ChatterboxAdapterError(
                    "予期しない Hugging Face download request を拒否しました。",
                )
            if cache_dir is None or Path(cache_dir).resolve() != snapshot_path:
                raise ChatterboxAdapterError(
                    "Cangjie mapping の model root が一致しません。",
                )
            return str(cangjie_path)

        def disable_unused_chinese_segmenter(converter: Any) -> None:
            nonlocal segmenter_calls
            segmenter_calls += 1
            converter.segmenter = None

        self._tokenizer_module.hf_hub_download = local_cangjie_download
        converter_class._init_segmenter = disable_unused_chinese_segmenter
        try:
            model = self._model_class.from_local(
                snapshot_path,
                device=DEVICE,
                t3_model=T3_MODEL,
            )
        finally:
            self._tokenizer_module.hf_hub_download = original_download
            converter_class._init_segmenter = original_segmenter_init

        if download_calls != 1:
            raise ChatterboxAdapterError(
                "Cangjie mapping の local resolver 呼び出し回数が不正です: "
                f"{download_calls}",
            )
        if segmenter_calls != 1:
            raise ChatterboxAdapterError(
                "未使用 Chinese segmenter の抑止回数が不正です: "
                f"{segmenter_calls}",
            )
        converter = getattr(model.tokenizer, "cangjie_converter", None)
        if not getattr(converter, "word2cj", None):
            raise ChatterboxAdapterError(
                "固定 Cangjie mapping をロードできませんでした。",
            )
        return model

    def model_identity(self, model: Any) -> Mapping[str, Any]:
        parameter = next(model.t3.parameters())
        return {
            "architecture": ARCHITECTURE,
            "sample_rate_hz": int(model.sr),
            "device": str(parameter.device),
            "dtype": str(parameter.dtype).removeprefix("torch."),
            "watermarker": type(model.watermarker).__name__,
        }

    def synthesize(
        self,
        model: Any,
        *,
        text: str,
        reference_wav: Path,
        exaggeration: float,
    ) -> Any:
        if self._torch is None:
            raise ChatterboxAdapterError("Chatterbox runtime が初期化されていません。")
        self._torch.manual_seed(SEED)
        self._torch.cuda.manual_seed_all(SEED)
        return model.generate(
            text,
            language_id=LANGUAGE_ID,
            audio_prompt_path=str(reference_wav),
            exaggeration=exaggeration,
            cfg_weight=CFG_WEIGHT,
            temperature=TEMPERATURE,
            repetition_penalty=REPETITION_PENALTY,
            min_p=MIN_P,
            top_p=TOP_P,
        )

    def write_pcm16(self, path: Path, waveform: Any, sample_rate: int) -> None:
        if self._soundfile is None:
            raise ChatterboxAdapterError("Chatterbox runtime が初期化されていません。")
        values = waveform.detach().cpu().float().reshape(-1).numpy()
        self._soundfile.write(
            str(path),
            values,
            sample_rate,
            subtype="PCM_16",
            format="WAV",
        )

    def reset_peak_memory_stats(self) -> None:
        self._load_dependencies()
        if self._torch is None:
            raise ChatterboxAdapterError("Chatterbox runtime が初期化されていません。")
        self._torch.cuda.reset_peak_memory_stats()

    def peak_memory_mib(self) -> Mapping[str, float]:
        self._load_dependencies()
        if self._torch is None:
            raise ChatterboxAdapterError("Chatterbox runtime が初期化されていません。")
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


class ChatterboxAdapter:
    profile = ModelProfile(
        id=MODEL_ID,
        name="Chatterbox Multilingual V3",
        version=PROFILE_VERSION,
        license_note=(
            "Chatterbox コード・公式重み・PerTh は MIT。"
            "生成音声には PerTh 電子透かしが自動で入る。"
            "参照音声の利用規約・クレジット・再配布条件にも従い、"
            "無断の声真似や誤認を招く利用を禁止する。"
        ),
        capabilities=Capabilities(
            emotion=True,
            voice_prompt=False,
            clone=True,
            nonverbal=False,
            reading=False,
        ),
    )

    def __init__(
        self,
        *,
        runtime: _Runtime | None = None,
        model_root: Path | None = None,
    ) -> None:
        self._runtime = runtime if runtime is not None else _NativeRuntime()
        self._model_root = model_root
        self._model: Any | None = None
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
                raise ChatterboxAdapterError(
                    f"同じ line job が重複しています: {key[0]}/{key[1]}",
                )
            text, emotion, intensity, exaggeration = _line_input(job)
            voice_id, selection_source = _select_reference_voice(job)
            reference_key = (voice_id, selection_source)
            reference = references.get(reference_key)
            if reference is None:
                try:
                    entry = entries[voice_id]
                except KeyError as error:
                    raise ChatterboxAdapterError(
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
                text=text,
                emotion=emotion,
                intensity=intensity,
                exaggeration=exaggeration,
                reference=reference,
            )
        self._prepared = True

    def generation_params(self) -> Mapping[str, Any]:
        return {
            "upstream_repository": UPSTREAM_REPOSITORY,
            "upstream_revision": UPSTREAM_REVISION,
            "weights_repository": WEIGHTS_REPOSITORY,
            "weights_revision": WEIGHTS_REVISION,
            "perth_repository": PERTH_REPOSITORY,
            "perth_revision": PERTH_REVISION,
            "model_root_environment": MODEL_ROOT_ENV,
            "model_files": {
                name: {"size": size, "sha256": sha256}
                for name, (size, sha256) in MODEL_FILE_SPECS.items()
            },
            "perth_checkpoint": {
                "path": PERTH_CHECKPOINT_RELATIVE_PATH,
                "size": PERTH_CHECKPOINT_SIZE,
                "sha256": PERTH_CHECKPOINT_SHA256,
            },
            "architecture": ARCHITECTURE,
            "t3_model": T3_MODEL,
            "language_id": LANGUAGE_ID,
            "device": DEVICE,
            "dtype": DTYPE,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "chatterbox_version": CHATTERBOX_VERSION,
            "perth_version": PERTH_VERSION,
            "torch_version": TORCH_VERSION,
            "torchaudio_version": TORCHAUDIO_VERSION,
            "cuda_version": CUDA_VERSION,
            "transformers_version": TRANSFORMERS_VERSION,
            "diffusers_version": DIFFUSERS_VERSION,
            "pykakasi_version": PYKAKASI_VERSION,
            "safetensors_version": SAFETENSORS_VERSION,
            "s3tokenizer_version": S3TOKENIZER_VERSION,
            "seed": SEED,
            "cfg_weight": CFG_WEIGHT,
            "temperature": TEMPERATURE,
            "repetition_penalty": REPETITION_PENALTY,
            "min_p": MIN_P,
            "top_p": TOP_P,
            "exaggeration_by_intensity": {
                str(intensity): exaggeration
                for intensity, exaggeration in EXAGGERATION_BY_INTENSITY.items()
            },
            "emotion_control": "exaggeration_only",
            "perth_watermark": True,
        }

    def generation_input(self, job: LineJob) -> Mapping[str, Any]:
        return self._prepared_input(job).as_generation_input()

    def generate(self, job: LineJob, output_wav: Path) -> Mapping[str, Any]:
        prepared = self._prepared_input(job)
        model = self._ensure_model()
        self._runtime.reset_peak_memory_stats()
        waveform = self._run_phase(
            f"Chatterbox generation ({job.scenario_id}/{job.line_id})",
            lambda: self._runtime.synthesize(
                model,
                text=prepared.text,
                reference_wav=prepared.reference.wav_path,
                exaggeration=prepared.exaggeration,
            ),
        )
        _validate_waveform(waveform)
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
        _validate_pcm16_wav(output_wav)
        if self._runtime_load_peak is None:
            raise ChatterboxAdapterError("runtime load の CUDA peak profile がありません。")
        return {
            "phase_peak_vram_mib": {
                "runtime_load": _copy_peak(self._runtime_load_peak),
                "generation": generation_peak,
            },
            "seed": SEED,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "language_id": LANGUAGE_ID,
            "line_emotion_audit": prepared.emotion,
            "intensity": prepared.intensity,
            "exaggeration": prepared.exaggeration,
            "reference_selection_source": prepared.reference.selection_source,
            "reference_voice": prepared.reference.voice_id,
            "reference_sha256": prepared.reference.sha256,
            "perth_watermark_stage_executed": True,
        }

    def _prepared_input(self, job: LineJob) -> _PreparedInput:
        if not self._prepared:
            raise ChatterboxAdapterError("prepare() が完了していません。")
        key = _job_key(job)
        try:
            return self._prepared_inputs[key]
        except KeyError as error:
            raise ChatterboxAdapterError(
                f"prepare 済み input がありません: {key[0]}/{key[1]}",
            ) from error

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        model_root = (
            _model_root_from_environment()
            if self._model_root is None
            else self._model_root.resolve()
        )
        _validate_model_root(model_root)
        self._runtime.reset_peak_memory_stats()
        model = self._run_phase(
            "Chatterbox runtime load",
            lambda: self._runtime.load_model(model_root),
        )
        identity = dict(self._runtime.model_identity(model))
        expected = {
            "architecture": ARCHITECTURE,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "device": DEVICE,
            "dtype": DTYPE,
            "watermarker": "PerthImplicitWatermarker",
        }
        if identity != expected:
            raise ChatterboxAdapterError(
                f"model identity が一致しません: {identity}, expected={expected}",
            )
        self._runtime_load_peak = _copy_peak(self._runtime.peak_memory_mib())
        self._model = model
        return model

    def _run_phase(self, phase: str, action: Any) -> Any:
        try:
            return action()
        except Exception as error:
            if isinstance(error, ChatterboxAdapterError):
                raise
            if self._runtime.is_out_of_memory(error):
                raise ChatterboxAdapterError(
                    f"{phase} で CUDA out of memory になりました。",
                ) from error
            raise ChatterboxAdapterError(f"{phase} に失敗しました: {error}") from error


def _line_input(job: LineJob) -> tuple[str, str, int, float]:
    if job.locale != "ja":
        raise ChatterboxAdapterError(
            f"Chatterbox Multilingual V3 adapter は Japanese 固定です: "
            f"locale={job.locale}",
        )
    text = _required_string(job.line, "text", "line")
    emotion = _required_string(job.line, "emotion", "line")
    intensity = job.line.get("intensity", 2)
    if isinstance(intensity, bool) or not isinstance(intensity, int):
        raise ChatterboxAdapterError("line.intensity は 1〜3 の整数が必要です。")
    try:
        exaggeration = EXAGGERATION_BY_INTENSITY[intensity]
    except KeyError as error:
        raise ChatterboxAdapterError(
            f"line.intensity は 1〜3 が必要です: {intensity}",
        ) from error
    return text, emotion, intensity, exaggeration


def _select_reference_voice(job: LineJob) -> tuple[str, str]:
    scenario_id = _required_identifier(job.scene, "id", "scene")
    character_id = _required_identifier(job.character, "id", "character")
    value = job.character.get("reference_voice")
    if value is not None:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ChatterboxAdapterError(
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
        raise ChatterboxAdapterError(
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
    expected_file = f"{voice_id}/reference.wav"
    if entry.get("file") != expected_file:
        raise ChatterboxAdapterError(
            f"reference voice path が不正です: {voice_id}",
        )
    expected_sha256 = entry.get("sha256")
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(
        expected_sha256,
    ):
        raise ChatterboxAdapterError(
            f"reference voice SHA-256 が不正です: {voice_id}",
        )

    root = voices_dir.resolve()
    source = root / expected_file
    resolved = source.resolve()
    if source.is_symlink() or not resolved.is_relative_to(root):
        raise ChatterboxAdapterError(
            f"reference voice は voices 内の通常ファイルが必要です: {source}",
        )
    _validate_file(
        source,
        expected_size=None,
        expected_sha256=expected_sha256,
        label=f"reference voice {voice_id}",
    )
    with wave.open(str(source), "rb") as wav_file:
        if (
            wav_file.getnchannels() != 1
            or wav_file.getsampwidth() != 2
            or wav_file.getframerate() != REFERENCE_SAMPLE_RATE_HZ
            or wav_file.getcomptype() != "NONE"
        ):
            raise ChatterboxAdapterError(
                f"reference voice は PCM16/48kHz/mono が必要です: {voice_id}",
            )
        if wav_file.getnframes() < REFERENCE_SAMPLE_RATE_HZ * REFERENCE_MIN_SECONDS:
            raise ChatterboxAdapterError(
                f"reference voice は10秒以上が必要です: {voice_id}",
            )
    return _Reference(
        voice_id=voice_id,
        selection_source=selection_source,
        wav_path=resolved,
        sha256=expected_sha256,
    )


def _load_reference_entries(
    voices_dir: Path,
) -> dict[str, Mapping[str, Any]]:
    result = validate_voice_metadata(voices_dir)
    if result.problems:
        raise ChatterboxAdapterError(
            f"参照音声 metadata が不正です: {result.problems[0]}",
        )
    metadata_path = voices_dir / "metadata.yaml"
    try:
        document = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ChatterboxAdapterError(
            f"参照音声 metadata を読めません: {metadata_path}: {error}",
        ) from error
    if not isinstance(document, Mapping) or not isinstance(
        document.get("voices"),
        list,
    ):
        raise ChatterboxAdapterError(
            f"参照音声 metadata の構造が不正です: {metadata_path}",
        )
    return {
        str(entry["id"]): entry
        for entry in document["voices"]
        if isinstance(entry, Mapping)
    }


def _validate_model_root(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        raise ChatterboxAdapterError(
            f"固定 Chatterbox model root がありません: {root}",
        )
    actual_files = {path.name for path in root.iterdir() if path.is_file()}
    if actual_files != set(MODEL_FILE_SPECS):
        raise ChatterboxAdapterError(
            "Chatterbox model root の file inventory が一致しません: "
            f"{sorted(actual_files)}",
        )
    for name, (size, sha256) in MODEL_FILE_SPECS.items():
        _validate_file(
            root / name,
            expected_size=size,
            expected_sha256=sha256,
            label=f"model file {name}",
        )


def _model_root_from_environment() -> Path:
    raw = os.getenv(MODEL_ROOT_ENV)
    if raw is None or not raw.strip():
        raise ChatterboxAdapterError(
            f"{MODEL_ROOT_ENV} に固定 Chatterbox model root を設定してください。",
        )
    path = Path(raw)
    if not path.is_absolute():
        raise ChatterboxAdapterError(f"{MODEL_ROOT_ENV} は絶対パスが必要です。")
    return path.resolve()


def _validate_perth_bundle(package_root: Path) -> None:
    pretrained_dir = package_root / PERTH_PRETRAINED_RELATIVE_DIR
    if not pretrained_dir.is_dir() or pretrained_dir.is_symlink():
        raise ChatterboxAdapterError(
            f"固定 PerTh pretrained directory がありません: {pretrained_dir}",
        )
    actual_entries = {path.name for path in pretrained_dir.iterdir()}
    if actual_entries != set(PERTH_BUNDLE_FILE_SPECS):
        raise ChatterboxAdapterError(
            "PerTh pretrained directory の inventory が一致しません: "
            f"{sorted(actual_entries)}",
        )
    for name, (size, sha256) in PERTH_BUNDLE_FILE_SPECS.items():
        _validate_file(
            pretrained_dir / name,
            expected_size=size,
            expected_sha256=sha256,
            label=f"PerTh pretrained file {name}",
        )


def _validate_file(
    path: Path,
    *,
    expected_size: int | None,
    expected_sha256: str,
    label: str,
) -> None:
    if not path.is_file() or path.is_symlink():
        raise ChatterboxAdapterError(f"{label} がありません: {path}")
    if expected_size is not None and path.stat().st_size != expected_size:
        raise ChatterboxAdapterError(f"{label} の file size が一致しません。")
    if _sha256_file(path) != expected_sha256:
        raise ChatterboxAdapterError(f"{label} の SHA-256 が一致しません。")


def _validate_waveform(waveform: Any) -> None:
    values = waveform
    if hasattr(values, "detach"):
        values = values.detach().cpu().reshape(-1).tolist()
    elif isinstance(values, (list, tuple)) and len(values) == 1 and isinstance(
        values[0],
        (list, tuple),
    ):
        values = values[0]
    if not isinstance(values, (list, tuple)) or not values:
        raise ChatterboxAdapterError("Chatterbox waveform が空です。")
    for value in values:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ChatterboxAdapterError(
                "Chatterbox waveform に非有限値があります。",
            )


def _validate_pcm16_wav(path: Path) -> None:
    if not path.is_file():
        raise ChatterboxAdapterError(f"adapter 出力がありません: {path}")
    try:
        with wave.open(str(path), "rb") as wav_file:
            valid = (
                wav_file.getnchannels() == 1
                and wav_file.getsampwidth() == 2
                and wav_file.getframerate() == SAMPLE_RATE_HZ
                and wav_file.getcomptype() == "NONE"
                and wav_file.getnframes() > 0
            )
    except (EOFError, OSError, wave.Error) as error:
        raise ChatterboxAdapterError(
            f"adapter 出力 WAV を読めません: {path}: {error}",
        ) from error
    if not valid:
        raise ChatterboxAdapterError(
            "adapter 出力は PCM16/24kHz/mono が必要です。",
        )


def _copy_peak(value: Mapping[str, Any]) -> dict[str, float]:
    if set(value) != {"allocated_mib", "reserved_mib"}:
        raise ChatterboxAdapterError("CUDA peak profile の項目が不正です。")
    result: dict[str, float] = {}
    for key in ("allocated_mib", "reserved_mib"):
        raw = value[key]
        if (
            isinstance(raw, bool)
            or not isinstance(raw, (int, float))
            or not math.isfinite(float(raw))
            or float(raw) < 0
        ):
            raise ChatterboxAdapterError("CUDA peak profile の値が不正です。")
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
        raise ChatterboxAdapterError(
            f"{section}.{key} は identifier が必要です: {result}",
        )
    return result


def _required_string(
    value: Mapping[str, Any],
    key: str,
    section: str,
) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ChatterboxAdapterError(
            f"{section}.{key} は空でない文字列が必要です。",
        )
    return result


def _distribution_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError as error:
        raise ChatterboxAdapterError(
            f"必須 package がありません: {distribution}",
        ) from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
