import { describe, expect, it } from "vite-plus/test";

import { buildRoleReviewDecision, buildRoleReviewDecisionJson } from "./export";
import {
  confirmRoleReviewGroup,
  createRoleReviewDraft,
  markRoleReviewNoUsableCandidate,
  markRoleReviewCandidateHeard,
  selectRoleReviewCandidate,
} from "./storage";
import { completeRoleReviewRubric, makeRoleReviewCatalog } from "./storage.test";

describe("role review v2 decision", () => {
  it("全候选听取、显式选择、问题记录和候选集绑定进入最终结果", () => {
    const catalog = makeRoleReviewCatalog();
    const group = catalog.groups[0]!;
    let draft = createRoleReviewDraft(catalog);
    for (const candidateId of group.candidate_ids) {
      draft = markRoleReviewCandidateHeard(catalog, draft, group.id, candidateId);
    }
    draft = selectRoleReviewCandidate(catalog, draft, group.id, group.candidate_ids[2]!);
    draft = confirmRoleReviewGroup(catalog, draft, group.id, {
      ...completeRoleReviewRubric(),
      pitch_accent: "fail",
      notes: "音调不准",
    });

    const decision = buildRoleReviewDecision(catalog, draft);
    expect(decision).toMatchObject({
      format_version: 2,
      protocol: "role-review-decision-v2",
      phase: "anchor",
      plan_sha256: catalog.planSha256,
      candidate_set_sha256: catalog.candidateSetSha256,
    });
    expect(decision.groups[0]).toMatchObject({
      selected_candidate_id: group.candidate_ids[2],
      heard_candidate_ids: group.candidate_ids,
      confirmed: true,
      rubric: {
        pitch_accent: "fail",
        voice_identity: "not_applicable",
        delivery: "not_applicable",
        notes: "音调不准",
      },
    });
    expect(buildRoleReviewDecisionJson(catalog, draft)).toBe(
      JSON.stringify(JSON.parse(buildRoleReviewDecisionJson(catalog, draft))),
    );
  });

  it("未确认项目不生成最终结果", () => {
    const catalog = makeRoleReviewCatalog();
    expect(() => buildRoleReviewDecision(catalog, createRoleReviewDraft(catalog))).toThrow(
      "确认全部",
    );
  });

  it("四条都不可用作为明确结果进入decision而不伪造候选", () => {
    const catalog = makeRoleReviewCatalog();
    const group = catalog.groups[0]!;
    let draft = createRoleReviewDraft(catalog);
    for (const candidateId of group.candidate_ids) {
      draft = markRoleReviewCandidateHeard(catalog, draft, group.id, candidateId);
    }
    draft = markRoleReviewNoUsableCandidate(catalog, draft, group.id);
    draft = confirmRoleReviewGroup(catalog, draft, group.id, {
      ...completeRoleReviewRubric(),
      reading: "fail",
      notes: "四条都有误读",
    });

    expect(buildRoleReviewDecision(catalog, draft).groups[0]).toMatchObject({
      no_usable_candidate: true,
      selected_candidate_id: null,
      confirmed: true,
      rubric: { reading: "fail", notes: "四条都有误读" },
    });
  });
});
