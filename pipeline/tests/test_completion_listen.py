from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import gaya_pipeline.completion_listen as completion_listen
from gaya_pipeline.completion_listen import (
    CompletionListeningError,
    CompletionSourceRun,
    PRIMARY_MODELS,
    _load_completion_scenario_authority,
    _read_canonical_authority_bytes,
    _validate_manifest_candidate_authority,
    build_completion_listening_bundle,
    phase_b_generation_binding,
    resolve_completion_sources,
)
from gaya_pipeline.completion_plan import (
    CompletionPlanError,
    CompletionTarget,
    load_completion_plan,
)
from gaya_pipeline.qc_report import QCAuthority
from gaya_pipeline.take_identity import canonical_json, derive_seed, make_take_id


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_PLAN_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "research"
    / "full-baseline-completion"
    / "plan.json"
)
FROZEN_BASE_MANIFEST_PATH = PRODUCTION_PLAN_PATH.with_name("base-manifest-v4.json")


def test_production_generationとlisteningは非正式planを処理前に拒否する(
    tmp_path: Path,
) -> None:
    plan = SimpleNamespace(plan_id="a" * 64, raw_sha256="a" * 64)

    with pytest.raises(CompletionPlanError, match="Phase B production"):
        phase_b_generation_binding(
            plan=plan,
            model="qwen3-tts-12hz-1.7b",
            scenarios_dir=tmp_path / "scenarios",
            voices_dir=tmp_path / "voices",
            anchor_selection_path=tmp_path / "anchor.json",
        )
    with pytest.raises(CompletionPlanError, match="Phase B production"):
        build_completion_listening_bundle(
            plan=plan,
            plan_path=(tmp_path / "plan.json").resolve(),
            primary_run_ids=(),
            topup_run_ids=(),
            anchor_selection_path=tmp_path / "anchor.json",
            artifacts_dir=tmp_path / "artifacts",
            scenarios_dir=tmp_path / "scenarios",
            voices_dir=tmp_path / "voices",
            output_dir=tmp_path / "output",
        )


def test_phase_b_generation_bindingは単一scenario_authorityをrole_epochへ渡す(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = CompletionTarget("model", "scene", "line", "dry")
    plan = SimpleNamespace(
        targets_for_model=lambda _model: (target,),
    )
    authority = completion_listen.CompletionScenarioAuthority(
        scenario_sha256="1" * 64,
        lines=(),
        contexts={},
        line_characters={("scene", "line"): "actor"},
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        completion_listen,
        "require_production_completion_plan",
        lambda _plan: None,
    )
    monkeypatch.setattr(
        completion_listen,
        "_load_anchor_selection",
        lambda *_args: ("2" * 64, {}),
    )
    monkeypatch.setattr(
        completion_listen,
        "_load_completion_scenario_authority",
        lambda **_kwargs: authority,
    )
    monkeypatch.setattr(
        completion_listen,
        "expected_phase_b_role_epochs",
        lambda **kwargs: (
            captured.update(kwargs) or {target.identity: "3" * 64}
        ),
    )

    anchor_sha, epochs = phase_b_generation_binding(
        plan=plan,
        model="model",
        scenarios_dir=tmp_path / "scenarios",
        voices_dir=tmp_path / "voices",
        anchor_selection_path=tmp_path / "anchor.json",
    )

    assert anchor_sha == "2" * 64
    assert epochs == {("scene", "line"): "3" * 64}
    assert captured["line_characters"] is authority.line_characters


def test_authority_bytesはcanonical_SHA_markerをexact検証する(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "authority.json").resolve()
    raw = canonical_json({"protocol": "authority-v1"}).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    path.write_bytes(raw)
    marker = path.with_suffix(".sha256")
    marker.write_text(digest, encoding="ascii")

    assert _read_canonical_authority_bytes(
        path,
        label="authority",
        expected_sha256=digest,
        marker_path=marker,
    ) == raw

    path.write_text('{"protocol": "authority-v1"}', encoding="utf-8")
    with pytest.raises(CompletionListeningError, match="canonical bytes"):
        _read_canonical_authority_bytes(
            path,
            label="authority",
            expected_sha256=digest,
        )

    path.write_bytes(raw)
    with pytest.raises(CompletionListeningError, match="loaded authority"):
        _read_canonical_authority_bytes(
            path,
            label="authority",
            expected_sha256="f" * 64,
        )

    marker.write_text("e" * 64, encoding="ascii")
    with pytest.raises(CompletionListeningError, match="SHA marker"):
        _read_canonical_authority_bytes(
            path,
            label="authority",
            expected_sha256=digest,
            marker_path=marker,
        )


def test_source_map_contextは597group全件をfrozen_planとscenarioから構成する() -> None:
    plan = load_completion_plan(
        PRODUCTION_PLAN_PATH,
        base_manifest_path=FROZEN_BASE_MANIFEST_PATH,
        scenarios_dir=REPOSITORY_ROOT / "scenarios",
        voices_dir=REPOSITORY_ROOT / "assets" / "voices",
    )
    authority = _load_completion_scenario_authority(
        scenarios_dir=REPOSITORY_ROOT / "scenarios",
        voices_dir=REPOSITORY_ROOT / "assets" / "voices",
        plan=plan,
    )
    contexts = authority.contexts
    groups = [
        {
            "model": target.model,
            "scenario": target.scenario,
            "line": target.line,
            "variant": target.variant,
            **contexts[(target.scenario, target.line)],
        }
        for target in plan.targets
    ]

    assert len(contexts) == 161
    assert len(groups) == 597
    expected_context_fields = {
        "character",
        "role_identity_sha256",
        "reference_voice",
        "role",
        "scene_setting",
        "reading",
        "situation",
        "emotion",
        "intensity",
    }
    assert all(expected_context_fields <= set(group) for group in groups)
    scenario_lines: dict[tuple[str, str], dict[str, Any]] = {}
    for scenario in sorted({target.scenario for target in plan.targets}):
        document = yaml.safe_load(
            (REPOSITORY_ROOT / "scenarios" / f"{scenario}.yaml").read_text(
                encoding="utf-8",
            ),
        )
        scenario_lines.update(
            {
                (scenario, str(line["id"])): line
                for line in document["lines"]
            },
        )
    for group in groups:
        line = scenario_lines[(group["scenario"], group["line"])]
        role = plan.role(group["scenario"], str(line["character"]))
        assert {
            field: group[field]
            for field in expected_context_fields
        } == {
            "character": str(line["character"]),
            "role_identity_sha256": role.role_identity_sha256,
            "reference_voice": role.reference_voice,
            "role": dict(role.role),
            "scene_setting": role.scene_setting,
            "reading": line.get("reading"),
            "situation": str(line["situation"]),
            "emotion": str(line["emotion"]),
            "intensity": line["intensity"],
        }
    guard = contexts[("castle-gate", "guard-otoko-001")]
    assert guard["character"] == "guard-otoko"
    assert guard["reading"] is None
    assert guard["emotion"] == "shout"
    assert guard["intensity"] == 3
    assert guard["role"]["gender"] == "male"
    wounded = contexts[("battlefield-camp", "wounded-001")]
    assert wounded["reading"] == "グッ……ソコワサワルナ……"
    assert wounded["situation"] == "傷口に触れられて激痛に耐えている。"


def test_listening_bundleはplanとanchorの入力bytesをそのまま保存する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = ("fixture-model", "fixture-scene", "fixture-line", "dry")
    plan_raw = canonical_json({"protocol": "fixture-plan-v1"}).encode("utf-8")
    plan_sha = hashlib.sha256(plan_raw).hexdigest()
    plan_path = (tmp_path / "plan.json").resolve()
    plan_path.write_bytes(plan_raw)
    anchor_raw = canonical_json(
        {"protocol": "role-anchor-selection-v1"},
    ).encode("utf-8")
    anchor_sha = hashlib.sha256(anchor_raw).hexdigest()
    anchor_path = (tmp_path / "anchor.json").resolve()
    anchor_path.write_bytes(anchor_raw)
    anchor_path.with_suffix(".sha256").write_text(anchor_sha, encoding="ascii")

    audio = b"fixture-opus"
    audio_sha = hashlib.sha256(audio).hexdigest()
    run_root = (tmp_path / "run").resolve()
    audio_path = run_root / "take-0001.opus"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(audio)
    candidate = {
        "model": identity[0],
        "scenario": identity[1],
        "line": identity[2],
        "variant": identity[3],
        "take_index": 1,
        "path": "fixture.opus",
        "sha256": audio_sha,
    }
    model = {"id": identity[0]}
    run = CompletionSourceRun(
        run_id="fixture-run",
        model=identity[0],
        kind="primary",
        supersedes_run_id=None,
        root=run_root,
        ledger_sha256="2" * 64,
        qc_report_sha256="3" * 64,
        manifest_sha256="4" * 64,
        candidate_set_sha256="5" * 64,
        manifest={
            "generated_at": "2026-08-02T00:00:00Z",
            "models": [model],
            "candidates": [candidate],
        },
        groups=frozenset({identity}),
        role_epochs={identity: "6" * 64},
        seed_base=None,
        attempt_seeds={identity: frozenset()},
    )
    resolution = completion_listen.CompletionSourceResolution(
        runs=(run,),
        group_sources={identity: run},
        anchor_selection_sha256=anchor_sha,
        expected_role_epochs={identity: "6" * 64},
    )
    plan = SimpleNamespace(
        plan_id=plan_sha,
        raw_sha256=plan_sha,
        targets=(SimpleNamespace(scenario=identity[1], line=identity[2]),),
        policy_for_model=lambda _model: SimpleNamespace(
            minimum_eligible_candidates=1,
        ),
    )
    context = {
        "character": "actor",
        "role_identity_sha256": "7" * 64,
        "reference_voice": None,
        "role": {
            "name": "Actor",
            "kind": "human",
            "gender": "neutral",
            "age": "adult",
            "archetype": "fixture",
            "voice": "fixture voice",
            "personality": "fixture personality",
        },
        "scene_setting": "fixture setting",
        "reading": None,
        "situation": "fixture situation",
        "emotion": "neutral",
        "intensity": 2,
    }
    scenario_authority = completion_listen.CompletionScenarioAuthority(
        scenario_sha256="8" * 64,
        lines=(),
        contexts={(identity[1], identity[2]): context},
        line_characters={(identity[1], identity[2]): "actor"},
    )
    captured_authorities: list[object] = []
    monkeypatch.setattr(
        completion_listen,
        "require_production_completion_plan",
        lambda _plan: None,
    )
    monkeypatch.setattr(
        completion_listen,
        "resolve_completion_sources",
        lambda **kwargs: (
            captured_authorities.append(kwargs["scenario_authority"])
            or resolution
        ),
    )
    monkeypatch.setattr(
        completion_listen,
        "_load_completion_scenario_authority",
        lambda **_kwargs: scenario_authority,
    )
    monkeypatch.setattr(
        completion_listen,
        "build_candidate_set",
        lambda **_kwargs: {"models": [model]},
    )
    monkeypatch.setattr(
        completion_listen,
        "canonical_candidate_set_bytes",
        lambda candidate_set: canonical_json(candidate_set).encode("utf-8"),
    )
    monkeypatch.setattr(
        completion_listen,
        "validate_manifest_v4",
        lambda manifest: manifest,
    )
    monkeypatch.setattr(
        completion_listen,
        "_local_audio_path",
        lambda _root, _candidate: audio_path,
    )
    output = (tmp_path / "bundle").resolve()

    build_completion_listening_bundle(
        plan=plan,
        plan_path=plan_path,
        primary_run_ids=(run.run_id,),
        topup_run_ids=(),
        anchor_selection_path=anchor_path,
        artifacts_dir=(tmp_path / "artifacts").resolve(),
        scenarios_dir=(tmp_path / "scenarios").resolve(),
        voices_dir=(tmp_path / "voices").resolve(),
        output_dir=output,
    )

    assert captured_authorities == [scenario_authority]
    assert (output / "completion-plan.json").read_bytes() == plan_raw
    assert (output / "completion-plan.sha256").read_text() == plan_sha
    assert (output / "role-anchor-selection-v1.json").read_bytes() == anchor_raw
    assert (
        output / "role-anchor-selection-v1.sha256"
    ).read_text() == anchor_sha
    source_map = json.loads(
        (output / "phase-b-source-map-v1.json").read_bytes(),
    )
    assert source_map["groups"][0] == {
        "model": identity[0],
        "scenario": identity[1],
        "line": identity[2],
        "variant": identity[3],
        **context,
        "role_epoch_sha256": "6" * 64,
        "source_run_id": run.run_id,
        "minimum_eligible_candidates": 1,
    }


@pytest.mark.parametrize(
    ("scenario", "before", "after"),
    (
        (
            "battlefield-camp",
            "situation: 傷口に触れられて激痛に耐えている。",
            "situation: 差し替えられた状況。",
        ),
        (
            "battlefield-camp",
            "reading: グッ……ソコワサワルナ……",
            "reading: カイザンシタヨミ",
        ),
    ),
)
def test_plan_load後のscenario改変はauthority構築前に拒否する(
    tmp_path: Path,
    scenario: str,
    before: str,
    after: str,
) -> None:
    scenarios = tmp_path / "scenarios"
    voices = tmp_path / "assets" / "voices"
    shutil.copytree(REPOSITORY_ROOT / "scenarios", scenarios)
    voices.mkdir(parents=True)
    shutil.copyfile(
        REPOSITORY_ROOT / "assets" / "voices" / "metadata.yaml",
        voices / "metadata.yaml",
    )
    plan = load_completion_plan(
        PRODUCTION_PLAN_PATH,
        base_manifest_path=FROZEN_BASE_MANIFEST_PATH,
        scenarios_dir=scenarios.resolve(),
        voices_dir=voices.resolve(),
    )
    scenario_path = scenarios / f"{scenario}.yaml"
    source = scenario_path.read_text(encoding="utf-8")
    assert before in source
    scenario_path.write_text(source.replace(before, after, 1), encoding="utf-8")

    with pytest.raises(CompletionListeningError, match="frozen plan source"):
        _load_completion_scenario_authority(
            scenarios_dir=scenarios,
            voices_dir=voices,
            plan=plan,
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


def _scenario_authority_for_targets(
    targets: list[CompletionTarget],
) -> completion_listen.CompletionScenarioAuthority:
    line_characters = {
        (target.scenario, target.line): "actor" for target in targets
    }
    return completion_listen.CompletionScenarioAuthority(
        scenario_sha256="9" * 64,
        lines=(),
        contexts={},
        line_characters=line_characters,
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
        scenario_authority=_scenario_authority_for_targets(targets),
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
            scenario_authority=_scenario_authority_for_targets(targets),
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
            scenario_authority=_scenario_authority_for_targets(plan_targets),
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
