from __future__ import annotations

import math
from copy import deepcopy

import pytest

from gaya_pipeline.take_identity import make_take_id
from gaya_pipeline.take_sidecar import TakeSidecarError, validate_take_sidecar


def _sidecar() -> dict[str, object]:
    input_sha = "a" * 64
    opus_sha = "b" * 64
    return {
        "format_version": 1,
        "run_id": "2026-07-29T120000Z-dummy-n1",
        "model": "dummy",
        "scenario": "tavern-night",
        "line": "barmaid-001",
        "variant": "dry",
        "take_index": 1,
        "take_id": make_take_id(
            generation_input_sha256=input_sha,
            final_opus_sha256=opus_sha,
        ),
        "generation_input_sha256": input_sha,
        "wav_sha256": "c" * 64,
        "opus_sha256": opus_sha,
        "duration_sec": 1.25,
        "generation_seconds": 0.5,
        "rtf": 0.4,
        "take": {
            "seed": None,
            "recipe_version": "fixed-single-v1",
            "sampling": {},
        },
        "gen_params": {
            "requested": {"frequency_hz": 440},
            "realized": {"frequency_hz": 440},
        },
        "postprocess": {"algorithm_version": 7},
        "toolchain": {
            "ffmpeg_version": "ffmpeg version 8.0",
            "ffprobe_version": "ffprobe version 8.0",
            "libopus_encoder": True,
        },
        "loudness": {"encoded_opus": {"integrated_lufs": -18.0}},
    }


def test_take_sidecar_v1のexact_contract() -> None:
    sidecar = _sidecar()
    assert validate_take_sidecar(sidecar) is sidecar


@pytest.mark.parametrize(
    "mutation",
    [
        lambda sidecar: sidecar.update(extra=True),
        lambda sidecar: sidecar.pop("take_id"),
        lambda sidecar: sidecar.update(take_index=True),
        lambda sidecar: sidecar.update(rtf=math.nan),
        lambda sidecar: sidecar["take"].update(seed=True),
        lambda sidecar: sidecar["gen_params"].update(hidden={}),
    ],
)
def test_unknown_missing_bool_nanを拒否(mutation: object) -> None:
    sidecar = _sidecar()
    mutation(sidecar)  # type: ignore[operator]
    with pytest.raises(TakeSidecarError):
        validate_take_sidecar(sidecar)


def test_take_idとaudio_provenance不一致を拒否() -> None:
    sidecar = deepcopy(_sidecar())
    sidecar["opus_sha256"] = "d" * 64
    with pytest.raises(TakeSidecarError, match="take_id"):
        validate_take_sidecar(sidecar)
