from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from gaya_pipeline.japanese_reading import (
    normalize_japanese_reading,
    resolve_japanese_reading,
)
from gaya_pipeline.qc import count_japanese_mora


FORMAT_VERSION = 1
PROTOCOL = "issue-190-baseline-quality-v1"
IRODORI_MODEL = "irodori-tts-600m-v3-voicedesign"
SUPERTONIC_MODEL = "supertonic-3"


class AnalysisError(RuntimeError):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the deterministic Issue #190 baseline quality report.",
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--irodori-qc", type=Path, required=True)
    parser.add_argument("--supertonic-qc", type=Path, required=True)
    parser.add_argument("--source-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        raise AnalysisError(f"outputは新規pathが必要です: {output}")

    manifest = _load_json(args.manifest)
    irodori_qc = _load_json(args.irodori_qc)
    supertonic_qc = _load_json(args.supertonic_qc)
    source_audit = _load_json(args.source_audit)
    lines = _load_lines(args.scenarios)
    selected = _selected_candidates(manifest)
    rows = _release_rows(selected, lines)

    report = {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "sources": {
            "manifest": _source_receipt(args.manifest),
            "irodori_qc": _source_receipt(args.irodori_qc),
            "supertonic_qc": _source_receipt(args.supertonic_qc),
            "source_audit": _source_receipt(args.source_audit),
        },
        "release": _release_summary(manifest, rows),
        "model_rate_summary": _model_rate_summary(rows),
        "irodori": _irodori_report(rows, irodori_qc),
        "supertonic": _supertonic_report(rows, supertonic_qc),
        "reading": _reading_report(source_audit),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AnalysisError(f"JSONを読み込めません: {path}") from error
    if not isinstance(value, dict):
        raise AnalysisError(f"JSON rootはobjectが必要です: {path}")
    return value


def _source_receipt(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": path.as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
    }


def _load_lines(scenarios_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    catalog: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(scenarios_dir.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("id"), str):
            raise AnalysisError(f"scenarioが不正です: {path}")
        scenario_id = document["id"]
        characters = {
            character["id"]: character
            for character in document.get("characters", [])
            if isinstance(character, dict) and isinstance(character.get("id"), str)
        }
        for line in document.get("lines", []):
            if not isinstance(line, dict) or not isinstance(line.get("id"), str):
                raise AnalysisError(f"scenario lineが不正です: {path}")
            character = characters.get(line.get("character"))
            if character is None:
                raise AnalysisError(
                    f"characterが見つかりません: {scenario_id}/{line.get('id')}",
                )
            reading = resolve_japanese_reading(
                text=line["text"],
                reading=line.get("reading"),
            )
            normalized = normalize_japanese_reading(reading.text)
            key = (scenario_id, line["id"])
            if key in catalog:
                raise AnalysisError(f"scenario lineが重複しています: {key}")
            catalog[key] = {
                "scenario": scenario_id,
                "line": line["id"],
                "text": line["text"],
                "reading": normalized,
                "explicit_reading": isinstance(line.get("reading"), str)
                and bool(line["reading"].strip()),
                "mora_count": count_japanese_mora(normalized),
                "character": character["id"],
                "character_name": character["name"],
                "gender": character["gender"],
                "age": character["age"],
                "emotion": line["emotion"],
                "intensity": line["intensity"],
                "delivery": line["delivery"],
            }
    if len(catalog) != 161:
        raise AnalysisError(f"scenario lineは161件が必要です: actual={len(catalog)}")
    return catalog


def _selected_candidates(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates = manifest.get("candidates")
    curations = manifest.get("curations")
    if not isinstance(candidates, list) or not isinstance(curations, list):
        raise AnalysisError("manifest candidates/curationsが不正です。")
    by_take = {
        candidate["take_id"]: candidate
        for candidate in candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("take_id"), str)
    }
    selected: list[dict[str, Any]] = []
    for curation in curations:
        if not isinstance(curation, dict) or curation.get("decision") != "selected":
            continue
        candidate = by_take.get(curation.get("take_id"))
        if candidate is None:
            raise AnalysisError("selected curationのcandidateが見つかりません。")
        selected.append(candidate)
    if len(selected) != 1288:
        raise AnalysisError(f"selected candidateは1288件が必要です: actual={len(selected)}")
    return selected


def _release_rows(
    selected: Sequence[Mapping[str, Any]],
    lines: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in selected:
        key = (candidate["scenario"], candidate["line"])
        line = lines.get(key)
        if line is None:
            raise AnalysisError(f"candidateのlineが見つかりません: {key}")
        duration = _positive_number(candidate.get("duration_sec"), "duration_sec")
        mora_count = int(line["mora_count"])
        if mora_count <= 0:
            raise AnalysisError(f"mora_countが不正です: {key}")
        realized = candidate.get("gen_params", {}).get("realized")
        if not isinstance(realized, dict):
            raise AnalysisError(f"realizedが不正です: {candidate.get('take_id')}")
        rows.append(
            {
                **line,
                "model": candidate["model"],
                "take_id": candidate["take_id"],
                "duration_sec": duration,
                "overall_mora_per_sec": mora_count / duration,
                "realized": realized,
            },
        )

    line_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        line_groups[(row["scenario"], row["line"])].append(row)
    for key, group in line_groups.items():
        if len(group) != 8:
            raise AnalysisError(f"各lineは8モデルが必要です: {key}, actual={len(group)}")
        rates = [row["overall_mora_per_sec"] for row in group]
        durations = [row["duration_sec"] for row in group]
        for index, row in enumerate(group):
            other_rates = rates[:index] + rates[index + 1 :]
            other_durations = durations[:index] + durations[index + 1 :]
            row["rate_ratio_vs_other_models"] = row["overall_mora_per_sec"] / statistics.median(
                other_rates,
            )
            row["duration_ratio_vs_other_models"] = row["duration_sec"] / statistics.median(
                other_durations,
            )
            row["same_line_rate_rank"] = 1 + sum(
                value > row["overall_mora_per_sec"] for value in rates
            )
    return rows


def _release_summary(
    manifest: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    models = sorted({row["model"] for row in rows})
    lines = {(row["scenario"], row["line"]) for row in rows}
    return {
        "format_version": manifest.get("format_version"),
        "candidate_set_sha256": manifest.get("candidate_set_sha256"),
        "selected_count": len(rows),
        "model_count": len(models),
        "line_count": len(lines),
        "models": models,
    }


def _model_rate_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["model"])].append(row)
    result: list[dict[str, Any]] = []
    for model, group in sorted(groups.items()):
        result.append(
            {
                "model": model,
                "count": len(group),
                "overall_mora_per_sec": _distribution(
                    row["overall_mora_per_sec"] for row in group
                ),
                "rate_ratio_vs_other_models": _distribution(
                    row["rate_ratio_vs_other_models"] for row in group
                ),
                "fastest_same_line_count": sum(
                    row["same_line_rate_rank"] == 1 for row in group
                ),
            },
        )
    return result


def _irodori_report(
    rows: Sequence[Mapping[str, Any]],
    qc_report: Mapping[str, Any],
) -> dict[str, Any]:
    selected = [dict(row) for row in rows if row["model"] == IRODORI_MODEL]
    by_take = _qc_attempts(qc_report)
    enriched: list[dict[str, Any]] = []
    for row in selected:
        attempt = by_take.get(row["take_id"])
        if attempt is None:
            raise AnalysisError(f"Irodori QCにselected takeがありません: {row['take_id']}")
        prosody = attempt.get("content", {}).get("prosody")
        if not isinstance(prosody, dict):
            raise AnalysisError(f"Irodori prosodyがありません: {row['take_id']}")
        f0 = prosody.get("f0")
        energy = prosody.get("energy")
        if not isinstance(f0, dict) or not isinstance(energy, dict):
            raise AnalysisError(f"Irodori F0/energyがありません: {row['take_id']}")
        active_rate = _positive_number(prosody.get("active_mora_per_sec"), "active_mora_per_sec")
        p10 = _positive_number(f0.get("p10_hz"), "f0.p10_hz")
        p90 = _positive_number(f0.get("p90_hz"), "f0.p90_hz")
        energy_median = _finite_number(energy.get("median_dbfs"), "energy.median_dbfs")
        energy_p95 = _finite_number(energy.get("p95_dbfs"), "energy.p95_dbfs")
        final_intonation = f0.get("final_intonation")
        if not isinstance(final_intonation, dict):
            raise AnalysisError(f"Irodori final intonationがありません: {row['take_id']}")
        raw_final_interval = final_intonation.get("clipped_interval_semitones")
        final_interval = (
            _finite_number(
                raw_final_interval,
                "f0.final_intonation.clipped_interval_semitones",
            )
            if raw_final_interval is not None
            else None
        )
        final_rise = final_intonation.get("rise_anchor_met")
        if final_rise is not None and not isinstance(final_rise, bool):
            raise AnalysisError(f"Irodori final rise signalが不正です: {row['take_id']}")
        enriched.append(
            {
                **row,
                "active_mora_per_sec": active_rate,
                "active_mora_duration_ms": 1000.0 / active_rate,
                "active_speech_sec": _positive_number(
                    prosody.get("active_speech_sec"),
                    "active_speech_sec",
                ),
                "f0_semitone_std": _finite_number(f0.get("semitone_std"), "f0.semitone_std"),
                "f0_p10_p90_range_st": 12.0 * math.log2(p90 / p10),
                "energy_p95_minus_median_db": energy_p95 - energy_median,
                "final_f0_interval_st": final_interval,
                "final_rise_anchor_met": final_rise,
                "pause_internal_sec": _finite_number(
                    prosody.get("pause", {}).get("internal_total_sec"),
                    "pause.internal_total_sec",
                ),
            },
        )

    features = [
        "active_mora_per_sec",
        "active_mora_duration_ms",
        "duration_ratio_vs_other_models",
        "f0_semitone_std",
        "f0_p10_p90_range_st",
        "energy_p95_minus_median_db",
    ]
    return {
        "selected_count": len(selected),
        "qc_joined_count": len(enriched),
        "metrics": {feature: _distribution(row[feature] for row in enriched) for feature in features},
        "by_intensity": _group_metrics(enriched, "intensity", features),
        "by_emotion": _group_metrics(enriched, "emotion", features),
        "character_normalized_intensity_correlation": {
            feature: _character_normalized_correlation(enriched, feature)
            for feature in features
        },
        "final_intonation": {
            "measured_count": sum(row["final_f0_interval_st"] is not None for row in enriched),
            "unavailable_count": sum(
                row["final_f0_interval_st"] is None for row in enriched
            ),
            "clipped_interval_semitones": _distribution(
                row["final_f0_interval_st"]
                for row in enriched
                if row["final_f0_interval_st"] is not None
            ),
            "rise_anchor_met_count": sum(
                row["final_rise_anchor_met"] is True for row in enriched
            ),
        },
        "outliers": {
            "slowest_active_rate": _outliers(enriched, "active_mora_per_sec", reverse=False),
            "longest_active_mora_proxy": _outliers(
                enriched,
                "active_mora_duration_ms",
                reverse=True,
            ),
            "longest_vs_other_models": _outliers(
                enriched,
                "duration_ratio_vs_other_models",
                reverse=True,
            ),
            "widest_f0_range": _outliers(enriched, "f0_p10_p90_range_st", reverse=True),
            "largest_energy_dynamics": _outliers(
                enriched,
                "energy_p95_minus_median_db",
                reverse=True,
            ),
            "strongest_final_rise": _outliers(
                [row for row in enriched if row["final_f0_interval_st"] is not None],
                "final_f0_interval_st",
                reverse=True,
            ),
            "strongest_final_fall": _outliers(
                [row for row in enriched if row["final_f0_interval_st"] is not None],
                "final_f0_interval_st",
                reverse=False,
            ),
        },
        "vowel_duration_scope": {
            "measured": False,
            "proxy": "active_mora_duration_ms",
            "reason": "公開QCにはphoneme alignmentがないため、母音単位の持続時間を捏造しない。",
        },
    }


def _supertonic_report(
    rows: Sequence[Mapping[str, Any]],
    qc_report: Mapping[str, Any],
) -> dict[str, Any]:
    selected = [dict(row) for row in rows if row["model"] == SUPERTONIC_MODEL]
    by_take = _qc_attempts(qc_report)
    qc_rows: list[dict[str, Any]] = []
    speeds: Counter[str] = Counter()
    styles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        sampling = row["realized"].get("sampling")
        if not isinstance(sampling, dict):
            raise AnalysisError(f"Supertonic samplingがありません: {row['take_id']}")
        speed = _positive_number(sampling.get("speed"), "sampling.speed")
        speeds[_number_key(speed)] += 1
        style = row["realized"].get("voice_style")
        if not isinstance(style, str) or not style:
            raise AnalysisError(f"Supertonic voice_styleがありません: {row['take_id']}")
        styles[style].append(row)
        attempt = by_take.get(row["take_id"])
        if attempt is not None:
            prosody = attempt.get("content", {}).get("prosody")
            if not isinstance(prosody, dict):
                raise AnalysisError(f"Supertonic prosodyがありません: {row['take_id']}")
            qc_rows.append(
                {
                    **row,
                    "active_mora_per_sec": _positive_number(
                        prosody.get("active_mora_per_sec"),
                        "active_mora_per_sec",
                    ),
                },
            )
    return {
        "selected_count": len(selected),
        "realized_speed_counts": dict(sorted(speeds.items())),
        "overall_mora_per_sec": _distribution(
            row["overall_mora_per_sec"] for row in selected
        ),
        "rate_ratio_vs_other_models": _distribution(
            row["rate_ratio_vs_other_models"] for row in selected
        ),
        "fastest_same_line_count": sum(row["same_line_rate_rank"] == 1 for row in selected),
        "qc_joined_count": len(qc_rows),
        "qc_active_mora_per_sec": _distribution(
            row["active_mora_per_sec"] for row in qc_rows
        ),
        "by_voice_style": [
            {
                "voice_style": style,
                "count": len(group),
                "overall_mora_per_sec": _distribution(
                    row["overall_mora_per_sec"] for row in group
                ),
                "rate_ratio_vs_other_models": _distribution(
                    row["rate_ratio_vs_other_models"] for row in group
                ),
            }
            for style, group in sorted(styles.items())
        ],
        "fastest_outliers": _outliers(selected, "rate_ratio_vs_other_models", reverse=True),
    }


def _reading_report(source_audit: Mapping[str, Any]) -> dict[str, Any]:
    receipts = source_audit.get("conditioning_receipts")
    if not isinstance(receipts, list) or len(receipts) != 1288:
        raise AnalysisError("source audit conditioning_receiptsは1288件が必要です。")
    by_model: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for receipt in receipts:
        if not isinstance(receipt, dict) or not isinstance(receipt.get("reading"), dict):
            raise AnalysisError("source audit reading receiptが不正です。")
        by_model[receipt["model"]].append(receipt["reading"])
    return {
        "receipt_count": len(receipts),
        "by_model": [
            {
                "model": model,
                "count": len(group),
                "status_counts": dict(sorted(Counter(item.get("status") for item in group).items())),
                "reading_transport_counts": dict(
                    sorted(Counter(_nullable_key(item.get("reading_transport")) for item in group).items())
                ),
                "model_text_field_counts": dict(
                    sorted(Counter(_nullable_key(item.get("model_text_field")) for item in group).items())
                ),
            }
            for model, group in sorted(by_model.items())
        ],
    }


def _qc_attempts(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    attempts = report.get("attempts")
    if not isinstance(attempts, list):
        raise AnalysisError("QC report attemptsが不正です。")
    result: dict[str, Mapping[str, Any]] = {}
    for attempt in attempts:
        if not isinstance(attempt, dict) or not isinstance(attempt.get("take_id"), str):
            raise AnalysisError("QC attemptが不正です。")
        result[attempt["take_id"]] = attempt
    return result


def _group_metrics(
    rows: Sequence[Mapping[str, Any]],
    key: str,
    features: Sequence[str],
) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[key])].append(row)
    return [
        {
            key: value,
            "count": len(group),
            "metrics": {
                feature: _distribution(row[feature] for row in group)
                for feature in features
            },
        }
        for value, group in sorted(groups.items())
    ]


def _character_normalized_correlation(
    rows: Sequence[Mapping[str, Any]],
    feature: str,
) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["scenario"], row["character"])].append(row)
    pairs: list[tuple[float, float]] = []
    for group in groups.values():
        values = [float(row[feature]) for row in group]
        if len(values) < 2:
            continue
        deviation = statistics.pstdev(values)
        if deviation == 0:
            continue
        mean = statistics.fmean(values)
        pairs.extend(
            (float(row["intensity"]), (float(row[feature]) - mean) / deviation)
            for row in group
        )
    return {
        "n": len(pairs),
        "pearson_r": _round(_pearson(pairs)) if len(pairs) >= 2 else None,
        "normalization": "model+scenario+character arithmetic mean / population std",
    }


def _pearson(pairs: Sequence[tuple[float, float]]) -> float:
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    denominator = math.sqrt(
        sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys),
    )
    return numerator / denominator if denominator else 0.0


def _outliers(
    rows: Sequence[Mapping[str, Any]],
    feature: str,
    *,
    reverse: bool,
    limit: int = 12,
) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: float(row[feature]), reverse=reverse)[:limit]
    return [
        {
            "scenario": row["scenario"],
            "line": row["line"],
            "character": row["character"],
            "character_name": row["character_name"],
            "text": row["text"],
            "emotion": row["emotion"],
            "intensity": row["intensity"],
            "delivery": row["delivery"],
            "take_id": row["take_id"],
            feature: _round(float(row[feature])),
        }
        for row in ordered
    ]


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    data = sorted(float(value) for value in values)
    if not data or not all(math.isfinite(value) for value in data):
        raise AnalysisError("distributionには有限値が必要です。")
    return {
        "count": len(data),
        "min": _round(data[0]),
        "p10": _round(_percentile(data, 0.1)),
        "median": _round(statistics.median(data)),
        "mean": _round(statistics.fmean(data)),
        "p90": _round(_percentile(data, 0.9)),
        "max": _round(data[-1]),
    }


def _percentile(data: Sequence[float], quantile: float) -> float:
    position = (len(data) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return data[lower]
    weight = position - lower
    return data[lower] * (1.0 - weight) + data[upper] * weight


def _positive_number(value: Any, field: str) -> float:
    number = _finite_number(value, field)
    if number <= 0:
        raise AnalysisError(f"{field}は正数が必要です。")
    return number


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnalysisError(f"{field}はnumberが必要です。")
    number = float(value)
    if not math.isfinite(number):
        raise AnalysisError(f"{field}は有限値が必要です。")
    return number


def _number_key(value: float) -> str:
    return format(value, ".6g")


def _nullable_key(value: Any) -> str:
    return "null" if value is None else str(value)


def _round(value: float) -> float:
    return round(value, 6)


if __name__ == "__main__":
    main()
