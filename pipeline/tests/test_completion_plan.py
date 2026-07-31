from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline.completion_plan import (
    BASE_MANIFEST_SHA256,
    BASE_SELECTION_SHA256,
    CompletionPlanError,
    compute_completion_plan_id,
    load_completion_plan,
)
from gaya_pipeline.take_identity import canonical_json


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "research"
    / "full-baseline-completion"
    / "plan.json"
)
MANIFEST_PATH = REPOSITORY_ROOT / "data" / "manifest.json"


def _plan_document() -> dict[str, Any]:
    return json.loads(PLAN_PATH.read_bytes())


def _write_plan(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "plan.json"
    path.write_bytes(canonical_json(document).encode("utf-8"))
    return path


def test_canonical_planは全45未公開slotと固定実行条件を束縛する() -> None:
    plan = load_completion_plan(
        PLAN_PATH,
        base_manifest_path=MANIFEST_PATH,
    )

    assert plan.plan_id == compute_completion_plan_id(_plan_document())
    assert plan.base_manifest_sha256 == BASE_MANIFEST_SHA256
    assert plan.base_selection_sha256 == BASE_SELECTION_SHA256
    assert plan.takes == 4
    assert plan.seed_base == 104
    assert plan.minimum_eligible_candidates == 3
    assert len(plan.targets) == 45
    assert len(plan.targets_for_model("qwen3-tts-12hz-1.7b")) == 40
    assert len(plan.targets_for_model("chatterbox-multilingual-v3")) == 1
    assert len(plan.targets_for_model("cosyvoice3-0.5b-2512")) == 2
    assert len(plan.targets_for_model("voxcpm2")) == 2
    assert plan.target_lines_for_model("voxcpm2") == (
        ("goblin-camp", "goblin-cook-001"),
        ("spirit-forest", "pixie-003"),
    )
    assert plan.targets_for_model("supertonic-3") == ()


def test_planはcanonical_bytes以外を拒否する(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_bytes(PLAN_PATH.read_bytes() + b"\n")

    with pytest.raises(CompletionPlanError, match="canonical bytes"):
        load_completion_plan(path, base_manifest_path=MANIFEST_PATH)


def test_planはexact_fields以外を拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    document["fallback"] = True

    with pytest.raises(CompletionPlanError, match="exact contract"):
        load_completion_plan(
            _write_plan(tmp_path, document),
            base_manifest_path=MANIFEST_PATH,
        )


def test_planはtarget改ざんを拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    document["targets"][0]["prior_outcome"]["reason"] = "generation_failed"

    with pytest.raises(CompletionPlanError, match="no_eligible_take"):
        load_completion_plan(
            _write_plan(tmp_path, document),
            base_manifest_path=MANIFEST_PATH,
        )


def test_planはtarget欠落を拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    del document["targets"][3]

    with pytest.raises(CompletionPlanError, match="model 別件数"):
        load_completion_plan(
            _write_plan(tmp_path, document),
            base_manifest_path=MANIFEST_PATH,
        )


def test_planはtarget重複を拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    document["targets"][4] = document["targets"][3]

    with pytest.raises(CompletionPlanError, match="重複"):
        load_completion_plan(
            _write_plan(tmp_path, document),
            base_manifest_path=MANIFEST_PATH,
        )


def test_planはselected_targetを拒否する(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_bytes())
    selected = next(
        curation
        for curation in manifest["curations"]
        if curation["model"] == "chatterbox-multilingual-v3"
        and curation["decision"] == "selected"
    )
    document = _plan_document()
    document["targets"][0] = {
        "model": selected["model"],
        "scenario": selected["scenario"],
        "line": selected["line"],
        "variant": selected["variant"],
        "prior_outcome": {"reason": "no_eligible_take"},
    }
    document["targets"].sort(
        key=lambda target: (
            target["model"],
            target["scenario"],
            target["line"],
            target["variant"],
        ),
    )

    with pytest.raises(CompletionPlanError, match="selected group"):
        load_completion_plan(
            _write_plan(tmp_path, document),
            base_manifest_path=MANIFEST_PATH,
        )


def test_planはbase_manifest_raw_hash漂移を拒否する(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(MANIFEST_PATH.read_bytes() + b"\n")

    with pytest.raises(CompletionPlanError, match="raw SHA-256"):
        load_completion_plan(PLAN_PATH, base_manifest_path=manifest_path)


def test_planはbase_candidate_set改ざんを拒否する(tmp_path: Path) -> None:
    document = _plan_document()
    document["base"]["candidate_set_sha256"] = "0" * 64

    with pytest.raises(CompletionPlanError, match="固定 baseline"):
        load_completion_plan(
            _write_plan(tmp_path, document),
            base_manifest_path=MANIFEST_PATH,
        )
