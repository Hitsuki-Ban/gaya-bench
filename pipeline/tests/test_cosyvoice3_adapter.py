from __future__ import annotations

import hashlib
import math
import struct
import wave
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from gaya_pipeline.adapters import create_adapter
from gaya_pipeline.adapters.base import LineJob, TakeContext
from gaya_pipeline.adapters.cosyvoice3 import (
    ARCHITECTURE,
    CODE_ROOT_ENV,
    DEVICE,
    EMOTION_INSTRUCTION_TEMPLATES,
    FP16,
    INSTRUCTION_END,
    INSTRUCTION_POLICY_VERSION,
    MATCHA_REVISION,
    MODEL_ARCHITECTURE,
    MODEL_FILE_SPECS,
    MODEL_ID,
    MODEL_ROOT_ENV,
    OFFLINE_ENVIRONMENT,
    PROFILE_VERSION,
    SAMPLE_RATE_HZ,
    SEED,
    UPSTREAM_REVISION,
    UPSTREAM_REPOSITORY,
    WEIGHTS_REVISION,
    CosyVoice3Adapter,
    CosyVoice3AdapterError,
    _NativeRuntime,
)
from gaya_pipeline.adapters.voice_assignments import (
    CLONE_REFERENCE_ASSIGNMENTS,
)

TAKE_CONTEXT = CosyVoice3Adapter().take_recipe().single_take_context()

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REGISTERED_VOICES = {
    "amitaro-countdown",
    "hadou-emotion-11",
    "lux-emotion-76",
    "sayoko-emotion-75",
    "tsukuyomi-corpus-94",
}
EXPECTED_EMOTION_INSTRUCTION_TEMPLATES = {
    "neutral": "You are a helpful assistant.<|endofprompt|>",
    "cheerful": (
        "You are a helpful assistant. "
        "请非常开心地说一句话。<|endofprompt|>"
    ),
    "angry": (
        "You are a helpful assistant. "
        "请非常生气地说一句话。<|endofprompt|>"
    ),
    "sad": (
        "You are a helpful assistant. "
        "请非常伤心地说一句话。<|endofprompt|>"
    ),
    "fearful": (
        "You are a helpful assistant. "
        "请害怕地说一句话。<|endofprompt|>"
    ),
    "surprised": (
        "You are a helpful assistant. "
        "请惊讶地说一句话。<|endofprompt|>"
    ),
    "tired": (
        "You are a helpful assistant. "
        "请用疲惫的语气说一句话。<|endofprompt|>"
    ),
    "drunk": (
        "You are a helpful assistant. "
        "请用醉酒的语气说一句话。<|endofprompt|>"
    ),
    "whisper": (
        "You are a helpful assistant. "
        "Please say a sentence in a very soft voice.<|endofprompt|>"
    ),
    "shout": (
        "You are a helpful assistant. "
        "Please say a sentence as loudly as possible.<|endofprompt|>"
    ),
    "laughing": (
        "You are a helpful assistant. "
        "请笑着说一句话。<|endofprompt|>"
    ),
    "pain": (
        "You are a helpful assistant. "
        "请用痛苦的语气说一句话。<|endofprompt|>"
    ),
}


class FakeOutOfMemoryError(RuntimeError):
    pass


class FakeRuntime:
    def __init__(self) -> None:
        self.load_calls: list[tuple[Path, Path]] = []
        self.synthesize_calls: list[dict[str, Any]] = []
        self.write_calls: list[dict[str, Any]] = []
        self.identity: dict[str, Any] = {
            "architecture": ARCHITECTURE,
            "model_architecture": MODEL_ARCHITECTURE,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "fp16": FP16,
            "frontend_text_frontend": "",
            "frontend_device": "cuda",
            "llm_device": DEVICE,
            "flow_device": DEVICE,
            "hift_device": DEVICE,
            "speech_tokenizer_providers": [
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ],
            "campplus_providers": ["CPUExecutionProvider"],
        }
        self.chunks: Sequence[Mapping[str, Any]] = [
            {"tts_speech": [[0.0, 0.25]]},
            {"tts_speech": [[-0.25, 0.0, 0.125]]},
        ]
        self.oom_on: str | None = None
        self._phase = 0

    def load_model(self, code_root: Path, model_root: Path) -> dict[str, str]:
        self.load_calls.append((code_root, model_root))
        self._phase = 1
        if self.oom_on == "load":
            raise FakeOutOfMemoryError("load")
        return {"model": "cosyvoice3"}

    def model_identity(self, model: Any) -> Mapping[str, Any]:
        assert model == {"model": "cosyvoice3"}
        return dict(self.identity)

    def synthesize(
        self,
        model: Any,
        *,
        tts_text: str,
        instruction: str,
        reference_wav: Path,
        seed: int,
    ) -> Sequence[Mapping[str, Any]]:
        assert model == {"model": "cosyvoice3"}
        self.synthesize_calls.append(
            {
                "tts_text": tts_text,
                "instruction": instruction,
                "reference_wav": reference_wav,
                "seed": seed,
            },
        )
        if self.oom_on == "generate":
            raise FakeOutOfMemoryError("generate")
        return self.chunks

    def concatenate_waveforms(self, waveforms: Sequence[Any]) -> Any:
        if self.oom_on == "concatenate":
            raise FakeOutOfMemoryError("concatenate")
        return [[value for waveform in waveforms for value in waveform[0]]]

    def write_pcm16(self, path: Path, waveform: Any, sample_rate: int) -> None:
        self.write_calls.append(
            {
                "path": path,
                "waveform": waveform,
                "sample_rate": sample_rate,
            },
        )
        if self.oom_on == "write":
            raise FakeOutOfMemoryError("write")
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(
                b"".join(
                    struct.pack("<h", round(float(value) * 32_767))
                    for value in waveform[0]
                ),
            )

    def reset_peak_memory_stats(self) -> None:
        self._phase += 1

    def peak_memory_mib(self) -> Mapping[str, float]:
        return {
            "allocated_mib": float(self._phase * 100),
            "reserved_mib": float(self._phase * 100 + 25),
        }

    def is_out_of_memory(self, error: BaseException) -> bool:
        return isinstance(error, FakeOutOfMemoryError)


def _job(
    *,
    scenario_id: str = "tavern-night",
    character_id: str = "barmaid",
    line_id: str | None = None,
    reference_voice: str | None = "amitaro-countdown",
    text: str = "乾杯しよう！",
    reading: str | None = "カンパイシヨウ！",
    emotion: str = "cheerful",
    intensity: Any = 2,
    delivery: str = "明るく話す。",
    locale: str = "ja",
) -> LineJob:
    line: dict[str, Any] = {
        "id": line_id or f"{character_id}-001",
        "text": text,
        "reading": reading,
        "emotion": emotion,
        "intensity": intensity,
        "delivery": delivery,
    }
    return LineJob(
        scene={"id": scenario_id, "setting": "夜の酒場。"},
        character={
            "id": character_id,
            "name": "給仕",
            "gender": "female",
            "age": "young_adult",
            "voice": "明るい声。",
            "reference_voice": reference_voice,
        },
        line=line,
        locale=locale,
    )


def _voices_dir(
    tmp_path: Path,
    *,
    materialize: set[str],
) -> Path:
    voices_dir = tmp_path / "voices"
    voices_dir.mkdir(parents=True)
    metadata = yaml.safe_load(
        (REPOSITORY_ROOT / "assets" / "voices" / "metadata.yaml").read_text(
            encoding="utf-8",
        ),
    )
    (voices_dir / "metadata.schema.json").write_text(
        (
            REPOSITORY_ROOT / "assets" / "voices" / "metadata.schema.json"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for index, entry in enumerate(metadata["voices"]):
        voice_id = str(entry["id"])
        if voice_id not in materialize:
            continue
        audio_path = voices_dir / voice_id / "reference.wav"
        audio_path.parent.mkdir(parents=True)
        with wave.open(str(audio_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(48_000)
            frames = bytearray(b"\x00\x00" * (48_000 * 10))
            frames[index * 2 : index * 2 + 2] = struct.pack("<h", index + 1)
            wav_file.writeframes(frames)
        entry["sha256"] = hashlib.sha256(audio_path.read_bytes()).hexdigest()
        entry["duration_sec"] = 10.0
    (voices_dir / "metadata.yaml").write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return voices_dir


def _model_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cache: bool = False,
) -> Path:
    import gaya_pipeline.adapters.cosyvoice3 as module

    root = tmp_path / "weights"
    root.mkdir()
    contents = {
        "CosyVoice-BlankEN/config.json": b"config",
        "CosyVoice-BlankEN/generation_config.json": b"generation",
        "CosyVoice-BlankEN/merges.txt": b"merges",
        "CosyVoice-BlankEN/model.safetensors": b"tokenizer",
        "CosyVoice-BlankEN/tokenizer_config.json": b"tokenizer-config",
        "CosyVoice-BlankEN/vocab.json": b"vocab",
        "campplus.onnx": b"campplus",
        "cosyvoice3.yaml": b"yaml",
        "flow.pt": b"flow",
        "hift.pt": b"hift",
        "llm.pt": b"llm",
        "speech_tokenizer_v3.onnx": b"speech-tokenizer",
    }
    specs: dict[str, tuple[int, str]] = {}
    for name, content in contents.items():
        path = root / Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        specs[name] = (len(content), hashlib.sha256(content).hexdigest())
    if cache:
        cache_file = root / ".cache" / "huggingface" / "download.json"
        cache_file.parent.mkdir(parents=True)
        cache_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "MODEL_FILE_SPECS", specs)
    return root


def _code_root(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "third_party" / "Matcha-TTS").mkdir(parents=True)
    return root


def _configure_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cache: bool = False,
) -> tuple[Path, Path]:
    import gaya_pipeline.adapters.cosyvoice3 as module

    code_root = _code_root(tmp_path)
    model_root = _model_root(tmp_path, monkeypatch, cache=cache)
    monkeypatch.setenv(CODE_ROOT_ENV, str(code_root))
    monkeypatch.setenv(MODEL_ROOT_ENV, str(model_root))
    monkeypatch.setattr(module, "_validate_source_checkout", lambda root: None)
    return code_root, model_root


def _prepare_one(
    adapter: CosyVoice3Adapter,
    tmp_path: Path,
    *,
    job: LineJob | None = None,
    voice_id: str = "amitaro-countdown",
) -> LineJob:
    selected_job = _job() if job is None else job
    adapter.prepare(
        [selected_job],
        tmp_path / "artifacts",
        _voices_dir(tmp_path, materialize={voice_id}),
    )
    return selected_job


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_absolute_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_absolute_path(item) for item in value)
    return isinstance(value, str) and Path(value).is_absolute()


def test_profile_registry_and_generation_params_are_canonical() -> None:
    adapter = create_adapter(MODEL_ID)

    assert isinstance(adapter, CosyVoice3Adapter)
    assert adapter.profile.name == "CosyVoice 3 0.5B 2512"
    assert UPSTREAM_REPOSITORY == "QwenAudio/CosyVoice"
    assert adapter.profile.version == PROFILE_VERSION
    assert UPSTREAM_REVISION in adapter.profile.version
    assert MATCHA_REVISION in adapter.profile.version
    assert WEIGHTS_REVISION in adapter.profile.version
    assert "Apache-2.0" in adapter.profile.license_note
    assert "明記されていない" in adapter.profile.license_note
    assert "電子透かしを開示していない" in adapter.profile.license_note
    assert adapter.profile.capabilities.as_dict() == {
        "emotion": True,
        "voice_prompt": False,
        "clone": True,
        "nonverbal": False,
        "reading": True,
    }
    recipe = adapter.take_recipe()
    assert recipe.version == "seed-only-v1"
    assert recipe.seed_policy == "derived-sha256-v1"
    assert recipe.single_take_seed == SEED
    assert recipe.seed_range == (0, 2**32 - 1)
    assert recipe.supports_multiple is True

    params = adapter.generation_params()
    assert params["code_root_environment"] == CODE_ROOT_ENV
    assert params["model_root_environment"] == MODEL_ROOT_ENV
    assert params["architecture"] == ARCHITECTURE
    assert params["model_architecture"] == MODEL_ARCHITECTURE
    assert params["device"] == DEVICE
    assert params["sample_rate_hz"] == SAMPLE_RATE_HZ
    assert "seed" not in params
    assert params["fp16"] is True
    assert params["load_trt"] is False
    assert params["load_vllm"] is False
    assert params["stream"] is False
    assert params["speed"] == 1.0
    assert params["text_frontend"] is False
    assert params["offline_environment"] == dict(OFFLINE_ENVIRONMENT)
    assert (
        params["instruction_policy_version"] == INSTRUCTION_POLICY_VERSION
    )
    assert params["emotion_instruction_templates"] == dict(
        EMOTION_INSTRUCTION_TEMPLATES,
    )
    assert params["model_files"] == {
        name: {"size": size, "sha256": sha256}
        for name, (size, sha256) in MODEL_FILE_SPECS.items()
    }
    assert sum(spec["size"] for spec in params["model_files"].values()) == (
        5_427_029_103
    )
    assert params["reference_assignments"] == {
        f"{scenario}/{character}": voice
        for (scenario, character), voice in (
            CLONE_REFERENCE_ASSIGNMENTS.items()
        )
    }
    assert not _contains_absolute_path(params)


def test_generation_input_preserves_exact_reading_instruction_and_provenance(
    tmp_path: Path,
) -> None:
    adapter = CosyVoice3Adapter(runtime=FakeRuntime())
    job = _prepare_one(adapter, tmp_path)

    generation_input = adapter.generation_input(job, TAKE_CONTEXT)

    assert generation_input == {
        "source_text": "乾杯しよう！",
        "tts_text": "カンパイシヨウ！",
        "reading_source": "line.reading",
        "instruction": EMOTION_INSTRUCTION_TEMPLATES["cheerful"],
        "emotion": "cheerful",
        "intensity": 2,
        "delivery": "明るく話す。",
        "instruction_policy_version": INSTRUCTION_POLICY_VERSION,
        "instruction_template_id": "cheerful",
        "reference_selection_source": "character.reference_voice",
        "reference_voice": "amitaro-countdown",
        "reference_sha256": generation_input["reference_sha256"],
    }
    assert len(generation_input["reference_sha256"]) == 64
    assert generation_input["instruction"].endswith(INSTRUCTION_END)
    assert not _contains_absolute_path(generation_input)


def test_missing_reading_uses_pyopenjtalk_result_without_text_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gaya_pipeline.adapters.cosyvoice3 as module

    calls: list[dict[str, Any]] = []

    def fake_resolve(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            text="カンパイ",
            source="pyopenjtalk.g2p(kana=True)",
        )

    monkeypatch.setattr(module, "resolve_japanese_reading", fake_resolve)
    adapter = CosyVoice3Adapter(runtime=FakeRuntime())
    job = _job(reading=None, text="乾杯")
    _prepare_one(adapter, tmp_path, job=job)

    generation_input = adapter.generation_input(job, TAKE_CONTEXT)

    assert calls == [{"text": "乾杯", "reading": None}]
    assert generation_input["tts_text"] == "カンパイ"
    assert generation_input["tts_text"] != generation_input["source_text"]
    assert generation_input["reading_source"] == (
        "pyopenjtalk.g2p(kana=True)"
    )


@pytest.mark.parametrize(
    ("emotion", "expected"),
    list(EXPECTED_EMOTION_INSTRUCTION_TEMPLATES.items()),
)
def test_all_schema_emotions_have_exact_fixed_instruction_template(
    tmp_path: Path,
    emotion: str,
    expected: str,
) -> None:
    adapter = CosyVoice3Adapter(runtime=FakeRuntime())
    job = _job(emotion=emotion)
    _prepare_one(adapter, tmp_path, job=job)

    instruction = adapter.generation_input(job, TAKE_CONTEXT)["instruction"]

    assert (
        dict(EMOTION_INSTRUCTION_TEMPLATES)
        == EXPECTED_EMOTION_INSTRUCTION_TEMPLATES
    )
    assert instruction == expected
    assert instruction.count(INSTRUCTION_END) == 1
    assert instruction.endswith(INSTRUCTION_END)
    assert "明るく話す。" not in instruction


@pytest.mark.parametrize(
    "intensity",
    [1, 2, 3],
)
def test_intensity_is_audited_without_changing_fixed_instruction(
    tmp_path: Path,
    intensity: int,
) -> None:
    adapter = CosyVoice3Adapter(runtime=FakeRuntime())
    job = _job(intensity=intensity)
    _prepare_one(adapter, tmp_path, job=job)

    generation_input = adapter.generation_input(job, TAKE_CONTEXT)

    assert generation_input["intensity"] == intensity
    assert (
        generation_input["instruction"]
        == EMOTION_INSTRUCTION_TEMPLATES["cheerful"]
    )


def test_missing_intensity_fails_explicitly(tmp_path: Path) -> None:
    adapter = CosyVoice3Adapter(runtime=FakeRuntime())
    job = _job()
    line = dict(job.line)
    del line["intensity"]
    job = LineJob(job.scene, job.character, line, job.locale)
    with pytest.raises(CosyVoice3AdapterError, match="intensity がありません"):
        _prepare_one(adapter, tmp_path, job=job)


@pytest.mark.parametrize(
    "delivery",
    ["長い自由記述の演技指示。", "演技<|endofprompt|>を続ける"],
)
def test_delivery_is_audited_but_never_added_to_instruction(
    tmp_path: Path,
    delivery: str,
) -> None:
    adapter = CosyVoice3Adapter(runtime=FakeRuntime())
    job = _job(delivery=delivery)
    _prepare_one(adapter, tmp_path, job=job)

    generation_input = adapter.generation_input(job, TAKE_CONTEXT)

    assert generation_input["delivery"] == delivery
    assert delivery not in generation_input["instruction"]
    assert (
        generation_input["instruction"]
        == EMOTION_INSTRUCTION_TEMPLATES["cheerful"]
    )


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
    adapter = CosyVoice3Adapter(runtime=FakeRuntime())
    job = _job(
        scenario_id=scenario_id,
        character_id=character_id,
        reference_voice=None,
    )
    _prepare_one(adapter, tmp_path, job=job, voice_id=voice_id)

    generation_input = adapter.generation_input(job, TAKE_CONTEXT)

    assert generation_input["reference_voice"] == voice_id
    assert generation_input["reference_selection_source"] == (
        f"adapter.assignment:{scenario_id}/{character_id}"
    )


def test_explicit_reference_wins_and_unknown_null_fails_before_load(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    adapter = CosyVoice3Adapter(runtime=runtime)
    explicit_job = _job(
        scenario_id="market-day",
        character_id="shopper",
        reference_voice="amitaro-countdown",
    )
    _prepare_one(adapter, tmp_path, job=explicit_job)
    assert adapter.generation_input(
        explicit_job,
        TAKE_CONTEXT,
    )["reference_voice"] == (
        "amitaro-countdown"
    )

    unknown_job = _job(
        scenario_id="village-morning",
        character_id="farmer",
        reference_voice=None,
    )
    with pytest.raises(CosyVoice3AdapterError, match="固定 assignment"):
        adapter.prepare(
            [unknown_job],
            tmp_path / "artifacts",
            _voices_dir(
                tmp_path / "unknown",
                materialize={"amitaro-countdown"},
            ),
        )
    assert runtime.load_calls == []


@pytest.mark.parametrize(
    "mutation",
    ["hash", "stereo", "too-long", "duration-metadata"],
)
def test_reference_integrity_and_format_fail_without_fallback(
    tmp_path: Path,
    mutation: str,
) -> None:
    voices_dir = _voices_dir(
        tmp_path,
        materialize={"amitaro-countdown"},
    )
    audio_path = voices_dir / "amitaro-countdown" / "reference.wav"
    metadata_path = voices_dir / "metadata.yaml"
    document = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    entry = next(
        item
        for item in document["voices"]
        if item["id"] == "amitaro-countdown"
    )

    if mutation == "hash":
        entry["sha256"] = "0" * 64
    elif mutation == "duration-metadata":
        entry["duration_sec"] = 11.0
    else:
        channels = 2 if mutation == "stereo" else 1
        seconds = 31 if mutation == "too-long" else 10
        with wave.open(str(audio_path), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(48_000)
            wav_file.writeframes(b"\x00\x00" * (48_000 * seconds * channels))
        entry["sha256"] = hashlib.sha256(audio_path.read_bytes()).hexdigest()
        entry["duration_sec"] = 20.0 if mutation == "too-long" else 10.0
    metadata_path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(CosyVoice3AdapterError):
        CosyVoice3Adapter(runtime=FakeRuntime()).prepare(
            [_job()],
            tmp_path / "artifacts",
            voices_dir,
        )


def test_generate_concatenates_all_chunks_and_writes_native_pcm16(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime()
    adapter = CosyVoice3Adapter(runtime=runtime)
    job = _prepare_one(adapter, tmp_path)
    code_root, model_root = _configure_roots(tmp_path / "roots", monkeypatch)
    output = tmp_path / "output" / "clip.wav"

    realized = adapter.generate(job, TAKE_CONTEXT, output)

    assert runtime.load_calls == [(code_root.resolve(), model_root.resolve())]
    assert len(runtime.synthesize_calls) == 1
    assert runtime.synthesize_calls[0]["tts_text"] == "カンパイシヨウ！"
    assert (
        runtime.synthesize_calls[0]["instruction"]
        == EMOTION_INSTRUCTION_TEMPLATES["cheerful"]
    )
    assert "明るく話す。" not in runtime.synthesize_calls[0]["instruction"]
    assert runtime.write_calls[0]["waveform"] == [
        [0.0, 0.25, -0.25, 0.0, 0.125],
    ]
    with wave.open(str(output), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == SAMPLE_RATE_HZ
        assert wav_file.getnframes() == 5
    assert realized["phase_peak_vram_mib"] == {
        "runtime_load": {
            "allocated_mib": 100.0,
            "reserved_mib": 125.0,
        },
        "generation": {
            "allocated_mib": 200.0,
            "reserved_mib": 225.0,
        },
    }
    assert realized["providers"] == {
        "speech_tokenizer": [
            "CUDAExecutionProvider",
            "CPUExecutionProvider",
        ],
        "campplus": ["CPUExecutionProvider"],
    }
    assert realized["samples"] == 5
    assert realized["duration_sec"] == 5 / SAMPLE_RATE_HZ
    assert realized["reference_samples"] == 480_000
    assert realized["reference_duration_sec"] == 10.0
    assert realized["instruction_policy_version"] == INSTRUCTION_POLICY_VERSION
    assert realized["instruction_template_id"] == "cheerful"
    assert not _contains_absolute_path(realized)


def test_take_seed_reaches_runtime_and_realized_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime()
    adapter = CosyVoice3Adapter(runtime=runtime)
    job = _prepare_one(adapter, tmp_path)
    _configure_roots(tmp_path / "roots", monkeypatch)
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


@pytest.mark.parametrize(
    "chunks",
    [
        [],
        [{"wrong": [[0.0]]}],
        [{"tts_speech": [[0.0]], "extra": [[0.0]]}],
        [{"tts_speech": []}],
        [{"tts_speech": [[math.nan]]}],
        [{"tts_speech": [[math.inf]]}],
    ],
)
def test_generator_output_rejects_empty_bad_keys_shape_and_nonfinite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chunks: Sequence[Mapping[str, Any]],
) -> None:
    runtime = FakeRuntime()
    runtime.chunks = chunks
    adapter = CosyVoice3Adapter(runtime=runtime)
    job = _prepare_one(adapter, tmp_path)
    _configure_roots(tmp_path / "roots", monkeypatch)

    with pytest.raises(CosyVoice3AdapterError):
        adapter.generate(job, TAKE_CONTEXT, tmp_path / "output.wav")


def test_chunk_concatenation_rejects_sample_count_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime()
    runtime.concatenate_waveforms = lambda waveforms: [[0.0]]
    adapter = CosyVoice3Adapter(runtime=runtime)
    job = _prepare_one(adapter, tmp_path)
    _configure_roots(tmp_path / "roots", monkeypatch)

    with pytest.raises(CosyVoice3AdapterError, match="sample 数"):
        adapter.generate(job, TAKE_CONTEXT, tmp_path / "output.wav")


@pytest.mark.parametrize(
    ("phase", "message"),
    [
        ("load", "runtime load"),
        ("generate", "generation"),
        ("concatenate", "chunk concatenation"),
        ("write", "PCM16 WAV write"),
    ],
)
def test_oom_is_phase_specific(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    message: str,
) -> None:
    runtime = FakeRuntime()
    runtime.oom_on = phase
    adapter = CosyVoice3Adapter(runtime=runtime)
    job = _prepare_one(adapter, tmp_path)
    _configure_roots(tmp_path / "roots", monkeypatch)

    with pytest.raises(
        CosyVoice3AdapterError,
        match=rf"{message}.*out of memory",
    ):
        adapter.generate(job, TAKE_CONTEXT, tmp_path / "output.wav")


def test_prepare_gate_duplicate_unknown_and_ordering(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    adapter = CosyVoice3Adapter(runtime=runtime)
    job = _job()
    voices_dir = _voices_dir(
        tmp_path,
        materialize={"amitaro-countdown"},
    )

    with pytest.raises(CosyVoice3AdapterError, match=r"prepare\(\)"):
        adapter.generation_input(job, TAKE_CONTEXT)
    with pytest.raises(CosyVoice3AdapterError, match="重複"):
        adapter.prepare(
            [job, job],
            tmp_path / "artifacts",
            voices_dir,
        )
    assert runtime.load_calls == []

    adapter.prepare([job], tmp_path / "artifacts", voices_dir)
    assert runtime.load_calls == []
    unknown = _job(line_id="barmaid-999")
    with pytest.raises(CosyVoice3AdapterError, match="input がありません"):
        adapter.generation_input(unknown, TAKE_CONTEXT)


@pytest.mark.parametrize("variable", [CODE_ROOT_ENV, MODEL_ROOT_ENV])
def test_both_environment_roots_are_required_and_absolute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
) -> None:
    runtime = FakeRuntime()
    adapter = CosyVoice3Adapter(runtime=runtime)
    job = _prepare_one(adapter, tmp_path)
    code_root, model_root = _configure_roots(tmp_path / "roots", monkeypatch)
    del code_root, model_root
    monkeypatch.delenv(variable)

    with pytest.raises(CosyVoice3AdapterError, match=variable):
        adapter.generate(job, TAKE_CONTEXT, tmp_path / "missing.wav")
    monkeypatch.setenv(variable, "relative/path")
    with pytest.raises(CosyVoice3AdapterError, match="絶対パス"):
        adapter.generate(job, TAKE_CONTEXT, tmp_path / "relative.wav")
    assert runtime.load_calls == []


def test_environment_root_rejects_symlink_before_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gaya_pipeline.adapters.cosyvoice3 as module

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("この環境では directory symlink を作成できません。")
    monkeypatch.setenv(CODE_ROOT_ENV, str(link))

    with pytest.raises(CosyVoice3AdapterError, match="symlink"):
        module._required_absolute_environment_path(CODE_ROOT_ENV, "source")


def test_model_inventory_allows_cache_but_rejects_extras_hash_and_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gaya_pipeline.adapters.cosyvoice3 as module

    root = _model_root(tmp_path, monkeypatch, cache=True)
    module._validate_model_root(root)

    extra = root / "llm.rl.pt"
    extra.write_bytes(b"extra")
    with pytest.raises(CosyVoice3AdapterError, match="file inventory"):
        module._validate_model_root(root)
    extra.unlink()

    config = root / "CosyVoice-BlankEN" / "config.json"
    config.write_bytes(b"tampered")
    with pytest.raises(CosyVoice3AdapterError, match="file size|SHA-256"):
        module._validate_model_root(root)


def test_model_inventory_rejects_symlinked_root_directory_and_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gaya_pipeline.adapters.cosyvoice3 as module

    root = _model_root(tmp_path, monkeypatch)
    link = tmp_path / "linked-root"
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("この環境では directory symlink を作成できません。")
    with pytest.raises(CosyVoice3AdapterError):
        module._validate_model_root(link)


@pytest.mark.parametrize(
    (
        "revision",
        "status",
        "matcha_revision",
        "matcha_status",
        "message",
    ),
    [
        ("wrong", "", MATCHA_REVISION, "", "source revision"),
        (
            UPSTREAM_REVISION,
            " M file.py",
            MATCHA_REVISION,
            "",
            "未コミット",
        ),
        (UPSTREAM_REVISION, "", "wrong", "", "submodule revision"),
        (
            UPSTREAM_REVISION,
            "",
            MATCHA_REVISION,
            "?? dirty.py",
            "submodule に未コミット",
        ),
    ],
)
def test_source_checkout_requires_exact_clean_revisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision: str,
    status: str,
    matcha_revision: str,
    matcha_status: str,
    message: str,
) -> None:
    import gaya_pipeline.adapters.cosyvoice3 as module

    root = _code_root(tmp_path)
    matcha_root = root / "third_party" / "Matcha-TTS"

    def fake_git(path: Path, *args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(path)
        if args == ("status", "--porcelain=v1", "--untracked-files=all"):
            return matcha_status if path == matcha_root else status
        if path == matcha_root:
            return matcha_revision
        return revision

    monkeypatch.setattr(module, "_git_output", fake_git)

    with pytest.raises(CosyVoice3AdapterError, match=message):
        module._validate_source_checkout(root)


def test_source_checkout_accepts_exact_clean_revisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gaya_pipeline.adapters.cosyvoice3 as module

    root = _code_root(tmp_path)
    matcha_root = root / "third_party" / "Matcha-TTS"

    def fake_git(path: Path, *args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(path)
        if args == ("status", "--porcelain=v1", "--untracked-files=all"):
            return ""
        if path == matcha_root:
            return MATCHA_REVISION
        return UPSTREAM_REVISION

    monkeypatch.setattr(module, "_git_output", fake_git)
    module._validate_source_checkout(root)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("architecture", "CosyVoice2"),
        ("model_architecture", "CosyVoice2Model"),
        ("sample_rate_hz", 22_050),
        ("fp16", False),
        ("frontend_text_frontend", "wetext"),
        ("frontend_device", "cpu"),
        ("llm_device", "cpu"),
        ("flow_device", "cpu"),
        ("hift_device", "cpu"),
        ("speech_tokenizer_providers", ["CPUExecutionProvider"]),
        (
            "speech_tokenizer_providers",
            ["CPUExecutionProvider", "CUDAExecutionProvider"],
        ),
        (
            "campplus_providers",
            ["CUDAExecutionProvider", "CPUExecutionProvider"],
        ),
    ],
)
def test_model_identity_rejects_architecture_cpu_provider_and_sample_rate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: Any,
) -> None:
    runtime = FakeRuntime()
    runtime.identity[key] = value
    adapter = CosyVoice3Adapter(runtime=runtime)
    job = _prepare_one(adapter, tmp_path)
    _configure_roots(tmp_path / "roots", monkeypatch)

    with pytest.raises(CosyVoice3AdapterError, match="identity|provider"):
        adapter.generate(job, TAKE_CONTEXT, tmp_path / "output.wav")


def test_native_load_uses_only_absolute_local_model_and_offline_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _NativeRuntime()
    code_root = _code_root(tmp_path)
    model_root = tmp_path / "weights"
    model_root.mkdir()
    calls: list[dict[str, Any]] = []
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(reset_peak_memory_stats=lambda: None),
    )

    def fake_auto_model(**kwargs: Any) -> object:
        calls.append(kwargs)
        assert Path(kwargs["model_dir"]).is_absolute()
        assert Path(kwargs["model_dir"]).is_dir()
        return object()

    def fake_dependencies(root: Path) -> None:
        assert root == code_root
        runtime._torch = fake_torch
        runtime._auto_model = fake_auto_model

    monkeypatch.setattr(runtime, "_load_dependencies", fake_dependencies)
    for key in OFFLINE_ENVIRONMENT:
        monkeypatch.delenv(key, raising=False)

    runtime.load_model(code_root, model_root)

    assert calls == [
        {
            "model_dir": str(model_root),
            "load_trt": False,
            "load_vllm": False,
            "fp16": True,
        },
    ]
    assert {
        key: __import__("os").environ[key] for key in OFFLINE_ENVIRONMENT
    } == dict(OFFLINE_ENVIRONMENT)


def test_cuda_preload_requires_device_zero() -> None:
    import gaya_pipeline.adapters.cosyvoice3 as module

    initialized: list[bool] = []
    cuda = SimpleNamespace(
        is_available=lambda: True,
        device_count=lambda: 2,
        init=lambda: initialized.append(True),
        current_device=lambda: 1,
    )

    with pytest.raises(CosyVoice3AdapterError, match="device は 0"):
        module._preload_cuda_zero(SimpleNamespace(cuda=cuda))
    assert initialized == [True]


def test_onnxruntime_preflight_allows_tensorrt_before_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gaya_pipeline.adapters.cosyvoice3 as module

    original_version = module.metadata.version

    def fake_version(distribution: str) -> str:
        if distribution == "onnxruntime":
            raise module.metadata.PackageNotFoundError(distribution)
        return original_version(distribution)

    monkeypatch.setattr(module.metadata, "version", fake_version)
    onnxruntime = SimpleNamespace(
        __version__="1.18.0",
        get_available_providers=lambda: [
            "TensorrtExecutionProvider",
            "CUDAExecutionProvider",
            "CPUExecutionProvider",
        ],
    )

    module._validate_onnxruntime_installation(onnxruntime)


def test_onnxruntime_preflight_rejects_cpu_distribution_coexistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gaya_pipeline.adapters.cosyvoice3 as module

    monkeypatch.setattr(module.metadata, "version", lambda name: "1.18.0")
    onnxruntime = SimpleNamespace(
        __version__="1.18.0",
        get_available_providers=lambda: [
            "CUDAExecutionProvider",
            "CPUExecutionProvider",
        ],
    )

    with pytest.raises(CosyVoice3AdapterError, match="CPU distribution"):
        module._validate_onnxruntime_installation(onnxruntime)


def test_native_synthesize_seeds_every_line_and_passes_fixed_api_arguments(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, Any]] = []

    class FakeBackendFlag:
        benchmark = True
        allow_tf32 = True

    fake_torch = SimpleNamespace(
        manual_seed=lambda value: events.append(("torch", value)),
        cuda=SimpleNamespace(
            manual_seed_all=lambda value: events.append(("cuda", value)),
        ),
        backends=SimpleNamespace(
            cudnn=FakeBackendFlag(),
            cuda=SimpleNamespace(matmul=FakeBackendFlag()),
        ),
    )
    fake_numpy = SimpleNamespace(
        random=SimpleNamespace(
            seed=lambda value: events.append(("numpy", value)),
        ),
    )
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class FakeModel:
        def inference_instruct2(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            calls.append((args, kwargs))
            yield {"tts_speech": [[0.0]]}

    runtime = _NativeRuntime()
    runtime._torch = fake_torch
    runtime._numpy = fake_numpy

    chunks = runtime.synthesize(
        FakeModel(),
        tts_text="ヨミ",
        instruction=f"Instruction.{INSTRUCTION_END}",
        reference_wav=tmp_path / "reference.wav",
        seed=123_456,
    )

    assert events == [
        ("numpy", 123_456),
        ("torch", 123_456),
        ("cuda", 123_456),
    ]
    assert fake_torch.backends.cudnn.benchmark is False
    assert fake_torch.backends.cudnn.allow_tf32 is False
    assert fake_torch.backends.cuda.matmul.allow_tf32 is False
    assert chunks == [{"tts_speech": [[0.0]]}]
    assert calls == [
        (
            (
                "ヨミ",
                f"Instruction.{INSTRUCTION_END}",
                str(tmp_path / "reference.wav"),
            ),
            {
                "zero_shot_spk_id": "",
                "stream": False,
                "speed": 1.0,
                "text_frontend": False,
            },
        ),
    ]
