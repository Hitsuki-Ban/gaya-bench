"""公開済みbaselineへ1 modelを増分追加するplan契約。

`completion_plan` は8 model / 106 anchor group / 597 replacement を
SHA凍結した公開release証跡であり、in-place拡張はできない。
本moduleは同じ素材(scenario/voice snapshot・role identity・seed policy)から
**単一の新規model**だけを対象にした並行planを組み立てる。
凍結側の定数は一切変更せず、汎用helperだけを再利用する。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from gaya_pipeline.completion_plan import (
    DERIVED_SEED_POLICY,
    HEX,
    CompletionTarget,
    ModelPolicy,
    RoleSnapshot,
    ScenarioSource,
    _exact,
    _path_segment,
    _read_bytes,
    _read_json,
    _require_absolute,
    _sha256,
    _source_snapshot,
    _text,
)
from gaya_pipeline.take_identity import canonical_json


class IncrementPlanError(RuntimeError):
    pass


FORMAT_VERSION = 1
PROTOCOL = "role-increment-plan-v1"
VARIANT = "dry"

# 公開済み 8 model x 161 line baseline (#174 release 174-full-baseline-auto-v1)。
# publish で data/manifest.json / data/quality-signals.json へ activate 済みのbytes。
BASE_MANIFEST_SHA256 = (
    "5bd13d6b90007c1f597d6e593dc886000f0afe31f1b919ed92ac7163a6aea351"
)
BASE_CANDIDATE_SET_SHA256 = (
    "5287cee156f8212c8249f202931717dc4ef448410fc3c83371650b6d5ff28fdd"
)
BASE_SELECTION_SHA256 = (
    "250ed85b8f9d4e7adad2dd227dfd2ac5ee35ab23267d040333eab97d3b4a95db"
)
BASE_QUALITY_SIGNALS_SHA256 = (
    "f8592bec1c7911f165ffbf9e1f74727e5a2cf62ba6b38bf0f001f9bc26b66e45"
)
BASE_RELEASE_PROVENANCE_SHA256 = (
    "f775cd2d4d8d752151cddaa0cf02b6d76b98a692b16ba2ec30ec626ae24d612e"
)
BASE_GROUPS = 1_288
INCREMENT_GROUPS = 161
FINAL_GROUPS = BASE_GROUPS + INCREMENT_GROUPS
ROLE_COUNT = 58
SCENARIO_COUNT = 15

# #174 の non-aivisspeech policy と同一 (takes=4 / minimum 3 / derived-sha256-v1)。
# seed_base だけは frozen plan の 104 と衝突しないよう増分専用値を採る。
INCREMENT_TAKES = 4
INCREMENT_MINIMUM_ELIGIBLE = 3
INCREMENT_SEED_POLICY = DERIVED_SEED_POLICY
INCREMENT_PRIMARY_SEED_BASE = 194

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
    "candidate_set_sha256",
    "selection_sha256",
    "quality_signals_sha256",
    "release_provenance_sha256",
    "base_groups",
    "increment_groups",
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


@dataclass(frozen=True)
class IncrementPlan:
    """`CompletionPlan` と同じ属性名で1 model分のPhase B契約を表す。

    `completion_listen` / `completion_auto` / `completion_release` の
    model非依存helperをそのまま再利用できるよう、属性名は意図的に揃える。
    """

    plan_id: str
    model: str
    base_manifest_sha256: str
    base_candidate_set_sha256: str
    base_selection_sha256: str
    base_quality_signals_sha256: str
    base_release_provenance_sha256: str
    base_groups: int
    increment_groups: int
    final_groups: int
    anchor_source_plan_sha256: str
    anchor_candidate_set_sha256: str
    anchor_selection_sha256: str
    scenario_registry_sha256: str
    scenario_files: tuple[ScenarioSource, ...]
    voice_registry_path: str
    voice_registry_sha256: str
    models: Mapping[str, str]
    roles: tuple[RoleSnapshot, ...]
    model_policies: tuple[ModelPolicy, ...]
    targets: tuple[CompletionTarget, ...]
    raw_sha256: str

    def role(self, scenario: str, character: str) -> RoleSnapshot:
        matches = [
            role for role in self.roles if role.identity == (scenario, character)
        ]
        if len(matches) != 1:
            raise IncrementPlanError(
                f"increment plan role が一意ではありません: {scenario}/{character}",
            )
        return matches[0]

    def policy_for_model(self, model_id: str) -> ModelPolicy:
        matches = [
            policy for policy in self.model_policies if policy.model == model_id
        ]
        if len(matches) != 1:
            raise IncrementPlanError(
                f"increment plan model policy が一意ではありません: {model_id}",
            )
        return matches[0]

    def targets_for_model(self, model_id: str) -> tuple[CompletionTarget, ...]:
        return tuple(target for target in self.targets if target.model == model_id)

    def target_lines_for_model(self, model_id: str) -> tuple[tuple[str, str], ...]:
        return tuple(
            (target.scenario, target.line)
            for target in self.targets_for_model(model_id)
        )

    def anchor_roles(self) -> tuple[RoleSnapshot, ...]:
        """anchorが必要なrole (explicit reference を持たない全role)。"""

        return tuple(role for role in self.roles if role.reference_voice is None)


def increment_model_policy(model: str) -> ModelPolicy:
    return ModelPolicy(
        model=_path_segment(model, "increment plan model"),
        takes=INCREMENT_TAKES,
        minimum_eligible_candidates=INCREMENT_MINIMUM_ELIGIBLE,
        seed_policy=INCREMENT_SEED_POLICY,
        primary_seed_base=INCREMENT_PRIMARY_SEED_BASE,
    )


def build_increment_plan_document(
    *,
    model: str,
    model_revision: str,
    scenarios_dir: Path,
    voices_dir: Path,
    anchor_source_plan_sha256: str,
    anchor_candidate_set_sha256: str,
    anchor_selection_sha256: str,
) -> dict[str, Any]:
    """増分plan documentを組む。callerがcanonical bytesで書き出す。"""

    _require_absolute(scenarios_dir, "scenarios")
    _require_absolute(voices_dir, "voices")
    model_id = _path_segment(model, "increment plan model")
    revision = _text(model_revision, "increment plan model_revision")
    sources, roles, scenario_documents = _source_snapshot(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
    )
    if len(roles) != ROLE_COUNT:
        raise IncrementPlanError(
            f"increment planは{ROLE_COUNT} roleが必要です: actual={len(roles)}",
        )
    targets = _increment_targets(model_id, scenario_documents)
    return {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "base": {
            "manifest_sha256": BASE_MANIFEST_SHA256,
            "candidate_set_sha256": BASE_CANDIDATE_SET_SHA256,
            "selection_sha256": BASE_SELECTION_SHA256,
            "quality_signals_sha256": BASE_QUALITY_SIGNALS_SHA256,
            "release_provenance_sha256": BASE_RELEASE_PROVENANCE_SHA256,
            "base_groups": BASE_GROUPS,
            "increment_groups": INCREMENT_GROUPS,
            "final_groups": FINAL_GROUPS,
        },
        "sources": sources,
        "models": [{"id": model_id, "revision": revision}],
        "roles": [role.document() for role in roles],
        "anchor_authority": {
            "source_plan_sha256": _sha256(
                anchor_source_plan_sha256,
                "anchor_authority.source_plan_sha256",
            ),
            "candidate_set_sha256": _sha256(
                anchor_candidate_set_sha256,
                "anchor_authority.candidate_set_sha256",
            ),
            "selection_sha256": _sha256(
                anchor_selection_sha256,
                "anchor_authority.selection_sha256",
            ),
        },
        "phase_b": {
            "model_policies": [
                _model_policy_document(increment_model_policy(model_id)),
            ],
            "targets": targets,
        },
    }


def compute_increment_plan_id(document: Any) -> str:
    normalized, _parsed = _validate_plan_document(document)
    return hashlib.sha256(
        canonical_json(normalized).encode("utf-8"),
    ).hexdigest()


def load_increment_plan(
    plan_path: Path,
    *,
    scenarios_dir: Path,
    voices_dir: Path,
) -> IncrementPlan:
    """canonical bytesの増分planを読み、source snapshotと突き合わせる。"""

    _require_absolute(plan_path, "increment plan")
    _require_absolute(scenarios_dir, "scenarios")
    _require_absolute(voices_dir, "voices")

    plan_raw = _read_bytes(plan_path, "increment plan")
    document = _read_json(plan_raw, plan_path, "increment plan")
    normalized, parsed = _validate_plan_document(document)
    if plan_raw != canonical_json(normalized).encode("utf-8"):
        raise IncrementPlanError(
            "increment plan は canonical bytes である必要があります。",
        )
    plan_sha256 = hashlib.sha256(plan_raw).hexdigest()

    expected_sources, expected_roles, _documents = _source_snapshot(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
    )
    if normalized["sources"] != expected_sources:
        raise IncrementPlanError(
            "increment plan の source snapshot が現在のscenario/voiceと一致しません。",
        )
    roles: tuple[RoleSnapshot, ...] = parsed["roles"]
    if tuple(role.document() for role in roles) != tuple(
        role.document() for role in expected_roles
    ):
        raise IncrementPlanError(
            "increment plan の role snapshot が現在のscenarioと一致しません。",
        )

    return IncrementPlan(
        plan_id=plan_sha256,
        model=parsed["model"],
        base_manifest_sha256=normalized["base"]["manifest_sha256"],
        base_candidate_set_sha256=normalized["base"]["candidate_set_sha256"],
        base_selection_sha256=normalized["base"]["selection_sha256"],
        base_quality_signals_sha256=normalized["base"]["quality_signals_sha256"],
        base_release_provenance_sha256=normalized["base"][
            "release_provenance_sha256"
        ],
        base_groups=normalized["base"]["base_groups"],
        increment_groups=normalized["base"]["increment_groups"],
        final_groups=normalized["base"]["final_groups"],
        anchor_source_plan_sha256=normalized["anchor_authority"][
            "source_plan_sha256"
        ],
        anchor_candidate_set_sha256=normalized["anchor_authority"][
            "candidate_set_sha256"
        ],
        anchor_selection_sha256=normalized["anchor_authority"]["selection_sha256"],
        scenario_registry_sha256=normalized["sources"]["scenario_registry_sha256"],
        scenario_files=tuple(
            ScenarioSource(**source)
            for source in normalized["sources"]["scenario_files"]
        ),
        voice_registry_path=normalized["sources"]["voice_registry_path"],
        voice_registry_sha256=normalized["sources"]["voice_registry_sha256"],
        models=parsed["models"],
        roles=roles,
        model_policies=parsed["model_policies"],
        targets=parsed["targets"],
        raw_sha256=plan_sha256,
    )


def _increment_targets(
    model: str,
    scenario_documents: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """新規modelは全161 lineを対象にする (方式差の全面比較が増分の目的)。"""

    targets: list[dict[str, str]] = []
    for document in scenario_documents:
        scenario = _path_segment(document["id"], "increment target.scenario")
        for line in document["lines"]:
            targets.append(
                {
                    "model": model,
                    "scenario": scenario,
                    "line": _path_segment(line["id"], "increment target.line"),
                    "variant": VARIANT,
                },
            )
    if len(targets) != INCREMENT_GROUPS:
        raise IncrementPlanError(
            f"increment planは{INCREMENT_GROUPS} targetが必要です: "
            f"actual={len(targets)}",
        )
    targets.sort(key=lambda item: (item["scenario"], item["line"]))
    return targets


def _model_policy_document(policy: ModelPolicy) -> dict[str, Any]:
    return {
        "model": policy.model,
        "takes": policy.takes,
        "minimum_eligible_candidates": policy.minimum_eligible_candidates,
        "seed_policy": policy.seed_policy,
        "primary_seed_base": policy.primary_seed_base,
    }


def _validate_plan_document(
    document: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = _exact(document, ROOT_FIELDS, "increment plan")
    if root["format_version"] != FORMAT_VERSION:
        raise IncrementPlanError(
            f"increment plan format_version は {FORMAT_VERSION} が必要です。",
        )
    if root["protocol"] != PROTOCOL:
        raise IncrementPlanError(
            f"increment plan protocol は {PROTOCOL} が必要です。",
        )

    base = _validate_base(root["base"])
    sources = _validate_sources(root["sources"])
    models = _validate_models(root["models"])
    model = models[0]["id"]
    roles = _validate_roles(root["roles"])
    anchor_authority = _validate_anchor_authority(root["anchor_authority"])
    model_policies, targets = _validate_phase_b(root["phase_b"], model=model)

    normalized = {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "base": base,
        "sources": sources,
        "models": models,
        "roles": [role.document() for role in roles],
        "anchor_authority": anchor_authority,
        "phase_b": {
            "model_policies": [
                _model_policy_document(policy) for policy in model_policies
            ],
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
    }
    parsed = {
        "model": model,
        "models": {item["id"]: item["revision"] for item in models},
        "roles": roles,
        "model_policies": model_policies,
        "targets": targets,
    }
    return normalized, parsed


def _validate_base(value: Any) -> dict[str, Any]:
    base = _exact(value, BASE_FIELDS, "increment plan.base")
    normalized = {
        "manifest_sha256": _fixed_sha256(
            base["manifest_sha256"],
            BASE_MANIFEST_SHA256,
            "increment plan.base.manifest_sha256",
        ),
        "candidate_set_sha256": _fixed_sha256(
            base["candidate_set_sha256"],
            BASE_CANDIDATE_SET_SHA256,
            "increment plan.base.candidate_set_sha256",
        ),
        "selection_sha256": _fixed_sha256(
            base["selection_sha256"],
            BASE_SELECTION_SHA256,
            "increment plan.base.selection_sha256",
        ),
        "quality_signals_sha256": _fixed_sha256(
            base["quality_signals_sha256"],
            BASE_QUALITY_SIGNALS_SHA256,
            "increment plan.base.quality_signals_sha256",
        ),
        "release_provenance_sha256": _fixed_sha256(
            base["release_provenance_sha256"],
            BASE_RELEASE_PROVENANCE_SHA256,
            "increment plan.base.release_provenance_sha256",
        ),
        "base_groups": _fixed_integer(
            base["base_groups"],
            BASE_GROUPS,
            "increment plan.base.base_groups",
        ),
        "increment_groups": _fixed_integer(
            base["increment_groups"],
            INCREMENT_GROUPS,
            "increment plan.base.increment_groups",
        ),
        "final_groups": _fixed_integer(
            base["final_groups"],
            FINAL_GROUPS,
            "increment plan.base.final_groups",
        ),
    }
    return normalized


def _validate_sources(value: Any) -> dict[str, Any]:
    sources = _exact(value, SOURCES_FIELDS, "increment plan.sources")
    files = sources["scenario_files"]
    if not isinstance(files, list) or len(files) != SCENARIO_COUNT:
        raise IncrementPlanError(
            f"increment plan.sources.scenario_filesは{SCENARIO_COUNT}件が必要です。",
        )
    normalized_files: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(files):
        entry = _exact(
            item,
            SOURCE_FILE_FIELDS,
            f"increment plan.sources.scenario_files[{index}]",
        )
        scenario = _path_segment(entry["scenario"], "scenario_files.scenario")
        if scenario in seen:
            raise IncrementPlanError(
                f"increment plan.sources.scenario_filesが重複しています: {scenario}",
            )
        seen.add(scenario)
        path = _text(entry["path"], "scenario_files.path")
        if path != f"scenarios/{scenario}.yaml":
            raise IncrementPlanError(
                f"increment plan.sources.scenario_files.pathが不正です: {path}",
            )
        normalized_files.append(
            {
                "scenario": scenario,
                "path": path,
                "sha256": _sha256(entry["sha256"], "scenario_files.sha256"),
            },
        )
    normalized_files.sort(key=lambda item: item["scenario"])
    if [item["scenario"] for item in files] != [
        item["scenario"] for item in normalized_files
    ]:
        raise IncrementPlanError(
            "increment plan.sources.scenario_filesはscenario昇順が必要です。",
        )
    registry_sha = hashlib.sha256(
        canonical_json(normalized_files).encode("utf-8"),
    ).hexdigest()
    if (
        _sha256(
            sources["scenario_registry_sha256"],
            "increment plan.sources.scenario_registry_sha256",
        )
        != registry_sha
    ):
        raise IncrementPlanError(
            "increment plan.sources.scenario_registry_sha256が不一致です。",
        )
    voice_path = _text(
        sources["voice_registry_path"],
        "increment plan.sources.voice_registry_path",
    )
    if voice_path != "assets/voices/metadata.yaml":
        raise IncrementPlanError(
            "increment plan.sources.voice_registry_pathが不正です。",
        )
    return {
        "scenario_registry_sha256": registry_sha,
        "scenario_files": normalized_files,
        "voice_registry_path": voice_path,
        "voice_registry_sha256": _sha256(
            sources["voice_registry_sha256"],
            "increment plan.sources.voice_registry_sha256",
        ),
    }


def _validate_models(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != 1:
        raise IncrementPlanError(
            "increment plan.modelsは新規1 modelだけが必要です。",
        )
    entry = _exact(value[0], MODEL_FIELDS, "increment plan.models[0]")
    return [
        {
            "id": _path_segment(entry["id"], "increment plan.models[0].id"),
            "revision": _text(
                entry["revision"],
                "increment plan.models[0].revision",
            ),
        },
    ]


def _validate_roles(value: Any) -> tuple[RoleSnapshot, ...]:
    if not isinstance(value, list) or len(value) != ROLE_COUNT:
        raise IncrementPlanError(
            f"increment plan.rolesは{ROLE_COUNT}件が必要です。",
        )
    roles: list[RoleSnapshot] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        entry = _exact(item, ROLE_SNAPSHOT_FIELDS, f"increment plan.roles[{index}]")
        role_value = entry["role"]
        if not isinstance(role_value, dict):
            raise IncrementPlanError(f"increment plan.roles[{index}].roleが不正です。")
        reference_voice = entry["reference_voice"]
        if reference_voice is not None:
            reference_voice = _path_segment(
                reference_voice,
                f"increment plan.roles[{index}].reference_voice",
            )
        snapshot_without_hash = {
            "scenario": _path_segment(
                entry["scenario"],
                f"increment plan.roles[{index}].scenario",
            ),
            "character": _path_segment(
                entry["character"],
                f"increment plan.roles[{index}].character",
            ),
            "role": {
                key: _text(role_value[key], f"role.{key}")
                for key in sorted(role_value)
            },
            "reference_voice": reference_voice,
            "scene_setting": _text(
                entry["scene_setting"],
                f"increment plan.roles[{index}].scene_setting",
            ),
        }
        identity_sha256 = hashlib.sha256(
            canonical_json(snapshot_without_hash).encode("utf-8"),
        ).hexdigest()
        if (
            _sha256(
                entry["role_identity_sha256"],
                f"increment plan.roles[{index}].role_identity_sha256",
            )
            != identity_sha256
        ):
            raise IncrementPlanError(
                f"increment plan.roles[{index}].role_identity_sha256が不一致です。",
            )
        identity = (
            snapshot_without_hash["scenario"],
            snapshot_without_hash["character"],
        )
        if identity in seen:
            raise IncrementPlanError(
                f"increment plan.rolesが重複しています: {identity}",
            )
        seen.add(identity)
        roles.append(
            RoleSnapshot(
                scenario=identity[0],
                character=identity[1],
                role=snapshot_without_hash["role"],
                reference_voice=reference_voice,
                scene_setting=snapshot_without_hash["scene_setting"],
                role_identity_sha256=identity_sha256,
            ),
        )
    if [role.identity for role in roles] != sorted(role.identity for role in roles):
        raise IncrementPlanError("increment plan.rolesはscenario/character昇順が必要です。")
    return tuple(roles)


def _validate_anchor_authority(value: Any) -> dict[str, str]:
    authority = _exact(
        value,
        ANCHOR_AUTHORITY_FIELDS,
        "increment plan.anchor_authority",
    )
    return {
        field: _sha256(
            authority[field],
            f"increment plan.anchor_authority.{field}",
        )
        for field in sorted(ANCHOR_AUTHORITY_FIELDS)
    }


def _validate_phase_b(
    value: Any,
    *,
    model: str,
) -> tuple[tuple[ModelPolicy, ...], tuple[CompletionTarget, ...]]:
    phase_b = _exact(value, PHASE_B_FIELDS, "increment plan.phase_b")
    policies_value = phase_b["model_policies"]
    if not isinstance(policies_value, list) or len(policies_value) != 1:
        raise IncrementPlanError(
            "increment plan.phase_b.model_policiesは1件が必要です。",
        )
    policy_entry = _exact(
        policies_value[0],
        MODEL_POLICY_FIELDS,
        "increment plan.phase_b.model_policies[0]",
    )
    expected_policy = increment_model_policy(model)
    if policy_entry != _model_policy_document(expected_policy):
        raise IncrementPlanError(
            "increment plan.phase_b.model_policiesが増分既定policyと一致しません。",
        )

    targets_value = phase_b["targets"]
    if not isinstance(targets_value, list) or len(targets_value) != INCREMENT_GROUPS:
        raise IncrementPlanError(
            f"increment plan.phase_b.targetsは{INCREMENT_GROUPS}件が必要です。",
        )
    targets: list[CompletionTarget] = []
    seen: set[GroupIdentity] = set()
    for index, item in enumerate(targets_value):
        entry = _exact(
            item,
            PHASE_B_TARGET_FIELDS,
            f"increment plan.phase_b.targets[{index}]",
        )
        if entry["model"] != model:
            raise IncrementPlanError(
                "increment plan.phase_b.targetsは単一modelだけが必要です。",
            )
        if entry["variant"] != VARIANT:
            raise IncrementPlanError(
                f"increment plan.phase_b.targets[{index}].variantは{VARIANT}が必要です。",
            )
        target = CompletionTarget(
            model=model,
            scenario=_path_segment(entry["scenario"], "target.scenario"),
            line=_path_segment(entry["line"], "target.line"),
            variant=VARIANT,
        )
        if target.identity in seen:
            raise IncrementPlanError(
                f"increment plan.phase_b.targetsが重複しています: {target.identity}",
            )
        seen.add(target.identity)
        targets.append(target)
    if [target.identity for target in targets] != sorted(
        target.identity for target in targets
    ):
        raise IncrementPlanError(
            "increment plan.phase_b.targetsはscenario/line昇順が必要です。",
        )
    return (expected_policy,), tuple(targets)


def _fixed_sha256(value: Any, expected: str, field: str) -> str:
    digest = _sha256(value, field)
    if digest != expected:
        raise IncrementPlanError(f"{field}は{expected}が必要です。")
    return digest


def _fixed_integer(value: Any, expected: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise IncrementPlanError(f"{field}は{expected}が必要です。")
    return value


__all__ = [
    "BASE_CANDIDATE_SET_SHA256",
    "BASE_GROUPS",
    "BASE_MANIFEST_SHA256",
    "BASE_QUALITY_SIGNALS_SHA256",
    "BASE_RELEASE_PROVENANCE_SHA256",
    "BASE_SELECTION_SHA256",
    "FINAL_GROUPS",
    "FORMAT_VERSION",
    "HEX",
    "INCREMENT_GROUPS",
    "INCREMENT_MINIMUM_ELIGIBLE",
    "INCREMENT_PRIMARY_SEED_BASE",
    "INCREMENT_SEED_POLICY",
    "INCREMENT_TAKES",
    "PROTOCOL",
    "IncrementPlan",
    "IncrementPlanError",
    "build_increment_plan_document",
    "compute_increment_plan_id",
    "increment_model_policy",
    "load_increment_plan",
]
