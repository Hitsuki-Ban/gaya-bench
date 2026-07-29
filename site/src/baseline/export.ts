import { canonicalJson } from "@/lib/canonical-json";
import { writeBaselineCurationDraft, type BaselineCurationStorage } from "@/baseline/storage";
import type { BaselineCatalog, BaselineCurationDraft } from "@/baseline/types";
import { groupKey } from "@/curate/types";
import { isRubricComplete } from "@/curate/storage";

export function buildBaselineCurationJson(
  catalog: BaselineCatalog,
  draft: BaselineCurationDraft,
): string {
  validateCurrentDraft(catalog, draft);
  if (draft.groups.some((group) => group.decision === null)) {
    throw new Error(
      "baseline export には全ての策展可能 group の selected または skipped が必要です。",
    );
  }
  const groups = draft.groups.map((group) => {
    const exportCandidates = catalog.exportCandidatesByGroup.get(groupKey(group));
    if (!exportCandidates || exportCandidates.length !== 1) {
      throw new Error(`baseline export candidate が 1 件ではありません: ${groupKey(group)}`);
    }
    const candidate = exportCandidates[0]!;
    const candidateDraft = group.candidates[0];
    if (
      !candidateDraft ||
      candidateDraft.take_id !== candidate.takeId ||
      !isRubricComplete(candidateDraft.rubric)
    ) {
      throw new Error(`baseline candidate rubric が未完了です: ${candidate.takeId}`);
    }
    return {
      model: group.model,
      scenario: group.scenario,
      line: group.line,
      variant: group.variant,
      candidates: [
        {
          take_id: candidate.takeId,
          path: candidate.path,
          audio_sha256: candidate.audioSha256,
          rubric: candidateDraft.rubric,
        },
      ],
      decision: group.decision,
    };
  });
  return canonicalJson(
    {
      format_version: 1,
      rubric_version: "baseline-curation-v1",
      candidate_set_sha256: catalog.candidateSetSha256,
      baseline_reference_sha256: catalog.baselineReferenceSha256,
      groups,
    },
    "baseline curation artifact",
  );
}

export function downloadBaselineCurationJson(contents: string): void {
  const url = URL.createObjectURL(new Blob([contents], { type: "application/json" }));
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "baseline-curation.json";
    anchor.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

function validateCurrentDraft(catalog: BaselineCatalog, draft: BaselineCurationDraft): void {
  const sink: BaselineCurationStorage = {
    getItem() {
      return null;
    },
    setItem() {},
    removeItem() {},
  };
  writeBaselineCurationDraft(sink, catalog, draft);
}
