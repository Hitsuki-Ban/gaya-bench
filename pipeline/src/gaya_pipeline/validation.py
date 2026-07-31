from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from gaya_pipeline.japanese_reading import find_ambiguous_japanese_readings
from gaya_pipeline.voice_assets import validate_voice_metadata


@dataclass(frozen=True)
class Problem:
    file: Path
    target: str
    reason: str

    def __str__(self) -> str:
        return f"{self.file.as_posix()} [{self.target}] {self.reason}"


@dataclass(frozen=True)
class ValidationResult:
    file_count: int
    problems: tuple[Problem, ...]
    warnings: tuple[Problem, ...] = ()


def default_scenarios_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "scenarios"


def validate_scenarios(
    scenarios_dir: Path,
    *,
    voices_dir: Path | None = None,
) -> ValidationResult:
    scenarios_dir = scenarios_dir.resolve()
    if not scenarios_dir.is_dir():
        return ValidationResult(
            file_count=0,
            problems=(
                Problem(scenarios_dir, "scenarios", "ディレクトリが存在しません。"),
            ),
        )
    scenario_files = tuple(sorted(scenarios_dir.glob("*.yaml")))
    if not scenario_files:
        return ValidationResult(
            file_count=0,
            problems=(
                Problem(scenarios_dir, "scenarios", "YAML ファイルがありません。"),
            ),
        )

    return _validate_scenario_files(
        scenarios_dir,
        scenario_files,
        voices_dir=voices_dir,
    )


def validate_scenario_ids(
    scenarios_dir: Path,
    scenario_ids: list[str],
    *,
    voices_dir: Path | None = None,
) -> ValidationResult:
    scenarios_dir = scenarios_dir.resolve()
    if not scenarios_dir.is_dir():
        return ValidationResult(
            file_count=0,
            problems=(
                Problem(scenarios_dir, "scenarios", "ディレクトリが存在しません。"),
            ),
        )
    scenario_files = tuple(
        scenarios_dir / f"{scenario_id}.yaml"
        for scenario_id in sorted(set(scenario_ids))
    )
    return _validate_scenario_files(
        scenarios_dir,
        scenario_files,
        voices_dir=voices_dir,
    )


def _validate_scenario_files(
    scenarios_dir: Path,
    scenario_files: tuple[Path, ...],
    *,
    voices_dir: Path | None,
) -> ValidationResult:
    schema_path = scenarios_dir / "schema" / "scenario.schema.json"
    if not schema_path.is_file():
        return ValidationResult(
            file_count=0,
            problems=(Problem(schema_path, "schema", "スキーマが存在しません。"),),
        )
    try:
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, yaml.YAMLError, SchemaError) as error:
        return ValidationResult(
            file_count=0,
            problems=(Problem(schema_path, "schema", str(error)),),
        )

    validator = Draft202012Validator(schema)
    voice_result = validate_voice_metadata(
        (
            voices_dir
            if voices_dir is not None
            else scenarios_dir.parent / "assets" / "voices"
        ),
    )
    problems: list[Problem] = [
        Problem(problem.file, problem.target, problem.reason)
        for problem in voice_result.problems
    ]
    known_voice_ids = None if voice_result.problems else voice_result.voice_ids
    valid_documents: list[tuple[Path, Mapping[str, Any]]] = []

    for scenario_file in scenario_files:
        document, load_problem = _load_yaml(scenario_file)
        if load_problem is not None:
            problems.append(load_problem)
            continue

        schema_errors = sorted(
            validator.iter_errors(document),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if schema_errors:
            for error in schema_errors:
                problems.append(
                    Problem(
                        scenario_file,
                        _json_path(error.absolute_path),
                        error.message,
                    ),
                )
            continue

        valid_documents.append((scenario_file, document))

    problems.extend(_validate_references(valid_documents, known_voice_ids))
    warnings = _ambiguous_reading_warnings(valid_documents)
    return ValidationResult(
        file_count=len(scenario_files),
        problems=tuple(problems),
        warnings=tuple(warnings),
    )


def _load_yaml(
    scenario_file: Path,
) -> tuple[Mapping[str, Any], Problem | None]:
    try:
        document = yaml.safe_load(scenario_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        return {}, Problem(scenario_file, "yaml", str(error))

    if not isinstance(document, Mapping):
        return {}, Problem(
            scenario_file,
            "$",
            "トップレベルはオブジェクトである必要があります。",
        )
    return document, None


def _validate_references(
    documents: list[tuple[Path, Mapping[str, Any]]],
    known_voice_ids: frozenset[str] | None,
) -> list[Problem]:
    problems: list[Problem] = []
    scenario_files_by_id: dict[str, Path] = {}

    for scenario_file, document in documents:
        scenario_id = str(document["id"])

        if scenario_file.stem != scenario_id:
            problems.append(
                Problem(
                    scenario_file,
                    scenario_id,
                    (
                        f"ファイル名 '{scenario_file.stem}' と"
                        f" scenario id '{scenario_id}' が一致しません。"
                    ),
                ),
            )

        previous_file = scenario_files_by_id.get(scenario_id)
        if previous_file is not None:
            problems.append(
                Problem(
                    scenario_file,
                    scenario_id,
                    f"scenario id が {previous_file.as_posix()} と重複しています。",
                ),
            )
        else:
            scenario_files_by_id[scenario_id] = scenario_file

        characters = document["characters"]
        if known_voice_ids is not None:
            for character in characters:
                reference_voice = character.get("reference_voice")
                if (
                    reference_voice is not None
                    and reference_voice not in known_voice_ids
                ):
                    problems.append(
                        Problem(
                            scenario_file,
                            f"{scenario_id}/{character['id']}",
                            (
                                f"reference_voice '{reference_voice}' が "
                                "assets/voices/metadata.yaml に存在しません。"
                            ),
                        ),
                    )
        character_ids = [str(character["id"]) for character in characters]
        problems.extend(
            _duplicate_id_problems(
                scenario_file,
                scenario_id,
                "character",
                character_ids,
            ),
        )
        known_characters = set(character_ids)

        lines = document["lines"]
        line_ids = [str(line["id"]) for line in lines]
        problems.extend(
            _duplicate_id_problems(
                scenario_file,
                scenario_id,
                "line",
                line_ids,
            ),
        )

        for line in lines:
            line_id = str(line["id"])
            character_id = str(line["character"])
            if character_id not in known_characters:
                problems.append(
                    Problem(
                        scenario_file,
                        f"{scenario_id}/{line_id}",
                        f"character '{character_id}' が存在しません。",
                    ),
                )

    return problems


def _duplicate_id_problems(
    scenario_file: Path,
    scenario_id: str,
    kind: str,
    ids: list[str],
) -> list[Problem]:
    seen: set[str] = set()
    problems: list[Problem] = []
    for item_id in ids:
        if item_id in seen:
            problems.append(
                Problem(
                    scenario_file,
                    f"{scenario_id}/{item_id}",
                    f"{kind} id がシーン内で重複しています。",
                ),
            )
        seen.add(item_id)
    return problems


def _ambiguous_reading_warnings(
    documents: list[tuple[Path, Mapping[str, Any]]],
) -> list[Problem]:
    warnings: list[Problem] = []
    for scenario_file, document in documents:
        scenario_id = str(document["id"])
        for line in document["lines"]:
            reading = line.get("reading")
            if isinstance(reading, str) and reading.strip():
                continue
            for ambiguous in find_ambiguous_japanese_readings(line["text"]):
                warnings.append(
                    Problem(
                        scenario_file,
                        f"{scenario_id}/{line['id']}",
                        (
                            f"多読み語 '{ambiguous.surface}' の読み候補 "
                            f"({', '.join(ambiguous.candidates)}) を判定できません。"
                            " line.reading を明記してください。"
                        ),
                    ),
                )
    return warnings


def _json_path(parts: Any) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path
