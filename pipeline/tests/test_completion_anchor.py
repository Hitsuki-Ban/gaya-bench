from __future__ import annotations

import hashlib
import json
import math
import struct
import wave
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline.adapters.base import (
    Capabilities,
    ModelProfile,
)
from gaya_pipeline.completion_anchor import (
    CompletionAnchorError,
    build_anchor_listening_bundle,
    build_anchor_topup_plan,
    finalize_anchor_selection,
    merge_anchor_runs,
    resolve_selected_anchor,
    run_anchor_generation,
    validate_anchor_selection,
    validate_anchor_topup_plan,
)
from gaya_pipeline.completion_plan import (
    IRODORI_MODEL,
    QWEN_MODEL,
    CompletionPlan,
    RoleSnapshot,
    derive_anchor_seed,
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
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"
VOICES_DIR = REPOSITORY_ROOT / "assets" / "voices"


@pytest.fixture(scope="module")
def plan() -> CompletionPlan:
    return load_completion_plan(
        PLAN_PATH,
        base_manifest_path=MANIFEST_PATH,
        scenarios_dir=SCENARIOS_DIR,
        voices_dir=VOICES_DIR,
    )


class FakeAnchorGenerator:
    def __init__(
        self,
        *,
        model: str,
        revision: str,
        fail_seeds: frozenset[int] = frozenset(),
    ) -> None:
        self.profile = ModelProfile(
            id=model,
            name="Fake Anchor",
            version=revision,
            license_note="test",
            capabilities=Capabilities(
                emotion=False,
                voice_prompt=True,
                clone=True,
                nonverbal=False,
                reading=False,
            ),
        )
        self.closed = False
        self.fail_seeds = fail_seeds

    def role_anchor_generation_input(
        self,
        role: RoleSnapshot,
    ) -> dict[str, Any]:
        return {
            "role_identity": {
                "scenario": role.scenario,
                "character": role.character,
                "role": dict(role.role),
                "reference_voice": role.reference_voice,
                "scene_setting": role.scene_setting,
            },
            "performance": "neutral",
        }

    def generate_role_anchor(
        self,
        role: RoleSnapshot,
        *,
        seed: int,
        output_wav: Path,
    ) -> dict[str, Any]:
        if seed in self.fail_seeds:
            raise RuntimeError(f"planned anchor failure: {seed}")
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 8_000
        samples = [
            int(4_000 * math.sin(2 * math.pi * 220 * index / sample_rate))
            for index in range(sample_rate // 2)
        ]
        with wave.open(str(output_wav), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        return {
            "seed": seed,
            "scenario": role.scenario,
            "character": role.character,
        }

    def close_role_anchor_generation(self) -> None:
        self.closed = True


def _generator(
    plan: CompletionPlan,
    model: str,
    *,
    fail_seeds: frozenset[int] = frozenset(),
) -> FakeAnchorGenerator:
    return FakeAnchorGenerator(
        model=model,
        revision=plan.models[model],
        fail_seeds=fail_seeds,
    )


def _build_complete_candidate_set(
    tmp_path: Path,
    plan: CompletionPlan,
) -> tuple[Path, Path]:
    artifacts = (tmp_path / "artifacts").resolve()
    run_anchor_generation(
        plan=plan,
        model_id=QWEN_MODEL,
        run_id="qwen-initial",
        artifacts_dir=artifacts,
        generator=_generator(plan, QWEN_MODEL),
    )
    run_anchor_generation(
        plan=plan,
        model_id=IRODORI_MODEL,
        run_id="irodori-initial",
        artifacts_dir=artifacts,
        generator=_generator(plan, IRODORI_MODEL),
    )
    candidate_set = (tmp_path / "candidate-set.json").resolve()
    summary = merge_anchor_runs(
        plan=plan,
        run_ids=["qwen-initial", "irodori-initial"],
        artifacts_dir=artifacts,
        output_path=candidate_set,
    )
    assert summary.group_count == 106
    assert summary.eligible_count == 106 * 4
    return artifacts, candidate_set


def _write_canonical(path: Path, document: Any) -> None:
    path.write_bytes(canonical_json(document).encode("utf-8"))


def _write_selection_canonical(path: Path, document: Any) -> None:
    _write_canonical(path, document)
    path.with_suffix(".sha256").write_bytes(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode("ascii"),
    )


def _complete_decision(
    tmp_path: Path,
    *,
    plan: CompletionPlan,
    artifacts: Path,
    candidate_set: Path,
) -> tuple[Path, Path]:
    listening = build_anchor_listening_bundle(
        plan=plan,
        candidate_set_path=candidate_set,
        artifacts_dir=artifacts,
        output_dir=(tmp_path / "listening").resolve(),
    )
    review = json.loads(listening.review_path.read_bytes())
    decision = {
        "format_version": 1,
        "protocol": "role-review-decision-v1",
        "phase": "anchor",
        "plan_sha256": review["plan_sha256"],
        "candidate_set_sha256": review["candidate_set_sha256"],
        "groups": [
            {
                "id": group["id"],
                "model": group["model"],
                "scenario": group["scenario"],
                "character": group["character"],
                "line": None,
                "role_epoch_sha256": group["role_epoch_sha256"],
                "group_sha256": hashlib.sha256(
                    canonical_json(group).encode("utf-8"),
                ).hexdigest(),
                "heard_candidate_ids": group["candidate_ids"][:2],
                "selected_candidate_id": group["candidate_ids"][0],
                "rubric": {
                    "content": "pass",
                    "prompt_leakage": "pass",
                    "reading": "not_applicable",
                    "pitch_accent": "not_applicable",
                    "gender": "pass",
                    "age": "pass",
                    "archetype": "pass",
                    "voice_identity": "pass",
                    "delivery": "not_applicable",
                    "naturalness_quality": 4,
                    "notes": "",
                },
                "confirmed": True,
            }
            for group in review["groups"]
        ],
        "role_reopen_requests": [],
    }
    decision_path = (tmp_path / "decision.json").resolve()
    _write_canonical(decision_path, decision)
    selection = finalize_anchor_selection(
        plan=plan,
        candidate_set_path=candidate_set,
        decision_path=decision_path,
        artifacts_dir=artifacts,
        output_dir=(tmp_path / "selection").resolve(),
    )
    assert selection.selected_count == 106
    assert (
        selection.selection_sha256
        == hashlib.sha256(selection.selection_path.read_bytes()).hexdigest()
    )
    return decision_path, selection.selection_path


def test_Phase_Aは53role_N4をrun_owned_pathへ生成する(
    tmp_path: Path,
    plan: CompletionPlan,
) -> None:
    artifacts = (tmp_path / "artifacts").resolve()
    generator = _generator(plan, QWEN_MODEL)

    summary = run_anchor_generation(
        plan=plan,
        model_id=QWEN_MODEL,
        run_id="qwen-run",
        artifacts_dir=artifacts,
        generator=generator,
    )

    assert summary.eligible_count == 53 * 4
    assert summary.rejected_count == 0
    assert summary.failed_count == 0
    assert generator.closed
    ledger = json.loads(summary.ledger_path.read_bytes())
    assert len(ledger["attempts"]) == 53 * 4
    assert len({item["seed"] for item in ledger["attempts"]}) == 53 * 4
    assert all(
        "emotion" not in canonical_json(item["generation_input"])
        and "intensity" not in canonical_json(item["generation_input"])
        for item in ledger["attempts"]
    )
    assert all(
        item["qc"]
        == {
            "mechanical": "pass",
            "content": "not_checked",
            "notes": [],
        }
        for item in ledger["attempts"]
    )

    with pytest.raises(CompletionAnchorError, match="新規"):
        run_anchor_generation(
            plan=plan,
            model_id=QWEN_MODEL,
            run_id="qwen-run",
            artifacts_dir=artifacts,
            generator=_generator(plan, QWEN_MODEL),
        )


def test_candidate_merge_review_selectionは106group完了後だけ成立する(
    tmp_path: Path,
    plan: CompletionPlan,
) -> None:
    artifacts, candidate_set = _build_complete_candidate_set(tmp_path, plan)
    decision_path, selection_path = _complete_decision(
        tmp_path,
        plan=plan,
        artifacts=artifacts,
        candidate_set=candidate_set,
    )

    review = json.loads((tmp_path / "listening" / "role-review-v1.json").read_bytes())
    assert set(review) == {
        "format_version",
        "protocol",
        "phase",
        "plan_sha256",
        "candidate_set_sha256",
        "groups",
    }
    assert len(review["groups"]) == 106
    assert all(group["line"] is None for group in review["groups"])
    assert all(len(group["candidates"]) == 4 for group in review["groups"])
    assert all(
        set(group["coverage"]) == {"gender", "age", "archetype"}
        for group in review["groups"]
    )
    assert {
        path.relative_to(tmp_path / "listening").as_posix()
        for path in (tmp_path / "listening").rglob("*")
        if path.is_file()
    } == {
        "role-review-v1.json",
        *{
            candidate["audio_path"]
            for group in review["groups"]
            for candidate in group["candidates"]
        },
    }

    decision = json.loads(decision_path.read_bytes())
    decision["groups"].pop()
    incomplete_path = (tmp_path / "incomplete-decision.json").resolve()
    _write_canonical(incomplete_path, decision)
    with pytest.raises(CompletionAnchorError, match="106"):
        finalize_anchor_selection(
            plan=plan,
            candidate_set_path=candidate_set,
            decision_path=incomplete_path,
            artifacts_dir=artifacts,
            output_dir=(tmp_path / "incomplete-selection").resolve(),
        )
    assert not (tmp_path / "incomplete-selection").exists()

    role = next(
        item
        for item in plan.roles
        if item.reference_voice is None
    )
    selected = resolve_selected_anchor(
        selection_path=selection_path,
        plan_sha256=plan.plan_id,
        model=QWEN_MODEL,
        model_revision=plan.models[QWEN_MODEL],
        role=role,
    )
    assert selected.anchor_id
    assert selected.audio_path.is_file()
    assert selected.role_epoch_sha256 != next(
        target.role_epoch_sha256
        for target in plan.anchor_targets_for_model(QWEN_MODEL)
        if (target.scenario, target.character) == role.identity
    )


def test_finalizeはpage_exportのhash_heard_rubric_confirmedを厳密検証する(
    tmp_path: Path,
    plan: CompletionPlan,
) -> None:
    artifacts, candidate_set = _build_complete_candidate_set(tmp_path, plan)
    decision_path, _selection_path = _complete_decision(
        tmp_path,
        plan=plan,
        artifacts=artifacts,
        candidate_set=candidate_set,
    )
    original = json.loads(decision_path.read_bytes())
    mutations = [
        (
            "group-hash",
            lambda document: document["groups"][0].__setitem__(
                "group_sha256",
                "0" * 64,
            ),
            "group_sha256",
        ),
        (
            "heard",
            lambda document: document["groups"][0].__setitem__(
                "heard_candidate_ids",
                document["groups"][0]["heard_candidate_ids"][:1],
            ),
            "2件",
        ),
        (
            "rubric",
            lambda document: document["groups"][0]["rubric"].pop("gender"),
            "exact contract",
        ),
        (
            "confirmed",
            lambda document: document["groups"][0].__setitem__(
                "confirmed",
                False,
            ),
            "confirmed=true",
        ),
    ]
    for name, mutate, match in mutations:
        document = json.loads(canonical_json(original))
        mutate(document)
        path = (tmp_path / f"{name}-decision.json").resolve()
        _write_canonical(path, document)
        with pytest.raises(CompletionAnchorError, match=match):
            finalize_anchor_selection(
                plan=plan,
                candidate_set_path=candidate_set,
                decision_path=path,
                artifacts_dir=artifacts,
                output_dir=(tmp_path / f"{name}-selection").resolve(),
            )

    reopen = json.loads(canonical_json(original))
    first = reopen["groups"][0]
    reopen["role_reopen_requests"] = [
        {
            "model": first["model"],
            "character": first["character"],
            "role_epoch_sha256": first["role_epoch_sha256"],
            "reason": "role指定を再確認する",
        },
    ]
    reopen_path = (tmp_path / "reopen-decision.json").resolve()
    _write_canonical(reopen_path, reopen)
    with pytest.raises(CompletionAnchorError, match="reopen request"):
        finalize_anchor_selection(
            plan=plan,
            candidate_set_path=candidate_set,
            decision_path=reopen_path,
            artifacts_dir=artifacts,
            output_dir=(tmp_path / "reopen-selection").resolve(),
        )


def test_finalizeは2candidateの手製decisionでもminimum_gateを迂回できない(
    tmp_path: Path,
    plan: CompletionPlan,
) -> None:
    artifacts, candidate_set = _build_complete_candidate_set(tmp_path, plan)
    decision_path, _selection_path = _complete_decision(
        tmp_path,
        plan=plan,
        artifacts=artifacts,
        candidate_set=candidate_set,
    )
    candidate_document = json.loads(candidate_set.read_bytes())
    candidate_group = candidate_document["groups"][0]
    candidate_group["candidates"] = candidate_group["candidates"][:2]
    deficient_path = (tmp_path / "two-candidate-set.json").resolve()
    _write_canonical(deficient_path, candidate_document)

    review = json.loads(
        (tmp_path / "listening" / "role-review-v1.json").read_bytes(),
    )
    review_group = next(
        group
        for group in review["groups"]
        if (
            group["model"],
            group["scenario"],
            group["character"],
        )
        == (
            candidate_group["model"],
            candidate_group["scenario"],
            candidate_group["character"],
        )
    )
    review_group["candidates"] = review_group["candidates"][:2]
    review_group["candidate_ids"] = [
        candidate["id"] for candidate in review_group["candidates"]
    ]
    review_group["provisional_candidate_id"] = review_group["candidate_ids"][0]

    decision = json.loads(decision_path.read_bytes())
    decision["candidate_set_sha256"] = hashlib.sha256(
        deficient_path.read_bytes(),
    ).hexdigest()
    decision_group = next(
        group
        for group in decision["groups"]
        if (
            group["model"],
            group["scenario"],
            group["character"],
        )
        == (
            candidate_group["model"],
            candidate_group["scenario"],
            candidate_group["character"],
        )
    )
    decision_group["group_sha256"] = hashlib.sha256(
        canonical_json(review_group).encode("utf-8"),
    ).hexdigest()
    decision_group["heard_candidate_ids"] = review_group["candidate_ids"]
    decision_group["selected_candidate_id"] = review_group["candidate_ids"][0]
    handcrafted_path = (tmp_path / "two-candidate-decision.json").resolve()
    _write_canonical(handcrafted_path, decision)

    with pytest.raises(CompletionAnchorError, match="3件以上"):
        finalize_anchor_selection(
            plan=plan,
            candidate_set_path=deficient_path,
            decision_path=handcrafted_path,
            artifacts_dir=artifacts,
            output_dir=(tmp_path / "two-candidate-selection").resolve(),
        )


def test_topupは新attemptだけを加え衝突を拒否する(
    tmp_path: Path,
    plan: CompletionPlan,
) -> None:
    artifacts, candidate_set = _build_complete_candidate_set(tmp_path, plan)
    document = json.loads(candidate_set.read_bytes())
    document["groups"][0]["candidates"] = document["groups"][0]["candidates"][:2]
    deficient_path = (tmp_path / "deficient.json").resolve()
    _write_canonical(deficient_path, document)
    topup_path = (tmp_path / "topup.json").resolve()
    summary = build_anchor_topup_plan(
        plan=plan,
        candidate_set_path=deficient_path,
        output_path=topup_path,
    )
    assert summary.target_count == 1
    topup = json.loads(topup_path.read_bytes())
    assert topup["targets"][0]["attempt"] == 5

    deficient_group = document["groups"][0]
    sufficient_group = next(
        group
        for group in document["groups"][1:]
        if len(group["candidates"])
        >= plan.phase_a_minimum_eligible_candidates
    )

    def target_for(group: dict[str, Any], attempt: int) -> dict[str, Any]:
        return {
            "model": group["model"],
            "scenario": group["scenario"],
            "character": group["character"],
            "role_identity_sha256": group["role_identity_sha256"],
            "role_epoch_sha256": group["role_epoch_sha256"],
            "attempt": attempt,
            "seed": derive_anchor_seed(
                plan_sha256=plan.plan_id,
                seed_base=plan.phase_a_seed_base,
                model=group["model"],
                scenario=group["scenario"],
                character=group["character"],
                attempt=attempt,
            ),
        }

    sufficient = json.loads(canonical_json(topup))
    sufficient["targets"] = [target_for(sufficient_group, 5)]
    extra_count = json.loads(canonical_json(topup))
    extra_count["targets"].append(target_for(deficient_group, 6))
    gap = json.loads(canonical_json(topup))
    gap["targets"] = [target_for(deficient_group, 100)]
    for tampered in (sufficient, extra_count, gap):
        with pytest.raises(CompletionAnchorError, match="exact deficit"):
            validate_anchor_topup_plan(
                tampered,
                plan=plan,
                candidate_set=document,
            )

    topup["targets"][0]["attempt"] = 4
    conflict_path = (tmp_path / "conflict-topup.json").resolve()
    _write_canonical(conflict_path, topup)
    with pytest.raises(CompletionAnchorError, match="N4"):
        validate_anchor_topup_plan(
            json.loads(conflict_path.read_bytes()),
            plan=plan,
            candidate_set=json.loads(deficient_path.read_bytes()),
        )

    target_model = topup["targets"][0]["model"]
    with pytest.raises(CompletionAnchorError, match="source set"):
        run_anchor_generation(
            plan=plan,
            model_id=target_model,
            run_id="stale-topup",
            artifacts_dir=artifacts,
            generator=_generator(plan, target_model),
            topup_plan_path=topup_path,
            candidate_set_path=candidate_set,
        )


def test_topup失敗後もattempt履歴から次slotを割り当て全runをmergeできる(
    tmp_path: Path,
    plan: CompletionPlan,
) -> None:
    artifacts = (tmp_path / "artifacts").resolve()
    target = plan.anchor_targets_for_model(QWEN_MODEL)[0]

    def target_seed(attempt: int) -> int:
        return derive_anchor_seed(
            plan_sha256=plan.plan_id,
            seed_base=plan.phase_a_seed_base,
            model=target.model,
            scenario=target.scenario,
            character=target.character,
            attempt=attempt,
        )

    initial = run_anchor_generation(
        plan=plan,
        model_id=QWEN_MODEL,
        run_id="qwen-initial-with-failures",
        artifacts_dir=artifacts,
        generator=_generator(
            plan,
            QWEN_MODEL,
            fail_seeds=frozenset({target_seed(1), target_seed(2)}),
        ),
    )
    assert initial.failed_count == 2
    run_anchor_generation(
        plan=plan,
        model_id=IRODORI_MODEL,
        run_id="irodori-initial",
        artifacts_dir=artifacts,
        generator=_generator(plan, IRODORI_MODEL),
    )

    first_set = (tmp_path / "candidate-set-first.json").resolve()
    merge_anchor_runs(
        plan=plan,
        run_ids=["qwen-initial-with-failures", "irodori-initial"],
        artifacts_dir=artifacts,
        output_path=first_set,
    )
    first_document = json.loads(first_set.read_bytes())
    first_group = next(
        group
        for group in first_document["groups"]
        if (
            group["model"],
            group["scenario"],
            group["character"],
        )
        == target.identity
    )
    assert first_group["attempts"] == [1, 2, 3, 4]
    assert [candidate["attempt"] for candidate in first_group["candidates"]] == [
        3,
        4,
    ]

    first_topup = (tmp_path / "topup-first.json").resolve()
    build_anchor_topup_plan(
        plan=plan,
        candidate_set_path=first_set,
        output_path=first_topup,
    )
    assert json.loads(first_topup.read_bytes())["targets"][0]["attempt"] == 5
    failed_topup = run_anchor_generation(
        plan=plan,
        model_id=QWEN_MODEL,
        run_id="qwen-topup-failed",
        artifacts_dir=artifacts,
        generator=_generator(
            plan,
            QWEN_MODEL,
            fail_seeds=frozenset({target_seed(5)}),
        ),
        topup_plan_path=first_topup,
        candidate_set_path=first_set,
    )
    assert failed_topup.failed_count == 1
    failed_attempt = json.loads(failed_topup.ledger_path.read_bytes())["attempts"][0]
    assert failed_attempt["qc"] == {
        "mechanical": "fail",
        "content": "not_checked",
        "notes": ["generation_failed"],
    }

    second_set = (tmp_path / "candidate-set-second.json").resolve()
    first_three_runs = [
        "qwen-initial-with-failures",
        "irodori-initial",
        "qwen-topup-failed",
    ]
    merge_anchor_runs(
        plan=plan,
        run_ids=first_three_runs,
        artifacts_dir=artifacts,
        output_path=second_set,
    )
    second_document = json.loads(second_set.read_bytes())
    second_group = next(
        group
        for group in second_document["groups"]
        if (
            group["model"],
            group["scenario"],
            group["character"],
        )
        == target.identity
    )
    assert second_group["attempts"] == [1, 2, 3, 4, 5]
    assert [candidate["attempt"] for candidate in second_group["candidates"]] == [
        3,
        4,
    ]

    second_topup = (tmp_path / "topup-second.json").resolve()
    build_anchor_topup_plan(
        plan=plan,
        candidate_set_path=second_set,
        output_path=second_topup,
    )
    assert json.loads(second_topup.read_bytes())["targets"][0]["attempt"] == 6
    run_anchor_generation(
        plan=plan,
        model_id=QWEN_MODEL,
        run_id="qwen-topup-success",
        artifacts_dir=artifacts,
        generator=_generator(plan, QWEN_MODEL),
        topup_plan_path=second_topup,
        candidate_set_path=second_set,
    )

    final_set = (tmp_path / "candidate-set-final.json").resolve()
    all_runs = [*first_three_runs, "qwen-topup-success"]
    merge_anchor_runs(
        plan=plan,
        run_ids=all_runs,
        artifacts_dir=artifacts,
        output_path=final_set,
    )
    final_document = json.loads(final_set.read_bytes())
    assert final_document["runs"] == sorted(all_runs)
    final_group = next(
        group
        for group in final_document["groups"]
        if (
            group["model"],
            group["scenario"],
            group["character"],
        )
        == target.identity
    )
    assert final_group["attempts"] == [1, 2, 3, 4, 5, 6]
    assert [candidate["attempt"] for candidate in final_group["candidates"]] == [
        3,
        4,
        6,
    ]


def test_selectionはrevision_identity_decision_WAV改ざんを拒否する(
    tmp_path: Path,
    plan: CompletionPlan,
) -> None:
    artifacts, candidate_set = _build_complete_candidate_set(tmp_path, plan)
    _decision_path, selection_path = _complete_decision(
        tmp_path,
        plan=plan,
        artifacts=artifacts,
        candidate_set=candidate_set,
    )
    original = json.loads(selection_path.read_bytes())
    group = original["groups"][0]
    role = plan.role(group["scenario"], group["character"])

    stale = json.loads(selection_path.read_bytes())
    stale_group = stale["groups"][0]
    stale_group["model_revision"] += "-stale"
    stale_group["role_epoch_sha256"] = hashlib.sha256(
        canonical_json(
            {
                "protocol": "selected-role-epoch-v1",
                "model": stale_group["model"],
                "model_revision": stale_group["model_revision"],
                "scenario": stale_group["scenario"],
                "character": stale_group["character"],
                "role_identity_sha256": stale_group["role_identity_sha256"],
                "review_role_epoch_sha256": stale_group[
                    "review_role_epoch_sha256"
                ],
                "anchor_id": stale_group["anchor_id"],
                "audio_sha256": stale_group["audio_sha256"],
                "decision_sha256": stale_group["decision_sha256"],
            },
        ).encode("utf-8"),
    ).hexdigest()
    stale_path = selection_path.parent / "stale-selection.json"
    _write_selection_canonical(stale_path, stale)
    with pytest.raises(CompletionAnchorError, match="model revision"):
        resolve_selected_anchor(
            selection_path=stale_path,
            plan_sha256=plan.plan_id,
            model=group["model"],
            model_revision=plan.models[group["model"]],
            role=role,
        )

    identity = json.loads(selection_path.read_bytes())
    identity_group = identity["groups"][0]
    identity_group["role_identity"]["role"]["name"] += "改"
    identity_path = selection_path.parent / "identity-selection.json"
    _write_selection_canonical(identity_path, identity)
    with pytest.raises(CompletionAnchorError, match="role identity SHA"):
        resolve_selected_anchor(
            selection_path=identity_path,
            plan_sha256=plan.plan_id,
            model=group["model"],
            model_revision=plan.models[group["model"]],
            role=role,
        )

    wrong_plan = json.loads(selection_path.read_bytes())
    wrong_plan["plan_sha256"] = "0" * 64
    wrong_plan_path = selection_path.parent / "wrong-plan-selection.json"
    _write_selection_canonical(wrong_plan_path, wrong_plan)
    with pytest.raises(CompletionAnchorError, match="frozen plan"):
        resolve_selected_anchor(
            selection_path=wrong_plan_path,
            plan_sha256=plan.plan_id,
            model=group["model"],
            model_revision=plan.models[group["model"]],
            role=role,
        )

    missing_marker_path = selection_path.parent / "missing-marker-selection.json"
    _write_canonical(missing_marker_path, original)
    with pytest.raises(CompletionAnchorError, match="marker"):
        resolve_selected_anchor(
            selection_path=missing_marker_path,
            plan_sha256=plan.plan_id,
            model=group["model"],
            model_revision=plan.models[group["model"]],
            role=role,
        )

    marker_path = selection_path.with_suffix(".sha256")
    original_marker = marker_path.read_bytes()
    marker_path.write_text(f"{'0' * 64}\n", encoding="ascii")
    try:
        with pytest.raises(CompletionAnchorError, match="marker"):
            resolve_selected_anchor(
                selection_path=selection_path,
                plan_sha256=plan.plan_id,
                model=group["model"],
                model_revision=plan.models[group["model"]],
                role=role,
            )
    finally:
        marker_path.write_bytes(original_marker)

    decision = json.loads(selection_path.read_bytes())
    decision["groups"][0]["decision"]["rubric"]["notes"] = "tampered"
    decision_path = selection_path.parent / "decision-selection.json"
    _write_canonical(decision_path, decision)
    with pytest.raises(CompletionAnchorError, match="decision SHA"):
        validate_anchor_selection(json.loads(decision_path.read_bytes()))

    selected_audio = selection_path.parent / Path(group["audio_path"])
    original_audio = selected_audio.read_bytes()
    selected_audio.write_bytes(original_audio + b"tampered")
    try:
        with pytest.raises(CompletionAnchorError, match="SHA-256"):
            resolve_selected_anchor(
                selection_path=selection_path,
                plan_sha256=plan.plan_id,
                model=group["model"],
                model_revision=plan.models[group["model"]],
                role=role,
            )
    finally:
        selected_audio.write_bytes(original_audio)


def test_role_snapshot_reference有りはselected_anchorで解決できない(
    tmp_path: Path,
    plan: CompletionPlan,
) -> None:
    artifacts, candidate_set = _build_complete_candidate_set(tmp_path, plan)
    _decision_path, selection_path = _complete_decision(
        tmp_path,
        plan=plan,
        artifacts=artifacts,
        candidate_set=candidate_set,
    )
    explicit = next(role for role in plan.roles if role.reference_voice is not None)
    no_ref = next(role for role in plan.roles if role.reference_voice is None)
    forged = replace(
        no_ref,
        reference_voice=explicit.reference_voice,
    )

    with pytest.raises(CompletionAnchorError, match="role identity"):
        resolve_selected_anchor(
            selection_path=selection_path,
            plan_sha256=plan.plan_id,
            model=QWEN_MODEL,
            model_revision=plan.models[QWEN_MODEL],
            role=forged,
        )
