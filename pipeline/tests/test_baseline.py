from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaya_pipeline import baseline
from gaya_pipeline.adapters import UnknownAdapterError, get_model_profile
from gaya_pipeline.audio import PostprocessProfile
from gaya_pipeline.baseline import (
    BaselineError,
    generation_selection,
    plan_baseline,
    validate_baseline_plan,
    validate_baseline_reference,
)
from gaya_pipeline.curation import (
    build_candidate_set,
    canonical_candidate_set_bytes,
    load_authoritative_candidate_lines,
)
from gaya_pipeline.take_identity import make_take_id
from gaya_pipeline.take_ledger import write_ledger_atomic
from gaya_pipeline.take_manifest_v4 import candidate_from_attempt, validate_manifest_v4
from gaya_pipeline.take_sidecar import validate_take_sidecar


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPOSITORY_ROOT / "data" / "manifest.json"
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"


def _real_plan(tmp_path: Path, name: str = "baseline-plan.json") -> Path:
    output = tmp_path / name
    plan_baseline(manifest_path=MANIFEST_PATH, output_path=output)
    return output


def test_planは381_groupと7_modelをcanonicalかつdeterministicに固定(
    tmp_path: Path,
) -> None:
    first = _real_plan(tmp_path, "first.json")
    second = _real_plan(tmp_path, "second.json")

    assert first.read_bytes() == second.read_bytes()
    assert not first.read_bytes().endswith(b"\n")
    plan = json.loads(first.read_bytes())
    assert plan["format_version"] == 1
    assert plan["plan_version"] == "baseline-plan-v1"
    assert len(plan["groups"]) == 381
    assert len(plan["models"]) == 7
    assert len(plan["excluded_failures"]) == 1
    assert sum(group["model"] == "dummy" for group in plan["groups"]) == 161
    assert plan["source"]["manifest_sha256"] == hashlib.sha256(
        MANIFEST_PATH.read_bytes(),
    ).hexdigest()
    assert len(
        [
            group
            for group in plan["groups"]
            if group["model"] == "qwen3-tts-12hz-1.7b"
        ],
    ) == 160
    assert all(
        failure not in plan["groups"] for failure in plan["excluded_failures"]
    )


def test_planは既存outputとtamper_duplicateを拒否(tmp_path: Path) -> None:
    plan_path = _real_plan(tmp_path)
    original = json.loads(plan_path.read_bytes())

    with pytest.raises(BaselineError, match="既存"):
        plan_baseline(manifest_path=MANIFEST_PATH, output_path=plan_path)

    tampered = deepcopy(original)
    tampered["format_version"] = 3
    with pytest.raises(BaselineError, match="format_version"):
        validate_baseline_plan(tampered)

    duplicated = deepcopy(original)
    duplicated["groups"][-1] = deepcopy(duplicated["groups"][0])
    duplicated["groups"].sort(
        key=lambda group: tuple(group[key] for key in baseline.GROUP_KEYS),
    )
    with pytest.raises(BaselineError, match="重複"):
        validate_baseline_plan(duplicated)


@pytest.mark.parametrize(
    ("model_id", "profile_error"),
    [
        ("unknown-model", None),
        ("dummy", AttributeError),
        ("dummy", ImportError),
    ],
)
def test_planはunknownまたはcurrent_profile欠落modelを拒否(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
    profile_error: type[Exception] | None,
) -> None:
    monkeypatch.setattr(baseline, "EXPECTED_GROUP_COUNT", 1)
    monkeypatch.setattr(baseline, "EXPECTED_MODEL_COUNT", 1)
    repository_root = tmp_path / "repository"
    (repository_root / "data").mkdir(parents=True)
    (repository_root / "scenarios").mkdir()
    manifest_path = repository_root / "data" / "manifest.json"
    manifest_path.write_bytes(b"legacy manifest bytes")
    monkeypatch.setattr(
        baseline,
        "load_manifest",
        lambda _path: {
            "models": [
                {
                    "id": model_id,
                    "name": "Legacy",
                    "version": "legacy",
                    "license_note": "legacy",
                    "capabilities": {
                        "emotion": False,
                        "voice_prompt": False,
                        "clone": False,
                        "nonverbal": False,
                        "reading": False,
                    },
                },
            ],
            "clips": [
                {
                    "model": model_id,
                    "scenario": "s",
                    "line": "l",
                    "variant": "dry",
                    "path": f"audio/{model_id}/s/l-dry.opus",
                    "sha256": "a" * 64,
                },
            ],
            "failures": [],
        },
    )
    if profile_error is not None:
        def unavailable_profile(_model_id: str) -> Any:
            raise profile_error("profile unavailable")

        monkeypatch.setattr(
            baseline,
            "get_model_profile",
            unavailable_profile,
        )

    with pytest.raises(BaselineError, match=model_id) as caught:
        plan_baseline(
            manifest_path=manifest_path,
            output_path=repository_root / "plan.json",
        )
    expected_cause = UnknownAdapterError if profile_error is None else profile_error
    assert isinstance(caught.value.__cause__, expected_cause)


def test_selectionはcurrent_v3_metadataとsourceを固定してQwen160(
    tmp_path: Path,
) -> None:
    plan_path = _real_plan(tmp_path)
    plan = json.loads(plan_path.read_bytes())
    model = next(
        model
        for model in plan["models"]
        if model["id"] == "qwen3-tts-12hz-1.7b"
    )

    selection = generation_selection(
        plan_path=plan_path,
        model_id=model["id"],
        scenarios_dir=SCENARIOS_DIR,
    )

    assert len(selection) == 160
    with pytest.raises(BaselineError, match="model がありません"):
        generation_selection(
            plan_path=plan_path,
            model_id="unknown-model",
            scenarios_dir=SCENARIOS_DIR,
        )


def test_real_planのaggregate投影はDummy161件をcandidate0へ固定(
    tmp_path: Path,
) -> None:
    plan = json.loads(_real_plan(tmp_path).read_bytes())
    source_candidates = [
        {
            **{key: group[key] for key in baseline.GROUP_KEYS},
            "take_index": 1,
        }
        for group in plan["groups"]
    ]
    original_candidates = deepcopy(source_candidates)

    candidates, failures = baseline._project_baseline_aggregate(
        plan=plan,
        candidates=source_candidates,
        failures=[],
    )

    assert source_candidates == original_candidates
    assert len(candidates) == 220
    assert len(failures) == 161
    assert all(candidate["model"] != "dummy" for candidate in candidates)
    assert {
        (failure["model"], failure["reason"])
        for failure in failures
    } == {("dummy", "test_only_adapter")}

    missing_dummy = next(
        candidate for candidate in source_candidates if candidate["model"] == "dummy"
    )
    incomplete_candidates = [
        candidate for candidate in source_candidates if candidate is not missing_dummy
    ]
    with pytest.raises(BaselineError, match="eligible source candidate"):
        baseline._project_baseline_aggregate(
            plan=plan,
            candidates=incomplete_candidates,
            failures=[
                {
                    **{key: missing_dummy[key] for key in baseline.GROUP_KEYS},
                    "reason": "no_eligible_take",
                },
            ],
        )


def _small_plan(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(baseline, "EXPECTED_GROUP_COUNT", 2)
    monkeypatch.setattr(baseline, "EXPECTED_MODEL_COUNT", 1)
    model = get_model_profile("dummy").as_manifest_entry()
    groups = [
        {
            "model": "dummy",
            "scenario": "s",
            "line": line,
            "variant": "dry",
            "legacy": {
                "path": f"audio/dummy/s/{line}-dry.opus",
                "sha256": character * 64,
            },
        }
        for line, character in (("candidate", "a"), ("zero", "b"))
    ]
    return validate_baseline_plan(
        {
            "format_version": 1,
            "plan_version": "baseline-plan-v1",
            "source": {
                "manifest_path": "data/manifest.json",
                "manifest_sha256": "c" * 64,
                "scenario_sha256": "d" * 64,
            },
            "models": [model],
            "groups": groups,
            "excluded_failures": [],
        },
    )


def _candidate() -> dict[str, Any]:
    audio_sha = "2" * 64
    input_sha = "3" * 64
    return {
        "model": "dummy",
        "scenario": "s",
        "line": "candidate",
        "variant": "dry",
        "take_index": 1,
        "take_id": make_take_id(
            generation_input_sha256=input_sha,
            final_opus_sha256=audio_sha,
        ),
        "path": "audio/takes/dummy/s/candidate/dry/take-0001-" + "2" * 64 + ".opus",
        "duration_sec": 1.0,
        "sha256": audio_sha,
        "generation_input_sha256": input_sha,
        "gen_params": {
            "seed": 104,
            "recipe_version": "test-v1",
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
            "content": "pass",
            "policy_version": "take-gates-v2",
        },
    }


def _single_group_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: str = "eligible",
    project_dummy: bool = False,
) -> dict[str, Any]:
    monkeypatch.setattr(baseline, "EXPECTED_GROUP_COUNT", 1)
    monkeypatch.setattr(baseline, "EXPECTED_MODEL_COUNT", 1)
    if not project_dummy:
        monkeypatch.setattr(baseline, "DUMMY_MODEL_ID", "test-only-dummy")
    repository_root = tmp_path / "repository"
    scenarios_dir = repository_root / "scenarios"
    (scenarios_dir / "schema").mkdir(parents=True)
    shutil.copyfile(
        SCENARIOS_DIR / "tavern-night.yaml",
        scenarios_dir / "tavern-night.yaml",
    )
    shutil.copyfile(
        SCENARIOS_DIR / "schema" / "scenario.schema.json",
        scenarios_dir / "schema" / "scenario.schema.json",
    )
    shutil.copytree(
        REPOSITORY_ROOT / "assets" / "voices",
        repository_root / "assets" / "voices",
    )

    model = get_model_profile("dummy").as_manifest_entry()
    legacy_model = deepcopy(model)
    legacy_model["license_note"] = "legacy license note"
    legacy_bytes = b"legacy opus"
    legacy_path = "audio/dummy/tavern-night/barmaid-001-dry.opus"
    legacy_root = repository_root / "site" / "public"
    (legacy_root / legacy_path).parent.mkdir(parents=True)
    (legacy_root / legacy_path).write_bytes(legacy_bytes)
    manifest = {
        "format_version": 3,
        "generated_at": "2026-07-29T00:00:00Z",
        "models": [legacy_model],
        "clips": [
            {
                "model": "dummy",
                "scenario": "tavern-night",
                "line": "barmaid-001",
                "variant": "dry",
                "path": legacy_path,
                "duration_sec": 1.0,
                "sha256": hashlib.sha256(legacy_bytes).hexdigest(),
                "gen_params": {},
                "rtf": 0.5,
                "loudness": {
                    "source": "encoded_opus",
                    "i_lufs": -18.0,
                    "tp_dbtp": -1.0,
                    "shortfall": False,
                },
            },
        ],
        "failures": [],
    }
    manifest_path = repository_root / "data" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    plan_path = repository_root / "baseline-plan.json"
    plan_baseline(manifest_path=manifest_path, output_path=plan_path)
    plan = json.loads(plan_path.read_bytes())

    run_id = f"run-{status}"
    artifacts_dir = repository_root / "artifacts"
    run_root = artifacts_dir / "takes" / run_id
    audio_root = (
        run_root / "audio/dummy/tavern-night/barmaid-001/dry"
    )
    audio_root.mkdir(parents=True)
    wav_bytes = b"wav evidence"
    opus_bytes = b"candidate opus"
    wav_path = audio_root / "take-0001.wav"
    opus_path = audio_root / "take-0001.opus"
    sidecar_path = audio_root / "take-0001.json"
    wav_path.write_bytes(wav_bytes)
    opus_path.write_bytes(opus_bytes)
    wav_sha = hashlib.sha256(wav_bytes).hexdigest()
    opus_sha = hashlib.sha256(opus_bytes).hexdigest()
    input_sha = "a" * 64
    take_id = make_take_id(
        generation_input_sha256=input_sha,
        final_opus_sha256=opus_sha,
    )
    sidecar = {
        "format_version": 1,
        "run_id": run_id,
        "model": "dummy",
        "scenario": "tavern-night",
        "line": "barmaid-001",
        "variant": "dry",
        "take_index": 1,
        "take_id": take_id,
        "generation_input_sha256": input_sha,
        "wav_sha256": wav_sha,
        "opus_sha256": opus_sha,
        "duration_sec": 1.0,
        "generation_seconds": 0.5,
        "rtf": 0.5,
        "take": {
            "seed": 104,
            "recipe_version": "recipe-v1",
            "sampling": {},
        },
        "gen_params": {"requested": {}, "realized": {}},
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
    gates = (
        {"mechanical": "pass", "content": "review_required"}
        if status == "eligible"
        else {"mechanical": "reject", "content": "not_run"}
    )
    attempt = {
        "model": "dummy",
        "scenario": "tavern-night",
        "line": "barmaid-001",
        "variant": "dry",
        "take_index": 1,
        "take_id": take_id,
        "generation_input_sha256": input_sha,
        "generation": {
            "status": "succeeded",
            "seed": 104,
            "sampling": {},
            "rtf": 0.5,
        },
        "audio": {
            "wav_path": "audio/dummy/tavern-night/barmaid-001/dry/take-0001.wav",
            "wav_sha256": wav_sha,
            "opus_path": "audio/dummy/tavern-night/barmaid-001/dry/take-0001.opus",
            "opus_sha256": opus_sha,
            "sidecar_sha256": hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
        },
        "gates": gates,
        "features": {"status": "unscored"},
        "status": status,
    }
    ledger = {
        "format_version": 1,
        "run_id": run_id,
        "created_at": "2026-07-29T00:00:00Z",
        "source": {
            "scenario_sha256": plan["source"]["scenario_sha256"],
            "model": "dummy",
            "takes": 1,
            "seed_base": 104,
            "recipe_version": "recipe-v1",
            "groups": [
                {
                    "model": "dummy",
                    "scenario": "tavern-night",
                    "line": "barmaid-001",
                    "variant": "dry",
                },
            ],
        },
        "attempts": [attempt],
    }
    write_ledger_atomic(run_root / "ledger.json", ledger)
    candidates = (
        [
            candidate_from_attempt(
                attempt,
                duration_sec=1.0,
                loudness={
                    "source": "encoded_opus",
                    "i_lufs": -18.0,
                    "tp_dbtp": -1.0,
                    "shortfall": False,
                },
                gate_policy_version="take-gates-v2",
                recipe_version="recipe-v1",
                requested_params={},
                realized_params={},
            ),
        ]
        if status == "eligible"
        else []
    )
    failures = (
        []
        if status == "eligible"
        else [
            {
                "model": "dummy",
                "scenario": "tavern-night",
                "line": "barmaid-001",
                "variant": "dry",
                "reason": "no_eligible_take",
            },
        ]
    )
    scenario_sha, lines = load_authoritative_candidate_lines(
        scenarios_dir=scenarios_dir,
        ledger_source=ledger["source"],
    )
    candidate_set = build_candidate_set(
        scenario_sha256=scenario_sha,
        lines=lines,
        models=[model],
        candidates=candidates,
        failures=failures,
    )
    candidate_bytes = canonical_candidate_set_bytes(candidate_set)
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    snapshot = validate_manifest_v4(
        {
            "format_version": 4,
            "generated_at": "2026-07-29T00:00:00Z",
            "candidate_set_sha256": candidate_sha,
            "models": [model],
            "candidates": candidates,
            "curations": [],
            "failures": failures,
        },
    )
    (run_root / "manifest-v4.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_root / "candidate-set.json").write_bytes(candidate_bytes)
    (run_root / "candidate-set.sha256").write_bytes(candidate_sha.encode("ascii"))

    mechanical = {
        "status": "pass" if status == "eligible" else "reject",
        **({"reason": "duration_out_of_range"} if status != "eligible" else {}),
        "duration_sec": 1.0,
        "wav": {"codec": "pcm_s16le", "sample_rate_hz": 48_000, "channels": 1},
        "opus": {"codec": "opus", "sample_rate_hz": 48_000, "channels": 1},
        "loudness": {
            "source": "encoded_opus",
            "i_lufs": -18.0,
            "tp_dbtp": -1.0,
            "shortfall": False,
        },
        "generation_params": {"requested": {}, "realized": {}},
        "sidecar_provenance": {
            "generation_seconds": 0.5,
            "postprocess": sidecar["postprocess"],
            "toolchain": sidecar["toolchain"],
            "loudness": {},
        },
    }
    content = (
        {
            "status": "review_required",
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
        if status == "eligible"
        else {"status": "not_run"}
    )
    counts = {
        "eligible": int(status == "eligible"),
        "hard_rejected": int(status == "hard_rejected"),
        "blocked": 0,
        "generation_failed": 0,
        "planned": 0,
        "generated": 0,
    }
    qc_report = {
        "format_version": 2,
        "generated_at": snapshot["generated_at"],
        "gate_policy_version": "take-gates-v2",
        "run_id": run_id,
        "source": {
            "ledger": (run_root / "ledger.json").as_posix(),
            "scenario_sha256": ledger["source"]["scenario_sha256"],
            "model": "dummy",
            "recipe_version": "recipe-v1",
        },
        "runtime": {"status": "fixture"},
        "summary": {
            "attempt_count": 1,
            **counts,
            "pending": 0,
            "content_review_required": int(status == "eligible"),
        },
        "attempts": [
            {
                **{key: attempt[key] for key in (*baseline.GROUP_KEYS, "take_index")},
                "take_id": take_id,
                "status": status,
                "gates": gates,
                "mechanical": mechanical,
                "content": content,
            },
        ],
    }
    (run_root / "qc-report.json").write_text(
        json.dumps(qc_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "repository_root": repository_root,
        "scenarios_dir": scenarios_dir,
        "legacy_root": legacy_root,
        "artifacts_dir": artifacts_dir,
        "plan_path": plan_path,
        "plan": plan,
        "run_id": run_id,
        "run_root": run_root,
        "snapshot": snapshot,
        "candidate_set": candidate_set,
    }


def test_reference_exact_contractはcandidate0と比較を固定(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _small_plan(monkeypatch)
    candidate = _candidate()
    document = {
        "format_version": 1,
        "source_manifest_sha256": "c" * 64,
        "candidate_set_sha256": "4" * 64,
        "references": [
            {
                **{
                    key: group[key]
                    for key in baseline.GROUP_KEYS
                },
                "public_path": group["legacy"]["path"],
                "legacy_sha256": group["legacy"]["sha256"],
                "local_path": (
                    f"reference/dummy/s/{group['line']}/dry.opus"
                ),
                "candidate_sha256": (
                    candidate["sha256"]
                    if group["line"] == "candidate"
                    else None
                ),
                "comparison": (
                    "different"
                    if group["line"] == "candidate"
                    else "no_candidate"
                ),
            }
            for group in plan["groups"]
        ],
    }
    assert len(
        validate_baseline_reference(
            document,
            expected_plan=plan,
            candidates=[candidate],
        )["references"],
    ) == 2

    tampered = deepcopy(document)
    tampered["references"][1]["candidate_sha256"] = "5" * 64
    with pytest.raises(BaselineError, match="plan/candidate"):
        validate_baseline_reference(
            tampered,
            expected_plan=plan,
            candidates=[candidate],
        )

    plan_out = deepcopy(candidate)
    plan_out["variant"] = "wet"
    with pytest.raises(BaselineError, match="plan 外"):
        validate_baseline_reference(
            document,
            expected_plan=plan,
            candidates=[candidate, plan_out],
        )


def test_real_assembleはDummy_source_evidenceを保持してaggregateから除外(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _single_group_fixture(tmp_path, monkeypatch, project_dummy=True)
    legacy_manifest = json.loads(
        (
            fixture["repository_root"] / "data" / "manifest.json"
        ).read_text(encoding="utf-8"),
    )
    assert legacy_manifest["models"][0]["license_note"] == "legacy license note"
    assert fixture["plan"]["models"] == [
        get_model_profile("dummy").as_manifest_entry(),
    ]
    orphan = fixture["run_root"] / "audio/orphan.opus"
    orphan.write_bytes(b"not in ledger inventory")
    bundle_dir = fixture["repository_root"] / "bundle"

    assembled = baseline.assemble_baseline(
        plan_path=fixture["plan_path"],
        run_ids=[fixture["run_id"]],
        output_dir=bundle_dir,
        artifacts_dir=fixture["artifacts_dir"],
        legacy_root=fixture["legacy_root"],
        scenarios_dir=fixture["scenarios_dir"],
    )

    inventory_bytes = (bundle_dir / baseline.BUNDLE_INVENTORY_PATH).read_bytes()
    inventory = json.loads(inventory_bytes)
    assert inventory_bytes == baseline.canonical_json(inventory).encode("utf-8")
    assert (bundle_dir / baseline.BUNDLE_INVENTORY_MARKER_PATH).read_bytes() == (
        hashlib.sha256(inventory_bytes).hexdigest().encode("ascii") + b"\n"
    )
    inventory_paths = [item["path"] for item in inventory["files"]]
    assert inventory_paths == sorted(inventory_paths)
    assert baseline.BUNDLE_INVENTORY_PATH not in inventory_paths
    assert baseline.BUNDLE_INVENTORY_MARKER_PATH not in inventory_paths
    assert not (
        bundle_dir / "source-runs" / "dummy" / "audio" / "orphan.opus"
    ).exists()
    candidate = fixture["snapshot"]["candidates"][0]
    aggregate_manifest = json.loads(
        (bundle_dir / "manifest-v4.json").read_text(encoding="utf-8"),
    )
    assert assembled.candidate_count == 0
    assert assembled.failure_count == 1
    assert aggregate_manifest["candidates"] == []
    assert aggregate_manifest["failures"] == [
        {
            "model": "dummy",
            "scenario": "tavern-night",
            "line": "barmaid-001",
            "variant": "dry",
            "reason": "test_only_adapter",
        },
    ]
    assert not (bundle_dir / candidate["path"]).exists()
    copied_dummy_opus = (
        bundle_dir
        / "source-runs"
        / "dummy"
        / "audio"
        / "dummy"
        / "tavern-night"
        / "barmaid-001"
        / "dry"
        / "take-0001.opus"
    )
    assert copied_dummy_opus.read_bytes() == b"candidate opus"
    copied_report = json.loads(
        (
            bundle_dir / "source-runs" / "dummy" / "qc-report.json"
        ).read_text(encoding="utf-8"),
    )
    assert copied_report["source"]["ledger"] == (
        fixture["run_root"] / "ledger.json"
    ).as_posix()


@pytest.mark.parametrize("mutation", ["missing", "extra", "tamper"])
def test_finalize入口はbundle_inventoryのexact_bytesを要求(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    fixture = _single_group_fixture(tmp_path, monkeypatch)
    bundle_dir = fixture["repository_root"] / "bundle"
    baseline.assemble_baseline(
        plan_path=fixture["plan_path"],
        run_ids=[fixture["run_id"]],
        output_dir=bundle_dir,
        artifacts_dir=fixture["artifacts_dir"],
        legacy_root=fixture["legacy_root"],
        scenarios_dir=fixture["scenarios_dir"],
    )
    if mutation == "missing":
        (bundle_dir / "baseline-reference.json").unlink()
    elif mutation == "extra":
        (bundle_dir / "unexpected.bin").write_bytes(b"extra")
    else:
        candidate = fixture["snapshot"]["candidates"][0]
        (bundle_dir / candidate["path"]).write_bytes(b"tampered")
    output = fixture["repository_root"] / f"release-{mutation}"

    with pytest.raises(BaselineError, match="inventory"):
        baseline.finalize_baseline(
            bundle_dir=bundle_dir,
            input_path=fixture["repository_root"] / "unused-curation.json",
            output_dir=output,
            scenarios_dir=fixture["scenarios_dir"],
        )
    assert not output.exists()


@pytest.mark.parametrize(
    ("artifact", "mutation"),
    [
        ("take-0001.json", "delete"),
        ("take-0001.opus", "tamper"),
    ],
)
def test_hard_rejected_candidate0もterminal_audio_evidenceを必須化(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact: str,
    mutation: str,
) -> None:
    fixture = _single_group_fixture(
        tmp_path,
        monkeypatch,
        status="hard_rejected",
    )
    path = (
        fixture["run_root"]
        / "audio/dummy/tavern-night/barmaid-001/dry"
        / artifact
    )
    if mutation == "delete":
        path.unlink()
    else:
        path.write_bytes(b"tampered")

    with pytest.raises(BaselineError, match="terminal|SHA-256 source"):
        baseline._validate_source_run(
            run_id=fixture["run_id"],
            run_root=fixture["run_root"],
            scenarios_dir=fixture["scenarios_dir"],
            qc_ledger_path=fixture["run_root"] / "ledger.json",
        )


def test_generation_failedは偽audio_evidenceを拒否(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    fake = run_root / "audio/dummy/s/l/dry/take-0001.opus"
    fake.parent.mkdir(parents=True)
    fake.write_bytes(b"fake")
    ledger = {
        "run_id": "run-1",
        "attempts": [
            {
                "model": "dummy",
                "scenario": "s",
                "line": "l",
                "variant": "dry",
                "take_index": 1,
                "status": "generation_failed",
            },
        ],
    }

    with pytest.raises(BaselineError, match="generation_failed"):
        baseline._validate_all_terminal_audio_evidence(
            ledger=ledger,
            run_root=run_root,
        )


def test_finalizeはverified_copy_raceを拒否してoutputを残さない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _single_group_fixture(tmp_path, monkeypatch)
    bundle_dir = fixture["repository_root"] / "bundle"
    assembled = baseline.assemble_baseline(
        plan_path=fixture["plan_path"],
        run_ids=[fixture["run_id"]],
        output_dir=bundle_dir,
        artifacts_dir=fixture["artifacts_dir"],
        legacy_root=fixture["legacy_root"],
        scenarios_dir=fixture["scenarios_dir"],
    )
    candidate = fixture["snapshot"]["candidates"][0]
    curation = _baseline_curation(candidate)
    curation["candidate_set_sha256"] = assembled.candidate_set_sha256
    curation["baseline_reference_sha256"] = assembled.baseline_reference_sha256
    curation_path = fixture["repository_root"] / "curation.json"
    curation_path.write_text(json.dumps(curation), encoding="utf-8")
    original_copy = baseline._copy_exact

    def racing_copy(source: Path, target: Path) -> None:
        original_copy(source, target)
        if "audio/takes" in target.as_posix() and target.suffix == ".opus":
            target.write_bytes(b"raced")

    monkeypatch.setattr(baseline, "_copy_exact", racing_copy)
    output = fixture["repository_root"] / "release-raced"
    with pytest.raises(BaselineError, match="copied candidate Opus"):
        baseline.finalize_baseline(
            bundle_dir=bundle_dir,
            input_path=curation_path,
            output_dir=output,
            scenarios_dir=fixture["scenarios_dir"],
        )
    assert not output.exists()


def test_finalizeは相対bundle_pathでもsource_runを検証してreleaseを生成(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _single_group_fixture(tmp_path, monkeypatch)
    bundle_dir = fixture["repository_root"] / "bundle"
    assembled = baseline.assemble_baseline(
        plan_path=fixture["plan_path"],
        run_ids=[fixture["run_id"]],
        output_dir=bundle_dir,
        artifacts_dir=fixture["artifacts_dir"],
        legacy_root=fixture["legacy_root"],
        scenarios_dir=fixture["scenarios_dir"],
    )
    candidate = fixture["snapshot"]["candidates"][0]
    curation = _baseline_curation(candidate)
    curation["candidate_set_sha256"] = assembled.candidate_set_sha256
    curation["baseline_reference_sha256"] = assembled.baseline_reference_sha256
    curation_path = fixture["repository_root"] / "curation.json"
    curation_path.write_text(json.dumps(curation), encoding="utf-8")
    monkeypatch.chdir(fixture["repository_root"])

    summary = baseline.finalize_baseline(
        bundle_dir=Path("bundle"),
        input_path=Path("curation.json"),
        output_dir=Path("release-relative"),
        scenarios_dir=Path("scenarios"),
    )

    assert summary.selected_count == 1
    assert summary.skipped_count == 0
    assert (fixture["repository_root"] / "release-relative").is_dir()


def test_finalizeはinventory直前のcuration改変を拒否してoutputを残さない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _single_group_fixture(tmp_path, monkeypatch)
    bundle_dir = fixture["repository_root"] / "bundle"
    assembled = baseline.assemble_baseline(
        plan_path=fixture["plan_path"],
        run_ids=[fixture["run_id"]],
        output_dir=bundle_dir,
        artifacts_dir=fixture["artifacts_dir"],
        legacy_root=fixture["legacy_root"],
        scenarios_dir=fixture["scenarios_dir"],
    )
    candidate = fixture["snapshot"]["candidates"][0]
    curation = _baseline_curation(candidate)
    curation["candidate_set_sha256"] = assembled.candidate_set_sha256
    curation["baseline_reference_sha256"] = assembled.baseline_reference_sha256
    curation_path = fixture["repository_root"] / "curation.json"
    curation_path.write_text(json.dumps(curation), encoding="utf-8")
    original_write_inventory = baseline._write_bundle_inventory

    def tamper_then_inventory(bundle_root: Path) -> None:
        curation_files = list((bundle_root / "data" / "curation").glob("*.json"))
        if curation_files:
            curation_files[0].write_bytes(b"{}")
        original_write_inventory(bundle_root)

    monkeypatch.setattr(
        baseline,
        "_write_bundle_inventory",
        tamper_then_inventory,
    )
    output = fixture["repository_root"] / "release-curation-raced"
    with pytest.raises(BaselineError, match="curation authority SHA-256"):
        baseline.finalize_baseline(
            bundle_dir=bundle_dir,
            input_path=curation_path,
            output_dir=output,
            scenarios_dir=fixture["scenarios_dir"],
        )
    assert not output.exists()


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("source-ledger", "source run ledger SHA"),
        ("audit-marker", "baseline audit SHA marker"),
    ],
)
def test_finalizeはinventoryに自証されたrelease証拠改変を拒否(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    message: str,
) -> None:
    fixture = _single_group_fixture(tmp_path, monkeypatch)
    bundle_dir = fixture["repository_root"] / "bundle"
    assembled = baseline.assemble_baseline(
        plan_path=fixture["plan_path"],
        run_ids=[fixture["run_id"]],
        output_dir=bundle_dir,
        artifacts_dir=fixture["artifacts_dir"],
        legacy_root=fixture["legacy_root"],
        scenarios_dir=fixture["scenarios_dir"],
    )
    candidate = fixture["snapshot"]["candidates"][0]
    curation = _baseline_curation(candidate)
    curation["candidate_set_sha256"] = assembled.candidate_set_sha256
    curation["baseline_reference_sha256"] = assembled.baseline_reference_sha256
    curation_path = fixture["repository_root"] / "curation.json"
    curation_path.write_text(json.dumps(curation), encoding="utf-8")
    original_write_inventory = baseline._write_bundle_inventory

    def tamper_then_inventory(bundle_root: Path) -> None:
        if (bundle_root / "baseline-audit.sha256").is_file():
            tamper_path = (
                bundle_root / "source-runs" / "dummy" / "ledger.json"
                if target == "source-ledger"
                else bundle_root / "baseline-audit.sha256"
            )
            tamper_path.write_bytes(b"tampered")
        original_write_inventory(bundle_root)

    monkeypatch.setattr(
        baseline,
        "_write_bundle_inventory",
        tamper_then_inventory,
    )
    output = fixture["repository_root"] / f"release-{target}-raced"
    with pytest.raises(BaselineError, match=message):
        baseline.finalize_baseline(
            bundle_dir=bundle_dir,
            input_path=curation_path,
            output_dir=output,
            scenarios_dir=fixture["scenarios_dir"],
        )
    assert not output.exists()


def test_finalizeは既知lineでもplan外variantのsource_runを拒否(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _single_group_fixture(tmp_path, monkeypatch)
    bundle_dir = fixture["repository_root"] / "bundle"
    assembled = baseline.assemble_baseline(
        plan_path=fixture["plan_path"],
        run_ids=[fixture["run_id"]],
        output_dir=bundle_dir,
        artifacts_dir=fixture["artifacts_dir"],
        legacy_root=fixture["legacy_root"],
        scenarios_dir=fixture["scenarios_dir"],
    )
    candidate = fixture["snapshot"]["candidates"][0]
    curation = _baseline_curation(candidate)
    curation["candidate_set_sha256"] = assembled.candidate_set_sha256
    curation["baseline_reference_sha256"] = assembled.baseline_reference_sha256
    curation_path = fixture["repository_root"] / "curation.json"
    curation_path.write_text(json.dumps(curation), encoding="utf-8")
    original_validate = baseline._validate_source_run

    def plan_out_source_run(**kwargs: Any) -> tuple[dict[str, Any], Any]:
        ledger, source_bundle = original_validate(**kwargs)
        replaced = deepcopy(ledger)
        replaced["source"]["groups"][0]["variant"] = "wet"
        replaced["attempts"][0]["variant"] = "wet"
        return replaced, source_bundle

    monkeypatch.setattr(
        baseline,
        "_validate_source_run",
        plan_out_source_run,
    )
    output = fixture["repository_root"] / "release-plan-out"
    with pytest.raises(BaselineError, match="model selection.*exact"):
        baseline.finalize_baseline(
            bundle_dir=bundle_dir,
            input_path=curation_path,
            output_dir=output,
            scenarios_dir=fixture["scenarios_dir"],
        )
    assert not output.exists()


def _baseline_curation(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "format_version": 1,
        "rubric_version": "baseline-curation-v1",
        "candidate_set_sha256": "4" * 64,
        "baseline_reference_sha256": "5" * 64,
        "groups": [
            {
                **{key: candidate[key] for key in baseline.GROUP_KEYS},
                "candidates": [
                    {
                        "take_id": candidate["take_id"],
                        "path": candidate["path"],
                        "audio_sha256": candidate["sha256"],
                        "rubric": {
                            "content_correct": True,
                            "intent_match": 4,
                            "character_naturalness": 4,
                            "adoptable": True,
                        },
                    },
                ],
                "decision": {
                    "type": "selected",
                    "take_id": candidate["take_id"],
                },
            },
        ],
    }


def test_finalize_curationは全candidate_groupとselected_rubricを要求() -> None:
    candidate = _candidate()
    failure = {
        "model": "dummy",
        "scenario": "s",
        "line": "zero",
        "variant": "dry",
        "reason": "no_eligible_take",
    }
    valid = _baseline_curation(candidate)
    normalized = baseline._validate_baseline_curation(
        valid,
        candidate_set_sha256="4" * 64,
        baseline_reference_sha256="5" * 64,
        candidates=[candidate],
        failures=[failure],
    )
    assert len(normalized["groups"]) == 1

    missing = deepcopy(valid)
    missing["groups"] = []
    with pytest.raises(BaselineError, match="group/rubric"):
        baseline._validate_baseline_curation(
            missing,
            candidate_set_sha256="4" * 64,
            baseline_reference_sha256="5" * 64,
            candidates=[candidate],
            failures=[failure],
        )

    bad_selected = deepcopy(valid)
    bad_selected["groups"][0]["candidates"][0]["rubric"]["adoptable"] = False
    with pytest.raises(BaselineError, match="selected candidate"):
        baseline._validate_baseline_curation(
            bad_selected,
            candidate_set_sha256="4" * 64,
            baseline_reference_sha256="5" * 64,
            candidates=[candidate],
            failures=[failure],
        )


def test_assembleはmissing_extra_runをoutput作成前に拒否(tmp_path: Path) -> None:
    plan = _real_plan(tmp_path)
    output = tmp_path / "bundle"
    with pytest.raises(BaselineError, match="7 件"):
        baseline.assemble_baseline(
            plan_path=plan,
            run_ids=["only-one"],
            output_dir=output,
            artifacts_dir=tmp_path / "artifacts",
            legacy_root=tmp_path / "legacy",
            scenarios_dir=SCENARIOS_DIR,
        )
    assert not output.exists()


def test_source_run_QCは自報ledger_pathでなく実ledger_pathに拘束(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-1"
    run_root = tmp_path / "artifacts" / "takes" / run_id
    run_root.mkdir(parents=True)
    ledger_path = run_root / "ledger.json"
    ledger = {
        "run_id": run_id,
        "source": {
            "scenario_sha256": "a" * 64,
            "model": "dummy",
            "recipe_version": "recipe-v1",
            "groups": [
                {
                    "model": "dummy",
                    "scenario": "s",
                    "line": "l",
                    "variant": "dry",
                },
            ],
        },
        "attempts": [
            {
                "model": "dummy",
                "scenario": "s",
                "line": "l",
                "variant": "dry",
                "take_index": 1,
                "status": "generation_failed",
            },
        ],
    }
    monkeypatch.setattr(baseline, "read_ledger", lambda _path: ledger)
    monkeypatch.setattr(
        baseline,
        "validate_snapshot_bundle",
        lambda **_kwargs: SimpleNamespace(
            manifest={"curations": [], "generated_at": "2026-07-29T00:00:00Z"},
        ),
    )
    report = {
        "format_version": 2,
        "generated_at": "2026-07-29T00:00:00Z",
        "gate_policy_version": "take-gates-v2",
        "run_id": run_id,
        "source": {
            "ledger": "self-declared/ledger.json",
            "scenario_sha256": "a" * 64,
            "model": "dummy",
            "recipe_version": "recipe-v1",
        },
        "runtime": {"status": "not_required"},
        "summary": {
            "attempt_count": 1,
            "eligible": 0,
            "hard_rejected": 0,
            "blocked": 0,
            "generation_failed": 1,
            "planned": 0,
            "generated": 0,
            "pending": 0,
            "content_review_required": 0,
        },
        "attempts": [
            {
                "model": "dummy",
                "scenario": "s",
                "line": "l",
                "variant": "dry",
                "take_index": 1,
                "status": "generation_failed",
                "gates": None,
                "mechanical": {"status": "not_run"},
                "content": {"status": "not_run"},
            },
        ],
    }
    (run_root / "qc-report.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )

    with pytest.raises(BaselineError, match="QC report.*source"):
        baseline._validate_source_run(
            run_id=run_id,
            run_root=run_root,
            scenarios_dir=tmp_path / "scenarios",
            qc_ledger_path=ledger_path,
        )

    assert report["source"]["ledger"] != ledger_path.as_posix()


def test_assembleはlegacyとcandidateをSHA検証してreferenceをcanonical固定(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _small_plan(monkeypatch)
    monkeypatch.setattr(baseline, "DUMMY_MODEL_ID", "test-only-dummy")
    candidate = _candidate()
    failure = {
        "model": "dummy",
        "scenario": "s",
        "line": "zero",
        "variant": "dry",
        "reason": "no_eligible_take",
    }
    run_id = "run-dummy"
    run_root = tmp_path / "artifacts" / "takes" / run_id
    run_root.mkdir(parents=True)
    for name in (
        "ledger.json",
        "qc-report.json",
        "manifest-v4.json",
        "candidate-set.json",
        "candidate-set.sha256",
    ):
        (run_root / name).write_bytes(name.encode())
    source_opus = run_root / "audio/dummy/s/candidate/dry/take-0001.opus"
    source_opus.parent.mkdir(parents=True)
    source_opus.write_bytes(b"candidate")
    candidate["sha256"] = hashlib.sha256(b"candidate").hexdigest()
    candidate["take_id"] = make_take_id(
        generation_input_sha256=candidate["generation_input_sha256"],
        final_opus_sha256=candidate["sha256"],
    )
    candidate["path"] = (
        "audio/takes/dummy/s/candidate/dry/take-0001-"
        f"{candidate['sha256']}.opus"
    )
    ledger = {
        "source": {
            "model": "dummy",
            "takes": 1,
            "groups": [
                {key: group[key] for key in baseline.GROUP_KEYS}
                for group in plan["groups"]
            ],
        },
        "attempts": [
            {
                "status": "eligible",
                "take_id": candidate["take_id"],
                "audio": {"opus_path": source_opus.relative_to(run_root).as_posix()},
            },
        ],
    }
    bundle = SimpleNamespace(
        manifest={
            "models": plan["models"],
            "candidates": [candidate],
            "failures": [failure],
        },
        candidate_set_sha256="6" * 64,
    )
    monkeypatch.setattr(baseline, "load_baseline_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(
        baseline,
        "_validate_source_run",
        lambda **_kwargs: (ledger, bundle),
    )
    monkeypatch.setattr(
        baseline,
        "_authoritative_plan_lines",
        lambda **_kwargs: (
            plan["source"]["scenario_sha256"],
            [
                {
                    "scenario": "s",
                    "line": line,
                    "scenario_title": "S",
                    "text": line,
                    "delivery": "test",
                }
                for line in ("candidate", "zero")
            ],
        ),
    )
    monkeypatch.setattr(
        baseline,
        "_copy_source_run",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        baseline,
        "_load_assembled_bundle",
        lambda **_kwargs: {},
    )
    legacy_root = tmp_path / "legacy"
    for group in plan["groups"]:
        payload = (
            b"legacy-candidate"
            if group["line"] == "candidate"
            else b"legacy-zero"
        )
        group["legacy"]["sha256"] = hashlib.sha256(payload).hexdigest()
        path = legacy_root / group["legacy"]["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    output = tmp_path / "bundle"
    summary = baseline.assemble_baseline(
        plan_path=tmp_path / "plan.json",
        run_ids=[run_id],
        output_dir=output,
        artifacts_dir=tmp_path / "artifacts",
        legacy_root=legacy_root,
        scenarios_dir=tmp_path / "scenarios",
    )

    reference_bytes = (output / "baseline-reference.json").read_bytes()
    reference = json.loads(reference_bytes)
    assert reference_bytes == baseline.canonical_json(reference).encode()
    assert (output / "baseline-reference.sha256").read_bytes() == (
        hashlib.sha256(reference_bytes).hexdigest().encode()
    )
    assert [item["comparison"] for item in reference["references"]] == [
        "different",
        "no_candidate",
    ]
    assert summary.group_count == 2
    assert summary.candidate_count == 1
    assert summary.failure_count == 1
    assert (output / candidate["path"]).read_bytes() == b"candidate"

    tampered_output = tmp_path / "tampered-bundle"
    first_reference = legacy_root / plan["groups"][0]["legacy"]["path"]
    first_reference.write_bytes(b"tampered")
    with pytest.raises(BaselineError, match="legacy reference Opus SHA"):
        baseline.assemble_baseline(
            plan_path=tmp_path / "plan.json",
            run_ids=[run_id],
            output_dir=tampered_output,
            artifacts_dir=tmp_path / "artifacts",
            legacy_root=legacy_root,
            scenarios_dir=tmp_path / "scenarios",
        )
    assert not tampered_output.exists()


def test_finalizeは全countとcanonical_SHA_artifactを固定(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _small_plan(monkeypatch)
    candidate = _candidate()
    failure = {
        "model": "dummy",
        "scenario": "s",
        "line": "zero",
        "variant": "dry",
        "reason": "no_eligible_take",
    }
    lines = [
        {
            "scenario": "s",
            "line": line,
            "scenario_title": "S",
            "text": line,
            "delivery": "test",
        }
        for line in ("candidate", "zero")
    ]
    candidate_set = baseline.build_candidate_set(
        scenario_sha256=plan["source"]["scenario_sha256"],
        lines=lines,
        models=plan["models"],
        candidates=[candidate],
        failures=[failure],
    )
    candidate_bytes = baseline.canonical_candidate_set_bytes(candidate_set)
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    manifest = baseline.validate_manifest_v4(
        {
            "format_version": 4,
            "generated_at": "baseline-assemble-v1",
            "candidate_set_sha256": candidate_sha,
            "models": plan["models"],
            "candidates": [candidate],
            "curations": [],
            "failures": [failure],
        },
    )
    reference = {
        "format_version": 1,
        "source_manifest_sha256": plan["source"]["manifest_sha256"],
        "candidate_set_sha256": candidate_sha,
        "references": [],
    }
    reference_sha = "5" * 64
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    for name in (
        "baseline-plan.json",
        "baseline-plan.sha256",
        "baseline-provenance.json",
        "baseline-provenance.sha256",
    ):
        (bundle_dir / name).write_bytes(name.encode())
    material = {
        "plan": plan,
        "manifest": manifest,
        "candidate_set": candidate_set,
        "reference": reference,
        "reference_sha256": reference_sha,
        "provenance": {
            "format_version": 1,
            "plan_sha256": hashlib.sha256(
                baseline.canonical_json(plan).encode(),
            ).hexdigest(),
            "runs": [],
        },
        "source_runs": {},
    }
    monkeypatch.setattr(
        baseline,
        "_load_assembled_bundle",
        lambda **_kwargs: material,
    )
    monkeypatch.setattr(baseline, "_copy_verified", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        baseline,
        "_validate_release_bundle",
        lambda **_kwargs: None,
    )
    curation = _baseline_curation(candidate)
    curation["candidate_set_sha256"] = candidate_sha
    curation["baseline_reference_sha256"] = reference_sha
    input_path = tmp_path / "curation.json"
    input_path.write_text(json.dumps(curation), encoding="utf-8")

    summary = baseline.finalize_baseline(
        bundle_dir=bundle_dir,
        input_path=input_path,
        output_dir=tmp_path / "release",
        scenarios_dir=tmp_path / "scenarios",
    )

    assert summary.candidate_zero_count == 1
    assert summary.selected_count == 1
    assert summary.skipped_count == 0
    audit_bytes = (summary.output_dir / "baseline-audit.json").read_bytes()
    audit = json.loads(audit_bytes)
    assert audit["counts"] == {
        "total": 2,
        "candidate_zero": 1,
        "selected": 1,
        "skipped": 0,
        "uncurated": 0,
    }
    assert audit_bytes == baseline.canonical_json(audit).encode()
    assert (summary.output_dir / "baseline-audit.sha256").read_bytes() == (
        hashlib.sha256(audit_bytes).hexdigest().encode()
    )


def test_finalizeは既存outputをfail_fastで拒否(tmp_path: Path) -> None:
    output = tmp_path / "release"
    output.mkdir()
    with pytest.raises(BaselineError, match="既存"):
        baseline.finalize_baseline(
            bundle_dir=tmp_path / "missing",
            input_path=tmp_path / "missing.json",
            output_dir=output,
            scenarios_dir=SCENARIOS_DIR,
        )
