from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaya_pipeline.completion_plan import (
    build_frozen_plan_document,
    load_completion_plan,
)
from gaya_pipeline.completion_release import (
    CompletionReleaseError,
    _decision_group_sha256,
    _validate_decision_against_sources,
    _validate_candidate_set_manifest_join,
    _validate_audit_partition,
    _validate_provenance_document,
)
from gaya_pipeline.completion_selection import reconstruct_base_selection
from gaya_pipeline.take_identity import canonical_json


ROOT = Path(__file__).resolve().parents[2]
BASE_MANIFEST_PATH = ROOT / "data" / "manifest.json"
SCENARIOS = ROOT / "scenarios"
VOICES = ROOT / "assets" / "voices"
AUDIT_PATH = (
    ROOT
    / "docs"
    / "research"
    / "role-conditioning-audit"
    / "source-audit.json"
)


def _plan(tmp_path: Path) -> Any:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        canonical_json(
            build_frozen_plan_document(
                scenarios_dir=SCENARIOS.resolve(),
                voices_dir=VOICES.resolve(),
                anchor_selection_sha256="a" * 64,
            ),
        ),
        encoding="utf-8",
    )
    return load_completion_plan(
        plan_path.resolve(),
        base_manifest_path=BASE_MANIFEST_PATH.resolve(),
        scenarios_dir=SCENARIOS.resolve(),
        voices_dir=VOICES.resolve(),
    )


def _base_selection() -> dict[str, Any]:
    curation = next(
        (
            ROOT
            / "docs"
            / "research"
            / "baseline-v4"
            / "release"
            / "curation"
        ).glob("*.json"),
    )
    return reconstruct_base_selection(
        base_manifest=json.loads(BASE_MANIFEST_PATH.read_bytes()),
        qwen_curation=json.loads(curation.read_bytes()),
    )


def _key(value: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        value["model"],
        value["scenario"],
        value["line"],
        value["variant"],
    )


def test_frozen_planは597_691_1288とmodel分布を固定する(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    assert len(plan.targets) == 597
    assert plan.inherited_groups == 691
    assert plan.final_groups == 1288
    assert Counter(target.model for target in plan.targets) == {
        "qwen3-tts-12hz-1.7b": 161,
        "irodori-tts-600m-v3-voicedesign": 161,
        "voxcpm2": 161,
        "aivisspeech-kohaku": 25,
        "supertonic-3": 25,
        "chatterbox-multilingual-v3": 13,
        "cosyvoice3-0.5b-2512": 14,
        "gpt-sovits-v2-pro-plus": 37,
    }


def test_inheritedはpublished_selectedからreplacementを引いた691である(
    tmp_path: Path,
) -> None:
    replacement = {target.identity for target in _plan(tmp_path).targets}
    selected = {
        _key(group): group
        for group in _base_selection()["groups"]
        if group["decision"]["type"] == "selected"
    }
    assert len(set(selected) & replacement) == 552
    inherited = {
        identity: group
        for identity, group in selected.items()
        if identity not in replacement
    }
    assert len(inherited) == 691
    assert len(replacement - set(selected)) == 45


def test_source_auditはreplacement分布と691_inherited_matchを逐条固定する(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    replacement = {target.identity for target in plan.targets}
    selected = {
        _key(group): group
        for group in _base_selection()["groups"]
        if group["decision"]["type"] == "selected"
    }
    inherited = {
        identity: group
        for identity, group in selected.items()
        if identity not in replacement
    }
    partition = _validate_audit_partition(
        audit=json.loads(AUDIT_PATH.read_bytes()),
        replacement=replacement,
        inherited=inherited,
    )
    assert partition["matched_candidate_count"] == 691
    records = partition["inherited_identity_unverifiable"]
    assert records == []


def test_provenanceはinherited_unverifiable列挙を拒否する() -> None:
    value = {
        "format_version": 1,
        "protocol": "role-baseline-release-v1",
        "plan_sha256": (
            "f21f7ffa598c38b24f345b8c05f4d18fe3073618deaa742bb55ff30e0a26a0e5"
        ),
        "anchor_selection_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
        "candidate_set_sha256": "d" * 64,
        "selection_sha256": "e" * 64,
        "counts": {
            "replacement_groups": 597,
            "inherited_groups": 691,
            "selected_groups": 1288,
            "failures": 0,
        },
        "base": {
            "manifest_sha256": (
                "f9dfda542fd1120fe0f74daae3036eab5211d7394d155f7b9953978e59bbe89d"
            ),
            "git_blob": "44061fafe330a9bebfed7a97a0b69ebe234c8724",
            "candidate_set_sha256": (
                "91913e08f97497f1f7604f109a6d0f7308742237277f6bbc5483678ac9858cc2"
            ),
            "selection_sha256": (
                "629cc80346160eb8e687757e6f792ef519da9a4fb74f79bdf97eb4d00f56126e"
            ),
            "source_audit_sha256": (
                "d7d48a053474251996ce5b63e509dce2a8b8df10189fb7fc49d0cdc859bad5cc"
            ),
            "matched_candidate_count": 691,
            "inherited_identity_unverifiable": [{"unexpected": True}],
        },
        "source_runs": [
            {"run_id": "20260730T204323380360Z-voxcpm2-n4"},
        ],
    }
    with pytest.raises(CompletionReleaseError, match="0 unverifiable"):
        _validate_provenance_document(
            value,
            manifest_sha="c" * 64,
            candidate_sha="d" * 64,
            selection_sha="e" * 64,
        )


def test_candidate_setはmanifestのmodels_candidates_failuresをexact固定する() -> None:
    manifest = {
        "models": [{"id": "model"}],
        "candidates": [{"take_id": "a" * 64}],
        "failures": [],
    }
    candidate_set = {
        **manifest,
        "format_version": 4,
        "scenario_sha256": "b" * 64,
        "lines": [],
    }
    _validate_candidate_set_manifest_join(
        candidate_set=candidate_set,
        manifest=manifest,
    )
    for field, replacement in (
        ("models", [{"id": "other"}]),
        ("candidates", [{"take_id": "c" * 64}]),
        ("failures", [{"reason": "no_eligible_take"}]),
    ):
        tampered = {**candidate_set, field: replacement}
        with pytest.raises(CompletionReleaseError, match=field):
            _validate_candidate_set_manifest_join(
                candidate_set=tampered,
                manifest=manifest,
            )


def test_decision_group_sha256はsite_candidate_catalogと同じexact投影を使う() -> None:
    identity = ("model", "scene", "line", "dry")
    candidate = {
        "take_id": "a" * 64,
        "path": "audio/takes/model/scene/line/dry/take.opus",
        "sha256": "b" * 64,
        "gate": {
            "mechanical": "pass",
            "content": "review_required",
            "policy_version": "take-gates-v2",
        },
    }
    line = {
        "scenario_title": "Scene",
        "text": "台詞",
        "delivery": "強く",
    }
    document = {
        "model": identity[0],
        "scenario": identity[1],
        "line": identity[2],
        "variant": identity[3],
        "scenario_title": line["scenario_title"],
        "text": line["text"],
        "delivery": line["delivery"],
        "role_epoch_sha256": "c" * 64,
        "source_run_id": "run-1",
        "minimum_eligible_candidates": 1,
        "candidates": [
            {
                "take_id": candidate["take_id"],
                "path": candidate["path"],
                "audio_sha256": candidate["sha256"],
                "gate": candidate["gate"],
            },
        ],
    }
    assert _decision_group_sha256(
        identity=identity,
        line=line,
        role_epoch_sha256="c" * 64,
        source_run_id="run-1",
        minimum_eligible_candidates=1,
        candidates=[candidate],
    ) == hashlib.sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
    ).hexdigest()


def test_releaseはdecision_group_sha256をcandidate_catalogから再計算する() -> None:
    identity = ("model", "scene", "line", "dry")
    candidate = {
        "model": identity[0],
        "scenario": identity[1],
        "line": identity[2],
        "variant": identity[3],
        "take_id": "a" * 64,
        "path": "audio/takes/model/scene/line/dry/take.opus",
        "sha256": "b" * 64,
        "gate": {
            "mechanical": "pass",
            "content": "review_required",
            "policy_version": "take-gates-v2",
        },
    }
    line = {
        "scenario": identity[1],
        "line": identity[2],
        "scenario_title": "Scene",
        "text": "台詞",
        "delivery": "強く",
    }
    epoch = "c" * 64
    source = SimpleNamespace(
        run_id="run-1",
        manifest={"candidates": [candidate]},
    )
    resolution = SimpleNamespace(
        expected_role_epochs={identity: epoch},
        group_sources={identity: source},
    )
    group_sha256 = _decision_group_sha256(
        identity=identity,
        line=line,
        role_epoch_sha256=epoch,
        source_run_id=source.run_id,
        minimum_eligible_candidates=1,
        candidates=[candidate],
    )
    decision_group = {
        "role_epoch_sha256": epoch,
        "group_sha256": group_sha256,
        "authority": {"minimum_eligible_candidates": 1},
        "candidates": [
            {
                "take_id": candidate["take_id"],
                "path": candidate["path"],
                "audio_sha256": candidate["sha256"],
                "gate": candidate["gate"],
            },
        ],
    }
    candidate_set = {"lines": [line], "candidates": [candidate]}
    plan = SimpleNamespace(
        policy_for_model=lambda _model: SimpleNamespace(
            minimum_eligible_candidates=1,
        ),
    )
    _validate_decision_against_sources(
        decision_groups={identity: decision_group},
        plan=plan,
        resolution=resolution,
        candidate_set=candidate_set,
    )
    with pytest.raises(CompletionReleaseError, match="group_sha256"):
        _validate_decision_against_sources(
            decision_groups={
                identity: {**decision_group, "group_sha256": "d" * 64},
            },
            plan=plan,
            resolution=resolution,
            candidate_set=candidate_set,
        )
