from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import os
import platform
import sys
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from gaya_pipeline.adapters.base import (
    Capabilities,
    LineJob,
    ModelProfile,
    TakeContext,
    TakeRecipe,
    require_take_context,
)

MODEL_ID = "supertonic-3"
MODEL_NAME = "Supertonic 3"
UPSTREAM_REPOSITORY = "supertone-inc/supertonic"
UPSTREAM_REVISION = "7e2804f96016a7028cb1ed627353c61c1e9dd281"
SDK_REPOSITORY = "supertone-inc/supertonic-py"
SDK_REVISION = "908a56486e821e833a80530ff0cae3ad0b046fce"
SDK_VERSION = "1.3.1"
WEIGHTS_REPOSITORY = "Supertone/supertonic-3"
WEIGHTS_REVISION = "724fb5abbf5502583fb520898d45929e62f02c0b"
TTS_ASSET_VERSION = "v1.7.3"
ONNXRUNTIME_VERSION = "1.23.1"
NUMPY_VERSION = "2.3.3"
SOUNDFILE_VERSION = "0.13.1"
MODEL_ROOT_ENV = "GAYA_SUPERTONIC3_ROOT"
LANGUAGE_ID = "ja"
SAMPLE_RATE_HZ = 44_100
TOTAL_STEPS = 8
SPEED = 1.05
MAX_CHUNK_LENGTH = 300
SILENCE_DURATION_SEC = 0.3
SEED = 42
INTRA_OP_THREADS = 10
INTER_OP_THREADS = 1
EXPECTED_PROVIDERS = ("CPUExecutionProvider",)
EXPECTED_VOICE_STYLES = (
    "F1",
    "F2",
    "F3",
    "F4",
    "F5",
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
)

MODEL_FILES: dict[str, tuple[int, str]] = {
    "LICENSE": (
        15_007,
        "0d944a9110fed9a9602d60e0423a272903e7bd21ab060490774efc77c2275e9f",
    ),
    "README.md": (
        5_390,
        "168fda97edbd2a80167b105ff4a3b62a169bc93fe2611b3a5d04ecf983f62438",
    ),
    "config.json": (
        174,
        "4099082b107a9d4029849ac76b89eca65e03732660969c2babe5bf308c7357f2",
    ),
    "onnx/duration_predictor.onnx": (
        3_700_147,
        "c3eb91414d5ff8a7a239b7fe9e34e7e2bf8a8140d8375ffb14718b1c639325db",
    ),
    "onnx/text_encoder.onnx": (
        36_416_150,
        "c7befd5ea8c3119769e8a6c1486c4edc6a3bc8365c67621c881bbb774b9902ff",
    ),
    "onnx/tts.json": (
        8_253,
        "42078d3aef1cd43ab43021f3c54f47d2d75ceb4e75f627f118890128b06a0d09",
    ),
    "onnx/unicode_indexer.json": (
        277_676,
        "9bf7346e43883a81f8645c81224f786d43c5b57f3641f6e7671a7d6c493cb24f",
    ),
    "onnx/vector_estimator.onnx": (
        256_534_781,
        "883ac868ea0275ef0e991524dc64f16b3c0376efd7c320af6b53f5b780d7c61c",
    ),
    "onnx/vocoder.onnx": (
        101_424_195,
        "085de76dd8e8d5836d6ca66826601f615939218f90e519f70ee8a36ed2a4c4ba",
    ),
    "voice_styles/F1.json": (
        292_046,
        "bbdec6ee00231c2c742ad05483df5334cab3b52fda3ba38e6a07059c4563dbc2",
    ),
    "voice_styles/F2.json": (
        292_423,
        "7c722c6a72707b1a77f035d67f0d1351ba187738e06f7683e8c72b1df3477fc6",
    ),
    "voice_styles/F3.json": (
        290_794,
        "12f6ef2573baa2defa1128069cb59f203e3ab67c92af77b42df8a0e3a2f7c6ab",
    ),
    "voice_styles/F4.json": (
        291_808,
        "c2fa764c1225a76dfc3e2c73e8aa4f70d9ee48793860eb34c295fff01c2e032b",
    ),
    "voice_styles/F5.json": (
        291_479,
        "45966e73316415626cf41a7d1c6f3b4c70dbc1ba2bee5c1978ef0ce33244fc8d",
    ),
    "voice_styles/M1.json": (
        291_748,
        "e35604687f5d23694b8e91593a93eec0e4eca6c0b02bb8ed69139ab2ea6b0a5b",
    ),
    "voice_styles/M2.json": (
        292_055,
        "b76cbf62bac707c710cf0ae5aba5e31eea1a6339a9734bfae33ab98499534a50",
    ),
    "voice_styles/M3.json": (
        290_198,
        "ea1ac35ccb91b0d7ecad533a2fbd0eec10c91513d8951e3b25fbba99954e159b",
    ),
    "voice_styles/M4.json": (
        291_522,
        "ca8eefad4fcd989c9379032ff3e50738adc547eeb5e221b82593a6d7b3bac303",
    ),
    "voice_styles/M5.json": (
        291_469,
        "dd22b92740314321f8ae11c5e87f8dd60d060f15dd3a632b5adf77f471f77af2",
    ),
}

VOICE_ASSIGNMENTS = {
    ("tavern-night", "barmaid"): "F2",
    ("tavern-night", "drunkard"): "M1",
    ("tavern-night", "old-regular"): "M5",
    ("market-day", "fruit-vendor"): "M1",
    ("market-day", "shopper"): "F1",
    ("market-day", "street-kid"): "F2",
}

PROFILE_VERSION = (
    f"supertonic {SDK_VERSION} ({SDK_REPOSITORY}@{SDK_REVISION}); "
    f"{WEIGHTS_REPOSITORY}@{WEIGHTS_REVISION}; tts {TTS_ASSET_VERSION}"
)


class Supertonic3AdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class _PreparedInput:
    source_text: str
    tts_text: str
    reading_source: str
    voice_style: str
    voice_style_sha256: str
    voice_selection_source: str

    def as_generation_input(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "tts_text": self.tts_text,
            "reading_source": self.reading_source,
            "language_id": LANGUAGE_ID,
            "voice_style": self.voice_style,
            "voice_style_sha256": self.voice_style_sha256,
            "voice_selection_source": self.voice_selection_source,
        }


class _Runtime(Protocol):
    def prepare(self, model_root: Path) -> None: ...

    def synthesize(
        self,
        *,
        text: str,
        voice_style: str,
        output_wav: Path,
        seed: int,
    ) -> Mapping[str, Any]: ...


class _LocalRuntime:
    def __init__(self) -> None:
        self._tts: Any | None = None
        self._numpy: Any | None = None
        self._soundfile: Any | None = None

    def prepare(self, model_root: Path) -> None:
        if self._tts is not None:
            raise Supertonic3AdapterError(
                "Supertonic runtime はすでに prepare 済みです。",
            )
        _validate_windows_runtime()
        _validate_model_root(model_root)
        _require_distribution_version("supertonic", SDK_VERSION)
        _require_distribution_version("onnxruntime", ONNXRUNTIME_VERSION)
        _require_distribution_version("numpy", NUMPY_VERSION)
        _require_distribution_version("soundfile", SOUNDFILE_VERSION)

        try:
            numpy = importlib.import_module("numpy")
            onnxruntime = importlib.import_module("onnxruntime")
            soundfile = importlib.import_module("soundfile")
            supertonic = importlib.import_module("supertonic")
        except ImportError as error:
            raise Supertonic3AdapterError(
                f"Supertonic runtime の import に失敗しました: {error}",
            ) from error

        if "CPUExecutionProvider" not in onnxruntime.get_available_providers():
            raise Supertonic3AdapterError(
                "ONNX Runtime に CPUExecutionProvider がありません。",
            )

        try:
            tts = supertonic.TTS(
                model="supertonic-3",
                model_dir=model_root,
                auto_download=False,
                intra_op_num_threads=INTRA_OP_THREADS,
                inter_op_num_threads=INTER_OP_THREADS,
            )
        except Exception as error:
            raise Supertonic3AdapterError(
                f"Supertonic 3 のローカル読込に失敗しました: {error}",
            ) from error

        if str(getattr(tts, "model_name", "")) != "supertonic-3":
            raise Supertonic3AdapterError(
                f"Supertonic model identity が不正です: {tts.model_name!r}",
            )
        if int(getattr(tts, "sample_rate", 0)) != SAMPLE_RATE_HZ:
            raise Supertonic3AdapterError(
                "Supertonic native sample rate が一致しません: "
                f"expected={SAMPLE_RATE_HZ}, actual={getattr(tts, 'sample_rate', None)!r}",
            )
        if tuple(getattr(tts, "voice_style_names", ())) != EXPECTED_VOICE_STYLES:
            raise Supertonic3AdapterError(
                "Supertonic preset voice inventory が一致しません。",
            )

        model = getattr(tts, "model", None)
        session_names = (
            "dp_ort",
            "text_enc_ort",
            "vector_est_ort",
            "vocoder_ort",
        )
        for name in session_names:
            session = getattr(model, name, None)
            providers = (
                tuple(session.get_providers())
                if session is not None and hasattr(session, "get_providers")
                else ()
            )
            if providers != EXPECTED_PROVIDERS:
                raise Supertonic3AdapterError(
                    f"{name} provider が一致しません: "
                    f"expected={EXPECTED_PROVIDERS}, actual={providers}",
                )

        configs = getattr(model, "cfgs", None)
        if not isinstance(configs, dict):
            raise Supertonic3AdapterError("Supertonic model config が不正です。")
        if configs.get("tts_version") != TTS_ASSET_VERSION:
            raise Supertonic3AdapterError(
                "Supertonic tts_version が一致しません: "
                f"expected={TTS_ASSET_VERSION}, actual={configs.get('tts_version')!r}",
            )

        self._tts = tts
        self._numpy = numpy
        self._soundfile = soundfile

    def synthesize(
        self,
        *,
        text: str,
        voice_style: str,
        output_wav: Path,
        seed: int,
    ) -> Mapping[str, Any]:
        if self._tts is None or self._numpy is None or self._soundfile is None:
            raise Supertonic3AdapterError(
                "Supertonic runtime が prepare されていません。",
            )

        try:
            style = self._tts.get_voice_style(voice_style)
            self._numpy.random.seed(seed)
            waveform, durations = self._tts.synthesize(
                text,
                style,
                total_steps=TOTAL_STEPS,
                speed=SPEED,
                max_chunk_length=MAX_CHUNK_LENGTH,
                silence_duration=SILENCE_DURATION_SEC,
                lang=LANGUAGE_ID,
                verbose=False,
            )
        except Exception as error:
            raise Supertonic3AdapterError(
                f"Supertonic 3 synthesis に失敗しました: {error}",
            ) from error

        samples = self._numpy.asarray(waveform)
        duration_values = self._numpy.asarray(durations)
        if samples.dtype != self._numpy.float32:
            raise Supertonic3AdapterError(
                f"Supertonic waveform dtype が不正です: {samples.dtype}",
            )
        if samples.ndim != 2 or samples.shape[0] != 1 or samples.shape[1] == 0:
            raise Supertonic3AdapterError(
                f"Supertonic waveform shape が不正です: {samples.shape}",
            )
        if not bool(self._numpy.isfinite(samples).all()):
            raise Supertonic3AdapterError(
                "Supertonic waveform に NaN または Inf があります。",
            )
        peak = float(self._numpy.max(self._numpy.abs(samples)))
        if peak > 1.0:
            raise Supertonic3AdapterError(
                f"Supertonic waveform が [-1, 1] を超えています: peak={peak}",
            )
        if (
            duration_values.shape != (1,)
            or not bool(self._numpy.isfinite(duration_values).all())
            or float(duration_values[0]) <= 0
        ):
            raise Supertonic3AdapterError(
                f"Supertonic duration が不正です: {duration_values!r}",
            )

        output_wav.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._soundfile.write(
                str(output_wav),
                samples.reshape(-1),
                SAMPLE_RATE_HZ,
                subtype="PCM_16",
                format="WAV",
            )
        except Exception as error:
            raise Supertonic3AdapterError(
                f"Supertonic WAV の保存に失敗しました: {error}",
            ) from error
        audio = _inspect_pcm_wav(output_wav)
        return {
            "seed": seed,
            "language_id": LANGUAGE_ID,
            "voice_style": voice_style,
            "sample_rate_hz": audio["sample_rate_hz"],
            "channels": audio["channels"],
            "sample_width_bytes": audio["sample_width_bytes"],
            "returned_duration_sec": float(duration_values[0]),
            "waveform_peak": peak,
            "onnx_providers": list(EXPECTED_PROVIDERS),
        }


class Supertonic3Adapter:
    profile = ModelProfile(
        id=MODEL_ID,
        name=MODEL_NAME,
        version=PROFILE_VERSION,
        license_note=(
            "コードと SDK は MIT、公式重みは BigScience Open RAIL-M。"
            "商用の事前生成音声は禁止されないが、出力が機械生成であることを"
            "明確かつ理解可能な形で開示し、同ライセンスの禁止用途に使用しない。"
            "固定 preset voice のみを用い、clone と Voice Builder は使用しない。"
            "公式資料は電子透かしを開示していないが、不存在は保証しない。"
        ),
        capabilities=Capabilities(
            emotion=False,
            voice_prompt=False,
            clone=False,
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
                ("total_steps", TOTAL_STEPS),
            ),
            supports_multiple=True,
        )

    def __init__(self, *, runtime: _Runtime | None = None) -> None:
        self._runtime = runtime if runtime is not None else _LocalRuntime()
        self._prepared_inputs: dict[tuple[str, str], _PreparedInput] = {}
        self._prepared = False

    def prepare(
        self,
        jobs: Sequence[LineJob],
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        del artifacts_dir, voices_dir
        self._prepared = False
        self._prepared_inputs.clear()

        model_root = _resolve_model_root()
        for job in jobs:
            key = _job_key(job)
            if key in self._prepared_inputs:
                raise Supertonic3AdapterError(
                    f"同じ line job が重複しています: {key[0]}/{key[1]}",
                )
            self._prepared_inputs[key] = _prepare_input(job)

        self._runtime.prepare(model_root)
        self._prepared = True

    def generation_params(self) -> Mapping[str, Any]:
        return {
            "upstream_repository": UPSTREAM_REPOSITORY,
            "upstream_revision": UPSTREAM_REVISION,
            "sdk_repository": SDK_REPOSITORY,
            "sdk_revision": SDK_REVISION,
            "sdk_version": SDK_VERSION,
            "weights_repository": WEIGHTS_REPOSITORY,
            "weights_revision": WEIGHTS_REVISION,
            "tts_asset_version": TTS_ASSET_VERSION,
            "model_root_environment": MODEL_ROOT_ENV,
            "model_files": {
                path: {"size": size, "sha256": sha256}
                for path, (size, sha256) in MODEL_FILES.items()
            },
            "architecture": "supertonic-3",
            "language_id": LANGUAGE_ID,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "provider": EXPECTED_PROVIDERS[0],
            "supertonic_version": SDK_VERSION,
            "onnxruntime_version": ONNXRUNTIME_VERSION,
            "numpy_version": NUMPY_VERSION,
            "soundfile_version": SOUNDFILE_VERSION,
            "total_steps": TOTAL_STEPS,
            "speed": SPEED,
            "max_chunk_length": MAX_CHUNK_LENGTH,
            "silence_duration_sec": SILENCE_DURATION_SEC,
            "intra_op_num_threads": INTRA_OP_THREADS,
            "inter_op_num_threads": INTER_OP_THREADS,
            "voice_assignments": {
                f"{scenario}/{character}": voice
                for (scenario, character), voice in VOICE_ASSIGNMENTS.items()
            },
            "expression_tags": False,
            "voice_builder": False,
            "auto_download": False,
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
        try:
            realized = self._runtime.synthesize(
                text=prepared.tts_text,
                voice_style=prepared.voice_style,
                output_wav=output_wav,
                seed=seed,
            )
        except Exception as error:
            if isinstance(error, Supertonic3AdapterError):
                raise
            raise Supertonic3AdapterError(
                f"Supertonic 3 生成 ({job.scenario_id}/{job.line_id}) "
                f"に失敗しました: {error}",
            ) from error
        if not output_wav.is_file():
            raise Supertonic3AdapterError(
                f"adapter 出力がありません: {output_wav}",
            )
        return {
            **dict(realized),
            "seed": seed,
            "sampling": take_context.sampling_dict(),
            "reading_source": prepared.reading_source,
            "voice_selection_source": prepared.voice_selection_source,
            "voice_style_sha256": prepared.voice_style_sha256,
        }

    def _prepared_input(self, job: LineJob) -> _PreparedInput:
        if not self._prepared:
            raise Supertonic3AdapterError("prepare() が完了していません。")
        key = _job_key(job)
        try:
            return self._prepared_inputs[key]
        except KeyError as error:
            raise Supertonic3AdapterError(
                f"prepare 済み input がありません: {key[0]}/{key[1]}",
            ) from error


def _prepare_input(job: LineJob) -> _PreparedInput:
    if job.locale != LANGUAGE_ID:
        raise Supertonic3AdapterError(
            f"Supertonic 3 adapter は Japanese 固定です: locale={job.locale}",
        )
    source_text = _required_string(job.line, "text", "line")
    reading = job.line.get("reading")
    if reading is None:
        tts_text = source_text
        reading_source = "line.text"
    elif isinstance(reading, str) and reading.strip():
        tts_text = reading
        reading_source = "line.reading"
    else:
        raise Supertonic3AdapterError(
            "line.reading は non-empty string または null が必要です。",
        )
    if "<" in tts_text or ">" in tts_text:
        raise Supertonic3AdapterError(
            "Supertonic expression/language tag は使用できません。",
        )

    character_id = _required_string(job.character, "id", "character")
    assignment_key = (job.scenario_id, character_id)
    try:
        voice_style = VOICE_ASSIGNMENTS[assignment_key]
    except KeyError as error:
        raise Supertonic3AdapterError(
            "固定 voice assignment がありません: "
            f"{assignment_key[0]}/{assignment_key[1]}",
        ) from error

    style_path = f"voice_styles/{voice_style}.json"
    return _PreparedInput(
        source_text=source_text,
        tts_text=tts_text,
        reading_source=reading_source,
        voice_style=voice_style,
        voice_style_sha256=MODEL_FILES[style_path][1],
        voice_selection_source=(
            f"adapter.assignment:{assignment_key[0]}/{assignment_key[1]}"
        ),
    )


def _resolve_model_root() -> Path:
    raw_root = os.environ.get(MODEL_ROOT_ENV)
    if raw_root is None or not raw_root.strip():
        raise Supertonic3AdapterError(
            f"{MODEL_ROOT_ENV} に固定 model root の絶対パスを設定してください。",
        )
    root = Path(raw_root)
    if not root.is_absolute():
        raise Supertonic3AdapterError(
            f"{MODEL_ROOT_ENV} は絶対パスである必要があります: {raw_root!r}",
        )
    if not root.is_dir():
        raise Supertonic3AdapterError(
            f"Supertonic model root が存在しません: {root}",
        )
    if _is_reparse_point(root):
        raise Supertonic3AdapterError(
            f"Supertonic model root に symlink/reparse point は使えません: {root}",
        )
    return root.resolve(strict=True)


def _validate_model_root(
    root: Path,
    *,
    expected_files: Mapping[str, tuple[int, str]] = MODEL_FILES,
) -> None:
    actual_files: dict[str, Path] = {}
    for current, directory_names, file_names in os.walk(root):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        if relative_dir == Path(".") and ".cache" in directory_names:
            directory_names.remove(".cache")
        for directory_name in directory_names:
            path = current_path / directory_name
            if _is_reparse_point(path):
                raise Supertonic3AdapterError(
                    f"model asset directory に symlink/reparse point があります: {path}",
                )
        for file_name in file_names:
            path = current_path / file_name
            if _is_reparse_point(path):
                raise Supertonic3AdapterError(
                    f"model asset に symlink/reparse point があります: {path}",
                )
            relative = path.relative_to(root).as_posix()
            actual_files[relative] = path

    expected_names = set(expected_files)
    actual_names = set(actual_files)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        raise Supertonic3AdapterError(
            "Supertonic model inventory が一致しません: "
            f"missing={missing}, extra={extra}",
        )

    for relative, (expected_size, expected_sha256) in expected_files.items():
        path = actual_files[relative]
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            raise Supertonic3AdapterError(
                f"model asset size が一致しません: {relative}, "
                f"expected={expected_size}, actual={actual_size}",
            )
        actual_sha256 = _sha256_file(path)
        if actual_sha256 != expected_sha256:
            raise Supertonic3AdapterError(
                f"model asset SHA-256 が一致しません: {relative}, "
                f"expected={expected_sha256}, actual={actual_sha256}",
            )


def _validate_windows_runtime() -> None:
    if platform.system() != "Windows":
        raise Supertonic3AdapterError(
            f"Supertonic 3 CPU baseline は Windows native 固定です: {platform.system()}",
        )
    if sys.version_info[:2] != (3, 12):
        raise Supertonic3AdapterError(
            "Supertonic 3 CPU baseline は Python 3.12 固定です: "
            f"{platform.python_version()}",
        )


def _require_distribution_version(distribution: str, expected: str) -> None:
    try:
        actual = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError as error:
        raise Supertonic3AdapterError(
            f"必要な package がありません: {distribution}=={expected}",
        ) from error
    if actual != expected:
        raise Supertonic3AdapterError(
            f"{distribution} version が一致しません: "
            f"expected={expected}, actual={actual}",
        )


def _inspect_pcm_wav(path: Path) -> dict[str, int]:
    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate_hz = wav.getframerate()
            sample_width_bytes = wav.getsampwidth()
            frame_count = wav.getnframes()
            compression = wav.getcomptype()
    except (OSError, wave.Error) as error:
        raise Supertonic3AdapterError(
            f"Supertonic 出力 WAV が不正です: {error}",
        ) from error
    if (
        channels != 1
        or sample_rate_hz != SAMPLE_RATE_HZ
        or sample_width_bytes != 2
        or frame_count <= 0
        or compression != "NONE"
    ):
        raise Supertonic3AdapterError(
            "Supertonic 出力は 44.1kHz mono PCM16 WAV である必要があります: "
            f"channels={channels}, sample_rate={sample_rate_hz}, "
            f"sample_width={sample_width_bytes}, frames={frame_count}, "
            f"compression={compression}",
        )
    return {
        "channels": channels,
        "sample_rate_hz": sample_rate_hz,
        "sample_width_bytes": sample_width_bytes,
        "frame_count": frame_count,
    }


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & 0x400)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_string(
    mapping: Mapping[str, Any],
    key: str,
    owner: str,
) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Supertonic3AdapterError(
            f"{owner}.{key} は空でない文字列である必要があります。",
        )
    return value


def _job_key(job: LineJob) -> tuple[str, str]:
    return job.scenario_id, job.line_id
