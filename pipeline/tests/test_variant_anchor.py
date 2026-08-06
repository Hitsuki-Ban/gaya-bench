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
