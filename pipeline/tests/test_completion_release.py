from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import pytest

from gaya_pipeline.completion_release import (
    CompletionReleaseError,
    _SupplementRun,
    _build_completion_material,
    _rebuild_base_candidate_set,
    _load_current_lines,
    _validate_plan_against_base,
    _validate_supplement_ledger_contract,
    _write_release,
    finalize_completion_release,
    validate_completion_plan,
    validate_completion_release,
)
from gaya_pipeline.completion_selection import (
    canonical_completion_selection_bytes,
    reconstruct_base_selection,
    validate_completion_decision,
)
from gaya_pipeline.curation import (
    CurationError,
    build_candidate_set,
    canonical_candidate_set_bytes,
)
from gaya_pipeline.take_identity import make_take_id
from gaya_pipeline.take_identity import canonical_json


ROOT = Path(__file__).resolve().parents[2]
BASE_MANIFEST_PATH = ROOT / "data" / "manifest.json"
PLAN_PATH = (
    ROOT / "docs" / "research" / "full-baseline-completion" / "plan.json"
)
QWEN_CURATION_PATH = next(
    (ROOT / "docs" / "research" / "baseline-v4" / "release" / "curation").glob(
        "*.json",
    ),
)
VOICES_DIR = ROOT / "assets" / "voices"


@pytest.fixture(autouse=True)
def _voice_validationはworktree内のexplicit_pathを受け取る(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def validate(
        _scenarios_dir: Path,
        scenario_ids: list[str],
        *,
        voices_dir: Path,
    ) -> SimpleNamespace:
        assert voices_dir == VOICES_DIR
        return SimpleNamespace(file_count=len(scenario_ids), problems=(), warnings=())

    monkeypatch.setattr(
        "gaya_pipeline.completion_release.validate_scenario_ids",
        validate,
    )


def _base_inputs() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    base = json.loads(BASE_MANIFEST_PATH.read_bytes())
    plan = validate_completion_plan(json.loads(PLAN_PATH.read_bytes()))
    qwen = json.loads(QWEN_CURATION_PATH.read_bytes())
    legacy = reconstruct_base_selection(base_manifest=base, qwen_curation=qwen)
    base_candidate_set = _rebuild_base_candidate_set(
        base_manifest=base,
        scenarios_dir=ROOT / "scenarios",
        voices_dir=VOICES_DIR,
    )
    return base, plan, legacy, base_candidate_set


def _candidate(
    identity: tuple[str, str, str, str],
    take_index: int,
) -> tuple[dict[str, Any], bytes, str]:
    model, scenario, line, variant = identity
    content = f"{model}/{scenario}/{line}/{take_index}".encode()
    audio_sha = hashlib.sha256(content).hexdigest()
    input_sha = hashlib.sha256(b"input:" + content).hexdigest()
    take_id = make_take_id(
        generation_input_sha256=input_sha,
        final_opus_sha256=audio_sha,
    )
    relative = f"audio/{model}/{scenario}/{line}/{variant}/take-{take_index:04d}.opus"
    return (
        {
            "model": model,
            "scenario": scenario,
            "line": line,
            "variant": variant,
            "take_index": take_index,
            "take_id": take_id,
            "path": (
                f"audio/takes/{model}/{scenario}/{line}/{variant}/"
                f"take-{take_index:04d}-{audio_sha}.opus"
            ),
            "duration_sec": 1.0,
            "sha256": audio_sha,
            "generation_input_sha256": input_sha,
            "gen_params": {
                "seed": take_index,
                "recipe_version": "completion-test-v1",
                "sampling": {},
                "requested": {},
                "realized": {},
            },
            "rtf": 0.5,
            "loudness": {
                "source": "encoded_opus",
                "i_lufs": -18.0,
                "tp_dbtp": -1.0,
                "shortfall": False,
            },
            "gate": {
                "mechanical": "pass",
                "content": "review_required",
                "policy_version": "take-gates-v2",
            },
        },
        content,
        relative,
    )


def _material_fixture() -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[_SupplementRun],
    dict[tuple[str, str], bytes],
]:
    base, plan, legacy, base_candidate_set = _base_inputs()
    target_map = _validate_plan_against_base(plan, base)
    supplement_scenario_sha256, supplement_lines = _load_current_lines(
        scenarios_dir=ROOT / "scenarios",
        voices_dir=VOICES_DIR,
        groups=set(target_map),
    )
    model_metadata = {item["id"]: item for item in base["models"]}
    candidates_by_model: dict[str, list[dict[str, Any]]] = {}
    provenance_by_model: dict[str, list[dict[str, Any]]] = {}
    contents: dict[tuple[str, str], bytes] = {}
    for identity in target_map:
        for take_index in range(1, 4):
            candidate, content, relative = _candidate(identity, take_index)
            model = identity[0]
            candidates_by_model.setdefault(model, []).append(candidate)
            run_id = f"completion-{model}"
            provenance_by_model.setdefault(model, []).append(
                {
                    "take_id": candidate["take_id"],
                    "path": candidate["path"],
                    "audio_sha256": candidate["sha256"],
                    "run_relative_path": relative,
                    "size_bytes": len(content),
                },
            )
            contents[(run_id, relative)] = content

    runs = [
        _SupplementRun(
            run_id=f"completion-{model}",
            model=model,
            root=ROOT,
            manifest={
                "format_version": 4,
                "generated_at": "2026-07-31T12:00:00Z",
                "candidate_set_sha256": "b" * 64,
                "models": [model_metadata[model]],
                "candidates": candidates,
                "curations": [],
                "failures": [],
            },
            candidate_set_sha256="b" * 64,
            ledger_sha256="c" * 64,
            qc_report_sha256="d" * 64,
            manifest_sha256="e" * 64,
            groups=frozenset(
                (
                    item["model"],
                    item["scenario"],
                    item["line"],
                    item["variant"],
                )
                for item in candidates
            ),
            provenance_candidates=tuple(provenance_by_model[model]),
        )
        for model, candidates in sorted(candidates_by_model.items())
    ]
    all_candidates = [
        candidate for run in runs for candidate in run.manifest["candidates"]
    ]
    supplement_candidate_set = build_candidate_set(
        scenario_sha256=supplement_scenario_sha256,
        lines=[dict(line) for line in supplement_lines],
        models=[model_metadata[model] for model in sorted(candidates_by_model)],
        candidates=sorted(
            all_candidates,
            key=lambda item: (
                item["model"],
                item["scenario"],
                item["line"],
                item["variant"],
                item["take_index"],
            ),
        ),
        failures=[],
    )
    supplement_sha = hashlib.sha256(
        canonical_candidate_set_bytes(supplement_candidate_set),
    ).hexdigest()
    grouped = {
        identity: [
            item
            for item in all_candidates
            if (
                item["model"],
                item["scenario"],
                item["line"],
                item["variant"],
            )
            == identity
        ]
        for identity in target_map
    }
    decision = validate_completion_decision(
        {
            "format_version": 1,
            "protocol": "baseline-completion-decision-v1",
            "candidate_set_sha256": supplement_sha,
            "groups": [
                {
                    "model": identity[0],
                    "scenario": identity[1],
                    "line": identity[2],
                    "variant": identity[3],
                    "authority": {
                        "type": "best_available",
                        "policy_version": "missing-slot-best-of-n-v1",
                        "reviewer": "owner",
                        "minimum_eligible_candidates": 3,
                    },
                    "candidates": [
                        {
                            "take_id": item["take_id"],
                            "path": item["path"],
                            "audio_sha256": item["sha256"],
                            "gate": item["gate"],
                            "rubric": {
                                "content_correct": False,
                                "prompt_leakage": False,
                                "reading_correct": False,
                                "accent_naturalness": 2,
                                "role_match": 3,
                                "delivery_match": 2,
                                "audio_quality": 3,
                                "adoptable": False,
                                "notes": "候補内のbest available",
                            },
                        }
                        for item in grouped[identity]
                    ],
                    "decision": {
                        "type": "selected",
                        "take_id": grouped[identity][0]["take_id"],
                    },
                }
                for identity in sorted(target_map)
            ],
        },
    )
    material = _build_completion_material(
        base_manifest=base,
        base_candidate_set=base_candidate_set,
        legacy_selection=legacy,
        plan=plan,
        target_map=target_map,
        supplement_scenario_sha256=supplement_scenario_sha256,
        supplement_lines=supplement_lines,
        decision=decision,
        supplement_runs=runs,
    )
    return material, base, runs, contents


def _write_audio(
    artifacts_dir: Path,
    contents: dict[tuple[str, str], bytes],
) -> None:
    for (run_id, relative), content in contents.items():
        path = artifacts_dir / "takes" / run_id / Path(*PurePosixPath(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def test_N3なら1378候補1288selectedのcomplete_releaseになる(tmp_path: Path) -> None:
    material, base, _runs, contents = _material_fixture()
    release = tmp_path / "release"
    summary = _write_release(output_dir=release, material=material)
    artifacts = tmp_path / "artifacts"
    _write_audio(artifacts, contents)

    validated = validate_completion_release(
        release_dir=release,
        artifacts_dir=artifacts,
    )

    assert summary.candidate_count == 1378
    assert summary.selected_count == 1288
    assert summary.supplement_candidate_count == 135
    assert validated.manifest["failures"] == []
    assert all(item["decision"] == "selected" for item in validated.manifest["curations"])
    old_selected_ids = {
        item["take_id"]
        for item in base["curations"]
        if item["decision"] == "selected"
    }
    old_candidates = {
        item["take_id"]: item
        for item in base["candidates"]
        if item["take_id"] in old_selected_ids
    }
    final_candidates = {
        item["take_id"]: item for item in validated.manifest["candidates"]
    }
    assert {key: final_candidates[key] for key in old_candidates} == old_candidates


def test_source_target欠落とextraを拒否する() -> None:
    material, _base, runs, _contents = _material_fixture()
    assert material
    base, plan, legacy, base_candidate_set = _base_inputs()
    target_map = _validate_plan_against_base(plan, base)
    supplement_scenario_sha256, supplement_lines = _load_current_lines(
        scenarios_dir=ROOT / "scenarios",
        voices_dir=VOICES_DIR,
        groups=set(target_map),
    )
    decision = material["selection"]
    decision = {
        "format_version": 1,
        "protocol": "baseline-completion-decision-v1",
        "candidate_set_sha256": "0" * 64,
        "groups": [
            group
            for group in material["selection"]["groups"]
            if group["authority"]["type"] == "best_available"
        ],
    }
    first = runs[0]
    runs[0] = _SupplementRun(
        **{
            **first.__dict__,
            "groups": frozenset(list(first.groups)[1:]),
        },
    )
    with pytest.raises(CompletionReleaseError, match="source groups"):
        _build_completion_material(
            base_manifest=base,
            base_candidate_set=base_candidate_set,
            legacy_selection=legacy,
            plan=plan,
            target_map=target_map,
            supplement_scenario_sha256=supplement_scenario_sha256,
            supplement_lines=supplement_lines,
            decision=decision,
            supplement_runs=runs,
        )
    runs[0] = _SupplementRun(
        **{
            **first.__dict__,
            "groups": frozenset(
                {
                    *first.groups,
                    ("qwen3-tts-12hz-1.7b", "extra-scene", "extra-line", "dry"),
                },
            ),
        },
    )
    with pytest.raises(CompletionReleaseError, match="source groups"):
        _build_completion_material(
            base_manifest=base,
            base_candidate_set=base_candidate_set,
            legacy_selection=legacy,
            plan=plan,
            target_map=target_map,
            supplement_scenario_sha256=supplement_scenario_sha256,
            supplement_lines=supplement_lines,
            decision=decision,
            supplement_runs=runs,
        )


def test_supplement物理hash漂移を拒否する(tmp_path: Path) -> None:
    material, _base, _runs, contents = _material_fixture()
    release = tmp_path / "release"
    _write_release(output_dir=release, material=material)
    artifacts = tmp_path / "artifacts"
    _write_audio(artifacts, contents)
    first_path = next((artifacts / "takes").rglob("*.opus"))
    first_path.write_bytes(b"tampered")

    with pytest.raises(CompletionReleaseError, match="size|SHA"):
        validate_completion_release(
            release_dir=release,
            artifacts_dir=artifacts,
        )


def test_base_manifest漂移はrun探索前に拒否する(tmp_path: Path) -> None:
    base = tmp_path / "manifest.json"
    base.write_bytes(BASE_MANIFEST_PATH.read_bytes() + b"\n")

    with pytest.raises(CompletionReleaseError, match="canonical|SHA"):
        finalize_completion_release(
            base_manifest_path=base,
            qwen_curation_path=QWEN_CURATION_PATH,
            completion_plan_path=PLAN_PATH,
            decision_path=tmp_path / "not-reached.json",
            supplement_run_ids=["not-reached"],
            artifacts_dir=tmp_path / "not-reached",
            scenarios_dir=ROOT / "scenarios",
            voices_dir=VOICES_DIR,
            output_dir=tmp_path / "release",
        )


def test_provenanceはself_consistentでも固定published_base改ざんを拒否する(
    tmp_path: Path,
) -> None:
    material, _base, _runs, _contents = _material_fixture()
    release = tmp_path / "release"
    _write_release(output_dir=release, material=material)
    provenance_path = release / "release-provenance.json"
    provenance = json.loads(provenance_path.read_bytes())
    provenance["base"]["manifest_sha256"] = "0" * 64
    provenance["base"]["git_blob"] = "0" * 40
    raw = canonical_json(provenance).encode("utf-8")
    provenance_path.write_bytes(raw)
    (release / "release-provenance.sha256").write_text(
        hashlib.sha256(raw).hexdigest(),
        encoding="ascii",
    )

    with pytest.raises(CompletionReleaseError, match="公開基準"):
        validate_completion_release(release_dir=release)


def test_release_planはpublished_base_identityの全0改ざんを拒否する() -> None:
    plan = json.loads(PLAN_PATH.read_bytes())
    plan["base"]["manifest_sha256"] = "0" * 64
    plan["base"]["git_blob"] = "0" * 40

    with pytest.raises(CompletionReleaseError, match="base manifest SHA"):
        validate_completion_plan(plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("takes", 3),
        ("seed_base", 105),
    ],
)
def test_supplement_ledgerは固定実行条件の漂移を拒否する(
    field: str,
    value: int,
) -> None:
    ledger = {"source": {"takes": 4, "seed_base": 104}}
    ledger["source"][field] = value

    with pytest.raises(CompletionReleaseError, match=f"source.{field}"):
        _validate_supplement_ledger_contract(ledger)


@pytest.mark.parametrize("mutation", ["path", "audio_sha256"])
def test_selection_candidateはmanifestとのexact投影を要求する(
    tmp_path: Path,
    mutation: str,
) -> None:
    material, _base, _runs, _contents = _material_fixture()
    release = tmp_path / "release"
    _write_release(output_dir=release, material=material)

    selection_path = release / "selection.json"
    selection = json.loads(selection_path.read_bytes())
    candidate = selection["groups"][0]["candidates"][0]
    if mutation == "path":
        candidate["path"] = "audio/takes/forged/selection-candidate.opus"
    else:
        candidate["audio_sha256"] = "0" * 64
    selection_raw = canonical_completion_selection_bytes(selection)
    selection_sha = hashlib.sha256(selection_raw).hexdigest()
    selection_path.write_bytes(selection_raw)
    (release / "selection.sha256").write_text(selection_sha, encoding="ascii")

    manifest_path = release / "manifest-v4.json"
    manifest = json.loads(manifest_path.read_bytes())
    for projection in manifest["curations"]:
        projection["curation_sha256"] = selection_sha
    manifest_raw = canonical_json(manifest).encode("utf-8")
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    manifest_path.write_bytes(manifest_raw)
    (release / "manifest-v4.sha256").write_text(manifest_sha, encoding="ascii")

    provenance_path = release / "release-provenance.json"
    provenance = json.loads(provenance_path.read_bytes())
    provenance["selection_sha256"] = selection_sha
    provenance["manifest_sha256"] = manifest_sha
    provenance_raw = canonical_json(provenance).encode("utf-8")
    provenance_path.write_bytes(provenance_raw)
    (release / "release-provenance.sha256").write_text(
        hashlib.sha256(provenance_raw).hexdigest(),
        encoding="ascii",
    )

    with pytest.raises(CompletionReleaseError, match="path/SHA"):
        validate_completion_release(release_dir=release)


def test_bad_decisionはpublic_finalize境界でCompletionReleaseErrorになる(
    tmp_path: Path,
) -> None:
    decision_path = tmp_path / "bad-decision.json"
    decision_path.write_bytes(
        canonical_json(
            {
                "format_version": 1,
                "protocol": "baseline-completion-decision-v1",
                "candidate_set_sha256": "0" * 64,
                "groups": [],
            },
        ).encode("utf-8"),
    )

    with pytest.raises(CompletionReleaseError, match="入力契約") as captured:
        finalize_completion_release(
            base_manifest_path=BASE_MANIFEST_PATH,
            qwen_curation_path=QWEN_CURATION_PATH,
            completion_plan_path=PLAN_PATH,
            decision_path=decision_path,
            supplement_run_ids=["not-reached"],
            artifacts_dir=tmp_path / "not-reached",
            scenarios_dir=ROOT / "scenarios",
            voices_dir=VOICES_DIR,
            output_dir=tmp_path / "release",
        )

    assert isinstance(captured.value.__cause__, CurationError)


def test_forged_release_schemaはpublic_validate境界でCompletionReleaseErrorになる(
    tmp_path: Path,
) -> None:
    material, _base, _runs, _contents = _material_fixture()
    release = tmp_path / "release"
    _write_release(output_dir=release, material=material)
    selection_path = release / "selection.json"
    selection = json.loads(selection_path.read_bytes())
    selection["groups"][0]["forged"] = True
    raw = canonical_json(selection).encode("utf-8")
    selection_path.write_bytes(raw)
    (release / "selection.sha256").write_text(
        hashlib.sha256(raw).hexdigest(),
        encoding="ascii",
    )

    with pytest.raises(CompletionReleaseError, match="bundle") as captured:
        validate_completion_release(release_dir=release)

    assert isinstance(captured.value.__cause__, CurationError)
