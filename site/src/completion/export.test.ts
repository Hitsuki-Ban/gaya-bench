import { describe, expect, it } from "vite-plus/test";

import { buildCompletionDecisionJson } from "./export";
import { createCompletionDraft, setCompletionDecision, updateCompletionRubric } from "./storage";
import { completeRubric, makeCatalog } from "./storage.test";
import { completionGroupKey } from "./types";

describe("baseline completion export", () => {
  it("best_available authority、gate、実rubricをexact artifactへ出力する", () => {
    const catalog = makeCatalog();
    let draft = createCompletionDraft(catalog);
    const key = completionGroupKey(draft.groups[0]!);
    for (const candidate of draft.groups[0]!.candidates) {
      draft = updateCompletionRubric(draft, key, candidate.take_id, {
        ...completeRubric(),
        content_correct: false,
        adoptable: false,
        notes: "三候補中では最良",
      });
    }
    draft = setCompletionDecision(draft, key, draft.groups[0]!.candidates[1]!.take_id);

    const document = JSON.parse(buildCompletionDecisionJson(catalog, draft));
    expect(document).toMatchObject({
      format_version: 1,
      protocol: "baseline-completion-decision-v1",
      candidate_set_sha256: "d".repeat(64),
    });
    expect(document.groups[0].authority).toEqual({
      type: "best_available",
      policy_version: "missing-slot-best-of-n-v1",
      reviewer: "owner",
      minimum_eligible_candidates: 3,
    });
    expect(document.groups[0].candidates).toHaveLength(3);
    expect(document.groups[0].candidates[0].gate).toEqual({
      mechanical: "pass",
      content: "review_required",
      policy_version: "take-gates-v2",
    });
    expect(document.groups[0].candidates[0].rubric).toMatchObject({
      content_correct: false,
      adoptable: false,
      notes: "三候補中では最良",
    });
  });

  it("未選択groupを含むexportを拒否する", () => {
    const catalog = makeCatalog();
    const draft = createCompletionDraft(catalog);

    expect(() => buildCompletionDecisionJson(catalog, draft)).toThrow("全 group");
  });
});
