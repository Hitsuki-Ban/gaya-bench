from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

from gaya_pipeline.japanese_reading import (
    JapaneseReadingError,
    contains_japanese_ideograph,
    find_ambiguous_japanese_readings,
    normalize_japanese_reading,
    resolve_japanese_reading,
)
from gaya_pipeline.manifest import ManifestError, load_manifest
from gaya_pipeline.validation import validate_scenarios


class QCError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeInspection:
    transcript: str
    average_log_probability: float | None
    prosody: Mapping[str, Any]


class QCRuntime(Protocol):
    def prepare(self) -> None: ...

    def describe(self) -> Mapping[str, Any]: ...

    def inspect(
        self,
        audio_path: Path,
        *,
        mora_count: int,
    ) -> RuntimeInspection: ...


@dataclass(frozen=True)
class QCSummary:
    output_path: Path
    clip_count: int
    pass_count: int
    mismatch_count: int
    needs_reading_count: int
    review_required_count: int
    analysis_error_count: int


@dataclass(frozen=True)
class _QCClipInput:
    clip: Mapping[str, Any]
    line: Mapping[str, Any]
    audio_path: Path
    expected_reading: Mapping[str, Any]


REPORT_FORMAT_VERSION = 1


def run_qc(
    *,
    manifest_path: Path,
    scenarios_dir: Path,
    artifacts_dir: Path,
    output_path: Path,
    runtime: QCRuntime,
    model_id: str | None = None,
    scenario_id: str | None = None,
    line_id: str | None = None,
) -> QCSummary:
    if line_id is not None and scenario_id is None:
        raise QCError("--line を指定する場合は --scenario も必要です。")

    manifest_path = manifest_path.resolve()
    manifest_sha256 = _file_sha256(manifest_path)
    catalog = _load_scenario_catalog(scenarios_dir)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as error:
        raise QCError(str(error)) from error
    if _file_sha256(manifest_path) != manifest_sha256:
        raise QCError("manifest が読み込み中に変更されました。")

    selected_clips = [
        clip
        for clip in manifest["clips"]
        if (model_id is None or clip["model"] == model_id)
        and (scenario_id is None or clip["scenario"] == scenario_id)
        and (line_id is None or clip["line"] == line_id)
    ]
    if not selected_clips:
        raise QCError("指定条件に一致する manifest clip がありません。")

    artifacts_dir = artifacts_dir.resolve()
    clip_inputs = _preflight_clips(
        selected_clips,
        catalog=catalog,
        artifacts_dir=artifacts_dir,
    )
    output_path = output_path.resolve()
    _validate_output_path(
        output_path,
        manifest_path=manifest_path,
        manifest_clips=manifest["clips"],
        artifacts_dir=artifacts_dir,
    )

    try:
        runtime.prepare()
        runtime_description = dict(runtime.describe())
    except Exception as error:
        raise QCError(f"QC runtime の準備に失敗しました: {error}") from error

    clip_reports: list[dict[str, Any]] = []
    counts = {
        "pass": 0,
        "mismatch": 0,
        "needs_reading": 0,
        "review_required": 0,
        "analysis_error": 0,
    }

    for clip_input in clip_inputs:
        clip = clip_input.clip
        report = _clip_identity(clip)
        try:
            mora_count = (
                count_japanese_mora(clip_input.expected_reading["normalized"])
                if clip_input.expected_reading["normalized"] is not None
                else 0
            )
            inspection = runtime.inspect(
                clip_input.audio_path,
                mora_count=mora_count,
            )
            _validate_audio_artifact(
                clip_input.audio_path,
                str(clip["sha256"]),
            )
            report.update(
                _evaluate_reading(
                    line=clip_input.line,
                    expected=clip_input.expected_reading,
                    inspection=inspection,
                ),
            )
            status = str(report["status"])
            counts[status] += 1
        except Exception as error:
            report["status"] = "analysis_error"
            report["qc_error"] = str(error)
            counts["analysis_error"] += 1
        clip_reports.append(report)

    if _file_sha256(manifest_path) != manifest_sha256:
        raise QCError("QC 実行中に manifest が変更されました。")
    for clip_input in clip_inputs:
        _validate_audio_artifact(
            clip_input.audio_path,
            str(clip_input.clip["sha256"]),
        )

    generated_at = datetime.now(timezone.utc).isoformat()
    is_full_manifest = (
        model_id is None
        and scenario_id is None
        and line_id is None
    )
    report_document = {
        "format_version": REPORT_FORMAT_VERSION,
        "algorithm_version": 1,
        "generated_at": generated_at,
        "source": {
            "manifest": manifest_path.as_posix(),
            "manifest_format_version": manifest["format_version"],
            "manifest_sha256": manifest_sha256,
            "manifest_generated_at": manifest["generated_at"],
            "clip_set": (
                "manifest.clips"
                if is_full_manifest
                else "manifest.clips.selection"
            ),
            "selection": {
                "coverage": "full" if is_full_manifest else "filtered",
                "model": model_id,
                "scenario": scenario_id,
                "line": line_id,
            },
        },
        "runtime": runtime_description,
        "summary": {
            "clip_count": len(selected_clips),
            "pass": counts["pass"],
            "mismatch": counts["mismatch"],
            "needs_reading": counts["needs_reading"],
            "review_required": counts["review_required"],
            "analysis_error": counts["analysis_error"],
        },
        "clips": clip_reports,
    }
    _atomic_write_json(output_path, report_document)
    return QCSummary(
        output_path=output_path,
        clip_count=len(selected_clips),
        pass_count=counts["pass"],
        mismatch_count=counts["mismatch"],
        needs_reading_count=counts["needs_reading"],
        review_required_count=counts["review_required"],
        analysis_error_count=counts["analysis_error"],
    )


def count_japanese_mora(reading: str) -> int:
    normalized = normalize_japanese_reading(reading)
    non_mora = frozenset("ァィゥェォャュョヮヵヶ")
    return sum(
        character not in non_mora
        for character in normalized
        if "\u30a1" <= character <= "\u30ff"
    )


def _load_scenario_catalog(
    scenarios_dir: Path,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    validation = validate_scenarios(scenarios_dir)
    if validation.problems:
        details = "\n".join(str(problem) for problem in validation.problems)
        raise QCError(f"scenario 検証に失敗しました:\n{details}")

    catalog: dict[tuple[str, str], Mapping[str, Any]] = {}
    for scenario_path in sorted(scenarios_dir.resolve().glob("*.yaml")):
        try:
            document = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise QCError(f"scenario を読み込めません: {scenario_path}") from error
        scenario_id = str(document["id"])
        for line in document["lines"]:
            catalog[(scenario_id, str(line["id"]))] = line
    return catalog


def _preflight_clips(
    clips: list[Mapping[str, Any]],
    *,
    catalog: Mapping[tuple[str, str], Mapping[str, Any]],
    artifacts_dir: Path,
) -> list[_QCClipInput]:
    inputs: list[_QCClipInput] = []
    for clip in clips:
        key = (str(clip["scenario"]), str(clip["line"]))
        line = catalog.get(key)
        if line is None:
            raise QCError(
                "manifest clip に対応する scenario line がありません: "
                f"{key[0]}/{key[1]}",
            )

        unresolved_path = artifacts_dir / str(clip["path"])
        try:
            audio_path = unresolved_path.resolve(strict=True)
        except OSError as error:
            raise QCError(
                f"audio artifact が存在しません: {unresolved_path}",
            ) from error
        if not audio_path.is_relative_to(artifacts_dir):
            raise QCError(
                f"audio artifact path が artifacts 外を参照しています: {unresolved_path}",
            )
        if not audio_path.is_file():
            raise QCError(f"audio artifact が通常ファイルではありません: {audio_path}")
        _validate_audio_artifact(audio_path, str(clip["sha256"]))
        inputs.append(
            _QCClipInput(
                clip=clip,
                line=line,
                audio_path=audio_path,
                expected_reading=_expected_reading(line),
            ),
        )
    return inputs


def _validate_output_path(
    output_path: Path,
    *,
    manifest_path: Path,
    manifest_clips: list[Mapping[str, Any]],
    artifacts_dir: Path,
) -> None:
    protected_paths = {
        manifest_path,
        *(
            (artifacts_dir / str(clip["path"])).resolve()
            for clip in manifest_clips
        ),
    }
    if output_path in protected_paths:
        raise QCError(
            "QC report の出力先は manifest または audio artifact と同一にできません: "
            f"{output_path}",
        )


def _expected_reading(line: Mapping[str, Any]) -> dict[str, Any]:
    ambiguous = find_ambiguous_japanese_readings(line["text"])
    explicit = line.get("reading")
    has_explicit = isinstance(explicit, str) and bool(explicit.strip())
    if ambiguous and not has_explicit:
        return {
            "text": None,
            "source": None,
            "normalized": None,
            "authoritative": False,
            "ambiguous_terms": [
                {
                    "surface": item.surface,
                    "candidates": list(item.candidates),
                }
                for item in ambiguous
            ],
        }
    try:
        reading = resolve_japanese_reading(
            text=line["text"],
            reading=explicit,
        )
    except JapaneseReadingError as error:
        raise QCError(str(error)) from error
    return {
        "text": reading.text,
        "source": reading.source,
        "normalized": normalize_japanese_reading(reading.text),
        "authoritative": has_explicit,
        "ambiguous_terms": [
            {
                "surface": item.surface,
                "candidates": list(item.candidates),
            }
            for item in ambiguous
        ],
    }


def _evaluate_reading(
    *,
    line: Mapping[str, Any],
    expected: Mapping[str, Any],
    inspection: RuntimeInspection,
) -> dict[str, Any]:
    expected_normalized = expected["normalized"]
    transcript = inspection.transcript.strip()
    asr_reading: str | None = None
    unresolved_reason: str | None = None

    if expected_normalized is None:
        unresolved_reason = "expected_reading_ambiguous"
    elif not transcript:
        asr_reading = ""
    elif contains_japanese_ideograph(transcript):
        ambiguous_surfaces = {
            item.surface
            for item in find_ambiguous_japanese_readings(line["text"])
        }
        if any(surface in transcript for surface in ambiguous_surfaces):
            unresolved_reason = "asr_orthography_ambiguous"
        else:
            asr_reading = resolve_japanese_reading(text=transcript).text
    else:
        asr_reading = transcript

    asr_normalized = (
        normalize_japanese_reading(asr_reading)
        if asr_reading is not None
        else None
    )
    if expected_normalized is None:
        status = "needs_reading"
        cer = None
        reading_mismatch = None
    elif unresolved_reason is not None:
        status = "review_required"
        cer = None
        reading_mismatch = None
    else:
        assert isinstance(expected_normalized, str)
        assert isinstance(asr_normalized, str)
        matches_expected = expected_normalized == asr_normalized
        if expected["authoritative"]:
            status = "pass" if matches_expected else "mismatch"
            reading_mismatch = not matches_expected
        else:
            status = "review_required"
            reading_mismatch = None
        cer = _character_error_rate(expected_normalized, asr_normalized)

    reason = unresolved_reason
    if status == "mismatch":
        reason = _ambiguous_mismatch_reason(
            expected_normalized,
            asr_normalized,
            expected["ambiguous_terms"],
        ) or "reading_differs"

    return {
        "status": status,
        "expected_reading": dict(expected),
        "asr": {
            "text": inspection.transcript,
            "normalized_reading": asr_normalized,
            "average_log_probability": inspection.average_log_probability,
        },
        "reading": {
            "character_error_rate": cer,
            "reading_mismatch": reading_mismatch,
            "reason": reason,
            "suggested_reading": (
                expected["text"] if status == "mismatch" else None
            ),
        },
        "prosody": dict(inspection.prosody),
    }


def _ambiguous_mismatch_reason(
    expected: str,
    actual: str,
    ambiguous_terms: Any,
) -> str | None:
    for term in ambiguous_terms:
        candidates = [
            normalize_japanese_reading(candidate)
            for candidate in term["candidates"]
        ]
        expected_candidates = [
            candidate for candidate in candidates if candidate in expected
        ]
        actual_candidates = [
            candidate for candidate in candidates if candidate in actual
        ]
        if (
            expected_candidates
            and actual_candidates
            and expected_candidates[0] != actual_candidates[0]
        ):
            return (
                f"ambiguous_reading:{term['surface']}:"
                f"{expected_candidates[0]}->{actual_candidates[0]}"
            )
    return None


def _character_error_rate(expected: str, actual: str) -> float:
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for expected_index, expected_character in enumerate(expected, start=1):
        current = [expected_index]
        for actual_index, actual_character in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[actual_index] + 1,
                    previous[actual_index - 1]
                    + (expected_character != actual_character),
                ),
            )
        previous = current
    return round(previous[-1] / len(expected), 6)


def _validate_audio_artifact(path: Path, expected_sha256: str) -> None:
    if not path.is_file():
        raise QCError(f"audio artifact が存在しません: {path}")
    actual_sha256 = _file_sha256(path)
    if actual_sha256 != expected_sha256:
        raise QCError(
            f"audio artifact の SHA-256 が manifest と一致しません: {path}",
        )


def _clip_identity(clip: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model": clip["model"],
        "scenario": clip["scenario"],
        "line": clip["line"],
        "variant": clip["variant"],
        "path": clip["path"],
        "audio_sha256": clip["sha256"],
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as input_file:
            for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise QCError(f"ファイルを読み込めません: {path}") from error
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    except (OSError, TypeError, ValueError) as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise QCError(f"QC report を書き込めません: {path}") from error
