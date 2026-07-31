import { describe, expect, it } from "vite-plus/test";

import {
  createCompletionDraft,
  isCompletionRubricComplete,
  setCompletionDecision,
  updateCompletionRubric,
  writeCompletionDraft,
} from "./storage";
import { completionGroupKey, type CompletionCatalog, type CompletionRubric } from "./types";

describe("baseline completion storage", () => {
  it("全候補の実評価後は低品質候補もbest availableとして選べる", () => {
    const catalog = makeCatalog();
    let draft = createCompletionDraft(catalog);
    const key = completionGroupKey(draft.groups[0]!);
    for (const candidate of draft.groups[0]!.candidates) {
      draft = updateCompletionRubric(draft, key, candidate.take_id, {
        ...completeRubric(),
        content_correct: false,
        adoptable: false,
      });
    }

    expect(() =>
      setCompletionDecision(draft, key, draft.groups[0]!.candidates[1]!.take_id),
    ).not.toThrow();
  });

  it("候補評価が一つでも未完了なら選択を拒否する", () => {
    const catalog = makeCatalog();
    const draft = createCompletionDraft(catalog);
    const key = completionGroupKey(draft.groups[0]!);

    expect(() =>
      setCompletionDecision(draft, key, draft.groups[0]!.candidates[0]!.take_id),
    ).toThrow("全必須項目");
  });

  it("candidate setに束縛してcanonical draftだけ保存する", () => {
    const catalog = makeCatalog();
    const draft = createCompletionDraft(catalog);
    let stored = "";

    writeCompletionDraft(
      {
        getItem: () => null,
        setItem: (_key, value) => {
          stored = value;
        },
        removeItem() {},
      },
      catalog,
      draft,
    );

    expect(JSON.parse(stored)).toEqual(draft);
    expect(isCompletionRubricComplete(draft.groups[0]!.candidates[0]!.rubric)).toBe(false);
  });
});

export function makeCatalog(): CompletionCatalog {
  const candidates = [0, 1, 2].map((index) => ({
    label: String.fromCharCode(65 + index),
    takeId: String.fromCharCode(97 + index).repeat(64),
    audio: { key: `audio-${index}`, url: `blob:${index}` },
    gateContent: index === 0 ? ("review_required" as const) : ("pass" as const),
  }));
  const group = {
    model: "model",
    scenario: "scene",
    line: "line-001",
    variant: "dry",
    scenarioTitle: "Scene",
    lineText: "台詞",
    delivery: "強く",
    candidates,
  };
  return {
    candidateSetSha256: "d".repeat(64),
    manifestCurationCount: 0,
    manifestFailureCount: 0,
    groups: [group],
    exportCandidatesByGroup: new Map([
      [
        completionGroupKey(group),
        candidates.map((candidate, index) => ({
          takeId: candidate.takeId,
          path: `audio/takes/model/scene/line-001/dry/take-${String(index + 1).padStart(4, "0")}-${String.fromCharCode(101 + index).repeat(64)}.opus`,
          audioSha256: String.fromCharCode(101 + index).repeat(64),
          gate: {
            mechanical: "pass" as const,
            content: candidate.gateContent,
            policy_version: "take-gates-v2",
          },
        })),
      ],
    ]),
    dispose() {},
  };
}

export function completeRubric(): CompletionRubric {
  return {
    content_correct: true,
    prompt_leakage: false,
    reading_correct: true,
    accent_naturalness: 4,
    role_match: 4,
    delivery_match: 4,
    audio_quality: 4,
    adoptable: true,
    notes: "",
  };
}
