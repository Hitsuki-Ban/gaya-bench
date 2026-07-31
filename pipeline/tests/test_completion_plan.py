from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline.completion_plan import (
    BASE_MANIFEST_SHA256,
    BASE_SELECTION_SHA256,
    IRODORI_MODEL,
    PROTOCOL,
    QWEN_MODEL,
    CompletionPlanError,
    compute_completion_plan_id,
    derive_anchor_seed,
    load_completion_plan,
)
from gaya_pipeline.take_identity import canonical_json


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "research"
    / "full-baseline-completion"
    / "plan.json"
)
MANIFEST_PATH = REPOSITORY_ROOT / "data" / "manifest.json"
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"
VOICES_DIR = REPOSITORY_ROOT / "assets" / "voices"


def _plan_document() -> dict[str, Any]:
    return json.loads(PLAN_PATH.read_bytes())


def _write_plan(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "plan.json"
    path.write_bytes(canonical_json(document).encode("utf-8"))
    return path


def _load(path: Path = PLAN_PATH):
    return load_completion_plan(
        path,
        base_manifest_path=MANIFEST_PATH,
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
    )


def test_canonical_planは全roleと二段階replacementを固定する() -> None:
    plan = _load()

    assert plan.plan_id == compute_completion_plan_id(_plan_document())
    assert plan.base_manifest_sha256 == BASE_MANIFEST_SHA256
    assert plan.base_selection_sha256 == BASE_SELECTION_SHA256
    assert plan.inherited_groups == 925
    assert plan.final_groups == 1_288
    assert len(plan.roles) == 58
    assert len([role for role in plan.roles if role.reference_voice is None]) == 53
    assert len(plan.anchor_targets) == 106
    assert len(plan.anchor_targets_for_model(QWEN_MODEL)) == 53
    assert len(plan.anchor_targets_for_model(IRODORI_MODEL)) == 53
    assert plan.phase_a_takes == 4
    assert plan.phase_a_minimum_eligible_candidates == 3
    assert len(plan.targets) == 363
    assert plan.takes == 4
    assert plan.minimum_eligible_candidates == 3
    assert Counter(target.model for target in plan.targets) == {
        "chatterbox-multilingual-v3": 13,
        "cosyvoice3-0.5b-2512": 14,
        "gpt-sovits-v2-pro-plus": 12,
        IRODORI_MODEL: 161,
        QWEN_MODEL: 161,
        "voxcpm2": 2,
    }
    assert len(plan.target_lines_for_model(QWEN_MODEL)) == 161
    assert plan.target_lines_for_model("voxcpm2") == ()


def test_planはcanonical_bytes以外を拒否する(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_bytes(PLAN_PATH.read_bytes() + b"\n")

    with pytest.raises(CompletionPlanError, match="canonical bytes"):
        _load(path)


def test_旧completion_protocolは拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    document["protocol"] = "baseline-completion-plan-v1"

    with pytest.raises(CompletionPlanError, match=PROTOCOL):
        _load(_write_plan(tmp_path, document))


def test_planはexact_fields以外を拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    document["fallback"] = True

    with pytest.raises(CompletionPlanError, match="exact contract"):
        _load(_write_plan(tmp_path, document))


def test_role_snapshotの改ざんを拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    document["roles"][0]["role"]["gender"] = "neutral"

    with pytest.raises(CompletionPlanError, match="role_identity_sha256"):
        _load(_write_plan(tmp_path, document))


def test_scenario_registryの改ざんを拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    document["sources"]["scenario_registry_sha256"] = "0" * 64

    with pytest.raises(CompletionPlanError, match="sources"):
        _load(_write_plan(tmp_path, document))


def test_明示reference_roleをPhase_Aへ入れられない(tmp_path: Path) -> None:
    document = _plan_document()
    explicit = next(
        role for role in document["roles"] if role["reference_voice"] is not None
    )
    target = document["phase_a"]["targets"][0]
    target["scenario"] = explicit["scenario"]
    target["character"] = explicit["character"]
    target["role_identity_sha256"] = explicit["role_identity_sha256"]

    with pytest.raises(CompletionPlanError, match="明示reference"):
        _load(_write_plan(tmp_path, document))


def test_Phase_B対象の追加と欠落を拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    document["phase_b"]["targets"][-1]["line"] = "not-a-line"

    with pytest.raises(CompletionPlanError, match="固定対象"):
        _load(_write_plan(tmp_path, document))


def test_anchor_seedはplan_role_attemptごとに全て異なる() -> None:
    plan = _load()
    seeds = [
        derive_anchor_seed(
            plan_sha256=plan.plan_id,
            seed_base=plan.phase_a_seed_base,
            model=target.model,
            scenario=target.scenario,
            character=target.character,
            attempt=attempt,
        )
        for target in plan.anchor_targets
        for attempt in range(1, plan.phase_a_takes + 1)
    ]

    assert len(seeds) == 106 * 4
    assert len(set(seeds)) == len(seeds)
