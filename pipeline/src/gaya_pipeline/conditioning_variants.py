"""条件バリアント (見本あり / 見本なし) の語彙と派生規則。

テキスト指示型4モデルは #174/#194 の公開列で「明示reference 5役 + anchor 53役」の
**混合条件**になっている。#201 はこれを列内で条件の揃った2列へ分離する:

- `<base-id>--ref`  : 全58役を人間収録素材 (voice asset) へ条件付ける
- `<base-id>--text` : 全58役をモデル自作の見本 (役別anchor / voice design) へ条件付ける

本moduleはその語彙 (model id・表示名・`conditioning` field・adapter強制mode・
realized receiptからの条件判定) だけを持ち、release/plan/anchorの各機構が共有する。
凍結済みの単方式モデル (プリセット/クローン) には一切影響しない。
"""

from __future__ import annotations

from typing import Any, Mapping


class ConditioningVariantError(RuntimeError):
    pass


MODE_HUMAN_REFERENCE = "human-reference"
MODE_TEXT_ONLY = "text-only"
CONDITIONING_MODES: tuple[str, ...] = (MODE_HUMAN_REFERENCE, MODE_TEXT_ONLY)

MODE_SUFFIX: Mapping[str, str] = {
    MODE_HUMAN_REFERENCE: "--ref",
    MODE_TEXT_ONLY: "--text",
}
SUFFIX_MODE: Mapping[str, str] = {
    suffix: mode for mode, suffix in MODE_SUFFIX.items()
}
# site の列ヘッダ/モデル名に出る短いラベル。manifest `models[].name` は
# base名 + このラベルの丸括弧表記で一意にする (site側の hardcode は増やさない)。
MODE_LABEL: Mapping[str, str] = {
    MODE_HUMAN_REFERENCE: "見本あり",
    MODE_TEXT_ONLY: "見本なし",
}

# 2列に分離する text-instruct モデル (Owner決定 / #201)。
VARIANT_BASE_MODELS: tuple[str, ...] = (
    "irodori-tts-600m-v3-voicedesign",
    "irodori-tts-v4-small",
    "qwen3-tts-12hz-1.7b",
    "voxcpm2",
)

# `--text` 側で **役別anchor selection** を消費するmodel。
# VoxCPM2 は adapter 内蔵の voice design (自己参照) が text-only 経路そのものなので
# 別途 anchor WAV を必要としない (#201 設計判断)。
ANCHOR_BASE_MODELS: frozenset[str] = frozenset(
    {
        "irodori-tts-600m-v3-voicedesign",
        "irodori-tts-v4-small",
        "qwen3-tts-12hz-1.7b",
    },
)

CONDITIONING_FIELDS = frozenset({"mode", "base_model"})

# --------------------------------------------------------------------------- #
# anchor role scope
#
# anchor bootstrap が対象にする役の部分集合。adapter 側の anchor 生成 guard も
# この語彙で判断するため、増分/バリアント両方から参照できる本moduleに置く。
# --------------------------------------------------------------------------- #
ROLE_SCOPE_NO_REFERENCE = "no-reference-roles-v1"
ROLE_SCOPE_EXPLICIT_REFERENCE = "explicit-reference-roles-v1"
ROLE_SCOPES: tuple[str, ...] = (
    ROLE_SCOPE_NO_REFERENCE,
    ROLE_SCOPE_EXPLICIT_REFERENCE,
)


def require_role_scope(value: Any) -> str:
    if value not in ROLE_SCOPES:
        raise ConditioningVariantError(
            f"role_scopeは{ROLE_SCOPES}のいずれかが必要です: {value!r}",
        )
    return str(value)


def anchor_scope_allows_explicit_reference(role_scope: Any) -> bool:
    """その scope で「明示referenceを持つ役」が anchor 対象になりうるか。

    既定 (`no-reference-roles-v1`) では False。`--text` バリアント用の
    `explicit-reference-roles-v1` では、明示referenceを意図的に無視して
    役別anchorを作るのが目的なので True。
    """

    return require_role_scope(role_scope) == ROLE_SCOPE_EXPLICIT_REFERENCE

# 最終形 (#201): 単方式5列 + バリアント8列。
VARIANT_COLUMN_COUNT = len(VARIANT_BASE_MODELS) * len(CONDITIONING_MODES)
SINGLE_MODE_COLUMN_COUNT = 5
FINAL_MODEL_COUNT = SINGLE_MODE_COLUMN_COUNT + VARIANT_COLUMN_COUNT
LINES_PER_MODEL = 161
FINAL_SELECTED_COUNT = FINAL_MODEL_COUNT * LINES_PER_MODEL


def require_mode(mode: Any) -> str:
    if mode not in CONDITIONING_MODES:
        raise ConditioningVariantError(
            f"conditioning modeは{CONDITIONING_MODES}のいずれかが必要です: {mode!r}",
        )
    return str(mode)


def require_base_model(base_model: Any) -> str:
    if base_model not in VARIANT_BASE_MODELS:
        raise ConditioningVariantError(
            f"conditioning variantの対象modelではありません: {base_model!r}",
        )
    return str(base_model)


def variant_model_id(base_model: str, mode: str) -> str:
    return f"{require_base_model(base_model)}{MODE_SUFFIX[require_mode(mode)]}"


def split_variant_model_id(model_id: Any) -> tuple[str, str] | None:
    """`<base>--ref` / `<base>--text` を (base, mode) へ分解する。

    variantでない model id は `None` を返す (単方式モデルの経路を変えない)。
    """

    if not isinstance(model_id, str):
        return None
    for suffix, mode in sorted(SUFFIX_MODE.items()):
        if model_id.endswith(suffix):
            base = model_id[: -len(suffix)]
            if base in VARIANT_BASE_MODELS:
                return base, mode
    return None


def base_model_of(model_id: str) -> str:
    """variantなら base id、そうでなければ自分自身。"""

    split = split_variant_model_id(model_id)
    return model_id if split is None else split[0]


def conditioning_mode_of(model_id: str) -> str | None:
    split = split_variant_model_id(model_id)
    return None if split is None else split[1]


def variant_columns() -> tuple[tuple[str, str], ...]:
    """(base_model, mode) の8列。model id 昇順で安定。"""

    columns = [
        (base, mode) for base in VARIANT_BASE_MODELS for mode in CONDITIONING_MODES
    ]
    columns.sort(key=lambda item: variant_model_id(*item))
    return tuple(columns)


def variant_model_ids() -> tuple[str, ...]:
    return tuple(variant_model_id(base, mode) for base, mode in variant_columns())


def conditioning_document(*, base_model: str, mode: str) -> dict[str, str]:
    return {
        "base_model": require_base_model(base_model),
        "mode": require_mode(mode),
    }


def variant_model_name(base_name: str, mode: str) -> str:
    if not isinstance(base_name, str) or not base_name:
        raise ConditioningVariantError("base model nameは空にできません。")
    return f"{base_name}（{MODE_LABEL[require_mode(mode)]}）"


def variant_model_entry(
    base_entry: Mapping[str, Any],
    mode: str,
) -> dict[str, Any]:
    """base の manifest model entry から variant entry を派生する。

    `version` / `license_note` / `capabilities` は base をそのまま継承する
    (同一checkpoint・同一ライセンスで条件だけが違う)。`id` と `name` に
    variant識別を足し、`conditioning` を追加する。
    """

    mode = require_mode(mode)
    if not isinstance(base_entry, Mapping):
        raise ConditioningVariantError("base model entryはobjectが必要です。")
    missing = {"id", "name", "version", "license_note", "capabilities"} - set(
        base_entry,
    )
    if missing:
        raise ConditioningVariantError(
            f"base model entryに必須fieldがありません: {sorted(missing)}",
        )
    base_model = require_base_model(base_entry["id"])
    capabilities = base_entry["capabilities"]
    if not isinstance(capabilities, Mapping):
        raise ConditioningVariantError("base model entry.capabilitiesが不正です。")
    return {
        "id": variant_model_id(base_model, mode),
        "name": variant_model_name(str(base_entry["name"]), mode),
        "version": base_entry["version"],
        "license_note": base_entry["license_note"],
        "capabilities": dict(capabilities),
        "conditioning": conditioning_document(base_model=base_model, mode=mode),
    }


def validate_conditioning(value: Any, *, model_id: str) -> dict[str, str]:
    """manifest v4 の optional `conditioning` field 契約。"""

    if not isinstance(value, dict) or set(value) != set(CONDITIONING_FIELDS):
        raise ConditioningVariantError(
            "model.conditioningは{base_model, mode}が必要です。",
        )
    mode = require_mode(value["mode"])
    base_model = require_base_model(value["base_model"])
    if model_id != variant_model_id(base_model, mode):
        raise ConditioningVariantError(
            "model.conditioningがmodel idと一致しません: "
            f"{model_id} != {variant_model_id(base_model, mode)}",
        )
    return {"base_model": base_model, "mode": mode}


def requires_anchor_authority(model_id: str) -> bool:
    """その model id の Phase B が anchor selection 権限を必要とするか。

    - 単方式/凍結モデル: 従来どおり `ANCHOR_BASE_MODELS` のみ true
    - `--ref` variant: 全役が voice asset なので anchor は消費しない
    - `--text` variant: base が anchor 型なら true (VoxCPM2 は voice design)
    """

    split = split_variant_model_id(model_id)
    if split is None:
        return model_id in ANCHOR_BASE_MODELS
    base, mode = split
    return mode == MODE_TEXT_ONLY and base in ANCHOR_BASE_MODELS


def effective_reference_voice(
    *,
    mode: str | None,
    scenario: str,
    character: str,
    explicit: str | None,
) -> str | None:
    """adapterが実際に使う reference voice id を条件modeで確定する。

    - `mode=None`: 従来契約 (scenario の明示referenceが最優先、無ければanchor)
    - `human-reference`: 明示 → 無ければ clone系と同じ `CLONE_REFERENCE_ASSIGNMENTS`。
      どちらも無い役は **fail fast** (勝手な推測をしない)
    - `text-only`: 明示referenceを無視して常に anchor / voice design
    """

    if mode is None:
        return explicit
    mode = require_mode(mode)
    if mode == MODE_TEXT_ONLY:
        return None
    if explicit is not None:
        return explicit
    # adapters package からの循環importを避けるため関数内で解決する。
    from gaya_pipeline.adapters.voice_assignments import (
        CLONE_REFERENCE_ASSIGNMENTS,
    )

    assigned = CLONE_REFERENCE_ASSIGNMENTS.get((scenario, character))
    if assigned is None:
        raise ConditioningVariantError(
            "human-reference variantに割当のない役です "
            f"(CLONE_REFERENCE_ASSIGNMENTS未登録): {scenario}/{character}",
        )
    return assigned


def reference_selection_source(
    *,
    mode: str | None,
    scenario: str,
    character: str,
    explicit: str | None,
) -> str | None:
    """その役の reference voice を **どこから** 選んだかのラベル。

    clone系adapter (`gpt_sovits` / `cosyvoice3` / `chatterbox`) と同じ表記に揃える。
    """

    voice = effective_reference_voice(
        mode=mode,
        scenario=scenario,
        character=character,
        explicit=explicit,
    )
    if voice is None:
        return None
    if explicit is not None:
        return "character.reference_voice"
    return f"adapter.assignment:{scenario}/{character}"


# --------------------------------------------------------------------------- #
# realized receipt からの条件判定 (#178 の参照由来表示と同じ素材で機械判定する)
# --------------------------------------------------------------------------- #

_HUMAN_REFERENCE_MARKERS: Mapping[str, tuple[str, str]] = {
    "irodori-tts-600m-v3-voicedesign": ("reference_source", "voice-asset"),
    "irodori-tts-v4-small": ("reference_source", "voice-asset"),
    "qwen3-tts-12hz-1.7b": ("reference_control", "voice_asset"),
    "voxcpm2": ("reference_kind", "asset"),
}
_TEXT_ONLY_MARKERS: Mapping[str, tuple[str, str]] = {
    "irodori-tts-600m-v3-voicedesign": ("reference_source", "selected-role-anchor"),
    "irodori-tts-v4-small": ("reference_source", "selected-role-anchor"),
    "qwen3-tts-12hz-1.7b": ("reference_control", "selected_voice_design_anchor"),
    "voxcpm2": ("reference_kind", "voice_design"),
}


def realized_conditioning_mode(
    *,
    base_model: str,
    realized: Mapping[str, Any],
) -> str:
    """takeの realized receipt から実際に消費した条件を判定する。

    継承テイクの条件一致検証 (`variant_plan`) と release 監査が共有する。
    """

    base_model = require_base_model(base_model)
    if not isinstance(realized, Mapping):
        raise ConditioningVariantError("realized receiptはobjectが必要です。")
    for mode, markers in (
        (MODE_HUMAN_REFERENCE, _HUMAN_REFERENCE_MARKERS),
        (MODE_TEXT_ONLY, _TEXT_ONLY_MARKERS),
    ):
        field, expected = markers[base_model]
        if realized.get(field) == expected:
            return mode
    raise ConditioningVariantError(
        f"realized receiptから条件を判定できません: model={base_model}",
    )


__all__ = [
    "ANCHOR_BASE_MODELS",
    "CONDITIONING_FIELDS",
    "CONDITIONING_MODES",
    "FINAL_MODEL_COUNT",
    "FINAL_SELECTED_COUNT",
    "LINES_PER_MODEL",
    "MODE_HUMAN_REFERENCE",
    "MODE_LABEL",
    "MODE_SUFFIX",
    "MODE_TEXT_ONLY",
    "ROLE_SCOPES",
    "ROLE_SCOPE_EXPLICIT_REFERENCE",
    "ROLE_SCOPE_NO_REFERENCE",
    "SINGLE_MODE_COLUMN_COUNT",
    "anchor_scope_allows_explicit_reference",
    "require_role_scope",
    "SUFFIX_MODE",
    "VARIANT_BASE_MODELS",
    "VARIANT_COLUMN_COUNT",
    "ConditioningVariantError",
    "base_model_of",
    "conditioning_document",
    "conditioning_mode_of",
    "effective_reference_voice",
    "realized_conditioning_mode",
    "reference_selection_source",
    "require_base_model",
    "require_mode",
    "requires_anchor_authority",
    "split_variant_model_id",
    "validate_conditioning",
    "variant_columns",
    "variant_model_entry",
    "variant_model_id",
    "variant_model_ids",
    "variant_model_name",
]
