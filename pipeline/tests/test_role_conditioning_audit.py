from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline import role_conditioning_audit
from gaya_pipeline.adapters import (
    chatterbox,
    irodori_tts,
    qwen3_tts,
    voxcpm2,
)
from gaya_pipeline.role_conditioning_audit import (
    RoleConditioningAuditError,
    build_role_source_audit,
)
from gaya_pipeline.take_identity import canonical_json

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "research"
    / "role-conditioning-audit"
    / "source-audit.json"
)
README_PATH = SNAPSHOT_PATH.with_name("README.md")


def test_current_role_truth_reference_split_and_8x161_receipts() -> None:
    report = build_role_source_audit(REPOSITORY_ROOT)

    assert report["audit_role_anchor_selection"] == {
        "kind": "deterministic_audit_fixture",
        "protocol": "role-anchor-selection-v1",
        "completion_plan": {
            "file": "docs/research/full-baseline-completion/plan.json",
            "sha256": report["audit_role_anchor_selection"][
                "completion_plan"
            ]["sha256"],
        },
        "selection_sha256": report["audit_role_anchor_selection"][
            "selection_sha256"
        ],
        "candidate_set_sha256": report["audit_role_anchor_selection"][
            "candidate_set_sha256"
        ],
        "group_count": 106,
    }
    for key in (
        "sha256",
        "selection_sha256",
        "candidate_set_sha256",
    ):
        value = (
            report["audit_role_anchor_selection"]["completion_plan"][key]
            if key == "sha256"
            else report["audit_role_anchor_selection"][key]
        )
        assert re.fullmatch(r"[0-9a-f]{64}", value)

    assert report["summary"] == {
        "scenario_count": 15,
        "character_count": 58,
        "line_count": 161,
        "model_count": 8,
        "conditioning_receipt_count": 1288,
        "explicit_reference_character_count": 5,
        "assigned_reference_character_count": 53,
        "all_reference_character_count": 58,
        "all_reference_gender_exact_character_count": 51,
        "all_reference_gender_unsupported_neutral_character_count": 7,
        "all_reference_gender_mismatch_character_count": 0,
        "all_reference_age_exact_character_count": 21,
        "all_reference_age_approximate_character_count": 37,
        "all_reference_role_exact_character_count": 18,
        "assigned_reference_gender_exact_character_count": 46,
        "assigned_reference_gender_unsupported_neutral_character_count": 7,
        "assigned_reference_gender_mismatch_character_count": 0,
        "assigned_reference_age_exact_character_count": 16,
        "assigned_reference_age_approximate_character_count": 37,
        "assigned_reference_role_exact_character_count": 13,
        "published_candidate_count": 1282,
        "published_failure_count": 6,
        "published_conditioning_match_count": 780,
        "published_conditioning_mismatch_count": 357,
        "published_conditioning_unverifiable_count": 145,
        "published_conditioning_failure_count": 6,
        "problem_count": 0,
    }
    assert report["problems"] == []
    assert len(report["characters"]) == 58
    assert len(report["all_references"]) == 58
    assert len(report["assigned_references"]) == 53
    assert all(
        reference["source"] == "adapter_assignment"
        for reference in report["assigned_references"]
    )

    receipts = report["conditioning_receipts"]
    assert len(receipts) == 8 * 161
    assert Counter(receipt["model"] for receipt in receipts) == {
        "aivisspeech-kohaku": 161,
        "chatterbox-multilingual-v3": 161,
        "cosyvoice3-0.5b-2512": 161,
        "gpt-sovits-v2-pro-plus": 161,
        "irodori-tts-600m-v3-voicedesign": 161,
        "qwen3-tts-12hz-1.7b": 161,
        "supertonic-3": 161,
        "voxcpm2": 161,
    }
    assert len(
        {
            (receipt["model"], receipt["scenario"], receipt["line"])
            for receipt in receipts
        },
    ) == 1288
    for receipt in receipts:
        assert set(receipt["field_transport"]) == {
            "name",
            "kind",
            "gender",
            "age",
            "archetype",
            "voice",
            "personality",
            "scene_setting",
        }
        assert receipt["input_identity"]["sha256"]
        assert receipt["published_provenance"]["status"] in {
            "candidate",
            "failure",
        }

    receptionist = {
        receipt["model"]: receipt
        for receipt in receipts
        if receipt["scenario"] == "guild-hall"
        and receipt["character"] == "receptionist"
    }
    chatter_reference = receptionist["chatterbox-multilingual-v3"]["reference"]
    chatter_payload = receptionist["chatterbox-multilingual-v3"][
        "input_identity"
    ]["payload"]
    assert (
        chatter_reference["prepare_state_sha256"]
        == chatter_payload["reference_sha256"]
    )
    assert (
        chatter_reference["audit_fixture_source_sha256"]
        == chatter_payload["reference_sha256"]
    )
    assert chatter_reference["sha256"] != chatter_payload["reference_sha256"]

    gpt_reference = receptionist["gpt-sovits-v2-pro-plus"]["reference"]
    gpt_payload = receptionist["gpt-sovits-v2-pro-plus"]["input_identity"][
        "payload"
    ]
    assert (
        gpt_reference["prepare_state_sha256"]
        == gpt_payload["reference_clip_sha256"]
    )
    assert (
        gpt_reference["audit_fixture_source_sha256"]
        == gpt_payload["reference_source_sha256"]
    )
    assert gpt_reference["sha256"] != gpt_payload["reference_source_sha256"]

    for model in ("qwen3-tts-12hz-1.7b", "voxcpm2"):
        reference = receptionist[model]["reference"]
        payload = receptionist[model]["input_identity"]["payload"]
        assert reference["prepare_state_sha256"] == payload["reference_sha256"]
        assert (
            reference["audit_fixture_source_sha256"]
            == payload["reference_sha256"]
        )
        assert reference["sha256"] != payload["reference_sha256"]

    generated_qwen = next(
        receipt
        for receipt in receipts
        if receipt["model"] == "qwen3-tts-12hz-1.7b"
        and receipt["role_truth"]["declared_reference_voice"] is None
    )
    assert generated_qwen["reference"]["sha256"] is None
    assert (
        generated_qwen["reference"]["prepare_state_sha256"]
        == generated_qwen["input_identity"]["payload"]["reference_sha256"]
    )
    selected_anchor = generated_qwen["reference"]["selected_anchor"]
    assert selected_anchor == generated_qwen["input_identity"]["payload"][
        "selected_anchor"
    ]
    assert (
        selected_anchor["anchor_audio_sha256"]
        == generated_qwen["reference"]["prepare_state_sha256"]
    )
    assert (
        selected_anchor["anchor_plan_sha256"]
        == report["audit_role_anchor_selection"]["completion_plan"]["sha256"]
    )
    assert (
        selected_anchor["anchor_selection_sha256"]
        == report["audit_role_anchor_selection"]["selection_sha256"]
    )


def test_adapter_role_receipt_drops_gender_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = irodori_tts._role_caption

    def without_gender(identity: dict[str, str]) -> str:
        return "\n".join(
            line
            for line in original(identity).splitlines()
            if not line.startswith("性別:")
        )

    monkeypatch.setattr(irodori_tts, "_role_caption", without_gender)
    with pytest.raises(
        RoleConditioningAuditError,
        match="prompt へ伝達していません",
    ):
        build_role_source_audit(REPOSITORY_ROOT)


def test_qwen_production_generation_input_wrong_gender_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = qwen3_tts.Qwen3TTSAdapter.generation_input

    def wrong_male_identity(
        self: qwen3_tts.Qwen3TTSAdapter,
        *args: Any,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        result = dict(original(self, *args, **kwargs))
        identity = result.get("character_identity")
        assert isinstance(identity, Mapping)
        result["character_identity"] = {
            **identity,
            "gender": "wrong-male-speaker",
        }
        return result

    monkeypatch.setattr(
        qwen3_tts.Qwen3TTSAdapter,
        "generation_input",
        wrong_male_identity,
    )
    with pytest.raises(RoleConditioningAuditError, match="gender"):
        build_role_source_audit(REPOSITORY_ROOT)


def test_clone_production_generation_input_wrong_voice_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = chatterbox.ChatterboxAdapter.generation_input

    def wrong_reference_voice(
        self: chatterbox.ChatterboxAdapter,
        *args: Any,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        return {
            **original(self, *args, **kwargs),
            "reference_voice": "hadou-emotion-11",
        }

    monkeypatch.setattr(
        chatterbox.ChatterboxAdapter,
        "generation_input",
        wrong_reference_voice,
    )
    with pytest.raises(RoleConditioningAuditError, match="reference_voice"):
        build_role_source_audit(REPOSITORY_ROOT)


def test_vox_production_generation_input_wrong_identity_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = voxcpm2.VoxCPM2Adapter.generation_input

    def wrong_design_identity(
        self: voxcpm2.VoxCPM2Adapter,
        *args: Any,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        result = dict(original(self, *args, **kwargs))
        provenance = result.get("reference_provenance")
        assert isinstance(provenance, Mapping)
        identity = provenance.get("identity")
        if isinstance(identity, Mapping):
            result["reference_provenance"] = {
                **provenance,
                "identity": {
                    **identity,
                    "gender": "wrong-male-speaker",
                },
            }
        return result

    monkeypatch.setattr(
        voxcpm2.VoxCPM2Adapter,
        "generation_input",
        wrong_design_identity,
    )
    with pytest.raises(RoleConditioningAuditError, match="gender"):
        build_role_source_audit(REPOSITORY_ROOT)


@pytest.mark.parametrize(
    ("mutation", "error_pattern"),
    (
        ("selection", "role identity SHA"),
        ("marker", "marker"),
        ("wav", "SHA-256"),
        ("epoch", "role_epoch_sha256"),
    ),
)
def test_role_anchor_selection_tampering_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    error_pattern: str,
) -> None:
    original = role_conditioning_audit._build_audit_role_anchor_selection

    def tampered_selection(
        *,
        root: Path,
        output_dir: Path,
    ) -> Any:
        selection = original(root=root, output_dir=output_dir)
        path = selection.selection_path
        if mutation == "marker":
            path.with_suffix(".sha256").write_bytes(f"{'0' * 64}\n".encode())
            return selection
        document = json.loads(path.read_text(encoding="utf-8"))
        first = document["groups"][0]
        if mutation == "wav":
            audio_path = path.parent / first["audio_path"]
            audio_path.write_bytes(audio_path.read_bytes() + b"tampered")
            return selection
        if mutation == "selection":
            first["role_identity"]["role"]["gender"] = "wrong-male-speaker"
        elif mutation == "epoch":
            first["role_epoch_sha256"] = "0" * 64
        else:
            raise AssertionError(f"unknown mutation: {mutation}")
        raw = canonical_json(document).encode("utf-8")
        path.write_bytes(raw)
        path.with_suffix(".sha256").write_bytes(
            f"{hashlib.sha256(raw).hexdigest()}\n".encode("ascii"),
        )
        return selection

    monkeypatch.setattr(
        role_conditioning_audit,
        "_build_audit_role_anchor_selection",
        tampered_selection,
    )
    with pytest.raises(RoleConditioningAuditError, match=error_pattern):
        build_role_source_audit(REPOSITORY_ROOT)


def test_committed_snapshot_equals_live_build_and_readme_sha() -> None:
    live = build_role_source_audit(REPOSITORY_ROOT)
    snapshot_bytes = SNAPSHOT_PATH.read_bytes()
    assert b"\r\n" not in snapshot_bytes
    committed = json.loads(snapshot_bytes.decode("utf-8"))
    assert committed == live

    snapshot_sha256 = hashlib.sha256(snapshot_bytes).hexdigest()
    readme = README_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"`source-audit\.json` SHA-256 は\s*"
        r"`([0-9a-f]{64})`。",
        readme,
    )
    assert match is not None
    assert match.group(1) == snapshot_sha256
