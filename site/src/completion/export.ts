import { canonicalJson } from "@/lib/canonical-json";

import { isCompletionRubricComplete, writeCompletionDraft } from "./storage";
import { completionGroupKey, type CompletionCatalog, type CompletionDraft } from "./types";

export function buildCompletionDecisionJson(
  catalog: CompletionCatalog,
  draft: CompletionDraft,
): string {
  const sink = {
    getItem() {
      return null;
    },
    setItem() {},
    removeItem() {},
  };
  writeCompletionDraft(sink, catalog, draft);
  if (draft.groups.some((group) => group.decision === null)) {
    throw new Error("補録 decision の export には全 group の選択が必要です。");
  }

  const groups = draft.groups.map((group) => {
    const exportCandidates = catalog.exportCandidatesByGroup.get(completionGroupKey(group));
    if (!exportCandidates) {
      throw new Error(`export candidate group がありません: ${completionGroupKey(group)}`);
    }
    const draftsByTake = new Map(
      group.candidates.map((candidate) => [candidate.take_id, candidate]),
    );
    const candidates = exportCandidates.map((candidate) => {
      const candidateDraft = draftsByTake.get(candidate.takeId);
      if (!candidateDraft || !isCompletionRubricComplete(candidateDraft.rubric)) {
        throw new Error(`candidate rubric が未完了です: ${candidate.takeId}`);
      }
      if (!candidate.gate) {
        throw new Error(`candidate gate が export catalog にありません: ${candidate.takeId}`);
      }
      return {
        take_id: candidate.takeId,
        path: candidate.path,
        audio_sha256: candidate.audioSha256,
        gate: candidate.gate,
        rubric: candidateDraft.rubric,
      };
    });
    return {
      model: group.model,
      scenario: group.scenario,
      line: group.line,
      variant: group.variant,
      authority: {
        type: "best_available",
        policy_version: "missing-slot-best-of-n-v1",
        reviewer: "owner",
        minimum_eligible_candidates: 3,
      },
      candidates,
      decision: group.decision,
    };
  });

  return canonicalJson(
    {
      format_version: 1,
      protocol: "baseline-completion-decision-v1",
      candidate_set_sha256: catalog.candidateSetSha256,
      groups,
    },
    "baseline completion decision",
  );
}

export function downloadCompletionDecisionJson(contents: string): void {
  const url = URL.createObjectURL(new Blob([contents], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "baseline-completion-decision.json";
  document.body.append(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    globalThis.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
