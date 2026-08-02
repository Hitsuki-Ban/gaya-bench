from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping

from gaya_pipeline.curation import (
    GROUP_KEYS,
    RUBRIC_VERSION as LEGACY_RUBRIC_VERSION,
    CurationError,
    _exact,
    _path_segment,
    _sha,
    _text,
    _validate_decision,
    _validate_rubric as _validate_legacy_rubric,
    validate_curation,
)
from gaya_pipeline.selection import (
    AUTOMATIC_GATE_POLICY,
    AUTOMATIC_SELECTION_POLICY,
    canonical_selection_bytes as canonical_legacy_selection_bytes,
    validate_selection as validate_legacy_selection,
)
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.take_manifest_v4 import TakeManifestError, validate_manifest_v4


FORMAT_VERSION = 4
PROTOCOL = "role-baseline-selection-v1"
DECISION_FORMAT_VERSION = 1
DECISION_PROTOCOL = "role-baseline-decision-v1"
AUTO_SELECTION_POLICY = "phase-b-auto-selection-v1"
GENDER_SCREENING_PROTOCOL = "role-gender-f0-soft-v1"
FEMALE_F0_LOWER_BOUND_HZ = 165.0
MALE_F0_UPPER_BOUND_HZ = 180.0
BASE_CANDIDATE_SET_SHA256 = (
    "91913e08f97497f1f7604f109a6d0f7308742237277f6bbc5483678ac9858cc2"
)
BASE_SELECTION_SHA256 = (
    "629cc80346160eb8e687757e6f792ef519da9a4fb74f79bdf97eb4d00f56126e"
)
QWEN_MODEL = "qwen3-tts-12hz-1.7b"

ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_sha256",
    "anchor_selection_sha256",
    "candidate_set_sha256",
    "ranking_report_sha256",
    "groups",
}
SELECTION_GROUP_FIELDS = {
    *GROUP_KEYS,
    "role_epoch_sha256",
    "authority",
    "candidates",
    "decision",
}
DECISION_GROUP_FIELDS = {
    *SELECTION_GROUP_FIELDS,
    "group_sha256",
    "screening",
}
HUMAN_AUTHORITY_FIELDS = {"type", "rubric_version"}
AUTOMATIC_AUTHORITY_FIELDS = {
    "type",
    "selection_policy_version",
    "gate_policy_version",
}
AUTO_SELECTED_AUTHORITY_FIELDS = {
    "type",
    "policy_version",
    "minimum_eligible_candidates",
    "gate_policy_version",
}
LEGACY_HUMAN_CANDIDATE_FIELDS = {"take_id", "path", "audio_sha256", "rubric"}
AUTOMATIC_CANDIDATE_FIELDS = {"take_id", "path", "audio_sha256", "gate"}
AUTO_SELECTED_CANDIDATE_FIELDS = {
    "take_id",
    "path",
    "audio_sha256",
    "gate",
    "rank",
    "pasqa_score",
    "duration_sec",
}
GATE_FIELDS = {"mechanical", "content", "policy_version"}
CONTENT_VALUES = {"pass", "review_required"}
SCREENING_FIELDS = {
    "protocol",
    "expected_gender",
    "median_f0_hz",
    "status",
    "signal",
    "qc_report_sha256",
}


def validate_completion_selection(document: Any) -> dict[str, Any]:
    """Validate and canonicalize the complete role-baseline selection."""

    return _validate_document(
        document,
        format_version=FORMAT_VERSION,
        protocol=PROTOCOL,
        auto_selected_only=False,
        label="completion selection",
        bind_group_sha256=False,
    )


def canonical_completion_selection_bytes(document: Any) -> bytes:
    return canonical_json(validate_completion_selection(document)).encode("utf-8")


def validate_completion_decision(document: Any) -> dict[str, Any]:
    """Validate the exact machine-selected Phase B decision."""

    return _validate_document(
        document,
        format_version=DECISION_FORMAT_VERSION,
        protocol=DECISION_PROTOCOL,
        auto_selected_only=True,
        label="completion decision",
        bind_group_sha256=True,
    )


def canonical_completion_decision_bytes(document: Any) -> bytes:
    return canonical_json(validate_completion_decision(document)).encode("utf-8")


def reconstruct_base_selection(
    *,
    base_manifest: Any,
    qwen_curation: Any,
) -> dict[str, Any]:
    """Rebuild the published format-v2 aggregate selection and verify its SHA."""

    try:
        manifest = validate_manifest_v4(base_manifest)
    except TakeManifestError as error:
        raise CurationError(f"base manifest が不正です: {error}") from error
    if manifest["candidate_set_sha256"] != BASE_CANDIDATE_SET_SHA256:
        raise CurationError("base manifest candidate-set SHA が公開基準と一致しません。")

    curation = validate_curation(qwen_curation)
    candidates_by_group = {
        _group_key(candidate): candidate for candidate in manifest["candidates"]
    }
    if len(candidates_by_group) != len(manifest["candidates"]):
        raise CurationError("base manifest は group ごとに candidate 1件が必要です。")
    projections_by_group = {
        _group_key(item): item for item in manifest["curations"]
    }
    if len(projections_by_group) != len(manifest["curations"]):
        raise CurationError("base manifest curation group が重複しています。")
    qwen_groups = {
        _group_key(group): group
        for group in curation["groups"]
        if group["model"] == QWEN_MODEL
    }
    expected_qwen_groups = {
        identity
        for identity in candidates_by_group
        if identity[0] == QWEN_MODEL
    }
    if set(qwen_groups) != expected_qwen_groups:
        raise CurationError(
            "Qwen curation が base manifest の Qwen candidate groups と exact に"
            "一致しません。",
        )

    groups: list[dict[str, Any]] = []
    for identity in sorted(candidates_by_group):
        candidate = candidates_by_group[identity]
        projection = projections_by_group.get(identity)
        if projection is None:
            raise CurationError("base manifest candidate に curation がありません。")
        if identity[0] == QWEN_MODEL:
            group = qwen_groups[identity]
            _verify_human_group_against_candidate(group, candidate)
            expected_projection = _projection_from_decision(group["decision"])
            if projection["decision"] != expected_projection["decision"]:
                raise CurationError("Qwen curation decision が base manifest と不一致です。")
            if projection.get("take_id") != expected_projection.get("take_id"):
                raise CurationError("Qwen curation take_id が base manifest と不一致です。")
            groups.append(
                {
                    **{key: group[key] for key in GROUP_KEYS},
                    "authority": {
                        "type": "human",
                        "rubric_version": LEGACY_RUBRIC_VERSION,
                    },
                    "candidates": [dict(item) for item in group["candidates"]],
                    "decision": dict(group["decision"]),
                },
            )
            continue

        if projection["decision"] != "selected":
            raise CurationError("非Qwen base candidate は selected が必要です。")
        if projection["take_id"] != candidate["take_id"]:
            raise CurationError("非Qwen base selected take が candidate と不一致です。")
        groups.append(
            {
                **{key: candidate[key] for key in GROUP_KEYS},
                "authority": {
                    "type": "automatic_gate",
                    "selection_policy_version": AUTOMATIC_SELECTION_POLICY,
                    "gate_policy_version": AUTOMATIC_GATE_POLICY,
                },
                "candidates": [
                    {
                        "take_id": candidate["take_id"],
                        "path": candidate["path"],
                        "audio_sha256": candidate["sha256"],
                        "gate": dict(candidate["gate"]),
                    },
                ],
                "decision": {
                    "type": "selected",
                    "take_id": candidate["take_id"],
                },
            },
        )

    legacy = validate_legacy_selection(
        {
            "format_version": 2,
            "protocol": "take-selection-v1",
            "candidate_set_sha256": BASE_CANDIDATE_SET_SHA256,
            "groups": groups,
        },
    )
    actual_sha256 = hashlib.sha256(
        canonical_legacy_selection_bytes(legacy),
    ).hexdigest()
    if actual_sha256 != BASE_SELECTION_SHA256:
        raise CurationError(
            "再構築した公開 selection SHA が固定基準と一致しません: "
            f"{actual_sha256}",
        )
    if any(
        item["curation_sha256"] != BASE_SELECTION_SHA256
        for item in manifest["curations"]
    ):
        raise CurationError("base manifest curation SHA が公開 selection と不一致です。")
    return legacy


def _validate_document(
    document: Any,
    *,
    format_version: int,
    protocol: str,
    auto_selected_only: bool,
    label: str,
    bind_group_sha256: bool,
) -> dict[str, Any]:
    root = _exact(document, ROOT_FIELDS, label)
    if root["format_version"] != format_version:
        raise CurationError(f"{label}.format_version は {format_version} が必要です。")
    if root["protocol"] != protocol:
        raise CurationError(f"{label}.protocol は {protocol} が必要です。")
    candidate_set_sha256 = _sha(
        root["candidate_set_sha256"],
        f"{label}.candidate_set_sha256",
    )
    ranking_report_sha256 = _sha(
        root["ranking_report_sha256"],
        f"{label}.ranking_report_sha256",
    )
    plan_sha256 = _sha(root["plan_sha256"], f"{label}.plan_sha256")
    anchor_selection_sha256 = _sha(
        root["anchor_selection_sha256"],
        f"{label}.anchor_selection_sha256",
    )
    if not isinstance(root["groups"], list) or not root["groups"]:
        raise CurationError(f"{label}.groups は1件以上の配列が必要です。")

    normalized_groups: list[dict[str, Any]] = []
    seen_groups: set[tuple[str, str, str, str]] = set()
    seen_take_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, value in enumerate(root["groups"]):
        field = f"{label}.groups[{index}]"
        group = _exact(
            value,
            DECISION_GROUP_FIELDS
            if bind_group_sha256
            else SELECTION_GROUP_FIELDS,
            field,
        )
        identity = tuple(
            _path_segment(group[key], f"{field}.{key}") for key in GROUP_KEYS
        )
        role_epoch_sha256 = _sha(
            group["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        )
        group_sha256 = (
            _sha(group["group_sha256"], f"{field}.group_sha256")
            if bind_group_sha256
            else None
        )
        if identity in seen_groups:
            raise CurationError(f"{label} group が重複しています。")
        seen_groups.add(identity)
        authority = _validate_authority(group["authority"], f"{field}.authority")
        if auto_selected_only and authority["type"] != "auto-selected":
            raise CurationError(f"{label} は auto-selected authority のみ許可します。")
        candidates = _validate_candidates(
            group["candidates"],
            authority=authority,
            field=f"{field}.candidates",
            seen_take_ids=seen_take_ids,
            seen_paths=seen_paths,
        )
        decision = _validate_decision(group["decision"], f"{field}.decision")
        candidates_by_take = {
            candidate["take_id"]: candidate for candidate in candidates
        }
        if decision["type"] == "selected":
            selected = candidates_by_take.get(decision["take_id"])
            if selected is None:
                raise CurationError(
                    f"{field}.decision.take_id が同一 group にありません。",
                )
            if authority["type"] == "human":
                rubric = selected["rubric"]
                if not rubric["adoptable"] or not rubric["content_correct"]:
                    raise CurationError(
                        "人評selected candidateはadoptable/content_correctが必要です。",
                    )
        if authority["type"] == "automatic_gate":
            if len(candidates) != 1:
                raise CurationError("自動選定 group は candidate が1件必要です。")
            if decision != {
                "type": "selected",
                "take_id": candidates[0]["take_id"],
            }:
                raise CurationError("自動選定 group は唯一のcandidateを選ぶ必要があります。")
        if authority["type"] == "auto-selected":
            minimum = authority["minimum_eligible_candidates"]
            if len(candidates) < minimum:
                raise CurationError(
                    f"auto-selected group は mechanical-pass candidate が"
                    f" {minimum}件以上必要です。",
                )
            if decision["type"] != "selected":
                raise CurationError("auto-selected group は1件の selected が必要です。")
            expected_ranks = list(range(1, len(candidates) + 1))
            if sorted(candidate["rank"] for candidate in candidates) != expected_ranks:
                raise CurationError("auto-selected candidate rank は1..Nが必要です。")
            selected = candidates_by_take[decision["take_id"]]
            if selected["rank"] != 1:
                raise CurationError("auto-selected group はrank 1を選ぶ必要があります。")

        normalized_group = {
            **dict(zip(GROUP_KEYS, identity, strict=True)),
            "role_epoch_sha256": role_epoch_sha256,
            "authority": authority,
            "candidates": sorted(candidates, key=lambda item: item["take_id"]),
            "decision": decision,
        }
        if group_sha256 is not None:
            normalized_group["group_sha256"] = group_sha256
            normalized_group["screening"] = _validate_screening(
                group["screening"],
                f"{field}.screening",
            )
        normalized_groups.append(normalized_group)

    normalized_groups.sort(key=lambda item: _group_key(item))
    return {
        "format_version": format_version,
        "protocol": protocol,
        "plan_sha256": plan_sha256,
        "anchor_selection_sha256": anchor_selection_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "ranking_report_sha256": ranking_report_sha256,
        "groups": normalized_groups,
    }


def _validate_authority(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or "type" not in value:
        raise CurationError(f"{field} は authority object が必要です。")
    authority_type = value["type"]
    if authority_type == "human":
        authority = _exact(value, HUMAN_AUTHORITY_FIELDS, field)
        if authority["rubric_version"] != LEGACY_RUBRIC_VERSION:
            raise CurationError(f"{field}.rubric_version が不正です。")
        return {"type": "human", "rubric_version": LEGACY_RUBRIC_VERSION}
    if authority_type == "automatic_gate":
        authority = _exact(value, AUTOMATIC_AUTHORITY_FIELDS, field)
        if authority != {
            "type": "automatic_gate",
            "selection_policy_version": AUTOMATIC_SELECTION_POLICY,
            "gate_policy_version": AUTOMATIC_GATE_POLICY,
        }:
            raise CurationError(f"{field} の automatic_gate 契約が不正です。")
        return dict(authority)
    if authority_type == "auto-selected":
        authority = _exact(value, AUTO_SELECTED_AUTHORITY_FIELDS, field)
        expected = {
            "type": "auto-selected",
            "policy_version": AUTO_SELECTION_POLICY,
            "gate_policy_version": AUTOMATIC_GATE_POLICY,
        }
        minimum = authority["minimum_eligible_candidates"]
        if (
            {key: authority[key] for key in expected} != expected
            or not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or minimum < 1
        ):
            raise CurationError(f"{field} の auto-selected 契約が不正です。")
        return {**expected, "minimum_eligible_candidates": minimum}
    raise CurationError(f"{field}.type が不正です。")


def _validate_candidates(
    value: Any,
    *,
    authority: Mapping[str, Any],
    field: str,
    seen_take_ids: set[str],
    seen_paths: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise CurationError(f"{field} は1件以上の配列が必要です。")
    result: list[dict[str, Any]] = []
    expected_fields = {
        "human": LEGACY_HUMAN_CANDIDATE_FIELDS,
        "automatic_gate": AUTOMATIC_CANDIDATE_FIELDS,
        "auto-selected": AUTO_SELECTED_CANDIDATE_FIELDS,
    }[str(authority["type"])]
    for index, value_candidate in enumerate(value):
        candidate_field = f"{field}[{index}]"
        candidate = _exact(value_candidate, expected_fields, candidate_field)
        take_id = _sha(candidate["take_id"], f"{candidate_field}.take_id")
        path = _text(candidate["path"], f"{candidate_field}.path")
        audio_sha256 = _sha(
            candidate["audio_sha256"],
            f"{candidate_field}.audio_sha256",
        )
        if take_id in seen_take_ids:
            raise CurationError("selection candidate take_id が重複しています。")
        if path in seen_paths:
            raise CurationError("selection candidate path が重複しています。")
        seen_take_ids.add(take_id)
        seen_paths.add(path)
        normalized: dict[str, Any] = {
            "take_id": take_id,
            "path": path,
            "audio_sha256": audio_sha256,
        }
        if authority["type"] == "human":
            normalized["rubric"] = _validate_legacy_rubric(
                candidate["rubric"],
                f"{candidate_field}.rubric",
            )
        else:
            normalized["gate"] = _validate_gate(
                candidate["gate"],
                f"{candidate_field}.gate",
            )
        if authority["type"] == "auto-selected":
            rank = candidate["rank"]
            if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
                raise CurationError(f"{candidate_field}.rank は正の整数が必要です。")
            score = candidate["pasqa_score"]
            if score is not None and (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
            ):
                raise CurationError(
                    f"{candidate_field}.pasqa_score は有限数またはnullが必要です。"
                )
            duration = candidate["duration_sec"]
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(float(duration))
                or float(duration) <= 0
            ):
                raise CurationError(
                    f"{candidate_field}.duration_sec は正の有限数が必要です。"
                )
            normalized.update(
                {
                    "rank": rank,
                    "pasqa_score": float(score) if score is not None else None,
                    "duration_sec": float(duration),
                }
            )
        result.append(normalized)
    return result


def _validate_gate(value: Any, field: str) -> dict[str, str]:
    gate = _exact(value, GATE_FIELDS, field)
    if gate["mechanical"] != "pass":
        raise CurationError(f"{field}.mechanical は pass が必要です。")
    if gate["content"] not in CONTENT_VALUES:
        raise CurationError(f"{field}.content が不正です。")
    if gate["policy_version"] != AUTOMATIC_GATE_POLICY:
        raise CurationError(
            f"{field}.policy_version は {AUTOMATIC_GATE_POLICY} が必要です。",
        )
    return {
        "mechanical": "pass",
        "content": str(gate["content"]),
        "policy_version": AUTOMATIC_GATE_POLICY,
    }


def _validate_screening(value: Any, field: str) -> dict[str, Any]:
    screening = _exact(value, SCREENING_FIELDS, field)
    if screening["protocol"] != GENDER_SCREENING_PROTOCOL:
        raise CurationError(f"{field}.protocol が不正です。")
    gender = screening["expected_gender"]
    if gender not in {"female", "male", "neutral"}:
        raise CurationError(f"{field}.expected_gender が不正です。")
    median = screening["median_f0_hz"]
    if median is not None and (
        isinstance(median, bool)
        or not isinstance(median, (int, float))
        or not math.isfinite(float(median))
        or float(median) <= 0
    ):
        raise CurationError(f"{field}.median_f0_hz が不正です。")
    status = screening["status"]
    signal = screening["signal"]
    expected_status: str
    expected_signal: str | None
    if gender == "neutral":
        expected_status = "not_applicable"
        expected_signal = None
    elif median is None:
        expected_status = "review_required"
        expected_signal = "gender_f0_unavailable"
    elif gender == "female" and float(median) < FEMALE_F0_LOWER_BOUND_HZ:
        expected_status = "review_required"
        expected_signal = "gender_f0_below_expected"
    elif gender == "male" and float(median) > MALE_F0_UPPER_BOUND_HZ:
        expected_status = "review_required"
        expected_signal = "gender_f0_above_expected"
    else:
        expected_status = "pass"
        expected_signal = None
    if status != expected_status or signal != expected_signal:
        raise CurationError(f"{field} のF0判定結果がpolicyと一致しません。")
    return {
        "protocol": GENDER_SCREENING_PROTOCOL,
        "expected_gender": gender,
        "median_f0_hz": float(median) if median is not None else None,
        "status": expected_status,
        "signal": expected_signal,
        "qc_report_sha256": _sha(
            screening["qc_report_sha256"],
            f"{field}.qc_report_sha256",
        ),
    }


def _verify_human_group_against_candidate(
    group: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> None:
    if len(group["candidates"]) != 1:
        raise CurationError("公開Qwen curation は group ごとに candidate 1件が必要です。")
    member = group["candidates"][0]
    if (
        member["take_id"] != candidate["take_id"]
        or member["path"] != candidate["path"]
        or member["audio_sha256"] != candidate["sha256"]
    ):
        raise CurationError("Qwen curation candidate が base manifest と不一致です。")


def _projection_from_decision(decision: Mapping[str, Any]) -> dict[str, Any]:
    if decision["type"] == "selected":
        return {"decision": "selected", "take_id": decision["take_id"]}
    return {"decision": "skipped"}


def _group_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(value[key]) for key in GROUP_KEYS)  # type: ignore[return-value]
