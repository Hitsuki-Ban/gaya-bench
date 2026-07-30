from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline import cli
from gaya_pipeline.curation import apply_curation
from gaya_pipeline.release import (
    ReleaseError,
    ReleaseFinalizeSummary,
    finalize_release,
    validate_finalized_release,
)
from gaya_pipeline.selection import (
    AUTOMATIC_SELECTION_POLICY,
    selection_group_to_human_curation,
)
from gaya_pipeline.take_identity import canonical_json, make_take_id
from gaya_pipeline.take_ledger import read_ledger, write_ledger_atomic
from test_curation import (
    SCENARIOS_DIR,
    _add_candidate_groups,
    _curation,
    _curation_for_lines,
    _rewrite_snapshot_bundle,
    _setup_run,
    _write_qc_report,
    _write_take_files,
    _write_input,
)


def _setup_curated_run(
    tmp_path: Path,
    *,
    run_id: str,
    model: str,
    audio_bytes: bytes,
    scenarios_dir: Path = SCENARIOS_DIR,
    line_text: str = "はいよっ、エール二つお待ち！",
    decision: str = "selected",
) -> tuple[str, dict[str, Any]]:
    configured_run_id, manifest, _snapshot_path, _audio_path = _setup_run(
        tmp_path,
        run_id=run_id,
        model=model,
        audio_bytes=audio_bytes,
        scenarios_dir=scenarios_dir,
        line_text=line_text,
    )
    apply_curation(
        run_id=configured_run_id,
        input_path=_write_input(tmp_path, _curation(manifest, decision=decision)),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=scenarios_dir,
    )
    return configured_run_id, manifest


def _add_second_candidate_to_group(
    *,
    run_id: str,
    manifest: dict[str, Any],
    snapshot_path: Path,
) -> None:
    first = manifest["candidates"][0]
    audio_bytes = b"second candidate opus"
    audio_sha = hashlib.sha256(audio_bytes).hexdigest()
    input_sha = "d" * 64
    second = deepcopy(first)
    second.update(
        take_index=2,
        sha256=audio_sha,
        generation_input_sha256=input_sha,
        take_id=make_take_id(
            generation_input_sha256=input_sha,
            final_opus_sha256=audio_sha,
        ),
        path=(
            f"audio/takes/{first['model']}/tavern-night/barmaid-001/dry/"
            f"take-0002-{audio_sha}.opus"
        ),
    )
    manifest["candidates"].append(second)
    run_root = snapshot_path.parent
    _audio_path, ledger_audio = _write_take_files(
        run_root=run_root,
        run_id=run_id,
        candidate=second,
        opus_bytes=audio_bytes,
    )
    ledger = read_ledger(run_root / "ledger.json")
    attempt = deepcopy(ledger["attempts"][0])
    attempt.update(
        take_index=2,
        take_id=second["take_id"],
        generation_input_sha256=input_sha,
        audio=ledger_audio,
    )
    ledger["source"]["takes"] = 2
    ledger["attempts"].append(attempt)
    write_ledger_atomic(run_root / "ledger.json", ledger)
    _rewrite_snapshot_bundle(manifest=manifest, snapshot_path=snapshot_path)
    _write_qc_report(run_root=run_root, manifest=manifest)


def _finalize(
    tmp_path: Path,
    *,
    run_ids: list[str],
    output_name: str = "release",
    scenarios_dir: Path = SCENARIOS_DIR,
    projection_plan_path: Path | None = None,
    selection_policy: str | None = None,
) -> ReleaseFinalizeSummary:
    return finalize_release(
        run_ids=run_ids,
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=scenarios_dir,
        output_dir=tmp_path / output_name,
        projection_plan_path=projection_plan_path,
        selection_policy=selection_policy,
    )


def _inventory(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _copy_scenarios_with_source_drift(
    tmp_path: Path,
    *,
    line_text_drift: bool = False,
) -> Path:
    destination = tmp_path / "preserved-scenarios"
    shutil.copytree(SCENARIOS_DIR, destination)
    shutil.copytree(
        SCENARIOS_DIR.parent / "assets" / "voices",
        tmp_path / "assets" / "voices",
    )
    scenario_path = destination / "tavern-night.yaml"
    contents = scenario_path.read_text(encoding="utf-8")
    if line_text_drift:
        contents = contents.replace(
            "はいよっ、エール二つお待ち！",
            "はいよっ、別の台詞だよ！",
        )
    scenario_path.write_text(
        f"{contents}\n# preserved source drift\n",
        encoding="utf-8",
    )
    return destination


def _write_projection_plan(
    tmp_path: Path,
    *,
    preserved_release: Path,
    preserved_model: str,
    target_run_id: str,
    missing_line: str = "barmaid-002",
) -> Path:
    manifest = json.loads(
        (preserved_release / "manifest-v4.json").read_bytes(),
    )
    curation_hashes = {
        item["curation_sha256"] for item in manifest["curations"]
    }
    assert len(curation_hashes) == 1
    document = {
        "format_version": 1,
        "target_run_id": target_run_id,
        "source_release": {
            "path": preserved_release.relative_to(tmp_path).as_posix(),
            "model": preserved_model,
            "manifest_sha256": hashlib.sha256(
                (preserved_release / "manifest-v4.json").read_bytes(),
            ).hexdigest(),
            "candidate_set_sha256": (
                preserved_release / "candidate-set.sha256"
            ).read_text(encoding="ascii"),
            "provenance_sha256": hashlib.sha256(
                (preserved_release / "release-provenance.json").read_bytes(),
            ).hexdigest(),
            "curation_sha256": next(iter(curation_hashes)),
        },
        "target_failures": [
            {
                "model": preserved_model,
                "scenario": "tavern-night",
                "line": missing_line,
                "variant": "dry",
                "reason": "no_eligible_take",
            },
        ],
    }
    path = tmp_path / "projection-plan.json"
    path.write_bytes(canonical_json(document).encode("utf-8"))
    return path


def _setup_projection_fixture(
    tmp_path: Path,
    *,
    line_text_drift: bool = False,
    missing_line: str = "barmaid-002",
    curate_target: bool = True,
    preserved_decision: str = "selected",
) -> tuple[str, Path, Path]:
    preserved_scenarios = _copy_scenarios_with_source_drift(
        tmp_path,
        line_text_drift=line_text_drift,
    )
    preserved_run, _ = _setup_curated_run(
        tmp_path,
        run_id="preserved-run",
        model="model-preserved",
        audio_bytes=b"preserved opus",
        scenarios_dir=preserved_scenarios,
        line_text=(
            "はいよっ、別の台詞だよ！"
            if line_text_drift
            else "はいよっ、エール二つお待ち！"
        ),
        decision=preserved_decision,
    )
    preserved_release = _finalize(
        tmp_path,
        run_ids=[preserved_run],
        output_name="preserved-release",
        scenarios_dir=preserved_scenarios,
    ).output_dir

    target_run, target_manifest, target_snapshot, _ = _setup_run(
        tmp_path,
        run_id="target-run",
        model="model-current",
        audio_bytes=b"current opus",
    )
    _add_candidate_groups(
        manifest=target_manifest,
        snapshot_path=target_snapshot,
        line_ids=("barmaid-002",),
    )
    if curate_target:
        apply_curation(
            run_id=target_run,
            input_path=_write_input(
                tmp_path,
                _curation_for_lines(
                    target_manifest,
                    ("barmaid-001", "barmaid-002"),
                ),
            ),
            artifacts_dir=tmp_path / "artifacts",
            data_dir=tmp_path / "data",
            scenarios_dir=SCENARIOS_DIR,
        )
    plan_path = _write_projection_plan(
        tmp_path,
        preserved_release=preserved_release,
        preserved_model="model-preserved",
        target_run_id=target_run,
        missing_line=missing_line,
    )
    return target_run, preserved_release, plan_path


def test_finalizeは複数modelをcanonical_releaseへ集約し入力順に非依存(
    tmp_path: Path,
) -> None:
    run_a, _manifest_a = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"model a opus",
    )
    run_b, _manifest_b = _setup_curated_run(
        tmp_path,
        run_id="run-b",
        model="model-b",
        audio_bytes=b"model b opus",
    )

    summary_a = _finalize(
        tmp_path,
        run_ids=[run_b, run_a],
        output_name="release-a",
    )
    summary_b = _finalize(
        tmp_path,
        run_ids=[run_a, run_b],
        output_name="release-b",
    )
    release = validate_finalized_release(
        release_dir=summary_a.output_dir,
        takes_root=tmp_path / "artifacts" / "takes",
    )

    assert summary_a == ReleaseFinalizeSummary(
        output_dir=tmp_path / "release-a",
        manifest_sha256=summary_b.manifest_sha256,
        candidate_set_sha256=summary_b.candidate_set_sha256,
        curation_sha256=summary_b.curation_sha256,
        model_count=2,
        candidate_count=2,
        selected_count=2,
        skipped_count=0,
        failure_count=0,
    )
    assert _inventory(tmp_path / "release-a") == _inventory(
        tmp_path / "release-b",
    )
    assert [model["id"] for model in release.manifest["models"]] == [
        "model-a",
        "model-b",
    ]
    assert set(release.run_roots) == {"model-a", "model-b"}
    assert release.provenance["manifest_sha256"] == summary_a.manifest_sha256


def test_finalizeは自動gateと既存人評decisionを混合して保持する(
    tmp_path: Path,
) -> None:
    automatic_run, _ = _setup_run(
        tmp_path,
        run_id="automatic-run",
        model="model-auto",
        audio_bytes=b"automatic opus",
    )[:2]
    human_run, human_manifest, _snapshot_path, _audio_path = _setup_run(
        tmp_path,
        run_id="human-run",
        model="model-human",
        audio_bytes=b"human opus",
    )
    apply_curation(
        run_id=human_run,
        input_path=_write_input(
            tmp_path,
            _curation(human_manifest, decision="skipped"),
        ),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )

    summary = _finalize(
        tmp_path,
        run_ids=[automatic_run, human_run],
        selection_policy=AUTOMATIC_SELECTION_POLICY,
    )
    release = validate_finalized_release(
        release_dir=summary.output_dir,
        takes_root=tmp_path / "artifacts" / "takes",
    )
    groups_by_model = {
        group["model"]: group for group in release.curation["groups"]
    }

    assert summary.selected_count == 1
    assert summary.skipped_count == 1
    assert release.curation["format_version"] == 2
    assert groups_by_model["model-auto"]["authority"] == {
        "type": "automatic_gate",
        "selection_policy_version": AUTOMATIC_SELECTION_POLICY,
        "gate_policy_version": "take-gates-v2",
    }
    assert groups_by_model["model-auto"]["candidates"][0]["gate"]["content"] == (
        "review_required"
    )
    assert groups_by_model["model-human"]["authority"] == {
        "type": "human",
        "rubric_version": "take-curation-v1",
    }
    assert groups_by_model["model-human"]["decision"] == {"type": "skipped"}


def test_finalize自動gateは未策展N2_groupを拒否する(tmp_path: Path) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(
        tmp_path,
        run_id="automatic-n2-run",
        model="model-auto",
    )
    _add_second_candidate_to_group(
        run_id=run_id,
        manifest=manifest,
        snapshot_path=snapshot_path,
    )

    with pytest.raises(ReleaseError, match="N=1"):
        _finalize(
            tmp_path,
            run_ids=[run_id],
            selection_policy=AUTOMATIC_SELECTION_POLICY,
        )

    assert not (tmp_path / "release").exists()


def test_finalize自動gateは不正なgate_policyを拒否する(tmp_path: Path) -> None:
    run_id, manifest, snapshot_path, _audio_path = _setup_run(
        tmp_path,
        run_id="automatic-policy-run",
        model="model-auto",
    )
    manifest["candidates"][0]["gate"]["policy_version"] = "take-gates-v1"
    _rewrite_snapshot_bundle(manifest=manifest, snapshot_path=snapshot_path)
    _write_qc_report(run_root=snapshot_path.parent, manifest=manifest)

    with pytest.raises(ReleaseError, match="take-gates-v2"):
        _finalize(
            tmp_path,
            run_ids=[run_id],
            selection_policy=AUTOMATIC_SELECTION_POLICY,
        )

    assert not (tmp_path / "release").exists()


def test_finalizeは未知のselection_policyを拒否する(tmp_path: Path) -> None:
    with pytest.raises(ReleaseError, match="未知のselection policy"):
        _finalize(
            tmp_path,
            run_ids=["unused"],
            selection_policy="automatic-gate-v0",
        )

    assert not (tmp_path / "release").exists()


def test_finalize自動gateはprojection人評をexactに保持する(
    tmp_path: Path,
) -> None:
    target_run, preserved_release, plan_path = _setup_projection_fixture(
        tmp_path,
        curate_target=False,
        preserved_decision="skipped",
    )
    preserved = validate_finalized_release(
        release_dir=preserved_release,
        takes_root=tmp_path / "artifacts" / "takes",
    )

    summary = _finalize(
        tmp_path,
        run_ids=[target_run],
        output_name="projected-automatic-release",
        projection_plan_path=plan_path,
        selection_policy=AUTOMATIC_SELECTION_POLICY,
    )
    release = validate_finalized_release(
        release_dir=summary.output_dir,
        takes_root=tmp_path / "artifacts" / "takes",
    )
    projected_groups = [
        selection_group_to_human_curation(group)
        for group in release.curation["groups"]
        if group["model"] == "model-preserved"
    ]

    assert summary == ReleaseFinalizeSummary(
        output_dir=tmp_path / "projected-automatic-release",
        manifest_sha256=summary.manifest_sha256,
        candidate_set_sha256=summary.candidate_set_sha256,
        curation_sha256=summary.curation_sha256,
        model_count=2,
        candidate_count=3,
        selected_count=2,
        skipped_count=1,
        failure_count=1,
    )
    assert projected_groups == preserved.curation["groups"]


def test_finalizeは保持済みreleaseを現行line_snapshotへ明示投影する(
    tmp_path: Path,
) -> None:
    target_run, preserved_release, plan_path = _setup_projection_fixture(tmp_path)

    summary = _finalize(
        tmp_path,
        run_ids=[target_run],
        output_name="projected-release",
        projection_plan_path=plan_path,
    )
    release = validate_finalized_release(
        release_dir=summary.output_dir,
        takes_root=tmp_path / "artifacts" / "takes",
    )

    assert summary.model_count == 2
    assert summary.candidate_count == 3
    assert summary.failure_count == 1
    assert release.provenance["format_version"] == 2
    assert release.projection_plan is not None
    assert release.projection_plan["source_release"]["path"] == (
        preserved_release.relative_to(tmp_path).as_posix()
    )
    assert (
        release.provenance["projection"]["source_scenario_sha256"]
        != release.provenance["projection"]["target_scenario_sha256"]
    )
    assert release.manifest["failures"] == [
        {
            "model": "model-preserved",
            "scenario": "tavern-night",
            "line": "barmaid-002",
            "variant": "dry",
            "reason": "no_eligible_take",
        },
    ]
    assert (summary.output_dir / "projection-plan.json").read_bytes() == (
        plan_path.read_bytes()
    )


def test_finalizeは凍結projection_sourceを別worktreeから検証する(
    tmp_path: Path,
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    target_run, _preserved_release, plan_path = _setup_projection_fixture(origin)
    relocated = tmp_path / "relocated"
    origin.rename(relocated)

    target_report_path = (
        relocated / "artifacts" / "takes" / target_run / "qc-report.json"
    )
    target_report = json.loads(target_report_path.read_bytes())
    target_report["source"]["ledger"] = (
        relocated / "artifacts" / "takes" / target_run / "ledger.json"
    ).as_posix()
    target_report_path.write_bytes(
        canonical_json(target_report).encode("utf-8"),
    )
    relocated_plan = relocated / plan_path.relative_to(origin)

    summary = _finalize(
        relocated,
        run_ids=[target_run],
        output_name="projected-release",
        projection_plan_path=relocated_plan,
    )

    assert summary.model_count == 2
    assert summary.candidate_count == 3
    assert summary.failure_count == 1


def test_finalizeは通常runのQC_ledger_path差異を拒否する(
    tmp_path: Path,
) -> None:
    run_id, _ = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"take a",
    )
    report_path = tmp_path / "artifacts" / "takes" / run_id / "qc-report.json"
    report = json.loads(report_path.read_bytes())
    report["source"]["ledger"] = "C:/different-worktree/ledger.json"
    report_path.write_bytes(canonical_json(report).encode("utf-8"))

    with pytest.raises(ReleaseError, match="QC report"):
        _finalize(tmp_path, run_ids=[run_id])

    assert not (tmp_path / "release").exists()


def test_finalizeは保持sourceとtargetのline_snapshot差異を拒否する(
    tmp_path: Path,
) -> None:
    target_run, _preserved_release, plan_path = _setup_projection_fixture(
        tmp_path,
        line_text_drift=True,
    )

    with pytest.raises(ReleaseError, match="line snapshot"):
        _finalize(
            tmp_path,
            run_ids=[target_run],
            output_name="projected-release",
            projection_plan_path=plan_path,
        )

    assert not (tmp_path / "projected-release").exists()


def test_finalizeは未宣言target_missing_groupを拒否する(
    tmp_path: Path,
) -> None:
    target_run, _preserved_release, plan_path = _setup_projection_fixture(
        tmp_path,
        missing_line="barmaid-003",
    )

    with pytest.raises(ReleaseError, match="group coverage"):
        _finalize(
            tmp_path,
            run_ids=[target_run],
            output_name="projected-release",
            projection_plan_path=plan_path,
        )

    assert not (tmp_path / "projected-release").exists()


def test_finalizeはprojection_source_digest差異を拒否する(tmp_path: Path) -> None:
    target_run, _preserved_release, plan_path = _setup_projection_fixture(tmp_path)
    plan = json.loads(plan_path.read_bytes())
    plan["source_release"]["manifest_sha256"] = "f" * 64
    plan_path.write_bytes(canonical_json(plan).encode("utf-8"))

    with pytest.raises(ReleaseError, match="manifest_sha256"):
        _finalize(
            tmp_path,
            run_ids=[target_run],
            output_name="projected-release",
            projection_plan_path=plan_path,
        )

    assert not (tmp_path / "projected-release").exists()


def test_finalizeはprojection_sourceの物理audio改変を拒否する(
    tmp_path: Path,
) -> None:
    target_run, _preserved_release, plan_path = _setup_projection_fixture(tmp_path)
    run_root = tmp_path / "artifacts" / "takes" / "preserved-run"
    ledger = read_ledger(run_root / "ledger.json")
    opus_path = run_root / ledger["attempts"][0]["audio"]["opus_path"]
    opus_path.write_bytes(b"tampered opus")

    with pytest.raises(ReleaseError, match="物理artifact provenance"):
        _finalize(
            tmp_path,
            run_ids=[target_run],
            output_name="projected-release",
            projection_plan_path=plan_path,
        )

    assert not (tmp_path / "projected-release").exists()


def test_finalizeは非canonical_projection_planを拒否する(tmp_path: Path) -> None:
    target_run, _preserved_release, plan_path = _setup_projection_fixture(tmp_path)
    plan_path.write_bytes(plan_path.read_bytes() + b"\n")

    with pytest.raises(ReleaseError, match="canonical bytes"):
        _finalize(
            tmp_path,
            run_ids=[target_run],
            output_name="projected-release",
            projection_plan_path=plan_path,
        )

    assert not (tmp_path / "projected-release").exists()


def test_finalizeはtarget_run_idが通常入力にないplanを拒否する(
    tmp_path: Path,
) -> None:
    target_run, _preserved_release, plan_path = _setup_projection_fixture(tmp_path)
    plan = json.loads(plan_path.read_bytes())
    plan["target_run_id"] = "missing-target-run"
    plan_path.write_bytes(canonical_json(plan).encode("utf-8"))

    with pytest.raises(ReleaseError, match="target_run_id"):
        _finalize(
            tmp_path,
            run_ids=[target_run],
            output_name="projected-release",
            projection_plan_path=plan_path,
        )

    assert not (tmp_path / "projected-release").exists()


def test_finalizeはprojected_releaseからの連鎖投影を拒否する(
    tmp_path: Path,
) -> None:
    target_run, _preserved_release, plan_path = _setup_projection_fixture(tmp_path)
    projected_release = _finalize(
        tmp_path,
        run_ids=[target_run],
        output_name="projected-release",
        projection_plan_path=plan_path,
    ).output_dir
    chained_plan = _write_projection_plan(
        tmp_path,
        preserved_release=projected_release,
        preserved_model="model-preserved",
        target_run_id=target_run,
    )

    with pytest.raises(ReleaseError, match="format_version=1"):
        _finalize(
            tmp_path,
            run_ids=[target_run],
            output_name="chained-release",
            projection_plan_path=chained_plan,
        )

    assert not (tmp_path / "chained-release").exists()


def test_finalizeは未策展runを拒否しoutputを残さない(tmp_path: Path) -> None:
    run_id, _manifest, _snapshot_path, _audio_path = _setup_run(
        tmp_path,
        run_id="uncurated",
        model="model-a",
    )
    output = tmp_path / "release"

    with pytest.raises(ReleaseError, match="未策展"):
        _finalize(tmp_path, run_ids=[run_id])

    assert not output.exists()


def test_finalizeはdummy_runをproduction入力として拒否する(
    tmp_path: Path,
) -> None:
    run_id, _ = _setup_curated_run(
        tmp_path,
        run_id="run-dummy",
        model="dummy",
        audio_bytes=b"dummy beep",
    )

    with pytest.raises(ReleaseError, match="dummy run"):
        _finalize(tmp_path, run_ids=[run_id])

    assert not (tmp_path / "release").exists()


def test_finalizeは非terminal_runを拒否する(tmp_path: Path) -> None:
    run_id, _ = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"take a",
    )
    ledger_path = tmp_path / "artifacts" / "takes" / run_id / "ledger.json"
    ledger = read_ledger(ledger_path)
    ledger["attempts"][0]["status"] = "generated"
    ledger["attempts"][0]["gates"] = {}
    write_ledger_atomic(ledger_path, ledger)

    with pytest.raises(ReleaseError, match="全 attempt terminal"):
        _finalize(tmp_path, run_ids=[run_id])

    assert not (tmp_path / "release").exists()


def test_finalizeは異なるline_snapshotのrunを集約しない(
    tmp_path: Path,
) -> None:
    run_a, _ = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"take a",
    )
    run_b, manifest_b, snapshot_b, _audio_b = _setup_run(
        tmp_path,
        run_id="run-b",
        model="model-b",
        audio_bytes=b"take b",
    )
    _add_candidate_groups(
        manifest=manifest_b,
        snapshot_path=snapshot_b,
        line_ids=("barmaid-002",),
    )
    apply_curation(
        run_id=run_b,
        input_path=_write_input(
            tmp_path,
            _curation_for_lines(
                manifest_b,
                ("barmaid-001", "barmaid-002"),
            ),
        ),
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )

    with pytest.raises(ReleaseError, match="candidate line snapshot"):
        _finalize(tmp_path, run_ids=[run_a, run_b])

    assert not (tmp_path / "release").exists()


def test_finalizeは同一modelの複数runを拒否する(tmp_path: Path) -> None:
    run_a, _ = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"take a",
    )
    run_b, _ = _setup_curated_run(
        tmp_path,
        run_id="run-b",
        model="model-a",
        audio_bytes=b"take b",
    )

    with pytest.raises(ReleaseError, match="model ごとに1 run"):
        _finalize(tmp_path, run_ids=[run_a, run_b])

    assert not (tmp_path / "release").exists()


def test_finalizeは既存outputを上書きしない(tmp_path: Path) -> None:
    run_id, _ = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"take a",
    )
    output = tmp_path / "release"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ReleaseError, match="既存 path"):
        _finalize(tmp_path, run_ids=[run_id])

    assert marker.read_text(encoding="utf-8") == "keep"


def test_finalized_releaseはsource改変を拒否する(tmp_path: Path) -> None:
    run_id, _ = _setup_curated_run(
        tmp_path,
        run_id="run-a",
        model="model-a",
        audio_bytes=b"take a",
    )
    summary = _finalize(tmp_path, run_ids=[run_id])
    ledger_path = tmp_path / "artifacts" / "takes" / run_id / "ledger.json"
    ledger = json.loads(ledger_path.read_bytes())
    ledger["created_at"] = "2026-07-30T00:00:00Z"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    with pytest.raises(ReleaseError, match="source ledger SHA"):
        validate_finalized_release(
            release_dir=summary.output_dir,
            takes_root=tmp_path / "artifacts" / "takes",
        )


def test_finalize_cliはexplicit_run_idsとoutputを渡す(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}
    output = tmp_path / "release"

    def fake_finalize(**kwargs: Any) -> ReleaseFinalizeSummary:
        captured.update(kwargs)
        return ReleaseFinalizeSummary(
            output_dir=output,
            manifest_sha256="a" * 64,
            candidate_set_sha256="b" * 64,
            curation_sha256="c" * 64,
            model_count=2,
            candidate_count=2,
            selected_count=1,
            skipped_count=1,
            failure_count=0,
        )

    monkeypatch.setattr(cli, "finalize_release", fake_finalize)
    monkeypatch.setattr(cli, "default_scenarios_dir", lambda: tmp_path / "scenarios")

    assert cli.main(
        [
            "takes",
            "finalize",
            "--run-id",
            "run-b",
            "--run-id",
            "run-a",
            "--output",
            str(output),
        ],
    ) == 0

    assert captured == {
        "run_ids": ["run-b", "run-a"],
        "artifacts_dir": tmp_path / "artifacts",
        "data_dir": tmp_path / "data",
        "scenarios_dir": tmp_path / "scenarios",
        "output_dir": output,
        "projection_plan_path": None,
        "selection_policy": None,
    }
    assert "model 2" in capsys.readouterr().out


def test_finalize_cliはprojection_planを明示的に渡す(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    output = tmp_path / "release"
    plan = tmp_path / "projection-plan.json"

    def fake_finalize(**kwargs: Any) -> ReleaseFinalizeSummary:
        captured.update(kwargs)
        return ReleaseFinalizeSummary(
            output_dir=output,
            manifest_sha256="a" * 64,
            candidate_set_sha256="b" * 64,
            curation_sha256="c" * 64,
            model_count=2,
            candidate_count=2,
            selected_count=2,
            skipped_count=0,
            failure_count=1,
        )

    monkeypatch.setattr(cli, "finalize_release", fake_finalize)
    monkeypatch.setattr(cli, "default_scenarios_dir", lambda: tmp_path / "scenarios")

    assert cli.main(
        [
            "takes",
            "finalize",
            "--run-id",
            "target-run",
            "--projection-plan",
            str(plan),
            "--output",
            str(output),
        ],
    ) == 0

    assert captured["projection_plan_path"] == plan


def test_finalize_cliはautomatic_selection_policyを明示的に渡す(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    output = tmp_path / "release"

    def fake_finalize(**kwargs: Any) -> ReleaseFinalizeSummary:
        captured.update(kwargs)
        return ReleaseFinalizeSummary(
            output_dir=output,
            manifest_sha256="a" * 64,
            candidate_set_sha256="b" * 64,
            curation_sha256="c" * 64,
            model_count=1,
            candidate_count=1,
            selected_count=1,
            skipped_count=0,
            failure_count=0,
        )

    monkeypatch.setattr(cli, "finalize_release", fake_finalize)
    monkeypatch.setattr(cli, "default_scenarios_dir", lambda: tmp_path / "scenarios")

    assert cli.main(
        [
            "takes",
            "finalize",
            "--run-id",
            "automatic-run",
            "--selection-policy",
            AUTOMATIC_SELECTION_POLICY,
            "--output",
            str(output),
        ],
    ) == 0

    assert captured["selection_policy"] == AUTOMATIC_SELECTION_POLICY
