import type { AudioClip } from "@/audio/playback-manager";

export interface BaselineGate {
  readonly mechanical: "pass";
  readonly content: "pass" | "review_required";
  readonly policy_version: "take-gates-v2";
}

export interface BaselineCandidatePresentation {
  readonly label: string;
  readonly takeId: string;
  readonly audio: AudioClip;
  readonly gateContent: BaselineGate["content"];
}

export interface BaselineExportCandidate {
  readonly takeId: string;
  readonly path: string;
  readonly audioSha256: string;
  readonly gate: BaselineGate;
}

export interface BaselineGroup {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly scenarioTitle: string;
  readonly lineText: string;
  readonly delivery: string;
  readonly roleEpochSha256: string;
  readonly sourceRunId: string;
  readonly groupSha256: string;
  readonly candidates: readonly BaselineCandidatePresentation[];
  readonly exportCandidates: readonly BaselineExportCandidate[];
}

export interface BaselineCatalog {
  readonly planSha256: string;
  readonly anchorSelectionSha256: string;
  readonly candidateSetSha256: string;
  readonly groups: readonly BaselineGroup[];
  dispose(): void;
}

export interface BaselineRubric {
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

export interface BaselineCandidateDraft {
  readonly take_id: string;
  readonly rubric: BaselineRubric;
}

export interface BaselineGroupDraft {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly role_epoch_sha256: string;
  readonly group_sha256: string;
  readonly plan_sha256: string;
  readonly anchor_selection_sha256: string;
  readonly candidate_set_sha256: string;
  readonly revalidation_reason: string | null;
  readonly candidates: readonly BaselineCandidateDraft[];
  readonly decision: { readonly type: "selected"; readonly take_id: string } | null;
}

export interface BaselineDraft {
  readonly format_version: 1;
  readonly protocol: "role-baseline-draft-v1";
  readonly plan_sha256: string;
  readonly anchor_selection_sha256: string;
  readonly candidate_set_sha256: string;
  readonly groups: readonly BaselineGroupDraft[];
}

export function baselineGroupKey(
  value: Pick<BaselineGroup | BaselineGroupDraft, "model" | "scenario" | "line" | "variant">,
): string {
  return JSON.stringify([value.model, value.scenario, value.line, value.variant]);
}

export function compareBaselineGroups(
  left: Pick<BaselineGroup | BaselineGroupDraft, "model" | "scenario" | "line" | "variant">,
  right: Pick<BaselineGroup | BaselineGroupDraft, "model" | "scenario" | "line" | "variant">,
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
