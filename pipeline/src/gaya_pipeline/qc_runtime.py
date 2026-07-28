from __future__ import annotations

import importlib
import importlib.metadata
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from gaya_pipeline.qc import RuntimeInspection


class QCRuntimeError(RuntimeError):
    pass


TORCH_VERSION = "2.11.0+cu130"
TRANSFORMERS_VERSION = "5.3.0"
LIBROSA_VERSION = "0.11.0"
MODEL_REPOSITORY = "sbintuitions/kana-whisper"
MODEL_REVISION = "88ecb3d79c5846cb4fcf76f4107b84c8fa2acd82"
SAMPLE_RATE_HZ = 16_000
DEVICE = "cuda:0"
DTYPE = "float16"
BEAM_SIZE = 1


class KanaWhisperQCRuntime:
    def __init__(self, model_dir: Path) -> None:
        self._model_dir = model_dir.resolve()
        self._model: Any | None = None
        self._processor: Any | None = None
        self._torch: Any | None = None
        self._librosa: Any | None = None
        self._numpy: Any | None = None
        self._ffmpeg: str | None = None

    def prepare(self) -> None:
        self._ensure_loaded()

    def describe(self) -> dict[str, Any]:
        _require_distribution_version("torch", TORCH_VERSION)
        _require_distribution_version("transformers", TRANSFORMERS_VERSION)
        _require_distribution_version("librosa", LIBROSA_VERSION)
        return {
            "asr_model": MODEL_REPOSITORY,
            "asr_revision": MODEL_REVISION,
            "torch_version": TORCH_VERSION,
            "transformers_version": TRANSFORMERS_VERSION,
            "librosa_version": LIBROSA_VERSION,
            "device": DEVICE,
            "dtype": DTYPE,
            "beam_size": BEAM_SIZE,
            "prosody_thresholds": "report_only",
        }

    def inspect(
        self,
        audio_path: Path,
        *,
        mora_count: int,
    ) -> RuntimeInspection:
        self._ensure_loaded()
        assert self._model is not None
        assert self._processor is not None
        assert self._torch is not None
        assert self._librosa is not None
        assert self._numpy is not None

        samples = self._decode_audio(audio_path)
        prosody = self._analyze_prosody(samples, mora_count=mora_count)
        if not prosody["active_speech_sec"]:
            return RuntimeInspection(
                transcript="",
                average_log_probability=None,
                prosody=prosody,
            )
        try:
            inputs = self._processor(
                samples,
                sampling_rate=SAMPLE_RATE_HZ,
                return_tensors="pt",
                return_attention_mask=True,
            )
            input_features = inputs.input_features.to(
                device=DEVICE,
                dtype=self._torch.float16,
            )
            attention_mask = inputs.attention_mask.to(device=DEVICE)
            with self._torch.inference_mode():
                generated_ids = self._model.generate(
                    input_features,
                    attention_mask=attention_mask,
                    language="ja",
                    task="transcribe",
                    num_beams=BEAM_SIZE,
                )
            transcript = self._processor.batch_decode(
                generated_ids,
                skip_special_tokens=True,
            )[0].strip()
        except Exception as error:
            raise QCRuntimeError(f"Kana ASRに失敗しました: {audio_path}") from error

        return RuntimeInspection(
            transcript=transcript,
            average_log_probability=None,
            prosody=prosody,
        )

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        self.describe()
        try:
            huggingface_hub = importlib.import_module("huggingface_hub")
            transformers = importlib.import_module("transformers")
            self._torch = importlib.import_module("torch")
            self._librosa = importlib.import_module("librosa")
            self._numpy = importlib.import_module("numpy")
        except ImportError as error:
            raise QCRuntimeError(
                "QC依存関係がありません。uv sync --locked --extra qc を実行してください。",
            ) from error
        transformers.utils.logging.set_verbosity_error()

        if not bool(self._torch.cuda.is_available()):
            raise QCRuntimeError("Kana ASRにはCUDAが必要です。")
        if self._torch.version.cuda != "13.0":
            raise QCRuntimeError(
                "torch CUDA version が一致しません: "
                f"expected=13.0, actual={self._torch.version.cuda}",
            )
        capability = self._torch.cuda.get_device_capability(0)
        if capability[0] < 7:
            raise QCRuntimeError(
                f"GPUがFP16推論要件を満たしません: capability={capability}",
            )
        self._ffmpeg = shutil.which("ffmpeg")
        if self._ffmpeg is None:
            raise QCRuntimeError("ffmpeg が見つかりません。")

        self._model_dir.mkdir(parents=True, exist_ok=True)
        try:
            snapshot_path = huggingface_hub.snapshot_download(
                repo_id=MODEL_REPOSITORY,
                revision=MODEL_REVISION,
                local_dir=self._model_dir,
            )
            self._processor = transformers.AutoProcessor.from_pretrained(
                snapshot_path,
                local_files_only=True,
            )
            self._model = (
                transformers.AutoModelForSpeechSeq2Seq.from_pretrained(
                    snapshot_path,
                    dtype=self._torch.float16,
                    use_safetensors=True,
                    local_files_only=True,
                )
                .to(DEVICE)
                .eval()
            )
        except Exception as error:
            raise QCRuntimeError(
                f"固定Kana ASR modelの取得・読み込みに失敗しました: "
                f"{MODEL_REPOSITORY}@{MODEL_REVISION}",
            ) from error

    def _decode_audio(self, audio_path: Path) -> Any:
        assert self._ffmpeg is not None
        assert self._numpy is not None
        try:
            result = subprocess.run(
                [
                    self._ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-i",
                    str(audio_path),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-sn",
                    "-dn",
                    "-ar",
                    str(SAMPLE_RATE_HZ),
                    "-ac",
                    "1",
                    "-f",
                    "f32le",
                    "-",
                ],
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise QCRuntimeError(f"音声のdecodeに失敗しました: {audio_path}") from error
        samples = self._numpy.frombuffer(result.stdout, dtype=self._numpy.float32)
        if (
            samples.ndim != 1
            or samples.size == 0
            or not bool(self._numpy.isfinite(samples).all())
        ):
            raise QCRuntimeError(f"decode済み音声が不正です: {audio_path}")
        return samples

    def _analyze_prosody(
        self,
        samples: Any,
        *,
        mora_count: int,
    ) -> dict[str, Any]:
        assert self._librosa is not None
        assert self._numpy is not None
        librosa = self._librosa
        numpy = self._numpy

        duration_sec = samples.size / SAMPLE_RATE_HZ
        intervals = librosa.effects.split(
            samples,
            top_db=35,
            frame_length=1024,
            hop_length=256,
        )
        active_sec = sum(
            (int(end) - int(start)) / SAMPLE_RATE_HZ
            for start, end in intervals
        )
        internal_pauses = [
            (int(intervals[index + 1][0]) - int(intervals[index][1]))
            / SAMPLE_RATE_HZ
            for index in range(max(0, len(intervals) - 1))
        ]
        internal_pauses = [pause for pause in internal_pauses if pause >= 0.08]
        leading_silence_sec = (
            int(intervals[0][0]) / SAMPLE_RATE_HZ if len(intervals) else duration_sec
        )
        trailing_silence_sec = (
            (samples.size - int(intervals[-1][1])) / SAMPLE_RATE_HZ
            if len(intervals)
            else duration_sec
        )

        f0, voiced_flag, _ = librosa.pyin(
            samples,
            fmin=65,
            fmax=1000,
            sr=SAMPLE_RATE_HZ,
            frame_length=1024,
            hop_length=256,
        )
        valid_f0 = f0[numpy.isfinite(f0)]
        voiced_ratio = (
            float(numpy.count_nonzero(voiced_flag)) / len(voiced_flag)
            if len(voiced_flag)
            else 0.0
        )
        if valid_f0.size:
            median_f0 = float(numpy.median(valid_f0))
            semitones = 12 * numpy.log2(valid_f0 / median_f0)
            f0_report = {
                "median_hz": _rounded_or_none(median_f0),
                "p10_hz": _rounded_or_none(float(numpy.percentile(valid_f0, 10))),
                "p90_hz": _rounded_or_none(float(numpy.percentile(valid_f0, 90))),
                "semitone_std": _rounded_or_none(float(numpy.std(semitones))),
                "voiced_ratio": _rounded_or_none(voiced_ratio),
            }
        else:
            f0_report = {
                "median_hz": None,
                "p10_hz": None,
                "p90_hz": None,
                "semitone_std": None,
                "voiced_ratio": 0.0,
            }

        rms = librosa.feature.rms(
            y=samples,
            frame_length=1024,
            hop_length=256,
        )[0]
        rms_db = librosa.amplitude_to_db(rms, ref=1.0)
        finite_rms_db = rms_db[numpy.isfinite(rms_db)]
        energy_report = {
            "median_dbfs": (
                _rounded_or_none(float(numpy.median(finite_rms_db)))
                if finite_rms_db.size
                else None
            ),
            "p95_dbfs": (
                _rounded_or_none(float(numpy.percentile(finite_rms_db, 95)))
                if finite_rms_db.size
                else None
            ),
        }
        return {
            "duration_sec": _rounded_or_none(duration_sec),
            "active_speech_sec": _rounded_or_none(active_sec),
            "estimated_mora_count": mora_count,
            "overall_mora_per_sec": (
                _rounded_or_none(mora_count / duration_sec)
                if mora_count and duration_sec > 0
                else None
            ),
            "active_mora_per_sec": (
                _rounded_or_none(mora_count / active_sec)
                if mora_count and active_sec > 0
                else None
            ),
            "pause": {
                "internal_count": len(internal_pauses),
                "internal_total_sec": _rounded_or_none(sum(internal_pauses)),
                "internal_longest_sec": _rounded_or_none(
                    max(internal_pauses, default=0.0),
                ),
                "leading_sec": _rounded_or_none(leading_silence_sec),
                "trailing_sec": _rounded_or_none(trailing_silence_sec),
            },
            "f0": f0_report,
            "energy": energy_report,
        }


def _require_distribution_version(name: str, expected: str) -> None:
    try:
        actual = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError as error:
        raise QCRuntimeError(
            "QC依存関係がありません。"
            "uv sync --locked --extra qc を実行してください。",
        ) from error
    if actual != expected:
        raise QCRuntimeError(
            f"{name} version が一致しません: expected={expected}, actual={actual}",
        )


def _rounded_or_none(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 6)
