from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from gaya_pipeline.audio import (
    AudioProcessingError,
    AudioTools,
    find_audio_tools,
    probe_audio,
)


@dataclass(frozen=True)
class VoiceAssetProblem:
    file: Path
    target: str
    reason: str

    def __str__(self) -> str:
        return f"{self.file.as_posix()} [{self.target}] {self.reason}"


@dataclass(frozen=True)
class VoiceValidationResult:
    voice_ids: frozenset[str]
    problems: tuple[VoiceAssetProblem, ...]


def default_voices_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "assets" / "voices"


def validate_voice_metadata(voices_dir: Path) -> VoiceValidationResult:
    voices_dir = voices_dir.resolve()
    schema_path = voices_dir / "metadata.schema.json"
    metadata_path = voices_dir / "metadata.yaml"

    if not voices_dir.is_dir():
        return _result_with_problem(
            voices_dir,
            "voices",
            "ディレクトリが存在しません。",
        )
    if not schema_path.is_file():
        return _result_with_problem(
            schema_path,
            "schema",
            "メタデータスキーマが存在しません。",
        )
    if not metadata_path.is_file():
        return _result_with_problem(
            metadata_path,
            "metadata",
            "メタデータが存在しません。",
        )

    try:
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        document = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, SchemaError) as error:
        return _result_with_problem(metadata_path, "metadata", str(error))

    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )
    schema_errors = sorted(
        validator.iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if schema_errors:
        return VoiceValidationResult(
            voice_ids=frozenset(),
            problems=tuple(
                VoiceAssetProblem(
                    metadata_path,
                    _json_path(error.absolute_path),
                    error.message,
                )
                for error in schema_errors
            ),
        )

    assert isinstance(document, Mapping)
    voices = document["voices"]
    problems: list[VoiceAssetProblem] = []
    voice_ids: set[str] = set()
    for index, entry in enumerate(voices):
        voice_id = str(entry["id"])
        if voice_id in voice_ids:
            problems.append(
                VoiceAssetProblem(
                    metadata_path,
                    f"$.voices[{index}].id",
                    f"voice id '{voice_id}' が重複しています。",
                ),
            )
        voice_ids.add(voice_id)

        expected_file = f"{voice_id}/reference.wav"
        if entry["file"] != expected_file:
            problems.append(
                VoiceAssetProblem(
                    metadata_path,
                    f"$.voices[{index}].file",
                    f"file は '{expected_file}' である必要があります。",
                ),
            )

    return VoiceValidationResult(
        voice_ids=frozenset(voice_ids),
        problems=tuple(problems),
    )


def validate_local_voice_assets(
    voices_dir: Path,
    *,
    tools: AudioTools | None = None,
) -> VoiceValidationResult:
    voices_dir = voices_dir.resolve()
    metadata_result = validate_voice_metadata(voices_dir)
    if metadata_result.problems:
        return metadata_result

    metadata_path = voices_dir / "metadata.yaml"
    try:
        document = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        return _result_with_problem(metadata_path, "metadata", str(error))

    if tools is None:
        try:
            tools = find_audio_tools()
        except AudioProcessingError as error:
            return _result_with_problem(voices_dir, "tools", str(error))

    problems: list[VoiceAssetProblem] = []
    registered_audio_paths = {
        voices_dir / str(entry["file"]) for entry in document["voices"]
    }
    for index, entry in enumerate(document["voices"]):
        target = f"$.voices[{index}].file"
        audio_path = voices_dir / str(entry["file"])
        resolved_audio_path = audio_path.resolve()

        if audio_path.is_symlink() or not resolved_audio_path.is_relative_to(
            voices_dir
        ):
            problems.append(
                VoiceAssetProblem(
                    audio_path,
                    target,
                    "参照音声は voices ディレクトリ内の通常ファイルである必要があります。",
                ),
            )
            continue
        if not audio_path.is_file():
            problems.append(
                VoiceAssetProblem(
                    audio_path,
                    target,
                    "参照 WAV が存在しません。",
                ),
            )
            continue

        actual_sha256 = _sha256(audio_path)
        if actual_sha256 != entry["sha256"]:
            problems.append(
                VoiceAssetProblem(
                    audio_path,
                    f"$.voices[{index}].sha256",
                    (
                        f"SHA-256 が一致しません: expected={entry['sha256']} "
                        f"actual={actual_sha256}"
                    ),
                ),
            )

        try:
            probe = probe_audio(tools, audio_path)
        except AudioProcessingError as error:
            problems.append(VoiceAssetProblem(audio_path, target, str(error)))
            continue

        if probe.codec_name != "pcm_s16le":
            problems.append(
                VoiceAssetProblem(
                    audio_path,
                    target,
                    (f"codec は pcm_s16le である必要があります: {probe.codec_name}"),
                ),
            )
        if probe.sample_rate_hz != 48_000:
            problems.append(
                VoiceAssetProblem(
                    audio_path,
                    target,
                    (
                        "sample rate は 48000 Hz である必要があります: "
                        f"{probe.sample_rate_hz}"
                    ),
                ),
            )
        if probe.channels != 1:
            problems.append(
                VoiceAssetProblem(
                    audio_path,
                    target,
                    f"channels は 1 である必要があります: {probe.channels}",
                ),
            )
        if not 10.0 <= probe.duration_sec <= 20.0:
            problems.append(
                VoiceAssetProblem(
                    audio_path,
                    target,
                    (
                        "duration は 10〜20 秒である必要があります: "
                        f"{probe.duration_sec:.6f}"
                    ),
                ),
            )
        if abs(probe.duration_sec - float(entry["duration_sec"])) > 0.001:
            problems.append(
                VoiceAssetProblem(
                    audio_path,
                    f"$.voices[{index}].duration_sec",
                    (
                        "メタデータと実測 duration が一致しません: "
                        f"metadata={entry['duration_sec']} "
                        f"actual={probe.duration_sec:.6f}"
                    ),
                ),
            )

    for audio_path in sorted(voices_dir.glob("*/reference.wav")):
        if audio_path not in registered_audio_paths:
            problems.append(
                VoiceAssetProblem(
                    audio_path,
                    "local-files",
                    "metadata.yaml に未登録の参照 WAV です。",
                ),
            )

    return VoiceValidationResult(
        voice_ids=metadata_result.voice_ids,
        problems=tuple(problems),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _result_with_problem(
    file: Path,
    target: str,
    reason: str,
) -> VoiceValidationResult:
    return VoiceValidationResult(
        voice_ids=frozenset(),
        problems=(VoiceAssetProblem(file, target, reason),),
    )


def _json_path(parts: Any) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path
