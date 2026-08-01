from __future__ import annotations

import hashlib
import json
import struct
import wave
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from gaya_pipeline.adapters.irodori_tts import ROLE_ANCHOR_TEXT
from gaya_pipeline.adapters.qwen3_tts import REFERENCE_TEXT
from gaya_pipeline.completion_anchor import (
    CompletionAnchorError,
    build_role_anchor_topup_draft,
    build_role_anchor_topup_plan,
    build_role_review_bundle_v2,
    finalize_role_anchor_selection,
    merge_role_anchor_topup,
    run_role_anchor_topup_generation,
    validate_anchor_selection,
)
from gaya_pipeline.completion_plan import IRODORI_MODEL, QWEN_MODEL, RoleSnapshot
from gaya_pipeline.take_identity import canonical_json

MODELS = (IRODORI_MODEL, QWEN_MODEL)
MODEL_REVISIONS = {
    IRODORI_MODEL: "irodori-test-revision",
    QWEN_MODEL: "qwen-test-revision",
}
ANCHOR_TEXTS = {
    IRODORI_MODEL: ROLE_ANCHOR_TEXT,
    QWEN_MODEL: REFERENCE_TEXT,
}


@dataclass(frozen=True)
class _Plan:
    anchor_source_plan_sha256: str
    anchor_source_candidate_set_sha256: str
    anchor_candidate_set_sha256: str
    models: dict[str, str]
    roles: tuple[RoleSnapshot, ...]

    def role(self, scenario: str, character: str) -> RoleSnapshot:
        matches = [
            role
            for role in self.roles
            if (role.scenario, role.character) == (scenario, character)
        ]
        assert len(matches) == 1
        return matches[0]


@dataclass(frozen=True)
class _Fixture:
    plan: _Plan
    candidate_set_path: Path
    artifacts_dir: Path


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256(canonical_json(value).encode("utf-8"))


def _write_canonical(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(document).encode("utf-8"))


def _roles() -> tuple[RoleSnapshot, ...]:
    roles: list[RoleSnapshot] = []
    for index in range(53):
        scenario = f"scene-{index:02d}"
        character = f"role-{index:02d}"
        role = {
            "name": f"役{index}",
            "kind": "human",
            "gender": "neutral" if index % 3 == 0 else "female",
            "age": "adult",
            "archetype": "案内役",
            "voice": "明瞭な声",
            "personality": "落ち着いている",
        }
        identity = {
            "scenario": scenario,
            "character": character,
            "role": role,
            "reference_voice": None,
            "scene_setting": "静かな広場",
        }
        roles.append(
            RoleSnapshot(
                scenario=scenario,
                character=character,
                role=role,
                reference_voice=None,
                scene_setting="静かな広場",
                role_identity_sha256=_canonical_sha256(identity),
            ),
        )
    return tuple(roles)


def _fixture(tmp_path: Path) -> _Fixture:
    source_plan_sha256 = _sha256(b"anchor-source-plan")
    artifacts_dir = (tmp_path / "artifacts").resolve()
    artifacts_dir.mkdir()
    roles = _roles()
    groups: list[dict[str, Any]] = []
    candidate_number = 0
    for model in MODELS:
        revision = MODEL_REVISIONS[model]
        anchor_text_sha256 = _sha256(ANCHOR_TEXTS[model].encode("utf-8"))
        for role in roles:
            role_epoch_sha256 = _canonical_sha256(
                {
                    "model": model,
                    "model_revision": revision,
                    "scenario": role.scenario,
                    "character": role.character,
                    "role_identity_sha256": role.role_identity_sha256,
                    "anchor_text_sha256": anchor_text_sha256,
                },
            )
            candidates: list[dict[str, Any]] = []
            for attempt in range(1, 5):
                candidate_number += 1
                candidate_id = _sha256(
                    f"{model}/{role.scenario}/{role.character}/{attempt}".encode(),
                )
                relative = (
                    f"role-anchors/runs/run-{model}/{model}/"
                    f"{role.scenario}/{role.character}/attempt-{attempt:04d}.wav"
                )
                audio = f"RIFF-{candidate_id}".encode()
                audio_path = artifacts_dir / Path(*relative.split("/"))
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(audio)
                candidates.append(
                    {
                        "id": candidate_id,
                        "model": model,
                        "model_revision": revision,
                        "scenario": role.scenario,
                        "character": role.character,
                        "role_identity_sha256": role.role_identity_sha256,
                        "role_epoch_sha256": role_epoch_sha256,
                        "attempt": attempt,
                        "seed": candidate_number,
                        "audio_path": relative,
                        "audio_sha256": _sha256(audio),
                        "generation_input_sha256": _sha256(
                            f"input-{candidate_id}".encode(),
                        ),
                        "qc": {
                            "mechanical": "pass",
                            "content": "not_checked",
                            "notes": [],
                        },
                    },
                )
            groups.append(
                {
                    "model": model,
                    "model_revision": revision,
                    "scenario": role.scenario,
                    "character": role.character,
                    "role_identity_sha256": role.role_identity_sha256,
                    "role_epoch_sha256": role_epoch_sha256,
                    "attempts": [1, 2, 3, 4],
                    "candidates": candidates,
                },
            )
    candidate_set = {
        "format_version": 1,
        "protocol": "role-anchor-candidate-set-v1",
        "plan_sha256": source_plan_sha256,
        "runs": sorted(f"run-{model}" for model in MODELS),
        "groups": groups,
    }
    candidate_set_path = (tmp_path / "candidate-set.json").resolve()
    _write_canonical(candidate_set_path, candidate_set)
    plan = _Plan(
        anchor_source_plan_sha256=source_plan_sha256,
        anchor_source_candidate_set_sha256=_sha256(candidate_set_path.read_bytes()),
        anchor_candidate_set_sha256=_sha256(candidate_set_path.read_bytes()),
        models=dict(MODEL_REVISIONS),
        roles=roles,
    )
    return _Fixture(
        plan=plan,
        candidate_set_path=candidate_set_path,
        artifacts_dir=artifacts_dir,
    )


def _build_bundle(fixture: _Fixture, tmp_path: Path) -> Path:
    bundle_dir = (tmp_path / "bundle").resolve()
    summary = build_role_review_bundle_v2(
        plan=fixture.plan,  # type: ignore[arg-type]
        candidate_set_path=fixture.candidate_set_path,
        artifacts_dir=fixture.artifacts_dir,
        output_dir=bundle_dir,
    )
    assert summary.group_count == 106
    assert summary.candidate_count == 424
    return bundle_dir


def _decision(bundle_dir: Path) -> dict[str, Any]:
    bundle = json.loads((bundle_dir / "role-review-v2.json").read_bytes())
    return {
        "format_version": 2,
        "protocol": "role-review-decision-v2",
        "phase": "anchor",
        "plan_sha256": bundle["plan_sha256"],
        "candidate_set_sha256": bundle["candidate_set_sha256"],
        "groups": [
            {
                "id": group["id"],
                "model": group["model"],
                "scenario": group["scenario"],
                "character": group["character"],
                "line": None,
                "role_epoch_sha256": group["role_epoch_sha256"],
                "group_sha256": _canonical_sha256(group),
                "heard_candidate_ids": list(group["candidate_ids"]),
                "selected_candidate_id": group["candidate_ids"][1],
                "no_usable_candidate": False,
                "rubric": {
                    "content": "pass",
                    "prompt_leakage": "pass",
                    "reading": "pass",
                    "pitch_accent": "pass",
                    "gender": "pass",
                    "age": "pass",
                    "archetype": "pass",
                    "voice_identity": "not_applicable",
                    "delivery": "not_applicable",
                    "naturalness_quality": 4,
                    "notes": "",
                },
                "confirmed": True,
            }
            for group in bundle["groups"]
        ],
    }


def _write_decision(path: Path, decision: dict[str, Any]) -> None:
    _write_canonical(path, decision)
    path.with_suffix(".sha256").write_bytes(
        f"{_sha256(path.read_bytes())}\n".encode("ascii"),
    )


def test_v2_bundleとselectionを厳密に構築する(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    bundle_dir = _build_bundle(fixture, tmp_path)
    bundle_files = {
        path.relative_to(bundle_dir).as_posix()
        for path in bundle_dir.rglob("*")
        if path.is_file()
    }
    assert len(bundle_files) == 425
    assert "role-review-v2.json" in bundle_files

    decision_path = (
        tmp_path / "decision" / "role-review-anchor-decision-v2.json"
    ).resolve()
    _write_decision(decision_path, _decision(bundle_dir))
    output_dir = (tmp_path / "selection").resolve()
    summary = finalize_role_anchor_selection(
        plan=fixture.plan,  # type: ignore[arg-type]
        candidate_set_path=fixture.candidate_set_path,
        bundle_dir=bundle_dir,
        decision_path=decision_path,
        output_dir=output_dir,
    )

    assert summary.selected_count == 106
    selection = validate_anchor_selection(
        json.loads(summary.selection_path.read_bytes()),
    )
    assert len(selection["groups"]) == 106
    assert len(list((output_dir / "audio").glob("*.wav"))) == 106
    assert (output_dir / "role-anchor-selection-v1.sha256").read_text() == (
        f"{summary.selection_sha256}\n"
    )


def test_noncanonical_candidate_setを拒否してoutputを残さない(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    document = json.loads(fixture.candidate_set_path.read_bytes())
    fixture.candidate_set_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_dir = (tmp_path / "bundle").resolve()

    with pytest.raises(CompletionAnchorError, match="canonical bytes"):
        build_role_review_bundle_v2(
            plan=fixture.plan,  # type: ignore[arg-type]
            candidate_set_path=fixture.candidate_set_path,
            artifacts_dir=fixture.artifacts_dir,
            output_dir=output_dir,
        )
    assert not output_dir.exists()


def test_source_audio欠落を拒否してoutputを残さない(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    candidate_set = json.loads(fixture.candidate_set_path.read_bytes())
    relative = candidate_set["groups"][0]["candidates"][0]["audio_path"]
    (fixture.artifacts_dir / Path(*relative.split("/"))).unlink()
    output_dir = (tmp_path / "bundle").resolve()

    with pytest.raises(CompletionAnchorError, match="ありません"):
        build_role_review_bundle_v2(
            plan=fixture.plan,  # type: ignore[arg-type]
            candidate_set_path=fixture.candidate_set_path,
            artifacts_dir=fixture.artifacts_dir,
            output_dir=output_dir,
        )
    assert not output_dir.exists()


def test_model内の重複role座標で欠落を隠せない(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    candidate_set = json.loads(fixture.candidate_set_path.read_bytes())
    qwen_indexes = [
        index
        for index, group in enumerate(candidate_set["groups"])
        if group["model"] == QWEN_MODEL
    ]
    candidate_set["groups"][qwen_indexes[0]] = deepcopy(
        candidate_set["groups"][qwen_indexes[1]],
    )
    _write_canonical(fixture.candidate_set_path, candidate_set)
    plan = replace(
        fixture.plan,
        anchor_candidate_set_sha256=_sha256(
            fixture.candidate_set_path.read_bytes(),
        ),
    )

    with pytest.raises(CompletionAnchorError, match="model内のrole座標が重複"):
        build_role_review_bundle_v2(
            plan=plan,  # type: ignore[arg-type]
            candidate_set_path=fixture.candidate_set_path,
            artifacts_dir=fixture.artifacts_dir,
            output_dir=(tmp_path / "bundle").resolve(),
        )


@pytest.mark.parametrize("mutation", ["heard", "group_hash"])
def test_decisionの全4件聴取とgroup_bindingを強制する(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _fixture(tmp_path)
    bundle_dir = _build_bundle(fixture, tmp_path)
    decision = _decision(bundle_dir)
    if mutation == "heard":
        decision["groups"][0]["heard_candidate_ids"].pop()
    else:
        decision["groups"][0]["group_sha256"] = "0" * 64
    decision_path = (
        tmp_path / "decision" / "role-review-anchor-decision-v2.json"
    ).resolve()
    _write_decision(decision_path, decision)

    with pytest.raises(CompletionAnchorError, match="bundleと一致"):
        finalize_role_anchor_selection(
            plan=fixture.plan,  # type: ignore[arg-type]
            candidate_set_path=fixture.candidate_set_path,
            bundle_dir=bundle_dir,
            decision_path=decision_path,
            output_dir=(tmp_path / "selection").resolve(),
        )


def test_final_rubricのapplicableとnot_applicable境界を強制する(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    bundle_dir = _build_bundle(fixture, tmp_path)
    mutations = [
        *(
            (field, "not_applicable", "許可値")
            for field in (
                "content",
                "prompt_leakage",
                "reading",
                "pitch_accent",
                "gender",
                "age",
                "archetype",
            )
        ),
        ("voice_identity", "pass", "not_applicable"),
        ("delivery", "fail", "not_applicable"),
    ]
    for index, (field, value, match) in enumerate(mutations):
        decision = _decision(bundle_dir)
        decision["groups"][0]["rubric"][field] = value
        decision_path = (
            tmp_path
            / f"decision-rubric-{index}"
            / "role-review-anchor-decision-v2.json"
        ).resolve()
        _write_decision(decision_path, decision)
        with pytest.raises(CompletionAnchorError, match=match):
            finalize_role_anchor_selection(
                plan=fixture.plan,  # type: ignore[arg-type]
                candidate_set_path=fixture.candidate_set_path,
                bundle_dir=bundle_dir,
                decision_path=decision_path,
                output_dir=(tmp_path / f"selection-rubric-{index}").resolve(),
            )


def test_no_usable_decisionを保存可能だがselection確定は拒否する(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    bundle_dir = _build_bundle(fixture, tmp_path)
    decision = _decision(bundle_dir)
    blocked_groups = decision["groups"][:2]
    for blocked in blocked_groups:
        blocked["no_usable_candidate"] = True
        blocked["selected_candidate_id"] = None
    decision_path = (
        tmp_path / "decision-blocked" / "role-review-anchor-decision-v2.json"
    ).resolve()
    _write_decision(decision_path, decision)
    output_dir = (tmp_path / "selection-blocked").resolve()

    with pytest.raises(CompletionAnchorError) as caught:
        finalize_role_anchor_selection(
            plan=fixture.plan,  # type: ignore[arg-type]
            candidate_set_path=fixture.candidate_set_path,
            bundle_dir=bundle_dir,
            decision_path=decision_path,
            output_dir=output_dir,
        )
    message = str(caught.value)
    assert "使用可能なAnchor候補がない" in message
    for blocked in blocked_groups:
        assert blocked["id"] in message
        assert (
            f"{blocked['model']}/{blocked['scenario']}/{blocked['character']}"
            in message
        )
    assert not output_dir.exists()


def test_gender不一致のselected_anchorを拒否する(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    bundle_dir = _build_bundle(fixture, tmp_path)
    decision = _decision(bundle_dir)
    decision["groups"][0]["rubric"]["gender"] = "fail"
    decision_path = (
        tmp_path / "decision-gender-fail" / "role-review-anchor-decision-v2.json"
    ).resolve()
    _write_decision(decision_path, decision)
    output_dir = (tmp_path / "selection-gender-fail").resolve()

    with pytest.raises(CompletionAnchorError, match="gender=pass"):
        finalize_role_anchor_selection(
            plan=fixture.plan,  # type: ignore[arg-type]
            candidate_set_path=fixture.candidate_set_path,
            bundle_dir=bundle_dir,
            decision_path=decision_path,
            output_dir=output_dir,
        )
    assert not output_dir.exists()


def test_no_usableとselected_candidateの排他的contractを強制する(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    bundle_dir = _build_bundle(fixture, tmp_path)
    mutations = ("blocked_with_selection", "selected_without_selection", "missing")
    for index, mutation in enumerate(mutations):
        decision = _decision(bundle_dir)
        group = decision["groups"][0]
        if mutation == "blocked_with_selection":
            group["no_usable_candidate"] = True
        elif mutation == "selected_without_selection":
            group["selected_candidate_id"] = None
        else:
            del group["no_usable_candidate"]
        decision_path = (
            tmp_path
            / f"decision-no-usable-{index}"
            / "role-review-anchor-decision-v2.json"
        ).resolve()
        _write_decision(decision_path, decision)

        with pytest.raises(CompletionAnchorError):
            finalize_role_anchor_selection(
                plan=fixture.plan,  # type: ignore[arg-type]
                candidate_set_path=fixture.candidate_set_path,
                bundle_dir=bundle_dir,
                decision_path=decision_path,
                output_dir=(tmp_path / f"selection-no-usable-{index}").resolve(),
            )


def test_noncanonical_decisionとbundle_audio欠落を拒否する(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    bundle_dir = _build_bundle(fixture, tmp_path)
    decision = _decision(bundle_dir)
    decision_path = (
        tmp_path / "decision" / "role-review-anchor-decision-v2.json"
    ).resolve()
    decision_path.parent.mkdir()
    decision_path.write_text(
        json.dumps(decision, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    decision_path.with_suffix(".sha256").write_text(
        f"{_sha256(decision_path.read_bytes())}\n",
        encoding="ascii",
    )
    with pytest.raises(CompletionAnchorError, match="canonical bytes"):
        finalize_role_anchor_selection(
            plan=fixture.plan,  # type: ignore[arg-type]
            candidate_set_path=fixture.candidate_set_path,
            bundle_dir=bundle_dir,
            decision_path=decision_path,
            output_dir=(tmp_path / "selection-a").resolve(),
        )

    candidate_id = decision["groups"][0]["heard_candidate_ids"][0]
    (bundle_dir / "audio" / f"{candidate_id}.wav").unlink()
    canonical_decision_path = (
        tmp_path / "decision-b" / "role-review-anchor-decision-v2.json"
    ).resolve()
    _write_decision(canonical_decision_path, decision)
    with pytest.raises(CompletionAnchorError, match="treeがexact contract"):
        finalize_role_anchor_selection(
            plan=fixture.plan,  # type: ignore[arg-type]
            candidate_set_path=fixture.candidate_set_path,
            bundle_dir=bundle_dir,
            decision_path=canonical_decision_path,
            output_dir=(tmp_path / "selection-b").resolve(),
        )


class _TopupGenerator:
    def __init__(self, model: str, *, fail_on_call: int | None = None) -> None:
        self.profile = SimpleNamespace(
            id=model,
            version=MODEL_REVISIONS[model],
        )
        self.closed = False
        self.calls = 0
        self.fail_on_call = fail_on_call

    def role_anchor_generation_input(self, role: RoleSnapshot) -> dict[str, Any]:
        return {"role_identity_sha256": role.role_identity_sha256}

    def generate_role_anchor(
        self,
        role: RoleSnapshot,
        *,
        seed: int,
        output_wav: Path,
    ) -> dict[str, Any]:
        del role
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("synthetic topup generation failure")
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        samples = [1200 if index % 2 else -1200 for index in range(4_800)]
        with wave.open(str(output_wav), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16_000)
            wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        return {"seed": seed, "sample_rate_hz": 16_000}

    def close_role_anchor_generation(self) -> None:
        self.closed = True


def test_topupはdecision由来targetをattempt5から8で整組置換しdraftを継承する(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    bundle_dir = _build_bundle(fixture, tmp_path)
    decision = _decision(bundle_dir)
    first, second = decision["groups"][:2]
    first["no_usable_candidate"] = True
    first["selected_candidate_id"] = None
    second["rubric"]["gender"] = "fail"
    decision_path = (
        tmp_path / "topup-decision" / "role-review-anchor-decision-v2.json"
    ).resolve()
    _write_decision(decision_path, decision)
    topup_plan_path = (tmp_path / "anchor-topup.json").resolve()
    topup_summary = build_role_anchor_topup_plan(
        plan=fixture.plan,  # type: ignore[arg-type]
        candidate_set_path=fixture.candidate_set_path,
        decision_path=decision_path,
        output_path=topup_plan_path,
    )
    topup = json.loads(topup_plan_path.read_bytes())
    assert topup_summary.target_count == 2
    assert topup_summary.attempt_count == 8
    assert all(target["attempts"] == [5, 6, 7, 8] for target in topup["targets"])
    assert len(
        {
            seed
            for target in topup["targets"]
            for seed in target["seeds"]
        },
    ) == 8

    model = topup["targets"][0]["model"]
    generator = _TopupGenerator(model)
    generation = run_role_anchor_topup_generation(
        plan=fixture.plan,  # type: ignore[arg-type]
        candidate_set_path=fixture.candidate_set_path,
        decision_path=decision_path,
        topup_plan_path=topup_plan_path,
        model_id=model,
        run_id="topup-irodori",
        artifacts_dir=fixture.artifacts_dir,
        generator=generator,  # type: ignore[arg-type]
    )
    assert generation.eligible_count == 8
    assert generation.failed_count == 0
    assert generator.closed
    ledger = json.loads(generation.ledger_path.read_bytes())
    assert {attempt["attempt"] for attempt in ledger["attempts"]} == {5, 6, 7, 8}
    assert not generation.ledger_path.parent.with_name(
        f".{generation.run_id}.pending",
    ).exists()

    source = json.loads(fixture.candidate_set_path.read_bytes())
    untouched_identity = (
        source["groups"][2]["model"],
        source["groups"][2]["scenario"],
        source["groups"][2]["character"],
    )
    untouched_before = deepcopy(source["groups"][2])
    merged_path = (tmp_path / "merged-candidate-set.json").resolve()
    wrong_artifacts_dir = (tmp_path / "wrong-artifacts").resolve()
    wrong_artifacts_dir.mkdir()
    wrong_output_path = (tmp_path / "wrong-merged-candidate-set.json").resolve()
    with pytest.raises(CompletionAnchorError, match="source anchor candidate audio"):
        merge_role_anchor_topup(
            plan=fixture.plan,  # type: ignore[arg-type]
            candidate_set_path=fixture.candidate_set_path,
            decision_path=decision_path,
            topup_plan_path=topup_plan_path,
            run_ids=[generation.run_id],
            artifacts_dir=wrong_artifacts_dir,
            output_path=wrong_output_path,
        )
    assert not wrong_output_path.exists()

    merged_summary = merge_role_anchor_topup(
        plan=fixture.plan,  # type: ignore[arg-type]
        candidate_set_path=fixture.candidate_set_path,
        decision_path=decision_path,
        topup_plan_path=topup_plan_path,
        run_ids=[generation.run_id],
        artifacts_dir=fixture.artifacts_dir,
        output_path=merged_path,
    )
    merged = json.loads(merged_path.read_bytes())
    assert merged_summary.candidate_count == 106 * 4
    assert merged["runs"] == sorted([*source["runs"], generation.run_id])
    untouched_after = next(
        group
        for group in merged["groups"]
        if (group["model"], group["scenario"], group["character"])
        == untouched_identity
    )
    assert untouched_after == untouched_before
    target_identities = {
        (target["model"], target["scenario"], target["character"])
        for target in topup["targets"]
    }
    assert all(
        group["attempts"] == [5, 6, 7, 8]
        and [candidate["attempt"] for candidate in group["candidates"]]
        == [5, 6, 7, 8]
        for group in merged["groups"]
        if (group["model"], group["scenario"], group["character"])
        in target_identities
    )

    merged_plan = replace(
        fixture.plan,
        anchor_candidate_set_sha256=merged_summary.candidate_set_sha256,
    )
    merged_bundle_dir = (tmp_path / "merged-bundle").resolve()
    build_role_review_bundle_v2(
        plan=merged_plan,  # type: ignore[arg-type]
        candidate_set_path=merged_path,
        artifacts_dir=fixture.artifacts_dir,
        output_dir=merged_bundle_dir,
    )
    draft_path = (tmp_path / "inherited-draft.json").resolve()
    draft_summary = build_role_anchor_topup_draft(
        plan=fixture.plan,  # type: ignore[arg-type]
        source_candidate_set_path=fixture.candidate_set_path,
        decision_path=decision_path,
        topup_plan_path=topup_plan_path,
        merged_candidate_set_path=merged_path,
        merged_bundle_dir=merged_bundle_dir,
        output_path=draft_path,
    )
    draft = json.loads(draft_path.read_bytes())
    assert draft_summary.inherited_count == 104
    assert draft_summary.reset_count == 2
    assert draft["current_group_id"] == draft["groups"][0]["id"]
    assert all(
        group["confirmed"] is False
        and group["heard_candidate_ids"] == []
        and group["selected_candidate_id"] is None
        and group["rubric"]["gender"] is None
        for group in draft["groups"][:2]
    )
    assert draft["groups"][2] == decision["groups"][2]


def test_topup生成は二対象目の例外でpendingを消して同じrun_idを再利用できる(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    bundle_dir = _build_bundle(fixture, tmp_path)
    decision = _decision(bundle_dir)
    for group in decision["groups"][:2]:
        group["no_usable_candidate"] = True
        group["selected_candidate_id"] = None
    decision_path = (
        tmp_path / "retry-decision" / "role-review-anchor-decision-v2.json"
    ).resolve()
    _write_decision(decision_path, decision)
    topup_plan_path = (tmp_path / "retry-anchor-topup.json").resolve()
    build_role_anchor_topup_plan(
        plan=fixture.plan,  # type: ignore[arg-type]
        candidate_set_path=fixture.candidate_set_path,
        decision_path=decision_path,
        output_path=topup_plan_path,
    )

    run_id = "retryable-topup"
    run_root = fixture.artifacts_dir / "role-anchors" / "runs" / run_id
    pending = run_root.with_name(f".{run_id}.pending")
    failed_generator = _TopupGenerator(IRODORI_MODEL, fail_on_call=5)
    with pytest.raises(CompletionAnchorError, match="synthetic topup generation failure"):
        run_role_anchor_topup_generation(
            plan=fixture.plan,  # type: ignore[arg-type]
            candidate_set_path=fixture.candidate_set_path,
            decision_path=decision_path,
            topup_plan_path=topup_plan_path,
            model_id=IRODORI_MODEL,
            run_id=run_id,
            artifacts_dir=fixture.artifacts_dir,
            generator=failed_generator,  # type: ignore[arg-type]
        )
    assert failed_generator.closed
    assert not run_root.exists()
    assert not pending.exists()

    retry_generator = _TopupGenerator(IRODORI_MODEL)
    summary = run_role_anchor_topup_generation(
        plan=fixture.plan,  # type: ignore[arg-type]
        candidate_set_path=fixture.candidate_set_path,
        decision_path=decision_path,
        topup_plan_path=topup_plan_path,
        model_id=IRODORI_MODEL,
        run_id=run_id,
        artifacts_dir=fixture.artifacts_dir,
        generator=retry_generator,  # type: ignore[arg-type]
    )
    assert retry_generator.closed
    assert summary.eligible_count == 8
    assert summary.ledger_path == run_root / "ledger.json"
    assert summary.ledger_path.is_file()
    assert not pending.exists()


def test_candidate_setは四attemptの重複とtopup_target欠落を拒否する(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    candidate_set = json.loads(fixture.candidate_set_path.read_bytes())
    candidate_set["groups"][0]["attempts"] = [1, 2, 2, 4]
    invalid_path = (tmp_path / "invalid-attempts.json").resolve()
    _write_canonical(invalid_path, candidate_set)
    invalid_plan = replace(
        fixture.plan,
        anchor_candidate_set_sha256=_sha256(invalid_path.read_bytes()),
    )
    with pytest.raises(CompletionAnchorError, match="unique厳密昇順"):
        build_role_review_bundle_v2(
            plan=invalid_plan,  # type: ignore[arg-type]
            candidate_set_path=invalid_path,
            artifacts_dir=fixture.artifacts_dir,
            output_dir=(tmp_path / "invalid-bundle").resolve(),
        )

    bundle_dir = _build_bundle(fixture, tmp_path)
    decision = _decision(bundle_dir)
    decision["groups"][0]["no_usable_candidate"] = True
    decision["groups"][0]["selected_candidate_id"] = None
    decision_path = (
        tmp_path / "valid-decision" / "role-review-anchor-decision-v2.json"
    ).resolve()
    _write_decision(decision_path, decision)
    topup_path = (tmp_path / "valid-topup.json").resolve()
    build_role_anchor_topup_plan(
        plan=fixture.plan,  # type: ignore[arg-type]
        candidate_set_path=fixture.candidate_set_path,
        decision_path=decision_path,
        output_path=topup_path,
    )
    tampered = json.loads(topup_path.read_bytes())
    tampered["targets"] = []
    _write_canonical(topup_path, tampered)
    with pytest.raises(CompletionAnchorError, match="空でない"):
        run_role_anchor_topup_generation(
            plan=fixture.plan,  # type: ignore[arg-type]
            candidate_set_path=fixture.candidate_set_path,
            decision_path=decision_path,
            topup_plan_path=topup_path,
            model_id=IRODORI_MODEL,
            run_id="invalid-topup",
            artifacts_dir=fixture.artifacts_dir,
            generator=_TopupGenerator(IRODORI_MODEL),  # type: ignore[arg-type]
        )
