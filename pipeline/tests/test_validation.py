from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from gaya_pipeline.cli import main
from gaya_pipeline.validation import validate_scenarios

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"
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
    for fixture_name in fixture_names:
        shutil.copy2(
            FIXTURES_DIR / fixture_name,
            scenarios_dir / fixture_name,
        )
    return scenarios_dir


def test_current_scenarios_are_valid() -> None:
    result = validate_scenarios(SCENARIOS_DIR)

    assert result.file_count == len(list(SCENARIOS_DIR.glob("*.yaml")))
    assert result.problems == ()


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
