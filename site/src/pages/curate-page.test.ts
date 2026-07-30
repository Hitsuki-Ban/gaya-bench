import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { CurationJudgmentCriteria } from "@/pages/curate-page";

describe("CurationJudgmentCriteria", () => {
  it("今回の独立した判断基準とskip条件を明示する", () => {
    const markup = renderToStaticMarkup(createElement(CurationJudgmentCriteria));

    for (const text of [
      "今回の判断基準",
      "厳密な日本語の音調・アクセント",
      "語の読みが理論上正しくても",
      "意図一致",
      "役として自然",
      "採用可能",
      "単純な品質理由",
      "提示語の漏洩",
      "感情名、話し方、演技指示、メタ文",
      "それ以外は skip",
    ]) {
      expect(markup).toContain(text);
    }
  });
});
