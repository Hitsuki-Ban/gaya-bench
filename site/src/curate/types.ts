import type { AudioClip } from "@/audio/playback-manager";

export type GateContent = "pass" | "review_required";

export interface CurateCandidatePresentation {
  readonly label: string;
  readonly takeId: string;
  readonly audio: AudioClip;
  readonly gateContent: GateContent;
}

export interface CurateGroup {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly scenarioTitle: string;
  readonly lineText: string;
  readonly delivery: string;
  readonly candidates: readonly CurateCandidatePresentation[];
}

export interface ExportCandidate {
  readonly takeId: string;
  readonly path: string;
  readonly audioSha256: string;
  readonly gate?: {
    readonly mechanical: "pass";
    readonly content: GateContent;
    readonly policy_version: string;
  };
}

export interface CurateCatalog {
  readonly candidateSetSha256: string;
  readonly manifestCurationCount: number;
  readonly manifestFailureCount: number;
  readonly groups: readonly CurateGroup[];
  readonly exportCandidatesByGroup: ReadonlyMap<string, readonly ExportCandidate[]>;
  dispose(): void;
}

export interface Rubric {
  readonly content_correct: boolean | null;
  readonly intent_match: number | null;
  readonly character_naturalness: number | null;
  readonly adoptable: boolean | null;
}

export type CurateDecision =
  | { readonly type: "selected"; readonly take_id: string }
  | { readonly type: "skipped" };

export interface CandidateDraft {
  readonly take_id: string;
  readonly rubric: Rubric;
}

export interface GroupDraft {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly candidates: readonly CandidateDraft[];
  readonly decision: CurateDecision | null;
}

export interface CurationDraft {
  readonly version: 1;
  readonly candidate_set_sha256: string;
  readonly groups: readonly GroupDraft[];
}

export function groupKey(
  value: Pick<CurateGroup | GroupDraft, "model" | "scenario" | "line" | "variant">,
): string {
  return JSON.stringify([value.model, value.scenario, value.line, value.variant]);
}

export function compareGroupTuple(
  left: Pick<CurateGroup | GroupDraft, "model" | "scenario" | "line" | "variant">,
  right: Pick<CurateGroup | GroupDraft, "model" | "scenario" | "line" | "variant">,
): number {
  for (const key of ["model", "scenario", "line", "variant"] as const) {
    if (left[key] < right[key]) {
      return -1;
    }
    if (left[key] > right[key]) {
      return 1;
    }
  }
  return 0;
}
