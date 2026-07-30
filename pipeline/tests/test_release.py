from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline import cli
from gaya_pipeline.curation import apply_curation
from gaya_pipeline.release import (
    ReleaseError,
    ReleaseFinalizeSummary,
    finalize_release,
    validate_finalized_release,
)
from gaya_pipeline.take_ledger import read_ledger, write_ledger_atomic
from test_curation import (
    SCENARIOS_DIR,
    _add_candidate_groups,
    _curation,
    _curation_for_lines,
    _setup_run,
    _write_input,
)


def _setup_curated_run(
    tmp_path: Path,
    *,
    run_id: str,
    model: str,
    audio_bytes: bytes,
) -> tuple[str, dict[str, Any]]:
    configured_run_id, manifest, _snapshot_path, _audio_path = _setup_run(
        tmp_path,
        run_id=run_id,
        model=model,
        audio_bytes=audio_bytes,
    )
    apply_curation(
        run_id=configured_run_id,
        input_path=_write_input(tmp_path, _curation(manifest)),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    return configured_run_id, manifest


def _finalize(
    tmp_path: Path,
    *,
    run_ids: list[str],
    output_name: str = "release",
) -> ReleaseFinalizeSummary:
    return finalize_release(
        run_ids=run_ids,
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
        output_dir=tmp_path / output_name,
    )


def _inventory(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_finalizeは複数modelをcanonical_releaseへ集約し入力順に非依存(
    tmp_path: Path,
) -> None:
    run_a, _manifest_a = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"model a opus",
    )
    run_b, _manifest_b = _setup_curated_run(
        tmp_path,
        run_id="run-b",
        model="model-b",
        audio_bytes=b"model b opus",
    )

    summary_a = _finalize(
        tmp_path,
        run_ids=[run_b, run_a],
        output_name="release-a",
    )
    summary_b = _finalize(
        tmp_path,
        run_ids=[run_a, run_b],
        output_name="release-b",
    )
    release = validate_finalized_release(
        release_dir=summary_a.output_dir,
        takes_root=tmp_path / "artifacts" / "takes",
    )

    assert summary_a == ReleaseFinalizeSummary(
        output_dir=tmp_path / "release-a",
        manifest_sha256=summary_b.manifest_sha256,
        candidate_set_sha256=summary_b.candidate_set_sha256,
        curation_sha256=summary_b.curation_sha256,
        model_count=2,
        candidate_count=2,
        selected_count=2,
        skipped_count=0,
        failure_count=0,
    )
    assert _inventory(tmp_path / "release-a") == _inventory(
        tmp_path / "release-b",
    )
    assert [model["id"] for model in release.manifest["models"]] == [
        "model-a",
        "model-b",
    ]
    assert set(release.run_roots) == {"model-a", "model-b"}
    assert release.provenance["manifest_sha256"] == summary_a.manifest_sha256


def test_finalizeは未策展runを拒否しoutputを残さない(tmp_path: Path) -> None:
    run_id, _manifest, _snapshot_path, _audio_path = _setup_run(
        tmp_path,
        run_id="uncurated",
        model="model-a",
    )
    output = tmp_path / "release"

    with pytest.raises(ReleaseError, match="未策展"):
        _finalize(tmp_path, run_ids=[run_id])

    assert not output.exists()


def test_finalizeはdummy_runをproduction入力として拒否する(
    tmp_path: Path,
) -> None:
    run_id, _ = _setup_curated_run(
        tmp_path,
        run_id="run-dummy",
        model="dummy",
        audio_bytes=b"dummy beep",
    )

    with pytest.raises(ReleaseError, match="dummy run"):
        _finalize(tmp_path, run_ids=[run_id])

    assert not (tmp_path / "release").exists()


def test_finalizeは非terminal_runを拒否する(tmp_path: Path) -> None:
    run_id, _ = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"take a",
    )
    ledger_path = tmp_path / "artifacts" / "takes" / run_id / "ledger.json"
    ledger = read_ledger(ledger_path)
    ledger["attempts"][0]["status"] = "generated"
    ledger["attempts"][0]["gates"] = {}
    write_ledger_atomic(ledger_path, ledger)

    with pytest.raises(ReleaseError, match="全 attempt terminal"):
        _finalize(tmp_path, run_ids=[run_id])

    assert not (tmp_path / "release").exists()


def test_finalizeは異なるline_snapshotのrunを集約しない(
    tmp_path: Path,
) -> None:
    run_a, _ = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"take a",
    )
    run_b, manifest_b, snapshot_b, _audio_b = _setup_run(
        tmp_path,
        run_id="run-b",
        model="model-b",
        audio_bytes=b"take b",
    )
    _add_candidate_groups(
        manifest=manifest_b,
        snapshot_path=snapshot_b,
        line_ids=("barmaid-002",),
    )
    apply_curation(
        run_id=run_b,
        input_path=_write_input(
            tmp_path,
            _curation_for_lines(
                manifest_b,
                ("barmaid-001", "barmaid-002"),
            ),
        ),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )

    with pytest.raises(ReleaseError, match="candidate line snapshot"):
        _finalize(tmp_path, run_ids=[run_a, run_b])

    assert not (tmp_path / "release").exists()


def test_finalizeは同一modelの複数runを拒否する(tmp_path: Path) -> None:
    run_a, _ = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"take a",
    )
    run_b, _ = _setup_curated_run(
        tmp_path,
        run_id="run-b",
        model="model-a",
        audio_bytes=b"take b",
    )

    with pytest.raises(ReleaseError, match="model ごとに1 run"):
        _finalize(tmp_path, run_ids=[run_a, run_b])

    assert not (tmp_path / "release").exists()


def test_finalizeは既存outputを上書きしない(tmp_path: Path) -> None:
    run_id, _ = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"take a",
    )
    output = tmp_path / "release"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ReleaseError, match="既存 path"):
        _finalize(tmp_path, run_ids=[run_id])

    assert marker.read_text(encoding="utf-8") == "keep"


def test_finalized_releaseはsource改変を拒否する(tmp_path: Path) -> None:
    run_id, _ = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"take a",
    )
    summary = _finalize(tmp_path, run_ids=[run_id])
    ledger_path = tmp_path / "artifacts" / "takes" / run_id / "ledger.json"
    ledger = json.loads(ledger_path.read_bytes())
    ledger["created_at"] = "2026-07-30T00:00:00Z"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    with pytest.raises(ReleaseError, match="source ledger SHA"):
        validate_finalized_release(
            release_dir=summary.output_dir,
            takes_root=tmp_path / "artifacts" / "takes",
        )


def test_finalize_cliはexplicit_run_idsとoutputを渡す(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}
    output = tmp_path / "release"

    def fake_finalize(**kwargs: Any) -> ReleaseFinalizeSummary:
        captured.update(kwargs)
        return ReleaseFinalizeSummary(
            output_dir=output,
            manifest_sha256="a" * 64,
            candidate_set_sha256="b" * 64,
            curation_sha256="c" * 64,
            model_count=2,
            candidate_count=2,
            selected_count=1,
            skipped_count=1,
            failure_count=0,
        )

    monkeypatch.setattr(cli, "finalize_release", fake_finalize)
    monkeypatch.setattr(cli, "default_scenarios_dir", lambda: tmp_path / "scenarios")

    assert cli.main(
        [
            "takes",
            "finalize",
            "--run-id",
            "run-b",
            "--run-id",
            "run-a",
            "--output",
            str(output),
        ],
    ) == 0

    assert captured == {
        "run_ids": ["run-b", "run-a"],
        "artifacts_dir": tmp_path / "artifacts",
        "data_dir": tmp_path / "data",
        "scenarios_dir": tmp_path / "scenarios",
        "output_dir": output,
    }
    assert "model 2" in capsys.readouterr().out
