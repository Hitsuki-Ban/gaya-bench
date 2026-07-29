from __future__ import annotations

from types import SimpleNamespace

import numpy
import pytest

from gaya_pipeline.qc_runtime import (
    _final_intonation_report,
    analyze_prosody_samples,
)


def test_共通prosody関数はASRを介さず注入した音響依存だけを使う() -> None:
    samples = numpy.ones(16_000, dtype=numpy.float32)
    frequencies = numpy.asarray(
        [100.0, 100.0, 100.0, 100.0, 112.246205, 112.246205],
    )
    fake_librosa = SimpleNamespace(
        effects=SimpleNamespace(
            split=lambda *_args, **_kwargs: numpy.asarray([[0, 16_000]]),
        ),
        pyin=lambda *_args, **_kwargs: (
            frequencies,
            numpy.asarray([True] * len(frequencies)),
            numpy.ones(len(frequencies)),
        ),
        feature=SimpleNamespace(
            rms=lambda **_kwargs: numpy.asarray([[0.1, 0.1]]),
        ),
        amplitude_to_db=lambda *_args, **_kwargs: numpy.asarray([-20.0, -20.0]),
    )

    report = analyze_prosody_samples(
        samples,
        mora_count=8,
        final_intonation="rise",
        librosa_module=fake_librosa,
        numpy_module=numpy,
    )

    assert report["active_speech_sec"] == 1.0
    assert report["estimated_mora_count"] == 8
    assert report["f0"]["final_intonation"]["expected"] == "rise"
    assert report["f0"]["final_intonation"][
        "clipped_interval_semitones"
    ] == pytest.approx(2.0, abs=1e-5)


@pytest.mark.parametrize(
    ("frequencies", "expected_interval"),
    [
        ([100.0, 100.0, 100.0, 100.0, 112.246205, 112.246205], 2.0),
        ([112.246205, 112.246205, 100.0, 100.0, 100.0, 100.0], -2.0),
    ],
)
def test_末端F0区間は上昇と下降を半音差で返す(
    frequencies: list[float],
    expected_interval: float,
) -> None:
    report = _final_intonation_report(
        frequencies,
        [True] * len(frequencies),
        last_active_interval=(0, 10_000),
        median_f0_hz=100.0,
        expected="fall",
    )

    assert report["reason"] is None
    assert report["raw_interval_semitones"] == pytest.approx(
        expected_interval,
        abs=1e-5,
    )
    assert report["clipped_interval_semitones"] == pytest.approx(
        expected_interval,
        abs=1e-5,
    )
    assert report["voiced_tail_frame_count"] == 6
    assert report["window_frame_count"] == 6
    assert report["policy"] == "report_only"
    assert report["rise_anchor_semitones"] == 2.0
    assert report["rise_anchor_met"] is (expected_interval >= 2.0)


def test_全句medianからプラスマイナス6半音に裁断してoctave誤りを抑える(
) -> None:
    report = _final_intonation_report(
        [50.0, 50.0, 100.0, 100.0, 200.0, 200.0],
        [True] * 6,
        last_active_interval=(0, 10_000),
        median_f0_hz=100.0,
        expected="rise",
    )

    assert report["raw_interval_semitones"] == 24.0
    assert report["clipped_interval_semitones"] == 12.0
    assert report["clipped_frame_count"] == 4
    assert report["expected"] == "rise"


def test_表示値が2半音に丸められる境界ではrise_anchor判定も一致する() -> None:
    unrounded_interval = 1.9999996
    end_frequency = 100.0 * 2 ** (unrounded_interval / 12)

    report = _final_intonation_report(
        [100.0, 100.0, 100.0, 100.0, end_frequency, end_frequency],
        [True] * 6,
        last_active_interval=(0, 10_000),
        median_f0_hz=100.0,
        expected="rise",
    )

    assert unrounded_interval < 2.0
    assert report["clipped_interval_semitones"] == 2.0
    assert report["rise_anchor_met"] is True


def test_末端の連続voicedが50ms未満ならintervalをnullにする() -> None:
    report = _final_intonation_report(
        [100.0, 100.0, 100.0],
        [True, True, True],
        last_active_interval=(0, 10_000),
        median_f0_hz=100.0,
        expected="free",
    )

    assert report["raw_interval_semitones"] is None
    assert report["clipped_interval_semitones"] is None
    assert report["reason"] == "insufficient_voiced_tail"
    assert report["rise_anchor_met"] is None
    assert report["voiced_tail_frame_count"] == 3
    assert report["voiced_tail_duration_sec"] == 0.048
    assert report["window_frame_count"] == 0


def test_末端windowは200ms以下に制限する() -> None:
    report = _final_intonation_report(
        [100.0] * 20,
        [True] * 20,
        last_active_interval=(0, 10_000),
        median_f0_hz=100.0,
        expected="fall",
    )

    assert report["voiced_tail_frame_count"] == 20
    assert report["voiced_tail_duration_sec"] == 0.32
    assert report["window_frame_count"] == 12
    assert report["window_duration_sec"] == 0.192
