import { describe, expect, it } from "vite-plus/test";

import {
  applyRoleReviewPlaybackCompletion,
  completeAnchorRubric,
  confirmRoleReviewGroup,
  createRoleReviewDraft,
  markRoleReviewNoUsableCandidate,
  markRoleReviewCandidateHeard,
  parseRoleReviewDraft,
  requiredHeardCount,
  roleReviewProblemCount,
  selectRoleReviewCandidate,
  setCurrentRoleReviewGroup,
  updateRoleReviewRubric,
} from "./storage";
import type {
  RoleReviewCandidatePresentation,
  RoleReviewCatalog,
  RoleReviewGroup,
  RoleReviewRubric,
} from "./types";

describe("role review v2 draft", () => {
  it("初始没有默认候选，四选一必须完整听完4条并显式选择", () => {
    const catalog = makeRoleReviewCatalog();
    const group = catalog.groups[0]!;
    let draft = createRoleReviewDraft(catalog);
    expect(draft.groups[0]!.selected_candidate_id).toBeNull();
    expect(draft.groups[0]!.no_usable_candidate).toBe(false);
    expect(requiredHeardCount(group)).toBe(4);

    for (const candidateId of group.candidate_ids.slice(0, 3)) {
      draft = markRoleReviewCandidateHeard(catalog, draft, group.id, candidateId);
    }
    draft = selectRoleReviewCandidate(catalog, draft, group.id, group.candidate_ids[0]!);
    expect(() =>
      confirmRoleReviewGroup(
        catalog,
        draft,
        group.id,
        completeAnchorRubric(draft.groups[0]!.rubric),
      ),
    ).toThrow("全部 4 个候选");

    draft = markRoleReviewCandidateHeard(catalog, draft, group.id, group.candidate_ids[3]!);
    draft = confirmRoleReviewGroup(
      catalog,
      draft,
      group.id,
      completeAnchorRubric(draft.groups[0]!.rubric),
    );
    expect(draft.groups[0]).toMatchObject({
      confirmed: true,
      selected_candidate_id: group.candidate_ids[0],
    });
  });

  it("四条都不可用可明确保存，但必须听完并标记原因", () => {
    const catalog = makeRoleReviewCatalog();
    const group = catalog.groups[0]!;
    let draft = createRoleReviewDraft(catalog);
    for (const candidateId of group.candidate_ids) {
      draft = markRoleReviewCandidateHeard(catalog, draft, group.id, candidateId);
    }
    draft = markRoleReviewNoUsableCandidate(catalog, draft, group.id);
    expect(draft.groups[0]).toMatchObject({
      no_usable_candidate: true,
      selected_candidate_id: null,
    });
    expect(() =>
      confirmRoleReviewGroup(
        catalog,
        draft,
        group.id,
        completeAnchorRubric(draft.groups[0]!.rubric),
      ),
    ).toThrow("至少需要标记一个问题");

    draft = updateRoleReviewRubric(catalog, draft, group.id, {
      ...draft.groups[0]!.rubric,
      gender: "fail",
      notes: "四条都是男声",
    });
    draft = confirmRoleReviewGroup(
      catalog,
      draft,
      group.id,
      completeAnchorRubric(draft.groups[0]!.rubric),
    );
    expect(draft.groups[0]).toMatchObject({
      confirmed: true,
      no_usable_candidate: true,
      selected_candidate_id: null,
      rubric: { gender: "fail", notes: "四条都是男声" },
    });

    draft = selectRoleReviewCandidate(catalog, draft, group.id, group.candidate_ids[0]!);
    expect(draft.groups[0]).toMatchObject({
      confirmed: false,
      no_usable_candidate: false,
      selected_candidate_id: group.candidate_ids[0],
      rubric: { gender: null, notes: "" },
    });
  });

  it("性别不符的候选不能确认为可用anchor", () => {
    const catalog = makeRoleReviewCatalog();
    const group = catalog.groups[0]!;
    let draft = createRoleReviewDraft(catalog);
    for (const candidateId of group.candidate_ids) {
      draft = markRoleReviewCandidateHeard(catalog, draft, group.id, candidateId);
    }
    draft = selectRoleReviewCandidate(catalog, draft, group.id, group.candidate_ids[0]!);
    const rubric = completeAnchorRubric({
      ...draft.groups[0]!.rubric,
      gender: "fail",
    });

    expect(() => confirmRoleReviewGroup(catalog, draft, group.id, rubric)).toThrow(
      "所选候选性别不符",
    );
  });

  it("只有自然播放结束会记录为完整听过", () => {
    const catalog = makeRoleReviewCatalog();
    const draft = createRoleReviewDraft(catalog);
    const clipKey = catalog.groups[0]!.candidates[0]!.audio.key;
    const stopped = applyRoleReviewPlaybackCompletion(catalog, draft, {
      sessionId: 1,
      clipKey,
      termination: "stopped",
    });
    expect(stopped).toBe(draft);
    const ended = applyRoleReviewPlaybackCompletion(catalog, draft, {
      sessionId: 2,
      clipKey,
      termination: "ended",
    });
    expect(ended.groups[0]!.heard_candidate_ids).toEqual([catalog.groups[0]!.candidate_ids[0]]);
  });

  it("更换候选时清空旧候选的问题记录，避免串用评价", () => {
    const catalog = makeRoleReviewCatalog();
    const group = catalog.groups[0]!;
    let draft = createRoleReviewDraft(catalog);
    draft = selectRoleReviewCandidate(catalog, draft, group.id, group.candidate_ids[0]!);
    draft = updateRoleReviewRubric(catalog, draft, group.id, {
      ...draft.groups[0]!.rubric,
      gender: "fail",
      notes: "男声",
    });
    draft = selectRoleReviewCandidate(catalog, draft, group.id, group.candidate_ids[1]!);
    expect(draft.groups[0]!.rubric).toMatchObject({ gender: null, notes: "" });
  });

  it("Anchor确认只补全本轮可判断项，跨行一致性和逐句演技固定为不适用", () => {
    const rubric = completeAnchorRubric({
      ...emptyRubric(),
      pitch_accent: "fail",
      naturalness_quality: 3,
    });
    expect(rubric).toMatchObject({
      content: "pass",
      pitch_accent: "fail",
      gender: "pass",
      voice_identity: "not_applicable",
      delivery: "not_applicable",
      naturalness_quality: 3,
    });
  });

  it("未标记音质问题时的默认4分不计为问题", () => {
    const rubric = completeAnchorRubric(emptyRubric());

    expect(rubric.naturalness_quality).toBe(4);
    expect(roleReviewProblemCount(rubric)).toBe(0);
  });

  it("草稿绑定候选集并恢复当前组，未知旧字段不迁移", () => {
    const catalog = makeRoleReviewCatalog({ groupCount: 2 });
    const draft = setCurrentRoleReviewGroup(
      catalog,
      createRoleReviewDraft(catalog),
      catalog.groups[1]!.id,
    );
    expect(parseRoleReviewDraft(JSON.parse(JSON.stringify(draft)), catalog).current_group_id).toBe(
      catalog.groups[1]!.id,
    );
    expect(() =>
      parseRoleReviewDraft({ ...draft, candidate_set_sha256: "0".repeat(64) }, catalog),
    ).toThrow("候选集不一致");
    expect(() => parseRoleReviewDraft({ ...draft, role_reopen_requests: [] }, catalog)).toThrow(
      "exact contract",
    );
    const invalidApplicableResult = JSON.parse(JSON.stringify(draft)) as {
      groups: Array<{ rubric: { content: string } }>;
    };
    invalidApplicableResult.groups[0]!.rubric.content = "not_applicable";
    expect(() => parseRoleReviewDraft(invalidApplicableResult, catalog)).toThrow("pass / fail");
  });
});

export function makeRoleReviewCatalog({
  groupCount = 1,
}: { groupCount?: number } = {}): RoleReviewCatalog {
  return {
    phase: "anchor",
    planSha256: "1".repeat(64),
    candidateSetSha256: "2".repeat(64),
    groups: Array.from({ length: groupCount }, (_, index) => makeRoleReviewGroup(index)),
  };
}

export function makeRoleReviewGroup(index = 0): RoleReviewGroup {
  const candidates = Array.from(
    { length: 4 },
    (_, candidateIndex): RoleReviewCandidatePresentation => {
      const id = shaValue(index * 10 + candidateIndex + 1);
      return {
        id,
        attempt: candidateIndex + 1,
        seed: candidateIndex,
        audio_path: `audio/${id}.wav`,
        audio_sha256: shaValue(index * 100 + candidateIndex + 100),
        qc: { mechanical: "pass", content: "not_checked", notes: [] },
        label: String.fromCharCode(65 + candidateIndex),
        audio: { key: `clip-${index}-${candidateIndex}`, url: `/audio/${id}.wav` },
      };
    },
  );
  return {
    id: shaValue(1_000 + index),
    model: "qwen3-tts-12hz-1.7b",
    scenario: `scene-${index}`,
    character: `role-${index}`,
    line: null,
    anchor_text: "さて、きょうもいちにちをはじめましょう。",
    role_epoch_sha256: shaValue(2_000 + index),
    group_sha256: shaValue(3_000 + index),
    role: {
      name: "受付嬢",
      kind: "human",
      gender: "female",
      age: "young_adult",
      archetype: "受付",
      voice: "落ち着いた声",
      personality: "親切",
    },
    conditioning: { method: "voice-design-anchor-then-clone", summary: "角色锚点" },
    coverage: { gender: "exact", age: "exact", archetype: "exact" },
    comparison_required: true,
    comparison_reasons: ["role_match", "same_role_voice_identity", "anchor_audio_quality"],
    candidate_ids: candidates.map((candidate) => candidate.id),
    candidates,
  };
}

export function completeRoleReviewRubric(): RoleReviewRubric {
  return completeAnchorRubric(emptyRubric());
}

function emptyRubric(): RoleReviewRubric {
  return {
    content: null,
    prompt_leakage: null,
    reading: null,
    pitch_accent: null,
    gender: null,
    age: null,
    archetype: null,
    voice_identity: null,
    delivery: null,
    naturalness_quality: null,
    notes: "",
  };
}

function shaValue(value: number): string {
  return value.toString(16).padStart(64, "0");
}
