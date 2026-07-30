from __future__ import annotations

from typing import Any, Mapping

from gaya_pipeline.curation import (
    GROUP_KEYS,
    RUBRIC_VERSION,
    CurationError,
    _exact,
    _path_segment,
    _sha,
    _text,
    _validate_decision,
    _validate_rubric,
)
from gaya_pipeline.take_identity import canonical_json


FORMAT_VERSION = 2
PROTOCOL = "take-selection-v1"
AUTOMATIC_SELECTION_POLICY = "automatic-gate-v1"
AUTOMATIC_GATE_POLICY = "take-gates-v2"
ROOT_FIELDS = {
    "format_version",
    "protocol",
    "candidate_set_sha256",
    "groups",
}
GROUP_FIELDS = {*GROUP_KEYS, "authority", "candidates", "decision"}
HUMAN_AUTHORITY_FIELDS = {"type", "rubric_version"}
AUTOMATIC_AUTHORITY_FIELDS = {
    "type",
    "selection_policy_version",
    "gate_policy_version",
}
HUMAN_CANDIDATE_FIELDS = {"take_id", "path", "audio_sha256", "rubric"}
AUTOMATIC_CANDIDATE_FIELDS = {"take_id", "path", "audio_sha256", "gate"}
GATE_FIELDS = {"mechanical", "content", "policy_version"}
AUTOMATIC_CONTENT_VALUES = {"pass", "review_required"}


def validate_selection(document: Any) -> dict[str, Any]:
    root = _exact(document, ROOT_FIELDS, "selection")
    if root["format_version"] != FORMAT_VERSION:
        raise CurationError("selection.format_version は 2 が必要です。")
    if root["protocol"] != PROTOCOL:
        raise CurationError("selection.protocol は take-selection-v1 が必要です。")
    candidate_set_sha256 = _sha(
        root["candidate_set_sha256"],
        "selection.candidate_set_sha256",
    )
    groups_value = root["groups"]
    if not isinstance(groups_value, list) or not groups_value:
        raise CurationError("selection.groups は 1 件以上の配列が必要です。")

    groups: list[dict[str, Any]] = []
    seen_groups: set[tuple[str, str, str, str]] = set()
    seen_candidates: set[str] = set()
    for index, value in enumerate(groups_value):
        field = f"selection.groups[{index}]"
        group = _exact(value, GROUP_FIELDS, field)
        identity = tuple(
            _path_segment(group[key], f"{field}.{key}") for key in GROUP_KEYS
        )
        if identity in seen_groups:
            raise CurationError("selection group が重複しています。")
        seen_groups.add(identity)

        authority = _validate_authority(group["authority"], f"{field}.authority")
        candidates = _validate_candidates(
            group["candidates"],
            authority=authority,
            field=f"{field}.candidates",
            seen_candidates=seen_candidates,
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
                raise CurationError(
                    "自動選定 group は唯一のcandidateをselectedにする必要があります。",
                )

        groups.append(
            {
                **dict(zip(GROUP_KEYS, identity, strict=True)),
                "authority": authority,
                "candidates": sorted(
                    candidates,
                    key=lambda candidate: candidate["take_id"],
                ),
                "decision": decision,
            },
        )

    groups.sort(key=lambda group: tuple(group[key] for key in GROUP_KEYS))
    return {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "candidate_set_sha256": candidate_set_sha256,
        "groups": groups,
    }


def canonical_selection_bytes(document: Any) -> bytes:
    return canonical_json(validate_selection(document)).encode("utf-8")


def human_selection_group(group: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **{key: group[key] for key in GROUP_KEYS},
        "authority": {
            "type": "human",
            "rubric_version": RUBRIC_VERSION,
        },
        "candidates": [
            {
                "take_id": candidate["take_id"],
                "path": candidate["path"],
                "audio_sha256": candidate["audio_sha256"],
                "rubric": dict(candidate["rubric"]),
            }
            for candidate in group["candidates"]
        ],
        "decision": dict(group["decision"]),
    }


def automatic_selection_group(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
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
    }


def selection_group_to_human_curation(
    group: Mapping[str, Any],
) -> dict[str, Any]:
    authority = group.get("authority")
    if authority != {
        "type": "human",
        "rubric_version": RUBRIC_VERSION,
    }:
        raise CurationError("selection group は人評authorityではありません。")
    return {
        **{key: group[key] for key in GROUP_KEYS},
        "candidates": [
            {
                "take_id": candidate["take_id"],
                "path": candidate["path"],
                "audio_sha256": candidate["audio_sha256"],
                "rubric": dict(candidate["rubric"]),
            }
            for candidate in group["candidates"]
        ],
        "decision": dict(group["decision"]),
    }


def _validate_authority(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or "type" not in value:
        raise CurationError(f"{field} は authority object が必要です。")
    authority_type = value["type"]
    if authority_type == "human":
        authority = _exact(value, HUMAN_AUTHORITY_FIELDS, field)
        if authority["rubric_version"] != RUBRIC_VERSION:
            raise CurationError(
                f"{field}.rubric_version は {RUBRIC_VERSION} が必要です。",
            )
        return {
            "type": "human",
            "rubric_version": RUBRIC_VERSION,
        }
    if authority_type == "automatic_gate":
        authority = _exact(value, AUTOMATIC_AUTHORITY_FIELDS, field)
        if authority["selection_policy_version"] != AUTOMATIC_SELECTION_POLICY:
            raise CurationError(
                f"{field}.selection_policy_version は"
                f" {AUTOMATIC_SELECTION_POLICY} が必要です。",
            )
        if authority["gate_policy_version"] != AUTOMATIC_GATE_POLICY:
            raise CurationError(
                f"{field}.gate_policy_version は"
                f" {AUTOMATIC_GATE_POLICY} が必要です。",
            )
        return {
            "type": "automatic_gate",
            "selection_policy_version": AUTOMATIC_SELECTION_POLICY,
            "gate_policy_version": AUTOMATIC_GATE_POLICY,
        }
    raise CurationError(f"{field}.type が不正です。")


def _validate_candidates(
    value: Any,
    *,
    authority: Mapping[str, str],
    field: str,
    seen_candidates: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise CurationError(f"{field} は 1 件以上の配列が必要です。")
    candidates: list[dict[str, Any]] = []
    for index, candidate_value in enumerate(value):
        candidate_field = f"{field}[{index}]"
        expected = (
            HUMAN_CANDIDATE_FIELDS
            if authority["type"] == "human"
            else AUTOMATIC_CANDIDATE_FIELDS
        )
        candidate = _exact(candidate_value, expected, candidate_field)
        take_id = _sha(candidate["take_id"], f"{candidate_field}.take_id")
        if take_id in seen_candidates:
            raise CurationError("selection candidate が重複しています。")
        seen_candidates.add(take_id)
        normalized = {
            "take_id": take_id,
            "path": _text(candidate["path"], f"{candidate_field}.path"),
            "audio_sha256": _sha(
                candidate["audio_sha256"],
                f"{candidate_field}.audio_sha256",
            ),
        }
        if authority["type"] == "human":
            normalized["rubric"] = _validate_rubric(
                candidate["rubric"],
                f"{candidate_field}.rubric",
            )
        else:
            normalized["gate"] = _validate_gate(
                candidate["gate"],
                f"{candidate_field}.gate",
            )
        candidates.append(normalized)
    return candidates


def _validate_gate(value: Any, field: str) -> dict[str, str]:
    gate = _exact(value, GATE_FIELDS, field)
    if gate["mechanical"] != "pass":
        raise CurationError(f"{field}.mechanical は pass が必要です。")
    if gate["content"] not in AUTOMATIC_CONTENT_VALUES:
        raise CurationError(
            f"{field}.content は pass または review_required が必要です。",
        )
    if gate["policy_version"] != AUTOMATIC_GATE_POLICY:
        raise CurationError(
            f"{field}.policy_version は {AUTOMATIC_GATE_POLICY} が必要です。",
        )
    return {
        "mechanical": "pass",
        "content": gate["content"],
        "policy_version": AUTOMATIC_GATE_POLICY,
    }
