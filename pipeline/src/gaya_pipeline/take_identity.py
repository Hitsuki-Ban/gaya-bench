from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from gaya_pipeline.adapters.base import TakeContext


class TakeIdentityError(ValueError):
    pass


def _validate_json(value: Any, *, field: str = "$") -> None:
    if value is None or isinstance(value, str | bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TakeIdentityError(f"{field} に非有限数は使用できません。")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TakeIdentityError(f"{field} のキーは文字列が必要です。")
            _validate_json(item, field=f"{field}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for index, item in enumerate(value):
            _validate_json(item, field=f"{field}[{index}]")
        return
    raise TakeIdentityError(f"{field} は JSON 値ではありません。")


def canonical_json(value: Any) -> str:
    _validate_json(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_canonical(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def take_context_dict(context: TakeContext) -> dict[str, Any]:
    return {
        "index": context.index,
        "seed": context.seed,
        "recipe_version": context.recipe_version,
        "sampling": dict(context.sampling),
    }


def take_generation_dict(context: TakeContext) -> dict[str, Any]:
    return {
        "seed": context.seed,
        "recipe_version": context.recipe_version,
        "sampling": dict(context.sampling),
    }


def derive_seed(
    *,
    policy_version: str,
    seed_base: int,
    model: str,
    scenario: str,
    line: str,
    variant: str,
    index: int,
    seed_min: int,
    seed_max: int,
) -> int:
    for field, value in (
        ("seed_base", seed_base),
        ("index", index),
        ("seed_min", seed_min),
        ("seed_max", seed_max),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TakeIdentityError(f"{field} は整数である必要があります。")
    if index < 1:
        raise TakeIdentityError("index は 1 以上である必要があります。")
    if seed_min < 0 or seed_max < seed_min:
        raise TakeIdentityError("seed range が不正です。")
    for field, value in (
        ("policy_version", policy_version),
        ("model", model),
        ("scenario", scenario),
        ("line", line),
        ("variant", variant),
    ):
        if not isinstance(value, str) or not value:
            raise TakeIdentityError(f"{field} は空でない文字列が必要です。")

    digest = hashlib.sha256(
        canonical_json(
            {
                "policy_version": policy_version,
                "seed_base": seed_base,
                "model": model,
                "scenario": scenario,
                "line": line,
                "variant": variant,
                "index": index,
            },
        ).encode("utf-8"),
    ).digest()
    span = seed_max - seed_min + 1
    return seed_min + int.from_bytes(digest[:8], "big", signed=False) % span


def generation_input_sha256(
    *,
    model_id: str,
    model_version: str,
    resolved_input: Mapping[str, Any],
    take_context: TakeContext,
    generation_params: Mapping[str, Any],
    postprocess: Mapping[str, Any],
) -> str:
    return sha256_canonical(
        {
            "model": {"id": model_id, "version": model_version},
            "input": resolved_input,
            "take": take_generation_dict(take_context),
            "gen_params": generation_params,
            "postprocess": postprocess,
        },
    )


def make_take_id(
    *,
    generation_input_sha256: str,
    final_opus_sha256: str,
) -> str:
    for field, value in (
        ("generation_input_sha256", generation_input_sha256),
        ("final_opus_sha256", final_opus_sha256),
    ):
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise TakeIdentityError(f"{field} は完全な小文字 SHA-256 が必要です。")
    return sha256_canonical(
        {
            "generation_input_sha256": generation_input_sha256,
            "final_opus_sha256": final_opus_sha256,
        },
    )
