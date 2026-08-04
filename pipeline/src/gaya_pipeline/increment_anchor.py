"""増分model向けrole anchorのbootstrap生成と機械選抜。

#174のPhase Aは `completion_anchor` に106 group / 2 model / 人手rubricで
SHA凍結されている。新規modelのanchorはそこへ差し込めないため、本moduleが

  1. 53 role x N=4 の決定論的bootstrap plan、
  2. 既存harnessと同じlayoutでの候補生成、
  3. mechanical QC + F0 gender screening による**機械選抜**、
  4. 上限2回の有界top-up、

を並行経路として提供する。選抜権限は `auto-selected-v1` として正直に記録し、
人手rubric (`role-review-decision-v2`) は一切捏造しない。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from gaya_pipeline.audio import find_audio_tools
from gaya_pipeline.completion_anchor import (
    CompletionAnchorError,
    SelectedRoleAnchor,
    _mechanical_qc,
    resolve_selected_anchor,
)
from gaya_pipeline.completion_plan import RoleSnapshot
from gaya_pipeline.completion_selection import (
    FEMALE_F0_LOWER_BOUND_HZ,
    GENDER_SCREENING_PROTOCOL,
    MALE_F0_UPPER_BOUND_HZ,
)
from gaya_pipeline.increment_plan import IncrementPlan
from gaya_pipeline.qc import count_japanese_mora
from gaya_pipeline.qc_runtime import SAMPLE_RATE_HZ, analyze_prosody_samples
from gaya_pipeline.take_identity import canonical_json


class IncrementAnchorError(RuntimeError):
    pass


PLAN_FORMAT_VERSION = 1
PLAN_PROTOCOL = "role-anchor-bootstrap-plan-v1"
RUN_FORMAT_VERSION = 1
RUN_PROTOCOL = "role-anchor-bootstrap-run-v1"
DECISION_FORMAT_VERSION = 1
DECISION_PROTOCOL = "role-anchor-machine-decision-v1"
SELECTION_FORMAT_VERSION = 1
SELECTION_PROTOCOL = "role-anchor-machine-selection-v1"
INPUT_PROTOCOL = "role-anchor-increment-input-v1"
EPOCH_PROTOCOL = "role-anchor-bootstrap-epoch-v1"
IDENTITY_PROTOCOL = "role-anchor-identity-v1"
SELECTED_EPOCH_PROTOCOL = "selected-role-epoch-v1"

SEED_POLICY = "role-anchor-increment-derived-sha256-v1"
SEED_BASE = 194
CANDIDATES_PER_ROLE = 4
MINIMUM_ELIGIBLE_CANDIDATES = 3
MAX_TOPUP_ROUNDS = 2
MAX_ATTEMPT = CANDIDATES_PER_ROLE * (MAX_TOPUP_ROUNDS + 1)
ANCHOR_ROLE_COUNT = 53

SELECTION_AUTHORITY_TYPE = "auto-selected-v1"
SELECTION_POLICY = "role-anchor-auto-selection-v1"
GATE_POLICY_VERSION = "take-gates-v2"
SOFT_SIGNAL_EXHAUSTED = "anchor_gender_f0_soft_signal_exhausted"

_PLAN_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "model",
    "model_revision",
    "sources",
    "anchor_text",
    "anchor_text_sha256",
    "phase_a",
    "roles",
    "targets",
}
_PLAN_SOURCES_FIELDS = {"scenario_registry_sha256", "voice_registry_sha256"}
_PLAN_PHASE_A_FIELDS = {
    "takes",
    "minimum_eligible_candidates",
    "seed_policy",
    "seed_base",
    "max_topup_rounds",
}
_PLAN_ROLE_FIELDS = {
    "scenario",
    "character",
    "role",
    "reference_voice",
    "scene_setting",
    "role_identity_sha256",
}
_PLAN_TARGET_FIELDS = {
    "model",
    "scenario",
    "character",
    "role_identity_sha256",
    "role_epoch_sha256",
}

@dataclass(frozen=True)
class AnchorBootstrapPlanSummary:
    plan_path: Path
    plan_sha256: str
    target_count: int


@dataclass(frozen=True)
class AnchorBootstrapGenerationSummary:
    run_id: str
    run_dir: Path
    ledger_sha256: str
    generated_count: int
    eligible_count: int
    rejected_count: int
    failed_count: int


@dataclass(frozen=True)
class AnchorMachineSelectionSummary:
    output_dir: Path
    decision_path: Path
    decision_sha256: str
    selection_path: Path
    selection_sha256: str
    selected_count: int
    screening_pass_count: int
    soft_signal_count: int


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #


def build_anchor_bootstrap_plan_document(
    *,
    model: str,
    model_revision: str,
    scenarios_dir: Path,
    voices_dir: Path,
    anchor_text: str,
) -> dict[str, Any]:
    """53 anchor role の決定論的bootstrap planを組む。

    increment plan は anchor authority SHA を束縛するため、こちらが先に確定する。
    role snapshot は `completion_plan._source_snapshot` と同一の素材から取る。
    """

    from gaya_pipeline.completion_plan import _source_snapshot

    model = _path_segment(model, "anchor bootstrap plan.model")
    model_revision = _text(model_revision, "anchor bootstrap plan.model_revision")
    if not isinstance(anchor_text, str) or not anchor_text:
        raise IncrementAnchorError("anchor textは空でない文字列が必要です。")
    anchor_text_sha256 = hashlib.sha256(anchor_text.encode("utf-8")).hexdigest()
    source_snapshot, all_roles, _documents = _source_snapshot(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
    )
    roles = tuple(role for role in all_roles if role.reference_voice is None)
    if len(roles) != ANCHOR_ROLE_COUNT:
        raise IncrementAnchorError(
            f"anchor対象のno-reference roleは{ANCHOR_ROLE_COUNT}件が必要です: "
            f"actual={len(roles)}",
        )
    sources = {
        "scenario_registry_sha256": source_snapshot["scenario_registry_sha256"],
        "voice_registry_sha256": source_snapshot["voice_registry_sha256"],
    }
    targets = [
        {
            "model": model,
            "scenario": role.scenario,
            "character": role.character,
            "role_identity_sha256": role.role_identity_sha256,
            "role_epoch_sha256": anchor_role_epoch_sha256(
                model=model,
                model_revision=model_revision,
                role=role,
                sources=sources,
                anchor_text_sha256=anchor_text_sha256,
            ),
        }
        for role in roles
    ]
    targets.sort(key=lambda item: (item["scenario"], item["character"]))
    return {
        "format_version": PLAN_FORMAT_VERSION,
        "protocol": PLAN_PROTOCOL,
        "model": model,
        "model_revision": model_revision,
        "sources": sources,
        "anchor_text": anchor_text,
        "anchor_text_sha256": anchor_text_sha256,
        "phase_a": {
            "takes": CANDIDATES_PER_ROLE,
            "minimum_eligible_candidates": MINIMUM_ELIGIBLE_CANDIDATES,
            "seed_policy": SEED_POLICY,
            "seed_base": SEED_BASE,
            "max_topup_rounds": MAX_TOPUP_ROUNDS,
        },
        "roles": [role.document() for role in all_roles],
        "targets": targets,
    }


def anchor_role_epoch_sha256(
    *,
    model: str,
    model_revision: str,
    role: RoleSnapshot,
    sources: Mapping[str, str],
    anchor_text_sha256: str,
) -> str:
    """plan SHAへ循環しないrole epoch (seed導出の基準になる)。"""

    return _canonical_sha256(
        {
            "protocol": EPOCH_PROTOCOL,
            "model": model,
            "model_revision": model_revision,
            "scenario": role.scenario,
            "character": role.character,
            "role_identity_sha256": role.role_identity_sha256,
            "scenario_registry_sha256": sources["scenario_registry_sha256"],
            "voice_registry_sha256": sources["voice_registry_sha256"],
            "anchor_text_sha256": anchor_text_sha256,
        },
    )


def derive_anchor_seed(
    *,
    role_epoch_sha256: str,
    model: str,
    scenario: str,
    character: str,
    attempt: int,
) -> int:
    """#174 `_derive_anchor_seed` と同形。循環する plan SHA だけ role epoch に置換。"""

    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise IncrementAnchorError("anchor seed attemptは1以上の整数が必要です。")
    identity = {
        "policy": SEED_POLICY,
        "seed_base": SEED_BASE,
        "role_epoch_sha256": _require_sha256(role_epoch_sha256, "anchor seed epoch"),
        "model": model,
        "scenario": scenario,
        "character": character,
        "attempt": attempt,
    }
    return int.from_bytes(
        hashlib.sha256(canonical_json(identity).encode("utf-8")).digest()[:4],
        "big",
    )


def round_attempts(round_index: int) -> tuple[int, ...]:
    """round 0 = [1..4]、top-up は round 1/2 のみ (#174の2回上限に一致)。"""

    if (
        isinstance(round_index, bool)
        or not isinstance(round_index, int)
        or not 0 <= round_index <= MAX_TOPUP_ROUNDS
    ):
        raise IncrementAnchorError(
            f"anchor roundは0..{MAX_TOPUP_ROUNDS}が必要です (top-upは最大"
            f"{MAX_TOPUP_ROUNDS}回)。",
        )
    first = round_index * CANDIDATES_PER_ROLE + 1
    return tuple(range(first, first + CANDIDATES_PER_ROLE))


def load_anchor_bootstrap_plan(plan_path: Path) -> dict[str, Any]:
    _require_absolute(plan_path, "anchor bootstrap plan")
    raw = _read_bytes(plan_path, "anchor bootstrap plan")
    document = _decode_json(raw, plan_path)
    normalized = validate_anchor_bootstrap_plan(document)
    if raw != canonical_json(normalized).encode("utf-8"):
        raise IncrementAnchorError(
            "anchor bootstrap planはcanonical bytesが必要です。",
        )
    normalized["plan_sha256"] = hashlib.sha256(raw).hexdigest()
    return normalized


def validate_anchor_bootstrap_plan(document: Any) -> dict[str, Any]:
    root = _exact(document, _PLAN_ROOT_FIELDS, "anchor bootstrap plan")
    if (
        root["format_version"] != PLAN_FORMAT_VERSION
        or root["protocol"] != PLAN_PROTOCOL
    ):
        raise IncrementAnchorError("anchor bootstrap plan identityが不正です。")
    model = _path_segment(root["model"], "anchor bootstrap plan.model")
    model_revision = _text(
        root["model_revision"],
        "anchor bootstrap plan.model_revision",
    )
    sources = _exact(
        root["sources"],
        _PLAN_SOURCES_FIELDS,
        "anchor bootstrap plan.sources",
    )
    sources = {
        field: _require_sha256(sources[field], f"anchor bootstrap plan.sources.{field}")
        for field in sorted(_PLAN_SOURCES_FIELDS)
    }
    anchor_text = _text(root["anchor_text"], "anchor bootstrap plan.anchor_text")
    anchor_text_sha256 = _require_sha256(
        root["anchor_text_sha256"],
        "anchor bootstrap plan.anchor_text_sha256",
    )
    if hashlib.sha256(anchor_text.encode("utf-8")).hexdigest() != anchor_text_sha256:
        raise IncrementAnchorError("anchor bootstrap plan.anchor_text_sha256が不一致です。")
    phase_a = _exact(
        root["phase_a"],
        _PLAN_PHASE_A_FIELDS,
        "anchor bootstrap plan.phase_a",
    )
    if (
        phase_a["takes"] != CANDIDATES_PER_ROLE
        or phase_a["minimum_eligible_candidates"] != MINIMUM_ELIGIBLE_CANDIDATES
        or phase_a["seed_policy"] != SEED_POLICY
        or phase_a["seed_base"] != SEED_BASE
        or phase_a["max_topup_rounds"] != MAX_TOPUP_ROUNDS
    ):
        raise IncrementAnchorError("anchor bootstrap plan.phase_aが既定policyと不一致です。")

    roles_value = root["roles"]
    if not isinstance(roles_value, list) or not roles_value:
        raise IncrementAnchorError("anchor bootstrap plan.rolesが不正です。")
    roles: list[dict[str, Any]] = []
    role_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for index, item in enumerate(roles_value):
        entry = _exact(
            item,
            _PLAN_ROLE_FIELDS,
            f"anchor bootstrap plan.roles[{index}]",
        )
        role = dict(entry)
        role["scenario"] = _path_segment(entry["scenario"], "roles.scenario")
        role["character"] = _path_segment(entry["character"], "roles.character")
        role["role_identity_sha256"] = _require_sha256(
            entry["role_identity_sha256"],
            "roles.role_identity_sha256",
        )
        identity = (role["scenario"], role["character"])
        if identity in role_by_identity:
            raise IncrementAnchorError(
                f"anchor bootstrap plan.rolesが重複しています: {identity}",
            )
        role_by_identity[identity] = role
        roles.append(role)

    targets_value = root["targets"]
    if not isinstance(targets_value, list) or len(targets_value) != ANCHOR_ROLE_COUNT:
        raise IncrementAnchorError(
            f"anchor bootstrap plan.targetsは{ANCHOR_ROLE_COUNT}件が必要です。",
        )
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(targets_value):
        entry = _exact(
            item,
            _PLAN_TARGET_FIELDS,
            f"anchor bootstrap plan.targets[{index}]",
        )
        if entry["model"] != model:
            raise IncrementAnchorError("anchor bootstrap plan.targetsは単一modelが必要です。")
        scenario = _path_segment(entry["scenario"], "targets.scenario")
        character = _path_segment(entry["character"], "targets.character")
        identity = (scenario, character)
        if identity in seen:
            raise IncrementAnchorError(
                f"anchor bootstrap plan.targetsが重複しています: {identity}",
            )
        seen.add(identity)
        role = role_by_identity.get(identity)
        if role is None or role["reference_voice"] is not None:
            raise IncrementAnchorError(
                f"anchor bootstrap plan.targetsはno-reference roleが必要です: {identity}",
            )
        if entry["role_identity_sha256"] != role["role_identity_sha256"]:
            raise IncrementAnchorError(
                f"anchor bootstrap plan.targets role identityが不一致です: {identity}",
            )
        expected_epoch = _canonical_sha256(
            {
                "protocol": EPOCH_PROTOCOL,
                "model": model,
                "model_revision": model_revision,
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": role["role_identity_sha256"],
                "scenario_registry_sha256": sources["scenario_registry_sha256"],
                "voice_registry_sha256": sources["voice_registry_sha256"],
                "anchor_text_sha256": anchor_text_sha256,
            },
        )
        if (
            _require_sha256(entry["role_epoch_sha256"], "targets.role_epoch_sha256")
            != expected_epoch
        ):
            raise IncrementAnchorError(
                f"anchor bootstrap plan.targets role epochが不一致です: {identity}",
            )
        targets.append(
            {
                "model": model,
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": role["role_identity_sha256"],
                "role_epoch_sha256": expected_epoch,
            },
        )
    if [(item["scenario"], item["character"]) for item in targets] != sorted(seen):
        raise IncrementAnchorError(
            "anchor bootstrap plan.targetsはscenario/character昇順が必要です。",
        )
    return {
        "format_version": PLAN_FORMAT_VERSION,
        "protocol": PLAN_PROTOCOL,
        "model": model,
        "model_revision": model_revision,
        "sources": sources,
        "anchor_text": anchor_text,
        "anchor_text_sha256": anchor_text_sha256,
        "phase_a": {
            "takes": CANDIDATES_PER_ROLE,
            "minimum_eligible_candidates": MINIMUM_ELIGIBLE_CANDIDATES,
            "seed_policy": SEED_POLICY,
            "seed_base": SEED_BASE,
            "max_topup_rounds": MAX_TOPUP_ROUNDS,
        },
        "roles": roles,
        "targets": targets,
    }


def write_anchor_bootstrap_plan(
    *,
    model: str,
    model_revision: str,
    scenarios_dir: Path,
    voices_dir: Path,
    anchor_text: str,
    output_path: Path,
) -> AnchorBootstrapPlanSummary:
    _require_absolute(output_path, "anchor bootstrap plan output")
    document = build_anchor_bootstrap_plan_document(
        model=model,
        model_revision=model_revision,
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        anchor_text=anchor_text,
    )
    normalized = validate_anchor_bootstrap_plan(document)
    payload = canonical_json(normalized).encode("utf-8")
    _write_new(output_path, payload)
    digest = hashlib.sha256(payload).hexdigest()
    _write_new(
        output_path.with_suffix(".sha256"),
        f"{digest}\n".encode("ascii"),
    )
    return AnchorBootstrapPlanSummary(
        plan_path=output_path,
        plan_sha256=digest,
        target_count=len(normalized["targets"]),
    )


def anchor_round_targets(
    *,
    plan_document: Mapping[str, Any],
    round_index: int,
    identities: Sequence[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """あるroundで生成すべきtarget x attempt x seed を決定論的に導く。"""

    attempts = round_attempts(round_index)
    selected = (
        None if identities is None else {tuple(item) for item in identities}
    )
    if selected is not None and not selected:
        raise IncrementAnchorError("top-up対象が空です。")
    result: list[dict[str, Any]] = []
    for target in plan_document["targets"]:
        identity = (target["scenario"], target["character"])
        if selected is not None and identity not in selected:
            continue
        result.append(
            {
                **{key: target[key] for key in sorted(_PLAN_TARGET_FIELDS)},
                "round": round_index,
                "attempts": list(attempts),
                "seeds": [
                    derive_anchor_seed(
                        role_epoch_sha256=target["role_epoch_sha256"],
                        model=target["model"],
                        scenario=target["scenario"],
                        character=target["character"],
                        attempt=attempt,
                    )
                    for attempt in attempts
                ],
            },
        )
    if selected is not None and len(result) != len(selected):
        raise IncrementAnchorError("top-up対象がplan targetに存在しません。")
    return result


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #


def run_anchor_bootstrap_generation(
    *,
    plan_document: Mapping[str, Any],
    plan_sha256: str,
    round_index: int,
    identities: Sequence[tuple[str, str]] | None,
    artifacts_dir: Path,
    run_id: str,
    generator: Any | None = None,
) -> AnchorBootstrapGenerationSummary:
    """既存Phase A harnessと同じlayoutで候補を生成する。"""

    _require_absolute(artifacts_dir, "artifacts")
    run_id = _path_segment(run_id, "anchor run id")
    model = plan_document["model"]
    model_revision = plan_document["model_revision"]
    anchor_text = plan_document["anchor_text"]
    anchor_text_sha256 = plan_document["anchor_text_sha256"]
    targets = anchor_round_targets(
        plan_document=plan_document,
        round_index=round_index,
        identities=identities,
    )
    roles = {
        (role["scenario"], role["character"]): role
        for role in plan_document["roles"]
    }

    run_root = artifacts_dir / "role-anchors" / "runs" / run_id
    if run_root.exists():
        raise IncrementAnchorError(f"anchor runは既存pathを拒否します: {run_root}")
    pending = run_root.with_name(f".{run_id}.pending")
    if pending.exists():
        raise IncrementAnchorError(f"pending anchor runが残っています: {pending}")

    effective = generator if generator is not None else _create_generator(model)
    profile = getattr(effective, "profile", None)
    if profile is not None and (
        profile.id != model or profile.version != model_revision
    ):
        raise IncrementAnchorError(
            "anchor generatorのprofileがplan model/revisionと一致しません。",
        )

    attempts_records: list[dict[str, Any]] = []
    eligible = rejected = failed = 0
    pending.mkdir(parents=True)
    try:
        for target in targets:
            role_document = roles[(target["scenario"], target["character"])]
            role = _role_snapshot(role_document)
            for attempt, seed in zip(
                target["attempts"],
                target["seeds"],
                strict=True,
            ):
                relative = PurePosixPath(
                    "role-anchors",
                    "runs",
                    run_id,
                    model,
                    target["scenario"],
                    target["character"],
                    f"attempt-{attempt:04d}.wav",
                )
                output_wav = pending / Path(
                    *relative.parts[3:],
                )
                output_wav.parent.mkdir(parents=True, exist_ok=True)
                generation_input = _generation_input(
                    generator=effective,
                    plan_sha256=plan_sha256,
                    target=target,
                    role=role,
                    model_revision=model_revision,
                    attempt=attempt,
                    seed=seed,
                    anchor_text=anchor_text,
                    anchor_text_sha256=anchor_text_sha256,
                )
                generation_input_sha256 = _canonical_sha256(generation_input)
                error: str | None = None
                realized: dict[str, Any] | None = None
                try:
                    realized = dict(
                        effective.generate_role_anchor(
                            role,
                            seed=seed,
                            output_wav=output_wav,
                        ),
                    )
                except Exception as exception:  # noqa: BLE001 - 記録して継続
                    error = f"{type(exception).__name__}: {exception}"
                if error is not None:
                    failed += 1
                    status = "failed"
                    qc = {
                        "mechanical": "fail",
                        "content": "not_checked",
                        "notes": ["generation_failed"],
                    }
                    audio_sha256 = None
                else:
                    qc = _mechanical_qc(output_wav)
                    audio_sha256 = _sha256_file(output_wav)
                    status = (
                        "eligible" if qc["mechanical"] == "pass" else "rejected"
                    )
                    if status == "eligible":
                        eligible += 1
                    else:
                        rejected += 1
                anchor_id = (
                    None
                    if audio_sha256 is None
                    else _canonical_sha256(
                        {
                            "protocol": IDENTITY_PROTOCOL,
                            "plan_sha256": plan_sha256,
                            "model": model,
                            "model_revision": model_revision,
                            "scenario": target["scenario"],
                            "character": target["character"],
                            "role_identity_sha256": target["role_identity_sha256"],
                            "role_epoch_sha256": target["role_epoch_sha256"],
                            "attempt": attempt,
                            "seed": seed,
                            "generation_input_sha256": generation_input_sha256,
                            "audio_sha256": audio_sha256,
                        },
                    )
                )
                record = {
                    "model": model,
                    "model_revision": model_revision,
                    "scenario": target["scenario"],
                    "character": target["character"],
                    "role_identity_sha256": target["role_identity_sha256"],
                    "role_epoch_sha256": target["role_epoch_sha256"],
                    "round": round_index,
                    "attempt": attempt,
                    "seed": seed,
                    "generation_input": generation_input,
                    "generation_input_sha256": generation_input_sha256,
                    "status": status,
                    "anchor_id": anchor_id,
                    "audio_path": str(relative),
                    "audio_sha256": audio_sha256,
                    "qc": qc,
                    "realized": realized,
                    "error": error,
                }
                attempts_records.append(record)
                _write_new(
                    output_wav.with_suffix(".json"),
                    canonical_json(record).encode("utf-8"),
                )
        ledger = {
            "format_version": RUN_FORMAT_VERSION,
            "protocol": RUN_PROTOCOL,
            "run_id": run_id,
            "plan_sha256": plan_sha256,
            "model": model,
            "model_revision": model_revision,
            "round": round_index,
            "seed_policy": SEED_POLICY,
            "seed_base": SEED_BASE,
            "attempts": attempts_records,
        }
        ledger_payload = canonical_json(ledger).encode("utf-8")
        _write_new(pending / "ledger.json", ledger_payload)
        ledger_sha256 = hashlib.sha256(ledger_payload).hexdigest()
        _write_new(
            pending / "ledger.sha256",
            f"{ledger_sha256}\n".encode("ascii"),
        )
        run_root.parent.mkdir(parents=True, exist_ok=True)
        pending.rename(run_root)
    except Exception:
        shutil.rmtree(pending, ignore_errors=True)
        raise
    finally:
        close = getattr(effective, "close_role_anchor_generation", None)
        if close is not None:
            close()

    return AnchorBootstrapGenerationSummary(
        run_id=run_id,
        run_dir=run_root,
        ledger_sha256=ledger_sha256,
        generated_count=len(attempts_records),
        eligible_count=eligible,
        rejected_count=rejected,
        failed_count=failed,
    )


# --------------------------------------------------------------------------- #
# machine selection
# --------------------------------------------------------------------------- #


def gender_screening(
    *,
    expected_gender: str,
    median_f0_hz: float | None,
) -> dict[str, Any]:
    """`role-gender-f0-soft-v1` と同一policy (site側も同じ式で再計算する)。"""

    median = None if median_f0_hz is None else float(median_f0_hz)
    if expected_gender == "neutral":
        status = "not_applicable"
        signal = None
    elif median is None:
        status = "review_required"
        signal = "gender_f0_unavailable"
    elif expected_gender == "female" and median < FEMALE_F0_LOWER_BOUND_HZ:
        status = "review_required"
        signal = "gender_f0_below_expected"
    elif expected_gender == "male" and median > MALE_F0_UPPER_BOUND_HZ:
        status = "review_required"
        signal = "gender_f0_above_expected"
    else:
        status = "pass"
        signal = None
    return {
        "protocol": GENDER_SCREENING_PROTOCOL,
        "expected_gender": expected_gender,
        "median_f0_hz": median,
        "status": status,
        "signal": signal,
    }


def screening_distance_hz(
    *,
    expected_gender: str,
    median_f0_hz: float | None,
) -> float:
    """境界からの逸脱量。pass候補は必ず0になる。"""

    if expected_gender == "neutral":
        return 0.0
    if median_f0_hz is None:
        return float("inf")
    median = float(median_f0_hz)
    if expected_gender == "female":
        return max(0.0, FEMALE_F0_LOWER_BOUND_HZ - median)
    if expected_gender == "male":
        return max(0.0, median - MALE_F0_UPPER_BOUND_HZ)
    raise IncrementAnchorError(f"未知のexpected genderです: {expected_gender}")


def measure_median_f0_hz(
    audio_path: Path,
    *,
    anchor_text: str,
    librosa_module: Any | None = None,
    numpy_module: Any | None = None,
) -> dict[str, Any]:
    """QC本体と同じ 16 kHz decode -> `analyze_prosody_samples` でF0を測る。

    canary script は 48 kHz のsampleを 16 kHz 前提の解析へ渡していたため
    F0 が約1/3にずれていた。ここでは QC runtime と同じ ffmpeg decode を通す。
    """

    import importlib

    librosa = (
        librosa_module
        if librosa_module is not None
        else importlib.import_module("librosa")
    )
    numpy = (
        numpy_module
        if numpy_module is not None
        else importlib.import_module("numpy")
    )
    samples = _decode_16k_mono(audio_path, numpy_module=numpy)
    prosody = analyze_prosody_samples(
        samples,
        mora_count=count_japanese_mora(anchor_text),
        final_intonation="free",
        librosa_module=librosa,
        numpy_module=numpy,
    )
    return prosody["f0"]


def _decode_16k_mono(audio_path: Path, *, numpy_module: Any) -> Any:
    tools = find_audio_tools()
    completed = subprocess.run(  # noqa: S603 - 固定引数のffmpeg
        [
            str(tools.ffmpeg),
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-ar",
            str(SAMPLE_RATE_HZ),
            "-ac",
            "1",
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise IncrementAnchorError(
            f"anchor audioをdecodeできません: {audio_path}: "
            f"{completed.stderr.decode('utf-8', 'replace').strip()}",
        )
    samples = numpy_module.frombuffer(completed.stdout, dtype=numpy_module.float32)
    if samples.ndim != 1 or not samples.size:
        raise IncrementAnchorError(f"anchor audio sampleが空です: {audio_path}")
    if not bool(numpy_module.all(numpy_module.isfinite(samples))):
        raise IncrementAnchorError(f"anchor audio sampleが有限ではありません: {audio_path}")
    return samples


def rank_anchor_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """機械順位付け。mechanical passのみeligible、F0 screening順、最後にattempt順。"""

    scored = [
        (
            0 if candidate["screening"]["status"] != "review_required" else 1,
            candidate["screening_distance_hz"],
            int(candidate["attempt"]),
            candidate,
        )
        for candidate in candidates
    ]
    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    return [
        {**dict(candidate), "rank": index}
        for index, (_status, _distance, _attempt, candidate) in enumerate(scored, 1)
    ]


def select_role_anchors(
    *,
    plan_document: Mapping[str, Any],
    plan_sha256: str,
    run_ids: Sequence[str],
    artifacts_dir: Path,
    output_dir: Path,
    librosa_module: Any | None = None,
    numpy_module: Any | None = None,
) -> AnchorMachineSelectionSummary:
    """全候補を機械選抜し、決定と選択をwrite-onceで確定する。"""

    _require_absolute(artifacts_dir, "artifacts")
    _require_absolute(output_dir, "anchor selection output")
    if output_dir.exists():
        raise IncrementAnchorError(
            f"anchor selection outputは既存pathを拒否します: {output_dir}",
        )
    if not run_ids:
        raise IncrementAnchorError("anchor selectionには1件以上のrun-idが必要です。")
    if len(set(run_ids)) != len(run_ids):
        raise IncrementAnchorError("anchor run-idが重複しています。")

    model = plan_document["model"]
    model_revision = plan_document["model_revision"]
    anchor_text = plan_document["anchor_text"]
    anchor_text_sha256 = plan_document["anchor_text_sha256"]
    roles = {
        (role["scenario"], role["character"]): role
        for role in plan_document["roles"]
    }
    targets = {
        (target["scenario"], target["character"]): target
        for target in plan_document["targets"]
    }

    by_role: dict[tuple[str, str], dict[int, dict[str, Any]]] = {
        identity: {} for identity in targets
    }
    run_records: list[dict[str, Any]] = []
    for run_id in run_ids:
        ledger, ledger_sha256 = _load_run_ledger(
            artifacts_dir=artifacts_dir,
            run_id=run_id,
            plan_sha256=plan_sha256,
            model=model,
            model_revision=model_revision,
        )
        run_records.append(
            {
                "run_id": run_id,
                "round": ledger["round"],
                "ledger_sha256": ledger_sha256,
            },
        )
        for attempt_record in ledger["attempts"]:
            identity = (attempt_record["scenario"], attempt_record["character"])
            if identity not in by_role:
                raise IncrementAnchorError(
                    f"anchor runにplan外のroleがあります: {identity}",
                )
            attempt = int(attempt_record["attempt"])
            if attempt in by_role[identity]:
                raise IncrementAnchorError(
                    f"anchor attemptが重複しています: {identity}/{attempt}",
                )
            expected_seed = derive_anchor_seed(
                role_epoch_sha256=targets[identity]["role_epoch_sha256"],
                model=model,
                scenario=identity[0],
                character=identity[1],
                attempt=attempt,
            )
            if int(attempt_record["seed"]) != expected_seed:
                raise IncrementAnchorError(
                    f"anchor seedがplan導出と一致しません: {identity}/{attempt}",
                )
            by_role[identity][attempt] = dict(attempt_record)

    decision_groups: list[dict[str, Any]] = []
    selection_groups: list[dict[str, Any]] = []
    copy_jobs: list[tuple[Path, str, str]] = []
    topup_required: list[tuple[str, str]] = []
    screening_pass = 0
    soft_signals = 0

    for identity in sorted(targets):
        target = targets[identity]
        role_document = roles[identity]
        expected_gender = str(role_document["role"]["gender"])
        attempts = by_role[identity]
        if not attempts:
            raise IncrementAnchorError(f"anchor候補がありません: {identity}")
        candidates: list[dict[str, Any]] = []
        for attempt in sorted(attempts):
            record = attempts[attempt]
            if record["status"] != "eligible":
                continue
            audio_path = _resolve_run_audio(artifacts_dir, record)
            _verify_file_sha256(audio_path, record["audio_sha256"])
            f0 = measure_median_f0_hz(
                audio_path,
                anchor_text=anchor_text,
                librosa_module=librosa_module,
                numpy_module=numpy_module,
            )
            screening = gender_screening(
                expected_gender=expected_gender,
                median_f0_hz=f0["median_hz"],
            )
            candidates.append(
                {
                    "anchor_id": record["anchor_id"],
                    "attempt": attempt,
                    "seed": int(record["seed"]),
                    "audio_path": record["audio_path"],
                    "audio_sha256": record["audio_sha256"],
                    "generation_input_sha256": record["generation_input_sha256"],
                    "qc": dict(record["qc"]),
                    "f0": dict(f0),
                    "screening": screening,
                    "screening_distance_hz": screening_distance_hz(
                        expected_gender=expected_gender,
                        median_f0_hz=f0["median_hz"],
                    ),
                },
            )
        if not candidates:
            raise IncrementAnchorError(
                f"mechanical passのanchor候補が1件もありません: {identity}",
            )
        ranked = rank_anchor_candidates(candidates)
        selected = ranked[0]
        eligible_count = len(ranked)
        passing = [
            item for item in ranked if item["screening"]["status"] != "review_required"
        ]
        rounds_used = 1 + max(
            (attempt - 1) // CANDIDATES_PER_ROLE for attempt in attempts
        )
        exhausted = rounds_used > MAX_TOPUP_ROUNDS
        group_soft_signals: list[str] = []
        if not passing:
            if not exhausted:
                topup_required.append(identity)
            else:
                group_soft_signals.append(SOFT_SIGNAL_EXHAUSTED)
                soft_signals += 1
        else:
            screening_pass += 1
        decision_group = {
            "model": model,
            "scenario": identity[0],
            "character": identity[1],
            "role_identity_sha256": target["role_identity_sha256"],
            "role_epoch_sha256": target["role_epoch_sha256"],
            "expected_gender": expected_gender,
            "authority": {
                "type": SELECTION_AUTHORITY_TYPE,
                "policy_version": SELECTION_POLICY,
                "minimum_eligible_candidates": MINIMUM_ELIGIBLE_CANDIDATES,
                "gate_policy_version": GATE_POLICY_VERSION,
            },
            "rounds_used": rounds_used,
            "eligible_candidate_count": eligible_count,
            "candidates": [
                {
                    key: item[key]
                    for key in (
                        "anchor_id",
                        "attempt",
                        "seed",
                        "audio_path",
                        "audio_sha256",
                        "generation_input_sha256",
                        "qc",
                        "f0",
                        "screening",
                        "rank",
                    )
                }
                for item in ranked
            ],
            "decision": {
                "type": "selected",
                "anchor_id": selected["anchor_id"],
                "screening_status": selected["screening"]["status"],
            },
            "soft_signals": group_soft_signals,
        }
        decision_groups.append(decision_group)

        source_relative = f"audio/{selected['anchor_id']}.wav"
        copy_jobs.append(
            (
                _resolve_run_audio(
                    artifacts_dir,
                    {"audio_path": selected["audio_path"]},
                ),
                source_relative,
                selected["audio_sha256"],
            ),
        )
        group_decision_sha256 = _canonical_sha256(decision_group)
        selection_groups.append(
            {
                "model": model,
                "model_revision": model_revision,
                "scenario": identity[0],
                "character": identity[1],
                "role_identity": _role_identity_document(role_document),
                "role_identity_sha256": target["role_identity_sha256"],
                "review_role_epoch_sha256": target["role_epoch_sha256"],
                "role_epoch_sha256": _canonical_sha256(
                    {
                        "protocol": SELECTED_EPOCH_PROTOCOL,
                        "model": model,
                        "model_revision": model_revision,
                        "scenario": identity[0],
                        "character": identity[1],
                        "role_identity_sha256": target["role_identity_sha256"],
                        "review_role_epoch_sha256": target["role_epoch_sha256"],
                        "anchor_id": selected["anchor_id"],
                        "audio_sha256": selected["audio_sha256"],
                        "decision_sha256": group_decision_sha256,
                    },
                ),
                "anchor_id": selected["anchor_id"],
                "attempt": selected["attempt"],
                "seed": selected["seed"],
                "audio_path": source_relative,
                "audio_sha256": selected["audio_sha256"],
                "anchor_text": anchor_text,
                "anchor_text_sha256": anchor_text_sha256,
                "authority": dict(decision_group["authority"]),
                "screening": dict(selected["screening"]),
                "soft_signals": list(group_soft_signals),
                "decision_sha256": group_decision_sha256,
            },
        )

    if topup_required:
        details = ", ".join(f"{scenario}/{character}" for scenario, character in topup_required)
        raise IncrementAnchorError(
            "F0 gender screeningを通過する候補がないroleがあります。"
            f"`increment anchor-topup-plan` で有界top-up ({MAX_TOPUP_ROUNDS}回上限) "
            f"を実施してください: {details}",
        )

    decision = {
        "format_version": DECISION_FORMAT_VERSION,
        "protocol": DECISION_PROTOCOL,
        "plan_sha256": plan_sha256,
        "model": model,
        "model_revision": model_revision,
        "runs": sorted(run_records, key=lambda item: item["run_id"]),
        "groups": decision_groups,
    }
    decision_payload = canonical_json(decision).encode("utf-8")
    decision_sha256 = hashlib.sha256(decision_payload).hexdigest()
    selection = validate_machine_anchor_selection(
        {
            "format_version": SELECTION_FORMAT_VERSION,
            "protocol": SELECTION_PROTOCOL,
            "plan_sha256": plan_sha256,
            "candidate_set_sha256": decision_sha256,
            "model": model,
            "model_revision": model_revision,
            "groups": selection_groups,
        },
    )
    selection_payload = canonical_json(selection).encode("utf-8")
    selection_sha256 = hashlib.sha256(selection_payload).hexdigest()

    pending = output_dir.with_name(f".{output_dir.name}.pending")
    if pending.exists():
        raise IncrementAnchorError(f"pending outputが残っています: {pending}")
    pending.mkdir(parents=True)
    try:
        for source, relative, expected_sha256 in copy_jobs:
            destination = pending / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            _verify_file_sha256(destination, expected_sha256)
        _write_new(pending / f"{DECISION_PROTOCOL}.json", decision_payload)
        _write_new(
            pending / f"{DECISION_PROTOCOL}.sha256",
            f"{decision_sha256}\n".encode("ascii"),
        )
        _write_new(pending / f"{SELECTION_PROTOCOL}.json", selection_payload)
        _write_new(
            pending / f"{SELECTION_PROTOCOL}.sha256",
            f"{selection_sha256}\n".encode("ascii"),
        )
        if output_dir.exists():
            raise IncrementAnchorError(
                f"anchor selection outputは既存pathを拒否します: {output_dir}",
            )
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        pending.rename(output_dir)
    except Exception:
        shutil.rmtree(pending, ignore_errors=True)
        raise

    return AnchorMachineSelectionSummary(
        output_dir=output_dir,
        decision_path=output_dir / f"{DECISION_PROTOCOL}.json",
        decision_sha256=decision_sha256,
        selection_path=output_dir / f"{SELECTION_PROTOCOL}.json",
        selection_sha256=selection_sha256,
        selected_count=len(selection_groups),
        screening_pass_count=screening_pass,
        soft_signal_count=soft_signals,
    )


_SELECTION_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_sha256",
    "candidate_set_sha256",
    "model",
    "model_revision",
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
    "authority",
    "screening",
    "soft_signals",
    "decision_sha256",
}
_SELECTION_AUTHORITY_FIELDS = {
    "type",
    "policy_version",
    "minimum_eligible_candidates",
    "gate_policy_version",
}
_SELECTION_SCREENING_FIELDS = {
    "protocol",
    "expected_gender",
    "median_f0_hz",
    "status",
    "signal",
}
_ROLE_IDENTITY_FIELDS = {
    "scenario",
    "character",
    "role",
    "reference_voice",
    "scene_setting",
}


def validate_machine_anchor_selection(document: Any) -> dict[str, Any]:
    root = _exact(document, _SELECTION_ROOT_FIELDS, "machine anchor selection")
    if (
        root["format_version"] != SELECTION_FORMAT_VERSION
        or root["protocol"] != SELECTION_PROTOCOL
    ):
        raise IncrementAnchorError("machine anchor selection identityが不正です。")
    model = _path_segment(root["model"], "machine anchor selection.model")
    model_revision = _text(
        root["model_revision"],
        "machine anchor selection.model_revision",
    )
    plan_sha = _require_sha256(root["plan_sha256"], "machine anchor selection.plan_sha256")
    candidate_sha = _require_sha256(
        root["candidate_set_sha256"],
        "machine anchor selection.candidate_set_sha256",
    )
    groups_value = root["groups"]
    if not isinstance(groups_value, list) or not groups_value:
        raise IncrementAnchorError("machine anchor selection.groupsが不正です。")
    groups: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, item in enumerate(groups_value):
        group = _exact(
            item,
            _SELECTION_GROUP_FIELDS,
            f"machine anchor selection.groups[{index}]",
        )
        if group["model"] != model or group["model_revision"] != model_revision:
            raise IncrementAnchorError(
                "machine anchor selection groupのmodel bindingが不一致です。",
            )
        identity = (
            model,
            _path_segment(group["scenario"], "groups.scenario"),
            _path_segment(group["character"], "groups.character"),
        )
        if identity in seen:
            raise IncrementAnchorError(
                f"machine anchor selection groupが重複しています: {identity}",
            )
        seen.add(identity)
        role_identity = _exact(
            group["role_identity"],
            _ROLE_IDENTITY_FIELDS,
            "groups.role_identity",
        )
        if (
            role_identity["scenario"] != identity[1]
            or role_identity["character"] != identity[2]
        ):
            raise IncrementAnchorError(
                "machine anchor selection role identityが不一致です。",
            )
        if role_identity["reference_voice"] is not None:
            raise IncrementAnchorError(
                "explicit reference roleにgenerated anchorは使用できません。",
            )
        authority = _exact(
            group["authority"],
            _SELECTION_AUTHORITY_FIELDS,
            "groups.authority",
        )
        if (
            authority["type"] != SELECTION_AUTHORITY_TYPE
            or authority["policy_version"] != SELECTION_POLICY
            or authority["gate_policy_version"] != GATE_POLICY_VERSION
            or authority["minimum_eligible_candidates"]
            != MINIMUM_ELIGIBLE_CANDIDATES
        ):
            raise IncrementAnchorError(
                "machine anchor selection authorityが機械選抜契約と不一致です。",
            )
        screening = _exact(
            group["screening"],
            _SELECTION_SCREENING_FIELDS,
            "groups.screening",
        )
        if screening["protocol"] != GENDER_SCREENING_PROTOCOL:
            raise IncrementAnchorError("groups.screening.protocolが不正です。")
        expected = gender_screening(
            expected_gender=str(screening["expected_gender"]),
            median_f0_hz=screening["median_f0_hz"],
        )
        if screening != expected:
            raise IncrementAnchorError(
                "groups.screeningがF0 policyと一致しません (status/signalは再計算されます)。",
            )
        soft_signals = group["soft_signals"]
        if not isinstance(soft_signals, list) or any(
            signal != SOFT_SIGNAL_EXHAUSTED for signal in soft_signals
        ):
            raise IncrementAnchorError("groups.soft_signalsが不正です。")
        if screening["status"] == "review_required" and not soft_signals:
            raise IncrementAnchorError(
                "screening review_requiredのgroupにはsoft signalが必要です。",
            )
        if screening["status"] != "review_required" and soft_signals:
            raise IncrementAnchorError(
                "screening pass/not_applicableのgroupにsoft signalは記録できません。",
            )
        audio_path = _text(group["audio_path"], "groups.audio_path")
        if audio_path != f"audio/{group['anchor_id']}.wav":
            raise IncrementAnchorError("groups.audio_pathがanchor_idと一致しません。")
        anchor_text = _text(group["anchor_text"], "groups.anchor_text")
        if (
            hashlib.sha256(anchor_text.encode("utf-8")).hexdigest()
            != group["anchor_text_sha256"]
        ):
            raise IncrementAnchorError("groups.anchor_text_sha256が不一致です。")
        attempt = group["attempt"]
        if (
            isinstance(attempt, bool)
            or not isinstance(attempt, int)
            or not 1 <= attempt <= MAX_ATTEMPT
        ):
            raise IncrementAnchorError(
                f"groups.attemptは1..{MAX_ATTEMPT}が必要です (top-up上限"
                f"{MAX_TOPUP_ROUNDS}回)。",
            )
        normalized = dict(group)
        normalized["role_identity"] = role_identity
        normalized["authority"] = authority
        normalized["screening"] = screening
        normalized["soft_signals"] = list(soft_signals)
        for field in (
            "role_identity_sha256",
            "review_role_epoch_sha256",
            "role_epoch_sha256",
            "anchor_id",
            "audio_sha256",
            "anchor_text_sha256",
            "decision_sha256",
        ):
            normalized[field] = _require_sha256(group[field], f"groups.{field}")
        groups.append(normalized)
    if [
        (group["scenario"], group["character"]) for group in groups
    ] != sorted((group["scenario"], group["character"]) for group in groups):
        raise IncrementAnchorError(
            "machine anchor selection.groupsはscenario/character昇順が必要です。",
        )
    return {
        "format_version": SELECTION_FORMAT_VERSION,
        "protocol": SELECTION_PROTOCOL,
        "plan_sha256": plan_sha,
        "candidate_set_sha256": candidate_sha,
        "model": model,
        "model_revision": model_revision,
        "groups": groups,
    }


def validate_any_anchor_selection(document: Any) -> dict[str, Any]:
    """人手選抜 v1 / 増分の機械選抜 v1 をprotocolで分岐して検証する。

    どちらも root に `plan_sha256` / `candidate_set_sha256` / `groups` を持つ。
    """

    protocol = document.get("protocol") if isinstance(document, dict) else None
    if protocol == SELECTION_PROTOCOL:
        return validate_machine_anchor_selection(document)
    from gaya_pipeline.completion_anchor import validate_anchor_selection

    return validate_anchor_selection(document)


def resolve_increment_anchor(
    *,
    selection_path: Path,
    plan_sha256: str,
    model: str,
    model_revision: str,
    role: RoleSnapshot,
) -> SelectedRoleAnchor:
    """protocolで分岐し、人手選抜/機械選抜どちらのselectionも解決する。"""

    _require_absolute(selection_path, "anchor selection")
    raw = _read_bytes(selection_path, "anchor selection")
    document = _decode_json(raw, selection_path)
    protocol = document.get("protocol") if isinstance(document, dict) else None
    if protocol != SELECTION_PROTOCOL:
        return resolve_selected_anchor(
            selection_path=selection_path,
            plan_sha256=plan_sha256,
            model=model,
            model_revision=model_revision,
            role=role,
        )

    selection = validate_machine_anchor_selection(document)
    if canonical_json(selection).encode("utf-8") != raw:
        raise IncrementAnchorError("anchor selectionはcanonical bytesが必要です。")
    selection_sha256 = hashlib.sha256(raw).hexdigest()
    marker = selection_path.with_suffix(".sha256")
    try:
        marker_value = marker.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise IncrementAnchorError(
            f"anchor selection SHA markerを読めません: {marker}",
        ) from error
    if marker_value != selection_sha256:
        raise IncrementAnchorError("anchor selection SHA markerが不一致です。")
    if selection["plan_sha256"] != plan_sha256:
        raise IncrementAnchorError(
            "anchor selection plan SHAが現在のanchor bootstrap planと一致しません。",
        )
    if selection["model"] != model or selection["model_revision"] != model_revision:
        raise IncrementAnchorError(
            "anchor selectionのmodel/revisionがadapterと一致しません。",
        )
    if role.reference_voice is not None:
        raise IncrementAnchorError(
            "明示reference roleにgenerated anchor selectionは使用できません。",
        )
    matches = [
        group
        for group in selection["groups"]
        if (group["scenario"], group["character"]) == (role.scenario, role.character)
    ]
    if len(matches) != 1:
        raise IncrementAnchorError(
            "anchor selectionにscenario/characterの一意な選択がありません: "
            f"{role.scenario}/{role.character}",
        )
    group = matches[0]
    expected_identity = {
        "scenario": role.scenario,
        "character": role.character,
        "role": dict(role.role),
        "reference_voice": role.reference_voice,
        "scene_setting": role.scene_setting,
    }
    if (
        group["role_identity"] != expected_identity
        or group["role_identity_sha256"] != role.role_identity_sha256
    ):
        raise IncrementAnchorError(
            "selected anchorの完全role identityがscenarioと一致しません。",
        )
    audio_path = selection_path.parent / group["audio_path"]
    _verify_file_sha256(audio_path, group["audio_sha256"])
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


def load_machine_anchor_selection(
    selection_path: Path,
    *,
    plan: IncrementPlan,
) -> tuple[str, dict[tuple[str, str, str], str]]:
    """Phase B用に (selection SHA, role epoch) を取り出し、planへ束縛する。"""

    _require_absolute(selection_path, "anchor selection")
    raw = _read_bytes(selection_path, "anchor selection")
    selection = validate_machine_anchor_selection(_decode_json(raw, selection_path))
    if canonical_json(selection).encode("utf-8") != raw:
        raise IncrementAnchorError("anchor selectionはcanonical bytesが必要です。")
    digest = hashlib.sha256(raw).hexdigest()
    marker = selection_path.with_suffix(".sha256")
    try:
        marker_value = marker.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise IncrementAnchorError(
            f"anchor selection SHA markerを読めません: {marker}",
        ) from error
    if (
        marker_value != digest
        or digest != plan.anchor_selection_sha256
        or selection["plan_sha256"] != plan.anchor_source_plan_sha256
        or selection["candidate_set_sha256"] != plan.anchor_candidate_set_sha256
    ):
        raise IncrementAnchorError(
            "anchor selection marker/authority SHAがincrement planと一致しません。",
        )
    if selection["model"] != plan.model:
        raise IncrementAnchorError("anchor selection modelがincrement planと一致しません。")
    if len(selection["groups"]) != ANCHOR_ROLE_COUNT:
        raise IncrementAnchorError(
            f"anchor selectionはexact {ANCHOR_ROLE_COUNT} groupが必要です。",
        )
    expected = {
        (plan.model, role.scenario, role.character)
        for role in plan.anchor_roles()
    }
    actual = {
        (group["model"], group["scenario"], group["character"])
        for group in selection["groups"]
    }
    if actual != expected:
        raise IncrementAnchorError(
            f"anchor selection group集合が{ANCHOR_ROLE_COUNT} no-reference roleと一致しません。",
        )
    for group in selection["groups"]:
        role = plan.role(group["scenario"], group["character"])
        if (
            group["model_revision"] != plan.models[group["model"]]
            or group["role_identity_sha256"] != role.role_identity_sha256
        ):
            raise IncrementAnchorError(
                "anchor selection model revision/role identityがplanと一致しません。",
            )
    return digest, {
        (group["model"], group["scenario"], group["character"]): group[
            "role_epoch_sha256"
        ]
        for group in selection["groups"]
    }


def increment_generation_binding(
    *,
    plan: IncrementPlan,
    scenarios_dir: Path,
    voices_dir: Path,
    anchor_selection_path: Path,
) -> tuple[str, dict[tuple[str, str], str]]:
    """1 runぶんのanchor digestと行ごとrole epochを確定する。

    `completion_listen.phase_b_generation_binding` の増分版。role epochの導出は
    `expected_phase_b_role_epochs` をそのまま使う (増分modelはanchor selection
    由来のepochを持ち、explicit reference の5 roleだけがfallback式になる)。
    """

    from gaya_pipeline.completion_listen import (
        _load_completion_scenario_authority,
        expected_phase_b_role_epochs,
    )

    anchor_sha, anchor_epochs = load_machine_anchor_selection(
        anchor_selection_path,
        plan=plan,
    )
    scenario_authority = _load_completion_scenario_authority(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        plan=plan,
    )
    expected = expected_phase_b_role_epochs(
        plan=plan,
        line_characters=scenario_authority.line_characters,
        anchor_selection_sha256=anchor_sha,
        selected_anchor_epochs=anchor_epochs,
    )
    model_targets = {target.identity for target in plan.targets_for_model(plan.model)}
    if not model_targets:
        raise IncrementAnchorError(f"Phase B生成対象外modelです: {plan.model}")
    return anchor_sha, {
        (identity[1], identity[2]): expected[identity]
        for identity in sorted(model_targets)
    }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _create_generator(model_id: str) -> Any:
    from gaya_pipeline import adapters

    adapter = adapters.create_adapter(model_id)
    for name in (
        "role_anchor_generation_input",
        "generate_role_anchor",
        "close_role_anchor_generation",
    ):
        if not callable(getattr(adapter, name, None)):
            raise IncrementAnchorError(
                f"adapterがanchor生成契約を満たしません: {model_id}.{name}",
            )
    return adapter


def _generation_input(
    *,
    generator: Any,
    plan_sha256: str,
    target: Mapping[str, Any],
    role: RoleSnapshot,
    model_revision: str,
    attempt: int,
    seed: int,
    anchor_text: str,
    anchor_text_sha256: str,
) -> dict[str, Any]:
    conditioning = dict(generator.role_anchor_generation_input(role))
    canonical_json(conditioning)
    if "emotion" in _recursive_keys(conditioning) or "intensity" in _recursive_keys(
        conditioning,
    ):
        raise IncrementAnchorError(
            "anchor conditioningにemotion/intensityを含められません。",
        )
    return {
        "protocol": INPUT_PROTOCOL,
        "plan_sha256": plan_sha256,
        "model": target["model"],
        "model_revision": model_revision,
        "scenario": target["scenario"],
        "character": target["character"],
        "role_identity_sha256": target["role_identity_sha256"],
        "role_epoch_sha256": target["role_epoch_sha256"],
        "attempt": attempt,
        "seed": seed,
        "anchor_text": anchor_text,
        "anchor_text_sha256": anchor_text_sha256,
        "conditioning": conditioning,
    }


def _load_run_ledger(
    *,
    artifacts_dir: Path,
    run_id: str,
    plan_sha256: str,
    model: str,
    model_revision: str,
) -> tuple[dict[str, Any], str]:
    path = artifacts_dir / "role-anchors" / "runs" / run_id / "ledger.json"
    raw = _read_bytes(path, "anchor run ledger")
    ledger = _decode_json(raw, path)
    if not isinstance(ledger, dict) or ledger.get("protocol") != RUN_PROTOCOL:
        raise IncrementAnchorError(f"anchor run ledger protocolが不正です: {path}")
    digest = hashlib.sha256(raw).hexdigest()
    marker = path.with_suffix(".sha256")
    try:
        marker_value = marker.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise IncrementAnchorError(
            f"anchor run ledger SHA markerを読めません: {marker}",
        ) from error
    if marker_value != digest:
        raise IncrementAnchorError(f"anchor run ledger SHA markerが不一致です: {path}")
    if (
        ledger.get("run_id") != run_id
        or ledger.get("plan_sha256") != plan_sha256
        or ledger.get("model") != model
        or ledger.get("model_revision") != model_revision
        or ledger.get("seed_policy") != SEED_POLICY
        or ledger.get("seed_base") != SEED_BASE
    ):
        raise IncrementAnchorError(f"anchor run ledger bindingが不一致です: {path}")
    return ledger, digest


def _resolve_run_audio(artifacts_dir: Path, record: Mapping[str, Any]) -> Path:
    relative = PurePosixPath(str(record["audio_path"]))
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[:2] != ("role-anchors", "runs")
        or relative.suffix != ".wav"
    ):
        raise IncrementAnchorError(f"anchor audio pathが不正です: {relative}")
    return artifacts_dir.joinpath(*relative.parts)


def _role_snapshot(document: Mapping[str, Any]) -> RoleSnapshot:
    return RoleSnapshot(
        scenario=str(document["scenario"]),
        character=str(document["character"]),
        role=dict(document["role"]),
        reference_voice=document["reference_voice"],
        scene_setting=str(document["scene_setting"]),
        role_identity_sha256=str(document["role_identity_sha256"]),
    )


def _role_identity_document(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "scenario": str(document["scenario"]),
        "character": str(document["character"]),
        "role": dict(document["role"]),
        "reference_voice": document["reference_voice"],
        "scene_setting": str(document["scene_setting"]),
    }


def _recursive_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            keys.add(str(key))
            keys |= _recursive_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            keys |= _recursive_keys(item)
    return keys


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file_sha256(path: Path, expected: Any) -> None:
    actual = _sha256_file(path)
    if actual != expected:
        raise IncrementAnchorError(f"audio SHA-256が不一致です: {path}")


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as error:
        raise IncrementAnchorError(f"既存fileを上書きしません: {path}") from error


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise IncrementAnchorError(f"{label}を読めません: {path}") from error


def _decode_json(raw: bytes, path: Path) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IncrementAnchorError(f"JSONが不正です: {path}: {error}") from error


def _exact(value: Any, fields: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise IncrementAnchorError(f"{field} のfield集合が不正です。")
    return dict(value)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise IncrementAnchorError(f"{field} は空でない文字列が必要です。")
    return value


def _path_segment(value: Any, field: str) -> str:
    text = _text(value, field)
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise IncrementAnchorError(f"{field} はpath segmentが必要です。")
    return text


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise IncrementAnchorError(f"{field} はlowercase SHA-256が必要です。")
    return value


def _require_absolute(path: Path, label: str) -> None:
    if not isinstance(path, Path) or not path.is_absolute():
        raise IncrementAnchorError(f"{label} は絶対pathが必要です: {path}")


__all__ = [
    "ANCHOR_ROLE_COUNT",
    "CANDIDATES_PER_ROLE",
    "DECISION_PROTOCOL",
    "MAX_TOPUP_ROUNDS",
    "MINIMUM_ELIGIBLE_CANDIDATES",
    "PLAN_PROTOCOL",
    "SEED_BASE",
    "SEED_POLICY",
    "SELECTION_AUTHORITY_TYPE",
    "SELECTION_POLICY",
    "SELECTION_PROTOCOL",
    "SOFT_SIGNAL_EXHAUSTED",
    "AnchorBootstrapGenerationSummary",
    "AnchorBootstrapPlanSummary",
    "AnchorMachineSelectionSummary",
    "IncrementAnchorError",
    "anchor_role_epoch_sha256",
    "anchor_round_targets",
    "build_anchor_bootstrap_plan_document",
    "derive_anchor_seed",
    "gender_screening",
    "increment_generation_binding",
    "load_anchor_bootstrap_plan",
    "load_machine_anchor_selection",
    "measure_median_f0_hz",
    "rank_anchor_candidates",
    "resolve_increment_anchor",
    "round_attempts",
    "run_anchor_bootstrap_generation",
    "screening_distance_hz",
    "select_role_anchors",
    "validate_anchor_bootstrap_plan",
    "validate_machine_anchor_selection",
    "write_anchor_bootstrap_plan",
]
