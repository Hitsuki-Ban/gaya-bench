from __future__ import annotations

import hashlib
import json
import math
import struct
import wave
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from gaya_pipeline.adapters import create_adapter
from gaya_pipeline.adapters.base import LineJob, TakeContext
from gaya_pipeline.adapters.voxcpm2 import (
    ARCHITECTURE,
    CFG_VALUE,
    DEVICE,
    DTYPE,
    EMOTION_INSTRUCTIONS,
    INFERENCE_TIMESTEPS,
    INTENSITY_INSTRUCTIONS,
    MODEL_ID,
    MODEL_ROOT_ENV,
    PROFILE_VERSION,
    SAMPLE_RATE_HZ,
    SEED,
    UPSTREAM_REVISION,
    WEIGHTS_REVISION,
    VoxCPM2Adapter,
    VoxCPM2AdapterError,
    _NativeRuntime,
)

TAKE_CONTEXT = VoxCPM2Adapter().take_recipe().single_take_context()

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EMOTIONS = tuple(EMOTION_INSTRUCTIONS)


class FakeOutOfMemoryError(RuntimeError):
    pass


class FakeRuntime:
    def __init__(self) -> None:
        self.load_calls: list[Path] = []
        self.generate_calls: list[dict[str, Any]] = []
        self.write_calls: list[dict[str, Any]] = []
        self.identity: dict[str, Any] = {
            "architecture": ARCHITECTURE,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "device": DEVICE,
            "dtype": DTYPE,
        }
        self.waveform: Any = [0.0, 0.25, -0.25, 0.0]
        self.oom_on: str | None = None
        self._phase = 0

    def load_model(self, snapshot_path: Path) -> dict[str, str]:
        self.load_calls.append(snapshot_path)
        if self.oom_on == "load":
            raise FakeOutOfMemoryError("load")
        return {"model": "voxcpm2"}

    def model_identity(self, model: Any) -> dict[str, Any]:
        assert model == {"model": "voxcpm2"}
        return dict(self.identity)

    def generate(
        self,
        model: Any,
        *,
        text: str,
        reference_wav_path: Path | None,
        seed: int,
    ) -> Any:
        assert model == {"model": "voxcpm2"}
        phase = "design" if reference_wav_path is None else "clone"
        self.generate_calls.append(
            {
                "text": text,
                "reference_wav_path": reference_wav_path,
                "phase": phase,
                "seed": seed,
            },
        )
        if self.oom_on == phase:
            raise FakeOutOfMemoryError(phase)
        return self.waveform

    def write_pcm16(self, path: Path, samples: Any, sample_rate: int) -> None:
        self.write_calls.append(
            {
                "path": path,
                "samples": samples,
                "sample_rate": sample_rate,
            },
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            frames = bytearray()
            for sample in samples:
                value = max(-1.0, min(1.0, float(sample)))
                frames.extend(struct.pack("<h", round(value * 32_767)))
            wav_file.writeframes(frames)

    def reset_peak_memory_stats(self) -> None:
        self._phase += 1

    def peak_memory_mib(self) -> dict[str, float]:
        return {
            "allocated_mib": float(self._phase * 100),
            "reserved_mib": float(self._phase * 100 + 25),
        }

    def is_out_of_memory(self, error: BaseException) -> bool:
        return isinstance(error, FakeOutOfMemoryError)


def _job(
    *,
    line_id: str = "vendor-001",
    reference_voice: str | None = "amitaro-countdown",
    emotion: str = "cheerful",
    intensity: int = 2,
    delivery: str = "明るく、少し早口で話す。",
    reading: str | None = "ヤスイヨ、ミテッテ！",
    voice: str = "明るく通る中高音。",
    locale: str = "ja",
) -> LineJob:
    return LineJob(
        scene={"id": "market-day", "setting": "昼の市場。"},
        character={
            "id": "vendor",
            "name": "果物売り",
            "gender": "female",
            "age": "adult",
            "archetype": "果物売り",
            "voice": voice,
            "personality": "快活で商売熱心",
            "reference_voice": reference_voice,
        },
        line={
            "id": line_id,
            "text": "安いよ、見てって！",
            "reading": reading,
            "emotion": emotion,
            "intensity": intensity,
            "delivery": delivery,
        },
        locale=locale,
    )


def _model_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_cache: bool = True,
) -> Path:
    import gaya_pipeline.adapters.voxcpm2 as module

    root = tmp_path / "weights"
    root.mkdir()
    config = {
        "architecture": ARCHITECTURE,
        "audio_vae_config": {
            "sample_rate": 16_000,
            "out_sample_rate": SAMPLE_RATE_HZ,
        },
        "device": "cuda",
        "dtype": DTYPE,
    }
    contents = {
        "config.json": json.dumps(config).encode(),
        "model.safetensors": b"model",
        "audiovae.pth": b"audio-vae",
    }
    specs: dict[str, tuple[int, str]] = {}
    for name, content in contents.items():
        (root / name).write_bytes(content)
        specs[name] = (len(content), hashlib.sha256(content).hexdigest())
    if include_cache:
        (root / ".cache").mkdir()
        (root / ".cache" / "ignored-download-metadata").write_text(
            "not runtime input",
            encoding="utf-8",
        )
    monkeypatch.setattr(module, "MODEL_FILE_SPECS", specs)
    return root


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
            encoding="utf-8",
        ),
        encoding="utf-8",
    )
    selected = {"amitaro-countdown"} if materialize is None else materialize
    for index, entry in enumerate(metadata["voices"]):
        voice_id = str(entry["id"])
        if voice_id not in selected:
            continue
        path = voices_dir / voice_id / "reference.wav"
        path.parent.mkdir()
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE_HZ)
            frames = bytearray(b"\x00\x00" * (SAMPLE_RATE_HZ * 10))
            frames[index * 2 : index * 2 + 2] = struct.pack("<h", index + 1)
            wav_file.writeframes(frames)
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        entry["duration_sec"] = 10.0
    (voices_dir / "metadata.yaml").write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return voices_dir


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_absolute_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_absolute_path(item) for item in value)
    return isinstance(value, str) and Path(value).is_absolute()


def test_profile_registry_and_requested_parameters_are_canonical() -> None:
    adapter = create_adapter(MODEL_ID)

    assert isinstance(adapter, VoxCPM2Adapter)
    assert adapter.profile.version == PROFILE_VERSION
    assert UPSTREAM_REVISION in adapter.profile.version
    assert WEIGHTS_REVISION in adapter.profile.version
    assert "Apache-2.0" in adapter.profile.license_note
    assert adapter.profile.capabilities.as_dict() == {
        "emotion": True,
        "voice_prompt": True,
        "clone": True,
        "nonverbal": False,
        "reading": False,
    }
    recipe = adapter.take_recipe()
    assert recipe.version == "seed-only-v1"
    assert recipe.seed_policy == "derived-sha256-v1"
    assert recipe.single_take_seed == SEED
    assert recipe.seed_range == (0, 2**32 - 1)
    assert recipe.supports_multiple is True
    params = adapter.generation_params()
    assert params["model_root_environment"] == MODEL_ROOT_ENV
    assert params["architecture"] == "voxcpm2"
    assert params["device"] == "cuda:0"
    assert params["dtype"] == "bfloat16"
    assert params["sample_rate_hz"] == 48_000
    assert params["load_denoiser"] is False
    assert params["optimize"] is False
    assert params["normalize"] is False
    assert params["denoise"] is False
    assert params["retry_badcase"] is False
    assert params["cfg_value"] == CFG_VALUE
    assert params["inference_timesteps"] == INFERENCE_TIMESTEPS
    assert "seed" not in params
    assert params["emotion_instructions"] == EMOTION_INSTRUCTIONS
    assert params["intensity_instructions"] == {
        str(intensity): instruction
        for intensity, instruction in INTENSITY_INSTRUCTIONS.items()
    }
    assert json.loads(json.dumps(params)) == params


@pytest.mark.parametrize(
    ("component", "actual"),
    [
        ("torch", "2.9.0+cu130"),
        ("torchaudio", "2.9.0+cu130"),
        ("cuda", "12.8"),
        ("voxcpm", "2.0.3"),
        ("transformers", "5.2.0"),
    ],
)
def test_native_runtime_dependency_versions_are_exact_and_never_import_torchcodec(
    monkeypatch: pytest.MonkeyPatch,
    component: str,
    actual: str,
) -> None:
    import gaya_pipeline.adapters.voxcpm2 as module

    expected = {
        "torch": module.TORCH_VERSION,
        "torchaudio": module.TORCHAUDIO_VERSION,
        "cuda": module.CUDA_VERSION,
        "voxcpm": module.VOXCPM_VERSION,
        "transformers": module.TRANSFORMERS_VERSION,
    }
    expected[component] = actual
    imported: list[str] = []
    fake_cuda = SimpleNamespace(
        is_available=lambda: True,
        device_count=lambda: 1,
        is_bf16_supported=lambda: True,
    )
    modules = {
        "torch": SimpleNamespace(
            __version__=expected["torch"],
            version=SimpleNamespace(cuda=expected["cuda"]),
            cuda=fake_cuda,
        ),
        "torchaudio": SimpleNamespace(__version__=expected["torchaudio"]),
        "soundfile": SimpleNamespace(write=lambda *args, **kwargs: None),
        "voxcpm": SimpleNamespace(VoxCPM=object()),
    }

    def fake_import(name: str) -> Any:
        imported.append(name)
        return modules[name]

    def fake_version(name: str) -> str:
        return {
            "voxcpm": expected["voxcpm"],
            "transformers": expected["transformers"],
        }[name]

    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module.importlib, "import_module", fake_import)
    monkeypatch.setattr(module.metadata, "version", fake_version)

    runtime = module._NativeRuntime()
    with pytest.raises(VoxCPM2AdapterError, match="version が一致"):
        runtime._load_dependencies()
    assert "torchcodec" not in imported
    assert "huggingface_hub" not in imported


def test_native_runtime_disables_retry_and_verifies_actual_seed() -> None:
    calls: dict[str, Any] = {}

    class FakeModel:
        def __init__(self) -> None:
            self.tts_model = SimpleNamespace(last_successful_seed=123_456)

        def generate(self, **kwargs: Any) -> list[float]:
            calls.update(kwargs)
            return [0.0]

    model = FakeModel()
    runtime = _NativeRuntime()
    assert runtime.generate(
        model,
        text="テスト",
        reference_wav_path=None,
        seed=123_456,
    ) == [0.0]
    assert calls["retry_badcase"] is False
    assert calls["seed"] == 123_456

    model.tts_model.last_successful_seed = 654_321
    with pytest.raises(VoxCPM2AdapterError, match="actual=654321"):
        runtime.generate(
            model,
            text="テスト",
            reference_wav_path=None,
            seed=123_456,
        )


def test_all_emotions_use_surface_text_and_explicit_reference_provenance(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    adapter = VoxCPM2Adapter(runtime=runtime)
    jobs = [
        _job(
            line_id=f"vendor-{index:03d}",
            emotion=emotion,
            intensity=(index % 3) + 1,
            delivery=f"演技指示 {index}",
        )
        for index, emotion in enumerate(EMOTIONS, start=1)
    ]
    voices_dir = _voices_dir(tmp_path)

    adapter.prepare(jobs, tmp_path / "artifacts", voices_dir)

    assert runtime.load_calls == []
    assert runtime.generate_calls == []
    for index, (emotion, job) in enumerate(zip(EMOTIONS, jobs, strict=True), start=1):
        generation_input = adapter.generation_input(job, TAKE_CONTEXT)
        intensity = (index % 3) + 1
        expected_control = (
            f"Speak {INTENSITY_INSTRUCTIONS[intensity]} "
            f"{EMOTION_INSTRUCTIONS[emotion]}. Delivery: 演技指示 {index}"
        )
        assert generation_input["source_text"] == "安いよ、見てって！"
        assert generation_input["text"] == "安いよ、見てって！"
        assert generation_input["reading_source"] == "line.text"
        assert generation_input["emotion"] == emotion
        assert generation_input["intensity"] == intensity
        assert generation_input["delivery"] == f"演技指示 {index}"
        assert generation_input["control"] == expected_control
        assert generation_input["model_text"] == (
            f"({expected_control})安いよ、見てって！"
        )
        assert generation_input["reference_kind"] == "asset"
        assert generation_input["reference_selection_source"] == (
            "character.reference_voice"
        )
        assert generation_input["reference_voice"] == "amitaro-countdown"
        assert not _contains_absolute_path(generation_input)


def test_clone_is_lazy_uses_reference_and_writes_pcm16(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _model_root(tmp_path, monkeypatch)
    runtime = FakeRuntime()
    adapter = VoxCPM2Adapter(runtime=runtime, model_root=root)
    job = _job()
    voices_dir = _voices_dir(tmp_path)
    adapter.prepare([job], tmp_path / "artifacts", voices_dir)

    output = tmp_path / "result.wav"
    realized = adapter.generate(job, TAKE_CONTEXT, output)

    assert runtime.load_calls == [root]
    assert len(runtime.generate_calls) == 1
    clone_call = runtime.generate_calls[0]
    assert clone_call["phase"] == "clone"
    generation_input = adapter.generation_input(job, TAKE_CONTEXT)
    assert clone_call["text"] == generation_input["model_text"]
    assert clone_call["text"].endswith("安いよ、見てって！")
    assert clone_call["reference_wav_path"] == (
        voices_dir.resolve() / "amitaro-countdown" / "reference.wav"
    )
    assert realized["phase_peak_vram_mib"] == {
        "runtime_load": {
            "allocated_mib": 100.0,
            "reserved_mib": 125.0,
        },
        "controllable_clone_generate": {
            "allocated_mib": 200.0,
            "reserved_mib": 225.0,
        },
    }
    assert realized["seed"] == 42
    assert realized["sample_rate_hz"] == 48_000
    assert realized["reading_source"] == "line.text"
    with wave.open(str(output), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 48_000
        assert wav_file.getnframes() == 4


def test_reference_seed_is_fixed_and_take_seed_reaches_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _model_root(tmp_path, monkeypatch)
    runtime = FakeRuntime()
    adapter = VoxCPM2Adapter(runtime=runtime, model_root=root)
    job = _job(reference_voice=None)
    adapter.prepare([job], tmp_path / "artifacts", tmp_path / "unused-voices")
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

    assert [call["phase"] for call in runtime.generate_calls] == [
        "design",
        "clone",
        "clone",
    ]
    assert [call["seed"] for call in runtime.generate_calls] == [
        SEED,
        SEED,
        123_456,
    ]
    assert first["seed"] == SEED
    assert first["sampling"] == first_context.sampling_dict()
    assert second["seed"] == 123_456
    assert second["sampling"] == second_context.sampling_dict()


def test_voice_design_cache_reuse_identity_change_and_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _model_root(tmp_path, monkeypatch)
    artifacts = tmp_path / "artifacts"
    voices_dir = tmp_path / "unused-voices"
    job = _job(reference_voice=None)

    first_runtime = FakeRuntime()
    first = VoxCPM2Adapter(runtime=first_runtime, model_root=root)
    first.prepare([job], artifacts, voices_dir)
    cache_dir = artifacts / "voices" / MODEL_ID / "market-day" / "vendor"
    wav_path = cache_dir / "reference.wav"
    metadata_path = cache_dir / "reference.json"

    assert [call["phase"] for call in first_runtime.generate_calls] == ["design"]
    assert wav_path.is_file()
    assert metadata_path.is_file()
    first_input = first.generation_input(job, TAKE_CONTEXT)
    assert first_input["reference_kind"] == "voice_design"
    assert first_input["reference_selection_source"] == "adapter.voice_design"
    assert first_input["reference_voice"] is None
    assert not _contains_absolute_path(first_input)

    reuse_runtime = FakeRuntime()
    reuse = VoxCPM2Adapter(runtime=reuse_runtime, model_root=root)
    reuse.prepare([job], artifacts, voices_dir)
    assert reuse_runtime.load_calls == []
    assert reuse_runtime.generate_calls == []
    assert reuse.generation_input(job, TAKE_CONTEXT) == first_input

    changed_job = _job(reference_voice=None, voice="低く落ち着いた声。")
    changed_runtime = FakeRuntime()
    changed = VoxCPM2Adapter(runtime=changed_runtime, model_root=root)
    changed.prepare([changed_job], artifacts, voices_dir)
    assert [call["phase"] for call in changed_runtime.generate_calls] == ["design"]
    assert changed.generation_input(changed_job, TAKE_CONTEXT) != first_input

    wav_path.write_bytes(wav_path.read_bytes() + b"corrupt")
    corrupt_runtime = FakeRuntime()
    corrupt = VoxCPM2Adapter(runtime=corrupt_runtime, model_root=root)
    with pytest.raises(VoxCPM2AdapterError, match="WAV|SHA-256"):
        corrupt.prepare([changed_job], artifacts, voices_dir)
    assert corrupt_runtime.load_calls == []
    assert corrupt_runtime.generate_calls == []


def test_schema_optional_character_fields_and_intensity_may_be_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _model_root(tmp_path, monkeypatch)
    base_job = _job(reference_voice=None)
    character = dict(base_job.character)
    for key in ("archetype", "personality", "reference_voice"):
        character.pop(key)
    line = dict(base_job.line)
    line.pop("intensity")
    job = LineJob(
        scene=base_job.scene,
        character=character,
        line=line,
        locale=base_job.locale,
    )

    runtime = FakeRuntime()
    adapter = VoxCPM2Adapter(runtime=runtime, model_root=root)
    adapter.prepare([job], tmp_path / "artifacts", tmp_path / "unused-voices")

    assert [call["phase"] for call in runtime.generate_calls] == ["design"]
    design_text = runtime.generate_calls[0]["text"]
    assert "Voice qualities: 明るく通る中高音。" in design_text
    assert "Role:" not in design_text
    assert "Personality:" not in design_text
    generation_input = adapter.generation_input(job, TAKE_CONTEXT)
    assert generation_input["intensity"] == 2
    assert generation_input["reference_kind"] == "voice_design"
    assert generation_input["reference_voice"] is None


def test_half_or_pending_voice_design_cache_fails_without_rebuild(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    cache_dir = artifacts / "voices" / MODEL_ID / "market-day" / "vendor"
    cache_dir.mkdir(parents=True)
    (cache_dir / "reference.json").write_text("{}", encoding="utf-8")

    runtime = FakeRuntime()
    adapter = VoxCPM2Adapter(runtime=runtime, model_root=tmp_path / "unused")
    with pytest.raises(VoxCPM2AdapterError, match="pair が壊れ"):
        adapter.prepare(
            [_job(reference_voice=None)],
            artifacts,
            tmp_path / "unused-voices",
        )
    assert runtime.load_calls == []

    (cache_dir / "reference.json").unlink()
    (cache_dir / ".reference.pending.wav").write_bytes(b"partial")
    with pytest.raises(VoxCPM2AdapterError, match="pending file"):
        adapter.prepare(
            [_job(reference_voice=None)],
            artifacts,
            tmp_path / "unused-voices",
        )
    assert runtime.load_calls == []


def test_explicit_reference_corruption_and_unsafe_metadata_do_not_fallback(
    tmp_path: Path,
) -> None:
    voices_dir = _voices_dir(tmp_path)
    reference = voices_dir / "amitaro-countdown" / "reference.wav"
    reference.write_bytes(reference.read_bytes() + b"tampered")
    runtime = FakeRuntime()
    adapter = VoxCPM2Adapter(runtime=runtime)

    with pytest.raises(VoxCPM2AdapterError, match="SHA-256|WAV"):
        adapter.prepare([_job()], tmp_path / "artifacts", voices_dir)
    assert runtime.load_calls == []
    assert runtime.generate_calls == []

    metadata = yaml.safe_load(
        (voices_dir / "metadata.yaml").read_text(encoding="utf-8"),
    )
    metadata["voices"][0]["file"] = "../outside.wav"
    (voices_dir / "metadata.yaml").write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(VoxCPM2AdapterError, match="metadata"):
        adapter.prepare([_job()], tmp_path / "artifacts", voices_dir)
    assert runtime.load_calls == []


def test_model_root_environment_snapshot_allowlist_and_identity_fail_fast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voices_dir = _voices_dir(tmp_path)
    job = _job()
    runtime = FakeRuntime()
    adapter = VoxCPM2Adapter(runtime=runtime)
    adapter.prepare([job], tmp_path / "artifacts", voices_dir)

    monkeypatch.delenv(MODEL_ROOT_ENV, raising=False)
    with pytest.raises(VoxCPM2AdapterError, match=MODEL_ROOT_ENV):
        adapter.generate(job, TAKE_CONTEXT, tmp_path / "missing-env.wav")

    monkeypatch.setenv(MODEL_ROOT_ENV, str(tmp_path / "missing"))
    with pytest.raises(VoxCPM2AdapterError, match="存在しません"):
        adapter.generate(job, TAKE_CONTEXT, tmp_path / "missing-root.wav")

    root = _model_root(tmp_path, monkeypatch)
    monkeypatch.setenv(MODEL_ROOT_ENV, str(root))
    (root / "unexpected.bin").write_bytes(b"unexpected")
    with pytest.raises(VoxCPM2AdapterError, match="allowlist"):
        adapter.generate(job, TAKE_CONTEXT, tmp_path / "unexpected.wav")
    (root / "unexpected.bin").unlink()

    runtime.identity["dtype"] = "float16"
    with pytest.raises(VoxCPM2AdapterError, match="runtime identity"):
        adapter.generate(job, TAKE_CONTEXT, tmp_path / "identity.wav")


@pytest.mark.parametrize("oom_phase", ["load", "design", "clone"])
def test_runtime_oom_fails_fast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    oom_phase: str,
) -> None:
    root = _model_root(tmp_path, monkeypatch)
    runtime = FakeRuntime()
    runtime.oom_on = oom_phase
    reference_voice = None if oom_phase == "design" else "amitaro-countdown"
    adapter = VoxCPM2Adapter(runtime=runtime, model_root=root)
    job = _job(reference_voice=reference_voice)
    voices_dir = (
        tmp_path / "unused-voices"
        if reference_voice is None
        else _voices_dir(tmp_path)
    )

    if oom_phase == "design":
        action = lambda: adapter.prepare([job], tmp_path / "artifacts", voices_dir)
    else:
        adapter.prepare([job], tmp_path / "artifacts", voices_dir)
        action = lambda: adapter.generate(
            job,
            TAKE_CONTEXT,
            tmp_path / "output.wav",
        )
    with pytest.raises(VoxCPM2AdapterError, match="CUDA out of memory"):
        action()


@pytest.mark.parametrize(
    "waveform",
    [
        [],
        [math.nan],
        [math.inf],
        [0],
        [[0.0]],
    ],
)
def test_invalid_runtime_waveform_fails_before_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    waveform: Any,
) -> None:
    root = _model_root(tmp_path, monkeypatch)
    runtime = FakeRuntime()
    runtime.waveform = waveform
    adapter = VoxCPM2Adapter(runtime=runtime, model_root=root)
    job = _job()
    adapter.prepare([job], tmp_path / "artifacts", _voices_dir(tmp_path))
    output = tmp_path / "output.wav"

    with pytest.raises(VoxCPM2AdapterError, match="waveform|float"):
        adapter.generate(job, TAKE_CONTEXT, output)
    assert not output.exists()


def test_explicit_reading_is_ignored_and_invalid_job_fails_fast(
    tmp_path: Path,
) -> None:
    adapter = VoxCPM2Adapter(runtime=FakeRuntime())
    job = _job(reading="ヤスイヨ、ミテッテ！")
    adapter.prepare([job], tmp_path / "artifacts", _voices_dir(tmp_path))
    generation_input = adapter.generation_input(job, TAKE_CONTEXT)
    assert generation_input["text"] == "安いよ、見てって！"
    assert generation_input["reading_source"] == "line.text"

    with pytest.raises(VoxCPM2AdapterError, match="locale は ja"):
        adapter.prepare(
            [_job(locale="en")],
            tmp_path / "artifacts",
            _voices_dir(tmp_path / "second"),
        )
