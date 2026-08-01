from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

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
    "CompletionAnchorError",
    "RoleAnchorSelectionSummary",
    "RoleReviewBundleSummary",
    "SelectedRoleAnchor",
    "build_role_review_bundle_v2",
    "finalize_role_anchor_selection",
    "load_anchor_review_plan",
    "load_anchor_source_plan",
    "resolve_selected_anchor",
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


def _validate_anchor_candidate_set_v1(
    document: Any,
    *,
    plan: CompletionPlan | AnchorReviewPlan,
    candidate_set_sha256: str,
) -> dict[str, Any]:
    expected_source_plan_sha256 = _sha256(
        plan.anchor_source_plan_sha256,
        "completion plan.anchor_source_plan_sha256",
    )
    expected_candidate_set_sha256 = _sha256(
        plan.anchor_candidate_set_sha256,
        "completion plan.anchor_candidate_set_sha256",
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
        if attempts_value != [1, 2, 3, 4]:
            raise CompletionAnchorError(
                f"{field}.attemptsはexactly [1,2,3,4]が必要です。",
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
            expected_attempt = candidate_index + 1
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
                "attempts": [1, 2, 3, 4],
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
        expected_attempt = index + 1
        if candidate["attempt"] != expected_attempt:
            raise CompletionAnchorError(
                f"{candidate_field}.attemptは{expected_attempt}が必要です。",
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
                "qc": _validate_review_qc(
                    candidate["qc"],
                    f"{candidate_field}.qc",
                ),
            },
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


def _validate_role_review_decision_group_v2(
    value: Any,
    *,
    field: str,
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
        "rubric": _validate_role_review_rubric_v2(
            decision["rubric"],
            f"{field}.rubric",
        ),
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
