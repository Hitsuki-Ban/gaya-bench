"""Build the two-group targeted listening bundle for Issue #192."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


MODEL = "irodori-tts-600m-v3-voicedesign"
SOURCE_RUN_ID = "20260801T192432301893Z-irodori-tts-600m-v3-voicedesign-n4"
CANDIDATE_RUN_ID = "20260802T143828023146Z-irodori-tts-600m-v3-voicedesign-n4"
BUNDLE_FILE = "baseline-quality-ab-bundle-v1.json"

TARGETS = (
    {
        "scenario": "festival-night",
        "line": "yatai-obasan-003",
        "text": "あいよ、まいどありぃ！",
        "track": "irodori-targeted",
        "focus": (
            "选开头没有单独多笑一串、同时最像成年女性屋台老板的一条。"
            "应是笑着道谢，语气、日语音调和音质也不能比其他候选差。"
        ),
        "new_take_indices": (3,),
    },
    {
        "scenario": "market-day",
        "line": "fruit-vendor-002",
        "text": "お、姐さんお目が高いね！",
        "track": "irodori-targeted",
        "focus": (
            "选把「姐さん」自然读成「ねえさん」、同时最像成年男性露天商的一条。"
            "不要选成「あねさん／あねえさん」；轻松招呼客人的语气、音调和音质也要自然。"
        ),
        "new_take_indices": (1, 4),
    },
)


class BundleError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def load_object(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BundleError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise BundleError(f"JSON root must be an object: {path}")
    return value, digest(raw)


def load_marker_bound_object(path: Path) -> tuple[dict[str, Any], str]:
    value, actual_sha = load_object(path)
    if path.read_bytes() != canonical_bytes(value):
        raise BundleError(f"marker-bound JSON must use canonical bytes: {path}")
    marker_path = path.with_suffix(".sha256")
    try:
        marker = marker_path.read_bytes()
    except OSError as error:
        raise BundleError(f"cannot read SHA marker: {marker_path}") from error
    try:
        expected_sha = marker.decode("ascii")
    except UnicodeError as error:
        raise BundleError(f"SHA marker must be ASCII: {marker_path}") from error
    if expected_sha != actual_sha:
        raise BundleError(f"SHA marker mismatch: {marker_path}")
    return value, actual_sha


def unique(rows: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    if len(rows) != 1:
        raise BundleError(f"{label} must resolve to exactly one row")
    return rows[0]


def selected_take_id(
    manifest: dict[str, Any],
    *,
    scenario: str,
    line: str,
) -> str:
    row = unique(
        [
            item
            for item in manifest.get("curations", [])
            if isinstance(item, dict)
            and item.get("model") == MODEL
            and item.get("scenario") == scenario
            and item.get("line") == line
            and item.get("variant") == "dry"
            and item.get("decision") == "selected"
        ],
        label=f"current selection {scenario}/{line}",
    )
    take_id = row.get("take_id")
    if not isinstance(take_id, str) or len(take_id) != 64:
        raise BundleError(f"current selection has invalid take_id: {scenario}/{line}")
    matching_candidates = [
        item
        for item in manifest.get("candidates", [])
        if isinstance(item, dict)
        and item.get("model") == MODEL
        and item.get("scenario") == scenario
        and item.get("line") == line
        and item.get("variant") == "dry"
        and item.get("take_id") == take_id
    ]
    unique(matching_candidates, label=f"current candidate {scenario}/{line}")
    return take_id


def ledger_attempt(
    ledger: dict[str, Any],
    *,
    scenario: str,
    line: str,
    take_id: str | None = None,
    take_index: int | None = None,
) -> dict[str, Any]:
    rows = [
        item
        for item in ledger.get("attempts", [])
        if isinstance(item, dict)
        and item.get("model") == MODEL
        and item.get("scenario") == scenario
        and item.get("line") == line
        and item.get("variant") == "dry"
        and (take_id is None or item.get("take_id") == take_id)
        and (take_index is None or item.get("take_index") == take_index)
    ]
    return unique(rows, label=f"ledger attempt {scenario}/{line}")


def require_eligible_qc(
    report: dict[str, Any],
    *,
    scenario: str,
    line: str,
    take_id: str,
) -> None:
    attempt = unique(
        [
            item
            for item in report.get("attempts", [])
            if isinstance(item, dict)
            and item.get("model") == MODEL
            and item.get("scenario") == scenario
            and item.get("line") == line
            and item.get("variant") == "dry"
            and item.get("take_id") == take_id
        ],
        label=f"QC attempt {scenario}/{line}/{take_id}",
    )
    if attempt.get("status") != "eligible" or attempt.get("gates") != {
        "mechanical": "pass",
        "content": "review_required",
    }:
        raise BundleError(f"shortlisted take is not eligible: {take_id}")


def source_wav(run_root: Path, attempt: dict[str, Any]) -> tuple[Path, str]:
    audio = attempt.get("audio")
    if not isinstance(audio, dict):
        raise BundleError("ledger attempt has no audio object")
    relative = audio.get("wav_path")
    expected_sha = audio.get("wav_sha256")
    if not isinstance(relative, str) or not isinstance(expected_sha, str):
        raise BundleError("ledger attempt has invalid WAV binding")
    path = (run_root / relative).resolve()
    if not path.is_relative_to(run_root.resolve()) or not path.is_file():
        raise BundleError(f"ledger WAV escapes or is missing: {relative}")
    if file_digest(path) != expected_sha:
        raise BundleError(f"ledger WAV hash mismatch: {relative}")
    return path, expected_sha


def prepare_sources(
    manifest_path: Path,
    artifacts: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    if not manifest_path.is_file():
        raise BundleError(f"manifest does not exist: {manifest_path}")
    if not artifacts.is_dir():
        raise BundleError(f"artifacts root does not exist: {artifacts}")

    source_root = artifacts / "takes" / SOURCE_RUN_ID
    candidate_root = artifacts / "takes" / CANDIDATE_RUN_ID
    manifest, manifest_sha = load_object(manifest_path)
    source_ledger, source_ledger_sha = load_object(source_root / "ledger.json")
    candidate_ledger, candidate_ledger_sha = load_object(candidate_root / "ledger.json")
    source_qc, source_qc_sha = load_object(source_root / "qc-report.json")
    candidate_qc, candidate_qc_sha = load_object(candidate_root / "qc-report.json")
    _, source_candidate_set_sha = load_marker_bound_object(
        source_root / "candidate-set.json"
    )
    _, candidate_candidate_set_sha = load_marker_bound_object(
        candidate_root / "candidate-set.json"
    )

    if source_ledger.get("run_id") != SOURCE_RUN_ID:
        raise BundleError("source ledger run_id mismatch")
    if candidate_ledger.get("run_id") != CANDIDATE_RUN_ID:
        raise BundleError("candidate ledger run_id mismatch")
    if source_qc.get("run_id") != SOURCE_RUN_ID:
        raise BundleError("source QC run_id mismatch")
    if candidate_qc.get("run_id") != CANDIDATE_RUN_ID:
        raise BundleError("candidate QC run_id mismatch")

    prepared: list[dict[str, Any]] = []
    for target in TARGETS:
        scenario = str(target["scenario"])
        line = str(target["line"])
        current_take_id = selected_take_id(manifest, scenario=scenario, line=line)
        current_attempt = ledger_attempt(
            source_ledger,
            scenario=scenario,
            line=line,
            take_id=current_take_id,
        )
        require_eligible_qc(
            source_qc,
            scenario=scenario,
            line=line,
            take_id=current_take_id,
        )
        candidates = [
            {
                "source": "current-selected",
                "attempt": current_attempt,
                "run_root": source_root,
            }
        ]
        for take_index in target["new_take_indices"]:
            attempt = ledger_attempt(
                candidate_ledger,
                scenario=scenario,
                line=line,
                take_index=int(take_index),
            )
            take_id = attempt.get("take_id")
            if not isinstance(take_id, str):
                raise BundleError("candidate attempt has invalid take_id")
            require_eligible_qc(
                candidate_qc,
                scenario=scenario,
                line=line,
                take_id=take_id,
            )
            candidates.append(
                {
                    "source": f"new-take-{int(take_index):04d}",
                    "attempt": attempt,
                    "run_root": candidate_root,
                }
            )
        prepared.append({"target": target, "candidates": candidates})

    authority = {
        "protocol": "issue-192-targeted-listening-v1",
        "manifest_sha256": manifest_sha,
        "source_run": {
            "run_id": SOURCE_RUN_ID,
            "ledger_sha256": source_ledger_sha,
            "qc_report_sha256": source_qc_sha,
            "candidate_set_sha256": source_candidate_set_sha,
        },
        "candidate_run": {
            "run_id": CANDIDATE_RUN_ID,
            "ledger_sha256": candidate_ledger_sha,
            "qc_report_sha256": candidate_qc_sha,
            "candidate_set_sha256": candidate_candidate_set_sha,
        },
        "groups": [
            {
                "scenario": item["target"]["scenario"],
                "line": item["target"]["line"],
                "takes": [
                    {
                        "source": candidate["source"],
                        "take_id": candidate["attempt"]["take_id"],
                        "wav_sha256": candidate["attempt"]["audio"]["wav_sha256"],
                    }
                    for candidate in item["candidates"]
                ],
            }
            for item in prepared
        ],
    }
    study_id = digest(canonical_bytes(authority))
    return prepared, authority, study_id


def build(manifest_path: Path, artifacts: Path, output: Path) -> None:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise BundleError(f"output must be absent or empty: {output}")
    prepared, _authority, study_id = prepare_sources(manifest_path, artifacts)

    output.mkdir(parents=True, exist_ok=True)
    audio_root = output / "audio"
    audio_root.mkdir()
    groups: list[dict[str, Any]] = []
    for item in prepared:
        target = item["target"]
        group_identity = {
            "study_id": study_id,
            "model": MODEL,
            "scenario": target["scenario"],
            "line": target["line"],
        }
        group_id = digest(canonical_bytes(group_identity))
        candidates: list[dict[str, str]] = []
        for candidate in item["candidates"]:
            attempt = candidate["attempt"]
            source, wav_sha = source_wav(candidate["run_root"], attempt)
            candidate_id = digest(
                canonical_bytes(
                    {
                        **group_identity,
                        "source": candidate["source"],
                        "take_id": attempt["take_id"],
                        "wav_sha256": wav_sha,
                    }
                )
            )
            destination = audio_root / f"{candidate_id}.wav"
            shutil.copy2(source, destination)
            candidates.append(
                {
                    "id": candidate_id,
                    "variant": str(candidate["source"]),
                    "audio_path": f"audio/{candidate_id}.wav",
                    "audio_sha256": file_digest(destination),
                }
            )
        candidates.sort(key=lambda row: digest(f"{study_id}:{group_id}:{row['id']}".encode()))
        groups.append(
            {
                "id": group_id,
                "track": target["track"],
                "model": MODEL,
                "scenario": target["scenario"],
                "line": target["line"],
                "text": target["text"],
                "focus": target["focus"],
                "candidates": candidates,
            }
        )

    bundle = {
        "format_version": 1,
        "protocol": "baseline-quality-ab-bundle-v1",
        "study_id": study_id,
        "title": "#192 Irodori 两句定向复听",
        "instructions": "每题听完全部候选再选最好的一条；候选顺序已打乱，结果会自动保存。",
        "groups": groups,
    }
    raw = canonical_bytes(bundle)
    (output / BUNDLE_FILE).write_bytes(raw)
    (output / BUNDLE_FILE.replace(".json", ".sha256")).write_text(
        digest(raw),
        encoding="ascii",
    )
    print(f"wrote {len(groups)} groups / {sum(len(row['candidates']) for row in groups)} clips")
    print(f"study_id={study_id}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build(args.manifest.resolve(), args.artifacts.resolve(), args.output.resolve())
