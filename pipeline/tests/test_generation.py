from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from gaya_pipeline import cli, generation
from gaya_pipeline.adapters.base import LineJob
from gaya_pipeline.adapters.dummy import DummyAdapter
from gaya_pipeline.audio import PostprocessProfile, find_audio_tools, probe_audio
from gaya_pipeline.generation import (
    GenerationError,
    GenerationRecord,
    GenerationSummary,
    run_generation,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"
VOICES_DIR = REPOSITORY_ROOT / "assets" / "voices"


def _two_scenarios(tmp_path: Path) -> Path:
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
    for scenario_id in ("tavern-night", "market-day"):
        shutil.copy2(
            SCENARIOS_DIR / f"{scenario_id}.yaml",
            scenarios_dir / f"{scenario_id}.yaml",
        )
    return scenarios_dir


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_line_jobにcharacter_kindをそのまま渡す(tmp_path: Path) -> None:
    scenarios_dir = _two_scenarios(tmp_path)
    scenario_path = scenarios_dir / "tavern-night.yaml"
    document = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    character = document["characters"][0]
    character["kind"] = "machine"
    scenario_path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    jobs = generation._load_jobs(
        scenarios_dir,
        scenario_id="tavern-night",
        line_id="barmaid-001",
    )

    assert len(jobs) == 1
    assert jobs[0].character == character
    assert jobs[0].character["kind"] == "machine"


def test_dummy_two_scenario_e2e_and_idempotency(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenarios_dir = _two_scenarios(tmp_path)
    artifacts_dir = tmp_path / "artifacts"
    manifest_path = tmp_path / "data" / "manifest.json"

    first = run_generation(
        model_id="dummy",
        scenarios_dir=scenarios_dir,
        artifacts_dir=artifacts_dir,
        manifest_path=manifest_path,
    )
    cli._print_generation_summary(first)
    first_log = capsys.readouterr().out

    assert first.generated_count == 12
    assert first.skipped_count == 0
    assert first.manifest_updated is True
    assert "生成" in first_log
    assert "RTF=" in first_log
    assert "所要時間" in first_log

    wav_files = sorted(artifacts_dir.rglob("*-dry.wav"))
    opus_files = sorted(artifacts_dir.rglob("*-dry.opus"))
    metadata_files = sorted(artifacts_dir.rglob("*-dry.json"))
    assert len(wav_files) == 12
    assert len(opus_files) == 12
    assert len(metadata_files) == 12
    assert not list(artifacts_dir.rglob("*-source.wav"))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["format_version"] == 2
    assert len(manifest["models"]) == 1
    assert manifest["models"][0]["id"] == "dummy"
    assert manifest["models"][0]["capabilities"] == {
        "emotion": False,
        "voice_prompt": False,
        "clone": False,
        "nonverbal": False,
        "reading": False,
    }
    assert len(manifest["clips"]) == 12
    assert manifest["failures"] == []
    for clip in manifest["clips"]:
        opus_path = artifacts_dir / clip["path"]
        assert opus_path.is_file()
        assert clip["sha256"] == _sha256(opus_path)
        assert clip["rtf"] >= 0

    tools = find_audio_tools()
    opus_probe = probe_audio(tools, opus_files[0])
    assert opus_probe.codec_name == "opus"
    assert opus_probe.sample_rate_hz == 48_000
    assert opus_probe.channels == 1

    for metadata_path in metadata_files:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        loudness = metadata["loudness"]
        assert loudness["integrated_lufs"] == pytest.approx(-18.0, abs=0.2)
        assert loudness["true_peak_dbtp"] <= -0.9
        assert loudness["normalization_type"] in {"linear", "dynamic"}

    tracked_outputs = [manifest_path, *wav_files, *opus_files, *metadata_files]
    mtimes = {path: path.stat().st_mtime_ns for path in tracked_outputs}
    manifest_bytes = manifest_path.read_bytes()

    second = run_generation(
        model_id="dummy",
        scenarios_dir=scenarios_dir,
        artifacts_dir=artifacts_dir,
        manifest_path=manifest_path,
    )
    cli._print_generation_summary(second)
    second_log = capsys.readouterr().out

    assert second.generated_count == 0
    assert second.skipped_count == 12
    assert second.manifest_updated is False
    assert "スキップ" in second_log
    assert manifest_path.read_bytes() == manifest_bytes
    assert {path: path.stat().st_mtime_ns for path in tracked_outputs} == mtimes


def test_selector_hash_invalidation_force_and_failed_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios_dir = _two_scenarios(tmp_path)
    artifacts_dir = tmp_path / "artifacts"
    manifest_path = tmp_path / "data" / "manifest.json"

    def run_selected(
        *,
        line_id: str = "barmaid-001",
        force: bool = False,
    ) -> GenerationSummary:
        return run_generation(
            model_id="dummy",
            scenarios_dir=scenarios_dir,
            artifacts_dir=artifacts_dir,
            manifest_path=manifest_path,
            scenario_id="tavern-night",
            line_id=line_id,
            force=force,
        )

    first = run_selected()
    second = run_selected()
    assert first.generated_count == 1
    assert second.skipped_count == 1

    scenario_path = scenarios_dir / "tavern-night.yaml"
    document = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    document["lines"][0]["text"] += "！"
    scenario_path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    changed = run_selected()
    forced = run_selected(force=True)
    assert changed.generated_count == 1
    assert forced.generated_count == 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["clips"]) == 1

    output_dir = artifacts_dir / "audio" / "dummy" / "tavern-night"
    stable_artifact_paths = [
        output_dir / "barmaid-001-dry.wav",
        output_dir / "barmaid-001-dry.opus",
        output_dir / "barmaid-001-dry.json",
    ]
    stable_bytes = {path: path.read_bytes() for path in stable_artifact_paths}
    metadata_path = output_dir / "barmaid-001-dry.json"
    broken_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    del broken_metadata["loudness"]
    metadata_path.write_text(
        json.dumps(broken_metadata, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(GenerationError, match="生成メタの項目"):
        run_selected()
    forced_after_broken_metadata = run_selected(force=True)
    assert forced_after_broken_metadata.generated_count == 1
    stable_bytes = {path: path.read_bytes() for path in stable_artifact_paths}

    original_generate = DummyAdapter.generate

    def fail_generation(
        adapter: DummyAdapter,
        job: object,
        output_wav: Path,
    ) -> dict[str, object]:
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(DummyAdapter, "generate", fail_generation)
    with pytest.raises(
        GenerationError,
        match="tavern-night/barmaid-001.*CUDA out of memory",
    ):
        run_selected(force=True)
    assert {path: path.read_bytes() for path in stable_artifact_paths} == stable_bytes
    assert not list(output_dir.glob("*.pending.*"))
    failed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert failed_manifest["clips"] == []
    assert failed_manifest["failures"] == [
        {
            "model": "dummy",
            "scenario": "tavern-night",
            "line": "barmaid-001",
            "variant": "dry",
            "reason": "generation_failed",
        },
    ]

    monkeypatch.setattr(DummyAdapter, "generate", original_generate)
    retried = run_selected()
    assert retried.generated_count == 1
    assert retried.skipped_count == 0
    recovered_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(recovered_manifest["clips"]) == 1
    assert recovered_manifest["failures"] == []

    with pytest.raises(GenerationError, match="--scenario"):
        run_generation(
            model_id="dummy",
            scenarios_dir=scenarios_dir,
            artifacts_dir=artifacts_dir,
            manifest_path=manifest_path,
            line_id="barmaid-001",
        )
    with pytest.raises(GenerationError, match="line id"):
        run_selected(line_id="missing-line")


def test_adapter_boundary_errors_are_controlled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios_dir = _two_scenarios(tmp_path)
    artifacts_dir = tmp_path / "artifacts"
    manifest_path = tmp_path / "data" / "manifest.json"

    def run_selected() -> GenerationSummary:
        return run_generation(
            model_id="dummy",
            scenarios_dir=scenarios_dir,
            artifacts_dir=artifacts_dir,
            manifest_path=manifest_path,
            scenario_id="tavern-night",
            line_id="barmaid-001",
        )

    original_create_adapter = generation.create_adapter

    def fail_initialization(model_id: str) -> DummyAdapter:
        raise RuntimeError(f"{model_id}: CUDA initialization failed")

    monkeypatch.setattr(generation, "create_adapter", fail_initialization)
    with pytest.raises(
        GenerationError,
        match="adapter 初期化.*CUDA initialization failed",
    ):
        run_selected()

    monkeypatch.setattr(generation, "create_adapter", original_create_adapter)

    def fail_preparation(
        adapter: DummyAdapter,
        jobs: object,
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        del adapter, jobs, artifacts_dir, voices_dir
        raise RuntimeError("CUDA preparation failed")

    monkeypatch.setattr(DummyAdapter, "prepare", fail_preparation)
    with pytest.raises(
        GenerationError,
        match="adapter 準備.*CUDA preparation failed",
    ):
        run_selected()

    monkeypatch.undo()

    def fail_input(adapter: DummyAdapter, job: object) -> dict[str, object]:
        raise RuntimeError("prompt preprocessing failed")

    monkeypatch.setattr(DummyAdapter, "generation_input", fail_input)
    with pytest.raises(
        GenerationError,
        match="tavern-night/barmaid-001.*adapter 入力構築.*prompt preprocessing",
    ):
        run_selected()


def test_adapter_prepare_runs_once_before_generation_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios_dir = _two_scenarios(tmp_path)
    events: list[str] = []
    original_prepare = DummyAdapter.prepare
    original_generation_input = DummyAdapter.generation_input

    def record_prepare(
        adapter: DummyAdapter,
        jobs: object,
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        events.append(f"prepare:{voices_dir.as_posix()}")
        original_prepare(adapter, jobs, artifacts_dir, voices_dir)

    def record_generation_input(
        adapter: DummyAdapter,
        job: LineJob,
    ) -> Mapping[str, Any]:
        events.append(f"input:{job.line_id}")
        return original_generation_input(adapter, job)

    monkeypatch.setattr(DummyAdapter, "prepare", record_prepare)
    monkeypatch.setattr(
        DummyAdapter,
        "generation_input",
        record_generation_input,
    )

    summary = run_generation(
        model_id="dummy",
        scenarios_dir=scenarios_dir,
        artifacts_dir=tmp_path / "artifacts",
        manifest_path=tmp_path / "data" / "manifest.json",
        scenario_id="tavern-night",
    )

    assert summary.generated_count == 6
    assert events[0] == (
        f"prepare:{(scenarios_dir.parent / 'assets' / 'voices').as_posix()}"
    )
    assert sum(event.startswith("prepare:") for event in events) == 1
    assert events[1:] == [
        "input:barmaid-001",
        "input:barmaid-002",
        "input:drunkard-001",
        "input:drunkard-002",
        "input:old-regular-001",
        "input:old-regular-002",
    ]


def test_postprocess_algorithm_version_invalidates_cached_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios_dir = _two_scenarios(tmp_path)
    artifacts_dir = tmp_path / "artifacts"
    manifest_path = tmp_path / "data" / "manifest.json"

    monkeypatch.setattr(
        generation,
        "PostprocessProfile",
        lambda: PostprocessProfile(algorithm_version=1),
    )
    first = run_generation(
        model_id="dummy",
        scenarios_dir=scenarios_dir,
        artifacts_dir=artifacts_dir,
        manifest_path=manifest_path,
        scenario_id="tavern-night",
        line_id="barmaid-001",
    )
    monkeypatch.setattr(generation, "PostprocessProfile", PostprocessProfile)
    upgraded = run_generation(
        model_id="dummy",
        scenarios_dir=scenarios_dir,
        artifacts_dir=artifacts_dir,
        manifest_path=manifest_path,
        scenario_id="tavern-night",
        line_id="barmaid-001",
    )

    assert first.generated_count == 1
    assert upgraded.generated_count == 1
    metadata_path = (
        artifacts_dir / "audio" / "dummy" / "tavern-night" / "barmaid-001-dry.json"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["postprocess"]["algorithm_version"] == 2


def test_later_batch_failure_keeps_manifest_in_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios_dir = _two_scenarios(tmp_path)
    artifacts_dir = tmp_path / "artifacts"
    manifest_path = tmp_path / "data" / "manifest.json"

    def run_scenario(*, force: bool = False) -> GenerationSummary:
        return run_generation(
            model_id="dummy",
            scenarios_dir=scenarios_dir,
            artifacts_dir=artifacts_dir,
            manifest_path=manifest_path,
            scenario_id="tavern-night",
            force=force,
        )

    baseline = run_scenario()
    assert baseline.generated_count == 6
    baseline_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    baseline_first_sha = baseline_manifest["clips"][0]["sha256"]

    scenario_path = scenarios_dir / "tavern-night.yaml"
    document = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    document["lines"][0]["text"] += "！"
    scenario_path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    original_generate = DummyAdapter.generate
    generation_count = 0

    def fail_second_generation(
        adapter: DummyAdapter,
        job: LineJob,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        nonlocal generation_count
        generation_count += 1
        if generation_count == 2:
            raise RuntimeError("CUDA out of memory later")
        return original_generate(adapter, job, output_wav)

    monkeypatch.setattr(DummyAdapter, "generate", fail_second_generation)
    with pytest.raises(
        GenerationError,
        match="tavern-night/barmaid-002.*CUDA out of memory later",
    ):
        run_scenario(force=True)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["clips"]) == 5
    assert manifest["failures"] == [
        {
            "model": "dummy",
            "scenario": "tavern-night",
            "line": "barmaid-002",
            "variant": "dry",
            "reason": "generation_failed",
        },
    ]
    assert manifest["clips"][0]["sha256"] != baseline_first_sha
    for clip in manifest["clips"]:
        assert clip["sha256"] == _sha256(artifacts_dir / clip["path"])


def test_first_batch_failure_stops_and_preserves_unprocessed_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios_dir = _two_scenarios(tmp_path)
    artifacts_dir = tmp_path / "artifacts"
    manifest_path = tmp_path / "data" / "manifest.json"

    baseline = run_generation(
        model_id="dummy",
        scenarios_dir=scenarios_dir,
        artifacts_dir=artifacts_dir,
        manifest_path=manifest_path,
        scenario_id="tavern-night",
    )
    assert baseline.generated_count == 6
    old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unprocessed = {
        clip["line"]: clip
        for clip in old_manifest["clips"]
        if clip["line"] != "barmaid-001"
    }
    generation_count = 0

    def fail_first_generation(
        adapter: DummyAdapter,
        job: LineJob,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        nonlocal generation_count
        generation_count += 1
        raise RuntimeError("first job failed")

    monkeypatch.setattr(DummyAdapter, "generate", fail_first_generation)
    with pytest.raises(
        GenerationError,
        match="tavern-night/barmaid-001.*first job failed",
    ):
        run_generation(
            model_id="dummy",
            scenarios_dir=scenarios_dir,
            artifacts_dir=artifacts_dir,
            manifest_path=manifest_path,
            scenario_id="tavern-night",
            force=True,
        )

    output = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert generation_count == 1
    assert {clip["line"]: clip for clip in output["clips"]} == unprocessed
    assert output["failures"][0]["line"] == "barmaid-001"


def test_manifest_write_failure_preserves_original_generation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios_dir = _two_scenarios(tmp_path)
    artifacts_dir = tmp_path / "artifacts"
    manifest_path = tmp_path / "data" / "manifest.json"
    original_update_manifest = generation.update_manifest

    def fail_generation(
        adapter: DummyAdapter,
        job: LineJob,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        raise RuntimeError("CUDA original failure")

    def fail_failure_write(
        path: Path,
        manifest: dict[str, Any],
        profile: object,
        clips: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        **scope: object,
    ) -> bool:
        if failures:
            raise OSError("manifest disk failure")
        return original_update_manifest(
            path,
            manifest,
            profile,  # type: ignore[arg-type]
            clips,
            failures,
            **scope,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(DummyAdapter, "generate", fail_generation)
    monkeypatch.setattr(generation, "update_manifest", fail_failure_write)

    with pytest.raises(GenerationError) as raised:
        run_generation(
            model_id="dummy",
            scenarios_dir=scenarios_dir,
            artifacts_dir=artifacts_dir,
            manifest_path=manifest_path,
            scenario_id="tavern-night",
            line_id="barmaid-001",
            force=True,
        )

    message = str(raised.value)
    assert "CUDA original failure" in message
    assert "manifest disk failure" in message


def test_gen_cli_routes_selectors_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    received: dict[str, object] = {}

    def fake_run_generation(**arguments: object) -> GenerationSummary:
        received.update(arguments)
        return GenerationSummary(
            records=(
                GenerationRecord(
                    scenario_id="tavern-night",
                    line_id="barmaid-001",
                    status="generated",
                    generation_seconds=0.25,
                    rtf=0.5,
                ),
            ),
            elapsed_seconds=0.5,
            manifest_updated=True,
        )

    monkeypatch.setattr(cli, "run_generation", fake_run_generation)

    exit_code = cli.main(
        [
            "gen",
            "--model",
            "dummy",
            "--scenario",
            "tavern-night",
            "--line",
            "barmaid-001",
            "--force",
        ],
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert received["model_id"] == "dummy"
    assert received["scenario_id"] == "tavern-night"
    assert received["line_id"] == "barmaid-001"
    assert received["force"] is True
    assert "生成=0.250s RTF=0.500" in output
    assert "所要時間 0.500s" in output
