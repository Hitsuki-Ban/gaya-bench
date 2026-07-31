from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml

from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.take_manifest_v4 import TakeManifestError, validate_manifest_v4


class CompletionPlanError(RuntimeError):
    pass


FORMAT_VERSION = 1
PROTOCOL = "role-baseline-plan-v1"
BASE_MANIFEST_SHA256 = (
    "f9dfda542fd1120fe0f74daae3036eab5211d7394d155f7b9953978e59bbe89d"
)
BASE_MANIFEST_GIT_BLOB = "44061fafe330a9bebfed7a97a0b69ebe234c8724"
BASE_CANDIDATE_SET_SHA256 = (
    "91913e08f97497f1f7604f109a6d0f7308742237277f6bbc5483678ac9858cc2"
)
BASE_SELECTION_SHA256 = (
    "629cc80346160eb8e687757e6f792ef519da9a4fb74f79bdf97eb4d00f56126e"
)

QWEN_MODEL = "qwen3-tts-12hz-1.7b"
IRODORI_MODEL = "irodori-tts-600m-v3-voicedesign"
CHATTERBOX_MODEL = "chatterbox-multilingual-v3"
COSYVOICE_MODEL = "cosyvoice3-0.5b-2512"
GPT_SOVITS_MODEL = "gpt-sovits-v2-pro-plus"
VOXCPM_MODEL = "voxcpm2"

MODEL_REVISIONS = {
    "aivisspeech-kohaku": (
        "AivisSpeech Engine 1.2.0; コハク AIVMX 1.1.0@"
        "sha256:3f5c08b52bb8a64efd361268580c81510f96c927cd6905aa7dbae6851333270a"
    ),
    CHATTERBOX_MODEL: (
        "Chatterbox 65b18437192794391a0308a8f705b1e33e633948; "
        "ResembleAI/chatterbox 5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18; "
        "PerTh ce86c49d029f42272c1902eccb675556b9ed2330"
    ),
    COSYVOICE_MODEL: (
        "CosyVoice 074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc; "
        "Matcha-TTS dd9105b34bf2be2230f4aa1e4769fb586a3c824e; "
        "FunAudioLLM/Fun-CosyVoice3-0.5B-2512 "
        "29e01c4e8d000f4bcd70751be16fa94bf3d85a18"
    ),
    GPT_SOVITS_MODEL: (
        "GPT-SoVITS v2ProPlus; "
        "RVC-Boss/GPT-SoVITS@d523079fc05d9a8028d6085bffe4a2757c32abb6; "
        "lj1995/GPT-SoVITS@336b2ec4e8d4ac74740798dd40af44e74659ecaf"
    ),
    IRODORI_MODEL: (
        "Irodori-TTS 0.1.0@eaf74d6a19138f743acb5b71a445fd25a57db987; "
        "Aratako/Irodori-TTS-600M-v3-VoiceDesign@"
        "e863a3a93e652e09afeff3e84823a206a0a60314; "
        "Aratako/Semantic-DACVAE-Japanese-32dim@"
        "47376ee24834d7a05a48ebabfe3cde29b3c5e214; "
        "DACVAE@414c20785fc3a28373073ea8ef7a1316eeeaca6e; "
        "llm-jp/llm-jp-3-150m@b112feef602fff752e4dac4c30af6a2c2fa41c7a; "
        "sony/silentcipher@a1c4d021905e0dc5b24be5f68db5fc4dba410ee1"
    ),
    QWEN_MODEL: (
        "qwen-tts 0.1.1; Base fd4b254389122332181a7c3db7f27e918eec64e3; "
        "VoiceDesign 5ecdb67327fd37bb2e042aab12ff7391903235d3"
    ),
    "supertonic-3": (
        "supertonic 1.3.1 "
        "(supertone-inc/supertonic-py@908a56486e821e833a80530ff0cae3ad0b046fce); "
        "Supertone/supertonic-3@724fb5abbf5502583fb520898d45929e62f02c0b; "
        "tts v1.7.3"
    ),
    VOXCPM_MODEL: (
        "VoxCPM 616d3d3e630a9c96c2853250eef91b0f39dcd5fa; "
        "openbmb/VoxCPM2 bffb3df5a29440629464e5e839f4d214c8714c3d"
    ),
}

ANCHOR_TEXTS = {
    IRODORI_MODEL: "そらにはくもがうかび、とおくでかぜのおとがきこえます。",
    QWEN_MODEL: "さて、きょうもいちにちをはじめましょう。",
}
PHASE_A_TAKES = 4
PHASE_A_MINIMUM_ELIGIBLE = 3
PHASE_A_SEED_POLICY = "role-anchor-derived-sha256-v1"
PHASE_A_SEED_BASE = 177
PHASE_B_TAKES = 4
PHASE_B_MINIMUM_ELIGIBLE = 3
PHASE_B_SEED_POLICY = "derived-sha256-v1"
PHASE_B_SEED_BASE = 104
INHERITED_GROUPS = 925
FINAL_GROUPS = 1_288
ROLE_FIELDS = (
    "name",
    "kind",
    "gender",
    "age",
    "archetype",
    "voice",
    "personality",
)
HEX = frozenset("0123456789abcdef")

ROOT_FIELDS = {
    "format_version",
    "protocol",
    "base",
    "sources",
    "models",
    "roles",
    "phase_a",
    "phase_b",
}
BASE_FIELDS = {
    "manifest_sha256",
    "git_blob",
    "candidate_set_sha256",
    "selection_sha256",
    "inherited_groups",
    "final_groups",
}
SOURCES_FIELDS = {
    "scenario_registry_sha256",
    "scenario_files",
    "voice_registry_path",
    "voice_registry_sha256",
}
SOURCE_FILE_FIELDS = {"scenario", "path", "sha256"}
MODEL_FIELDS = {"id", "revision"}
ROLE_SNAPSHOT_FIELDS = {
    "scenario",
    "character",
    "role",
    "reference_voice",
    "scene_setting",
    "role_identity_sha256",
}
PHASE_A_FIELDS = {
    "takes",
    "minimum_eligible_candidates",
    "seed_policy",
    "seed_base",
    "anchor_texts",
    "targets",
}
ANCHOR_TEXT_FIELDS = {"model", "text", "sha256"}
PHASE_A_TARGET_FIELDS = {
    "model",
    "scenario",
    "character",
    "role_identity_sha256",
    "role_epoch_sha256",
}
PHASE_B_FIELDS = {
    "takes",
    "minimum_eligible_candidates",
    "seed_policy",
    "seed_base",
    "targets",
}
PHASE_B_TARGET_FIELDS = {"model", "scenario", "line", "variant", "source"}

GroupIdentity = tuple[str, str, str, str]
RoleIdentity = tuple[str, str]


@dataclass(frozen=True)
class RoleSnapshot:
    scenario: str
    character: str
    role: Mapping[str, str]
    reference_voice: str | None
    scene_setting: str
    role_identity_sha256: str

    @property
    def identity(self) -> RoleIdentity:
        return (self.scenario, self.character)

    def document(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "character": self.character,
            "role": dict(self.role),
            "reference_voice": self.reference_voice,
            "scene_setting": self.scene_setting,
            "role_identity_sha256": self.role_identity_sha256,
        }


@dataclass(frozen=True)
class AnchorTarget:
    model: str
    scenario: str
    character: str
    role_identity_sha256: str
    role_epoch_sha256: str

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.model, self.scenario, self.character)


@dataclass(frozen=True)
class CompletionTarget:
    model: str
    scenario: str
    line: str
    variant: str
    source: Literal["generate", "reuse"]

    @property
    def identity(self) -> GroupIdentity:
        return (self.model, self.scenario, self.line, self.variant)


@dataclass(frozen=True)
class CompletionPlan:
    plan_id: str
    base_manifest_sha256: str
    base_manifest_git_blob: str
    base_candidate_set_sha256: str
    base_selection_sha256: str
    inherited_groups: int
    final_groups: int
    scenario_registry_sha256: str
    voice_registry_sha256: str
    models: Mapping[str, str]
    roles: tuple[RoleSnapshot, ...]
    anchor_texts: Mapping[str, str]
    phase_a_takes: int
    phase_a_minimum_eligible_candidates: int
    phase_a_seed_policy: str
    phase_a_seed_base: int
    anchor_targets: tuple[AnchorTarget, ...]
    takes: int
    minimum_eligible_candidates: int
    seed_policy: str
    seed_base: int
    targets: tuple[CompletionTarget, ...]
    raw_sha256: str

    def role(self, scenario: str, character: str) -> RoleSnapshot:
        matches = [
            role
            for role in self.roles
            if role.identity == (scenario, character)
        ]
        if len(matches) != 1:
            raise CompletionPlanError(
                f"plan role が一意ではありません: {scenario}/{character}",
            )
        return matches[0]

    def anchor_targets_for_model(self, model_id: str) -> tuple[AnchorTarget, ...]:
        return tuple(
            target for target in self.anchor_targets if target.model == model_id
        )

    def targets_for_model(self, model_id: str) -> tuple[CompletionTarget, ...]:
        return tuple(target for target in self.targets if target.model == model_id)

    def target_lines_for_model(self, model_id: str) -> tuple[tuple[str, str], ...]:
        return tuple(
            (target.scenario, target.line)
            for target in self.targets_for_model(model_id)
            if target.source == "generate"
        )


def load_completion_plan(
    plan_path: Path,
    *,
    base_manifest_path: Path,
    scenarios_dir: Path,
    voices_dir: Path,
) -> CompletionPlan:
    _require_absolute(plan_path, "completion plan")
    _require_absolute(base_manifest_path, "base manifest")
    _require_absolute(scenarios_dir, "scenarios")
    _require_absolute(voices_dir, "voices")

    plan_raw = _read_bytes(plan_path, "completion plan")
    document = _read_json(plan_raw, plan_path, "completion plan")
    normalized, parsed = _validate_plan_document(document)
    if plan_raw != canonical_json(normalized).encode("utf-8"):
        raise CompletionPlanError(
            "completion plan は canonical bytes である必要があります。",
        )
    plan_sha256 = hashlib.sha256(plan_raw).hexdigest()

    base_raw = _read_bytes(base_manifest_path, "base manifest")
    _validate_base_manifest(
        raw=base_raw,
        path=base_manifest_path,
        base=normalized["base"],
    )
    actual_sources, actual_roles, scenario_documents = _source_snapshot(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
    )
    if normalized["sources"] != actual_sources:
        raise CompletionPlanError(
            "completion plan sources が明示された scenario/voice registry と"
            "一致しません。",
        )
    actual_role_documents = [role.document() for role in actual_roles]
    if normalized["roles"] != actual_role_documents:
        raise CompletionPlanError(
            "completion plan roles が scenario の58役柄snapshotと一致しません。",
        )
    expected_phase_b = _expected_phase_b_targets(scenario_documents)
    if normalized["phase_b"]["targets"] != expected_phase_b:
        raise CompletionPlanError(
            "completion plan phase_b.targets が363 groupの固定対象と一致しません。",
        )

    models = {
        item["id"]: item["revision"] for item in normalized["models"]
    }
    roles = tuple(parsed["roles"])
    anchor_targets = tuple(parsed["anchor_targets"])
    targets = tuple(parsed["targets"])
    return CompletionPlan(
        plan_id=plan_sha256,
        base_manifest_sha256=normalized["base"]["manifest_sha256"],
        base_manifest_git_blob=normalized["base"]["git_blob"],
        base_candidate_set_sha256=normalized["base"]["candidate_set_sha256"],
        base_selection_sha256=normalized["base"]["selection_sha256"],
        inherited_groups=normalized["base"]["inherited_groups"],
        final_groups=normalized["base"]["final_groups"],
        scenario_registry_sha256=normalized["sources"][
            "scenario_registry_sha256"
        ],
        voice_registry_sha256=normalized["sources"]["voice_registry_sha256"],
        models=models,
        roles=roles,
        anchor_texts={
            item["model"]: item["text"]
            for item in normalized["phase_a"]["anchor_texts"]
        },
        phase_a_takes=normalized["phase_a"]["takes"],
        phase_a_minimum_eligible_candidates=normalized["phase_a"][
            "minimum_eligible_candidates"
        ],
        phase_a_seed_policy=normalized["phase_a"]["seed_policy"],
        phase_a_seed_base=normalized["phase_a"]["seed_base"],
        anchor_targets=anchor_targets,
        takes=normalized["phase_b"]["takes"],
        minimum_eligible_candidates=normalized["phase_b"][
            "minimum_eligible_candidates"
        ],
        seed_policy=normalized["phase_b"]["seed_policy"],
        seed_base=normalized["phase_b"]["seed_base"],
        targets=targets,
        raw_sha256=plan_sha256,
    )


def compute_completion_plan_id(document: Any) -> str:
    normalized, _parsed = _validate_plan_document(document)
    return hashlib.sha256(
        canonical_json(normalized).encode("utf-8"),
    ).hexdigest()


def build_role_snapshot(
    *,
    scenario: str,
    character: str,
    character_document: Mapping[str, Any],
    scene_setting: str,
) -> RoleSnapshot:
    role = {
        field: _text(character_document[field], f"character.{field}")
        for field in ROLE_FIELDS
        if field != "kind"
    }
    # `kind` is the one scenario-schema field with an explicit "human" default.
    kind = (
        character_document["kind"]
        if "kind" in character_document
        else "human"
    )
    role["kind"] = _text(kind, "character.kind")
    role = {field: role[field] for field in ROLE_FIELDS}
    if "reference_voice" not in character_document:
        raise CompletionPlanError(
            "character.reference_voice は明示的な voice id または null が必要です。",
        )
    reference_voice_value = character_document["reference_voice"]
    if reference_voice_value is not None:
        reference_voice = _path_segment(
            reference_voice_value,
            "character.reference_voice",
        )
    else:
        reference_voice = None
    snapshot_without_hash = {
        "scenario": _path_segment(scenario, "role.scenario"),
        "character": _path_segment(character, "role.character"),
        "role": role,
        "reference_voice": reference_voice,
        "scene_setting": _text(scene_setting, "role.scene_setting"),
    }
    identity_sha256 = _canonical_sha256(snapshot_without_hash)
    return RoleSnapshot(
        scenario=snapshot_without_hash["scenario"],
        character=snapshot_without_hash["character"],
        role=role,
        reference_voice=reference_voice,
        scene_setting=snapshot_without_hash["scene_setting"],
        role_identity_sha256=identity_sha256,
    )


def build_frozen_plan_document(
    *,
    scenarios_dir: Path,
    voices_dir: Path,
) -> dict[str, Any]:
    """Build the one current plan document; callers still write canonical bytes."""

    _require_absolute(scenarios_dir, "scenarios")
    _require_absolute(voices_dir, "voices")
    sources, roles, scenario_documents = _source_snapshot(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
    )
    model_documents = [
        {"id": model, "revision": revision}
        for model, revision in sorted(MODEL_REVISIONS.items())
    ]
    anchor_text_documents = [
        {
            "model": model,
            "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        for model, text in sorted(ANCHOR_TEXTS.items())
    ]
    roles_by_identity = {role.identity: role for role in roles}
    anchor_targets: list[dict[str, str]] = []
    for model in sorted(ANCHOR_TEXTS):
        revision = MODEL_REVISIONS[model]
        anchor_text_sha256 = hashlib.sha256(
            ANCHOR_TEXTS[model].encode("utf-8"),
        ).hexdigest()
        for role in roles:
            if role.reference_voice is not None:
                continue
            epoch = {
                "model": model,
                "model_revision": revision,
                "scenario": role.scenario,
                "character": role.character,
                "role_identity_sha256": role.role_identity_sha256,
                "anchor_text_sha256": anchor_text_sha256,
            }
            anchor_targets.append(
                {
                    "model": model,
                    "scenario": role.scenario,
                    "character": role.character,
                    "role_identity_sha256": role.role_identity_sha256,
                    "role_epoch_sha256": _canonical_sha256(epoch),
                },
            )
    anchor_targets.sort(
        key=lambda item: (item["model"], item["scenario"], item["character"]),
    )
    if len(roles_by_identity) != 58 or len(anchor_targets) != 106:
        raise CompletionPlanError(
            "固定 plan は58 role / 106 no-ref anchor targetが必要です。",
        )
    return {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "base": {
            "manifest_sha256": BASE_MANIFEST_SHA256,
            "git_blob": BASE_MANIFEST_GIT_BLOB,
            "candidate_set_sha256": BASE_CANDIDATE_SET_SHA256,
            "selection_sha256": BASE_SELECTION_SHA256,
            "inherited_groups": INHERITED_GROUPS,
            "final_groups": FINAL_GROUPS,
        },
        "sources": sources,
        "models": model_documents,
        "roles": [role.document() for role in roles],
        "phase_a": {
            "takes": PHASE_A_TAKES,
            "minimum_eligible_candidates": PHASE_A_MINIMUM_ELIGIBLE,
            "seed_policy": PHASE_A_SEED_POLICY,
            "seed_base": PHASE_A_SEED_BASE,
            "anchor_texts": anchor_text_documents,
            "targets": anchor_targets,
        },
        "phase_b": {
            "takes": PHASE_B_TAKES,
            "minimum_eligible_candidates": PHASE_B_MINIMUM_ELIGIBLE,
            "seed_policy": PHASE_B_SEED_POLICY,
            "seed_base": PHASE_B_SEED_BASE,
            "targets": _expected_phase_b_targets(scenario_documents),
        },
    }


def derive_anchor_seed(
    *,
    plan_sha256: str,
    seed_base: int,
    model: str,
    scenario: str,
    character: str,
    attempt: int,
) -> int:
    _sha256(plan_sha256, "plan_sha256")
    if isinstance(seed_base, bool) or not isinstance(seed_base, int):
        raise CompletionPlanError("anchor seed_base は integer が必要です。")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise CompletionPlanError("anchor attempt は1以上のintegerが必要です。")
    identity = {
        "policy": PHASE_A_SEED_POLICY,
        "seed_base": seed_base,
        "plan_sha256": plan_sha256,
        "model": _path_segment(model, "anchor.model"),
        "scenario": _path_segment(scenario, "anchor.scenario"),
        "character": _path_segment(character, "anchor.character"),
        "attempt": attempt,
    }
    return int.from_bytes(
        hashlib.sha256(canonical_json(identity).encode("utf-8")).digest()[:4],
        "big",
    )


def _validate_plan_document(
    document: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = _exact(document, ROOT_FIELDS, "completion plan")
    if root["format_version"] != FORMAT_VERSION:
        raise CompletionPlanError(
            f"completion plan format_version は {FORMAT_VERSION} が必要です。",
        )
    if root["protocol"] != PROTOCOL:
        raise CompletionPlanError(
            f"completion plan protocol は {PROTOCOL} が必要です。",
        )

    base_source = _exact(root["base"], BASE_FIELDS, "completion plan.base")
    base = {
        "manifest_sha256": _fixed_sha256(
            base_source["manifest_sha256"],
            BASE_MANIFEST_SHA256,
            "completion plan.base.manifest_sha256",
        ),
        "git_blob": _fixed_git_blob(
            base_source["git_blob"],
            BASE_MANIFEST_GIT_BLOB,
            "completion plan.base.git_blob",
        ),
        "candidate_set_sha256": _fixed_sha256(
            base_source["candidate_set_sha256"],
            BASE_CANDIDATE_SET_SHA256,
            "completion plan.base.candidate_set_sha256",
        ),
        "selection_sha256": _fixed_sha256(
            base_source["selection_sha256"],
            BASE_SELECTION_SHA256,
            "completion plan.base.selection_sha256",
        ),
        "inherited_groups": _fixed_integer(
            base_source["inherited_groups"],
            INHERITED_GROUPS,
            "completion plan.base.inherited_groups",
        ),
        "final_groups": _fixed_integer(
            base_source["final_groups"],
            FINAL_GROUPS,
            "completion plan.base.final_groups",
        ),
    }
    sources = _validate_sources(root["sources"])
    models = _validate_models(root["models"])
    roles = _validate_roles(root["roles"])
    phase_a, anchor_targets = _validate_phase_a(root["phase_a"], roles)
    phase_b, targets = _validate_phase_b(root["phase_b"])
    normalized = {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "base": base,
        "sources": sources,
        "models": models,
        "roles": [role.document() for role in roles],
        "phase_a": phase_a,
        "phase_b": phase_b,
    }
    return normalized, {
        "roles": roles,
        "anchor_targets": anchor_targets,
        "targets": targets,
    }


def _validate_sources(value: Any) -> dict[str, Any]:
    source = _exact(value, SOURCES_FIELDS, "completion plan.sources")
    scenario_files_value = source["scenario_files"]
    if not isinstance(scenario_files_value, list) or not scenario_files_value:
        raise CompletionPlanError(
            "completion plan.sources.scenario_files は空でない配列が必要です。",
        )
    scenario_files: list[dict[str, str]] = []
    for index, item_value in enumerate(scenario_files_value):
        field = f"completion plan.sources.scenario_files[{index}]"
        item = _exact(item_value, SOURCE_FILE_FIELDS, field)
        scenario = _path_segment(item["scenario"], f"{field}.scenario")
        path = _text(item["path"], f"{field}.path")
        if path != f"scenarios/{scenario}.yaml":
            raise CompletionPlanError(f"{field}.path が scenario id と一致しません。")
        scenario_files.append(
            {
                "scenario": scenario,
                "path": path,
                "sha256": _sha256(item["sha256"], f"{field}.sha256"),
            },
        )
    if scenario_files != sorted(
        scenario_files,
        key=lambda item: item["scenario"],
    ):
        raise CompletionPlanError(
            "completion plan.sources.scenario_files はcanonical順が必要です。",
        )
    if len({item["scenario"] for item in scenario_files}) != len(scenario_files):
        raise CompletionPlanError(
            "completion plan.sources.scenario_files に重複があります。",
        )
    voice_path = _text(
        source["voice_registry_path"],
        "completion plan.sources.voice_registry_path",
    )
    if voice_path != "assets/voices/metadata.yaml":
        raise CompletionPlanError(
            "completion plan.sources.voice_registry_path が不正です。",
        )
    return {
        "scenario_registry_sha256": _sha256(
            source["scenario_registry_sha256"],
            "completion plan.sources.scenario_registry_sha256",
        ),
        "scenario_files": scenario_files,
        "voice_registry_path": voice_path,
        "voice_registry_sha256": _sha256(
            source["voice_registry_sha256"],
            "completion plan.sources.voice_registry_sha256",
        ),
    }


def _validate_models(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise CompletionPlanError("completion plan.models は配列が必要です。")
    models: list[dict[str, str]] = []
    for index, item_value in enumerate(value):
        field = f"completion plan.models[{index}]"
        item = _exact(item_value, MODEL_FIELDS, field)
        models.append(
            {
                "id": _path_segment(item["id"], f"{field}.id"),
                "revision": _text(item["revision"], f"{field}.revision"),
            },
        )
    expected = [
        {"id": model, "revision": revision}
        for model, revision in sorted(MODEL_REVISIONS.items())
    ]
    if models != expected:
        raise CompletionPlanError(
            "completion plan.models は8 modelの固定revisionと一致しません。",
        )
    return models


def _validate_roles(value: Any) -> tuple[RoleSnapshot, ...]:
    if not isinstance(value, list):
        raise CompletionPlanError("completion plan.roles は配列が必要です。")
    roles: list[RoleSnapshot] = []
    for index, item_value in enumerate(value):
        field = f"completion plan.roles[{index}]"
        item = _exact(item_value, ROLE_SNAPSHOT_FIELDS, field)
        role_value = _exact(item["role"], set(ROLE_FIELDS), f"{field}.role")
        role = {
            key: _text(role_value[key], f"{field}.role.{key}")
            for key in ROLE_FIELDS
        }
        scenario = _path_segment(item["scenario"], f"{field}.scenario")
        character = _path_segment(item["character"], f"{field}.character")
        reference_value = item["reference_voice"]
        reference_voice = (
            None
            if reference_value is None
            else _path_segment(reference_value, f"{field}.reference_voice")
        )
        scene_setting = _text(item["scene_setting"], f"{field}.scene_setting")
        expected_sha = _canonical_sha256(
            {
                "scenario": scenario,
                "character": character,
                "role": role,
                "reference_voice": reference_voice,
                "scene_setting": scene_setting,
            },
        )
        identity_sha = _sha256(
            item["role_identity_sha256"],
            f"{field}.role_identity_sha256",
        )
        if identity_sha != expected_sha:
            raise CompletionPlanError(
                f"{field}.role_identity_sha256 がsnapshotと一致しません。",
            )
        roles.append(
            RoleSnapshot(
                scenario=scenario,
                character=character,
                role=role,
                reference_voice=reference_voice,
                scene_setting=scene_setting,
                role_identity_sha256=identity_sha,
            ),
        )
    if len(roles) != 58:
        raise CompletionPlanError("completion plan.roles は58件が必要です。")
    if roles != sorted(roles, key=lambda role: role.identity):
        raise CompletionPlanError("completion plan.roles はcanonical順が必要です。")
    if len({role.identity for role in roles}) != len(roles):
        raise CompletionPlanError("completion plan.roles に重複があります。")
    return tuple(roles)


def _validate_phase_a(
    value: Any,
    roles: tuple[RoleSnapshot, ...],
) -> tuple[dict[str, Any], tuple[AnchorTarget, ...]]:
    phase = _exact(value, PHASE_A_FIELDS, "completion plan.phase_a")
    takes = _fixed_integer(
        phase["takes"],
        PHASE_A_TAKES,
        "completion plan.phase_a.takes",
    )
    minimum = _fixed_integer(
        phase["minimum_eligible_candidates"],
        PHASE_A_MINIMUM_ELIGIBLE,
        "completion plan.phase_a.minimum_eligible_candidates",
    )
    seed_policy = _fixed_text(
        phase["seed_policy"],
        PHASE_A_SEED_POLICY,
        "completion plan.phase_a.seed_policy",
    )
    seed_base = _fixed_integer(
        phase["seed_base"],
        PHASE_A_SEED_BASE,
        "completion plan.phase_a.seed_base",
    )
    anchor_text_values = phase["anchor_texts"]
    if not isinstance(anchor_text_values, list):
        raise CompletionPlanError(
            "completion plan.phase_a.anchor_texts は配列が必要です。",
        )
    anchor_texts: list[dict[str, str]] = []
    for index, item_value in enumerate(anchor_text_values):
        field = f"completion plan.phase_a.anchor_texts[{index}]"
        item = _exact(item_value, ANCHOR_TEXT_FIELDS, field)
        model = _path_segment(item["model"], f"{field}.model")
        text = _text(item["text"], f"{field}.text")
        sha256 = _sha256(item["sha256"], f"{field}.sha256")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != sha256:
            raise CompletionPlanError(f"{field}.sha256 がanchor textと一致しません。")
        anchor_texts.append({"model": model, "text": text, "sha256": sha256})
    expected_anchor_texts = [
        {
            "model": model,
            "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        for model, text in sorted(ANCHOR_TEXTS.items())
    ]
    if anchor_texts != expected_anchor_texts:
        raise CompletionPlanError(
            "completion plan.phase_a.anchor_texts が固定値と一致しません。",
        )

    targets_value = phase["targets"]
    if not isinstance(targets_value, list):
        raise CompletionPlanError(
            "completion plan.phase_a.targets は配列が必要です。",
        )
    roles_by_identity = {role.identity: role for role in roles}
    targets: list[AnchorTarget] = []
    for index, item_value in enumerate(targets_value):
        field = f"completion plan.phase_a.targets[{index}]"
        item = _exact(item_value, PHASE_A_TARGET_FIELDS, field)
        model = _path_segment(item["model"], f"{field}.model")
        scenario = _path_segment(item["scenario"], f"{field}.scenario")
        character = _path_segment(item["character"], f"{field}.character")
        role_sha = _sha256(
            item["role_identity_sha256"],
            f"{field}.role_identity_sha256",
        )
        epoch_sha = _sha256(
            item["role_epoch_sha256"],
            f"{field}.role_epoch_sha256",
        )
        if model not in ANCHOR_TEXTS:
            raise CompletionPlanError(f"{field}.model はPhase A対象外です。")
        role = roles_by_identity.get((scenario, character))
        if role is None:
            raise CompletionPlanError(f"{field} に対応するroleがありません。")
        if role.reference_voice is not None:
            raise CompletionPlanError(
                f"{field} は明示reference roleをanchor対象にできません。",
            )
        if role.role_identity_sha256 != role_sha:
            raise CompletionPlanError(f"{field} のrole identityが一致しません。")
        expected_epoch = _canonical_sha256(
            {
                "model": model,
                "model_revision": MODEL_REVISIONS[model],
                "scenario": scenario,
                "character": character,
                "role_identity_sha256": role_sha,
                "anchor_text_sha256": hashlib.sha256(
                    ANCHOR_TEXTS[model].encode("utf-8"),
                ).hexdigest(),
            },
        )
        if expected_epoch != epoch_sha:
            raise CompletionPlanError(f"{field} のrole epochが一致しません。")
        targets.append(
            AnchorTarget(
                model=model,
                scenario=scenario,
                character=character,
                role_identity_sha256=role_sha,
                role_epoch_sha256=epoch_sha,
            ),
        )
    if len(targets) != 106:
        raise CompletionPlanError(
            "completion plan.phase_a.targets は106件が必要です。",
        )
    if targets != sorted(targets, key=lambda target: target.identity):
        raise CompletionPlanError(
            "completion plan.phase_a.targets はcanonical順が必要です。",
        )
    if len({target.identity for target in targets}) != len(targets):
        raise CompletionPlanError(
            "completion plan.phase_a.targets に重複があります。",
        )
    counts = {
        model: sum(target.model == model for target in targets)
        for model in ANCHOR_TEXTS
    }
    if counts != {IRODORI_MODEL: 53, QWEN_MODEL: 53}:
        raise CompletionPlanError(
            "completion plan.phase_a.targets はmodelごとに53件が必要です。",
        )
    expected_target_identities = {
        (model, role.scenario, role.character)
        for model in ANCHOR_TEXTS
        for role in roles
        if role.reference_voice is None
    }
    if {target.identity for target in targets} != expected_target_identities:
        raise CompletionPlanError(
            "completion plan.phase_a.targets が全no-ref roleと一致しません。",
        )
    return (
        {
            "takes": takes,
            "minimum_eligible_candidates": minimum,
            "seed_policy": seed_policy,
            "seed_base": seed_base,
            "anchor_texts": anchor_texts,
            "targets": [
                {
                    "model": target.model,
                    "scenario": target.scenario,
                    "character": target.character,
                    "role_identity_sha256": target.role_identity_sha256,
                    "role_epoch_sha256": target.role_epoch_sha256,
                }
                for target in targets
            ],
        },
        tuple(targets),
    )


def _validate_phase_b(
    value: Any,
) -> tuple[dict[str, Any], tuple[CompletionTarget, ...]]:
    phase = _exact(value, PHASE_B_FIELDS, "completion plan.phase_b")
    takes = _fixed_integer(
        phase["takes"],
        PHASE_B_TAKES,
        "completion plan.phase_b.takes",
    )
    minimum = _fixed_integer(
        phase["minimum_eligible_candidates"],
        PHASE_B_MINIMUM_ELIGIBLE,
        "completion plan.phase_b.minimum_eligible_candidates",
    )
    seed_policy = _fixed_text(
        phase["seed_policy"],
        PHASE_B_SEED_POLICY,
        "completion plan.phase_b.seed_policy",
    )
    seed_base = _fixed_integer(
        phase["seed_base"],
        PHASE_B_SEED_BASE,
        "completion plan.phase_b.seed_base",
    )
    targets_value = phase["targets"]
    if not isinstance(targets_value, list):
        raise CompletionPlanError(
            "completion plan.phase_b.targets は配列が必要です。",
        )
    targets: list[CompletionTarget] = []
    for index, item_value in enumerate(targets_value):
        field = f"completion plan.phase_b.targets[{index}]"
        item = _exact(item_value, PHASE_B_TARGET_FIELDS, field)
        source = item["source"]
        if source not in {"generate", "reuse"}:
            raise CompletionPlanError(f"{field}.source が不正です。")
        target = CompletionTarget(
            model=_path_segment(item["model"], f"{field}.model"),
            scenario=_path_segment(item["scenario"], f"{field}.scenario"),
            line=_path_segment(item["line"], f"{field}.line"),
            variant=_path_segment(item["variant"], f"{field}.variant"),
            source=source,
        )
        if target.variant != "dry":
            raise CompletionPlanError(f"{field}.variant はdryが必要です。")
        if (source == "reuse") != (target.model == VOXCPM_MODEL):
            raise CompletionPlanError(
                f"{field}.source reuse はVoxCPM2固定2件だけに使用できます。",
            )
        targets.append(target)
    if len(targets) != 363:
        raise CompletionPlanError(
            "completion plan.phase_b.targets は363件が必要です。",
        )
    if targets != sorted(targets, key=lambda target: target.identity):
        raise CompletionPlanError(
            "completion plan.phase_b.targets はcanonical順が必要です。",
        )
    if len({target.identity for target in targets}) != len(targets):
        raise CompletionPlanError(
            "completion plan.phase_b.targets に重複があります。",
        )
    expected_counts = {
        CHATTERBOX_MODEL: 13,
        COSYVOICE_MODEL: 14,
        GPT_SOVITS_MODEL: 12,
        IRODORI_MODEL: 161,
        QWEN_MODEL: 161,
        VOXCPM_MODEL: 2,
    }
    counts = {
        model: sum(target.model == model for target in targets)
        for model in expected_counts
    }
    if counts != expected_counts:
        raise CompletionPlanError(
            "completion plan.phase_b.targets のmodel別件数が不正です。",
        )
    return (
        {
            "takes": takes,
            "minimum_eligible_candidates": minimum,
            "seed_policy": seed_policy,
            "seed_base": seed_base,
            "targets": [
                {
                    "model": target.model,
                    "scenario": target.scenario,
                    "line": target.line,
                    "variant": target.variant,
                    "source": target.source,
                }
                for target in targets
            ],
        },
        tuple(targets),
    )


def _source_snapshot(
    *,
    scenarios_dir: Path,
    voices_dir: Path,
) -> tuple[dict[str, Any], tuple[RoleSnapshot, ...], tuple[dict[str, Any], ...]]:
    scenario_files: list[dict[str, str]] = []
    documents: list[dict[str, Any]] = []
    roles: list[RoleSnapshot] = []
    paths = sorted(scenarios_dir.glob("*.yaml"))
    if len(paths) != 15:
        raise CompletionPlanError(
            f"固定 plan は15 scenario fileが必要です: actual={len(paths)}",
        )
    for path in paths:
        raw = _read_bytes(path, "scenario")
        try:
            document = yaml.safe_load(raw.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as error:
            raise CompletionPlanError(
                f"scenario YAMLが不正です: {path}: {error}",
            ) from error
        if not isinstance(document, dict):
            raise CompletionPlanError(f"scenario はobjectが必要です: {path}")
        scenario = _path_segment(document.get("id"), f"{path}.id")
        if path.name != f"{scenario}.yaml":
            raise CompletionPlanError(
                f"scenario id とfile名が一致しません: {path}",
            )
        scene = document.get("scene")
        if not isinstance(scene, Mapping):
            raise CompletionPlanError(f"scenario.sceneが不正です: {path}")
        setting = _text(scene.get("setting"), f"{path}.scene.setting")
        characters = document.get("characters")
        if not isinstance(characters, list):
            raise CompletionPlanError(f"scenario.charactersが不正です: {path}")
        for character_value in characters:
            if not isinstance(character_value, Mapping):
                raise CompletionPlanError(f"scenario characterが不正です: {path}")
            character = _path_segment(
                character_value.get("id"),
                f"{path}.character.id",
            )
            roles.append(
                build_role_snapshot(
                    scenario=scenario,
                    character=character,
                    character_document=character_value,
                    scene_setting=setting,
                ),
            )
        scenario_files.append(
            {
                "scenario": scenario,
                "path": f"scenarios/{scenario}.yaml",
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        documents.append(document)
    scenario_files.sort(key=lambda item: item["scenario"])
    documents.sort(key=lambda item: str(item["id"]))
    roles.sort(key=lambda role: role.identity)
    voice_path = voices_dir / "metadata.yaml"
    voice_raw = _read_bytes(voice_path, "voice registry")
    sources = {
        "scenario_registry_sha256": _canonical_sha256(scenario_files),
        "scenario_files": scenario_files,
        "voice_registry_path": "assets/voices/metadata.yaml",
        "voice_registry_sha256": hashlib.sha256(voice_raw).hexdigest(),
    }
    return sources, tuple(roles), tuple(documents)


def _expected_phase_b_targets(
    scenario_documents: tuple[dict[str, Any], ...],
) -> list[dict[str, str]]:
    all_lines: list[tuple[str, str]] = []
    lines_by_character: dict[tuple[str, str], list[str]] = {}
    for document in scenario_documents:
        scenario = str(document["id"])
        for line in document["lines"]:
            line_id = str(line["id"])
            all_lines.append((scenario, line_id))
            lines_by_character.setdefault(
                (scenario, str(line["character"])),
                [],
            ).append(line_id)
    if len(all_lines) != 161:
        raise CompletionPlanError(
            f"固定 plan は161 lineが必要です: actual={len(all_lines)}",
        )
    changed_roles = (
        ("goblin-camp", "goblin-lookout"),
        ("guild-hall", "rookie"),
        ("village-morning", "teen-boy"),
        ("west-crowd", "shinbun-shounen"),
    )
    changed_lines = sorted(
        (scenario, line)
        for scenario, character in changed_roles
        for line in lines_by_character.get((scenario, character), [])
    )
    if len(changed_lines) != 12:
        raise CompletionPlanError(
            "固定 voice assignment 対象は12 lineが必要です。",
        )

    targets: list[dict[str, str]] = []
    for model in (QWEN_MODEL, IRODORI_MODEL):
        for scenario, line in all_lines:
            targets.append(_phase_b_target(model, scenario, line, "generate"))
    for scenario, line in changed_lines:
        targets.append(
            _phase_b_target(CHATTERBOX_MODEL, scenario, line, "generate"),
        )
        targets.append(
            _phase_b_target(COSYVOICE_MODEL, scenario, line, "generate"),
        )
        targets.append(
            _phase_b_target(GPT_SOVITS_MODEL, scenario, line, "generate"),
        )
    targets.extend(
        (
            _phase_b_target(
                CHATTERBOX_MODEL,
                "chinatown-street",
                "tenshin-okami-002",
                "generate",
            ),
            _phase_b_target(
                COSYVOICE_MODEL,
                "battlefield-camp",
                "wounded-001",
                "generate",
            ),
            _phase_b_target(
                COSYVOICE_MODEL,
                "west-crowd",
                "isogi-shinshi-002",
                "generate",
            ),
            _phase_b_target(
                VOXCPM_MODEL,
                "goblin-camp",
                "goblin-cook-001",
                "reuse",
            ),
            _phase_b_target(
                VOXCPM_MODEL,
                "spirit-forest",
                "pixie-003",
                "reuse",
            ),
        ),
    )
    targets.sort(
        key=lambda item: (
            item["model"],
            item["scenario"],
            item["line"],
            item["variant"],
        ),
    )
    if len(targets) != 363:
        raise CompletionPlanError(
            f"固定 phase B target は363件が必要です: actual={len(targets)}",
        )
    return targets


def _phase_b_target(
    model: str,
    scenario: str,
    line: str,
    source: str,
) -> dict[str, str]:
    return {
        "model": model,
        "scenario": scenario,
        "line": line,
        "variant": "dry",
        "source": source,
    }


def _validate_base_manifest(
    *,
    raw: bytes,
    path: Path,
    base: Mapping[str, Any],
) -> None:
    if hashlib.sha256(raw).hexdigest() != base["manifest_sha256"]:
        raise CompletionPlanError("base manifest raw SHA-256 がplanと一致しません。")
    git_blob = hashlib.sha1(  # noqa: S324 - Git object identity is SHA-1 by design.
        f"blob {len(raw)}\0".encode("ascii") + raw,
    ).hexdigest()
    if git_blob != base["git_blob"]:
        raise CompletionPlanError("base manifest Git blob がplanと一致しません。")
    document = _read_json(raw, path, "base manifest")
    if canonical_json(document).encode("utf-8") != raw:
        raise CompletionPlanError("base manifest はcanonical bytesではありません。")
    try:
        manifest = validate_manifest_v4(document)
    except TakeManifestError as error:
        raise CompletionPlanError(f"base manifest が不正です: {error}") from error
    if manifest["candidate_set_sha256"] != base["candidate_set_sha256"]:
        raise CompletionPlanError(
            "base manifest candidate-set SHAがplanと一致しません。",
        )
    terminal_count = len(manifest["curations"]) + len(manifest["failures"])
    if terminal_count != FINAL_GROUPS:
        raise CompletionPlanError(
            f"base manifest terminal groupは{FINAL_GROUPS}件が必要です。",
        )
    if any(
        item["curation_sha256"] != base["selection_sha256"]
        for item in manifest["curations"]
    ):
        raise CompletionPlanError(
            "base manifest selection SHAがplanと一致しません。",
        )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _require_absolute(path: Path, field: str) -> None:
    if not path.is_absolute():
        raise CompletionPlanError(f"{field} は絶対pathが必要です: {path}")


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise CompletionPlanError(f"{label}を読み込めません: {path}: {error}") from error


def _read_json(raw: bytes, path: Path, label: str) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CompletionPlanError(
                    f"{label}に重複JSON keyがあります: {key}",
                )
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompletionPlanError(f"{label}が不正なJSONです: {path}: {error}") from error


def _exact(value: Any, fields: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise CompletionPlanError(f"{field}の項目がexact contractと一致しません。")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CompletionPlanError(f"{field}は空でない文字列が必要です。")
    return value


def _fixed_text(value: Any, expected: str, field: str) -> str:
    text = _text(value, field)
    if text != expected:
        raise CompletionPlanError(f"{field}は{expected}が必要です。")
    return text


def _path_segment(value: Any, field: str) -> str:
    text = _text(value, field)
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise CompletionPlanError(f"{field}は安全なpath segmentが必要です。")
    return text


def _sha256(value: Any, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(character not in HEX for character in text):
        raise CompletionPlanError(f"{field}は完全な小文字SHA-256が必要です。")
    return text


def _fixed_sha256(value: Any, expected: str, field: str) -> str:
    sha256 = _sha256(value, field)
    if sha256 != expected:
        raise CompletionPlanError(f"{field}は固定baselineと一致しません。")
    return sha256


def _fixed_git_blob(value: Any, expected: str, field: str) -> str:
    text = _text(value, field)
    if len(text) != 40 or any(character not in HEX for character in text):
        raise CompletionPlanError(f"{field}は完全な小文字Git blobが必要です。")
    if text != expected:
        raise CompletionPlanError(f"{field}は固定baselineと一致しません。")
    return text


def _fixed_integer(value: Any, expected: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise CompletionPlanError(f"{field}は{expected}が必要です。")
    return value
