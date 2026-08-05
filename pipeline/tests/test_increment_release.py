from __future__ import annotations

import hashlib
from typing import Any

import pytest

from gaya_pipeline import increment_release
from gaya_pipeline.increment_release import (
    BASE_QUALITY_SIGNAL_GROUPS,
    FINAL_QUALITY_SIGNAL_GROUPS,
    RELEASE_PROTOCOL,
    IncrementReleaseError,
    _validate_manifest_joins,
    _validate_provenance,
    build_curations,
    merge_selection_groups,
)

BASE_MODELS = (
    "aivisspeech-kohaku",
    "chatterbox-multilingual-v3",
    "cosyvoice3-0.5b-2512",
    "gpt-sovits-v2-pro-plus",
    "irodori-tts-600m-v3-voicedesign",
    "qwen3-tts-12hz-1.7b",
    "supertonic-3",
    "voxcpm2",
)
INCREMENT_MODEL = "irodori-tts-v4-small"
LINES = ("medic-001", "medic-002")
SCENARIO = "battlefield-camp"
MINI_BASE_GROUPS = len(BASE_MODELS) * len(LINES)
MINI_INCREMENT_GROUPS = len(LINES)
MINI_FINAL_GROUPS = MINI_BASE_GROUPS + MINI_INCREMENT_GROUPS


def _take_id(model: str, line: str) -> str:
    return hashlib.sha256(f"{model}/{line}".encode("utf-8")).hexdigest()


def _identity(model: str, line: str) -> tuple[str, str, str, str]:
    return (model, SCENARIO, line, "dry")


def _base_selection_groups() -> list[dict[str, Any]]:
    return [
        {
            "model": model,
            "scenario": SCENARIO,
            "line": line,
            "variant": "dry",
            "role_epoch_sha256": "a" * 64,
            "authority": {"type": "automatic_gate"},
            "candidates": [],
            "decision": {"type": "selected", "take_id": _take_id(model, line)},
        }
        for model in BASE_MODELS
        for line in LINES
    ]


def _decision_groups() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    return {
        _identity(INCREMENT_MODEL, line): {
            "model": INCREMENT_MODEL,
            "scenario": SCENARIO,
            "line": line,
            "variant": "dry",
            "role_epoch_sha256": "b" * 64,
            "authority": {
                "type": "auto-selected",
                "policy_version": "phase-b-auto-selection-v1",
                "minimum_eligible_candidates": 3,
                "gate_policy_version": "take-gates-v2",
            },
            "candidates": [],
            "decision": {
                "type": "selected",
                "take_id": _take_id(INCREMENT_MODEL, line),
            },
            # release selection からは落ちる decision 固有 field。
            "group_sha256": "c" * 64,
            "screening": {
                "protocol": "role-gender-f0-soft-v1",
                "expected_gender": "female",
                "median_f0_hz": 220.0,
                "status": "pass",
                "signal": None,
                "qc_report_sha256": "d" * 64,
            },
        }
        for line in LINES
    }


def test_miniature_fixtureで8model2lineへv4を足すと9model分になる() -> None:
    groups = merge_selection_groups(
        base_groups=_base_selection_groups(),
        decision_groups=_decision_groups(),
    )
    assert len(groups) == MINI_FINAL_GROUPS == 18
    assert {group["model"] for group in groups} == {*BASE_MODELS, INCREMENT_MODEL}
    # 公開済み1,288相当のgroupはbytes単位でそのまま継承される。
    assert groups[:MINI_BASE_GROUPS] == _base_selection_groups()
    increment = groups[MINI_BASE_GROUPS:]
    assert len(increment) == MINI_INCREMENT_GROUPS
    for group in increment:
        assert group["model"] == INCREMENT_MODEL
        assert group["authority"]["type"] == "auto-selected"
        assert "group_sha256" not in group
        assert "screening" not in group
    assert [group["line"] for group in increment] == sorted(LINES)


def test_増分groupがbaseと衝突したら停止する() -> None:
    collide = {
        _identity(BASE_MODELS[0], LINES[0]): {
            "model": BASE_MODELS[0],
            "scenario": SCENARIO,
            "line": LINES[0],
            "variant": "dry",
        },
    }
    with pytest.raises(IncrementReleaseError, match="衝突"):
        merge_selection_groups(
            base_groups=_base_selection_groups(),
            decision_groups=collide,
        )


def test_curationは全groupへ最終selection_SHAを刻む() -> None:
    groups = merge_selection_groups(
        base_groups=_base_selection_groups(),
        decision_groups=_decision_groups(),
    )
    curations = build_curations(selection_groups=groups, selection_sha256="e" * 64)
    assert len(curations) == MINI_FINAL_GROUPS
    assert {curation["decision"] for curation in curations} == {"selected"}
    assert {curation["curation_sha256"] for curation in curations} == {"e" * 64}
    assert curations[-1]["take_id"] == _take_id(INCREMENT_MODEL, sorted(LINES)[-1])


def _provenance(**overrides: Any) -> dict[str, Any]:
    document = {
        "format_version": 1,
        "protocol": RELEASE_PROTOCOL,
        "plan_sha256": "1" * 64,
        "model": INCREMENT_MODEL,
        "anchor_selection_sha256": "2" * 64,
        "increment_candidate_set_sha256": "3" * 64,
        "manifest_sha256": "4" * 64,
        "candidate_set_sha256": "5" * 64,
        "selection_sha256": "6" * 64,
        "decision_sha256": "7" * 64,
        "quality_signals_sha256": "8" * 64,
        "counts": {
            "base_groups": MINI_BASE_GROUPS,
            "increment_groups": MINI_INCREMENT_GROUPS,
            "selected_groups": MINI_FINAL_GROUPS,
            "failures": 0,
        },
        "base": {
            "manifest_sha256": "9" * 64,
            "candidate_set_sha256": "a" * 64,
            "selection_sha256": "b" * 64,
            "quality_signals_sha256": "c" * 64,
            "release_provenance_sha256": "d" * 64,
        },
        "source_runs": [
            {
                "model": INCREMENT_MODEL,
                "run_id": "run-v4",
                "kind": "primary",
                "supersedes_run_id": None,
                "ledger_sha256": "e" * 64,
                "qc_report_sha256": "f" * 64,
                "manifest_sha256": "0" * 64,
                "candidate_set_sha256": "1" * 64,
                "effective_groups": [
                    {
                        "model": INCREMENT_MODEL,
                        "scenario": SCENARIO,
                        "line": line,
                        "variant": "dry",
                        "role_epoch_sha256": "b" * 64,
                    }
                    for line in LINES
                ],
                "candidates": [],
            },
        ],
    }
    document.update(overrides)
    return document


@pytest.fixture()
def mini_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(increment_release, "BASE_GROUPS", MINI_BASE_GROUPS)
    monkeypatch.setattr(
        increment_release,
        "INCREMENT_GROUPS",
        MINI_INCREMENT_GROUPS,
    )
    monkeypatch.setattr(increment_release, "FINAL_GROUPS", MINI_FINAL_GROUPS)


def _validate(document: dict[str, Any]) -> dict[str, Any]:
    return _validate_provenance(
        document,
        manifest_sha=document["manifest_sha256"],
        candidate_sha=document["candidate_set_sha256"],
        selection_sha=document["selection_sha256"],
        quality_signals_sha=document["quality_signals_sha256"],
    )


def test_provenanceはbase加算increment等式を固定する(mini_counts: None) -> None:
    assert _validate(_provenance())["protocol"] == RELEASE_PROTOCOL

    broken = _provenance()
    broken["counts"]["selected_groups"] = MINI_FINAL_GROUPS + 1
    with pytest.raises(IncrementReleaseError, match="counts"):
        _validate(broken)


def test_provenanceは複数modelのsource_runを拒否する(mini_counts: None) -> None:
    document = _provenance()
    document["source_runs"][0]["model"] = "voxcpm2"
    with pytest.raises(IncrementReleaseError, match="単一model"):
        _validate(document)


def test_provenanceのdocument_SHA不一致は拒否される(mini_counts: None) -> None:
    document = _provenance()
    with pytest.raises(IncrementReleaseError, match="document SHA"):
        _validate_provenance(
            document,
            manifest_sha="0" * 64,
            candidate_sha=document["candidate_set_sha256"],
            selection_sha=document["selection_sha256"],
            quality_signals_sha=document["quality_signals_sha256"],
        )


def _manifest_bundle() -> dict[str, Any]:
    selection_groups = merge_selection_groups(
        base_groups=_base_selection_groups(),
        decision_groups=_decision_groups(),
    )
    candidates = [
        {
            "model": group["model"],
            "scenario": group["scenario"],
            "line": group["line"],
            "variant": group["variant"],
            "take_index": 1,
            "take_id": group["decision"]["take_id"],
        }
        for group in selection_groups
    ]
    manifest = {
        "models": [{"id": model} for model in (*BASE_MODELS, INCREMENT_MODEL)],
        "candidates": candidates,
        "failures": [],
        "curations": build_curations(
            selection_groups=selection_groups,
            selection_sha256="e" * 64,
        ),
    }
    candidate_set = {
        "models": manifest["models"],
        "candidates": manifest["candidates"],
        "failures": manifest["failures"],
    }
    quality_signals = {
        "groups": [
            {
                "model": INCREMENT_MODEL,
                "scenario": SCENARIO,
                "line": line,
                "variant": "dry",
            }
            for line in LINES
        ],
    }
    return {
        "manifest": manifest,
        "candidate_set": candidate_set,
        "selection": {"groups": selection_groups},
        "quality_signals": quality_signals,
        "provenance": _provenance(),
    }


def test_manifest_joinは9model分の選抜をcurationとcandidateへ突き合わせる(
    mini_counts: None,
) -> None:
    bundle = _manifest_bundle()
    _validate_manifest_joins(**bundle)

    missing = _manifest_bundle()
    missing["manifest"]["curations"].pop()
    with pytest.raises(IncrementReleaseError, match="curations"):
        _validate_manifest_joins(**missing)

    stray = _manifest_bundle()
    stray["quality_signals"]["groups"].append(
        {
            "model": "unknown-model",
            "scenario": SCENARIO,
            "line": LINES[0],
            "variant": "dry",
        },
    )
    with pytest.raises(IncrementReleaseError, match="quality signal"):
        _validate_manifest_joins(**stray)


def test_quality_signalはbaseの597へ増分161を足した758になる() -> None:
    assert BASE_QUALITY_SIGNAL_GROUPS == 597
    assert FINAL_QUALITY_SIGNAL_GROUPS == 758
    assert increment_release.BASE_GROUPS == 1_288
    assert increment_release.FINAL_GROUPS == 1_449
