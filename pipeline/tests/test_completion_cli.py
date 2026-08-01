from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaya_pipeline import cli


def _common(tmp_path: Path) -> list[str]:
    return [
        "--plan",
        str((tmp_path / "plan.json").resolve()),
        "--base-manifest",
        str((tmp_path / "manifest.json").resolve()),
        "--artifacts",
        str((tmp_path / "artifacts").resolve()),
        "--scenarios",
        str((tmp_path / "scenarios").resolve()),
        "--voices",
        str((tmp_path / "voices").resolve()),
    ]


def test_completion_generateはphase_b契約を全て明示する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    policy = SimpleNamespace(
        takes=4,
        seed_policy="derived-sha256-v1",
        primary_seed_base=104,
    )
    plan = SimpleNamespace(
        plan_id="a" * 64,
        anchor_source_plan_sha256="d" * 64,
        policy_for_model=lambda _model: policy,
        target_lines_for_model=lambda _model: (("scene", "line"),),
    )
    monkeypatch.setattr(cli, "load_completion_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(
        cli,
        "phase_b_generation_binding",
        lambda **_kwargs: ("b" * 64, {("scene", "line"): "c" * 64}),
    )

    def generate(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(failed_count=0)

    monkeypatch.setattr(cli, "run_generation", generate)
    monkeypatch.setattr(cli, "_print_generation_summary", lambda _summary: None)
    anchor = (tmp_path / "anchors" / "role-anchor-selection-v1.json").resolve()
    result = cli.main(
        [
            "completion",
            "generate",
            *_common(tmp_path),
            "--model",
            "qwen3-tts-12hz-1.7b",
            "--anchor-selection",
            str(anchor),
            "--run-kind",
            "primary",
            "--seed-base",
            "104",
        ],
    )
    assert result == 0
    assert captured["completion_plan_sha256"] == "a" * 64
    assert captured["role_epochs"] == {("scene", "line"): "c" * 64}
    assert captured["run_kind"] == "primary"
    assert captured["supersedes_run_id"] is None
    assert captured["takes"] == 4
    assert captured["seed_base"] == 104
    assert captured["role_anchor_selection_path"] == anchor
    assert captured["role_anchor_plan_sha256"] == "d" * 64


def test_completion_generateはAivisをN1_seedなしに固定しtopupを拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}
    policy = SimpleNamespace(
        takes=1,
        seed_policy="none",
        primary_seed_base=None,
    )
    plan = SimpleNamespace(
        plan_id="a" * 64,
        anchor_source_plan_sha256="d" * 64,
        policy_for_model=lambda _model: policy,
        target_lines_for_model=lambda _model: (("scene", "line"),),
    )
    monkeypatch.setattr(cli, "load_completion_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(
        cli,
        "phase_b_generation_binding",
        lambda **_kwargs: ("b" * 64, {("scene", "line"): "c" * 64}),
    )
    monkeypatch.setattr(
        cli,
        "run_generation",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(failed_count=0),
    )
    monkeypatch.setattr(cli, "_print_generation_summary", lambda _summary: None)
    common = [
        "completion",
        "generate",
        *_common(tmp_path),
        "--model",
        "aivisspeech-kohaku",
        "--anchor-selection",
        str((tmp_path / "anchor.json").resolve()),
    ]

    assert cli.main([*common, "--run-kind", "primary"]) == 0
    assert captured["takes"] == 1
    assert captured["seed_base"] is None

    assert (
        cli.main(
            [
                *common,
                "--run-kind",
                "topup",
                "--supersedes-run-id",
                "old-run",
                "--target",
                "scene/line",
            ],
        )
        == 1
    )
    assert "topup" in capsys.readouterr().err


def test_completion_topupはsupersedesを必須にする(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = SimpleNamespace(
        takes=4,
        seed_policy="derived-sha256-v1",
        primary_seed_base=104,
    )
    plan = SimpleNamespace(
        plan_id="a" * 64,
        anchor_source_plan_sha256="d" * 64,
        policy_for_model=lambda _model: policy,
        target_lines_for_model=lambda _model: (("scene", "line"),),
    )
    monkeypatch.setattr(cli, "load_completion_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(
        cli,
        "phase_b_generation_binding",
        lambda **_kwargs: ("b" * 64, {("scene", "line"): "c" * 64}),
    )
    result = cli.main(
        [
            "completion",
            "generate",
            *_common(tmp_path),
            "--model",
            "chatterbox-multilingual-v3",
            "--anchor-selection",
            str((tmp_path / "anchor.json").resolve()),
            "--run-kind",
            "topup",
            "--seed-base",
            "105",
            "--target",
            "scene/line",
        ],
    )
    assert result == 1
    assert "supersedes" in capsys.readouterr().err


def test_completion_commandは相対pathを拒否する(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["completion", "generate"])
    result = cli.main(
        [
            "completion",
            "generate",
            "--plan",
            "plan.json",
            "--base-manifest",
            "manifest.json",
            "--artifacts",
            "artifacts",
            "--scenarios",
            "scenarios",
            "--voices",
            "voices",
            "--model",
            "model",
            "--anchor-selection",
            "anchor.json",
            "--run-kind",
            "primary",
            "--seed-base",
            "104",
        ],
    )
    assert result == 1
    assert "絶対path" in capsys.readouterr().err


def test_completion_listenは八主runとtopupを分離する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "load_completion_plan", lambda *_a, **_k: object())
    monkeypatch.setattr(
        cli,
        "build_completion_listening_bundle",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(),
    )
    monkeypatch.setattr(
        cli,
        "_print_completion_listening_summary",
        lambda _summary: None,
    )
    primary_args = [
        item
        for index in range(8)
        for item in ("--primary-run-id", f"primary-{index}")
    ]
    result = cli.main(
        [
            "completion",
            "listen",
            *_common(tmp_path),
            *primary_args,
            "--topup-run-id",
            "topup-1",
            "--anchor-selection",
            str((tmp_path / "anchor.json").resolve()),
            "--output",
            str((tmp_path / "listen").resolve()),
        ],
    )
    assert result == 0
    assert captured["primary_run_ids"] == [f"primary-{index}" for index in range(8)]
    assert captured["topup_run_ids"] == ["topup-1"]


def test_completion_publishはactivationとreceiptを明示する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "create_r2_client", lambda _path: object())
    monkeypatch.setattr(
        cli,
        "run_completion_publish",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(),
    )
    monkeypatch.setattr(
        cli,
        "_print_completion_publish_summary",
        lambda _summary: None,
    )
    activation = (tmp_path / "data" / "manifest.json").resolve()
    receipt = (tmp_path / "publish-receipt.json").resolve()
    source_audit = (tmp_path / "source-audit.json").resolve()
    result = cli.main(
        [
            "completion",
            "publish",
            "--release",
            str((tmp_path / "release").resolve()),
            "--artifacts",
            str((tmp_path / "artifacts").resolve()),
            "--source-audit",
            str(source_audit),
            "--env-file",
            str((tmp_path / ".env").resolve()),
            "--manifest-activation",
            str(activation),
            "--publish-receipt",
            str(receipt),
        ],
    )
    assert result == 0
    assert captured["manifest_activation_path"] == activation
    assert captured["publish_receipt_path"] == receipt
    assert captured["source_audit_path"] == source_audit
