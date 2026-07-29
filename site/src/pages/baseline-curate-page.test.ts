import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { baselineSelectionStatus } from "@/baseline/selection";
import { baselineReviewGroupIndices, resolveBaselineReviewGroupIndex } from "@/baseline/review";
import type { BaselineCurationDraft, BaselineGroup } from "@/baseline/types";
import { BaselineGroupEditor, BaselineReviewGuide } from "@/pages/baseline-curate-page";

const TAKE_ID = "a".repeat(64);
const CANDIDATE_SHA = "b".repeat(64);
const REFERENCE_SHA = "c".repeat(64);

describe("BaselineGroupEditor", () => {
  it("同じgroupで旧referenceと新候補のSHA状態を明示し、新候補だけにrubricを表示する", () => {
    const markup = renderEditor(makeGroup("identical"));

    expect(markup).toContain("現行公開 reference");
    expect(markup).toContain("新 baseline candidate");
    expect(markup).toContain("SHA 一致");
    expect(markup).toContain("自動選択はしません");
    expect(markup).toContain(REFERENCE_SHA);
    expect(markup).toContain(CANDIDATE_SHA);
    expect(markup).toContain("比較専用・評価対象外");
    for (const label of ["内容は正しい", "意図一致", "役として自然", "採用可能"]) {
      expect(markup.match(new RegExp(label, "g"))).toHaveLength(1);
    }
    expect(markup).toContain("rubric 全4項目の入力が必要");
    expect(markup).not.toContain("相対的");
    expect(markup).not.toContain("winner");
    expect(markup).not.toContain("Pilot");
  });

  it("SHA相違も明示し、同一でも相違でもdecisionを自動生成しない", () => {
    const identical = renderEditor(makeGroup("identical"));
    const different = renderEditor(makeGroup("different"));

    expect(different).toContain("SHA 相違");
    expect(different).toContain("音声 SHA は異なります");
    expect(identical).toContain("未策展");
    expect(different).toContain("未策展");
    expect(identical).not.toContain("策展済み:");
    expect(different).not.toContain("策展済み:");
  });
});

describe("baselineSelectionStatus", () => {
  it("buttonの無効理由と選択可能状態を具体的に返す", () => {
    expect(
      baselineSelectionStatus({
        content_correct: null,
        intent_match: null,
        character_naturalness: null,
        adoptable: null,
      }),
    ).toContain("全4項目");
    expect(
      baselineSelectionStatus({
        content_correct: false,
        intent_match: 5,
        character_naturalness: 5,
        adoptable: true,
      }),
    ).toContain("content_correct=true");
    expect(
      baselineSelectionStatus({
        content_correct: true,
        intent_match: 5,
        character_naturalness: 5,
        adoptable: true,
      }),
    ).toContain("選択できます");
  });
});

describe("baseline skip 復聴", () => {
  it("品質理由のskipだけをcontent_correct=trueから抽出し、元の並び順を維持する", () => {
    const draft = makeDraft([
      ["selected", true],
      ["skipped", true],
      ["skipped", false],
      ["skipped", true],
    ]);

    expect(baselineReviewGroupIndices(draft, "quality-skipped")).toEqual([1, 3]);
    expect(baselineReviewGroupIndices(draft, "skipped")).toEqual([1, 2, 3]);
    expect(baselineReviewGroupIndices(draft, "all")).toEqual([0, 1, 2, 3]);
  });

  it("現在組がfilterから外れたら次の可視組へ進み、切替を検出できる", () => {
    expect(resolveBaselineReviewGroupIndex([1, 3], 1)).toBe(1);
    expect(resolveBaselineReviewGroupIndex([3], 1)).toBe(3);
    expect(resolveBaselineReviewGroupIndex([], 1)).toBe(-1);
  });

  it("復聴画面に今回の判断基準を明示する", () => {
    const markup = renderToStaticMarkup(
      createElement(BaselineReviewGuide, { mode: "quality-skipped" }),
    );

    expect(markup).toContain("本輪: 品質理由の skip");
    expect(markup).toContain("厳密な日本語の音調・アクセント");
    expect(markup).toContain("音質・自然さ・演技の総合品質");
    expect(markup).toContain("実際の音声内容に混入");
  });
});

function renderEditor(group: BaselineGroup): string {
  return renderToStaticMarkup(
    createElement(BaselineGroupEditor, {
      candidateDraft: {
        take_id: TAKE_ID,
        rubric: {
          content_correct: null,
          intent_match: null,
          character_naturalness: null,
          adoptable: null,
        },
      },
      decision: null,
      group,
      onClearDecision() {},
      onDecision() {},
      onNext() {},
      onPrevious() {},
      onRubric() {},
      player: {
        currentClipKey: null,
        status: "idle",
        async toggle() {},
      },
      position: 0,
      total: 1,
    }),
  );
}

function makeDraft(
  rows: readonly (readonly ["selected" | "skipped", boolean])[],
): BaselineCurationDraft {
  return {
    version: 1,
    candidate_set_sha256: "d".repeat(64),
    baseline_reference_sha256: "e".repeat(64),
    groups: rows.map(([decision, contentCorrect], index) => ({
      model: "model",
      scenario: "scene",
      line: `line-${index}`,
      variant: "dry",
      candidates: [
        {
          take_id: `${index}`.padStart(64, "0"),
          rubric: {
            content_correct: contentCorrect,
            intent_match: 3,
            character_naturalness: 3,
            adoptable: false,
          },
        },
      ],
      decision:
        decision === "selected"
          ? { type: "selected", take_id: `${index}`.padStart(64, "0") }
          : { type: "skipped" },
    })),
  };
}

function makeGroup(comparison: "identical" | "different"): BaselineGroup {
  return {
    model: "dummy",
    scenario: "scene",
    line: "line",
    variant: "dry",
    scenarioTitle: "警告",
    lineText: "ここは危険だ、下がれ！",
    delivery: "切迫して強く警告する",
    candidate: {
      label: "A",
      takeId: TAKE_ID,
      audio: { key: "candidate", url: "blob:candidate" },
      gateContent: "pass",
    },
    candidateSha256: CANDIDATE_SHA,
    reference: {
      audio: { key: "reference", url: "blob:reference" },
      publicPath: "audio/dummy/scene/line/dry.opus",
      sha256: REFERENCE_SHA,
      comparison,
    },
  };
}
