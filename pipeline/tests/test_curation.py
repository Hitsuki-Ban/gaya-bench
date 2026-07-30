from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaya_pipeline import cli, curation as curation_module
from gaya_pipeline.audio import PostprocessProfile
from gaya_pipeline.curation import (
    CurationError,
    apply_curation,
    build_candidate_set,
    canonical_candidate_set_bytes,
    canonical_curation_bytes,
    load_authoritative_candidate_lines,
)
from gaya_pipeline.take_identity import canonical_json, make_take_id
from gaya_pipeline.take_ledger import read_ledger, write_ledger_atomic
from gaya_pipeline.take_manifest_v4 import validate_manifest_v4
from gaya_pipeline.take_sidecar import validate_take_sidecar


FIXTURE = Path(__file__).parent / "fixtures" / "manifest-v4-valid.json"
SCENARIOS_DIR = Path(__file__).parents[2] / "scenarios"
GROUP_KEYS = ("model", "scenario", "line", "variant")


def _write_take_files(
    *,
    run_root: Path,
    run_id: str,
    candidate: dict[str, Any],
    opus_bytes: bytes,
) -> tuple[Path, dict[str, str]]:
    path_root = (
        run_root
        / "audio"
        / candidate["model"]
        / candidate["scenario"]
        / candidate["line"]
        / candidate["variant"]
    )
    base_name = f"take-{candidate['take_index']:04d}"
    wav_path = path_root / f"{base_name}.wav"
    opus_path = path_root / f"{base_name}.opus"
    sidecar_path = path_root / f"{base_name}.json"
    wav_bytes = b"wav fixture:" + opus_bytes
    path_root.mkdir(parents=True, exist_ok=True)
    wav_path.write_bytes(wav_bytes)
    opus_path.write_bytes(opus_bytes)
    wav_sha = hashlib.sha256(wav_bytes).hexdigest()
    opus_sha = hashlib.sha256(opus_bytes).hexdigest()
    assert opus_sha == candidate["sha256"]
    sidecar = {
        "format_version": 1,
        "run_id": run_id,
        **{key: candidate[key] for key in GROUP_KEYS},
        "take_index": candidate["take_index"],
        "take_id": candidate["take_id"],
        "generation_input_sha256": candidate["generation_input_sha256"],
        "wav_sha256": wav_sha,
        "opus_sha256": opus_sha,
        "duration_sec": candidate["duration_sec"],
        "generation_seconds": candidate["duration_sec"] * candidate["rtf"],
        "rtf": candidate["rtf"],
        "take": {
            "seed": candidate["gen_params"]["seed"],
            "recipe_version": candidate["gen_params"]["recipe_version"],
            "sampling": candidate["gen_params"]["sampling"],
        },
        "gen_params": {
            "requested": candidate["gen_params"]["requested"],
            "realized": candidate["gen_params"]["realized"],
        },
        "postprocess": PostprocessProfile().as_dict(),
        "toolchain": {
            "ffmpeg_version": "fixture-ffmpeg",
            "ffprobe_version": "fixture-ffprobe",
            "libopus_encoder": True,
        },
        "loudness": {},
    }
    validate_take_sidecar(sidecar)
    sidecar_path.write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    relative_root = (
        f"audio/{candidate['model']}/{candidate['scenario']}/"
        f"{candidate['line']}/{candidate['variant']}/{base_name}"
    )
    return opus_path, {
        "wav_path": f"{relative_root}.wav",
        "wav_sha256": wav_sha,
        "opus_path": f"{relative_root}.opus",
        "opus_sha256": opus_sha,
        "sidecar_sha256": hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
    }


def _write_qc_report(
    *,
    run_root: Path,
    manifest: dict[str, Any],
) -> None:
    ledger_path = run_root / "ledger.json"
    ledger = read_ledger(ledger_path)
    candidates_by_slot = {
        tuple(candidate[key] for key in GROUP_KEYS) + (candidate["take_index"],): candidate
        for candidate in manifest["candidates"]
    }
    reports: list[dict[str, Any]] = []
    for attempt in ledger["attempts"]:
        slot = tuple(attempt[key] for key in GROUP_KEYS) + (attempt["take_index"],)
        report = {
            **{key: attempt[key] for key in (*GROUP_KEYS, "take_index")},
            "status": attempt["status"],
            "gates": attempt.get("gates"),
            "mechanical": {"status": "not_run"},
            "content": {"status": "not_run"},
        }
        if "take_id" in attempt:
            report["take_id"] = attempt["take_id"]
        if attempt["status"] == "eligible":
            candidate = candidates_by_slot[slot]
            sidecar_path = (
                run_root / attempt["audio"]["opus_path"]
            ).with_suffix(".json")
            sidecar = validate_take_sidecar(
                json.loads(sidecar_path.read_text(encoding="utf-8")),
            )
            report["mechanical"] = {
                "status": "pass",
                "duration_sec": candidate["duration_sec"],
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
                "loudness": candidate["loudness"],
                "generation_params": {
                    "requested": candidate["gen_params"]["requested"],
                    "realized": candidate["gen_params"]["realized"],
                },
                "sidecar_provenance": {
                    "generation_seconds": sidecar["generation_seconds"],
                    "postprocess": sidecar["postprocess"],
                    "toolchain": sidecar["toolchain"],
                    "loudness": sidecar["loudness"],
                },
            }
            report["content"] = {
                "status": attempt["gates"]["content"],
                "review_reason": "non_authoritative_expected_reading",
                "expected_reading": {
                    "text": "フィクスチャ",
                    "source": "derived",
                    "normalized": "フィクスチャ",
                    "authoritative": False,
                    "ambiguous_terms": [],
                },
                "asr": {
                    "text": "フィクスチャ",
                    "normalized_reading": "フィクスチャ",
                    "average_log_probability": None,
                },
                "reading": {
                    "character_error_rate": 0.0,
                    "reading_mismatch": None,
                },
                "prosody": {},
            }
        reports.append(report)
    statuses = (
        "eligible",
        "hard_rejected",
        "blocked",
        "generation_failed",
        "planned",
        "generated",
    )
    counts = {
        status: sum(attempt["status"] == status for attempt in ledger["attempts"])
        for status in statuses
    }
    policy_versions = {
        candidate["gate"]["policy_version"] for candidate in manifest["candidates"]
    }
    assert len(policy_versions) == 1
    report_document = {
        "format_version": 2,
        "generated_at": manifest["generated_at"],
        "gate_policy_version": next(iter(policy_versions)),
        "run_id": ledger["run_id"],
        "source": {
            "ledger": ledger_path.as_posix(),
            "scenario_sha256": ledger["source"]["scenario_sha256"],
            "model": ledger["source"]["model"],
            "recipe_version": ledger["source"]["recipe_version"],
        },
        "runtime": {"status": "fixture"},
        "summary": {
            "attempt_count": len(ledger["attempts"]),
            **counts,
            "pending": counts["planned"] + counts["generated"],
            "content_review_required": sum(
                attempt["status"] == "eligible"
                and attempt.get("gates", {}).get("content") == "review_required"
                for attempt in ledger["attempts"]
            ),
        },
        "attempts": reports,
    }
    (run_root / "qc-report.json").write_text(
        json.dumps(report_document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _setup_run(
    tmp_path: Path,
    *,
    run_id: str = "run-1",
    model: str = "dummy",
    audio_bytes: bytes = b"local opus fixture",
    scenarios_dir: Path = SCENARIOS_DIR,
    line_text: str = "はいよっ、エール二つお待ち！",
) -> tuple[str, dict[str, Any], Path, Path]:
    run_root = tmp_path / "artifacts" / "takes" / run_id
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    manifest["models"][0]["id"] = model
    candidate = manifest["candidates"][0]
    candidate["model"] = model
    audio_sha = hashlib.sha256(audio_bytes).hexdigest()
    input_sha = "a" * 64
    candidate.update(
        sha256=audio_sha,
        generation_input_sha256=input_sha,
        take_id=make_take_id(
            generation_input_sha256=input_sha,
            final_opus_sha256=audio_sha,
        ),
        path=(
            f"audio/takes/{model}/tavern-night/barmaid-001/dry/"
            f"take-0001-{audio_sha}.opus"
        ),
    )
    candidate["gate"]["policy_version"] = "take-gates-v2"
    manifest["curations"] = []
    manifest["failures"] = []
    scenario_path = scenarios_dir / "tavern-night.yaml"
    source_sha = hashlib.sha256(scenario_path.read_bytes()).hexdigest()
    scenario_sha = hashlib.sha256(
        canonical_json(
            [{"path": scenario_path.name, "sha256": source_sha}],
        ).encode(),
    ).hexdigest()
    candidate_set = build_candidate_set(
        scenario_sha256=scenario_sha,
        lines=[
            {
                "scenario": "tavern-night",
                "line": "barmaid-001",
                "scenario_title": "酒場・夜の喧騒",
                "text": line_text,
                "delivery": (
                    "客へ呼びかける。喧騒に負けないやや大きめの声。語尾が弾む。"
                ),
            },
        ],
        models=manifest["models"],
        candidates=manifest["candidates"],
        failures=manifest["failures"],
    )
    candidate_bytes = canonical_candidate_set_bytes(candidate_set)
    manifest["candidate_set_sha256"] = hashlib.sha256(candidate_bytes).hexdigest()
    validate_manifest_v4(manifest)

    run_root.mkdir(parents=True)
    snapshot_path = run_root / "manifest-v4.json"
    snapshot_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_root / "candidate-set.json").write_bytes(candidate_bytes)
    (run_root / "candidate-set.sha256").write_bytes(
        manifest["candidate_set_sha256"].encode("ascii"),
    )
    audio_path, ledger_audio = _write_take_files(
        run_root=run_root,
        run_id=run_id,
        candidate=candidate,
        opus_bytes=audio_bytes,
    )
    ledger = {
        "format_version": 1,
        "run_id": run_id,
        "created_at": "2026-07-29T00:00:00Z",
        "source": {
            "scenario_sha256": scenario_sha,
            "model": model,
            "takes": 1,
            "seed_base": 0,
            "recipe_version": "fixed-single-v1",
            "groups": [
                {
                    "model": model,
                    "scenario": "tavern-night",
                    "line": "barmaid-001",
                    "variant": "dry",
                },
            ],
        },
        "attempts": [
            {
                "model": model,
                "scenario": "tavern-night",
                "line": "barmaid-001",
                "variant": "dry",
                "take_index": 1,
                "take_id": candidate["take_id"],
                "generation_input_sha256": input_sha,
                "generation": {
                    "status": "succeeded",
                    "seed": None,
                    "sampling": {},
                    "rtf": 0.5,
                },
                "audio": ledger_audio,
                "gates": {
                    "mechanical": "pass",
                    "content": "review_required",
                },
                "features": {"status": "unscored"},
                "status": "eligible",
            },
        ],
    }
    write_ledger_atomic(run_root / "ledger.json", ledger)
    _write_qc_report(run_root=run_root, manifest=manifest)
    return run_id, manifest, snapshot_path, audio_path


def _curation(manifest: dict[str, Any], *, decision: str = "selected") -> dict[str, Any]:
    candidate = manifest["candidates"][0]
    group = {
        **{key: candidate[key] for key in GROUP_KEYS},
        "candidates": [
            {
                "take_id": candidate["take_id"],
                "path": candidate["path"],
                "audio_sha256": candidate["sha256"],
                "rubric": {
                    "content_correct": True,
                    "intent_match": 4,
                    "character_naturalness": 5,
                    "adoptable": True,
                },
            },
        ],
        "decision": (
            {"type": "selected", "take_id": candidate["take_id"]}
            if decision == "selected"
            else {"type": "skipped"}
        ),
    }
    return {
        "groups": [group],
        "candidate_set_sha256": manifest["candidate_set_sha256"],
        "rubric_version": "take-curation-v1",
        "format_version": 1,
    }


def _write_input(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "curation-input.json"
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _add_candidate_groups(
    *,
    manifest: dict[str, Any],
    snapshot_path: Path,
    line_ids: tuple[str, ...],
) -> None:
    run_root = snapshot_path.parent
    ledger_path = run_root / "ledger.json"
    ledger = read_ledger(ledger_path)
    template_candidate = manifest["candidates"][0]
    template_attempt = ledger["attempts"][0]
    model = template_candidate["model"]
    for line_id in line_ids:
        audio_bytes = f"local opus fixture {line_id}".encode()
        audio_sha = hashlib.sha256(audio_bytes).hexdigest()
        input_sha = hashlib.sha256(f"input {line_id}".encode()).hexdigest()
        candidate = deepcopy(template_candidate)
        candidate.update(
            line=line_id,
            sha256=audio_sha,
            generation_input_sha256=input_sha,
            take_id=make_take_id(
                generation_input_sha256=input_sha,
                final_opus_sha256=audio_sha,
            ),
            path=(
                f"audio/takes/{model}/tavern-night/{line_id}/dry/"
                f"take-0001-{audio_sha}.opus"
            ),
        )
        manifest["candidates"].append(candidate)
        group = {
            "model": model,
            "scenario": "tavern-night",
            "line": line_id,
            "variant": "dry",
        }
        ledger["source"]["groups"].append(group)
        attempt = deepcopy(template_attempt)
        attempt.update(
            **group,
            take_id=candidate["take_id"],
            generation_input_sha256=input_sha,
        )
        _audio_path, ledger_audio = _write_take_files(
            run_root=run_root,
            run_id=ledger["run_id"],
            candidate=candidate,
            opus_bytes=audio_bytes,
        )
        attempt["audio"] = ledger_audio
        ledger["attempts"].append(attempt)
    write_ledger_atomic(ledger_path, ledger)
    scenario_sha, lines = load_authoritative_candidate_lines(
        scenarios_dir=SCENARIOS_DIR,
        ledger_source=ledger["source"],
    )
    candidate_set = build_candidate_set(
        scenario_sha256=scenario_sha,
        lines=lines,
        models=manifest["models"],
        candidates=manifest["candidates"],
        failures=manifest["failures"],
    )
    candidate_bytes = canonical_candidate_set_bytes(candidate_set)
    manifest["candidate_set_sha256"] = hashlib.sha256(candidate_bytes).hexdigest()
    validate_manifest_v4(manifest)
    snapshot_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_root / "candidate-set.json").write_bytes(candidate_bytes)
    (run_root / "candidate-set.sha256").write_bytes(
        manifest["candidate_set_sha256"].encode("ascii"),
    )
    _write_qc_report(run_root=run_root, manifest=manifest)


def _curation_for_lines(
    manifest: dict[str, Any],
    line_ids: tuple[str, ...],
) -> dict[str, Any]:
    groups = []
    for line_id in line_ids:
        candidate = next(
            candidate
            for candidate in manifest["candidates"]
            if candidate["line"] == line_id
        )
        groups.append(
            {
                **{key: candidate[key] for key in GROUP_KEYS},
                "candidates": [
                    {
                        "take_id": candidate["take_id"],
                        "path": candidate["path"],
                        "audio_sha256": candidate["sha256"],
                        "rubric": {
                            "content_correct": True,
                            "intent_match": 4,
                            "character_naturalness": 5,
                            "adoptable": True,
                        },
                    },
                ],
                "decision": {
                    "type": "selected",
                    "take_id": candidate["take_id"],
                },
            },
        )
    return {
        "format_version": 1,
        "rubric_version": "take-curation-v1",
        "candidate_set_sha256": manifest["candidate_set_sha256"],
        "groups": groups,
    }


def _rewrite_snapshot_bundle(
    *,
    manifest: dict[str, Any],
    snapshot_path: Path,
) -> None:
    ledger = read_ledger(snapshot_path.parent / "ledger.json")
    scenario_sha, lines = load_authoritative_candidate_lines(
        scenarios_dir=SCENARIOS_DIR,
        ledger_source=ledger["source"],
    )
    candidate_set = build_candidate_set(
        scenario_sha256=scenario_sha,
        lines=lines,
        models=manifest["models"],
        candidates=manifest["candidates"],
        failures=manifest["failures"],
    )
    candidate_bytes = canonical_candidate_set_bytes(candidate_set)
    manifest["candidate_set_sha256"] = hashlib.sha256(candidate_bytes).hexdigest()
    validate_manifest_v4(manifest)
    snapshot_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (snapshot_path.parent / "candidate-set.json").write_bytes(candidate_bytes)
    (snapshot_path.parent / "candidate-set.sha256").write_bytes(
        manifest["candidate_set_sha256"].encode("ascii"),
    )


def test_applyはcanonical_artifactとselected_projectionを書いて再適用は冪等(
    tmp_path: Path,
) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    document = _curation(manifest)
    input_path = _write_input(tmp_path, document)

    first = apply_curation(
        run_id=run_id,
        input_path=input_path,
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )

    expected_bytes = canonical_curation_bytes(document)
    assert first.artifact_path.read_bytes() == expected_bytes
    assert first.curation_sha256 == hashlib.sha256(expected_bytes).hexdigest()
    assert first.added_projection_count == 1
    projection = json.loads(snapshot_path.read_text(encoding="utf-8"))["curations"][0]
    assert projection == {
        "model": "dummy",
        "scenario": "tavern-night",
        "line": "barmaid-001",
        "variant": "dry",
        "decision": "selected",
        "take_id": manifest["candidates"][0]["take_id"],
        "curation_sha256": first.curation_sha256,
    }
    snapshot_bytes = snapshot_path.read_bytes()

    second = apply_curation(
        run_id=run_id,
        input_path=input_path,
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    assert second.added_projection_count == 0
    assert snapshot_path.read_bytes() == snapshot_bytes


def test_skippedはtake_idなしでprojection化する(tmp_path: Path) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    summary = apply_curation(
        run_id=run_id,
        input_path=_write_input(tmp_path, _curation(manifest, decision="skipped")),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    projection = json.loads(snapshot_path.read_text(encoding="utf-8"))["curations"][0]
    assert projection["decision"] == "skipped"
    assert "take_id" not in projection
    assert projection["curation_sha256"] == summary.curation_sha256


def test_applyはhard_rejected_attemptをcandidateに含むsnapshotを拒否(
    tmp_path: Path,
) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    ledger_path = snapshot_path.parent / "ledger.json"
    ledger = read_ledger(ledger_path)
    ledger["attempts"][0]["status"] = "hard_rejected"
    ledger["attempts"][0]["gates"]["content"] = "reject"
    write_ledger_atomic(ledger_path, ledger)
    snapshot_before = snapshot_path.read_bytes()

    with pytest.raises(CurationError, match="ledger"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, _curation(manifest)),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == snapshot_before
    assert not (tmp_path / "data" / "curation").exists()


def test_applyはledgerにないorphan_candidate_takeを拒否(tmp_path: Path) -> None:
    run_id, manifest, snapshot_path, audio_path = _setup_run(tmp_path)
    first = manifest["candidates"][0]
    orphan_audio = b"orphan candidate audio"
    orphan_sha = hashlib.sha256(orphan_audio).hexdigest()
    orphan_input_sha = "d" * 64
    orphan = deepcopy(first)
    orphan.update(
        take_index=2,
        sha256=orphan_sha,
        generation_input_sha256=orphan_input_sha,
        take_id=make_take_id(
            generation_input_sha256=orphan_input_sha,
            final_opus_sha256=orphan_sha,
        ),
        path=(
            "audio/takes/dummy/tavern-night/barmaid-001/dry/"
            f"take-0002-{orphan_sha}.opus"
        ),
    )
    manifest["candidates"].append(orphan)
    _rewrite_snapshot_bundle(manifest=manifest, snapshot_path=snapshot_path)
    audio_path.with_name("take-0002.opus").write_bytes(orphan_audio)
    document = _curation(manifest)
    document["groups"][0]["candidates"].append(
        {
            "take_id": orphan["take_id"],
            "path": orphan["path"],
            "audio_sha256": orphan["sha256"],
            "rubric": {
                "content_correct": True,
                "intent_match": 4,
                "character_naturalness": 4,
                "adoptable": True,
            },
        },
    )
    snapshot_before = snapshot_path.read_bytes()

    with pytest.raises(CurationError, match="ledger"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, document),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == snapshot_before
    assert not (tmp_path / "data" / "curation").exists()


def test_applyはcandidateとeligible_ledger_provenanceの不一致を拒否(
    tmp_path: Path,
) -> None:
    run_id, manifest, snapshot_path, audio_path = _setup_run(tmp_path)
    candidate = manifest["candidates"][0]
    replacement_audio = b"self-consistent snapshot but not ledger"
    replacement_sha = hashlib.sha256(replacement_audio).hexdigest()
    replacement_input_sha = "d" * 64
    candidate.update(
        sha256=replacement_sha,
        generation_input_sha256=replacement_input_sha,
        take_id=make_take_id(
            generation_input_sha256=replacement_input_sha,
            final_opus_sha256=replacement_sha,
        ),
        path=(
            "audio/takes/dummy/tavern-night/barmaid-001/dry/"
            f"take-0001-{replacement_sha}.opus"
        ),
    )
    _rewrite_snapshot_bundle(manifest=manifest, snapshot_path=snapshot_path)
    audio_path.write_bytes(replacement_audio)
    snapshot_before = snapshot_path.read_bytes()

    with pytest.raises(CurationError, match="ledger"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, _curation(manifest)),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == snapshot_before
    assert not (tmp_path / "data" / "curation").exists()


def test_applyは全ledger_attemptがterminalであることを要求(tmp_path: Path) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    ledger_path = snapshot_path.parent / "ledger.json"
    ledger = read_ledger(ledger_path)
    ledger["attempts"][0]["status"] = "generated"
    ledger["attempts"][0]["gates"] = {}
    write_ledger_atomic(ledger_path, ledger)
    snapshot_before = snapshot_path.read_bytes()

    with pytest.raises(CurationError, match="terminal"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, _curation(manifest)),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == snapshot_before
    assert not (tmp_path / "data" / "curation").exists()


def test_applyはself_consistent_ledger_sidecarとcandidateの不一致を拒否(
    tmp_path: Path,
) -> None:
    run_id, manifest, snapshot_path, audio_path = _setup_run(tmp_path)
    sidecar_path = audio_path.with_suffix(".json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["duration_sec"] = 9.0
    validate_take_sidecar(sidecar)
    sidecar_path.write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    ledger_path = snapshot_path.parent / "ledger.json"
    ledger = read_ledger(ledger_path)
    ledger["attempts"][0]["audio"]["sidecar_sha256"] = hashlib.sha256(
        sidecar_path.read_bytes(),
    ).hexdigest()
    write_ledger_atomic(ledger_path, ledger)
    snapshot_before = snapshot_path.read_bytes()

    with pytest.raises(CurationError, match="duration"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, _curation(manifest)),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == snapshot_before
    assert not (tmp_path / "data" / "curation").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda sidecar: sidecar.update(generation_seconds=9.0),
        lambda sidecar: sidecar.update(postprocess={"tampered": True}),
        lambda sidecar: sidecar["toolchain"].update(
            ffmpeg_version="tampered ffmpeg",
        ),
        lambda sidecar: sidecar.update(loudness={"tampered": True}),
    ],
)
def test_applyはsidecar_provenance単独改変をQC_authorityで拒否(
    tmp_path: Path,
    mutation: Any,
) -> None:
    run_id, manifest, snapshot_path, audio_path = _setup_run(tmp_path)
    sidecar_path = audio_path.with_suffix(".json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    mutation(sidecar)
    validate_take_sidecar(sidecar)
    sidecar_path.write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    ledger_path = snapshot_path.parent / "ledger.json"
    ledger = read_ledger(ledger_path)
    ledger["attempts"][0]["audio"]["sidecar_sha256"] = hashlib.sha256(
        sidecar_path.read_bytes(),
    ).hexdigest()
    write_ledger_atomic(ledger_path, ledger)
    snapshot_before = snapshot_path.read_bytes()

    with pytest.raises(CurationError, match="QC report|sidecar provenance"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, _curation(manifest)),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == snapshot_before
    assert not (tmp_path / "data" / "curation").exists()


@pytest.mark.parametrize("field", ["loudness", "policy_version"])
def test_applyはcandidate_QC_authority単独改変を拒否(
    tmp_path: Path,
    field: str,
) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    candidate = manifest["candidates"][0]
    if field == "loudness":
        candidate["loudness"]["i_lufs"] = -9.0
    else:
        candidate["gate"]["policy_version"] = "tampered-policy"
    _rewrite_snapshot_bundle(manifest=manifest, snapshot_path=snapshot_path)
    snapshot_before = snapshot_path.read_bytes()

    with pytest.raises(CurationError, match="QC report"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, _curation(manifest)),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == snapshot_before
    assert not (tmp_path / "data" / "curation").exists()


@pytest.mark.parametrize("target", ["root", "attempt"])
def test_applyはQC_reportのunknown_keyを拒否(
    tmp_path: Path,
    target: str,
) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    report_path = snapshot_path.parent / "qc-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if target == "root":
        report["extra"] = True
    else:
        report["attempts"][0]["extra"] = True
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(CurationError, match="exact contract"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, _curation(manifest)),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert not (tmp_path / "data" / "curation").exists()


@pytest.mark.parametrize(
    ("target", "mutation"),
    [
        (
            "mechanical.wav",
            lambda report: report["attempts"][0]["mechanical"]["wav"].update(
                extra=True,
            ),
        ),
        (
            "mechanical.wav.codec",
            lambda report: report["attempts"][0]["mechanical"]["wav"].update(
                codec="mp3",
            ),
        ),
        (
            "content.status",
            lambda report: report["attempts"][0]["content"].update(
                status="reject",
            ),
        ),
        (
            "content",
            lambda report: report["attempts"][0]["content"].update(extra=True),
        ),
    ],
)
def test_applyはQC_reportのrecursive_contract違反を拒否(
    tmp_path: Path,
    target: str,
    mutation: Any,
) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    report_path = snapshot_path.parent / "qc-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    mutation(report)
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(CurationError, match="QC report"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, _curation(manifest)),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert target
    assert not (tmp_path / "data" / "curation").exists()


def test_incremental_applyは既存groupのartifact_shaを保持してABCと重放を許可(
    tmp_path: Path,
) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    _add_candidate_groups(
        manifest=manifest,
        snapshot_path=snapshot_path,
        line_ids=("barmaid-002", "drunkard-001"),
    )

    first = apply_curation(
        run_id=run_id,
        input_path=_write_input(
            tmp_path,
            _curation_for_lines(manifest, ("barmaid-001",)),
        ),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    second = apply_curation(
        run_id=run_id,
        input_path=_write_input(
            tmp_path,
            _curation_for_lines(manifest, ("barmaid-001", "barmaid-002")),
        ),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    third_input = _write_input(
        tmp_path,
        _curation_for_lines(
            manifest,
            ("barmaid-001", "barmaid-002", "drunkard-001"),
        ),
    )
    third = apply_curation(
        run_id=run_id,
        input_path=third_input,
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    projections = {
        projection["line"]: projection
        for projection in json.loads(
            snapshot_path.read_text(encoding="utf-8"),
        )["curations"]
    }
    assert projections["barmaid-001"]["curation_sha256"] == first.curation_sha256
    assert projections["barmaid-002"]["curation_sha256"] == second.curation_sha256
    assert projections["drunkard-001"]["curation_sha256"] == third.curation_sha256
    snapshot_bytes = snapshot_path.read_bytes()

    replay = apply_curation(
        run_id=run_id,
        input_path=third_input,
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    assert replay.added_projection_count == 0
    assert snapshot_path.read_bytes() == snapshot_bytes


def test_concurrent_applyはrun_lockでlost_projectionを防ぐ(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    _add_candidate_groups(
        manifest=manifest,
        snapshot_path=snapshot_path,
        line_ids=("barmaid-002",),
    )
    input_a = tmp_path / "curation-a.json"
    input_b = tmp_path / "curation-b.json"
    input_a.write_text(
        json.dumps(_curation_for_lines(manifest, ("barmaid-001",))),
        encoding="utf-8",
    )
    input_b.write_text(
        json.dumps(_curation_for_lines(manifest, ("barmaid-002",))),
        encoding="utf-8",
    )

    first_at_manifest_write = threading.Event()
    release_first_writer = threading.Event()
    second_started = threading.Event()
    second_entered_transaction = threading.Event()
    real_write = curation_module._write_bytes_atomic
    real_validate_bundle = curation_module.validate_snapshot_bundle

    def blocking_write(path: Path, payload: bytes) -> None:
        if (
            path == snapshot_path
            and threading.current_thread().name == "apply-a"
            and not first_at_manifest_write.is_set()
        ):
            first_at_manifest_write.set()
            assert release_first_writer.wait(timeout=5)
        real_write(path, payload)

    def recording_validate_bundle(**kwargs: Any) -> Any:
        if threading.current_thread().name == "apply-b":
            second_entered_transaction.set()
        return real_validate_bundle(**kwargs)

    monkeypatch.setattr(curation_module, "_write_bytes_atomic", blocking_write)
    monkeypatch.setattr(
        curation_module,
        "validate_snapshot_bundle",
        recording_validate_bundle,
    )
    results: list[Any] = []
    errors: list[BaseException] = []

    def worker(input_path: Path, *, is_second: bool) -> None:
        if is_second:
            second_started.set()
        try:
            results.append(
                apply_curation(
                    run_id=run_id,
                    input_path=input_path,
                    artifacts_dir=tmp_path / "artifacts",
                    data_dir=tmp_path / "data",
                    scenarios_dir=SCENARIOS_DIR,
                ),
            )
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(
        target=worker,
        kwargs={"input_path": input_a, "is_second": False},
        name="apply-a",
    )
    second = threading.Thread(
        target=worker,
        kwargs={"input_path": input_b, "is_second": True},
        name="apply-b",
    )
    first.start()
    assert first_at_manifest_write.wait(timeout=5)
    second.start()
    assert second_started.wait(timeout=5)
    entered_while_first_held_lock = second_entered_transaction.wait(timeout=1)
    release_first_writer.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert not entered_while_first_held_lock
    assert second_entered_transaction.is_set()
    assert errors == []
    assert len(results) == 2
    projections = json.loads(snapshot_path.read_text(encoding="utf-8"))["curations"]
    assert {projection["line"] for projection in projections} == {
        "barmaid-001",
        "barmaid-002",
    }


@pytest.mark.parametrize("mutation", ["decision", "rubric"])
def test_referenced_artifactの付随groupは権威projection内容と一致が必要(
    tmp_path: Path,
    mutation: str,
) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    _add_candidate_groups(
        manifest=manifest,
        snapshot_path=snapshot_path,
        line_ids=("barmaid-002",),
    )
    first_document = _curation_for_lines(manifest, ("barmaid-001",))
    apply_curation(
        run_id=run_id,
        input_path=_write_input(tmp_path, first_document),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    combined_document = _curation_for_lines(
        manifest,
        ("barmaid-001", "barmaid-002"),
    )
    combined = apply_curation(
        run_id=run_id,
        input_path=_write_input(tmp_path, combined_document),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    conflicting = json.loads(combined.artifact_path.read_text(encoding="utf-8"))
    attached_group = next(
        group for group in conflicting["groups"] if group["line"] == "barmaid-001"
    )
    if mutation == "decision":
        attached_group["decision"] = {"type": "skipped"}
    else:
        attached_group["candidates"][0]["rubric"]["intent_match"] = 1
    conflicting_bytes = canonical_curation_bytes(conflicting)
    conflicting_sha = hashlib.sha256(conflicting_bytes).hexdigest()
    conflicting_path = combined.artifact_path.with_name(f"{conflicting_sha}.json")
    conflicting_path.write_bytes(conflicting_bytes)
    updated_manifest = json.loads(snapshot_path.read_text(encoding="utf-8"))
    next(
        projection
        for projection in updated_manifest["curations"]
        if projection["line"] == "barmaid-002"
    )["curation_sha256"] = conflicting_sha
    validate_manifest_v4(updated_manifest)
    snapshot_path.write_text(
        json.dumps(updated_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    snapshot_before = snapshot_path.read_bytes()
    artifact_paths_before = sorted(
        path.name for path in combined.artifact_path.parent.glob("*.json")
    )

    with pytest.raises(CurationError, match="権威"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, combined_document),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == snapshot_before
    assert sorted(
        path.name for path in combined.artifact_path.parent.glob("*.json")
    ) == artifact_paths_before


def test_referenced_artifactのknown_but_unprojected_groupを拒否(
    tmp_path: Path,
) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    _add_candidate_groups(
        manifest=manifest,
        snapshot_path=snapshot_path,
        line_ids=("barmaid-002",),
    )
    first_document = _curation_for_lines(manifest, ("barmaid-001",))
    first = apply_curation(
        run_id=run_id,
        input_path=_write_input(tmp_path, first_document),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    combined_document = _curation_for_lines(
        manifest,
        ("barmaid-001", "barmaid-002"),
    )
    combined_bytes = canonical_curation_bytes(combined_document)
    combined_sha = hashlib.sha256(combined_bytes).hexdigest()
    combined_path = first.artifact_path.with_name(f"{combined_sha}.json")
    combined_path.write_bytes(combined_bytes)
    updated_manifest = json.loads(snapshot_path.read_text(encoding="utf-8"))
    updated_manifest["curations"][0]["curation_sha256"] = combined_sha
    validate_manifest_v4(updated_manifest)
    snapshot_path.write_text(
        json.dumps(updated_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    snapshot_before = snapshot_path.read_bytes()
    artifact_paths_before = sorted(
        path.name for path in first.artifact_path.parent.glob("*.json")
    )

    with pytest.raises(CurationError, match="projection"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, first_document),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == snapshot_before
    assert sorted(
        path.name for path in first.artifact_path.parent.glob("*.json")
    ) == artifact_paths_before


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda document: document.update(candidate_set_sha256="0" * 64),
            "stale",
        ),
        (
            lambda document: document["groups"][0]["decision"].update(
                take_id="f" * 64,
            ),
            "同一 group",
        ),
        (
            lambda document: document["groups"][0]["candidates"][0][
                "rubric"
            ].update(intent_match=True),
            "1..5",
        ),
        (
            lambda document: document["groups"][0]["candidates"][0].update(
                path="audio/other.opus",
            ),
            "path",
        ),
        (
            lambda document: document["groups"][0]["candidates"][0].update(
                audio_sha256="0" * 64,
            ),
            "audio SHA",
        ),
        (
            lambda document: document["groups"][0]["candidates"][0][
                "rubric"
            ].update(content_correct=False),
            "content_correct",
        ),
        (
            lambda document: document["groups"][0]["candidates"][0][
                "rubric"
            ].update(adoptable=False),
            "adoptable",
        ),
    ],
)
def test_applyはstale_orphan_range_path_selected不適格を全件拒否(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    document = _curation(manifest)
    mutation(document)
    old_snapshot = snapshot_path.read_bytes()

    with pytest.raises(CurationError, match=message):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, document),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == old_snapshot
    assert not (tmp_path / "data" / "curation").exists()


@pytest.mark.parametrize("coverage", ["missing", "extra"])
def test_applyはgroupのcandidate集合を完全被覆させる(
    tmp_path: Path,
    coverage: str,
) -> None:
    run_id, manifest, snapshot_path, _first_audio_path = _setup_run(tmp_path)
    first = manifest["candidates"][0]
    second_audio_bytes = b"second local opus fixture"
    second_audio_sha = hashlib.sha256(second_audio_bytes).hexdigest()
    second_input_sha = "d" * 64
    second = deepcopy(first)
    second.update(
        take_index=2,
        sha256=second_audio_sha,
        generation_input_sha256=second_input_sha,
        take_id=make_take_id(
            generation_input_sha256=second_input_sha,
            final_opus_sha256=second_audio_sha,
        ),
        path=(
            "audio/takes/dummy/tavern-night/barmaid-001/dry/"
            f"take-0002-{second_audio_sha}.opus"
        ),
    )
    manifest["candidates"].append(second)
    candidate_set_path = snapshot_path.parent / "candidate-set.json"
    candidate_set = json.loads(candidate_set_path.read_text(encoding="utf-8"))
    candidate_set["candidates"].append(second)
    candidate_bytes = canonical_candidate_set_bytes(candidate_set)
    manifest["candidate_set_sha256"] = hashlib.sha256(candidate_bytes).hexdigest()
    validate_manifest_v4(manifest)
    snapshot_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    candidate_set_path.write_bytes(candidate_bytes)
    (snapshot_path.parent / "candidate-set.sha256").write_bytes(
        manifest["candidate_set_sha256"].encode("ascii"),
    )
    ledger_path = snapshot_path.parent / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    second_attempt = deepcopy(ledger["attempts"][0])
    second_attempt.update(
        take_index=2,
        take_id=second["take_id"],
        generation_input_sha256=second_input_sha,
    )
    _second_audio_path, second_ledger_audio = _write_take_files(
        run_root=snapshot_path.parent,
        run_id=run_id,
        candidate=second,
        opus_bytes=second_audio_bytes,
    )
    second_attempt["audio"] = second_ledger_audio
    ledger["source"]["takes"] = 2
    ledger["attempts"].append(second_attempt)
    write_ledger_atomic(ledger_path, ledger)
    _write_qc_report(run_root=snapshot_path.parent, manifest=manifest)

    document = _curation(manifest)
    if coverage == "extra":
        document["groups"][0]["candidates"].extend(
            [
                {
                    "take_id": second["take_id"],
                    "path": second["path"],
                    "audio_sha256": second["sha256"],
                    "rubric": {
                        "content_correct": True,
                        "intent_match": 4,
                        "character_naturalness": 4,
                        "adoptable": True,
                    },
                },
                {
                    "take_id": "e" * 64,
                    "path": (
                        "audio/takes/dummy/tavern-night/barmaid-001/dry/"
                        "extra.opus"
                    ),
                    "audio_sha256": "f" * 64,
                    "rubric": {
                        "content_correct": True,
                        "intent_match": 3,
                        "character_naturalness": 3,
                        "adoptable": True,
                    },
                },
            ],
        )
    old_snapshot = snapshot_path.read_bytes()

    with pytest.raises(CurationError, match="完全に被覆"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, document),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == old_snapshot
    assert not (tmp_path / "data" / "curation").exists()


def test_candidate_set_sidecarとlocal_audioの不一致を拒否(tmp_path: Path) -> None:
    run_id, manifest, snapshot_path, audio_path = _setup_run(tmp_path)
    input_path = _write_input(tmp_path, _curation(manifest))
    sidecar_path = snapshot_path.parent / "candidate-set.json"
    sidecar_bytes = sidecar_path.read_bytes()
    sidecar_path.write_bytes(sidecar_path.read_bytes() + b"\n")
    with pytest.raises(CurationError, match="candidate-set.json"):
        apply_curation(
            run_id=run_id,
            input_path=input_path,
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    sidecar_path.write_bytes(sidecar_bytes)
    audio_path.write_bytes(b"tampered")
    with pytest.raises(CurationError, match="Opus SHA"):
        apply_curation(
            run_id=run_id,
            input_path=input_path,
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )


def test_source_manifestはtest_only_adapter_failureを拒否(tmp_path: Path) -> None:
    group = {
        "model": "dummy",
        "scenario": "tavern-night",
        "line": "barmaid-001",
        "variant": "dry",
    }
    with pytest.raises(CurationError, match="no_eligible_take"):
        curation_module._validate_manifest_against_terminal_ledger(
            manifest={
                "candidates": [],
                "failures": [{**group, "reason": "test_only_adapter"}],
            },
            ledger={"attempts": [], "source": {"groups": [group]}},
            run_root=tmp_path,
            qc_authority=SimpleNamespace(),
        )


def test_candidate_setはPython_float_lexicalをcanonical_sidecarに保持() -> None:
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    encoded = canonical_candidate_set_bytes(
        build_candidate_set(
            scenario_sha256="a" * 64,
            lines=[
                {
                    "scenario": "tavern-night",
                    "line": "barmaid-001",
                    "scenario_title": "酒場",
                    "text": "台詞",
                    "delivery": "演技",
                },
                {
                    "scenario": "tavern-night",
                    "line": "missing-line",
                    "scenario_title": "酒場",
                    "text": "欠落",
                    "delivery": "演技",
                },
            ],
            models=manifest["models"],
            candidates=manifest["candidates"],
            failures=manifest["failures"],
        ),
    )
    assert b'"temperature":1.0' in encoded
    assert not encoded.endswith(b"\n")
    assert set(json.loads(encoded)) == {
        "format_version",
        "scenario_sha256",
        "lines",
        "models",
        "candidates",
        "failures",
    }


def test_projection競合は新artifactを書かない(tmp_path: Path) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    existing = deepcopy(_curation(manifest))
    existing_summary = apply_curation(
        run_id=run_id,
        input_path=_write_input(tmp_path, existing),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    conflicting = _curation(manifest)
    conflicting["groups"][0]["candidates"][0]["rubric"]["intent_match"] = 1
    input_path = _write_input(tmp_path, conflicting)

    with pytest.raises(CurationError, match="競合"):
        apply_curation(
            run_id=run_id,
            input_path=input_path,
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    artifacts = list((tmp_path / "data" / "curation").glob("*.json"))
    assert artifacts == [existing_summary.artifact_path]
    validate_manifest_v4(json.loads(snapshot_path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda group: group["candidates"][0].update(path="audio/other.opus"),
            "path",
        ),
        (
            lambda group: group["candidates"][0].update(audio_sha256="0" * 64),
            "audio SHA",
        ),
        (
            lambda group: group["candidates"][0]["rubric"].update(
                content_correct=False,
            ),
            "content_correct",
        ),
        (
            lambda group: group.update(line="unknown-line"),
            "未知の group",
        ),
    ],
)
def test_旧artifactは全groupのcandidate内容を再検証して書込前に拒否(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    original_document = _curation(manifest)
    applied = apply_curation(
        run_id=run_id,
        input_path=_write_input(tmp_path, original_document),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    tampered = json.loads(applied.artifact_path.read_text(encoding="utf-8"))
    mutation(tampered["groups"][0])
    tampered_bytes = canonical_curation_bytes(tampered)
    tampered_sha = hashlib.sha256(tampered_bytes).hexdigest()
    tampered_path = applied.artifact_path.with_name(f"{tampered_sha}.json")
    tampered_path.write_bytes(tampered_bytes)
    updated_manifest = json.loads(snapshot_path.read_text(encoding="utf-8"))
    updated_manifest["curations"][0]["curation_sha256"] = tampered_sha
    validate_manifest_v4(updated_manifest)
    snapshot_path.write_text(
        json.dumps(updated_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    snapshot_before = snapshot_path.read_bytes()
    artifact_paths_before = sorted(
        path.name for path in applied.artifact_path.parent.glob("*.json")
    )

    with pytest.raises(CurationError, match=message):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, original_document),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == snapshot_before
    assert sorted(
        path.name for path in applied.artifact_path.parent.glob("*.json")
    ) == artifact_paths_before


def test_snapshot_replace失敗は旧bytesを保持しpendingを清掃(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(tmp_path)
    old_snapshot = snapshot_path.read_bytes()
    original_replace = Path.replace

    def fail_snapshot_replace(source: Path, target: Path) -> Path:
        if target == snapshot_path:
            raise OSError("replace failed")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_snapshot_replace)
    with pytest.raises(CurationError, match="replace failed"):
        apply_curation(
            run_id=run_id,
            input_path=_write_input(tmp_path, _curation(manifest)),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )

    assert snapshot_path.read_bytes() == old_snapshot
    assert not list(snapshot_path.parent.glob(f".{snapshot_path.name}.*.pending"))
    assert len(list((tmp_path / "data" / "curation").glob("*.json"))) == 1


def test_curate_cliは両引数必須でrun_traversalを拒否(tmp_path: Path) -> None:
    parser = cli.build_parser()
    parsed = parser.parse_args(
        ["curate", "apply", "--run-id", "run-1", "--input", "decision.json"],
    )
    assert parsed.run_id == "run-1"
    assert parsed.input == Path("decision.json")
    for argv in (
        ["curate", "apply", "--run-id", "run-1"],
        ["curate", "apply", "--input", "decision.json"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)

    with pytest.raises(CurationError, match="path segment"):
        apply_curation(
            run_id="../outside",
            input_path=tmp_path / "missing.json",
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )
    assert (
        cli.main(
            [
                "curate",
                "apply",
                "--run-id",
                "../outside",
                "--input",
                str(tmp_path / "missing.json"),
            ],
        )
        == 1
    )
