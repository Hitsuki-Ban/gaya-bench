from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline import increment_plan
from gaya_pipeline.increment_plan import (
    INCREMENT_GROUPS,
    INCREMENT_MINIMUM_ELIGIBLE,
    INCREMENT_PRIMARY_SEED_BASE,
    INCREMENT_TAKES,
    IncrementPlanError,
    build_increment_plan_document,
    compute_increment_plan_id,
    increment_model_policy,
    load_increment_plan,
)
from gaya_pipeline.take_identity import canonical_json

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = REPOSITORY_ROOT / "scenarios"
VOICES = REPOSITORY_ROOT / "assets" / "voices"
MODEL = "irodori-tts-v4-small"
REVISION = "test-revision-v4"
ZERO = "0" * 64
ONE = "1" * 64
TWO = "2" * 64


def _document() -> dict[str, Any]:
    return build_increment_plan_document(
        model=MODEL,
        model_revision=REVISION,
        scenarios_dir=SCENARIOS,
        voices_dir=VOICES,
        anchor_source_plan_sha256=ZERO,
        anchor_candidate_set_sha256=ONE,
        anchor_selection_sha256=TWO,
    )


def _write(tmp_path: Path, document: Any) -> Path:
    path = tmp_path / "increment-plan.json"
    path.write_bytes(canonical_json(document).encode("utf-8"))
    return path


def test_増分planは161_targetと58_roleと53_anchor_roleを固定する(
    tmp_path: Path,
) -> None:
    document = _document()
    assert len(document["phase_b"]["targets"]) == INCREMENT_GROUPS == 161
    assert len(document["roles"]) == 58
    assert [item["id"] for item in document["models"]] == [MODEL]

    plan = load_increment_plan(
        _write(tmp_path, document),
        scenarios_dir=SCENARIOS,
        voices_dir=VOICES,
    )
    assert plan.model == MODEL
    assert len(plan.targets) == 161
    assert len(plan.anchor_roles()) == 53
    assert {target.model for target in plan.targets} == {MODEL}
    assert {target.variant for target in plan.targets} == {"dry"}


def test_増分planのmodel_policyは非aivisspeechの既定と同形である() -> None:
    policy = increment_model_policy(MODEL)
    assert policy.takes == INCREMENT_TAKES == 4
    assert policy.minimum_eligible_candidates == INCREMENT_MINIMUM_ELIGIBLE == 3
    assert policy.seed_policy == "derived-sha256-v1"
    assert policy.primary_seed_base == INCREMENT_PRIMARY_SEED_BASE
    # frozen 8-model path の seed_base 104 とは必ず別値にする。
    assert policy.primary_seed_base != 104


def test_plan_idは同一入力で決定論的である(tmp_path: Path) -> None:
    first = compute_increment_plan_id(_document())
    second = compute_increment_plan_id(_document())
    assert first == second
    path = _write(tmp_path, _document())
    plan = load_increment_plan(path, scenarios_dir=SCENARIOS, voices_dir=VOICES)
    assert plan.plan_id == first == hashlib.sha256(path.read_bytes()).hexdigest()


def test_anchor_authority違いはplan_idを変える() -> None:
    other = build_increment_plan_document(
        model=MODEL,
        model_revision=REVISION,
        scenarios_dir=SCENARIOS,
        voices_dir=VOICES,
        anchor_source_plan_sha256=ZERO,
        anchor_candidate_set_sha256=ONE,
        anchor_selection_sha256=ONE,
    )
    assert compute_increment_plan_id(other) != compute_increment_plan_id(_document())


def test_非canonical_bytesのplanは拒否される(tmp_path: Path) -> None:
    path = tmp_path / "increment-plan.json"
    path.write_bytes(
        canonical_json(_document()).encode("utf-8").replace(b"{", b"{ ", 1),
    )
    with pytest.raises(IncrementPlanError):
        load_increment_plan(path, scenarios_dir=SCENARIOS, voices_dir=VOICES)


def test_公開済みbase_SHAの改竄は拒否される(tmp_path: Path) -> None:
    document = _document()
    document["base"]["manifest_sha256"] = ZERO
    with pytest.raises(IncrementPlanError, match="manifest_sha256"):
        load_increment_plan(
            _write(tmp_path, document),
            scenarios_dir=SCENARIOS,
            voices_dir=VOICES,
        )


def test_複数modelのplanは拒否される(tmp_path: Path) -> None:
    document = _document()
    document["models"].append({"id": "voxcpm2", "revision": "x"})
    with pytest.raises(IncrementPlanError, match="新規1 model"):
        load_increment_plan(
            _write(tmp_path, document),
            scenarios_dir=SCENARIOS,
            voices_dir=VOICES,
        )


def test_target数の増減は拒否される(tmp_path: Path) -> None:
    document = _document()
    document["phase_b"]["targets"].pop()
    with pytest.raises(IncrementPlanError, match="161"):
        load_increment_plan(
            _write(tmp_path, document),
            scenarios_dir=SCENARIOS,
            voices_dir=VOICES,
        )


def test_model_policyの改竄は拒否される(tmp_path: Path) -> None:
    document = _document()
    document["phase_b"]["model_policies"][0]["takes"] = 1
    with pytest.raises(IncrementPlanError, match="既定policy"):
        load_increment_plan(
            _write(tmp_path, document),
            scenarios_dir=SCENARIOS,
            voices_dir=VOICES,
        )


def test_role_identity_SHAの改竄は拒否される(tmp_path: Path) -> None:
    document = _document()
    document["roles"][0]["role_identity_sha256"] = ZERO
    with pytest.raises(IncrementPlanError, match="role_identity_sha256"):
        load_increment_plan(
            _write(tmp_path, document),
            scenarios_dir=SCENARIOS,
            voices_dir=VOICES,
        )


def test_増分planは凍結された完全baseline_planを一切参照し直さない() -> None:
    # frozen 8-model path の定数は import するだけで、値を上書きしない。
    from gaya_pipeline import completion_plan

    assert completion_plan.PRIMARY_SEED_BASE == 104
    assert len(completion_plan.MODEL_REVISIONS) == 8
    assert MODEL not in completion_plan.MODEL_REVISIONS
    assert increment_plan.PROTOCOL != completion_plan.PROTOCOL
