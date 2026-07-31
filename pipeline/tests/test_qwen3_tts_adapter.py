from __future__ import annotations

import hashlib
import math
import struct
import wave
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from gaya_pipeline.adapters.base import LineJob, TakeContext
from gaya_pipeline.adapters.qwen3_tts import (
    BASE_MODEL_ID,
    BASE_REVISION,
    MODEL_ID,
    PROFILE_VERSION,
    REFERENCE_TEXT,
    VOICE_DESIGN_MODEL_ID,
    VOICE_DESIGN_REVISION,
    Qwen3TTSAdapter,
    Qwen3TTSAdapterError,
)
from gaya_pipeline.completion_plan import build_role_snapshot
from gaya_pipeline.take_identity import canonical_json


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLAN_SHA256 = "2" * 64


class FakeOutOfMemoryError(RuntimeError):
    pass


class FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.snapshot_repos: dict[Path, str] = {}
        self.loaded: list[str] = []
        self.design_calls: list[dict[str, Any]] = []
        self.prompt_calls: list[dict[str, Any]] = []
        self.clone_calls: list[dict[str, Any]] = []
        self.seeds: list[int] = []
        self.release_count = 0
        self.oom_on: str | None = None

    def snapshot_download(self, repo_id: str, revision: str) -> Path:
        path = self.root / "snapshots" / repo_id.rsplit("/", 1)[-1] / revision
        path.mkdir(parents=True, exist_ok=True)
        self.snapshot_repos[path] = repo_id
        return path

    def load_model(self, snapshot_path: Path) -> dict[str, str]:
        repo = self.snapshot_repos[snapshot_path]
        if self.oom_on == "load":
            raise FakeOutOfMemoryError("load")
        self.loaded.append(repo)
        return {"repo_id": repo}

    def generate_voice_design(
        self,
        model: dict[str, str],
        *,
        text: str,
        language: str,
        instruct: str,
        sampling: Mapping[str, int | float | bool],
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
        return ([[0.0, 0.25, -0.25, 0.0]], 24_000)

    def create_voice_clone_prompt(
        self,
        model: dict[str, str],
        *,
        ref_audio: str,
        ref_text: str,
    ) -> dict[str, int]:
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
        voice_clone_prompt: Any,
        sampling: Mapping[str, int | float | bool],
    ) -> tuple[list[list[float]], int]:
        self.clone_calls.append(
            {
                "model": model["repo_id"],
                "text": text,
                "language": language,
                "prompt": voice_clone_prompt,
                "sampling": dict(sampling),
            },
        )
        return ([[0.0, 0.2, -0.2, 0.0]], 24_000)

    def write_pcm16(
        self,
        path: Path,
        samples: Any,
        sample_rate: int,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        values = list(samples)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(
                b"".join(
                    struct.pack(
                        "<h",
                        round(max(-1.0, min(1.0, float(value))) * 32767),
                    )
                    for value in values
                ),
            )

    def seed(self, seed: int) -> None:
        self.seeds.append(seed)

    def reset_peak_memory_stats(self) -> None:
        return

    def peak_memory_mib(self) -> dict[str, float]:
        return {"allocated_mib": 1024.0, "reserved_mib": 1280.0}

    def release_model(self) -> None:
        self.release_count += 1

    def is_out_of_memory(self, error: BaseException) -> bool:
        return isinstance(error, FakeOutOfMemoryError)


def _job(
    *,
    line_id: str = "vendor-001",
    text: str = "いらっしゃい！",
    reference_voice: str | None = None,
    locale: str = "ja",
) -> LineJob:
    return LineJob(
        scene={"id": "market-day", "setting": "港町の朝市。人通りが多い。"},
        character={
            "id": "vendor",
            "name": "受付嬢",
            "kind": "human",
            "gender": "female",
            "age": "young_adult",
            "archetype": "受付",
            "voice": "明るく張りのある声。",
            "personality": "親切で事務的。",
            "reference_voice": reference_voice,
        },
        line={
            "id": line_id,
            "text": text,
            "emotion": "cheerful",
            "intensity": 2,
            "delivery": "明るく呼びかける。",
        },
        locale=locale,
    )


def _role(job: LineJob):
    return build_role_snapshot(
        scenario=job.scenario_id,
        character=str(job.character["id"]),
        character_document=job.character,
        scene_setting=str(job.scene["setting"]),
    )


def _write_wave(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 8_000
    samples = [
        int(3_000 * math.sin(2 * math.pi * 220 * index / sample_rate))
        for index in range(sample_rate // 2)
    ]
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _selection(tmp_path: Path, job: LineJob) -> Path:
    role = _role(job)
    root = tmp_path / "selection"
    anchor_id = "1" * 64
    audio = root / "audio" / f"{anchor_id}.wav"
    _write_wave(audio)
    audio_sha = hashlib.sha256(audio.read_bytes()).hexdigest()
    plan_sha = PLAN_SHA256
    candidate_sha = "3" * 64
    review_epoch = "4" * 64
    decision = {
        "id": "5" * 64,
        "model": MODEL_ID,
        "scenario": role.scenario,
        "character": role.character,
        "line": None,
        "role_epoch_sha256": review_epoch,
        "group_sha256": "6" * 64,
        "heard_candidate_ids": [anchor_id, "7" * 64],
        "selected_candidate_id": anchor_id,
        "rubric": {
            "content": "pass",
            "prompt_leakage": "pass",
            "reading": "not_applicable",
            "pitch_accent": "not_applicable",
            "gender": "pass",
            "age": "pass",
            "archetype": "pass",
            "voice_identity": "pass",
            "delivery": "not_applicable",
            "naturalness_quality": 4,
            "notes": "",
        },
        "confirmed": True,
    }
    decision_sha = hashlib.sha256(
        canonical_json(decision).encode("utf-8"),
    ).hexdigest()
    selected_epoch = hashlib.sha256(
        canonical_json(
            {
                "protocol": "selected-role-epoch-v1",
                "model": MODEL_ID,
                "model_revision": PROFILE_VERSION,
                "scenario": role.scenario,
                "character": role.character,
                "role_identity_sha256": role.role_identity_sha256,
                "review_role_epoch_sha256": review_epoch,
                "anchor_id": anchor_id,
                "audio_sha256": audio_sha,
                "decision_sha256": decision_sha,
            },
        ).encode("utf-8"),
    ).hexdigest()
    document = {
        "format_version": 1,
        "protocol": "role-anchor-selection-v1",
        "plan_sha256": plan_sha,
        "candidate_set_sha256": candidate_sha,
        "groups": [
            {
                "model": MODEL_ID,
                "model_revision": PROFILE_VERSION,
                "scenario": role.scenario,
                "character": role.character,
                "role_identity": {
                    "scenario": role.scenario,
                    "character": role.character,
                    "role": dict(role.role),
                    "reference_voice": None,
                    "scene_setting": role.scene_setting,
                },
                "role_identity_sha256": role.role_identity_sha256,
                "review_role_epoch_sha256": review_epoch,
                "role_epoch_sha256": selected_epoch,
                "anchor_id": anchor_id,
                "attempt": 2,
                "seed": 123,
                "audio_path": f"audio/{anchor_id}.wav",
                "audio_sha256": audio_sha,
                "anchor_text": REFERENCE_TEXT,
                "anchor_text_sha256": hashlib.sha256(
                    REFERENCE_TEXT.encode("utf-8"),
                ).hexdigest(),
                "decision": decision,
                "decision_sha256": decision_sha,
            },
        ],
    }
    path = (root / "role-anchor-selection-v1.json").resolve()
    raw = canonical_json(document).encode("utf-8")
    path.write_bytes(raw)
    path.with_suffix(".sha256").write_bytes(
        f"{hashlib.sha256(raw).hexdigest()}\n".encode("ascii"),
    )
    return path


def _take(adapter: Qwen3TTSAdapter, seed: int = 7) -> TakeContext:
    recipe = adapter.take_recipe()
    return TakeContext.create(
        index=1,
        seed=seed,
        recipe_version=recipe.version,
        sampling=dict(recipe.sampling),
    )


def _mutate_pcm_byte(path: Path) -> None:
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 0x01
    path.write_bytes(payload)
    with wave.open(str(path), "rb") as wav:
        assert wav.getnframes() > 0


def _voices_dir(tmp_path: Path) -> Path:
    voices = tmp_path / "voices"
    metadata = yaml.safe_load(
        (REPOSITORY_ROOT / "assets" / "voices" / "metadata.yaml").read_text(
            encoding="utf-8",
        ),
    )
    (voices / "metadata.schema.json").parent.mkdir(parents=True, exist_ok=True)
    (voices / "metadata.schema.json").write_text(
        (
            REPOSITORY_ROOT / "assets" / "voices" / "metadata.schema.json"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    entry = next(
        item for item in metadata["voices"] if item["id"] == "lux-emotion-76"
    )
    wav = voices / entry["file"]
    _write_wave(wav)
    entry["sha256"] = hashlib.sha256(wav.read_bytes()).hexdigest()
    entry["duration_sec"] = 10.0
    (voices / "metadata.yaml").write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return voices


def test_profile_and_params_use_explicit_selection_contract(tmp_path: Path) -> None:
    adapter = Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path))
    params = adapter.generation_params()

    assert adapter.profile.version == PROFILE_VERSION
    assert BASE_REVISION in PROFILE_VERSION
    assert VOICE_DESIGN_REVISION in PROFILE_VERSION
    assert params["role_anchor_selection_protocol"] == "role-anchor-selection-v1"
    assert params["role_anchor_selection_required_for_null_reference"] is True
    assert "voice_design_cache_directory" not in params


def test_Phase_A_voice_design_input_is_role_complete_and_neutral(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(tmp_path)
    adapter = Qwen3TTSAdapter(runtime=runtime)
    role = _role(_job())
    input_document = adapter.role_anchor_generation_input(role)

    assert input_document["text"] == REFERENCE_TEXT
    instruct = str(input_document["instruct"])
    for value in role.role.values():
        assert value in instruct
    assert "感情:" not in instruct
    assert "intensity" not in canonical_json(input_document)

    output = tmp_path / "anchor.wav"
    realized = adapter.generate_role_anchor(role, seed=991, output_wav=output)
    assert output.is_file()
    assert runtime.loaded == [VOICE_DESIGN_MODEL_ID]
    assert runtime.seeds == [991]
    assert realized["seed"] == 991
    adapter.close_role_anchor_generation()
    assert runtime.release_count == 1


def test_null_reference_requires_selected_anchor(tmp_path: Path) -> None:
    adapter = Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path))

    with pytest.raises(Qwen3TTSAdapterError, match="selection"):
        adapter.prepare([_job()], tmp_path / "artifacts", tmp_path / "voices")


def test_Phase_B_uses_selected_WAV_and_receipt_for_all_character_lines(
    tmp_path: Path,
) -> None:
    first = _job()
    second = _job(line_id="vendor-002", text="こちらへどうぞ。")
    selection = _selection(tmp_path, first)
    runtime = FakeRuntime(tmp_path)
    adapter = Qwen3TTSAdapter(
        runtime=runtime,
        role_anchor_selection_path=selection,
        role_anchor_plan_sha256=PLAN_SHA256,
    )
    adapter.prepare(
        [first, second],
        tmp_path / "unused-artifacts",
        tmp_path / "unused-voices",
    )

    generation_input = adapter.generation_input(first, _take(adapter))
    assert generation_input["selected_anchor"]["anchor_id"] == "1" * 64
    assert generation_input["selected_anchor"]["role_epoch_sha256"]
    assert generation_input["reference_control"] == "selected_voice_design_anchor"
    assert runtime.design_calls == []

    adapter.generate(first, _take(adapter, 11), tmp_path / "first.wav")
    adapter.generate(second, _take(adapter, 12), tmp_path / "second.wav")
    assert runtime.loaded == [BASE_MODEL_ID]
    assert len(runtime.prompt_calls) == 1
    assert runtime.prompt_calls[0]["ref_text"] == REFERENCE_TEXT
    assert runtime.prompt_calls[0]["ref_audio"].endswith(".wav")
    assert len(runtime.clone_calls) == 2


def test_selected_anchor_WAVのprepare後変更を初回消費前に拒否(
    tmp_path: Path,
) -> None:
    job = _job()
    selection = _selection(tmp_path, job)
    runtime = FakeRuntime(tmp_path)
    adapter = Qwen3TTSAdapter(
        runtime=runtime,
        role_anchor_selection_path=selection,
        role_anchor_plan_sha256=PLAN_SHA256,
    )
    adapter.prepare(
        [job],
        tmp_path / "unused-artifacts",
        tmp_path / "unused-voices",
    )
    audio = next((selection.parent / "audio").glob("*.wav"))
    _mutate_pcm_byte(audio)

    with pytest.raises(Qwen3TTSAdapterError, match="prepare後"):
        adapter.generate(job, _take(adapter), tmp_path / "target.wav")
    assert runtime.prompt_calls == []
    assert runtime.clone_calls == []
    assert not (tmp_path / "target.wav").exists()


def test_explicit_reference_needs_no_anchor_selection(tmp_path: Path) -> None:
    voices = _voices_dir(tmp_path)
    runtime = FakeRuntime(tmp_path)
    adapter = Qwen3TTSAdapter(runtime=runtime)
    job = _job(reference_voice="lux-emotion-76")
    adapter.prepare([job], tmp_path / "artifacts", voices)

    generation_input = adapter.generation_input(job, _take(adapter))
    assert generation_input["reference_control"] == "voice_asset"
    assert "selected_anchor" not in generation_input
    assert runtime.design_calls == []


def test_selection_path_must_be_absolute(tmp_path: Path) -> None:
    with pytest.raises(Qwen3TTSAdapterError, match="絶対path"):
        Qwen3TTSAdapter(
            runtime=FakeRuntime(tmp_path),
            role_anchor_selection_path=Path("relative.json"),
            role_anchor_plan_sha256=PLAN_SHA256,
        )


def test_non_japanese_and_oom_fail_fast(tmp_path: Path) -> None:
    adapter = Qwen3TTSAdapter(runtime=FakeRuntime(tmp_path))
    role = _role(_job())
    with pytest.raises(Qwen3TTSAdapterError, match="Japanese"):
        adapter.prepare(
            [_job(locale="en")],
            tmp_path / "artifacts",
            tmp_path / "voices",
        )
    runtime = FakeRuntime(tmp_path)
    runtime.oom_on = "design"
    with pytest.raises(Qwen3TTSAdapterError, match="out of memory"):
        Qwen3TTSAdapter(runtime=runtime).generate_role_anchor(
            role,
            seed=1,
            output_wav=tmp_path / "oom.wav",
        )
