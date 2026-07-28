from __future__ import annotations

import hashlib
import json
import struct
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from gaya_pipeline.adapters import qwen3_tts
from gaya_pipeline.adapters.base import LineJob, TakeContext
from gaya_pipeline.adapters.qwen3_tts import (
    ATTENTION_BACKEND,
    BASE_MODEL_ID,
    BASE_REVISION,
    DELIVERY_INSTRUCTION,
    DEVICE,
    DTYPE,
    EMOTION_INSTRUCTION,
    INTENSITY_INSTRUCTION,
    LANGUAGE,
    MODEL_ID,
    PROFILE_VERSION,
    QWEN_TTS_VERSION,
    REFERENCE_TEXT_BY_EMOTION,
    SEED,
    VOICE_DESIGN_MODEL_ID,
    VOICE_DESIGN_REVISION,
    Qwen3TTSAdapter,
    Qwen3TTSAdapterError,
    _NativeRuntime,
)


class FakeOutOfMemoryError(RuntimeError):
    pass


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.snapshots: list[tuple[str, str, Path]] = []
        self.snapshot_repos: dict[Path, str] = {}
        self.loaded: list[tuple[str, Path]] = []
        self.design_calls: list[dict[str, Any]] = []
        self.prompt_calls: list[dict[str, Any]] = []
        self.clone_calls: list[dict[str, Any]] = []
        self.write_calls: list[tuple[Path, int]] = []
        self.released: list[str] = []
        self.resident_models: set[str] = set()
        self.seeds: list[int] = []
        self.reset_count = 0
        self.peak_count = 0
        self.oom_on: str | None = None

    def snapshot_download(self, repo_id: str, revision: str) -> Path:
        if self.oom_on == "snapshot":
            raise RuntimeError("snapshot failed")
        path = self.root / "snapshots" / repo_id.rsplit("/", 1)[1] / revision
        path.mkdir(parents=True, exist_ok=True)
        self.snapshots.append((repo_id, revision, path))
        self.snapshot_repos[path] = repo_id
        return path

    def load_model(self, snapshot_path: Path) -> dict[str, str]:
        repo_id = self.snapshot_repos[snapshot_path]
        phase = "design_load" if repo_id == VOICE_DESIGN_MODEL_ID else "base_load"
        if self.oom_on == phase:
            raise FakeOutOfMemoryError(phase)
        if repo_id == BASE_MODEL_ID and VOICE_DESIGN_MODEL_ID in self.resident_models:
            raise AssertionError("VoiceDesign and Base are co-resident")
        model = {"repo_id": repo_id}
        self.loaded.append((repo_id, snapshot_path))
        self.resident_models.add(repo_id)
        return model

    def generate_voice_design(
        self,
        model: dict[str, str],
        *,
        text: str,
        language: str,
        instruct: str,
        sampling: dict[str, int | float | bool],
    ) -> tuple[list[list[float]], int]:
        if self.oom_on == "design":
            raise FakeOutOfMemoryError("design")
        call = {
            "model": model["repo_id"],
            "text": text,
            "language": language,
            "instruct": instruct,
            "sampling": dict(sampling),
        }
        self.design_calls.append(call)
        marker = len(self.design_calls) / 10
        return ([[0.0, marker, -marker, 0.0]], 24_000)

    def create_voice_clone_prompt(
        self,
        model: dict[str, str],
        *,
        ref_audio: str,
        ref_text: str,
    ) -> dict[str, int]:
        if self.oom_on == "prompt":
            raise FakeOutOfMemoryError("prompt")
        self.prompt_calls.append(
            {
                "model": model["repo_id"],
                "ref_audio": ref_audio,
                "ref_text": ref_text,
            },
        )
        return {"prompt": len(self.prompt_calls)}

    def generate_voice_clone(
        self,
        model: dict[str, str],
        *,
        text: str,
        language: str,
        voice_clone_prompt: dict[str, int],
        sampling: dict[str, int | float | bool],
    ) -> tuple[list[list[float]], int]:
        if self.oom_on == "clone":
            raise FakeOutOfMemoryError("clone")
        self.clone_calls.append(
            {
                "model": model["repo_id"],
                "text": text,
                "language": language,
                "prompt": voice_clone_prompt,
                "sampling": dict(sampling),
            },
        )
        return ([[0.0, 0.25, -0.25, 0.0]], 24_000)

    def write_pcm16(
        self,
        path: Path,
        samples: list[float],
        sample_rate: int,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(
                b"".join(
                    struct.pack(
                        "<h",
                        round(max(-1.0, min(1.0, sample)) * 32767),
                    )
                    for sample in samples
                ),
            )
        self.write_calls.append((path, sample_rate))

    def seed(self, seed: int) -> None:
        self.seeds.append(seed)

    def reset_peak_memory_stats(self) -> None:
        self.reset_count += 1

    def peak_memory_mib(self) -> dict[str, float]:
        self.peak_count += 1
        return {
            "allocated_mib": float(self.peak_count),
            "reserved_mib": float(self.peak_count) + 0.5,
        }

    def release_model(self) -> None:
        self.released.extend(sorted(self.resident_models))
        self.resident_models.clear()

    def is_out_of_memory(self, error: BaseException) -> bool:
        return isinstance(error, FakeOutOfMemoryError)


def _take_context(adapter: Qwen3TTSAdapter) -> TakeContext:
    return adapter.take_recipe().single_take_context()


def _job(
    *,
    line_id: str = "vendor-001",
    text: str = "いらっしゃい！",
    character_id: str = "vendor",
    voice: str = "明るく張りのある声。",
    personality: str | None = "商売熱心。",
    emotion: str = "cheerful",
    intensity: object = 2,
    delivery: str = "明るく弾むように呼びかける。",
) -> LineJob:
    character: dict[str, Any] = {
        "id": character_id,
        "voice": voice,
    }
    if personality is not None:
        character["personality"] = personality
    return LineJob(
        scene={
            "id": "market-day",
            "setting": "港町の朝市。人通りが多い。",
        },
        character=character,
        line={
            "id": line_id,
            "text": text,
            "emotion": emotion,
            "intensity": intensity,
            "delivery": delivery,
        },
        locale="ja",
    )


def _reference_paths(
    root: Path,
    character: str = "vendor",
    emotion: str = "cheerful",
    intensity: int = 2,
) -> tuple[Path, Path]:
    reference_dir = (
        root
        / "voices"
        / MODEL_ID
        / "market-day"
        / character
        / emotion
        / f"intensity-{intensity}"
    )
    return reference_dir / "reference.wav", reference_dir / "reference.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_profile_and_requested_parameters_are_canonical(tmp_path: Path) -> None:
    adapter = Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path))

    assert adapter.profile.id == "qwen3-tts-12hz-1.7b"
    assert adapter.profile.version == PROFILE_VERSION
    assert QWEN_TTS_VERSION in adapter.profile.version
    assert BASE_REVISION in adapter.profile.version
    assert VOICE_DESIGN_REVISION in adapter.profile.version
    assert "感情参照 bank は A/B 検証前の実験経路" in adapter.profile.license_note
    assert adapter.profile.capabilities.as_dict() == {
        "emotion": False,
        "voice_prompt": True,
        "clone": True,
        "nonverbal": False,
        "reading": False,
    }
    recipe = adapter.take_recipe()
    assert recipe.version == "fixed-single-v1"
    assert recipe.seed_policy == "fixed"
    assert recipe.single_take_seed == SEED
    assert recipe.seed_range == (0, 2**32 - 1)
    assert recipe.supports_multiple is False
    params = adapter.generation_params()
    assert params == {
        "qwen_tts_version": QWEN_TTS_VERSION,
        "base_model": BASE_MODEL_ID,
        "base_revision": BASE_REVISION,
        "voice_design_model": VOICE_DESIGN_MODEL_ID,
        "voice_design_revision": VOICE_DESIGN_REVISION,
        "device": DEVICE,
        "dtype": DTYPE,
        "attention_backend": ATTENTION_BACKEND,
        "sampling": {
            "do_sample": True,
            "repetition_penalty": 1.05,
            "temperature": 0.9,
            "top_p": 1.0,
            "top_k": 50,
            "subtalker_dosample": True,
            "subtalker_temperature": 0.9,
            "subtalker_top_p": 1.0,
            "subtalker_top_k": 50,
            "max_new_tokens": 2048,
        },
        "reference_control": "voice_design_emotion_bank",
        "reference_key": [
            "scenario",
            "character",
            "emotion",
            "intensity",
        ],
        "reference_text_by_emotion": REFERENCE_TEXT_BY_EMOTION,
        "emotion_instruction": EMOTION_INSTRUCTION,
        "delivery_instruction": DELIVERY_INSTRUCTION,
        "intensity_instruction": {
            str(intensity): instruction
            for intensity, instruction in INTENSITY_INSTRUCTION.items()
        },
    }
    assert json.loads(json.dumps(params, ensure_ascii=False)) == params


def test_prepare_caches_by_scenario_character_and_rebuilds_changed_input(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    jobs = [
        _job(),
        _job(line_id="vendor-002", text="今日は安いよ！"),
        _job(
            line_id="child-001",
            text="わあ！",
            character_id="child",
            voice="甲高く元気な子供の声。",
            personality=None,
        ),
    ]
    adapter = Qwen3TTSAdapter(runtime=runtime)
    adapter.prepare(jobs, tmp_path / "artifacts", tmp_path / "voices")

    assert [(repo, revision) for repo, revision, _ in runtime.snapshots] == [
        (VOICE_DESIGN_MODEL_ID, VOICE_DESIGN_REVISION),
    ]
    assert [repo for repo, _ in runtime.loaded] == [VOICE_DESIGN_MODEL_ID]
    assert len(runtime.design_calls) == 2
    assert runtime.released == [VOICE_DESIGN_MODEL_ID]
    vendor_call = next(
        call
        for call in runtime.design_calls
        if "声質: 明るく張りのある声。" in call["instruct"]
    )
    assert "性格: 商売熱心。" in vendor_call["instruct"]
    assert "場面: 港町の朝市。人通りが多い。" in vendor_call["instruct"]
    assert f"感情: {EMOTION_INSTRUCTION['cheerful']}" in (
        vendor_call["instruct"]
    )
    assert f"感情の強度: {INTENSITY_INSTRUCTION[2]}" in (
        vendor_call["instruct"]
    )
    assert f"演技: {DELIVERY_INSTRUCTION['cheerful']}" in vendor_call["instruct"]
    assert "同じキャラクターの声質、年齢感" in vendor_call["instruct"]
    assert "実在の人物や声優を模倣せず" in vendor_call["instruct"]
    assert vendor_call["text"] == REFERENCE_TEXT_BY_EMOTION["cheerful"]

    wav_path, metadata_path = _reference_paths(tmp_path / "artifacts")
    metadata_value = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert set(metadata_value) == {
        "format_version",
        "model",
        "revision",
        "scenario",
        "character",
        "emotion",
        "intensity",
        "delivery",
        "language",
        "text",
        "instruct",
        "seed",
        "sampling",
        "phase_peak_vram_mib",
        "wav_sha256",
    }
    assert metadata_value["wav_sha256"] == _sha256(wav_path)
    assert not list(wav_path.parent.glob("*.pending.*"))

    cached_runtime = FakeRuntime(tmp_path)
    cached_adapter = Qwen3TTSAdapter(runtime=cached_runtime)
    cached_adapter.prepare(jobs, tmp_path / "artifacts", tmp_path / "voices")
    assert cached_runtime.snapshots == []
    assert cached_runtime.loaded == []
    assert cached_runtime.design_calls == []
    assert cached_adapter.generation_input(
        jobs[0],
        _take_context(cached_adapter),
    ) == {
        "text": "いらっしゃい！",
        "language": LANGUAGE,
        "emotion": "cheerful",
        "intensity": 2,
        "reference_control": "voice_design_emotion_bank",
        "reference_text": REFERENCE_TEXT_BY_EMOTION["cheerful"],
        "reference_sha256": _sha256(wav_path),
    }

    original_metadata = metadata_path.read_text(encoding="utf-8")
    changed_runtime = FakeRuntime(tmp_path)
    changed_adapter = Qwen3TTSAdapter(runtime=changed_runtime)
    changed_job = _job(voice="落ち着いた低い声。")
    with pytest.raises(Qwen3TTSAdapterError, match="cache identity"):
        changed_adapter.prepare(
            [changed_job],
            tmp_path / "artifacts",
            tmp_path / "voices",
        )
    assert changed_runtime.snapshots == []
    assert changed_runtime.design_calls == []
    assert metadata_path.read_text(encoding="utf-8") == original_metadata


def test_prepare_builds_distinct_emotion_intensity_banks_and_reuses_prompts(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    cheerful = _job()
    cheerful_low = _job(
        line_id="vendor-002",
        text="今日は安いよ！",
        intensity=1,
        delivery="親しみを込めて軽やかに話す。",
    )
    angry = _job(
        line_id="vendor-003",
        text="ふざけるな！",
        emotion="angry",
        intensity=3,
        delivery="腹の底から強く怒鳴る。",
    )
    adapter = Qwen3TTSAdapter(runtime=runtime)
    artifacts = tmp_path / "artifacts"
    adapter.prepare(
        [cheerful, cheerful_low, angry],
        artifacts,
        tmp_path / "voices",
    )

    assert len(runtime.design_calls) == 3
    for emotion, intensity in (("cheerful", 2), ("cheerful", 1), ("angry", 3)):
        wav_path, metadata_path = _reference_paths(
            artifacts,
            emotion=emotion,
            intensity=intensity,
        )
        assert wav_path.is_file()
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert metadata["emotion"] == emotion
        assert metadata["intensity"] == intensity
        assert metadata["text"] == REFERENCE_TEXT_BY_EMOTION[emotion]

    adapter.generate(
        cheerful,
        _take_context(adapter),
        tmp_path / "audio" / "cheerful.wav",
    )
    adapter.generate(
        angry,
        _take_context(adapter),
        tmp_path / "audio" / "angry.wav",
    )
    adapter.generate(
        cheerful,
        _take_context(adapter),
        tmp_path / "audio" / "cheerful-again.wav",
    )

    assert len(runtime.prompt_calls) == 2
    assert [call["ref_text"] for call in runtime.prompt_calls] == [
        REFERENCE_TEXT_BY_EMOTION["cheerful"],
        REFERENCE_TEXT_BY_EMOTION["angry"],
    ]
    assert runtime.clone_calls[0]["prompt"] is runtime.clone_calls[2]["prompt"]
    assert runtime.clone_calls[0]["prompt"] is not runtime.clone_calls[1]["prompt"]


def test_reference_delivery_recipe_is_line_order_independent(
    tmp_path: Path,
) -> None:
    later = _job(
        line_id="vendor-020",
        delivery="大きく身振りを付けるように話す。",
    )
    earlier = _job(
        line_id="vendor-010",
        delivery="相手へ優しく微笑みかけるように話す。",
    )
    runtime = FakeRuntime(tmp_path)
    artifacts = tmp_path / "artifacts"
    Qwen3TTSAdapter(runtime=runtime).prepare(
        [later, earlier],
        artifacts,
        tmp_path / "voices",
    )

    _, metadata_path = _reference_paths(artifacts)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["delivery"] == DELIVERY_INSTRUCTION["cheerful"]
    assert f"演技: {DELIVERY_INSTRUCTION['cheerful']}" in metadata["instruct"]

    cached_runtime = FakeRuntime(tmp_path)
    Qwen3TTSAdapter(runtime=cached_runtime).prepare(
        [earlier, later],
        artifacts,
        tmp_path / "voices",
    )
    assert cached_runtime.design_calls == []


@pytest.mark.parametrize(
    ("job", "message"),
    [
        (_job(emotion="unknown"), "未対応の line.emotion"),
        (_job(intensity=None), "line.intensity"),
        (_job(intensity=True), "line.intensity"),
        (_job(intensity=4), "line.intensity"),
    ],
)
def test_prepare_rejects_invalid_emotion_bank_inputs(
    tmp_path: Path,
    job: LineJob,
    message: str,
) -> None:
    with pytest.raises(Qwen3TTSAdapterError, match=message):
        Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path)).prepare(
            [job],
            tmp_path / "artifacts",
            tmp_path / "voices",
        )


def test_prepare_fails_fast_on_corrupt_cache(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    job = _job()
    Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path)).prepare(
        [job],
        artifacts,
        tmp_path / "voices",
    )
    wav_path, metadata_path = _reference_paths(artifacts)

    wav_path.write_bytes(wav_path.read_bytes() + b"tampered")
    with pytest.raises(Qwen3TTSAdapterError, match="WAV SHA-256"):
        Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path)).prepare(
            [job],
            artifacts,
            tmp_path / "voices",
        )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["wav_sha256"] = _sha256(wav_path)
    metadata["unexpected"] = True
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(Qwen3TTSAdapterError, match="項目が一致"):
        Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path)).prepare(
            [job],
            artifacts,
            tmp_path / "voices",
        )


def test_base_is_lazy_prompt_is_reused_and_output_is_pcm16(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    first = _job()
    second = _job(line_id="vendor-002", text="今日は安いよ！")
    adapter = Qwen3TTSAdapter(runtime=runtime)
    artifacts = tmp_path / "artifacts"
    adapter.prepare([first, second], artifacts, tmp_path / "voices")

    assert [repo for repo, _ in runtime.loaded] == [VOICE_DESIGN_MODEL_ID]
    output_one = tmp_path / "audio" / "one.wav"
    output_two = tmp_path / "audio" / "two.wav"
    realized_one = adapter.generate(first, _take_context(adapter), output_one)
    realized_two = adapter.generate(second, _take_context(adapter), output_two)

    assert [(repo, revision) for repo, revision, _ in runtime.snapshots] == [
        (VOICE_DESIGN_MODEL_ID, VOICE_DESIGN_REVISION),
        (BASE_MODEL_ID, BASE_REVISION),
    ]
    assert [repo for repo, _ in runtime.loaded] == [
        VOICE_DESIGN_MODEL_ID,
        BASE_MODEL_ID,
    ]
    assert runtime.released == [VOICE_DESIGN_MODEL_ID]
    assert runtime.loaded[1][0] not in runtime.released
    assert all(path.is_dir() for _, path in runtime.loaded)
    assert len(runtime.prompt_calls) == 1
    assert (
        runtime.prompt_calls[0]["ref_text"]
        == REFERENCE_TEXT_BY_EMOTION["cheerful"]
    )
    assert len(runtime.clone_calls) == 2
    assert runtime.clone_calls[0]["language"] == LANGUAGE
    assert runtime.clone_calls[0]["prompt"] is runtime.clone_calls[1]["prompt"]
    assert runtime.seeds == [SEED, SEED, SEED]

    for output_path in (output_one, output_two):
        with wave.open(str(output_path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 24_000

    assert realized_one["seed"] == SEED
    assert realized_one["sample_rate_hz"] == 24_000
    assert set(realized_one["phase_peak_vram_mib"]) == {
        "voice_design_load",
        "voice_design_generate",
        "base_load",
        "voice_clone_generate",
    }
    for peak in realized_one["phase_peak_vram_mib"].values():
        assert set(peak) == {"allocated_mib", "reserved_mib"}
    assert (
        realized_one["phase_peak_vram_mib"]["base_load"]
        == realized_two["phase_peak_vram_mib"]["base_load"]
    )


def test_prepare_and_generate_fail_fast_on_invalid_environment_and_oom(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(qwen3_tts.sys, "platform", "linux")
    with pytest.raises(Qwen3TTSAdapterError, match="Windows native CUDA:0"):
        Qwen3TTSAdapter()

    monkeypatch.setattr(qwen3_tts.sys, "platform", "win32")

    def package_missing(_: str) -> str:
        raise qwen3_tts.metadata.PackageNotFoundError

    monkeypatch.setattr(qwen3_tts.metadata, "version", package_missing)
    with pytest.raises(Qwen3TTSAdapterError, match="qwen-tts==0.1.1"):
        Qwen3TTSAdapter()

    monkeypatch.setattr(
        qwen3_tts.metadata,
        "version",
        lambda _: QWEN_TTS_VERSION,
    )
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
    )
    fake_modules = {
        "torch": fake_torch,
        "soundfile": SimpleNamespace(),
        "huggingface_hub": SimpleNamespace(snapshot_download=lambda **_: ""),
        "qwen_tts": SimpleNamespace(Qwen3TTSModel=object()),
    }
    monkeypatch.setattr(
        qwen3_tts.importlib,
        "import_module",
        lambda name: fake_modules[name],
    )
    with pytest.raises(Qwen3TTSAdapterError, match="CUDA:0"):
        Qwen3TTSAdapter()

    runtime = FakeRuntime(tmp_path)
    adapter = Qwen3TTSAdapter(runtime=runtime)
    job = _job()
    adapter.prepare([job], tmp_path / "artifacts", tmp_path / "voices")
    runtime.oom_on = "clone"
    with pytest.raises(Qwen3TTSAdapterError, match="CUDA out of memory"):
        adapter.generate(job, _take_context(adapter), tmp_path / "output.wav")
    assert not (tmp_path / "output.wav").exists()


def test_native_runtime_writes_soundfile_pcm16(tmp_path: Path) -> None:
    received: dict[str, Any] = {}

    class FakeSoundFile:
        def write(
            self,
            path: str,
            samples: list[float],
            *,
            samplerate: int,
            format: str,
            subtype: str,
        ) -> None:
            received.update(
                {
                    "path": path,
                    "samples": samples,
                    "samplerate": samplerate,
                    "format": format,
                    "subtype": subtype,
                },
            )

    runtime = object.__new__(_NativeRuntime)
    runtime.soundfile = FakeSoundFile()
    output = tmp_path / "native.wav"
    runtime.write_pcm16(output, [0.0, 0.5], 24_000)

    assert received == {
        "path": str(output),
        "samples": [0.0, 0.5],
        "samplerate": 24_000,
        "format": "WAV",
        "subtype": "PCM_16",
    }


def test_adapter_rejects_unprepared_and_non_japanese_jobs(tmp_path: Path) -> None:
    adapter = Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path))
    job = _job()
    with pytest.raises(Qwen3TTSAdapterError, match=r"prepare\(\)"):
        adapter.generation_input(job, _take_context(adapter))

    english_job = LineJob(
        scene=job.scene,
        character=job.character,
        line=job.line,
        locale="en",
    )
    with pytest.raises(Qwen3TTSAdapterError, match="Japanese 固定"):
        adapter.prepare(
            [english_job],
            tmp_path / "artifacts",
            tmp_path / "voices",
        )
