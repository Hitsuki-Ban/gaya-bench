from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import pytest
from gaya_pipeline.completion_anchor import (
    CompletionAnchorError,
    resolve_selected_anchor,
    validate_anchor_selection,
)
from gaya_pipeline.completion_plan import RoleSnapshot
from gaya_pipeline.take_identity import canonical_json

PLAN_SHA256 = hashlib.sha256(b"anchor plan").hexdigest()
CANDIDATE_SET_SHA256 = hashlib.sha256(b"candidate set").hexdigest()
MODEL = "qwen3-tts-12hz-1.7b"
MODEL_REVISION = "qwen-test-revision"


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _role(*, scenario: str = "castle-gate", character: str = "guard") -> RoleSnapshot:
    identity = {
        "scenario": scenario,
        "character": character,
        "role": {
            "name": "門番",
            "kind": "human",
            "gender": "male",
            "age": "adult",
            "archetype": "guard",
            "voice": "落ち着いた低音",
            "personality": "実直",
        },
        "reference_voice": None,
        "scene_setting": "城門前",
    }
    return RoleSnapshot(
        scenario=scenario,
        character=character,
        role=identity["role"],
        reference_voice=None,
        scene_setting=identity["scene_setting"],
        role_identity_sha256=_canonical_sha256(identity),
    )


def _group(
    *,
    role: RoleSnapshot,
    audio_path: str,
    audio_sha256: str,
    model: str = MODEL,
) -> dict[str, Any]:
    role_identity = {
        "scenario": role.scenario,
        "character": role.character,
        "role": dict(role.role),
        "reference_voice": role.reference_voice,
        "scene_setting": role.scene_setting,
    }
    review_epoch_sha256 = hashlib.sha256(
        f"review:{model}:{role.scenario}:{role.character}".encode(),
    ).hexdigest()
    anchor_id = hashlib.sha256(
        f"anchor:{model}:{role.scenario}:{role.character}".encode(),
    ).hexdigest()
    decision = {
        "id": hashlib.sha256(
            f"decision:{model}:{role.scenario}:{role.character}".encode(),
        ).hexdigest(),
        "model": model,
        "scenario": role.scenario,
        "character": role.character,
        "line": None,
        "role_epoch_sha256": review_epoch_sha256,
        "group_sha256": hashlib.sha256(b"review group").hexdigest(),
        "heard_candidate_ids": [anchor_id],
        "selected_candidate_id": anchor_id,
        "no_usable_candidate": False,
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
    decision_sha256 = _canonical_sha256(decision)
    role_epoch_sha256 = _canonical_sha256(
        {
            "protocol": "selected-role-epoch-v1",
            "model": model,
            "model_revision": MODEL_REVISION,
            "scenario": role.scenario,
            "character": role.character,
            "role_identity_sha256": role.role_identity_sha256,
            "review_role_epoch_sha256": review_epoch_sha256,
            "anchor_id": anchor_id,
            "audio_sha256": audio_sha256,
            "decision_sha256": decision_sha256,
        },
    )
    anchor_text = "持ち場を離れるな。"
    return {
        "model": model,
        "model_revision": MODEL_REVISION,
        "scenario": role.scenario,
        "character": role.character,
        "role_identity": role_identity,
        "role_identity_sha256": role.role_identity_sha256,
        "review_role_epoch_sha256": review_epoch_sha256,
        "role_epoch_sha256": role_epoch_sha256,
        "anchor_id": anchor_id,
        "attempt": 2,
        "seed": 104,
        "audio_path": audio_path,
        "audio_sha256": audio_sha256,
        "anchor_text": anchor_text,
        "anchor_text_sha256": hashlib.sha256(anchor_text.encode()).hexdigest(),
        "decision": decision,
        "decision_sha256": decision_sha256,
    }


def _selection(groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format_version": 1,
        "protocol": "role-anchor-selection-v1",
        "plan_sha256": PLAN_SHA256,
        "candidate_set_sha256": CANDIDATE_SET_SHA256,
        "groups": groups,
    }


def _write_selection(path: Path, document: dict[str, Any]) -> None:
    raw = canonical_json(document).encode("utf-8")
    path.write_bytes(raw)
    path.with_suffix(".sha256").write_bytes(
        f"{hashlib.sha256(raw).hexdigest()}\n".encode("ascii"),
    )


def _valid_selection(tmp_path: Path) -> tuple[Path, dict[str, Any], RoleSnapshot]:
    role = _role()
    audio = b"RIFF-selected-anchor"
    audio_path = tmp_path / "audio" / "selected.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(audio)
    document = _selection(
        [
            _group(
                role=role,
                audio_path="audio/selected.wav",
                audio_sha256=hashlib.sha256(audio).hexdigest(),
            ),
        ],
    )
    selection_path = (tmp_path / "selection.json").resolve()
    _write_selection(selection_path, document)
    return selection_path, document, role


def _resolve(selection_path: Path, role: RoleSnapshot, **overrides: Any) -> Any:
    arguments = {
        "selection_path": selection_path,
        "plan_sha256": PLAN_SHA256,
        "model": MODEL,
        "model_revision": MODEL_REVISION,
        "role": role,
    }
    arguments.update(overrides)
    return resolve_selected_anchor(**arguments)


def test_valid_selectionを検証して選択済みanchorを解決する(tmp_path: Path) -> None:
    selection_path, document, role = _valid_selection(tmp_path)

    assert validate_anchor_selection(document) == document
    selected = _resolve(selection_path, role)

    assert selected.audio_path == (tmp_path / "audio" / "selected.wav").resolve()
    assert selected.anchor_text == "持ち場を離れるな。"
    assert (
        selected.selection_sha256
        == hashlib.sha256(
            selection_path.read_bytes(),
        ).hexdigest()
    )
    assert selected.receipt() == {
        "anchor_selection_sha256": selected.selection_sha256,
        "anchor_plan_sha256": PLAN_SHA256,
        "anchor_candidate_set_sha256": CANDIDATE_SET_SHA256,
        "anchor_id": document["groups"][0]["anchor_id"],
        "anchor_attempt": 2,
        "anchor_seed": 104,
        "anchor_audio_sha256": document["groups"][0]["audio_sha256"],
        "anchor_text_sha256": document["groups"][0]["anchor_text_sha256"],
        "anchor_decision_sha256": document["groups"][0]["decision_sha256"],
        "role_identity_sha256": role.role_identity_sha256,
        "role_epoch_sha256": document["groups"][0]["role_epoch_sha256"],
    }


def test_markerまたはplanの不一致を拒否する(tmp_path: Path) -> None:
    selection_path, _document, role = _valid_selection(tmp_path)
    selection_path.with_suffix(".sha256").write_bytes(f"{'0' * 64}\n".encode())
    with pytest.raises(CompletionAnchorError, match="marker"):
        _resolve(selection_path, role)

    selection_path, _document, role = _valid_selection(tmp_path / "plan")
    with pytest.raises(CompletionAnchorError, match="frozen plan"):
        _resolve(selection_path, role, plan_sha256="0" * 64)


def test_model_revisionまたはrole_identityの不一致を拒否する(
    tmp_path: Path,
) -> None:
    selection_path, _document, role = _valid_selection(tmp_path)
    with pytest.raises(CompletionAnchorError, match="model revision"):
        _resolve(selection_path, role, model_revision="stale-revision")

    changed_role = RoleSnapshot(
        scenario=role.scenario,
        character=role.character,
        role={**role.role, "name": "別人"},
        reference_voice=role.reference_voice,
        scene_setting=role.scene_setting,
        role_identity_sha256=role.role_identity_sha256,
    )
    with pytest.raises(CompletionAnchorError, match="role identity"):
        _resolve(selection_path, changed_role)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document.update({"unknown": True}),
        lambda document: document["groups"][0].update({"unknown": True}),
        lambda document: document["groups"][0]["role_identity"].update(
            {"unknown": True},
        ),
        lambda document: document["groups"][0]["decision"].update(
            {"unknown": True},
        ),
        lambda document: document["groups"][0]["decision"]["rubric"].update(
            {"unknown": True},
        ),
    ],
)
def test_未知fieldをcontractの全階層で拒否する(
    tmp_path: Path,
    mutate: Any,
) -> None:
    _selection_path, document, _role_value = _valid_selection(tmp_path)
    mutate(document)

    with pytest.raises(CompletionAnchorError, match="exact contract"):
        validate_anchor_selection(document)


def test_groupのcanonical順とselected_role_epoch改ざんを拒否する(
    tmp_path: Path,
) -> None:
    first_role = _role(scenario="a-scene", character="first")
    second_role = _role(scenario="b-scene", character="second")
    first = _group(
        role=first_role,
        audio_path="audio/first.wav",
        audio_sha256=hashlib.sha256(b"first").hexdigest(),
    )
    second = _group(
        role=second_role,
        audio_path="audio/second.wav",
        audio_sha256=hashlib.sha256(b"second").hexdigest(),
    )

    with pytest.raises(CompletionAnchorError, match="canonical順"):
        validate_anchor_selection(_selection([second, first]))

    tampered = _selection([copy.deepcopy(first)])
    tampered["groups"][0]["role_epoch_sha256"] = "0" * 64
    with pytest.raises(CompletionAnchorError, match="selected anchor"):
        validate_anchor_selection(tampered)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda group: group.update({"anchor_text": "改ざん"}),
            "anchor text SHA",
        ),
        (
            lambda group: group["decision"]["rubric"].update(
                {"notes": "tampered"},
            ),
            "decision SHA",
        ),
    ],
)
def test_anchor_textとdecisionのhash改ざんを拒否する(
    tmp_path: Path,
    mutate: Any,
    match: str,
) -> None:
    _selection_path, document, _role_value = _valid_selection(tmp_path)
    mutate(document["groups"][0])

    with pytest.raises(CompletionAnchorError, match=match):
        validate_anchor_selection(document)


def test_audio_SHA改ざんを拒否する(tmp_path: Path) -> None:
    selection_path, _document, role = _valid_selection(tmp_path)
    (tmp_path / "audio" / "selected.wav").write_bytes(b"tampered")

    with pytest.raises(CompletionAnchorError, match="SHA-256"):
        _resolve(selection_path, role)
