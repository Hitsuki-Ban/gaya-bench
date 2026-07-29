from __future__ import annotations

import math
from typing import Any

from gaya_pipeline.take_identity import (
    TakeIdentityError,
    canonical_json,
    make_take_id,
)
from gaya_pipeline.take_ledger import TakeLedgerError, validate_attempt


class TakeManifestError(ValueError):
    pass


ROOT_KEYS = {
    "format_version",
    "generated_at",
    "candidate_set_sha256",
    "models",
    "candidates",
    "curations",
    "failures",
}
MODEL_KEYS = {"id", "name", "version", "license_note", "capabilities"}
CAPABILITY_KEYS = {"emotion", "voice_prompt", "clone", "nonverbal", "reading"}
CANDIDATE_KEYS = {
    "model",
    "scenario",
    "line",
    "variant",
    "take_index",
    "take_id",
    "path",
    "duration_sec",
    "sha256",
    "generation_input_sha256",
    "gen_params",
    "rtf",
    "loudness",
    "gate",
}
GROUP_KEYS = ("model", "scenario", "line", "variant")
FAILURE_KEYS = {*GROUP_KEYS, "reason"}
FAILURE_REASONS = {"no_eligible_take", "test_only_adapter"}
HEX = frozenset("0123456789abcdef")


def _exact(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise TakeManifestError(f"{field} の項目が v4 契約と一致しません。")
    return value


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise TakeManifestError(f"{field} は文字列が必要です。")
    return value


def _path_segment(value: Any, field: str) -> str:
    text = _text(value, field)
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise TakeManifestError(f"{field} は安全な path segment が必要です。")
    return text


def _sha(value: Any, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(character not in HEX for character in text):
        raise TakeManifestError(f"{field} は完全な小文字 SHA-256 が必要です。")
    return text


def _number(value: Any, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value < 0
    ):
        raise TakeManifestError(f"{field} は有限の非負数が必要です。")
    return float(value)


def _group(value: dict[str, Any], field: str) -> tuple[str, str, str, str]:
    return tuple(
        _path_segment(value[key], f"{field}.{key}") for key in GROUP_KEYS
    )  # type: ignore[return-value]


def _validate_model(value: Any, field: str) -> str:
    model = _exact(value, MODEL_KEYS, field)
    capabilities = _exact(
        model["capabilities"],
        CAPABILITY_KEYS,
        f"{field}.capabilities",
    )
    if not all(isinstance(value, bool) for value in capabilities.values()):
        raise TakeManifestError(f"{field}.capabilities は bool が必要です。")
    _path_segment(model["id"], f"{field}.id")
    for key in ("name", "version", "license_note"):
        _text(model[key], f"{field}.{key}", allow_empty=key == "license_note")
    return model["id"]


def _validate_candidate(
    value: Any,
    field: str,
) -> tuple[tuple[str, str, str, str], int, str]:
    candidate = _exact(value, CANDIDATE_KEYS, field)
    group = _group(candidate, field)
    take_index = candidate["take_index"]
    if isinstance(take_index, bool) or not isinstance(take_index, int) or take_index < 1:
        raise TakeManifestError(f"{field}.take_index は 1 以上の整数が必要です。")
    take_id = _sha(candidate["take_id"], f"{field}.take_id")
    audio_sha = _sha(candidate["sha256"], f"{field}.sha256")
    input_sha = _sha(
        candidate["generation_input_sha256"],
        f"{field}.generation_input_sha256",
    )
    if take_id != make_take_id(
        generation_input_sha256=input_sha,
        final_opus_sha256=audio_sha,
    ):
        raise TakeManifestError(f"{field}.take_id が provenance と一致しません。")
    expected_path = (
        f"audio/takes/{group[0]}/{group[1]}/{group[2]}/{group[3]}/"
        f"take-{take_index:04d}-{audio_sha}.opus"
    )
    if candidate["path"] != expected_path:
        raise TakeManifestError(f"{field}.path が field から再構成できません。")
    _number(candidate["duration_sec"], f"{field}.duration_sec")
    _number(candidate["rtf"], f"{field}.rtf")
    gen_params = _exact(
        candidate["gen_params"],
        {"seed", "recipe_version", "sampling", "requested", "realized"},
        f"{field}.gen_params",
    )
    seed = gen_params["seed"]
    if seed is not None and (
        isinstance(seed, bool)
        or not isinstance(seed, int)
    ):
        raise TakeManifestError(
            f"{field}.gen_params.seed は整数または null が必要です。",
        )
    _text(
        gen_params["recipe_version"],
        f"{field}.gen_params.recipe_version",
    )
    for key in ("sampling", "requested", "realized"):
        if not isinstance(gen_params[key], dict):
            raise TakeManifestError(
                f"{field}.gen_params.{key} は object が必要です。",
            )
    try:
        canonical_json(gen_params)
    except TakeIdentityError as error:
        raise TakeManifestError(f"{field}.gen_params が JSON 契約を満たしません。") from error
    loudness = _exact(
        candidate["loudness"],
        {"source", "i_lufs", "tp_dbtp", "shortfall"},
        f"{field}.loudness",
    )
    if loudness["source"] != "encoded_opus":
        raise TakeManifestError(f"{field}.loudness.source が不正です。")
    for key in ("i_lufs", "tp_dbtp"):
        number = loudness[key]
        if (
            isinstance(number, bool)
            or not isinstance(number, int | float)
            or not math.isfinite(number)
        ):
            raise TakeManifestError(f"{field}.loudness.{key} は有限数が必要です。")
    if not isinstance(loudness["shortfall"], bool):
        raise TakeManifestError(f"{field}.loudness.shortfall は bool が必要です。")
    gate = _exact(
        candidate["gate"],
        {"mechanical", "content", "policy_version"},
        f"{field}.gate",
    )
    if gate["mechanical"] != "pass" or gate["content"] not in {
        "pass",
        "review_required",
    }:
        raise TakeManifestError(f"{field}.gate は eligible 判定が必要です。")
    _text(gate["policy_version"], f"{field}.gate.policy_version")
    return group, take_index, take_id


def validate_manifest_v4(document: Any) -> dict[str, Any]:
    manifest = _exact(document, ROOT_KEYS, "manifest")
    if manifest["format_version"] != 4:
        raise TakeManifestError("manifest.format_version は 4 が必要です。")
    _text(manifest["generated_at"], "manifest.generated_at")
    _sha(manifest["candidate_set_sha256"], "manifest.candidate_set_sha256")
    for key in ("models", "candidates", "curations", "failures"):
        if not isinstance(manifest[key], list):
            raise TakeManifestError(f"manifest.{key} は配列が必要です。")

    model_ids = [
        _validate_model(model, f"manifest.models[{index}]")
        for index, model in enumerate(manifest["models"])
    ]
    if len(model_ids) != len(set(model_ids)):
        raise TakeManifestError("manifest model id が重複しています。")
    known_models = set(model_ids)

    candidate_groups: set[tuple[str, str, str, str]] = set()
    candidate_slots: set[tuple[tuple[str, str, str, str], int]] = set()
    candidates_by_id: dict[str, tuple[str, str, str, str]] = {}
    for index, candidate in enumerate(manifest["candidates"]):
        group, take_index, take_id = _validate_candidate(
            candidate,
            f"manifest.candidates[{index}]",
        )
        if group[0] not in known_models:
            raise TakeManifestError("candidate が未知の model を参照しています。")
        slot = (group, take_index)
        if slot in candidate_slots:
            raise TakeManifestError("同じ group の take_index が重複しています。")
        if take_id in candidates_by_id:
            raise TakeManifestError("take_id が manifest 全体で重複しています。")
        candidate_slots.add(slot)
        candidate_groups.add(group)
        candidates_by_id[take_id] = group

    curation_groups = set()
    for index, value in enumerate(manifest["curations"]):
        field = f"manifest.curations[{index}]"
        if not isinstance(value, dict):
            raise TakeManifestError(f"{field} は object が必要です。")
        decision = value.get("decision")
        keys = (
            {*GROUP_KEYS, "decision", "take_id", "curation_sha256"}
            if decision == "selected"
            else {*GROUP_KEYS, "decision", "curation_sha256"}
        )
        curation = _exact(value, keys, field)
        if decision not in {"selected", "skipped"}:
            raise TakeManifestError(f"{field}.decision が不正です。")
        group = _group(curation, field)
        if group in curation_groups:
            raise TakeManifestError("同じ group の curation が重複しています。")
        curation_groups.add(group)
        _sha(curation["curation_sha256"], f"{field}.curation_sha256")
        if decision == "selected":
            take_id = _sha(curation["take_id"], f"{field}.take_id")
            if candidates_by_id.get(take_id) != group:
                raise TakeManifestError("selected curation が同一 group の take を参照していません。")
        elif group not in candidate_groups:
            raise TakeManifestError("skipped curation に対応する candidate group がありません。")

    failure_groups = set()
    for index, value in enumerate(manifest["failures"]):
        field = f"manifest.failures[{index}]"
        failure = _exact(value, FAILURE_KEYS, field)
        group = _group(failure, field)
        if group[0] not in known_models:
            raise TakeManifestError("failure が未知の model を参照しています。")
        if failure["reason"] not in FAILURE_REASONS:
            raise TakeManifestError(f"{field}.reason が不正です。")
        if failure["reason"] == "test_only_adapter" and group[0] != "dummy":
            raise TakeManifestError(
                f"{field}.reason=test_only_adapter は model=dummy が必要です。",
            )
        if group in failure_groups:
            raise TakeManifestError("failure group が重複しています。")
        if group in candidate_groups:
            raise TakeManifestError("candidate と failure の group が競合しています。")
        failure_groups.add(group)

    try:
        canonical_json(manifest)
    except TakeIdentityError as error:
        raise TakeManifestError("manifest が JSON 契約を満たしません。") from error
    return manifest


def candidate_from_attempt(
    attempt: dict[str, Any],
    *,
    duration_sec: float,
    loudness: dict[str, Any],
    gate_policy_version: str,
    recipe_version: str,
    requested_params: dict[str, Any],
    realized_params: dict[str, Any],
) -> dict[str, Any]:
    validate_attempt(attempt)
    if attempt.get("status") != "eligible":
        raise TakeLedgerError("eligible attempt だけが candidate を構築できます。")
    try:
        audio = attempt["audio"]
        generation = attempt["generation"]
        path_root = "/".join(str(attempt[key]) for key in GROUP_KEYS)
        candidate = {
            **{key: attempt[key] for key in GROUP_KEYS},
            "take_index": attempt["take_index"],
            "take_id": attempt["take_id"],
            "path": (
                f"audio/takes/{path_root}/take-{attempt['take_index']:04d}-"
                f"{audio['opus_sha256']}.opus"
            ),
            "duration_sec": duration_sec,
            "sha256": audio["opus_sha256"],
            "generation_input_sha256": attempt["generation_input_sha256"],
            "gen_params": {
                "seed": generation["seed"],
                "recipe_version": recipe_version,
                "sampling": generation["sampling"],
                "requested": dict(requested_params),
                "realized": dict(realized_params),
            },
            "rtf": generation["rtf"],
            "loudness": loudness,
            "gate": {
                "mechanical": attempt["gates"]["mechanical"],
                "content": attempt["gates"]["content"],
                "policy_version": gate_policy_version,
            },
        }
    except (KeyError, TypeError) as error:
        raise TakeLedgerError("eligible attempt の provenance が不足しています。") from error
    _validate_candidate(candidate, "candidate")
    return candidate
