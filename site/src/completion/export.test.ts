import { describe, expect, it } from "vite-plus/test";

import { buildRoleReviewDecisionJson } from "./export";
import { createRoleReviewDraft } from "./storage";
import { confirmAllGroups, makeRoleReviewCatalog, makeRoleReviewGroup } from "./storage.test";

describe("role review export", () => {
  it("plan/candidate-setと各groupのepoch/hash/heard/rubric/confirmedを束縛する", () => {
    const catalog = makeRoleReviewCatalog({
      groups: [makeRoleReviewGroup({ comparisonRequired: true })],
    });
    const confirmed = confirmAllGroups(catalog);
    const reopenReason = "role epochを再確認したため";
    const draft = {
      ...confirmed,
      groups: confirmed.groups.map((group) => ({
        ...group,
        role_reopen_reason: reopenReason,
      })),
      role_reopen_requests: [
        {
          model: "model-a",
          character: "character-a",
          role_epoch_sha256: "e".repeat(64),
          reason: reopenReason,
        },
      ],
    };

    const document = JSON.parse(buildRoleReviewDecisionJson(catalog, draft));
    expect(document).toMatchObject({
      format_version: 1,
      protocol: "role-review-decision-v1",
      phase: "line",
      plan_sha256: "1".repeat(64),
      candidate_set_sha256: "2".repeat(64),
    });
    expect(document.groups[0]).toMatchObject({
      id: "9".repeat(64),
      role_epoch_sha256: "e".repeat(64),
      group_sha256: "f".repeat(64),
      selected_candidate_id: "a".repeat(64),
      confirmed: true,
      rubric: {
        content: "pass",
        pitch_accent: "pass",
        voice_identity: "pass",
        naturalness_quality: 4,
      },
    });
    expect(document.groups[0].heard_candidate_ids).toEqual(["a".repeat(64), "b".repeat(64)]);
    expect(document.role_reopen_requests[0].reason).toBe("role epochを再確認したため");
  });

  it("未確認groupが一つでもあればexportを拒否する", () => {
    const catalog = makeRoleReviewCatalog();
    expect(() => buildRoleReviewDecisionJson(catalog, createRoleReviewDraft(catalog))).toThrow(
      "全group",
    );
  });
});
