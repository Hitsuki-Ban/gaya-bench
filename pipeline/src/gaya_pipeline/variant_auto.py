"""条件バリアント列のPhase B binding と機械決定 (#201)。

`increment_auto` と同じく `completion_auto.create_completion_auto_decision` の
thin wrapper。違うのは対象 group 数が列ごとに変わること (`--text` は14行、
`--ref` は147行) と、anchor 権限を持たない列があること。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from gaya_pipeline import completion_auto
from gaya_pipeline.completion_auto import (
    CompletionAutoDecisionError,
    CompletionAutoDecisionSummary,
)
from gaya_pipeline.variant_anchor import (
    VariantAnchorError,
    load_variant_anchor_selection,
)
from gaya_pipeline.variant_plan import VariantPlan, VariantPlanError


class VariantAutoDecisionError(CompletionAutoDecisionError):
    pass


VariantAutoDecisionSummary = CompletionAutoDecisionSummary

# anchor権限を持たない列 (`--ref` 全部 / `voxcpm2--text`) 用の sentinel。
NO_ANCHOR_SENTINEL = "0" * 64


def variant_anchor_loader(
    selection_path: Path,
    plan: Any,
) -> tuple[str, Mapping[tuple[str, str, str], str]]:
    try:
        return load_variant_anchor_selection(selection_path, plan=plan)
    except VariantAnchorError as error:
        raise VariantAutoDecisionError(
            f"variant anchor selectionが不正です: {error}",
        ) from error


def null_anchor_loader(
    _selection_path: Path,
    _plan: Any,
) -> tuple[str, Mapping[tuple[str, str, str], str]]:
    return NO_ANCHOR_SENTINEL, {}


def anchor_binding(
    plan: VariantPlan,
    anchor_selection_path: Path | None,
    *,
    fallback_path: Path,
) -> tuple[Path, Any, frozenset[str]]:
    """列の anchor 権限有無から (path, loader, bound models) を決める。"""

    required = plan.requires_anchor_authority()
    if required != (anchor_selection_path is not None):
        raise VariantAutoDecisionError(
            f"{plan.model} のanchor selection指定がplanと一致しません。",
        )
    if required:
        assert anchor_selection_path is not None
        return anchor_selection_path, variant_anchor_loader, frozenset({plan.model})
    return fallback_path, null_anchor_loader, frozenset()


def variant_generation_binding(
    *,
    plan: VariantPlan,
    scenarios_dir: Path,
    voices_dir: Path,
    anchor_selection_path: Path | None,
) -> tuple[str | None, dict[tuple[str, str], str]]:
    """1 runぶんのanchor digestと行ごとrole epochを確定する。"""

    from gaya_pipeline.completion_listen import (
        _load_completion_scenario_authority,
        expected_phase_b_role_epochs,
    )

    if plan.requires_anchor_authority():
        if anchor_selection_path is None:
            raise VariantAutoDecisionError(
                f"{plan.model} にはanchor selectionが必要です。",
            )
        anchor_sha, anchor_epochs = variant_anchor_loader(
            anchor_selection_path,
            plan,
        )
    else:
        if anchor_selection_path is not None:
            raise VariantAutoDecisionError(
                f"{plan.model} にanchor selectionは指定できません。",
            )
        anchor_sha, anchor_epochs = None, {}

    try:
        scenario_authority = _load_completion_scenario_authority(
            scenarios_dir=scenarios_dir,
            voices_dir=voices_dir,
            plan=plan,
        )
    except VariantPlanError as error:  # pragma: no cover - 防御的
        raise VariantAutoDecisionError(str(error)) from error
    expected = expected_phase_b_role_epochs(
        plan=plan,
        line_characters=scenario_authority.line_characters,
        anchor_selection_sha256=(
            NO_ANCHOR_SENTINEL if anchor_sha is None else anchor_sha
        ),
        selected_anchor_epochs=anchor_epochs,
    )
    model_targets = {target.identity for target in plan.targets_for_model(plan.model)}
    if not model_targets:
        raise VariantAutoDecisionError(f"Phase B生成対象外modelです: {plan.model}")
    return anchor_sha, {
        (identity[1], identity[2]): expected[identity]
        for identity in sorted(model_targets)
    }


def create_variant_auto_decision(
    *,
    plan: VariantPlan,
    primary_run_ids: Sequence[str],
    topup_run_ids: Sequence[str],
    anchor_selection_path: Path | None,
    fallback_anchor_path: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    pasqa_project_dir: Path,
    pasqa_model_dir: Path,
    output_dir: Path,
) -> VariantAutoDecisionSummary:
    """列の生成対象 group を #174 と同一protocolで機械確定する。"""

    if not isinstance(plan, VariantPlan):
        raise VariantAutoDecisionError(
            "variant auto decisionはVariantPlanが必要です。",
        )
    expected_group_count = len(plan.targets)
    if not expected_group_count:
        raise VariantAutoDecisionError("variant planに生成targetがありません。")
    minimum_candidate_count = (
        expected_group_count
        * plan.policy_for_model(plan.model).minimum_eligible_candidates
    )
    path, loader, bound = anchor_binding(
        plan,
        anchor_selection_path,
        fallback_path=fallback_anchor_path,
    )
    return completion_auto.create_completion_auto_decision(
        plan=plan,
        primary_run_ids=primary_run_ids,
        topup_run_ids=topup_run_ids,
        anchor_selection_path=path,
        artifacts_dir=artifacts_dir,
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        pasqa_project_dir=pasqa_project_dir,
        pasqa_model_dir=pasqa_model_dir,
        output_dir=output_dir,
        expected_group_count=expected_group_count,
        expected_candidate_count=None,
        require_production=False,
        primary_models=frozenset({plan.model}),
        anchor_loader=loader,
        anchor_bound_models=bound,
        minimum_candidate_count=minimum_candidate_count,
    )


__all__ = [
    "NO_ANCHOR_SENTINEL",
    "VariantAutoDecisionError",
    "VariantAutoDecisionSummary",
    "anchor_binding",
    "create_variant_auto_decision",
    "null_anchor_loader",
    "variant_anchor_loader",
    "variant_generation_binding",
]
