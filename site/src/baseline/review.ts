import type { BaselineCurationDraft } from "@/baseline/types";

export type BaselineReviewMode = "all" | "quality-skipped" | "skipped";

export function baselineReviewGroupIndices(
  draft: BaselineCurationDraft,
  mode: BaselineReviewMode,
): number[] {
  if (mode === "all") return draft.groups.map((_, index) => index);
  return draft.groups.flatMap((group, index) => {
    if (group.decision?.type !== "skipped") return [];
    if (mode === "quality-skipped" && group.candidates[0]!.rubric.content_correct !== true) {
      return [];
    }
    return [index];
  });
}

export function resolveBaselineReviewGroupIndex(
  indices: readonly number[],
  requestedIndex: number,
): number {
  if (indices.includes(requestedIndex)) return requestedIndex;
  return indices.find((index) => index >= requestedIndex) ?? indices.at(-1) ?? -1;
}
