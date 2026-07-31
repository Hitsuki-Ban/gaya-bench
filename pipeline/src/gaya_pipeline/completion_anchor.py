from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from gaya_pipeline.adapters.base import ModelProfile
from gaya_pipeline.completion_plan import (
    IRODORI_MODEL,
    QWEN_MODEL,
    AnchorTarget,
    CompletionPlan,
    RoleSnapshot,
    derive_anchor_seed,
)
from gaya_pipeline.take_identity import canonical_json


class CompletionAnchorError(RuntimeError):
    pass


RUN_FORMAT_VERSION = 1
RUN_PROTOCOL = "role-anchor-run-v1"
CANDIDATE_SET_FORMAT_VERSION = 1
CANDIDATE_SET_PROTOCOL = "role-anchor-candidate-set-v1"
TOPUP_FORMAT_VERSION = 1
TOPUP_PROTOCOL = "role-anchor-topup-v1"
REVIEW_FORMAT_VERSION = 1
REVIEW_PROTOCOL = "role-review-v1"
DECISION_FORMAT_VERSION = 1
DECISION_PROTOCOL = "role-review-decision-v1"
SELECTION_FORMAT_VERSION = 1
SELECTION_PROTOCOL = "role-anchor-selection-v1"
PHASE = "anchor"

RUN_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_sha256",
    "run_id",
    "model",
    "model_revision",
    "kind",
    "source_candidate_set_sha256",
    "attempts",
}
RUN_ATTEMPT_FIELDS = {
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
QC_FIELDS = {"mechanical", "content", "notes"}
CANDIDATE_SET_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_sha256",
    "runs",
    "groups",
}
CANDIDATE_GROUP_FIELDS = {
    "model",
    "model_revision",
    "scenario",
    "character",
    "role_identity_sha256",
    "role_epoch_sha256",
    "attempts",
    "candidates",
}
CANDIDATE_FIELDS = {
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
TOPUP_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_sha256",
    "candidate_set_sha256",
    "targets",
}
TOPUP_TARGET_FIELDS = {
    "model",
    "scenario",
    "character",
    "role_identity_sha256",
    "role_epoch_sha256",
    "attempt",
    "seed",
}
REVIEW_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "phase",
    "plan_sha256",
    "candidate_set_sha256",
    "groups",
}
REVIEW_GROUP_FIELDS = {
    "id",
    "model",
    "scenario",
    "character",
    "line",
    "role_epoch_sha256",
    "role",
    "conditioning",
    "coverage",
    "comparison_required",
    "comparison_reasons",
    "candidate_ids",
    "provisional_candidate_id",
    "candidates",
}
REVIEW_CONDITIONING_FIELDS = {"method", "summary"}
REVIEW_COVERAGE_FIELDS = {"gender", "age", "archetype"}
REVIEW_CANDIDATE_FIELDS = {
    "id",
    "attempt",
    "seed",
    "audio_path",
    "audio_sha256",
    "qc",
}
DECISION_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "phase",
    "plan_sha256",
    "candidate_set_sha256",
    "groups",
    "role_reopen_requests",
}
DECISION_GROUP_FIELDS = {
    "id",
    "model",
    "scenario",
    "character",
    "line",
    "role_epoch_sha256",
    "group_sha256",
    "heard_candidate_ids",
    "selected_candidate_id",
    "rubric",
    "confirmed",
}
DECISION_RUBRIC_FIELDS = {
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
ROLE_REOPEN_FIELDS = {
    "model",
    "character",
    "role_epoch_sha256",
    "reason",
}
SELECTION_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_sha256",
    "candidate_set_sha256",
    "groups",
}
SELECTION_GROUP_FIELDS = {
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
ROLE_IDENTITY_FIELDS = {
    "scenario",
    "character",
    "role",
    "reference_voice",
    "scene_setting",
}
ROLE_FIELDS = {
    "name",
    "kind",
    "gender",
    "age",
    "archetype",
    "voice",
    "personality",
}
HEX = frozenset("0123456789abcdef")
_ALLOWED_COVERAGE = {"exact", "approximate", "neutral"}
_ALLOWED_RUBRIC_RESULTS = {"pass", "fail", "not_applicable"}


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
class AnchorGenerationSummary:
    run_id: str
    ledger_path: Path
    eligible_count: int
    rejected_count: int
    failed_count: int


@dataclass(frozen=True)
class AnchorCandidateSetSummary:
    path: Path
    candidate_set_sha256: str
    group_count: int
    eligible_count: int


@dataclass(frozen=True)
class AnchorTopupSummary:
    path: Path
    target_count: int


@dataclass(frozen=True)
class AnchorListeningSummary:
    output_dir: Path
    review_path: Path
    group_count: int
    candidate_count: int


@dataclass(frozen=True)
class AnchorSelectionSummary:
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


def run_anchor_generation(
    *,
    plan: CompletionPlan,
    model_id: str,
    run_id: str,
    artifacts_dir: Path,
    generator: RoleAnchorGenerator | None = None,
    topup_plan_path: Path | None = None,
    candidate_set_path: Path | None = None,
) -> AnchorGenerationSummary:
    _require_absolute(artifacts_dir, "artifacts")
    run_id = _path_segment(run_id, "run_id")
    targets = plan.anchor_targets_for_model(model_id)
    if not targets:
        raise CompletionAnchorError(f"Phase A対象外modelです: {model_id}")
    revision = _model_revision(plan, model_id)
    effective_generator = (
        _create_anchor_generator(model_id) if generator is None else generator
    )
    if (
        effective_generator.profile.id != model_id
        or effective_generator.profile.version != revision
    ):
        raise CompletionAnchorError(
            "anchor generatorのmodel/revisionがplanと一致しません。",
        )

    if topup_plan_path is None and candidate_set_path is not None:
        raise CompletionAnchorError(
            "candidate setはtopup planと同時に指定する必要があります。",
        )
    if topup_plan_path is not None and candidate_set_path is None:
        raise CompletionAnchorError(
            "topup generationにはcandidate setの明示が必要です。",
        )
    if topup_plan_path is None:
        kind = "initial"
        source_candidate_set_sha256 = None
        attempts = [
            (target, attempt)
            for target in targets
            for attempt in range(1, plan.phase_a_takes + 1)
        ]
    else:
        assert candidate_set_path is not None
        _require_absolute(topup_plan_path, "topup plan")
        _require_absolute(candidate_set_path, "candidate set")
        candidate_raw, candidate_document = _read_canonical_json(
            candidate_set_path,
            "anchor candidate set",
        )
        normalized_candidate_set = validate_anchor_candidate_set(
            candidate_document,
            plan=plan,
        )
        candidate_sha256 = hashlib.sha256(candidate_raw).hexdigest()
        topup_raw, topup_document = _read_canonical_json(
            topup_plan_path,
            "anchor topup plan",
        )
        del topup_raw
        normalized_topup = validate_anchor_topup_plan(
            topup_document,
            plan=plan,
            candidate_set=normalized_candidate_set,
        )
        # The explicit source set must remain the exact file validated above.
        validate_anchor_candidate_set(normalized_candidate_set, plan=plan)
        kind = "topup"
        source_candidate_set_sha256 = candidate_sha256
        target_map = {target.identity: target for target in targets}
        attempts = []
        for item in normalized_topup["targets"]:
            if item["model"] != model_id:
                continue
            target = target_map.get(
                (item["model"], item["scenario"], item["character"]),
            )
            if target is None:
                raise CompletionAnchorError(
                    "topup targetがmodelのPhase A対象と一致しません。",
                )
            attempts.append((target, item["attempt"]))
        if not attempts:
            raise CompletionAnchorError(
                f"topup planにmodel対象がありません: {model_id}",
            )

    run_root = artifacts_dir / "role-anchors" / "runs" / run_id
    if run_root.exists():
        raise CompletionAnchorError(
            f"anchor run directoryは新規である必要があります: {run_root}",
        )
    run_root.mkdir(parents=True)
    records: list[dict[str, Any]] = []
    seen_seeds: set[int] = set()
    try:
        for target, attempt in attempts:
            role = plan.role(target.scenario, target.character)
            if role.reference_voice is not None:
                raise CompletionAnchorError(
                    "明示reference roleはanchor生成対象にできません。",
                )
            seed = derive_anchor_seed(
                plan_sha256=plan.plan_id,
                seed_base=plan.phase_a_seed_base,
                model=model_id,
                scenario=target.scenario,
                character=target.character,
                attempt=attempt,
            )
            if seed in seen_seeds:
                raise CompletionAnchorError("同一run内のanchor seedが重複しています。")
            seen_seeds.add(seed)
            generation_input = _anchor_generation_input(
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
                target.scenario,
                target.character,
                f"attempt-{attempt:04d}",
            )
            output_wav = artifacts_dir / Path(f"{relative_base.as_posix()}.wav")
            sidecar_path = artifacts_dir / Path(f"{relative_base.as_posix()}.json")
            output_wav.parent.mkdir(parents=True, exist_ok=True)
            if output_wav.exists() or sidecar_path.exists():
                raise CompletionAnchorError(
                    f"anchor candidate pathが既に存在します: {relative_base}",
                )
            try:
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
                        f"anchor generatorがWAVを書き込みませんでした: {output_wav}",
                    )
                audio_sha256 = _sha256_file(output_wav)
                qc = _mechanical_qc(output_wav)
                anchor_id = _canonical_sha256(
                    {
                        "protocol": "role-anchor-identity-v1",
                        "plan_sha256": plan.plan_id,
                        "model": model_id,
                        "model_revision": revision,
                        "scenario": target.scenario,
                        "character": target.character,
                        "role_identity_sha256": target.role_identity_sha256,
                        "role_epoch_sha256": target.role_epoch_sha256,
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
                    "scenario": target.scenario,
                    "character": target.character,
                    "role_identity_sha256": target.role_identity_sha256,
                    "role_epoch_sha256": target.role_epoch_sha256,
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
            except Exception as error:
                if output_wav.exists():
                    output_wav.unlink()
                if sidecar_path.exists():
                    sidecar_path.unlink()
                record = {
                    "model": model_id,
                    "model_revision": revision,
                    "scenario": target.scenario,
                    "character": target.character,
                    "role_identity_sha256": target.role_identity_sha256,
                    "role_epoch_sha256": target.role_epoch_sha256,
                    "attempt": attempt,
                    "seed": seed,
                    "generation_input": generation_input,
                    "generation_input_sha256": generation_input_sha256,
                    "status": "failed",
                    "anchor_id": None,
                    "audio_path": None,
                    "audio_sha256": None,
                    "qc": {
                        "mechanical": "fail",
                        "content": "not_checked",
                        "notes": ["generation_failed"],
                    },
                    "realized": None,
                    "error": str(error),
                }
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
    ledger = {
        "format_version": RUN_FORMAT_VERSION,
        "protocol": RUN_PROTOCOL,
        "plan_sha256": plan.plan_id,
        "run_id": run_id,
        "model": model_id,
        "model_revision": revision,
        "kind": kind,
        "source_candidate_set_sha256": source_candidate_set_sha256,
        "attempts": records,
    }
    normalized = validate_anchor_run(ledger, plan=plan)
    ledger_path = run_root / "ledger.json"
    _write_canonical_new(ledger_path, normalized)
    return AnchorGenerationSummary(
        run_id=run_id,
        ledger_path=ledger_path,
        eligible_count=sum(item["status"] == "eligible" for item in records),
        rejected_count=sum(item["status"] == "rejected" for item in records),
        failed_count=sum(item["status"] == "failed" for item in records),
    )


def merge_anchor_runs(
    *,
    plan: CompletionPlan,
    run_ids: Sequence[str],
    artifacts_dir: Path,
    output_path: Path,
) -> AnchorCandidateSetSummary:
    _require_absolute(artifacts_dir, "artifacts")
    _require_absolute(output_path, "candidate set output")
    if not run_ids or len(set(run_ids)) != len(run_ids):
        raise CompletionAnchorError("run_idsは重複のない1件以上が必要です。")
    normalized_run_ids = sorted(_path_segment(value, "run_id") for value in run_ids)
    attempts_by_slot: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    seen_anchor_ids: set[str] = set()
    seen_paths: set[str] = set()
    seen_seeds: set[int] = set()
    for run_id in normalized_run_ids:
        ledger_path = (
            artifacts_dir / "role-anchors" / "runs" / run_id / "ledger.json"
        )
        _raw, document = _read_canonical_json(ledger_path, "anchor run ledger")
        ledger = validate_anchor_run(document, plan=plan)
        if ledger["run_id"] != run_id:
            raise CompletionAnchorError("run idとledger identityが一致しません。")
        for attempt in ledger["attempts"]:
            slot = (
                attempt["model"],
                attempt["scenario"],
                attempt["character"],
                attempt["attempt"],
            )
            if slot in attempts_by_slot:
                raise CompletionAnchorError(
                    f"anchor attempt slotがrun間で衝突しています: {slot}",
                )
            attempts_by_slot[slot] = attempt
            if attempt["seed"] in seen_seeds:
                raise CompletionAnchorError("anchor seedがrun間で重複しています。")
            seen_seeds.add(attempt["seed"])
            if attempt["status"] != "eligible":
                continue
            assert attempt["anchor_id"] is not None
            assert attempt["audio_path"] is not None
            assert attempt["audio_sha256"] is not None
            if attempt["anchor_id"] in seen_anchor_ids:
                raise CompletionAnchorError("anchor_idが重複しています。")
            if attempt["audio_path"] in seen_paths:
                raise CompletionAnchorError("anchor audio pathが重複しています。")
            seen_anchor_ids.add(attempt["anchor_id"])
            seen_paths.add(attempt["audio_path"])
            audio_path = _artifact_path(artifacts_dir, attempt["audio_path"])
            _verify_file_sha256(
                audio_path,
                attempt["audio_sha256"],
                "anchor candidate WAV",
            )

    groups: list[dict[str, Any]] = []
    for target in plan.anchor_targets:
        revision = _model_revision(plan, target.model)
        attempted = sorted(
            slot[3]
            for slot in attempts_by_slot
            if slot[:3] == target.identity
        )
        candidates = [
            _candidate_from_attempt(attempt)
            for slot, attempt in attempts_by_slot.items()
            if slot[:3] == target.identity and attempt["status"] == "eligible"
        ]
        candidates.sort(key=lambda item: item["attempt"])
        groups.append(
            {
                "model": target.model,
                "model_revision": revision,
                "scenario": target.scenario,
                "character": target.character,
                "role_identity_sha256": target.role_identity_sha256,
                "role_epoch_sha256": target.role_epoch_sha256,
                "attempts": attempted,
                "candidates": candidates,
            },
        )
    candidate_set = {
        "format_version": CANDIDATE_SET_FORMAT_VERSION,
        "protocol": CANDIDATE_SET_PROTOCOL,
        "plan_sha256": plan.plan_id,
        "runs": normalized_run_ids,
        "groups": groups,
    }
    normalized = validate_anchor_candidate_set(candidate_set, plan=plan)
    _write_canonical_new(output_path, normalized)
    raw = output_path.read_bytes()
    return AnchorCandidateSetSummary(
        path=output_path,
        candidate_set_sha256=hashlib.sha256(raw).hexdigest(),
        group_count=len(groups),
        eligible_count=sum(len(group["candidates"]) for group in groups),
    )


def build_anchor_topup_plan(
    *,
    plan: CompletionPlan,
    candidate_set_path: Path,
    output_path: Path,
) -> AnchorTopupSummary:
    _require_absolute(candidate_set_path, "candidate set")
    _require_absolute(output_path, "topup output")
    raw, document = _read_canonical_json(candidate_set_path, "anchor candidate set")
    candidate_set = validate_anchor_candidate_set(document, plan=plan)
    candidate_set_sha256 = hashlib.sha256(raw).hexdigest()
    targets = _expected_anchor_topup_targets(
        plan=plan,
        candidate_set=candidate_set,
    )
    if not targets:
        raise CompletionAnchorError("topup対象はありません。")
    topup = {
        "format_version": TOPUP_FORMAT_VERSION,
        "protocol": TOPUP_PROTOCOL,
        "plan_sha256": plan.plan_id,
        "candidate_set_sha256": candidate_set_sha256,
        "targets": targets,
    }
    normalized = validate_anchor_topup_plan(
        topup,
        plan=plan,
        candidate_set=candidate_set,
    )
    _write_canonical_new(output_path, normalized)
    return AnchorTopupSummary(path=output_path, target_count=len(targets))


def build_anchor_listening_bundle(
    *,
    plan: CompletionPlan,
    candidate_set_path: Path,
    artifacts_dir: Path,
    output_dir: Path,
) -> AnchorListeningSummary:
    _require_absolute(candidate_set_path, "candidate set")
    _require_absolute(artifacts_dir, "artifacts")
    _require_absolute(output_dir, "listening output")
    if output_dir.exists():
        raise CompletionAnchorError(
            f"listening outputは新規directoryが必要です: {output_dir}",
        )
    candidate_raw, candidate_document = _read_canonical_json(
        candidate_set_path,
        "anchor candidate set",
    )
    candidate_set = validate_anchor_candidate_set(candidate_document, plan=plan)
    candidate_set_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    deficient = [
        (
            group["model"],
            group["scenario"],
            group["character"],
            len(group["candidates"]),
        )
        for group in candidate_set["groups"]
        if len(group["candidates"])
        < plan.phase_a_minimum_eligible_candidates
    ]
    if deficient:
        raise CompletionAnchorError(
            "anchor listeningは全106 groupにmechanical-pass candidateが"
            f"{plan.phase_a_minimum_eligible_candidates}件以上必要です: "
            f"{deficient[:5]}",
        )

    review_groups: list[dict[str, Any]] = []
    copy_jobs: list[tuple[Path, Path, str]] = []
    for group in candidate_set["groups"]:
        review_group = _review_group_document(plan=plan, group=group)
        for candidate, review_candidate in zip(
            group["candidates"],
            review_group["candidates"],
            strict=True,
        ):
            source = _artifact_path(artifacts_dir, candidate["audio_path"])
            _verify_file_sha256(
                source,
                candidate["audio_sha256"],
                "anchor listening source",
            )
            destination = output_dir / Path(review_candidate["audio_path"])
            copy_jobs.append((source, destination, candidate["audio_sha256"]))
        review_groups.append(review_group)

    review = validate_role_review(
        {
            "format_version": REVIEW_FORMAT_VERSION,
            "protocol": REVIEW_PROTOCOL,
            "phase": PHASE,
            "plan_sha256": plan.plan_id,
            "candidate_set_sha256": candidate_set_sha256,
            "groups": review_groups,
        },
    )
    output_dir.mkdir(parents=True)
    try:
        for source, destination, sha256 in copy_jobs:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise CompletionAnchorError(
                    f"listening audio destinationが重複しています: {destination}",
                )
            shutil.copy2(source, destination)
            _verify_file_sha256(destination, sha256, "copied listening audio")
        review_path = output_dir / "role-review-v1.json"
        _write_canonical_new(review_path, review)
    except Exception:
        shutil.rmtree(output_dir)
        raise
    return AnchorListeningSummary(
        output_dir=output_dir,
        review_path=review_path,
        group_count=len(review_groups),
        candidate_count=sum(
            len(group["candidates"]) for group in candidate_set["groups"]
        ),
    )


def finalize_anchor_selection(
    *,
    plan: CompletionPlan,
    candidate_set_path: Path,
    decision_path: Path,
    artifacts_dir: Path,
    output_dir: Path,
) -> AnchorSelectionSummary:
    for path, label in (
        (candidate_set_path, "candidate set"),
        (decision_path, "decision"),
        (artifacts_dir, "artifacts"),
        (output_dir, "selection output"),
    ):
        _require_absolute(path, label)
    if output_dir.exists():
        raise CompletionAnchorError(
            f"selection outputは新規directoryが必要です: {output_dir}",
        )
    candidate_raw, candidate_document = _read_canonical_json(
        candidate_set_path,
        "anchor candidate set",
    )
    candidate_set = validate_anchor_candidate_set(candidate_document, plan=plan)
    candidate_set_sha256 = hashlib.sha256(candidate_raw).hexdigest()
    deficient = [
        (
            group["model"],
            group["scenario"],
            group["character"],
            len(group["candidates"]),
        )
        for group in candidate_set["groups"]
        if len(group["candidates"])
        < plan.phase_a_minimum_eligible_candidates
    ]
    if deficient:
        raise CompletionAnchorError(
            "anchor finalizeは全106 groupにmechanical-pass candidateが"
            f"{plan.phase_a_minimum_eligible_candidates}件以上必要です: "
            f"{deficient[:5]}",
        )
    _decision_raw, decision_document = _read_canonical_json(
        decision_path,
        "anchor decision",
    )
    decision = validate_anchor_decision(decision_document)
    if decision["role_reopen_requests"]:
        raise CompletionAnchorError(
            "anchor decisionに未解決のrole reopen requestが残っています。",
        )
    if decision["plan_sha256"] != plan.plan_id:
        raise CompletionAnchorError("decision plan SHAがplanと一致しません。")
    if decision["candidate_set_sha256"] != candidate_set_sha256:
        raise CompletionAnchorError(
            "decision candidate-set SHAが明示candidate setと一致しません。",
        )

    groups_by_id = {
        _review_group_id(group): group for group in candidate_set["groups"]
    }
    if len(decision["groups"]) != 106 or {
        group["id"] for group in decision["groups"]
    } != set(groups_by_id):
        raise CompletionAnchorError(
            "anchor decisionは全106 groupとexactに一致する必要があります。",
        )
    selection_groups: list[dict[str, Any]] = []
    copy_jobs: list[tuple[Path, Path, str]] = []
    for decision_group in decision["groups"]:
        group = groups_by_id[decision_group["id"]]
        candidate = next(
            (
                item
                for item in group["candidates"]
                if item["id"] == decision_group["selected_candidate_id"]
            ),
            None,
        )
        if candidate is None:
            raise CompletionAnchorError(
                "decision selected_candidate_idが同一candidate groupにありません。",
            )
        review_group = _review_group_document(plan=plan, group=group)
        _verify_decision_candidate(
            decision_group,
            review_group=review_group,
            candidate=candidate,
        )
        role = plan.role(group["scenario"], group["character"])
        source = _artifact_path(artifacts_dir, candidate["audio_path"])
        _verify_file_sha256(source, candidate["audio_sha256"], "selected anchor")
        relative = f"audio/{candidate['id']}.wav"
        destination = output_dir / Path(relative)
        copy_jobs.append((source, destination, candidate["audio_sha256"]))
        decision_sha256 = _canonical_sha256(decision_group)
        selected_role_epoch_sha256 = _canonical_sha256(
            {
                "protocol": "selected-role-epoch-v1",
                "model": group["model"],
                "model_revision": group["model_revision"],
                "scenario": group["scenario"],
                "character": group["character"],
                "role_identity_sha256": group["role_identity_sha256"],
                "review_role_epoch_sha256": group["role_epoch_sha256"],
                "anchor_id": candidate["id"],
                "audio_sha256": candidate["audio_sha256"],
                "decision_sha256": decision_sha256,
            },
        )
        role_identity = _role_identity_document(role)
        selection_groups.append(
            {
                "model": group["model"],
                "model_revision": group["model_revision"],
                "scenario": group["scenario"],
                "character": group["character"],
                "role_identity": role_identity,
                "role_identity_sha256": group["role_identity_sha256"],
                "review_role_epoch_sha256": group["role_epoch_sha256"],
                "role_epoch_sha256": selected_role_epoch_sha256,
                "anchor_id": candidate["id"],
                "attempt": candidate["attempt"],
                "seed": candidate["seed"],
                "audio_path": relative,
                "audio_sha256": candidate["audio_sha256"],
                "anchor_text": plan.anchor_texts[group["model"]],
                "anchor_text_sha256": hashlib.sha256(
                    plan.anchor_texts[group["model"]].encode("utf-8"),
                ).hexdigest(),
                "decision": dict(decision_group),
                "decision_sha256": decision_sha256,
            },
        )
    selection_groups.sort(
        key=lambda item: (
            item["model"],
            item["scenario"],
            item["character"],
        ),
    )
    selection = validate_anchor_selection(
        {
            "format_version": SELECTION_FORMAT_VERSION,
            "protocol": SELECTION_PROTOCOL,
            "plan_sha256": plan.plan_id,
            "candidate_set_sha256": candidate_set_sha256,
            "groups": selection_groups,
        },
    )
    output_dir.mkdir(parents=True)
    try:
        for source, destination, sha256 in copy_jobs:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise CompletionAnchorError(
                    f"selection audio destinationが重複しています: {destination}",
                )
            shutil.copy2(source, destination)
            _verify_file_sha256(destination, sha256, "copied selected anchor")
        selection_path = output_dir / "role-anchor-selection-v1.json"
        _write_canonical_new(selection_path, selection)
        selection_sha256 = hashlib.sha256(selection_path.read_bytes()).hexdigest()
        (output_dir / "role-anchor-selection-v1.sha256").write_bytes(
            f"{selection_sha256}\n".encode("ascii"),
        )
    except Exception:
        shutil.rmtree(output_dir)
        raise
    return AnchorSelectionSummary(
        output_dir=output_dir,
        selection_path=selection_path,
        selection_sha256=selection_sha256,
        selected_count=len(selection_groups),
    )


def resolve_selected_anchor(
    *,
    selection_path: Path,
    plan_sha256: str,
    model: str,
    model_revision: str,
    role: RoleSnapshot,
) -> SelectedRoleAnchor:
    _require_absolute(selection_path, "anchor selection")
    raw, document = _read_canonical_json(selection_path, "anchor selection")
    selection_sha256 = hashlib.sha256(raw).hexdigest()
    _verify_adjacent_sha256_marker(
        selection_path.with_suffix(".sha256"),
        selection_sha256,
        "anchor selection",
    )
    selection = validate_anchor_selection(document)
    expected_plan_sha256 = _sha256(
        plan_sha256,
        "expected anchor plan_sha256",
    )
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
        == (model, role.scenario, role.character)
    ]
    if len(matches) != 1:
        raise CompletionAnchorError(
            "anchor selectionにmodel/scenario/characterの一意な選択がありません: "
            f"{model}/{role.scenario}/{role.character}",
        )
    group = matches[0]
    if group["model_revision"] != model_revision:
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


def validate_anchor_run(
    document: Any,
    *,
    plan: CompletionPlan,
) -> dict[str, Any]:
    root = _exact(document, RUN_ROOT_FIELDS, "anchor run")
    if root["format_version"] != RUN_FORMAT_VERSION:
        raise CompletionAnchorError("anchor run format_versionが不正です。")
    if root["protocol"] != RUN_PROTOCOL:
        raise CompletionAnchorError("anchor run protocolが不正です。")
    plan_sha = _sha256(root["plan_sha256"], "anchor run.plan_sha256")
    if plan_sha != plan.plan_id:
        raise CompletionAnchorError("anchor run plan SHAが一致しません。")
    run_id = _path_segment(root["run_id"], "anchor run.run_id")
    model = _path_segment(root["model"], "anchor run.model")
    revision = _text(root["model_revision"], "anchor run.model_revision")
    if revision != _model_revision(plan, model):
        raise CompletionAnchorError("anchor run model revisionがplanと一致しません。")
    kind = root["kind"]
    if kind not in {"initial", "topup"}:
        raise CompletionAnchorError("anchor run kindが不正です。")
    source_sha = root["source_candidate_set_sha256"]
    if kind == "initial" and source_sha is not None:
        raise CompletionAnchorError("initial runにsource candidate setは不要です。")
    if kind == "topup":
        source_sha = _sha256(
            source_sha,
            "anchor run.source_candidate_set_sha256",
        )
    attempts_value = root["attempts"]
    if not isinstance(attempts_value, list) or not attempts_value:
        raise CompletionAnchorError("anchor run attemptsは空でない配列が必要です。")
    targets = {target.identity: target for target in plan.anchor_targets}
    attempts: list[dict[str, Any]] = []
    slots: set[tuple[str, str, str, int]] = set()
    seeds: set[int] = set()
    for index, value in enumerate(attempts_value):
        field = f"anchor run.attempts[{index}]"
        item = _exact(value, RUN_ATTEMPT_FIELDS, field)
        item_model = _path_segment(item["model"], f"{field}.model")
        item_revision = _text(item["model_revision"], f"{field}.model_revision")
        scenario = _path_segment(item["scenario"], f"{field}.scenario")
        character = _path_segment(item["character"], f"{field}.character")
        target = targets.get((item_model, scenario, character))
        if target is None:
            raise CompletionAnchorError(f"{field}はPhase A対象外です。")
        if item_model != model or item_revision != revision:
            raise CompletionAnchorError(f"{field}のmodel/revisionがrunと不一致です。")
        role_sha = _sha256(
            item["role_identity_sha256"],
            f"{field}.role_identity_sha256",
        )
        epoch_sha = _sha256(
            item["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        )
        if (
            role_sha != target.role_identity_sha256
            or epoch_sha != target.role_epoch_sha256
        ):
            raise CompletionAnchorError(f"{field}のrole identity/epochが不正です。")
        attempt = _positive_integer(item["attempt"], f"{field}.attempt")
        seed = _integer(item["seed"], f"{field}.seed")
        expected_seed = derive_anchor_seed(
            plan_sha256=plan.plan_id,
            seed_base=plan.phase_a_seed_base,
            model=item_model,
            scenario=scenario,
            character=character,
            attempt=attempt,
        )
        if seed != expected_seed:
            raise CompletionAnchorError(f"{field}.seedがplan derivationと不一致です。")
        slot = (item_model, scenario, character, attempt)
        if slot in slots or seed in seeds:
            raise CompletionAnchorError("anchor runのslotまたはseedが重複しています。")
        slots.add(slot)
        seeds.add(seed)
        generation_input = item["generation_input"]
        if not isinstance(generation_input, dict):
            raise CompletionAnchorError(f"{field}.generation_inputが不正です。")
        canonical_json(generation_input)
        if "emotion" in _recursive_keys(generation_input) or "intensity" in _recursive_keys(
            generation_input
        ):
            raise CompletionAnchorError(
                f"{field}.generation_inputにemotion/intensityを含められません。",
            )
        input_sha = _sha256(
            item["generation_input_sha256"],
            f"{field}.generation_input_sha256",
        )
        if input_sha != _canonical_sha256(generation_input):
            raise CompletionAnchorError(f"{field}.generation input SHAが不正です。")
        status = item["status"]
        if status not in {"eligible", "rejected", "failed"}:
            raise CompletionAnchorError(f"{field}.statusが不正です。")
        qc = _validate_qc(item["qc"], f"{field}.qc")
        if status == "eligible" and qc["mechanical"] != "pass":
            raise CompletionAnchorError("eligible anchorはmechanical passが必要です。")
        if status == "rejected" and qc["mechanical"] != "fail":
            raise CompletionAnchorError("rejected anchorはmechanical failが必要です。")
        if status == "failed":
            if any(
                item[key] is not None
                for key in ("anchor_id", "audio_path", "audio_sha256", "realized")
            ):
                raise CompletionAnchorError("failed anchorにaudio identityは持てません。")
            error = _text(item["error"], f"{field}.error")
            anchor_id = audio_path = audio_sha = realized = None
        else:
            anchor_id = _sha256(item["anchor_id"], f"{field}.anchor_id")
            audio_path = _relative_artifact_path(
                item["audio_path"],
                f"{field}.audio_path",
            )
            audio_sha = _sha256(item["audio_sha256"], f"{field}.audio_sha256")
            realized = item["realized"]
            if not isinstance(realized, dict):
                raise CompletionAnchorError(f"{field}.realizedはobjectが必要です。")
            canonical_json(realized)
            error = None
        attempts.append(
            {
                "model": item_model,
                "model_revision": item_revision,
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": role_sha,
                "role_epoch_sha256": epoch_sha,
                "attempt": attempt,
                "seed": seed,
                "generation_input": generation_input,
                "generation_input_sha256": input_sha,
                "status": status,
                "anchor_id": anchor_id,
                "audio_path": audio_path,
                "audio_sha256": audio_sha,
                "qc": qc,
                "realized": realized,
                "error": error,
            },
        )
    expected_order = sorted(
        attempts,
        key=lambda item: (
            item["model"],
            item["scenario"],
            item["character"],
            item["attempt"],
        ),
    )
    if attempts != expected_order:
        raise CompletionAnchorError("anchor run attemptsはcanonical順が必要です。")
    if kind == "initial":
        expected_slots = {
            (target.model, target.scenario, target.character, attempt)
            for target in plan.anchor_targets_for_model(model)
            for attempt in range(1, plan.phase_a_takes + 1)
        }
        if slots != expected_slots:
            raise CompletionAnchorError(
                "initial anchor runはmodelの53 role × N4とexact一致が必要です。",
            )
    return {
        "format_version": RUN_FORMAT_VERSION,
        "protocol": RUN_PROTOCOL,
        "plan_sha256": plan_sha,
        "run_id": run_id,
        "model": model,
        "model_revision": revision,
        "kind": kind,
        "source_candidate_set_sha256": source_sha,
        "attempts": attempts,
    }


def validate_anchor_candidate_set(
    document: Any,
    *,
    plan: CompletionPlan,
) -> dict[str, Any]:
    root = _exact(document, CANDIDATE_SET_ROOT_FIELDS, "anchor candidate set")
    if (
        root["format_version"] != CANDIDATE_SET_FORMAT_VERSION
        or root["protocol"] != CANDIDATE_SET_PROTOCOL
    ):
        raise CompletionAnchorError("anchor candidate set contractが不正です。")
    plan_sha = _sha256(
        root["plan_sha256"],
        "anchor candidate set.plan_sha256",
    )
    if plan_sha != plan.plan_id:
        raise CompletionAnchorError("anchor candidate set plan SHAが不一致です。")
    runs_value = root["runs"]
    if not isinstance(runs_value, list) or not runs_value:
        raise CompletionAnchorError("anchor candidate set runsが不正です。")
    runs = [_path_segment(value, "anchor candidate set.run") for value in runs_value]
    if runs != sorted(set(runs)):
        raise CompletionAnchorError("anchor candidate set runsはcanonical uniqueが必要です。")
    groups_value = root["groups"]
    if not isinstance(groups_value, list):
        raise CompletionAnchorError("anchor candidate set groupsは配列が必要です。")
    targets = {target.identity: target for target in plan.anchor_targets}
    groups: list[dict[str, Any]] = []
    seen_candidates: set[str] = set()
    seen_paths: set[str] = set()
    seen_attempt_seeds: set[int] = set()
    seen_attempt_slots: set[tuple[str, str, str, int]] = set()
    for index, value in enumerate(groups_value):
        field = f"anchor candidate set.groups[{index}]"
        item = _exact(value, CANDIDATE_GROUP_FIELDS, field)
        model = _path_segment(item["model"], f"{field}.model")
        revision = _text(item["model_revision"], f"{field}.model_revision")
        scenario = _path_segment(item["scenario"], f"{field}.scenario")
        character = _path_segment(item["character"], f"{field}.character")
        target = targets.get((model, scenario, character))
        if target is None or revision != _model_revision(plan, model):
            raise CompletionAnchorError(f"{field}がplan targetと一致しません。")
        role_sha = _sha256(
            item["role_identity_sha256"],
            f"{field}.role_identity_sha256",
        )
        epoch_sha = _sha256(
            item["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        )
        if (
            role_sha != target.role_identity_sha256
            or epoch_sha != target.role_epoch_sha256
        ):
            raise CompletionAnchorError(f"{field}のrole identity/epochが不正です。")
        attempts_value = item["attempts"]
        if not isinstance(attempts_value, list):
            raise CompletionAnchorError(f"{field}.attemptsは配列が必要です。")
        attempted = [
            _positive_integer(value, f"{field}.attempts[{attempt_index}]")
            for attempt_index, value in enumerate(attempts_value)
        ]
        if attempted != sorted(set(attempted)):
            raise CompletionAnchorError(
                f"{field}.attemptsはcanonical unique昇順が必要です。",
            )
        for attempt in attempted:
            slot = (model, scenario, character, attempt)
            seed = derive_anchor_seed(
                plan_sha256=plan.plan_id,
                seed_base=plan.phase_a_seed_base,
                model=model,
                scenario=scenario,
                character=character,
                attempt=attempt,
            )
            if slot in seen_attempt_slots or seed in seen_attempt_seeds:
                raise CompletionAnchorError(
                    "anchor attempt historyのslotまたはseedが重複しています。",
                )
            seen_attempt_slots.add(slot)
            seen_attempt_seeds.add(seed)
        candidates_value = item["candidates"]
        if not isinstance(candidates_value, list):
            raise CompletionAnchorError(f"{field}.candidatesは配列が必要です。")
        candidates: list[dict[str, Any]] = []
        candidate_attempts: set[int] = set()
        for candidate_index, candidate_value in enumerate(candidates_value):
            candidate_field = f"{field}.candidates[{candidate_index}]"
            candidate = _exact(
                candidate_value,
                CANDIDATE_FIELDS,
                candidate_field,
            )
            normalized = _validate_candidate(
                candidate,
                field=candidate_field,
                group={
                    "model": model,
                    "model_revision": revision,
                    "scenario": scenario,
                    "character": character,
                    "role_identity_sha256": role_sha,
                    "role_epoch_sha256": epoch_sha,
                },
            )
            slot = (model, scenario, character, normalized["attempt"])
            expected_seed = derive_anchor_seed(
                plan_sha256=plan.plan_id,
                seed_base=plan.phase_a_seed_base,
                model=model,
                scenario=scenario,
                character=character,
                attempt=normalized["attempt"],
            )
            if (
                normalized["id"] in seen_candidates
                or normalized["audio_path"] in seen_paths
                or normalized["attempt"] in candidate_attempts
            ):
                raise CompletionAnchorError(
                    "anchor candidate identity/path/seed/attemptが重複しています。",
                )
            if (
                normalized["attempt"] not in attempted
                or normalized["seed"] != expected_seed
                or slot not in seen_attempt_slots
            ):
                raise CompletionAnchorError(
                    f"{candidate_field}はattempt historyのeligible subsetではありません。",
                )
            seen_candidates.add(normalized["id"])
            seen_paths.add(normalized["audio_path"])
            candidate_attempts.add(normalized["attempt"])
            candidates.append(normalized)
        if candidates != sorted(candidates, key=lambda item: item["attempt"]):
            raise CompletionAnchorError(f"{field}.candidatesはattempt順が必要です。")
        groups.append(
            {
                "model": model,
                "model_revision": revision,
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": role_sha,
                "role_epoch_sha256": epoch_sha,
                "attempts": attempted,
                "candidates": candidates,
            },
        )
    if len(groups) != 106 or {
        (group["model"], group["scenario"], group["character"])
        for group in groups
    } != set(targets):
        raise CompletionAnchorError(
            "anchor candidate set groupsは全106 Phase A targetが必要です。",
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
        raise CompletionAnchorError("anchor candidate set groupsはcanonical順が必要です。")
    return {
        "format_version": CANDIDATE_SET_FORMAT_VERSION,
        "protocol": CANDIDATE_SET_PROTOCOL,
        "plan_sha256": plan_sha,
        "runs": runs,
        "groups": groups,
    }


def validate_anchor_topup_plan(
    document: Any,
    *,
    plan: CompletionPlan,
    candidate_set: Mapping[str, Any],
) -> dict[str, Any]:
    normalized_candidate_set = validate_anchor_candidate_set(
        candidate_set,
        plan=plan,
    )
    candidate_set_sha256 = _canonical_sha256(normalized_candidate_set)
    root = _exact(document, TOPUP_ROOT_FIELDS, "anchor topup plan")
    if (
        root["format_version"] != TOPUP_FORMAT_VERSION
        or root["protocol"] != TOPUP_PROTOCOL
    ):
        raise CompletionAnchorError("anchor topup plan contractが不正です。")
    plan_sha = _sha256(root["plan_sha256"], "anchor topup plan.plan_sha256")
    if plan_sha != plan.plan_id:
        raise CompletionAnchorError("anchor topup plan SHAが不一致です。")
    source_sha = _sha256(
        root["candidate_set_sha256"],
        "anchor topup plan.candidate_set_sha256",
    )
    if source_sha != candidate_set_sha256:
        raise CompletionAnchorError("anchor topup planのsource setが不一致です。")
    values = root["targets"]
    if not isinstance(values, list) or not values:
        raise CompletionAnchorError("anchor topup targetsは空でない配列が必要です。")
    targets_by_identity = {target.identity: target for target in plan.anchor_targets}
    attempted_slots = {
        (
            group["model"],
            group["scenario"],
            group["character"],
            attempt,
        )
        for group in normalized_candidate_set["groups"]
        for attempt in group["attempts"]
    }
    attempted_seeds = {
        derive_anchor_seed(
            plan_sha256=plan.plan_id,
            seed_base=plan.phase_a_seed_base,
            model=group["model"],
            scenario=group["scenario"],
            character=group["character"],
            attempt=attempt,
        )
        for group in normalized_candidate_set["groups"]
        for attempt in group["attempts"]
    }
    targets: list[dict[str, Any]] = []
    slots: set[tuple[str, str, str, int]] = set()
    seeds: set[int] = set()
    for index, value in enumerate(values):
        field = f"anchor topup plan.targets[{index}]"
        item = _exact(value, TOPUP_TARGET_FIELDS, field)
        model = _path_segment(item["model"], f"{field}.model")
        scenario = _path_segment(item["scenario"], f"{field}.scenario")
        character = _path_segment(item["character"], f"{field}.character")
        target = targets_by_identity.get((model, scenario, character))
        if target is None:
            raise CompletionAnchorError(f"{field}がPhase A対象外です。")
        role_sha = _sha256(
            item["role_identity_sha256"],
            f"{field}.role_identity_sha256",
        )
        epoch_sha = _sha256(
            item["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        )
        if (
            role_sha != target.role_identity_sha256
            or epoch_sha != target.role_epoch_sha256
        ):
            raise CompletionAnchorError(f"{field}のrole identity/epochが不正です。")
        attempt = _positive_integer(item["attempt"], f"{field}.attempt")
        if attempt <= plan.phase_a_takes:
            raise CompletionAnchorError("topup attemptは初回N4より後が必要です。")
        seed = _integer(item["seed"], f"{field}.seed")
        expected_seed = derive_anchor_seed(
            plan_sha256=plan.plan_id,
            seed_base=plan.phase_a_seed_base,
            model=model,
            scenario=scenario,
            character=character,
            attempt=attempt,
        )
        if seed != expected_seed:
            raise CompletionAnchorError(f"{field}.seedがderivationと不一致です。")
        slot = (model, scenario, character, attempt)
        if slot in attempted_slots or seed in attempted_seeds:
            raise CompletionAnchorError(
                f"{field}は既に試行済みのslot/seedを再利用できません。",
            )
        if slot in slots or seed in seeds:
            raise CompletionAnchorError("topup slot/seedが重複しています。")
        slots.add(slot)
        seeds.add(seed)
        targets.append(
            {
                "model": model,
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": role_sha,
                "role_epoch_sha256": epoch_sha,
                "attempt": attempt,
                "seed": seed,
            },
        )
    expected_order = sorted(
        targets,
        key=lambda item: (
            item["model"],
            item["scenario"],
            item["character"],
            item["attempt"],
        ),
    )
    if targets != expected_order:
        raise CompletionAnchorError("topup targetsはcanonical順が必要です。")
    expected_targets = _expected_anchor_topup_targets(
        plan=plan,
        candidate_set=normalized_candidate_set,
    )
    if targets != expected_targets:
        raise CompletionAnchorError(
            "topup targetsはsource candidate setのexact deficit targetsと"
            "一致する必要があります。",
        )
    return {
        "format_version": TOPUP_FORMAT_VERSION,
        "protocol": TOPUP_PROTOCOL,
        "plan_sha256": plan_sha,
        "candidate_set_sha256": source_sha,
        "targets": targets,
    }


def _expected_anchor_topup_targets(
    *,
    plan: CompletionPlan,
    candidate_set: Mapping[str, Any],
) -> list[dict[str, Any]]:
    seen_seeds = {
        derive_anchor_seed(
            plan_sha256=plan.plan_id,
            seed_base=plan.phase_a_seed_base,
            model=group["model"],
            scenario=group["scenario"],
            character=group["character"],
            attempt=attempt,
        )
        for group in candidate_set["groups"]
        for attempt in group["attempts"]
    }
    targets: list[dict[str, Any]] = []
    for group in candidate_set["groups"]:
        deficit = (
            plan.phase_a_minimum_eligible_candidates
            - len(group["candidates"])
        )
        if deficit <= 0:
            continue
        attempt = max({plan.phase_a_takes, *group["attempts"]}) + 1
        for _ in range(deficit):
            seed = derive_anchor_seed(
                plan_sha256=plan.plan_id,
                seed_base=plan.phase_a_seed_base,
                model=group["model"],
                scenario=group["scenario"],
                character=group["character"],
                attempt=attempt,
            )
            if seed in seen_seeds:
                raise CompletionAnchorError(
                    "topup seedがcandidate setまたは別targetと衝突しました。",
                )
            seen_seeds.add(seed)
            targets.append(
                {
                    "model": group["model"],
                    "scenario": group["scenario"],
                    "character": group["character"],
                    "role_identity_sha256": group["role_identity_sha256"],
                    "role_epoch_sha256": group["role_epoch_sha256"],
                    "attempt": attempt,
                    "seed": seed,
                },
            )
            attempt += 1
    return sorted(
        targets,
        key=lambda item: (
            item["model"],
            item["scenario"],
            item["character"],
            item["attempt"],
        ),
    )


def validate_role_review(document: Any) -> dict[str, Any]:
    root = _exact(document, REVIEW_ROOT_FIELDS, "role review")
    if (
        root["format_version"] != REVIEW_FORMAT_VERSION
        or root["protocol"] != REVIEW_PROTOCOL
        or root["phase"] != PHASE
    ):
        raise CompletionAnchorError("role review root contractが不正です。")
    plan_sha = _sha256(root["plan_sha256"], "role review.plan_sha256")
    candidate_sha = _sha256(
        root["candidate_set_sha256"],
        "role review.candidate_set_sha256",
    )
    values = root["groups"]
    if not isinstance(values, list) or not values:
        raise CompletionAnchorError("role review groupsが不正です。")
    groups: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, value in enumerate(values):
        field = f"role review.groups[{index}]"
        item = _exact(value, REVIEW_GROUP_FIELDS, field)
        group_id = _sha256(item["id"], f"{field}.id")
        if group_id in ids:
            raise CompletionAnchorError("role review group idが重複しています。")
        ids.add(group_id)
        model = _path_segment(item["model"], f"{field}.model")
        scenario = _path_segment(item["scenario"], f"{field}.scenario")
        character = _path_segment(item["character"], f"{field}.character")
        if item["line"] is not None:
            raise CompletionAnchorError("anchor review group.lineはnullが必要です。")
        epoch = _sha256(
            item["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        )
        role_value = _exact(item["role"], ROLE_FIELDS, f"{field}.role")
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
        conditioning_value = _exact(
            item["conditioning"],
            REVIEW_CONDITIONING_FIELDS,
            f"{field}.conditioning",
        )
        conditioning = {
            "method": _text(
                conditioning_value["method"],
                f"{field}.conditioning.method",
            ),
            "summary": _text(
                conditioning_value["summary"],
                f"{field}.conditioning.summary",
            ),
        }
        coverage_value = _exact(
            item["coverage"],
            REVIEW_COVERAGE_FIELDS,
            f"{field}.coverage",
        )
        coverage: dict[str, str] = {}
        for key in ("gender", "age", "archetype"):
            coverage_item = coverage_value[key]
            if coverage_item not in _ALLOWED_COVERAGE:
                raise CompletionAnchorError(f"{field}.coverage.{key}が不正です。")
            coverage[key] = coverage_item
        if item["comparison_required"] is not True:
            raise CompletionAnchorError(
                "anchor review comparison_requiredはtrueが必要です。",
            )
        reasons = item["comparison_reasons"]
        if (
            not isinstance(reasons, list)
            or not reasons
            or any(not isinstance(reason, str) or not reason for reason in reasons)
            or len(set(reasons)) != len(reasons)
        ):
            raise CompletionAnchorError(f"{field}.comparison_reasonsが不正です。")
        candidates_value = item["candidates"]
        if not isinstance(candidates_value, list) or not candidates_value:
            raise CompletionAnchorError(f"{field}.candidatesが不正です。")
        candidates: list[dict[str, Any]] = []
        for candidate_index, candidate_value in enumerate(candidates_value):
            candidate_field = f"{field}.candidates[{candidate_index}]"
            candidate = _exact(
                candidate_value,
                REVIEW_CANDIDATE_FIELDS,
                candidate_field,
            )
            candidates.append(
                {
                    "id": _sha256(candidate["id"], f"{candidate_field}.id"),
                    "attempt": _positive_integer(
                        candidate["attempt"],
                        f"{candidate_field}.attempt",
                    ),
                    "seed": _integer(
                        candidate["seed"],
                        f"{candidate_field}.seed",
                    ),
                    "audio_path": _relative_bundle_path(
                        candidate["audio_path"],
                        f"{candidate_field}.audio_path",
                    ),
                    "audio_sha256": _sha256(
                        candidate["audio_sha256"],
                        f"{candidate_field}.audio_sha256",
                    ),
                    "qc": _validate_qc(
                        candidate["qc"],
                        f"{candidate_field}.qc",
                    ),
                },
            )
        candidate_ids = item["candidate_ids"]
        expected_ids = [candidate["id"] for candidate in candidates]
        if candidate_ids != expected_ids:
            raise CompletionAnchorError(
                f"{field}.candidate_idsがcandidate順と一致しません。",
            )
        provisional = _sha256(
            item["provisional_candidate_id"],
            f"{field}.provisional_candidate_id",
        )
        if provisional not in expected_ids:
            raise CompletionAnchorError(
                f"{field}.provisional candidateがgroup内にありません。",
            )
        groups.append(
            {
                "id": group_id,
                "model": model,
                "scenario": scenario,
                "character": character,
                "line": None,
                "role_epoch_sha256": epoch,
                "role": role,
                "conditioning": conditioning,
                "coverage": coverage,
                "comparison_required": True,
                "comparison_reasons": list(reasons),
                "candidate_ids": expected_ids,
                "provisional_candidate_id": provisional,
                "candidates": candidates,
            },
        )
    return {
        "format_version": REVIEW_FORMAT_VERSION,
        "protocol": REVIEW_PROTOCOL,
        "phase": PHASE,
        "plan_sha256": plan_sha,
        "candidate_set_sha256": candidate_sha,
        "groups": groups,
    }


def validate_anchor_decision(document: Any) -> dict[str, Any]:
    root = _exact(document, DECISION_ROOT_FIELDS, "anchor decision")
    if (
        root["format_version"] != DECISION_FORMAT_VERSION
        or root["protocol"] != DECISION_PROTOCOL
        or root["phase"] != PHASE
    ):
        raise CompletionAnchorError("anchor decision root contractが不正です。")
    plan_sha = _sha256(root["plan_sha256"], "anchor decision.plan_sha256")
    candidate_sha = _sha256(
        root["candidate_set_sha256"],
        "anchor decision.candidate_set_sha256",
    )
    values = root["groups"]
    if not isinstance(values, list) or not values:
        raise CompletionAnchorError("anchor decision groupsが不正です。")
    groups: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, value in enumerate(values):
        field = f"anchor decision.groups[{index}]"
        item = _exact(value, DECISION_GROUP_FIELDS, field)
        group_id = _sha256(item["id"], f"{field}.id")
        if group_id in ids:
            raise CompletionAnchorError("anchor decision group idが重複しています。")
        ids.add(group_id)
        if item["line"] is not None:
            raise CompletionAnchorError(
                f"{field}.lineはanchor phaseでnullが必要です。",
            )
        heard = _sha256_array(
            item["heard_candidate_ids"],
            f"{field}.heard_candidate_ids",
        )
        selected_candidate_id = _sha256(
            item["selected_candidate_id"],
            f"{field}.selected_candidate_id",
        )
        if selected_candidate_id not in heard:
            raise CompletionAnchorError(
                f"{field}.selected_candidate_idはheard候補である必要があります。",
            )
        if item["confirmed"] is not True:
            raise CompletionAnchorError(
                "anchor decisionには全groupのconfirmed=trueが必要です。",
            )
        groups.append(
            {
                "id": group_id,
                "model": _path_segment(item["model"], f"{field}.model"),
                "scenario": _path_segment(
                    item["scenario"],
                    f"{field}.scenario",
                ),
                "character": _path_segment(
                    item["character"],
                    f"{field}.character",
                ),
                "line": None,
                "role_epoch_sha256": _sha256(
                    item["role_epoch_sha256"],
                    f"{field}.role_epoch_sha256",
                ),
                "group_sha256": _sha256(
                    item["group_sha256"],
                    f"{field}.group_sha256",
                ),
                "heard_candidate_ids": heard,
                "selected_candidate_id": selected_candidate_id,
                "rubric": _validate_decision_rubric(
                    item["rubric"],
                    f"{field}.rubric",
                ),
                "confirmed": True,
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
        raise CompletionAnchorError("anchor decision groupsはcanonical順が必要です。")
    reopen_requests = _validate_role_reopen_requests(
        root["role_reopen_requests"],
        groups=groups,
    )
    return {
        "format_version": DECISION_FORMAT_VERSION,
        "protocol": DECISION_PROTOCOL,
        "phase": PHASE,
        "plan_sha256": plan_sha,
        "candidate_set_sha256": candidate_sha,
        "groups": groups,
        "role_reopen_requests": reopen_requests,
    }


def validate_anchor_selection(document: Any) -> dict[str, Any]:
    root = _exact(document, SELECTION_ROOT_FIELDS, "anchor selection")
    if (
        root["format_version"] != SELECTION_FORMAT_VERSION
        or root["protocol"] != SELECTION_PROTOCOL
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
        item = _exact(value, SELECTION_GROUP_FIELDS, field)
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
        audio_sha = _sha256(
            item["audio_sha256"],
            f"{field}.audio_sha256",
        )
        anchor_text = _text(item["anchor_text"], f"{field}.anchor_text")
        anchor_text_sha = _sha256(
            item["anchor_text_sha256"],
            f"{field}.anchor_text_sha256",
        )
        if hashlib.sha256(anchor_text.encode("utf-8")).hexdigest() != anchor_text_sha:
            raise CompletionAnchorError(f"{field}.anchor text SHAが不正です。")
        decision = validate_anchor_decision(
            {
                "format_version": DECISION_FORMAT_VERSION,
                "protocol": DECISION_PROTOCOL,
                "phase": PHASE,
                "plan_sha256": plan_sha,
                "candidate_set_sha256": candidate_sha,
                "groups": [item["decision"]],
                "role_reopen_requests": [],
            },
        )["groups"][0]
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
        "format_version": SELECTION_FORMAT_VERSION,
        "protocol": SELECTION_PROTOCOL,
        "plan_sha256": plan_sha,
        "candidate_set_sha256": candidate_sha,
        "groups": groups,
    }


def _anchor_generation_input(
    *,
    plan: CompletionPlan,
    target: AnchorTarget,
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
    text = plan.anchor_texts[target.model]
    return {
        "protocol": "role-anchor-input-v1",
        "plan_sha256": plan.plan_id,
        "model": target.model,
        "model_revision": _model_revision(plan, target.model),
        "scenario": target.scenario,
        "character": target.character,
        "role_identity_sha256": target.role_identity_sha256,
        "role_epoch_sha256": target.role_epoch_sha256,
        "attempt": attempt,
        "seed": seed,
        "anchor_text": text,
        "anchor_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "conditioning": conditioning,
    }


def _candidate_from_attempt(attempt: Mapping[str, Any]) -> dict[str, Any]:
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


def _validate_candidate(
    value: Mapping[str, Any],
    *,
    field: str,
    group: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_id = _sha256(value["id"], f"{field}.id")
    model = _path_segment(value["model"], f"{field}.model")
    revision = _text(value["model_revision"], f"{field}.model_revision")
    scenario = _path_segment(value["scenario"], f"{field}.scenario")
    character = _path_segment(value["character"], f"{field}.character")
    role_sha = _sha256(
        value["role_identity_sha256"],
        f"{field}.role_identity_sha256",
    )
    epoch_sha = _sha256(
        value["role_epoch_sha256"],
        f"{field}.role_epoch_sha256",
    )
    if any(
        (
            model != group["model"],
            revision != group["model_revision"],
            scenario != group["scenario"],
            character != group["character"],
            role_sha != group["role_identity_sha256"],
            epoch_sha != group["role_epoch_sha256"],
        ),
    ):
        raise CompletionAnchorError(f"{field}がcandidate groupと一致しません。")
    qc = _validate_qc(value["qc"], f"{field}.qc")
    if qc["mechanical"] != "pass":
        raise CompletionAnchorError("candidate setはmechanical passのみ許可します。")
    return {
        "id": candidate_id,
        "model": model,
        "model_revision": revision,
        "scenario": scenario,
        "character": character,
        "role_identity_sha256": role_sha,
        "role_epoch_sha256": epoch_sha,
        "attempt": _positive_integer(value["attempt"], f"{field}.attempt"),
        "seed": _integer(value["seed"], f"{field}.seed"),
        "audio_path": _relative_artifact_path(
            value["audio_path"],
            f"{field}.audio_path",
        ),
        "audio_sha256": _sha256(
            value["audio_sha256"],
            f"{field}.audio_sha256",
        ),
        "generation_input_sha256": _sha256(
            value["generation_input_sha256"],
            f"{field}.generation_input_sha256",
        ),
        "qc": qc,
    }


def _review_group_document(
    *,
    plan: CompletionPlan,
    group: Mapping[str, Any],
) -> dict[str, Any]:
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
    candidate_ids = [candidate["id"] for candidate in candidates]
    return {
        "id": _review_group_id(group),
        "model": group["model"],
        "scenario": group["scenario"],
        "character": group["character"],
        "line": None,
        "role_epoch_sha256": group["role_epoch_sha256"],
        "role": dict(role.role),
        "conditioning": _review_conditioning(group["model"]),
        "coverage": {
            "gender": (
                "neutral" if role.role["gender"] == "neutral" else "exact"
            ),
            "age": "exact",
            "archetype": "exact",
        },
        "comparison_required": True,
        "comparison_reasons": [
            "role_match",
            "same_role_voice_identity",
            "anchor_audio_quality",
        ],
        "candidate_ids": candidate_ids,
        "provisional_candidate_id": candidate_ids[0],
        "candidates": candidates,
    }


def _verify_decision_candidate(
    decision: Mapping[str, Any],
    *,
    review_group: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> None:
    expected = {
        "id": review_group["id"],
        "model": review_group["model"],
        "scenario": review_group["scenario"],
        "character": review_group["character"],
        "line": None,
        "role_epoch_sha256": review_group["role_epoch_sha256"],
        "group_sha256": _canonical_sha256(review_group),
        "selected_candidate_id": candidate["id"],
        "confirmed": True,
    }
    mismatched = [key for key, value in expected.items() if decision[key] != value]
    if mismatched:
        raise CompletionAnchorError(
            f"decisionがcandidate identityと一致しません: {mismatched}",
        )
    candidate_ids = review_group["candidate_ids"]
    heard = decision["heard_candidate_ids"]
    expected_heard_order = [
        candidate_id for candidate_id in candidate_ids if candidate_id in heard
    ]
    if heard != expected_heard_order:
        raise CompletionAnchorError(
            "decision heard_candidate_idsはbundleのcandidate順が必要です。",
        )
    if len(heard) < 2:
        raise CompletionAnchorError(
            "comparison_requiredなanchor判断は異なる候補を2件以上聴く必要があります。",
        )


def _role_identity_document(role: RoleSnapshot) -> dict[str, Any]:
    return {
        "scenario": role.scenario,
        "character": role.character,
        "role": dict(role.role),
        "reference_voice": role.reference_voice,
        "scene_setting": role.scene_setting,
    }


def _validate_role_identity(value: Any, *, field: str) -> dict[str, Any]:
    identity = _exact(value, ROLE_IDENTITY_FIELDS, field)
    role_value = _exact(identity["role"], ROLE_FIELDS, f"{field}.role")
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
        "scene_setting": _text(
            identity["scene_setting"],
            f"{field}.scene_setting",
        ),
    }


def _review_group_id(group: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {
            "protocol": "role-review-group-v1",
            "phase": PHASE,
            "model": group["model"],
            "scenario": group["scenario"],
            "character": group["character"],
            "role_epoch_sha256": group["role_epoch_sha256"],
        },
    )


def _review_conditioning(model: str) -> dict[str, str]:
    if model == QWEN_MODEL:
        return {
            "method": "voice-design-anchor-then-clone",
            "summary": (
                "完全な役柄指定から候補anchorをVoiceDesignし、選定WAVを"
                "Baseの同一キャラクターclone promptとして全台詞に固定する。"
            ),
        }
    if model == IRODORI_MODEL:
        return {
            "method": "caption-anchor-then-reference",
            "summary": (
                "完全な役柄captionで候補anchorを生成し、選定WAVと逐条の"
                "役柄・場面・演技captionを同時に全台詞へ渡す。"
            ),
        }
    raise CompletionAnchorError(f"anchor review対象外modelです: {model}")


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
            sum(float(sample) ** 2 for sample in samples)
            / max(1, len(samples)),
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


def _validate_qc(value: Any, field: str) -> dict[str, Any]:
    qc = _exact(value, QC_FIELDS, field)
    mechanical = qc["mechanical"]
    if mechanical not in {"pass", "fail"}:
        raise CompletionAnchorError(f"{field}.mechanicalが不正です。")
    content = qc["content"]
    if content not in {"not_checked", "pass", "review_required"}:
        raise CompletionAnchorError(f"{field}.contentが不正です。")
    notes_value = qc["notes"]
    if not isinstance(notes_value, list) or any(
        not isinstance(note, str) for note in notes_value
    ):
        raise CompletionAnchorError(f"{field}.notesは文字列配列が必要です。")
    return {
        "mechanical": mechanical,
        "content": content,
        "notes": list(notes_value),
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


def _model_revision(plan: CompletionPlan, model: str) -> str:
    try:
        return plan.models[model]
    except KeyError as error:
        raise CompletionAnchorError(f"planにmodel revisionがありません: {model}") from error


def _artifact_path(artifacts_dir: Path, relative: str) -> Path:
    return _relative_child(artifacts_dir, relative, "artifact")


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


def _relative_artifact_path(value: Any, field: str) -> str:
    text = _text(value, field)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or len(pure.parts) < 4
        or pure.parts[:2] != ("role-anchors", "runs")
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in text
        or pure.suffix != ".wav"
    ):
        raise CompletionAnchorError(f"{field}が不正なanchor artifact pathです。")
    return pure.as_posix()


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
    ):
        raise CompletionAnchorError(f"{field}が不正なbundle audio pathです。")
    return pure.as_posix()


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
    if canonical_json(document).encode("utf-8") != raw:
        raise CompletionAnchorError(f"{label}はcanonical bytesが必要です。")
    return raw, document


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


def _verify_file_sha256(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise CompletionAnchorError(f"{label}がありません: {path}")
    actual = _sha256_file(path)
    if actual != expected:
        raise CompletionAnchorError(
            f"{label} SHA-256が一致しません: expected={expected}, actual={actual}",
        )


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
    expected_marker = f"{expected}\n".encode("ascii")
    if marker != expected_marker:
        raise CompletionAnchorError(
            f"{label}の隣接SHA-256 markerが一致しません。",
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


def _recursive_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_recursive_keys(child))
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for child in value:
            keys.update(_recursive_keys(child))
    return keys


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
    if len(text) != 64 or any(character not in HEX for character in text):
        raise CompletionAnchorError(f"{field}は完全な小文字SHA-256が必要です。")
    return text


def _sha256_array(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise CompletionAnchorError(f"{field}は1件以上の配列が必要です。")
    result = [
        _sha256(item, f"{field}[{index}]") for index, item in enumerate(value)
    ]
    if len(set(result)) != len(result):
        raise CompletionAnchorError(f"{field}に重複があります。")
    return result


def _validate_decision_rubric(value: Any, field: str) -> dict[str, Any]:
    rubric = _exact(value, DECISION_RUBRIC_FIELDS, field)
    normalized: dict[str, Any] = {}
    for key in (
        "content",
        "prompt_leakage",
        "reading",
        "pitch_accent",
        "gender",
        "age",
        "archetype",
        "voice_identity",
        "delivery",
    ):
        result = rubric[key]
        if result not in _ALLOWED_RUBRIC_RESULTS:
            raise CompletionAnchorError(
                f"{field}.{key}はpass/fail/not_applicableが必要です。",
            )
        normalized[key] = result
    score = _integer(
        rubric["naturalness_quality"],
        f"{field}.naturalness_quality",
    )
    if not 1 <= score <= 5:
        raise CompletionAnchorError(
            f"{field}.naturalness_qualityは1..5が必要です。",
        )
    normalized["naturalness_quality"] = score
    normalized["notes"] = _string(rubric["notes"], f"{field}.notes")
    return normalized


def _validate_role_reopen_requests(
    value: Any,
    *,
    groups: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise CompletionAnchorError(
            "anchor decision.role_reopen_requestsは配列が必要です。",
        )
    available = {
        (
            group["model"],
            group["character"],
            group["role_epoch_sha256"],
        )
        for group in groups
    }
    seen: set[tuple[str, str]] = set()
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        field = f"anchor decision.role_reopen_requests[{index}]"
        request = _exact(item, ROLE_REOPEN_FIELDS, field)
        model = _path_segment(request["model"], f"{field}.model")
        character = _path_segment(request["character"], f"{field}.character")
        epoch = _sha256(
            request["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        )
        reason = _text(request["reason"], f"{field}.reason")
        if reason != reason.strip():
            raise CompletionAnchorError(f"{field}.reasonは前後空白を受理しません。")
        if (model, character, epoch) not in available:
            raise CompletionAnchorError(
                f"{field}がdecision groupのrole epochと一致しません。",
            )
        key = (model, character)
        if key in seen:
            raise CompletionAnchorError(
                f"anchor decision role reopen requestが重複しています: {key}",
            )
        seen.add(key)
        normalized.append(
            {
                "model": model,
                "character": character,
                "role_epoch_sha256": epoch,
                "reason": reason,
            },
        )
    return normalized


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CompletionAnchorError(f"{field}はintegerが必要です。")
    return value


def _positive_integer(value: Any, field: str) -> int:
    integer = _integer(value, field)
    if integer < 1:
        raise CompletionAnchorError(f"{field}は1以上が必要です。")
    return integer
