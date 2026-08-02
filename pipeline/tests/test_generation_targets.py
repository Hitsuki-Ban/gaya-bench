from __future__ import annotations

import hashlib
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
from gaya_pipeline.audio import AudioTools, PostprocessProfile
from gaya_pipeline.generation import (
    GenerationError,
    _build_attempt_plans,
    _ledger_source,
    _load_jobs,
    _phase_b_source,
    _validate_anchor_receipt,
    _validate_topup_source,
    _validate_cli_inputs,
)
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.take_ledger import write_ledger_atomic


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


def test_role_anchor_selectionは絶対pathだけを受理する(tmp_path: Path) -> None:
    with pytest.raises(GenerationError, match="絶対path"):
        generation.run_generation(
            model_id="dummy",
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=tmp_path,
            takes=1,
            seed_base=1,
            role_anchor_selection_path=Path("relative-selection.json"),
            role_anchor_plan_sha256="a" * 64,
        )


def test_phase_b_sourceはexact_role_epochとprimary契約を要求する() -> None:
    jobs, _sources = _load_jobs(
        SCENARIOS_DIR,
        scenario_id=None,
        line_id=None,
        target_lines=(("battlefield-camp", "wounded-001"),),
    )
    with pytest.raises(GenerationError, match="exact target groups"):
        _phase_b_source(
            model_id="cosyvoice3-0.5b-2512",
            jobs=jobs,
            completion_plan_sha256="a" * 64,
            role_epochs={},
            run_kind="primary",
            supersedes_run_id=None,
            role_anchor_selection_path=None,
            role_anchor_plan_sha256=None,
            role_anchor_selection_sha256=None,
        )
    with pytest.raises(GenerationError, match="primary"):
        _phase_b_source(
            model_id="cosyvoice3-0.5b-2512",
            jobs=jobs,
            completion_plan_sha256="a" * 64,
            role_epochs={("battlefield-camp", "wounded-001"): "b" * 64},
            run_kind="primary",
            supersedes_run_id="old-run",
            role_anchor_selection_path=None,
            role_anchor_plan_sha256=None,
            role_anchor_selection_sha256=None,
        )


def test_phase_b_sourceは同一role内のepoch漂移を拒否する() -> None:
    jobs, _sources = _load_jobs(
        SCENARIOS_DIR,
        scenario_id=None,
        line_id=None,
        target_lines=(
            ("battlefield-camp", "wounded-001"),
            ("battlefield-camp", "wounded-002"),
        ),
    )
    with pytest.raises(GenerationError, match="漂移"):
        _phase_b_source(
            model_id="cosyvoice3-0.5b-2512",
            jobs=jobs,
            completion_plan_sha256="a" * 64,
            role_epochs={
                ("battlefield-camp", "wounded-001"): "b" * 64,
                ("battlefield-camp", "wounded-002"): "c" * 64,
            },
            run_kind="primary",
            supersedes_run_id=None,
            role_anchor_selection_path=None,
            role_anchor_plan_sha256=None,
            role_anchor_selection_sha256=None,
        )


def test_voxcpm2_phase_bは全targetを通常生成する() -> None:
    jobs, _sources = _load_jobs(
        SCENARIOS_DIR,
        scenario_id=None,
        line_id=None,
        target_lines=(("battlefield-camp", "wounded-001"),),
    )
    source = _phase_b_source(
        model_id="voxcpm2",
        jobs=jobs,
        completion_plan_sha256="a" * 64,
        role_epochs={
            ("battlefield-camp", "wounded-001"): "b" * 64,
        },
        run_kind="primary",
        supersedes_run_id=None,
        role_anchor_selection_path=None,
        role_anchor_plan_sha256=None,
        role_anchor_selection_sha256=None,
    )
    assert source["protocol"] == "phase-b-generation-v2"
    assert source["anchor_selection_sha256"] is None
    assert source["anchor_plan_sha256"] is None


def test_anchor_selectionとplan_digestの不一致を生成前に拒否(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs, _sources = _load_jobs(
        SCENARIOS_DIR,
        scenario_id=None,
        line_id=None,
        target_lines=(("battlefield-camp", "wounded-001"),),
    )
    selection = tmp_path / "role-anchor-selection-v1.json"
    selection.write_bytes(b"{}")
    actual_sha = hashlib.sha256(b"{}").hexdigest()
    selection.with_suffix(".sha256").write_bytes(
        f"{actual_sha}\n".encode("ascii"),
    )
    arguments = {
        "model_id": "qwen3-tts-12hz-1.7b",
        "jobs": jobs,
        "completion_plan_sha256": "a" * 64,
        "role_epochs": {
            ("battlefield-camp", "wounded-001"): "b" * 64,
        },
        "run_kind": "primary",
        "supersedes_run_id": None,
        "role_anchor_selection_path": selection,
        "role_anchor_plan_sha256": "d" * 64,
    }
    with pytest.raises(GenerationError, match="selection SHA"):
        _phase_b_source(
            **arguments,
            role_anchor_selection_sha256="c" * 64,
        )
    monkeypatch.setattr(
        "gaya_pipeline.completion_anchor.validate_anchor_selection",
        lambda _document: {"plan_sha256": "d" * 64},
    )
    source = _phase_b_source(
        **arguments,
        role_anchor_selection_sha256=actual_sha,
    )
    assert source["plan_sha256"] == "a" * 64
    assert source["anchor_plan_sha256"] == "d" * 64

    with pytest.raises(GenerationError, match="anchor source plan"):
        _phase_b_source(
            **{
                **arguments,
                "role_anchor_plan_sha256": "e" * 64,
            },
            role_anchor_selection_sha256=actual_sha,
        )


def test_selected_anchor_receiptはepoch_plan_selectionをexact照合する() -> None:
    provenance = {
        "protocol": "phase-b-generation-v2",
        "plan_sha256": "a" * 64,
        "run_kind": "primary",
        "supersedes_run_id": None,
        "anchor_selection_sha256": "b" * 64,
        "anchor_plan_sha256": "e" * 64,
        "target_group": {
            "model": "qwen3-tts-12hz-1.7b",
            "scenario": "battlefield-camp",
            "line": "wounded-001",
            "variant": "dry",
            "role_epoch_sha256": "c" * 64,
        },
    }
    resolved = {
        "reference_control": "selected_voice_design_anchor",
        "selected_anchor": {
            "anchor_selection_sha256": "b" * 64,
            "anchor_plan_sha256": "e" * 64,
            "role_epoch_sha256": "d" * 64,
        },
    }
    with pytest.raises(GenerationError, match="role epoch"):
        _validate_anchor_receipt(
            "qwen3-tts-12hz-1.7b",
            resolved,
            provenance,
        )


def test_phase_b_provenanceはresolved_generation_input_shaへ入る() -> None:
    jobs, _sources = _load_jobs(
        SCENARIOS_DIR,
        scenario_id=None,
        line_id=None,
        target_lines=(("battlefield-camp", "wounded-001"),),
    )
    context = TakeContext.create(
        index=1,
        seed=100,
        recipe_version="test-v1",
        sampling={},
    )

    class InputProbeAdapter:
        profile = ModelProfile(
            id="dummy",
            name="Dummy",
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

        def generation_input(
            self,
            job: LineJob,
            take_context: TakeContext,
        ) -> dict[str, object]:
            del job, take_context
            return {"adapter_input": "fixed"}

    def build(epoch: str) -> object:
        phase_b = _phase_b_source(
            model_id="dummy",
            jobs=jobs,
            completion_plan_sha256="a" * 64,
            role_epochs={
                ("battlefield-camp", "wounded-001"): epoch,
            },
            run_kind="primary",
            supersedes_run_id=None,
            role_anchor_selection_path=None,
            role_anchor_plan_sha256=None,
            role_anchor_selection_sha256=None,
        )
        return _build_attempt_plans(
            adapter=InputProbeAdapter(),  # type: ignore[arg-type]
            jobs=jobs,
            contexts={
                ("battlefield-camp", "wounded-001", 1): context,
            },
            requested_params={},
            profile=PostprocessProfile(),
            tools=AudioTools(
                ffmpeg="ffmpeg",
                ffprobe="ffprobe",
                ffmpeg_version="ffmpeg 8",
                ffprobe_version="ffprobe 8",
                libopus_encoder=True,
            ),
            phase_b=phase_b,
        )[0]

    first = build("b" * 64)
    second = build("c" * 64)
    assert first.generation_input_sha256 != second.generation_input_sha256  # type: ignore[union-attr]
    assert first.phase_b_provenance_sha256 != second.phase_b_provenance_sha256  # type: ignore[union-attr]


def test_topupは明示supersedesと異なるseedを要求する(
    tmp_path: Path,
) -> None:
    group = {
        "model": "dummy",
        "scenario": "battlefield-camp",
        "line": "wounded-001",
        "variant": "dry",
    }
    target_group = {**group, "role_epoch_sha256": "b" * 64}
    primary_phase = {
        "protocol": "phase-b-generation-v2",
        "plan_sha256": "a" * 64,
        "run_kind": "primary",
        "supersedes_run_id": None,
        "anchor_selection_sha256": None,
        "anchor_plan_sha256": None,
        "target_groups": [target_group],
    }
    provenance = {
        "protocol": primary_phase["protocol"],
        "plan_sha256": primary_phase["plan_sha256"],
        "run_kind": primary_phase["run_kind"],
        "supersedes_run_id": primary_phase["supersedes_run_id"],
        "anchor_selection_sha256": primary_phase[
            "anchor_selection_sha256"
        ],
        "anchor_plan_sha256": primary_phase["anchor_plan_sha256"],
        "target_group": target_group,
    }
    run_id = "primary-run"
    ledger = {
        "format_version": 1,
        "run_id": run_id,
        "created_at": "2026-07-31T00:00:00Z",
        "source": {
            "scenario_sha256": "e" * 64,
            "model": "dummy",
            "takes": 1,
            "seed_base": 104,
            "recipe_version": "test-v1",
            "groups": [group],
            "phase_b": primary_phase,
        },
        "attempts": [
            {
                **group,
                "take_index": 1,
                "generation_input_sha256": "f" * 64,
                "phase_b_provenance_sha256": hashlib.sha256(
                    canonical_json(provenance).encode("utf-8"),
                ).hexdigest(),
                "generation": {
                    "status": "planned",
                    "seed": 1,
                    "sampling": {},
                },
                "status": "planned",
            },
        ],
    }
    write_ledger_atomic(
        tmp_path / "takes" / run_id / "ledger.json",
        ledger,
    )
    topup_phase = {
        **primary_phase,
        "run_kind": "topup",
        "supersedes_run_id": run_id,
    }
    with pytest.raises(GenerationError, match="seed_base"):
        _validate_topup_source(
            artifacts_dir=tmp_path,
            source={
                **ledger["source"],
                "phase_b": topup_phase,
            },
        )


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
