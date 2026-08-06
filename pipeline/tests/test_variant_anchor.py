"""`--text` バリアント用の58役 anchor authority 合成 (#201)。"""

from __future__ import annotations

import hashlib
import math
import struct
import wave
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline.completion_plan import RoleSnapshot, _source_snapshot
from gaya_pipeline.conditioning_variants import MODE_TEXT_ONLY, variant_model_id
from gaya_pipeline.increment_anchor import (
    ANCHOR_ROLE_COUNT,
    EXPLICIT_REFERENCE_ROLE_COUNT,
    GATE_POLICY_VERSION,
    MINIMUM_ELIGIBLE_CANDIDATES,
    ROLE_SCOPE_EXPLICIT_REFERENCE,
    ROLE_SCOPE_NO_REFERENCE,
    SEED_BASE,
    SELECTION_AUTHORITY_TYPE,
    SELECTION_POLICY,
    VARIANT_SEED_BASE,
    IncrementAnchorError,
    build_anchor_bootstrap_plan_document,
    gender_screening,
    validate_anchor_bootstrap_plan,
    validate_machine_anchor_selection,
)
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.variant_anchor import (
    AUTHORITY_AUTO,
    AUTHORITY_HUMAN,
    ROLE_COUNT,
    VariantAnchorError,
    compose_variant_anchor_selection,
    load_variant_anchor_selection,
    resolve_variant_anchor,
    variant_role_epoch_sha256,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"
VOICES_DIR = REPOSITORY_ROOT / "assets" / "voices"
BASE_MODEL = "irodori-tts-v4-small"
MODEL_REVISION = "test-revision-1"
ANCHOR_TEXT = "そらにはくもがうかび、とおくでかぜのおとがきこえます。"


def _roles() -> tuple[RoleSnapshot, ...]:
    _sources, roles, _documents = _source_snapshot(
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
    )
    return roles


def _write_wave(path: Path, *, offset: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 48_000
    samples = [
        int(3000 * math.sin(2 * math.pi * (110 + offset % 40) * index / sample_rate))
        for index in range(sample_rate // 20)
    ]
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _passing_median_f0(gender: str) -> float:
    return {"female": 200.0, "male": 120.0}.get(gender, 150.0)


def _machine_selection(
    root: Path,
    *,
    roles: tuple[RoleSnapshot, ...],
    role_scope: str,
    model: str = BASE_MODEL,
    model_revision: str = MODEL_REVISION,
    anchor_text: str = ANCHOR_TEXT,
) -> Path:
    groups: list[dict[str, Any]] = []
    for index, role in enumerate(sorted(roles, key=lambda item: item.identity)):
        anchor_id = hashlib.sha256(
            f"{model}/{role.scenario}/{role.character}".encode(),
        ).hexdigest()
        audio = root / "audio" / f"{anchor_id}.wav"
        _write_wave(audio, offset=index)
        groups.append(
            {
                "model": model,
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
                "review_role_epoch_sha256": hashlib.sha256(
                    f"review/{anchor_id}".encode(),
                ).hexdigest(),
                "role_epoch_sha256": hashlib.sha256(
                    f"epoch/{anchor_id}".encode(),
                ).hexdigest(),
                "anchor_id": anchor_id,
                "attempt": 1,
                "seed": 1000 + index,
                "audio_path": f"audio/{anchor_id}.wav",
                "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
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
                    median_f0_hz=_passing_median_f0(str(role.role["gender"])),
                ),
                "soft_signals": [],
                "decision_sha256": hashlib.sha256(
                    f"decision/{anchor_id}".encode(),
                ).hexdigest(),
            },
        )
    document: dict[str, Any] = {
        "format_version": 1,
        "protocol": "role-anchor-machine-selection-v1",
        "plan_sha256": hashlib.sha256(role_scope.encode()).hexdigest(),
        "candidate_set_sha256": hashlib.sha256(
            f"candidates/{role_scope}".encode(),
        ).hexdigest(),
        "model": model,
        "model_revision": model_revision,
        "groups": groups,
    }
    if role_scope != ROLE_SCOPE_NO_REFERENCE:
        document["role_scope"] = role_scope
    normalized = validate_machine_anchor_selection(document)
    payload = canonical_json(normalized).encode("utf-8")
    path = root / "role-anchor-machine-selection-v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.with_suffix(".sha256").write_bytes(
        f"{hashlib.sha256(payload).hexdigest()}\n".encode("ascii"),
    )
    return path


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    roles = _roles()
    no_reference = tuple(role for role in roles if role.reference_voice is None)
    explicit = tuple(role for role in roles if role.reference_voice is not None)
    assert len(no_reference) == ANCHOR_ROLE_COUNT
    assert len(explicit) == EXPLICIT_REFERENCE_ROLE_COUNT
    inherited = _machine_selection(
        tmp_path / "inherited",
        roles=no_reference,
        role_scope=ROLE_SCOPE_NO_REFERENCE,
    )
    supplement = _machine_selection(
        tmp_path / "supplement",
        roles=explicit,
        role_scope=ROLE_SCOPE_EXPLICIT_REFERENCE,
    )
    return inherited, supplement


# --------------------------------------------------------------------------- #
# role scope (increment_anchor の一般化)
# --------------------------------------------------------------------------- #


def test_explicit_scope_bootstrap_plan_targets_the_five_roles() -> None:
    document = build_anchor_bootstrap_plan_document(
        model=BASE_MODEL,
        model_revision=MODEL_REVISION,
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
        anchor_text=ANCHOR_TEXT,
        role_scope=ROLE_SCOPE_EXPLICIT_REFERENCE,
    )
    assert document["role_scope"] == ROLE_SCOPE_EXPLICIT_REFERENCE
    assert document["phase_a"]["seed_base"] == VARIANT_SEED_BASE
    assert len(document["targets"]) == EXPLICIT_REFERENCE_ROLE_COUNT
    assert len(document["roles"]) == ROLE_COUNT
    normalized = validate_anchor_bootstrap_plan(document)
    assert normalized == document
    identities = {
        (target["scenario"], target["character"]) for target in document["targets"]
    }
    assert identities == {
        ("castle-gate", "merchant"),
        ("chinatown-street", "kaimono-musume"),
        ("guild-hall", "receptionist"),
        ("tavern-night", "barmaid"),
        ("village-morning", "granny"),
    }


def test_default_scope_bootstrap_plan_is_unchanged() -> None:
    document = build_anchor_bootstrap_plan_document(
        model=BASE_MODEL,
        model_revision=MODEL_REVISION,
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
        anchor_text=ANCHOR_TEXT,
    )
    # 公開済みplanのcanonical bytesを保つため role_scope field は付かない。
    assert "role_scope" not in document
    assert document["phase_a"]["seed_base"] == SEED_BASE
    assert len(document["targets"]) == ANCHOR_ROLE_COUNT
    with pytest.raises(IncrementAnchorError):
        validate_anchor_bootstrap_plan(
            {**document, "role_scope": ROLE_SCOPE_NO_REFERENCE},
        )


def test_machine_selection_scope_is_enforced(tmp_path: Path) -> None:
    roles = _roles()
    explicit = tuple(role for role in roles if role.reference_voice is not None)
    # 明示reference役は既定scopeでは拒否される (凍結契約のまま)。
    with pytest.raises(IncrementAnchorError):
        _machine_selection(
            tmp_path / "bad",
            roles=explicit,
            role_scope=ROLE_SCOPE_NO_REFERENCE,
        )


# --------------------------------------------------------------------------- #
# 合成
# --------------------------------------------------------------------------- #


def test_compose_builds_58_role_authority(tmp_path: Path) -> None:
    inherited, supplement = _sources(tmp_path)
    summary = compose_variant_anchor_selection(
        base_model=BASE_MODEL,
        model_revision=MODEL_REVISION,
        inherited_selection_path=inherited,
        supplement_selection_path=supplement,
        output_dir=tmp_path / "variant",
    )
    assert summary.group_count == ROLE_COUNT
    assert summary.inherited_count == ANCHOR_ROLE_COUNT
    assert summary.supplement_count == EXPLICIT_REFERENCE_ROLE_COUNT
    assert summary.selection_path.is_file()
    assert summary.plan_path.is_file()
    assert (
        hashlib.sha256(summary.selection_path.read_bytes()).hexdigest()
        == summary.selection_sha256
    )

    model_id = variant_model_id(BASE_MODEL, MODE_TEXT_ONLY)
    roles = {role.identity: role for role in _roles()}
    barmaid = roles[("tavern-night", "barmaid")]
    assert barmaid.reference_voice is not None
    resolved = resolve_variant_anchor(
        selection_path=summary.selection_path,
        plan_sha256=summary.plan_sha256,
        model=model_id,
        model_revision=MODEL_REVISION,
        role=barmaid,
    )
    assert resolved.model == model_id
    assert resolved.anchor_text == ANCHOR_TEXT
    assert resolved.audio_path.is_file()
    assert resolved.role_epoch_sha256 == variant_role_epoch_sha256(
        model=model_id,
        model_revision=MODEL_REVISION,
        conditioning_mode=MODE_TEXT_ONLY,
        scenario="tavern-night",
        character="barmaid",
        role_identity_sha256=barmaid.role_identity_sha256,
        source_selection_sha256=hashlib.sha256(
            supplement.read_bytes(),
        ).hexdigest(),
        source_role_epoch_sha256=hashlib.sha256(
            "epoch/{}".format(
                hashlib.sha256(
                    f"{BASE_MODEL}/tavern-night/barmaid".encode(),
                ).hexdigest(),
            ).encode(),
        ).hexdigest(),
    )


def test_compose_records_source_authority(tmp_path: Path) -> None:
    inherited, supplement = _sources(tmp_path)
    summary = compose_variant_anchor_selection(
        base_model=BASE_MODEL,
        model_revision=MODEL_REVISION,
        inherited_selection_path=inherited,
        supplement_selection_path=supplement,
        output_dir=tmp_path / "variant",
    )
    import json

    selection = json.loads(summary.selection_path.read_text(encoding="utf-8"))
    sources = {
        group["source"]["selection_sha256"] for group in selection["groups"]
    }
    assert sources == {
        hashlib.sha256(inherited.read_bytes()).hexdigest(),
        hashlib.sha256(supplement.read_bytes()).hexdigest(),
    }
    assert {group["authority_type"] for group in selection["groups"]} == {
        AUTHORITY_AUTO,
    }
    plan = json.loads(summary.plan_path.read_text(encoding="utf-8"))
    assert {item["role_scope"] for item in plan["inputs"]} == {
        ROLE_SCOPE_NO_REFERENCE,
        ROLE_SCOPE_EXPLICIT_REFERENCE,
    }
    assert sum(item["role_count"] for item in plan["inputs"]) == ROLE_COUNT


def _human_selection(
    root: Path,
    *,
    roles: tuple[RoleSnapshot, ...],
    model: str,
    model_revision: str,
    anchor_text: str = ANCHOR_TEXT,
) -> Path:
    """#174 本番と同じ人手選抜 `role-anchor-selection-v1` を組む。

    irodori v3 / qwen3 の継承53役はこの protocol なので、compose がこの形を
    読めることを固定する。
    """

    from gaya_pipeline.completion_anchor import validate_anchor_selection

    groups: list[dict[str, Any]] = []
    for index, role in enumerate(sorted(roles, key=lambda item: item.identity)):
        anchor_id = hashlib.sha256(
            f"human/{model}/{role.scenario}/{role.character}".encode(),
        ).hexdigest()
        audio = root / "audio" / f"{anchor_id}.wav"
        _write_wave(audio, offset=index)
        decision = {
            "id": hashlib.sha256(f"id/{anchor_id}".encode()).hexdigest(),
            "model": model,
            "scenario": role.scenario,
            "character": role.character,
            "line": None,
            "role_epoch_sha256": hashlib.sha256(
                f"review/{anchor_id}".encode(),
            ).hexdigest(),
            "group_sha256": hashlib.sha256(f"g/{anchor_id}".encode()).hexdigest(),
            "heard_candidate_ids": [anchor_id],
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
        audio_sha = hashlib.sha256(audio.read_bytes()).hexdigest()
        decision_sha = hashlib.sha256(
            canonical_json(decision).encode("utf-8"),
        ).hexdigest()
        groups.append(
            {
                "model": model,
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
                "review_role_epoch_sha256": decision["role_epoch_sha256"],
                "role_epoch_sha256": hashlib.sha256(
                    canonical_json(
                        {
                            "protocol": "selected-role-epoch-v1",
                            "model": model,
                            "model_revision": model_revision,
                            "scenario": role.scenario,
                            "character": role.character,
                            "role_identity_sha256": role.role_identity_sha256,
                            "review_role_epoch_sha256": decision[
                                "role_epoch_sha256"
                            ],
                            "anchor_id": anchor_id,
                            "audio_sha256": audio_sha,
                            "decision_sha256": decision_sha,
                        },
                    ).encode("utf-8"),
                ).hexdigest(),
                "anchor_id": anchor_id,
                "attempt": 2,
                "seed": 2000 + index,
                "audio_path": f"audio/{anchor_id}.wav",
                "audio_sha256": audio_sha,
                "anchor_text": anchor_text,
                "anchor_text_sha256": hashlib.sha256(
                    anchor_text.encode("utf-8"),
                ).hexdigest(),
                "decision": decision,
                "decision_sha256": decision_sha,
            },
        )
    document = validate_anchor_selection(
        {
            "format_version": 1,
            "protocol": "role-anchor-selection-v1",
            "plan_sha256": hashlib.sha256(b"human-plan").hexdigest(),
            "candidate_set_sha256": hashlib.sha256(b"human-candidates").hexdigest(),
            "groups": groups,
        },
    )
    payload = canonical_json(document).encode("utf-8")
    path = root / "role-anchor-selection-v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.with_suffix(".sha256").write_bytes(
        f"{hashlib.sha256(payload).hexdigest()}\n".encode("ascii"),
    )
    return path


def test_compose_accepts_the_frozen_human_selection_as_inherited_source(
    tmp_path: Path,
) -> None:
    """v3 / qwen3 の継承53役は #174 の人手選抜 protocol で入ってくる。"""

    from gaya_pipeline.adapters.qwen3_tts import ROLE_ANCHOR_TEXT as QWEN_ANCHOR_TEXT

    model = "qwen3-tts-12hz-1.7b"
    roles = _roles()
    no_reference = tuple(role for role in roles if role.reference_voice is None)
    explicit = tuple(role for role in roles if role.reference_voice is not None)
    inherited = _human_selection(
        tmp_path / "inherited",
        roles=no_reference,
        model=model,
        model_revision=MODEL_REVISION,
        anchor_text=QWEN_ANCHOR_TEXT,
    )
    supplement = _machine_selection(
        tmp_path / "supplement",
        roles=explicit,
        role_scope=ROLE_SCOPE_EXPLICIT_REFERENCE,
        model=model,
        anchor_text=QWEN_ANCHOR_TEXT,
    )
    summary = compose_variant_anchor_selection(
        base_model=model,
        model_revision=MODEL_REVISION,
        inherited_selection_path=inherited,
        supplement_selection_path=supplement,
        output_dir=tmp_path / "variant",
    )
    assert summary.group_count == ROLE_COUNT
    assert summary.inherited_count == ANCHOR_ROLE_COUNT

    import json

    selection = json.loads(summary.selection_path.read_text(encoding="utf-8"))
    assert {group["authority_type"] for group in selection["groups"]} == {
        AUTHORITY_HUMAN,
        AUTHORITY_AUTO,
    }
    assert {group["anchor_text"] for group in selection["groups"]} == {
        QWEN_ANCHOR_TEXT,
    }
    # 継承分は人手選抜、補完分は機械選抜として由来が区別できる。
    by_authority = {group["authority_type"] for group in selection["groups"][:1]}
    assert by_authority <= {AUTHORITY_HUMAN, AUTHORITY_AUTO}


def test_compose_rejects_mixed_anchor_text(tmp_path: Path) -> None:
    """anchor 発話文が違う selection は合成できない (regime混在の防止)。"""

    roles = _roles()
    inherited = _machine_selection(
        tmp_path / "inherited",
        roles=tuple(role for role in roles if role.reference_voice is None),
        role_scope=ROLE_SCOPE_NO_REFERENCE,
        anchor_text=ANCHOR_TEXT,
    )
    supplement = _machine_selection(
        tmp_path / "supplement",
        roles=tuple(role for role in roles if role.reference_voice is not None),
        role_scope=ROLE_SCOPE_EXPLICIT_REFERENCE,
        anchor_text="ちがうぶんしょうです。",
    )
    with pytest.raises(VariantAnchorError, match="anchor text"):
        compose_variant_anchor_selection(
            base_model=BASE_MODEL,
            model_revision=MODEL_REVISION,
            inherited_selection_path=inherited,
            supplement_selection_path=supplement,
            output_dir=tmp_path / "variant",
        )


def test_compose_rejects_wrong_scope_order(tmp_path: Path) -> None:
    inherited, supplement = _sources(tmp_path)
    with pytest.raises(VariantAnchorError):
        compose_variant_anchor_selection(
            base_model=BASE_MODEL,
            model_revision=MODEL_REVISION,
            inherited_selection_path=supplement,
            supplement_selection_path=inherited,
            output_dir=tmp_path / "variant",
        )


def test_compose_rejects_voxcpm2(tmp_path: Path) -> None:
    inherited, supplement = _sources(tmp_path)
    with pytest.raises(VariantAnchorError):
        compose_variant_anchor_selection(
            base_model="voxcpm2",
            model_revision=MODEL_REVISION,
            inherited_selection_path=inherited,
            supplement_selection_path=supplement,
            output_dir=tmp_path / "variant",
        )


ANCHOR_MODELS = (
    "irodori-tts-600m-v3-voicedesign",
    "irodori-tts-v4-small",
    "qwen3-tts-12hz-1.7b",
)


def _frozen_anchor_texts() -> dict[str, str]:
    """#174 の凍結 anchor source plan が記録する model ごとの anchor 発話文。

    この document の SHA は `completion_anchor.ANCHOR_SOURCE_PLAN_SHA256` に
    束縛されており、本番53役 anchor はこの文で合成されている。
    """

    import json

    from gaya_pipeline.completion_anchor import ANCHOR_SOURCE_PLAN_SHA256

    path = (
        REPOSITORY_ROOT
        / "docs"
        / "research"
        / "full-baseline-completion"
        / "anchor-source-plan-v1.json"
    )
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ANCHOR_SOURCE_PLAN_SHA256
    document = json.loads(raw.decode("utf-8"))
    return {
        str(item["model"]): str(item["text"])
        for item in document["phase_a"]["anchor_texts"]
    }


def test_anchor_models_expose_the_frozen_anchor_text() -> None:
    """anchor bootstrap が読む ROLE_ANCHOR_TEXT が #174 の本番文と一致する。

    Qwen は VoiceDesign reference と同一文なので `REFERENCE_TEXT` の別名。
    文字列を複製していないこと (同一オブジェクト) までを固定する。
    """

    from gaya_pipeline.adapters import irodori_tts, irodori_tts_v4, qwen3_tts
    from gaya_pipeline.completion_anchor import _anchor_texts

    assert qwen3_tts.ROLE_ANCHOR_TEXT is qwen3_tts.REFERENCE_TEXT
    assert irodori_tts_v4.ROLE_ANCHOR_TEXT is irodori_tts.ROLE_ANCHOR_TEXT

    frozen = _frozen_anchor_texts()
    assert set(frozen) == {
        "irodori-tts-600m-v3-voicedesign",
        "qwen3-tts-12hz-1.7b",
    }
    assert irodori_tts.ROLE_ANCHOR_TEXT == frozen["irodori-tts-600m-v3-voicedesign"]
    assert qwen3_tts.ROLE_ANCHOR_TEXT == frozen["qwen3-tts-12hz-1.7b"]
    # v4 は #174 に無いが v3 と同じ regime で anchor を作る。
    assert irodori_tts_v4.ROLE_ANCHOR_TEXT == frozen[
        "irodori-tts-600m-v3-voicedesign"
    ]
    # 凍結側の解決表とも一致する (単一の情報源)。
    assert _anchor_texts() == frozen


@pytest.mark.parametrize("model", ANCHOR_MODELS)
def test_explicit_scope_bootstrap_interface_is_complete(model: str) -> None:
    """`gaya increment anchor-bootstrap --role-scope …` が3 model全部で通る。

    CLI helper (`_increment_model_revision` / `_increment_anchor_text`) と
    adapter の anchor 生成入力までを、GPUなしで一巡させる。
    """

    from gaya_pipeline.adapters import _adapter_class
    from gaya_pipeline.cli import _increment_anchor_text, _increment_model_revision

    revision = _increment_model_revision(model)
    anchor_text = _increment_anchor_text(model)
    assert revision == _adapter_class(model).profile.version
    assert anchor_text

    document = validate_anchor_bootstrap_plan(
        build_anchor_bootstrap_plan_document(
            model=model,
            model_revision=revision,
            scenarios_dir=SCENARIOS_DIR,
            voices_dir=VOICES_DIR,
            anchor_text=anchor_text,
            role_scope=ROLE_SCOPE_EXPLICIT_REFERENCE,
        ),
    )
    assert document["anchor_text"] == anchor_text
    assert len(document["targets"]) == EXPLICIT_REFERENCE_ROLE_COUNT
    assert document["phase_a"]["seed_base"] == VARIANT_SEED_BASE

    # adapter が anchor 生成契約 (3メソッド) を満たし、5役すべてで
    # generation input を返し、その発話文が plan と一致すること。
    adapter_class = _adapter_class(model)
    for name in (
        "role_anchor_generation_input",
        "generate_role_anchor",
        "close_role_anchor_generation",
    ):
        assert callable(getattr(adapter_class, name, None)), name

    adapter = _fake_anchor_adapter(model)
    assert adapter.profile.id == model
    assert adapter.profile.version == revision
    roles = {role.identity: role for role in _roles()}
    for target in document["targets"]:
        role = roles[(target["scenario"], target["character"])]
        assert role.reference_voice is not None
        generation_input = adapter.role_anchor_generation_input(
            role,
            role_scope=ROLE_SCOPE_EXPLICIT_REFERENCE,
        )
        assert generation_input["text"] == anchor_text


def _fake_anchor_adapter(model: str) -> Any:
    """GPU依存のない runtime で anchor adapter を組む。"""

    import tempfile

    from gaya_pipeline.adapters.irodori_tts import IrodoriTTSAdapter
    from gaya_pipeline.adapters.irodori_tts_v4 import IrodoriTTSV4Adapter
    from gaya_pipeline.adapters.qwen3_tts import Qwen3TTSAdapter

    from test_irodori_tts_v4_adapter import (  # type: ignore[import-not-found]
        FakeRuntime as IrodoriFakeRuntime,
    )
    from test_qwen3_tts_adapter import (  # type: ignore[import-not-found]
        FakeRuntime as QwenFakeRuntime,
    )

    if model == "qwen3-tts-12hz-1.7b":
        return Qwen3TTSAdapter(
            runtime=QwenFakeRuntime(Path(tempfile.mkdtemp())),
        )
    adapter_class = (
        IrodoriTTSAdapter
        if model == "irodori-tts-600m-v3-voicedesign"
        else IrodoriTTSV4Adapter
    )
    return adapter_class(runtime=IrodoriFakeRuntime())


def test_voxcpm2_is_not_an_anchor_model() -> None:
    """VoxCPM2 は bootstrap 経路に入らない (voice designが text-only 経路そのもの)。"""

    from gaya_pipeline.adapters import voxcpm2
    from gaya_pipeline.conditioning_variants import (
        ANCHOR_BASE_MODELS,
        requires_anchor_authority,
    )

    assert "voxcpm2" not in ANCHOR_BASE_MODELS
    assert requires_anchor_authority("voxcpm2") is False
    assert requires_anchor_authority("voxcpm2--text") is False
    assert requires_anchor_authority("voxcpm2--ref") is False
    # anchor 生成契約そのものを実装していないので bootstrap 対象になりえない。
    assert not hasattr(voxcpm2.VoxCPM2Adapter, "role_anchor_generation_input")
    assert not hasattr(voxcpm2.VoxCPM2Adapter, "generate_role_anchor")
    assert getattr(voxcpm2, "ROLE_ANCHOR_TEXT", None) is None


def test_composed_selection_resolves_through_the_variant_plan_path(
    tmp_path: Path,
) -> None:
    """compose → plan-build → generation binding → adapter解決 の一連を通す。"""

    import json

    from gaya_pipeline.take_identity import canonical_json as _canonical
    from gaya_pipeline.increment_anchor import (
        resolve_increment_anchor,
        validate_any_anchor_selection,
    )
    from gaya_pipeline.variant_auto import variant_generation_binding
    from gaya_pipeline.variant_plan import (
        build_variant_plan_document,
        load_variant_plan,
    )

    from test_variant_plan import (  # type: ignore[import-not-found]
        EXPLICIT_ROLE_LINES,
        _base_release,
    )

    inherited, supplement = _sources(tmp_path)
    composed = compose_variant_anchor_selection(
        base_model=BASE_MODEL,
        model_revision=MODEL_REVISION,
        inherited_selection_path=inherited,
        supplement_selection_path=supplement,
        output_dir=tmp_path / "variant",
    )
    selection_document = json.loads(
        composed.selection_path.read_text(encoding="utf-8"),
    )

    # `variant generate` は generation.py の `_phase_b_source` 経由で
    # この protocol を検証する。分岐が無いと human 選抜側に落ちて壊れる。
    validated = validate_any_anchor_selection(selection_document)
    assert validated["protocol"] == "role-anchor-variant-selection-v1"
    assert len(validated["groups"]) == ROLE_COUNT

    document = build_variant_plan_document(
        base_model=BASE_MODEL,
        mode=MODE_TEXT_ONLY,
        model_revision=MODEL_REVISION,
        base_release_dir=_base_release(tmp_path),
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
        anchor_source_plan_sha256=composed.plan_sha256,
        anchor_candidate_set_sha256=selection_document["candidate_set_sha256"],
        anchor_selection_sha256=composed.selection_sha256,
    )
    plan_path = tmp_path / "variant-plan.json"
    plan_path.write_bytes(_canonical(document).encode("utf-8"))
    plan = load_variant_plan(
        plan_path,
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
    )
    assert plan.model == variant_model_id(BASE_MODEL, MODE_TEXT_ONLY)

    anchor_sha, role_epochs = variant_generation_binding(
        plan=plan,
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
        anchor_selection_path=composed.selection_path,
    )
    assert anchor_sha == composed.selection_sha256
    # 生成対象は明示reference 5役ぶんの14行だけ。
    assert len(role_epochs) == EXPLICIT_ROLE_LINES
    epochs_by_role = {
        group["scenario"] + "/" + group["character"]: group["role_epoch_sha256"]
        for group in selection_document["groups"]
    }
    assert (
        role_epochs[("tavern-night", "barmaid-001")]
        == epochs_by_role["tavern-night/barmaid"]
    )

    # adapter は composed selection を variant protocol として解決する。
    barmaid = {role.identity: role for role in _roles()}[
        ("tavern-night", "barmaid")
    ]
    resolved = resolve_increment_anchor(
        selection_path=composed.selection_path,
        plan_sha256=plan.anchor_source_plan_sha256,
        model=plan.model,
        model_revision=MODEL_REVISION,
        role=barmaid,
    )
    assert resolved.model == plan.model
    assert resolved.role_epoch_sha256 == epochs_by_role["tavern-night/barmaid"]


class _PlanStub:
    def __init__(self, *, model: str, plan_sha: str, candidate_sha: str, sha: str):
        self.model = model
        self.anchor_source_plan_sha256 = plan_sha
        self.anchor_candidate_set_sha256 = candidate_sha
        self.anchor_selection_sha256 = sha
        self.models = {model: MODEL_REVISION}
        self._roles = {role.identity: role for role in _roles()}

    def role(self, scenario: str, character: str) -> RoleSnapshot:
        return self._roles[(scenario, character)]


def test_load_variant_anchor_selection_binds_to_plan(tmp_path: Path) -> None:
    inherited, supplement = _sources(tmp_path)
    summary = compose_variant_anchor_selection(
        base_model=BASE_MODEL,
        model_revision=MODEL_REVISION,
        inherited_selection_path=inherited,
        supplement_selection_path=supplement,
        output_dir=tmp_path / "variant",
    )
    import json

    selection = json.loads(summary.selection_path.read_text(encoding="utf-8"))
    plan = _PlanStub(
        model=variant_model_id(BASE_MODEL, MODE_TEXT_ONLY),
        plan_sha=summary.plan_sha256,
        candidate_sha=selection["candidate_set_sha256"],
        sha=summary.selection_sha256,
    )
    digest, epochs = load_variant_anchor_selection(
        summary.selection_path,
        plan=plan,
    )
    assert digest == summary.selection_sha256
    assert len(epochs) == ROLE_COUNT
    assert all(key[0] == plan.model for key in epochs)

    stale = _PlanStub(
        model=plan.model,
        plan_sha=summary.plan_sha256,
        candidate_sha=selection["candidate_set_sha256"],
        sha="0" * 64,
    )
    with pytest.raises(VariantAnchorError):
        load_variant_anchor_selection(summary.selection_path, plan=stale)
