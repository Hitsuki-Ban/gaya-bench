from __future__ import annotations

import hashlib
import json
import struct
import wave
from pathlib import Path
from typing import Any

import pytest
import yaml
from gaya_pipeline.adapters import irodori_tts
from gaya_pipeline.adapters.base import LineJob, TakeContext
from gaya_pipeline.adapters.irodori_tts import (
    CHECKPOINT_ID,
    CHECKPOINT_REVISION,
    CODEC_ID,
    CODEC_REVISION,
    DACVAE_REVISION,
    EMOTION_EMOJI,
    MODEL_ID,
    PROFILE_VERSION,
    PYOPENJTALK_VERSION,
    SEED,
    SILENTCIPHER_MODEL_ID,
    SILENTCIPHER_MODEL_REVISION,
    SILENTCIPHER_VERSION,
    TOKENIZER_ID,
    TOKENIZER_REVISION,
    UPSTREAM_REVISION,
    IrodoriTTSAdapter,
    IrodoriTTSAdapterError,
    _NativeRuntime,
    _pinned_silentcipher_loader,
)


class FakeOutOfMemoryError(RuntimeError):
    pass


class FakeRuntime:
    def __init__(self) -> None:
        self.prepare_count = 0
        self.synthesize_calls: list[dict[str, Any]] = []
        self.oom_on: str | None = None

    def prepare(self) -> dict[str, float]:
        self.prepare_count += 1
        if self.oom_on == "prepare":
            raise FakeOutOfMemoryError("load")
        return {
            "allocated_mib": 2048.0,
            "reserved_mib": 2304.0,
        }

    def synthesize(
        self,
        *,
        text: str,
        caption: str,
        reference_wav: Path | None,
        output_wav: Path,
        seed: int,
    ) -> dict[str, Any]:
        if self.oom_on == "synthesize":
            raise FakeOutOfMemoryError("generate")
        self.synthesize_calls.append(
            {
                "text": text,
                "caption": caption,
                "reference_wav": reference_wav,
                "output_wav": output_wav,
                "seed": seed,
            },
        )
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_wav), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(48_000)
            wav_file.writeframes(
                b"".join(struct.pack("<h", sample) for sample in (0, 4096, -4096, 0)),
            )
        return {
            "phase_peak_vram_mib": {
                "generation": {
                    "allocated_mib": 3072.0,
                    "reserved_mib": 3328.0,
                },
            },
            "seed": seed,
            "sample_rate_hz": 48_000,
            "silentcipher_watermark_stage_executed": True,
        }

    def is_out_of_memory(self, error: BaseException) -> bool:
        return isinstance(error, FakeOutOfMemoryError)


def _take_context(adapter: IrodoriTTSAdapter) -> TakeContext:
    return adapter.take_recipe().single_take_context()


def _job(
    *,
    locale: str = "ja",
    reading: str | None = None,
    emotion: str = "laughing",
    reference_voice: str | None = None,
) -> LineJob:
    return LineJob(
        scene={
            "id": "tavern-night",
            "setting": "夜の酒場。",
        },
        character={
            "id": "barmaid",
            "voice": "明るく通る若い女性の声。",
            "reference_voice": reference_voice,
        },
        line={
            "id": "barmaid-001",
            "text": "乾杯しよう！",
            "reading": reading,
            "emotion": emotion,
            "intensity": 2,
            "delivery": "笑いを含ませ、弾む調子で話す。",
        },
        locale=locale,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _voices_dir(
    tmp_path: Path,
    *,
    voice_id: str = "test-voice",
    include_wav: bool = True,
    sha_override: str | None = None,
) -> Path:
    voices_dir = tmp_path / "voices"
    voice_dir = voices_dir / voice_id
    voice_dir.mkdir(parents=True)
    wav_path = voice_dir / "reference.wav"
    if include_wav:
        wav_path.write_bytes(b"reference voice")
    sha256 = (
        sha_override
        if sha_override is not None
        else (_sha256(wav_path) if include_wav else "0" * 64)
    )
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["format_version", "voices"],
        "properties": {
            "format_version": {"const": 1},
            "voices": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "file", "sha256"],
                    "properties": {
                        "id": {
                            "type": "string",
                            "pattern": "^[a-z0-9][a-z0-9-]*$",
                        },
                        "file": {"type": "string"},
                        "sha256": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                        },
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": False,
    }
    (voices_dir / "metadata.schema.json").write_text(
        json.dumps(schema),
        encoding="utf-8",
    )
    (voices_dir / "metadata.yaml").write_text(
        yaml.safe_dump(
            {
                "format_version": 1,
                "voices": [
                    {
                        "id": voice_id,
                        "file": f"{voice_id}/reference.wav",
                        "sha256": sha256,
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return voices_dir


def test_profile_revisions_capabilities_and_parameters_are_canonical() -> None:
    adapter = IrodoriTTSAdapter(
        runtime=FakeRuntime(),
        reading_converter=lambda text: text,
    )

    assert adapter.profile.id == MODEL_ID
    assert adapter.profile.version == PROFILE_VERSION
    assert UPSTREAM_REVISION in adapter.profile.version
    assert CHECKPOINT_REVISION in adapter.profile.version
    assert CODEC_REVISION in adapter.profile.version
    assert DACVAE_REVISION in adapter.profile.version
    assert TOKENIZER_REVISION in adapter.profile.version
    assert SILENTCIPHER_MODEL_REVISION in adapter.profile.version
    assert adapter.profile.capabilities.as_dict() == {
        "emotion": True,
        "voice_prompt": True,
        "clone": True,
        "nonverbal": True,
        "reading": True,
    }
    recipe = adapter.take_recipe()
    assert recipe.version == "seed-only-v1"
    assert recipe.seed_policy == "derived-sha256-v1"
    assert recipe.single_take_seed == SEED
    assert recipe.seed_range == (0, 2**32 - 1)
    assert recipe.supports_multiple is True
    params = adapter.generation_params()
    assert params["checkpoint"] == CHECKPOINT_ID
    assert params["checkpoint_revision"] == CHECKPOINT_REVISION
    assert params["codec"] == CODEC_ID
    assert params["codec_revision"] == CODEC_REVISION
    assert params["dacvae_revision"] == DACVAE_REVISION
    assert params["tokenizer"] == TOKENIZER_ID
    assert params["tokenizer_revision"] == TOKENIZER_REVISION
    assert params["silentcipher_model"] == SILENTCIPHER_MODEL_ID
    assert params["silentcipher_model_revision"] == SILENTCIPHER_MODEL_REVISION
    assert params["silentcipher_version"] == SILENTCIPHER_VERSION
    assert params["pyopenjtalk_plus_version"] == PYOPENJTALK_VERSION
    assert "seed" not in params
    assert params["emotion_emoji"] == EMOTION_EMOJI
    assert params["silentcipher_watermark_stage_required"] is True
    assert params["silentcipher_payload"] == "IRDTS"
    assert "MIT" in adapter.profile.license_note
    assert "SilentCipher" in adapter.profile.license_note


def test_official_emotion_mapping_covers_every_scenario_value() -> None:
    assert EMOTION_EMOJI == {
        "neutral": None,
        "cheerful": "😊",
        "angry": "😠",
        "sad": "😭",
        "fearful": "😰",
        "surprised": "😲",
        "tired": "😪",
        "drunk": "🥴",
        "whisper": "👂",
        "shout": "😱",
        "laughing": "🤭",
        "pain": "😖",
    }


def test_no_reference_uses_explicit_no_ref_input_and_writes_pcm16(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    adapter = IrodoriTTSAdapter(
        runtime=runtime,
        reading_converter=lambda _text: "カンパイシヨウ！",
    )
    job = _job()
    voices_dir = _voices_dir(tmp_path)
    adapter.prepare([job], tmp_path / "artifacts", voices_dir)

    assert runtime.prepare_count == 0
    assert adapter.generation_input(job, _take_context(adapter)) == {
        "text": "🤭カンパイシヨウ！",
        "reading_source": "pyopenjtalk.g2p(kana=True)",
        "emotion": "laughing",
        "emotion_emoji": "🤭",
        "caption": (
            "架空のキャラクターとして、実在の人物や声優を模倣せずに話す。\n"
            "声質: 明るく通る若い女性の声。\n"
            "感情: 笑い（強度 2/3）\n"
            "演技: 笑いを含ませ、弾む調子で話す。"
        ),
        "reference_voice": None,
        "reference_sha256": None,
    }

    output_wav = tmp_path / "output.wav"
    realized = adapter.generate(job, _take_context(adapter), output_wav)
    assert runtime.prepare_count == 1
    assert runtime.synthesize_calls[0]["reference_wav"] is None
    assert realized["silentcipher_watermark_stage_executed"] is True
    assert realized["phase_peak_vram_mib"] == {
        "runtime_load": {
            "allocated_mib": 2048.0,
            "reserved_mib": 2304.0,
        },
        "generation": {
            "allocated_mib": 3072.0,
            "reserved_mib": 3328.0,
        },
    }
    with wave.open(str(output_wav), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 48_000


def test_take_seed_reaches_runtime_and_realized_metadata(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    adapter = IrodoriTTSAdapter(
        runtime=runtime,
        reading_converter=lambda text: text,
    )
    job = _job()
    adapter.prepare([job], tmp_path / "artifacts", _voices_dir(tmp_path))
    recipe = adapter.take_recipe()
    first_context = recipe.single_take_context()
    second_context = TakeContext.create(
        index=2,
        seed=123_456,
        recipe_version=recipe.version,
        sampling=dict(recipe.sampling),
    )

    first = adapter.generate(job, first_context, tmp_path / "first.wav")
    second = adapter.generate(job, second_context, tmp_path / "second.wav")

    assert [call["seed"] for call in runtime.synthesize_calls] == [SEED, 123_456]
    assert first["seed"] == SEED
    assert first["sampling"] == first_context.sampling_dict()
    assert second["seed"] == 123_456
    assert second["sampling"] == second_context.sampling_dict()


def test_explicit_reading_has_priority_without_invoking_converter(
    tmp_path: Path,
) -> None:
    def converter(_text: str) -> str:
        raise AssertionError("converter must not be called")

    adapter = IrodoriTTSAdapter(
        runtime=FakeRuntime(),
        reading_converter=converter,
    )
    job = _job(reading="カンパイシヨウ！", emotion="neutral")
    adapter.prepare([job], tmp_path / "artifacts", _voices_dir(tmp_path))

    generation_input = adapter.generation_input(job, _take_context(adapter))
    assert generation_input["text"] == "カンパイシヨウ！"
    assert generation_input["reading_source"] == "line.reading"
    assert generation_input["emotion_emoji"] is None


def test_registered_reference_is_hashed_and_used_for_clone(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    adapter = IrodoriTTSAdapter(
        runtime=runtime,
        reading_converter=lambda text: text,
    )
    voices_dir = _voices_dir(tmp_path)
    job = _job(reference_voice="test-voice")
    adapter.prepare([job], tmp_path / "artifacts", voices_dir)

    expected_path = voices_dir / "test-voice" / "reference.wav"
    assert adapter.generation_input(
        job,
        _take_context(adapter),
    )["reference_sha256"] == _sha256(expected_path)
    output_wav = tmp_path / "clone.wav"
    adapter.generate(job, _take_context(adapter), output_wav)
    assert runtime.synthesize_calls[0]["reference_wav"] == expected_path


@pytest.mark.parametrize(
    ("include_wav", "sha_override", "message"),
    [
        (False, None, "WAV がありません"),
        (True, "0" * 64, "SHA-256 が一致"),
    ],
)
def test_reference_voice_missing_or_hash_mismatch_fails_fast(
    tmp_path: Path,
    include_wav: bool,
    sha_override: str | None,
    message: str,
) -> None:
    adapter = IrodoriTTSAdapter(
        runtime=FakeRuntime(),
        reading_converter=lambda text: text,
    )
    voices_dir = _voices_dir(
        tmp_path,
        include_wav=include_wav,
        sha_override=sha_override,
    )
    with pytest.raises(IrodoriTTSAdapterError, match=message):
        adapter.prepare(
            [_job(reference_voice="test-voice")],
            tmp_path / "artifacts",
            voices_dir,
        )


def test_prepare_gate_language_and_oom_fail_fast(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    adapter = IrodoriTTSAdapter(
        runtime=runtime,
        reading_converter=lambda text: text,
    )
    job = _job()
    with pytest.raises(IrodoriTTSAdapterError, match=r"prepare\(\)"):
        adapter.generation_input(job, _take_context(adapter))

    with pytest.raises(IrodoriTTSAdapterError, match="Japanese 固定"):
        adapter.prepare(
            [_job(locale="en")],
            tmp_path / "artifacts",
            _voices_dir(tmp_path),
        )

    runtime.oom_on = "prepare"
    adapter.prepare(
        [job],
        tmp_path / "artifacts",
        _voices_dir(tmp_path / "oom"),
    )
    with pytest.raises(IrodoriTTSAdapterError, match="CUDA out of memory"):
        adapter.generate(
            job,
            _take_context(adapter),
            tmp_path / "load-failed.wav",
        )
    assert not (tmp_path / "load-failed.wav").exists()

    runtime.oom_on = None
    adapter.prepare(
        [job],
        tmp_path / "artifacts",
        _voices_dir(tmp_path / "generate"),
    )
    runtime.oom_on = "synthesize"
    with pytest.raises(IrodoriTTSAdapterError, match="CUDA out of memory"):
        adapter.generate(job, _take_context(adapter), tmp_path / "failed.wav")
    assert not (tmp_path / "failed.wav").exists()


def test_native_runtime_rejects_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(irodori_tts.sys, "platform", "linux")
    with pytest.raises(IrodoriTTSAdapterError, match="Windows native CUDA:0"):
        _NativeRuntime()


def test_silentcipher_loader_ignores_cwd_relative_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_checkpoint = tmp_path.parent / "Models" / "44_1_khz" / "73999_iteration"
    local_checkpoint.mkdir(parents=True)
    (local_checkpoint / "hparams.yaml").write_text("local: true", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    fixed_checkpoint = tmp_path / "fixed" / "44_1_khz" / "73999_iteration"
    fixed_config = fixed_checkpoint / "hparams.yaml"
    calls: list[dict[str, str]] = []

    def get_model(**kwargs: str) -> object:
        calls.append(kwargs)
        return object()

    loader = _pinned_silentcipher_loader(
        get_model,
        checkpoint_path=fixed_checkpoint,
        config_path=fixed_config,
    )
    result = loader(model_type="44.1k", device="cuda:0")

    assert result is not None
    assert calls == [
        {
            "model_type": "44.1k",
            "ckpt_path": str(fixed_checkpoint),
            "config_path": str(fixed_config),
            "device": "cuda:0",
        },
    ]
