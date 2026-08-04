"""増分1 modelのPhase B auto決定 (`#174` machineryのthin wrapper)。

`completion_auto.create_completion_auto_decision` は8 model / 597 group /
2,307 candidate へSHA凍結された公開release経路であり、in-place拡張できない。
本moduleは同じ関数へ「増分専用の期待値」だけを渡し、出力の
`role-baseline-decision-v1.json` / `role-quality-signals-v1.json` を
#174とbyte-schema同一 (protocol・policy version・F0 threshold・PASQA ranking・
write-once atomic publish) のまま161 groupで確定する。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from gaya_pipeline import completion_auto
from gaya_pipeline.completion_auto import (
    CompletionAutoDecisionError,
    CompletionAutoDecisionSummary,
)
from gaya_pipeline.increment_anchor import (
    IncrementAnchorError,
    load_machine_anchor_selection,
)
from gaya_pipeline.increment_plan import (
    INCREMENT_GROUPS,
    INCREMENT_MINIMUM_ELIGIBLE,
    IncrementPlan,
)


class IncrementAutoDecisionError(CompletionAutoDecisionError):
    pass


# 増分は「新規1 model x 161 line」だけを対象にする。candidate総数はtopupで
# 増減しうるため exact 値では固定せず、model policy の最小有効数だけを床とする。
EXPECTED_GROUP_COUNT = INCREMENT_GROUPS
MINIMUM_CANDIDATE_COUNT = INCREMENT_GROUPS * INCREMENT_MINIMUM_ELIGIBLE

IncrementAutoDecisionSummary = CompletionAutoDecisionSummary


def create_increment_auto_decision(
    *,
    plan: IncrementPlan,
    primary_run_ids: Sequence[str],
    topup_run_ids: Sequence[str],
    anchor_selection_path: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    pasqa_project_dir: Path,
    pasqa_model_dir: Path,
    output_dir: Path,
) -> IncrementAutoDecisionSummary:
    """増分planの161 groupを#174と同一protocolで機械確定する。"""

    if not isinstance(plan, IncrementPlan):
        raise IncrementAutoDecisionError(
            "increment auto decisionはIncrementPlanが必要です。",
        )
    if len(plan.targets) != EXPECTED_GROUP_COUNT:
        raise IncrementAutoDecisionError(
            f"increment planはexact {EXPECTED_GROUP_COUNT} targetが必要です: "
            f"actual={len(plan.targets)}",
        )
    increment_models = frozenset({plan.model})
    return completion_auto.create_completion_auto_decision(
        plan=plan,
        primary_run_ids=primary_run_ids,
        topup_run_ids=topup_run_ids,
        anchor_selection_path=anchor_selection_path,
        artifacts_dir=artifacts_dir,
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        pasqa_project_dir=pasqa_project_dir,
        pasqa_model_dir=pasqa_model_dir,
        output_dir=output_dir,
        expected_group_count=EXPECTED_GROUP_COUNT,
        expected_candidate_count=None,
        require_production=False,
        primary_models=increment_models,
        anchor_loader=machine_anchor_loader,
        anchor_bound_models=increment_models,
        minimum_candidate_count=MINIMUM_CANDIDATE_COUNT,
    )


def machine_anchor_loader(
    selection_path: Path,
    plan: Any,
) -> tuple[str, Mapping[tuple[str, str, str], str]]:
    """`completion_listen` の anchor loader 契約へmachine selectionを適合させる。"""

    try:
        return load_machine_anchor_selection(selection_path, plan=plan)
    except IncrementAnchorError as error:
        raise IncrementAutoDecisionError(
            f"machine anchor selectionが不正です: {error}",
        ) from error


__all__ = [
    "EXPECTED_GROUP_COUNT",
    "MINIMUM_CANDIDATE_COUNT",
    "IncrementAutoDecisionError",
    "IncrementAutoDecisionSummary",
    "create_increment_auto_decision",
    "machine_anchor_loader",
]
