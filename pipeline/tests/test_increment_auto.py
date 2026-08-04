from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

import gaya_pipeline.completion_auto as completion_auto
import gaya_pipeline.increment_auto as increment_auto
from gaya_pipeline.completion_auto import (
    EXPECTED_CANDIDATE_COUNT,
    EXPECTED_GROUP_COUNT,
    QUALITY_SIGNALS_PROTOCOL,
    CompletionAutoDecisionError,
    _gender_screening,
    _quality_signals_document,
    canonical_completion_quality_signals_bytes,
    validate_completion_quality_signals,
)
from gaya_pipeline.completion_plan import CompletionTarget, ModelPolicy, RoleSnapshot
from gaya_pipeline.completion_selection import (
    AUTO_SELECTION_POLICY,
    GENDER_SCREENING_PROTOCOL,
)
from gaya_pipeline.increment_anchor import IncrementAnchorError
from gaya_pipeline.increment_auto import (
    IncrementAutoDecisionError,
    create_increment_auto_decision,
    machine_anchor_loader,
)
from gaya_pipeline.increment_plan import (
    INCREMENT_GROUPS,
    INCREMENT_MINIMUM_ELIGIBLE,
    IncrementPlan,
)


INCREMENT_MODEL = "irodori-tts-v4-small"


def _quality_signal_group(index: int) -> dict[str, Any]:
    return {
        "model": INCREMENT_MODEL,
        "scenario": "scene",
        "line": f"line-{index:04d}",
        "variant": "dry",
        "protocol": GENDER_SCREENING_PROTOCOL,
        "expected_gender": "female",
        "median_f0_hz": 210.0,
        "status": "pass",
        "signal": None,
        "qc_report_sha256": "b" * 64,
    }


def _quality_signals(count: int) -> dict[str, Any]:
    return {
        "format_version": 1,
        "protocol": QUALITY_SIGNALS_PROTOCOL,
        "plan_sha256": "a" * 64,
        "decision_sha256": "c" * 64,
        "groups": [_quality_signal_group(index) for index in range(count)],
    }


def _increment_plan(
    *,
    model: str = INCREMENT_MODEL,
    target_count: int = INCREMENT_GROUPS,
) -> IncrementPlan:
    return IncrementPlan(
        plan_id="1" * 64,
        model=model,
        base_manifest_sha256="2" * 64,
        base_candidate_set_sha256="3" * 64,
        base_selection_sha256="4" * 64,
        base_quality_signals_sha256="5" * 64,
        base_release_provenance_sha256="6" * 64,
        base_groups=1_288,
        increment_groups=INCREMENT_GROUPS,
        final_groups=1_288 + INCREMENT_GROUPS,
        anchor_source_plan_sha256="7" * 64,
        anchor_candidate_set_sha256="8" * 64,
        anchor_selection_sha256="9" * 64,
        scenario_registry_sha256="a" * 64,
        scenario_files=(),
        voice_registry_path="assets/voices/metadata.yaml",
        voice_registry_sha256="b" * 64,
        models={model: "revision"},
        roles=(
            RoleSnapshot(
                scenario="scene",
                character="actor",
                role={"gender": "female"},
                reference_voice=None,
                scene_setting="setting",
                role_identity_sha256="c" * 64,
            ),
        ),
        model_policies=(
            ModelPolicy(
                model=model,
                takes=4,
                minimum_eligible_candidates=INCREMENT_MINIMUM_ELIGIBLE,
                seed_policy="derived-sha256-v1",
                primary_seed_base=194,
            ),
        ),
        targets=tuple(
            CompletionTarget(model, "scene", f"line-{index:04d}", "dry")
            for index in range(target_count)
        ),
        raw_sha256="d" * 64,
    )


def test_quality_signalsは増分161groupをexpected_group_countで受理する() -> None:
    document = _quality_signals(INCREMENT_GROUPS)

    normalized = validate_completion_quality_signals(
        document,
        expected_group_count=INCREMENT_GROUPS,
    )

    assert len(normalized["groups"]) == INCREMENT_GROUPS
    assert normalized["protocol"] == "role-quality-signals-v1"
    assert {group["protocol"] for group in normalized["groups"]} == {
        "role-gender-f0-soft-v1",
    }
    assert canonical_completion_quality_signals_bytes(
        document,
        expected_group_count=INCREMENT_GROUPS,
    ).startswith(b'{"decision_sha256"')


def test_quality_signalsの既定は597固定で161を拒否する() -> None:
    with pytest.raises(CompletionAutoDecisionError, match="exact 597 group"):
        validate_completion_quality_signals(_quality_signals(INCREMENT_GROUPS))


@pytest.mark.parametrize("count", [596, 598])
def test_quality_signalsの既定は597近傍も拒否する(count: int) -> None:
    with pytest.raises(CompletionAutoDecisionError, match="exact 597 group"):
        validate_completion_quality_signals(_quality_signals(count))
    with pytest.raises(CompletionAutoDecisionError, match="exact 597 group"):
        canonical_completion_quality_signals_bytes(_quality_signals(count))


@pytest.mark.parametrize("count", [160, 162])
def test_quality_signalsは増分expected_group_count以外を拒否する(count: int) -> None:
    with pytest.raises(CompletionAutoDecisionError, match="exact 161 group"):
        validate_completion_quality_signals(
            _quality_signals(count),
            expected_group_count=INCREMENT_GROUPS,
        )


@pytest.mark.parametrize(
    ("gender", "median", "status", "signal"),
    [
        ("female", 164.9, "review_required", "gender_f0_below_expected"),
        ("female", 165.0, "pass", None),
        ("female", 210.0, "pass", None),
        ("male", 180.0, "pass", None),
        ("male", 180.1, "review_required", "gender_f0_above_expected"),
        ("male", 120.0, "pass", None),
        ("neutral", 90.0, "not_applicable", None),
        ("neutral", None, "not_applicable", None),
        ("female", None, "review_required", "gender_f0_unavailable"),
        ("male", None, "review_required", "gender_f0_unavailable"),
    ],
)
def test_増分F0_gender判定は165_180_thresholdをreleaseと同一に保つ(
    gender: str,
    median: float | None,
    status: str,
    signal: str | None,
) -> None:
    screening = _gender_screening(
        expected_gender=gender,
        attempt={"content": {"prosody": {"f0": {"median_hz": median}}}},
        qc_report_sha256="b" * 64,
    )

    assert screening["protocol"] == GENDER_SCREENING_PROTOCOL
    assert (screening["status"], screening["signal"]) == (status, signal)
    assert screening["median_f0_hz"] == median


def test_増分quality_signalsはdecision_screeningから161group文書を作る() -> None:
    decision = {
        "groups": [
            {
                "model": INCREMENT_MODEL,
                "scenario": "scene",
                "line": f"line-{index:04d}",
                "variant": "dry",
                "screening": _gender_screening(
                    expected_gender="female" if index % 2 else "male",
                    attempt={
                        "content": {
                            "prosody": {"f0": {"median_hz": 120.0 + index}},
                        },
                    },
                    qc_report_sha256="b" * 64,
                ),
            }
            for index in range(INCREMENT_GROUPS)
        ],
    }

    document = _quality_signals_document(
        plan=_increment_plan(),
        decision=decision,
        decision_sha256="c" * 64,
    )

    normalized = validate_completion_quality_signals(
        document,
        expected_group_count=INCREMENT_GROUPS,
    )
    assert normalized["plan_sha256"] == "1" * 64
    assert len(normalized["groups"]) == INCREMENT_GROUPS
    with pytest.raises(CompletionAutoDecisionError, match="exact 597 group"):
        validate_completion_quality_signals(document)


def test_既定引数は597_2307_production固定のまま変わらない() -> None:
    assert (EXPECTED_GROUP_COUNT, EXPECTED_CANDIDATE_COUNT) == (597, 2_307)

    defaults = {
        name: parameter.default
        for name, parameter in inspect.signature(
            completion_auto.create_completion_auto_decision,
        ).parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }
    assert defaults == {
        "expected_group_count": 597,
        "expected_candidate_count": 2_307,
        "require_production": True,
        "primary_models": None,
        "anchor_loader": None,
        "anchor_bound_models": None,
        "minimum_candidate_count": None,
    }
    for function in (
        validate_completion_quality_signals,
        canonical_completion_quality_signals_bytes,
    ):
        assert (
            inspect.signature(function).parameters["expected_group_count"].default
            == 597
        )


def test_増分autoは161group_candidate非固定_非production契約を渡す(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        completion_auto,
        "create_completion_auto_decision",
        lambda **kwargs: captured.update(kwargs) or "summary",
    )
    plan = _increment_plan()

    result = create_increment_auto_decision(
        plan=plan,
        primary_run_ids=["primary-1"],
        topup_run_ids=[],
        anchor_selection_path=tmp_path / "anchor.json",
        artifacts_dir=tmp_path / "artifacts",
        scenarios_dir=tmp_path / "scenarios",
        voices_dir=tmp_path / "voices",
        pasqa_project_dir=tmp_path / "pasqa",
        pasqa_model_dir=tmp_path / "model",
        output_dir=tmp_path / "output",
    )

    assert result == "summary"
    assert captured["plan"] is plan
    assert captured["expected_group_count"] == INCREMENT_GROUPS
    assert captured["expected_candidate_count"] is None
    assert captured["require_production"] is False
    assert captured["primary_models"] == frozenset({INCREMENT_MODEL})
    assert captured["anchor_bound_models"] == frozenset({INCREMENT_MODEL})
    assert captured["anchor_loader"] is machine_anchor_loader
    assert captured["minimum_candidate_count"] == (
        INCREMENT_GROUPS * INCREMENT_MINIMUM_ELIGIBLE
    )


def test_増分autoはIncrementPlan以外と161件以外のtargetを拒否する(
    tmp_path: Path,
) -> None:
    arguments: dict[str, Any] = {
        "primary_run_ids": ["primary-1"],
        "topup_run_ids": [],
        "anchor_selection_path": tmp_path / "anchor.json",
        "artifacts_dir": tmp_path / "artifacts",
        "scenarios_dir": tmp_path / "scenarios",
        "voices_dir": tmp_path / "voices",
        "pasqa_project_dir": tmp_path / "pasqa",
        "pasqa_model_dir": tmp_path / "model",
        "output_dir": tmp_path / "output",
    }

    with pytest.raises(IncrementAutoDecisionError, match="IncrementPlan"):
        create_increment_auto_decision(plan=object(), **arguments)
    with pytest.raises(IncrementAutoDecisionError, match="exact 161 target"):
        create_increment_auto_decision(
            plan=_increment_plan(target_count=160),
            **arguments,
        )


def test_machine_anchor_loaderはIncrementAnchorErrorをfail_fastで包む(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _fail(_path: Path, *, plan: Any) -> None:
        raise IncrementAnchorError("selection不整合")

    monkeypatch.setattr(
        increment_auto,
        "load_machine_anchor_selection",
        _fail,
    )

    with pytest.raises(IncrementAutoDecisionError, match="machine anchor selection"):
        machine_anchor_loader(tmp_path / "anchor.json", _increment_plan())

    monkeypatch.setattr(
        increment_auto,
        "load_machine_anchor_selection",
        lambda _path, *, plan: ("e" * 64, {(plan.model, "scene", "actor"): "f" * 64}),
    )
    digest, epochs = machine_anchor_loader(
        tmp_path / "anchor.json",
        _increment_plan(),
    )

    assert digest == "e" * 64
    assert epochs == {(INCREMENT_MODEL, "scene", "actor"): "f" * 64}


def test_増分autoは公開protocolとpolicy_versionを再宣言しない() -> None:
    assert QUALITY_SIGNALS_PROTOCOL == "role-quality-signals-v1"
    assert GENDER_SCREENING_PROTOCOL == "role-gender-f0-soft-v1"
    assert AUTO_SELECTION_POLICY == "phase-b-auto-selection-v1"
    assert completion_auto.BATCH_POLICY == "qc-content-then-pasqa-then-duration-v1"
    assert increment_auto.IncrementAutoDecisionSummary is (
        completion_auto.CompletionAutoDecisionSummary
    )
