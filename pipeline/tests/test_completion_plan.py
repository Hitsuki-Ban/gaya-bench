from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable

import pytest

from gaya_pipeline.completion_plan import (
    ANCHOR_CANDIDATE_SET_SHA256,
    ANCHOR_SOURCE_PLAN_SHA256,
    BASE_MANIFEST_SHA256,
    IRODORI_MODEL,
    PROTOCOL,
    QWEN_MODEL,
    CompletionPlanError,
    build_frozen_plan_document,
    compute_completion_plan_id,
    load_completion_plan,
)
from gaya_pipeline.take_identity import canonical_json


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPOSITORY_ROOT / "data" / "manifest.json"
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"
VOICES_DIR = REPOSITORY_ROOT / "assets" / "voices"
ANCHOR_SELECTION_SHA256 = "a" * 64


def _plan_document() -> dict[str, Any]:
    return build_frozen_plan_document(
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
        anchor_selection_sha256=ANCHOR_SELECTION_SHA256,
    )


def _write_plan(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "plan.json"
    path.write_bytes(canonical_json(document).encode("utf-8"))
    return path


def _load(tmp_path: Path, document: dict[str, Any] | None = None):
    if document is None:
        document = _plan_document()
    path = _write_plan(tmp_path, document)
    return load_completion_plan(
        path,
        base_manifest_path=MANIFEST_PATH,
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
    )


def test_v2_planはanchor権威と597対象を固定する(tmp_path: Path) -> None:
    document = _plan_document()
    plan = _load(tmp_path, document)

    assert plan.plan_id == compute_completion_plan_id(document)
    assert plan.base_manifest_sha256 == BASE_MANIFEST_SHA256
    assert plan.anchor_source_plan_sha256 == ANCHOR_SOURCE_PLAN_SHA256
    assert plan.anchor_candidate_set_sha256 == ANCHOR_CANDIDATE_SET_SHA256
    assert plan.anchor_selection_sha256 == ANCHOR_SELECTION_SHA256
    assert plan.inherited_groups == 691
    assert plan.final_groups == 1_288
    assert len(plan.roles) == 58
    assert len(plan.model_policies) == 8
    assert len(plan.targets) == 597
    assert Counter(target.model for target in plan.targets) == {
        "aivisspeech-kohaku": 25,
        "chatterbox-multilingual-v3": 13,
        "cosyvoice3-0.5b-2512": 14,
        "gpt-sovits-v2-pro-plus": 37,
        IRODORI_MODEL: 161,
        QWEN_MODEL: 161,
        "supertonic-3": 25,
        "voxcpm2": 161,
    }
    assert len(plan.target_lines_for_model(QWEN_MODEL)) == 161
    assert len(plan.target_lines_for_model("voxcpm2")) == 161
    assert all(
        set(target) == {"model", "scenario", "line", "variant"}
        for target in document["phase_b"]["targets"]
    )


def test_model_policyはAivisだけ単発で他7modelはN4に固定する(
    tmp_path: Path,
) -> None:
    plan = _load(tmp_path)

    aivis = plan.policy_for_model("aivisspeech-kohaku")
    assert (
        aivis.takes,
        aivis.minimum_eligible_candidates,
        aivis.seed_policy,
        aivis.primary_seed_base,
    ) == (1, 1, "none", None)
    for model in sorted(set(plan.models) - {"aivisspeech-kohaku"}):
        policy = plan.policy_for_model(model)
        assert (
            policy.takes,
            policy.minimum_eligible_candidates,
            policy.seed_policy,
            policy.primary_seed_base,
        ) == (4, 3, "derived-sha256-v1", 104)

    with pytest.raises(CompletionPlanError, match="model policy"):
        plan.policy_for_model("unknown-model")


def test_planはcanonical_bytes以外を拒否する(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_bytes(
        canonical_json(_plan_document()).encode("utf-8") + b"\n",
    )

    with pytest.raises(CompletionPlanError, match="canonical bytes"):
        load_completion_plan(
            path,
            base_manifest_path=MANIFEST_PATH,
            scenarios_dir=SCENARIOS_DIR,
            voices_dir=VOICES_DIR,
        )


def test_v1_protocolは拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    document["protocol"] = "role-baseline-plan-v1"

    with pytest.raises(CompletionPlanError, match=PROTOCOL):
        _load(tmp_path, document)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda document: document.update({"fallback": True}), "exact contract"),
        (
            lambda document: document["anchor_authority"].update(
                {"phase_a": {}},
            ),
            "exact contract",
        ),
        (
            lambda document: document["phase_b"]["model_policies"][0].update(
                {"seed_base": 104},
            ),
            "exact contract",
        ),
        (
            lambda document: document["phase_b"]["targets"][0].update(
                {"source": "generate"},
            ),
            "exact contract",
        ),
    ],
)
def test_unknown_fieldは各階層でfail_fastする(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
    match: str,
) -> None:
    document = _plan_document()
    mutate(document)

    with pytest.raises(CompletionPlanError, match=match):
        _load(tmp_path, document)


def test_anchor_authorityの固定SHAと選択SHA形式を検証する(
    tmp_path: Path,
) -> None:
    source_changed = _plan_document()
    source_changed["anchor_authority"]["source_plan_sha256"] = "b" * 64
    with pytest.raises(CompletionPlanError, match="固定baseline"):
        _load(tmp_path, source_changed)

    selection_invalid = _plan_document()
    selection_invalid["anchor_authority"]["selection_sha256"] = "pending"
    with pytest.raises(CompletionPlanError, match="SHA-256"):
        _load(tmp_path, selection_invalid)

    with pytest.raises(CompletionPlanError, match="SHA-256"):
        build_frozen_plan_document(
            scenarios_dir=SCENARIOS_DIR,
            voices_dir=VOICES_DIR,
            anchor_selection_sha256="pending",
        )


def test_model_policyの不足順序改ざんを拒否する(tmp_path: Path) -> None:
    missing = _plan_document()
    missing["phase_b"]["model_policies"].pop()
    with pytest.raises(CompletionPlanError, match="8 model"):
        _load(tmp_path, missing)

    reordered = _plan_document()
    policies = reordered["phase_b"]["model_policies"]
    policies[0], policies[1] = policies[1], policies[0]
    with pytest.raises(CompletionPlanError, match="canonical"):
        _load(tmp_path, reordered)


def test_model_policyの値とmodelを固定する(tmp_path: Path) -> None:
    wrong_aivis = _plan_document()
    wrong_aivis["phase_b"]["model_policies"][0]["takes"] = 4
    with pytest.raises(CompletionPlanError, match="takes"):
        _load(tmp_path, wrong_aivis)

    unknown = _plan_document()
    unknown["phase_b"]["model_policies"][0]["model"] = "unknown-model"
    with pytest.raises(CompletionPlanError, match="不明"):
        _load(tmp_path, unknown)


def test_Phase_B対象の件数不足と追加を拒否する(tmp_path: Path) -> None:
    missing = _plan_document()
    missing["phase_b"]["targets"].pop()
    with pytest.raises(CompletionPlanError, match="597件"):
        _load(tmp_path, missing)

    extra = _plan_document()
    copied = dict(extra["phase_b"]["targets"][-1])
    copied["line"] = "not-a-line"
    extra["phase_b"]["targets"].append(copied)
    with pytest.raises(CompletionPlanError, match="597件"):
        _load(tmp_path, extra)


def test_Phase_B対象の固定集合とcanonical順を拒否する(tmp_path: Path) -> None:
    changed = _plan_document()
    changed["phase_b"]["targets"][-1]["line"] = "not-a-line"
    with pytest.raises(CompletionPlanError, match="固定対象"):
        _load(tmp_path, changed)

    reordered = _plan_document()
    targets = reordered["phase_b"]["targets"]
    targets[0], targets[1] = targets[1], targets[0]
    with pytest.raises(CompletionPlanError, match="canonical順"):
        _load(tmp_path, reordered)


def test_role_snapshotの改ざんを拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    document["roles"][0]["role"]["gender"] = "neutral"

    with pytest.raises(CompletionPlanError, match="role_identity_sha256"):
        _load(tmp_path, document)


def test_scenario_registryの改ざんを拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    document["sources"]["scenario_registry_sha256"] = "0" * 64

    with pytest.raises(CompletionPlanError, match="sources"):
        _load(tmp_path, document)
