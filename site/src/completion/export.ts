import { canonicalJson } from "@/lib/canonical-json";

import { assertRoleReviewDraft } from "./storage";
import type { RoleReviewCatalog, RoleReviewDecision, RoleReviewDraft } from "./types";

export function buildRoleReviewDecision(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
): RoleReviewDecision {
  assertRoleReviewDraft(draft, catalog);
  if (draft.groups.some((group) => !group.confirmed)) {
    throw new Error("保存最终结果前，需要确认全部听测项目。");
  }
  return {
    format_version: 2,
    protocol: "role-review-decision-v2",
    phase: "anchor",
    plan_sha256: catalog.planSha256,
    candidate_set_sha256: catalog.candidateSetSha256,
    groups: draft.groups.map((group) => {
      if (!group.confirmed) {
        throw new Error("已确认项目状态无效。");
      }
      return {
        ...group,
        confirmed: true,
      };
    }),
  };
}

export function buildRoleReviewDecisionJson(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
): string {
  return canonicalJson(buildRoleReviewDecision(catalog, draft), "role review decision");
}
