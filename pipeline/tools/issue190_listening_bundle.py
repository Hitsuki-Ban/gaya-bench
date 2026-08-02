"""Build the compact blinded listening bundle for Issue #190 research."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any


BUNDLE_FILE = "baseline-quality-ab-bundle-v1.json"
TRACKS = (
    ("irodori", "irodori-caption", 12),
    ("supertonic", "supertonic-speed", 10),
    ("cosyvoice", "cosyvoice-reading", 10),
)
FOCUS = {
    "irodori-caption": "哪一个演得更自然、不刻意拖长？角色声线也应保持合适。",
    "supertonic-speed": "哪一个语速更像自然的日语 NPC 台词，不显得赶？",
    "cosyvoice-reading": "哪一个日语发音和语调更自然？不要因汉字读法而变得僵硬。",
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def comparison_scores(track_root: Path) -> dict[tuple[str, str], float]:
    metrics = load_json(track_root / "metrics-v2.json")
    scores: dict[tuple[str, str], float] = defaultdict(float)
    for row in metrics["comparisons"]:
        duration_ratio = float(row["duration_ratio"])
        f0_delta = float(row["f0_p10_p90_range_st_delta"])
        score = abs(math.log(duration_ratio)) + min(abs(f0_delta), 24.0) / 48.0
        key = (str(row["scenario"]), str(row["line"]))
        scores[key] = max(scores[key], score)
    return scores


def selected_groups(track_root: Path, limit: int) -> list[list[dict[str, Any]]]:
    index = load_json(track_root / "index.json")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in index["entries"]:
        grouped[(str(entry["scenario"]), str(entry["line"]))].append(entry)
    scores = comparison_scores(track_root)
    ordered = sorted(grouped, key=lambda key: (-scores[key], key))[:limit]
    return [grouped[key] for key in ordered]


def build(source: Path, output: Path) -> None:
    if not source.is_dir():
        raise ValueError(f"source directory does not exist: {source}")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory must be absent or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    audio_root = output / "audio"
    audio_root.mkdir()

    all_groups: list[tuple[str, list[dict[str, Any]], Path]] = []
    candidate_set_sha256: str | None = None
    for source_name, track, limit in TRACKS:
        track_root = source / source_name
        index = load_json(track_root / "index.json")
        current_sha = str(index["candidate_set_sha256"])
        if candidate_set_sha256 is None:
            candidate_set_sha256 = current_sha
        elif current_sha != candidate_set_sha256:
            raise ValueError("track indexes do not share one frozen candidate set")
        all_groups.extend((track, group, track_root) for group in selected_groups(track_root, limit))

    study_contract = {
        "candidate_set_sha256": candidate_set_sha256,
        "protocol": "issue-190-listening-selection-v2",
        "tracks": TRACKS,
        "selected_audio": [
            {
                "track": track,
                "scenario": entries[0]["scenario"],
                "line": entries[0]["line"],
                "variants": [
                    {"variant": entry["variant"], "wav_sha256": entry["wav_sha256"]}
                    for entry in entries
                ],
            }
            for track, entries, _track_root in all_groups
        ],
    }
    study_id = digest(canonical_bytes(study_contract))
    bundle_groups: list[dict[str, Any]] = []
    for track, entries, track_root in all_groups:
        first = entries[0]
        identity = {
            "track": track,
            "model": first["model"],
            "scenario": first["scenario"],
            "line": first["line"],
        }
        group_id = digest(canonical_bytes(identity))
        candidates: list[dict[str, str]] = []
        for entry in entries:
            variant = str(entry["variant"])
            candidate_id = digest(
                canonical_bytes(
                    {**identity, "variant": variant, "wav_sha256": entry["wav_sha256"]}
                )
            )
            source_wav = track_root / str(entry["wav_path"])
            if digest(source_wav.read_bytes()) != entry["wav_sha256"]:
                raise ValueError(f"source WAV hash mismatch: {source_wav}")
            target = audio_root / f"{candidate_id}.wav"
            shutil.copy2(source_wav, target)
            candidates.append(
                {
                    "id": candidate_id,
                    "variant": variant,
                    "audio_path": f"audio/{candidate_id}.wav",
                    "audio_sha256": digest(target.read_bytes()),
                }
            )
        candidates.sort(key=lambda item: digest(f"{study_id}:{group_id}:{item['id']}".encode()))
        bundle_groups.append(
            {
                "id": group_id,
                "track": track,
                "model": first["model"],
                "scenario": first["scenario"],
                "line": first["line"],
                "text": first["text"],
                "focus": FOCUS[track],
                "candidates": candidates,
            }
        )

    bundle_groups.sort(key=lambda group: (next(i for i, row in enumerate(TRACKS) if row[1] == group["track"]), group["id"]))
    bundle = {
        "format_version": 1,
        "protocol": "baseline-quality-ab-bundle-v1",
        "study_id": study_id,
        "title": "#190 基线质量盲听",
        "instructions": "每组只按顶部的一句话判断；候选顺序已打乱，结果会自动保存。",
        "groups": bundle_groups,
    }
    raw = canonical_bytes(bundle)
    (output / BUNDLE_FILE).write_bytes(raw)
    (output / BUNDLE_FILE.replace(".json", ".sha256")).write_text(digest(raw), encoding="ascii")
    print(f"wrote {len(bundle_groups)} groups / {sum(len(g['candidates']) for g in bundle_groups)} clips")
    print(f"study_id={study_id}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    build(args.source.resolve(), args.output.resolve())
