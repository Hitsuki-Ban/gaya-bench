import type {
  QualityReviewDraft,
  QualityReviewListeningBootstrap,
} from "./local-listening-session";

export function createQualityReviewDraft(
  bootstrap: QualityReviewListeningBootstrap,
): QualityReviewDraft {
  return {
    format_version: 1,
    protocol: "role-quality-review-draft-v1",
    plan_sha256: bootstrap.bundle.plan_sha256,
    decision_sha256: bootstrap.bundle.decision_sha256,
    manifest_sha256: bootstrap.bundle.manifest_sha256,
    quality_signals_sha256: bootstrap.bundle.quality_signals_sha256,
    groups: bootstrap.bundle.groups.map((group) => ({
      model: group.model,
      scenario: group.scenario,
      line: group.line,
      variant: group.variant,
      take_id: group.take_id,
      heard: false,
      result: null,
      notes: "",
    })),
    current_index: 0,
  };
}

export function qualityReviewResultFromDraft(draft: QualityReviewDraft) {
  const { current_index: _currentIndex, ...result } = draft;
  return { ...result, protocol: "role-quality-review-result-v1" as const };
}
