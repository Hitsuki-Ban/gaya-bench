from __future__ import annotations

import hashlib
import json
import math
import struct
import wave
from pathlib import Path
from typing import Any

import pytest
import yaml

from gaya_pipeline.adapters import irodori_tts
from gaya_pipeline.adapters.base import LineJob, TakeContext
from gaya_pipeline.adapters.irodori_tts import (
    CHECKPOINT_REVISION,
    CODEC_REVISION,
    MODEL_ID,
    PROFILE_VERSION,
    REFERENCE_CONTROL,
    ROLE_ANCHOR_TEXT,
    UPSTREAM_REVISION,
    IrodoriTTSAdapter,
    IrodoriTTSAdapterError,
)
from gaya_pipeline.completion_plan import build_role_snapshot
from gaya_pipeline.take_identity import canonical_json


PLAN_SHA256 = "b" * 64


def test_native_runtimeはload不能なtorchcodecをmodel読込前に拒否(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(irodori_tts.sys, "platform", "win32")
    monkeypatch.setattr(
        irodori_tts,
        "_require_distribution",
        lambda *_args, **_kwargs: None,
    )

    def reject_torchcodec(module_name: str) -> Any:
        assert module_name == "torchcodec"
        raise RuntimeError("FFmpeg shared libraries are unavailable")

    monkeypatch.setattr(irodori_tts.importlib, "import_module", reject_torchcodec)

    with pytest.raises(
        IrodoriTTSAdapterError,
        match=r"TorchCodec.*FFmpeg full-shared",
    ):
        IrodoriTTSAdapter()


class FakeOutOfMemoryError(RuntimeError):
    pass


class FakeRuntime:
    def __init__(self) -> None:
        self.prepare_count = 0
        self.synthesize_calls: list[dict[str, Any]] = []
        self.oom_on: str | None = None
        self.watermark_executed = True

    def prepare(self) -> dict[str, float]:
        if self.oom_on == "prepare":
            raise FakeOutOfMemoryError("prepare")
        self.prepare_count += 1
        return {"allocated_mib": 2048.0, "reserved_mib": 2304.0}

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
            raise FakeOutOfMemoryError("synthesize")
        self.synthesize_calls.append(
            {
                "text": text,
                "caption": caption,
                "reference_wav": reference_wav,
                "output_wav": output_wav,
                "seed": seed,
            },
        )
        _write_wave(output_wav)
        return {
            "phase_peak_vram_mib": {
                "generation": {
                    "allocated_mib": 3072.0,
                    "reserved_mib": 3328.0,
                },
            },
            "seed": seed,
            "sample_rate_hz": 48_000,
            "silentcipher_watermark_stage_executed": self.watermark_executed,
        }

    def is_out_of_memory(self, error: BaseException) -> bool:
        return isinstance(error, FakeOutOfMemoryError)


def _job(
    *,
    line_id: str = "barmaid-001",
    text: str = "乾杯しよう！",
    reading: str | None = None,
    emotion: str = "laughing",
    reference_voice: str | None = None,
    locale: str = "ja",
) -> LineJob:
    return LineJob(
        scene={"id": "tavern-night", "setting": "夜の酒場。"},
        character={
            "id": "barmaid",
            "name": "給仕の女性",
            "kind": "human",
            "gender": "female",
            "age": "young_adult",
            "archetype": "給仕",
            "voice": "明るく通る若い女性の声。",
            "personality": "気さくで世話焼き。",
            "reference_voice": reference_voice,
        },
        line={
            "id": line_id,
            "text": text,
            "reading": reading,
            "emotion": emotion,
            "intensity": 2,
            "delivery": "笑いを含ませ、弾む調子で話す。",
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
    sample_rate = 48_000
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
    anchor_id = "a" * 64
    audio = root / "audio" / f"{anchor_id}.wav"
    _write_wave(audio)
    audio_sha = hashlib.sha256(audio.read_bytes()).hexdigest()
    plan_sha = PLAN_SHA256
    candidate_sha = "c" * 64
    review_epoch = "d" * 64
    decision = {
        "id": "e" * 64,
        "model": MODEL_ID,
        "scenario": role.scenario,
        "character": role.character,
        "line": None,
        "role_epoch_sha256": review_epoch,
        "group_sha256": "f" * 64,
        "heard_candidate_ids": [anchor_id, "1" * 64],
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
                "attempt": 3,
                "seed": 456,
                "audio_path": f"audio/{anchor_id}.wav",
                "audio_sha256": audio_sha,
                "anchor_text": ROLE_ANCHOR_TEXT,
                "anchor_text_sha256": hashlib.sha256(
                    ROLE_ANCHOR_TEXT.encode("utf-8"),
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


def _take(adapter: IrodoriTTSAdapter, seed: int = 17) -> TakeContext:
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
    wav = voices / "test-voice" / "reference.wav"
    _write_wave(wav)
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
                        "id": {"type": "string"},
                        "file": {"type": "string"},
                        "sha256": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": False,
    }
    (voices / "metadata.schema.json").write_text(
        json.dumps(schema),
        encoding="utf-8",
    )
    (voices / "metadata.yaml").write_text(
        yaml.safe_dump(
            {
                "format_version": 1,
                "voices": [
                    {
                        "id": "test-voice",
                        "file": "test-voice/reference.wav",
                        "sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return voices


def test_profile_and_params_use_selected_anchor_contract() -> None:
    adapter = IrodoriTTSAdapter(
        runtime=FakeRuntime(),
        reading_converter=lambda text: text,
    )
    params = adapter.generation_params()

    assert adapter.profile.version == PROFILE_VERSION
    assert UPSTREAM_REVISION in PROFILE_VERSION
    assert CHECKPOINT_REVISION in PROFILE_VERSION
    assert CODEC_REVISION in PROFILE_VERSION
    assert params["role_reference"]["selection_protocol"] == (
        "role-anchor-selection-v1"
    )
    assert params["role_reference"]["selection_required_for_null_reference"] is True
    assert "cache_directory" not in params["role_reference"]


def test_Phase_A_caption_is_role_complete_and_seeded(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    adapter = IrodoriTTSAdapter(runtime=runtime, reading_converter=lambda text: text)
    role = _role(_job())
    generation_input = adapter.role_anchor_generation_input(role)

    assert generation_input["text"] == ROLE_ANCHOR_TEXT
    caption = str(generation_input["caption"])
    for value in role.role.values():
        assert value in caption
    assert "感情:" not in caption
    assert "場面:" not in caption

    output = tmp_path / "anchor.wav"
    realized = adapter.generate_role_anchor(role, seed=998, output_wav=output)
    assert runtime.prepare_count == 1
    assert runtime.synthesize_calls[0]["seed"] == 998
    assert runtime.synthesize_calls[0]["reference_wav"] is None
    assert realized["seed"] == 998
    assert output.is_file()


def test_null_reference_requires_selected_anchor(tmp_path: Path) -> None:
    adapter = IrodoriTTSAdapter(
        runtime=FakeRuntime(),
        reading_converter=lambda text: text,
    )

    with pytest.raises(IrodoriTTSAdapterError, match="selection"):
        adapter.prepare([_job()], tmp_path / "artifacts", tmp_path / "voices")


def test_Phase_B_passes_selected_ref_and_full_target_caption(tmp_path: Path) -> None:
    first = _job()
    second = _job(line_id="barmaid-002", text="もう一杯どう？")
    runtime = FakeRuntime()
    adapter = IrodoriTTSAdapter(
        runtime=runtime,
        reading_converter=lambda text: text,
        role_anchor_selection_path=_selection(tmp_path, first),
        role_anchor_plan_sha256=PLAN_SHA256,
    )
    adapter.prepare(
        [first, second],
        tmp_path / "unused-artifacts",
        tmp_path / "unused-voices",
    )
    generation_input = adapter.generation_input(first, _take(adapter))
    assert generation_input["reference_control"] == REFERENCE_CONTROL
    assert generation_input["reference_source"] == "selected-role-anchor"
    assert generation_input["selected_anchor"]["anchor_id"] == "a" * 64
    assert "役柄: 給仕" in generation_input["caption"]
    assert "場面: 夜の酒場。" in generation_input["caption"]
    assert "感情:" in generation_input["caption"]
    assert "演技:" in generation_input["caption"]

    realized = adapter.generate(first, _take(adapter, 21), tmp_path / "target.wav")
    call = runtime.synthesize_calls[0]
    assert call["reference_wav"].is_file()
    assert call["caption"] == generation_input["caption"]
    assert realized["selected_anchor"]["role_epoch_sha256"]


def test_selected_anchor_WAVのprepare後変更をruntime消費前に拒否(
    tmp_path: Path,
) -> None:
    job = _job()
    selection = _selection(tmp_path, job)
    runtime = FakeRuntime()
    adapter = IrodoriTTSAdapter(
        runtime=runtime,
        reading_converter=lambda text: text,
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

    with pytest.raises(IrodoriTTSAdapterError, match="prepare後"):
        adapter.generate(job, _take(adapter), tmp_path / "target.wav")
    assert runtime.synthesize_calls == []
    assert not (tmp_path / "target.wav").exists()


def test_explicit_reference_needs_no_anchor_selection(tmp_path: Path) -> None:
    voices = _voices_dir(tmp_path)
    runtime = FakeRuntime()
    adapter = IrodoriTTSAdapter(
        runtime=runtime,
        reading_converter=lambda text: text,
    )
    job = _job(reference_voice="test-voice")
    adapter.prepare([job], tmp_path / "artifacts", voices)
    generation_input = adapter.generation_input(job, _take(adapter))

    assert generation_input["reference_source"] == "voice-asset"
    assert "selected_anchor" not in generation_input
    adapter.generate(job, _take(adapter), tmp_path / "target.wav")
    assert runtime.synthesize_calls[0]["reference_wav"].is_file()


def test_explicit_reading_remains_model_input(tmp_path: Path) -> None:
    job = _job(reading="カンパイシヨウ")
    adapter = IrodoriTTSAdapter(
        runtime=FakeRuntime(),
        reading_converter=lambda _text: pytest.fail("converter must not run"),
        role_anchor_selection_path=_selection(tmp_path, job),
        role_anchor_plan_sha256=PLAN_SHA256,
    )
    adapter.prepare(
        [job],
        tmp_path / "unused-artifacts",
        tmp_path / "unused-voices",
    )

    generation_input = adapter.generation_input(job, _take(adapter))
    assert "カンパイシヨウ" in generation_input["text"]
    assert generation_input["reading_source"] == "line.reading"


def test_selection_path_and_watermark_fail_fast(tmp_path: Path) -> None:
    with pytest.raises(IrodoriTTSAdapterError, match="絶対path"):
        IrodoriTTSAdapter(
            runtime=FakeRuntime(),
            role_anchor_selection_path=Path("relative.json"),
            role_anchor_plan_sha256=PLAN_SHA256,
        )
    runtime = FakeRuntime()
    runtime.watermark_executed = False
    adapter = IrodoriTTSAdapter(runtime=runtime)
    with pytest.raises(IrodoriTTSAdapterError, match="watermark"):
        adapter.generate_role_anchor(
            _role(_job()),
            seed=1,
            output_wav=tmp_path / "no-watermark.wav",
        )


def test_language_and_oom_fail_fast(tmp_path: Path) -> None:
    adapter = IrodoriTTSAdapter(runtime=FakeRuntime())
    with pytest.raises(IrodoriTTSAdapterError, match="Japanese"):
        adapter.prepare(
            [_job(locale="en")],
            tmp_path / "artifacts",
            tmp_path / "voices",
        )
    runtime = FakeRuntime()
    runtime.oom_on = "synthesize"
    with pytest.raises(IrodoriTTSAdapterError, match="out of memory"):
        IrodoriTTSAdapter(runtime=runtime).generate_role_anchor(
            _role(_job()),
            seed=3,
            output_wav=tmp_path / "oom.wav",
        )
