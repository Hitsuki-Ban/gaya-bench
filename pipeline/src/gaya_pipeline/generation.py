from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from gaya_pipeline.adapters import UnknownAdapterError, create_adapter
from gaya_pipeline.adapters.base import Adapter, LineJob
from gaya_pipeline.audio import (
    AudioProcessingError,
    AudioTools,
    EncodedLoudnessReport,
    PostprocessProfile,
    encode_opus,
    find_audio_tools,
    measure_encoded_opus,
    normalize_wav,
    probe_audio,
)
from gaya_pipeline.manifest import (
    ManifestError,
    load_manifest,
    update_manifest,
)
from gaya_pipeline.validation import validate_scenarios


class GenerationError(RuntimeError):
    pass


METADATA_KEYS = {
    "format_version",
    "model",
    "scenario",
    "line",
    "variant",
    "input_hash",
    "wav_sha256",
    "opus_sha256",
    "duration_sec",
    "generation_seconds",
    "rtf",
    "gen_params",
    "postprocess",
    "loudness",
}


@dataclass(frozen=True)
class GenerationRecord:
    scenario_id: str
    line_id: str
    status: Literal["generated", "skipped"]
    generation_seconds: float
    rtf: float


@dataclass(frozen=True)
class GenerationFailureRecord:
    scenario_id: str
    line_id: str
    message: str


@dataclass(frozen=True)
class GenerationSummary:
    records: tuple[GenerationRecord, ...]
    failures: tuple[GenerationFailureRecord, ...]
    elapsed_seconds: float
    manifest_updated: bool

    @property
    def generated_count(self) -> int:
        return sum(record.status == "generated" for record in self.records)

    @property
    def skipped_count(self) -> int:
        return sum(record.status == "skipped" for record in self.records)

    @property
    def failed_count(self) -> int:
        return len(self.failures)


def run_generation(
    *,
    model_id: str,
    scenarios_dir: Path,
    artifacts_dir: Path,
    manifest_path: Path,
    scenario_id: str | None = None,
    line_id: str | None = None,
    force: bool = False,
) -> GenerationSummary:
    if line_id is not None and scenario_id is None:
        raise GenerationError("--line には --scenario が必要です。")

    validation = validate_scenarios(scenarios_dir)
    if validation.problems:
        details = "\n".join(str(problem) for problem in validation.problems)
        raise GenerationError(f"シナリオ検証に失敗しました:\n{details}")

    try:
        manifest = load_manifest(manifest_path)
        tools = find_audio_tools()
        jobs = _load_jobs(
            scenarios_dir,
            scenario_id=scenario_id,
            line_id=line_id,
        )
        profile = PostprocessProfile()
    except (
        AudioProcessingError,
        ManifestError,
        OSError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ) as error:
        raise GenerationError(str(error)) from error

    started_at = time.perf_counter()
    try:
        adapter = create_adapter(model_id)
        requested_params = dict(adapter.generation_params())
        _canonical_json(requested_params)
    except UnknownAdapterError as error:
        raise GenerationError(str(error)) from error
    except Exception as error:
        raise GenerationError(
            f"adapter 初期化に失敗しました: {error}",
        ) from error
    try:
        adapter.prepare(
            jobs,
            artifacts_dir,
            scenarios_dir.parent / "assets" / "voices",
        )
    except Exception as error:
        raise GenerationError(
            f"adapter 準備に失敗しました: {error}",
        ) from error

    records: list[GenerationRecord] = []
    failures: list[GenerationFailureRecord] = []
    clips: list[dict[str, Any]] = []
    manifest_updated = False

    for job in jobs:
        retry_failed_result = _manifest_has_failure(
            manifest,
            model_id=adapter.profile.id,
            scenario_id=job.scenario_id,
            line_id=job.line_id,
        )
        try:
            record, clip = _process_job(
                adapter=adapter,
                job=job,
                tools=tools,
                profile=profile,
                requested_params=requested_params,
                artifacts_dir=artifacts_dir,
                force=force or retry_failed_result,
            )
        except (
            AudioProcessingError,
            GenerationError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            failure = _failure_result(adapter.profile.id, job)
            try:
                changed = update_manifest(
                    manifest_path,
                    manifest,
                    adapter.profile,
                    [],
                    [failure],
                    replace_model_results=False,
                    replace_scenario_results=None,
                )
                manifest_updated = manifest_updated or changed
                if changed:
                    manifest = load_manifest(manifest_path)
            except (ManifestError, OSError, TypeError, ValueError) as write_error:
                raise GenerationError(
                    f"{job.scenario_id}/{job.line_id}: {error}; "
                    f"manifest への失敗記録にも失敗しました: {write_error}",
                ) from error
            failures.append(
                GenerationFailureRecord(
                    scenario_id=job.scenario_id,
                    line_id=job.line_id,
                    message=str(error),
                ),
            )
            continue
        records.append(record)
        clips.append(clip)
        try:
            changed = update_manifest(
                manifest_path,
                manifest,
                adapter.profile,
                [clip],
                [],
                replace_model_results=False,
                replace_scenario_results=None,
            )
            manifest_updated = manifest_updated or changed
            if changed:
                manifest = load_manifest(manifest_path)
        except (ManifestError, OSError, TypeError, ValueError) as error:
            raise GenerationError(
                f"{job.scenario_id}/{job.line_id}: {error}",
            ) from error

    if not failures:
        try:
            final_manifest_updated = update_manifest(
                manifest_path,
                manifest,
                adapter.profile,
                clips,
                [],
                replace_model_results=scenario_id is None,
                replace_scenario_results=scenario_id if line_id is None else None,
            )
        except (ManifestError, OSError, TypeError, ValueError) as error:
            raise GenerationError(str(error)) from error
        manifest_updated = manifest_updated or final_manifest_updated

    return GenerationSummary(
        records=tuple(records),
        failures=tuple(failures),
        elapsed_seconds=time.perf_counter() - started_at,
        manifest_updated=manifest_updated,
    )


def _load_jobs(
    scenarios_dir: Path,
    *,
    scenario_id: str | None,
    line_id: str | None,
) -> list[LineJob]:
    documents: list[dict[str, Any]] = []
    for scenario_path in sorted(scenarios_dir.glob("*.yaml")):
        document = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise GenerationError(
                f"シナリオが object ではありません: {scenario_path}",
            )
        if scenario_id is None or document["id"] == scenario_id:
            documents.append(document)

    if not documents:
        raise GenerationError(f"scenario id が見つかりません: {scenario_id}")

    jobs: list[LineJob] = []
    for document in documents:
        scene = {
            "id": document["id"],
            "title": document["title"],
            **document["scene"],
        }
        if "tags" in document:
            scene["tags"] = document["tags"]
        characters = {
            character["id"]: character for character in document["characters"]
        }
        for line in document["lines"]:
            if line_id is not None and line["id"] != line_id:
                continue
            jobs.append(
                LineJob(
                    scene=scene,
                    character=characters[line["character"]],
                    line=line,
                    locale=document["locale"],
                ),
            )

    if not jobs:
        raise GenerationError(
            f"line id が見つかりません: {scenario_id}/{line_id}",
        )
    return jobs


def _process_job(
    *,
    adapter: Adapter,
    job: LineJob,
    tools: AudioTools,
    profile: PostprocessProfile,
    requested_params: dict[str, Any],
    artifacts_dir: Path,
    force: bool,
) -> tuple[GenerationRecord, dict[str, Any]]:
    output_dir = artifacts_dir / "audio" / adapter.profile.id / job.scenario_id
    base_name = f"{job.line_id}-dry"
    normalized_wav = output_dir / f"{base_name}.wav"
    opus_path = output_dir / f"{base_name}.opus"
    metadata_path = output_dir / f"{base_name}.json"
    source_wav = output_dir / f".{job.line_id}-source.wav"
    pending_wav = output_dir / f".{base_name}.pending.wav"
    pending_opus = output_dir / f".{base_name}.pending.opus"
    pending_metadata = output_dir / f".{base_name}.pending.json"
    input_hash = _generation_hash(
        adapter=adapter,
        job=job,
        requested_params=requested_params,
        profile=profile,
    )

    metadata = None if force else _read_metadata(metadata_path)
    if (
        not force
        and metadata is not None
        and _metadata_matches(
            metadata,
            input_hash=input_hash,
            normalized_wav=normalized_wav,
            opus_path=opus_path,
            adapter=adapter,
            job=job,
            requested_params=requested_params,
            profile=profile,
        )
    ):
        return _record_and_clip_from_metadata(
            adapter.profile.id,
            job,
            metadata,
            profile,
            status="skipped",
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    for temporary_path in (
        source_wav,
        pending_wav,
        pending_opus,
        pending_metadata,
    ):
        temporary_path.unlink(missing_ok=True)
    try:
        generation_started = time.perf_counter()
        try:
            realized_params = dict(adapter.generate(job, source_wav))
        except Exception as error:
            raise GenerationError(
                f"adapter 生成に失敗しました: {error}",
            ) from error
        generation_seconds = time.perf_counter() - generation_started
        _canonical_json(realized_params)

        source_probe = probe_audio(tools, source_wav)
        if not source_probe.codec_name.startswith("pcm_"):
            raise GenerationError(
                "adapter 出力は PCM WAV である必要があります。",
            )
        rtf = generation_seconds / source_probe.duration_sec
        normalized_loudness = normalize_wav(
            tools,
            source_wav,
            pending_wav,
            profile,
        )
        normalized_probe = probe_audio(tools, pending_wav)
        if (
            not normalized_probe.codec_name.startswith("pcm_")
            or normalized_probe.sample_rate_hz != profile.sample_rate_hz
            or normalized_probe.channels != profile.channels
        ):
            raise GenerationError("正規化 WAV の形式が profile と一致しません。")

        encode_opus(tools, pending_wav, pending_opus, profile)
        opus_probe = probe_audio(tools, pending_opus)
        if (
            opus_probe.codec_name != "opus"
            or opus_probe.sample_rate_hz != profile.sample_rate_hz
            or opus_probe.channels != profile.channels
        ):
            raise GenerationError("Opus の形式が profile と一致しません。")
        encoded_loudness = measure_encoded_opus(
            tools,
            pending_opus,
            profile,
        )

        gen_params = {
            "requested": requested_params,
            "realized": realized_params,
        }
        metadata = {
            "format_version": 2,
            "model": adapter.profile.id,
            "scenario": job.scenario_id,
            "line": job.line_id,
            "variant": "dry",
            "input_hash": input_hash,
            "wav_sha256": _sha256_file(pending_wav),
            "opus_sha256": _sha256_file(pending_opus),
            "duration_sec": round(opus_probe.duration_sec, 6),
            "generation_seconds": round(generation_seconds, 6),
            "rtf": round(rtf, 6),
            "gen_params": gen_params,
            "postprocess": profile.as_dict(),
            "loudness": {
                "normalized_wav": normalized_loudness.as_dict(),
                "encoded_opus": encoded_loudness.as_dict(),
            },
        }
        pending_metadata.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        pending_wav.replace(normalized_wav)
        pending_opus.replace(opus_path)
        pending_metadata.replace(metadata_path)
    finally:
        for temporary_path in (
            source_wav,
            pending_wav,
            pending_opus,
            pending_metadata,
        ):
            temporary_path.unlink(missing_ok=True)

    return _record_and_clip_from_metadata(
        adapter.profile.id,
        job,
        metadata,
        profile,
        status="generated",
    )


def _generation_hash(
    *,
    adapter: Adapter,
    job: LineJob,
    requested_params: dict[str, Any],
    profile: PostprocessProfile,
) -> str:
    try:
        generation_input = adapter.generation_input(job)
    except Exception as error:
        raise GenerationError(
            f"adapter 入力構築に失敗しました: {error}",
        ) from error
    payload = {
        "model": {
            "id": adapter.profile.id,
            "version": adapter.profile.version,
        },
        "input": generation_input,
        "gen_params": requested_params,
        "postprocess": profile.as_dict(),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _manifest_has_failure(
    manifest: dict[str, Any],
    *,
    model_id: str,
    scenario_id: str,
    line_id: str,
) -> bool:
    key = (model_id, scenario_id, line_id, "dry")
    return any(
        (
            failure["model"],
            failure["scenario"],
            failure["line"],
            failure["variant"],
        )
        == key
        for failure in manifest["failures"]
    )


def _failure_result(model_id: str, job: LineJob) -> dict[str, str]:
    return {
        "model": model_id,
        "scenario": job.scenario_id,
        "line": job.line_id,
        "variant": "dry",
        "reason": "generation_failed",
    }


def _read_metadata(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise GenerationError(f"生成メタがファイルではありません: {path}")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    _validate_metadata(metadata, path)
    return metadata


def _metadata_matches(
    metadata: dict[str, Any],
    *,
    input_hash: str,
    normalized_wav: Path,
    opus_path: Path,
    adapter: Adapter,
    job: LineJob,
    requested_params: dict[str, Any],
    profile: PostprocessProfile,
) -> bool:
    identity = (
        metadata["model"],
        metadata["scenario"],
        metadata["line"],
        metadata["variant"],
    )
    expected_identity = (
        adapter.profile.id,
        job.scenario_id,
        job.line_id,
        "dry",
    )
    if identity != expected_identity:
        raise GenerationError("生成メタの model/scenario/line/variant が不正です。")
    if metadata["input_hash"] != input_hash:
        return False
    if metadata["postprocess"] != profile.as_dict():
        raise GenerationError("生成メタの後処理 profile が不正です。")
    if metadata["gen_params"]["requested"] != requested_params:
        raise GenerationError("生成メタの生成パラメータが不正です。")
    if not normalized_wav.is_file() or not opus_path.is_file():
        return False
    return metadata.get("wav_sha256") == _sha256_file(normalized_wav) and metadata.get(
        "opus_sha256"
    ) == _sha256_file(opus_path)


def _validate_metadata(metadata: Any, path: Path) -> None:
    if not isinstance(metadata, dict) or set(metadata) != METADATA_KEYS:
        raise GenerationError(f"生成メタの項目が v2 と一致しません: {path}")
    if metadata["format_version"] != 2:
        raise GenerationError(f"生成メタの format_version が不正です: {path}")
    for key in (
        "model",
        "scenario",
        "line",
        "variant",
        "input_hash",
        "wav_sha256",
        "opus_sha256",
    ):
        if not isinstance(metadata[key], str):
            raise GenerationError(f"生成メタの {key} が不正です: {path}")
    for key in ("duration_sec", "generation_seconds", "rtf"):
        value = metadata[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise GenerationError(f"生成メタの {key} が不正です: {path}")

    gen_params = metadata["gen_params"]
    if (
        not isinstance(gen_params, dict)
        or set(gen_params) != {"requested", "realized"}
        or not isinstance(gen_params["requested"], dict)
        or not isinstance(gen_params["realized"], dict)
    ):
        raise GenerationError(f"生成メタの gen_params が不正です: {path}")

    postprocess = metadata["postprocess"]
    if not isinstance(postprocess, dict):
        raise GenerationError(f"生成メタの postprocess が不正です: {path}")

    loudness = metadata["loudness"]
    if (
        not isinstance(loudness, dict)
        or set(loudness) != {"normalized_wav", "encoded_opus"}
    ):
        raise GenerationError(f"生成メタの loudness が不正です: {path}")
    normalized_loudness = loudness["normalized_wav"]
    if (
        not isinstance(normalized_loudness, dict)
        or set(normalized_loudness)
        != {
            "integrated_lufs",
            "true_peak_dbtp",
            "loudness_range_lu",
            "normalization_type",
        }
        or normalized_loudness["normalization_type"] not in {"linear", "dynamic"}
    ):
        raise GenerationError(f"生成メタの loudness.normalized_wav が不正です: {path}")
    encoded_loudness = loudness["encoded_opus"]
    if (
        not isinstance(encoded_loudness, dict)
        or set(encoded_loudness)
        != {
            "integrated_lufs",
            "true_peak_dbtp",
            "loudness_range_lu",
        }
    ):
        raise GenerationError(f"生成メタの loudness.encoded_opus が不正です: {path}")
    for stage, report in (
        ("normalized_wav", normalized_loudness),
        ("encoded_opus", encoded_loudness),
    ):
        for key in ("integrated_lufs", "true_peak_dbtp", "loudness_range_lu"):
            value = report[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise GenerationError(
                    f"生成メタの loudness.{stage}.{key} が不正です: {path}"
                )


def _record_and_clip_from_metadata(
    model_id: str,
    job: LineJob,
    metadata: dict[str, Any],
    profile: PostprocessProfile,
    *,
    status: Literal["generated", "skipped"],
) -> tuple[GenerationRecord, dict[str, Any]]:
    try:
        generation_seconds = float(metadata["generation_seconds"])
        rtf = float(metadata["rtf"])
        duration_sec = float(metadata["duration_sec"])
        opus_sha256 = str(metadata["opus_sha256"])
        gen_params = metadata["gen_params"]
        encoded_loudness = metadata["loudness"]["encoded_opus"]
        loudness = EncodedLoudnessReport(
            integrated_lufs=float(encoded_loudness["integrated_lufs"]),
            true_peak_dbtp=float(encoded_loudness["true_peak_dbtp"]),
            loudness_range_lu=float(encoded_loudness["loudness_range_lu"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GenerationError("生成メタに必要な項目がありません。") from error
    if not isinstance(gen_params, dict):
        raise GenerationError("生成メタの gen_params は object が必要です。")

    record = GenerationRecord(
        scenario_id=job.scenario_id,
        line_id=job.line_id,
        status=status,
        generation_seconds=generation_seconds,
        rtf=rtf,
    )
    clip = {
        "model": model_id,
        "scenario": job.scenario_id,
        "line": job.line_id,
        "variant": "dry",
        "path": (f"audio/{model_id}/{job.scenario_id}/{job.line_id}-dry.opus"),
        "duration_sec": duration_sec,
        "sha256": opus_sha256,
        "gen_params": gen_params,
        "rtf": rtf,
        "loudness": loudness.as_manifest_dict(profile),
    }
    return record, clip


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
