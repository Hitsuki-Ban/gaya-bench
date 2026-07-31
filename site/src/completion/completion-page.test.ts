import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { CompletionJudgmentCriteria } from "./completion-page";
import { assertCompletionCatalogContract, COMPLETION_PLAN_SHA256 } from "./contract";
import { makeCatalog } from "./storage.test";

describe("CompletionJudgmentCriteria", () => {
  it("本ラウンド固有のbest available基準を常時明示する", () => {
    const markup = renderToStaticMarkup(createElement(CompletionJudgmentCriteria));

    for (const text of [
      "今回の判断基準",
      "best available",
      "台詞の欠落・追加・反復",
      "感情名・話し方・メタ文の漏洩",
      "語の読みが理論上正しくても",
      "厳密な音調・アクセント",
      "役柄・声線",
      "感情・強度・演技",
      "自然さ・音質",
      "完全合格がなくても skip はしません",
      "content_correct=false",
      "adoptable=false",
    ]) {
      expect(markup).toContain(text);
    }
  });

  it("固定plan、45 group、model内訳、空terminal stateを要求する", () => {
    const source = makeCatalog();
    const groups = [
      ...makeGroups(source.groups[0]!, "chatterbox-multilingual-v3", 1),
      ...makeGroups(source.groups[0]!, "cosyvoice3-0.5b-2512", 2),
      ...makeGroups(source.groups[0]!, "qwen3-tts-12hz-1.7b", 40),
      ...makeGroups(source.groups[0]!, "voxcpm2", 2),
    ];
    const catalog = { ...source, groups };

    expect(() => assertCompletionCatalogContract(catalog, COMPLETION_PLAN_SHA256)).not.toThrow();
    expect(() => assertCompletionCatalogContract(catalog, "0".repeat(64))).toThrow("固定plan");
    expect(() =>
      assertCompletionCatalogContract(
        { ...catalog, manifestCurationCount: 1 },
        COMPLETION_PLAN_SHA256,
      ),
    ).toThrow("curations 0");
    expect(() =>
      assertCompletionCatalogContract(
        { ...catalog, groups: groups.slice(1) },
        COMPLETION_PLAN_SHA256,
      ),
    ).toThrow("45 group");
    expect(() =>
      assertCompletionCatalogContract(
        {
          ...catalog,
          groups: groups.map((group, index) =>
            index === 0 ? { ...group, model: "wrong-model" } : group,
          ),
        },
        COMPLETION_PLAN_SHA256,
      ),
    ).toThrow("model別group数");
  });
});

function makeGroups(
  source: ReturnType<typeof makeCatalog>["groups"][number],
  model: string,
  count: number,
) {
  return Array.from({ length: count }, (_value, index) => ({
    ...source,
    model,
    scenario: `${model}-${index}`,
  }));
}
