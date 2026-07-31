from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from gaya_pipeline.take_identity import canonical_json


class CompletionPlanError(RuntimeError):
    pass


FORMAT_VERSION = 1
PROTOCOL = "baseline-completion-plan-v1"
BASE_MANIFEST_SHA256 = (
    "f9dfda542fd1120fe0f74daae3036eab5211d7394d155f7b9953978e59bbe89d"
)
BASE_MANIFEST_GIT_BLOB = "44061fafe330a9bebfed7a97a0b69ebe234c8724"
BASE_CANDIDATE_SET_SHA256 = (
    "91913e08f97497f1f7604f109a6d0f7308742237277f6bbc5483678ac9858cc2"
)
BASE_SELECTION_SHA256 = (
    "629cc80346160eb8e687757e6f792ef519da9a4fb74f79bdf97eb4d00f56126e"
)
TAKES = 4
SEED_BASE = 104
MINIMUM_ELIGIBLE_CANDIDATES = 3
TARGET_COUNTS = {
    "chatterbox-multilingual-v3": 1,
    "cosyvoice3-0.5b-2512": 2,
    "qwen3-tts-12hz-1.7b": 40,
    "voxcpm2": 2,
}
ROOT_FIELDS = {
    "format_version",
    "protocol",
    "base",
    "takes",
    "seed_base",
    "minimum_eligible_candidates",
    "targets",
}
BASE_FIELDS = {
    "manifest_sha256",
    "git_blob",
    "candidate_set_sha256",
    "selection_sha256",
}
TARGET_FIELDS = {"model", "scenario", "line", "variant", "prior_outcome"}
SKIPPED_PRIOR_FIELDS = {"decision", "curation_sha256"}
FAILURE_PRIOR_FIELDS = {"reason"}
CURATION_FIELDS = {
    "curation_sha256",
    "decision",
    "line",
    "model",
    "scenario",
    "variant",
}
SELECTED_CURATION_FIELDS = {*CURATION_FIELDS, "take_id"}
FAILURE_FIELDS = {"line", "model", "reason", "scenario", "variant"}
HEX = frozenset("0123456789abcdef")
GroupIdentity = tuple[str, str, str, str]


@dataclass(frozen=True)
class PriorOutcome:
    outcome: Literal["skipped", "failure"]
    curation_sha256: str | None
    reason: str | None


@dataclass(frozen=True)
class CompletionTarget:
    model: str
    scenario: str
    line: str
    variant: str
    prior: PriorOutcome

    @property
    def identity(self) -> GroupIdentity:
        return (self.model, self.scenario, self.line, self.variant)


@dataclass(frozen=True)
class CompletionPlan:
    plan_id: str
    base_manifest_sha256: str
    base_manifest_git_blob: str
    base_candidate_set_sha256: str
    base_selection_sha256: str
    takes: int
    seed_base: int
    minimum_eligible_candidates: int
    targets: tuple[CompletionTarget, ...]
    raw_sha256: str

    def targets_for_model(self, model_id: str) -> tuple[CompletionTarget, ...]:
        return tuple(target for target in self.targets if target.model == model_id)

    def target_lines_for_model(self, model_id: str) -> tuple[tuple[str, str], ...]:
        return tuple(
            (target.scenario, target.line)
            for target in self.targets_for_model(model_id)
        )


def load_completion_plan(
    plan_path: Path,
    *,
    base_manifest_path: Path,
) -> CompletionPlan:
    plan_raw = _read_bytes(plan_path, "completion plan")
    plan_document = _read_json(plan_raw, plan_path, "completion plan")
    normalized, targets = _validate_plan(plan_document)
    canonical = canonical_json(normalized).encode("utf-8")
    if plan_raw != canonical:
        raise CompletionPlanError(
            "completion plan は canonical bytes である必要があります。",
        )
    plan_id = hashlib.sha256(plan_raw).hexdigest()

    base_raw = _read_bytes(base_manifest_path, "base manifest")
    base_sha256 = hashlib.sha256(base_raw).hexdigest()
    base = normalized["base"]
    if base_sha256 != base["manifest_sha256"]:
        raise CompletionPlanError("base manifest raw SHA-256 が plan と一致しません。")
    git_blob = hashlib.sha1(  # noqa: S324 - Git object identity is SHA-1 by design.
        f"blob {len(base_raw)}\0".encode("ascii") + base_raw,
    ).hexdigest()
    if git_blob != base["git_blob"]:
        raise CompletionPlanError("base manifest Git blob が plan と一致しません。")

    manifest = _read_json(base_raw, base_manifest_path, "base manifest")
    if canonical_json(manifest).encode("utf-8") != base_raw:
        raise CompletionPlanError("base manifest は canonical bytes ではありません。")
    _validate_targets_against_manifest(
        targets=targets,
        manifest=manifest,
        expected_candidate_set_sha256=base["candidate_set_sha256"],
        expected_selection_sha256=base["selection_sha256"],
    )
    return CompletionPlan(
        plan_id=plan_id,
        base_manifest_sha256=base["manifest_sha256"],
        base_manifest_git_blob=base["git_blob"],
        base_candidate_set_sha256=base["candidate_set_sha256"],
        base_selection_sha256=base["selection_sha256"],
        takes=normalized["takes"],
        seed_base=normalized["seed_base"],
        minimum_eligible_candidates=normalized["minimum_eligible_candidates"],
        targets=targets,
        raw_sha256=hashlib.sha256(plan_raw).hexdigest(),
    )


def compute_completion_plan_id(document: Any) -> str:
    normalized, _targets = _validate_plan(document)
    return hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()


def _validate_plan(
    document: Any,
) -> tuple[dict[str, Any], tuple[CompletionTarget, ...]]:
    root = _exact(document, ROOT_FIELDS, "completion plan")
    if root["format_version"] != FORMAT_VERSION:
        raise CompletionPlanError(
            f"completion plan format_version は {FORMAT_VERSION} が必要です。",
        )
    if root["protocol"] != PROTOCOL:
        raise CompletionPlanError(
            f"completion plan protocol は {PROTOCOL} が必要です。",
        )
    base_source = _exact(root["base"], BASE_FIELDS, "completion plan.base")
    base = {
        "manifest_sha256": _fixed_sha256(
            base_source["manifest_sha256"],
            BASE_MANIFEST_SHA256,
            "completion plan.base.manifest_sha256",
        ),
        "git_blob": _fixed_git_blob(
            base_source["git_blob"],
            BASE_MANIFEST_GIT_BLOB,
            "completion plan.base.git_blob",
        ),
        "candidate_set_sha256": _fixed_sha256(
            base_source["candidate_set_sha256"],
            BASE_CANDIDATE_SET_SHA256,
            "completion plan.base.candidate_set_sha256",
        ),
        "selection_sha256": _fixed_sha256(
            base_source["selection_sha256"],
            BASE_SELECTION_SHA256,
            "completion plan.base.selection_sha256",
        ),
    }
    takes = _fixed_integer(root["takes"], TAKES, "completion plan.takes")
    seed_base = _fixed_integer(
        root["seed_base"],
        SEED_BASE,
        "completion plan.seed_base",
    )
    minimum = _fixed_integer(
        root["minimum_eligible_candidates"],
        MINIMUM_ELIGIBLE_CANDIDATES,
        "completion plan.minimum_eligible_candidates",
    )
    target_values = root["targets"]
    if not isinstance(target_values, list):
        raise CompletionPlanError("completion plan.targets は配列が必要です。")
    targets = tuple(
        _validate_target(value, f"completion plan.targets[{index}]")
        for index, value in enumerate(target_values)
    )
    identities = tuple(target.identity for target in targets)
    if len(set(identities)) != len(identities):
        raise CompletionPlanError("completion plan.targets に重複があります。")
    if identities != tuple(sorted(identities)):
        raise CompletionPlanError(
            "completion plan.targets は group identity の canonical 順が必要です。",
        )
    counts = {
        model: sum(target.model == model for target in targets)
        for model in TARGET_COUNTS
    }
    if counts != TARGET_COUNTS or len(targets) != sum(TARGET_COUNTS.values()):
        raise CompletionPlanError("completion plan.targets の model 別件数が不正です。")

    normalized = {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "base": base,
        "takes": takes,
        "seed_base": seed_base,
        "minimum_eligible_candidates": minimum,
        "targets": [_target_document(target) for target in targets],
    }
    return normalized, targets


def _validate_target(value: Any, field: str) -> CompletionTarget:
    target = _exact(value, TARGET_FIELDS, field)
    model = _path_segment(target["model"], f"{field}.model")
    scenario = _path_segment(target["scenario"], f"{field}.scenario")
    line = _path_segment(target["line"], f"{field}.line")
    variant = _path_segment(target["variant"], f"{field}.variant")
    if variant != "dry":
        raise CompletionPlanError(f"{field}.variant は dry が必要です。")
    prior_source = target["prior_outcome"]
    if not isinstance(prior_source, dict):
        raise CompletionPlanError(f"{field}.prior_outcome は object が必要です。")
    if set(prior_source) == SKIPPED_PRIOR_FIELDS:
        prior = _exact(
            prior_source,
            SKIPPED_PRIOR_FIELDS,
            f"{field}.prior_outcome",
        )
        if prior["decision"] != "skipped":
            raise CompletionPlanError(
                f"{field}.prior_outcome.decision は skipped が必要です。",
            )
        prior_outcome = PriorOutcome(
            outcome="skipped",
            curation_sha256=_sha256(
                prior["curation_sha256"],
                f"{field}.prior_outcome.curation_sha256",
            ),
            reason=None,
        )
    elif set(prior_source) == FAILURE_PRIOR_FIELDS:
        prior = _exact(
            prior_source,
            FAILURE_PRIOR_FIELDS,
            f"{field}.prior_outcome",
        )
        reason = _text(prior["reason"], f"{field}.prior_outcome.reason")
        if reason != "no_eligible_take":
            raise CompletionPlanError(
                f"{field}.prior_outcome.reason は no_eligible_take が必要です。",
            )
        prior_outcome = PriorOutcome(
            outcome="failure",
            curation_sha256=None,
            reason=reason,
        )
    else:
        raise CompletionPlanError(f"{field}.prior_outcome が不正です。")
    return CompletionTarget(
        model=model,
        scenario=scenario,
        line=line,
        variant=variant,
        prior=prior_outcome,
    )


def _validate_targets_against_manifest(
    *,
    targets: tuple[CompletionTarget, ...],
    manifest: Any,
    expected_candidate_set_sha256: str,
    expected_selection_sha256: str,
) -> None:
    if not isinstance(manifest, dict):
        raise CompletionPlanError("base manifest は object が必要です。")
    if manifest.get("candidate_set_sha256") != expected_candidate_set_sha256:
        raise CompletionPlanError(
            "base manifest candidate_set_sha256 が plan と一致しません。",
        )
    curations = manifest.get("curations")
    failures = manifest.get("failures")
    if not isinstance(curations, list) or not isinstance(failures, list):
        raise CompletionPlanError(
            "base manifest curations/failures は配列が必要です。",
        )

    selected: set[GroupIdentity] = set()
    incomplete: dict[GroupIdentity, PriorOutcome] = {}
    for index, value in enumerate(curations):
        field = f"base manifest.curations[{index}]"
        if not isinstance(value, dict):
            raise CompletionPlanError(f"{field} は object が必要です。")
        decision = value.get("decision")
        expected_fields = (
            SELECTED_CURATION_FIELDS
            if decision == "selected"
            else CURATION_FIELDS
        )
        curation = _exact(value, expected_fields, field)
        identity = _manifest_identity(curation, field)
        if identity in selected or identity in incomplete:
            raise CompletionPlanError(
                f"base manifest の terminal group が重複しています: {identity}",
            )
        if curation["curation_sha256"] != expected_selection_sha256:
            raise CompletionPlanError(
                f"{field}.curation_sha256 が base selection SHA と一致しません。",
            )
        if decision == "selected":
            selected.add(identity)
        elif decision == "skipped":
            incomplete[identity] = PriorOutcome(
                outcome="skipped",
                curation_sha256=expected_selection_sha256,
                reason=None,
            )
        else:
            raise CompletionPlanError(f"{field}.decision が不正です。")

    for index, value in enumerate(failures):
        field = f"base manifest.failures[{index}]"
        failure = _exact(value, FAILURE_FIELDS, field)
        identity = _manifest_identity(failure, field)
        reason = _text(failure["reason"], f"{field}.reason")
        if identity in selected or identity in incomplete:
            raise CompletionPlanError(
                f"base manifest の terminal group が重複しています: {identity}",
            )
        incomplete[identity] = PriorOutcome(
            outcome="failure",
            curation_sha256=None,
            reason=reason,
        )

    target_map = {target.identity: target.prior for target in targets}
    selected_targets = set(target_map) & selected
    if selected_targets:
        raise CompletionPlanError(
            f"completion plan は selected group を対象にできません: "
            f"{sorted(selected_targets)}",
        )
    if target_map != incomplete:
        missing = sorted(set(incomplete) - set(target_map))
        extra = sorted(set(target_map) - set(incomplete))
        mismatched = sorted(
            identity
            for identity in set(target_map) & set(incomplete)
            if target_map[identity] != incomplete[identity]
        )
        raise CompletionPlanError(
            "completion plan targets が base manifest の全非 selected slot と"
            f"一致しません: missing={missing}, extra={extra}, "
            f"prior_mismatch={mismatched}",
        )


def _target_document(target: CompletionTarget) -> dict[str, Any]:
    if target.prior.outcome == "skipped":
        prior = {
            "decision": "skipped",
            "curation_sha256": target.prior.curation_sha256,
        }
    else:
        prior = {
            "reason": target.prior.reason,
        }
    return {
        "model": target.model,
        "scenario": target.scenario,
        "line": target.line,
        "variant": target.variant,
        "prior_outcome": prior,
    }


def _manifest_identity(value: dict[str, Any], field: str) -> GroupIdentity:
    return (
        _path_segment(value["model"], f"{field}.model"),
        _path_segment(value["scenario"], f"{field}.scenario"),
        _path_segment(value["line"], f"{field}.line"),
        _path_segment(value["variant"], f"{field}.variant"),
    )


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise CompletionPlanError(f"{label} を読み込めません: {path}: {error}") from error


def _read_json(raw: bytes, path: Path, label: str) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CompletionPlanError(
                    f"{label} に重複 JSON key があります: {key}",
                )
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompletionPlanError(f"{label} が不正な JSON です: {path}: {error}") from error


def _exact(value: Any, fields: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise CompletionPlanError(f"{field} の項目が exact contract と一致しません。")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CompletionPlanError(f"{field} は空でない文字列が必要です。")
    return value


def _path_segment(value: Any, field: str) -> str:
    text = _text(value, field)
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise CompletionPlanError(f"{field} は安全な path segment が必要です。")
    return text


def _sha256(value: Any, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(character not in HEX for character in text):
        raise CompletionPlanError(f"{field} は完全な小文字 SHA-256 が必要です。")
    return text


def _fixed_sha256(value: Any, expected: str, field: str) -> str:
    sha256 = _sha256(value, field)
    if sha256 != expected:
        raise CompletionPlanError(f"{field} は固定 baseline と一致しません。")
    return sha256


def _fixed_git_blob(value: Any, expected: str, field: str) -> str:
    text = _text(value, field)
    if len(text) != 40 or any(character not in HEX for character in text):
        raise CompletionPlanError(f"{field} は完全な小文字 Git blob が必要です。")
    if text != expected:
        raise CompletionPlanError(f"{field} は固定 baseline と一致しません。")
    return text


def _fixed_integer(value: Any, expected: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise CompletionPlanError(f"{field} は {expected} が必要です。")
    return value
