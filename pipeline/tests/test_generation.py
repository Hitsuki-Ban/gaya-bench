from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

from gaya_pipeline import cli, generation
from gaya_pipeline.adapters.base import (
    Capabilities,
    LineJob,
    ModelProfile,
    TakeContext,
    TakeRecipe,
)
from gaya_pipeline.adapters.dummy import DummyAdapter
from gaya_pipeline.audio import (
    AudioProbe,
    AudioTools,
    EncodedLoudnessReport,
    NormalizedLoudnessReport,
)
from gaya_pipeline.generation import (
    GenerationError,
    GenerationFailureRecord,
    GenerationRecord,
    GenerationSummary,
    run_generation,
)
from gaya_pipeline.take_ledger import read_ledger
from gaya_pipeline.take_sidecar import validate_take_sidecar


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"
VOICES_DIR = REPOSITORY_ROOT / "assets" / "voices"


def _scenarios(tmp_path: Path) -> Path:
    scenarios_dir = tmp_path / "scenarios"
    schema_dir = scenarios_dir / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        SCENARIOS_DIR / "schema" / "scenario.schema.json",
        schema_dir / "scenario.schema.json",
    )
    shutil.copy2(
        SCENARIOS_DIR / "tavern-night.yaml",
        scenarios_dir / "tavern-night.yaml",
    )
    voices_dir = tmp_path / "assets" / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("metadata.schema.json", "metadata.yaml"):
        shutil.copy2(VOICES_DIR / filename, voices_dir / filename)
    return scenarios_dir


@pytest.fixture
def fake_audio(monkeypatch: pytest.MonkeyPatch) -> AudioTools:
    tools = AudioTools(
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        ffmpeg_version="ffmpeg version 8.0",
        ffprobe_version="ffprobe version 8.0",
        libopus_encoder=True,
    )

    def probe(_tools: AudioTools, path: Path) -> AudioProbe:
        return AudioProbe(
            codec_name="opus" if path.suffix == ".opus" else "pcm_s16le",
            sample_rate_hz=48_000,
            channels=1,
            duration_sec=1.0,
        )

    def normalize(
        _tools: AudioTools,
        input_wav: Path,
        output_wav: Path,
        _profile: object,
    ) -> NormalizedLoudnessReport:
        output_wav.write_bytes(input_wav.read_bytes() + b"-normalized")
        return NormalizedLoudnessReport(
            integrated_lufs=-18.0,
            true_peak_dbtp=-1.75,
            loudness_range_lu=1.0,
            normalization_type="linear",
        )

    def encode(
        _tools: AudioTools,
        input_wav: Path,
        output_opus: Path,
        _profile: object,
    ) -> None:
        output_opus.write_bytes(input_wav.read_bytes() + b"-opus")

    def measure(
        _tools: AudioTools,
        _path: Path,
        _profile: object,
    ) -> EncodedLoudnessReport:
        return EncodedLoudnessReport(
            integrated_lufs=-18.0,
            true_peak_dbtp=-1.0,
            loudness_range_lu=1.0,
        )

    monkeypatch.setattr(generation, "find_audio_tools", lambda: tools)
    monkeypatch.setattr(generation, "probe_audio", probe)
    monkeypatch.setattr(generation, "normalize_wav", normalize)
    monkeypatch.setattr(generation, "encode_opus", encode)
    monkeypatch.setattr(generation, "measure_encoded_opus", measure)
    return tools


class FakeStochasticAdapter:
    profile = ModelProfile(
        id="fake-stochastic",
        name="Fake Stochastic",
        version="1",
        license_note="test",
        capabilities=Capabilities(
            emotion=False,
            voice_prompt=False,
            clone=False,
            nonverbal=False,
            reading=False,
        ),
    )

    def __init__(
        self,
        *,
        input_salt: str = "v1",
        output_salt: str = "stable",
        fail_indices: set[int] | None = None,
        wrong_sampling_indices: set[int] | None = None,
    ) -> None:
        self.input_salt = input_salt
        self.output_salt = output_salt
        self.fail_indices = fail_indices or set()
        self.wrong_sampling_indices = wrong_sampling_indices or set()
        self.prepare_count = 0
        self.generate_contexts: list[TakeContext] = []

    def take_recipe(self) -> TakeRecipe:
        return TakeRecipe(
            version="seed-only-v1",
            seed_policy="derived-sha256-v1",
            single_take_seed=42,
            seed_range=(0, 2**32 - 1),
            sampling=(("temperature", 0.8),),
            supports_multiple=True,
        )

    def prepare(
        self,
        jobs: Sequence[LineJob],
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        del jobs, artifacts_dir, voices_dir
        self.prepare_count += 1

    def generation_params(self) -> Mapping[str, Any]:
        return {"backend": "fake-v1"}

    def generation_input(
        self,
        job: LineJob,
        take_context: TakeContext,
    ) -> Mapping[str, Any]:
        return {
            "text": str(job.line["text"]),
            "input_salt": self.input_salt,
            "seed_seen": take_context.seed,
        }

    def generate(
        self,
        job: LineJob,
        take_context: TakeContext,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        del job
        self.generate_contexts.append(take_context)
        if take_context.index in self.fail_indices:
            raise RuntimeError(f"failed index {take_context.index}")
        output_wav.write_bytes(
            f"{take_context.seed}:{self.output_salt}".encode(),
        )
        sampling = take_context.sampling_dict()
        if take_context.index in self.wrong_sampling_indices:
            sampling = {"temperature": 9.9}
        return {
            "seed": take_context.seed,
            "sampling": sampling,
        }


def _run_fake(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter: FakeStochasticAdapter,
    takes: int = 3,
    seed_base: int = 42,
    force: bool = False,
) -> GenerationSummary:
    monkeypatch.setattr(generation, "create_adapter", lambda _model: adapter)
    return run_generation(
        model_id=adapter.profile.id,
        scenarios_dir=_scenarios(tmp_path),
        artifacts_dir=tmp_path / "artifacts",
        scenario_id="tavern-night",
        line_id="barmaid-001",
        takes=takes,
        seed_base=seed_base,
        force=force,
    )


def test_line_jobにcharacter_kindをそのまま渡す(tmp_path: Path) -> None:
    scenarios_dir = _scenarios(tmp_path)
    scenario_path = scenarios_dir / "tavern-night.yaml"
    document = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    character = document["characters"][0]
    character["kind"] = "machine"
    scenario_path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    jobs, _sources = generation._load_jobs(
        scenarios_dir,
        scenario_id="tavern-night",
        line_id="barmaid-001",
    )

    assert jobs[0].character == character
    assert jobs[0].character["kind"] == "machine"


def test_n_takeは固有path_sidecar_ledgerとidentityを生成する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_audio: AudioTools,
) -> None:
    del fake_audio
    summary = _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=FakeStochasticAdapter(),
    )

    assert summary.generated_count == 3
    assert summary.skipped_count == 0
    assert summary.failed_count == 0
    ledger = read_ledger(summary.ledger_path)
    assert [attempt["take_index"] for attempt in ledger["attempts"]] == [1, 2, 3]
    assert len({attempt["generation"]["seed"] for attempt in ledger["attempts"]}) == 3
    assert len({attempt["generation_input_sha256"] for attempt in ledger["attempts"]}) == 3
    assert len({attempt["take_id"] for attempt in ledger["attempts"]}) == 3

    paths: set[str] = set()
    for attempt in ledger["attempts"]:
        assert attempt["status"] == "generated"
        for kind in ("wav", "opus"):
            relative = attempt["audio"][f"{kind}_path"]
            assert relative not in paths
            paths.add(relative)
            assert (summary.ledger_path.parent / relative).is_file()
        sidecar_path = (
            summary.ledger_path.parent
            / attempt["audio"]["opus_path"]
        ).with_suffix(".json")
        sidecar = validate_take_sidecar(
            json.loads(sidecar_path.read_text(encoding="utf-8")),
        )
        assert sidecar["run_id"] == summary.run_id
        assert sidecar["take_id"] == attempt["take_id"]


def test_whole_run_cacheとforceは既存provenanceを変更しない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_audio: AudioTools,
) -> None:
    del fake_audio
    first_adapter = FakeStochasticAdapter()
    first = _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=first_adapter,
        takes=2,
    )
    first_bytes = {
        path.relative_to(first.ledger_path.parent).as_posix(): path.read_bytes()
        for path in first.ledger_path.parent.rglob("*")
        if path.is_file()
    }

    cached_adapter = FakeStochasticAdapter()
    cached = _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=cached_adapter,
        takes=2,
    )
    assert cached.run_id == first.run_id
    assert cached.generated_count == 0
    assert cached.skipped_count == 2
    assert cached_adapter.generate_contexts == []

    forced_adapter = FakeStochasticAdapter()
    forced = _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=forced_adapter,
        takes=2,
        force=True,
    )
    assert forced.run_id != first.run_id
    assert [record.take_id for record in forced.records] == [
        record.take_id for record in first.records
    ]
    assert {
        path.relative_to(first.ledger_path.parent).as_posix(): path.read_bytes()
        for path in first.ledger_path.parent.rglob("*")
        if path.is_file()
    } == first_bytes


def test_seed_baseとresolved_input変更は新しいrunとidentityになる(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_audio: AudioTools,
) -> None:
    del fake_audio
    first = _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=FakeStochasticAdapter(),
        takes=1,
    )
    seed_changed = _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=FakeStochasticAdapter(),
        takes=1,
        seed_base=43,
    )
    input_changed = _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=FakeStochasticAdapter(input_salt="v2"),
        takes=1,
    )

    assert len({first.run_id, seed_changed.run_id, input_changed.run_id}) == 3
    assert len(
        {
            first.records[0].take_id,
            seed_changed.records[0].take_id,
            input_changed.records[0].take_id,
        },
    ) == 3


def test_forceで同inputから異なる音声があればcacheを自動選択しない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_audio: AudioTools,
) -> None:
    del fake_audio
    _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=FakeStochasticAdapter(output_salt="one"),
        takes=1,
    )
    _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=FakeStochasticAdapter(output_salt="two"),
        takes=1,
        force=True,
    )

    with pytest.raises(GenerationError, match="自動選択"):
        _run_fake(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            adapter=FakeStochasticAdapter(output_salt="three"),
            takes=1,
        )


def test_generation_failureは一度だけ記録して残りを続行する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_audio: AudioTools,
) -> None:
    del fake_audio
    adapter = FakeStochasticAdapter(fail_indices={2})
    summary = _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=adapter,
    )

    assert [context.index for context in adapter.generate_contexts] == [1, 2, 3]
    assert summary.generated_count == 2
    assert summary.failed_count == 1
    assert summary.failures[0].take_index == 2
    statuses = [
        attempt["status"]
        for attempt in read_ledger(summary.ledger_path)["attempts"]
    ]
    assert statuses == ["generated", "generation_failed", "generated"]


def test_generated_checkpoint_failureは成果物を除去して残りを続行する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_audio: AudioTools,
) -> None:
    del fake_audio
    adapter = FakeStochasticAdapter()
    real_write_ledger = generation.write_ledger_atomic
    write_count = 0

    def flaky_write_ledger(path: Path, ledger: dict[str, Any]) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise OSError("transient checkpoint failure")
        real_write_ledger(path, ledger)

    monkeypatch.setattr(generation, "write_ledger_atomic", flaky_write_ledger)
    summary = _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=adapter,
        takes=2,
    )

    assert [context.index for context in adapter.generate_contexts] == [1, 2]
    assert summary.generated_count == 1
    assert summary.failed_count == 1
    assert "ledger checkpoint" in summary.failures[0].message
    ledger = read_ledger(summary.ledger_path)
    assert [attempt["status"] for attempt in ledger["attempts"]] == [
        "generation_failed",
        "generated",
    ]
    assert not list(summary.ledger_path.parent.rglob("take-0001.*"))


def test_realized_sampling不一致はgeneration_failedにする(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_audio: AudioTools,
) -> None:
    del fake_audio
    summary = _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=FakeStochasticAdapter(wrong_sampling_indices={1}),
        takes=1,
    )

    assert summary.failed_count == 1
    assert "realized sampling" in summary.failures[0].message
    attempt = read_ledger(summary.ledger_path)["attempts"][0]
    assert attempt["status"] == "generation_failed"
    assert not list(summary.ledger_path.parent.rglob("take-0001.json"))


def test_prepare中のscenario変更はrun作成前に拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_audio: AudioTools,
) -> None:
    del fake_audio
    scenarios_dir = _scenarios(tmp_path)
    scenario_path = scenarios_dir / "tavern-night.yaml"
    adapter = FakeStochasticAdapter()

    def mutate_scenario(
        jobs: Sequence[LineJob],
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        del jobs, artifacts_dir, voices_dir
        scenario_path.write_text(
            scenario_path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(adapter, "prepare", mutate_scenario)
    monkeypatch.setattr(generation, "create_adapter", lambda _model: adapter)

    with pytest.raises(GenerationError, match="scenario source が変更"):
        run_generation(
            model_id=adapter.profile.id,
            scenarios_dir=scenarios_dir,
            artifacts_dir=tmp_path / "artifacts",
            scenario_id="tavern-night",
            line_id="barmaid-001",
            takes=1,
            seed_base=42,
        )
    assert adapter.generate_contexts == []
    assert not (tmp_path / "artifacts" / "takes").exists()


def test_unsupported_nとseed_collisionはprepare前に拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_audio: AudioTools,
) -> None:
    del fake_audio

    class SpyDummy(DummyAdapter):
        def __init__(self) -> None:
            self.prepare_count = 0

        def prepare(
            self,
            jobs: Sequence[LineJob],
            artifacts_dir: Path,
            voices_dir: Path,
        ) -> None:
            del jobs, artifacts_dir, voices_dir
            self.prepare_count += 1

    deterministic = SpyDummy()
    monkeypatch.setattr(generation, "create_adapter", lambda _model: deterministic)
    with pytest.raises(GenerationError, match="複数 take"):
        run_generation(
            model_id="dummy",
            scenarios_dir=_scenarios(tmp_path),
            artifacts_dir=tmp_path / "artifacts",
            scenario_id="tavern-night",
            line_id="barmaid-001",
            takes=2,
            seed_base=42,
        )
    assert deterministic.prepare_count == 0
    assert not (tmp_path / "artifacts" / "takes").exists()

    stochastic = FakeStochasticAdapter()
    monkeypatch.setattr(generation, "create_adapter", lambda _model: stochastic)
    monkeypatch.setattr(generation, "derive_seed", lambda **_arguments: 7)
    with pytest.raises(GenerationError, match="seed が重複"):
        run_generation(
            model_id=stochastic.profile.id,
            scenarios_dir=_scenarios(tmp_path / "collision"),
            artifacts_dir=tmp_path / "collision" / "artifacts",
            scenario_id="tavern-night",
            line_id="barmaid-001",
            takes=2,
            seed_base=42,
        )
    assert stochastic.prepare_count == 0


def test_public_manifestは読まず書かず存在しなくても生成できる(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_audio: AudioTools,
) -> None:
    del fake_audio
    manifest_path = tmp_path / "data" / "manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_bytes(b"legacy-public-manifest\n")

    _run_fake(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        adapter=FakeStochasticAdapter(),
        takes=1,
    )

    assert manifest_path.read_bytes() == b"legacy-public-manifest\n"
    manifest_path.unlink()
    _run_fake(
        tmp_path=tmp_path / "missing",
        monkeypatch=monkeypatch,
        adapter=FakeStochasticAdapter(),
        takes=1,
    )
    assert not (tmp_path / "missing" / "data" / "manifest.json").exists()


@pytest.mark.parametrize(
    ("takes", "seed_base", "message"),
    [
        (0, 42, "--takes"),
        (True, 42, "--takes"),
        (1, True, "--seed-base"),
    ],
)
def test_cli_inputのboolと範囲を拒否(
    tmp_path: Path,
    takes: object,
    seed_base: object,
    message: str,
) -> None:
    with pytest.raises(GenerationError, match=message):
        run_generation(
            model_id="dummy",
            scenarios_dir=_scenarios(tmp_path),
            artifacts_dir=tmp_path / "artifacts",
            takes=takes,  # type: ignore[arg-type]
            seed_base=seed_base,  # type: ignore[arg-type]
        )


def test_gen_cliはselectors_take_seed_forceをroutingしてrunを表示する(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_run_generation(**arguments: object) -> GenerationSummary:
        captured.update(arguments)
        return GenerationSummary(
            run_id="20260729T000000000000Z-dummy-n3",
            ledger_path=Path("artifacts/takes/run/ledger.json"),
            records=(
                GenerationRecord(
                    scenario_id="tavern-night",
                    line_id="barmaid-001",
                    take_index=1,
                    status="generated",
                    generation_seconds=0.25,
                    rtf=0.5,
                    take_id="a" * 64,
                ),
            ),
            failures=(),
            elapsed_seconds=0.3,
        )

    monkeypatch.setattr(cli, "run_generation", fake_run_generation)
    result = cli.main(
        [
            "gen",
            "--model",
            "dummy",
            "--scenario",
            "tavern-night",
            "--line",
            "barmaid-001",
            "--takes",
            "3",
            "--seed-base",
            "77",
            "--force",
        ],
    )

    assert result == 0
    assert captured["takes"] == 3
    assert captured["seed_base"] == 77
    assert captured["force"] is True
    assert "manifest_path" not in captured
    output = capsys.readouterr().out
    assert "Run ID:" in output
    assert "take-0001" in output


def test_gen_cliはfailure_summaryを最後に出して非zeroを返す(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "run_generation",
        lambda **_arguments: GenerationSummary(
            run_id="run",
            ledger_path=Path("artifacts/takes/run/ledger.json"),
            records=(),
            failures=(
                GenerationFailureRecord(
                    scenario_id="tavern-night",
                    line_id="barmaid-001",
                    take_index=2,
                    message="backend error",
                ),
            ),
            elapsed_seconds=0.5,
        ),
    )

    result = cli.main(
        [
            "gen",
            "--model",
            "dummy",
            "--takes",
            "1",
            "--seed-base",
            "42",
        ],
    )

    assert result == 1
    output = capsys.readouterr().out
    assert "失敗サマリ:" in output
    assert "take-0002: backend error" in output
    assert output.rstrip().endswith("所要時間 0.500s")


def test_adapter初期化errorはcliの安定したerrorへ変換する(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_adapter(_model: str) -> FakeStochasticAdapter:
        raise RuntimeError("model extra is missing")

    monkeypatch.setattr(generation, "create_adapter", fail_adapter)

    result = cli.main(
        [
            "gen",
            "--model",
            "fake-stochastic",
            "--takes",
            "1",
            "--seed-base",
            "42",
        ],
    )

    assert result == 1
    error = capsys.readouterr().err
    assert error == "ERROR: model extra is missing\n"
    assert "Traceback" not in error
