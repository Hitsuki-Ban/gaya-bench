"""Validate and bind the completed targeted listening result for Issue #192."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from issue192_listening_bundle import (
    BUNDLE_FILE,
    MODEL,
    BundleError,
    canonical_bytes,
    digest,
    file_digest,
    prepare_sources,
    source_wav,
)


RESULT_FILE = "baseline-quality-ab-result-v1.json"
RESULT_PROTOCOL = "baseline-quality-ab-result-v1"
REPORT_PROTOCOL = "issue-192-targeted-listening-result-v1"
SPECIAL_CHOICES = {"no_preference", "none_acceptable"}


class ResultError(RuntimeError):
    pass


def _load_canonical(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ResultError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise ResultError(f"JSON root must be an object: {path}")
    if raw != canonical_bytes(value):
        raise ResultError(f"JSON must use canonical bytes: {path}")
    return value, raw


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ResultError(f"{label} exact fields do not match")
    return value


def _sha(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ResultError(f"{label} must be a lowercase SHA-256")
    return value


def _candidate_receipt(
    *,
    study_id: str,
    target: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    attempt = candidate["attempt"]
    source = str(candidate["source"])
    take_id = _sha(attempt.get("take_id"), "attempt.take_id")
    source_path, wav_sha256 = source_wav(candidate["run_root"], attempt)
    group_identity = {
        "study_id": study_id,
        "model": MODEL,
        "scenario": target["scenario"],
        "line": target["line"],
    }
    candidate_id = digest(
        canonical_bytes(
            {
                **group_identity,
                "source": source,
                "take_id": take_id,
                "wav_sha256": wav_sha256,
            }
        )
    )
    return {
        "candidate_id": candidate_id,
        "source": source,
        "run_id": candidate["run_root"].name,
        "take_id": take_id,
        "take_index": int(attempt["take_index"]),
        "wav_sha256": wav_sha256,
        "source_wav_path": source_path.relative_to(candidate["run_root"]).as_posix(),
    }


def summarize(
    *,
    bundle_dir: Path,
    result_path: Path,
    manifest_path: Path,
    artifacts: Path,
) -> dict[str, Any]:
    try:
        prepared, authority, study_id = prepare_sources(manifest_path, artifacts)
    except BundleError as error:
        raise ResultError(f"source authority is invalid: {error}") from error

    bundle, bundle_raw = _load_canonical(bundle_dir / BUNDLE_FILE)
    bundle_marker = bundle_dir / BUNDLE_FILE.replace(".json", ".sha256")
    try:
        marker = bundle_marker.read_bytes().decode("ascii")
    except (OSError, UnicodeError) as error:
        raise ResultError("cannot read bundle SHA marker") from error
    if marker != digest(bundle_raw):
        raise ResultError("bundle SHA marker does not match")
    _exact(
        bundle,
        {"format_version", "protocol", "study_id", "title", "instructions", "groups"},
        "bundle",
    )
    if (
        bundle["format_version"] != 1
        or bundle["protocol"] != "baseline-quality-ab-bundle-v1"
        or bundle["study_id"] != study_id
    ):
        raise ResultError("bundle is not bound to the current source authority")

    result, result_raw = _load_canonical(result_path)
    _exact(result, {"format_version", "protocol", "study_id", "groups"}, "result")
    if (
        result["format_version"] != 1
        or result["protocol"] != RESULT_PROTOCOL
        or result["study_id"] != study_id
    ):
        raise ResultError("result is not bound to the listening study")

    bundle_groups = bundle["groups"]
    result_groups = result["groups"]
    if (
        not isinstance(bundle_groups, list)
        or not isinstance(result_groups, list)
        or len(bundle_groups) != len(prepared)
        or len(result_groups) != len(prepared)
    ):
        raise ResultError("bundle/result must contain the exact prepared groups")

    expected_files = {BUNDLE_FILE, BUNDLE_FILE.replace(".json", ".sha256")}
    groups: list[dict[str, Any]] = []
    for index, (prepared_group, raw_group, raw_result) in enumerate(
        zip(prepared, bundle_groups, result_groups, strict=True)
    ):
        group = _exact(
            raw_group,
            {"id", "track", "model", "scenario", "line", "text", "focus", "candidates"},
            f"bundle.groups[{index}]",
        )
        result_group = _exact(
            raw_result,
            {"id", "heard_candidate_ids", "choice", "notes"},
            f"result.groups[{index}]",
        )
        target = prepared_group["target"]
        group_identity = {
            "study_id": study_id,
            "model": MODEL,
            "scenario": target["scenario"],
            "line": target["line"],
        }
        expected_group_id = digest(canonical_bytes(group_identity))
        if (
            group["id"] != expected_group_id
            or result_group["id"] != expected_group_id
            or group["model"] != MODEL
            or group["scenario"] != target["scenario"]
            or group["line"] != target["line"]
            or group["text"] != target["text"]
            or group["focus"] != target["focus"]
            or group["track"] != target["track"]
        ):
            raise ResultError(f"group {index} identity/content drifted")

        receipts = [
            _candidate_receipt(study_id=study_id, target=target, candidate=candidate)
            for candidate in prepared_group["candidates"]
        ]
        receipts_by_id = {item["candidate_id"]: item for item in receipts}
        candidates = group["candidates"]
        if not isinstance(candidates, list) or len(candidates) != len(receipts):
            raise ResultError(f"group {index} candidate count drifted")
        presented_ids: set[str] = set()
        for candidate_index, raw_candidate in enumerate(candidates):
            presented = _exact(
                raw_candidate,
                {"id", "variant", "audio_path", "audio_sha256"},
                f"bundle.groups[{index}].candidates[{candidate_index}]",
            )
            candidate_id = _sha(presented["id"], "bundle candidate id")
            receipt = receipts_by_id.get(candidate_id)
            if receipt is None or candidate_id in presented_ids:
                raise ResultError(f"group {index} candidate mapping drifted")
            presented_ids.add(candidate_id)
            relative = f"audio/{candidate_id}.wav"
            if (
                presented["variant"] != receipt["source"]
                or presented["audio_path"] != relative
                or presented["audio_sha256"] != receipt["wav_sha256"]
            ):
                raise ResultError(f"group {index} candidate receipt drifted")
            audio_path = bundle_dir / relative
            if not audio_path.is_file() or file_digest(audio_path) != receipt["wav_sha256"]:
                raise ResultError(f"group {index} bundle audio drifted")
            expected_files.add(relative)
        if presented_ids != set(receipts_by_id):
            raise ResultError(f"group {index} candidate set is incomplete")

        heard = result_group["heard_candidate_ids"]
        if not isinstance(heard, list) or set(heard) != presented_ids or len(heard) != len(presented_ids):
            raise ResultError(f"group {index} does not prove complete playback")
        choice = result_group["choice"]
        if choice in receipts_by_id:
            selected: dict[str, Any] | None = dict(receipts_by_id[choice])
            decision = selected["source"]
        elif choice in SPECIAL_CHOICES:
            selected = None
            decision = choice
        else:
            raise ResultError(f"group {index} choice is invalid")
        notes = result_group["notes"]
        if not isinstance(notes, str) or len(notes) > 500:
            raise ResultError(f"group {index} notes are invalid")
        groups.append(
            {
                "model": MODEL,
                "scenario": target["scenario"],
                "line": target["line"],
                "variant": "dry",
                "group_id": expected_group_id,
                "heard_candidates": sorted(receipts, key=lambda item: item["candidate_id"]),
                "decision": decision,
                "selected": selected,
                "notes": notes,
            }
        )

    actual_files = {
        path.relative_to(bundle_dir).as_posix()
        for path in bundle_dir.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise ResultError("bundle file set does not match the exact contract")

    return {
        "format_version": 1,
        "protocol": REPORT_PROTOCOL,
        "study_id": study_id,
        "sources": {
            "authority": authority,
            "bundle": {"sha256": digest(bundle_raw), "size_bytes": len(bundle_raw)},
            "result": {"sha256": digest(result_raw), "size_bytes": len(result_raw)},
        },
        "groups": groups,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ResultError(f"output must be a new path: {output}")
    report = summarize(
        bundle_dir=args.bundle.resolve(),
        result_path=args.result.resolve(),
        manifest_path=args.manifest.resolve(),
        artifacts=args.artifacts.resolve(),
    )
    if not output.parent.is_dir():
        raise ResultError(f"output parent must exist: {output.parent}")
    output.write_bytes(canonical_bytes(report))
    print(f"wrote {len(report['groups'])} targeted listening decisions")


if __name__ == "__main__":
    main()
