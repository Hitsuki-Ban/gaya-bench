"""13列 release 再構成のユニット契約 (#201)。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline.conditioning_variants import (
    MODE_HUMAN_REFERENCE,
    MODE_TEXT_ONLY,
    VARIANT_BASE_MODELS,
    variant_model_entry,
    variant_model_id,
)
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.variant_plan import COLUMN_GROUPS, VARIANT, load_variant_plan
from gaya_pipeline.variant_release import (
    RELEASE_FORMAT_VERSION,
    RELEASE_PROTOCOL,
    SPEC_PROTOCOL,
    VariantReleaseError,
    _anchor_authority_set_sha256,
    _inherited_projection,
    _rekey_candidate,
    _validate_columns,
    _validate_provenance,
    load_variant_finalize_spec,
)

from test_variant_plan import (  # type: ignore[import-not-found]
    ANCHOR_ROLE_LINES,
    BASE_MODEL,
    EXPLICIT_ROLE_LINES,
    SCENARIOS_DIR,
    VOICES_DIR,
    _base_release,
    _build,
)

BASE_ENTRY = {
    "id": BASE_MODEL,
    "name": "Irodori-TTS v4-Small",
    "version": "test-revision-1",
    "license_note": "MIT",
    "capabilities": {
        "emotion": True,
        "voice_prompt": True,
        "clone": True,
        "nonverbal": True,
        "reading": False,
    },
}


@dataclass(frozen=True)
class _BaseStub:
    manifest: dict[str, Any]
    selection: dict[str, Any]
    quality_signals: dict[str, Any]


def _plan(tmp_path: Path, mode: str, base_release: Path):
    document = _build(tmp_path, mode, base_release=base_release)
    path = tmp_path / f"plan-{mode}.json"
    path.write_bytes(canonical_json(document).encode("utf-8"))
    return load_variant_plan(
        path,
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
    )


def _base_stub(base_release: Path) -> _BaseStub:
    manifest = json.loads((base_release / "manifest-v4.json").read_text("utf-8"))
    selection = json.loads((base_release / "selection.json").read_text("utf-8"))
    return _BaseStub(
        manifest=manifest,
        selection=selection,
        quality_signals={
            "groups": [
                {
                    "model": BASE_MODEL,
                    "scenario": group["scenario"],
                    "line": group["line"],
                    "variant": VARIANT,
                    "protocol": "role-gender-f0-soft-v1",
                    "expected_gender": "female",
                    "median_f0_hz": 200.0,
                    "status": "pass",
                    "signal": None,
                    "qc_report_sha256": "9" * 64,
                }
                for group in selection["groups"][:3]
            ],
        },
    )


def test_rekey_candidate_preserves_take_identity() -> None:
    candidate = {
        "model": BASE_MODEL,
        "scenario": "tavern-night",
        "line": "barmaid-001",
        "variant": VARIANT,
        "take_index": 2,
        "take_id": "a" * 64,
        "sha256": "b" * 64,
        "generation_input_sha256": "c" * 64,
        "path": "audio/takes/old/x/y/dry/take-0002-" + "b" * 64 + ".opus",
    }
    rekeyed = _rekey_candidate(candidate, "irodori-tts-v4-small--text")
    assert rekeyed["model"] == "irodori-tts-v4-small--text"
    assert rekeyed["take_id"] == candidate["take_id"]
    assert rekeyed["sha256"] == candidate["sha256"]
    assert (
        rekeyed["generation_input_sha256"] == candidate["generation_input_sha256"]
    )
    assert rekeyed["path"] == (
        "audio/takes/irodori-tts-v4-small--text/tavern-night/barmaid-001/dry/"
        "take-0002-" + "b" * 64 + ".opus"
    )


def test_inherited_projection_rekeys_without_touching_bytes(tmp_path: Path) -> None:
    base_release = _base_release(tmp_path)
    plan = _plan(tmp_path, MODE_TEXT_ONLY, base_release)
    base = _base_stub(base_release)
    candidates, selection_groups, signal_groups = _inherited_projection(
        plan=plan,
        base=base,  # type: ignore[arg-type]
    )
    assert len(selection_groups) == ANCHOR_ROLE_LINES
    assert len(candidates) == ANCHOR_ROLE_LINES * 2
    assert all(item["model"] == plan.model for item in candidates)
    assert all(item["model"] == plan.model for item in selection_groups)
    assert all(item["model"] == plan.model for item in signal_groups)
    original = {
        candidate["take_id"]: candidate
        for candidate in base.manifest["candidates"]
    }
    for candidate in candidates:
        source = original[candidate["take_id"]]
        assert candidate["sha256"] == source["sha256"]
        assert (
            candidate["generation_input_sha256"]
            == source["generation_input_sha256"]
        )
        assert candidate["path"].startswith(f"audio/takes/{plan.model}/")


def test_inherited_projection_rejects_take_pin_drift(tmp_path: Path) -> None:
    base_release = _base_release(tmp_path)
    plan = _plan(tmp_path, MODE_TEXT_ONLY, base_release)
    base = _base_stub(base_release)
    base.manifest["candidates"].pop()
    with pytest.raises(VariantReleaseError, match="candidate集合"):
        _inherited_projection(plan=plan, base=base)  # type: ignore[arg-type]


def test_inherited_projection_rejects_conditioning_drift(tmp_path: Path) -> None:
    base_release = _base_release(tmp_path)
    plan = _plan(tmp_path, MODE_TEXT_ONLY, base_release)
    base = _base_stub(base_release)
    inherited = plan.inherit[0]
    for candidate in base.manifest["candidates"]:
        if (candidate["scenario"], candidate["line"]) == inherited.identity:
            candidate["gen_params"]["realized"] = {
                "reference_source": "voice-asset",
            }
    with pytest.raises(VariantReleaseError, match="realized条件"):
        _inherited_projection(plan=plan, base=base)  # type: ignore[arg-type]


def test_human_reference_projection_inherits_explicit_lines(tmp_path: Path) -> None:
    base_release = _base_release(tmp_path)
    plan = _plan(tmp_path, MODE_HUMAN_REFERENCE, base_release)
    base = _base_stub(base_release)
    _candidates, selection_groups, _signals = _inherited_projection(
        plan=plan,
        base=base,  # type: ignore[arg-type]
    )
    assert len(selection_groups) == EXPLICIT_ROLE_LINES


# --------------------------------------------------------------------------- #
# 列契約
# --------------------------------------------------------------------------- #


def _final_models() -> list[dict[str, Any]]:
    models = [
        {
            "id": model_id,
            "name": model_id,
            "version": "v",
            "license_note": "x",
            "capabilities": dict(BASE_ENTRY["capabilities"]),
        }
        for model_id in (
            "aivisspeech-kohaku",
            "chatterbox-multilingual-v3",
            "cosyvoice3-0.5b-2512",
            "gpt-sovits-v2-pro-plus",
            "supertonic-3",
        )
    ]
    for base_model in VARIANT_BASE_MODELS:
        entry = {**BASE_ENTRY, "id": base_model, "name": base_model}
        for mode in (MODE_HUMAN_REFERENCE, MODE_TEXT_ONLY):
            models.append(variant_model_entry(entry, mode))
    return models


def _column_manifest(
    models: list[dict[str, Any]],
    *,
    drift: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    for model in models:
        conditioning = model.get("conditioning")
        for index in range(COLUMN_GROUPS):
            realized: dict[str, Any] = {}
            if conditioning is not None:
                base_model = conditioning["base_model"]
                mode = conditioning["mode"]
                if drift == model["id"] and index == 0:
                    mode = (
                        MODE_TEXT_ONLY
                        if mode == MODE_HUMAN_REFERENCE
                        else MODE_HUMAN_REFERENCE
                    )
                realized = _realized_for(base_model, mode)
            candidates.append(
                {
                    "model": model["id"],
                    "scenario": "s",
                    "line": f"line-{index:04d}",
                    "variant": VARIANT,
                    "gen_params": {"realized": realized},
                },
            )
            groups.append(
                {
                    "model": model["id"],
                    "scenario": "s",
                    "line": f"line-{index:04d}",
                    "variant": VARIANT,
                },
            )
    return {"models": models, "candidates": candidates}, {"groups": groups}


def _realized_for(base_model: str, mode: str) -> dict[str, Any]:
    table = {
        "irodori-tts-600m-v3-voicedesign": ("reference_source", "voice-asset", "selected-role-anchor"),
        "irodori-tts-v4-small": ("reference_source", "voice-asset", "selected-role-anchor"),
        "qwen3-tts-12hz-1.7b": ("reference_control", "voice_asset", "selected_voice_design_anchor"),
        "voxcpm2": ("reference_kind", "asset", "voice_design"),
    }
    field, human, text = table[base_model]
    return {field: human if mode == MODE_HUMAN_REFERENCE else text}


def test_validate_columns_accepts_thirteen_columns() -> None:
    models = _final_models()
    assert len(models) == 13
    manifest, selection = _column_manifest(models)
    _validate_columns(manifest=manifest, selection=selection)


def test_validate_columns_rejects_leftover_mixed_column() -> None:
    models = _final_models()
    models.append({**BASE_ENTRY, "id": BASE_MODEL})
    manifest, selection = _column_manifest(models)
    with pytest.raises(VariantReleaseError, match="旧列"):
        _validate_columns(manifest=manifest, selection=selection)


def test_validate_columns_rejects_mixed_conditioning_inside_a_column() -> None:
    models = _final_models()
    target = variant_model_id(BASE_MODEL, MODE_TEXT_ONLY)
    manifest, selection = _column_manifest(models, drift=target)
    with pytest.raises(VariantReleaseError, match="条件の異なるテイク"):
        _validate_columns(manifest=manifest, selection=selection)


def test_validate_columns_rejects_wrong_row_count() -> None:
    models = _final_models()
    manifest, selection = _column_manifest(models)
    selection["groups"].pop()
    with pytest.raises(VariantReleaseError, match="161"):
        _validate_columns(manifest=manifest, selection=selection)


# --------------------------------------------------------------------------- #
# provenance / spec
# --------------------------------------------------------------------------- #


def _provenance(**overrides: Any) -> dict[str, Any]:
    document = {
        "format_version": RELEASE_FORMAT_VERSION,
        "protocol": RELEASE_PROTOCOL,
        "plan_authority_sha256": "1" * 64,
        "anchor_authority_sha256": "2" * 64,
        "decision_authority_sha256": "3" * 64,
        "manifest_sha256": "4" * 64,
        "candidate_set_sha256": "5" * 64,
        "selection_sha256": "6" * 64,
        "quality_signals_sha256": "7" * 64,
        "counts": {
            "models": 13,
            "selected_groups": 2_093,
            "quality_signal_groups": 10,
            "failures": 0,
        },
        "base": {
            field: "8" * 64
            for field in (
                "manifest_sha256",
                "candidate_set_sha256",
                "selection_sha256",
                "quality_signals_sha256",
                "release_provenance_sha256",
            )
        },
        "superseded_by": [
            {
                "model": base_model,
                "replaced_by": sorted(
                    variant_model_id(base_model, mode)
                    for mode in (MODE_HUMAN_REFERENCE, MODE_TEXT_ONLY)
                ),
            }
            for base_model in sorted(VARIANT_BASE_MODELS)
        ],
        "columns": [
            {
                "model": variant_model_id(base_model, mode),
                "base_model": base_model,
                "conditioning_mode": mode,
                "plan_sha256": "a" * 64,
                "decision_sha256": "b" * 64,
                "anchor_selection_sha256": None,
                "inherited_groups": 147,
                "generated_groups": 14,
                "inherited_candidates": 0,
                "generated_candidates": 0,
                "source_runs": [],
            }
            for base_model in sorted(VARIANT_BASE_MODELS)
            for mode in (MODE_HUMAN_REFERENCE, MODE_TEXT_ONLY)
        ],
    }
    document.update(overrides)
    return document


def _validate(document: dict[str, Any]) -> dict[str, Any]:
    return _validate_provenance(
        document,
        manifest_sha="4" * 64,
        candidate_sha="5" * 64,
        selection_sha="6" * 64,
        quality_signals_sha="7" * 64,
        manifest={"models": [None] * 13},
        selection={"groups": [None] * 2_093},
        quality_signal_count=10,
    )


def test_provenance_records_superseded_lineage() -> None:
    document = _provenance()
    assert _validate(document)["superseded_by"][0]["replaced_by"] == [
        "irodori-tts-600m-v3-voicedesign--ref",
        "irodori-tts-600m-v3-voicedesign--text",
    ]


def test_provenance_rejects_missing_superseded_entry() -> None:
    document = _provenance()
    document["superseded_by"] = document["superseded_by"][:-1]
    with pytest.raises(VariantReleaseError, match="superseded_by"):
        _validate(document)


def test_provenance_rejects_column_row_count_drift() -> None:
    document = _provenance()
    document["columns"][0]["generated_groups"] = 13
    with pytest.raises(VariantReleaseError, match="161"):
        _validate(document)


def test_anchor_authority_set_sha_is_deterministic() -> None:
    @dataclass(frozen=True)
    class _Column:
        plan: Any
        anchor_selection_sha256: str | None

    @dataclass(frozen=True)
    class _Plan:
        model: str

    columns = [
        _Column(plan=_Plan(model="voxcpm2--ref"), anchor_selection_sha256=None),
        _Column(
            plan=_Plan(model="voxcpm2--text"),
            anchor_selection_sha256="c" * 64,
        ),
    ]
    first = _anchor_authority_set_sha256(
        base_anchor_selection_sha256="d" * 64,
        columns=columns,  # type: ignore[arg-type]
    )
    second = _anchor_authority_set_sha256(
        base_anchor_selection_sha256="d" * 64,
        columns=list(reversed(columns)),  # type: ignore[arg-type]
    )
    assert first == second
    assert first != _anchor_authority_set_sha256(
        base_anchor_selection_sha256="e" * 64,
        columns=columns,  # type: ignore[arg-type]
    )


def _spec(tmp_path: Path, *, columns: int = 8) -> Path:
    entries = []
    for index, base_model in enumerate(sorted(VARIANT_BASE_MODELS)):
        for mode in (MODE_HUMAN_REFERENCE, MODE_TEXT_ONLY):
            entries.append(
                {
                    "plan": str(tmp_path / f"plan-{base_model}-{mode}.json"),
                    "decision": str(tmp_path / f"decision-{index}.json"),
                    "quality_signals": str(tmp_path / f"signals-{index}.json"),
                    "anchor_selection": (
                        str(tmp_path / "anchor.json")
                        if mode == MODE_TEXT_ONLY
                        else None
                    ),
                    "primary_run_ids": [f"run-{base_model}-{mode}"],
                    "topup_run_ids": [],
                },
            )
    document = {
        "format_version": 1,
        "protocol": SPEC_PROTOCOL,
        "columns": entries[:columns],
    }
    path = tmp_path / "columns.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_finalize_spec_round_trip(tmp_path: Path) -> None:
    columns = load_variant_finalize_spec(_spec(tmp_path))
    assert len(columns) == 8
    assert sum(1 for column in columns if column.anchor_selection_path is None) == 4
    assert all(column.plan_path.is_absolute() for column in columns)


def test_finalize_spec_rejects_wrong_column_count(tmp_path: Path) -> None:
    with pytest.raises(VariantReleaseError, match="columns"):
        load_variant_finalize_spec(_spec(tmp_path, columns=7))


def test_finalize_spec_rejects_relative_paths(tmp_path: Path) -> None:
    path = _spec(tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["columns"][0]["plan"] = "relative/plan.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(VariantReleaseError, match="絶対path"):
        load_variant_finalize_spec(path)


def test_finalize_spec_rejects_unknown_protocol(tmp_path: Path) -> None:
    path = _spec(tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["protocol"] = "something-else"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(VariantReleaseError, match="identity"):
        load_variant_finalize_spec(path)


_ = hashlib
