from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AudioProcessingError(RuntimeError):
    pass


LOUDNESS_TARGET_TOLERANCE_LU = 0.2
LOUDNESS_GATE_TOLERANCE_LU = 1.5
TRUE_PEAK_TOLERANCE_DB = 0.1
MAX_LIMITER_CORRECTION_PASSES = 2
LIMITER_SAMPLE_RATE_HZ = 192_000


@dataclass(frozen=True)
class AudioTools:
    ffmpeg: str
    ffprobe: str


@dataclass(frozen=True)
class PostprocessProfile:
    algorithm_version: int = 6
    integrated_lufs: float = -18.0
    loudness_range_lu: float = 7.0
    true_peak_dbtp: float = -1.0
    sample_rate_hz: int = 48_000
    channels: int = 1
    codec: str = "libopus"
    bitrate_bps: int = 64_000
    vbr: str = "on"
    application: str = "audio"

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithm_version": self.algorithm_version,
            "integrated_lufs": self.integrated_lufs,
            "loudness_range_lu": self.loudness_range_lu,
            "true_peak_dbtp": self.true_peak_dbtp,
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "codec": self.codec,
            "bitrate_bps": self.bitrate_bps,
            "vbr": self.vbr,
            "application": self.application,
        }


@dataclass(frozen=True)
class AudioProbe:
    codec_name: str
    sample_rate_hz: int
    channels: int
    duration_sec: float


@dataclass(frozen=True)
class LoudnessReport:
    integrated_lufs: float
    true_peak_dbtp: float
    loudness_range_lu: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "integrated_lufs": self.integrated_lufs,
            "true_peak_dbtp": self.true_peak_dbtp,
            "loudness_range_lu": self.loudness_range_lu,
        }


@dataclass(frozen=True)
class NormalizedLoudnessReport(LoudnessReport):
    normalization_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            **super().as_dict(),
            "normalization_type": self.normalization_type,
        }


@dataclass(frozen=True)
class EncodedLoudnessReport(LoudnessReport):
    def as_manifest_dict(
        self,
        profile: PostprocessProfile,
    ) -> dict[str, str | float | bool]:
        return {
            "source": "encoded_opus",
            "i_lufs": self.integrated_lufs,
            "tp_dbtp": self.true_peak_dbtp,
            "shortfall": (
                abs(self.integrated_lufs - profile.integrated_lufs)
                > LOUDNESS_TARGET_TOLERANCE_LU
            ),
        }


def find_audio_tools() -> AudioTools:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        missing = [
            name
            for name, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe))
            if path is None
        ]
        raise AudioProcessingError(
            f"必要な音声ツールが見つかりません: {', '.join(missing)}",
        )

    encoder_result = _run([ffmpeg, "-hide_banner", "-encoders"])
    if "libopus" not in encoder_result.stdout:
        raise AudioProcessingError(
            "ffmpeg に libopus encoder が含まれていません。",
        )
    return AudioTools(ffmpeg=ffmpeg, ffprobe=ffprobe)


def normalize_wav(
    tools: AudioTools,
    input_wav: Path,
    output_wav: Path,
    profile: PostprocessProfile,
) -> NormalizedLoudnessReport:
    target = (
        f"I={profile.integrated_lufs:g}:"
        f"LRA={profile.loudness_range_lu:g}:"
        f"TP={profile.true_peak_dbtp:g}"
    )
    measurement = _measure_loudness(tools, input_wav, target)
    measured_filter = _measured_filter(measurement)

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    normalize_result = _run(
        [
            tools.ffmpeg,
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            "-nostdin",
            "-y",
            "-i",
            str(input_wav),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-af",
            (f"loudnorm={target}:{measured_filter}:linear=true:print_format=json"),
            "-ar",
            str(profile.sample_rate_hz),
            "-ac",
            str(profile.channels),
            "-c:a",
            "pcm_s16le",
            "-map_metadata",
            "-1",
            str(output_wav),
        ],
    )
    normalized = _parse_loudnorm_json(normalize_result.stderr)
    normalization_type = normalized.get("normalization_type")
    if normalization_type not in {"linear", "dynamic"}:
        raise AudioProcessingError(
            "loudnorm の normalization_type が不正です。",
        )
    final_report = _measure_normalized_output(
        tools,
        output_wav,
        target,
        normalization_type,
    )
    if _loudness_matches_profile(final_report, profile):
        return final_report

    correction_wav = output_wav.with_suffix(".limiter-correction.wav")
    correction_wav.unlink(missing_ok=True)
    try:
        for _ in range(MAX_LIMITER_CORRECTION_PASSES):
            gain_db = profile.integrated_lufs - final_report.integrated_lufs
            gain = 10 ** (gain_db / 20)
            limit = 10 ** (profile.true_peak_dbtp / 20)
            if not 0.015625 <= gain <= 64:
                break
            _run(
                [
                    tools.ffmpeg,
                    "-hide_banner",
                    "-nostats",
                    "-v",
                    "error",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(output_wav),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-sn",
                    "-dn",
                    "-af",
                    (
                        f"aresample={LIMITER_SAMPLE_RATE_HZ},"
                        f"alimiter=level_in={gain:.12g}:limit={limit:.12g}:"
                        "level=false:latency=true,"
                        f"aresample={profile.sample_rate_hz}"
                    ),
                    "-ar",
                    str(profile.sample_rate_hz),
                    "-ac",
                    str(profile.channels),
                    "-c:a",
                    "pcm_s16le",
                    "-map_metadata",
                    "-1",
                    str(correction_wav),
                ],
            )
            final_report = _measure_normalized_output(
                tools,
                correction_wav,
                target,
                "dynamic",
            )
            correction_wav.replace(output_wav)
            if _loudness_matches_profile(final_report, profile):
                return final_report
        _validate_loudness_report(
            final_report,
            profile,
            stage="正規化後 WAV",
        )
        return final_report
    finally:
        correction_wav.unlink(missing_ok=True)


def encode_opus(
    tools: AudioTools,
    input_wav: Path,
    output_opus: Path,
    profile: PostprocessProfile,
) -> None:
    output_opus.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            tools.ffmpeg,
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(input_wav),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-ar",
            str(profile.sample_rate_hz),
            "-ac",
            str(profile.channels),
            "-c:a",
            profile.codec,
            "-b:a",
            str(profile.bitrate_bps),
            "-vbr",
            profile.vbr,
            "-application",
            profile.application,
            "-map_metadata",
            "-1",
            "-fflags",
            "+bitexact",
            "-f",
            "opus",
            str(output_opus),
        ],
    )


def measure_encoded_opus(
    tools: AudioTools,
    input_opus: Path,
    profile: PostprocessProfile,
) -> EncodedLoudnessReport:
    target = (
        f"I={profile.integrated_lufs:g}:"
        f"LRA={profile.loudness_range_lu:g}:"
        f"TP={profile.true_peak_dbtp:g}"
    )
    measurement = _measure_loudness(tools, input_opus, target)
    report = EncodedLoudnessReport(
        integrated_lufs=_finite_value(measurement, "input_i"),
        true_peak_dbtp=_finite_value(measurement, "input_tp"),
        loudness_range_lu=_finite_value(measurement, "input_lra"),
    )
    _validate_loudness_report(
        report,
        profile,
        stage="エンコード後 Opus",
    )
    return report


def probe_audio(tools: AudioTools, audio_path: Path) -> AudioProbe:
    result = _run(
        [
            tools.ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels:format=duration",
            "-of",
            "json",
            str(audio_path),
        ],
    )
    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        duration_sec = float(payload["format"]["duration"])
        sample_rate_hz = int(stream["sample_rate"])
        channels = int(stream["channels"])
        codec_name = str(stream["codec_name"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as error:
        raise AudioProcessingError(
            f"ffprobe の出力を解釈できません: {audio_path}",
        ) from error

    if not math.isfinite(duration_sec) or duration_sec <= 0:
        raise AudioProcessingError(
            f"音声の長さが不正です: {audio_path}",
        )
    return AudioProbe(
        codec_name=codec_name,
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        duration_sec=duration_sec,
    )


def _measure_normalized_output(
    tools: AudioTools,
    output_wav: Path,
    target: str,
    normalization_type: str,
) -> NormalizedLoudnessReport:
    measurement = _measure_loudness(tools, output_wav, target)
    return NormalizedLoudnessReport(
        integrated_lufs=_finite_value(measurement, "input_i"),
        true_peak_dbtp=_finite_value(measurement, "input_tp"),
        loudness_range_lu=_finite_value(measurement, "input_lra"),
        normalization_type=normalization_type,
    )


def _measure_loudness(
    tools: AudioTools,
    audio_path: Path,
    target: str,
) -> dict[str, Any]:
    result = _run(
        [
            tools.ffmpeg,
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            "-nostdin",
            "-i",
            str(audio_path),
            "-map",
            "0:a:0",
            "-af",
            f"loudnorm={target}:print_format=json",
            "-f",
            "null",
            "-",
        ],
    )
    return _parse_loudnorm_json(result.stderr)


def _measured_filter(measurement: dict[str, Any]) -> str:
    measured_values = {
        "measured_I": _finite_value(measurement, "input_i"),
        "measured_TP": _finite_value(measurement, "input_tp"),
        "measured_LRA": _finite_value(measurement, "input_lra"),
        "measured_thresh": _finite_value(measurement, "input_thresh"),
        "offset": _finite_value(measurement, "target_offset"),
    }
    return ":".join(f"{name}={value:g}" for name, value in measured_values.items())


def _loudness_matches_profile(
    report: LoudnessReport,
    profile: PostprocessProfile,
) -> bool:
    return (
        abs(report.integrated_lufs - profile.integrated_lufs)
        <= LOUDNESS_TARGET_TOLERANCE_LU
        and report.true_peak_dbtp <= profile.true_peak_dbtp + TRUE_PEAK_TOLERANCE_DB
    )


def _validate_loudness_report(
    report: LoudnessReport,
    profile: PostprocessProfile,
    *,
    stage: str,
) -> None:
    if (
        abs(report.integrated_lufs - profile.integrated_lufs)
        <= LOUDNESS_GATE_TOLERANCE_LU
        and report.true_peak_dbtp <= profile.true_peak_dbtp + TRUE_PEAK_TOLERANCE_DB
    ):
        return
    raise AudioProcessingError(
        f"{stage} が loudness profile を満たしません: "
        f"I={report.integrated_lufs:g} LUFS "
        f"(target={profile.integrated_lufs:g}±{LOUDNESS_GATE_TOLERANCE_LU:g}), "
        f"TP={report.true_peak_dbtp:g} dBTP "
        f"(max={profile.true_peak_dbtp + TRUE_PEAK_TOLERANCE_DB:g})",
    )


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        if len(detail) > 2000:
            detail = detail[-2000:]
        raise AudioProcessingError(
            f"音声処理コマンドが失敗しました: {argv[0]}\n{detail}",
        ) from error


def _parse_loudnorm_json(stderr: str) -> dict[str, Any]:
    start = stderr.rfind("{")
    end = stderr.find("}", start)
    if start < 0 or end < 0:
        raise AudioProcessingError(
            "ffmpeg loudnorm の JSON 出力が見つかりません。",
        )
    try:
        payload = json.loads(stderr[start : end + 1])
    except json.JSONDecodeError as error:
        raise AudioProcessingError(
            "ffmpeg loudnorm の JSON 出力を解釈できません。",
        ) from error
    if not isinstance(payload, dict):
        raise AudioProcessingError(
            "ffmpeg loudnorm の JSON 出力が不正です。",
        )
    return payload


def _finite_value(payload: dict[str, Any], key: str) -> float:
    try:
        value = float(payload[key])
    except (KeyError, TypeError, ValueError) as error:
        raise AudioProcessingError(
            f"loudnorm の {key} が不正です。",
        ) from error
    if not math.isfinite(value):
        raise AudioProcessingError(
            f"loudnorm の {key} が有限値ではありません。",
        )
    return value
