import { canonicalJson } from "@/lib/canonical-json";
import { isPilotRubricComplete, writePilotDecisionDraft, type PilotStorage } from "@/pilot/storage";
import type { PilotCatalog, PilotDecisionDraft } from "@/pilot/types";

export function buildPilotDecisionJson(catalog: PilotCatalog, draft: PilotDecisionDraft): string {
  validateCurrentDraft(catalog, draft);
  if (draft.groups.some((group) => group.decision === null)) {
    throw new Error("pilot decision export には全 group の selected または skipped が必要です。");
  }
  const groups = draft.groups.map((group) => ({
    group_id: group.group_id,
    candidates: group.candidates.map((candidate) => {
      if (!isPilotRubricComplete(candidate.rubric)) {
        throw new Error(`candidate rubric が未完了です: ${candidate.candidate_id}`);
      }
      return {
        candidate_id: candidate.candidate_id,
        rubric: candidate.rubric,
      };
    }),
    decision: group.decision,
  }));
  return canonicalJson(
    {
      format_version: 1,
      rubric_version: "n3-pilot-human-v1",
      pilot_set_sha256: catalog.pilotSetSha256,
      groups,
    },
    "pilot decision artifact",
  );
}

export function downloadPilotDecisionJson(contents: string): void {
  const url = URL.createObjectURL(new Blob([contents], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "pilot-decision.json";
  document.body.append(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    globalThis.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

function validateCurrentDraft(catalog: PilotCatalog, draft: PilotDecisionDraft): void {
  const sink: PilotStorage = {
    getItem() {
      return null;
    },
    setItem() {},
    removeItem() {},
  };
  writePilotDecisionDraft(sink, catalog, draft);
}
