import type { AudioClip } from "@/audio/playback-manager";

export type RoleCoverage = "exact" | "neutral";
export type RubricResult = "pass" | "fail";

export interface RoleReviewRole {
  readonly name: string;
  readonly kind: "human" | "machine" | "creature" | "spirit";
  readonly gender: "female" | "male" | "neutral";
  readonly age: "child" | "teen" | "young_adult" | "adult" | "middle_aged" | "elderly";
  readonly archetype: string;
  readonly voice: string;
  readonly personality: string;
}

export interface RoleReviewConditioning {
  readonly method: string;
  readonly summary: string;
}

export interface RoleReviewCoverage {
  readonly gender: RoleCoverage;
  readonly age: RoleCoverage;
  readonly archetype: RoleCoverage;
}

export interface RoleReviewQc {
  readonly mechanical: "pass";
  readonly content: "not_checked" | "pass" | "review_required";
  readonly notes: readonly string[];
}

export interface RoleReviewCandidate {
  readonly id: string;
  readonly attempt: number;
  readonly seed: number;
  readonly audio_path: string;
  readonly audio_sha256: string;
  readonly qc: RoleReviewQc;
}

export interface RoleReviewInputGroup {
  readonly id: string;
  readonly model: string;
  readonly scenario: string;
  readonly character: string;
  readonly line: null;
  readonly anchor_text: string;
  readonly role_epoch_sha256: string;
  readonly role: RoleReviewRole;
  readonly conditioning: RoleReviewConditioning;
  readonly coverage: RoleReviewCoverage;
  readonly comparison_required: true;
  readonly comparison_reasons: readonly string[];
  readonly candidate_ids: readonly string[];
  readonly candidates: readonly RoleReviewCandidate[];
}

export interface RoleReviewBundle {
  readonly format_version: 2;
  readonly protocol: "role-review-v2";
  readonly phase: "anchor";
  readonly plan_sha256: string;
  readonly candidate_set_sha256: string;
  readonly groups: readonly RoleReviewInputGroup[];
}

export interface RoleReviewCandidatePresentation extends RoleReviewCandidate {
  readonly label: string;
  readonly audio: AudioClip;
}

export interface RoleReviewGroup extends Omit<
  RoleReviewInputGroup,
  "candidate_ids" | "candidates"
> {
  readonly group_sha256: string;
  readonly candidate_ids: readonly string[];
  readonly candidates: readonly RoleReviewCandidatePresentation[];
}

export interface RoleReviewCatalog {
  readonly phase: "anchor";
  readonly planSha256: string;
  readonly candidateSetSha256: string;
  readonly groups: readonly RoleReviewGroup[];
}

export interface RoleReviewRubric {
  readonly content: RubricResult | null;
  readonly prompt_leakage: RubricResult | null;
  readonly reading: RubricResult | null;
  readonly pitch_accent: RubricResult | null;
  readonly gender: RubricResult | null;
  readonly age: RubricResult | null;
  readonly archetype: RubricResult | null;
  readonly voice_identity: "not_applicable" | null;
  readonly delivery: "not_applicable" | null;
  readonly naturalness_quality: number | null;
  readonly notes: string;
}

export interface RoleReviewGroupDraft {
  readonly id: string;
  readonly model: string;
  readonly scenario: string;
  readonly character: string;
  readonly line: null;
  readonly role_epoch_sha256: string;
  readonly group_sha256: string;
  readonly heard_candidate_ids: readonly string[];
  readonly selected_candidate_id: string | null;
  readonly no_usable_candidate: boolean;
  readonly rubric: RoleReviewRubric;
  readonly confirmed: boolean;
}

export interface RoleReviewDraft {
  readonly format_version: 2;
  readonly protocol: "role-review-draft-v2";
  readonly phase: "anchor";
  readonly plan_sha256: string;
  readonly candidate_set_sha256: string;
  readonly current_group_id: string;
  readonly groups: readonly RoleReviewGroupDraft[];
}

export interface RoleReviewDecision {
  readonly format_version: 2;
  readonly protocol: "role-review-decision-v2";
  readonly phase: "anchor";
  readonly plan_sha256: string;
  readonly candidate_set_sha256: string;
  readonly groups: readonly (RoleReviewGroupDraft & {
    readonly confirmed: true;
  })[];
}

export function roleReviewGroupKey(
  value: Pick<RoleReviewGroup | RoleReviewGroupDraft, "id" | "group_sha256">,
): string {
  return JSON.stringify([value.id, value.group_sha256]);
}
