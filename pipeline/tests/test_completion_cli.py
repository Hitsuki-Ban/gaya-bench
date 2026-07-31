from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaya_pipeline import cli


def test_completion_generateはplanと全rootを明示して生成する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    plan_path = tmp_path / "plan.json"
    manifest_path = tmp_path / "manifest.json"
    artifacts_dir = tmp_path / "artifacts"
    scenarios_dir = tmp_path / "scenarios"
    voices_dir = tmp_path / "voices"
    plan = SimpleNamespace(
        takes=4,
        seed_base=104,
        target_lines_for_model=lambda model: (
            (("scene", "line-001"),) if model == "model" else ()
        ),
    )

    def fake_load_plan(path: Path, *, base_manifest_path: Path) -> Any:
        captured["plan_path"] = path
        captured["base_manifest_path"] = base_manifest_path
        return plan

    def fake_generation(**kwargs: Any) -> Any:
        captured["generation"] = kwargs
        return SimpleNamespace(failed_count=0)

    monkeypatch.setattr(cli, "load_completion_plan", fake_load_plan)
    monkeypatch.setattr(cli, "run_generation", fake_generation)
    monkeypatch.setattr(cli, "_print_generation_summary", lambda _summary: None)

    result = cli.main(
        [
            "completion",
            "generate",
            "--plan",
            str(plan_path),
            "--base-manifest",
            str(manifest_path),
            "--model",
            "model",
            "--artifacts",
            str(artifacts_dir),
            "--scenarios",
            str(scenarios_dir),
            "--voices",
            str(voices_dir),
        ],
    )

    assert result == 0
    assert captured["plan_path"] == plan_path
    assert captured["base_manifest_path"] == manifest_path
    assert captured["generation"] == {
        "model_id": "model",
        "scenarios_dir": scenarios_dir,
        "artifacts_dir": artifacts_dir,
        "voices_dir": voices_dir,
        "target_lines": (("scene", "line-001"),),
        "takes": 4,
        "seed_base": 104,
        "force": False,
    }


def test_completion_commandは相対pathを拒否する(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        [
            "completion",
            "generate",
            "--plan",
            "plan.json",
            "--base-manifest",
            "manifest.json",
            "--model",
            "model",
            "--artifacts",
            "artifacts",
            "--scenarios",
            "scenarios",
            "--voices",
            "voices",
        ],
    )

    assert result == 1
    assert "絶対path" in capsys.readouterr().err


def test_completion_qcはreference_voiceとmodel_rootを明示する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    model_root = tmp_path / "qc-model"

    monkeypatch.setattr(
        cli,
        "KanaWhisperQCRuntime",
        lambda path: captured.setdefault("runtime_path", path) or object(),
    )

    def fake_qc(**kwargs: Any) -> Any:
        captured["qc"] = kwargs
        return SimpleNamespace(blocked_count=0, pending_count=0)

    monkeypatch.setattr(cli, "run_qc", fake_qc)
    monkeypatch.setattr(cli, "_print_qc_summary", lambda _summary: None)

    result = cli.main(
        [
            "completion",
            "qc",
            "--run-id",
            "run-1",
            "--artifacts",
            str(tmp_path / "artifacts"),
            "--scenarios",
            str(tmp_path / "scenarios"),
            "--voices",
            str(tmp_path / "voices"),
            "--qc-model-root",
            str(model_root),
        ],
    )

    assert result == 0
    assert captured["runtime_path"] == model_root
    assert captured["qc"]["voices_dir"] == tmp_path / "voices"
    assert captured["qc"]["artifacts_dir"] == tmp_path / "artifacts"


def test_completion_listenはplanと全runを専用builderへ渡す(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    plan = object()
    monkeypatch.setattr(
        cli,
        "load_completion_plan",
        lambda _path, *, base_manifest_path: (
            captured.setdefault("base_manifest", base_manifest_path),
            plan,
        )[1],
    )

    def fake_listen(**kwargs: Any) -> Any:
        captured["listen"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr(cli, "build_completion_listening_bundle", fake_listen)
    monkeypatch.setattr(
        cli,
        "_print_completion_listening_summary",
        lambda _summary: None,
    )

    result = cli.main(
        [
            "completion",
            "listen",
            "--plan",
            str(tmp_path / "plan.json"),
            "--base-manifest",
            str(tmp_path / "manifest.json"),
            "--run-id",
            "run-a",
            "--run-id",
            "run-b",
            "--artifacts",
            str(tmp_path / "artifacts"),
            "--scenarios",
            str(tmp_path / "scenarios"),
            "--voices",
            str(tmp_path / "voices"),
            "--output",
            str(tmp_path / "listen"),
        ],
    )

    assert result == 0
    assert captured["listen"]["plan"] is plan
    assert captured["listen"]["run_ids"] == ["run-a", "run-b"]
    assert captured["listen"]["voices_dir"] == tmp_path / "voices"


def test_completion_finalizeは契約errorを安定したexit1へ変換する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_finalize(**_kwargs: Any) -> Any:
        raise cli.CompletionReleaseError("bad completion decision")

    monkeypatch.setattr(cli, "finalize_completion_release", fail_finalize)

    result = cli.main(
        [
            "completion",
            "finalize",
            "--base-manifest",
            str(tmp_path / "manifest.json"),
            "--qwen-curation",
            str(tmp_path / "qwen.json"),
            "--plan",
            str(tmp_path / "plan.json"),
            "--decision",
            str(tmp_path / "decision.json"),
            "--run-id",
            "run-a",
            "--artifacts",
            str(tmp_path / "artifacts"),
            "--scenarios",
            str(tmp_path / "scenarios"),
            "--voices",
            str(tmp_path / "voices"),
            "--output",
            str(tmp_path / "release"),
        ],
    )

    assert result == 1
    assert "bad completion decision" in capsys.readouterr().err


def test_completion_publishは専用publisherと明示credentialを使う(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    client = object()
    monkeypatch.setattr(
        cli,
        "create_r2_client",
        lambda path: captured.setdefault("env_file", path) and client,
    )

    def fake_publish(**kwargs: Any) -> Any:
        captured["publish"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr(cli, "run_completion_publish", fake_publish)
    monkeypatch.setattr(
        cli,
        "_print_completion_publish_summary",
        lambda _summary: None,
    )

    result = cli.main(
        [
            "completion",
            "publish",
            "--release",
            str(tmp_path / "release"),
            "--artifacts",
            str(tmp_path / "artifacts"),
            "--env-file",
            str(tmp_path / ".env"),
        ],
    )

    assert result == 0
    assert captured["env_file"] == tmp_path / ".env"
    assert captured["publish"] == {
        "release_dir": tmp_path / "release",
        "artifacts_dir": tmp_path / "artifacts",
        "client": client,
    }
