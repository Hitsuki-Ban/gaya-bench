from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import gaya_pipeline.completion_listen as completion_listen
from gaya_pipeline.completion_listen import (
    CompletionListeningError,
    CompletionSourceRun,
    PRIMARY_MODELS,
    _load_target_lines,
    _validate_manifest_candidate_authority,
    build_completion_listening_bundle,
    phase_b_generation_binding,
    resolve_completion_sources,
)
from gaya_pipeline.completion_plan import CompletionPlanError, CompletionTarget
from gaya_pipeline.qc_report import QCAuthority
from gaya_pipeline.take_identity import canonical_json, derive_seed, make_take_id


def test_production_generationとlisteningは非正式planを処理前に拒否する(
    tmp_path: Path,
) -> None:
    plan = SimpleNamespace(plan_id="a" * 64, raw_sha256="a" * 64)

    with pytest.raises(CompletionPlanError, match="Phase B production"):
        phase_b_generation_binding(
            plan=plan,
            model="qwen3-tts-12hz-1.7b",
            scenarios_dir=tmp_path / "scenarios",
            anchor_selection_path=tmp_path / "anchor.json",
        )
    with pytest.raises(CompletionPlanError, match="Phase B production"):
        build_completion_listening_bundle(
            plan=plan,
            primary_run_ids=(),
            topup_run_ids=(),
            anchor_selection_path=tmp_path / "anchor.json",
            artifacts_dir=tmp_path / "artifacts",
            scenarios_dir=tmp_path / "scenarios",
            voices_dir=tmp_path / "voices",
            output_dir=tmp_path / "output",
        )


def test_target_linesは重複modelを除いたscenario_line集合を構成する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (tmp_path / "assets" / "voices").mkdir(parents=True)
    monkeypatch.setattr(
        completion_listen,
        "validate_scenario_ids",
        lambda *_args, **_kwargs: SimpleNamespace(problems=()),
    )
    (scenarios / "scene.yaml").write_text(
        """
format_version: 1
id: scene
title: Scene
locale: ja
scene:
  setting: test
characters:
  - id: actor
    name: Actor
    gender: neutral
    age: adult
    voice: Test voice
lines:
  - id: line-001
    character: actor
    text: 台詞
    delivery: 強く
    emotion: neutral
    intensity: 1
""".lstrip(),
        encoding="utf-8",
    )

    scenario_sha256, lines = _load_target_lines(
        scenarios_dir=scenarios,
        voices_dir=tmp_path / "assets" / "voices",
        targets={("scene", "line-001")},
    )

    assert len(scenario_sha256) == 64
    assert lines == [
        {
            "scenario": "scene",
            "line": "line-001",
            "scenario_title": "Scene",
            "text": "台詞",
            "delivery": "強く",
        },
    ]


def test_target_linesは存在しないlineを拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (tmp_path / "assets" / "voices").mkdir(parents=True)
    monkeypatch.setattr(
        completion_listen,
        "validate_scenario_ids",
        lambda *_args, **_kwargs: SimpleNamespace(problems=()),
    )
    (scenarios / "scene.yaml").write_text(
        """
format_version: 1
id: scene
title: Scene
locale: ja
scene:
  setting: test
characters:
  - id: actor
    name: Actor
    gender: neutral
    age: adult
    voice: Test voice
lines:
  - id: line-001
    character: actor
    text: 台詞
    delivery: 強く
    emotion: neutral
    intensity: 1
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(CompletionListeningError, match="ありません"):
        _load_target_lines(
            scenarios_dir=scenarios,
            voices_dir=tmp_path / "assets" / "voices",
            targets={("scene", "line-999")},
        )


def _source_run(
    run_id: str,
    model: str,
    groups: set[tuple[str, str, str, str]],
    *,
    kind: str,
    supersedes: str | None = None,
) -> CompletionSourceRun:
    return CompletionSourceRun(
        run_id=run_id,
        model=model,
        kind=kind,
        supersedes_run_id=supersedes,
        root=Path("run"),
        ledger_sha256="a" * 64,
        qc_report_sha256="b" * 64,
        manifest_sha256="c" * 64,
        candidate_set_sha256="d" * 64,
        manifest={"models": [], "candidates": [], "failures": []},
        groups=frozenset(groups),
        role_epochs={group: "e" * 64 for group in groups},
        seed_base=(
            None
            if model == "aivisspeech-kohaku"
            else 104 if kind == "primary" else 204
        ),
        attempt_seeds={
            group: (
                frozenset()
                if model == "aivisspeech-kohaku"
                else frozenset(
                    {
                        index + (0 if kind == "primary" else 1_000)
                        for index in range(4)
                    },
                )
            )
            for group in groups
        },
    )


def test_topupはsuperseded_primaryのgroupを整組取代する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "artifacts" / "takes").mkdir(parents=True)
    counts = {
        "aivisspeech-kohaku": 25,
        "chatterbox-multilingual-v3": 13,
        "cosyvoice3-0.5b-2512": 14,
        "gpt-sovits-v2-pro-plus": 37,
        "irodori-tts-600m-v3-voicedesign": 161,
        "qwen3-tts-12hz-1.7b": 161,
        "supertonic-3": 25,
        "voxcpm2": 161,
    }
    targets: list[CompletionTarget] = []
    primary: dict[str, CompletionSourceRun] = {}
    for model in sorted(PRIMARY_MODELS):
        groups = {
            (model, "scene", f"{model}-{index:03d}", "dry")
            for index in range(counts[model])
        }
        targets.extend(
            CompletionTarget(*group) for group in sorted(groups)
        )
        primary[f"primary-{model}"] = _source_run(
            f"primary-{model}",
            model,
            groups,
            kind="primary",
        )
    first_primary = next(
        run for run in primary.values() if run.model != "aivisspeech-kohaku"
    )
    replaced_group = next(iter(first_primary.groups))
    topup = _source_run(
        "topup-1",
        first_primary.model,
        {replaced_group},
        kind="topup",
        supersedes=first_primary.run_id,
    )
    plan = SimpleNamespace(
        targets=tuple(targets),
        targets_for_model=lambda model: tuple(
            target for target in targets if target.model == model
        ),
        policy_for_model=lambda model: SimpleNamespace(
            seed_policy=(
                "none"
                if model == "aivisspeech-kohaku"
                else "derived-sha256-v1"
            ),
        ),
    )
    expected = {target.identity: "e" * 64 for target in targets}
    monkeypatch.setattr(
        completion_listen,
        "_load_anchor_selection",
        lambda *_args: ("f" * 64, {}),
    )
    monkeypatch.setattr(
        completion_listen,
        "expected_phase_b_role_epochs",
        lambda **_kwargs: expected,
    )
    monkeypatch.setattr(
        completion_listen,
        "_load_phase_b_run",
        lambda **kwargs: (
            topup if kwargs["run_id"] == "topup-1" else primary[kwargs["run_id"]]
        ),
    )
    resolution = resolve_completion_sources(
        plan=plan,
        primary_run_ids=list(primary),
        topup_run_ids=["topup-1"],
        anchor_selection_path=(tmp_path / "anchor.json").resolve(),
        artifacts_dir=(tmp_path / "artifacts").resolve(),
        scenarios_dir=(tmp_path / "scenarios").resolve(),
    )

    assert len(resolution.group_sources) == 597
    assert resolution.group_sources[replaced_group] is topup

    overlapping_topup = replace(
        topup,
        attempt_seeds={
            replaced_group: first_primary.attempt_seeds[replaced_group],
        },
    )
    monkeypatch.setattr(
        completion_listen,
        "_load_phase_b_run",
        lambda **kwargs: (
            overlapping_topup
            if kwargs["run_id"] == "topup-1"
            else primary[kwargs["run_id"]]
        ),
    )
    with pytest.raises(CompletionListeningError, match="derived seed"):
        resolve_completion_sources(
            plan=plan,
            primary_run_ids=list(primary),
            topup_run_ids=["topup-1"],
            anchor_selection_path=(tmp_path / "anchor.json").resolve(),
            artifacts_dir=(tmp_path / "artifacts").resolve(),
            scenarios_dir=(tmp_path / "scenarios").resolve(),
        )


def test_topupは既に取代済みgroupへの古いsupersedesを拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = {
        "aivisspeech-kohaku": 25,
        "chatterbox-multilingual-v3": 13,
        "cosyvoice3-0.5b-2512": 14,
        "gpt-sovits-v2-pro-plus": 37,
        "irodori-tts-600m-v3-voicedesign": 161,
        "qwen3-tts-12hz-1.7b": 161,
        "supertonic-3": 25,
        "voxcpm2": 161,
    }
    plan_targets = [
        CompletionTarget(model, "scene", f"{model}-{index:03d}", "dry")
        for model in sorted(PRIMARY_MODELS)
        for index in range(counts[model])
    ]
    assert len(plan_targets) == 597
    (tmp_path / "artifacts" / "takes").mkdir(parents=True)
    by_model = {
        model: {target.identity for target in plan_targets if target.model == model}
        for model in PRIMARY_MODELS
    }
    primaries = {
        f"p-{model}": _source_run(f"p-{model}", model, groups, kind="primary")
        for model, groups in by_model.items()
    }
    predecessor = next(
        run for run in primaries.values() if run.model != "aivisspeech-kohaku"
    )
    group = next(iter(predecessor.groups))
    topup1 = _source_run(
        "t1",
        predecessor.model,
        {group},
        kind="topup",
        supersedes=predecessor.run_id,
    )
    topup2 = _source_run(
        "t2",
        predecessor.model,
        {group},
        kind="topup",
        supersedes=predecessor.run_id,
    )
    runs: dict[str, Any] = {**primaries, "t1": topup1, "t2": topup2}
    plan = SimpleNamespace(
        targets=tuple(plan_targets),
        targets_for_model=lambda model: tuple(
            target for target in plan_targets if target.model == model
        ),
        policy_for_model=lambda model: SimpleNamespace(
            seed_policy=(
                "none"
                if model == "aivisspeech-kohaku"
                else "derived-sha256-v1"
            ),
        ),
    )
    expected = {target.identity: "e" * 64 for target in plan_targets}
    monkeypatch.setattr(
        completion_listen,
        "_load_anchor_selection",
        lambda *_args: ("f" * 64, {}),
    )
    monkeypatch.setattr(
        completion_listen,
        "expected_phase_b_role_epochs",
        lambda **_kwargs: expected,
    )
    monkeypatch.setattr(
        completion_listen,
        "_load_phase_b_run",
        lambda **kwargs: runs[kwargs["run_id"]],
    )
    with pytest.raises(CompletionListeningError, match="supersedes chain"):
        resolve_completion_sources(
            plan=plan,
            primary_run_ids=list(primaries),
            topup_run_ids=["t1", "t2"],
            anchor_selection_path=(tmp_path / "anchor.json").resolve(),
            artifacts_dir=(tmp_path / "artifacts").resolve(),
            scenarios_dir=(tmp_path / "scenarios").resolve(),
        )
def test_candidateはledger_QC_generation_provenanceへ逐slot_exact_joinする() -> None:
    group = ("model", "scene", "line", "dry")
    provenance = {
        "protocol": "phase-b-generation-v2",
        "plan_sha256": "a" * 64,
        "run_kind": "primary",
        "supersedes_run_id": None,
        "anchor_selection_sha256": None,
        "anchor_plan_sha256": None,
        "target_group": {
            "model": group[0],
            "scenario": group[1],
            "line": group[2],
            "variant": group[3],
            "role_epoch_sha256": "b" * 64,
        },
    }
    input_sha = "c" * 64
    audio_sha = "d" * 64
    take_id = make_take_id(
        generation_input_sha256=input_sha,
        final_opus_sha256=audio_sha,
    )
    requested = {"phase_b_provenance": provenance}
    realized = {"phase_b_provenance": provenance}
    attempt = {
        "model": group[0],
        "scenario": group[1],
        "line": group[2],
        "variant": group[3],
        "take_index": 1,
        "take_id": take_id,
        "generation_input_sha256": input_sha,
        "phase_b_provenance_sha256": hashlib.sha256(
            canonical_json(provenance).encode("utf-8"),
        ).hexdigest(),
        "generation": {
            "status": "succeeded",
            "seed": 123,
            "sampling": {"temperature": 0.7},
            "rtf": 0.5,
        },
        "audio": {"opus_sha256": audio_sha},
        "gates": {"mechanical": "pass", "content": "review_required"},
        "status": "eligible",
    }
    candidate = {
        **{
            field: attempt[field]
            for field in ("model", "scenario", "line", "variant", "take_index")
        },
        "take_id": take_id,
        "generation_input_sha256": input_sha,
        "sha256": audio_sha,
        "path": (
            "audio/takes/model/scene/line/dry/"
            f"take-0001-{audio_sha}.opus"
        ),
        "gen_params": {
            "seed": 123,
            "recipe_version": "recipe-v1",
            "sampling": {"temperature": 0.7},
            "requested": requested,
            "realized": realized,
        },
        "rtf": 0.5,
        "duration_sec": 1.25,
        "loudness": {
            "source": "encoded_opus",
            "i_lufs": -18.0,
            "tp_dbtp": -2.0,
            "shortfall": False,
        },
        "gate": {
            "mechanical": "pass",
            "content": "review_required",
            "policy_version": "take-gates-v2",
        },
    }
    ledger = {
        "source": {"recipe_version": "recipe-v1"},
        "attempts": [attempt],
    }
    authority = QCAuthority(
        gate_policy_version="take-gates-v2",
        attempts_by_slot={
            (*group, 1): {
                **{
                    field: attempt[field]
                    for field in (
                        "model",
                        "scenario",
                        "line",
                        "variant",
                        "take_index",
                        "status",
                        "gates",
                    )
                },
                "mechanical": {
                    "duration_sec": 1.25,
                    "loudness": {
                        "source": "encoded_opus",
                        "i_lufs": -18.0,
                        "tp_dbtp": -2.0,
                        "shortfall": False,
                    },
                    "generation_params": {
                        "requested": requested,
                        "realized": realized,
                    },
                },
            },
        },
    )
    phase_b = {
        key: provenance[key]
        for key in (
            "protocol",
            "plan_sha256",
            "run_kind",
            "supersedes_run_id",
            "anchor_selection_sha256",
            "anchor_plan_sha256",
        )
    }
    phase_b["target_groups"] = [provenance["target_group"]]
    seed_policy = "derived-sha256-v1"
    expected_seed = derive_seed(
        policy_version=seed_policy,
        seed_base=104,
        model=group[0],
        scenario=group[1],
        line=group[2],
        variant=group[3],
        index=1,
        seed_min=0,
        seed_max=2**32 - 1,
    )
    attempt["generation"]["seed"] = expected_seed
    candidate["gen_params"]["seed"] = expected_seed
    ledger["source"]["seed_base"] = 104

    _validate_manifest_candidate_authority(
        run_id="run",
        ledger=ledger,
        manifest={"candidates": [candidate]},
        qc_authority=authority,
        phase_b=phase_b,
        seed_policy=seed_policy,
    )

    seedless_attempt = {
        **attempt,
        "generation": {**attempt["generation"], "seed": None},
    }
    seedless_candidate = {
        **candidate,
        "gen_params": {**candidate["gen_params"], "seed": None},
    }
    seedless_ledger = {
        **ledger,
        "source": {**ledger["source"], "seed_base": None},
        "attempts": [seedless_attempt],
    }
    _validate_manifest_candidate_authority(
        run_id="aivis-run",
        ledger=seedless_ledger,
        manifest={"candidates": [seedless_candidate]},
        qc_authority=authority,
        phase_b=phase_b,
        seed_policy="none",
    )
    with pytest.raises(CompletionListeningError, match="seed contract"):
        _validate_manifest_candidate_authority(
            run_id="aivis-forged",
            ledger=ledger,
            manifest={"candidates": [candidate]},
            qc_authority=authority,
            phase_b=phase_b,
            seed_policy="none",
        )

    forged = {**candidate, "gate": {**candidate["gate"], "content": "pass"}}
    with pytest.raises(CompletionListeningError, match="authority"):
        _validate_manifest_candidate_authority(
            run_id="run",
            ledger=ledger,
            manifest={"candidates": [forged]},
            qc_authority=authority,
            phase_b=phase_b,
            seed_policy=seed_policy,
        )

    rejected = {**attempt, "status": "hard_rejected"}
    with pytest.raises(CompletionListeningError, match="eligible terminal"):
        _validate_manifest_candidate_authority(
            run_id="run",
            ledger={**ledger, "attempts": [rejected]},
            manifest={"candidates": [candidate]},
            qc_authority=authority,
            phase_b=phase_b,
            seed_policy=seed_policy,
        )

    disguised_seed = derive_seed(
        policy_version=seed_policy,
        seed_base=999,
        model=group[0],
        scenario=group[1],
        line=group[2],
        variant=group[3],
        index=1,
        seed_min=0,
        seed_max=2**32 - 1,
    )
    disguised_attempt = {
        **attempt,
        "generation": {**attempt["generation"], "seed": disguised_seed},
    }
    disguised_candidate = {
        **candidate,
        "gen_params": {**candidate["gen_params"], "seed": disguised_seed},
    }
    with pytest.raises(CompletionListeningError, match="canonical derive"):
        _validate_manifest_candidate_authority(
            run_id="run",
            ledger={**ledger, "attempts": [disguised_attempt]},
            manifest={"candidates": [disguised_candidate]},
            qc_authority=authority,
            phase_b=phase_b,
            seed_policy=seed_policy,
        )

    for field, forged_value in (
        ("duration_sec", 1.5),
        (
            "loudness",
            {
                "source": "encoded_opus",
                "i_lufs": -17.0,
                "tp_dbtp": -2.0,
                "shortfall": False,
            },
        ),
    ):
        forged_candidate = {**candidate, field: forged_value}
        with pytest.raises(CompletionListeningError, match="duration/loudness"):
            _validate_manifest_candidate_authority(
                run_id="run",
                ledger=ledger,
                manifest={"candidates": [forged_candidate]},
                qc_authority=authority,
                phase_b=phase_b,
                seed_policy=seed_policy,
            )
