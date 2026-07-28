from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from gaya_pipeline.cli import main
from gaya_pipeline.validation import validate_scenarios

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"
VOICES_DIR = REPOSITORY_ROOT / "assets" / "voices"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _scenarios_from_fixtures(
    tmp_path: Path,
    *fixture_names: str,
) -> Path:
    scenarios_dir = tmp_path / "scenarios"
    schema_dir = scenarios_dir / "schema"
    schema_dir.mkdir(parents=True)
    shutil.copy2(
        SCENARIOS_DIR / "schema" / "scenario.schema.json",
        schema_dir / "scenario.schema.json",
    )
    voices_dir = tmp_path / "assets" / "voices"
    voices_dir.mkdir(parents=True)
    for filename in ("metadata.schema.json", "metadata.yaml"):
        shutil.copy2(VOICES_DIR / filename, voices_dir / filename)
    for fixture_name in fixture_names:
        shutil.copy2(
            FIXTURES_DIR / fixture_name,
            scenarios_dir / fixture_name,
        )
    return scenarios_dir


def _scenario_with_character_kind(tmp_path: Path, kind: object) -> Path:
    scenarios_dir = _scenarios_from_fixtures(tmp_path)
    scenario_path = SCENARIOS_DIR / "tavern-night.yaml"
    document = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    document["characters"][0]["kind"] = kind
    (scenarios_dir / scenario_path.name).write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return scenarios_dir


def test_current_scenarios_are_valid() -> None:
    result = validate_scenarios(SCENARIOS_DIR)

    assert result.file_count == len(list(SCENARIOS_DIR.glob("*.yaml")))
    assert result.problems == ()


@pytest.mark.parametrize(
    "kind",
    ["human", "machine", "creature", "spirit"],
    ids=["人間", "機械", "クリーチャー", "精霊"],
)
def test_有効なcharacter_kindを受理する(tmp_path: Path, kind: str) -> None:
    result = validate_scenarios(_scenario_with_character_kind(tmp_path, kind))

    assert result.file_count == 1
    assert result.problems == ()


@pytest.mark.parametrize(
    "kind",
    ["robot", None],
    ids=["未定義の値", "null"],
)
def test_無効なcharacter_kindを拒否する(
    tmp_path: Path,
    kind: object,
) -> None:
    result = validate_scenarios(_scenario_with_character_kind(tmp_path, kind))

    assert len(result.problems) == 1
    assert result.problems[0].target == "$.characters[0].kind"


def test_schema_violation_is_rejected(tmp_path: Path) -> None:
    result = validate_scenarios(
        _scenarios_from_fixtures(tmp_path, "broken-schema.yaml"),
    )

    assert len(result.problems) == 1
    assert result.problems[0].file.name == "broken-schema.yaml"
    assert result.problems[0].target == "$"
    assert "lines" in result.problems[0].reason


def test_missing_character_reference_is_rejected(tmp_path: Path) -> None:
    result = validate_scenarios(
        _scenarios_from_fixtures(tmp_path, "broken-reference.yaml"),
    )

    assert len(result.problems) == 1
    assert result.problems[0].target == "broken-reference/guard-001"
    assert "missing-character" in result.problems[0].reason


def test_identifier_constraints_are_rejected(tmp_path: Path) -> None:
    result = validate_scenarios(
        _scenarios_from_fixtures(
            tmp_path,
            "broken-reference.yaml",
            "broken-identifiers.yaml",
        ),
    )
    reasons = [problem.reason for problem in result.problems]

    assert any("ファイル名" in reason for reason in reasons)
    assert any("scenario id" in reason and "重複" in reason for reason in reasons)
    assert any("line id" in reason and "重複" in reason for reason in reasons)


def test_unknown_reference_voice_is_rejected(tmp_path: Path) -> None:
    scenarios_dir = _scenarios_from_fixtures(tmp_path)
    scenario_path = SCENARIOS_DIR / "tavern-night.yaml"
    document = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    document["characters"][0]["reference_voice"] = "missing-voice"
    (scenarios_dir / scenario_path.name).write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    result = validate_scenarios(scenarios_dir)

    assert len(result.problems) == 1
    assert result.problems[0].target == "tavern-night/barmaid"
    assert "missing-voice" in result.problems[0].reason


def test_cli_reports_file_target_reason_and_nonzero_exit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "validate",
            "--scenarios",
            str(
                _scenarios_from_fixtures(
                    tmp_path,
                    "broken-reference.yaml",
                ),
            ),
        ],
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "broken-reference.yaml" in captured.err
    assert "broken-reference/guard-001" in captured.err
    assert "missing-character" in captured.err
