import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { CurationJudgmentCriteria } from "@/pages/curate-page";

describe("CurationJudgmentCriteria", () => {
  it("今回の独立した判断基準とN=1のskip条件を明示する", () => {
    const markup = renderToStaticMarkup(
      createElement(CurationJudgmentCriteria, { candidateCount: 1 }),
    );

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

  it("N>1では全候補を比較して最大1件を選ぶ基準を明示する", () => {
    const markup = renderToStaticMarkup(
      createElement(CurationJudgmentCriteria, { candidateCount: 3 }),
    );

    for (const text of [
      "候補が3件",
      "全候補を個別に評価",
      "最も適した1件だけ",
      "該当候補がなければ全候補を見送って",
    ]) {
      expect(markup).toContain(text);
    }
    expect(markup).not.toContain("候補が1件");
  });
});
