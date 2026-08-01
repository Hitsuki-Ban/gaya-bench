from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from gaya_pipeline.adapters.irodori_tts import ROLE_ANCHOR_TEXT
from gaya_pipeline.adapters.qwen3_tts import REFERENCE_TEXT
from gaya_pipeline.completion_anchor import (
    CompletionAnchorError,
    build_role_review_bundle_v2,
    finalize_role_anchor_selection,
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
