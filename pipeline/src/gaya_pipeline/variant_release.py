"""条件バリアント 13列 release finalizer (#201)。

`increment_release` は「公開済みbase + 新規1 model」だった。#201 は列の
**組み替え** なので:

1. 単方式5列は base release から verbatim 継承
2. テキスト指示型4列は削除し、`--ref` / `--text` の8列に置換
3. 各バリアント列は「条件が一致する行 = 公開済みテイクをbyte不変で継承」
   + 「不一致の行 = 新規生成」

継承テイクは take_id / audio SHA / generation_input SHA をそのまま保つ。
`path` だけが model id を含むため列 id に合わせて再構成される (音声bytesは同一)。
旧列の系譜は release provenance の `superseded_by` に残す。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from gaya_pipeline.completion_auto import (
    CompletionAutoDecisionError,
    canonical_completion_quality_signals_bytes,
    validate_completion_quality_signals,
)
from gaya_pipeline.completion_listen import (
    CompletionListeningError,
    CompletionScenarioAuthority,
    CompletionSourceResolution,
    _load_completion_scenario_authority,
    _local_audio_path,
    resolve_completion_sources,
)
from gaya_pipeline.completion_release import (
    CompletionReleaseBundle,
    _build_candidate_set,
    _read_marker_bound,
    _write_release,
)
from gaya_pipeline.completion_selection import (
    FORMAT_VERSION as SELECTION_FORMAT_VERSION,
    PROTOCOL as SELECTION_PROTOCOL,
    canonical_completion_decision_bytes,
    canonical_completion_selection_bytes,
    validate_completion_decision,
    validate_completion_selection,
)
from gaya_pipeline.conditioning_variants import (
    LINES_PER_MODEL,
    VARIANT_BASE_MODELS,
    ConditioningVariantError,
    realized_conditioning_mode,
    variant_model_entry,
    variant_model_id,
)
from gaya_pipeline.curation import CurationError, canonical_candidate_set_bytes
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.take_manifest_v4 import TakeManifestError, validate_manifest_v4
from gaya_pipeline.variant_anchor import (
    VariantAnchorError,
    load_variant_anchor_selection,
)
from gaya_pipeline.variant_plan import (
    COLUMN_GROUPS,
    VARIANT,
    VariantPlan,
    VariantPlanError,
    load_variant_plan,
)


class VariantReleaseError(RuntimeError):
    pass


RELEASE_PROTOCOL = "role-conditioning-variant-release-v1"
RELEASE_FORMAT_VERSION = 1
SPEC_PROTOCOL = "role-conditioning-variant-finalize-spec-v1"
SPEC_FORMAT_VERSION = 1
ANCHOR_AUTHORITY_SET_PROTOCOL = "variant-anchor-authority-set-v1"
# anchor権限を持たない列 (`--ref` 全部と `voxcpm2--text`) 用の sentinel。
# `anchor_bound_models` が空なので run 側と突き合わせには使われない。
_NO_ANCHOR_SENTINEL = "0" * 64

_SPEC_COLUMN_FIELDS = {
    "plan",
    "decision",
    "quality_signals",
    "anchor_selection",
    "primary_run_ids",
    "topup_run_ids",
}


@dataclass(frozen=True)
class VariantColumnInput:
    plan_path: Path
    decision_path: Path
    quality_signals_path: Path
    anchor_selection_path: Path | None
    primary_run_ids: tuple[str, ...]
    topup_run_ids: tuple[str, ...]


@dataclass(frozen=True)
class VariantColumnSummary:
    model: str
    base_model: str
    conditioning_mode: str
    inherited_groups: int
    generated_groups: int
    inherited_candidates: int
    generated_candidates: int


@dataclass(frozen=True)
class VariantReleaseSummary:
    output_dir: Path
    manifest_sha256: str
    candidate_set_sha256: str
    selection_sha256: str
    quality_signals_sha256: str
    candidate_count: int
    selected_count: int
    model_count: int
    columns: tuple[VariantColumnSummary, ...]


# --------------------------------------------------------------------------- #
# finalize spec
# --------------------------------------------------------------------------- #


def load_variant_finalize_spec(spec_path: Path) -> tuple[VariantColumnInput, ...]:
    """8列ぶんの入力をまとめた spec を読む。"""

    if not spec_path.is_absolute():
        raise VariantReleaseError(f"finalize specは絶対pathが必要です: {spec_path}")
    try:
        document = json.loads(spec_path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VariantReleaseError(f"finalize specが不正です: {spec_path}") from error
    if not isinstance(document, dict) or set(document) != {
        "format_version",
        "protocol",
        "columns",
    }:
        raise VariantReleaseError("finalize spec rootが不正です。")
    if (
        document["format_version"] != SPEC_FORMAT_VERSION
        or document["protocol"] != SPEC_PROTOCOL
    ):
        raise VariantReleaseError("finalize spec identityが不正です。")
    columns_value = document["columns"]
    expected_columns = len(VARIANT_BASE_MODELS) * 2
    if not isinstance(columns_value, list) or len(columns_value) != expected_columns:
        raise VariantReleaseError(
            f"finalize spec.columnsは{expected_columns}件が必要です。",
        )
    columns: list[VariantColumnInput] = []
    for index, item in enumerate(columns_value):
        if not isinstance(item, dict) or set(item) != _SPEC_COLUMN_FIELDS:
            raise VariantReleaseError(
                f"finalize spec.columns[{index}]のfield集合が不正です。",
            )
        anchor = item["anchor_selection"]
        primary = item["primary_run_ids"]
        topup = item["topup_run_ids"]
        if (
            not isinstance(primary, list)
            or len(primary) != 1
            or not isinstance(topup, list)
        ):
            raise VariantReleaseError(
                f"finalize spec.columns[{index}]のrun idが不正です。",
            )
        columns.append(
            VariantColumnInput(
                plan_path=_absolute(item["plan"], "plan"),
                decision_path=_absolute(item["decision"], "decision"),
                quality_signals_path=_absolute(
                    item["quality_signals"],
                    "quality_signals",
                ),
                anchor_selection_path=(
                    None if anchor is None else _absolute(anchor, "anchor_selection")
                ),
                primary_run_ids=tuple(str(value) for value in primary),
                topup_run_ids=tuple(str(value) for value in topup),
            ),
        )
    return tuple(columns)


def _absolute(value: Any, label: str) -> Path:
    path = Path(str(value))
    if not path.is_absolute():
        raise VariantReleaseError(f"finalize spec {label} は絶対pathが必要です: {path}")
    return path


# --------------------------------------------------------------------------- #
# finalize
# --------------------------------------------------------------------------- #


def finalize_variant_release(
    *,
    columns: Sequence[VariantColumnInput],
    base_release_dir: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    output_dir: Path,
) -> VariantReleaseSummary:
    try:
        return _finalize(
            columns=columns,
            base_release_dir=base_release_dir,
            artifacts_dir=artifacts_dir,
            scenarios_dir=scenarios_dir,
            voices_dir=voices_dir,
            output_dir=output_dir,
        )
    except VariantReleaseError:
        raise
    except (
        CompletionAutoDecisionError,
        CompletionListeningError,
        ConditioningVariantError,
        CurationError,
        TakeManifestError,
        VariantAnchorError,
        VariantPlanError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise VariantReleaseError(
            f"variant release入力契約が不正です: {error}",
        ) from error


@dataclass(frozen=True)
class _ResolvedColumn:
    plan: VariantPlan
    resolution: CompletionSourceResolution
    decision_sha256: str
    decision_groups: dict[tuple[str, str, str, str], Mapping[str, Any]]
    quality_signal_groups: list[dict[str, Any]]
    generated_candidates: list[dict[str, Any]]
    inherited_candidates: list[dict[str, Any]]
    inherited_selection_groups: list[dict[str, Any]]
    inherited_quality_signal_groups: list[dict[str, Any]]
    model_entry: dict[str, Any]
    anchor_selection_sha256: str | None


def _finalize(
    *,
    columns: Sequence[VariantColumnInput],
    base_release_dir: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    output_dir: Path,
) -> VariantReleaseSummary:
    for path, label in (
        (base_release_dir, "base release"),
        (artifacts_dir, "artifacts"),
        (scenarios_dir, "scenarios"),
        (voices_dir, "voices"),
        (output_dir, "variant release output"),
    ):
        if not path.is_absolute():
            raise VariantReleaseError(f"{label}は絶対pathが必要です: {path}")
    if output_dir.exists():
        raise VariantReleaseError(
            f"variant release outputは既存pathを拒否します: {output_dir}",
        )
    expected_columns = len(VARIANT_BASE_MODELS) * 2
    if len(columns) != expected_columns:
        raise VariantReleaseError(
            f"variant releaseはexact {expected_columns} 列が必要です。",
        )

    base = _load_base_release(base_release_dir)
    base_models_by_id = {
        str(model["id"]): dict(model) for model in base.manifest["models"]
    }
    missing_base = set(VARIANT_BASE_MODELS) - set(base_models_by_id)
    if missing_base:
        raise VariantReleaseError(
            f"base releaseにvariant対象列がありません: {sorted(missing_base)}",
        )

    resolved: list[_ResolvedColumn] = []
    scenario_authority: CompletionScenarioAuthority | None = None
    for column in columns:
        plan = load_variant_plan(
            column.plan_path,
            scenarios_dir=scenarios_dir,
            voices_dir=voices_dir,
        )
        _verify_base_binding(plan, base)
        authority = _scenario_authority(
            plan,
            scenarios_dir=scenarios_dir,
            voices_dir=voices_dir,
        )
        if scenario_authority is None:
            scenario_authority = authority
        elif (
            authority.scenario_sha256 != scenario_authority.scenario_sha256
            or authority.lines != scenario_authority.lines
        ):
            raise VariantReleaseError(
                "列ごとのscenario authorityが一致しません。",
            )
        resolved.append(
            _resolve_column(
                column=column,
                plan=plan,
                base=base,
                base_models_by_id=base_models_by_id,
                artifacts_dir=artifacts_dir,
                scenario_authority=authority,
            ),
        )
    if scenario_authority is None:  # pragma: no cover - 列数チェック済み
        raise VariantReleaseError("scenario authorityを構築できませんでした。")

    expected_ids = {
        variant_model_id(base_model, mode)
        for base_model in VARIANT_BASE_MODELS
        for mode in ("human-reference", "text-only")
    }
    actual_ids = {item.plan.model for item in resolved}
    if actual_ids != expected_ids:
        raise VariantReleaseError(
            "variant releaseは4 model x 2 modeのexact列集合が必要です: "
            f"missing={sorted(expected_ids - actual_ids)}, "
            f"extra={sorted(actual_ids - expected_ids)}",
        )

    inherited_models = [
        dict(model)
        for model_id, model in sorted(base_models_by_id.items())
        if model_id not in set(VARIANT_BASE_MODELS)
    ]
    final_models = sorted(
        [*inherited_models, *(item.model_entry for item in resolved)],
        key=lambda item: str(item["id"]),
    )

    replaced = set(VARIANT_BASE_MODELS)
    final_candidates = [
        dict(candidate)
        for candidate in base.manifest["candidates"]
        if str(candidate["model"]) not in replaced
    ]
    final_selection_groups = [
        dict(group)
        for group in base.selection["groups"]
        if str(group["model"]) not in replaced
    ]
    final_quality_signal_groups = [
        dict(group)
        for group in base.quality_signals["groups"]
        if str(group["model"]) not in replaced
    ]
    for item in resolved:
        final_candidates.extend(item.inherited_candidates)
        final_candidates.extend(item.generated_candidates)
        final_selection_groups.extend(item.inherited_selection_groups)
        final_selection_groups.extend(
            {
                key: value
                for key, value in item.decision_groups[identity].items()
                if key not in {"group_sha256", "screening"}
            }
            for identity in sorted(item.decision_groups)
        )
        final_quality_signal_groups.extend(item.inherited_quality_signal_groups)
        final_quality_signal_groups.extend(item.quality_signal_groups)

    final_candidates.sort(
        key=lambda item: (_group_key(item), int(item["take_index"])),
    )
    final_selection_groups.sort(key=_group_key)
    final_quality_signal_groups.sort(key=_group_key)

    expected_selected = len(final_models) * LINES_PER_MODEL
    if len(final_selection_groups) != expected_selected:
        raise VariantReleaseError(
            f"variant releaseはexact {expected_selected} selected groupが必要です: "
            f"actual={len(final_selection_groups)}",
        )

    final_candidate_set = _build_candidate_set(
        candidates=final_candidates,
        models=final_models,
        scenario_authority=scenario_authority,
    )
    if (
        final_candidate_set["scenario_sha256"] != base.candidate_set["scenario_sha256"]
        or final_candidate_set["lines"] != base.candidate_set["lines"]
    ):
        raise VariantReleaseError(
            "variant releaseのscenario snapshotが公開済みbaseと一致しません。",
        )
    candidate_set_bytes = canonical_candidate_set_bytes(final_candidate_set)
    candidate_set_sha256 = hashlib.sha256(candidate_set_bytes).hexdigest()

    anchor_authority_sha256 = _anchor_authority_set_sha256(
        base_anchor_selection_sha256=str(
            base.selection["anchor_selection_sha256"],
        ),
        columns=resolved,
    )
    plan_authority_sha256 = _plan_authority_set_sha256(resolved)
    selection = validate_completion_selection(
        {
            "format_version": SELECTION_FORMAT_VERSION,
            "protocol": SELECTION_PROTOCOL,
            "plan_sha256": plan_authority_sha256,
            "anchor_selection_sha256": anchor_authority_sha256,
            "candidate_set_sha256": candidate_set_sha256,
            "ranking_report_sha256": base.selection["ranking_report_sha256"],
            "groups": final_selection_groups,
        },
    )
    selection_bytes = canonical_completion_selection_bytes(selection)
    selection_sha256 = hashlib.sha256(selection_bytes).hexdigest()
    curations = [
        {
            "model": group["model"],
            "scenario": group["scenario"],
            "line": group["line"],
            "variant": group["variant"],
            "decision": "selected",
            "take_id": group["decision"]["take_id"],
            "curation_sha256": selection_sha256,
        }
        for group in selection["groups"]
    ]
    manifest = validate_manifest_v4(
        {
            "format_version": 4,
            "generated_at": max(
                [str(base.manifest["generated_at"])]
                + [
                    str(run.manifest["generated_at"])
                    for item in resolved
                    for run in item.resolution.runs
                ],
            ),
            "candidate_set_sha256": candidate_set_sha256,
            "models": final_candidate_set["models"],
            "candidates": final_candidates,
            "curations": curations,
            "failures": [],
        },
    )
    manifest_bytes = canonical_json(manifest).encode("utf-8")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

    quality_signal_count = len(final_quality_signal_groups)
    quality_signals = validate_completion_quality_signals(
        {
            "format_version": 1,
            "protocol": base.quality_signals["protocol"],
            "plan_sha256": plan_authority_sha256,
            "decision_sha256": _decision_authority_set_sha256(resolved),
            "groups": final_quality_signal_groups,
        },
        expected_group_count=quality_signal_count,
    )
    quality_signals_bytes = canonical_completion_quality_signals_bytes(
        quality_signals,
        expected_group_count=quality_signal_count,
    )
    quality_signals_sha256 = hashlib.sha256(quality_signals_bytes).hexdigest()

    provenance = _build_provenance(
        base=base,
        resolved=resolved,
        manifest_sha256=manifest_sha256,
        candidate_set_sha256=candidate_set_sha256,
        selection_sha256=selection_sha256,
        quality_signals_sha256=quality_signals_sha256,
        plan_authority_sha256=plan_authority_sha256,
        anchor_authority_sha256=anchor_authority_sha256,
        model_count=len(final_models),
        selected_count=len(selection["groups"]),
        quality_signal_count=quality_signal_count,
    )
    _write_release(
        output_dir=output_dir,
        manifest_bytes=manifest_bytes,
        candidate_set_bytes=candidate_set_bytes,
        selection_bytes=selection_bytes,
        quality_signals_bytes=quality_signals_bytes,
        provenance_bytes=canonical_json(provenance).encode("utf-8"),
    )
    validate_variant_release(release_dir=output_dir, artifacts_dir=artifacts_dir)
    return VariantReleaseSummary(
        output_dir=output_dir,
        manifest_sha256=manifest_sha256,
        candidate_set_sha256=candidate_set_sha256,
        selection_sha256=selection_sha256,
        quality_signals_sha256=quality_signals_sha256,
        candidate_count=len(final_candidates),
        selected_count=len(selection["groups"]),
        model_count=len(final_models),
        columns=tuple(
            VariantColumnSummary(
                model=item.plan.model,
                base_model=item.plan.base_model,
                conditioning_mode=item.plan.conditioning_mode,
                inherited_groups=len(item.plan.inherit),
                generated_groups=len(item.plan.targets),
                inherited_candidates=len(item.inherited_candidates),
                generated_candidates=len(item.generated_candidates),
            )
            for item in sorted(resolved, key=lambda value: value.plan.model)
        ),
    )


def _resolve_column(
    *,
    column: VariantColumnInput,
    plan: VariantPlan,
    base: CompletionReleaseBundle,
    base_models_by_id: Mapping[str, Mapping[str, Any]],
    artifacts_dir: Path,
    scenario_authority: CompletionScenarioAuthority,
) -> _ResolvedColumn:
    anchor_required = plan.requires_anchor_authority()
    if anchor_required != (column.anchor_selection_path is not None):
        raise VariantReleaseError(
            f"{plan.model} のanchor selection指定がplanと一致しません。",
        )
    if anchor_required:
        anchor_loader = load_variant_anchor_selection
        anchor_path = column.anchor_selection_path
        anchor_bound = frozenset({plan.model})
    else:
        anchor_loader = _null_anchor_loader
        anchor_path = column.plan_path
        anchor_bound = frozenset()
    assert anchor_path is not None

    resolution = resolve_completion_sources(
        plan=plan,
        primary_run_ids=column.primary_run_ids,
        topup_run_ids=column.topup_run_ids,
        anchor_selection_path=anchor_path,
        artifacts_dir=artifacts_dir,
        scenario_authority=scenario_authority,
        primary_models=frozenset({plan.model}),
        anchor_loader=anchor_loader,
        anchor_bound_models=anchor_bound,
        expected_group_count=len(plan.targets),
    )
    generated_candidates = _generated_candidates(plan, resolution)
    # auto-decide が束縛した candidate set と同じ導出をする。決定対象は
    # 列の生成partitionだけなので lines もその部分集合になる (#201)。
    column_candidate_set = _build_candidate_set(
        candidates=generated_candidates,
        models=[run.manifest["models"][0] for run in resolution.runs],
        scenario_authority=_narrow_authority(scenario_authority, generated_candidates),
    )
    column_candidate_set_sha256 = hashlib.sha256(
        canonical_candidate_set_bytes(column_candidate_set),
    ).hexdigest()

    decision_raw = _read_bytes(column.decision_path, "variant decision")
    decision_sha256 = hashlib.sha256(decision_raw).hexdigest()
    decision = validate_completion_decision(
        _decode_json(decision_raw, column.decision_path),
    )
    if decision_raw != canonical_completion_decision_bytes(decision):
        raise VariantReleaseError("variant decisionはcanonical bytesが必要です。")
    if (
        decision["plan_sha256"] != plan.plan_id
        or decision["anchor_selection_sha256"] != resolution.anchor_selection_sha256
        or decision["candidate_set_sha256"] != column_candidate_set_sha256
    ):
        raise VariantReleaseError(
            f"{plan.model} のdecision plan/anchor/candidate-set bindingが不一致です。",
        )
    decision_groups = {_group_key(group): group for group in decision["groups"]}
    target_identities = {target.identity for target in plan.targets}
    if set(decision_groups) != target_identities:
        raise VariantReleaseError(
            f"{plan.model} のdecisionはplan targetのexact集合が必要です。",
        )

    quality_signals_raw = _read_bytes(
        column.quality_signals_path,
        "variant quality signals",
    )
    column_signals = validate_completion_quality_signals(
        _decode_json(quality_signals_raw, column.quality_signals_path),
        expected_group_count=len(plan.targets),
    )
    if (
        column_signals["plan_sha256"] != plan.plan_id
        or column_signals["decision_sha256"] != decision_sha256
    ):
        raise VariantReleaseError(
            f"{plan.model} のquality signals bindingが不一致です。",
        )
    if {_group_key(group) for group in column_signals["groups"]} != target_identities:
        raise VariantReleaseError(
            f"{plan.model} のquality signalsはdecisionと同じgroup集合が必要です。",
        )

    (
        inherited_candidates,
        inherited_selection_groups,
        inherited_quality_signal_groups,
    ) = _inherited_projection(plan=plan, base=base)

    model_entry = variant_model_entry(
        base_models_by_id[plan.base_model],
        plan.conditioning_mode,
    )
    run_revision = str(resolution.runs[0].manifest["models"][0]["version"])
    if run_revision != model_entry["version"]:
        raise VariantReleaseError(
            f"{plan.model} の run model revision が base model と一致しません。",
        )
    return _ResolvedColumn(
        plan=plan,
        resolution=resolution,
        decision_sha256=decision_sha256,
        decision_groups=decision_groups,
        quality_signal_groups=[dict(group) for group in column_signals["groups"]],
        generated_candidates=generated_candidates,
        inherited_candidates=inherited_candidates,
        inherited_selection_groups=inherited_selection_groups,
        inherited_quality_signal_groups=inherited_quality_signal_groups,
        model_entry=model_entry,
        anchor_selection_sha256=(
            resolution.anchor_selection_sha256 if anchor_required else None
        ),
    )


def _inherited_projection(
    *,
    plan: VariantPlan,
    base: CompletionReleaseBundle,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """公開済み列のテイクをvariant列へbyte不変で写す。

    `model` と `path` だけが列 id に追従する。`take_id` /
    `generation_input_sha256` / audio SHA は不変なので、音声objectの中身は
    完全に同一で、provenance も base の生成条件をそのまま指す。
    """

    candidates_by_group: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for candidate in base.manifest["candidates"]:
        if (
            str(candidate["model"]) != plan.base_model
            or str(candidate["variant"]) != VARIANT
        ):
            continue
        candidates_by_group.setdefault(
            (str(candidate["scenario"]), str(candidate["line"])),
            [],
        ).append(candidate)
    selection_by_group = {
        (str(group["scenario"]), str(group["line"])): group
        for group in base.selection["groups"]
        if str(group["model"]) == plan.base_model
        and str(group["variant"]) == VARIANT
    }
    signals_by_group = {
        (str(group["scenario"]), str(group["line"])): group
        for group in base.quality_signals["groups"]
        if str(group["model"]) == plan.base_model
        and str(group["variant"]) == VARIANT
    }

    candidates: list[dict[str, Any]] = []
    selection_groups: list[dict[str, Any]] = []
    signal_groups: list[dict[str, Any]] = []
    for inherited in plan.inherit:
        identity = inherited.identity
        group_candidates = candidates_by_group.get(identity, [])
        actual_take_ids = tuple(
            sorted(str(candidate["take_id"]) for candidate in group_candidates),
        )
        if actual_take_ids != inherited.candidate_take_ids:
            raise VariantReleaseError(
                f"継承行のcandidate集合がplanのpinと一致しません: {identity}",
            )
        selected_group = selection_by_group.get(identity)
        if (
            selected_group is None
            or str(selected_group["decision"]["take_id"])
            != inherited.selected_take_id
        ):
            raise VariantReleaseError(
                f"継承行のselected takeがplanのpinと一致しません: {identity}",
            )
        for candidate in group_candidates:
            take_id = str(candidate["take_id"])
            if take_id == inherited.selected_take_id and (
                str(candidate["sha256"]) != inherited.selected_audio_sha256
                or str(candidate["generation_input_sha256"])
                != inherited.selected_generation_input_sha256
            ):
                raise VariantReleaseError(
                    f"継承行のtake identityがplanのpinと一致しません: {identity}",
                )
            try:
                actual_mode = realized_conditioning_mode(
                    base_model=plan.base_model,
                    realized=candidate["gen_params"]["realized"],
                )
            except ConditioningVariantError as error:
                raise VariantReleaseError(
                    f"継承候補の条件を判定できません: {identity}: {error}",
                ) from error
            if actual_mode != plan.conditioning_mode:
                raise VariantReleaseError(
                    "継承候補のrealized条件が列の条件と一致しません: "
                    f"{identity}: {actual_mode} != {plan.conditioning_mode}",
                )
            candidates.append(_rekey_candidate(candidate, plan.model))
        selection_groups.append({**dict(selected_group), "model": plan.model})
        signal_group = signals_by_group.get(identity)
        if signal_group is not None:
            signal_groups.append({**dict(signal_group), "model": plan.model})
    return candidates, selection_groups, signal_groups


def _rekey_candidate(
    candidate: Mapping[str, Any],
    model_id: str,
) -> dict[str, Any]:
    rekeyed = dict(candidate)
    rekeyed["model"] = model_id
    rekeyed["path"] = (
        f"audio/takes/{model_id}/{candidate['scenario']}/{candidate['line']}/"
        f"{candidate['variant']}/take-{int(candidate['take_index']):04d}-"
        f"{candidate['sha256']}.opus"
    )
    return rekeyed


def _generated_candidates(
    plan: VariantPlan,
    resolution: CompletionSourceResolution,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    minimum = plan.policy_for_model(plan.model).minimum_eligible_candidates
    for identity, run in sorted(resolution.group_sources.items()):
        group_candidates = [
            dict(candidate)
            for candidate in run.manifest["candidates"]
            if _group_key(candidate) == identity
        ]
        if len(group_candidates) < minimum:
            raise VariantReleaseError(
                "variant groupはeligible candidateがmodel policyの"
                f"{minimum}件以上必要です: {identity}",
            )
        result.extend(group_candidates)
    return result


def _null_anchor_loader(
    _selection_path: Path,
    _plan: Any,
) -> tuple[str, Mapping[tuple[str, str, str], str]]:
    return _NO_ANCHOR_SENTINEL, {}


def _narrow_authority(
    authority: CompletionScenarioAuthority,
    candidates: Sequence[Mapping[str, Any]],
) -> CompletionScenarioAuthority:
    """candidate の scenario/line 集合へ authority の lines を絞る。

    `curation.validate_candidate_set` は lines と candidate の行集合の
    exact 一致を要求する。列の candidate set は生成partitionだけを含むので、
    161行の authority をそのまま渡すと被覆契約に落ちる。
    """

    identities = {
        (str(candidate["scenario"]), str(candidate["line"]))
        for candidate in candidates
    }
    return CompletionScenarioAuthority(
        scenario_sha256=authority.scenario_sha256,
        lines=tuple(
            dict(line)
            for line in authority.lines
            if (str(line["scenario"]), str(line["line"])) in identities
        ),
        contexts={
            identity: context
            for identity, context in authority.contexts.items()
            if identity in identities
        },
        line_characters={
            identity: character
            for identity, character in authority.line_characters.items()
            if identity in identities
        },
    )


def _scenario_authority(
    plan: VariantPlan,
    *,
    scenarios_dir: Path,
    voices_dir: Path,
) -> CompletionScenarioAuthority:
    return _load_completion_scenario_authority(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        plan=plan,
    )


def _verify_base_binding(plan: VariantPlan, base: CompletionReleaseBundle) -> None:
    actual = _base_marker_shas(base)
    expected = {
        "manifest_sha256": plan.base_manifest_sha256,
        "candidate_set_sha256": plan.base_candidate_set_sha256,
        "selection_sha256": plan.base_selection_sha256,
        "quality_signals_sha256": plan.base_quality_signals_sha256,
        "release_provenance_sha256": plan.base_release_provenance_sha256,
    }
    if actual != expected:
        raise VariantReleaseError(
            f"{plan.model} のplanが束縛するbase release SHAが実体と一致しません。",
        )
    if len(base.selection["groups"]) != plan.base_groups:
        raise VariantReleaseError(
            f"{plan.model} のplan base_groupsがbase releaseと一致しません。",
        )


_BASE_SHAS: dict[int, dict[str, str]] = {}


def _base_marker_shas(base: CompletionReleaseBundle) -> dict[str, str]:
    cached = _BASE_SHAS.get(id(base))
    if cached is not None:
        return cached
    result = {
        "manifest_sha256": _file_sha256(base.root / "manifest-v4.json"),
        "candidate_set_sha256": _file_sha256(base.root / "candidate-set.json"),
        "selection_sha256": _file_sha256(base.root / "selection.json"),
        "quality_signals_sha256": _file_sha256(base.root / "quality-signals.json"),
        "release_provenance_sha256": _file_sha256(
            base.root / "release-provenance.json",
        ),
    }
    _BASE_SHAS[id(base)] = result
    return result


def _load_base_release(base_release_dir: Path) -> CompletionReleaseBundle:
    root = base_release_dir.resolve(strict=True)
    if not root.is_dir():
        raise VariantReleaseError("base releaseはdirectoryが必要です。")
    manifest, manifest_sha = _read_marker_bound(
        root / "manifest-v4.json",
        root / "manifest-v4.sha256",
        "base manifest",
    )
    candidate_set, candidate_sha = _read_marker_bound(
        root / "candidate-set.json",
        root / "candidate-set.sha256",
        "base candidate set",
    )
    selection, _selection_sha = _read_marker_bound(
        root / "selection.json",
        root / "selection.sha256",
        "base selection",
    )
    quality_signals, _quality_sha = _read_marker_bound(
        root / "quality-signals.json",
        root / "quality-signals.sha256",
        "base quality signals",
    )
    provenance, _provenance_sha = _read_marker_bound(
        root / "release-provenance.json",
        root / "release-provenance.sha256",
        "base release provenance",
    )
    normalized_manifest = validate_manifest_v4(manifest)
    if normalized_manifest["candidate_set_sha256"] != candidate_sha:
        raise VariantReleaseError(
            "base manifestのcandidate_set_sha256がbundleと一致しません。",
        )
    _ = manifest_sha
    normalized_selection = validate_completion_selection(selection)
    normalized_signals = validate_completion_quality_signals(
        quality_signals,
        expected_group_count=len(quality_signals["groups"]),
    )
    if len(normalized_selection["groups"]) % LINES_PER_MODEL != 0:
        raise VariantReleaseError("base selectionは列数×161 groupが必要です。")
    return CompletionReleaseBundle(
        root=root,
        manifest=normalized_manifest,
        candidate_set=candidate_set,
        selection=normalized_selection,
        quality_signals=normalized_signals,
        provenance=provenance,
    )


# --------------------------------------------------------------------------- #
# 権限集合SHA (13列を1つのrelease documentへ束ねるための決定論的束縛)
# --------------------------------------------------------------------------- #


def _plan_authority_set_sha256(columns: Sequence[_ResolvedColumn]) -> str:
    return _canonical_sha256(
        {
            "protocol": RELEASE_PROTOCOL,
            "kind": "plan-set",
            "columns": sorted(
                (
                    {"model": item.plan.model, "plan_sha256": item.plan.plan_id}
                    for item in columns
                ),
                key=lambda item: item["model"],
            ),
        },
    )


def _decision_authority_set_sha256(columns: Sequence[_ResolvedColumn]) -> str:
    return _canonical_sha256(
        {
            "protocol": RELEASE_PROTOCOL,
            "kind": "decision-set",
            "columns": sorted(
                (
                    {
                        "model": item.plan.model,
                        "decision_sha256": item.decision_sha256,
                    }
                    for item in columns
                ),
                key=lambda item: item["model"],
            ),
        },
    )


def _anchor_authority_set_sha256(
    *,
    base_anchor_selection_sha256: str,
    columns: Sequence[_ResolvedColumn],
) -> str:
    return _canonical_sha256(
        {
            "protocol": ANCHOR_AUTHORITY_SET_PROTOCOL,
            "base_anchor_selection_sha256": base_anchor_selection_sha256,
            "columns": sorted(
                (
                    {
                        "model": item.plan.model,
                        "anchor_selection_sha256": item.anchor_selection_sha256,
                    }
                    for item in columns
                ),
                key=lambda item: item["model"],
            ),
        },
    )


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #

_PROVENANCE_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_authority_sha256",
    "anchor_authority_sha256",
    "decision_authority_sha256",
    "manifest_sha256",
    "candidate_set_sha256",
    "selection_sha256",
    "quality_signals_sha256",
    "counts",
    "base",
    "superseded_by",
    "columns",
}
_PROVENANCE_COUNTS_FIELDS = {
    "models",
    "selected_groups",
    "quality_signal_groups",
    "failures",
}
_PROVENANCE_BASE_FIELDS = {
    "manifest_sha256",
    "candidate_set_sha256",
    "selection_sha256",
    "quality_signals_sha256",
    "release_provenance_sha256",
}
_PROVENANCE_COLUMN_FIELDS = {
    "model",
    "base_model",
    "conditioning_mode",
    "plan_sha256",
    "decision_sha256",
    "anchor_selection_sha256",
    "inherited_groups",
    "generated_groups",
    "inherited_candidates",
    "generated_candidates",
    "source_runs",
}


def _build_provenance(
    *,
    base: CompletionReleaseBundle,
    resolved: Sequence[_ResolvedColumn],
    manifest_sha256: str,
    candidate_set_sha256: str,
    selection_sha256: str,
    quality_signals_sha256: str,
    plan_authority_sha256: str,
    anchor_authority_sha256: str,
    model_count: int,
    selected_count: int,
    quality_signal_count: int,
) -> dict[str, Any]:
    base_shas = _base_marker_shas(base)
    columns: list[dict[str, Any]] = []
    for item in sorted(resolved, key=lambda value: value.plan.model):
        source_runs: list[dict[str, Any]] = []
        for run in item.resolution.runs:
            effective = {
                identity
                for identity, source in item.resolution.group_sources.items()
                if source is run
            }
            candidates: list[dict[str, Any]] = []
            for candidate in run.manifest["candidates"]:
                if _group_key(candidate) not in effective:
                    continue
                local_path = _local_audio_path(run.root, candidate)
                candidates.append(
                    {
                        "take_id": candidate["take_id"],
                        "path": candidate["path"],
                        "audio_sha256": candidate["sha256"],
                        "run_relative_path": local_path.relative_to(
                            run.root,
                        ).as_posix(),
                        "size_bytes": local_path.stat().st_size,
                    },
                )
            source_runs.append(
                {
                    "model": run.model,
                    "run_id": run.run_id,
                    "kind": run.kind,
                    "supersedes_run_id": run.supersedes_run_id,
                    "ledger_sha256": run.ledger_sha256,
                    "qc_report_sha256": run.qc_report_sha256,
                    "manifest_sha256": run.manifest_sha256,
                    "candidate_set_sha256": run.candidate_set_sha256,
                    "effective_groups": [
                        {
                            "model": identity[0],
                            "scenario": identity[1],
                            "line": identity[2],
                            "variant": identity[3],
                            "role_epoch_sha256": item.resolution.expected_role_epochs[
                                identity
                            ],
                        }
                        for identity in sorted(effective)
                    ],
                    "candidates": sorted(
                        candidates,
                        key=lambda value: value["take_id"],
                    ),
                },
            )
        columns.append(
            {
                "model": item.plan.model,
                "base_model": item.plan.base_model,
                "conditioning_mode": item.plan.conditioning_mode,
                "plan_sha256": item.plan.plan_id,
                "decision_sha256": item.decision_sha256,
                "anchor_selection_sha256": item.anchor_selection_sha256,
                "inherited_groups": len(item.plan.inherit),
                "generated_groups": len(item.plan.targets),
                "inherited_candidates": len(item.inherited_candidates),
                "generated_candidates": len(item.generated_candidates),
                "source_runs": sorted(
                    source_runs,
                    key=lambda value: value["run_id"],
                ),
            },
        )
    superseded_by = [
        {
            "model": base_model,
            "replaced_by": sorted(
                item.plan.model
                for item in resolved
                if item.plan.base_model == base_model
            ),
        }
        for base_model in sorted(VARIANT_BASE_MODELS)
    ]
    return {
        "format_version": RELEASE_FORMAT_VERSION,
        "protocol": RELEASE_PROTOCOL,
        "plan_authority_sha256": plan_authority_sha256,
        "anchor_authority_sha256": anchor_authority_sha256,
        "decision_authority_sha256": _decision_authority_set_sha256(resolved),
        "manifest_sha256": manifest_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "selection_sha256": selection_sha256,
        "quality_signals_sha256": quality_signals_sha256,
        "counts": {
            "models": model_count,
            "selected_groups": selected_count,
            "quality_signal_groups": quality_signal_count,
            "failures": 0,
        },
        "base": {field: base_shas[field] for field in sorted(_PROVENANCE_BASE_FIELDS)},
        "superseded_by": superseded_by,
        "columns": columns,
    }


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #


def validate_variant_release(
    *,
    release_dir: Path,
    artifacts_dir: Path | None = None,
    source_audit_path: Path | None = None,
) -> CompletionReleaseBundle:
    """publishが受理する条件バリアントreleaseの完全性契約。"""

    try:
        root = release_dir.resolve(strict=True)
        if not root.is_dir():
            raise VariantReleaseError("release_dirはdirectoryが必要です。")
        manifest, manifest_sha = _read_marker_bound(
            root / "manifest-v4.json",
            root / "manifest-v4.sha256",
            "manifest",
        )
        candidate_set, candidate_sha = _read_marker_bound(
            root / "candidate-set.json",
            root / "candidate-set.sha256",
            "candidate set",
        )
        selection_doc, selection_sha = _read_marker_bound(
            root / "selection.json",
            root / "selection.sha256",
            "selection",
        )
        quality_signals_doc, quality_signals_sha = _read_marker_bound(
            root / "quality-signals.json",
            root / "quality-signals.sha256",
            "quality signals",
        )
        provenance, _provenance_sha = _read_marker_bound(
            root / "release-provenance.json",
            root / "release-provenance.sha256",
            "release provenance",
        )
        manifest = validate_manifest_v4(manifest)
        selection = validate_completion_selection(selection_doc)
        signal_count = len(quality_signals_doc["groups"])
        quality_signals = validate_completion_quality_signals(
            quality_signals_doc,
            expected_group_count=signal_count,
        )
        if canonical_json(manifest).encode("utf-8") != (
            root / "manifest-v4.json"
        ).read_bytes():
            raise VariantReleaseError("manifestはcanonical bytesが必要です。")
        if canonical_candidate_set_bytes(candidate_set) != (
            root / "candidate-set.json"
        ).read_bytes():
            raise VariantReleaseError("candidate setはcanonical bytesが必要です。")
        if canonical_completion_selection_bytes(selection) != (
            root / "selection.json"
        ).read_bytes():
            raise VariantReleaseError("selectionはcanonical bytesが必要です。")
        if canonical_completion_quality_signals_bytes(
            quality_signals,
            expected_group_count=signal_count,
        ) != (root / "quality-signals.json").read_bytes():
            raise VariantReleaseError("quality signalsはcanonical bytesが必要です。")
        _validate_provenance(
            provenance,
            manifest_sha=manifest_sha,
            candidate_sha=candidate_sha,
            selection_sha=selection_sha,
            quality_signals_sha=quality_signals_sha,
            manifest=manifest,
            selection=selection,
            quality_signal_count=signal_count,
        )
        if (
            manifest["candidate_set_sha256"] != candidate_sha
            or selection["candidate_set_sha256"] != candidate_sha
        ):
            raise VariantReleaseError("candidate set SHA bindingが不一致です。")
        if manifest["failures"]:
            raise VariantReleaseError("variant releaseにfailureは許可されません。")
        _validate_columns(manifest=manifest, selection=selection)
        _validate_manifest_joins(
            manifest=manifest,
            candidate_set=candidate_set,
            selection=selection,
            quality_signals=quality_signals,
        )
        if artifacts_dir is not None:
            _verify_generated_audio(
                manifest=manifest,
                provenance=provenance,
                artifacts_dir=artifacts_dir,
            )
        _ = source_audit_path
        return CompletionReleaseBundle(
            root=root,
            manifest=manifest,
            candidate_set=candidate_set,
            selection=selection,
            quality_signals=quality_signals,
            provenance=provenance,
        )
    except VariantReleaseError:
        raise
    except (
        CompletionAutoDecisionError,
        ConditioningVariantError,
        CurationError,
        TakeManifestError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise VariantReleaseError(
            f"variant release契約が不正です: {error}",
        ) from error


def _validate_columns(
    *,
    manifest: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> None:
    model_ids = [str(model["id"]) for model in manifest["models"]]
    for base_model in VARIANT_BASE_MODELS:
        if base_model in model_ids:
            raise VariantReleaseError(
                f"混合条件の旧列が残っています: {base_model}",
            )
        for mode in ("human-reference", "text-only"):
            expected = variant_model_id(base_model, mode)
            if expected not in model_ids:
                raise VariantReleaseError(f"variant列がありません: {expected}")
    conditioning_by_model = {
        str(model["id"]): model.get("conditioning") for model in manifest["models"]
    }
    counts: dict[str, int] = {}
    for group in selection["groups"]:
        counts[str(group["model"])] = counts.get(str(group["model"]), 0) + 1
    if set(counts) != set(model_ids):
        raise VariantReleaseError("selectionのmodel集合がmanifestと一致しません。")
    for model_id, count in sorted(counts.items()):
        if count != LINES_PER_MODEL:
            raise VariantReleaseError(
                f"{model_id} はexact {LINES_PER_MODEL} 行が必要です: actual={count}",
            )
    for base_model in VARIANT_BASE_MODELS:
        for mode in ("human-reference", "text-only"):
            model_id = variant_model_id(base_model, mode)
            conditioning = conditioning_by_model.get(model_id)
            if (
                not isinstance(conditioning, Mapping)
                or conditioning.get("mode") != mode
                or conditioning.get("base_model") != base_model
            ):
                raise VariantReleaseError(
                    f"{model_id} のconditioning fieldが不正です。",
                )
    for model_id, conditioning in sorted(conditioning_by_model.items()):
        if conditioning is None:
            continue
        base_model = str(conditioning["base_model"])
        mode = str(conditioning["mode"])
        for candidate in manifest["candidates"]:
            if str(candidate["model"]) != model_id:
                continue
            actual = realized_conditioning_mode(
                base_model=base_model,
                realized=candidate["gen_params"]["realized"],
            )
            if actual != mode:
                raise VariantReleaseError(
                    "列内に条件の異なるテイクがあります: "
                    f"{model_id}/{candidate['scenario']}/{candidate['line']}: "
                    f"{actual} != {mode}",
                )


def _validate_provenance(
    value: Any,
    *,
    manifest_sha: str,
    candidate_sha: str,
    selection_sha: str,
    quality_signals_sha: str,
    manifest: Mapping[str, Any],
    selection: Mapping[str, Any],
    quality_signal_count: int,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _PROVENANCE_ROOT_FIELDS:
        raise VariantReleaseError("variant release provenance rootが不正です。")
    if (
        value["format_version"] != RELEASE_FORMAT_VERSION
        or value["protocol"] != RELEASE_PROTOCOL
    ):
        raise VariantReleaseError("variant release provenance identityが不正です。")
    if (
        value["manifest_sha256"] != manifest_sha
        or value["candidate_set_sha256"] != candidate_sha
        or value["selection_sha256"] != selection_sha
        or value["quality_signals_sha256"] != quality_signals_sha
    ):
        raise VariantReleaseError(
            "variant release provenanceのdocument SHAが不一致です。",
        )
    counts = value["counts"]
    if not isinstance(counts, dict) or set(counts) != _PROVENANCE_COUNTS_FIELDS:
        raise VariantReleaseError("variant release provenance countsが不正です。")
    if (
        counts["models"] != len(manifest["models"])
        or counts["selected_groups"] != len(selection["groups"])
        or counts["quality_signal_groups"] != quality_signal_count
        or counts["failures"] != 0
    ):
        raise VariantReleaseError("variant release provenance countsが実体と不一致です。")
    base = value["base"]
    if not isinstance(base, dict) or set(base) != _PROVENANCE_BASE_FIELDS:
        raise VariantReleaseError("variant release provenance baseが不正です。")
    for field in sorted(_PROVENANCE_BASE_FIELDS):
        _require_sha256(base[field], f"release provenance.base.{field}")
    superseded = value["superseded_by"]
    if not isinstance(superseded, list) or len(superseded) != len(
        VARIANT_BASE_MODELS,
    ):
        raise VariantReleaseError(
            "variant release provenance superseded_byが不正です。",
        )
    for entry in superseded:
        if not isinstance(entry, dict) or set(entry) != {"model", "replaced_by"}:
            raise VariantReleaseError(
                "superseded_by entryのfield集合が不正です。",
            )
        base_model = str(entry["model"])
        if base_model not in VARIANT_BASE_MODELS:
            raise VariantReleaseError(f"superseded_by modelが不正です: {base_model}")
        expected = sorted(
            variant_model_id(base_model, mode)
            for mode in ("human-reference", "text-only")
        )
        if list(entry["replaced_by"]) != expected:
            raise VariantReleaseError(
                f"superseded_by replaced_byが不正です: {base_model}",
            )
    columns = value["columns"]
    expected_columns = len(VARIANT_BASE_MODELS) * 2
    if not isinstance(columns, list) or len(columns) != expected_columns:
        raise VariantReleaseError(
            f"variant release provenance columnsは{expected_columns}件が必要です。",
        )
    for column in columns:
        if not isinstance(column, dict) or set(column) != _PROVENANCE_COLUMN_FIELDS:
            raise VariantReleaseError("provenance columnのfield集合が不正です。")
        if (
            int(column["inherited_groups"]) + int(column["generated_groups"])
            != COLUMN_GROUPS
        ):
            raise VariantReleaseError(
                f"provenance column {column['model']} の行数が161ではありません。",
            )
    return dict(value)


def _validate_manifest_joins(
    *,
    manifest: Mapping[str, Any],
    candidate_set: Mapping[str, Any],
    selection: Mapping[str, Any],
    quality_signals: Mapping[str, Any],
) -> None:
    if (
        candidate_set["models"] != manifest["models"]
        or candidate_set["candidates"] != manifest["candidates"]
        or candidate_set["failures"] != manifest["failures"]
    ):
        raise VariantReleaseError("candidate setとmanifestが一致しません。")
    candidates_by_take = {
        candidate["take_id"]: candidate for candidate in manifest["candidates"]
    }
    curations = {
        _group_key(curation): curation for curation in manifest["curations"]
    }
    if len(curations) != len(manifest["curations"]) or len(curations) != len(
        selection["groups"],
    ):
        raise VariantReleaseError("manifest curationsがselectionと一致しません。")
    for group in selection["groups"]:
        identity = _group_key(group)
        curation = curations.get(identity)
        if curation is None or curation["decision"] != "selected":
            raise VariantReleaseError(f"selected curationがありません: {identity}")
        take_id = group["decision"]["take_id"]
        if curation["take_id"] != take_id:
            raise VariantReleaseError(f"curation take_idが不一致です: {identity}")
        candidate = candidates_by_take.get(take_id)
        if candidate is None or _group_key(candidate) != identity:
            raise VariantReleaseError(
                f"selected take がmanifest candidateにありません: {identity}",
            )
    selected_identities = {_group_key(group) for group in selection["groups"]}
    signal_identities = {_group_key(group) for group in quality_signals["groups"]}
    if not signal_identities <= selected_identities:
        raise VariantReleaseError(
            "quality signal groupがselected groupに存在しません。",
        )


def _verify_generated_audio(
    *,
    manifest: Mapping[str, Any],
    provenance: Mapping[str, Any],
    artifacts_dir: Path,
) -> None:
    takes_root = artifacts_dir / "takes"
    by_take = {
        candidate["take_id"]: candidate for candidate in manifest["candidates"]
    }
    for column in provenance["columns"]:
        for run in column["source_runs"]:
            run_root = takes_root / str(run["run_id"])
            for item in run["candidates"]:
                candidate = by_take.get(item["take_id"])
                if candidate is None:
                    raise VariantReleaseError(
                        f"source run candidateがmanifestにありません: {item['take_id']}",
                    )
                path = run_root / Path(str(item["run_relative_path"]))
                try:
                    payload = path.read_bytes()
                except OSError as error:
                    raise VariantReleaseError(
                        f"variant audioを読めません: {path}",
                    ) from error
                if (
                    hashlib.sha256(payload).hexdigest() != item["audio_sha256"]
                    or item["audio_sha256"] != candidate["sha256"]
                    or len(payload) != int(item["size_bytes"])
                ):
                    raise VariantReleaseError(
                        f"variant audioのSHA/sizeが不一致です: {path}",
                    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _group_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value["model"]),
        str(value["scenario"]),
        str(value["line"]),
        str(value["variant"]),
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise VariantReleaseError(f"{field}はlowercase SHA-256が必要です。")
    return value


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise VariantReleaseError(f"{label}を読めません: {path}") from error


def _decode_json(raw: bytes, path: Path) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VariantReleaseError(f"JSONが不正です: {path}: {error}") from error


__all__ = [
    "RELEASE_PROTOCOL",
    "SPEC_PROTOCOL",
    "VariantColumnInput",
    "VariantColumnSummary",
    "VariantReleaseError",
    "VariantReleaseSummary",
    "finalize_variant_release",
    "load_variant_finalize_spec",
    "load_variant_release_columns",
    "validate_variant_release",
]


def load_variant_release_columns(spec_path: Path) -> tuple[VariantColumnInput, ...]:
    """`load_variant_finalize_spec` の別名 (CLIからの読みやすさのため)。"""

    return load_variant_finalize_spec(spec_path)
