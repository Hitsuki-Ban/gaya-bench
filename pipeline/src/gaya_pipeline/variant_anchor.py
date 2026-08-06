"""`--text` バリアント列のための 58 役 anchor authority 合成 (#201)。

`--text` 列は **全58役** を役別anchorへ条件付ける。既存の権限は役の一部しか
覆っていない:

- `irodori-tts-600m-v3-voicedesign` / `qwen3-tts-12hz-1.7b`
  → #174 の人手選抜 `role-anchor-selection-v1` (2 model x 53 no-reference役)
- `irodori-tts-v4-small`
  → #194 の機械選抜 `role-anchor-machine-selection-v1` (53 no-reference役)
- 明示reference 5役 → **どのmodelにも存在しない**。`increment anchor-bootstrap`
  の `role_scope=explicit-reference-roles-v1` で新規に機械選抜する

本moduleはその2系統を **byte単位で束縛したまま1つの58役 authority へ合成** する。
既存selectionは一切書き換えず、SHAとrole epochを引用するだけなので監査は連続する。

VoxCPM2 は adapter 内蔵の voice design (自己参照) が text-only 経路そのもので、
anchor WAV を必要としない。したがって本moduleの対象は3 modelだけ。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from gaya_pipeline.completion_anchor import SelectedRoleAnchor
from gaya_pipeline.completion_plan import RoleSnapshot
from gaya_pipeline.conditioning_variants import (
    MODE_TEXT_ONLY,
    ANCHOR_BASE_MODELS,
    require_base_model,
    variant_model_id,
)
from gaya_pipeline.increment_anchor import (
    IncrementAnchorError,
    ROLE_SCOPE_EXPLICIT_REFERENCE,
    ROLE_SCOPE_NO_REFERENCE,
    validate_any_anchor_selection,
)
from gaya_pipeline.take_identity import canonical_json


class VariantAnchorError(RuntimeError):
    pass


PLAN_FORMAT_VERSION = 1
PLAN_PROTOCOL = "role-anchor-variant-plan-v1"
SELECTION_FORMAT_VERSION = 1
SELECTION_PROTOCOL = "role-anchor-variant-selection-v1"
ROLE_EPOCH_PROTOCOL = "variant-role-epoch-v1"

ROLE_COUNT = 58
AUTHORITY_HUMAN = "human-selected-v1"
AUTHORITY_AUTO = "auto-selected-v1"

_PLAN_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "model",
    "base_model",
    "conditioning_mode",
    "model_revision",
    "anchor_text",
    "anchor_text_sha256",
    "inputs",
    "targets",
}
_PLAN_INPUT_FIELDS = {
    "protocol",
    "model",
    "plan_sha256",
    "candidate_set_sha256",
    "selection_sha256",
    "role_scope",
    "role_count",
}
_PLAN_TARGET_FIELDS = {
    "scenario",
    "character",
    "role_identity_sha256",
    "source_selection_sha256",
    "source_role_epoch_sha256",
    "role_epoch_sha256",
}
_SELECTION_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_sha256",
    "candidate_set_sha256",
    "model",
    "base_model",
    "conditioning_mode",
    "model_revision",
    "groups",
}
_SELECTION_GROUP_FIELDS = {
    "model",
    "model_revision",
    "scenario",
    "character",
    "role_identity",
    "role_identity_sha256",
    "source_role_epoch_sha256",
    "role_epoch_sha256",
    "anchor_id",
    "attempt",
    "seed",
    "audio_path",
    "audio_sha256",
    "anchor_text",
    "anchor_text_sha256",
    "decision_sha256",
    "authority_type",
    "source",
}
_SELECTION_SOURCE_FIELDS = {
    "protocol",
    "model",
    "plan_sha256",
    "candidate_set_sha256",
    "selection_sha256",
}
_ROLE_IDENTITY_FIELDS = {
    "scenario",
    "character",
    "role",
    "reference_voice",
    "scene_setting",
}


# --------------------------------------------------------------------------- #
# role epoch
# --------------------------------------------------------------------------- #


def variant_role_epoch_sha256(
    *,
    model: str,
    model_revision: str,
    conditioning_mode: str,
    scenario: str,
    character: str,
    role_identity_sha256: str,
    source_selection_sha256: str,
    source_role_epoch_sha256: str,
) -> str:
    """由来selectionのepochを variant 列へ写す決定論的束縛。"""

    return _canonical_sha256(
        {
            "protocol": ROLE_EPOCH_PROTOCOL,
            "model": model,
            "model_revision": model_revision,
            "conditioning_mode": conditioning_mode,
            "scenario": scenario,
            "character": character,
            "role_identity_sha256": role_identity_sha256,
            "source_selection_sha256": source_selection_sha256,
            "source_role_epoch_sha256": source_role_epoch_sha256,
        },
    )


# --------------------------------------------------------------------------- #
# 合成
# --------------------------------------------------------------------------- #


class VariantAnchorCompositionSummary:
    __slots__ = (
        "output_dir",
        "plan_path",
        "plan_sha256",
        "selection_path",
        "selection_sha256",
        "group_count",
        "inherited_count",
        "supplement_count",
    )

    def __init__(
        self,
        *,
        output_dir: Path,
        plan_path: Path,
        plan_sha256: str,
        selection_path: Path,
        selection_sha256: str,
        group_count: int,
        inherited_count: int,
        supplement_count: int,
    ) -> None:
        self.output_dir = output_dir
        self.plan_path = plan_path
        self.plan_sha256 = plan_sha256
        self.selection_path = selection_path
        self.selection_sha256 = selection_sha256
        self.group_count = group_count
        self.inherited_count = inherited_count
        self.supplement_count = supplement_count


def compose_variant_anchor_selection(
    *,
    base_model: str,
    model_revision: str,
    inherited_selection_path: Path,
    supplement_selection_path: Path,
    output_dir: Path,
) -> VariantAnchorCompositionSummary:
    """既存53役 + 新規5役を 58 役の variant anchor authority へ合成する。

    出力は `anchor-select` と同じ write-once layout:
      `<output>/role-anchor-variant-plan-v1.json` (+ `.sha256`)
      `<output>/role-anchor-variant-selection-v1.json` (+ `.sha256`)
      `<output>/audio/<anchor_id>.wav`
    """

    base_model = require_base_model(base_model)
    if base_model not in ANCHOR_BASE_MODELS:
        raise VariantAnchorError(
            "variant anchor合成はanchor型modelだけが対象です "
            f"(VoxCPM2はvoice designが text-only 経路): {base_model}",
        )
    for path, label in (
        (inherited_selection_path, "inherited anchor selection"),
        (supplement_selection_path, "supplement anchor selection"),
        (output_dir, "variant anchor output"),
    ):
        if not path.is_absolute():
            raise VariantAnchorError(f"{label}は絶対pathが必要です: {path}")
    if output_dir.exists():
        raise VariantAnchorError(
            f"variant anchor outputは既存pathを拒否します: {output_dir}",
        )

    model_id = variant_model_id(base_model, MODE_TEXT_ONLY)
    inherited = _load_source_selection(
        inherited_selection_path,
        base_model=base_model,
        model_revision=model_revision,
        expected_scope=ROLE_SCOPE_NO_REFERENCE,
    )
    supplement = _load_source_selection(
        supplement_selection_path,
        base_model=base_model,
        model_revision=model_revision,
        expected_scope=ROLE_SCOPE_EXPLICIT_REFERENCE,
    )

    groups: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    copy_jobs: list[tuple[Path, str, str]] = []
    seen: set[tuple[str, str]] = set()
    anchor_texts: set[str] = set()
    for source in (inherited, supplement):
        for group in source["groups"]:
            identity = (group["scenario"], group["character"])
            if identity in seen:
                raise VariantAnchorError(
                    f"variant anchorのroleが重複しています: {identity}",
                )
            seen.add(identity)
            anchor_texts.add(str(group["anchor_text"]))
            epoch = variant_role_epoch_sha256(
                model=model_id,
                model_revision=model_revision,
                conditioning_mode=MODE_TEXT_ONLY,
                scenario=identity[0],
                character=identity[1],
                role_identity_sha256=group["role_identity_sha256"],
                source_selection_sha256=source["selection_sha256"],
                source_role_epoch_sha256=group["role_epoch_sha256"],
            )
            relative = f"audio/{group['anchor_id']}.wav"
            groups.append(
                {
                    "model": model_id,
                    "model_revision": model_revision,
                    "scenario": identity[0],
                    "character": identity[1],
                    "role_identity": dict(group["role_identity"]),
                    "role_identity_sha256": group["role_identity_sha256"],
                    "source_role_epoch_sha256": group["role_epoch_sha256"],
                    "role_epoch_sha256": epoch,
                    "anchor_id": group["anchor_id"],
                    "attempt": int(group["attempt"]),
                    "seed": int(group["seed"]),
                    "audio_path": relative,
                    "audio_sha256": group["audio_sha256"],
                    "anchor_text": group["anchor_text"],
                    "anchor_text_sha256": group["anchor_text_sha256"],
                    "decision_sha256": group["decision_sha256"],
                    "authority_type": source["authority_type"],
                    "source": dict(source["binding"]),
                },
            )
            targets.append(
                {
                    "scenario": identity[0],
                    "character": identity[1],
                    "role_identity_sha256": group["role_identity_sha256"],
                    "source_selection_sha256": source["selection_sha256"],
                    "source_role_epoch_sha256": group["role_epoch_sha256"],
                    "role_epoch_sha256": epoch,
                },
            )
            copy_jobs.append(
                (
                    source["root"] / str(group["audio_path"]),
                    relative,
                    str(group["audio_sha256"]),
                ),
            )
    if len(groups) != ROLE_COUNT:
        raise VariantAnchorError(
            f"variant anchor authorityはexact {ROLE_COUNT} 役が必要です: "
            f"actual={len(groups)}",
        )
    if len(anchor_texts) != 1:
        raise VariantAnchorError(
            "由来selectionのanchor textが一致しません: "
            f"{sorted(anchor_texts)}",
        )
    anchor_text = anchor_texts.pop()
    groups.sort(key=lambda item: (item["scenario"], item["character"]))
    targets.sort(key=lambda item: (item["scenario"], item["character"]))

    plan_document = {
        "format_version": PLAN_FORMAT_VERSION,
        "protocol": PLAN_PROTOCOL,
        "model": model_id,
        "base_model": base_model,
        "conditioning_mode": MODE_TEXT_ONLY,
        "model_revision": model_revision,
        "anchor_text": anchor_text,
        "anchor_text_sha256": hashlib.sha256(
            anchor_text.encode("utf-8"),
        ).hexdigest(),
        "inputs": sorted(
            (
                {**dict(source["binding"]), **{
                    "role_scope": source["role_scope"],
                    "role_count": len(source["groups"]),
                }}
                for source in (inherited, supplement)
            ),
            key=lambda item: item["selection_sha256"],
        ),
        "targets": targets,
    }
    plan_document = validate_variant_anchor_plan(plan_document)
    plan_payload = canonical_json(plan_document).encode("utf-8")
    plan_sha256 = hashlib.sha256(plan_payload).hexdigest()

    candidate_set_sha256 = _canonical_sha256(
        [dict(source["binding"]) for source in (inherited, supplement)],
    )
    selection = validate_variant_anchor_selection(
        {
            "format_version": SELECTION_FORMAT_VERSION,
            "protocol": SELECTION_PROTOCOL,
            "plan_sha256": plan_sha256,
            "candidate_set_sha256": candidate_set_sha256,
            "model": model_id,
            "base_model": base_model,
            "conditioning_mode": MODE_TEXT_ONLY,
            "model_revision": model_revision,
            "groups": groups,
        },
    )
    selection_payload = canonical_json(selection).encode("utf-8")
    selection_sha256 = hashlib.sha256(selection_payload).hexdigest()

    pending = output_dir.with_name(f".{output_dir.name}.pending")
    if pending.exists():
        raise VariantAnchorError(f"pending outputが残っています: {pending}")
    pending.mkdir(parents=True)
    try:
        for source_path, relative, expected_sha256 in copy_jobs:
            destination = pending / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
            _verify_file_sha256(destination, expected_sha256)
        _write_new(pending / f"{PLAN_PROTOCOL}.json", plan_payload)
        _write_new(
            pending / f"{PLAN_PROTOCOL}.sha256",
            f"{plan_sha256}\n".encode("ascii"),
        )
        _write_new(pending / f"{SELECTION_PROTOCOL}.json", selection_payload)
        _write_new(
            pending / f"{SELECTION_PROTOCOL}.sha256",
            f"{selection_sha256}\n".encode("ascii"),
        )
        if output_dir.exists():
            raise VariantAnchorError(
                f"variant anchor outputは既存pathを拒否します: {output_dir}",
            )
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        pending.rename(output_dir)
    except Exception:
        shutil.rmtree(pending, ignore_errors=True)
        raise

    return VariantAnchorCompositionSummary(
        output_dir=output_dir,
        plan_path=output_dir / f"{PLAN_PROTOCOL}.json",
        plan_sha256=plan_sha256,
        selection_path=output_dir / f"{SELECTION_PROTOCOL}.json",
        selection_sha256=selection_sha256,
        group_count=len(groups),
        inherited_count=len(inherited["groups"]),
        supplement_count=len(supplement["groups"]),
    )


def _load_source_selection(
    path: Path,
    *,
    base_model: str,
    model_revision: str,
    expected_scope: str,
) -> dict[str, Any]:
    raw = _read_bytes(path, "anchor selection")
    document = _decode_json(raw, path)
    try:
        selection = validate_any_anchor_selection(document)
    except IncrementAnchorError as error:
        raise VariantAnchorError(f"anchor selectionが不正です: {error}") from error
    except Exception as error:  # noqa: BLE001 - 由来protocolのエラーを束ねる
        raise VariantAnchorError(f"anchor selectionが不正です: {error}") from error
    if canonical_json(selection).encode("utf-8") != raw:
        raise VariantAnchorError("anchor selectionはcanonical bytesが必要です。")
    digest = hashlib.sha256(raw).hexdigest()
    marker = path.with_suffix(".sha256")
    try:
        marker_value = marker.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise VariantAnchorError(
            f"anchor selection SHA markerを読めません: {marker}",
        ) from error
    if marker_value != digest:
        raise VariantAnchorError("anchor selection SHA markerが不一致です。")

    protocol = str(selection["protocol"])
    scope = str(selection.get("role_scope", ROLE_SCOPE_NO_REFERENCE))
    if scope != expected_scope:
        raise VariantAnchorError(
            f"anchor selectionのrole_scopeが期待と一致しません: "
            f"expected={expected_scope}, actual={scope}",
        )
    groups = [
        dict(group)
        for group in selection["groups"]
        if str(group["model"]) == base_model
    ]
    if not groups:
        raise VariantAnchorError(
            f"anchor selectionに{base_model}のgroupがありません: {path}",
        )
    for group in groups:
        if str(group["model_revision"]) != model_revision:
            raise VariantAnchorError(
                "anchor selectionのmodel_revisionがadapterと一致しません。",
            )
    authority_type = (
        AUTHORITY_AUTO
        if protocol == "role-anchor-machine-selection-v1"
        else AUTHORITY_HUMAN
    )
    return {
        "root": path.parent,
        "selection_sha256": digest,
        "role_scope": scope,
        "authority_type": authority_type,
        "groups": sorted(
            groups,
            key=lambda item: (item["scenario"], item["character"]),
        ),
        "binding": {
            "protocol": protocol,
            "model": base_model,
            "plan_sha256": str(selection["plan_sha256"]),
            "candidate_set_sha256": str(selection["candidate_set_sha256"]),
            "selection_sha256": digest,
        },
    }


# --------------------------------------------------------------------------- #
# 契約検証
# --------------------------------------------------------------------------- #


def validate_variant_anchor_plan(document: Any) -> dict[str, Any]:
    root = _exact(document, _PLAN_ROOT_FIELDS, "variant anchor plan")
    if (
        root["format_version"] != PLAN_FORMAT_VERSION
        or root["protocol"] != PLAN_PROTOCOL
    ):
        raise VariantAnchorError("variant anchor plan identityが不正です。")
    base_model = require_base_model(root["base_model"])
    if root["conditioning_mode"] != MODE_TEXT_ONLY:
        raise VariantAnchorError(
            f"variant anchor planは{MODE_TEXT_ONLY}だけが対象です。",
        )
    if root["model"] != variant_model_id(base_model, MODE_TEXT_ONLY):
        raise VariantAnchorError("variant anchor plan.modelが不正です。")
    anchor_text = _text(root["anchor_text"], "variant anchor plan.anchor_text")
    if (
        hashlib.sha256(anchor_text.encode("utf-8")).hexdigest()
        != root["anchor_text_sha256"]
    ):
        raise VariantAnchorError("variant anchor plan.anchor_text_sha256が不一致です。")
    inputs_value = root["inputs"]
    if not isinstance(inputs_value, list) or len(inputs_value) != 2:
        raise VariantAnchorError("variant anchor plan.inputsは2件が必要です。")
    inputs: list[dict[str, Any]] = []
    scopes: list[str] = []
    for index, item in enumerate(inputs_value):
        entry = _exact(
            item,
            _PLAN_INPUT_FIELDS,
            f"variant anchor plan.inputs[{index}]",
        )
        if entry["model"] != base_model:
            raise VariantAnchorError("variant anchor plan.inputs.modelが不正です。")
        scopes.append(str(entry["role_scope"]))
        for field in ("plan_sha256", "candidate_set_sha256", "selection_sha256"):
            _require_sha256(entry[field], f"inputs.{field}")
        inputs.append(dict(entry))
    if sorted(scopes) != sorted(
        (ROLE_SCOPE_EXPLICIT_REFERENCE, ROLE_SCOPE_NO_REFERENCE),
    ):
        raise VariantAnchorError(
            "variant anchor plan.inputsはno-reference/explicit-referenceの"
            "両scopeが必要です。",
        )
    if sum(int(entry["role_count"]) for entry in inputs) != ROLE_COUNT:
        raise VariantAnchorError(
            f"variant anchor plan.inputsのrole合計は{ROLE_COUNT}が必要です。",
        )
    targets_value = root["targets"]
    if not isinstance(targets_value, list) or len(targets_value) != ROLE_COUNT:
        raise VariantAnchorError(
            f"variant anchor plan.targetsは{ROLE_COUNT}件が必要です。",
        )
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(targets_value):
        entry = _exact(
            item,
            _PLAN_TARGET_FIELDS,
            f"variant anchor plan.targets[{index}]",
        )
        identity = (
            _path_segment(entry["scenario"], "targets.scenario"),
            _path_segment(entry["character"], "targets.character"),
        )
        if identity in seen:
            raise VariantAnchorError(
                f"variant anchor plan.targetsが重複しています: {identity}",
            )
        seen.add(identity)
        for field in (
            "role_identity_sha256",
            "source_selection_sha256",
            "source_role_epoch_sha256",
            "role_epoch_sha256",
        ):
            _require_sha256(entry[field], f"targets.{field}")
        targets.append(dict(entry))
    if [(item["scenario"], item["character"]) for item in targets] != sorted(seen):
        raise VariantAnchorError(
            "variant anchor plan.targetsはscenario/character昇順が必要です。",
        )
    return {
        "format_version": PLAN_FORMAT_VERSION,
        "protocol": PLAN_PROTOCOL,
        "model": root["model"],
        "base_model": base_model,
        "conditioning_mode": MODE_TEXT_ONLY,
        "model_revision": _text(
            root["model_revision"],
            "variant anchor plan.model_revision",
        ),
        "anchor_text": anchor_text,
        "anchor_text_sha256": root["anchor_text_sha256"],
        "inputs": sorted(inputs, key=lambda item: item["selection_sha256"]),
        "targets": targets,
    }


def validate_variant_anchor_selection(document: Any) -> dict[str, Any]:
    root = _exact(document, _SELECTION_ROOT_FIELDS, "variant anchor selection")
    if (
        root["format_version"] != SELECTION_FORMAT_VERSION
        or root["protocol"] != SELECTION_PROTOCOL
    ):
        raise VariantAnchorError("variant anchor selection identityが不正です。")
    base_model = require_base_model(root["base_model"])
    if root["conditioning_mode"] != MODE_TEXT_ONLY:
        raise VariantAnchorError(
            f"variant anchor selectionは{MODE_TEXT_ONLY}だけが対象です。",
        )
    model = root["model"]
    if model != variant_model_id(base_model, MODE_TEXT_ONLY):
        raise VariantAnchorError("variant anchor selection.modelが不正です。")
    model_revision = _text(
        root["model_revision"],
        "variant anchor selection.model_revision",
    )
    plan_sha = _require_sha256(root["plan_sha256"], "selection.plan_sha256")
    candidate_sha = _require_sha256(
        root["candidate_set_sha256"],
        "selection.candidate_set_sha256",
    )
    groups_value = root["groups"]
    if not isinstance(groups_value, list) or len(groups_value) != ROLE_COUNT:
        raise VariantAnchorError(
            f"variant anchor selection.groupsは{ROLE_COUNT}件が必要です。",
        )
    groups: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(groups_value):
        group = _exact(
            item,
            _SELECTION_GROUP_FIELDS,
            f"variant anchor selection.groups[{index}]",
        )
        if group["model"] != model or group["model_revision"] != model_revision:
            raise VariantAnchorError(
                "variant anchor selection groupのmodel bindingが不一致です。",
            )
        identity = (
            _path_segment(group["scenario"], "groups.scenario"),
            _path_segment(group["character"], "groups.character"),
        )
        if identity in seen:
            raise VariantAnchorError(
                f"variant anchor selection groupが重複しています: {identity}",
            )
        seen.add(identity)
        role_identity = _exact(
            group["role_identity"],
            _ROLE_IDENTITY_FIELDS,
            "groups.role_identity",
        )
        if (
            role_identity["scenario"] != identity[0]
            or role_identity["character"] != identity[1]
        ):
            raise VariantAnchorError(
                "variant anchor selection role identityが不一致です。",
            )
        if group["authority_type"] not in {AUTHORITY_AUTO, AUTHORITY_HUMAN}:
            raise VariantAnchorError("groups.authority_typeが不正です。")
        source = _exact(
            group["source"],
            _SELECTION_SOURCE_FIELDS,
            "groups.source",
        )
        if source["model"] != base_model:
            raise VariantAnchorError("groups.source.modelがbase modelと不一致です。")
        for field in ("plan_sha256", "candidate_set_sha256", "selection_sha256"):
            _require_sha256(source[field], f"groups.source.{field}")
        audio_path = _text(group["audio_path"], "groups.audio_path")
        if audio_path != f"audio/{group['anchor_id']}.wav":
            raise VariantAnchorError("groups.audio_pathがanchor_idと一致しません。")
        anchor_text = _text(group["anchor_text"], "groups.anchor_text")
        if (
            hashlib.sha256(anchor_text.encode("utf-8")).hexdigest()
            != group["anchor_text_sha256"]
        ):
            raise VariantAnchorError("groups.anchor_text_sha256が不一致です。")
        attempt = group["attempt"]
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise VariantAnchorError("groups.attemptは1以上の整数が必要です。")
        seed = group["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise VariantAnchorError("groups.seedは非負整数が必要です。")
        expected_epoch = variant_role_epoch_sha256(
            model=model,
            model_revision=model_revision,
            conditioning_mode=MODE_TEXT_ONLY,
            scenario=identity[0],
            character=identity[1],
            role_identity_sha256=str(group["role_identity_sha256"]),
            source_selection_sha256=str(source["selection_sha256"]),
            source_role_epoch_sha256=str(group["source_role_epoch_sha256"]),
        )
        normalized = dict(group)
        normalized["role_identity"] = role_identity
        normalized["source"] = source
        for field in (
            "role_identity_sha256",
            "source_role_epoch_sha256",
            "role_epoch_sha256",
            "anchor_id",
            "audio_sha256",
            "anchor_text_sha256",
            "decision_sha256",
        ):
            normalized[field] = _require_sha256(group[field], f"groups.{field}")
        if normalized["role_epoch_sha256"] != expected_epoch:
            raise VariantAnchorError(
                f"groups.role_epoch_sha256が導出値と一致しません: {identity}",
            )
        groups.append(normalized)
    if [
        (group["scenario"], group["character"]) for group in groups
    ] != sorted(seen):
        raise VariantAnchorError(
            "variant anchor selection.groupsはscenario/character昇順が必要です。",
        )
    return {
        "format_version": SELECTION_FORMAT_VERSION,
        "protocol": SELECTION_PROTOCOL,
        "plan_sha256": plan_sha,
        "candidate_set_sha256": candidate_sha,
        "model": model,
        "base_model": base_model,
        "conditioning_mode": MODE_TEXT_ONLY,
        "model_revision": model_revision,
        "groups": groups,
    }


# --------------------------------------------------------------------------- #
# 解決 (adapter / Phase B)
# --------------------------------------------------------------------------- #


def _read_bound_selection(selection_path: Path) -> tuple[dict[str, Any], str]:
    if not selection_path.is_absolute():
        raise VariantAnchorError(
            f"variant anchor selectionは絶対pathが必要です: {selection_path}",
        )
    raw = _read_bytes(selection_path, "variant anchor selection")
    selection = validate_variant_anchor_selection(_decode_json(raw, selection_path))
    if canonical_json(selection).encode("utf-8") != raw:
        raise VariantAnchorError(
            "variant anchor selectionはcanonical bytesが必要です。",
        )
    digest = hashlib.sha256(raw).hexdigest()
    marker = selection_path.with_suffix(".sha256")
    try:
        marker_value = marker.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise VariantAnchorError(
            f"variant anchor selection SHA markerを読めません: {marker}",
        ) from error
    if marker_value != digest:
        raise VariantAnchorError(
            "variant anchor selection SHA markerが不一致です。",
        )
    return selection, digest


def resolve_variant_anchor(
    *,
    selection_path: Path,
    plan_sha256: str,
    model: str,
    model_revision: str,
    role: RoleSnapshot,
) -> SelectedRoleAnchor:
    """adapterの anchor 解決契約に variant selection を適合させる。"""

    selection, digest = _read_bound_selection(selection_path)
    if selection["plan_sha256"] != plan_sha256:
        raise VariantAnchorError(
            "variant anchor selectionのplan SHAがadapter指定と一致しません。",
        )
    if selection["model"] != model or selection["model_revision"] != model_revision:
        raise VariantAnchorError(
            "variant anchor selectionのmodel/revisionがadapterと一致しません。",
        )
    matches = [
        group
        for group in selection["groups"]
        if (group["scenario"], group["character"]) == (role.scenario, role.character)
    ]
    if len(matches) != 1:
        raise VariantAnchorError(
            "variant anchor selectionにscenario/characterの一意な選択がありません: "
            f"{role.scenario}/{role.character}",
        )
    group = matches[0]
    expected_identity = {
        "scenario": role.scenario,
        "character": role.character,
        "role": dict(role.role),
        "reference_voice": role.reference_voice,
        "scene_setting": role.scene_setting,
    }
    if (
        group["role_identity"] != expected_identity
        or group["role_identity_sha256"] != role.role_identity_sha256
    ):
        raise VariantAnchorError(
            "variant anchorの完全role identityがscenarioと一致しません。",
        )
    audio_path = selection_path.parent / str(group["audio_path"])
    _verify_file_sha256(audio_path, str(group["audio_sha256"]))
    return SelectedRoleAnchor(
        selection_sha256=digest,
        plan_sha256=selection["plan_sha256"],
        candidate_set_sha256=selection["candidate_set_sha256"],
        model=group["model"],
        model_revision=group["model_revision"],
        scenario=group["scenario"],
        character=group["character"],
        role_identity_sha256=group["role_identity_sha256"],
        role_epoch_sha256=group["role_epoch_sha256"],
        anchor_id=group["anchor_id"],
        attempt=group["attempt"],
        seed=group["seed"],
        audio_path=audio_path,
        audio_sha256=group["audio_sha256"],
        anchor_text=group["anchor_text"],
        anchor_text_sha256=group["anchor_text_sha256"],
        decision_sha256=group["decision_sha256"],
    )


def load_variant_anchor_selection(
    selection_path: Path,
    *,
    plan: Any,
) -> tuple[str, dict[tuple[str, str, str], str]]:
    """Phase B の anchor loader 契約 (`completion_listen`) へ適合させる。"""

    selection, digest = _read_bound_selection(selection_path)
    if (
        digest != plan.anchor_selection_sha256
        or selection["plan_sha256"] != plan.anchor_source_plan_sha256
        or selection["candidate_set_sha256"] != plan.anchor_candidate_set_sha256
    ):
        raise VariantAnchorError(
            "variant anchor selectionのauthority SHAがplanと一致しません。",
        )
    if selection["model"] != plan.model:
        raise VariantAnchorError(
            "variant anchor selectionのmodelがplanと一致しません。",
        )
    for group in selection["groups"]:
        role = plan.role(group["scenario"], group["character"])
        if (
            group["model_revision"] != plan.models[group["model"]]
            or group["role_identity_sha256"] != role.role_identity_sha256
        ):
            raise VariantAnchorError(
                "variant anchor selectionのrevision/role identityがplanと一致しません。",
            )
    return digest, {
        (group["model"], group["scenario"], group["character"]): group[
            "role_epoch_sha256"
        ]
        for group in selection["groups"]
    }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file_sha256(path: Path, expected: str) -> None:
    if _sha256_file(path) != expected:
        raise VariantAnchorError(f"anchor audio SHA-256が不一致です: {path}")


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as error:
        raise VariantAnchorError(f"既存fileを上書きしません: {path}") from error


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise VariantAnchorError(f"{label}を読めません: {path}") from error


def _decode_json(raw: bytes, path: Path) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VariantAnchorError(f"JSONが不正です: {path}: {error}") from error


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise VariantAnchorError(f"{label} のfield集合が不正です。")
    return dict(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise VariantAnchorError(f"{label} は空でない文字列が必要です。")
    return value


def _path_segment(value: Any, label: str) -> str:
    text = _text(value, label)
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise VariantAnchorError(f"{label} はpath segmentが必要です。")
    return text


def _require_sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise VariantAnchorError(f"{label} はlowercase SHA-256が必要です。")
    return value


__all__ = [
    "AUTHORITY_AUTO",
    "AUTHORITY_HUMAN",
    "PLAN_PROTOCOL",
    "ROLE_COUNT",
    "SELECTION_PROTOCOL",
    "VariantAnchorCompositionSummary",
    "VariantAnchorError",
    "compose_variant_anchor_selection",
    "load_variant_anchor_selection",
    "resolve_variant_anchor",
    "validate_variant_anchor_plan",
    "validate_variant_anchor_selection",
    "variant_role_epoch_sha256",
]
