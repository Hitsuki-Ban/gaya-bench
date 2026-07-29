import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { PilotGroupEditor } from "@/pages/pilot-page";
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

function hex(value: number): string {
  return value.toString(16).padStart(64, "0");
}
