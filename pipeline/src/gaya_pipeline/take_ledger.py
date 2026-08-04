from __future__ import annotations

import hashlib
import json
import math
import time
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

from gaya_pipeline.take_identity import (
    TakeIdentityError,
    canonical_json,
    make_take_id,
)


class TakeLedgerError(ValueError):
    pass


STATUSES = {
    "planned",
    "generated",
    "blocked",
    "generation_failed",
    "hard_rejected",
    "eligible",
}
TERMINAL_STATUSES = {"generation_failed", "hard_rejected", "eligible"}
TRANSITIONS = {
    "planned": {"generated", "generation_failed"},
    "generated": {"blocked", "hard_rejected", "eligible"},
    "blocked": {"hard_rejected", "eligible"},
}
MECHANICAL_GATES = {"pass", "reject", "blocked"}
CONTENT_GATES = {"pass", "review_required", "reject", "blocked", "not_run"}
GROUP_KEYS = {"model", "scenario", "line", "variant"}
ROOT_KEYS = {"format_version", "run_id", "created_at", "source", "attempts"}
SOURCE_KEYS = {
    "scenario_sha256",
    "model",
    "takes",
    "seed_base",
    "recipe_version",
    "groups",
}
PHASE_B_SOURCE_KEYS = {
    "protocol",
    "plan_sha256",
    "run_kind",
    "supersedes_run_id",
    "anchor_selection_sha256",
    "anchor_plan_sha256",
    "target_groups",
}
PHASE_B_TARGET_KEYS = GROUP_KEYS | {"role_epoch_sha256"}
PHASE_B_PROTOCOL = "phase-b-generation-v2"
ANCHOR_MODELS = {
    "irodori-tts-600m-v3-voicedesign",
    "irodori-tts-v4-small",
    "qwen3-tts-12hz-1.7b",
}
ATTEMPT_KEYS = {
    "model",
    "scenario",
    "line",
    "variant",
    "take_index",
    "take_id",
    "generation_input_sha256",
    "generation",
    "audio",
    "gates",
    "features",
    "status",
}
_RETRYABLE_REPLACE_WINERRORS = {5, 32}
_REPLACE_RETRY_DELAYS_SECONDS = (0.01, 0.02, 0.04, 0.08)


def _exact_keys(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise TakeLedgerError(f"{field} の項目が契約と一致しません。")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TakeLedgerError(f"{field} は空でない文字列が必要です。")
    return value


def _path_segment(value: Any, field: str) -> str:
    text = _string(value, field)
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise TakeLedgerError(f"{field} は安全な path segment が必要です。")
    return text


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TakeLedgerError(f"{field} は 1 以上の整数が必要です。")
    return value


def _sha(value: Any, field: str) -> str:
    text = _string(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise TakeLedgerError(f"{field} は完全な小文字 SHA-256 が必要です。")
    return text


def _relative_path(value: Any, field: str) -> str:
    text = _string(value, field)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "\\" in text or "." in path.parts:
        raise TakeLedgerError(f"{field} は run root 配下の相対 POSIX path が必要です。")
    return text


def _group(value: Any, field: str) -> tuple[str, str, str, str]:
    item = _exact_keys(value, GROUP_KEYS, field)
    return tuple(
        _path_segment(item[key], f"{field}.{key}")
        for key in ("model", "scenario", "line", "variant")
    )  # type: ignore[return-value]


def _validate_generation(value: Any, status: str, field: str) -> None:
    if status == "planned":
        expected = {"status", "seed", "sampling"}
        required_status = "planned"
    elif status == "generation_failed":
        expected = {"status", "seed", "sampling", "error"}
        required_status = "failed"
    else:
        expected = {"status", "seed", "sampling", "rtf"}
        required_status = "succeeded"
    generation = _exact_keys(value, expected, field)
    if generation["status"] != required_status:
        raise TakeLedgerError(f"{field}.status が attempt status と一致しません。")
    seed = generation["seed"]
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise TakeLedgerError(f"{field}.seed は整数または null が必要です。")
    if not isinstance(generation["sampling"], dict):
        raise TakeLedgerError(f"{field}.sampling は object が必要です。")
    try:
        canonical_json(generation["sampling"])
    except TakeIdentityError as error:
        raise TakeLedgerError(f"{field}.sampling が JSON 契約を満たしません。") from error
    if "rtf" in generation:
        rtf = generation["rtf"]
        if (
            isinstance(rtf, bool)
            or not isinstance(rtf, int | float)
            or not math.isfinite(rtf)
            or rtf < 0
        ):
            raise TakeLedgerError(f"{field}.rtf は有限の非負数が必要です。")
    if "error" in generation:
        _string(generation["error"], f"{field}.error")


def _validate_attempt(
    value: Any,
    field: str,
    *,
    phase_b: bool | None = None,
) -> tuple[str, str, str, str, int]:
    if not isinstance(value, dict):
        raise TakeLedgerError(f"{field} は object が必要です。")
    status = value.get("status")
    if status not in STATUSES:
        raise TakeLedgerError(f"{field}.status が不正です。")
    minimal = status in {"planned", "generation_failed"}
    expected = (
        GROUP_KEYS
        | {"take_index", "generation_input_sha256", "generation", "status"}
        if minimal
        else ATTEMPT_KEYS
    )
    has_phase_b = "phase_b_provenance_sha256" in value
    if phase_b is not None and has_phase_b != phase_b:
        raise TakeLedgerError(
            f"{field} のPhase B provenance有無がledger sourceと一致しません。",
        )
    if has_phase_b:
        expected = expected | {"phase_b_provenance_sha256"}
    attempt = _exact_keys(value, expected, field)
    group = _group({key: attempt[key] for key in GROUP_KEYS}, field)
    index = _positive_int(attempt["take_index"], f"{field}.take_index")
    _sha(attempt["generation_input_sha256"], f"{field}.generation_input_sha256")
    if has_phase_b:
        _sha(
            attempt["phase_b_provenance_sha256"],
            f"{field}.phase_b_provenance_sha256",
        )
    _validate_generation(attempt["generation"], status, f"{field}.generation")
    if not minimal:
        take_id = _sha(attempt["take_id"], f"{field}.take_id")
        audio = _exact_keys(
            attempt["audio"],
            {
                "wav_path",
                "wav_sha256",
                "opus_path",
                "opus_sha256",
                "sidecar_sha256",
            },
            f"{field}.audio",
        )
        wav_path = _relative_path(audio["wav_path"], f"{field}.audio.wav_path")
        _sha(audio["wav_sha256"], f"{field}.audio.wav_sha256")
        opus_path = _relative_path(audio["opus_path"], f"{field}.audio.opus_path")
        opus_sha = _sha(audio["opus_sha256"], f"{field}.audio.opus_sha256")
        _sha(audio["sidecar_sha256"], f"{field}.audio.sidecar_sha256")
        path_root = f"audio/{group[0]}/{group[1]}/{group[2]}/{group[3]}"
        if wav_path != f"{path_root}/take-{index:04d}.wav":
            raise TakeLedgerError(f"{field}.audio.wav_path が slot と一致しません。")
        if opus_path != f"{path_root}/take-{index:04d}.opus":
            raise TakeLedgerError(f"{field}.audio.opus_path が slot と一致しません。")
        if take_id != make_take_id(
            generation_input_sha256=attempt["generation_input_sha256"],
            final_opus_sha256=opus_sha,
        ):
            raise TakeLedgerError(f"{field}.take_id が provenance と一致しません。")
        gates = attempt["gates"]
        features = attempt["features"]
        if status == "generated":
            _exact_keys(gates, set(), f"{field}.gates")
        else:
            gate = _exact_keys(gates, {"mechanical", "content"}, f"{field}.gates")
            mechanical = gate["mechanical"]
            content = gate["content"]
            if mechanical not in MECHANICAL_GATES:
                raise TakeLedgerError(f"{field}.gates.mechanical が不正です。")
            if content not in CONTENT_GATES:
                raise TakeLedgerError(f"{field}.gates.content が不正です。")
            allowed_gates = {
                "eligible": {
                    ("pass", "pass"),
                    ("pass", "review_required"),
                },
                "hard_rejected": {
                    ("reject", "not_run"),
                    ("pass", "reject"),
                },
                "blocked": {
                    ("blocked", "not_run"),
                    ("pass", "blocked"),
                },
            }
            if (mechanical, content) not in allowed_gates[status]:
                raise TakeLedgerError(
                    f"{field}.gates が {status} と一致しません。",
                )
        _exact_keys(features, {"status"}, f"{field}.features")
        if features["status"] != "unscored":
            raise TakeLedgerError(f"{field}.features.status が不正です。")
    return (*group, index)


def validate_attempt(attempt: Any) -> dict[str, Any]:
    _validate_attempt(attempt, "attempt")
    return attempt


def validate_ledger(document: Any) -> dict[str, Any]:
    ledger = _exact_keys(document, ROOT_KEYS, "ledger")
    if ledger["format_version"] != 1:
        raise TakeLedgerError("ledger.format_version は 1 が必要です。")
    _path_segment(ledger["run_id"], "ledger.run_id")
    _string(ledger["created_at"], "ledger.created_at")
    if not isinstance(ledger["source"], dict):
        raise TakeLedgerError("ledger.source はobjectが必要です。")
    source_keys = (
        SOURCE_KEYS | {"phase_b"}
        if "phase_b" in ledger["source"]
        else SOURCE_KEYS
    )
    source = _exact_keys(ledger["source"], source_keys, "ledger.source")
    _sha(source["scenario_sha256"], "ledger.source.scenario_sha256")
    model = _path_segment(source["model"], "ledger.source.model")
    takes = _positive_int(source["takes"], "ledger.source.takes")
    seed_base = source["seed_base"]
    if seed_base is not None and (
        isinstance(seed_base, bool) or not isinstance(seed_base, int)
    ):
        raise TakeLedgerError(
            "ledger.source.seed_base は整数または null が必要です。",
        )
    _string(source["recipe_version"], "ledger.source.recipe_version")
    if not isinstance(source["groups"], list) or not source["groups"]:
        raise TakeLedgerError("ledger.source.groups は空でない配列が必要です。")
    groups = {
        _group(group, f"ledger.source.groups[{index}]")
        for index, group in enumerate(source["groups"])
    }
    if len(groups) != len(source["groups"]) or any(group[0] != model for group in groups):
        raise TakeLedgerError("ledger.source.groups が重複または model 不一致です。")
    phase_b = (
        _validate_phase_b_source(source["phase_b"], model=model)
        if "phase_b" in source
        else None
    )
    if phase_b is not None:
        target_identities = [
            tuple(group[key] for key in ("model", "scenario", "line", "variant"))
            for group in phase_b["target_groups"]
        ]
        source_identities = [
            tuple(group[key] for key in ("model", "scenario", "line", "variant"))
            for group in source["groups"]
        ]
        if target_identities != source_identities:
            raise TakeLedgerError(
                "ledger.source.phase_b.target_groupsがsource.groupsと"
                "exact一致しません。",
            )
    if not isinstance(ledger["attempts"], list):
        raise TakeLedgerError("ledger.attempts は配列が必要です。")
    slots = set()
    counts = {group: 0 for group in groups}
    for index, attempt in enumerate(ledger["attempts"]):
        slot = _validate_attempt(
            attempt,
            f"ledger.attempts[{index}]",
            phase_b=phase_b is not None,
        )
        if seed_base is None and attempt["generation"]["seed"] is not None:
            raise TakeLedgerError(
                "ledger.source.seed_base が null の場合、"
                "すべての attempt generation.seed は null が必要です。",
            )
        group = slot[:4]
        if group not in groups:
            raise TakeLedgerError("attempt が source.groups の範囲外です。")
        if slot in slots:
            raise TakeLedgerError("attempt slot が重複しています。")
        slots.add(slot)
        counts[group] += 1
        if phase_b is not None:
            target_group = next(
                item
                for item in phase_b["target_groups"]
                if tuple(
                    item[key]
                    for key in ("model", "scenario", "line", "variant")
                )
                == group
            )
            provenance = {
                "protocol": phase_b["protocol"],
                "plan_sha256": phase_b["plan_sha256"],
                "run_kind": phase_b["run_kind"],
                "supersedes_run_id": phase_b["supersedes_run_id"],
                "anchor_selection_sha256": phase_b[
                    "anchor_selection_sha256"
                ],
                "anchor_plan_sha256": phase_b["anchor_plan_sha256"],
                "target_group": target_group,
            }
            expected_provenance_sha = hashlib.sha256(
                canonical_json(provenance).encode("utf-8"),
            ).hexdigest()
            if (
                attempt["phase_b_provenance_sha256"]
                != expected_provenance_sha
            ):
                raise TakeLedgerError(
                    "attempt Phase B provenanceがledger sourceと一致しません。",
                )
        if slot[4] > takes:
            raise TakeLedgerError("take_index が source.takes を超えています。")
    if any(count != takes for count in counts.values()):
        raise TakeLedgerError("各 group には source.takes 件の attempt が必要です。")
    return ledger


def _validate_phase_b_source(
    value: Any,
    *,
    model: str,
) -> dict[str, Any]:
    phase_b = _exact_keys(value, PHASE_B_SOURCE_KEYS, "ledger.source.phase_b")
    if phase_b["protocol"] != PHASE_B_PROTOCOL:
        raise TakeLedgerError(
            f"ledger.source.phase_b.protocol は{PHASE_B_PROTOCOL}が必要です。",
        )
    _sha(phase_b["plan_sha256"], "ledger.source.phase_b.plan_sha256")
    run_kind = phase_b["run_kind"]
    if run_kind not in {"primary", "topup"}:
        raise TakeLedgerError(
            "ledger.source.phase_b.run_kindはprimaryまたはtopupが必要です。",
        )
    supersedes = phase_b["supersedes_run_id"]
    if run_kind == "primary":
        if supersedes is not None:
            raise TakeLedgerError(
                "primary Phase B runにsupersedes_run_idは指定できません。",
            )
    elif supersedes is None:
        raise TakeLedgerError(
            "topup Phase B runにはsupersedes_run_idが必要です。",
        )
    else:
        _path_segment(
            supersedes,
            "ledger.source.phase_b.supersedes_run_id",
        )
    anchor_sha = phase_b["anchor_selection_sha256"]
    anchor_plan_sha = phase_b["anchor_plan_sha256"]
    if model in ANCHOR_MODELS:
        _sha(
            anchor_sha,
            "ledger.source.phase_b.anchor_selection_sha256",
        )
        _sha(
            anchor_plan_sha,
            "ledger.source.phase_b.anchor_plan_sha256",
        )
    elif anchor_sha is not None or anchor_plan_sha is not None:
        raise TakeLedgerError(
            "非Qwen/Irodori Phase B runにanchor authorityは指定できません。",
        )
    target_values = phase_b["target_groups"]
    if not isinstance(target_values, list) or not target_values:
        raise TakeLedgerError(
            "ledger.source.phase_b.target_groupsは空でない配列が必要です。",
        )
    identities: set[tuple[str, str, str, str]] = set()
    for index, value in enumerate(target_values):
        field = f"ledger.source.phase_b.target_groups[{index}]"
        target = _exact_keys(value, PHASE_B_TARGET_KEYS, field)
        identity = _group(
            {key: target[key] for key in GROUP_KEYS},
            field,
        )
        if identity[0] != model or identity in identities:
            raise TakeLedgerError(
                "ledger.source.phase_b.target_groupsが重複または"
                "model不一致です。",
            )
        identities.add(identity)
        _sha(target["role_epoch_sha256"], f"{field}.role_epoch_sha256")
    ordered_identities = [
        tuple(
            target[key]
            for key in ("model", "scenario", "line", "variant")
        )
        for target in target_values
    ]
    if ordered_identities != sorted(ordered_identities):
        raise TakeLedgerError(
            "ledger.source.phase_b.target_groupsはcanonical順が必要です。",
        )
    return phase_b


def transition_attempt(
    ledger: dict[str, Any],
    *,
    slot: tuple[str, str, str, str, int],
    replacement: dict[str, Any],
) -> dict[str, Any]:
    validate_ledger(ledger)
    result = deepcopy(ledger)
    for index, current in enumerate(result["attempts"]):
        current_slot = tuple(current[key] for key in ("model", "scenario", "line", "variant")) + (
            current["take_index"],
        )
        if current_slot != slot:
            continue
        current_status = current["status"]
        if current_status in TERMINAL_STATUSES:
            raise TakeLedgerError("terminal attempt は変更できません。")
        if replacement.get("status") not in TRANSITIONS[current_status]:
            raise TakeLedgerError(
                f"不正な attempt 遷移です: {current_status}->{replacement.get('status')}",
            )
        replacement_slot = _validate_attempt(replacement, "replacement")
        if replacement_slot != slot:
            raise TakeLedgerError("replacement の attempt slot が変わっています。")
        if replacement["generation_input_sha256"] != current["generation_input_sha256"]:
            raise TakeLedgerError("generation_input_sha256 は遷移中に変更できません。")
        if current_status in {"generated", "blocked"}:
            for field in ("take_id", "generation", "audio"):
                if replacement.get(field) != current.get(field):
                    raise TakeLedgerError(
                        f"生成済み attempt の {field} provenance は変更できません。",
                    )
        result["attempts"][index] = deepcopy(replacement)
        validate_ledger(result)
        return result
    raise TakeLedgerError("attempt slot が見つかりません。")


def read_ledger(path: Path) -> dict[str, Any]:
    return validate_ledger(json.loads(path.read_text(encoding="utf-8")))


def _replace_ledger_pending(temporary: Path, path: Path) -> None:
    for attempt in range(len(_REPLACE_RETRY_DELAYS_SECONDS) + 1):
        try:
            temporary.replace(path)
            return
        except OSError as error:
            if (
                getattr(error, "winerror", None)
                not in _RETRYABLE_REPLACE_WINERRORS
                or attempt == len(_REPLACE_RETRY_DELAYS_SECONDS)
            ):
                raise
            time.sleep(_REPLACE_RETRY_DELAYS_SECONDS[attempt])


def write_ledger_atomic(path: Path, ledger: dict[str, Any]) -> None:
    validate_ledger(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.pending")
    try:
        temporary.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _replace_ledger_pending(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
