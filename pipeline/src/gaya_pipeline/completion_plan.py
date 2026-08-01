from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.take_manifest_v4 import TakeManifestError, validate_manifest_v4


class CompletionPlanError(RuntimeError):
    pass


FORMAT_VERSION = 2
PROTOCOL = "role-baseline-plan-v2"
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
ANCHOR_SOURCE_PLAN_SHA256 = (
    "f21f7ffa598c38b24f345b8c05f4d18fe3073618deaa742bb55ff30e0a26a0e5"
)
ANCHOR_CANDIDATE_SET_SHA256 = (
    "9ff3bb11452ca80899944121edaba5e9a361a1cd8000a1ef716375e673062765"
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

NO_SEED_POLICY = "none"
DERIVED_SEED_POLICY = "derived-sha256-v1"
PRIMARY_SEED_BASE = 104
INHERITED_GROUPS = 691
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
    "anchor_authority",
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
ANCHOR_AUTHORITY_FIELDS = {
    "source_plan_sha256",
    "candidate_set_sha256",
    "selection_sha256",
}
PHASE_B_FIELDS = {"model_policies", "targets"}
MODEL_POLICY_FIELDS = {
    "model",
    "takes",
    "minimum_eligible_candidates",
    "seed_policy",
    "primary_seed_base",
}
PHASE_B_TARGET_FIELDS = {"model", "scenario", "line", "variant"}

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
class CompletionTarget:
    model: str
    scenario: str
    line: str
    variant: str

    @property
    def identity(self) -> GroupIdentity:
        return (self.model, self.scenario, self.line, self.variant)


@dataclass(frozen=True)
class ModelPolicy:
    model: str
    takes: int
    minimum_eligible_candidates: int
    seed_policy: str
    primary_seed_base: int | None


@dataclass(frozen=True)
class CompletionPlan:
    plan_id: str
    base_manifest_sha256: str
    base_manifest_git_blob: str
    base_candidate_set_sha256: str
    base_selection_sha256: str
    anchor_source_plan_sha256: str
    anchor_candidate_set_sha256: str
    anchor_selection_sha256: str
    inherited_groups: int
    final_groups: int
    scenario_registry_sha256: str
    voice_registry_sha256: str
    models: Mapping[str, str]
    roles: tuple[RoleSnapshot, ...]
    model_policies: tuple[ModelPolicy, ...]
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

    def policy_for_model(self, model_id: str) -> ModelPolicy:
        matches = [
            policy for policy in self.model_policies if policy.model == model_id
        ]
        if len(matches) != 1:
            raise CompletionPlanError(
                f"plan model policy が一意ではありません: {model_id}",
            )
        return matches[0]

    def targets_for_model(self, model_id: str) -> tuple[CompletionTarget, ...]:
        return tuple(target for target in self.targets if target.model == model_id)

    def target_lines_for_model(self, model_id: str) -> tuple[tuple[str, str], ...]:
        return tuple(
            (target.scenario, target.line)
            for target in self.targets_for_model(model_id)
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
            "completion plan phase_b.targets が597 groupの固定対象と一致しません。",
        )

    models = {
        item["id"]: item["revision"] for item in normalized["models"]
    }
    roles = tuple(parsed["roles"])
    model_policies = tuple(parsed["model_policies"])
    targets = tuple(parsed["targets"])
    return CompletionPlan(
        plan_id=plan_sha256,
        base_manifest_sha256=normalized["base"]["manifest_sha256"],
        base_manifest_git_blob=normalized["base"]["git_blob"],
        base_candidate_set_sha256=normalized["base"]["candidate_set_sha256"],
        base_selection_sha256=normalized["base"]["selection_sha256"],
        anchor_source_plan_sha256=normalized["anchor_authority"][
            "source_plan_sha256"
        ],
        anchor_candidate_set_sha256=normalized["anchor_authority"][
            "candidate_set_sha256"
        ],
        anchor_selection_sha256=normalized["anchor_authority"][
            "selection_sha256"
        ],
        inherited_groups=normalized["base"]["inherited_groups"],
        final_groups=normalized["base"]["final_groups"],
        scenario_registry_sha256=normalized["sources"][
            "scenario_registry_sha256"
        ],
        voice_registry_sha256=normalized["sources"]["voice_registry_sha256"],
        models=models,
        roles=roles,
        model_policies=model_policies,
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
    anchor_selection_sha256: str,
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
    if len(roles) != 58:
        raise CompletionPlanError("固定 plan は58 roleが必要です。")
    anchor_selection = _sha256(
        anchor_selection_sha256,
        "anchor_selection_sha256",
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
        "anchor_authority": {
            "source_plan_sha256": ANCHOR_SOURCE_PLAN_SHA256,
            "candidate_set_sha256": ANCHOR_CANDIDATE_SET_SHA256,
            "selection_sha256": anchor_selection,
        },
        "phase_b": {
            "model_policies": _expected_model_policy_documents(),
            "targets": _expected_phase_b_targets(scenario_documents),
        },
    }


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
    anchor_authority = _validate_anchor_authority(root["anchor_authority"])
    phase_b, model_policies, targets = _validate_phase_b(root["phase_b"])
    normalized = {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "base": base,
        "sources": sources,
        "models": models,
        "roles": [role.document() for role in roles],
        "anchor_authority": anchor_authority,
        "phase_b": phase_b,
    }
    return normalized, {
        "roles": roles,
        "model_policies": model_policies,
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


def _validate_anchor_authority(value: Any) -> dict[str, str]:
    authority = _exact(
        value,
        ANCHOR_AUTHORITY_FIELDS,
        "completion plan.anchor_authority",
    )
    return {
        "source_plan_sha256": _fixed_sha256(
            authority["source_plan_sha256"],
            ANCHOR_SOURCE_PLAN_SHA256,
            "completion plan.anchor_authority.source_plan_sha256",
        ),
        "candidate_set_sha256": _fixed_sha256(
            authority["candidate_set_sha256"],
            ANCHOR_CANDIDATE_SET_SHA256,
            "completion plan.anchor_authority.candidate_set_sha256",
        ),
        "selection_sha256": _sha256(
            authority["selection_sha256"],
            "completion plan.anchor_authority.selection_sha256",
        ),
    }


def _validate_phase_b(
    value: Any,
) -> tuple[
    dict[str, Any],
    tuple[ModelPolicy, ...],
    tuple[CompletionTarget, ...],
]:
    phase = _exact(value, PHASE_B_FIELDS, "completion plan.phase_b")
    policies_value = phase["model_policies"]
    if not isinstance(policies_value, list):
        raise CompletionPlanError(
            "completion plan.phase_b.model_policies は配列が必要です。",
        )
    policies: list[ModelPolicy] = []
    normalized_policies: list[dict[str, Any]] = []
    for index, item_value in enumerate(policies_value):
        field = f"completion plan.phase_b.model_policies[{index}]"
        item = _exact(item_value, MODEL_POLICY_FIELDS, field)
        model = _path_segment(item["model"], f"{field}.model")
        expected = _expected_model_policy(model)
        policy = ModelPolicy(
            model=model,
            takes=_fixed_integer(
                item["takes"],
                expected.takes,
                f"{field}.takes",
            ),
            minimum_eligible_candidates=_fixed_integer(
                item["minimum_eligible_candidates"],
                expected.minimum_eligible_candidates,
                f"{field}.minimum_eligible_candidates",
            ),
            seed_policy=_fixed_text(
                item["seed_policy"],
                expected.seed_policy,
                f"{field}.seed_policy",
            ),
            primary_seed_base=_fixed_optional_integer(
                item["primary_seed_base"],
                expected.primary_seed_base,
                f"{field}.primary_seed_base",
            ),
        )
        policies.append(policy)
        normalized_policies.append(_model_policy_document(policy))
    if normalized_policies != _expected_model_policy_documents():
        raise CompletionPlanError(
            "completion plan.phase_b.model_policies は8 modelの固定policyを"
            "canonical順で指定する必要があります。",
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
        target = CompletionTarget(
            model=_path_segment(item["model"], f"{field}.model"),
            scenario=_path_segment(item["scenario"], f"{field}.scenario"),
            line=_path_segment(item["line"], f"{field}.line"),
            variant=_path_segment(item["variant"], f"{field}.variant"),
        )
        if target.variant != "dry":
            raise CompletionPlanError(f"{field}.variant はdryが必要です。")
        targets.append(target)
    if len(targets) != 597:
        raise CompletionPlanError(
            "completion plan.phase_b.targets は597件が必要です。",
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
        "aivisspeech-kohaku": 25,
        CHATTERBOX_MODEL: 13,
        COSYVOICE_MODEL: 14,
        GPT_SOVITS_MODEL: 37,
        IRODORI_MODEL: 161,
        QWEN_MODEL: 161,
        "supertonic-3": 25,
        VOXCPM_MODEL: 161,
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
            "model_policies": normalized_policies,
            "targets": [
                {
                    "model": target.model,
                    "scenario": target.scenario,
                    "line": target.line,
                    "variant": target.variant,
                }
                for target in targets
            ],
        },
        tuple(policies),
        tuple(targets),
    )


def _expected_model_policy(model: str) -> ModelPolicy:
    if model not in MODEL_REVISIONS:
        raise CompletionPlanError(
            f"completion plan model policy が不明です: {model}",
        )
    if model == "aivisspeech-kohaku":
        return ModelPolicy(
            model=model,
            takes=1,
            minimum_eligible_candidates=1,
            seed_policy=NO_SEED_POLICY,
            primary_seed_base=None,
        )
    return ModelPolicy(
        model=model,
        takes=4,
        minimum_eligible_candidates=3,
        seed_policy=DERIVED_SEED_POLICY,
        primary_seed_base=PRIMARY_SEED_BASE,
    )


def _model_policy_document(policy: ModelPolicy) -> dict[str, Any]:
    return {
        "model": policy.model,
        "takes": policy.takes,
        "minimum_eligible_candidates": policy.minimum_eligible_candidates,
        "seed_policy": policy.seed_policy,
        "primary_seed_base": policy.primary_seed_base,
    }


def _expected_model_policy_documents() -> list[dict[str, Any]]:
    return [
        _model_policy_document(_expected_model_policy(model))
        for model in sorted(MODEL_REVISIONS)
    ]


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
    explicit_reading_lines: list[tuple[str, str]] = []
    lines_by_character: dict[tuple[str, str], list[str]] = {}
    for document in scenario_documents:
        scenario = str(document["id"])
        for line in document["lines"]:
            line_id = str(line["id"])
            all_lines.append((scenario, line_id))
            if "reading" in line:
                explicit_reading_lines.append((scenario, line_id))
            lines_by_character.setdefault(
                (scenario, str(line["character"])),
                [],
            ).append(line_id)
    if len(all_lines) != 161:
        raise CompletionPlanError(
            f"固定 plan は161 lineが必要です: actual={len(all_lines)}",
        )
    if len(explicit_reading_lines) != 25:
        raise CompletionPlanError(
            "固定 reading 影響対象は25 lineが必要です。",
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
    for model in (IRODORI_MODEL, QWEN_MODEL, VOXCPM_MODEL):
        for scenario, line in all_lines:
            targets.append(_phase_b_target(model, scenario, line))
    for model in (
        "aivisspeech-kohaku",
        GPT_SOVITS_MODEL,
        "supertonic-3",
    ):
        for scenario, line in explicit_reading_lines:
            targets.append(_phase_b_target(model, scenario, line))
    for scenario, line in changed_lines:
        targets.append(_phase_b_target(CHATTERBOX_MODEL, scenario, line))
        targets.append(_phase_b_target(COSYVOICE_MODEL, scenario, line))
        targets.append(_phase_b_target(GPT_SOVITS_MODEL, scenario, line))
    targets.extend(
        (
            _phase_b_target(
                CHATTERBOX_MODEL,
                "chinatown-street",
                "tenshin-okami-002",
            ),
            _phase_b_target(
                COSYVOICE_MODEL,
                "battlefield-camp",
                "wounded-001",
            ),
            _phase_b_target(
                COSYVOICE_MODEL,
                "west-crowd",
                "isogi-shinshi-002",
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
    if len(targets) != 597:
        raise CompletionPlanError(
            f"固定 phase B target は597件が必要です: actual={len(targets)}",
        )
    return targets


def _phase_b_target(
    model: str,
    scenario: str,
    line: str,
) -> dict[str, str]:
    return {
        "model": model,
        "scenario": scenario,
        "line": line,
        "variant": "dry",
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


def _fixed_optional_integer(
    value: Any,
    expected: int | None,
    field: str,
) -> int | None:
    if expected is None:
        if value is not None:
            raise CompletionPlanError(f"{field}はnullが必要です。")
        return None
    return _fixed_integer(value, expected, field)
