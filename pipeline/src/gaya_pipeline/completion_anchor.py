from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import tempfile
import wave
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, Sequence

from gaya_pipeline.adapters.base import ModelProfile

from gaya_pipeline.completion_plan import (
    ANCHOR_CANDIDATE_SET_SHA256,
    ANCHOR_SOURCE_PLAN_SHA256,
    IRODORI_MODEL,
    QWEN_MODEL,
    CompletionPlan,
    RoleSnapshot,
)
from gaya_pipeline.take_identity import canonical_json

__all__ = [
    "AnchorReviewPlan",
    "AnchorTopupGenerationSummary",
    "AnchorTopupMergeSummary",
    "AnchorTopupPlanSummary",
    "AnchorTopupDraftSummary",
    "CompletionAnchorError",
    "RoleAnchorSelectionSummary",
    "RoleReviewBundleSummary",
    "SelectedRoleAnchor",
    "build_role_review_bundle_v2",
    "build_role_anchor_topup_plan",
    "build_role_anchor_topup_draft",
    "finalize_role_anchor_selection",
    "load_anchor_review_plan",
    "load_anchor_source_plan",
    "merge_role_anchor_topup",
    "resolve_selected_anchor",
    "run_role_anchor_topup_generation",
    "validate_anchor_selection",
]


class CompletionAnchorError(RuntimeError):
    pass


_REVIEW_FORMAT_VERSION = 2
_REVIEW_PROTOCOL = "role-review-v2"
_DECISION_FORMAT_VERSION = 2
_DECISION_PROTOCOL = "role-review-decision-v2"
_PHASE = "anchor"
_REVIEW_GROUP_COUNT = 106
_REVIEW_CANDIDATE_COUNT = 4
_REVIEW_MODELS = (IRODORI_MODEL, QWEN_MODEL)
_CANDIDATE_SET_FORMAT_VERSION = 1
_CANDIDATE_SET_PROTOCOL = "role-anchor-candidate-set-v1"
_TOPUP_PLAN_FORMAT_VERSION = 2
_TOPUP_PLAN_PROTOCOL = "role-anchor-topup-v2"
_TOPUP_RUN_FORMAT_VERSION = 2
_TOPUP_RUN_PROTOCOL = "role-anchor-topup-run-v2"
_DRAFT_FORMAT_VERSION = 2
_DRAFT_PROTOCOL = "role-review-draft-v2"
_ANCHOR_SEED_POLICY = "role-anchor-derived-sha256-v1"
_ANCHOR_SEED_BASE = 177
_CANDIDATE_SET_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_sha256",
    "runs",
    "groups",
}
_CANDIDATE_SET_GROUP_FIELDS = {
    "model",
    "model_revision",
    "scenario",
    "character",
    "role_identity_sha256",
    "role_epoch_sha256",
    "attempts",
    "candidates",
}
_CANDIDATE_SET_CANDIDATE_FIELDS = {
    "id",
    "model",
    "model_revision",
    "scenario",
    "character",
    "role_identity_sha256",
    "role_epoch_sha256",
    "attempt",
    "seed",
    "audio_path",
    "audio_sha256",
    "generation_input_sha256",
    "qc",
}
_TOPUP_PLAN_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_sha256",
    "candidate_set_sha256",
    "decision_sha256",
    "targets",
}
_TOPUP_PLAN_TARGET_FIELDS = {
    "model",
    "scenario",
    "character",
    "role_identity_sha256",
    "role_epoch_sha256",
    "attempts",
    "seeds",
}
_TOPUP_RUN_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_sha256",
    "candidate_set_sha256",
    "decision_sha256",
    "topup_plan_sha256",
    "run_id",
    "model",
    "model_revision",
    "attempts",
}
_TOPUP_RUN_ATTEMPT_FIELDS = {
    "model",
    "model_revision",
    "scenario",
    "character",
    "role_identity_sha256",
    "role_epoch_sha256",
    "attempt",
    "seed",
    "generation_input",
    "generation_input_sha256",
    "status",
    "anchor_id",
    "audio_path",
    "audio_sha256",
    "qc",
    "realized",
    "error",
}
_TOPUP_GENERATION_INPUT_FIELDS = {
    "protocol",
    "plan_sha256",
    "model",
    "model_revision",
    "scenario",
    "character",
    "role_identity_sha256",
    "role_epoch_sha256",
    "attempt",
    "seed",
    "anchor_text",
    "anchor_text_sha256",
    "conditioning",
}
_DRAFT_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "phase",
    "plan_sha256",
    "candidate_set_sha256",
    "current_group_id",
    "groups",
}
_QC_FIELDS = {"mechanical", "content", "notes"}
_REVIEW_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "phase",
    "plan_sha256",
    "candidate_set_sha256",
    "groups",
}
_REVIEW_GROUP_FIELDS = {
    "id",
    "model",
    "scenario",
    "character",
    "line",
    "anchor_text",
    "role_epoch_sha256",
    "role",
    "conditioning",
    "coverage",
    "comparison_required",
    "comparison_reasons",
    "candidate_ids",
    "candidates",
}
_REVIEW_CANDIDATE_FIELDS = {
    "id",
    "attempt",
    "seed",
    "audio_path",
    "audio_sha256",
    "qc",
}
_CONDITIONING_FIELDS = {"method", "summary"}
_COVERAGE_FIELDS = {"gender", "age", "archetype"}
_COMPARISON_REASONS = [
    "role_match",
    "same_role_voice_identity",
    "anchor_audio_quality",
]
_DECISION_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "phase",
    "plan_sha256",
    "candidate_set_sha256",
    "groups",
}
_APPLICABLE_RUBRIC_FIELDS = (
    "content",
    "prompt_leakage",
    "reading",
    "pitch_accent",
    "gender",
    "age",
    "archetype",
)
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_ANCHOR_PLAN_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "base",
    "sources",
    "models",
    "roles",
    "phase_a",
    "phase_b",
}
_ANCHOR_PLAN_MODEL_FIELDS = {"id", "revision"}
_ANCHOR_PLAN_ROLE_FIELDS = {
    "scenario",
    "character",
    "role",
    "reference_voice",
    "scene_setting",
    "role_identity_sha256",
}
_ANCHOR_PLAN_PHASE_A_FIELDS = {
    "takes",
    "minimum_eligible_candidates",
    "seed_policy",
    "seed_base",
    "anchor_texts",
    "targets",
}
_ANCHOR_PLAN_TEXT_FIELDS = {"model", "text", "sha256"}
_ANCHOR_PLAN_TARGET_FIELDS = {
    "model",
    "scenario",
    "character",
    "role_identity_sha256",
    "role_epoch_sha256",
}


_SELECTION_FORMAT_VERSION = 1
_SELECTION_PROTOCOL = "role-anchor-selection-v1"
_SELECTION_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_sha256",
    "candidate_set_sha256",
    "groups",
}
_SELECTION_GROUP_FIELDS = {
    "model",
    "model_revision",
    "scenario",
    "character",
    "role_identity",
    "role_identity_sha256",
    "review_role_epoch_sha256",
    "role_epoch_sha256",
    "anchor_id",
    "attempt",
    "seed",
    "audio_path",
    "audio_sha256",
    "anchor_text",
    "anchor_text_sha256",
    "decision",
    "decision_sha256",
}
_ROLE_IDENTITY_FIELDS = {
    "scenario",
    "character",
    "role",
    "reference_voice",
    "scene_setting",
}
_ROLE_FIELDS = {
    "name",
    "kind",
    "gender",
    "age",
    "archetype",
    "voice",
    "personality",
}
_DECISION_FIELDS = {
    "id",
    "model",
    "scenario",
    "character",
    "line",
    "role_epoch_sha256",
    "group_sha256",
    "heard_candidate_ids",
    "selected_candidate_id",
    "no_usable_candidate",
    "rubric",
    "confirmed",
}
_DECISION_RUBRIC_FIELDS = {
    "content",
    "prompt_leakage",
    "reading",
    "pitch_accent",
    "gender",
    "age",
    "archetype",
    "voice_identity",
    "delivery",
    "naturalness_quality",
    "notes",
}
_RUBRIC_RESULT_FIELDS = (
    "content",
    "prompt_leakage",
    "reading",
    "pitch_accent",
    "gender",
    "age",
    "archetype",
    "voice_identity",
    "delivery",
)
_ALLOWED_RUBRIC_RESULTS = {"pass", "fail", "not_applicable"}
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class AnchorReviewPlan:
    plan_id: str
    anchor_source_plan_sha256: str
    anchor_candidate_set_sha256: str
    models: Mapping[str, str]
    roles: tuple[RoleSnapshot, ...]

    def role(self, scenario: str, character: str) -> RoleSnapshot:
        matches = [
            role
            for role in self.roles
            if (role.scenario, role.character) == (scenario, character)
        ]
        if len(matches) != 1:
            raise CompletionAnchorError(
                f"Anchor plan roleが一意ではありません: {scenario}/{character}",
            )
        return matches[0]


class RoleAnchorGenerator(Protocol):
    profile: ModelProfile

    def role_anchor_generation_input(
        self,
        role: RoleSnapshot,
    ) -> Mapping[str, Any]: ...

    def generate_role_anchor(
        self,
        role: RoleSnapshot,
        *,
        seed: int,
        output_wav: Path,
    ) -> Mapping[str, Any]: ...

    def close_role_anchor_generation(self) -> None: ...


@dataclass(frozen=True)
class AnchorTopupPlanSummary:
    path: Path
    target_count: int
    attempt_count: int


@dataclass(frozen=True)
class AnchorTopupGenerationSummary:
    run_id: str
    ledger_path: Path
    eligible_count: int
    rejected_count: int
    failed_count: int


@dataclass(frozen=True)
class AnchorTopupMergeSummary:
    path: Path
    candidate_set_sha256: str
    replaced_group_count: int
    candidate_count: int


@dataclass(frozen=True)
class AnchorTopupDraftSummary:
    path: Path
    inherited_count: int
    reset_count: int


@dataclass(frozen=True)
class RoleReviewBundleSummary:
    output_dir: Path
    review_path: Path
    review_sha256: str
    group_count: int
    candidate_count: int


@dataclass(frozen=True)
class RoleAnchorSelectionSummary:
    output_dir: Path
    selection_path: Path
    selection_sha256: str
    selected_count: int


@dataclass(frozen=True)
class SelectedRoleAnchor:
    selection_sha256: str
    plan_sha256: str
    candidate_set_sha256: str
    model: str
    model_revision: str
    scenario: str
    character: str
    role_identity_sha256: str
    role_epoch_sha256: str
    anchor_id: str
    attempt: int
    seed: int
    audio_path: Path
    audio_sha256: str
    anchor_text: str
    anchor_text_sha256: str
    decision_sha256: str

    def receipt(self) -> dict[str, Any]:
        return {
            "anchor_selection_sha256": self.selection_sha256,
            "anchor_plan_sha256": self.plan_sha256,
            "anchor_candidate_set_sha256": self.candidate_set_sha256,
            "anchor_id": self.anchor_id,
            "anchor_attempt": self.attempt,
            "anchor_seed": self.seed,
            "anchor_audio_sha256": self.audio_sha256,
            "anchor_text_sha256": self.anchor_text_sha256,
            "anchor_decision_sha256": self.decision_sha256,
            "role_identity_sha256": self.role_identity_sha256,
            "role_epoch_sha256": self.role_epoch_sha256,
        }


def load_anchor_source_plan(
    *,
    plan_path: Path,
) -> AnchorReviewPlan:
    """現在凍結済みのPhase A source planを厳密に読む。"""

    _require_absolute(plan_path, "Anchor source plan")
    plan_raw, document = _read_canonical_json(plan_path, "Anchor source plan")
    plan_sha256 = hashlib.sha256(plan_raw).hexdigest()
    if plan_sha256 != ANCHOR_SOURCE_PLAN_SHA256:
        raise CompletionAnchorError(
            "Anchor source plan SHAが現在の固定authorityと一致しません。",
        )
    candidate_set_sha256 = ANCHOR_CANDIDATE_SET_SHA256

    root = _exact(document, _ANCHOR_PLAN_ROOT_FIELDS, "Anchor source plan")
    if root["format_version"] != 1 or root["protocol"] != "role-baseline-plan-v1":
        raise CompletionAnchorError(
            "Anchor source planはformat_version=1 / role-baseline-plan-v1が必要です。",
        )
    models_value = root["models"]
    if not isinstance(models_value, list) or not models_value:
        raise CompletionAnchorError("Anchor source plan.modelsが不正です。")
    models: dict[str, str] = {}
    model_documents: list[dict[str, str]] = []
    for index, value in enumerate(models_value):
        field = f"Anchor source plan.models[{index}]"
        item = _exact(value, _ANCHOR_PLAN_MODEL_FIELDS, field)
        model = _path_segment(item["id"], f"{field}.id")
        if model in models:
            raise CompletionAnchorError("Anchor source plan modelが重複しています。")
        revision = _trimmed_text(item["revision"], f"{field}.revision")
        models[model] = revision
        model_documents.append({"id": model, "revision": revision})
    if model_documents != sorted(model_documents, key=lambda item: item["id"]):
        raise CompletionAnchorError(
            "Anchor source plan.modelsはcanonical順が必要です。",
        )
    if any(model not in models for model in _REVIEW_MODELS):
        raise CompletionAnchorError(
            "Anchor source planにreview対象2 modelがありません。",
        )

    roles_value = root["roles"]
    if not isinstance(roles_value, list) or len(roles_value) != 58:
        raise CompletionAnchorError(
            "Anchor source plan.rolesはexactly 58件が必要です。"
        )
    roles: list[RoleSnapshot] = []
    role_identities: set[tuple[str, str]] = set()
    for index, value in enumerate(roles_value):
        field = f"Anchor source plan.roles[{index}]"
        item = _exact(value, _ANCHOR_PLAN_ROLE_FIELDS, field)
        scenario = _safe_segment(item["scenario"], f"{field}.scenario")
        character = _safe_segment(item["character"], f"{field}.character")
        identity = (scenario, character)
        if identity in role_identities:
            raise CompletionAnchorError("Anchor source plan roleが重複しています。")
        role_identities.add(identity)
        role = _validate_review_role(item["role"], f"{field}.role")
        reference_voice = item["reference_voice"]
        if reference_voice is not None:
            reference_voice = _path_segment(
                reference_voice,
                f"{field}.reference_voice",
            )
        scene_setting = _trimmed_text(
            item["scene_setting"],
            f"{field}.scene_setting",
        )
        role_identity_document = {
            "scenario": scenario,
            "character": character,
            "role": role,
            "reference_voice": reference_voice,
            "scene_setting": scene_setting,
        }
        role_identity_sha256 = _sha256(
            item["role_identity_sha256"],
            f"{field}.role_identity_sha256",
        )
        if role_identity_sha256 != _canonical_sha256(role_identity_document):
            raise CompletionAnchorError(f"{field}.role identity SHAが不正です。")
        roles.append(
            RoleSnapshot(
                scenario=scenario,
                character=character,
                role=role,
                reference_voice=reference_voice,
                scene_setting=scene_setting,
                role_identity_sha256=role_identity_sha256,
            ),
        )
    if roles != sorted(roles, key=lambda role: (role.scenario, role.character)):
        raise CompletionAnchorError(
            "Anchor source plan.rolesはcanonical順が必要です。",
        )

    phase_a = _exact(
        root["phase_a"],
        _ANCHOR_PLAN_PHASE_A_FIELDS,
        "Anchor source plan.phase_a",
    )
    if (
        phase_a["takes"] != 4
        or phase_a["minimum_eligible_candidates"] != 3
        or phase_a["seed_policy"] != "role-anchor-derived-sha256-v1"
        or phase_a["seed_base"] != 177
    ):
        raise CompletionAnchorError("Anchor source plan.phase_a policyが不正です。")
    anchor_texts = _anchor_texts()
    expected_anchor_text_documents = [
        {
            "model": model,
            "text": anchor_texts[model],
            "sha256": hashlib.sha256(anchor_texts[model].encode("utf-8")).hexdigest(),
        }
        for model in _REVIEW_MODELS
    ]
    anchor_text_values = phase_a["anchor_texts"]
    if not isinstance(anchor_text_values, list):
        raise CompletionAnchorError(
            "Anchor source plan.phase_a.anchor_textsが不正です。"
        )
    normalized_anchor_texts: list[dict[str, str]] = []
    for index, value in enumerate(anchor_text_values):
        field = f"Anchor source plan.phase_a.anchor_texts[{index}]"
        item = _exact(value, _ANCHOR_PLAN_TEXT_FIELDS, field)
        normalized_anchor_texts.append(
            {
                "model": _anchor_model(item["model"], f"{field}.model"),
                "text": _trimmed_text(item["text"], f"{field}.text"),
                "sha256": _sha256(item["sha256"], f"{field}.sha256"),
            },
        )
    if normalized_anchor_texts != expected_anchor_text_documents:
        raise CompletionAnchorError(
            "Anchor source plan.phase_a.anchor_textsが固定値と一致しません。",
        )

    target_values = phase_a["targets"]
    if not isinstance(target_values, list) or len(target_values) != 106:
        raise CompletionAnchorError(
            "Anchor source plan.phase_a.targetsはexactly 106件が必要です。",
        )
    targets: list[dict[str, str]] = []
    plan = AnchorReviewPlan(
        plan_id=plan_sha256,
        anchor_source_plan_sha256=plan_sha256,
        anchor_candidate_set_sha256=candidate_set_sha256,
        models=models,
        roles=tuple(roles),
    )
    for index, value in enumerate(target_values):
        field = f"Anchor source plan.phase_a.targets[{index}]"
        item = _exact(value, _ANCHOR_PLAN_TARGET_FIELDS, field)
        model = _anchor_model(item["model"], f"{field}.model")
        scenario = _safe_segment(item["scenario"], f"{field}.scenario")
        character = _safe_segment(item["character"], f"{field}.character")
        role = plan.role(scenario, character)
        if role.reference_voice is not None:
            raise CompletionAnchorError(f"{field}はno-reference roleが必要です。")
        role_sha256 = _sha256(
            item["role_identity_sha256"],
            f"{field}.role_identity_sha256",
        )
        role_epoch_sha256 = _sha256(
            item["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        )
        expected_epoch = _canonical_sha256(
            {
                "model": model,
                "model_revision": models[model],
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": role.role_identity_sha256,
                "anchor_text_sha256": hashlib.sha256(
                    anchor_texts[model].encode("utf-8"),
                ).hexdigest(),
            },
        )
        if (
            role_sha256 != role.role_identity_sha256
            or role_epoch_sha256 != expected_epoch
        ):
            raise CompletionAnchorError(f"{field}のrole identity/epochが不正です。")
        targets.append(
            {
                "model": model,
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": role_sha256,
                "role_epoch_sha256": role_epoch_sha256,
            },
        )
    expected_target_identities = {
        (model, role.scenario, role.character)
        for model in _REVIEW_MODELS
        for role in roles
        if role.reference_voice is None
    }
    if (
        targets
        != sorted(
            targets,
            key=lambda item: (item["model"], item["scenario"], item["character"]),
        )
        or {
            (target["model"], target["scenario"], target["character"])
            for target in targets
        }
        != expected_target_identities
    ):
        raise CompletionAnchorError(
            "Anchor source plan.phase_a.targetsが2 model × 53 roleと一致しません。",
        )
    return plan


def load_anchor_review_plan(
    *,
    plan_path: Path,
    candidate_set_path: Path,
) -> AnchorReviewPlan:
    """Phase A source planと実candidate setを同時に厳密検証する。"""

    _require_absolute(candidate_set_path, "anchor candidate set")
    plan = load_anchor_source_plan(plan_path=plan_path)
    candidate_raw, _candidate_document = _read_canonical_json(
        candidate_set_path,
        "anchor candidate set",
    )
    candidate_set_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    if candidate_set_sha256 != plan.anchor_candidate_set_sha256:
        raise CompletionAnchorError(
            "anchor candidate set SHAが現在の固定authorityと一致しません。",
        )
    return plan


def build_role_anchor_topup_plan(
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    candidate_set_path: Path,
    decision_path: Path,
    output_path: Path,
) -> AnchorTopupPlanSummary:
    """確定decisionとcurrent candidateから次のN4 attemptを固定する。"""

    for path, label in (
        (candidate_set_path, "anchor candidate set"),
        (decision_path, "role review decision"),
        (output_path, "anchor topup plan output"),
    ):
        _require_absolute(path, label)
    candidate_set, candidate_sha256, decision, decision_sha256 = (
        _load_topup_source_authority(
            plan=plan,
            candidate_set_path=candidate_set_path,
            decision_path=decision_path,
            expected_candidate_set_sha256=plan.anchor_candidate_set_sha256,
        )
    )
    candidate_groups = {
        (group["model"], group["scenario"], group["character"]): group
        for group in candidate_set["groups"]
    }
    targets: list[dict[str, Any]] = []
    for group in decision["groups"]:
        if not (
            group["no_usable_candidate"]
            or group["rubric"]["gender"] != "pass"
        ):
            continue
        identity = (group["model"], group["scenario"], group["character"])
        candidate_group = candidate_groups[identity]
        attempts = _next_anchor_topup_attempts(
            candidate_group["attempts"],
            f"anchor topup source group {'/'.join(identity)}",
        )
        seeds = [
            _derive_anchor_seed(
                plan_sha256=plan.anchor_source_plan_sha256,
                model=identity[0],
                scenario=identity[1],
                character=identity[2],
                attempt=attempt,
            )
            for attempt in attempts
        ]
        targets.append(
            {
                "model": identity[0],
                "scenario": identity[1],
                "character": identity[2],
                "role_identity_sha256": candidate_group["role_identity_sha256"],
                "role_epoch_sha256": candidate_group["role_epoch_sha256"],
                "attempts": attempts,
                "seeds": seeds,
            },
        )
    if not targets:
        raise CompletionAnchorError("anchor topup対象はありません。")
    topup = _validate_anchor_topup_plan(
        {
            "format_version": _TOPUP_PLAN_FORMAT_VERSION,
            "protocol": _TOPUP_PLAN_PROTOCOL,
            "plan_sha256": plan.anchor_source_plan_sha256,
            "candidate_set_sha256": candidate_sha256,
            "decision_sha256": decision_sha256,
            "targets": targets,
        },
        plan=plan,
        candidate_set=candidate_set,
        decision=decision,
        decision_sha256=decision_sha256,
    )
    _write_canonical_new(output_path, topup)
    return AnchorTopupPlanSummary(
        path=output_path,
        target_count=len(targets),
        attempt_count=sum(len(target["attempts"]) for target in targets),
    )


def run_role_anchor_topup_generation(
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    candidate_set_path: Path,
    decision_path: Path,
    topup_plan_path: Path,
    model_id: str,
    run_id: str,
    artifacts_dir: Path,
    generator: RoleAnchorGenerator | None = None,
) -> AnchorTopupGenerationSummary:
    """topup planの単一model分を独立runとして生成する。"""

    for path, label in (
        (candidate_set_path, "anchor candidate set"),
        (decision_path, "role review decision"),
        (topup_plan_path, "anchor topup plan"),
        (artifacts_dir, "anchor artifacts"),
    ):
        _require_absolute(path, label)
    run_id = _path_segment(run_id, "anchor topup run id")
    (
        topup_raw,
        topup,
        _candidate_set,
        candidate_sha256,
        _decision,
        decision_sha256,
    ) = _load_topup_replay_authority(
        plan=plan,
        candidate_set_path=candidate_set_path,
        decision_path=decision_path,
        topup_plan_path=topup_plan_path,
    )
    topup_sha256 = hashlib.sha256(topup_raw).hexdigest()
    targets = [target for target in topup["targets"] if target["model"] == model_id]
    if not targets:
        raise CompletionAnchorError(
            f"anchor topup planにmodel対象がありません: {model_id}",
        )
    revision = _model_revision(plan, model_id)
    effective_generator = (
        _create_anchor_generator(model_id) if generator is None else generator
    )
    if (
        effective_generator.profile.id != model_id
        or effective_generator.profile.version != revision
    ):
        raise CompletionAnchorError(
            "anchor generatorのmodel/revisionがauthority planと一致しません。",
        )
    run_root = artifacts_dir / "role-anchors" / "runs" / run_id
    if run_root.exists():
        raise CompletionAnchorError(
            f"anchor topup run directoryは新規である必要があります: {run_root}",
        )
    pending = run_root.with_name(f".{run_root.name}.pending")
    if pending.exists():
        raise CompletionAnchorError(
            f"anchor topup pending directoryが既に存在します: {pending}",
        )
    pending.mkdir(parents=True)
    records: list[dict[str, Any]] = []
    try:
        try:
            for target in targets:
                role = plan.role(target["scenario"], target["character"])
                for attempt, seed in zip(
                    target["attempts"],
                    target["seeds"],
                    strict=True,
                ):
                    generation_input = _topup_generation_input(
                        plan=plan,
                        target=target,
                        role=role,
                        attempt=attempt,
                        seed=seed,
                        generator=effective_generator,
                    )
                    generation_input_sha256 = _canonical_sha256(generation_input)
                    relative_base = PurePosixPath(
                        "role-anchors",
                        "runs",
                        run_id,
                        model_id,
                        target["scenario"],
                        target["character"],
                        f"attempt-{attempt:04d}",
                    )
                    pending_base = pending.joinpath(
                        model_id,
                        target["scenario"],
                        target["character"],
                        f"attempt-{attempt:04d}",
                    )
                    output_wav = pending_base.with_suffix(".wav")
                    sidecar_path = pending_base.with_suffix(".json")
                    output_wav.parent.mkdir(parents=True, exist_ok=True)
                    realized = dict(
                        effective_generator.generate_role_anchor(
                            role,
                            seed=seed,
                            output_wav=output_wav,
                        ),
                    )
                    canonical_json(realized)
                    if not output_wav.is_file():
                        raise CompletionAnchorError(
                            "anchor generatorがWAVを書き込みませんでした: "
                            f"{output_wav}",
                        )
                    audio_sha256 = _sha256_file(output_wav)
                    qc = _mechanical_qc(output_wav)
                    anchor_id = _canonical_sha256(
                        {
                            "protocol": "role-anchor-identity-v1",
                            "plan_sha256": plan.anchor_source_plan_sha256,
                            "model": model_id,
                            "model_revision": revision,
                            "scenario": target["scenario"],
                            "character": target["character"],
                            "role_identity_sha256": target[
                                "role_identity_sha256"
                            ],
                            "role_epoch_sha256": target["role_epoch_sha256"],
                            "attempt": attempt,
                            "seed": seed,
                            "generation_input_sha256": generation_input_sha256,
                            "audio_sha256": audio_sha256,
                        },
                    )
                    status = "eligible" if qc["mechanical"] == "pass" else "rejected"
                    record = {
                        "model": model_id,
                        "model_revision": revision,
                        "scenario": target["scenario"],
                        "character": target["character"],
                        "role_identity_sha256": target["role_identity_sha256"],
                        "role_epoch_sha256": target["role_epoch_sha256"],
                        "attempt": attempt,
                        "seed": seed,
                        "generation_input": generation_input,
                        "generation_input_sha256": generation_input_sha256,
                        "status": status,
                        "anchor_id": anchor_id,
                        "audio_path": f"{relative_base.as_posix()}.wav",
                        "audio_sha256": audio_sha256,
                        "qc": qc,
                        "realized": realized,
                        "error": None,
                    }
                    _write_canonical_new(sidecar_path, record)
                    records.append(record)
        finally:
            effective_generator.close_role_anchor_generation()
        records.sort(
            key=lambda item: (
                item["model"],
                item["scenario"],
                item["character"],
                item["attempt"],
            ),
        )
        ledger = _validate_anchor_topup_run(
            {
                "format_version": _TOPUP_RUN_FORMAT_VERSION,
                "protocol": _TOPUP_RUN_PROTOCOL,
                "plan_sha256": plan.anchor_source_plan_sha256,
                "candidate_set_sha256": candidate_sha256,
                "decision_sha256": decision_sha256,
                "topup_plan_sha256": topup_sha256,
                "run_id": run_id,
                "model": model_id,
                "model_revision": revision,
                "attempts": records,
            },
            plan=plan,
            topup=topup,
            candidate_set_sha256=candidate_sha256,
            decision_sha256=decision_sha256,
            topup_plan_sha256=topup_sha256,
        )
        _write_canonical_new(pending / "ledger.json", ledger)
        _publish_pending_directory(pending, run_root)
    except CompletionAnchorError:
        shutil.rmtree(pending, ignore_errors=True)
        raise
    except Exception as error:
        shutil.rmtree(pending, ignore_errors=True)
        raise CompletionAnchorError(
            f"anchor topup generationに失敗しました: {error}",
        ) from error
    ledger_path = run_root / "ledger.json"
    return AnchorTopupGenerationSummary(
        run_id=run_id,
        ledger_path=ledger_path,
        eligible_count=sum(item["status"] == "eligible" for item in records),
        rejected_count=sum(item["status"] == "rejected" for item in records),
        failed_count=sum(item["status"] == "failed" for item in records),
    )


def merge_role_anchor_topup(
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    candidate_set_path: Path,
    decision_path: Path,
    topup_plan_path: Path,
    run_ids: Sequence[str],
    artifacts_dir: Path,
    output_path: Path,
) -> AnchorTopupMergeSummary:
    """topup対象groupだけをN4で整組置換したcandidate setを作る。"""

    for path, label in (
        (candidate_set_path, "anchor candidate set"),
        (decision_path, "role review decision"),
        (topup_plan_path, "anchor topup plan"),
        (artifacts_dir, "anchor artifacts"),
        (output_path, "merged anchor candidate set output"),
    ):
        _require_absolute(path, label)
    if not run_ids or len(run_ids) != len(set(run_ids)):
        raise CompletionAnchorError("topup run_idsは重複のない1件以上が必要です。")
    normalized_run_ids = sorted(
        _path_segment(run_id, "anchor topup run id") for run_id in run_ids
    )
    (
        topup_raw,
        topup,
        candidate_set,
        candidate_sha256,
        _decision,
        decision_sha256,
    ) = _load_topup_replay_authority(
        plan=plan,
        candidate_set_path=candidate_set_path,
        decision_path=decision_path,
        topup_plan_path=topup_plan_path,
    )
    _verify_anchor_candidate_set_audio(
        candidate_set=candidate_set,
        artifacts_dir=artifacts_dir,
        label="source anchor candidate audio",
    )
    topup_sha256 = hashlib.sha256(topup_raw).hexdigest()
    attempts_by_identity: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    run_models: set[str] = set()
    for run_id in normalized_run_ids:
        ledger_path = (
            artifacts_dir / "role-anchors" / "runs" / run_id / "ledger.json"
        )
        _raw, ledger_document = _read_canonical_json(
            ledger_path,
            "anchor topup run ledger",
        )
        ledger = _validate_anchor_topup_run(
            ledger_document,
            plan=plan,
            topup=topup,
            candidate_set_sha256=candidate_sha256,
            decision_sha256=decision_sha256,
            topup_plan_sha256=topup_sha256,
        )
        if ledger["run_id"] != run_id or ledger["model"] in run_models:
            raise CompletionAnchorError(
                "topup run idまたはmodelがrun集合内で重複しています。",
            )
        run_models.add(ledger["model"])
        for attempt in ledger["attempts"]:
            if attempt["status"] != "eligible":
                raise CompletionAnchorError(
                    "merged candidate setにはtopup全attemptのeligibleが必要です。",
                )
            assert attempt["audio_path"] is not None
            assert attempt["audio_sha256"] is not None
            _verify_file_sha256(
                _relative_child(
                    artifacts_dir,
                    attempt["audio_path"],
                    "anchor topup audio",
                ),
                attempt["audio_sha256"],
                "anchor topup audio",
            )
            identity = (
                attempt["model"],
                attempt["scenario"],
                attempt["character"],
            )
            attempts_by_identity.setdefault(identity, []).append(attempt)
    expected_models = {target["model"] for target in topup["targets"]}
    if run_models != expected_models:
        raise CompletionAnchorError(
            "topup run集合は対象modelとexact一致する必要があります。",
        )
    target_identities = {
        (target["model"], target["scenario"], target["character"])
        for target in topup["targets"]
    }
    if set(attempts_by_identity) != target_identities:
        raise CompletionAnchorError(
            "topup run集合は対象groupとexact一致する必要があります。",
        )
    groups: list[dict[str, Any]] = []
    for source_group in candidate_set["groups"]:
        identity = (
            source_group["model"],
            source_group["scenario"],
            source_group["character"],
        )
        if identity not in target_identities:
            groups.append(json.loads(canonical_json(source_group)))
            continue
        attempts = sorted(
            attempts_by_identity[identity],
            key=lambda item: item["attempt"],
        )
        groups.append(
            {
                **{
                    key: source_group[key]
                    for key in (
                        "model",
                        "model_revision",
                        "scenario",
                        "character",
                        "role_identity_sha256",
                        "role_epoch_sha256",
                    )
                },
                "attempts": [attempt["attempt"] for attempt in attempts],
                "candidates": [_candidate_from_topup_attempt(item) for item in attempts],
            },
        )
    merged = {
        "format_version": _CANDIDATE_SET_FORMAT_VERSION,
        "protocol": _CANDIDATE_SET_PROTOCOL,
        "plan_sha256": plan.anchor_source_plan_sha256,
        "runs": sorted([*candidate_set["runs"], *normalized_run_ids]),
        "groups": groups,
    }
    merged_sha256 = _canonical_sha256(merged)
    normalized = _validate_anchor_candidate_set_v1(
        merged,
        plan=plan,
        candidate_set_sha256=merged_sha256,
        expected_candidate_set_sha256=merged_sha256,
    )
    _verify_anchor_candidate_set_audio(
        candidate_set=normalized,
        artifacts_dir=artifacts_dir,
        label="merged anchor candidate audio",
    )
    _write_canonical_new(output_path, normalized)
    return AnchorTopupMergeSummary(
        path=output_path,
        candidate_set_sha256=merged_sha256,
        replaced_group_count=len(target_identities),
        candidate_count=sum(len(group["candidates"]) for group in groups),
    )


def build_role_anchor_topup_draft(
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    source_candidate_set_path: Path,
    decision_path: Path,
    topup_plan_path: Path,
    merged_candidate_set_path: Path,
    merged_bundle_dir: Path,
    output_path: Path,
) -> AnchorTopupDraftSummary:
    """非対象判断を継承し、topup対象だけを未確認へ戻すdraftを作る。"""

    for path, label in (
        (source_candidate_set_path, "source anchor candidate set"),
        (decision_path, "source role review decision"),
        (topup_plan_path, "anchor topup plan"),
        (merged_candidate_set_path, "merged anchor candidate set"),
        (merged_bundle_dir, "merged role review bundle"),
        (output_path, "role review inherited draft output"),
    ):
        _require_absolute(path, label)
    (
        _topup_raw,
        topup,
        source_set,
        _source_sha256,
        decision,
        _decision_sha256,
    ) = _load_topup_replay_authority(
        plan=plan,
        candidate_set_path=source_candidate_set_path,
        decision_path=decision_path,
        topup_plan_path=topup_plan_path,
    )
    merged_raw, merged_document = _read_canonical_json(
        merged_candidate_set_path,
        "merged anchor candidate set",
    )
    merged_sha256 = hashlib.sha256(merged_raw).hexdigest()
    merged_set = _validate_anchor_candidate_set_v1(
        merged_document,
        plan=plan,
        candidate_set_sha256=merged_sha256,
        expected_candidate_set_sha256=merged_sha256,
    )
    targets_by_identity = {
        (target["model"], target["scenario"], target["character"]): target
        for target in topup["targets"]
    }
    target_identities = set(targets_by_identity)
    source_groups = {
        (group["model"], group["scenario"], group["character"]): group
        for group in source_set["groups"]
    }
    merged_groups = {
        (group["model"], group["scenario"], group["character"]): group
        for group in merged_set["groups"]
    }
    for identity, source_group in source_groups.items():
        merged_group = merged_groups[identity]
        if identity not in target_identities and merged_group != source_group:
            raise CompletionAnchorError(
                "topup非対象candidate groupのobjectが変更されています: "
                f"{'/'.join(identity)}",
            )
        if (
            identity in target_identities
            and merged_group["attempts"]
            != targets_by_identity[identity]["attempts"]
        ):
            raise CompletionAnchorError(
                "topup対象candidate groupがplan attemptへ整組置換されていません: "
                f"{'/'.join(identity)}",
            )
    _assert_exact_directory_files(
        merged_bundle_dir,
        {
            "role-review-v2.json",
            *(
                f"audio/{candidate['id']}.wav"
                for group in merged_set["groups"]
                for candidate in group["candidates"]
            ),
        },
        "merged role review bundle",
    )
    _bundle_raw, bundle_document = _read_canonical_json(
        merged_bundle_dir / "role-review-v2.json",
        "merged role review bundle",
    )
    bundle = _validate_role_review_bundle_v2(
        bundle_document,
        plan=plan,
        candidate_set=merged_set,
        candidate_set_sha256=merged_sha256,
    )
    for group in bundle["groups"]:
        for candidate in group["candidates"]:
            _verify_file_sha256(
                _relative_child(
                    merged_bundle_dir,
                    candidate["audio_path"],
                    "merged role review audio",
                ),
                candidate["audio_sha256"],
                "merged role review audio",
            )
    decision_groups = {
        (group["model"], group["scenario"], group["character"]): group
        for group in decision["groups"]
    }
    draft_groups: list[dict[str, Any]] = []
    target_group_ids: list[str] = []
    empty_rubric = {
        **{key: None for key in _APPLICABLE_RUBRIC_FIELDS},
        "voice_identity": None,
        "delivery": None,
        "naturalness_quality": None,
        "notes": "",
    }
    for review_group in bundle["groups"]:
        identity = (
            review_group["model"],
            review_group["scenario"],
            review_group["character"],
        )
        source_decision = decision_groups[identity]
        new_group_sha256 = _canonical_sha256(review_group)
        if identity not in target_identities:
            if new_group_sha256 != source_decision["group_sha256"]:
                raise CompletionAnchorError(
                    "topup非対象review group hashが変更されています: "
                    f"{'/'.join(identity)}",
                )
            draft_groups.append(json.loads(canonical_json(source_decision)))
            continue
        target_group_ids.append(review_group["id"])
        draft_groups.append(
            {
                "id": review_group["id"],
                "model": review_group["model"],
                "scenario": review_group["scenario"],
                "character": review_group["character"],
                "line": None,
                "role_epoch_sha256": review_group["role_epoch_sha256"],
                "group_sha256": new_group_sha256,
                "heard_candidate_ids": [],
                "selected_candidate_id": None,
                "no_usable_candidate": False,
                "rubric": dict(empty_rubric),
                "confirmed": False,
            },
        )
    if len(target_group_ids) != len(target_identities):
        raise CompletionAnchorError("inherited draftにtopup targetの欠落があります。")
    draft = _validate_role_review_draft_v2(
        {
            "format_version": _DRAFT_FORMAT_VERSION,
            "protocol": _DRAFT_PROTOCOL,
            "phase": _PHASE,
            "plan_sha256": plan.anchor_source_plan_sha256,
            "candidate_set_sha256": merged_sha256,
            "current_group_id": target_group_ids[0],
            "groups": draft_groups,
        },
        bundle=bundle,
        target_identities=target_identities,
    )
    _write_canonical_new(output_path, draft)
    return AnchorTopupDraftSummary(
        path=output_path,
        inherited_count=len(draft_groups) - len(target_group_ids),
        reset_count=len(target_group_ids),
    )


def build_role_review_bundle_v2(
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    candidate_set_path: Path,
    artifacts_dir: Path,
    output_dir: Path,
) -> RoleReviewBundleSummary:
    """固定candidate setから自己完結したAnchor聴取bundleを作る。"""

    for path, label in (
        (candidate_set_path, "anchor candidate set"),
        (artifacts_dir, "anchor artifacts"),
        (output_dir, "role review output"),
    ):
        _require_absolute(path, label)
    _require_new_output_directory(output_dir, "role review output")

    candidate_raw, candidate_document = _read_canonical_json(
        candidate_set_path,
        "anchor candidate set",
    )
    candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    candidate_set = _validate_anchor_candidate_set_v1(
        candidate_document,
        plan=plan,
        candidate_set_sha256=candidate_sha256,
        expected_candidate_set_sha256=plan.anchor_candidate_set_sha256,
    )

    review_groups: list[dict[str, Any]] = []
    copy_jobs: list[tuple[Path, str, str]] = []
    for group in candidate_set["groups"]:
        review_group = _role_review_group_v2(plan=plan, group=group)
        review_groups.append(review_group)
        for candidate in group["candidates"]:
            source = _relative_child(
                artifacts_dir,
                candidate["audio_path"],
                "anchor candidate audio",
            )
            _verify_file_sha256(
                source,
                candidate["audio_sha256"],
                "anchor candidate audio",
            )
            copy_jobs.append(
                (
                    source,
                    f"audio/{candidate['id']}.wav",
                    candidate["audio_sha256"],
                ),
            )

    review = _validate_role_review_bundle_v2(
        {
            "format_version": _REVIEW_FORMAT_VERSION,
            "protocol": _REVIEW_PROTOCOL,
            "phase": _PHASE,
            "plan_sha256": plan.anchor_source_plan_sha256,
            "candidate_set_sha256": candidate_sha256,
            "groups": review_groups,
        },
        plan=plan,
        candidate_set=candidate_set,
        candidate_set_sha256=candidate_sha256,
    )
    pending = _new_pending_directory(output_dir)
    try:
        for source, relative, expected_sha256 in copy_jobs:
            destination = _relative_child(pending, relative, "review audio output")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            _verify_file_sha256(
                destination,
                expected_sha256,
                "copied review audio",
            )
        review_path = pending / "role-review-v2.json"
        _write_canonical_new(review_path, review)
        review_raw = review_path.read_bytes()
        review_sha256 = hashlib.sha256(review_raw).hexdigest()
        _assert_exact_directory_files(
            pending,
            {
                "role-review-v2.json",
                *(
                    f"audio/{candidate['id']}.wav"
                    for group in candidate_set["groups"]
                    for candidate in group["candidates"]
                ),
            },
            "role review bundle",
        )
        _publish_pending_directory(pending, output_dir)
    except Exception:
        shutil.rmtree(pending, ignore_errors=True)
        raise

    return RoleReviewBundleSummary(
        output_dir=output_dir,
        review_path=output_dir / "role-review-v2.json",
        review_sha256=review_sha256,
        group_count=len(review_groups),
        candidate_count=len(copy_jobs),
    )


def finalize_role_anchor_selection(
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    candidate_set_path: Path,
    bundle_dir: Path,
    decision_path: Path,
    output_dir: Path,
) -> RoleAnchorSelectionSummary:
    """確定済みv2 decisionをPhase B用selectionへ回収する。"""

    for path, label in (
        (candidate_set_path, "anchor candidate set"),
        (bundle_dir, "role review bundle"),
        (decision_path, "role review decision"),
        (output_dir, "role anchor selection output"),
    ):
        _require_absolute(path, label)
    _require_new_output_directory(output_dir, "role anchor selection output")

    candidate_raw, candidate_document = _read_canonical_json(
        candidate_set_path,
        "anchor candidate set",
    )
    candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    candidate_set = _validate_anchor_candidate_set_v1(
        candidate_document,
        plan=plan,
        candidate_set_sha256=candidate_sha256,
        expected_candidate_set_sha256=plan.anchor_candidate_set_sha256,
    )
    _assert_exact_directory_files(
        bundle_dir,
        {
            "role-review-v2.json",
            *(
                f"audio/{candidate['id']}.wav"
                for group in candidate_set["groups"]
                for candidate in group["candidates"]
            ),
        },
        "role review bundle",
    )
    _bundle_raw, bundle_document = _read_canonical_json(
        bundle_dir / "role-review-v2.json",
        "role review bundle",
    )
    bundle = _validate_role_review_bundle_v2(
        bundle_document,
        plan=plan,
        candidate_set=candidate_set,
        candidate_set_sha256=candidate_sha256,
    )
    for group in bundle["groups"]:
        for candidate in group["candidates"]:
            _verify_file_sha256(
                _relative_child(
                    bundle_dir,
                    candidate["audio_path"],
                    "role review audio",
                ),
                candidate["audio_sha256"],
                "role review audio",
            )

    decision_raw, decision_document = _read_canonical_json(
        decision_path,
        "role review decision",
    )
    decision_sha256 = hashlib.sha256(decision_raw).hexdigest()
    _verify_adjacent_sha256_marker(
        decision_path.with_suffix(".sha256"),
        decision_sha256,
        "role review decision",
    )
    decision = _validate_role_review_decision_v2(
        decision_document,
        bundle=bundle,
        require_selected_gender_pass=True,
    )
    blocked_groups = [
        group for group in decision["groups"] if group["no_usable_candidate"]
    ]
    if blocked_groups:
        details = ", ".join(
            f"{group['id']} ({group['model']}/{group['scenario']}/{group['character']})"
            for group in blocked_groups
        )
        raise CompletionAnchorError(
            "使用可能なAnchor候補がないgroupはselectionを確定できません。"
            "decision artifactを再生成の根拠として候補を補充してください: "
            f"{details}",
        )

    candidate_groups = {
        (group["model"], group["scenario"], group["character"]): group
        for group in candidate_set["groups"]
    }
    selection_groups: list[dict[str, Any]] = []
    copy_jobs: list[tuple[Path, str, str]] = []
    for decision_group, review_group in zip(
        decision["groups"],
        bundle["groups"],
        strict=True,
    ):
        identity = (
            decision_group["model"],
            decision_group["scenario"],
            decision_group["character"],
        )
        candidate_group = candidate_groups[identity]
        candidates = {
            candidate["id"]: candidate for candidate in candidate_group["candidates"]
        }
        candidate = candidates[decision_group["selected_candidate_id"]]
        role = plan.role(identity[1], identity[2])
        source_relative = f"audio/{candidate['id']}.wav"
        source = _relative_child(
            bundle_dir,
            source_relative,
            "selected review audio",
        )
        _verify_file_sha256(
            source,
            candidate["audio_sha256"],
            "selected review audio",
        )
        copy_jobs.append((source, source_relative, candidate["audio_sha256"]))
        group_decision_sha256 = _canonical_sha256(decision_group)
        selected_role_epoch_sha256 = _canonical_sha256(
            {
                "protocol": "selected-role-epoch-v1",
                "model": candidate_group["model"],
                "model_revision": candidate_group["model_revision"],
                "scenario": candidate_group["scenario"],
                "character": candidate_group["character"],
                "role_identity_sha256": candidate_group["role_identity_sha256"],
                "review_role_epoch_sha256": candidate_group["role_epoch_sha256"],
                "anchor_id": candidate["id"],
                "audio_sha256": candidate["audio_sha256"],
                "decision_sha256": group_decision_sha256,
            },
        )
        anchor_text = review_group["anchor_text"]
        selection_groups.append(
            {
                "model": candidate_group["model"],
                "model_revision": candidate_group["model_revision"],
                "scenario": candidate_group["scenario"],
                "character": candidate_group["character"],
                "role_identity": _role_identity_document(role),
                "role_identity_sha256": candidate_group["role_identity_sha256"],
                "review_role_epoch_sha256": candidate_group["role_epoch_sha256"],
                "role_epoch_sha256": selected_role_epoch_sha256,
                "anchor_id": candidate["id"],
                "attempt": candidate["attempt"],
                "seed": candidate["seed"],
                "audio_path": source_relative,
                "audio_sha256": candidate["audio_sha256"],
                "anchor_text": anchor_text,
                "anchor_text_sha256": hashlib.sha256(
                    anchor_text.encode("utf-8"),
                ).hexdigest(),
                "decision": dict(decision_group),
                "decision_sha256": group_decision_sha256,
            },
        )
    selection = validate_anchor_selection(
        {
            "format_version": _SELECTION_FORMAT_VERSION,
            "protocol": _SELECTION_PROTOCOL,
            "plan_sha256": plan.anchor_source_plan_sha256,
            "candidate_set_sha256": candidate_sha256,
            "groups": selection_groups,
        },
    )

    pending = _new_pending_directory(output_dir)
    try:
        for source, relative, expected_sha256 in copy_jobs:
            destination = _relative_child(
                pending,
                relative,
                "selected anchor output",
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            _verify_file_sha256(
                destination,
                expected_sha256,
                "copied selected anchor",
            )
        selection_path = pending / "role-anchor-selection-v1.json"
        _write_canonical_new(selection_path, selection)
        selection_raw = selection_path.read_bytes()
        selection_sha256 = hashlib.sha256(selection_raw).hexdigest()
        marker_path = pending / "role-anchor-selection-v1.sha256"
        marker_path.write_bytes(f"{selection_sha256}\n".encode("ascii"))
        _assert_exact_directory_files(
            pending,
            {
                "role-anchor-selection-v1.json",
                "role-anchor-selection-v1.sha256",
                *(relative for _source, relative, _sha256_value in copy_jobs),
            },
            "role anchor selection",
        )
        _publish_pending_directory(pending, output_dir)
    except Exception:
        shutil.rmtree(pending, ignore_errors=True)
        raise

    return RoleAnchorSelectionSummary(
        output_dir=output_dir,
        selection_path=output_dir / "role-anchor-selection-v1.json",
        selection_sha256=selection_sha256,
        selected_count=len(selection_groups),
    )


def validate_anchor_selection(document: Any) -> dict[str, Any]:
    root = _exact(document, _SELECTION_ROOT_FIELDS, "anchor selection")
    if (
        isinstance(root["format_version"], bool)
        or not isinstance(root["format_version"], int)
        or root["format_version"] != _SELECTION_FORMAT_VERSION
        or root["protocol"] != _SELECTION_PROTOCOL
    ):
        raise CompletionAnchorError("anchor selection root contractが不正です。")

    plan_sha = _sha256(root["plan_sha256"], "anchor selection.plan_sha256")
    candidate_sha = _sha256(
        root["candidate_set_sha256"],
        "anchor selection.candidate_set_sha256",
    )
    values = root["groups"]
    if not isinstance(values, list) or not values:
        raise CompletionAnchorError("anchor selection groupsが不正です。")

    groups: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    anchors: set[str] = set()
    paths: set[str] = set()
    for index, value in enumerate(values):
        field = f"anchor selection.groups[{index}]"
        item = _exact(value, _SELECTION_GROUP_FIELDS, field)
        model = _path_segment(item["model"], f"{field}.model")
        revision = _text(item["model_revision"], f"{field}.model_revision")
        scenario = _path_segment(item["scenario"], f"{field}.scenario")
        character = _path_segment(item["character"], f"{field}.character")
        identity = (model, scenario, character)
        if identity in identities:
            raise CompletionAnchorError("anchor selection groupが重複しています。")
        identities.add(identity)

        role_identity = _validate_role_identity(
            item["role_identity"],
            field=f"{field}.role_identity",
        )
        if (
            role_identity["scenario"] != scenario
            or role_identity["character"] != character
        ):
            raise CompletionAnchorError(
                f"{field}.role_identity keyがgroupと一致しません。",
            )
        role_sha = _sha256(
            item["role_identity_sha256"],
            f"{field}.role_identity_sha256",
        )
        if role_sha != _canonical_sha256(role_identity):
            raise CompletionAnchorError(f"{field}.role identity SHAが不正です。")

        review_epoch_sha = _sha256(
            item["review_role_epoch_sha256"],
            f"{field}.review_role_epoch_sha256",
        )
        epoch_sha = _sha256(
            item["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        )
        anchor_id = _sha256(item["anchor_id"], f"{field}.anchor_id")
        if anchor_id in anchors:
            raise CompletionAnchorError("anchor selection anchor_idが重複しています。")
        anchors.add(anchor_id)

        attempt = _positive_integer(item["attempt"], f"{field}.attempt")
        seed = _integer(item["seed"], f"{field}.seed")
        audio_path = _relative_bundle_path(
            item["audio_path"],
            f"{field}.audio_path",
        )
        if audio_path in paths:
            raise CompletionAnchorError("anchor selection audio pathが重複しています。")
        paths.add(audio_path)
        audio_sha = _sha256(item["audio_sha256"], f"{field}.audio_sha256")

        anchor_text = _text(item["anchor_text"], f"{field}.anchor_text")
        anchor_text_sha = _sha256(
            item["anchor_text_sha256"],
            f"{field}.anchor_text_sha256",
        )
        if hashlib.sha256(anchor_text.encode("utf-8")).hexdigest() != anchor_text_sha:
            raise CompletionAnchorError(f"{field}.anchor text SHAが不正です。")

        decision = _validate_decision(item["decision"], field=f"{field}.decision")
        decision_sha = _sha256(
            item["decision_sha256"],
            f"{field}.decision_sha256",
        )
        if decision_sha != _canonical_sha256(decision):
            raise CompletionAnchorError(f"{field}.decision SHAが不正です。")
        if (
            decision["model"] != model
            or decision["scenario"] != scenario
            or decision["character"] != character
            or decision["role_epoch_sha256"] != review_epoch_sha
            or decision["selected_candidate_id"] != anchor_id
        ):
            raise CompletionAnchorError(
                f"{field}.decisionがselected anchor identityと一致しません。",
            )

        expected_epoch = _canonical_sha256(
            {
                "protocol": "selected-role-epoch-v1",
                "model": model,
                "model_revision": revision,
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": role_sha,
                "review_role_epoch_sha256": review_epoch_sha,
                "anchor_id": anchor_id,
                "audio_sha256": audio_sha,
                "decision_sha256": decision_sha,
            },
        )
        if epoch_sha != expected_epoch:
            raise CompletionAnchorError(
                f"{field}.role_epoch_sha256がselected anchorと一致しません。",
            )

        groups.append(
            {
                "model": model,
                "model_revision": revision,
                "scenario": scenario,
                "character": character,
                "role_identity": role_identity,
                "role_identity_sha256": role_sha,
                "review_role_epoch_sha256": review_epoch_sha,
                "role_epoch_sha256": epoch_sha,
                "anchor_id": anchor_id,
                "attempt": attempt,
                "seed": seed,
                "audio_path": audio_path,
                "audio_sha256": audio_sha,
                "anchor_text": anchor_text,
                "anchor_text_sha256": anchor_text_sha,
                "decision": decision,
                "decision_sha256": decision_sha,
            },
        )

    expected_order = sorted(
        groups,
        key=lambda item: (
            item["model"],
            item["scenario"],
            item["character"],
        ),
    )
    if groups != expected_order:
        raise CompletionAnchorError("anchor selection groupsはcanonical順が必要です。")
    return {
        "format_version": _SELECTION_FORMAT_VERSION,
        "protocol": _SELECTION_PROTOCOL,
        "plan_sha256": plan_sha,
        "candidate_set_sha256": candidate_sha,
        "groups": groups,
    }


def resolve_selected_anchor(
    *,
    selection_path: Path,
    plan_sha256: str,
    model: str,
    model_revision: str,
    role: RoleSnapshot,
) -> SelectedRoleAnchor:
    _require_absolute(selection_path, "anchor selection")
    expected_plan_sha256 = _sha256(
        plan_sha256,
        "expected anchor plan_sha256",
    )
    expected_model = _path_segment(model, "expected anchor model")
    expected_revision = _text(model_revision, "expected anchor model_revision")

    raw, document = _read_canonical_json(selection_path, "anchor selection")
    selection_sha256 = hashlib.sha256(raw).hexdigest()
    _verify_adjacent_sha256_marker(
        selection_path.with_suffix(".sha256"),
        selection_sha256,
        "anchor selection",
    )
    selection = validate_anchor_selection(document)
    if canonical_json(selection).encode("utf-8") != raw:
        raise CompletionAnchorError("anchor selectionはcanonical contractが必要です。")
    if selection["plan_sha256"] != expected_plan_sha256:
        raise CompletionAnchorError(
            "anchor selection plan SHAが現在のfrozen planと一致しません。",
        )

    matches = [
        group
        for group in selection["groups"]
        if (
            group["model"],
            group["scenario"],
            group["character"],
        )
        == (expected_model, role.scenario, role.character)
    ]
    if len(matches) != 1:
        raise CompletionAnchorError(
            "anchor selectionにmodel/scenario/characterの一意な選択がありません: "
            f"{expected_model}/{role.scenario}/{role.character}",
        )
    group = matches[0]
    if group["model_revision"] != expected_revision:
        raise CompletionAnchorError(
            "selected anchorのmodel revisionがadapterと一致しません。",
        )

    expected_role_identity = _role_identity_document(role)
    if (
        group["role_identity"] != expected_role_identity
        or group["role_identity_sha256"] != role.role_identity_sha256
    ):
        raise CompletionAnchorError(
            "selected anchorの完全role identityがscenarioと一致しません。",
        )
    if role.reference_voice is not None:
        raise CompletionAnchorError(
            "明示reference roleにgenerated anchor selectionは使用できません。",
        )

    audio_path = _relative_child(
        selection_path.parent,
        group["audio_path"],
        "selected anchor audio",
    )
    _verify_file_sha256(audio_path, group["audio_sha256"], "selected anchor audio")
    return SelectedRoleAnchor(
        selection_sha256=selection_sha256,
        plan_sha256=selection["plan_sha256"],
        candidate_set_sha256=selection["candidate_set_sha256"],
        model=group["model"],
        model_revision=group["model_revision"],
        scenario=group["scenario"],
        character=group["character"],
        role_identity_sha256=group["role_identity_sha256"],
        role_epoch_sha256=group["role_epoch_sha256"],
        anchor_id=group["anchor_id"],
        attempt=group["attempt"],
        seed=group["seed"],
        audio_path=audio_path,
        audio_sha256=group["audio_sha256"],
        anchor_text=group["anchor_text"],
        anchor_text_sha256=group["anchor_text_sha256"],
        decision_sha256=group["decision_sha256"],
    )


def _next_anchor_topup_attempts(value: Any, field: str) -> list[int]:
    if not isinstance(value, list) or len(value) != _REVIEW_CANDIDATE_COUNT:
        raise CompletionAnchorError(
            f"{field}.attemptsは4件の連続する正整数が必要です。",
        )
    attempts = [
        _positive_integer(attempt, f"{field}.attempts[{index}]")
        for index, attempt in enumerate(value)
    ]
    if any(current != previous + 1 for previous, current in zip(attempts, attempts[1:])):
        raise CompletionAnchorError(
            f"{field}.attemptsは4件の連続する正整数が必要です。",
        )
    if attempts[0] >= 9:
        raise CompletionAnchorError(
            f"{field}はPhase A topup 2回の上限に達しています。",
        )
    if attempts not in ([1, 2, 3, 4], [5, 6, 7, 8]):
        raise CompletionAnchorError(
            f"{field}.attemptsは[1,2,3,4]または[5,6,7,8]が必要です。",
        )
    return [attempt + _REVIEW_CANDIDATE_COUNT for attempt in attempts]


def _load_topup_source_authority(
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    candidate_set_path: Path,
    decision_path: Path,
    expected_candidate_set_sha256: str,
) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    candidate_raw, candidate_document = _read_canonical_json(
        candidate_set_path,
        "anchor candidate set",
    )
    candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    candidate_set = _validate_anchor_candidate_set_v1(
        candidate_document,
        plan=plan,
        candidate_set_sha256=candidate_sha256,
        expected_candidate_set_sha256=expected_candidate_set_sha256,
    )
    review_groups = [
        _role_review_group_v2(plan=plan, group=group)
        for group in candidate_set["groups"]
    ]
    bundle = _validate_role_review_bundle_v2(
        {
            "format_version": _REVIEW_FORMAT_VERSION,
            "protocol": _REVIEW_PROTOCOL,
            "phase": _PHASE,
            "plan_sha256": plan.anchor_source_plan_sha256,
            "candidate_set_sha256": candidate_sha256,
            "groups": review_groups,
        },
        plan=plan,
        candidate_set=candidate_set,
        candidate_set_sha256=candidate_sha256,
    )
    decision_raw, decision_document = _read_canonical_json(
        decision_path,
        "role review decision",
    )
    decision_sha256 = hashlib.sha256(decision_raw).hexdigest()
    _verify_adjacent_sha256_marker(
        decision_path.with_suffix(".sha256"),
        decision_sha256,
        "role review decision",
    )
    decision = _validate_role_review_decision_v2(
        decision_document,
        bundle=bundle,
        require_selected_gender_pass=False,
    )
    return candidate_set, candidate_sha256, decision, decision_sha256


def _load_topup_replay_authority(
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    candidate_set_path: Path,
    decision_path: Path,
    topup_plan_path: Path,
) -> tuple[
    bytes,
    dict[str, Any],
    dict[str, Any],
    str,
    dict[str, Any],
    str,
]:
    topup_raw, topup_document = _read_canonical_json(
        topup_plan_path,
        "anchor topup plan",
    )
    (
        _root,
        expected_candidate_set_sha256,
        expected_decision_sha256,
    ) = _anchor_topup_plan_root(topup_document, plan=plan)
    candidate_set, candidate_sha256, decision, decision_sha256 = (
        _load_topup_source_authority(
            plan=plan,
            candidate_set_path=candidate_set_path,
            decision_path=decision_path,
            expected_candidate_set_sha256=expected_candidate_set_sha256,
        )
    )
    if decision_sha256 != expected_decision_sha256:
        raise CompletionAnchorError(
            "anchor topup planがfinal decisionと一致しません。",
        )
    topup = _validate_anchor_topup_plan(
        topup_document,
        plan=plan,
        candidate_set=candidate_set,
        decision=decision,
        decision_sha256=decision_sha256,
    )
    return (
        topup_raw,
        topup,
        candidate_set,
        candidate_sha256,
        decision,
        decision_sha256,
    )


def _anchor_topup_plan_root(
    document: Any,
    *,
    plan: CompletionPlan | AnchorReviewPlan,
) -> tuple[Mapping[str, Any], str, str]:
    root = _exact(document, _TOPUP_PLAN_ROOT_FIELDS, "anchor topup plan")
    if (
        root["format_version"] != _TOPUP_PLAN_FORMAT_VERSION
        or root["protocol"] != _TOPUP_PLAN_PROTOCOL
    ):
        raise CompletionAnchorError(
            "anchor topup planはformat_version=2 / "
            "role-anchor-topup-v2が必要です。",
        )
    plan_sha256 = _sha256(root["plan_sha256"], "anchor topup plan.plan_sha256")
    if plan_sha256 != plan.anchor_source_plan_sha256:
        raise CompletionAnchorError("anchor topup planがauthority planと一致しません。")
    candidate_set_sha256 = _sha256(
        root["candidate_set_sha256"],
        "anchor topup plan.candidate_set_sha256",
    )
    decision_sha256 = _sha256(
        root["decision_sha256"],
        "anchor topup plan.decision_sha256",
    )
    return root, candidate_set_sha256, decision_sha256


def _validate_anchor_topup_plan(
    document: Any,
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    candidate_set: Mapping[str, Any],
    decision: Mapping[str, Any],
    decision_sha256: str,
) -> dict[str, Any]:
    root, candidate_set_sha256, normalized_decision_sha256 = (
        _anchor_topup_plan_root(document, plan=plan)
    )
    plan_sha256 = plan.anchor_source_plan_sha256
    if candidate_set_sha256 != _canonical_sha256(candidate_set):
        raise CompletionAnchorError(
            "anchor topup planがsource candidate setと一致しません。",
        )
    if normalized_decision_sha256 != decision_sha256:
        raise CompletionAnchorError("anchor topup planがfinal decisionと一致しません。")
    values = root["targets"]
    if not isinstance(values, list) or not values:
        raise CompletionAnchorError("anchor topup plan.targetsは空でない配列が必要です。")
    candidate_groups = {
        (group["model"], group["scenario"], group["character"]): group
        for group in candidate_set["groups"]
    }
    decision_groups = {
        (group["model"], group["scenario"], group["character"]): group
        for group in decision["groups"]
    }
    targets: list[dict[str, Any]] = []
    seen_seeds: set[int] = set()
    for index, value in enumerate(values):
        field = f"anchor topup plan.targets[{index}]"
        item = _exact(value, _TOPUP_PLAN_TARGET_FIELDS, field)
        model = _anchor_model(item["model"], f"{field}.model")
        scenario = _safe_segment(item["scenario"], f"{field}.scenario")
        character = _safe_segment(item["character"], f"{field}.character")
        identity = (model, scenario, character)
        try:
            candidate_group = candidate_groups[identity]
            decision_group = decision_groups[identity]
        except KeyError as error:
            raise CompletionAnchorError(f"{field}がsource authority対象外です。") from error
        role_identity_sha256 = _sha256(
            item["role_identity_sha256"],
            f"{field}.role_identity_sha256",
        )
        role_epoch_sha256 = _sha256(
            item["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        )
        if (
            role_identity_sha256 != candidate_group["role_identity_sha256"]
            or role_epoch_sha256 != candidate_group["role_epoch_sha256"]
        ):
            raise CompletionAnchorError(f"{field}のrole identity/epochが不正です。")
        expected_attempts = _next_anchor_topup_attempts(
            candidate_group["attempts"],
            f"{field}.source",
        )
        attempts_value = item["attempts"]
        if attempts_value != expected_attempts:
            raise CompletionAnchorError(
                f"{field}.attemptsがsource attempts + 4と一致しません。",
            )
        seeds_value = item["seeds"]
        if not isinstance(seeds_value, list) or len(seeds_value) != len(
            expected_attempts
        ):
            raise CompletionAnchorError(f"{field}.seedsはexactly 4件が必要です。")
        seeds = [
            _non_negative_safe_integer(seed, f"{field}.seeds[{seed_index}]")
            for seed_index, seed in enumerate(seeds_value)
        ]
        expected_seeds = [
            _derive_anchor_seed(
                plan_sha256=plan_sha256,
                model=model,
                scenario=scenario,
                character=character,
                attempt=attempt,
            )
            for attempt in expected_attempts
        ]
        if seeds != expected_seeds or any(seed in seen_seeds for seed in seeds):
            raise CompletionAnchorError(f"{field}.seedsがcanonical derivationと不一致です。")
        seen_seeds.update(seeds)
        if not (
            decision_group["no_usable_candidate"]
            or decision_group["rubric"]["gender"] != "pass"
        ):
            raise CompletionAnchorError(f"{field}はtopup判定対象ではありません。")
        targets.append(
            {
                "model": model,
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": role_identity_sha256,
                "role_epoch_sha256": role_epoch_sha256,
                "attempts": expected_attempts,
                "seeds": seeds,
            },
        )
    if targets != sorted(
        targets,
        key=lambda item: (item["model"], item["scenario"], item["character"]),
    ):
        raise CompletionAnchorError("anchor topup plan.targetsはcanonical順が必要です。")
    actual_identities = {
        (target["model"], target["scenario"], target["character"])
        for target in targets
    }
    expected_identities = {
        (group["model"], group["scenario"], group["character"])
        for group in decision["groups"]
        if group["no_usable_candidate"] or group["rubric"]["gender"] != "pass"
    }
    if actual_identities != expected_identities or len(actual_identities) != len(targets):
        raise CompletionAnchorError(
            "anchor topup plan.targetsはdecision由来対象とexact一致が必要です。",
        )
    return {
        "format_version": _TOPUP_PLAN_FORMAT_VERSION,
        "protocol": _TOPUP_PLAN_PROTOCOL,
        "plan_sha256": plan_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "decision_sha256": normalized_decision_sha256,
        "targets": targets,
    }


def _validate_anchor_topup_run(
    document: Any,
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    topup: Mapping[str, Any],
    candidate_set_sha256: str,
    decision_sha256: str,
    topup_plan_sha256: str,
) -> dict[str, Any]:
    root = _exact(document, _TOPUP_RUN_ROOT_FIELDS, "anchor topup run")
    if (
        root["format_version"] != _TOPUP_RUN_FORMAT_VERSION
        or root["protocol"] != _TOPUP_RUN_PROTOCOL
    ):
        raise CompletionAnchorError(
            "anchor topup runはformat_version=2 / "
            "role-anchor-topup-run-v2が必要です。",
        )
    bindings = {
        "plan_sha256": plan.anchor_source_plan_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "decision_sha256": decision_sha256,
        "topup_plan_sha256": topup_plan_sha256,
    }
    for key, expected in bindings.items():
        if _sha256(root[key], f"anchor topup run.{key}") != expected:
            raise CompletionAnchorError(f"anchor topup run.{key}がauthorityと不一致です。")
    run_id = _path_segment(root["run_id"], "anchor topup run.run_id")
    model = _anchor_model(root["model"], "anchor topup run.model")
    revision = _trimmed_text(
        root["model_revision"],
        "anchor topup run.model_revision",
    )
    if revision != _model_revision(plan, model):
        raise CompletionAnchorError("anchor topup run model revisionが不正です。")
    expected_targets = {
        (target["scenario"], target["character"]): target
        for target in topup["targets"]
        if target["model"] == model
    }
    values = root["attempts"]
    if not isinstance(values, list) or not values:
        raise CompletionAnchorError("anchor topup run.attemptsは空でない配列が必要です。")
    attempts: list[dict[str, Any]] = []
    slots: set[tuple[str, str, int]] = set()
    for index, value in enumerate(values):
        field = f"anchor topup run.attempts[{index}]"
        item = _exact(value, _TOPUP_RUN_ATTEMPT_FIELDS, field)
        item_model = _anchor_model(item["model"], f"{field}.model")
        item_revision = _trimmed_text(item["model_revision"], f"{field}.model_revision")
        scenario = _safe_segment(item["scenario"], f"{field}.scenario")
        character = _safe_segment(item["character"], f"{field}.character")
        try:
            target = expected_targets[(scenario, character)]
        except KeyError as error:
            raise CompletionAnchorError(f"{field}がtopup plan対象外です。") from error
        attempt = _positive_integer(item["attempt"], f"{field}.attempt")
        if attempt not in target["attempts"]:
            raise CompletionAnchorError(f"{field}.attemptがtopup plan対象外です。")
        target_index = target["attempts"].index(attempt)
        seed = _non_negative_safe_integer(item["seed"], f"{field}.seed")
        if (
            item_model != model
            or item_revision != revision
            or item["role_identity_sha256"] != target["role_identity_sha256"]
            or item["role_epoch_sha256"] != target["role_epoch_sha256"]
            or seed != target["seeds"][target_index]
        ):
            raise CompletionAnchorError(f"{field}がtopup plan identityと不一致です。")
        slot = (scenario, character, attempt)
        if slot in slots:
            raise CompletionAnchorError("anchor topup run attempt slotが重複しています。")
        slots.add(slot)
        generation_input = _exact(
            item["generation_input"],
            _TOPUP_GENERATION_INPUT_FIELDS,
            f"{field}.generation_input",
        )
        expected_input_identity = {
            "protocol": "role-anchor-input-v1",
            "plan_sha256": plan.anchor_source_plan_sha256,
            "model": model,
            "model_revision": revision,
            "scenario": scenario,
            "character": character,
            "role_identity_sha256": target["role_identity_sha256"],
            "role_epoch_sha256": target["role_epoch_sha256"],
            "attempt": attempt,
            "seed": seed,
            "anchor_text": _anchor_texts()[model],
            "anchor_text_sha256": hashlib.sha256(
                _anchor_texts()[model].encode("utf-8"),
            ).hexdigest(),
        }
        if any(generation_input[key] != expected for key, expected in expected_input_identity.items()):
            raise CompletionAnchorError(f"{field}.generation_input identityが不正です。")
        conditioning = generation_input["conditioning"]
        if not isinstance(conditioning, dict):
            raise CompletionAnchorError(f"{field}.generation_input.conditioningが不正です。")
        canonical_json(conditioning)
        recursive_keys = _recursive_keys(conditioning)
        if "emotion" in recursive_keys or "intensity" in recursive_keys:
            raise CompletionAnchorError(
                f"{field}.generation_inputにemotion/intensityを含められません。",
            )
        generation_input_sha256 = _sha256(
            item["generation_input_sha256"],
            f"{field}.generation_input_sha256",
        )
        if generation_input_sha256 != _canonical_sha256(generation_input):
            raise CompletionAnchorError(f"{field}.generation input SHAが不正です。")
        status = _enum(item["status"], {"eligible", "rejected", "failed"}, f"{field}.status")
        qc = _validate_review_qc(item["qc"], f"{field}.qc")
        if status == "eligible" and qc["mechanical"] != "pass":
            raise CompletionAnchorError("eligible anchorはmechanical passが必要です。")
        if status in {"rejected", "failed"} and qc["mechanical"] != "fail":
            raise CompletionAnchorError("非eligible anchorはmechanical failが必要です。")
        if status == "failed":
            if any(item[key] is not None for key in ("anchor_id", "audio_path", "audio_sha256", "realized")):
                raise CompletionAnchorError("failed anchorにaudio identityは持てません。")
            anchor_id = audio_path = audio_sha256 = realized = None
            error = _trimmed_text(item["error"], f"{field}.error")
        else:
            anchor_id = _sha256(item["anchor_id"], f"{field}.anchor_id")
            audio_path = _relative_anchor_artifact_path(item["audio_path"], f"{field}.audio_path")
            if PurePosixPath(audio_path).parts[2] != run_id:
                raise CompletionAnchorError(f"{field}.audio_pathがrun idと不一致です。")
            audio_sha256 = _sha256(item["audio_sha256"], f"{field}.audio_sha256")
            expected_anchor_id = _canonical_sha256(
                {
                    "protocol": "role-anchor-identity-v1",
                    "plan_sha256": plan.anchor_source_plan_sha256,
                    "model": model,
                    "model_revision": revision,
                    "scenario": scenario,
                    "character": character,
                    "role_identity_sha256": target["role_identity_sha256"],
                    "role_epoch_sha256": target["role_epoch_sha256"],
                    "attempt": attempt,
                    "seed": seed,
                    "generation_input_sha256": generation_input_sha256,
                    "audio_sha256": audio_sha256,
                },
            )
            if anchor_id != expected_anchor_id:
                raise CompletionAnchorError(f"{field}.anchor_idがcontent identityと不一致です。")
            realized = item["realized"]
            if not isinstance(realized, dict):
                raise CompletionAnchorError(f"{field}.realizedはobjectが必要です。")
            canonical_json(realized)
            error = None
            if item["error"] is not None:
                raise CompletionAnchorError(f"{field}.errorはnullが必要です。")
        attempts.append(
            {
                "model": model,
                "model_revision": revision,
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": target["role_identity_sha256"],
                "role_epoch_sha256": target["role_epoch_sha256"],
                "attempt": attempt,
                "seed": seed,
                "generation_input": generation_input,
                "generation_input_sha256": generation_input_sha256,
                "status": status,
                "anchor_id": anchor_id,
                "audio_path": audio_path,
                "audio_sha256": audio_sha256,
                "qc": qc,
                "realized": realized,
                "error": error,
            },
        )
    expected_slots = {
        (target["scenario"], target["character"], attempt)
        for target in expected_targets.values()
        for attempt in target["attempts"]
    }
    if slots != expected_slots:
        raise CompletionAnchorError(
            "anchor topup runはmodel対象×plan attemptsとexact一致が必要です。",
        )
    if attempts != sorted(attempts, key=lambda item: (item["model"], item["scenario"], item["character"], item["attempt"])):
        raise CompletionAnchorError("anchor topup run.attemptsはcanonical順が必要です。")
    return {
        "format_version": _TOPUP_RUN_FORMAT_VERSION,
        "protocol": _TOPUP_RUN_PROTOCOL,
        **bindings,
        "run_id": run_id,
        "model": model,
        "model_revision": revision,
        "attempts": attempts,
    }


def _validate_anchor_candidate_set_v1(
    document: Any,
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    candidate_set_sha256: str,
    expected_candidate_set_sha256: str,
) -> dict[str, Any]:
    expected_source_plan_sha256 = _sha256(
        plan.anchor_source_plan_sha256,
        "completion plan.anchor_source_plan_sha256",
    )
    expected_candidate_set_sha256 = _sha256(
        expected_candidate_set_sha256,
        "expected anchor candidate set SHA-256",
    )
    actual_candidate_set_sha256 = _sha256(
        candidate_set_sha256,
        "anchor candidate set SHA-256",
    )
    if actual_candidate_set_sha256 != expected_candidate_set_sha256:
        raise CompletionAnchorError(
            "anchor candidate set SHAがcompletion plan authorityと一致しません。",
        )

    root = _exact(
        document,
        _CANDIDATE_SET_ROOT_FIELDS,
        "anchor candidate set",
    )
    if (
        root["format_version"] != _CANDIDATE_SET_FORMAT_VERSION
        or root["protocol"] != _CANDIDATE_SET_PROTOCOL
    ):
        raise CompletionAnchorError("anchor candidate set root contractが不正です。")
    plan_sha256 = _sha256(
        root["plan_sha256"],
        "anchor candidate set.plan_sha256",
    )
    if plan_sha256 != expected_source_plan_sha256:
        raise CompletionAnchorError(
            "anchor candidate set plan SHAがanchor source planと一致しません。",
        )
    runs_value = root["runs"]
    if not isinstance(runs_value, list) or not runs_value:
        raise CompletionAnchorError("anchor candidate set.runsが不正です。")
    runs = [
        _path_segment(value, f"anchor candidate set.runs[{index}]")
        for index, value in enumerate(runs_value)
    ]
    if runs != sorted(set(runs)):
        raise CompletionAnchorError(
            "anchor candidate set.runsはcanonical unique順が必要です。",
        )

    anchor_texts = _anchor_texts()
    if set(anchor_texts) != set(_REVIEW_MODELS):
        raise AssertionError("anchor text model setが不正です。")
    no_reference_roles = tuple(
        role for role in plan.roles if role.reference_voice is None
    )
    if len(no_reference_roles) != _REVIEW_GROUP_COUNT // len(_REVIEW_MODELS):
        raise CompletionAnchorError(
            "completion planはAnchor対象のno-reference roleが53件必要です。",
        )
    expected_identities = {
        (model, role.scenario, role.character)
        for model in _REVIEW_MODELS
        for role in no_reference_roles
    }
    for model in _REVIEW_MODELS:
        if model not in plan.models:
            raise CompletionAnchorError(
                f"completion planにAnchor modelがありません: {model}",
            )

    groups_value = root["groups"]
    if not isinstance(groups_value, list) or len(groups_value) != _REVIEW_GROUP_COUNT:
        raise CompletionAnchorError(
            "anchor candidate set.groupsはexactly 106件が必要です。",
        )
    groups: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    coordinates_by_model = {model: set() for model in _REVIEW_MODELS}
    candidate_ids_seen: set[str] = set()
    audio_paths_seen: set[str] = set()
    for index, value in enumerate(groups_value):
        field = f"anchor candidate set.groups[{index}]"
        item = _exact(value, _CANDIDATE_SET_GROUP_FIELDS, field)
        model = _anchor_model(item["model"], f"{field}.model")
        revision = _text(item["model_revision"], f"{field}.model_revision")
        if revision != plan.models[model]:
            raise CompletionAnchorError(
                f"{field}.model_revisionがcompletion planと一致しません。",
            )
        scenario = _safe_segment(item["scenario"], f"{field}.scenario")
        character = _safe_segment(item["character"], f"{field}.character")
        identity = (model, scenario, character)
        coordinate = (scenario, character)
        if coordinate in coordinates_by_model[model]:
            raise CompletionAnchorError(
                "anchor candidate setでmodel内のrole座標が重複しています: "
                f"{model}/{scenario}/{character}",
            )
        coordinates_by_model[model].add(coordinate)
        if identity in identities:
            raise CompletionAnchorError("anchor candidate set groupが重複しています。")
        identities.add(identity)
        if identity not in expected_identities:
            raise CompletionAnchorError(
                f"{field}がcompletion planのAnchor対象ではありません。",
            )
        role = plan.role(scenario, character)
        if role.reference_voice is not None:
            raise CompletionAnchorError(
                f"{field}はreference voice roleを含められません。",
            )
        role_identity_sha256 = _sha256(
            item["role_identity_sha256"],
            f"{field}.role_identity_sha256",
        )
        if role_identity_sha256 != role.role_identity_sha256:
            raise CompletionAnchorError(
                f"{field}.role_identity_sha256がcompletion planと一致しません。",
            )
        role_epoch_sha256 = _sha256(
            item["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        )
        anchor_text_sha256 = hashlib.sha256(
            anchor_texts[model].encode("utf-8"),
        ).hexdigest()
        expected_role_epoch_sha256 = _canonical_sha256(
            {
                "model": model,
                "model_revision": revision,
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": role_identity_sha256,
                "anchor_text_sha256": anchor_text_sha256,
            },
        )
        if role_epoch_sha256 != expected_role_epoch_sha256:
            raise CompletionAnchorError(
                f"{field}.role_epoch_sha256が固定Anchor入力と一致しません。",
            )

        attempts_value = item["attempts"]
        if (
            not isinstance(attempts_value, list)
            or len(attempts_value) != _REVIEW_CANDIDATE_COUNT
        ):
            raise CompletionAnchorError(
                f"{field}.attemptsはexactly 4件が必要です。",
            )
        attempts = [
            _positive_integer(value, f"{field}.attempts[{attempt_index}]")
            for attempt_index, value in enumerate(attempts_value)
        ]
        if attempts != sorted(set(attempts)):
            raise CompletionAnchorError(
                f"{field}.attemptsは4件のunique厳密昇順正整数が必要です。",
            )
        candidates_value = item["candidates"]
        if (
            not isinstance(candidates_value, list)
            or len(candidates_value) != _REVIEW_CANDIDATE_COUNT
        ):
            raise CompletionAnchorError(
                f"{field}.candidatesはexactly 4件が必要です。",
            )
        candidates: list[dict[str, Any]] = []
        for candidate_index, candidate_value in enumerate(candidates_value):
            candidate_field = f"{field}.candidates[{candidate_index}]"
            candidate = _exact(
                candidate_value,
                _CANDIDATE_SET_CANDIDATE_FIELDS,
                candidate_field,
            )
            expected_attempt = attempts[candidate_index]
            if candidate["attempt"] != expected_attempt:
                raise CompletionAnchorError(
                    f"{candidate_field}.attemptは{expected_attempt}が必要です。",
                )
            candidate_id = _sha256(candidate["id"], f"{candidate_field}.id")
            if candidate_id in candidate_ids_seen:
                raise CompletionAnchorError("anchor candidate idが重複しています。")
            candidate_ids_seen.add(candidate_id)
            if any(
                candidate[key] != expected
                for key, expected in (
                    ("model", model),
                    ("model_revision", revision),
                    ("scenario", scenario),
                    ("character", character),
                    ("role_identity_sha256", role_identity_sha256),
                    ("role_epoch_sha256", role_epoch_sha256),
                )
            ):
                raise CompletionAnchorError(
                    f"{candidate_field}のgroup identityが一致しません。",
                )
            audio_path = _relative_anchor_artifact_path(
                candidate["audio_path"],
                f"{candidate_field}.audio_path",
            )
            if PurePosixPath(audio_path).parts[2] not in runs:
                raise CompletionAnchorError(
                    f"{candidate_field}.audio_pathのrunがcandidate set.runsにありません。",
                )
            if audio_path in audio_paths_seen:
                raise CompletionAnchorError(
                    "anchor candidate audio pathが重複しています。",
                )
            audio_paths_seen.add(audio_path)
            candidates.append(
                {
                    "id": candidate_id,
                    "model": model,
                    "model_revision": revision,
                    "scenario": scenario,
                    "character": character,
                    "role_identity_sha256": role_identity_sha256,
                    "role_epoch_sha256": role_epoch_sha256,
                    "attempt": expected_attempt,
                    "seed": _non_negative_safe_integer(
                        candidate["seed"],
                        f"{candidate_field}.seed",
                    ),
                    "audio_path": audio_path,
                    "audio_sha256": _sha256(
                        candidate["audio_sha256"],
                        f"{candidate_field}.audio_sha256",
                    ),
                    "generation_input_sha256": _sha256(
                        candidate["generation_input_sha256"],
                        f"{candidate_field}.generation_input_sha256",
                    ),
                    "qc": _validate_review_qc(
                        candidate["qc"],
                        f"{candidate_field}.qc",
                    ),
                },
            )
        groups.append(
            {
                "model": model,
                "model_revision": revision,
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": role_identity_sha256,
                "role_epoch_sha256": role_epoch_sha256,
                "attempts": attempts,
                "candidates": candidates,
            },
        )
    if identities != expected_identities:
        raise CompletionAnchorError(
            "anchor candidate setは2 model × 53 roleとexact一致が必要です。",
        )
    expected_coordinates = {
        (role.scenario, role.character) for role in no_reference_roles
    }
    if (
        coordinates_by_model[IRODORI_MODEL] != expected_coordinates
        or coordinates_by_model[QWEN_MODEL] != expected_coordinates
        or coordinates_by_model[IRODORI_MODEL] != coordinates_by_model[QWEN_MODEL]
    ):
        raise CompletionAnchorError(
            "anchor candidate setは2 modelで同一の53 role座標集合が必要です。",
        )
    if groups != sorted(
        groups,
        key=lambda group: (
            group["model"],
            group["scenario"],
            group["character"],
        ),
    ):
        raise CompletionAnchorError(
            "anchor candidate set.groupsはcanonical順が必要です。",
        )
    return {
        "format_version": _CANDIDATE_SET_FORMAT_VERSION,
        "protocol": _CANDIDATE_SET_PROTOCOL,
        "plan_sha256": plan_sha256,
        "runs": runs,
        "groups": groups,
    }


def _role_review_group_v2(
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    group: Mapping[str, Any],
) -> dict[str, Any]:
    anchor_texts = _anchor_texts()
    role = plan.role(group["scenario"], group["character"])
    candidates = [
        {
            "id": candidate["id"],
            "attempt": candidate["attempt"],
            "seed": candidate["seed"],
            "audio_path": f"audio/{candidate['id']}.wav",
            "audio_sha256": candidate["audio_sha256"],
            "qc": dict(candidate["qc"]),
        }
        for candidate in group["candidates"]
    ]
    group_id = _canonical_sha256(
        {
            "protocol": "role-review-group-v2",
            "phase": _PHASE,
            "model": group["model"],
            "scenario": group["scenario"],
            "character": group["character"],
            "role_epoch_sha256": group["role_epoch_sha256"],
        },
    )
    return {
        "id": group_id,
        "model": group["model"],
        "scenario": group["scenario"],
        "character": group["character"],
        "line": None,
        "anchor_text": anchor_texts[group["model"]],
        "role_epoch_sha256": group["role_epoch_sha256"],
        "role": _review_role_document(role),
        "conditioning": _review_conditioning(group["model"]),
        "coverage": {
            "gender": ("neutral" if role.role["gender"] == "neutral" else "exact"),
            "age": "exact",
            "archetype": "exact",
        },
        "comparison_required": True,
        "comparison_reasons": list(_COMPARISON_REASONS),
        "candidate_ids": [candidate["id"] for candidate in candidates],
        "candidates": candidates,
    }


def _validate_role_review_bundle_v2(
    document: Any,
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    candidate_set: Mapping[str, Any],
    candidate_set_sha256: str,
) -> dict[str, Any]:
    root = _exact(document, _REVIEW_ROOT_FIELDS, "role review bundle")
    if (
        root["format_version"] != _REVIEW_FORMAT_VERSION
        or root["protocol"] != _REVIEW_PROTOCOL
        or root["phase"] != _PHASE
    ):
        raise CompletionAnchorError("role review bundle root contractが不正です。")
    plan_sha256 = _sha256(
        root["plan_sha256"],
        "role review bundle.plan_sha256",
    )
    if plan_sha256 != plan.anchor_source_plan_sha256:
        raise CompletionAnchorError(
            "role review bundle plan SHAがanchor source planと一致しません。",
        )
    source_sha256 = _sha256(
        root["candidate_set_sha256"],
        "role review bundle.candidate_set_sha256",
    )
    if source_sha256 != candidate_set_sha256:
        raise CompletionAnchorError(
            "role review bundle candidate-set SHAが一致しません。",
        )
    values = root["groups"]
    if not isinstance(values, list) or len(values) != _REVIEW_GROUP_COUNT:
        raise CompletionAnchorError(
            "role review bundle.groupsはexactly 106件が必要です。",
        )
    groups = [
        _validate_role_review_group_v2(
            value,
            field=f"role review bundle.groups[{index}]",
        )
        for index, value in enumerate(values)
    ]
    expected = [
        _role_review_group_v2(plan=plan, group=group)
        for group in candidate_set["groups"]
    ]
    if groups != expected:
        raise CompletionAnchorError(
            "role review bundle groupsがcandidate setとexact一致しません。",
        )
    return {
        "format_version": _REVIEW_FORMAT_VERSION,
        "protocol": _REVIEW_PROTOCOL,
        "phase": _PHASE,
        "plan_sha256": plan_sha256,
        "candidate_set_sha256": source_sha256,
        "groups": groups,
    }


def _validate_role_review_group_v2(value: Any, *, field: str) -> dict[str, Any]:
    item = _exact(value, _REVIEW_GROUP_FIELDS, field)
    if item["line"] is not None:
        raise CompletionAnchorError(f"{field}.lineはanchorでnullが必要です。")
    if item["comparison_required"] is not True:
        raise CompletionAnchorError(
            f"{field}.comparison_requiredはtrueが必要です。",
        )
    if item["comparison_reasons"] != _COMPARISON_REASONS:
        raise CompletionAnchorError(
            f"{field}.comparison_reasonsが固定順と一致しません。",
        )
    role = _validate_review_role(item["role"], f"{field}.role")
    conditioning_value = _exact(
        item["conditioning"],
        _CONDITIONING_FIELDS,
        f"{field}.conditioning",
    )
    conditioning = {
        "method": _trimmed_text(
            conditioning_value["method"],
            f"{field}.conditioning.method",
        ),
        "summary": _trimmed_text(
            conditioning_value["summary"],
            f"{field}.conditioning.summary",
        ),
    }
    coverage_value = _exact(
        item["coverage"],
        _COVERAGE_FIELDS,
        f"{field}.coverage",
    )
    coverage = {
        key: _enum(
            coverage_value[key],
            {"exact", "neutral"},
            f"{field}.coverage.{key}",
        )
        for key in ("gender", "age", "archetype")
    }
    expected_coverage = {
        "gender": "neutral" if role["gender"] == "neutral" else "exact",
        "age": "exact",
        "archetype": "exact",
    }
    if coverage != expected_coverage:
        raise CompletionAnchorError(f"{field}.coverageがroleと一致しません。")
    candidates_value = item["candidates"]
    if (
        not isinstance(candidates_value, list)
        or len(candidates_value) != _REVIEW_CANDIDATE_COUNT
    ):
        raise CompletionAnchorError(f"{field}.candidatesはexactly 4件が必要です。")
    candidates: list[dict[str, Any]] = []
    for index, value_item in enumerate(candidates_value):
        candidate_field = f"{field}.candidates[{index}]"
        candidate = _exact(
            value_item,
            _REVIEW_CANDIDATE_FIELDS,
            candidate_field,
        )
        attempt = _positive_integer(
            candidate["attempt"],
            f"{candidate_field}.attempt",
        )
        candidate_id = _sha256(candidate["id"], f"{candidate_field}.id")
        audio_path = _relative_bundle_path(
            candidate["audio_path"],
            f"{candidate_field}.audio_path",
        )
        if audio_path != f"audio/{candidate_id}.wav":
            raise CompletionAnchorError(
                f"{candidate_field}.audio_pathはcandidate id由来が必要です。",
            )
        candidates.append(
            {
                "id": candidate_id,
                "attempt": attempt,
                "seed": _non_negative_safe_integer(
                    candidate["seed"],
                    f"{candidate_field}.seed",
                ),
                "audio_path": audio_path,
                "audio_sha256": _sha256(
                    candidate["audio_sha256"],
                    f"{candidate_field}.audio_sha256",
                ),
                "qc": _validate_review_qc(
                    candidate["qc"],
                    f"{candidate_field}.qc",
                ),
            },
        )
    attempts = [candidate["attempt"] for candidate in candidates]
    if attempts != sorted(set(attempts)):
        raise CompletionAnchorError(
            f"{field}.candidatesはunique厳密昇順attemptが必要です。",
        )
    candidate_ids = _sha256_array(
        item["candidate_ids"],
        f"{field}.candidate_ids",
    )
    expected_candidate_ids = [candidate["id"] for candidate in candidates]
    if candidate_ids != expected_candidate_ids:
        raise CompletionAnchorError(
            f"{field}.candidate_idsがcandidateのexact順と一致しません。",
        )
    return {
        "id": _sha256(item["id"], f"{field}.id"),
        "model": _anchor_model(item["model"], f"{field}.model"),
        "scenario": _safe_segment(item["scenario"], f"{field}.scenario"),
        "character": _safe_segment(item["character"], f"{field}.character"),
        "line": None,
        "anchor_text": _trimmed_text(
            item["anchor_text"],
            f"{field}.anchor_text",
        ),
        "role_epoch_sha256": _sha256(
            item["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        ),
        "role": role,
        "conditioning": conditioning,
        "coverage": coverage,
        "comparison_required": True,
        "comparison_reasons": list(_COMPARISON_REASONS),
        "candidate_ids": candidate_ids,
        "candidates": candidates,
    }


def _validate_role_review_decision_v2(
    document: Any,
    *,
    bundle: Mapping[str, Any],
    require_selected_gender_pass: bool,
) -> dict[str, Any]:
    root = _exact(document, _DECISION_ROOT_FIELDS, "role review decision")
    if (
        root["format_version"] != _DECISION_FORMAT_VERSION
        or root["protocol"] != _DECISION_PROTOCOL
        or root["phase"] != _PHASE
    ):
        raise CompletionAnchorError("role review decision root contractが不正です。")
    plan_sha256 = _sha256(
        root["plan_sha256"],
        "role review decision.plan_sha256",
    )
    candidate_set_sha256 = _sha256(
        root["candidate_set_sha256"],
        "role review decision.candidate_set_sha256",
    )
    if (
        plan_sha256 != bundle["plan_sha256"]
        or candidate_set_sha256 != bundle["candidate_set_sha256"]
    ):
        raise CompletionAnchorError(
            "role review decision rootがbundle authorityと一致しません。",
        )
    values = root["groups"]
    if not isinstance(values, list) or len(values) != _REVIEW_GROUP_COUNT:
        raise CompletionAnchorError(
            "role review decision.groupsはexactly 106件が必要です。",
        )
    groups: list[dict[str, Any]] = []
    for index, (value, review_group) in enumerate(
        zip(values, bundle["groups"], strict=True),
    ):
        field = f"role review decision.groups[{index}]"
        decision = _validate_role_review_decision_group_v2(
            value,
            field=field,
            require_selected_gender_pass=require_selected_gender_pass,
        )
        expected = {
            "id": review_group["id"],
            "model": review_group["model"],
            "scenario": review_group["scenario"],
            "character": review_group["character"],
            "line": None,
            "role_epoch_sha256": review_group["role_epoch_sha256"],
            "group_sha256": _canonical_sha256(review_group),
            "heard_candidate_ids": review_group["candidate_ids"],
            "confirmed": True,
        }
        mismatched = [
            key
            for key, expected_value in expected.items()
            if decision[key] != expected_value
        ]
        if mismatched:
            raise CompletionAnchorError(
                f"{field}がbundleと一致しません: {mismatched}",
            )
        if (
            not decision["no_usable_candidate"]
            and decision["selected_candidate_id"] not in review_group["candidate_ids"]
        ):
            raise CompletionAnchorError(
                f"{field}.selected_candidate_idが同一groupにありません。",
            )
        groups.append(decision)
    return {
        "format_version": _DECISION_FORMAT_VERSION,
        "protocol": _DECISION_PROTOCOL,
        "phase": _PHASE,
        "plan_sha256": plan_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "groups": groups,
    }


def _validate_role_review_draft_v2(
    document: Any,
    *,
    bundle: Mapping[str, Any],
    target_identities: set[tuple[str, str, str]],
) -> dict[str, Any]:
    root = _exact(document, _DRAFT_ROOT_FIELDS, "role review draft")
    if (
        root["format_version"] != _DRAFT_FORMAT_VERSION
        or root["protocol"] != _DRAFT_PROTOCOL
        or root["phase"] != _PHASE
        or root["plan_sha256"] != bundle["plan_sha256"]
        or root["candidate_set_sha256"] != bundle["candidate_set_sha256"]
    ):
        raise CompletionAnchorError("role review draft root authorityが不正です。")
    values = root["groups"]
    if not isinstance(values, list) or len(values) != _REVIEW_GROUP_COUNT:
        raise CompletionAnchorError("role review draft.groupsはexactly 106件が必要です。")
    empty_rubric = {
        **{key: None for key in _APPLICABLE_RUBRIC_FIELDS},
        "voice_identity": None,
        "delivery": None,
        "naturalness_quality": None,
        "notes": "",
    }
    groups: list[dict[str, Any]] = []
    target_ids: list[str] = []
    for index, (value, review_group) in enumerate(
        zip(values, bundle["groups"], strict=True),
    ):
        field = f"role review draft.groups[{index}]"
        item = _exact(value, _DECISION_FIELDS, field)
        identity = (
            review_group["model"],
            review_group["scenario"],
            review_group["character"],
        )
        expected_binding = {
            "id": review_group["id"],
            "model": review_group["model"],
            "scenario": review_group["scenario"],
            "character": review_group["character"],
            "line": None,
            "role_epoch_sha256": review_group["role_epoch_sha256"],
            "group_sha256": _canonical_sha256(review_group),
        }
        if any(item[key] != expected for key, expected in expected_binding.items()):
            raise CompletionAnchorError(f"{field}がmerged bundleと一致しません。")
        if identity in target_identities:
            target_ids.append(review_group["id"])
            if any(
                (
                    item["heard_candidate_ids"] != [],
                    item["selected_candidate_id"] is not None,
                    item["no_usable_candidate"] is not False,
                    item["rubric"] != empty_rubric,
                    item["confirmed"] is not False,
                ),
            ):
                raise CompletionAnchorError(f"{field}のtopup target resetが不正です。")
            groups.append(json.loads(canonical_json(item)))
            continue
        normalized = _validate_role_review_decision_group_v2(
            item,
            field=field,
            require_selected_gender_pass=True,
        )
        if normalized["heard_candidate_ids"] != review_group["candidate_ids"]:
            raise CompletionAnchorError(f"{field}は全candidate聴取済みが必要です。")
        groups.append(normalized)
    current_group_id = _sha256(
        root["current_group_id"],
        "role review draft.current_group_id",
    )
    if not target_ids or current_group_id != target_ids[0]:
        raise CompletionAnchorError(
            "role review draft.current_group_idは先頭topup targetが必要です。",
        )
    return {
        "format_version": _DRAFT_FORMAT_VERSION,
        "protocol": _DRAFT_PROTOCOL,
        "phase": _PHASE,
        "plan_sha256": bundle["plan_sha256"],
        "candidate_set_sha256": bundle["candidate_set_sha256"],
        "current_group_id": current_group_id,
        "groups": groups,
    }


def _validate_role_review_decision_group_v2(
    value: Any,
    *,
    field: str,
    require_selected_gender_pass: bool,
) -> dict[str, Any]:
    decision = _exact(value, _DECISION_FIELDS, field)
    if decision["line"] is not None:
        raise CompletionAnchorError(f"{field}.lineはanchor phaseでnullが必要です。")
    heard = _sha256_array(
        decision["heard_candidate_ids"],
        f"{field}.heard_candidate_ids",
    )
    no_usable_candidate = decision["no_usable_candidate"]
    if no_usable_candidate is True:
        if decision["selected_candidate_id"] is not None:
            raise CompletionAnchorError(
                f"{field}はno_usable_candidate=trueなら"
                "selected_candidate_id=nullが必要です。",
            )
        selected_candidate_id = None
    elif no_usable_candidate is False:
        selected_candidate_id = _sha256(
            decision["selected_candidate_id"],
            f"{field}.selected_candidate_id",
        )
        if selected_candidate_id not in heard:
            raise CompletionAnchorError(
                f"{field}.selected_candidate_idはheard候補である必要があります。",
            )
    else:
        raise CompletionAnchorError(
            f"{field}.no_usable_candidateはbooleanが必要です。",
        )
    if decision["confirmed"] is not True:
        raise CompletionAnchorError(
            f"{field}.confirmedはtrueが必要です。",
        )
    rubric = _validate_role_review_rubric_v2(
        decision["rubric"],
        f"{field}.rubric",
    )
    if (
        require_selected_gender_pass
        and not no_usable_candidate
        and rubric["gender"] != "pass"
    ):
        raise CompletionAnchorError(
            f"{field}はselected anchorのgender=passが必要です。"
            "性別不一致ならno_usable_candidate=trueで再生成対象にしてください。",
        )
    return {
        "id": _sha256(decision["id"], f"{field}.id"),
        "model": _path_segment(decision["model"], f"{field}.model"),
        "scenario": _path_segment(decision["scenario"], f"{field}.scenario"),
        "character": _path_segment(
            decision["character"],
            f"{field}.character",
        ),
        "line": None,
        "role_epoch_sha256": _sha256(
            decision["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        ),
        "group_sha256": _sha256(
            decision["group_sha256"],
            f"{field}.group_sha256",
        ),
        "heard_candidate_ids": heard,
        "selected_candidate_id": selected_candidate_id,
        "no_usable_candidate": no_usable_candidate,
        "rubric": rubric,
        "confirmed": True,
    }


def _validate_role_review_rubric_v2(value: Any, field: str) -> dict[str, Any]:
    rubric = _exact(value, _DECISION_RUBRIC_FIELDS, field)
    normalized = {
        key: _enum(rubric[key], {"pass", "fail"}, f"{field}.{key}")
        for key in _APPLICABLE_RUBRIC_FIELDS
    }
    for key in ("voice_identity", "delivery"):
        if rubric[key] != "not_applicable":
            raise CompletionAnchorError(
                f"{field}.{key}はnot_applicableが必要です。",
            )
        normalized[key] = "not_applicable"
    score = _integer(rubric["naturalness_quality"], f"{field}.naturalness_quality")
    if not 1 <= score <= 5:
        raise CompletionAnchorError(
            f"{field}.naturalness_qualityは1..5が必要です。",
        )
    normalized["naturalness_quality"] = score
    normalized["notes"] = _string(rubric["notes"], f"{field}.notes")
    return normalized


def _derive_anchor_seed(
    *,
    plan_sha256: str,
    model: str,
    scenario: str,
    character: str,
    attempt: int,
) -> int:
    identity = {
        "policy": _ANCHOR_SEED_POLICY,
        "seed_base": _ANCHOR_SEED_BASE,
        "plan_sha256": _sha256(plan_sha256, "anchor seed plan SHA"),
        "model": _anchor_model(model, "anchor seed model"),
        "scenario": _safe_segment(scenario, "anchor seed scenario"),
        "character": _safe_segment(character, "anchor seed character"),
        "attempt": _positive_integer(attempt, "anchor seed attempt"),
    }
    return int.from_bytes(
        hashlib.sha256(canonical_json(identity).encode("utf-8")).digest()[:4],
        "big",
    )


def _topup_generation_input(
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    target: Mapping[str, Any],
    role: RoleSnapshot,
    attempt: int,
    seed: int,
    generator: RoleAnchorGenerator,
) -> dict[str, Any]:
    conditioning = dict(generator.role_anchor_generation_input(role))
    canonical_json(conditioning)
    recursive_keys = _recursive_keys(conditioning)
    if "emotion" in recursive_keys or "intensity" in recursive_keys:
        raise CompletionAnchorError(
            "anchor conditioningにemotion/intensityを含められません。",
        )
    anchor_text = _anchor_texts()[target["model"]]
    return {
        "protocol": "role-anchor-input-v1",
        "plan_sha256": plan.anchor_source_plan_sha256,
        "model": target["model"],
        "model_revision": _model_revision(plan, target["model"]),
        "scenario": target["scenario"],
        "character": target["character"],
        "role_identity_sha256": target["role_identity_sha256"],
        "role_epoch_sha256": target["role_epoch_sha256"],
        "attempt": attempt,
        "seed": seed,
        "anchor_text": anchor_text,
        "anchor_text_sha256": hashlib.sha256(anchor_text.encode("utf-8")).hexdigest(),
        "conditioning": conditioning,
    }


def _candidate_from_topup_attempt(attempt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": attempt["anchor_id"],
        "model": attempt["model"],
        "model_revision": attempt["model_revision"],
        "scenario": attempt["scenario"],
        "character": attempt["character"],
        "role_identity_sha256": attempt["role_identity_sha256"],
        "role_epoch_sha256": attempt["role_epoch_sha256"],
        "attempt": attempt["attempt"],
        "seed": attempt["seed"],
        "audio_path": attempt["audio_path"],
        "audio_sha256": attempt["audio_sha256"],
        "generation_input_sha256": attempt["generation_input_sha256"],
        "qc": dict(attempt["qc"]),
    }


def _mechanical_qc(path: Path) -> dict[str, Any]:
    notes: list[str] = []
    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frame_count = wav.getnframes()
            compression = wav.getcomptype()
            frames = wav.readframes(frame_count)
    except (OSError, EOFError, wave.Error) as error:
        return {
            "mechanical": "fail",
            "content": "not_checked",
            "notes": [f"invalid_wav:{error}"],
        }
    if channels != 1:
        notes.append(f"channels={channels}")
    if sample_width != 2:
        notes.append(f"sample_width={sample_width}")
    if compression != "NONE":
        notes.append(f"compression={compression}")
    if not 8_000 <= sample_rate <= 96_000:
        notes.append(f"sample_rate={sample_rate}")
    duration = frame_count / sample_rate if sample_rate > 0 else 0.0
    if not 0.25 <= duration <= 30.0:
        notes.append(f"duration={duration:.6f}")
    if sample_width == 2 and frames:
        sample_count = len(frames) // 2
        samples = struct.unpack(f"<{sample_count}h", frames[: sample_count * 2])
        peak = max((abs(sample) for sample in samples), default=0)
        rms = math.sqrt(
            sum(float(sample) ** 2 for sample in samples) / max(1, len(samples)),
        )
        if peak < 32 or rms < 8:
            notes.append(f"silence_peak={peak},rms={rms:.3f}")
    else:
        notes.append("no_pcm16_samples")
    return {
        "mechanical": "fail" if notes else "pass",
        "content": "not_checked",
        "notes": notes,
    }


def _create_anchor_generator(model_id: str) -> RoleAnchorGenerator:
    from gaya_pipeline.adapters import create_adapter

    adapter = create_adapter(model_id)
    for name in (
        "role_anchor_generation_input",
        "generate_role_anchor",
        "close_role_anchor_generation",
    ):
        if not callable(getattr(adapter, name, None)):
            raise CompletionAnchorError(
                f"modelはPhase A anchor interfaceを実装していません: {model_id}",
            )
    return adapter  # type: ignore[return-value]


def _model_revision(
    plan: CompletionPlan | AnchorReviewPlan,
    model: str,
) -> str:
    try:
        return plan.models[model]
    except KeyError as error:
        raise CompletionAnchorError(
            f"authority planにmodel revisionがありません: {model}",
        ) from error


def _recursive_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return set(value) | {
            key
            for child in value.values()
            for key in _recursive_keys(child)
        }
    if isinstance(value, list | tuple):
        return {key for child in value for key in _recursive_keys(child)}
    return set()


def _anchor_texts() -> dict[str, str]:
    from gaya_pipeline.adapters.irodori_tts import ROLE_ANCHOR_TEXT
    from gaya_pipeline.adapters.qwen3_tts import REFERENCE_TEXT

    return {
        IRODORI_MODEL: ROLE_ANCHOR_TEXT,
        QWEN_MODEL: REFERENCE_TEXT,
    }


def _review_conditioning(model: str) -> dict[str, str]:
    if model == IRODORI_MODEL:
        return {
            "method": "caption-anchor-then-reference",
            "summary": (
                "完全な役柄captionで候補anchorを生成し、選定WAVと逐条の"
                "役柄・場面・演技captionを同時に全台詞へ渡す。"
            ),
        }
    if model == QWEN_MODEL:
        return {
            "method": "voice-design-anchor-then-clone",
            "summary": (
                "完全な役柄指定から候補anchorをVoiceDesignし、選定WAVを"
                "Baseの同一キャラクターclone promptとして全台詞に固定する。"
            ),
        }
    raise CompletionAnchorError(f"Anchor review対象外modelです: {model}")


def _review_role_document(role: RoleSnapshot) -> dict[str, str]:
    return {
        key: _trimmed_text(role.role[key], f"role.{key}")
        for key in (
            "name",
            "kind",
            "gender",
            "age",
            "archetype",
            "voice",
            "personality",
        )
    }


def _validate_review_role(value: Any, field: str) -> dict[str, str]:
    role = _exact(value, _ROLE_FIELDS, field)
    return {
        "name": _trimmed_text(role["name"], f"{field}.name"),
        "kind": _enum(
            role["kind"],
            {"human", "machine", "creature", "spirit"},
            f"{field}.kind",
        ),
        "gender": _enum(
            role["gender"],
            {"female", "male", "neutral"},
            f"{field}.gender",
        ),
        "age": _enum(
            role["age"],
            {"child", "teen", "young_adult", "adult", "middle_aged", "elderly"},
            f"{field}.age",
        ),
        "archetype": _trimmed_text(role["archetype"], f"{field}.archetype"),
        "voice": _trimmed_text(role["voice"], f"{field}.voice"),
        "personality": _trimmed_text(
            role["personality"],
            f"{field}.personality",
        ),
    }


def _validate_review_qc(value: Any, field: str) -> dict[str, Any]:
    qc = _exact(value, _QC_FIELDS, field)
    if qc["mechanical"] != "pass":
        raise CompletionAnchorError(f"{field}.mechanicalはpassが必要です。")
    content = _enum(
        qc["content"],
        {"not_checked", "pass", "review_required"},
        f"{field}.content",
    )
    notes = qc["notes"]
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        raise CompletionAnchorError(f"{field}.notesは文字列配列が必要です。")
    return {
        "mechanical": "pass",
        "content": content,
        "notes": list(notes),
    }


def _validate_decision(value: Any, *, field: str) -> dict[str, Any]:
    decision = _exact(value, _DECISION_FIELDS, field)
    if decision["line"] is not None:
        raise CompletionAnchorError(f"{field}.lineはanchor phaseでnullが必要です。")

    heard = _sha256_array(
        decision["heard_candidate_ids"],
        f"{field}.heard_candidate_ids",
    )
    if decision["no_usable_candidate"] is not False:
        raise CompletionAnchorError(
            f"{field}.no_usable_candidateはselected selectionでfalseが必要です。",
        )
    selected_candidate_id = _sha256(
        decision["selected_candidate_id"],
        f"{field}.selected_candidate_id",
    )
    if selected_candidate_id not in heard:
        raise CompletionAnchorError(
            f"{field}.selected_candidate_idはheard候補である必要があります。",
        )
    if decision["confirmed"] is not True:
        raise CompletionAnchorError("anchor decisionにはconfirmed=trueが必要です。")

    return {
        "id": _sha256(decision["id"], f"{field}.id"),
        "model": _path_segment(decision["model"], f"{field}.model"),
        "scenario": _path_segment(decision["scenario"], f"{field}.scenario"),
        "character": _path_segment(
            decision["character"],
            f"{field}.character",
        ),
        "line": None,
        "role_epoch_sha256": _sha256(
            decision["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        ),
        "group_sha256": _sha256(
            decision["group_sha256"],
            f"{field}.group_sha256",
        ),
        "heard_candidate_ids": heard,
        "selected_candidate_id": selected_candidate_id,
        "no_usable_candidate": False,
        "rubric": _validate_decision_rubric(
            decision["rubric"],
            f"{field}.rubric",
        ),
        "confirmed": True,
    }


def _validate_decision_rubric(value: Any, field: str) -> dict[str, Any]:
    rubric = _exact(value, _DECISION_RUBRIC_FIELDS, field)
    normalized: dict[str, Any] = {}
    for key in _RUBRIC_RESULT_FIELDS:
        result = rubric[key]
        if result not in _ALLOWED_RUBRIC_RESULTS:
            raise CompletionAnchorError(
                f"{field}.{key}はpass/fail/not_applicableが必要です。",
            )
        normalized[key] = result
    score = _integer(rubric["naturalness_quality"], f"{field}.naturalness_quality")
    if not 1 <= score <= 5:
        raise CompletionAnchorError(f"{field}.naturalness_qualityは1..5が必要です。")
    normalized["naturalness_quality"] = score
    normalized["notes"] = _string(rubric["notes"], f"{field}.notes")
    return normalized


def _validate_role_identity(value: Any, *, field: str) -> dict[str, Any]:
    identity = _exact(value, _ROLE_IDENTITY_FIELDS, field)
    role_value = _exact(identity["role"], _ROLE_FIELDS, f"{field}.role")
    role = {
        key: _text(role_value[key], f"{field}.role.{key}")
        for key in (
            "name",
            "kind",
            "gender",
            "age",
            "archetype",
            "voice",
            "personality",
        )
    }
    reference = identity["reference_voice"]
    if reference is not None:
        reference = _path_segment(reference, f"{field}.reference_voice")
    return {
        "scenario": _path_segment(identity["scenario"], f"{field}.scenario"),
        "character": _path_segment(identity["character"], f"{field}.character"),
        "role": role,
        "reference_voice": reference,
        "scene_setting": _text(identity["scene_setting"], f"{field}.scene_setting"),
    }


def _role_identity_document(role: RoleSnapshot) -> dict[str, Any]:
    return {
        "scenario": role.scenario,
        "character": role.character,
        "role": dict(role.role),
        "reference_voice": role.reference_voice,
        "scene_setting": role.scene_setting,
    }


def _relative_bundle_path(value: Any, field: str) -> str:
    text = _text(value, field)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or len(pure.parts) != 2
        or pure.parts[0] != "audio"
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in text
        or pure.suffix != ".wav"
        or pure.as_posix() != text
    ):
        raise CompletionAnchorError(f"{field}が不正なbundle audio pathです。")
    return pure.as_posix()


def _relative_child(root: Path, relative: str, field: str) -> Path:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in relative
    ):
        raise CompletionAnchorError(f"{field}は安全なrelative POSIX pathが必要です。")
    candidate = (root / Path(*pure.parts)).resolve()
    resolved_root = root.resolve()
    if candidate == resolved_root or resolved_root not in candidate.parents:
        raise CompletionAnchorError(f"{field}がroot外を指しています。")
    return candidate


def _read_canonical_json(path: Path, label: str) -> tuple[bytes, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CompletionAnchorError(f"{label}を読めません: {path}: {error}") from error

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CompletionAnchorError(f"{label}に重複keyがあります: {key}")
            result[key] = value
        return result

    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompletionAnchorError(f"{label}が不正なJSONです: {path}") from error
    try:
        canonical_raw = canonical_json(document).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CompletionAnchorError(f"{label}が不正なJSONです: {path}") from error
    if canonical_raw != raw:
        raise CompletionAnchorError(f"{label}はcanonical bytesが必要です。")
    return raw, document


def _verify_adjacent_sha256_marker(
    path: Path,
    expected: str,
    label: str,
) -> None:
    try:
        marker = path.read_bytes()
    except OSError as error:
        raise CompletionAnchorError(
            f"{label}の隣接SHA-256 markerを読めません: {path}: {error}",
        ) from error
    if marker != f"{expected}\n".encode("ascii"):
        raise CompletionAnchorError(f"{label}の隣接SHA-256 markerが一致しません。")


def _verify_file_sha256(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise CompletionAnchorError(f"{label}がありません: {path}")
    actual = _sha256_file(path)
    if actual != expected:
        raise CompletionAnchorError(
            f"{label} SHA-256が一致しません: expected={expected}, actual={actual}",
        )


def _verify_anchor_candidate_set_audio(
    *,
    candidate_set: Mapping[str, Any],
    artifacts_dir: Path,
    label: str,
) -> None:
    for group in candidate_set["groups"]:
        for candidate in group["candidates"]:
            _verify_file_sha256(
                _relative_child(
                    artifacts_dir,
                    candidate["audio_path"],
                    label,
                ),
                candidate["audio_sha256"],
                label,
            )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise CompletionAnchorError(f"fileをhashできません: {path}: {error}") from error
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _write_canonical_new(path: Path, document: Any) -> None:
    if path.exists():
        raise CompletionAnchorError(f"immutable outputが既に存在します: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_json(document).encode("utf-8")
    pending = path.with_name(f".{path.name}.pending")
    if pending.exists():
        raise CompletionAnchorError(f"pending outputが残っています: {pending}")
    try:
        pending.write_bytes(raw)
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def _require_new_output_directory(path: Path, label: str) -> None:
    if path.exists():
        raise CompletionAnchorError(f"{label}は新規directoryが必要です: {path}")
    if not path.parent.is_dir():
        raise CompletionAnchorError(
            f"{label}のparent directoryがありません: {path.parent}",
        )


def _new_pending_directory(output_dir: Path) -> Path:
    try:
        return Path(
            tempfile.mkdtemp(
                prefix=f".{output_dir.name}.pending-",
                dir=output_dir.parent,
            ),
        ).resolve()
    except OSError as error:
        raise CompletionAnchorError(
            f"pending directoryを作れません: {output_dir.parent}: {error}",
        ) from error


def _publish_pending_directory(pending: Path, output_dir: Path) -> None:
    if output_dir.exists():
        raise CompletionAnchorError(
            f"immutable outputが同時に作成されました: {output_dir}",
        )
    try:
        pending.rename(output_dir)
    except OSError as error:
        raise CompletionAnchorError(
            f"output directoryをatomic publishできません: {output_dir}: {error}",
        ) from error


def _assert_exact_directory_files(
    root: Path,
    expected_files: set[str],
    label: str,
) -> None:
    if not root.is_dir() or root.is_symlink():
        raise CompletionAnchorError(f"{label} directoryがありません: {root}")
    expected_directories = {
        PurePosixPath(relative).parent.as_posix()
        for relative in expected_files
        if PurePosixPath(relative).parent.as_posix() != "."
    }
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for entry in root.rglob("*"):
        relative = entry.relative_to(root).as_posix()
        if entry.is_symlink():
            raise CompletionAnchorError(
                f"{label}にsymlinkは使用できません: {relative}",
            )
        if entry.is_file():
            actual_files.add(relative)
        elif entry.is_dir():
            actual_directories.add(relative)
        else:
            raise CompletionAnchorError(
                f"{label}に通常file/directory以外があります: {relative}",
            )
    if actual_files != expected_files or actual_directories != expected_directories:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        extra_directories = sorted(actual_directories - expected_directories)
        raise CompletionAnchorError(
            f"{label} treeがexact contractと一致しません: "
            f"missing={missing[:5]}, extra={extra[:5]}, "
            f"extra_directories={extra_directories[:5]}",
        )


def _relative_anchor_artifact_path(value: Any, field: str) -> str:
    text = _text(value, field)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or len(pure.parts) < 7
        or pure.parts[:2] != ("role-anchors", "runs")
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in text
        or pure.suffix != ".wav"
        or pure.as_posix() != text
    ):
        raise CompletionAnchorError(f"{field}が不正なanchor artifact pathです。")
    return text


def _anchor_model(value: Any, field: str) -> str:
    model = _path_segment(value, field)
    if model not in _REVIEW_MODELS:
        raise CompletionAnchorError(f"{field}がAnchor review対象modelではありません。")
    return model


def _safe_segment(value: Any, field: str) -> str:
    text = _text(value, field)
    if (
        not text[0].isalnum()
        or not text[0].isascii()
        or any(
            not (character.isascii() and (character.islower() or character.isdigit()))
            and character != "-"
            for character in text
        )
    ):
        raise CompletionAnchorError(
            f"{field}は小文字英数字とhyphenの安全なsegmentが必要です。",
        )
    return text


def _trimmed_text(value: Any, field: str) -> str:
    text = _text(value, field)
    if text != text.strip():
        raise CompletionAnchorError(f"{field}は前後空白なしが必要です。")
    return text


def _enum(value: Any, allowed: set[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise CompletionAnchorError(f"{field}が許可値ではありません。")
    return value


def _non_negative_safe_integer(value: Any, field: str) -> int:
    integer = _integer(value, field)
    if not 0 <= integer <= _MAX_SAFE_INTEGER:
        raise CompletionAnchorError(
            f"{field}はJavaScript安全範囲の非負整数が必要です。"
        )
    return integer


def _require_absolute(path: Path, field: str) -> None:
    if not path.is_absolute():
        raise CompletionAnchorError(f"{field}は絶対pathが必要です: {path}")


def _exact(value: Any, fields: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise CompletionAnchorError(f"{field}の項目がexact contractと一致しません。")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CompletionAnchorError(f"{field}は空でない文字列が必要です。")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise CompletionAnchorError(f"{field}は文字列が必要です。")
    return value


def _path_segment(value: Any, field: str) -> str:
    text = _text(value, field)
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise CompletionAnchorError(f"{field}は安全なpath segmentが必要です。")
    return text


def _sha256(value: Any, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(character not in _HEX for character in text):
        raise CompletionAnchorError(f"{field}は完全な小文字SHA-256が必要です。")
    return text


def _sha256_array(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise CompletionAnchorError(f"{field}は1件以上の配列が必要です。")
    result = [_sha256(item, f"{field}[{index}]") for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        raise CompletionAnchorError(f"{field}に重複があります。")
    return result


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CompletionAnchorError(f"{field}はintegerが必要です。")
    return value


def _positive_integer(value: Any, field: str) -> int:
    integer = _integer(value, field)
    if integer < 1:
        raise CompletionAnchorError(f"{field}は1以上が必要です。")
    return integer
