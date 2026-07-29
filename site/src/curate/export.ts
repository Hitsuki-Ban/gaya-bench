import { groupKey, type CurateCatalog, type CurationDraft } from "./types";
import { isRubricComplete, writeCurationDraft, type CurationStorage } from "./storage";

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
  return canonicalJson({
    format_version: 1,
    rubric_version: "take-curation-v1",
    candidate_set_sha256: catalog.candidateSetSha256,
    groups,
  });
}

export function downloadCurationJson(contents: string): void {
  const url = URL.createObjectURL(new Blob([contents], { type: "application/json" }));
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "curation.json";
    anchor.click();
  } finally {
    URL.revokeObjectURL(url);
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

function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalize(value));
}

function canonicalize(value: unknown): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) {
      throw new Error("curation artifact の数値は安全な整数である必要があります。");
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(canonicalize);
  }
  if (typeof value === "object" && value !== null) {
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) {
      if (!/^[\x20-\x7e]+$/.test(key)) {
        throw new Error(`curation artifact の key は ASCII である必要があります: ${key}`);
      }
      result[key] = canonicalize((value as Record<string, unknown>)[key]);
    }
    return result;
  }
  throw new Error("curation artifact に JSON ではない値があります。");
}
