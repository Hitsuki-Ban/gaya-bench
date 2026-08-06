import { describe, expect, it } from "vite-plus/test";

import {
  baseModelLabel,
  conditioningAccessibleLabel,
  findVariantSibling,
  groupModelColumns,
  hasConditioningVariants,
} from "./conditioning";
import type { ConditioningMode, Model } from "./types";

describe("groupModelColumns", () => {
  it("同一 base model の 2 列を base 名のグループへまとめる", () => {
    const models = [
      singleModel("aivisspeech-kohaku", "AivisSpeech コハク"),
      variantModel("irodori-tts-v4-small", "Irodori-TTS v4-Small", "human-reference"),
      variantModel("irodori-tts-v4-small", "Irodori-TTS v4-Small", "text-only"),
      singleModel("voxcpm2", "VoxCPM2"),
    ];

    const groups = groupModelColumns(models);

    expect(
      groups.map(({ key, label, baseModel, models: grouped }) => ({
        key,
        label,
        baseModel,
        ids: grouped.map(({ id }) => id),
      })),
    ).toEqual([
      {
        key: "aivisspeech-kohaku",
        label: "AivisSpeech コハク",
        baseModel: null,
        ids: ["aivisspeech-kohaku"],
      },
      {
        key: "irodori-tts-v4-small",
        label: "Irodori-TTS v4-Small",
        baseModel: "irodori-tts-v4-small",
        ids: ["irodori-tts-v4-small--ref", "irodori-tts-v4-small--text"],
      },
      { key: "voxcpm2", label: "VoxCPM2", baseModel: null, ids: ["voxcpm2"] },
    ]);
  });

  it("conditioning のない manifest では 1 列 1 グループのままにする", () => {
    const models = [singleModel("alpha", "Alpha"), singleModel("beta", "Beta")];

    expect(groupModelColumns(models).map(({ models: grouped }) => grouped.length)).toEqual([1, 1]);
    expect(hasConditioningVariants(models)).toBe(false);
  });

  it("フィルタで片方だけ残った variant も 1 列のグループとして扱う", () => {
    const groups = groupModelColumns([
      variantModel("irodori-tts-v4-small", "Irodori-TTS v4-Small", "human-reference"),
      singleModel("voxcpm2", "VoxCPM2"),
    ]);

    expect(groups.map(({ key, models: grouped }) => [key, grouped.length])).toEqual([
      ["irodori-tts-v4-small", 1],
      ["voxcpm2", 1],
    ]);
  });

  it("同一 base model の列が離れていたら fail fast する", () => {
    expect(() =>
      groupModelColumns([
        variantModel("irodori-tts-v4-small", "Irodori-TTS v4-Small", "human-reference"),
        singleModel("voxcpm2", "VoxCPM2"),
        variantModel("irodori-tts-v4-small", "Irodori-TTS v4-Small", "text-only"),
      ]),
    ).toThrow("条件バリアント列が隣接していません: irodori-tts-v4-small");
  });
});

describe("conditioning labels", () => {
  it("表示名から条件の接尾辞を落とし、単方式モデルは素通しする", () => {
    expect(baseModelLabel(variantModel("qwen3-tts-12hz-1.7b", "Qwen3-TTS", "text-only"))).toBe(
      "Qwen3-TTS",
    );
    expect(baseModelLabel(singleModel("voxcpm2", "VoxCPM2"))).toBe("VoxCPM2");
  });

  it("接尾辞のない variant 名はそのまま見出しにする", () => {
    const model = {
      ...variantModel("qwen3-tts-12hz-1.7b", "Qwen3-TTS", "text-only"),
      name: "Qwen3-TTS 見本なし列",
    };

    expect(baseModelLabel(model)).toBe("Qwen3-TTS 見本なし列");
  });

  it("チップの読み上げテキストに条件の説明を含める", () => {
    expect(conditioningAccessibleLabel({ mode: "human-reference", base_model: "voxcpm2" })).toBe(
      "条件: 見本あり（収録素材を見本にして生成）",
    );
    expect(conditioningAccessibleLabel({ mode: "text-only", base_model: "voxcpm2" })).toContain(
      "見本なし",
    );
  });
});

describe("findVariantSibling", () => {
  it("同じ base model のもう一方の条件列を返す", () => {
    const reference = variantModel(
      "irodori-tts-v4-small",
      "Irodori-TTS v4-Small",
      "human-reference",
    );
    const text = variantModel("irodori-tts-v4-small", "Irodori-TTS v4-Small", "text-only");
    const models = [singleModel("voxcpm2", "VoxCPM2"), reference, text];

    expect(findVariantSibling(models, reference)?.id).toBe("irodori-tts-v4-small--text");
    expect(findVariantSibling(models, text)?.id).toBe("irodori-tts-v4-small--ref");
    expect(findVariantSibling(models, singleModel("voxcpm2", "VoxCPM2"))).toBeUndefined();
    expect(findVariantSibling([reference], reference)).toBeUndefined();
  });
});

function singleModel(id: string, name: string): Model {
  return {
    id,
    name,
    version: "1",
    license_note: "テスト",
    capabilities: {
      emotion: false,
      voice_prompt: false,
      clone: false,
      nonverbal: false,
      reading: false,
    },
  };
}

function variantModel(baseModel: string, baseName: string, mode: ConditioningMode): Model {
  const suffix = mode === "human-reference" ? "ref" : "text";
  const label = mode === "human-reference" ? "見本あり" : "見本なし";
  return {
    ...singleModel(`${baseModel}--${suffix}`, `${baseName}（${label}）`),
    conditioning: { mode, base_model: baseModel },
  };
}
