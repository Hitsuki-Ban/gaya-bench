import type { CurateCatalog } from "@/curate/types";

export interface CompletionRubric {
  readonly content_correct: boolean | null;
  readonly prompt_leakage: boolean | null;
  readonly reading_correct: boolean | null;
  readonly accent_naturalness: number | null;
  readonly role_match: number | null;
  readonly delivery_match: number | null;
  readonly audio_quality: number | null;
  readonly adoptable: boolean | null;
  readonly notes: string;
}

export interface CompletionCandidateDraft {
  readonly take_id: string;
  readonly rubric: CompletionRubric;
}

export interface CompletionGroupDraft {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly candidates: readonly CompletionCandidateDraft[];
  readonly decision: { readonly type: "selected"; readonly take_id: string } | null;
}

export interface CompletionDraft {
  readonly version: 1;
  readonly candidate_set_sha256: string;
  readonly groups: readonly CompletionGroupDraft[];
}

export type CompletionCatalog = CurateCatalog;

export function completionGroupKey(
  value: Pick<CompletionGroupDraft, "model" | "scenario" | "line" | "variant">,
): string {
  return JSON.stringify([value.model, value.scenario, value.line, value.variant]);
}
