import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { describe, expect, it } from "vite-plus/test";

import type { ConditioningMode, Model } from "@/data/types";

import { ModelConditioningCard } from "./model-conditioning-card";

describe("ModelConditioningCard", () => {
  it("条件バリアント列で条件・base model・もう一方の列への導線を出す", () => {
    const models = [
      singleModel("preset", "プリセット話者モデル"),
      variantModel("human-reference"),
      variantModel("text-only"),
    ];

    const markup = render(models[1]!, models);

    expect(markup).toContain('data-conditioning-mode="human-reference"');
    expect(markup).toContain("見本あり");
    expect(markup).toContain("収録素材を見本にして生成");
    // base model 名は接尾辞なしで文脈として出す。
    expect(markup).toContain("Base Model</span>");
    expect(markup).toContain('href="/models/base-model--text?scenario=market"');
    expect(markup).toContain("もう一方の条件（見本なし）の列を見る");
  });

  it("単方式モデルでは何も描画しない", () => {
    const model = singleModel("preset", "プリセット話者モデル");

    expect(render(model, [model])).toBe("");
  });

  it("片方の列しか公開されていなければ sibling link を出さない", () => {
    const model = variantModel("text-only");

    const markup = render(model, [model]);

    expect(markup).toContain("見本なし");
    expect(markup).not.toContain("もう一方の条件");
  });
});

function render(model: Model, models: readonly Model[]): string {
  return renderToStaticMarkup(
    createElement(
      MemoryRouter,
      null,
      createElement(ModelConditioningCard, { model, models, search: "?scenario=market" }),
    ),
  );
}

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

function variantModel(mode: ConditioningMode): Model {
  const suffix = mode === "human-reference" ? "ref" : "text";
  const label = mode === "human-reference" ? "見本あり" : "見本なし";
  return {
    ...singleModel(`base-model--${suffix}`, `Base Model（${label}）`),
    conditioning: { mode, base_model: "base-model" },
  };
}
