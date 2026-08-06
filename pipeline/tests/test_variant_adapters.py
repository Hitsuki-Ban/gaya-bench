"""条件バリアント強制mode の adapter 配線 (#201)。

4 modelそれぞれについて、`--ref` / `--text` の両modeで
conditioning receipt が正しく分岐することを確認する。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline import adapters as adapters_package
from gaya_pipeline.adapters import create_adapter
from gaya_pipeline.adapters.base import LineJob
from gaya_pipeline.adapters.irodori_tts import IrodoriTTSAdapter
from gaya_pipeline.adapters.irodori_tts_v4 import IrodoriTTSV4Adapter
from gaya_pipeline.adapters.qwen3_tts import Qwen3TTSAdapter
from gaya_pipeline.adapters.voxcpm2 import VoxCPM2Adapter
from gaya_pipeline.adapters.voice_assignments import CLONE_REFERENCE_ASSIGNMENTS
from gaya_pipeline.completion_plan import build_role_snapshot
from gaya_pipeline.conditioning_variants import (
    MODE_HUMAN_REFERENCE,
    MODE_TEXT_ONLY,
    ConditioningVariantError,
    realized_conditioning_mode,
    variant_model_id,
)
from gaya_pipeline.increment_anchor import (
    GATE_POLICY_VERSION,
    MINIMUM_ELIGIBLE_CANDIDATES,
    ROLE_SCOPE_EXPLICIT_REFERENCE,
    SELECTION_AUTHORITY_TYPE,
    SELECTION_POLICY,
    gender_screening,
)
from gaya_pipeline.take_identity import canonical_json

from test_voxcpm2_adapter import (  # type: ignore[import-not-found]
    FakeRuntime as VoxCPM2FakeRuntime,
    _model_root as voxcpm2_model_root,
    _voices_dir as real_voices_dir,
)
from test_irodori_tts_v4_adapter import (  # type: ignore[import-not-found]
    FakeRuntime as IrodoriFakeRuntime,
    _write_wave,
)
from test_qwen3_tts_adapter import (  # type: ignore[import-not-found]
    FakeRuntime as QwenFakeRuntime,
)

PLAN_SHA256 = "b" * 64

# 明示reference を持つ5役のひとつ。`--text` はこれを無視して anchor を使う。
EXPLICIT_SCENARIO = "tavern-night"
EXPLICIT_CHARACTER = "barmaid"
EXPLICIT_VOICE = "amitaro-countdown"
# 明示reference を持たない役。`--ref` は clone 系と同じ割当を使う。
ASSIGNED_SCENARIO = "market-day"
ASSIGNED_CHARACTER = "fruit-vendor"


def _job(
    *,
    scenario: str,
    character: str,
    reference_voice: str | None,
    line_id: str = "line-001",
    gender: str = "female",
) -> LineJob:
    return LineJob(
        scene={"id": scenario, "setting": "夜の酒場。"},
        character={
            "id": character,
            "name": "給仕",
            "kind": "human",
            "gender": gender,
            "age": "adult",
            "archetype": "給仕",
            "voice": "明るく通る中高音。",
            "personality": "快活。",
            "reference_voice": reference_voice,
        },
        line={
            "id": line_id,
            "text": "いらっしゃい。",
            "reading": "イラッシャイ",
            "emotion": "neutral",
            "intensity": 1,
            "delivery": "短く迎える。",
        },
        locale="ja",
    )


def _anchor_adapter(
    base_model: str,
    tmp_path: Path,
    *,
    conditioning_mode: str,
    role_anchor_selection_path: Path | None = None,
) -> Any:
    """GPU依存を持たない fake runtime で anchor 型 adapter を作る。"""

    kwargs: dict[str, Any] = {"conditioning_mode": conditioning_mode}
    if role_anchor_selection_path is not None:
        kwargs["role_anchor_selection_path"] = role_anchor_selection_path
        kwargs["role_anchor_plan_sha256"] = PLAN_SHA256
    if base_model == "qwen3-tts-12hz-1.7b":
        return Qwen3TTSAdapter(runtime=QwenFakeRuntime(tmp_path), **kwargs)
    adapter_class = (
        IrodoriTTSAdapter
        if base_model == "irodori-tts-600m-v3-voicedesign"
        else IrodoriTTSV4Adapter
    )
    return adapter_class(runtime=IrodoriFakeRuntime(), **kwargs)


def _voxcpm2_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    conditioning_mode: str,
) -> Any:
    return VoxCPM2Adapter(
        runtime=VoxCPM2FakeRuntime(),
        model_root=voxcpm2_model_root(tmp_path, monkeypatch),
        conditioning_mode=conditioning_mode,
    )


def _role(job: LineJob) -> Any:
    return build_role_snapshot(
        scenario=job.scenario_id,
        character=str(job.character["id"]),
        character_document=job.character,
        scene_setting=str(job.scene["setting"]),
    )


def _explicit_scope_anchor_selection(
    tmp_path: Path,
    *,
    job: LineJob,
    model_id: str,
    model_revision: str,
    anchor_text: str,
) -> Path:
    """明示reference役ぶんの `role_scope` 付き機械選抜 selection を作る。"""

    role = _role(job)
    root = tmp_path / f"anchor-{model_id}"
    anchor_id = hashlib.sha256(model_id.encode("utf-8")).hexdigest()
    audio = root / "audio" / f"{anchor_id}.wav"
    _write_wave(audio)
    audio_sha = hashlib.sha256(audio.read_bytes()).hexdigest()
    group = {
        "model": model_id,
        "model_revision": model_revision,
        "scenario": role.scenario,
        "character": role.character,
        "role_identity": {
            "scenario": role.scenario,
            "character": role.character,
            "role": dict(role.role),
            "reference_voice": role.reference_voice,
            "scene_setting": role.scene_setting,
        },
        "role_identity_sha256": role.role_identity_sha256,
        "review_role_epoch_sha256": "d" * 64,
        "role_epoch_sha256": "e" * 64,
        "anchor_id": anchor_id,
        "attempt": 1,
        "seed": 12345,
        "audio_path": f"audio/{anchor_id}.wav",
        "audio_sha256": audio_sha,
        "anchor_text": anchor_text,
        "anchor_text_sha256": hashlib.sha256(
            anchor_text.encode("utf-8"),
        ).hexdigest(),
        "authority": {
            "type": SELECTION_AUTHORITY_TYPE,
            "policy_version": SELECTION_POLICY,
            "minimum_eligible_candidates": MINIMUM_ELIGIBLE_CANDIDATES,
            "gate_policy_version": GATE_POLICY_VERSION,
        },
        "screening": gender_screening(
            expected_gender=str(role.role["gender"]),
            median_f0_hz=200.0,
        ),
        "soft_signals": [],
        "decision_sha256": "f" * 64,
    }
    document = {
        "format_version": 1,
        "protocol": "role-anchor-machine-selection-v1",
        "plan_sha256": PLAN_SHA256,
        "candidate_set_sha256": "c" * 64,
        "model": model_id,
        "model_revision": model_revision,
        "role_scope": ROLE_SCOPE_EXPLICIT_REFERENCE,
        "groups": [group],
    }
    payload = canonical_json(document).encode("utf-8")
    path = root / "role-anchor-machine-selection-v1.json"
    path.write_bytes(payload)
    path.with_suffix(".sha256").write_bytes(
        f"{hashlib.sha256(payload).hexdigest()}\n".encode("ascii"),
    )
    return path


# --------------------------------------------------------------------------- #
# --text: 明示referenceを無視してモデル自作の見本を使う
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "base_model",
    [
        "irodori-tts-600m-v3-voicedesign",
        "irodori-tts-v4-small",
        "qwen3-tts-12hz-1.7b",
    ],
)
def test_text_only_ignores_explicit_reference_for_anchor_models(
    tmp_path: Path,
    base_model: str,
) -> None:
    module_name = {
        "irodori-tts-600m-v3-voicedesign": "gaya_pipeline.adapters.irodori_tts",
        "irodori-tts-v4-small": "gaya_pipeline.adapters.irodori_tts_v4",
        "qwen3-tts-12hz-1.7b": "gaya_pipeline.adapters.qwen3_tts",
    }[base_model]
    import importlib

    module = importlib.import_module(module_name)
    anchor_text = getattr(module, "ROLE_ANCHOR_TEXT", None) or module.REFERENCE_TEXT
    model_id = variant_model_id(base_model, MODE_TEXT_ONLY)
    job = _job(
        scenario=EXPLICIT_SCENARIO,
        character=EXPLICIT_CHARACTER,
        reference_voice=EXPLICIT_VOICE,
    )
    selection = _explicit_scope_anchor_selection(
        tmp_path,
        job=job,
        model_id=model_id,
        model_revision=module.PROFILE_VERSION,
        anchor_text=anchor_text,
    )
    adapter = _anchor_adapter(
        base_model,
        tmp_path,
        conditioning_mode=MODE_TEXT_ONLY,
        role_anchor_selection_path=selection,
    )
    assert adapter.profile.id == model_id
    assert adapter.profile.conditioning == {
        "base_model": base_model,
        "mode": MODE_TEXT_ONLY,
    }
    # 明示referenceがあっても voices は一切読まない。
    adapter.prepare([job], tmp_path / "artifacts", tmp_path / "missing-voices")
    resolved = adapter.generation_input(job, adapter.take_recipe().single_take_context())
    assert (
        realized_conditioning_mode(base_model=base_model, realized=resolved)
        == MODE_TEXT_ONLY
    )
    assert resolved["selected_anchor"]["anchor_selection_sha256"] == hashlib.sha256(
        selection.read_bytes(),
    ).hexdigest()


def test_text_only_voxcpm2_uses_voice_design(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = variant_model_id("voxcpm2", MODE_TEXT_ONLY)
    adapter = _voxcpm2_adapter(
        tmp_path,
        monkeypatch,
        conditioning_mode=MODE_TEXT_ONLY,
    )
    job = _job(
        scenario=EXPLICIT_SCENARIO,
        character=EXPLICIT_CHARACTER,
        reference_voice=EXPLICIT_VOICE,
    )
    assert adapter.profile.id == model_id
    adapter.prepare([job], tmp_path / "artifacts", tmp_path / "missing-voices")
    resolved = adapter.generation_input(job, adapter.take_recipe().single_take_context())
    assert resolved["reference_kind"] == "voice_design"
    assert resolved["reference_voice"] is None
    assert (
        realized_conditioning_mode(base_model="voxcpm2", realized=resolved)
        == MODE_TEXT_ONLY
    )


# --------------------------------------------------------------------------- #
# --ref: 全役を人間収録素材へ
# --------------------------------------------------------------------------- #


def _assigned_job() -> LineJob:
    assert (ASSIGNED_SCENARIO, ASSIGNED_CHARACTER) in CLONE_REFERENCE_ASSIGNMENTS
    return _job(
        scenario=ASSIGNED_SCENARIO,
        character=ASSIGNED_CHARACTER,
        reference_voice=None,
        gender="male",
    )


@pytest.mark.parametrize(
    "base_model",
    [
        "irodori-tts-600m-v3-voicedesign",
        "irodori-tts-v4-small",
        "qwen3-tts-12hz-1.7b",
    ],
)
def test_human_reference_uses_clone_assignment(
    tmp_path: Path,
    base_model: str,
) -> None:
    model_id = variant_model_id(base_model, MODE_HUMAN_REFERENCE)
    assigned = CLONE_REFERENCE_ASSIGNMENTS[(ASSIGNED_SCENARIO, ASSIGNED_CHARACTER)]
    voices = real_voices_dir(tmp_path, materialize={assigned})
    adapter = _anchor_adapter(
        base_model,
        tmp_path,
        conditioning_mode=MODE_HUMAN_REFERENCE,
    )
    assert adapter.profile.id == model_id
    job = _assigned_job()
    # anchor selection を一切与えなくても `--ref` は成立する。
    adapter.prepare([job], tmp_path / "artifacts", voices)
    resolved = adapter.generation_input(job, adapter.take_recipe().single_take_context())
    assert (
        realized_conditioning_mode(base_model=base_model, realized=resolved)
        == MODE_HUMAN_REFERENCE
    )
    assert "selected_anchor" not in resolved


def test_human_reference_voxcpm2_records_assignment_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assigned = CLONE_REFERENCE_ASSIGNMENTS[(ASSIGNED_SCENARIO, ASSIGNED_CHARACTER)]
    voices = real_voices_dir(tmp_path, materialize={assigned})
    adapter = _voxcpm2_adapter(
        tmp_path,
        monkeypatch,
        conditioning_mode=MODE_HUMAN_REFERENCE,
    )
    assert adapter.profile.id == variant_model_id("voxcpm2", MODE_HUMAN_REFERENCE)
    job = _assigned_job()
    adapter.prepare([job], tmp_path / "artifacts", voices)
    resolved = adapter.generation_input(job, adapter.take_recipe().single_take_context())
    assert resolved["reference_kind"] == "asset"
    assert resolved["reference_voice"] == assigned
    assert resolved["reference_selection_source"] == (
        f"adapter.assignment:{ASSIGNED_SCENARIO}/{ASSIGNED_CHARACTER}"
    )
    assert (
        realized_conditioning_mode(base_model="voxcpm2", realized=resolved)
        == MODE_HUMAN_REFERENCE
    )


def test_human_reference_fails_for_unassigned_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _voxcpm2_adapter(
        tmp_path,
        monkeypatch,
        conditioning_mode=MODE_HUMAN_REFERENCE,
    )
    job = _job(
        scenario="nowhere",
        character="nobody",
        reference_voice=None,
    )
    with pytest.raises(ConditioningVariantError):
        adapter.prepare([job], tmp_path / "artifacts", tmp_path / "voices")


def test_base_adapters_are_unchanged_without_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapters = [
        _anchor_adapter(base_model, tmp_path, conditioning_mode=None)
        for base_model in (
            "irodori-tts-600m-v3-voicedesign",
            "irodori-tts-v4-small",
            "qwen3-tts-12hz-1.7b",
        )
    ]
    adapters.append(_voxcpm2_adapter(tmp_path, monkeypatch, conditioning_mode=None))
    for adapter in adapters:
        assert adapter.profile.conditioning is None
        assert "conditioning" not in adapter.profile.as_manifest_entry()


def test_create_adapter_passes_conditioning_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class _Stub:
        profile = None

        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

    monkeypatch.setattr(
        adapters_package,
        "_adapter_class",
        lambda model_id: _Stub,
    )
    create_adapter("voxcpm2--ref")
    assert seen == {"conditioning_mode": MODE_HUMAN_REFERENCE}
    seen.clear()
    create_adapter("supertonic-3")
    assert seen == {}
