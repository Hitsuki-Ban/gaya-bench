import type { AudioClip } from "@/audio/playback-manager";
import type { HumanRubricDraft } from "@/components/human-rubric-fields";

export interface PilotCandidatePresentation {
  readonly candidateId: string;
  readonly label: "A" | "B" | "C";
  readonly audio: AudioClip;
}

export interface PilotGroupPresentation {
  readonly lineText: string;
  readonly reading: string;
  readonly delivery: string;
  readonly candidates: readonly PilotCandidatePresentation[];
}

export interface PilotCatalogGroup {
  readonly groupId: string;
  readonly presentation: PilotGroupPresentation;
}

export interface PilotCatalog {
  readonly pilotSetSha256: string;
  readonly groups: readonly PilotCatalogGroup[];
  dispose(): void;
}

export type PilotRubric = HumanRubricDraft;

export type PilotGroupDecision =
  | { readonly type: "selected"; readonly candidate_id: string }
  | { readonly type: "skipped" };

export interface PilotCandidateDraft {
  readonly candidate_id: string;
  readonly rubric: PilotRubric;
}

export interface PilotGroupDraft {
  readonly group_id: string;
  readonly candidates: readonly PilotCandidateDraft[];
  readonly decision: PilotGroupDecision | null;
}

export interface PilotDecisionDraft {
  readonly version: 1;
  readonly pilot_set_sha256: string;
  readonly groups: readonly PilotGroupDraft[];
}
