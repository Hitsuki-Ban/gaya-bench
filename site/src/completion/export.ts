import { canonicalJson } from "@/lib/canonical-json";

import { assertRoleReviewDraft } from "./storage";
import type { RoleReviewCatalog, RoleReviewDraft } from "./types";

export function buildRoleReviewDecisionJson(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
): string {
  assertRoleReviewDraft(draft, catalog);
  if (draft.groups.some((group) => !group.confirmed)) {
    throw new Error("role review decision のexportには全groupの明示確認が必要です。");
  }
  return canonicalJson(
    {
      format_version: 1,
      protocol: "role-review-decision-v1",
      phase: catalog.phase,
      plan_sha256: catalog.planSha256,
      candidate_set_sha256: catalog.candidateSetSha256,
      groups: draft.groups.map((group) => ({
        id: group.id,
        model: group.model,
        scenario: group.scenario,
        character: group.character,
        line: group.line,
        role_epoch_sha256: group.role_epoch_sha256,
        group_sha256: group.group_sha256,
        heard_candidate_ids: group.heard_candidate_ids,
        selected_candidate_id: group.selected_candidate_id,
        rubric: group.rubric,
        confirmed: true,
      })),
      role_reopen_requests: draft.role_reopen_requests,
    },
    "role review decision",
  );
}

export function downloadRoleReviewDecisionJson(
  contents: string,
  phase: RoleReviewCatalog["phase"],
): void {
  const url = URL.createObjectURL(new Blob([contents], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `role-review-${phase}-decision.json`;
  document.body.append(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    globalThis.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
