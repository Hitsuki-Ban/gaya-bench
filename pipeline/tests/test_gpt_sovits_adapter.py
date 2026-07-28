from __future__ import annotations

import hashlib
import re
import struct
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from gaya_pipeline.adapters import create_adapter
from gaya_pipeline.adapters.base import LineJob
from gaya_pipeline.adapters.gpt_sovits import (
    CUDA_WHEEL_VERSION,
    MODEL_ID,
    NATIVE_SAMPLE_RATE_HZ,
    PROFILE_VERSION,
    REFERENCE_ASSIGNMENTS,
    REFERENCE_FRAME_COUNT,
    REFERENCE_SECONDS,
    SEED,
    TORCHAUDIO_VERSION,
    TORCH_VERSION,
    UPSTREAM_REVISION,
    WEIGHTS_REVISION,
    GPTSoVITSAdapter,
    GPTSoVITSAdapterError,
    _NativeRuntime,
    _consume_single_result,
    _validate_japanese_dictionary_cache,
    _validate_upstream,
)

TAKE_CONTEXT = GPTSoVITSAdapter().take_recipe().single_take_context()

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REGISTERED_VOICES = {
    "amitaro-countdown",
    "hadou-emotion-11",
    "lux-emotion-76",
    "sayoko-emotion-75",
    "tsukuyomi-corpus-94",
}


class FakeOutOfMemoryError(RuntimeError):
    pass


class FakeRuntime:
    def __init__(self) -> None:
        self.prepare_calls: list[dict[str, Path]] = []
        self.synthesize_calls: list[dict[str, Any]] = []
        self.oom_on: str | None = None

    def prepare(
        self,
        *,
        upstream_root: Path,
        runtime_config_path: Path,
    ) -> dict[str, float]:
        if self.oom_on == "prepare":
            raise FakeOutOfMemoryError("CUDA out of memory")
        self.prepare_calls.append(
            {
                "upstream_root": upstream_root,
                "runtime_config_path": runtime_config_path,
            },
        )
        return {"allocated_mib": 2048.0, "reserved_mib": 2304.0}

    def synthesize(
        self,
        *,
        text: str,
        reference_wav: Path,
        output_wav: Path,
    ) -> dict[str, Any]:
        if self.oom_on == "synthesize":
            raise FakeOutOfMemoryError("CUDA out of memory")
        self.synthesize_calls.append(
            {
                "text": text,
                "reference_wav": reference_wav,
                "output_wav": output_wav,
            },
        )
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_wav), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(NATIVE_SAMPLE_RATE_HZ)
            wav_file.writeframes(
                b"".join(struct.pack("<h", sample) for sample in (0, 4096, -4096, 0)),
            )
        return {
            "seed": SEED,
            "sample_rate_hz": NATIVE_SAMPLE_RATE_HZ,
            "prompt_text_mode": "reference-free",
            "phase_peak_vram_mib": {
                "generation": {
                    "allocated_mib": 3072.0,
                    "reserved_mib": 3328.0,
                },
            },
        }

    def is_out_of_memory(self, error: BaseException) -> bool:
        return isinstance(error, FakeOutOfMemoryError)


def _job(
    *,
    scenario_id: str = "tavern-night",
    character_id: str = "barmaid",
    reference_voice: str | None = "amitaro-countdown",
    reading: str | None = None,
    emotion: str = "cheerful",
    locale: str = "ja",
) -> LineJob:
    return LineJob(
        scene={"id": scenario_id, "setting": "夜の酒場。"},
        character={
            "id": character_id,
            "voice": "架空のキャラクター声。",
            "reference_voice": reference_voice,
        },
        line={
            "id": f"{character_id}-001",
            "text": "乾杯しよう！",
            "reading": reading,
            "emotion": emotion,
            "intensity": 2,
            "delivery": "明るく話す。",
        },
        locale=locale,
    )


def _voices_dir(
    tmp_path: Path,
    *,
    materialize: set[str] | None = None,
) -> Path:
    voices_dir = tmp_path / "voices"
    voices_dir.mkdir(parents=True)
    metadata = yaml.safe_load(
        (REPOSITORY_ROOT / "assets" / "voices" / "metadata.yaml").read_text(
            encoding="utf-8",
        ),
    )
    (voices_dir / "metadata.schema.json").write_text(
        (REPOSITORY_ROOT / "assets" / "voices" / "metadata.schema.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    selected = REGISTERED_VOICES if materialize is None else materialize
    for index, entry in enumerate(metadata["voices"]):
        voice_id = str(entry["id"])
        if voice_id not in selected:
            continue
        audio_path = voices_dir / voice_id / "reference.wav"
        audio_path.parent.mkdir(parents=True)
        with wave.open(str(audio_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(48_000)
            samples = bytearray(b"\x00\x00" * (48_000 * 10))
            samples[index * 2 : index * 2 + 2] = struct.pack("<h", index + 1)
            wav_file.writeframes(samples)
        entry["sha256"] = hashlib.sha256(audio_path.read_bytes()).hexdigest()
        entry["duration_sec"] = 10.0
    (voices_dir / "metadata.yaml").write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return voices_dir


def test_profile_registry_and_generation_contract_are_canonical() -> None:
    adapter = create_adapter(MODEL_ID)

    assert isinstance(adapter, GPTSoVITSAdapter)
    assert adapter.profile.version == PROFILE_VERSION
    assert UPSTREAM_REVISION in adapter.profile.version
    assert WEIGHTS_REVISION in adapter.profile.version
    assert adapter.profile.capabilities.as_dict() == {
        "emotion": False,
        "voice_prompt": False,
        "clone": True,
        "nonverbal": False,
        "reading": True,
    }
    recipe = adapter.take_recipe()
    assert recipe.version == "fixed-single-v1"
    assert recipe.seed_policy == "fixed"
    assert recipe.single_take_seed == SEED
    assert recipe.seed_range == (0, 2**32 - 1)
    assert recipe.supports_multiple is False
    params = adapter.generation_params()
    assert params["torch_version"] == TORCH_VERSION
    assert params["torchaudio_version"] == TORCHAUDIO_VERSION
    assert params["cuda_wheel_version"] == CUDA_WHEEL_VERSION
    assert params["device"] == "cuda:0"
    assert params["precision"] == "fp16"
    assert params["reference_seconds"] == REFERENCE_SECONDS
    assert params["prompt_text_mode"] == "reference-free"
    assert params["parallel_infer"] is False
    assert params["reference_assignments"] == {
        f"{scenario}/{character}": voice
        for (scenario, character), voice in REFERENCE_ASSIGNMENTS.items()
    }
    assert "MIT" in adapter.profile.license_note
    assert "透かしなし" in adapter.profile.license_note


def test_explicit_reference_and_reading_create_exact_five_second_clip(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    adapter = GPTSoVITSAdapter(
        runtime=runtime,
        upstream_root=tmp_path / "upstream",
    )
    voices_dir = _voices_dir(
        tmp_path,
        materialize={"amitaro-countdown"},
    )
    job = _job(reading="カンパイシヨウ！")

    adapter.prepare([job], tmp_path / "artifacts", voices_dir)
    generation_input = adapter.generation_input(job, TAKE_CONTEXT)

    assert generation_input["text"] == "カンパイシヨウ！"
    assert generation_input["reading_source"] == "line.reading"
    assert generation_input["reference_selection_source"] == (
        "character.reference_voice"
    )
    assert generation_input["reference_voice"] == "amitaro-countdown"
    assert generation_input["reference_clip_start_frame"] == 0
    assert generation_input["reference_clip_frame_count"] == REFERENCE_FRAME_COUNT
    assert "emotion" not in generation_input
    assert len(generation_input["reference_clip_sha256"]) == 64
    assert runtime.prepare_calls == [
        {
            "upstream_root": tmp_path / "upstream",
            "runtime_config_path": (
                tmp_path / "artifacts" / "runtime" / MODEL_ID / "tts-infer.yaml"
            ),
        },
    ]

    output_wav = tmp_path / "output.wav"
    realized = adapter.generate(job, TAKE_CONTEXT, output_wav)
    reference_wav = runtime.synthesize_calls[0]["reference_wav"]
    with wave.open(str(reference_wav), "rb") as wav_file:
        assert wav_file.getnframes() == REFERENCE_FRAME_COUNT
        assert wav_file.getframerate() == 48_000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
    assert runtime.synthesize_calls[0]["text"] == "カンパイシヨウ！"
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
    assert realized["reading_source"] == "line.reading"
    assert realized["reference_selection_source"] == "character.reference_voice"
    assert realized["reference_voice"] == "amitaro-countdown"
    assert (
        realized["reference_source_sha256"]
        == (generation_input["reference_source_sha256"])
    )
    assert (
        realized["reference_clip_sha256"] == (generation_input["reference_clip_sha256"])
    )
    assert realized["reference_clip_start_frame"] == 0
    assert realized["reference_clip_frame_count"] == REFERENCE_FRAME_COUNT


@pytest.mark.parametrize(
    ("scenario_id", "character_id", "voice_id"),
    [
        ("tavern-night", "drunkard", "hadou-emotion-11"),
        ("tavern-night", "old-regular", "hadou-emotion-11"),
        ("market-day", "fruit-vendor", "hadou-emotion-11"),
        ("market-day", "shopper", "lux-emotion-76"),
        ("market-day", "street-kid", "tsukuyomi-corpus-94"),
    ],
)
def test_null_reference_uses_only_exact_assignment(
    tmp_path: Path,
    scenario_id: str,
    character_id: str,
    voice_id: str,
) -> None:
    adapter = GPTSoVITSAdapter(
        runtime=FakeRuntime(),
        upstream_root=tmp_path / "upstream",
    )
    voices_dir = _voices_dir(tmp_path, materialize={voice_id})
    job = _job(
        scenario_id=scenario_id,
        character_id=character_id,
        reference_voice=None,
    )

    adapter.prepare([job], tmp_path / "artifacts", voices_dir)

    generation_input = adapter.generation_input(job, TAKE_CONTEXT)
    assert generation_input["reference_voice"] == voice_id
    assert generation_input["reference_selection_source"] == (
        f"adapter.assignment:{scenario_id}/{character_id}"
    )
    assert generation_input["text"] == "乾杯しよう！"
    assert generation_input["reading_source"] == "line.text"


def test_unknown_null_reference_fails_without_runtime_load(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    adapter = GPTSoVITSAdapter(
        runtime=runtime,
        upstream_root=tmp_path / "upstream",
    )
    job = _job(
        scenario_id="castle-gate",
        character_id="guard-otoko",
        reference_voice=None,
    )

    with pytest.raises(GPTSoVITSAdapterError, match="固定 assignment"):
        adapter.prepare(
            [job],
            tmp_path / "artifacts",
            _voices_dir(tmp_path, materialize=set()),
        )

    assert runtime.prepare_calls == []


def test_reference_hash_mismatch_fails_before_runtime_load(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    adapter = GPTSoVITSAdapter(
        runtime=runtime,
        upstream_root=tmp_path / "upstream",
    )
    voices_dir = _voices_dir(
        tmp_path,
        materialize={"amitaro-countdown"},
    )
    reference = voices_dir / "amitaro-countdown" / "reference.wav"
    reference.write_bytes(reference.read_bytes() + b"tampered")

    with pytest.raises(GPTSoVITSAdapterError, match="SHA-256 が一致"):
        adapter.prepare(
            [_job()],
            tmp_path / "artifacts",
            voices_dir,
        )

    assert runtime.prepare_calls == []


def test_prepare_and_generation_oom_fail_fast(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    runtime.oom_on = "prepare"
    adapter = GPTSoVITSAdapter(
        runtime=runtime,
        upstream_root=tmp_path / "upstream",
    )
    voices_dir = _voices_dir(
        tmp_path,
        materialize={"amitaro-countdown"},
    )

    with pytest.raises(GPTSoVITSAdapterError, match="CUDA out of memory"):
        adapter.prepare([_job()], tmp_path / "artifacts", voices_dir)

    runtime = FakeRuntime()
    adapter = GPTSoVITSAdapter(
        runtime=runtime,
        upstream_root=tmp_path / "upstream",
    )
    adapter.prepare([_job()], tmp_path / "artifacts-2", voices_dir)
    runtime.oom_on = "synthesize"
    with pytest.raises(GPTSoVITSAdapterError, match="CUDA out of memory"):
        adapter.generate(_job(), TAKE_CONTEXT, tmp_path / "failed.wav")
    assert not (tmp_path / "failed.wav").exists()


def test_prepare_gate_and_delayed_generator_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GPTSoVITSAdapter(
        runtime=FakeRuntime(),
        upstream_root=tmp_path / "upstream",
    )
    with pytest.raises(GPTSoVITSAdapterError, match=r"prepare\(\)"):
        adapter.generation_input(_job(), TAKE_CONTEXT)

    with pytest.raises(GPTSoVITSAdapterError, match="Japanese 固定"):
        adapter.prepare(
            [_job(locale="en")],
            tmp_path / "artifacts",
            _voices_dir(tmp_path, materialize={"amitaro-countdown"}),
        )

    def delayed_failure() -> Any:
        yield (16_000, object())
        raise RuntimeError("late failure")

    with pytest.raises(RuntimeError, match="late failure"):
        _consume_single_result(delayed_failure())

    monkeypatch.setattr("gaya_pipeline.adapters.gpt_sovits.sys.platform", "linux")
    with pytest.raises(GPTSoVITSAdapterError, match="Windows native CUDA:0"):
        _NativeRuntime().prepare(
            upstream_root=tmp_path,
            runtime_config_path=tmp_path / "runtime.yaml",
        )


def test_upstream_rejects_untracked_python_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "upstream"
    root.mkdir()
    outputs = iter(
        [
            f"{UPSTREAM_REVISION}\n",
            "",
            "GPT_SoVITS/torchaudio.py\0",
            "",
        ],
    )
    monkeypatch.setattr(
        "gaya_pipeline.adapters.gpt_sovits.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=next(outputs)),
    )

    with pytest.raises(GPTSoVITSAdapterError, match="torchaudio.py"):
        _validate_upstream(root)


def test_upstream_allows_only_generated_japanese_dictionary_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "upstream"
    root.mkdir()
    dictionary_dir = root / "GPT_SoVITS" / "text" / "ja_userdic"
    dictionary_dir.mkdir(parents=True)
    csv_bytes = b"fixed csv"
    dictionary_bytes = b"fixed dictionary"
    (dictionary_dir / "userdict.csv").write_bytes(csv_bytes)
    (dictionary_dir / "user.dict").write_bytes(dictionary_bytes)
    (dictionary_dir / "userdict.md5").write_text(
        hashlib.md5(csv_bytes).hexdigest(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gaya_pipeline.adapters.gpt_sovits.USER_DICTIONARY_CSV_MD5",
        hashlib.md5(csv_bytes).hexdigest(),
    )
    monkeypatch.setattr(
        "gaya_pipeline.adapters.gpt_sovits.USER_DICTIONARY_SHA256",
        hashlib.sha256(dictionary_bytes).hexdigest(),
    )
    outputs = iter(
        [
            f"{UPSTREAM_REVISION}\n",
            "",
            (
                "GPT_SoVITS/text/ja_userdic/user.dict\0"
                "GPT_SoVITS/text/ja_userdic/userdict.md5\0"
            ),
            "",
        ],
    )
    monkeypatch.setattr(
        "gaya_pipeline.adapters.gpt_sovits.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=next(outputs)),
    )

    assert _validate_upstream(root) == root.resolve()


@pytest.mark.parametrize(
    "ignored_path",
    [
        "GPT_SoVITS/torchaudio.pyc",
        ("GPT_SoVITS/pretrained_models/chinese-hubert-base/model.safetensors"),
    ],
)
def test_upstream_rejects_ignored_executable_or_model_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ignored_path: str,
) -> None:
    root = tmp_path / "upstream"
    root.mkdir()
    outputs = iter(
        [
            f"{UPSTREAM_REVISION}\n",
            "",
            "",
            f"{ignored_path}\0",
        ],
    )
    monkeypatch.setattr(
        "gaya_pipeline.adapters.gpt_sovits.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=next(outputs)),
    )

    with pytest.raises(GPTSoVITSAdapterError, match=re.escape(ignored_path)):
        _validate_upstream(root)


def test_japanese_dictionary_cache_content_is_fixed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dictionary_dir = tmp_path / "GPT_SoVITS" / "text" / "ja_userdic"
    dictionary_dir.mkdir(parents=True)
    csv_bytes = b"fixed csv"
    dictionary_bytes = b"fixed dictionary"
    (dictionary_dir / "userdict.csv").write_bytes(csv_bytes)
    (dictionary_dir / "user.dict").write_bytes(dictionary_bytes)
    (dictionary_dir / "userdict.md5").write_text(
        hashlib.md5(csv_bytes).hexdigest(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gaya_pipeline.adapters.gpt_sovits.USER_DICTIONARY_CSV_MD5",
        hashlib.md5(csv_bytes).hexdigest(),
    )
    monkeypatch.setattr(
        "gaya_pipeline.adapters.gpt_sovits.USER_DICTIONARY_SHA256",
        hashlib.sha256(dictionary_bytes).hexdigest(),
    )

    _validate_japanese_dictionary_cache(tmp_path, required=True)
    (dictionary_dir / "user.dict").write_bytes(b"tampered")

    with pytest.raises(GPTSoVITSAdapterError, match="dictionary SHA-256"):
        _validate_japanese_dictionary_cache(tmp_path, required=True)
