import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { baselineSelectionStatus } from "@/baseline/selection";
import type { BaselineGroup } from "@/baseline/types";
import { BaselineGroupEditor } from "@/pages/baseline-curate-page";

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
