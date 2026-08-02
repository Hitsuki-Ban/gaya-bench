"""Validate and summarize the completed blinded listening result for Issue #190."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BUNDLE_FILE = "baseline-quality-ab-bundle-v1.json"
BUNDLE_PROTOCOL = "baseline-quality-ab-bundle-v1"
RESULT_PROTOCOL = "baseline-quality-ab-result-v1"
REPORT_PROTOCOL = "issue-190-listening-analysis-v1"
SHA_LENGTH = 64


class ResultError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def is_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def load_json(path: Path, *, canonical: bool) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ResultError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise ResultError(f"JSON root must be an object: {path}")
    if canonical and canonical_bytes(value) != raw:
        raise ResultError(f"JSON must use canonical bytes: {path}")
    return value, raw


def exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ResultError(f"{label} keys do not match the contract")


def summarize(bundle_dir: Path, result_path: Path) -> dict[str, Any]:
    bundle_path = bundle_dir / BUNDLE_FILE
    bundle, bundle_raw = load_json(bundle_path, canonical=True)
    marker = (bundle_dir / BUNDLE_FILE.replace(".json", ".sha256")).read_text(
        encoding="ascii"
    )
    if marker != digest(bundle_raw):
        raise ResultError("bundle SHA marker does not match")
    exact_keys(
        bundle,
        {"format_version", "protocol", "study_id", "title", "instructions", "groups"},
        "bundle",
    )
    if (
        bundle["format_version"] != 1
        or bundle["protocol"] != BUNDLE_PROTOCOL
        or not is_sha(bundle["study_id"])
    ):
        raise ResultError("bundle protocol is invalid")

    result, result_raw = load_json(result_path, canonical=True)
    exact_keys(result, {"format_version", "protocol", "study_id", "groups"}, "result")
    if (
        result["format_version"] != 1
        or result["protocol"] != RESULT_PROTOCOL
        or result["study_id"] != bundle["study_id"]
    ):
        raise ResultError("result root is not bound to the bundle")
    bundle_groups = bundle["groups"]
    result_groups = result["groups"]
    if not isinstance(bundle_groups, list) or not isinstance(result_groups, list):
        raise ResultError("bundle/result groups must be arrays")
    if len(bundle_groups) != len(result_groups) or not bundle_groups:
        raise ResultError("bundle/result group counts do not match")

    rows: list[dict[str, Any]] = []
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    seen_candidate_ids: set[str] = set()
    expected_files = {BUNDLE_FILE, BUNDLE_FILE.replace(".json", ".sha256")}
    for index, (bundle_group, result_group) in enumerate(zip(bundle_groups, result_groups)):
        if not isinstance(bundle_group, dict) or not isinstance(result_group, dict):
            raise ResultError(f"group {index} is invalid")
        exact_keys(result_group, {"id", "heard_candidate_ids", "choice", "notes"}, f"result group {index}")
        if result_group["id"] != bundle_group.get("id"):
            raise ResultError(f"result group {index} is not bound to the bundle")
        candidates = bundle_group.get("candidates")
        heard = result_group["heard_candidate_ids"]
        if not isinstance(candidates, list) or not isinstance(heard, list):
            raise ResultError(f"group {index} candidates/heard are invalid")
        by_id = {
            candidate["id"]: candidate
            for candidate in candidates
            if isinstance(candidate, dict) and isinstance(candidate.get("id"), str)
        }
        if len(by_id) != len(candidates) or set(heard) != set(by_id):
            raise ResultError(f"group {index} does not prove complete playback")
        for candidate_id, candidate in by_id.items():
            exact_keys(
                candidate,
                {"id", "variant", "audio_path", "audio_sha256"},
                f"bundle group {index} candidate",
            )
            if not is_sha(candidate_id) or candidate_id in seen_candidate_ids:
                raise ResultError(f"group {index} candidate id is invalid or duplicated")
            seen_candidate_ids.add(candidate_id)
            relative = candidate["audio_path"]
            expected_relative = f"audio/{candidate_id}.wav"
            if relative != expected_relative or not is_sha(candidate["audio_sha256"]):
                raise ResultError(f"group {index} candidate audio binding is invalid")
            audio_path = bundle_dir / "audio" / f"{candidate_id}.wav"
            if not audio_path.is_file() or file_digest(audio_path) != candidate["audio_sha256"]:
                raise ResultError(f"group {index} candidate audio hash does not match")
            expected_files.add(expected_relative)
        choice = result_group["choice"]
        if choice in by_id:
            decision = str(by_id[choice]["variant"])
        elif choice in {"no_preference", "none_acceptable"}:
            decision = str(choice)
        else:
            raise ResultError(f"group {index} choice is invalid or incomplete")
        notes = result_group["notes"]
        if not isinstance(notes, str) or len(notes) > 500:
            raise ResultError(f"group {index} notes are invalid")
        track = str(bundle_group["track"])
        counts[track][decision] += 1
        rows.append(
            {
                "id": bundle_group["id"],
                "track": track,
                "model": bundle_group["model"],
                "scenario": bundle_group["scenario"],
                "line": bundle_group["line"],
                "choice": decision,
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
        "study_id": bundle["study_id"],
        "sources": {
            "bundle": {"sha256": digest(bundle_raw), "size": len(bundle_raw)},
            "result": {"sha256": digest(result_raw), "size": len(result_raw)},
        },
        "group_count": len(rows),
        "by_track": [
            {
                "track": track,
                "group_count": sum(track_counts.values()),
                "choice_counts": dict(sorted(track_counts.items())),
            }
            for track, track_counts in sorted(counts.items())
        ],
        "groups": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ResultError(f"output must be a new path: {output}")
    report = summarize(args.bundle.resolve(), args.result.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_bytes(report))
    print(f"wrote {report['group_count']} listening decisions")


if __name__ == "__main__":
    main()
