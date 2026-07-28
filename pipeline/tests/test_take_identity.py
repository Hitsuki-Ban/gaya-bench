from __future__ import annotations

import math

import pytest

from gaya_pipeline.adapters.base import TakeContext
from gaya_pipeline.take_identity import (
    TakeIdentityError,
    canonical_json,
    derive_seed,
    generation_input_sha256,
    make_take_id,
)


def _seed(**changes: object) -> int:
    arguments = {
        "policy_version": "derived-sha256-v1",
        "seed_base": 42,
        "model": "dummy",
        "scenario": "tavern-night",
        "line": "barmaid-001",
        "variant": "dry",
        "index": 1,
        "seed_min": 0,
        "seed_max": 2**32 - 1,
    }
    arguments.update(changes)
    return derive_seed(**arguments)  # type: ignore[arg-type]


def test_canonical_jsonとseedの既知ベクトル() -> None:
    assert canonical_json({"b": [1, True, None], "a": "日本"}) == (
        '{"a":"日本","b":[1,true,null]}'
    )
    assert _seed() == 4_178_499_550


def test_seedは論理keyの全fieldに拘束されrange内に収まる() -> None:
    baseline = _seed()
    assert _seed() == baseline
    assert 0 <= baseline <= 2**32 - 1
    assert {
        _seed(policy_version="derived-sha256-v2"),
        _seed(seed_base=43),
        _seed(model="other"),
        _seed(scenario="market-day"),
        _seed(line="barmaid-002"),
        _seed(variant="wet"),
        _seed(index=2),
    }.isdisjoint({baseline})
    assert _seed(seed_min=7, seed_max=7) == 7


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"index": 0}, "index"),
        ({"index": True}, "index"),
        ({"seed_min": 2, "seed_max": 1}, "range"),
        ({"seed_base": False}, "seed_base"),
        ({"model": ""}, "model"),
    ],
)
def test_seedの不正入力を拒否(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(TakeIdentityError, match=message):
        _seed(**changes)


def test_take_contextは外部mapping変更から不変() -> None:
    sampling = {"temperature": 0.7, "top_p": 0.9}
    context = TakeContext.create(
        index=1,
        seed=42,
        recipe_version="seed-only-v1",
        sampling=sampling,
    )
    before = repr(context)

    sampling["temperature"] = 1.0
    sampling["top_p"] = 0.1

    assert repr(context) == before


def test_generation_inputと音声bytesがtake_idを決める() -> None:
    context = TakeContext.create(
        index=1,
        seed=42,
        recipe_version="seed-only-v1",
        sampling={"temperature": 0.7},
    )
    input_sha = generation_input_sha256(
        model_id="dummy",
        model_version="1",
        resolved_input={"text": "こんにちは"},
        take_context=context,
        generation_params={"voice": "test"},
        postprocess={"algorithm_version": 7},
    )
    same_input = generation_input_sha256(
        model_id="dummy",
        model_version="1",
        resolved_input={"text": "こんにちは"},
        take_context=context,
        generation_params={"voice": "test"},
        postprocess={"algorithm_version": 7},
    )
    audio_one = "1" * 64
    audio_two = "2" * 64

    assert input_sha == same_input
    assert make_take_id(
        generation_input_sha256=input_sha,
        final_opus_sha256=audio_one,
    ) == make_take_id(
        generation_input_sha256=same_input,
        final_opus_sha256=audio_one,
    )
    assert make_take_id(
        generation_input_sha256=input_sha,
        final_opus_sha256=audio_one,
    ) != make_take_id(
        generation_input_sha256=input_sha,
        final_opus_sha256=audio_two,
    )

    same_generation_different_slot = generation_input_sha256(
        model_id="dummy",
        model_version="1",
        resolved_input={"text": "こんにちは"},
        take_context=TakeContext.create(
            index=2,
            seed=42,
            recipe_version="seed-only-v1",
            sampling={"temperature": 0.7},
        ),
        generation_params={"voice": "test"},
        postprocess={"algorithm_version": 7},
    )
    assert same_generation_different_slot == input_sha


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, object()])
def test_canonical_jsonは非JSON値を拒否(value: object) -> None:
    with pytest.raises(TakeIdentityError):
        canonical_json({"value": value})
