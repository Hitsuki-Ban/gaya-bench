from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from gaya_pipeline.completion_listen import _load_completion_scenario_authority
from gaya_pipeline.completion_plan import CompletionPlan, require_production_completion_plan
from gaya_pipeline.completion_release import (
    CompletionReleaseError,
    validate_completion_release,
)
from gaya_pipeline.take_identity import canonical_json


class CompletionReviewError(RuntimeError):
    pass


PROTOCOL = "role-quality-review-bundle-v1"
BUNDLE_FILE = "role-quality-review-bundle-v1.json"
EXPECTED_REVIEW_COUNT = 145


@dataclass(frozen=True)
class CompletionReviewSummary:
    output_dir: Path
    bundle_sha256: str
    group_count: int


def build_completion_review_bundle(
    *,
    plan: CompletionPlan,
    release_dir: Path,
    source_audit_path: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    output_dir: Path,
) -> CompletionReviewSummary:
    require_production_completion_plan(plan)
    try:
        return _build_completion_review_bundle(
            plan=plan,
            release_dir=release_dir,
            source_audit_path=source_audit_path,
            artifacts_dir=artifacts_dir,
            scenarios_dir=scenarios_dir,
            voices_dir=voices_dir,
            output_dir=output_dir,
        )
    except CompletionReviewError:
        raise
    except (CompletionReleaseError, OSError, KeyError, TypeError, ValueError) as error:
        raise CompletionReviewError(f"quality review bundle入力契約が不正です: {error}") from error


def _build_completion_review_bundle(
    *,
    plan: CompletionPlan,
    release_dir: Path,
    source_audit_path: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    output_dir: Path,
) -> CompletionReviewSummary:
    for path, label in (
        (release_dir, "release"),
        (source_audit_path, "source audit"),
        (artifacts_dir, "artifacts"),
        (scenarios_dir, "scenarios"),
        (voices_dir, "voices"),
        (output_dir, "output"),
    ):
        if not path.is_absolute():
            raise CompletionReviewError(f"{label}は絶対pathが必要です。")
    if output_dir.exists():
        raise CompletionReviewError(f"review bundle outputは既存pathを拒否します: {output_dir}")
    if not output_dir.parent.is_dir():
        raise CompletionReviewError("review bundle outputの親directoryがありません。")

    release = validate_completion_release(
        release_dir=release_dir,
        source_audit_path=source_audit_path,
        artifacts_dir=artifacts_dir,
    )
    if release.selection["plan_sha256"] != plan.plan_id:
        raise CompletionReviewError("releaseとproduction planが一致しません。")
    authority = _load_completion_scenario_authority(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        plan=plan,
    )
    selected_by_group = {
        _group_key(group): str(group["decision"]["take_id"])
        for group in release.selection["groups"]
    }
    candidates_by_take = {
        str(candidate["take_id"]): candidate for candidate in release.manifest["candidates"]
    }
    lines = {
        (str(line["scenario"]), str(line["line"])): line
        for line in release.candidate_set["lines"]
    }
    local_sources: dict[str, Path] = {}
    for run in release.provenance["source_runs"]:
        run_root = artifacts_dir / "takes" / str(run["run_id"])
        for candidate in run["candidates"]:
            local_sources[str(candidate["take_id"])] = (
                run_root / Path(str(candidate["run_relative_path"]))
            )

    review_signals = [
        signal
        for signal in release.quality_signals["groups"]
        if signal["status"] == "review_required"
    ]
    if len(review_signals) != EXPECTED_REVIEW_COUNT:
        raise CompletionReviewError(
            f"quality review targetはexact {EXPECTED_REVIEW_COUNT} groupが必要です。",
        )

    groups: list[dict[str, Any]] = []
    audio_sources: dict[str, Path] = {}
    for signal in sorted(review_signals, key=_group_key):
        identity = _group_key(signal)
        take_id = selected_by_group.get(identity)
        if take_id is None:
            raise CompletionReviewError(f"quality signalのselected takeがありません: {identity}")
        candidate = candidates_by_take[take_id]
        source = local_sources.get(take_id)
        if source is None or not source.is_file():
            raise CompletionReviewError(f"quality review audioがありません: {identity}")
        context = authority.contexts[(identity[1], identity[2])]
        line = lines[(identity[1], identity[2])]
        audio_path = f"audio/{take_id}.opus"
        audio_sources[audio_path] = source
        groups.append(
            {
                "model": identity[0],
                "scenario": identity[1],
                "line": identity[2],
                "variant": identity[3],
                "scenario_title": str(line["scenario_title"]),
                "text": str(line["text"]),
                "delivery": str(line["delivery"]),
                "role": dict(context["role"]),
                "take_id": take_id,
                "audio_path": audio_path,
                "audio_sha256": str(candidate["sha256"]),
                "expected_gender": signal["expected_gender"],
                "median_f0_hz": signal["median_f0_hz"],
                "signal": signal["signal"],
            },
        )
    document = validate_completion_review_bundle(
        {
            "format_version": 1,
            "protocol": PROTOCOL,
            "plan_sha256": plan.plan_id,
            "decision_sha256": release.quality_signals["decision_sha256"],
            "manifest_sha256": release.provenance["manifest_sha256"],
            "quality_signals_sha256": release.provenance["quality_signals_sha256"],
            "groups": groups,
        },
    )
    payload = canonical_json(document).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent),
    )
    try:
        (temporary / BUNDLE_FILE).write_bytes(payload)
        (temporary / BUNDLE_FILE.replace(".json", ".sha256")).write_text(
            digest,
            encoding="ascii",
        )
        for relative_path, source in audio_sources.items():
            destination = temporary / Path(relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        temporary.replace(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return CompletionReviewSummary(
        output_dir=output_dir,
        bundle_sha256=digest,
        group_count=len(groups),
    )


def validate_completion_review_bundle(value: Any) -> dict[str, Any]:
    root_keys = {
        "format_version",
        "protocol",
        "plan_sha256",
        "decision_sha256",
        "manifest_sha256",
        "quality_signals_sha256",
        "groups",
    }
    if not isinstance(value, dict) or set(value) != root_keys:
        raise CompletionReviewError("quality review bundle exact rootが不正です。")
    if value["format_version"] != 1 or value["protocol"] != PROTOCOL:
        raise CompletionReviewError("quality review bundle protocolが不正です。")
    for field in (
        "plan_sha256",
        "decision_sha256",
        "manifest_sha256",
        "quality_signals_sha256",
    ):
        _sha(value[field], f"quality review bundle.{field}")
    groups = value["groups"]
    if not isinstance(groups, list) or len(groups) != EXPECTED_REVIEW_COUNT:
        raise CompletionReviewError(
            f"quality review bundleはexact {EXPECTED_REVIEW_COUNT} groupが必要です。",
        )
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str, str]] = set()
    takes: set[str] = set()
    for index, raw in enumerate(groups):
        label = f"quality review bundle.groups[{index}]"
        if not isinstance(raw, dict) or set(raw) != {
            "model",
            "scenario",
            "line",
            "variant",
            "scenario_title",
            "text",
            "delivery",
            "role",
            "take_id",
            "audio_path",
            "audio_sha256",
            "expected_gender",
            "median_f0_hz",
            "signal",
        }:
            raise CompletionReviewError(f"{label} exact contractが不正です。")
        identity = _group_key(raw)
        if identity in identities or any(not item for item in identity):
            raise CompletionReviewError(f"{label} identityが不正です。")
        identities.add(identity)
        for field in ("scenario_title", "text", "delivery"):
            if not isinstance(raw[field], str) or not raw[field].strip():
                raise CompletionReviewError(f"{label}.{field}が不正です。")
        role = raw["role"]
        if not isinstance(role, dict) or set(role) != {
            "name",
            "kind",
            "gender",
            "age",
            "archetype",
            "voice",
            "personality",
        } or any(not isinstance(item, str) or not item for item in role.values()):
            raise CompletionReviewError(f"{label}.roleが不正です。")
        take_id = _sha(raw["take_id"], f"{label}.take_id")
        if take_id in takes or raw["audio_path"] != f"audio/{take_id}.opus":
            raise CompletionReviewError(f"{label}.audio identityが不正です。")
        takes.add(take_id)
        _sha(raw["audio_sha256"], f"{label}.audio_sha256")
        gender = raw["expected_gender"]
        median = raw["median_f0_hz"]
        signal = raw["signal"]
        if gender not in {"female", "male"}:
            raise CompletionReviewError(f"{label}.expected_genderが不正です。")
        if median is not None and (
            isinstance(median, bool) or not isinstance(median, (int, float)) or median <= 0
        ):
            raise CompletionReviewError(f"{label}.median_f0_hzが不正です。")
        expected_signal = (
            "gender_f0_unavailable"
            if median is None
            else "gender_f0_below_expected"
            if gender == "female" and median < 165
            else "gender_f0_above_expected"
            if gender == "male" and median > 180
            else None
        )
        if signal != expected_signal or signal is None:
            raise CompletionReviewError(f"{label}.signalがsoft F0 policyと不一致です。")
        normalized.append(dict(raw))
    if normalized != sorted(normalized, key=_group_key):
        raise CompletionReviewError("quality review bundle.groupsはcanonical順が必要です。")
    return {
        "format_version": 1,
        "protocol": PROTOCOL,
        "plan_sha256": value["plan_sha256"],
        "decision_sha256": value["decision_sha256"],
        "manifest_sha256": value["manifest_sha256"],
        "quality_signals_sha256": value["quality_signals_sha256"],
        "groups": normalized,
    }


def _group_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value["model"]),
        str(value["scenario"]),
        str(value["line"]),
        str(value["variant"]),
    )


def _sha(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CompletionReviewError(f"{label}はlowercase SHA-256が必要です。")
    return value
