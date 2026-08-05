from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaya_pipeline import cli
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


def _stub_increment_generate(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
) -> None:
    policy = SimpleNamespace(
        takes=4,
        seed_policy="derived-sha256-v1",
        primary_seed_base=194,
    )
    plan = SimpleNamespace(
        model=MODEL,
        plan_id="a" * 64,
        anchor_source_plan_sha256="d" * 64,
        policy_for_model=lambda _model: policy,
        target_lines_for_model=lambda _model: (("tavern-night", "barmaid-001"),),
    )
    monkeypatch.setattr(cli, "load_increment_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(
        cli,
        "increment_generation_binding",
        lambda **_kwargs: (
            "b" * 64,
            {("tavern-night", "barmaid-001"): "c" * 64},
        ),
    )

    def generate(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(failed_count=0)

    monkeypatch.setattr(cli, "run_generation", generate)
    monkeypatch.setattr(cli, "_print_generation_summary", lambda _summary: None)


def _increment_generate_args(tmp_path: Path) -> list[str]:
    return [
        "increment",
        "generate",
        "--plan",
        str((tmp_path / "plan.json").resolve()),
        "--scenarios",
        str(SCENARIOS),
        "--voices",
        str(VOICES),
        "--anchor-selection",
        str((tmp_path / "anchors" / "selection.json").resolve()),
        "--artifacts",
        str((tmp_path / "artifacts").resolve()),
        "--run-kind",
        "primary",
        "--seed-base",
        "194",
    ]


def test_increment_generateはresume_run_idをそのまま生成へ渡す(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _stub_increment_generate(monkeypatch, captured)

    exit_code = main(
        [
            *_increment_generate_args(tmp_path),
            "--resume-run-id",
            "20260804T143045839373Z-irodori-tts-v4-small-n4",
        ],
    )

    assert exit_code == 0
    assert captured["resume_run_id"] == (
        "20260804T143045839373Z-irodori-tts-v4-small-n4"
    )
    # resumeでもprimaryのauthorityは一切変わらない (同じplan/seed/anchor)。
    assert captured["run_kind"] == "primary"
    assert captured["seed_base"] == 194
    assert captured["takes"] == 4
    assert captured["supersedes_run_id"] is None
    assert captured["completion_plan_sha256"] == "a" * 64
    assert captured["role_anchor_plan_sha256"] == "d" * 64
    assert captured["role_anchor_selection_sha256"] == "b" * 64


def test_increment_generateはresume省略時にNoneを渡す(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _stub_increment_generate(monkeypatch, captured)

    assert main(_increment_generate_args(tmp_path)) == 0
    assert captured["resume_run_id"] is None


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
