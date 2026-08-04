from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaya_pipeline.cli import main

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = REPOSITORY_ROOT / "scenarios"
VOICES = REPOSITORY_ROOT / "assets" / "voices"
MODEL = "irodori-tts-v4-small"


def test_increment_pathは相対pathを拒否する(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "increment",
            "anchor-bootstrap",
            "--model",
            MODEL,
            "--scenarios",
            "scenarios",
            "--voices",
            str(VOICES),
            "--output",
            str(tmp_path / "plan.json"),
        ],
    )
    assert exit_code == 1
    assert "絶対path" in capsys.readouterr().err


def test_anchor_bootstrapは53_targetのplanとSHA_markerを書く(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "role-anchor-bootstrap-plan-v1.json"
    exit_code = main(
        [
            "increment",
            "anchor-bootstrap",
            "--model",
            MODEL,
            "--scenarios",
            str(SCENARIOS),
            "--voices",
            str(VOICES),
            "--output",
            str(output),
        ],
    )
    assert exit_code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["protocol"] == "role-anchor-bootstrap-plan-v1"
    assert document["model"] == MODEL
    assert len(document["targets"]) == 53
    assert output.with_suffix(".sha256").is_file()
    assert "targets: 53" in capsys.readouterr().out


def test_anchor_bootstrapは既存outputを上書きしない(tmp_path: Path) -> None:
    output = tmp_path / "plan.json"
    output.write_text("{}", encoding="utf-8")
    exit_code = main(
        [
            "increment",
            "anchor-bootstrap",
            "--model",
            MODEL,
            "--scenarios",
            str(SCENARIOS),
            "--voices",
            str(VOICES),
            "--output",
            str(output),
        ],
    )
    assert exit_code == 1


def test_top_up_roundはtargetなしでは実行できない(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    assert (
        main(
            [
                "increment",
                "anchor-bootstrap",
                "--model",
                MODEL,
                "--scenarios",
                str(SCENARIOS),
                "--voices",
                str(VOICES),
                "--output",
                str(plan_path),
            ],
        )
        == 0
    )
    capsys.readouterr()
    exit_code = main(
        [
            "increment",
            "anchor-generate",
            "--anchor-plan",
            str(plan_path),
            "--artifacts",
            str(tmp_path / "artifacts"),
            "--run-id",
            "round1",
            "--round",
            "1",
        ],
    )
    assert exit_code == 1
    assert "--target" in capsys.readouterr().err


def test_increment_verifyは不在releaseを拒否する(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "increment",
            "verify",
            "--release",
            str(tmp_path / "missing"),
            "--artifacts",
            str(tmp_path),
        ],
    )
    assert exit_code == 1
    assert "ERROR" in capsys.readouterr().err
