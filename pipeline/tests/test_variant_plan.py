"""条件バリアント列の inherit/generate 分割と再利用mapping (#201)。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline.completion_plan import _source_snapshot
from gaya_pipeline.conditioning_variants import (
    MODE_HUMAN_REFERENCE,
    MODE_TEXT_ONLY,
    variant_model_id,
)
from gaya_pipeline.take_identity import canonical_json, make_take_id
from gaya_pipeline.variant_plan import (
    COLUMN_GROUPS,
    VARIANT,
    VARIANT_PRIMARY_SEED_BASE,
    VariantPlanError,
    build_variant_plan_document,
    load_variant_plan,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"
VOICES_DIR = REPOSITORY_ROOT / "assets" / "voices"
BASE_MODEL = "irodori-tts-v4-small"
OTHER_MODEL = "supertonic-3"
ANCHOR_AUTHORITY = ("1" * 64, "2" * 64, "3" * 64)

EXPLICIT_ROLE_LINES = 14
ANCHOR_ROLE_LINES = COLUMN_GROUPS - EXPLICIT_ROLE_LINES


def _scenario_lines() -> list[tuple[str, str, str]]:
    _sources, _roles, documents = _source_snapshot(
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
    )
    return [
        (str(document["id"]), str(line["id"]), str(line["character"]))
        for document in documents
        for line in document["lines"]
    ]


def _reference_voices() -> dict[tuple[str, str], str | None]:
    _sources, roles, _documents = _source_snapshot(
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
    )
    return {role.identity: role.reference_voice for role in roles}


def _candidate(
    *,
    model: str,
    scenario: str,
    line: str,
    take_index: int,
    realized: dict[str, Any],
) -> dict[str, Any]:
    audio_sha = hashlib.sha256(
        f"{model}/{scenario}/{line}/{take_index}".encode(),
    ).hexdigest()
    input_sha = hashlib.sha256(
        f"input/{model}/{scenario}/{line}/{take_index}".encode(),
    ).hexdigest()
    return {
        "model": model,
        "scenario": scenario,
        "line": line,
        "variant": VARIANT,
        "take_index": take_index,
        "take_id": make_take_id(
            generation_input_sha256=input_sha,
            final_opus_sha256=audio_sha,
        ),
        "path": (
            f"audio/takes/{model}/{scenario}/{line}/{VARIANT}/"
            f"take-{take_index:04d}-{audio_sha}.opus"
        ),
        "duration_sec": 1.5,
        "sha256": audio_sha,
        "generation_input_sha256": input_sha,
        "gen_params": {
            "seed": 1,
            "recipe_version": "seed-only-v1",
            "sampling": {},
            "requested": {},
            "realized": realized,
        },
        "rtf": 0.5,
        "loudness": {
            "source": "encoded_opus",
            "i_lufs": -18.0,
            "tp_dbtp": -2.0,
            "shortfall": False,
        },
        "gate": {
            "mechanical": "pass",
            "content": "pass",
            "policy_version": "take-gates-v2",
        },
    }


def _base_release(
    tmp_path: Path,
    *,
    contradiction: tuple[str, str] | None = None,
) -> Path:
    references = _reference_voices()
    candidates: list[dict[str, Any]] = []
    selection_groups: list[dict[str, Any]] = []
    for scenario, line, character in _scenario_lines():
        explicit = references[(scenario, character)] is not None
        realized = (
            {"reference_source": "voice-asset", "reference_voice": "x"}
            if explicit
            else {"reference_source": "selected-role-anchor"}
        )
        if contradiction == (scenario, line):
            realized = (
                {"reference_source": "selected-role-anchor"}
                if explicit
                else {"reference_source": "voice-asset", "reference_voice": "x"}
            )
        group_candidates = [
            _candidate(
                model=BASE_MODEL,
                scenario=scenario,
                line=line,
                take_index=index,
                realized=realized,
            )
            for index in (1, 2)
        ]
        candidates.extend(group_candidates)
        selection_groups.append(
            {
                "model": BASE_MODEL,
                "scenario": scenario,
                "line": line,
                "variant": VARIANT,
                "decision": {"take_id": group_candidates[0]["take_id"]},
            },
        )
    manifest = {
        "models": [{"id": BASE_MODEL}, {"id": OTHER_MODEL}],
        "candidates": candidates,
    }
    selection = {"groups": selection_groups}
    root = tmp_path / "base-release"
    root.mkdir(parents=True)
    for stem, document in (
        ("manifest-v4", manifest),
        ("selection", selection),
        ("candidate-set", {}),
        ("quality-signals", {}),
        ("release-provenance", {}),
    ):
        payload = canonical_json(document).encode("utf-8")
        (root / f"{stem}.json").write_bytes(payload)
        (root / f"{stem}.sha256").write_bytes(
            f"{hashlib.sha256(payload).hexdigest()}\n".encode("ascii"),
        )
    return root


def _build(
    tmp_path: Path,
    mode: str,
    *,
    base_release: Path | None = None,
) -> dict[str, Any]:
    anchor = (
        ANCHOR_AUTHORITY
        if mode == MODE_TEXT_ONLY
        else (None, None, None)
    )
    return build_variant_plan_document(
        base_model=BASE_MODEL,
        mode=mode,
        model_revision="test-revision-1",
        base_release_dir=base_release or _base_release(tmp_path),
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
        anchor_source_plan_sha256=anchor[0],
        anchor_candidate_set_sha256=anchor[1],
        anchor_selection_sha256=anchor[2],
    )


def test_text_only_inherits_anchor_lines(tmp_path: Path) -> None:
    document = _build(tmp_path, MODE_TEXT_ONLY)
    assert document["models"][0]["id"] == variant_model_id(BASE_MODEL, MODE_TEXT_ONLY)
    assert document["conditioning"] == {
        "base_model": BASE_MODEL,
        "mode": MODE_TEXT_ONLY,
    }
    assert len(document["reuse"]["inherit"]) == ANCHOR_ROLE_LINES
    assert len(document["reuse"]["generate"]) == EXPLICIT_ROLE_LINES
    assert len(document["phase_b"]["targets"]) == EXPLICIT_ROLE_LINES
    assert document["anchor_authority"]["selection_sha256"] == ANCHOR_AUTHORITY[2]
    assert all(
        group["realized_conditioning_mode"] == MODE_TEXT_ONLY
        for group in document["reuse"]["inherit"]
    )
    assert {item["character"] for item in document["reuse"]["generate"]} == {
        "merchant",
        "kaimono-musume",
        "receptionist",
        "barmaid",
        "granny",
    }


def test_human_reference_inherits_explicit_lines(tmp_path: Path) -> None:
    document = _build(tmp_path, MODE_HUMAN_REFERENCE)
    assert document["models"][0]["id"] == variant_model_id(
        BASE_MODEL,
        MODE_HUMAN_REFERENCE,
    )
    assert document["anchor_authority"] is None
    assert len(document["reuse"]["inherit"]) == EXPLICIT_ROLE_LINES
    assert len(document["reuse"]["generate"]) == ANCHOR_ROLE_LINES
    assert all(
        group["realized_conditioning_mode"] == MODE_HUMAN_REFERENCE
        for group in document["reuse"]["inherit"]
    )


def test_inherit_pins_take_identity(tmp_path: Path) -> None:
    base = _base_release(tmp_path)
    document = _build(tmp_path, MODE_TEXT_ONLY, base_release=base)
    manifest = json.loads((base / "manifest-v4.json").read_text(encoding="utf-8"))
    by_take = {
        candidate["take_id"]: candidate for candidate in manifest["candidates"]
    }
    for group in document["reuse"]["inherit"]:
        candidate = by_take[group["selected_take_id"]]
        assert candidate["sha256"] == group["selected_audio_sha256"]
        assert (
            candidate["generation_input_sha256"]
            == group["selected_generation_input_sha256"]
        )
        assert len(group["candidate_take_ids"]) == 2
        assert group["selected_take_id"] in group["candidate_take_ids"]
        assert group["source_model"] == BASE_MODEL


def test_plan_pins_base_release_shas(tmp_path: Path) -> None:
    base = _base_release(tmp_path)
    document = _build(tmp_path, MODE_TEXT_ONLY, base_release=base)
    for field, stem in (
        ("manifest_sha256", "manifest-v4"),
        ("candidate_set_sha256", "candidate-set"),
        ("selection_sha256", "selection"),
        ("quality_signals_sha256", "quality-signals"),
        ("release_provenance_sha256", "release-provenance"),
    ):
        assert document["base"][field] == (
            (base / f"{stem}.sha256").read_text(encoding="ascii").strip()
        )
    assert document["base"]["base_groups"] == COLUMN_GROUPS
    assert document["base"]["column_groups"] == COLUMN_GROUPS


def test_plan_round_trip_and_policy(tmp_path: Path) -> None:
    document = _build(tmp_path, MODE_TEXT_ONLY)
    path = tmp_path / "variant-plan.json"
    path.write_bytes(canonical_json(document).encode("utf-8"))
    plan = load_variant_plan(
        path,
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
    )
    assert plan.model == variant_model_id(BASE_MODEL, MODE_TEXT_ONLY)
    assert plan.conditioning_mode == MODE_TEXT_ONLY
    assert plan.base_model == BASE_MODEL
    assert len(plan.inherit) == ANCHOR_ROLE_LINES
    assert len(plan.targets) == EXPLICIT_ROLE_LINES
    assert len(plan.scenario_authority_targets) == COLUMN_GROUPS
    assert plan.requires_anchor_authority() is True
    policy = plan.policy_for_model(plan.model)
    assert policy.takes == 4
    assert policy.minimum_eligible_candidates == 3
    assert policy.primary_seed_base == VARIANT_PRIMARY_SEED_BASE
    assert plan.plan_id == hashlib.sha256(path.read_bytes()).hexdigest()


def test_plan_rejects_non_canonical_bytes(tmp_path: Path) -> None:
    document = _build(tmp_path, MODE_TEXT_ONLY)
    path = tmp_path / "variant-plan.json"
    path.write_bytes(json.dumps(document, ensure_ascii=False).encode("utf-8"))
    with pytest.raises(VariantPlanError):
        load_variant_plan(
            path,
            scenarios_dir=SCENARIOS_DIR,
            voices_dir=VOICES_DIR,
        )


def test_plan_fails_when_realized_contradicts_scenario(tmp_path: Path) -> None:
    scenario, line, _character = _scenario_lines()[0]
    base = _base_release(tmp_path, contradiction=(scenario, line))
    with pytest.raises(VariantPlanError, match="矛盾"):
        _build(tmp_path, MODE_TEXT_ONLY, base_release=base)


def test_plan_rejects_wrong_anchor_authority(tmp_path: Path) -> None:
    base = _base_release(tmp_path)
    with pytest.raises(VariantPlanError):
        build_variant_plan_document(
            base_model=BASE_MODEL,
            mode=MODE_HUMAN_REFERENCE,
            model_revision="test-revision-1",
            base_release_dir=base,
            scenarios_dir=SCENARIOS_DIR,
            voices_dir=VOICES_DIR,
            anchor_source_plan_sha256=ANCHOR_AUTHORITY[0],
            anchor_candidate_set_sha256=ANCHOR_AUTHORITY[1],
            anchor_selection_sha256=ANCHOR_AUTHORITY[2],
        )
    with pytest.raises(VariantPlanError):
        build_variant_plan_document(
            base_model=BASE_MODEL,
            mode=MODE_TEXT_ONLY,
            model_revision="test-revision-1",
            base_release_dir=base,
            scenarios_dir=SCENARIOS_DIR,
            voices_dir=VOICES_DIR,
        )


def test_voxcpm2_text_only_needs_no_anchor(tmp_path: Path) -> None:
    references = _reference_voices()
    candidates: list[dict[str, Any]] = []
    selection_groups: list[dict[str, Any]] = []
    for scenario, line, character in _scenario_lines():
        explicit = references[(scenario, character)] is not None
        realized = {
            "reference_kind": "asset" if explicit else "voice_design",
        }
        candidate = _candidate(
            model="voxcpm2",
            scenario=scenario,
            line=line,
            take_index=1,
            realized=realized,
        )
        candidates.append(candidate)
        selection_groups.append(
            {
                "model": "voxcpm2",
                "scenario": scenario,
                "line": line,
                "variant": VARIANT,
                "decision": {"take_id": candidate["take_id"]},
            },
        )
    root = tmp_path / "voxcpm2-base"
    root.mkdir(parents=True)
    for stem, document in (
        ("manifest-v4", {"models": [{"id": "voxcpm2"}], "candidates": candidates}),
        ("selection", {"groups": selection_groups}),
        ("candidate-set", {}),
        ("quality-signals", {}),
        ("release-provenance", {}),
    ):
        payload = canonical_json(document).encode("utf-8")
        (root / f"{stem}.json").write_bytes(payload)
        (root / f"{stem}.sha256").write_bytes(
            f"{hashlib.sha256(payload).hexdigest()}\n".encode("ascii"),
        )
    document = build_variant_plan_document(
        base_model="voxcpm2",
        mode=MODE_TEXT_ONLY,
        model_revision="test-revision-1",
        base_release_dir=root,
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
    )
    assert document["anchor_authority"] is None
    assert len(document["reuse"]["inherit"]) == ANCHOR_ROLE_LINES
    assert len(document["reuse"]["generate"]) == EXPLICIT_ROLE_LINES
