from __future__ import annotations

import json
import math
import struct
import subprocess
import wave
from pathlib import Path

import pytest
from gaya_pipeline.audio import (
    AudioProcessingError,
    EncodedLoudnessReport,
    LoudnessReport,
    PostprocessProfile,
    _validate_loudness_report,
    encode_opus,
    find_audio_tools,
    measure_encoded_opus,
    normalize_wav,
)


def _write_high_crest_wav(path: Path) -> None:
    sample_rate = 24_000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames: list[bytes] = []
        for index in range(sample_rate * 3):
            sample = 0.005 * math.sin(
                2 * math.pi * 220 * index / sample_rate,
            )
            if sample_rate <= index < sample_rate + 120:
                sample += 0.92
            sample = max(-1.0, min(1.0, sample))
            frames.append(struct.pack("<h", round(sample * 32767)))
        output.writeframes(b"".join(frames))


def _write_intersample_overshoot_wav(path: Path) -> None:
    sample_rate = 24_000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames: list[bytes] = []
        for index in range(sample_rate * 3):
            sample = 0.02 * math.sin(
                2 * math.pi * 220 * index / sample_rate,
            )
            if sample_rate <= index < sample_rate + 120:
                sample += 0.92 * math.sin(
                    2
                    * math.pi
                    * 10_000
                    * (index - sample_rate)
                    / sample_rate
                    + math.pi / 4,
                )
            sample = max(-1.0, min(1.0, sample))
            frames.append(struct.pack("<h", round(sample * 32767)))
        output.writeframes(b"".join(frames))


def _write_sine_wav(path: Path) -> None:
    sample_rate = 24_000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(
            b"".join(
                struct.pack(
                    "<h",
                    round(
                        0.1
                        * math.sin(2 * math.pi * 220 * index / sample_rate)
                        * 32767
                    ),
                )
                for index in range(sample_rate * 3)
            )
        )


def _measure_final_wav(path: Path) -> tuple[float, float]:
    tools = find_audio_tools()
    result = subprocess.run(
        [
            tools.ffmpeg,
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            "-nostdin",
            "-i",
            str(path),
            "-af",
            "loudnorm=I=-18:LRA=7:TP=-1:print_format=json",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    start = result.stderr.rfind("{")
    end = result.stderr.find("}", start)
    measurement = json.loads(result.stderr[start : end + 1])
    return float(measurement["input_i"]), float(measurement["input_tp"])


def test_dynamic_correction_hits_profile_and_reports_final_pcm(
    tmp_path: Path,
) -> None:
    source_wav = tmp_path / "source.wav"
    output_wav = tmp_path / "normalized.wav"
    _write_high_crest_wav(source_wav)

    report = normalize_wav(
        find_audio_tools(),
        source_wav,
        output_wav,
        PostprocessProfile(),
    )
    measured_lufs, measured_peak = _measure_final_wav(output_wav)

    assert report.normalization_type == "dynamic"
    assert report.integrated_lufs == measured_lufs
    assert report.true_peak_dbtp == measured_peak
    assert measured_lufs == pytest.approx(-18.0, abs=0.2)
    assert measured_peak <= -0.9
    assert not list(tmp_path.glob("*.limiter-correction.wav"))

    with wave.open(str(output_wav), "rb") as normalized:
        assert normalized.getnchannels() == 1
        assert normalized.getsampwidth() == 2
        assert normalized.getframerate() == 48_000


def test_intersample_overshoot_correction_hits_profile(
    tmp_path: Path,
) -> None:
    source_wav = tmp_path / "transient-24k.wav"
    output_wav = tmp_path / "normalized.wav"
    _write_intersample_overshoot_wav(source_wav)
    with wave.open(str(source_wav), "rb") as source:
        assert source.getframerate() == 24_000

    report = normalize_wav(
        find_audio_tools(),
        source_wav,
        output_wav,
        PostprocessProfile(),
    )
    measured_lufs, measured_peak = _measure_final_wav(output_wav)

    assert PostprocessProfile().algorithm_version == 6
    assert report.normalization_type == "dynamic"
    assert report.integrated_lufs == measured_lufs
    assert report.true_peak_dbtp == measured_peak
    assert measured_lufs == pytest.approx(-18.0, abs=0.2)
    assert measured_peak <= -0.9
    assert not list(tmp_path.glob("*.limiter-correction.wav"))


@pytest.mark.parametrize(
    ("integrated_lufs", "true_peak_dbtp"),
    [(-19.6, -1.0), (-18.0, -0.8)],
)
def test_loudness_profile_validation_fails_fast(
    integrated_lufs: float,
    true_peak_dbtp: float,
) -> None:
    with pytest.raises(
        AudioProcessingError,
        match="loudness profile",
    ):
        _validate_loudness_report(
            LoudnessReport(
                integrated_lufs=integrated_lufs,
                true_peak_dbtp=true_peak_dbtp,
                loudness_range_lu=0.0,
            ),
            PostprocessProfile(),
            stage="エンコード後 Opus",
        )


def test_whisper_within_hard_gate_is_reported_as_shortfall() -> None:
    report = EncodedLoudnessReport(
        integrated_lufs=-18.57,
        true_peak_dbtp=-0.94,
        loudness_range_lu=0.0,
    )

    _validate_loudness_report(
        report,
        PostprocessProfile(),
        stage="エンコード後 Opus",
    )

    assert report.as_manifest_dict(PostprocessProfile()) == {
        "source": "encoded_opus",
        "i_lufs": -18.57,
        "tp_dbtp": -0.94,
        "shortfall": True,
    }


def test_opus_encoding_is_bit_exact_for_identical_pcm(tmp_path: Path) -> None:
    source_wav = tmp_path / "source.wav"
    first_opus = tmp_path / "first.opus"
    second_opus = tmp_path / "second.opus"
    _write_high_crest_wav(source_wav)
    tools = find_audio_tools()
    profile = PostprocessProfile()

    encode_opus(tools, source_wav, first_opus, profile)
    encode_opus(tools, source_wav, second_opus, profile)

    assert first_opus.read_bytes() == second_opus.read_bytes()


def test_encoded_opus_is_decoded_measured_and_reported(
    tmp_path: Path,
) -> None:
    source_wav = tmp_path / "source.wav"
    normalized_wav = tmp_path / "normalized.wav"
    output_opus = tmp_path / "encoded.opus"
    _write_sine_wav(source_wav)
    tools = find_audio_tools()
    profile = PostprocessProfile()

    normalize_wav(tools, source_wav, normalized_wav, profile)
    encode_opus(tools, normalized_wav, output_opus, profile)
    report = measure_encoded_opus(tools, output_opus, profile)
    measured_lufs, measured_peak = _measure_final_wav(output_opus)

    assert report.integrated_lufs == measured_lufs
    assert report.true_peak_dbtp == measured_peak
    assert report.as_manifest_dict(profile)["source"] == "encoded_opus"


def test_encoded_opus_gate_error_identifies_stage() -> None:
    with pytest.raises(
        AudioProcessingError,
        match="エンコード後 Opus",
    ):
        _validate_loudness_report(
            LoudnessReport(
                integrated_lufs=-18.0,
                true_peak_dbtp=-0.8,
                loudness_range_lu=0.0,
            ),
            PostprocessProfile(),
            stage="エンコード後 Opus",
        )
