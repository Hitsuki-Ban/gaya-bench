import { groupKey, type CurateCatalog, type CurationDraft } from "./types";
import { isRubricComplete, writeCurationDraft, type CurationStorage } from "./storage";
import { canonicalJson } from "@/lib/canonical-json";

export function buildCurationJson(catalog: CurateCatalog, draft: CurationDraft): string {
  validateCurrentDraft(catalog, draft);
  const decidedGroups = draft.groups.filter((group) => group.decision !== null);
  if (decidedGroups.length === 0) {
    throw new Error("export には策展済み group が 1 件以上必要です。");
  }

  const groups = decidedGroups.map((group) => {
    const exportCandidates = catalog.exportCandidatesByGroup.get(groupKey(group));
    if (!exportCandidates) {
      throw new Error(`export candidate group がありません: ${groupKey(group)}`);
    }
    const draftsByTake = new Map(
      group.candidates.map((candidate) => [candidate.take_id, candidate]),
    );
    const candidates = exportCandidates.map((candidate) => {
      const candidateDraft = draftsByTake.get(candidate.takeId);
      if (!candidateDraft || !isRubricComplete(candidateDraft.rubric)) {
        throw new Error(`candidate rubric が未完了です: ${candidate.takeId}`);
      }
      return {
        take_id: candidate.takeId,
        path: candidate.path,
        audio_sha256: candidate.audioSha256,
        rubric: candidateDraft.rubric,
      };
    });
    return {
      model: group.model,
      scenario: group.scenario,
      line: group.line,
      variant: group.variant,
      candidates,
      decision: group.decision,
    };
  });
  return canonicalJson(
    {
      format_version: 1,
      rubric_version: "take-curation-v1",
      candidate_set_sha256: catalog.candidateSetSha256,
      groups,
    },
    "curation artifact",
  );
}

export function downloadCurationJson(contents: string): void {
  const url = URL.createObjectURL(new Blob([contents], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "curation.json";
  document.body.append(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    globalThis.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

function validateCurrentDraft(catalog: CurateCatalog, draft: CurationDraft): void {
  const sink: CurationStorage = {
    getItem() {
      return null;
    },
    setItem() {},
    removeItem() {},
  };
  writeCurationDraft(sink, catalog, draft);
}
