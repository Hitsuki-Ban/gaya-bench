import type { BaselineAbDraft, BaselineAbListeningBootstrap } from "./local-listening-session";

export function createBaselineAbDraft(bootstrap: BaselineAbListeningBootstrap): BaselineAbDraft {
  return {
    format_version: 1,
    protocol: "baseline-quality-ab-draft-v1",
    study_id: bootstrap.bundle.study_id,
    groups: bootstrap.bundle.groups.map((group) => ({
      id: group.id,
      heard_candidate_ids: [],
      choice: null,
      notes: "",
    })),
    current_index: 0,
  };
}

export function baselineAbResultFromDraft(draft: BaselineAbDraft) {
  const { current_index: _currentIndex, ...result } = draft;
  return { ...result, protocol: "baseline-quality-ab-result-v1" as const };
}
