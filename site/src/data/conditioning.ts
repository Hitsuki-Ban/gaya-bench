import type { Conditioning, ConditioningMode, Model } from "./types";

/** URL query・列並びで使う条件modeの正準順 (見本あり → 見本なし)。 */
export const CONDITIONING_MODE_ORDER = [
  "human-reference",
  "text-only",
] as const satisfies readonly ConditioningMode[];

/** 列ヘッダのチップ文言。manifest `models[].name` の丸括弧表記と同じ語を使う。 */
export const CONDITIONING_MODE_LABELS = {
  "human-reference": "見本あり",
  "text-only": "見本なし",
} as const satisfies Record<ConditioningMode, string>;

/** チップの補足 (title / aria-label / 凡例)。色だけに意味を持たせない。 */
export const CONDITIONING_MODE_DESCRIPTIONS = {
  "human-reference": "収録素材を見本にして生成",
  "text-only": "説明文だけで生成（見本もモデルの自作）",
} as const satisfies Record<ConditioningMode, string>;

export interface ModelColumnGroup {
  /** React key / DOM 属性用の安定 id。variant 群は base model id。 */
  readonly key: string;
  /** グループ見出し。variant 群は base model 名、単方式は model 名。 */
  readonly label: string;
  /** variant 群のときだけ base model id。単方式列は null。 */
  readonly baseModel: string | null;
  readonly models: readonly Model[];
}

export function conditioningModeLabel(mode: ConditioningMode): string {
  return CONDITIONING_MODE_LABELS[mode];
}

export function conditioningModeDescription(mode: ConditioningMode): string {
  return CONDITIONING_MODE_DESCRIPTIONS[mode];
}

/** チップ・凡例で使う読み上げ可能なテキスト (色に依存しない説明)。 */
export function conditioningAccessibleLabel(conditioning: Conditioning): string {
  return `条件: ${conditioningModeLabel(conditioning.mode)}（${conditioningModeDescription(
    conditioning.mode,
  )}）`;
}

/**
 * 条件バリアント列の表示名から `（見本あり）` 等の接尾辞を落とした base model 名。
 * 単方式モデル、および接尾辞が付いていない名前はそのまま返す。
 */
export function baseModelLabel(model: Model): string {
  if (!model.conditioning) {
    return model.name;
  }
  const suffix = `（${conditioningModeLabel(model.conditioning.mode)}）`;
  return model.name.endsWith(suffix) ? model.name.slice(0, -suffix.length) : model.name;
}

export function hasConditioningVariants(models: readonly Model[]): boolean {
  return models.some((model) => model.conditioning !== undefined);
}

/**
 * 比較マトリクスの列を base model でグループ化する。
 *
 * 同一 base model の variant 列は manifest 上で隣接している契約 (build 時に検証済み) なので、
 * 連続する同一 base をひとつのグループへまとめる。単方式モデルは 1 列 1 グループのまま。
 */
export function groupModelColumns(models: readonly Model[]): readonly ModelColumnGroup[] {
  const groups: { key: string; label: string; baseModel: string | null; models: Model[] }[] = [];
  const seenBaseModels = new Set<string>();

  for (const model of models) {
    const baseModel = model.conditioning?.base_model ?? null;
    const current = groups.at(-1);
    if (baseModel !== null && current?.baseModel === baseModel) {
      current.models.push(model);
      continue;
    }
    if (baseModel !== null) {
      if (seenBaseModels.has(baseModel)) {
        throw new Error(`条件バリアント列が隣接していません: ${baseModel}`);
      }
      seenBaseModels.add(baseModel);
    }
    groups.push({
      key: baseModel ?? model.id,
      label: baseModel === null ? model.name : baseModelLabel(model),
      baseModel,
      models: [model],
    });
  }

  return groups;
}

/** 同じ base model のもう一方の条件列 (見つからなければ undefined)。 */
export function findVariantSibling(models: readonly Model[], model: Model): Model | undefined {
  const conditioning = model.conditioning;
  if (!conditioning) {
    return undefined;
  }
  return models.find(
    (candidate) =>
      candidate.id !== model.id && candidate.conditioning?.base_model === conditioning.base_model,
  );
}
