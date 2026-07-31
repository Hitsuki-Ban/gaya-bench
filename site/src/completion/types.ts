import type { AudioClip } from "@/audio/playback-manager";

export type RoleReviewPhase = "anchor" | "line";
export type RoleCoverage = "exact" | "approximate" | "neutral";
export type RubricResult = "pass" | "fail" | "not_applicable";

export interface RoleReviewLine {
  readonly id: string;
  readonly text: string;
  readonly delivery: string;
}

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
  readonly mechanical: "pass" | "fail";
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
  readonly line: RoleReviewLine | null;
  readonly role_epoch_sha256: string;
  readonly role: RoleReviewRole;
  readonly conditioning: RoleReviewConditioning;
  readonly coverage: RoleReviewCoverage;
  readonly comparison_required: boolean;
  readonly comparison_reasons: readonly string[];
  readonly candidate_ids: readonly string[];
  readonly provisional_candidate_id: string;
  readonly candidates: readonly RoleReviewCandidate[];
}

export interface RoleReviewBundle {
  readonly format_version: 1;
  readonly protocol: "role-review-v1";
  readonly phase: RoleReviewPhase;
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
  readonly phase: RoleReviewPhase;
  readonly group_sha256: string;
  readonly candidate_ids: readonly string[];
  readonly candidates: readonly RoleReviewCandidatePresentation[];
}

export interface RoleReviewCatalog {
  readonly phase: RoleReviewPhase;
  readonly planSha256: string;
  readonly candidateSetSha256: string;
  readonly groups: readonly RoleReviewGroup[];
  dispose(): void;
}

export interface RoleReviewRubric {
  readonly content: RubricResult | null;
  readonly prompt_leakage: RubricResult | null;
  readonly reading: RubricResult | null;
  readonly pitch_accent: RubricResult | null;
  readonly gender: RubricResult | null;
  readonly age: RubricResult | null;
  readonly archetype: RubricResult | null;
  readonly voice_identity: RubricResult | null;
  readonly delivery: RubricResult | null;
  readonly naturalness_quality: number | null;
  readonly notes: string;
}

export interface RoleReviewGroupDraft {
  readonly id: string;
  readonly phase: RoleReviewPhase;
  readonly model: string;
  readonly scenario: string;
  readonly character: string;
  readonly line: string | null;
  readonly role_epoch_sha256: string;
  readonly group_sha256: string;
  readonly plan_sha256: string;
  readonly role_reopen_reason: string | null;
  readonly candidate_group_change_reason: string | null;
  readonly heard_candidate_ids: readonly string[];
  readonly selected_candidate_id: string;
  readonly rubric: RoleReviewRubric;
  readonly confirmed: boolean;
}

export interface RoleReopenRequest {
  readonly model: string;
  readonly character: string;
  readonly role_epoch_sha256: string;
  readonly reason: string;
}

export interface RoleReviewDraft {
  readonly format_version: 1;
  readonly protocol: "role-review-draft-v1";
  readonly phase: RoleReviewPhase;
  readonly plan_sha256: string;
  readonly candidate_set_sha256: string;
  readonly groups: readonly RoleReviewGroupDraft[];
  readonly role_reopen_requests: readonly RoleReopenRequest[];
}

export function roleKey(
  value: Pick<RoleReviewGroup | RoleReviewGroupDraft, "model" | "character">,
): string {
  return JSON.stringify([value.model, value.character]);
}

export function roleReviewGroupKey(
  value: Pick<RoleReviewGroup | RoleReviewGroupDraft, "id" | "group_sha256">,
): string {
  return JSON.stringify([value.id, value.group_sha256]);
}
