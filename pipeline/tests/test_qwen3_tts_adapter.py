from __future__ import annotations

import hashlib
import json
import struct
import wave
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from gaya_pipeline.adapters import qwen3_tts
from gaya_pipeline.adapters.base import LineJob, TakeContext
from gaya_pipeline.adapters.qwen3_tts import (
    ATTENTION_BACKEND,
    BASE_MODEL_ID,
    BASE_REVISION,
    DEVICE,
    DTYPE,
    LANGUAGE,
    MODEL_ID,
    PROFILE_VERSION,
    QWEN_TTS_VERSION,
    REFERENCE_TEXT,
    SEED,
    VOICE_DESIGN_MODEL_ID,
    VOICE_DESIGN_REVISION,
    Qwen3TTSAdapter,
    Qwen3TTSAdapterError,
    _NativeRuntime,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


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
        self.peak_values: list[float] = []
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
        self.design_calls.append(
            {
                "model": model["repo_id"],
                "text": text,
                "language": language,
                "instruct": instruct,
                "sampling": dict(sampling),
            },
        )
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
        value = (
            self.peak_values.pop(0)
            if self.peak_values
            else float(self.peak_count)
        )
        return {
            "allocated_mib": value,
            "reserved_mib": value + 0.5,
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
    name: str = "受付嬢",
    kind: str | None = "human",
    gender: str = "female",
    age: str = "young_adult",
    archetype: str = "受付",
    voice: str = "明るく張りのある声。",
    personality: str = "親切で事務的。",
    reference_voice: str | None = None,
    emotion: str = "cheerful",
    intensity: object = 2,
    delivery: str = "明るく弾むように呼びかける。",
    setting: str = "港町の朝市。人通りが多い。",
) -> LineJob:
    character: dict[str, Any] = {
        "id": character_id,
        "name": name,
        "gender": gender,
        "age": age,
        "archetype": archetype,
        "voice": voice,
        "personality": personality,
        "reference_voice": reference_voice,
    }
    if kind is not None:
        character["kind"] = kind
    return LineJob(
        scene={"id": "market-day", "setting": setting},
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
) -> tuple[Path, Path]:
    reference_dir = (
        root
        / "voices"
        / MODEL_ID
        / "market-day"
        / character
        / "character-anchor-v3"
    )
    return reference_dir / "reference.wav", reference_dir / "reference.json"


def _old_reference_paths(root: Path) -> tuple[Path, Path]:
    reference_dir = (
        root
        / "voices"
        / MODEL_ID
        / "market-day"
        / "vendor"
        / "cheerful"
        / "intensity-2"
    )
    return reference_dir / "reference.wav", reference_dir / "reference.json"


def _voices_dir(
    tmp_path: Path,
    *,
    voice_id: str = "lux-emotion-76",
) -> tuple[Path, Mapping[str, Any]]:
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
    entry = next(item for item in metadata["voices"] if item["id"] == voice_id)
    wav_path = voices_dir / entry["file"]
    wav_path.parent.mkdir(parents=True)
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48_000)
        wav_file.writeframes(b"\x00\x00" * 48_000 * 10)
    entry["sha256"] = _sha256(wav_path)
    entry["duration_sec"] = 10.0
    (voices_dir / "metadata.yaml").write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return voices_dir, entry


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_profile_and_requested_parameters_are_canonical(tmp_path: Path) -> None:
    adapter = Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path))

    assert adapter.profile.id == MODEL_ID
    assert adapter.profile.version == PROFILE_VERSION
    assert QWEN_TTS_VERSION in adapter.profile.version
    assert BASE_REVISION in adapter.profile.version
    assert VOICE_DESIGN_REVISION in adapter.profile.version
    assert "キャラクター単位" in adapter.profile.license_note
    assert adapter.profile.capabilities.as_dict() == {
        "emotion": False,
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
    assert params["reference_key"] == ["scenario", "character"]
    assert params["reference_controls"] == {
        "explicit_reference": "voice_asset",
        "designed_reference": "voice_design_character_anchor",
    }
    assert params["voice_design_anchor_text"] == REFERENCE_TEXT
    assert params["voice_design_cache_format_version"] == 3
    assert params["voice_design_cache_directory"] == "character-anchor-v3"
    assert params["character_identity_fields"] == [
        "scenario",
        "character",
        "name",
        "kind",
        "gender",
        "age",
        "archetype",
        "voice",
        "personality",
        "scene_setting",
    ]
    assert params["gender_labels"]["female"] == "女性"
    assert params["age_labels"]["young_adult"] == "若い成人"
    assert params["kind_labels"]["human"] == "人間"
    assert "emotion" not in params
    assert "delivery" not in params
    assert json.loads(json.dumps(params, ensure_ascii=False)) == params


def test_voice_design_anchor_contains_complete_character_identity(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    jobs = [
        _job(),
        _job(line_id="vendor-002", text="こちらへどうぞ。"),
        _job(
            line_id="spirit-001",
            character_id="spirit",
            name="森の精霊",
            kind="spirit",
            gender="neutral",
            age="child",
            archetype="案内役",
            voice="軽く澄んだ声。",
            personality="好奇心旺盛。",
        ),
    ]
    artifacts = tmp_path / "artifacts"
    adapter = Qwen3TTSAdapter(runtime=runtime)
    adapter.prepare(jobs, artifacts, tmp_path / "unused-voices")

    assert [repo for repo, _ in runtime.loaded] == [VOICE_DESIGN_MODEL_ID]
    assert len(runtime.design_calls) == 2
    assert runtime.released == [VOICE_DESIGN_MODEL_ID]
    vendor_call = next(
        call for call in runtime.design_calls if "名前: 受付嬢" in call["instruct"]
    )
    for expected in (
        "種別: 人間 (human)",
        "性別: 女性 (female)",
        "年齢: 若い成人 (young_adult)",
        "役柄: 受付",
        "声質: 明るく張りのある声。",
        "性格: 親切で事務的。",
        "場面: 港町の朝市。人通りが多い。",
        "指定した性別と年齢から絶対に逸脱しない",
        "自然で落ち着いた中立の発声",
    ):
        assert expected in vendor_call["instruct"]
    assert vendor_call["text"] == REFERENCE_TEXT
    assert vendor_call["language"] == LANGUAGE

    wav_path, metadata_path = _reference_paths(artifacts)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["format_version"] == 3
    assert metadata["reference_control"] == "voice_design_character_anchor"
    assert metadata["character_identity"] == {
        "scenario": "market-day",
        "character": "vendor",
        "name": "受付嬢",
        "kind": "human",
        "gender": "female",
        "age": "young_adult",
        "archetype": "受付",
        "voice": "明るく張りのある声。",
        "personality": "親切で事務的。",
        "scene_setting": "港町の朝市。人通りが多い。",
    }
    assert metadata["wav_sha256"] == _sha256(wav_path)

    receipt = adapter.generation_input(jobs[0], _take_context(adapter))
    assert receipt == {
        "text": "いらっしゃい！",
        "language": LANGUAGE,
        "character_identity": metadata["character_identity"],
        "reference_control": "voice_design_character_anchor",
        "reference_source_id": (
            f"{VOICE_DESIGN_MODEL_ID}@{VOICE_DESIGN_REVISION}"
        ),
        "reference_sha256": _sha256(wav_path),
        "reference_text": REFERENCE_TEXT,
    }
    assert "emotion" not in receipt
    assert "intensity" not in receipt
    assert "delivery" not in receipt


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "別の受付嬢"),
        ("kind", "spirit"),
        ("gender", "male"),
        ("age", "elderly"),
        ("archetype", "衛兵"),
        ("voice", "低く太い声。"),
        ("personality", "無愛想。"),
        ("setting", "王城の大広間。"),
    ],
)
def test_character_identity_change_fails_against_existing_cache(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    artifacts = tmp_path / "artifacts"
    Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path)).prepare(
        [_job()],
        artifacts,
        tmp_path / "unused-voices",
    )
    changed = _job(**{field: value})
    runtime = FakeRuntime(tmp_path)
    with pytest.raises(Qwen3TTSAdapterError, match="cache identity"):
        Qwen3TTSAdapter(runtime=runtime).prepare(
            [changed],
            artifacts,
            tmp_path / "unused-voices",
        )
    assert runtime.snapshots == []
    assert runtime.design_calls == []


def test_same_character_across_emotions_uses_one_anchor_and_prompt(
    tmp_path: Path,
) -> None:
    cheerful = _job()
    angry = _job(
        line_id="vendor-002",
        text="そこを動かないで！",
        emotion="angry",
        intensity=3,
        delivery="鋭く制止する。",
    )
    whisper = _job(
        line_id="vendor-003",
        text="静かにお願いします。",
        emotion="whisper",
        intensity=1,
        delivery="小声で注意する。",
    )
    runtime = FakeRuntime(tmp_path)
    adapter = Qwen3TTSAdapter(runtime=runtime)
    artifacts = tmp_path / "artifacts"
    adapter.prepare(
        [cheerful, angry, whisper],
        artifacts,
        tmp_path / "unused-voices",
    )

    assert len(runtime.design_calls) == 1
    assert _reference_paths(artifacts)[0].is_file()
    adapter.generate(cheerful, _take_context(adapter), tmp_path / "one.wav")
    adapter.generate(angry, _take_context(adapter), tmp_path / "two.wav")
    adapter.generate(whisper, _take_context(adapter), tmp_path / "three.wav")
    assert len(runtime.prompt_calls) == 1
    assert runtime.prompt_calls[0]["ref_text"] == REFERENCE_TEXT
    assert all(
        call["prompt"] is runtime.clone_calls[0]["prompt"]
        for call in runtime.clone_calls
    )


def test_explicit_reference_skips_voice_design_and_uses_metadata_transcript(
    tmp_path: Path,
) -> None:
    voices_dir, entry = _voices_dir(tmp_path)
    job = _job(reference_voice="lux-emotion-76")
    runtime = FakeRuntime(tmp_path)
    adapter = Qwen3TTSAdapter(runtime=runtime)
    adapter.prepare([job], tmp_path / "artifacts", voices_dir)

    assert runtime.snapshots == []
    assert runtime.loaded == []
    assert runtime.design_calls == []
    assert runtime.released == []
    receipt = adapter.generation_input(job, _take_context(adapter))
    assert receipt["reference_control"] == "voice_asset"
    assert receipt["reference_source_id"] == "lux-emotion-76"
    assert receipt["reference_sha256"] == entry["sha256"]
    assert receipt["reference_text"] == entry["transcript"]

    output = tmp_path / "explicit.wav"
    realized = adapter.generate(job, _take_context(adapter), output)
    assert [(repo, revision) for repo, revision, _ in runtime.snapshots] == [
        (BASE_MODEL_ID, BASE_REVISION),
    ]
    assert [repo for repo, _ in runtime.loaded] == [BASE_MODEL_ID]
    assert runtime.design_calls == []
    assert runtime.prompt_calls == [
        {
            "model": BASE_MODEL_ID,
            "ref_audio": str(voices_dir / entry["file"]),
            "ref_text": entry["transcript"],
        },
    ]
    assert set(realized["phase_peak_vram_mib"]) == {
        "base_load",
        "voice_clone_prompt_create",
        "voice_clone_generate",
    }
    assert realized["reference_control"] == "voice_asset"
    assert realized["reference_source_id"] == "lux-emotion-76"
    assert realized["reference_sha256"] == entry["sha256"]
    assert realized["reference_text"] == entry["transcript"]
    assert realized["character_identity"] == receipt["character_identity"]


def test_explicit_reference_hash_mismatch_fails_fast(tmp_path: Path) -> None:
    voices_dir, entry = _voices_dir(tmp_path)
    (voices_dir / entry["file"]).write_bytes(b"tampered")
    with pytest.raises(Qwen3TTSAdapterError, match="WAV SHA-256"):
        Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path)).prepare(
            [_job(reference_voice="lux-emotion-76")],
            tmp_path / "artifacts",
            voices_dir,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("language", "en"), ("transcript", "")],
)
def test_explicit_reference_language_and_transcript_are_strict(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    voices_dir, _ = _voices_dir(tmp_path)
    metadata_path = voices_dir / "metadata.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    entry = next(
        item for item in metadata["voices"] if item["id"] == "lux-emotion-76"
    )
    entry[field] = value
    metadata_path.write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(Qwen3TTSAdapterError, match="metadata"):
        Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path)).prepare(
            [_job(reference_voice="lux-emotion-76")],
            tmp_path / "artifacts",
            voices_dir,
        )


def test_old_emotion_bank_cache_is_not_reused(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    old_wav, old_metadata = _old_reference_paths(artifacts)
    old_wav.parent.mkdir(parents=True)
    old_wav.write_bytes(b"old-emotion-bank")
    old_metadata.write_text("{broken-old-cache", encoding="utf-8")

    runtime = FakeRuntime(tmp_path)
    Qwen3TTSAdapter(runtime=runtime).prepare(
        [_job()],
        artifacts,
        tmp_path / "unused-voices",
    )
    assert len(runtime.design_calls) == 1
    assert _reference_paths(artifacts)[0].is_file()
    assert old_wav.read_bytes() == b"old-emotion-bank"
    assert old_metadata.read_text(encoding="utf-8") == "{broken-old-cache"


def test_new_character_anchor_cache_corruption_fails_fast(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    job = _job()
    Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path)).prepare(
        [job],
        artifacts,
        tmp_path / "unused-voices",
    )
    wav_path, metadata_path = _reference_paths(artifacts)

    wav_path.write_bytes(wav_path.read_bytes() + b"tampered")
    with pytest.raises(Qwen3TTSAdapterError, match="WAV SHA-256"):
        Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path)).prepare(
            [job],
            artifacts,
            tmp_path / "unused-voices",
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
            tmp_path / "unused-voices",
        )


def test_models_are_staged_and_realized_receipt_matches_actual_input(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    first = _job()
    second = _job(line_id="vendor-002", text="こちらへどうぞ。")
    adapter = Qwen3TTSAdapter(runtime=runtime)
    adapter.prepare(
        [first, second],
        tmp_path / "artifacts",
        tmp_path / "unused-voices",
    )

    assert [repo for repo, _ in runtime.loaded] == [VOICE_DESIGN_MODEL_ID]
    assert runtime.released == [VOICE_DESIGN_MODEL_ID]
    recipe = adapter.take_recipe()
    first_context = recipe.single_take_context()
    second_context = TakeContext.create(
        index=2,
        seed=123_456,
        recipe_version=recipe.version,
        sampling=dict(recipe.sampling),
    )
    realized_one = adapter.generate(first, first_context, tmp_path / "one.wav")
    realized_two = adapter.generate(second, second_context, tmp_path / "two.wav")

    assert [repo for repo, _ in runtime.loaded] == [
        VOICE_DESIGN_MODEL_ID,
        BASE_MODEL_ID,
    ]
    assert runtime.loaded[1][0] not in runtime.released
    assert runtime.seeds == [SEED, SEED, 123_456]
    assert len(runtime.prompt_calls) == 1
    assert len(runtime.clone_calls) == 2
    assert realized_one["seed"] == SEED
    assert realized_two["seed"] == 123_456
    assert realized_one["sampling"] == first_context.sampling_dict()
    assert realized_one["sample_rate_hz"] == 24_000
    assert set(realized_one["phase_peak_vram_mib"]) == {
        "voice_design_load",
        "voice_design_generate",
        "base_load",
        "voice_clone_prompt_create",
        "voice_clone_generate",
    }
    for peak in realized_one["phase_peak_vram_mib"].values():
        assert set(peak) == {"allocated_mib", "reserved_mib"}
    for false_field in ("emotion", "intensity", "delivery"):
        assert false_field not in realized_one

    for output_path in (tmp_path / "one.wav", tmp_path / "two.wav"):
        with wave.open(str(output_path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 24_000


def test_clone_prompt_peak_is_profiled_separately_from_generation(
    tmp_path: Path,
) -> None:
    voices_dir, _ = _voices_dir(tmp_path)
    job = _job(reference_voice="lux-emotion-76")
    runtime = FakeRuntime(tmp_path)
    runtime.peak_values = [1_000.0, 11_000.0, 3_000.0]
    adapter = Qwen3TTSAdapter(runtime=runtime)
    adapter.prepare([job], tmp_path / "artifacts", voices_dir)

    realized = adapter.generate(job, _take_context(adapter), tmp_path / "out.wav")
    peaks = realized["phase_peak_vram_mib"]

    assert peaks["base_load"]["allocated_mib"] == 1_000.0
    assert peaks["voice_clone_prompt_create"]["allocated_mib"] == 11_000.0
    assert peaks["voice_clone_generate"]["allocated_mib"] == 3_000.0
    assert (
        peaks["voice_clone_prompt_create"]["allocated_mib"]
        > peaks["voice_clone_generate"]["allocated_mib"]
    )


def test_reprepare_releases_base_before_loading_voice_design(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    adapter = Qwen3TTSAdapter(runtime=runtime)
    first = _job()
    adapter.prepare(
        [first],
        tmp_path / "first-artifacts",
        tmp_path / "unused-voices",
    )
    adapter.generate(first, _take_context(adapter), tmp_path / "first.wav")
    assert runtime.resident_models == {BASE_MODEL_ID}

    second = _job(
        character_id="guard",
        name="門番",
        gender="male",
        age="adult",
        archetype="衛兵",
        voice="低く通る声。",
        personality="生真面目。",
    )
    adapter.prepare(
        [second],
        tmp_path / "second-artifacts",
        tmp_path / "unused-voices",
    )
    assert runtime.released == [
        VOICE_DESIGN_MODEL_ID,
        BASE_MODEL_ID,
        VOICE_DESIGN_MODEL_ID,
    ]
    assert runtime.resident_models == set()


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

    monkeypatch.setattr(qwen3_tts.metadata, "version", lambda _: QWEN_TTS_VERSION)
    fake_modules = {
        "torch": SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
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
    adapter.prepare(
        [job],
        tmp_path / "artifacts",
        tmp_path / "unused-voices",
    )
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


def test_adapter_rejects_unprepared_non_japanese_and_missing_reference_policy(
    tmp_path: Path,
) -> None:
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

    character = dict(job.character)
    del character["reference_voice"]
    missing_policy_job = LineJob(
        scene=job.scene,
        character=character,
        line=job.line,
        locale="ja",
    )
    with pytest.raises(Qwen3TTSAdapterError, match="reference_voice"):
        adapter.prepare(
            [missing_policy_job],
            tmp_path / "artifacts",
            tmp_path / "voices",
        )
