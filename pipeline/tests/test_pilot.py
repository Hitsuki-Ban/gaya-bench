from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from gaya_pipeline import pilot as pilot_module
from gaya_pipeline.audio import PostprocessProfile
from gaya_pipeline.curation import (
    build_candidate_set,
    canonical_candidate_set_bytes,
    validate_snapshot_bundle,
)
from gaya_pipeline.pilot import (
    ACTIVE_SPEECH_REASON,
    FEATURE_NAMES,
    LINE_COUNT,
    MODELS,
    PROTOCOL,
    RUBRIC_VERSION,
    SCENARIOS,
    PilotError,
    analyze_pilot_bundle,
    build_pilot_bundle,
    validate_pilot_decision,
)
from gaya_pipeline.take_identity import canonical_json, make_take_id
from gaya_pipeline.take_ledger import read_ledger, write_ledger_atomic
from gaya_pipeline.take_manifest_v4 import candidate_from_attempt
from gaya_pipeline.take_sidecar import validate_take_sidecar


SCENARIOS_DIR = Path(__file__).parents[2] / "scenarios"
GROUP_KEYS = ("model", "scenario", "line", "variant")
PROSODY_DURATION_SEC = 2.4
ENCODED_DURATION_SEC = 2.4065
LOUDNESS = {
    "source": "encoded_opus",
    "i_lufs": -18.0,
    "tp_dbtp": -1.0,
    "shortfall": False,
}
PROSODY = {
    "duration_sec": PROSODY_DURATION_SEC,
    "active_speech_sec": 1.5,
    "estimated_mora_count": 12,
    "overall_mora_per_sec": 6.0,
    "active_mora_per_sec": 8.0,
    "pause": {
        "internal_count": 1,
        "internal_total_sec": 0.2,
        "internal_longest_sec": 0.2,
        "leading_sec": 0.1,
        "trailing_sec": 0.2,
    },
    "f0": {
        "median_hz": 150.0,
        "p10_hz": 120.0,
        "p90_hz": 190.0,
        "semitone_std": 2.5,
        "voiced_ratio": 0.75,
    },
    "energy": {"median_dbfs": -24.0, "p95_dbfs": -12.0},
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _model_entry(model: str) -> dict[str, Any]:
    return {
        "id": model,
        "name": model,
        "version": "pilot-test",
        "license_note": "",
        "capabilities": {
            "emotion": True,
            "voice_prompt": True,
            "clone": False,
            "nonverbal": True,
            "reading": True,
        },
    }


def _scenario_document(scenario: str) -> dict[str, Any]:
    return yaml.safe_load(
        (SCENARIOS_DIR / f"{scenario}.yaml").read_text(encoding="utf-8"),
    )


def _scenario_sha(scenario: str) -> str:
    path = SCENARIOS_DIR / f"{scenario}.yaml"
    return _sha(
        canonical_json(
            [{"path": path.name, "sha256": _sha(path.read_bytes())}],
        ).encode("utf-8"),
    )


def _mechanical(sidecar: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "pass",
        "duration_sec": ENCODED_DURATION_SEC,
        "wav": {"codec": "pcm_s16le", "sample_rate_hz": 48_000, "channels": 1},
        "opus": {"codec": "opus", "sample_rate_hz": 48_000, "channels": 1},
        "loudness": LOUDNESS,
        "generation_params": {
            "requested": sidecar["gen_params"]["requested"],
            "realized": sidecar["gen_params"]["realized"],
        },
        "sidecar_provenance": {
            "generation_seconds": sidecar["generation_seconds"],
            "postprocess": sidecar["postprocess"],
            "toolchain": sidecar["toolchain"],
            "loudness": sidecar["loudness"],
        },
    }


def _content(
    *,
    reading: str,
    rejected: bool,
    unvoiced: bool,
) -> dict[str, Any]:
    prosody = deepcopy(PROSODY)
    if unvoiced:
        prosody["f0"]["semitone_std"] = None
        prosody["f0"]["voiced_ratio"] = 0.0
    return {
        "status": "reject" if rejected else "pass",
        "expected_reading": {
            "text": reading,
            "source": "line.reading",
            "normalized": reading,
            "authoritative": True,
            "ambiguous_terms": [],
        },
        "asr": {
            "text": "fixture",
            "normalized_reading": "MISMATCH" if rejected else reading,
            "average_log_probability": -0.2,
        },
        "reading": {
            "character_error_rate": 1.0 if rejected else 0.0,
            "reading_mismatch": rejected,
        },
        "prosody": prosody,
    }


def _write_run(
    *,
    artifacts_dir: Path,
    model: str,
    scenario: str,
    run_number: int,
) -> str:
    run_id = f"pilot-{run_number:02d}"
    run_root = artifacts_dir / "takes" / run_id
    scenario_document = _scenario_document(scenario)
    groups = [
        {
            "model": model,
            "scenario": scenario,
            "line": line["id"],
            "variant": "dry",
        }
        for line in scenario_document["lines"]
    ]
    attempts: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    line_readings = {
        line["id"]: line["reading"] for line in scenario_document["lines"]
    }
    for line_number, group in enumerate(groups):
        for take_index in range(1, 4):
            base = (
                f"audio/{model}/{scenario}/{group['line']}/dry/"
                f"take-{take_index:04d}"
            )
            path_root = run_root / base
            path_root.parent.mkdir(parents=True, exist_ok=True)
            wav_bytes = f"wav:{model}:{scenario}:{group['line']}:{take_index}".encode()
            opus_bytes = f"opus:{model}:{scenario}:{group['line']}:{take_index}".encode()
            wav_path = path_root.with_suffix(".wav")
            opus_path = path_root.with_suffix(".opus")
            sidecar_path = path_root.with_suffix(".json")
            wav_path.write_bytes(wav_bytes)
            opus_path.write_bytes(opus_bytes)
            input_sha = _sha(f"input:{model}:{scenario}:{group['line']}:{take_index}".encode())
            opus_sha = _sha(opus_bytes)
            take_id = make_take_id(
                generation_input_sha256=input_sha,
                final_opus_sha256=opus_sha,
            )
            sampling = {"temperature": 0.8}
            generation = {
                "status": "succeeded",
                "seed": 103 + take_index,
                "sampling": sampling,
                "rtf": 0.5,
            }
            sidecar = {
                "format_version": 1,
                "run_id": run_id,
                **group,
                "take_index": take_index,
                "take_id": take_id,
                "generation_input_sha256": input_sha,
                "wav_sha256": _sha(wav_bytes),
                "opus_sha256": opus_sha,
                "duration_sec": ENCODED_DURATION_SEC,
                "generation_seconds": 1.0,
                "rtf": 0.5,
                "take": {
                    "seed": 103 + take_index,
                    "recipe_version": "pilot-test-v1",
                    "sampling": sampling,
                },
                "gen_params": {
                    "requested": {"temperature": 0.8},
                    "realized": {"temperature": 0.8},
                },
                "postprocess": PostprocessProfile().as_dict(),
                "toolchain": {
                    "ffmpeg_version": "fixture",
                    "ffprobe_version": "fixture",
                    "libopus_encoder": True,
                },
                "loudness": {"fixture": True},
            }
            validate_take_sidecar(sidecar)
            sidecar_path.write_text(
                json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            content_reject = run_number == 1 and line_number == 0 and take_index == 1
            mechanical_reject = run_number == 1 and line_number == 0 and take_index == 2
            if content_reject:
                status = "hard_rejected"
                gates = {"mechanical": "pass", "content": "reject"}
            elif mechanical_reject:
                status = "hard_rejected"
                gates = {"mechanical": "reject", "content": "not_run"}
            else:
                status = "eligible"
                gates = {"mechanical": "pass", "content": "pass"}
            attempt = {
                **group,
                "take_index": take_index,
                "take_id": take_id,
                "generation_input_sha256": input_sha,
                "generation": generation,
                "audio": {
                    "wav_path": f"{base}.wav",
                    "wav_sha256": _sha(wav_bytes),
                    "opus_path": f"{base}.opus",
                    "opus_sha256": opus_sha,
                    "sidecar_sha256": _sha(sidecar_path.read_bytes()),
                },
                "gates": gates,
                "features": {"status": "unscored"},
                "status": status,
            }
            attempts.append(attempt)
            report = {
                **group,
                "take_index": take_index,
                "take_id": take_id,
                "status": status,
                "gates": gates,
                "mechanical": (
                    {
                        "status": "reject",
                        "reason": ACTIVE_SPEECH_REASON,
                    }
                    if mechanical_reject
                    else _mechanical(sidecar)
                ),
                "content": (
                    {"status": "not_run"}
                    if mechanical_reject
                    else _content(
                        reading=line_readings[group["line"]],
                        rejected=content_reject,
                        unvoiced=(
                            run_number == 1
                            and group["line"] == "medic-001"
                            and take_index == 1
                        ),
                    )
                ),
            }
            reports.append(report)
            if status == "eligible":
                candidates.append(
                    candidate_from_attempt(
                        attempt,
                        duration_sec=ENCODED_DURATION_SEC,
                        loudness=LOUDNESS,
                        gate_policy_version="take-gates-v1",
                        recipe_version="pilot-test-v1",
                        requested_params={"temperature": 0.8},
                        realized_params={"temperature": 0.8},
                    ),
                )

    ledger = {
        "format_version": 1,
        "run_id": run_id,
        "created_at": f"2026-07-29T00:00:{run_number:02d}Z",
        "source": {
            "scenario_sha256": _scenario_sha(scenario),
            "model": model,
            "takes": 3,
            "seed_base": 103,
            "recipe_version": "pilot-test-v1",
            "groups": groups,
        },
        "attempts": attempts,
    }
    ledger_path = run_root / "ledger.json"
    write_ledger_atomic(ledger_path, ledger)

    statuses = (
        "eligible",
        "hard_rejected",
        "blocked",
        "generation_failed",
        "planned",
        "generated",
    )
    counts = {
        status: sum(attempt["status"] == status for attempt in attempts)
        for status in statuses
    }
    generated_at = f"2026-07-29T00:00:{run_number:02d}Z"
    report = {
        "format_version": 1,
        "generated_at": generated_at,
        "gate_policy_version": "take-gates-v1",
        "run_id": run_id,
        "source": {
            "ledger": ledger_path.as_posix(),
            "scenario_sha256": ledger["source"]["scenario_sha256"],
            "model": model,
            "recipe_version": "pilot-test-v1",
        },
        "runtime": {"status": "fixture"},
        "summary": {
            "attempt_count": len(attempts),
            **counts,
            "pending": 0,
        },
        "attempts": reports,
    }
    (run_root / "qc-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        {
            "scenario": scenario,
            "line": line["id"],
            "scenario_title": scenario_document["title"],
            "text": line["text"],
            "delivery": line["delivery"],
        }
        for line in scenario_document["lines"]
    ]
    candidate_set = build_candidate_set(
        scenario_sha256=ledger["source"]["scenario_sha256"],
        lines=lines,
        models=[_model_entry(model)],
        candidates=candidates,
        failures=[],
    )
    candidate_bytes = canonical_candidate_set_bytes(candidate_set)
    candidate_sha = _sha(candidate_bytes)
    (run_root / "candidate-set.json").write_bytes(candidate_bytes)
    (run_root / "candidate-set.sha256").write_text(candidate_sha, encoding="ascii")
    manifest = {
        "format_version": 4,
        "generated_at": generated_at,
        "candidate_set_sha256": candidate_sha,
        "models": [_model_entry(model)],
        "candidates": candidates,
        "curations": [],
        "failures": [],
    }
    (run_root / "manifest-v4.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return run_id


@pytest.fixture
def pilot_runs(tmp_path: Path) -> tuple[Path, list[str]]:
    artifacts_dir = tmp_path / "artifacts"
    run_ids: list[str] = []
    for run_number, (model, scenario) in enumerate(
        ((model, scenario) for model in MODELS for scenario in SCENARIOS),
        start=1,
    ):
        run_ids.append(
            _write_run(
                artifacts_dir=artifacts_dir,
                model=model,
                scenario=scenario,
                run_number=run_number,
            ),
        )
    return artifacts_dir, run_ids


def _decision_for(bundle_dir: Path) -> dict[str, Any]:
    pilot_raw = (bundle_dir / "pilot-set.json").read_bytes()
    pilot = json.loads(pilot_raw)
    return {
        "format_version": 1,
        "rubric_version": RUBRIC_VERSION,
        "pilot_set_sha256": _sha(pilot_raw),
        "groups": [
            {
                "group_id": group["group_id"],
                "candidates": [
                    {
                        "candidate_id": candidate_id,
                        "rubric": {
                            "content_correct": True,
                            "intent_match": 4,
                            "character_naturalness": 4,
                            "adoptable": True,
                        },
                    }
                    for candidate_id in group["candidate_ids"]
                ],
                "decision": {
                    "type": "selected",
                    "candidate_id": group["candidate_ids"][0],
                },
            }
            for group in pilot["groups"]
        ],
    }


def test_pilot_scenarios_have_24_explicit_reviewed_readings() -> None:
    readings = {
        (scenario, line["id"]): line.get("reading")
        for scenario in SCENARIOS
        for line in _scenario_document(scenario)["lines"]
    }
    assert len(readings) == 24
    assert all(isinstance(reading, str) and reading for reading in readings.values())
    assert readings[("battlefield-camp", "wounded-003")] == (
        "ハハ……ナサケネースガタダナ、オレワ"
    )
    assert readings[("battlefield-camp", "veteran-soldier-001")] == (
        "モーナンニン、ミオクッタカワカランナ……"
    )


def test_duration_feature_prefers_prosody_measurement_domain() -> None:
    qc_attempt = {
        "content": {
            "prosody": {
                **PROSODY,
                "duration_sec": PROSODY_DURATION_SEC,
            },
        },
        "mechanical": {"duration_sec": ENCODED_DURATION_SEC},
    }
    assert pilot_module._extract_features(qc_attempt)["duration_sec"] == (
        PROSODY_DURATION_SEC
    )

    qc_attempt["content"] = {"status": "not_run"}
    assert pilot_module._extract_features(qc_attempt)["duration_sec"] == (
        ENCODED_DURATION_SEC
    )

    qc_attempt["content"] = {"prosody": {"duration_sec": float("inf")}}
    with pytest.raises(PilotError, match="有限数"):
        pilot_module._extract_features(qc_attempt)


def test_build_is_deterministic_complete_and_blind(
    tmp_path: Path,
    pilot_runs: tuple[Path, list[str]],
) -> None:
    artifacts_dir, run_ids = pilot_runs
    first = build_pilot_bundle(
        run_ids=run_ids,
        output_dir=tmp_path / "bundle-a",
        artifacts_dir=artifacts_dir,
        scenarios_dir=SCENARIOS_DIR,
    )
    second = build_pilot_bundle(
        run_ids=list(reversed(run_ids)),
        output_dir=tmp_path / "bundle-b",
        artifacts_dir=artifacts_dir,
        scenarios_dir=SCENARIOS_DIR,
    )

    assert first.group_count == len(MODELS) * LINE_COUNT
    assert first.candidate_count == len(MODELS) * LINE_COUNT * 3
    assert first.pilot_set_sha256 == second.pilot_set_sha256
    assert first.pilot_set_path.read_bytes() == second.pilot_set_path.read_bytes()
    pilot = json.loads(first.pilot_set_path.read_bytes())
    candidates = {
        candidate["candidate_id"]: candidate for candidate in pilot["candidates"]
    }
    first_take_indexes = [
        candidates[group["candidate_ids"][0]]["take_index"]
        for group in pilot["groups"]
    ]
    assert set(first_take_indexes) != {1}
    model_sequence = [group["model"] for group in pilot["groups"]]
    assert any(
        model_sequence[index] == model_sequence[index + 2]
        and model_sequence[index] != model_sequence[index + 1]
        for index in range(len(model_sequence) - 2)
    )
    for candidate in pilot["candidates"]:
        assert candidate["audio"]["path"] == (
            f"audio/{candidate['candidate_id']}.opus"
        )
        assert candidate["model"] not in candidate["audio"]["path"]
        assert "take-" not in candidate["audio"]["path"]
    assert pilot["generated_at"] == "2026-07-29T00:00:06Z"
    rejected = [
        candidate for candidate in pilot["candidates"]
        if candidate["status"] == "hard_rejected"
    ]
    assert {
        candidate["gates"]["primary_reject_rule"] for candidate in rejected
    } == {"explicit_reading_mismatch", "active_speech_nonpositive"}
    assert {
        candidate["features"]["duration_sec"]
        for candidate in pilot["candidates"]
        if candidate["status"] == "eligible"
    } == {PROSODY_DURATION_SEC}
    unvoiced = next(
        candidate
        for candidate in pilot["candidates"]
        if candidate["model"] == MODELS[0]
        and candidate["scenario"] == "battlefield-camp"
        and candidate["line"] == "medic-001"
        and candidate["take_index"] == 1
    )
    assert unvoiced["status"] == "eligible"
    assert unvoiced["features"]["f0_semitone_std"] is None
    assert unvoiced["features"]["voiced_ratio"] == 0.0


def test_analyze_validates_decision_and_writes_json_and_markdown(
    tmp_path: Path,
    pilot_runs: tuple[Path, list[str]],
) -> None:
    artifacts_dir, run_ids = pilot_runs
    bundle_dir = tmp_path / "bundle"
    build_pilot_bundle(
        run_ids=run_ids,
        output_dir=bundle_dir,
        artifacts_dir=artifacts_dir,
        scenarios_dir=SCENARIOS_DIR,
    )
    decision = _decision_for(bundle_dir)
    decision_path = tmp_path / "decision.json"
    decision_path.write_text(
        json.dumps(decision, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    summary = analyze_pilot_bundle(
        bundle_dir=bundle_dir,
        decision_path=decision_path,
        output_dir=tmp_path / "report",
    )

    report = json.loads(summary.report_json_path.read_text(encoding="utf-8"))
    assert report["scope"]["line_count"] == 24
    assert (
        report["raw_confusion_matrices"]["adoptable"][
            "automated_rejected_human_adoptable"
        ]
        == 2
    )
    assert (
        report["raw_confusion_matrices"]["content_correct"][
            "automated_rejected_content_correct"
        ]
        == 2
    )
    assert report["gate_metrics"]["false_reject_rate_content_correct"] == {
        "numerator": 2,
        "denominator": 216,
        "rate": 0.009259,
    }
    assert report["conclusions"] == {
        "exploratory_scope": "24-line exploratory",
        "production_scorer": "no-go without independent confirmation",
        "n5_policy": [
            {
                "model": model,
                "decision": "maintain_n3",
                "reason": "no paired take4/5 data",
            }
            for model in MODELS
        ],
        "asr_used_for_ranking": False,
    }
    active_rule = next(
        item
        for item in report["rule_false_rejects"]
        if item["rule"] == "active_speech_nonpositive"
    )
    assert active_rule == {
        "rule": "active_speech_nonpositive",
        "rejected_count": 1,
        "content_correct_false_reject_count": 1,
        "content_correct_false_reject_rate": 0.00463,
        "content_correct_share_of_rule_rejects": 1.0,
        "adoptable_false_reject_count": 1,
        "adoptable_false_reject_rate": 0.00463,
        "adoptable_share_of_rule_rejects": 1.0,
    }
    qwen_active_rule = next(
        item
        for item in report["model_rule_rejects"]
        if item["model"] == MODELS[0]
        and item["rule"] == "active_speech_nonpositive"
    )
    assert qwen_active_rule["content_correct_false_reject_rate"] == 0.013889
    assert qwen_active_rule["content_correct_share_of_rule_rejects"] == 1.0
    assert [item["feature"] for item in report["eligible_only_feature_lolo"]] == list(
        FEATURE_NAMES,
    )
    assert all(
        item["fold_count"] == 24 for item in report["eligible_only_feature_lolo"]
    )
    feature_reports = {
        item["feature"]: item for item in report["eligible_only_feature_lolo"]
    }
    assert feature_reports["f0_semitone_std"]["excluded_missing_feature"] == 1
    assert feature_reports["voiced_ratio"]["excluded_missing_feature"] == 0
    markdown = summary.report_markdown_path.read_text(encoding="utf-8")
    assert "# N3 pilot 校正レポート" in markdown
    assert "ASR は feature ranking に使用していない。" in markdown
    assert "24-line exploratory" in markdown
    assert "no-go without independent confirmation" in markdown


def test_decision_raw_sha_and_exact_contract_are_required(
    tmp_path: Path,
    pilot_runs: tuple[Path, list[str]],
) -> None:
    artifacts_dir, run_ids = pilot_runs
    bundle_dir = tmp_path / "bundle"
    build_pilot_bundle(
        run_ids=run_ids,
        output_dir=bundle_dir,
        artifacts_dir=artifacts_dir,
        scenarios_dir=SCENARIOS_DIR,
    )
    pilot = json.loads((bundle_dir / "pilot-set.json").read_bytes())
    decision = _decision_for(bundle_dir)
    decision["pilot_set_sha256"] = "0" * 64
    with pytest.raises(PilotError, match="raw bytes"):
        validate_pilot_decision(
            decision,
            pilot=pilot,
            pilot_set_sha256=_sha((bundle_dir / "pilot-set.json").read_bytes()),
        )

    decision = _decision_for(bundle_dir)
    decision["groups"][0]["extra"] = True
    with pytest.raises(PilotError, match="exact contract"):
        validate_pilot_decision(
            decision,
            pilot=pilot,
            pilot_set_sha256=_sha((bundle_dir / "pilot-set.json").read_bytes()),
        )


def test_decision_keeps_rubric_axes_and_relative_selection_independent(
    tmp_path: Path,
    pilot_runs: tuple[Path, list[str]],
) -> None:
    artifacts_dir, run_ids = pilot_runs
    bundle_dir = tmp_path / "bundle"
    build_pilot_bundle(
        run_ids=run_ids,
        output_dir=bundle_dir,
        artifacts_dir=artifacts_dir,
        scenarios_dir=SCENARIOS_DIR,
    )
    pilot_raw = (bundle_dir / "pilot-set.json").read_bytes()
    pilot = json.loads(pilot_raw)
    decision = _decision_for(bundle_dir)
    first = decision["groups"][0]
    first["candidates"][0]["rubric"] = {
        "content_correct": False,
        "intent_match": 4,
        "character_naturalness": 4,
        "adoptable": True,
    }
    second = decision["groups"][1]
    second["candidates"][0]["rubric"] = {
        "content_correct": True,
        "intent_match": 4,
        "character_naturalness": 4,
        "adoptable": False,
    }

    validated = validate_pilot_decision(
        decision,
        pilot=pilot,
        pilot_set_sha256=_sha(pilot_raw),
    )

    assert validated["groups"][0]["candidates"][0]["rubric"] == {
        "content_correct": False,
        "intent_match": 4,
        "character_naturalness": 4,
        "adoptable": True,
    }
    assert validated["groups"][0]["decision"]["candidate_id"] == (
        first["candidates"][0]["candidate_id"]
    )
    assert validated["groups"][1]["candidates"][0]["rubric"]["adoptable"] is False
    assert validated["groups"][1]["decision"]["candidate_id"] == (
        second["candidates"][0]["candidate_id"]
    )


def test_builder_rejects_blocked_run(
    tmp_path: Path,
    pilot_runs: tuple[Path, list[str]],
) -> None:
    artifacts_dir, run_ids = pilot_runs
    ledger_path = artifacts_dir / "takes" / run_ids[0] / "ledger.json"
    ledger = read_ledger(ledger_path)
    ledger["attempts"][0]["status"] = "blocked"
    ledger["attempts"][0]["gates"] = {
        "mechanical": "pass",
        "content": "blocked",
    }
    write_ledger_atomic(ledger_path, ledger)
    with pytest.raises(PilotError, match="blocked/failure/pending"):
        build_pilot_bundle(
            run_ids=run_ids,
            output_dir=tmp_path / "bundle",
            artifacts_dir=artifacts_dir,
            scenarios_dir=SCENARIOS_DIR,
        )


@pytest.mark.parametrize("drift_field", ["text", "reading"])
def test_builder_rejects_current_scenario_text_or_reading_drift(
    tmp_path: Path,
    pilot_runs: tuple[Path, list[str]],
    drift_field: str,
) -> None:
    artifacts_dir, run_ids = pilot_runs
    scenarios_dir = tmp_path / "scenarios"
    shutil.copytree(SCENARIOS_DIR, scenarios_dir)
    shutil.copytree(SCENARIOS_DIR.parent / "assets", tmp_path / "assets")
    scenario_path = scenarios_dir / "battlefield-camp.yaml"
    scenario = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    scenario["lines"][0][drift_field] += "ドリフト"
    scenario_path.write_text(
        yaml.safe_dump(
            scenario,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(PilotError, match="current scenario source"):
        build_pilot_bundle(
            run_ids=run_ids,
            output_dir=tmp_path / "bundle",
            artifacts_dir=artifacts_dir,
            scenarios_dir=scenarios_dir,
        )


def test_builder_rejects_internally_consistent_snapshot_candidate_tampering(
    tmp_path: Path,
    pilot_runs: tuple[Path, list[str]],
) -> None:
    artifacts_dir, run_ids = pilot_runs
    run_root = artifacts_dir / "takes" / run_ids[0]
    manifest_path = run_root / "manifest-v4.json"
    candidate_set_path = run_root / "candidate-set.json"
    marker_path = run_root / "candidate-set.sha256"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidate_set = json.loads(candidate_set_path.read_bytes())
    manifest["candidates"][0]["gen_params"]["requested"]["temperature"] = 0.9
    candidate_set["candidates"][0]["gen_params"]["requested"]["temperature"] = 0.9
    candidate_bytes = canonical_candidate_set_bytes(candidate_set)
    candidate_sha = _sha(candidate_bytes)
    candidate_set_path.write_bytes(candidate_bytes)
    marker_path.write_text(candidate_sha, encoding="ascii")
    manifest["candidate_set_sha256"] = candidate_sha
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    validate_snapshot_bundle(
        snapshot_path=manifest_path,
        candidate_set_path=candidate_set_path,
        marker_path=marker_path,
    )

    with pytest.raises(PilotError, match="snapshot provenance"):
        build_pilot_bundle(
            run_ids=run_ids,
            output_dir=tmp_path / "bundle",
            artifacts_dir=artifacts_dir,
            scenarios_dir=SCENARIOS_DIR,
        )


def test_analyzer_rejects_tampered_blind_audio(
    tmp_path: Path,
    pilot_runs: tuple[Path, list[str]],
) -> None:
    artifacts_dir, run_ids = pilot_runs
    bundle_dir = tmp_path / "bundle"
    build_pilot_bundle(
        run_ids=run_ids,
        output_dir=bundle_dir,
        artifacts_dir=artifacts_dir,
        scenarios_dir=SCENARIOS_DIR,
    )
    pilot = json.loads((bundle_dir / "pilot-set.json").read_bytes())
    audio_path = bundle_dir / pilot["candidates"][0]["audio"]["path"]
    audio_path.write_bytes(b"tampered")
    decision_path = tmp_path / "decision.json"
    decision_path.write_text(
        json.dumps(_decision_for(bundle_dir), ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PilotError, match="audio SHA"):
        analyze_pilot_bundle(
            bundle_dir=bundle_dir,
            decision_path=decision_path,
            output_dir=tmp_path / "report",
        )
