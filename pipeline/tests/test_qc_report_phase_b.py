from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from gaya_pipeline.qc_report import QCReportError, validate_qc_report
from gaya_pipeline.take_identity import canonical_json


def _fixture() -> tuple[dict[str, object], dict[str, object], Path]:
    ledger_path = Path("artifacts/takes/primary-run/ledger.json")
    phase_b = {
        "protocol": "phase-b-generation-v1",
        "plan_sha256": "a" * 64,
        "run_kind": "primary",
        "supersedes_run_id": None,
        "anchor_selection_sha256": None,
        "target_groups": [
            {
                "model": "dummy",
                "scenario": "battlefield-camp",
                "line": "wounded-001",
                "variant": "dry",
                "role_epoch_sha256": "b" * 64,
            },
        ],
    }
    ledger = {
        "run_id": "primary-run",
        "source": {
            "scenario_sha256": "c" * 64,
            "model": "dummy",
            "recipe_version": "test-v1",
            "phase_b": phase_b,
        },
        "attempts": [],
    }
    report = {
        "format_version": 2,
        "generated_at": "2026-07-31T00:00:00Z",
        "gate_policy_version": "take-gates-v2",
        "run_id": "primary-run",
        "source": {
            "ledger": ledger_path.as_posix(),
            "scenario_sha256": "c" * 64,
            "model": "dummy",
            "recipe_version": "test-v1",
            "phase_b": deepcopy(phase_b),
        },
        "runtime": {"status": "not_required"},
        "summary": {
            "attempt_count": 0,
            "eligible": 0,
            "hard_rejected": 0,
            "blocked": 0,
            "generation_failed": 0,
            "planned": 0,
            "generated": 0,
            "pending": 0,
            "content_review_required": 0,
        },
        "attempts": [],
    }
    return ledger, report, ledger_path


def _attempt_fixture() -> tuple[dict[str, object], dict[str, object], Path]:
    ledger, report, ledger_path = _fixture()
    phase_b = ledger["source"]["phase_b"]  # type: ignore[index]
    target = phase_b["target_groups"][0]  # type: ignore[index]
    target["model"] = "qwen3-tts-12hz-1.7b"
    phase_b["anchor_selection_sha256"] = "d" * 64  # type: ignore[index]
    ledger["source"]["model"] = "qwen3-tts-12hz-1.7b"  # type: ignore[index]
    report["source"]["model"] = "qwen3-tts-12hz-1.7b"  # type: ignore[index]
    report["source"]["phase_b"] = deepcopy(phase_b)  # type: ignore[index]
    provenance = {
        "protocol": phase_b["protocol"],  # type: ignore[index]
        "plan_sha256": phase_b["plan_sha256"],  # type: ignore[index]
        "run_kind": phase_b["run_kind"],  # type: ignore[index]
        "supersedes_run_id": phase_b["supersedes_run_id"],  # type: ignore[index]
        "anchor_selection_sha256": phase_b[  # type: ignore[index]
            "anchor_selection_sha256"
        ],
        "target_group": deepcopy(target),
    }
    identity = {
        "model": target["model"],
        "scenario": target["scenario"],
        "line": target["line"],
        "variant": target["variant"],
        "take_index": 1,
    }
    gates = {"mechanical": "pass", "content": "blocked"}
    ledger["attempts"] = [
        {
            **identity,
            "phase_b_provenance_sha256": hashlib.sha256(
                canonical_json(provenance).encode("utf-8"),
            ).hexdigest(),
            "gates": gates,
            "status": "blocked",
        },
    ]
    report["summary"] = {
        "attempt_count": 1,
        "eligible": 0,
        "hard_rejected": 0,
        "blocked": 1,
        "generation_failed": 0,
        "planned": 0,
        "generated": 0,
        "pending": 0,
        "content_review_required": 0,
    }
    report["attempts"] = [
        {
            **identity,
            "status": "blocked",
            "gates": gates,
            "mechanical": {
                "status": "pass",
                "duration_sec": 1.0,
                "wav": {
                    "codec": "pcm_s16le",
                    "sample_rate_hz": 48_000,
                    "channels": 1,
                },
                "opus": {
                    "codec": "opus",
                    "sample_rate_hz": 48_000,
                    "channels": 1,
                },
                "loudness": {
                    "source": "encoded_opus",
                    "i_lufs": -18.0,
                    "tp_dbtp": -2.0,
                    "shortfall": False,
                },
                "generation_params": {
                    "requested": {
                        "phase_b_provenance": deepcopy(provenance),
                    },
                    "realized": {
                        "phase_b_provenance": deepcopy(provenance),
                        "reference_control": (
                            "selected_voice_design_anchor"
                        ),
                        "selected_anchor": {
                            "anchor_selection_sha256": "d" * 64,
                            "anchor_plan_sha256": "a" * 64,
                            "role_epoch_sha256": "b" * 64,
                        },
                    },
                },
                "sidecar_provenance": {
                    "generation_seconds": 0.5,
                    "postprocess": {},
                    "toolchain": {},
                    "loudness": {},
                },
            },
            "content": {"status": "blocked", "reason": "review unavailable"},
        },
    ]
    return ledger, report, ledger_path


def test_qc_sourceはledger_phase_b_provenanceをexactに固定する() -> None:
    ledger, report, ledger_path = _fixture()
    validate_qc_report(report, ledger_path=ledger_path, ledger=ledger)

    report["source"]["phase_b"]["target_groups"][0][  # type: ignore[index]
        "role_epoch_sha256"
    ] = "d" * 64
    with pytest.raises(QCReportError, match="source"):
        validate_qc_report(report, ledger_path=ledger_path, ledger=ledger)


def test_qc_sourceはphase_b欠落を拒否する() -> None:
    ledger, report, ledger_path = _fixture()
    report["source"].pop("phase_b")  # type: ignore[union-attr]
    with pytest.raises(QCReportError, match="source"):
        validate_qc_report(report, ledger_path=ledger_path, ledger=ledger)


def test_qc_phase_b_attempt_provenanceをsourceから再構築する() -> None:
    ledger, report, ledger_path = _attempt_fixture()
    validate_qc_report(report, ledger_path=ledger_path, ledger=ledger)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda ledger, report: report["attempts"][0]["mechanical"][
            "generation_params"
        ]["requested"]["phase_b_provenance"].update(plan_sha256="e" * 64),
        lambda ledger, report: report["attempts"][0]["mechanical"][
            "generation_params"
        ]["realized"]["phase_b_provenance"]["target_group"].update(
            role_epoch_sha256="e" * 64,
        ),
        lambda ledger, report: report["attempts"][0]["mechanical"][
            "generation_params"
        ]["realized"]["phase_b_provenance"].update(
            anchor_selection_sha256="e" * 64,
        ),
        lambda ledger, report: report["attempts"][0]["mechanical"][
            "generation_params"
        ]["realized"]["selected_anchor"].update(
            anchor_plan_sha256="e" * 64,
        ),
        lambda ledger, report: ledger["attempts"][0].update(
            phase_b_provenance_sha256="e" * 64,
        ),
    ],
)
def test_qc_phase_bのrequested_realized_receipt_hash改変を拒否(
    mutation: object,
) -> None:
    ledger, report, ledger_path = _attempt_fixture()
    mutation(ledger, report)  # type: ignore[operator]
    with pytest.raises(QCReportError, match="provenance|receipt"):
        validate_qc_report(report, ledger_path=ledger_path, ledger=ledger)
