"""adapter側の条件バリアント配線 (#201)。

adapterは既定では scenario の `character.reference_voice` に従う
(明示reference優先 → 無ければ役別anchor / voice design)。条件バリアント列では
その分岐を **modeで強制** する:

- `human-reference`: 全役を人間収録素材へ。明示referenceが無い53役は
  clone系modelと同じ `CLONE_REFERENCE_ASSIGNMENTS` を共用する
- `text-only`: 明示referenceを無視し、全役を役別anchor / voice design へ

conditioning receiptは既存fieldのまま (`reference_source` / `reference_control` /
`reference_kind`) なので、#178 のクリップ単位「参照由来」表示は自動で整合する。
"""

from __future__ import annotations

from gaya_pipeline.adapters.base import ModelProfile
from gaya_pipeline.conditioning_variants import (
    ConditioningVariantError,
    effective_reference_voice,
    reference_selection_source,
    require_mode,
    variant_model_entry,
)

__all__ = [
    "ConditioningVariantError",
    "effective_reference_voice",
    "normalize_conditioning_mode",
    "reference_selection_source",
    "variant_profile",
]


def normalize_conditioning_mode(mode: object) -> str | None:
    if mode is None:
        return None
    return require_mode(mode)


def variant_profile(base_profile: ModelProfile, mode: str | None) -> ModelProfile:
    """base profileから variant profile (id/name/conditioning差し替え) を作る。

    `version` / `license_note` / `capabilities` はbaseをそのまま継承する。
    """

    if mode is None:
        return base_profile
    entry = variant_model_entry(base_profile.as_manifest_entry(), mode)
    return ModelProfile(
        id=entry["id"],
        name=entry["name"],
        version=base_profile.version,
        license_note=base_profile.license_note,
        capabilities=base_profile.capabilities,
        conditioning=entry["conditioning"],
    )
