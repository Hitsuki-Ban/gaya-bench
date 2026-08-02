import type { PlaybackCompletion } from "@/audio/playback-manager";

import type {
  RoleReviewCatalog,
  RoleReviewDraft,
  RoleReviewGroup,
  RoleReviewGroupDraft,
  RoleReviewRubric,
  RubricResult,
} from "./types";

const SHA_PATTERN = /^[0-9a-f]{64}$/;
const ROOT_KEYS = [
  "format_version",
  "protocol",
  "phase",
  "plan_sha256",
  "candidate_set_sha256",
  "current_group_id",
  "groups",
] as const;
const GROUP_KEYS = [
  "id",
  "model",
  "scenario",
  "character",
  "line",
  "role_epoch_sha256",
  "group_sha256",
  "heard_candidate_ids",
  "selected_candidate_id",
  "no_usable_candidate",
  "rubric",
  "confirmed",
] as const;
const RUBRIC_KEYS = [
  "content",
  "prompt_leakage",
  "reading",
  "pitch_accent",
  "gender",
  "age",
  "archetype",
  "voice_identity",
  "delivery",
  "naturalness_quality",
  "notes",
] as const;
const APPLICABLE_RUBRIC_FIELDS = [
  "content",
  "prompt_leakage",
  "reading",
  "pitch_accent",
  "gender",
  "age",
  "archetype",
] as const;

export const EMPTY_ROLE_REVIEW_RUBRIC: RoleReviewRubric = {
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

export function createRoleReviewDraft(catalog: RoleReviewCatalog): RoleReviewDraft {
  const first = catalog.groups[0];
  if (!first) {
    throw new Error("听测数据没有任何项目。");
  }
  return {
    format_version: 2,
    protocol: "role-review-draft-v2",
    phase: "anchor",
    plan_sha256: catalog.planSha256,
    candidate_set_sha256: catalog.candidateSetSha256,
    current_group_id: first.id,
    groups: catalog.groups.map(createGroupDraft),
  };
}

export function parseRoleReviewDraft(value: unknown, catalog: RoleReviewCatalog): RoleReviewDraft {
  const root = exactObject(value, ROOT_KEYS, "听测草稿");
  if (
    root.format_version !== 2 ||
    root.protocol !== "role-review-draft-v2" ||
    root.phase !== "anchor"
  ) {
    throw new Error("听测草稿不是当前 role-review-draft-v2 格式。");
  }
  if (
    root.plan_sha256 !== catalog.planSha256 ||
    root.candidate_set_sha256 !== catalog.candidateSetSha256
  ) {
    throw new Error("听测草稿与当前候选集不一致；请使用新的结果目录。");
  }
  if (typeof root.current_group_id !== "string") {
    throw new Error("听测草稿 current_group_id 无效。");
  }
  if (!Array.isArray(root.groups) || root.groups.length !== catalog.groups.length) {
    throw new Error("听测草稿项目数与当前候选集不一致。");
  }
  const groups = root.groups.map((item, index) =>
    parseGroupDraft(item, catalog.groups[index]!, `听测草稿.groups[${index}]`),
  );
  const draft = {
    format_version: 2,
    protocol: "role-review-draft-v2",
    phase: "anchor",
    plan_sha256: catalog.planSha256,
    candidate_set_sha256: catalog.candidateSetSha256,
    current_group_id: root.current_group_id,
    groups,
  } as const;
  assertRoleReviewDraft(draft, catalog);
  return draft;
}

export function setCurrentRoleReviewGroup(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
  groupId: string,
): RoleReviewDraft {
  assertRoleReviewDraft(draft, catalog);
  if (!catalog.groups.some((group) => group.id === groupId)) {
    throw new Error("要打开的听测项目不存在。");
  }
  return { ...draft, current_group_id: groupId };
}

export function applyRoleReviewPlaybackCompletion(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
  completion: PlaybackCompletion,
): RoleReviewDraft {
  if (completion.termination !== "ended") {
    return draft;
  }
  for (const group of catalog.groups) {
    const candidate = group.candidates.find((item) => item.audio.key === completion.clipKey);
    if (candidate) {
      return markRoleReviewCandidateHeard(catalog, draft, group.id, candidate.id);
    }
  }
  return draft;
}

export function markRoleReviewCandidateHeard(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
  groupId: string,
  candidateId: string,
): RoleReviewDraft {
  return updateGroup(catalog, draft, groupId, (groupDraft, group) => {
    assertCandidate(group, candidateId);
    const heard = new Set(groupDraft.heard_candidate_ids);
    heard.add(candidateId);
    return {
      ...groupDraft,
      heard_candidate_ids: group.candidate_ids.filter((id) => heard.has(id)),
    };
  });
}

export function selectRoleReviewCandidate(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
  groupId: string,
  candidateId: string,
): RoleReviewDraft {
  return updateGroup(catalog, draft, groupId, (groupDraft, group) => {
    assertCandidate(group, candidateId);
    if (groupDraft.selected_candidate_id === candidateId) {
      return groupDraft;
    }
    return {
      ...groupDraft,
      selected_candidate_id: candidateId,
      no_usable_candidate: false,
      rubric: { ...EMPTY_ROLE_REVIEW_RUBRIC },
      confirmed: false,
    };
  });
}

export function markRoleReviewNoUsableCandidate(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
  groupId: string,
): RoleReviewDraft {
  return updateGroup(catalog, draft, groupId, (groupDraft) => {
    if (groupDraft.no_usable_candidate) {
      return groupDraft;
    }
    return {
      ...groupDraft,
      selected_candidate_id: null,
      no_usable_candidate: true,
      rubric: { ...EMPTY_ROLE_REVIEW_RUBRIC },
      confirmed: false,
    };
  });
}

export function updateRoleReviewRubric(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
  groupId: string,
  rubric: RoleReviewRubric,
): RoleReviewDraft {
  assertRubric(rubric, "听测问题记录", false);
  return updateGroup(catalog, draft, groupId, (groupDraft) => ({
    ...groupDraft,
    rubric,
    confirmed: false,
  }));
}

export function confirmRoleReviewGroup(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
  groupId: string,
  rubric: RoleReviewRubric,
): RoleReviewDraft {
  assertRubric(rubric, "最终判断", true);
  return updateGroup(catalog, draft, groupId, (groupDraft, group) => {
    const next = { ...groupDraft, rubric, confirmed: true };
    assertConfirmationAllowed(next, group);
    return next;
  });
}

export function completeAnchorRubric(rubric: RoleReviewRubric): RoleReviewRubric {
  const completed = { ...rubric };
  for (const field of APPLICABLE_RUBRIC_FIELDS) {
    completed[field] ??= "pass";
  }
  completed.voice_identity = "not_applicable";
  completed.delivery = "not_applicable";
  completed.naturalness_quality ??= 4;
  assertRubric(completed, "Anchor 最终判断", true);
  return completed;
}

export function isRoleReviewRubricComplete(rubric: RoleReviewRubric): boolean {
  return (
    APPLICABLE_RUBRIC_FIELDS.every((field) => isRubricResult(rubric[field])) &&
    rubric.voice_identity === "not_applicable" &&
    rubric.delivery === "not_applicable" &&
    isScore(rubric.naturalness_quality) &&
    typeof rubric.notes === "string"
  );
}

export function requiredHeardCount(group: RoleReviewGroup): number {
  return group.candidates.length;
}

export function summarizeRoleReviewDraft(draft: RoleReviewDraft): {
  readonly confirmed: number;
  readonly withProblems: number;
  readonly total: number;
} {
  const confirmed = draft.groups.filter((group) => group.confirmed).length;
  const withProblems = draft.groups.filter((group) => rubricHasProblems(group.rubric)).length;
  return { confirmed, withProblems, total: draft.groups.length };
}

export function rubricHasProblems(rubric: RoleReviewRubric): boolean {
  return (
    APPLICABLE_RUBRIC_FIELDS.some((field) => rubric[field] === "fail") ||
    (rubric.naturalness_quality !== null && rubric.naturalness_quality <= 3) ||
    rubric.notes.trim().length > 0
  );
}

export function roleReviewProblemCount(rubric: RoleReviewRubric): number {
  return (
    APPLICABLE_RUBRIC_FIELDS.filter((field) => rubric[field] === "fail").length +
    (rubric.naturalness_quality !== null && rubric.naturalness_quality <= 3 ? 1 : 0)
  );
}

export function assertRoleReviewDraft(draft: RoleReviewDraft, catalog: RoleReviewCatalog): void {
  if (
    draft.format_version !== 2 ||
    draft.protocol !== "role-review-draft-v2" ||
    draft.phase !== "anchor" ||
    draft.plan_sha256 !== catalog.planSha256 ||
    draft.candidate_set_sha256 !== catalog.candidateSetSha256
  ) {
    throw new Error("听测草稿与当前候选集不一致。");
  }
  if (draft.groups.length !== catalog.groups.length) {
    throw new Error("听测草稿项目数与当前候选集不一致。");
  }
  if (!catalog.groups.some((group) => group.id === draft.current_group_id)) {
    throw new Error("听测草稿当前项目不存在于候选集。");
  }
  for (const [index, groupDraft] of draft.groups.entries()) {
    assertGroupState(groupDraft, catalog.groups[index]!);
  }
}

function createGroupDraft(group: RoleReviewGroup): RoleReviewGroupDraft {
  return {
    id: group.id,
    model: group.model,
    scenario: group.scenario,
    character: group.character,
    line: null,
    role_epoch_sha256: group.role_epoch_sha256,
    group_sha256: group.group_sha256,
    heard_candidate_ids: [],
    selected_candidate_id: null,
    no_usable_candidate: false,
    rubric: { ...EMPTY_ROLE_REVIEW_RUBRIC },
    confirmed: false,
  };
}

function updateGroup(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
  groupId: string,
  updater: (draft: RoleReviewGroupDraft, group: RoleReviewGroup) => RoleReviewGroupDraft,
): RoleReviewDraft {
  assertRoleReviewDraft(draft, catalog);
  let found = false;
  const groups = draft.groups.map((groupDraft, index) => {
    const group = catalog.groups[index]!;
    if (group.id !== groupId) {
      return groupDraft;
    }
    found = true;
    const updated = updater(groupDraft, group);
    assertGroupState(updated, group);
    return updated;
  });
  if (!found) {
    throw new Error("要更新的听测项目不存在。");
  }
  return { ...draft, groups };
}

function parseGroupDraft(
  value: unknown,
  group: RoleReviewGroup,
  label: string,
): RoleReviewGroupDraft {
  const item = exactObject(value, GROUP_KEYS, label);
  const rubric = parseRubric(item.rubric, `${label}.rubric`);
  const result: RoleReviewGroupDraft = {
    id: requiredString(item.id, `${label}.id`),
    model: requiredString(item.model, `${label}.model`),
    scenario: requiredString(item.scenario, `${label}.scenario`),
    character: requiredString(item.character, `${label}.character`),
    line: item.line === null ? null : invalid(`${label}.line 必须为 null。`),
    role_epoch_sha256: requiredSha(item.role_epoch_sha256, `${label}.role_epoch_sha256`),
    group_sha256: requiredSha(item.group_sha256, `${label}.group_sha256`),
    heard_candidate_ids: shaArray(item.heard_candidate_ids, `${label}.heard_candidate_ids`),
    selected_candidate_id:
      item.selected_candidate_id === null
        ? null
        : requiredSha(item.selected_candidate_id, `${label}.selected_candidate_id`),
    no_usable_candidate:
      typeof item.no_usable_candidate === "boolean"
        ? item.no_usable_candidate
        : invalid(`${label}.no_usable_candidate 必须为 boolean。`),
    rubric,
    confirmed:
      typeof item.confirmed === "boolean"
        ? item.confirmed
        : invalid(`${label}.confirmed 必须为 boolean。`),
  };
  assertGroupState(result, group);
  return result;
}

function assertGroupState(draft: RoleReviewGroupDraft, group: RoleReviewGroup): void {
  if (
    draft.id !== group.id ||
    draft.model !== group.model ||
    draft.scenario !== group.scenario ||
    draft.character !== group.character ||
    draft.line !== null ||
    draft.role_epoch_sha256 !== group.role_epoch_sha256 ||
    draft.group_sha256 !== group.group_sha256
  ) {
    throw new Error("听测草稿项目身份与当前候选集不一致。");
  }
  const heard = new Set<string>();
  for (const candidateId of draft.heard_candidate_ids) {
    assertCandidate(group, candidateId);
    if (heard.has(candidateId)) {
      throw new Error("听过的候选不能重复记录。");
    }
    heard.add(candidateId);
  }
  const ordered = group.candidate_ids.filter((candidateId) => heard.has(candidateId));
  if (ordered.some((candidateId, index) => candidateId !== draft.heard_candidate_ids[index])) {
    throw new Error("听过的候选必须按页面顺序记录。");
  }
  if (draft.selected_candidate_id !== null) {
    assertCandidate(group, draft.selected_candidate_id);
  }
  if (draft.no_usable_candidate && draft.selected_candidate_id !== null) {
    throw new Error("不可用项目不能同时选择候选。");
  }
  assertRubric(draft.rubric, "听测问题记录", false);
  if (draft.confirmed) {
    assertConfirmationAllowed(draft, group);
  }
}

function assertConfirmationAllowed(draft: RoleReviewGroupDraft, group: RoleReviewGroup): void {
  if (!isRoleReviewRubricComplete(draft.rubric)) {
    throw new Error("确认前需要形成完整判断。");
  }
  if (!draft.no_usable_candidate && draft.selected_candidate_id === null) {
    throw new Error("确认前需要选择一个候选。");
  }
  if (!draft.no_usable_candidate && draft.rubric.gender !== "pass") {
    throw new Error("所选候选性别不符；请改选性别相符的候选，或标记四条都不可用。");
  }
  if (
    draft.selected_candidate_id !== null &&
    !draft.heard_candidate_ids.includes(draft.selected_candidate_id)
  ) {
    throw new Error("确认前需要完整听完所选候选。");
  }
  if (draft.heard_candidate_ids.length !== requiredHeardCount(group)) {
    throw new Error(`四选一前需要完整听完全部 ${requiredHeardCount(group)} 个候选。`);
  }
  if (draft.no_usable_candidate && !rubricHasProblems(draft.rubric)) {
    throw new Error("四条都不可用时，至少需要标记一个问题或填写说明。");
  }
}

function parseRubric(value: unknown, label: string): RoleReviewRubric {
  const rubric = exactObject(value, RUBRIC_KEYS, label);
  const result = {
    content: nullableResult(rubric.content, `${label}.content`),
    prompt_leakage: nullableResult(rubric.prompt_leakage, `${label}.prompt_leakage`),
    reading: nullableResult(rubric.reading, `${label}.reading`),
    pitch_accent: nullableResult(rubric.pitch_accent, `${label}.pitch_accent`),
    gender: nullableResult(rubric.gender, `${label}.gender`),
    age: nullableResult(rubric.age, `${label}.age`),
    archetype: nullableResult(rubric.archetype, `${label}.archetype`),
    voice_identity: nullableNotApplicable(rubric.voice_identity, `${label}.voice_identity`),
    delivery: nullableNotApplicable(rubric.delivery, `${label}.delivery`),
    naturalness_quality:
      rubric.naturalness_quality === null
        ? null
        : isScore(rubric.naturalness_quality)
          ? rubric.naturalness_quality
          : invalid(`${label}.naturalness_quality 必须为 1..5 或 null。`),
    notes:
      typeof rubric.notes === "string" ? rubric.notes : invalid(`${label}.notes 必须为字符串。`),
  } satisfies RoleReviewRubric;
  return result;
}

function assertRubric(value: RoleReviewRubric, label: string, requireComplete: boolean): void {
  parseRubric(value, label);
  if (requireComplete && !isRoleReviewRubricComplete(value)) {
    throw new Error(`${label} 还不完整。`);
  }
}

function assertCandidate(group: RoleReviewGroup, candidateId: string): void {
  if (!group.candidate_ids.includes(candidateId)) {
    throw new Error("候选不属于当前听测项目。");
  }
}

function exactObject(
  value: unknown,
  expectedKeys: readonly string[],
  label: string,
): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} 必须为 object。`);
  }
  const object = value as Record<string, unknown>;
  const actual = Object.keys(object).sort(compareText);
  const expected = [...expectedKeys].sort(compareText);
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${label} 的字段与 exact contract 不一致。`);
  }
  return object;
}

function requiredString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} 必须为非空字符串。`);
  }
  return value;
}

function requiredSha(value: unknown, label: string): string {
  if (typeof value !== "string" || !SHA_PATTERN.test(value)) {
    throw new Error(`${label} 必须为小写 SHA-256。`);
  }
  return value;
}

function shaArray(value: unknown, label: string): readonly string[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} 必须为数组。`);
  }
  return value.map((item, index) => requiredSha(item, `${label}[${index}]`));
}

function nullableResult(value: unknown, label: string): RubricResult | null {
  if (value === null || isRubricResult(value)) {
    return value;
  }
  throw new Error(`${label} 必须为 pass / fail / null。`);
}

function nullableNotApplicable(value: unknown, label: string): "not_applicable" | null {
  if (value === null || value === "not_applicable") {
    return value;
  }
  throw new Error(`${label} 只能为 not_applicable / null。`);
}

function isRubricResult(value: unknown): value is RubricResult {
  return value === "pass" || value === "fail";
}

function isScore(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 5;
}

function invalid(message: string): never {
  throw new Error(message);
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
