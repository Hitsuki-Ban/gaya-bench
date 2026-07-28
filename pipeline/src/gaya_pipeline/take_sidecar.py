from __future__ import annotations

import math
from typing import Any

from gaya_pipeline.adapters.base import TakeContext
from gaya_pipeline.take_identity import (
    TakeIdentityError,
    canonical_json,
    make_take_id,
)


class TakeSidecarError(ValueError):
    pass


SIDECAR_KEYS = {
    "format_version",
    "run_id",
    "model",
    "scenario",
    "line",
    "variant",
    "take_index",
    "take_id",
    "generation_input_sha256",
    "wav_sha256",
    "opus_sha256",
    "duration_sec",
    "generation_seconds",
    "rtf",
    "take",
    "gen_params",
    "postprocess",
    "toolchain",
    "loudness",
}
HEX = frozenset("0123456789abcdef")


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in HEX for character in value)
    ):
        raise TakeSidecarError(f"{field} は完全な小文字 SHA-256 が必要です。")
    return value


def _finite_nonnegative(value: Any, field: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value < 0
    ):
        raise TakeSidecarError(f"{field} は有限の非負数が必要です。")


def validate_take_sidecar(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != SIDECAR_KEYS:
        raise TakeSidecarError("take sidecar の項目が v1 契約と一致しません。")
    if document["format_version"] != 1:
        raise TakeSidecarError("take sidecar format_version は 1 が必要です。")
    for key in ("run_id", "model", "scenario", "line", "variant"):
        if not isinstance(document[key], str) or not document[key]:
            raise TakeSidecarError(f"take sidecar {key} は空でない文字列が必要です。")
    index = document["take_index"]
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise TakeSidecarError("take sidecar take_index は 1 以上の整数が必要です。")
    input_sha = _sha(
        document["generation_input_sha256"],
        "take sidecar generation_input_sha256",
    )
    opus_sha = _sha(document["opus_sha256"], "take sidecar opus_sha256")
    _sha(document["wav_sha256"], "take sidecar wav_sha256")
    take_id = _sha(document["take_id"], "take sidecar take_id")
    if take_id != make_take_id(
        generation_input_sha256=input_sha,
        final_opus_sha256=opus_sha,
    ):
        raise TakeSidecarError("take sidecar take_id が provenance と一致しません。")
    for key in ("duration_sec", "generation_seconds", "rtf"):
        _finite_nonnegative(document[key], f"take sidecar {key}")

    take = document["take"]
    if not isinstance(take, dict) or set(take) != {
        "seed",
        "recipe_version",
        "sampling",
    }:
        raise TakeSidecarError("take sidecar take の項目が一致しません。")
    try:
        context = TakeContext.create(
            index=index,
            seed=take["seed"],
            recipe_version=take["recipe_version"],
            sampling=take["sampling"],
        )
    except (TypeError, ValueError) as error:
        raise TakeSidecarError("take sidecar take が不正です。") from error
    if context.index != index:
        raise TakeSidecarError("take sidecar take_index が一致しません。")

    gen_params = document["gen_params"]
    if (
        not isinstance(gen_params, dict)
        or set(gen_params) != {"requested", "realized"}
        or not isinstance(gen_params["requested"], dict)
        or not isinstance(gen_params["realized"], dict)
    ):
        raise TakeSidecarError("take sidecar gen_params の項目が一致しません。")
    if not isinstance(document["postprocess"], dict):
        raise TakeSidecarError("take sidecar postprocess は object が必要です。")
    toolchain = document["toolchain"]
    if (
        not isinstance(toolchain, dict)
        or set(toolchain)
        != {"ffmpeg_version", "ffprobe_version", "libopus_encoder"}
        or not isinstance(toolchain["ffmpeg_version"], str)
        or not toolchain["ffmpeg_version"]
        or not isinstance(toolchain["ffprobe_version"], str)
        or not toolchain["ffprobe_version"]
        or toolchain["libopus_encoder"] is not True
    ):
        raise TakeSidecarError("take sidecar toolchain が不正です。")
    if not isinstance(document["loudness"], dict):
        raise TakeSidecarError("take sidecar loudness は object が必要です。")
    try:
        canonical_json(
            {
                "take": take,
                "gen_params": gen_params,
                "postprocess": document["postprocess"],
                "toolchain": toolchain,
                "loudness": document["loudness"],
            },
        )
    except TakeIdentityError as error:
        raise TakeSidecarError("take sidecar が JSON 契約を満たしません。") from error
    return document
