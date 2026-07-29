from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from gaya_pipeline import qc_runtime
from gaya_pipeline.curation import CurationError, load_authoritative_candidate_lines
from gaya_pipeline.qc import count_japanese_mora
from gaya_pipeline.qc_report import QCAuthority, QCReportError, validate_qc_report
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.take_ledger import (
    TERMINAL_STATUSES,
    TakeLedgerError,
    read_ledger,
)


class IntonationReportError(RuntimeError):
    pass


@dataclass(frozen=True)
class IntonationReportSummary:
    output_dir: Path
    json_path: Path
    markdown_path: Path
    run_count: int
    eligible_attempt_count: int


@dataclass(frozen=True)
class _RunAuthority:
    run_id: str
    run_root: Path
    ledger: Mapping[str, Any]
    qc_authority: QCAuthority
    scenario_catalog: Mapping[tuple[str, str], Mapping[str, str]]
    input_provenance: dict[str, Any]


@dataclass(frozen=True)
class _AnalysisRuntime:
    ffmpeg: str
    ffmpeg_identity: Mapping[str, str]
    librosa: Any
    numpy: Any
    librosa_version: str
    numpy_version: str

    def identity(self) -> dict[str, Any]:
        return {
            "librosa": {
                "distribution": "librosa",
                "version": self.librosa_version,
            },
            "numpy": {
                "distribution": "numpy",
                "version": self.numpy_version,
            },
            "ffmpeg": dict(self.ffmpeg_identity),
        }

    def analyze(
        self,
        audio_path: Path,
        *,
        mora_count: int,
        final_intonation: str,
    ) -> dict[str, Any]:
        return _analyze_opus(
            audio_path,
            mora_count=mora_count,
            final_intonation=final_intonation,
            runtime=self,
        )


F0_FIELDS = (
    "median_hz",
    "p10_hz",
    "p90_hz",
    "semitone_std",
    "voiced_ratio",
    "final_raw_interval_semitones",
    "final_clipped_interval_semitones",
)
SPEAKER_KEY_FIELDS = ("model", "scenario", "character")
NUMPY_VERSION = "2.4.6"


def build_intonation_report(
    *,
    run_ids: Sequence[str],
    artifacts_dir: Path,
    scenarios_dir: Path,
    output_dir: Path,
) -> IntonationReportSummary:
    ordered_run_ids = sorted(run_ids)
    if not ordered_run_ids:
        raise IntonationReportError("run-id は1件以上必要です。")
    if len(set(ordered_run_ids)) != len(ordered_run_ids):
        raise IntonationReportError("run-id は重複できません。")

    artifacts_dir = artifacts_dir.resolve()
    scenarios_dir = scenarios_dir.resolve()
    output_dir = output_dir.resolve()
    if not artifacts_dir.is_dir():
        raise IntonationReportError(
            f"artifacts directory が存在しません: {artifacts_dir}",
        )
    if not scenarios_dir.is_dir():
        raise IntonationReportError(
            f"scenarios directory が存在しません: {scenarios_dir}",
        )
    if output_dir.exists():
        raise IntonationReportError(
            f"output directory は未作成である必要があります: {output_dir}",
        )
    if not output_dir.parent.is_dir():
        raise IntonationReportError(
            f"output parent directory が存在しません: {output_dir.parent}",
        )

    authorities = [
        _load_run_authority(
            run_id=run_id,
            artifacts_dir=artifacts_dir,
            scenarios_dir=scenarios_dir,
        )
        for run_id in ordered_run_ids
    ]
    runtime = _prepare_analysis_runtime()
    attempts = [
        attempt
        for authority in authorities
        for attempt in _analyze_run(authority, runtime=runtime)
    ]
    inputs = [authority.input_provenance for authority in authorities]

    attempts.sort(key=_attempt_sort_key)
    _apply_within_speaker_z(attempts)
    document = {
        "format_version": 1,
        "report": "final_intonation_distribution",
        "method": {
            "population": "eligible_attempts_only",
            "f0_fields": list(F0_FIELDS),
            "normalization": {
                "scope": "within_speaker",
                "speaker_key": list(SPEAKER_KEY_FIELDS),
                "mean": "arithmetic",
                "standard_deviation": "population",
                "null_policy": "n < 2 or population standard deviation = 0",
            },
        },
        "algorithm": {
            "sample_rate_hz": qc_runtime.SAMPLE_RATE_HZ,
            "f0_hop_length": qc_runtime.F0_HOP_LENGTH,
            "final_intonation_rise_anchor_semitones": (
                qc_runtime.FINAL_INTONATION_RISE_ANCHOR_SEMITONES
            ),
            "final_intonation_clip_semitones": (
                qc_runtime.FINAL_INTONATION_CLIP_SEMITONES
            ),
            "final_intonation_min_voiced_sec": (
                qc_runtime.FINAL_INTONATION_MIN_VOICED_SEC
            ),
            "final_intonation_max_window_sec": (
                qc_runtime.FINAL_INTONATION_MAX_WINDOW_SEC
            ),
            "runtime": runtime.identity(),
            "policy": "report_only",
        },
        "summary": {
            "run_count": len(inputs),
            "eligible_attempt_count": len(attempts),
            "model_gender_group_count": len(
                {(attempt["model"], attempt["gender"]) for attempt in attempts},
            ),
        },
        "inputs": inputs,
        "attempts": attempts,
        "distributions": _build_distributions(attempts),
    }
    _write_output_atomically(
        output_dir=output_dir,
        files={
            "intonation-report.json": (
                canonical_json(document) + "\n"
            ).encode("utf-8"),
            "intonation-report.md": _render_markdown(document).encode("utf-8"),
        },
    )
    return IntonationReportSummary(
        output_dir=output_dir,
        json_path=output_dir / "intonation-report.json",
        markdown_path=output_dir / "intonation-report.md",
        run_count=len(inputs),
        eligible_attempt_count=len(attempts),
    )


def _load_run_authority(
    *,
    run_id: str,
    artifacts_dir: Path,
    scenarios_dir: Path,
) -> _RunAuthority:
    run_root = artifacts_dir / "takes" / run_id
    ledger_path = run_root / "ledger.json"
    qc_path = run_root / "qc-report.json"
    try:
        ledger = read_ledger(ledger_path)
        if ledger["run_id"] != run_id:
            raise IntonationReportError(
                f"run-id が ledger と一致しません: {run_id}",
            )
        nonterminal = [
            attempt
            for attempt in ledger["attempts"]
            if attempt["status"] not in TERMINAL_STATUSES
        ]
        if nonterminal:
            statuses = sorted(
                {str(attempt["status"]) for attempt in nonterminal},
            )
            raise IntonationReportError(
                "intonation report には全 attempt が terminal の run が必要です: "
                f"{run_id}: nonterminal={statuses}",
            )
        qc_bytes = qc_path.read_bytes()
        qc_document = json.loads(qc_bytes.decode("utf-8"))
        authority = validate_qc_report(
            qc_document,
            ledger_path=ledger_path,
            ledger=ledger,
        )
        scenario_sha256, _ = load_authoritative_candidate_lines(
            scenarios_dir=scenarios_dir,
            ledger_source=ledger["source"],
        )
        scenario_catalog = _load_scenario_catalog(
            scenarios_dir=scenarios_dir,
            scenario_ids=sorted(
                {
                    str(group["scenario"])
                    for group in ledger["source"]["groups"]
                },
            ),
        )
        ledger_sha256 = _file_sha256(ledger_path)
    except IntonationReportError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TakeLedgerError,
        QCReportError,
        CurationError,
        yaml.YAMLError,
    ) as error:
        raise IntonationReportError(
            f"run authority を検証できません: {run_id}: {error}",
        ) from error

    eligible_count = sum(
        attempt["status"] == "eligible"
        for attempt in ledger["attempts"]
    )
    return _RunAuthority(
        run_id=run_id,
        run_root=run_root,
        ledger=ledger,
        qc_authority=authority,
        scenario_catalog=scenario_catalog,
        input_provenance={
            "run_id": run_id,
            "model": str(ledger["source"]["model"]),
            "scenario_sha256": scenario_sha256,
            "eligible_attempt_count": eligible_count,
            "ledger": {
                "path": f"takes/{run_id}/ledger.json",
                "sha256": ledger_sha256,
            },
            "qc_report": {
                "path": f"takes/{run_id}/qc-report.json",
                "sha256": hashlib.sha256(qc_bytes).hexdigest(),
                "gate_policy_version": authority.gate_policy_version,
            },
        },
    )


def _analyze_run(
    authority: _RunAuthority,
    *,
    runtime: _AnalysisRuntime,
) -> list[dict[str, Any]]:
    eligible = [
        attempt
        for attempt in authority.ledger["attempts"]
        if attempt["status"] == "eligible"
    ]
    rows: list[dict[str, Any]] = []
    for attempt in sorted(eligible, key=_ledger_attempt_sort_key):
        slot = tuple(
            attempt[key] for key in ("model", "scenario", "line", "variant")
        ) + (attempt["take_index"],)
        report_attempt = authority.qc_authority.attempts_by_slot[slot]
        scenario_line = authority.scenario_catalog.get(
            (str(attempt["scenario"]), str(attempt["line"])),
        )
        if scenario_line is None:
            raise IntonationReportError(
                "eligible attempt の line が current scenario にありません: "
                f"{authority.run_id}/{attempt['scenario']}/{attempt['line']}",
            )
        opus_path = _resolve_run_path(
            authority.run_root,
            str(attempt["audio"]["opus_path"]),
        )
        opus_sha256 = _file_sha256(opus_path)
        if opus_sha256 != attempt["audio"]["opus_sha256"]:
            raise IntonationReportError(
                "eligible attempt の Opus SHA-256 が ledger と一致しません: "
                f"{authority.run_id}/{attempt['scenario']}/{attempt['line']}/"
                f"take-{attempt['take_index']:04d}",
            )
        expected_reading = report_attempt["content"]["expected_reading"]["normalized"]
        prosody = runtime.analyze(
            opus_path,
            mora_count=count_japanese_mora(expected_reading),
            final_intonation=scenario_line["final_intonation"],
        )
        metrics, rise_anchor_met = _extract_f0_metrics(prosody)
        rows.append(
            {
                "run_id": authority.run_id,
                "model": str(attempt["model"]),
                "scenario": str(attempt["scenario"]),
                "line": str(attempt["line"]),
                "character": scenario_line["character"],
                "gender": scenario_line["gender"],
                "variant": str(attempt["variant"]),
                "take_index": int(attempt["take_index"]),
                "take_id": str(attempt["take_id"]),
                "expected_final_intonation": scenario_line["final_intonation"],
                "rise_anchor_met": rise_anchor_met,
                "unexpected_rise": (
                    None
                    if (
                        rise_anchor_met is None
                        or scenario_line["final_intonation"] != "fall"
                    )
                    else rise_anchor_met
                ),
                "audio": {
                    "path": str(attempt["audio"]["opus_path"]),
                    "sha256": opus_sha256,
                },
                "metrics": metrics,
            },
        )
    return rows


def _load_scenario_catalog(
    *,
    scenarios_dir: Path,
    scenario_ids: Sequence[str],
) -> dict[tuple[str, str], dict[str, str]]:
    catalog: dict[tuple[str, str], dict[str, str]] = {}
    for scenario_id in scenario_ids:
        path = scenarios_dir / f"{scenario_id}.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("id") != scenario_id:
            raise IntonationReportError(
                f"scenario source id が一致しません: {path}",
            )
        genders = {
            str(character["id"]): str(character["gender"])
            for character in document["characters"]
        }
        for line in document["lines"]:
            character = str(line["character"])
            final_intonation = (
                str(line["final_intonation"])
                if "final_intonation" in line
                else "fall"
            )
            catalog[(scenario_id, str(line["id"]))] = {
                "character": character,
                "gender": genders[character],
                "final_intonation": final_intonation,
            }
    return catalog


def _prepare_analysis_runtime() -> _AnalysisRuntime:
    librosa_version = _require_distribution_version(
        "librosa",
        qc_runtime.LIBROSA_VERSION,
    )
    numpy_version = _require_distribution_version("numpy", NUMPY_VERSION)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise IntonationReportError("ffmpeg が見つかりません。")
    try:
        numpy = importlib.import_module("numpy")
        librosa = importlib.import_module("librosa")
    except ImportError as error:
        raise IntonationReportError(
            "韻律解析依存がありません。"
            "uv sync --project pipeline --locked --extra qc を実行してください。",
        ) from error
    try:
        identity_result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-version",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise IntonationReportError(
            f"ffmpeg identity を取得できません: {ffmpeg}",
        ) from error
    version_lines = identity_result.stdout.splitlines()
    if not version_lines or not version_lines[0].startswith("ffmpeg version "):
        raise IntonationReportError(
            f"ffmpeg identity が不正です: {ffmpeg}",
        )
    return _AnalysisRuntime(
        ffmpeg=ffmpeg,
        ffmpeg_identity={
            "executable": Path(ffmpeg).resolve().as_posix(),
            "version": version_lines[0],
        },
        librosa=librosa,
        numpy=numpy,
        librosa_version=librosa_version,
        numpy_version=numpy_version,
    )


def _require_distribution_version(name: str, expected: str) -> str:
    try:
        actual = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError as error:
        raise IntonationReportError(
            "韻律解析依存がありません。"
            "uv sync --project pipeline --locked --extra qc を実行してください。",
        ) from error
    if actual != expected:
        raise IntonationReportError(
            f"{name} version が一致しません: "
            f"expected={expected}, actual={actual}",
        )
    return actual


def _analyze_opus(
    audio_path: Path,
    *,
    mora_count: int,
    final_intonation: str,
    runtime: _AnalysisRuntime,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                runtime.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                str(audio_path),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-ar",
                str(qc_runtime.SAMPLE_RATE_HZ),
                "-ac",
                "1",
                "-f",
                "f32le",
                "-",
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise IntonationReportError(
            f"Opus の mono 16kHz decode に失敗しました: {audio_path}",
        ) from error
    samples = runtime.numpy.frombuffer(
        result.stdout,
        dtype=runtime.numpy.float32,
    )
    if (
        samples.ndim != 1
        or samples.size == 0
        or not bool(runtime.numpy.isfinite(samples).all())
    ):
        raise IntonationReportError(f"decode 済み音声が不正です: {audio_path}")
    try:
        return qc_runtime.analyze_prosody_samples(
            samples,
            mora_count=mora_count,
            final_intonation=final_intonation,
            librosa_module=runtime.librosa,
            numpy_module=runtime.numpy,
        )
    except qc_runtime.QCRuntimeError as error:
        raise IntonationReportError(
            f"韻律解析に失敗しました: {audio_path}: {error}",
        ) from error


def _extract_f0_metrics(
    prosody: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], bool | None]:
    try:
        f0 = prosody["f0"]
        final = f0["final_intonation"]
        raw_values = {
            "median_hz": f0["median_hz"],
            "p10_hz": f0["p10_hz"],
            "p90_hz": f0["p90_hz"],
            "semitone_std": f0["semitone_std"],
            "voiced_ratio": f0["voiced_ratio"],
            "final_raw_interval_semitones": final["raw_interval_semitones"],
            "final_clipped_interval_semitones": final[
                "clipped_interval_semitones"
            ],
        }
        rise_anchor_met = final["rise_anchor_met"]
    except (KeyError, TypeError) as error:
        raise IntonationReportError("共有 prosody 結果の F0 契約が不正です。") from error
    if rise_anchor_met is not None and not isinstance(rise_anchor_met, bool):
        raise IntonationReportError(
            "共有 prosody 結果の rise_anchor_met が boolean/null ではありません。",
        )
    metrics: dict[str, dict[str, Any]] = {}
    for field in F0_FIELDS:
        value = raw_values[field]
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise IntonationReportError(
                f"共有 prosody 結果の {field} が有限数/null ではありません。",
            )
        metrics[field] = {
            "raw": None if value is None else round(float(value), 6),
            "z": None,
        }
    return metrics, rise_anchor_met


def _apply_within_speaker_z(attempts: list[dict[str, Any]]) -> None:
    speakers: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        speakers[
            (
                attempt["model"],
                attempt["scenario"],
                attempt["character"],
            )
        ].append(attempt)
    for speaker_attempts in speakers.values():
        for field in F0_FIELDS:
            measured = [
                attempt
                for attempt in speaker_attempts
                if attempt["metrics"][field]["raw"] is not None
            ]
            values = [
                float(attempt["metrics"][field]["raw"])
                for attempt in measured
            ]
            if len(values) < 2:
                continue
            mean = sum(values) / len(values)
            std = math.sqrt(
                sum((value - mean) ** 2 for value in values) / len(values),
            )
            if std == 0:
                continue
            for attempt, value in zip(measured, values, strict=True):
                attempt["metrics"][field]["z"] = round((value - mean) / std, 6)


def _build_distributions(
    attempts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        grouped[(str(attempt["model"]), str(attempt["gender"]))].append(attempt)

    distributions: list[dict[str, Any]] = []
    for (model, gender), group in sorted(grouped.items()):
        rise_measured = [
            attempt for attempt in group if attempt["rise_anchor_met"] is not None
        ]
        rise_hits = sum(
            attempt["rise_anchor_met"] is True for attempt in rise_measured
        )
        expected_fall = [
            attempt
            for attempt in group
            if attempt["expected_final_intonation"] == "fall"
            and attempt["unexpected_rise"] is not None
        ]
        unexpected_rise_count = sum(
            attempt["unexpected_rise"] is True for attempt in expected_fall
        )
        distributions.append(
            {
                "model": model,
                "gender": gender,
                "n": len(group),
                "rise_anchor": {
                    "measured_n": len(rise_measured),
                    "met_count": rise_hits,
                    "met_rate": _rate(rise_hits, len(rise_measured)),
                },
                "unexpected_rise": {
                    "measured_fall_n": len(expected_fall),
                    "count": unexpected_rise_count,
                    "rate": _rate(unexpected_rise_count, len(expected_fall)),
                },
                "fields": {
                    field: _field_distribution(group, field)
                    for field in F0_FIELDS
                },
            },
        )
    return distributions


def _field_distribution(
    attempts: Sequence[Mapping[str, Any]],
    field: str,
) -> dict[str, Any]:
    raw = [
        float(attempt["metrics"][field]["raw"])
        for attempt in attempts
        if attempt["metrics"][field]["raw"] is not None
    ]
    z = [
        float(attempt["metrics"][field]["z"])
        for attempt in attempts
        if attempt["metrics"][field]["z"] is not None
    ]
    return {"raw": _describe(raw), "z": _describe(z)}


def _describe(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "n": 0,
            "mean": None,
            "population_std": None,
            "min": None,
            "max": None,
        }
    mean = sum(values) / len(values)
    std = math.sqrt(
        sum((value - mean) ** 2 for value in values) / len(values),
    )
    return {
        "n": len(values),
        "mean": round(mean, 6),
        "population_std": round(std, 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
    }


def _render_markdown(document: Mapping[str, Any]) -> str:
    summary = document["summary"]
    lines = [
        "# 語尾イントネーション分布レポート",
        "",
        f"- run: {summary['run_count']}",
        f"- eligible attempt（実数）: {summary['eligible_attempt_count']}",
        f"- model × gender: {summary['model_gender_group_count']}",
        "",
        "## 方法",
        "",
        "ledger で `eligible` の attempt だけを集計した。F0 の各数値は "
        "`(model, scenario, character)` を話者キーとして、算術平均と母標準偏差で "
        "within-speaker z 正規化した。測定数が 2 未満、または母標準偏差が 0 の場合、"
        "z は `null` とした。",
        "",
        "語尾上昇は共有 QC 解析の `rise_anchor_met` を使用する。`unexpected rise` は "
        "期待値が `fall` の attempt に限って集計する。いずれも report-only で "
        "gate には使わない。",
        "",
        "## model × gender",
        "",
        "| model | gender | n | rise measured | rise hit | rise rate | "
        "fall measured | unexpected rise | unexpected rate |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for distribution in document["distributions"]:
        rise = distribution["rise_anchor"]
        unexpected = distribution["unexpected_rise"]
        lines.append(
            f"| {distribution['model']} | {distribution['gender']} | "
            f"{distribution['n']} | {rise['measured_n']} | {rise['met_count']} | "
            f"{_format_rate(rise['met_rate'])} | "
            f"{unexpected['measured_fall_n']} | {unexpected['count']} | "
            f"{_format_rate(unexpected['rate'])} |",
        )
    for distribution in document["distributions"]:
        lines.extend(
            [
                "",
                f"### {distribution['model']} × {distribution['gender']}",
                "",
                "| field | raw n | raw mean | raw std | z n | z mean | z std |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ],
        )
        for field in F0_FIELDS:
            values = distribution["fields"][field]
            lines.append(
                f"| {field} | {values['raw']['n']} | "
                f"{_format_number(values['raw']['mean'])} | "
                f"{_format_number(values['raw']['population_std'])} | "
                f"{values['z']['n']} | {_format_number(values['z']['mean'])} | "
                f"{_format_number(values['z']['population_std'])} |",
            )
    lines.extend(["", "## 入力 provenance", ""])
    for item in document["inputs"]:
        lines.extend(
            [
                f"### {item['run_id']}",
                "",
                f"- model: `{item['model']}`",
                f"- eligible attempt: {item['eligible_attempt_count']}",
                f"- scenario SHA-256: `{item['scenario_sha256']}`",
                f"- ledger SHA-256: `{item['ledger']['sha256']}`",
                f"- QC report SHA-256: `{item['qc_report']['sha256']}`",
                "",
            ],
        )
    return "\n".join(lines)


def _write_output_atomically(
    *,
    output_dir: Path,
    files: Mapping[str, bytes],
) -> None:
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.",
            dir=output_dir.parent,
        ),
    )
    try:
        for name, payload in files.items():
            (temporary / name).write_bytes(payload)
        os.replace(temporary, output_dir)
    except OSError as error:
        raise IntonationReportError(
            f"report output を原子的に確定できません: {output_dir}: {error}",
        ) from error
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _resolve_run_path(run_root: Path, relative: str) -> Path:
    resolved_root = run_root.resolve()
    resolved = (resolved_root / Path(relative)).resolve()
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise IntonationReportError(
            f"run artifact path が run root の範囲外です: {relative}",
        )
    if not resolved.is_file():
        raise IntonationReportError(f"run artifact がありません: {resolved}")
    return resolved


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise IntonationReportError(
            f"SHA-256 対象を読み込めません: {path}: {error}",
        ) from error
    return digest.hexdigest()


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _format_rate(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _format_number(value: float | None) -> str:
    return "—" if value is None else f"{value:.6f}"


def _ledger_attempt_sort_key(
    attempt: Mapping[str, Any],
) -> tuple[str, str, str, str, int]:
    return (
        str(attempt["model"]),
        str(attempt["scenario"]),
        str(attempt["line"]),
        str(attempt["variant"]),
        int(attempt["take_index"]),
    )


def _attempt_sort_key(
    attempt: Mapping[str, Any],
) -> tuple[str, str, str, str, str, int]:
    return (
        str(attempt["model"]),
        str(attempt["scenario"]),
        str(attempt["character"]),
        str(attempt["line"]),
        str(attempt["run_id"]),
        int(attempt["take_index"]),
    )
