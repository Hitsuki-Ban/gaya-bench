import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { PilotGroupEditor } from "@/pages/pilot-page";
import { findNextUndecidedGroupIndex } from "@/pilot/navigation";
import type { PilotGroupDraft, PilotGroupPresentation } from "@/pilot/types";

describe("PilotGroupEditor", () => {
  it("固定 line 情報と A/B/C rubric だけを表示し、内部 ID を露出しない", () => {
    const candidateIds = [hex(1), hex(2), hex(3)];
    const group: PilotGroupPresentation = {
      lineText: "ここは危険だ、下がれ！",
      reading: "ここはきけんだ、さがれ！",
      delivery: "切迫して、周囲へ強く警告する",
      candidates: candidateIds.map((candidateId, index) => ({
        candidateId,
        label: ["A", "B", "C"][index] as "A" | "B" | "C",
        audio: {
          key: `pilot:${candidateId}`,
          url: `blob:${candidateId}`,
        },
      })),
    };
    const draft: PilotGroupDraft = {
      group_id: hex(100),
      candidates: candidateIds.map((candidateId) => ({
        candidate_id: candidateId,
        rubric: {
          content_correct: null,
          intent_match: null,
          character_naturalness: null,
          adoptable: null,
        },
      })),
      decision: null,
    };
    const markup = renderToStaticMarkup(
      createElement(PilotGroupEditor, {
        draft,
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
        total: 72,
      }),
    );

    expect(markup).toContain(group.lineText);
    expect(markup).toContain(group.reading);
    expect(markup).toContain(group.delivery);
    expect(markup).toContain("候補 A");
    expect(markup).toContain("候補 B");
    expect(markup).toContain("候補 C");
    expect(markup).toContain("内容は正しい");
    expect(markup).toContain("意図一致");
    expect(markup).toContain("役として自然");
    expect(markup).toContain("採用可能");
    expect(markup).toContain("厳密な日本語の音調・アクセントまで含みます");
    expect(markup).toContain("内容の判定とは独立して");
    expect(markup).toContain("選択は絶対的な合格を意味しません");
    for (const candidateId of candidateIds) {
      expect(markup).not.toContain(candidateId);
    }
    expect(markup).not.toContain(draft.group_id);
    for (const hidden of [
      "qwen3-tts-12hz-1.7b",
      "hard_rejected",
      "explicit_reading_mismatch",
      "seed_base",
      "mora_per_second",
      "ASR",
    ]) {
      expect(markup).not.toContain(hidden);
    }
  });
});

describe("findNextUndecidedGroupIndex", () => {
  const selected = {
    decision: { type: "selected" as const, candidate_id: hex(1) },
  };
  const skipped = { decision: { type: "skipped" as const } };
  const undecided = { decision: null };

  it("現在位置より後ろの未評価 group へ進み、末尾では先頭へ循環する", () => {
    const groups = [undecided, selected, skipped, undecided];

    expect(findNextUndecidedGroupIndex(groups, 0)).toBe(3);
    expect(findNextUndecidedGroupIndex(groups, 3)).toBe(0);
  });

  it("全 group 評価済みなら移動先を返さない", () => {
    expect(findNextUndecidedGroupIndex([selected, skipped], 0)).toBeNull();
  });
});

function hex(value: number): string {
  return value.toString(16).padStart(64, "0");
}
