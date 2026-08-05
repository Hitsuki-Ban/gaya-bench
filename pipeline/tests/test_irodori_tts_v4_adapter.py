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

from gaya_pipeline.adapters.base import LineJob, TakeContext
from gaya_pipeline.adapters.irodori_tts import (
    IrodoriTTSAdapter,
)
from gaya_pipeline.adapters.irodori_tts import (
    MODEL_ID as V3_MODEL_ID,
)
from gaya_pipeline.adapters.irodori_tts_v4 import (
    CHECKPOINT_REVISION,
    MAX_REF_SECONDS,
    MODEL_ID,
    PROFILE_VERSION,
    REFERENCE_CONTROL,
    ROLE_ANCHOR_TEXT,
    UPSTREAM_REVISION,
    IrodoriTTSV4Adapter,
    IrodoriTTSV4AdapterError,
    build_role_anchor_caption,
)
from gaya_pipeline.completion_plan import build_role_snapshot
from gaya_pipeline.take_identity import canonical_json, generation_input_sha256

PLAN_SHA256 = "b" * 64


class FakeRuntime:
    def __init__(self) -> None:
        self.prepare_count = 0
        self.synthesize_calls: list[dict[str, Any]] = []

    def prepare(self) -> dict[str, float]:
        self.prepare_count += 1
        return {"allocated_mib": 3000.0, "reserved_mib": 3300.0}

    def synthesize(
        self,
        *,
        text: str,
        caption: str,
        reference_wav: Path | None,
        output_wav: Path,
        seed: int,
    ) -> dict[str, Any]:
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
                    "allocated_mib": 3500.0,
                    "reserved_mib": 3800.0,
                },
            },
            "seed": seed,
            "sample_rate_hz": 48_000,
            "silentcipher_watermark_stage_executed": True,
        }

    def is_out_of_memory(self, error: BaseException) -> bool:
        del error
        return False


def _job(
    *,
    gender: str = "male",
    age: str = "adult",
    reference_voice: str | None = None,
) -> LineJob:
    return LineJob(
        scene={"id": "tavern-night", "setting": "夜の酒場。"},
        character={
            "id": "guard",
            "name": "衛兵",
            "kind": "human",
            "gender": gender,
            "age": age,
            "archetype": "衛兵",
            "voice": "落ち着いてよく通る声。",
            "personality": "実直。",
            "reference_voice": reference_voice,
        },
        line={
            "id": "guard-001",
            "text": "異常なし。",
            "reading": "イジョウナシ",
            "emotion": "neutral",
            "intensity": 1,
            "delivery": "短く報告する。",
        },
        locale="ja",
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
        int(3000 * math.sin(2 * math.pi * 110 * index / sample_rate))
        for index in range(sample_rate // 10)
    ]
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))


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
    (voices / "metadata.schema.json").write_text(json.dumps(schema), encoding="utf-8")
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


def _selection(tmp_path: Path, job: LineJob) -> Path:
    role = _role(job)
    root = tmp_path / "selection"
    anchor_id = "a" * 64
    audio = root / "audio" / f"{anchor_id}.wav"
    _write_wave(audio)
    audio_sha = hashlib.sha256(audio.read_bytes()).hexdigest()
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
        "no_usable_candidate": False,
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
    decision_sha = hashlib.sha256(canonical_json(decision).encode("utf-8")).hexdigest()
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
        "plan_sha256": PLAN_SHA256,
        "candidate_set_sha256": "c" * 64,
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


def _take(adapter: IrodoriTTSV4Adapter, seed: int = 17) -> TakeContext:
    recipe = adapter.take_recipe()
    return TakeContext.create(
        index=1,
        seed=seed,
        recipe_version=recipe.version,
        sampling=dict(recipe.sampling),
    )


def test_profile_and_params_are_independent_from_v3() -> None:
    adapter = IrodoriTTSV4Adapter(runtime=FakeRuntime())
    params = adapter.generation_params()

    assert adapter.profile.id == MODEL_ID
    assert adapter.profile.version == PROFILE_VERSION
    assert adapter.profile.capabilities.as_dict() == {
        "emotion": True,
        "voice_prompt": True,
        "clone": True,
        "nonverbal": True,
        "reading": False,
    }
    assert params["upstream_revision"] == UPSTREAM_REVISION
    assert params["checkpoint_revision"] == CHECKPOINT_REVISION
    assert params["tokenizer"] == "bundled"
    assert params["reference"]["max_seconds"] == MAX_REF_SECONDS
    assert params["role_reference"]["anchor_caption_policy"] == (
        "short-gender-age-voice-acoustics-v2"
    )
    assert "cache_directory" not in params["role_reference"]
    assert "600m-v3" not in PROFILE_VERSION.lower()


def test_role_anchor_caption_is_short_acoustic_only() -> None:
    identity = dict(_job(gender="male", age="young_adult").character)
    identity["voice"] = "低く落ち着いた男性の声。"
    caption = build_role_anchor_caption(identity)

    assert caption == "若い成人の男性。低く落ち着いた男性の声。"
    for excluded in ("衛兵", "実直", "夜の酒場", "演技", "感情"):
        assert excluded not in caption


def test_role_anchor_receipt_contains_v4_identity_and_realized_conditioning(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    adapter = IrodoriTTSV4Adapter(runtime=runtime)
    role = _role(_job())
    generation_input = adapter.role_anchor_generation_input(role)

    assert generation_input["model"] == MODEL_ID
    assert generation_input["model_revision"] == PROFILE_VERSION
    assert generation_input["sampling"]["max_ref_seconds"] == MAX_REF_SECONDS
    assert generation_input["reference_wav"] is None

    receipt = adapter.generate_role_anchor(
        role,
        seed=73,
        output_wav=tmp_path / "anchor.wav",
    )
    assert runtime.prepare_count == 1
    assert runtime.synthesize_calls[0]["caption"] == generation_input["caption"]
    assert receipt["model"] == MODEL_ID
    assert receipt["model_revision"] == PROFILE_VERSION
    assert receipt["caption"] == generation_input["caption"]
    assert receipt["silentcipher_watermark_stage_executed"] is True
    assert set(receipt["phase_peak_vram_mib"]) == {
        "role_anchor_runtime_load",
        "role_anchor_generate",
    }


def test_null_referenceはanchor_selectionなしでfail_fastする(tmp_path: Path) -> None:
    adapter = IrodoriTTSV4Adapter(runtime=FakeRuntime())

    with pytest.raises(IrodoriTTSV4AdapterError, match="role anchor selection"):
        adapter.prepare([_job()], tmp_path / "artifacts", tmp_path / "voices")


def test_v3のanchor_selectionはv4のprepareで拒否される(tmp_path: Path) -> None:
    job = _job()
    selection = _selection(tmp_path, job)
    document = json.loads(selection.read_bytes().decode("utf-8"))
    document["groups"][0]["model"] = V3_MODEL_ID
    raw = canonical_json(document).encode("utf-8")
    selection.write_bytes(raw)
    selection.with_suffix(".sha256").write_bytes(
        f"{hashlib.sha256(raw).hexdigest()}\n".encode("ascii"),
    )
    adapter = IrodoriTTSV4Adapter(
        runtime=FakeRuntime(),
        role_anchor_selection_path=selection,
        role_anchor_plan_sha256=PLAN_SHA256,
    )

    with pytest.raises(IrodoriTTSV4AdapterError, match="selected role anchor"):
        adapter.prepare([job], tmp_path / "artifacts", tmp_path / "voices")


def test_selected_anchorとfull_role_captionを全台詞へ渡す(tmp_path: Path) -> None:
    job = _job()
    selection = _selection(tmp_path, job)
    runtime = FakeRuntime()
    adapter = IrodoriTTSV4Adapter(
        runtime=runtime,
        role_anchor_selection_path=selection,
        role_anchor_plan_sha256=PLAN_SHA256,
    )
    adapter.prepare([job], tmp_path / "artifacts", tmp_path / "voices")

    resolved = adapter.generation_input(job, _take(adapter))
    assert resolved["reference_control"] == REFERENCE_CONTROL
    assert resolved["reference_source"] == "selected-role-anchor"
    assert resolved["reference_voice"] is None
    assert resolved["reference_text"] == ROLE_ANCHOR_TEXT
    assert resolved["selected_anchor"]["anchor_seed"] == 456
    # 全 role caption: 名前・性別・年齢・役柄・声質を欠落させない。
    for required in ("衛兵", "男性", "成人", "落ち着いてよく通る声。"):
        assert required in resolved["caption"]
    # reading は非対応。原文をそのまま渡す。
    assert resolved["reading_source"] == "line.text"
    assert "イジョウナシ" not in resolved["text"]

    realized = adapter.generate(job, _take(adapter), tmp_path / "out.wav")
    assert realized["reference_source"] == "selected-role-anchor"
    assert realized["reference_sha256"] == resolved["reference_sha256"]
    assert realized["selected_anchor"] == resolved["selected_anchor"]
    assert set(realized["phase_peak_vram_mib"]) == {"runtime_load", "generation"}
    assert runtime.synthesize_calls[0]["reference_wav"].is_file()


def test_明示referenceはanchor_selectionより優先される(tmp_path: Path) -> None:
    voices = _voices_dir(tmp_path)
    job = _job(reference_voice="test-voice")
    adapter = IrodoriTTSV4Adapter(runtime=FakeRuntime())
    adapter.prepare([job], tmp_path / "artifacts", voices)

    resolved = adapter.generation_input(job, _take(adapter))
    assert resolved["reference_source"] == "voice-asset"
    assert resolved["reference_voice"] == "test-voice"
    assert resolved["reference_caption"] is None
    assert resolved["reference_text"] is None
    assert "selected_anchor" not in resolved


def test_同じ台詞でもv3とcache_identityが分離される(tmp_path: Path) -> None:
    voices = _voices_dir(tmp_path)
    job = _job(reference_voice="test-voice")

    identities = []
    for adapter in (
        IrodoriTTSAdapter(runtime=FakeRuntime()),
        IrodoriTTSV4Adapter(runtime=FakeRuntime()),
    ):
        adapter.prepare([job], tmp_path / "artifacts", voices)
        context = _take(adapter)  # type: ignore[arg-type]
        identities.append(
            generation_input_sha256(
                model_id=adapter.profile.id,
                model_version=adapter.profile.version,
                resolved_input=adapter.generation_input(job, context),
                take_context=context,
                generation_params=adapter.generation_params(),
                postprocess={},
            ),
        )

    assert identities[0] != identities[1]


def test_v4は120秒の連結referenceを条件として宣言する() -> None:
    adapter = IrodoriTTSV4Adapter(runtime=FakeRuntime())
    params = adapter.generation_params()

    assert MAX_REF_SECONDS == 120.0
    assert params["max_reference_seconds"] == MAX_REF_SECONDS
    assert params["reference"]["max_seconds"] == MAX_REF_SECONDS
    assert params["role_reference"]["anchor_sampling"]["max_ref_seconds"] == (
        MAX_REF_SECONDS
    )
    assert params["role_reference"]["explicit_reference_precedence"] is True
    assert params["role_reference"]["selection_required_for_null_reference"] is True


def _line(line_id: str) -> dict[str, Any]:
    return {
        "id": line_id,
        "text": "異常なし。",
        "reading": "イジョウナシ",
        "emotion": "neutral",
        "intensity": 1,
        "delivery": "短く報告する。",
    }


def test_anchor参照のcache鍵空間はroleでありlineやtakeではない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #194: 161 line x 4 take の run で anchor 参照が line 単位/take 単位に
    # 積み上がると host memory が枯渇する。解決も保持も role 単位で bound
    # されていることを固定する。
    base = _job()
    jobs = [
        LineJob(
            scene=base.scene,
            character=base.character,
            line=_line(f"guard-{index:03d}"),
            locale="ja",
        )
        for index in range(1, 8)
    ]
    selection = _selection(tmp_path, base)
    adapter = IrodoriTTSV4Adapter(
        runtime=FakeRuntime(),
        role_anchor_selection_path=selection,
        role_anchor_plan_sha256=PLAN_SHA256,
    )

    import gaya_pipeline.increment_anchor as increment_anchor

    resolved_roles: list[tuple[str, str]] = []
    real_resolve = increment_anchor.resolve_increment_anchor

    def counting_resolve(**kwargs: Any) -> Any:
        role = kwargs["role"]
        resolved_roles.append((role.scenario, role.character))
        return real_resolve(**kwargs)

    monkeypatch.setattr(
        increment_anchor,
        "resolve_increment_anchor",
        counting_resolve,
    )
    adapter.prepare(jobs, tmp_path / "artifacts", tmp_path / "voices")

    # 7 line / 1 role -> anchor 解決は role ごとに1回だけ。
    assert len(jobs) == 7
    assert resolved_roles == [("tavern-night", "guard")]

    prepared = [adapter._prepared_input(job) for job in jobs]
    assert len(prepared) == len(jobs)
    # reference 実体 (wav path / sha / anchor receipt) は role ごとに1つを共有し、
    # line 数・take 数に比例して増えない。
    assert len({id(item.selected_anchor_receipt) for item in prepared}) == 1
    assert len({item.reference_wav for item in prepared}) == 1
    assert len({item.reference_sha256 for item in prepared}) == 1


class _RecordingCuda:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    def synchronize(self, _device: int) -> None:
        return

    def reset_peak_memory_stats(self, _device: int) -> None:
        self._log.append("reset_peak")

    def empty_cache(self) -> None:
        self._log.append("empty_cache")

    def max_memory_allocated(self, _device: int) -> int:
        return 1024 * 1024

    def max_memory_reserved(self, _device: int) -> int:
        return 2 * 1024 * 1024


class _FakeTensor:
    ndim = 1

    def detach(self) -> _FakeTensor:
        return self

    def float(self) -> _FakeTensor:
        return self

    def cpu(self) -> _FakeTensor:
        return self

    def squeeze(self) -> _FakeTensor:
        return self

    def numel(self) -> int:
        return 4800

    def numpy(self) -> _FakeTensor:
        return self


def _native_runtime_stub(module: Any, log: list[str]) -> Any:
    from types import SimpleNamespace

    runtime = object.__new__(module._NativeRuntime)
    runtime.torch = SimpleNamespace(cuda=_RecordingCuda(log))
    runtime.inference_runtime = SimpleNamespace(
        SamplingRequest=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    runtime.soundfile = SimpleNamespace(
        write=lambda path, *_a, **_k: _write_wave(Path(path)),
    )

    def synthesize(_request: Any, *, log_fn: Any = None) -> Any:
        del log_fn
        log.append("synthesize")
        return SimpleNamespace(
            used_seed=17,
            sample_rate=48_000,
            audio=_FakeTensor(),
            stage_timings=(("silentcipher_watermark", 0.5),),
            total_to_decode=1.0,
        )

    runtime._runtime = SimpleNamespace(
        watermarker=SimpleNamespace(ready=True),
        synthesize=synthesize,
    )
    return runtime


def test_v4のsynthesizeはtakeごとにcaching_allocator_poolを畳む(
    tmp_path: Path,
) -> None:
    from gaya_pipeline.adapters import irodori_tts_v4 as v4_module

    log: list[str] = []
    runtime = _native_runtime_stub(v4_module, log)

    for index in range(3):
        runtime.synthesize(
            text="異常なし。",
            caption="成人の男性。",
            reference_wav=None,
            output_wav=tmp_path / f"out-{index}.wav",
            seed=17,
        )

    # take ごとに1回、かつ peak 計測の reset より前に解放する。
    assert log.count("empty_cache") == 3
    assert log[:3] == ["empty_cache", "reset_peak", "synthesize"]


def test_v3のsynthesizeは公開baselineのままpoolを畳まない(tmp_path: Path) -> None:
    from gaya_pipeline.adapters import irodori_tts as v3_module

    log: list[str] = []
    runtime = _native_runtime_stub(v3_module, log)
    runtime.synthesize(
        text="異常なし。",
        caption="成人の男性。",
        reference_wav=None,
        output_wav=tmp_path / "v3.wav",
        seed=17,
    )

    assert "empty_cache" not in log
    assert log[:2] == ["reset_peak", "synthesize"]


def test_v4はanchor_modelとして生成経路に登録されている() -> None:
    from gaya_pipeline.generation import ANCHOR_MODELS as GENERATION_ANCHOR_MODELS
    from gaya_pipeline.take_ledger import ANCHOR_MODELS as LEDGER_ANCHOR_MODELS

    assert MODEL_ID in GENERATION_ANCHOR_MODELS
    assert MODEL_ID in LEDGER_ANCHOR_MODELS
