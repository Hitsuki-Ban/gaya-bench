from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import statistics
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import librosa
import numpy

from gaya_pipeline.qc_runtime import SAMPLE_RATE_HZ, analyze_prosody_samples


FORMAT_VERSION = 1
PROTOCOL = "issue-190-ab-measurement-v1"
BASELINE_VARIANT = {
    "irodori": "current",
    "supertonic": "speed-1.05",
    "aivis": "explicit-reading-r1",
    "cosyvoice": "explicit-reading",
}


class MeasurementError(RuntimeError):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure the deterministic Issue #190 A/B canaries.",
    )
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        raise MeasurementError(f"output must be a new path: {output}")
    index = _load_json(args.index)
    track = index.get("track")
    if track not in BASELINE_VARIANT:
        raise MeasurementError(f"unsupported track: {track!r}")
    entries = index.get("entries")
    if not isinstance(entries, list) or not entries:
        raise MeasurementError("index entries are invalid")

    measured = [_measure_entry(args.index.parent, entry) for entry in entries]
    comparisons = _comparisons(str(track), measured)
    report = {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "track": track,
        "source_index": _receipt(args.index),
        "entry_count": len(measured),
        "entries": measured,
        "comparison_summary": _comparison_summary(comparisons),
        "comparisons": comparisons,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _measure_entry(root: Path, entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise MeasurementError("index entry is invalid")
    relative = entry.get("wav_path")
    if not isinstance(relative, str):
        raise MeasurementError("entry wav_path is invalid")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise MeasurementError(f"entry WAV is invalid: {relative}")
    if _sha256(path) != entry.get("wav_sha256"):
        raise MeasurementError(f"entry WAV SHA mismatch: {relative}")
    mora_count = entry.get("mora_count")
    if isinstance(mora_count, bool) or not isinstance(mora_count, int) or mora_count <= 0:
        raise MeasurementError(f"entry mora_count is invalid: {relative}")
    samples = _decode(path)
    prosody = analyze_prosody_samples(
        samples,
        mora_count=mora_count,
        final_intonation="free",
        librosa_module=librosa,
        numpy_module=numpy,
    )
    f0 = prosody["f0"]
    energy = prosody["energy"]
    p10 = f0["p10_hz"]
    p90 = f0["p90_hz"]
    f0_range = (
        12.0 * math.log2(float(p90) / float(p10))
        if isinstance(p10, (int, float))
        and isinstance(p90, (int, float))
        and p10 > 0
        and p90 > 0
        else None
    )
    median_energy = energy["median_dbfs"]
    p95_energy = energy["p95_dbfs"]
    energy_dynamic = (
        float(p95_energy) - float(median_energy)
        if isinstance(median_energy, (int, float))
        and isinstance(p95_energy, (int, float))
        else None
    )
    return {
        "model": entry["model"],
        "scenario": entry["scenario"],
        "line": entry["line"],
        "variant": entry["variant"],
        "wav_sha256": entry["wav_sha256"],
        "prosody": prosody,
        "derived": {
            "f0_p10_p90_range_st": _rounded(f0_range),
            "energy_p95_minus_median_db": _rounded(energy_dynamic),
        },
    }


def _comparisons(track: str, entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for entry in entries:
        groups[(str(entry["scenario"]), str(entry["line"]))].append(entry)
    baseline_variant = BASELINE_VARIANT[track]
    result: list[dict[str, Any]] = []
    for key, group in sorted(groups.items()):
        baseline = [entry for entry in group if entry["variant"] == baseline_variant]
        if len(baseline) != 1:
            raise MeasurementError(f"baseline variant is not unique: {key}")
        reference = baseline[0]
        for entry in group:
            if entry is reference:
                continue
            result.append(
                {
                    "scenario": key[0],
                    "line": key[1],
                    "baseline_variant": baseline_variant,
                    "variant": entry["variant"],
                    "duration_ratio": _ratio(
                        entry["prosody"]["duration_sec"],
                        reference["prosody"]["duration_sec"],
                    ),
                    "active_mora_per_sec_ratio": _ratio(
                        entry["prosody"]["active_mora_per_sec"],
                        reference["prosody"]["active_mora_per_sec"],
                    ),
                    "f0_semitone_std_delta": _delta(
                        entry["prosody"]["f0"]["semitone_std"],
                        reference["prosody"]["f0"]["semitone_std"],
                    ),
                    "f0_p10_p90_range_st_delta": _delta(
                        entry["derived"]["f0_p10_p90_range_st"],
                        reference["derived"]["f0_p10_p90_range_st"],
                    ),
                    "baseline_final_f0_interval_st": _final_f0_interval(reference),
                    "variant_final_f0_interval_st": _final_f0_interval(entry),
                    "final_f0_interval_st_delta": _delta(
                        _final_f0_interval(entry),
                        _final_f0_interval(reference),
                    ),
                    "baseline_final_rise_anchor_met": _final_rise(reference),
                    "variant_final_rise_anchor_met": _final_rise(entry),
                    "energy_dynamic_db_delta": _delta(
                        entry["derived"]["energy_p95_minus_median_db"],
                        reference["derived"]["energy_p95_minus_median_db"],
                    ),
                },
            )
    return result


def _comparison_summary(comparisons: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for comparison in comparisons:
        groups[str(comparison["variant"])].append(comparison)
    metrics = (
        "duration_ratio",
        "active_mora_per_sec_ratio",
        "f0_semitone_std_delta",
        "f0_p10_p90_range_st_delta",
        "final_f0_interval_st_delta",
        "energy_dynamic_db_delta",
    )
    return [
        {
            "variant": variant,
            "count": len(group),
            "metrics": {
                metric: _distribution(item[metric] for item in group)
                for metric in metrics
            },
            "final_rise_transitions": dict(
                sorted(
                    Counter(
                        f"{item['baseline_final_rise_anchor_met']}->{item['variant_final_rise_anchor_met']}"
                        for item in group
                    ).items()
                )
            ),
        }
        for variant, group in sorted(groups.items())
    ]


def _final_f0_interval(entry: Mapping[str, Any]) -> float | None:
    value = entry["prosody"]["f0"]["final_intonation"]["clipped_interval_semitones"]
    return _rounded(float(value)) if isinstance(value, (int, float)) else None


def _final_rise(entry: Mapping[str, Any]) -> bool | None:
    value = entry["prosody"]["f0"]["final_intonation"]["rise_anchor_met"]
    return value if isinstance(value, bool) else None


def _decode(path: Path) -> Any:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise MeasurementError("ffmpeg is not available")
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(path),
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
        check=True,
        capture_output=True,
    )
    samples = numpy.frombuffer(result.stdout, dtype=numpy.float32)
    if samples.ndim != 1 or samples.size == 0 or not bool(numpy.isfinite(samples).all()):
        raise MeasurementError(f"decoded samples are invalid: {path}")
    return samples


def _distribution(values: Iterable[Any]) -> dict[str, Any]:
    data = sorted(float(value) for value in values if isinstance(value, (int, float)))
    if not data or not all(math.isfinite(value) for value in data):
        raise MeasurementError("comparison distribution is invalid")
    return {
        "count": len(data),
        "min": _rounded(data[0]),
        "median": _rounded(statistics.median(data)),
        "mean": _rounded(statistics.fmean(data)),
        "max": _rounded(data[-1]),
    }


def _ratio(value: Any, baseline: Any) -> float | None:
    if not isinstance(value, (int, float)) or not isinstance(baseline, (int, float)):
        return None
    if baseline == 0:
        return None
    return _rounded(float(value) / float(baseline))


def _delta(value: Any, baseline: Any) -> float | None:
    if not isinstance(value, (int, float)) or not isinstance(baseline, (int, float)):
        return None
    return _rounded(float(value) - float(baseline))


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MeasurementError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise MeasurementError(f"JSON root must be an object: {path}")
    return value


def _receipt(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": path.as_posix(),
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
