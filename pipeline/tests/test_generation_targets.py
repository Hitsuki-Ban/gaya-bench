from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaya_pipeline import generation
from gaya_pipeline.adapters.base import (
    Capabilities,
    LineJob,
    ModelProfile,
    TakeContext,
    TakeRecipe,
)
from gaya_pipeline.audio import AudioTools
from gaya_pipeline.generation import (
    GenerationError,
    _ledger_source,
    _load_jobs,
    _validate_cli_inputs,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"


def test_discrete_targetsはcanonical順で対象jobとsourceだけを選ぶ() -> None:
    targets = _validate_cli_inputs(
        scenario_id=None,
        line_id=None,
        target_lines=(
            ("west-crowd", "isogi-shinshi-002"),
            ("battlefield-camp", "wounded-001"),
        ),
        takes=4,
        seed_base=104,
    )
    assert targets == (
        ("battlefield-camp", "wounded-001"),
        ("west-crowd", "isogi-shinshi-002"),
    )

    jobs, sources = _load_jobs(
        SCENARIOS_DIR,
        scenario_id=None,
        line_id=None,
        target_lines=targets,
    )

    assert [(job.scenario_id, job.line_id) for job in jobs] == list(targets)
    assert [source.path.name for source in sources] == [
        "battlefield-camp.yaml",
        "west-crowd.yaml",
    ]
    source = _ledger_source(
        jobs=jobs,
        model_id="cosyvoice3-0.5b-2512",
        takes=4,
        seed_base=104,
        recipe=TakeRecipe(
            version="seed-only-v1",
            seed_policy="derived-sha256-v1",
            single_take_seed=42,
            seed_range=(0, 2**32 - 1),
            sampling=(),
            supports_multiple=True,
        ),
        scenario_sources=sources,
    )
    assert [
        (group["scenario"], group["line"])
        for group in source["groups"]
    ] == list(targets)


@pytest.mark.parametrize(
    ("scenario_id", "line_id", "target_lines", "message"),
    [
        (
            "battlefield-camp",
            None,
            (("battlefield-camp", "wounded-001"),),
            "同時に指定",
        ),
        (None, None, (), "空にできません"),
        (
            None,
            None,
            (
                ("battlefield-camp", "wounded-001"),
                ("battlefield-camp", "wounded-001"),
            ),
            "重複",
        ),
    ],
)
def test_discrete_targetsは曖昧または空または重複指定を拒否する(
    scenario_id: str | None,
    line_id: str | None,
    target_lines: tuple[tuple[str, str], ...],
    message: str,
) -> None:
    with pytest.raises(GenerationError, match=message):
        _validate_cli_inputs(
            scenario_id=scenario_id,
            line_id=line_id,
            target_lines=target_lines,
            takes=4,
            seed_base=104,
        )


def test_discrete_targetsは存在しないlineを拒否する() -> None:
    with pytest.raises(GenerationError, match="target line が見つかりません"):
        _load_jobs(
            SCENARIOS_DIR,
            scenario_id=None,
            line_id=None,
            target_lines=(("battlefield-camp", "missing-001"),),
        )


def test_explicit_voices_dirをadapter_prepareへそのまま渡す(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Path] = {}

    class PrepareProbeAdapter:
        profile = ModelProfile(
            id="prepare-probe",
            name="Prepare Probe",
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

        def take_recipe(self) -> TakeRecipe:
            return TakeRecipe(
                version="seed-only-v1",
                seed_policy="derived-sha256-v1",
                single_take_seed=42,
                seed_range=(0, 2**32 - 1),
                sampling=(),
                supports_multiple=True,
            )

        def generation_params(self) -> dict[str, Any]:
            return {}

        def prepare(
            self,
            jobs: list[LineJob],
            artifacts_dir: Path,
            voices_dir: Path,
        ) -> None:
            del jobs, artifacts_dir
            captured["voices_dir"] = voices_dir
            raise RuntimeError("prepare probe stop")

        def generation_input(
            self,
            job: LineJob,
            take_context: TakeContext,
        ) -> dict[str, Any]:
            del job, take_context
            return {}

    monkeypatch.setattr(
        generation,
        "validate_scenarios",
        lambda _path, **_kwargs: SimpleNamespace(problems=[]),
    )
    monkeypatch.setattr(
        generation,
        "create_adapter",
        lambda _model: PrepareProbeAdapter(),
    )
    monkeypatch.setattr(
        generation,
        "find_audio_tools",
        lambda: AudioTools(
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
            ffmpeg_version="ffmpeg version 8",
            ffprobe_version="ffprobe version 8",
            libopus_encoder=True,
        ),
    )
    voices_dir = tmp_path / "authoritative-voices"

    with pytest.raises(GenerationError, match="prepare probe stop"):
        generation.run_generation(
            model_id="prepare-probe",
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=tmp_path / "artifacts",
            voices_dir=voices_dir,
            target_lines=(("battlefield-camp", "wounded-001"),),
            takes=4,
            seed_base=104,
        )

    assert captured["voices_dir"] == voices_dir
