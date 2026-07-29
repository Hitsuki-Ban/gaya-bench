import type { PilotGroupDraft } from "@/pilot/types";

export function findNextUndecidedGroupIndex(
  groups: readonly Pick<PilotGroupDraft, "decision">[],
  currentIndex: number,
): number | null {
  for (let offset = 1; offset <= groups.length; offset += 1) {
    const index = (currentIndex + offset) % groups.length;
    if (groups[index]?.decision === null) {
      return index;
    }
  }
  return null;
}
