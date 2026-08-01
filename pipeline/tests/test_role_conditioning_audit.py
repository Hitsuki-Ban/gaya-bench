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
    aivisspeech,
    chatterbox,
    gpt_sovits,
    irodori_tts,
    qwen3_tts,
    voxcpm2,
)
from gaya_pipeline.completion_anchor import load_anchor_source_plan
from gaya_pipeline.completion_plan import (
    ANCHOR_CANDIDATE_SET_SHA256,
    ANCHOR_SOURCE_PLAN_SHA256,
    PROTOCOL,
    CompletionPlanError,
    load_completion_plan,
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
SOURCE_PLAN_PATH = (
    REPOSITORY_ROOT / "docs" / "research" / "full-baseline-completion" / "plan.json"
)


def test_pre_hearingの正式planはv1_source_authorityのまま固定する() -> None:
    raw = SOURCE_PLAN_PATH.read_bytes()
    document = json.loads(raw)
    source_plan = load_anchor_source_plan(plan_path=SOURCE_PLAN_PATH.resolve())

    assert document["format_version"] == 1
    assert document["protocol"] == "role-baseline-plan-v1"
    assert hashlib.sha256(raw).hexdigest() == ANCHOR_SOURCE_PLAN_SHA256
    assert source_plan.plan_id == ANCHOR_SOURCE_PLAN_SHA256
    assert source_plan.anchor_candidate_set_sha256 == (ANCHOR_CANDIDATE_SET_SHA256)
    with pytest.raises(CompletionPlanError, match="exact contract"):
        load_completion_plan(
            SOURCE_PLAN_PATH.resolve(),
            base_manifest_path=(REPOSITORY_ROOT / "data" / "manifest.json").resolve(),
            scenarios_dir=(REPOSITORY_ROOT / "scenarios").resolve(),
            voices_dir=(REPOSITORY_ROOT / "assets" / "voices").resolve(),
        )


def test_synthetic_selection確定後だけaudit_v2_planを作る(
    tmp_path: Path,
) -> None:
    source_raw_before = SOURCE_PLAN_PATH.read_bytes()
    authority = role_conditioning_audit._build_audit_role_anchor_selection(
        root=REPOSITORY_ROOT,
        output_dir=tmp_path / "audit-anchor",
    )
    selection_raw = authority.selection_path.read_bytes()
    selection = json.loads(selection_raw)
    completion_document = json.loads(authority.completion_plan_path.read_bytes())
    plan = load_completion_plan(
        authority.completion_plan_path,
        base_manifest_path=REPOSITORY_ROOT / "data" / "manifest.json",
        scenarios_dir=REPOSITORY_ROOT / "scenarios",
        voices_dir=REPOSITORY_ROOT / "assets" / "voices",
    )

    assert SOURCE_PLAN_PATH.read_bytes() == source_raw_before
    assert hashlib.sha256(selection_raw).hexdigest() == (authority.selection_sha256)
    assert authority.completion_plan_sha256 == plan.plan_id
    assert authority.anchor_source_plan_sha256 == plan.anchor_source_plan_sha256
    assert selection["plan_sha256"] == plan.anchor_source_plan_sha256
    assert selection["candidate_set_sha256"] == (plan.anchor_candidate_set_sha256)
    assert plan.anchor_selection_sha256 == authority.selection_sha256
    assert completion_document["protocol"] == PROTOCOL
    assert plan.plan_id != plan.anchor_source_plan_sha256
    assert len(authority.bindings) == 106


def test_8x161の実generate呼び出しでreading_transportを検証する() -> None:
    report = build_role_source_audit(REPOSITORY_ROOT)
    probes = report["runtime_transport_probes"]

    assert report["summary"]["runtime_transport_probe_count"] == 1288
    assert Counter(probe["model"] for probe in probes) == {
        model: 161 for model in role_conditioning_audit.MODEL_ADAPTER_FILES
    }
    assert all(probe["status"] == "match" for probe in probes)
    assert (
        len(
            {(probe["model"], probe["scenario"], probe["line"]) for probe in probes},
        )
        == 1288
    )
    explicit_by_model = {
        model: next(
            probe
            for probe in probes
            if probe["model"] == model and probe["declared_reading"] is not None
        )
        for model in role_conditioning_audit.MODEL_ADAPTER_FILES
    }
    aivis = explicit_by_model["aivisspeech-kohaku"]
    assert aivis["runtime_text"] == aivis["source_text"]
    assert aivis["runtime_reading"] == aivis["declared_reading"]
    cosy = explicit_by_model["cosyvoice3-0.5b-2512"]
    assert cosy["runtime_text"] == cosy["declared_reading"]
    assert cosy["runtime_reading"] is None
    for model in set(explicit_by_model) - {
        "aivisspeech-kohaku",
        "cosyvoice3-0.5b-2512",
    }:
        probe = explicit_by_model[model]
        assert probe["runtime_text"] != probe["declared_reading"]
        assert probe["runtime_reading"] is None


def test_irodoriの実generateでcaptionへ別値を渡すと失敗する(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = role_conditioning_audit._IrodoriAuditRuntime.synthesize

    def substitute_caption(
        self: Any,
        **kwargs: Any,
    ) -> Any:
        return original(self, **{**kwargs, "caption": "グッ……ソコワサワルナ……"})

    monkeypatch.setattr(
        role_conditioning_audit._IrodoriAuditRuntime,
        "synthesize",
        substitute_caption,
    )
    with pytest.raises(RoleConditioningAuditError, match="runtime caption"):
        build_role_source_audit(REPOSITORY_ROOT)


def test_cosyvoiceの実generateでinstructionへ別値を渡すと失敗する(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = role_conditioning_audit._CaptureAuditRuntime.synthesize

    def substitute_instruction(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if "tts_text" in kwargs:
            kwargs = {**kwargs, "instruction": "グッ……ソコワサワルナ……"}
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        role_conditioning_audit._CaptureAuditRuntime,
        "synthesize",
        substitute_instruction,
    )
    with pytest.raises(RoleConditioningAuditError, match="runtime instruction"):
        build_role_source_audit(REPOSITORY_ROOT)


def test_qwenの実generateでclone_promptへ別値を渡すと失敗する(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = role_conditioning_audit._QwenAuditRuntime.create_voice_clone_prompt

    def substitute_reference_text(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        return original(
            self,
            *args,
            **{**kwargs, "ref_text": "グッ……ソコワサワルナ……"},
        )

    monkeypatch.setattr(
        role_conditioning_audit._QwenAuditRuntime,
        "create_voice_clone_prompt",
        substitute_reference_text,
    )
    with pytest.raises(RoleConditioningAuditError, match="runtime clone prompt"):
        build_role_source_audit(REPOSITORY_ROOT)


def test_aivisの実generate呼び出しがreadingで表記を置換すると失敗する(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = role_conditioning_audit._CaptureAuditRuntime.synthesize

    def substitute_at_runtime(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        reading = kwargs.get("reading")
        if isinstance(reading, str):
            kwargs = {**kwargs, "text": reading, "reading": None}
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        role_conditioning_audit._CaptureAuditRuntime,
        "synthesize",
        substitute_at_runtime,
    )
    with pytest.raises(RoleConditioningAuditError, match="runtime transport"):
        build_role_source_audit(REPOSITORY_ROOT)


def test_current_role_truth_reference_split_and_8x161_receipts() -> None:
    report = build_role_source_audit(REPOSITORY_ROOT)

    assert report["audit_role_anchor_selection"] == {
        "kind": "deterministic_audit_fixture",
        "protocol": "role-anchor-selection-v1",
        "anchor_source_plan": {
            "file": "docs/research/full-baseline-completion/plan.json",
            "sha256": ANCHOR_SOURCE_PLAN_SHA256,
        },
        "completion_plan": {
            "kind": "audit_only_v2",
            "protocol": PROTOCOL,
            "sha256": report["audit_role_anchor_selection"]["completion_plan"][
                "sha256"
            ],
        },
        "selection_sha256": report["audit_role_anchor_selection"]["selection_sha256"],
        "candidate_set_sha256": report["audit_role_anchor_selection"][
            "candidate_set_sha256"
        ],
        "group_count": 106,
    }
    for value in (
        report["audit_role_anchor_selection"]["completion_plan"]["sha256"],
        report["audit_role_anchor_selection"]["anchor_source_plan"]["sha256"],
        report["audit_role_anchor_selection"]["selection_sha256"],
        report["audit_role_anchor_selection"]["candidate_set_sha256"],
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", value)

    assert report["summary"] == {
        "scenario_count": 15,
        "character_count": 58,
        "line_count": 161,
        "model_count": 8,
        "conditioning_receipt_count": 1288,
        "reading_receipt_count": 1288,
        "runtime_transport_probe_count": 1288,
        "explicit_reading_line_count": 25,
        "explicit_reading_receipt_count": 200,
        "explicit_reading_applied_receipt_count": 50,
        "explicit_reading_unsupported_receipt_count": 150,
        "surface_text_receipt_count": 952,
        "model_required_auto_kana_receipt_count": 136,
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
    assert (
        len(
            {
                (receipt["model"], receipt["scenario"], receipt["line"])
                for receipt in receipts
            },
        )
        == 1288
    )
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
        assert receipt["reading"]["status"] in {
            "applied",
            "unsupported",
            "surface_text",
            "model_required_auto_kana",
        }
        assert receipt["published_provenance"]["status"] in {
            "candidate",
            "failure",
        }

    explicit = {
        receipt["model"]: receipt["reading"]
        for receipt in receipts
        if receipt["scenario"] == "battlefield-camp"
        and receipt["line"] == "wounded-001"
    }
    surface = "ぐっ……そこは触るな……"
    declared = "グッ……ソコワサワルナ……"
    assert explicit["aivisspeech-kohaku"] == {
        "surface_text": surface,
        "declared_reading": declared,
        "capability_reading": True,
        "model_text_field": "text",
        "model_text": surface,
        "surface_transport": "audio_query.text",
        "reading_field": "reading",
        "reading_input": declared,
        "reading_transport": "accent_phrases",
        "status": "applied",
    }
    assert explicit["cosyvoice3-0.5b-2512"] == {
        "surface_text": surface,
        "declared_reading": declared,
        "capability_reading": True,
        "model_text_field": "tts_text",
        "model_text": declared,
        "surface_transport": "source_text",
        "reading_field": "tts_text",
        "reading_input": declared,
        "reading_transport": "line.reading_to_tts_text",
        "status": "applied",
    }
    unsupported_models = set(explicit) - {
        "aivisspeech-kohaku",
        "cosyvoice3-0.5b-2512",
    }
    assert len(unsupported_models) == 6
    for model in unsupported_models:
        reading = explicit[model]
        assert reading["status"] == "unsupported"
        assert reading["capability_reading"] is False
        assert reading["reading_field"] is None
        assert reading["reading_input"] is None
        assert surface in reading["model_text"]

    implicit = {
        receipt["model"]: receipt["reading"]
        for receipt in receipts
        if receipt["scenario"] == "castle-gate" and receipt["line"] == "guard-otoko-001"
    }
    assert implicit["cosyvoice3-0.5b-2512"]["status"] == ("model_required_auto_kana")
    assert implicit["cosyvoice3-0.5b-2512"]["model_text"] == (
        "トマレ！ナニモノダ、ナヲナノレ！"
    )
    assert all(
        reading["status"] == "surface_text"
        for model, reading in implicit.items()
        if model != "cosyvoice3-0.5b-2512"
    )

    receptionist = {
        receipt["model"]: receipt
        for receipt in receipts
        if receipt["scenario"] == "guild-hall"
        and receipt["character"] == "receptionist"
    }
    chatter_reference = receptionist["chatterbox-multilingual-v3"]["reference"]
    chatter_payload = receptionist["chatterbox-multilingual-v3"]["input_identity"][
        "payload"
    ]
    assert (
        chatter_reference["prepare_state_sha256"] == chatter_payload["reference_sha256"]
    )
    assert (
        chatter_reference["audit_fixture_source_sha256"]
        == chatter_payload["reference_sha256"]
    )
    assert chatter_reference["sha256"] != chatter_payload["reference_sha256"]

    gpt_reference = receptionist["gpt-sovits-v2-pro-plus"]["reference"]
    gpt_payload = receptionist["gpt-sovits-v2-pro-plus"]["input_identity"]["payload"]
    assert gpt_reference["prepare_state_sha256"] == gpt_payload["reference_clip_sha256"]
    assert (
        gpt_reference["audit_fixture_source_sha256"]
        == gpt_payload["reference_source_sha256"]
    )
    assert gpt_reference["sha256"] != gpt_payload["reference_source_sha256"]

    for model in ("qwen3-tts-12hz-1.7b", "voxcpm2"):
        reference = receptionist[model]["reference"]
        payload = receptionist[model]["input_identity"]["payload"]
        assert reference["prepare_state_sha256"] == payload["reference_sha256"]
        assert reference["audit_fixture_source_sha256"] == payload["reference_sha256"]
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
    assert (
        selected_anchor
        == generated_qwen["input_identity"]["payload"]["selected_anchor"]
    )
    assert (
        selected_anchor["anchor_audio_sha256"]
        == generated_qwen["reference"]["prepare_state_sha256"]
    )
    assert (
        selected_anchor["anchor_plan_sha256"]
        == report["audit_role_anchor_selection"]["anchor_source_plan"]["sha256"]
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


def test_aivis_production_generation_input_drops_reading_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = aivisspeech.AivisSpeechAdapter.generation_input

    def without_reading(
        self: aivisspeech.AivisSpeechAdapter,
        *args: Any,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        result = dict(original(self, *args, **kwargs))
        result.pop("reading", None)
        return result

    monkeypatch.setattr(
        aivisspeech.AivisSpeechAdapter,
        "generation_input",
        without_reading,
    )
    with pytest.raises(RoleConditioningAuditError, match="reading"):
        build_role_source_audit(REPOSITORY_ROOT)


def test_gpt_production_generation_input_substitutes_reading_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = gpt_sovits.GPTSoVITSAdapter.generation_input

    def substitute_reading(
        self: gpt_sovits.GPTSoVITSAdapter,
        job: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        result = dict(original(self, job, *args, **kwargs))
        reading = job.line.get("reading")
        if reading is not None:
            result["text"] = reading
        return result

    monkeypatch.setattr(
        gpt_sovits.GPTSoVITSAdapter,
        "generation_input",
        substitute_reading,
    )
    with pytest.raises(RoleConditioningAuditError, match="runtime transport"):
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
