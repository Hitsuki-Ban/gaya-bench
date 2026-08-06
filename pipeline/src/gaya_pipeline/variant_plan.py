"""条件バリアント1列ぶんのplan契約 (#201)。

`increment_plan` は「公開済みbaseへ新規1 modelを161行まるごと足す」形だった。
条件バリアントは違う: 1列161行のうち **条件が一致する行は公開済みテイクを
byte不変で継承し、残りだけを新規生成する**。

本moduleはその分割 (`reuse.inherit` / `reuse.generate`) を、base releaseの
realized receipt から機械判定して確定する。継承側は take identity (take_id /
audio SHA / generation_input SHA) を pin するので、finalize は列を組み替える
だけで音声を作り直さない。

属性名は `CompletionPlan` / `IncrementPlan` と意図的に揃えてあり、
`completion_listen` / `completion_auto` の model 非依存 helper をそのまま使える。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from gaya_pipeline.completion_plan import (
    DERIVED_SEED_POLICY,
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
from gaya_pipeline.conditioning_variants import (
    LINES_PER_MODEL,
    MODE_TEXT_ONLY,
    ConditioningVariantError,
    conditioning_document,
    realized_conditioning_mode,
    require_base_model,
    require_mode,
    requires_anchor_authority,
    variant_model_id,
)
from gaya_pipeline.take_identity import canonical_json


class VariantPlanError(RuntimeError):
    pass


FORMAT_VERSION = 1
PROTOCOL = "role-conditioning-variant-plan-v1"
VARIANT = "dry"

ROLE_COUNT = 58
SCENARIO_COUNT = 15
COLUMN_GROUPS = LINES_PER_MODEL

# #174 (104) / #194 とも衝突しない条件バリアント専用の seed base。
VARIANT_TAKES = 4
VARIANT_MINIMUM_ELIGIBLE = 3
VARIANT_SEED_POLICY = DERIVED_SEED_POLICY
VARIANT_PRIMARY_SEED_BASE = 201

ROOT_FIELDS = {
    "format_version",
    "protocol",
    "base",
    "sources",
    "models",
    "conditioning",
    "roles",
    "anchor_authority",
    "reuse",
    "phase_b",
}
BASE_FIELDS = {
    "manifest_sha256",
    "candidate_set_sha256",
    "selection_sha256",
    "quality_signals_sha256",
    "release_provenance_sha256",
    "base_groups",
    "column_groups",
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
REUSE_FIELDS = {"inherit", "generate"}
INHERIT_FIELDS = {
    "scenario",
    "line",
    "character",
    "source_model",
    "selected_take_id",
    "selected_audio_sha256",
    "selected_generation_input_sha256",
    "candidate_take_ids",
    "realized_conditioning_mode",
}
GENERATE_FIELDS = {"scenario", "line", "character"}
PHASE_B_FIELDS = {"model_policies", "targets"}
MODEL_POLICY_FIELDS = {
    "model",
    "takes",
    "minimum_eligible_candidates",
    "seed_policy",
    "primary_seed_base",
}
PHASE_B_TARGET_FIELDS = {"model", "scenario", "line", "variant"}


@dataclass(frozen=True)
class InheritedGroup:
    scenario: str
    line: str
    character: str
    source_model: str
    selected_take_id: str
    selected_audio_sha256: str
    selected_generation_input_sha256: str
    candidate_take_ids: tuple[str, ...]
    realized_conditioning_mode: str

    @property
    def identity(self) -> tuple[str, str]:
        return (self.scenario, self.line)

    def document(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "line": self.line,
            "character": self.character,
            "source_model": self.source_model,
            "selected_take_id": self.selected_take_id,
            "selected_audio_sha256": self.selected_audio_sha256,
            "selected_generation_input_sha256": (
                self.selected_generation_input_sha256
            ),
            "candidate_take_ids": list(self.candidate_take_ids),
            "realized_conditioning_mode": self.realized_conditioning_mode,
        }


@dataclass(frozen=True)
class VariantPlan:
    plan_id: str
    model: str
    base_model: str
    conditioning_mode: str
    base_manifest_sha256: str
    base_candidate_set_sha256: str
    base_selection_sha256: str
    base_quality_signals_sha256: str
    base_release_provenance_sha256: str
    base_groups: int
    column_groups: int
    anchor_source_plan_sha256: str | None
    anchor_candidate_set_sha256: str | None
    anchor_selection_sha256: str | None
    scenario_registry_sha256: str
    scenario_files: tuple[ScenarioSource, ...]
    voice_registry_path: str
    voice_registry_sha256: str
    models: Mapping[str, str]
    roles: tuple[RoleSnapshot, ...]
    model_policies: tuple[ModelPolicy, ...]
    targets: tuple[CompletionTarget, ...]
    inherit: tuple[InheritedGroup, ...]
    line_characters: Mapping[tuple[str, str], str]
    raw_sha256: str

    def role(self, scenario: str, character: str) -> RoleSnapshot:
        matches = [
            role for role in self.roles if role.identity == (scenario, character)
        ]
        if len(matches) != 1:
            raise VariantPlanError(
                f"variant plan role が一意ではありません: {scenario}/{character}",
            )
        return matches[0]

    def policy_for_model(self, model_id: str) -> ModelPolicy:
        matches = [
            policy for policy in self.model_policies if policy.model == model_id
        ]
        if len(matches) != 1:
            raise VariantPlanError(
                f"variant plan model policy が一意ではありません: {model_id}",
            )
        return matches[0]

    def targets_for_model(self, model_id: str) -> tuple[CompletionTarget, ...]:
        return tuple(target for target in self.targets if target.model == model_id)

    def target_lines_for_model(self, model_id: str) -> tuple[tuple[str, str], ...]:
        return tuple(
            (target.scenario, target.line)
            for target in self.targets_for_model(model_id)
        )

    def all_line_identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                [(target.scenario, target.line) for target in self.targets]
                + [group.identity for group in self.inherit],
            ),
        )

    def requires_anchor_authority(self) -> bool:
        return requires_anchor_authority(self.model)

    @property
    def scenario_authority_targets(self) -> tuple[CompletionTarget, ...]:
        """scenario authority 用の全161行 target。

        Phase B の生成対象 (`targets`) は継承行を除いた部分集合だが、
        release は列の161行すべてを覆うため authority は広い集合で組む。
        """

        return tuple(
            CompletionTarget(
                model=self.model,
                scenario=scenario,
                line=line,
                variant=VARIANT,
            )
            for scenario, line in self.all_line_identities()
        )


def variant_model_policy(model: str) -> ModelPolicy:
    return ModelPolicy(
        model=_path_segment(model, "variant plan model"),
        takes=VARIANT_TAKES,
        minimum_eligible_candidates=VARIANT_MINIMUM_ELIGIBLE,
        seed_policy=VARIANT_SEED_POLICY,
        primary_seed_base=VARIANT_PRIMARY_SEED_BASE,
    )


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #


def build_variant_plan_document(
    *,
    base_model: str,
    mode: str,
    model_revision: str,
    base_release_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    anchor_source_plan_sha256: str | None = None,
    anchor_candidate_set_sha256: str | None = None,
    anchor_selection_sha256: str | None = None,
) -> dict[str, Any]:
    """公開済み base release から1列ぶんのplan documentを組む。"""

    _require_absolute(base_release_dir, "base release")
    _require_absolute(scenarios_dir, "scenarios")
    _require_absolute(voices_dir, "voices")
    base_model = require_base_model(base_model)
    mode = require_mode(mode)
    model_id = variant_model_id(base_model, mode)
    revision = _text(model_revision, "variant plan model_revision")

    sources, roles, scenario_documents = _source_snapshot(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
    )
    if len(roles) != ROLE_COUNT:
        raise VariantPlanError(
            f"variant planは{ROLE_COUNT} roleが必要です: actual={len(roles)}",
        )
    role_by_identity = {role.identity: role for role in roles}
    line_characters = _line_characters(scenario_documents)
    if len(line_characters) != COLUMN_GROUPS:
        raise VariantPlanError(
            f"variant planは{COLUMN_GROUPS} lineが必要です: "
            f"actual={len(line_characters)}",
        )

    base = _read_base_release_markers(base_release_dir)
    manifest = _read_base_document(base_release_dir / "manifest-v4.json")
    selection = _read_base_document(base_release_dir / "selection.json")
    base_groups = len(selection["groups"])

    inherit, generate = _partition_lines(
        base_model=base_model,
        mode=mode,
        manifest=manifest,
        selection=selection,
        line_characters=line_characters,
        role_by_identity=role_by_identity,
    )

    anchor_required = requires_anchor_authority(model_id)
    anchor_provided = [
        value
        for value in (
            anchor_source_plan_sha256,
            anchor_candidate_set_sha256,
            anchor_selection_sha256,
        )
        if value is not None
    ]
    if anchor_required and len(anchor_provided) != 3:
        raise VariantPlanError(
            f"{model_id} のplanにはanchor authority 3 SHAが必要です。",
        )
    if not anchor_required and anchor_provided:
        raise VariantPlanError(
            f"{model_id} のplanにanchor authorityは指定できません。",
        )
    anchor_authority = (
        None
        if not anchor_required
        else {
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
        }
    )

    return {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "base": {
            **base,
            "base_groups": base_groups,
            "column_groups": COLUMN_GROUPS,
        },
        "sources": sources,
        "models": [{"id": model_id, "revision": revision}],
        "conditioning": conditioning_document(base_model=base_model, mode=mode),
        "roles": [role.document() for role in roles],
        "anchor_authority": anchor_authority,
        "reuse": {
            "inherit": [group.document() for group in inherit],
            "generate": [
                {
                    "scenario": scenario,
                    "line": line,
                    "character": line_characters[(scenario, line)],
                }
                for scenario, line in generate
            ],
        },
        "phase_b": {
            "model_policies": [
                _model_policy_document(variant_model_policy(model_id)),
            ],
            "targets": [
                {
                    "model": model_id,
                    "scenario": scenario,
                    "line": line,
                    "variant": VARIANT,
                }
                for scenario, line in generate
            ],
        },
    }


def _partition_lines(
    *,
    base_model: str,
    mode: str,
    manifest: Mapping[str, Any],
    selection: Mapping[str, Any],
    line_characters: Mapping[tuple[str, str], str],
    role_by_identity: Mapping[tuple[str, str], RoleSnapshot],
) -> tuple[list[InheritedGroup], list[tuple[str, str]]]:
    """base列の realized receipt から inherit / generate を機械判定する。"""

    if not any(model["id"] == base_model for model in manifest["models"]):
        raise VariantPlanError(
            f"base releaseに{base_model}列がありません。",
        )
    candidates_by_group: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for candidate in manifest["candidates"]:
        if candidate["model"] != base_model or candidate["variant"] != VARIANT:
            continue
        candidates_by_group.setdefault(
            (str(candidate["scenario"]), str(candidate["line"])),
            [],
        ).append(candidate)
    selected_by_group = {
        (str(group["scenario"]), str(group["line"])): str(
            group["decision"]["take_id"],
        )
        for group in selection["groups"]
        if group["model"] == base_model and group["variant"] == VARIANT
    }
    if set(selected_by_group) != set(line_characters):
        raise VariantPlanError(
            f"base列 {base_model} は{COLUMN_GROUPS}行がexactに必要です。",
        )

    inherit: list[InheritedGroup] = []
    generate: list[tuple[str, str]] = []
    for identity in sorted(line_characters):
        character = line_characters[identity]
        role = role_by_identity[(identity[0], character)]
        group_candidates = candidates_by_group.get(identity, [])
        selected_take_id = selected_by_group[identity]
        selected = [
            candidate
            for candidate in group_candidates
            if str(candidate["take_id"]) == selected_take_id
        ]
        if len(selected) != 1:
            raise VariantPlanError(
                f"base列のselected takeが一意ではありません: {identity}",
            )
        realized = selected[0]["gen_params"]["realized"]
        try:
            actual_mode = realized_conditioning_mode(
                base_model=base_model,
                realized=realized,
            )
        except ConditioningVariantError as error:
            raise VariantPlanError(
                f"base列 takeの条件を判定できません: {identity}: {error}",
            ) from error
        # scenario 側の期待 (明示reference有無) と realized receipt の
        # 突き合わせ。どちらかがずれていれば混合条件の前提が壊れている。
        expected_mode = (
            MODE_TEXT_ONLY if role.reference_voice is None else "human-reference"
        )
        if actual_mode != expected_mode:
            raise VariantPlanError(
                "base列 takeのrealized条件がscenarioの明示referenceと矛盾します: "
                f"{identity}: realized={actual_mode}, expected={expected_mode}",
            )
        if actual_mode == mode:
            inherit.append(
                InheritedGroup(
                    scenario=identity[0],
                    line=identity[1],
                    character=character,
                    source_model=base_model,
                    selected_take_id=selected_take_id,
                    selected_audio_sha256=str(selected[0]["sha256"]),
                    selected_generation_input_sha256=str(
                        selected[0]["generation_input_sha256"],
                    ),
                    candidate_take_ids=tuple(
                        sorted(
                            str(candidate["take_id"])
                            for candidate in group_candidates
                        ),
                    ),
                    realized_conditioning_mode=actual_mode,
                ),
            )
        else:
            generate.append(identity)
    if len(inherit) + len(generate) != COLUMN_GROUPS:
        raise VariantPlanError(
            f"variant planは{COLUMN_GROUPS}行の分割が必要です。",
        )
    if not generate:
        raise VariantPlanError(
            "条件が全行一致しているためバリアント列を作る必要がありません。",
        )
    return inherit, generate


def _line_characters(
    scenario_documents: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for document in scenario_documents:
        scenario = _path_segment(str(document["id"]), "scenario id")
        for line in document["lines"]:
            identity = (
                scenario,
                _path_segment(str(line["id"]), "line id"),
            )
            if identity in result:
                raise VariantPlanError(f"line が重複しています: {identity}")
            result[identity] = _path_segment(
                str(line["character"]),
                "line character",
            )
    return result


def _read_base_release_markers(base_release_dir: Path) -> dict[str, str]:
    fields = {
        "manifest_sha256": "manifest-v4",
        "candidate_set_sha256": "candidate-set",
        "selection_sha256": "selection",
        "quality_signals_sha256": "quality-signals",
        "release_provenance_sha256": "release-provenance",
    }
    result: dict[str, str] = {}
    for field, stem in fields.items():
        document_path = base_release_dir / f"{stem}.json"
        marker_path = base_release_dir / f"{stem}.sha256"
        try:
            payload = document_path.read_bytes()
            marker = marker_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as error:
            raise VariantPlanError(
                f"base releaseを読めません: {document_path}",
            ) from error
        digest = hashlib.sha256(payload).hexdigest()
        if digest != marker:
            raise VariantPlanError(
                f"base release SHA markerが不一致です: {document_path}",
            )
        result[field] = digest
    return result


def _read_base_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VariantPlanError(f"base release documentが不正です: {path}") from error
    if not isinstance(document, dict):
        raise VariantPlanError(f"base release documentはobjectが必要です: {path}")
    return document


# --------------------------------------------------------------------------- #
# load / validate
# --------------------------------------------------------------------------- #


def compute_variant_plan_id(document: Any) -> str:
    normalized, _parsed = _validate_plan_document(document)
    return hashlib.sha256(
        canonical_json(normalized).encode("utf-8"),
    ).hexdigest()


def load_variant_plan(
    plan_path: Path,
    *,
    scenarios_dir: Path,
    voices_dir: Path,
) -> VariantPlan:
    _require_absolute(plan_path, "variant plan")
    _require_absolute(scenarios_dir, "scenarios")
    _require_absolute(voices_dir, "voices")

    plan_raw = _read_bytes(plan_path, "variant plan")
    document = _read_json(plan_raw, plan_path, "variant plan")
    normalized, parsed = _validate_plan_document(document)
    if plan_raw != canonical_json(normalized).encode("utf-8"):
        raise VariantPlanError("variant plan は canonical bytes である必要があります。")
    plan_sha256 = hashlib.sha256(plan_raw).hexdigest()

    expected_sources, expected_roles, scenario_documents = _source_snapshot(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
    )
    if normalized["sources"] != expected_sources:
        raise VariantPlanError(
            "variant plan の source snapshot が現在のscenario/voiceと一致しません。",
        )
    roles: tuple[RoleSnapshot, ...] = parsed["roles"]
    if tuple(role.document() for role in roles) != tuple(
        role.document() for role in expected_roles
    ):
        raise VariantPlanError(
            "variant plan の role snapshot が現在のscenarioと一致しません。",
        )
    line_characters = _line_characters(scenario_documents)
    covered = {
        *(
            (target.scenario, target.line)
            for target in parsed["targets"]
        ),
        *(group.identity for group in parsed["inherit"]),
    }
    if covered != set(line_characters):
        raise VariantPlanError(
            "variant plan の inherit + generate が161行を覆っていません。",
        )
    for group in parsed["inherit"]:
        if line_characters[group.identity] != group.character:
            raise VariantPlanError(
                f"variant plan inherit の character が不一致です: {group.identity}",
            )

    anchor = normalized["anchor_authority"]
    return VariantPlan(
        plan_id=plan_sha256,
        model=parsed["model"],
        base_model=normalized["conditioning"]["base_model"],
        conditioning_mode=normalized["conditioning"]["mode"],
        base_manifest_sha256=normalized["base"]["manifest_sha256"],
        base_candidate_set_sha256=normalized["base"]["candidate_set_sha256"],
        base_selection_sha256=normalized["base"]["selection_sha256"],
        base_quality_signals_sha256=normalized["base"]["quality_signals_sha256"],
        base_release_provenance_sha256=normalized["base"][
            "release_provenance_sha256"
        ],
        base_groups=normalized["base"]["base_groups"],
        column_groups=normalized["base"]["column_groups"],
        anchor_source_plan_sha256=(
            None if anchor is None else anchor["source_plan_sha256"]
        ),
        anchor_candidate_set_sha256=(
            None if anchor is None else anchor["candidate_set_sha256"]
        ),
        anchor_selection_sha256=(
            None if anchor is None else anchor["selection_sha256"]
        ),
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
        inherit=parsed["inherit"],
        line_characters=line_characters,
        raw_sha256=plan_sha256,
    )


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
    root = _exact_fields(document, ROOT_FIELDS, "variant plan")
    if root["format_version"] != FORMAT_VERSION:
        raise VariantPlanError(
            f"variant plan format_version は {FORMAT_VERSION} が必要です。",
        )
    if root["protocol"] != PROTOCOL:
        raise VariantPlanError(f"variant plan protocol は {PROTOCOL} が必要です。")

    conditioning = _exact_fields(
        root["conditioning"],
        {"base_model", "mode"},
        "variant plan.conditioning",
    )
    base_model = require_base_model(conditioning["base_model"])
    mode = require_mode(conditioning["mode"])
    model_id = variant_model_id(base_model, mode)

    base = _validate_base(root["base"])
    sources = _validate_sources(root["sources"])
    models = _validate_models(root["models"], model_id=model_id)
    roles = _validate_roles(root["roles"])
    anchor_authority = _validate_anchor_authority(
        root["anchor_authority"],
        model_id=model_id,
    )
    inherit, generate = _validate_reuse(root["reuse"], mode=mode)
    model_policies, targets = _validate_phase_b(
        root["phase_b"],
        model=model_id,
        generate=generate,
    )

    normalized = {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "base": base,
        "sources": sources,
        "models": models,
        "conditioning": {"base_model": base_model, "mode": mode},
        "roles": [role.document() for role in roles],
        "anchor_authority": anchor_authority,
        "reuse": {
            "inherit": [group.document() for group in inherit],
            "generate": [dict(item) for item in generate],
        },
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
        "model": model_id,
        "models": {item["id"]: item["revision"] for item in models},
        "roles": roles,
        "model_policies": model_policies,
        "targets": targets,
        "inherit": inherit,
    }
    return normalized, parsed


def _validate_base(value: Any) -> dict[str, Any]:
    base = _exact_fields(value, BASE_FIELDS, "variant plan.base")
    normalized = {
        field: _sha256(base[field], f"variant plan.base.{field}")
        for field in sorted(BASE_FIELDS - {"base_groups", "column_groups"})
    }
    for field in ("base_groups", "column_groups"):
        number = base[field]
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise VariantPlanError(f"variant plan.base.{field}が不正です。")
        normalized[field] = number
    if normalized["column_groups"] != COLUMN_GROUPS:
        raise VariantPlanError(
            f"variant plan.base.column_groupsは{COLUMN_GROUPS}が必要です。",
        )
    if normalized["base_groups"] % COLUMN_GROUPS != 0:
        raise VariantPlanError(
            "variant plan.base.base_groupsは列数×161が必要です。",
        )
    return normalized


def _validate_sources(value: Any) -> dict[str, Any]:
    sources = _exact_fields(value, SOURCES_FIELDS, "variant plan.sources")
    files = sources["scenario_files"]
    if not isinstance(files, list) or len(files) != SCENARIO_COUNT:
        raise VariantPlanError(
            f"variant plan.sources.scenario_filesは{SCENARIO_COUNT}件が必要です。",
        )
    normalized_files: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(files):
        entry = _exact_fields(
            item,
            SOURCE_FILE_FIELDS,
            f"variant plan.sources.scenario_files[{index}]",
        )
        scenario = _path_segment(entry["scenario"], "scenario_files.scenario")
        if scenario in seen:
            raise VariantPlanError(
                f"variant plan.sources.scenario_filesが重複しています: {scenario}",
            )
        seen.add(scenario)
        path = _text(entry["path"], "scenario_files.path")
        if path != f"scenarios/{scenario}.yaml":
            raise VariantPlanError(
                f"variant plan.sources.scenario_files.pathが不正です: {path}",
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
        raise VariantPlanError(
            "variant plan.sources.scenario_filesはscenario昇順が必要です。",
        )
    registry_sha = hashlib.sha256(
        canonical_json(normalized_files).encode("utf-8"),
    ).hexdigest()
    if (
        _sha256(
            sources["scenario_registry_sha256"],
            "variant plan.sources.scenario_registry_sha256",
        )
        != registry_sha
    ):
        raise VariantPlanError(
            "variant plan.sources.scenario_registry_sha256が不一致です。",
        )
    voice_path = _text(
        sources["voice_registry_path"],
        "variant plan.sources.voice_registry_path",
    )
    if voice_path != "assets/voices/metadata.yaml":
        raise VariantPlanError("variant plan.sources.voice_registry_pathが不正です。")
    return {
        "scenario_registry_sha256": registry_sha,
        "scenario_files": normalized_files,
        "voice_registry_path": voice_path,
        "voice_registry_sha256": _sha256(
            sources["voice_registry_sha256"],
            "variant plan.sources.voice_registry_sha256",
        ),
    }


def _validate_models(value: Any, *, model_id: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != 1:
        raise VariantPlanError("variant plan.modelsは1 modelだけが必要です。")
    entry = _exact_fields(value[0], MODEL_FIELDS, "variant plan.models[0]")
    if entry["id"] != model_id:
        raise VariantPlanError(
            "variant plan.models[0].idがconditioningと一致しません。",
        )
    return [
        {
            "id": model_id,
            "revision": _text(entry["revision"], "variant plan.models[0].revision"),
        },
    ]


def _validate_roles(value: Any) -> tuple[RoleSnapshot, ...]:
    if not isinstance(value, list) or len(value) != ROLE_COUNT:
        raise VariantPlanError(f"variant plan.rolesは{ROLE_COUNT}件が必要です。")
    roles: list[RoleSnapshot] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        entry = _exact_fields(
            item,
            ROLE_SNAPSHOT_FIELDS,
            f"variant plan.roles[{index}]",
        )
        role_value = entry["role"]
        if not isinstance(role_value, dict):
            raise VariantPlanError(f"variant plan.roles[{index}].roleが不正です。")
        reference_voice = entry["reference_voice"]
        if reference_voice is not None:
            reference_voice = _path_segment(
                reference_voice,
                f"variant plan.roles[{index}].reference_voice",
            )
        snapshot_without_hash = {
            "scenario": _path_segment(entry["scenario"], "roles.scenario"),
            "character": _path_segment(entry["character"], "roles.character"),
            "role": {
                key: _text(role_value[key], f"role.{key}")
                for key in sorted(role_value)
            },
            "reference_voice": reference_voice,
            "scene_setting": _text(entry["scene_setting"], "roles.scene_setting"),
        }
        identity_sha256 = hashlib.sha256(
            canonical_json(snapshot_without_hash).encode("utf-8"),
        ).hexdigest()
        if (
            _sha256(
                entry["role_identity_sha256"],
                f"variant plan.roles[{index}].role_identity_sha256",
            )
            != identity_sha256
        ):
            raise VariantPlanError(
                f"variant plan.roles[{index}].role_identity_sha256が不一致です。",
            )
        identity = (
            snapshot_without_hash["scenario"],
            snapshot_without_hash["character"],
        )
        if identity in seen:
            raise VariantPlanError(f"variant plan.rolesが重複しています: {identity}")
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
        raise VariantPlanError("variant plan.rolesはscenario/character昇順が必要です。")
    return tuple(roles)


def _validate_anchor_authority(
    value: Any,
    *,
    model_id: str,
) -> dict[str, str] | None:
    required = requires_anchor_authority(model_id)
    if value is None:
        if required:
            raise VariantPlanError(
                f"{model_id} のplanにはanchor authorityが必要です。",
            )
        return None
    if not required:
        raise VariantPlanError(
            f"{model_id} のplanにanchor authorityは指定できません。",
        )
    authority = _exact_fields(
        value,
        ANCHOR_AUTHORITY_FIELDS,
        "variant plan.anchor_authority",
    )
    return {
        field: _sha256(
            authority[field],
            f"variant plan.anchor_authority.{field}",
        )
        for field in sorted(ANCHOR_AUTHORITY_FIELDS)
    }


def _validate_reuse(
    value: Any,
    *,
    mode: str,
) -> tuple[tuple[InheritedGroup, ...], list[dict[str, str]]]:
    reuse = _exact_fields(value, REUSE_FIELDS, "variant plan.reuse")
    inherit_value = reuse["inherit"]
    generate_value = reuse["generate"]
    if not isinstance(inherit_value, list) or not isinstance(generate_value, list):
        raise VariantPlanError("variant plan.reuseは配列が必要です。")
    if len(inherit_value) + len(generate_value) != COLUMN_GROUPS:
        raise VariantPlanError(
            f"variant plan.reuseは合計{COLUMN_GROUPS}行が必要です。",
        )
    if not generate_value:
        raise VariantPlanError("variant plan.reuse.generateは1件以上が必要です。")

    inherit: list[InheritedGroup] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(inherit_value):
        entry = _exact_fields(
            item,
            INHERIT_FIELDS,
            f"variant plan.reuse.inherit[{index}]",
        )
        identity = (
            _path_segment(entry["scenario"], "inherit.scenario"),
            _path_segment(entry["line"], "inherit.line"),
        )
        if identity in seen:
            raise VariantPlanError(f"variant plan.reuseが重複しています: {identity}")
        seen.add(identity)
        if entry["realized_conditioning_mode"] != mode:
            raise VariantPlanError(
                "継承行のrealized条件がvariant modeと一致しません: "
                f"{identity}: {entry['realized_conditioning_mode']} != {mode}",
            )
        take_ids = entry["candidate_take_ids"]
        if (
            not isinstance(take_ids, list)
            or not take_ids
            or take_ids != sorted(take_ids)
            or len(set(take_ids)) != len(take_ids)
        ):
            raise VariantPlanError(
                f"variant plan.reuse.inherit[{index}].candidate_take_idsが不正です。",
            )
        normalized_take_ids = tuple(
            _sha256(take_id, "inherit.candidate_take_ids[]") for take_id in take_ids
        )
        selected_take_id = _sha256(
            entry["selected_take_id"],
            "inherit.selected_take_id",
        )
        if selected_take_id not in normalized_take_ids:
            raise VariantPlanError(
                "inherit.selected_take_idがcandidate_take_idsにありません: "
                f"{identity}",
            )
        inherit.append(
            InheritedGroup(
                scenario=identity[0],
                line=identity[1],
                character=_path_segment(entry["character"], "inherit.character"),
                source_model=require_base_model(entry["source_model"]),
                selected_take_id=selected_take_id,
                selected_audio_sha256=_sha256(
                    entry["selected_audio_sha256"],
                    "inherit.selected_audio_sha256",
                ),
                selected_generation_input_sha256=_sha256(
                    entry["selected_generation_input_sha256"],
                    "inherit.selected_generation_input_sha256",
                ),
                candidate_take_ids=normalized_take_ids,
                realized_conditioning_mode=mode,
            ),
        )
    if [group.identity for group in inherit] != sorted(
        group.identity for group in inherit
    ):
        raise VariantPlanError(
            "variant plan.reuse.inheritはscenario/line昇順が必要です。",
        )

    generate: list[dict[str, str]] = []
    for index, item in enumerate(generate_value):
        entry = _exact_fields(
            item,
            GENERATE_FIELDS,
            f"variant plan.reuse.generate[{index}]",
        )
        identity = (
            _path_segment(entry["scenario"], "generate.scenario"),
            _path_segment(entry["line"], "generate.line"),
        )
        if identity in seen:
            raise VariantPlanError(f"variant plan.reuseが重複しています: {identity}")
        seen.add(identity)
        generate.append(
            {
                "scenario": identity[0],
                "line": identity[1],
                "character": _path_segment(
                    entry["character"],
                    "generate.character",
                ),
            },
        )
    if [(item["scenario"], item["line"]) for item in generate] != sorted(
        (item["scenario"], item["line"]) for item in generate
    ):
        raise VariantPlanError(
            "variant plan.reuse.generateはscenario/line昇順が必要です。",
        )
    return tuple(inherit), generate


def _validate_phase_b(
    value: Any,
    *,
    model: str,
    generate: Sequence[Mapping[str, str]],
) -> tuple[tuple[ModelPolicy, ...], tuple[CompletionTarget, ...]]:
    phase_b = _exact_fields(value, PHASE_B_FIELDS, "variant plan.phase_b")
    policies_value = phase_b["model_policies"]
    if not isinstance(policies_value, list) or len(policies_value) != 1:
        raise VariantPlanError("variant plan.phase_b.model_policiesは1件が必要です。")
    policy_entry = _exact_fields(
        policies_value[0],
        MODEL_POLICY_FIELDS,
        "variant plan.phase_b.model_policies[0]",
    )
    expected_policy = variant_model_policy(model)
    if policy_entry != _model_policy_document(expected_policy):
        raise VariantPlanError(
            "variant plan.phase_b.model_policiesが既定policyと一致しません。",
        )

    targets_value = phase_b["targets"]
    if not isinstance(targets_value, list) or len(targets_value) != len(generate):
        raise VariantPlanError(
            "variant plan.phase_b.targetsはreuse.generateと同数が必要です。",
        )
    targets: list[CompletionTarget] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, item in enumerate(targets_value):
        entry = _exact_fields(
            item,
            PHASE_B_TARGET_FIELDS,
            f"variant plan.phase_b.targets[{index}]",
        )
        if entry["model"] != model:
            raise VariantPlanError(
                "variant plan.phase_b.targetsは単一modelだけが必要です。",
            )
        if entry["variant"] != VARIANT:
            raise VariantPlanError(
                f"variant plan.phase_b.targets[{index}].variantは{VARIANT}が必要です。",
            )
        target = CompletionTarget(
            model=model,
            scenario=_path_segment(entry["scenario"], "target.scenario"),
            line=_path_segment(entry["line"], "target.line"),
            variant=VARIANT,
        )
        if target.identity in seen:
            raise VariantPlanError(
                f"variant plan.phase_b.targetsが重複しています: {target.identity}",
            )
        seen.add(target.identity)
        targets.append(target)
    if [target.identity for target in targets] != sorted(
        target.identity for target in targets
    ):
        raise VariantPlanError(
            "variant plan.phase_b.targetsはscenario/line昇順が必要です。",
        )
    if {(target.scenario, target.line) for target in targets} != {
        (item["scenario"], item["line"]) for item in generate
    }:
        raise VariantPlanError(
            "variant plan.phase_b.targetsがreuse.generateと一致しません。",
        )
    return (expected_policy,), tuple(targets)


def _exact_fields(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    try:
        return _exact(value, fields, label)
    except Exception as error:  # noqa: BLE001 - completion_plan 側の例外種別を束ねる
        raise VariantPlanError(str(error)) from error


__all__ = [
    "COLUMN_GROUPS",
    "FORMAT_VERSION",
    "PROTOCOL",
    "ROLE_COUNT",
    "VARIANT",
    "VARIANT_MINIMUM_ELIGIBLE",
    "VARIANT_PRIMARY_SEED_BASE",
    "VARIANT_SEED_POLICY",
    "VARIANT_TAKES",
    "InheritedGroup",
    "VariantPlan",
    "VariantPlanError",
    "build_variant_plan_document",
    "compute_variant_plan_id",
    "load_variant_plan",
    "variant_model_policy",
]
