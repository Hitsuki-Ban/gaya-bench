import type { PlaybackCompletion } from "@/audio/playback-manager";

import {
  roleKey,
  roleReviewGroupKey,
  type RoleReopenRequest,
  type RoleReviewCatalog,
  type RoleReviewDraft,
  type RoleReviewGroup,
  type RoleReviewGroupDraft,
  type RoleReviewRubric,
  type RubricResult,
} from "./types";

export const ROLE_REVIEW_STORAGE_PREFIX = "gaya-bench:role-review:v1:group:";

const SHA_PATTERN = /^[0-9a-f]{64}$/;
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
const GROUP_DRAFT_KEYS = [
  "id",
  "phase",
  "model",
  "scenario",
  "character",
  "line",
  "role_epoch_sha256",
  "group_sha256",
  "plan_sha256",
  "role_reopen_reason",
  "candidate_group_change_reason",
  "heard_candidate_ids",
  "selected_candidate_id",
  "rubric",
  "confirmed",
] as const;

export interface RoleReviewStorage {
  readonly length: number;
  key(index: number): string | null;
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export class RoleReopenRequiredError extends Error {
  readonly model: string;
  readonly character: string;

  constructor(model: string, character: string, reason: string) {
    super(`役柄の再開が必要です: ${model}/${character}: ${reason}`);
    this.name = "RoleReopenRequiredError";
    this.model = model;
    this.character = character;
  }
}

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
  return {
    format_version: 1,
    protocol: "role-review-draft-v1",
    phase: catalog.phase,
    plan_sha256: catalog.planSha256,
    candidate_set_sha256: catalog.candidateSetSha256,
    groups: catalog.groups.map((group) => createGroupDraft(group, catalog.planSha256)),
    role_reopen_requests: [],
  };
}

export function readRoleReviewDraft(
  storage: RoleReviewStorage,
  catalog: RoleReviewCatalog,
): RoleReviewDraft {
  const currentRoles = new Map<string, RoleReviewGroup>();
  for (const group of catalog.groups) {
    currentRoles.set(roleKey(group), group);
  }

  const stored = readRelevantStoredGroups(storage, currentRoles);
  const staleRoleKeys = new Set<string>();
  for (const item of stored) {
    const current = currentRoles.get(roleKey(item.group));
    if (current && item.group.role_epoch_sha256 !== current.role_epoch_sha256) {
      staleRoleKeys.add(roleKey(current));
    }
  }

  const reopenRequests: RoleReopenRequest[] = [];
  for (const staleRoleKey of staleRoleKeys) {
    const current = currentRoles.get(staleRoleKey);
    if (!current) {
      throw new Error(`現在のroleを解決できません: ${staleRoleKey}`);
    }
    removeStoredRole(storage, current.model, current.character);
    reopenRequests.push({
      model: current.model,
      character: current.character,
      role_epoch_sha256: current.role_epoch_sha256,
      reason: "読み込んだbundleでrole epochが変化したため、当該役柄だけを再開しました。",
    });
  }

  const retained = stored.filter((item) => !staleRoleKeys.has(roleKey(item.group)));
  const currentCoordinates = new Set(catalog.groups.map((group) => storedGroupCoordinate(group)));
  for (const item of retained) {
    if (
      item.group.phase === catalog.phase &&
      !currentCoordinates.has(storedGroupCoordinate(item.group))
    ) {
      storage.removeItem(item.key);
    }
  }

  const groups = catalog.groups.map((group) => {
    const autoReopen = reopenRequests.find(
      (request) => request.model === group.model && request.character === group.character,
    );
    if (autoReopen) {
      const reopened = {
        ...createGroupDraft(group, catalog.planSha256),
        role_reopen_reason: autoReopen.reason,
      };
      storage.setItem(roleReviewStorageKey(reopened), JSON.stringify(reopened));
      return reopened;
    }

    const coordinateRecords = retained.filter(
      (item) => storedGroupCoordinate(item.group) === storedGroupCoordinate(group),
    );
    const exact = coordinateRecords.find((item) => item.group.group_sha256 === group.group_sha256);
    if (exact) {
      for (const stale of coordinateRecords) {
        if (stale.key !== exact.key) {
          storage.removeItem(stale.key);
        }
      }
      assertGroupBinding(exact.group, group, catalog);
      assertGroupState(exact.group, group);
      return exact.group;
    }

    if (coordinateRecords.length > 0) {
      for (const stale of coordinateRecords) {
        storage.removeItem(stale.key);
      }
      const changed = {
        ...createGroupDraft(group, catalog.planSha256),
        candidate_group_change_reason:
          "candidate groupが変化したため、このgroupだけを再評価してください。",
      };
      storage.setItem(roleReviewStorageKey(changed), JSON.stringify(changed));
      return changed;
    }
    return createGroupDraft(group, catalog.planSha256);
  });
  const requestsByRole = new Map(
    reopenRequests.map((request) => [JSON.stringify([request.model, request.character]), request]),
  );
  for (const group of groups) {
    if (group.role_reopen_reason === null) {
      continue;
    }
    requestsByRole.set(JSON.stringify([group.model, group.character]), {
      model: group.model,
      character: group.character,
      role_epoch_sha256: group.role_epoch_sha256,
      reason: group.role_reopen_reason,
    });
  }
  return {
    format_version: 1,
    protocol: "role-review-draft-v1",
    phase: catalog.phase,
    plan_sha256: catalog.planSha256,
    candidate_set_sha256: catalog.candidateSetSha256,
    groups,
    role_reopen_requests: [...requestsByRole.values()],
  };
}

export function writeRoleReviewDraft(
  storage: RoleReviewStorage,
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
): void {
  assertDraftBinding(draft, catalog);
  for (const group of draft.groups) {
    storage.setItem(roleReviewStorageKey(group), JSON.stringify(group));
  }
}

export function resetRoleReviewDraft(
  storage: RoleReviewStorage,
  catalog: RoleReviewCatalog,
): RoleReviewDraft {
  for (const group of catalog.groups) {
    storage.removeItem(roleReviewStorageKey(group));
  }
  return createRoleReviewDraft(catalog);
}

export function reopenRole(
  storage: RoleReviewStorage,
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
  model: string,
  character: string,
  reason: string,
): RoleReviewDraft {
  if (
    draft.format_version !== 1 ||
    draft.protocol !== "role-review-draft-v1" ||
    draft.phase !== catalog.phase ||
    draft.plan_sha256 !== catalog.planSha256 ||
    draft.candidate_set_sha256 !== catalog.candidateSetSha256 ||
    draft.groups.length !== catalog.groups.length
  ) {
    throw new Error("role reopen前にdraft rootと現在のbundleが一致している必要があります。");
  }
  if (reason.trim().length === 0 || reason !== reason.trim()) {
    throw new Error("role reopen reason は前後空白のない非空文字列が必要です。");
  }
  const roleGroups = catalog.groups.filter(
    (group) => group.model === model && group.character === character,
  );
  if (roleGroups.length === 0) {
    throw new Error(`reopen対象のroleが現在のbundleにありません: ${model}/${character}`);
  }
  for (const [index, currentDraft] of draft.groups.entries()) {
    const currentGroup = catalog.groups[index]!;
    if (currentGroup.model === model && currentGroup.character === character) {
      continue;
    }
    assertGroupBinding(currentDraft, currentGroup, catalog);
    assertGroupState(currentDraft, currentGroup);
  }
  removeStoredRole(storage, model, character);
  const resetByKey = new Map(
    roleGroups.map((group) => [
      roleReviewGroupKey(group),
      {
        ...createGroupDraft(group, catalog.planSha256),
        role_reopen_reason: reason,
        candidate_group_change_reason: null,
      },
    ]),
  );
  const roleEpoch = roleGroups[0]!.role_epoch_sha256;
  const request: RoleReopenRequest = {
    model,
    character,
    role_epoch_sha256: roleEpoch,
    reason,
  };
  const requests = draft.role_reopen_requests.filter(
    (item) => item.model !== model || item.character !== character,
  );
  requests.push(request);
  return {
    ...draft,
    groups: draft.groups.map((group) => resetByKey.get(roleReviewGroupKey(group)) ?? group),
    role_reopen_requests: requests,
  };
}

export function recoverRoleReviewDraft(
  storage: RoleReviewStorage,
  catalog: RoleReviewCatalog,
  model: string,
  character: string,
  reason: string,
): RoleReviewDraft {
  removeStoredRole(storage, model, character);
  const restored = readRoleReviewDraft(storage, catalog);
  return reopenRole(storage, catalog, restored, model, character, reason);
}

export function markRoleReviewCandidateHeard(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
  groupId: string,
  candidateId: string,
): RoleReviewDraft {
  return updateGroup(catalog, draft, groupId, (groupDraft, group) => {
    assertCandidate(group, candidateId);
    if (groupDraft.heard_candidate_ids.includes(candidateId)) {
      return groupDraft;
    }
    return {
      ...groupDraft,
      heard_candidate_ids: group.candidate_ids.filter(
        (id) => id === candidateId || groupDraft.heard_candidate_ids.includes(id),
      ),
    };
  });
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

export function selectRoleReviewCandidate(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
  groupId: string,
  candidateId: string,
): RoleReviewDraft {
  return updateGroup(catalog, draft, groupId, (groupDraft, group) => {
    assertCandidate(group, candidateId);
    return {
      ...groupDraft,
      selected_candidate_id: candidateId,
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
  assertRubric(rubric, "rubric", false);
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
): RoleReviewDraft {
  return updateGroup(catalog, draft, groupId, (groupDraft, group) => {
    assertConfirmationAllowed(groupDraft, group);
    return {
      ...groupDraft,
      candidate_group_change_reason: null,
      confirmed: true,
    };
  });
}

export function clearRoleReviewConfirmation(
  catalog: RoleReviewCatalog,
  draft: RoleReviewDraft,
  groupId: string,
): RoleReviewDraft {
  return updateGroup(catalog, draft, groupId, (groupDraft) => ({
    ...groupDraft,
    confirmed: false,
  }));
}

export function isRoleReviewRubricComplete(rubric: RoleReviewRubric): boolean {
  return (
    isRubricResult(rubric.content) &&
    isRubricResult(rubric.prompt_leakage) &&
    isRubricResult(rubric.reading) &&
    isRubricResult(rubric.pitch_accent) &&
    isRubricResult(rubric.gender) &&
    isRubricResult(rubric.age) &&
    isRubricResult(rubric.archetype) &&
    isRubricResult(rubric.voice_identity) &&
    isRubricResult(rubric.delivery) &&
    isScore(rubric.naturalness_quality) &&
    typeof rubric.notes === "string"
  );
}

export function requiredHeardCount(group: RoleReviewGroup, draft: RoleReviewGroupDraft): 1 | 2 {
  return group.comparison_required || draft.selected_candidate_id !== group.provisional_candidate_id
    ? 2
    : 1;
}

export function summarizeRoleReviewDraft(draft: RoleReviewDraft): {
  readonly confirmed: number;
  readonly remaining: number;
  readonly total: number;
} {
  const confirmed = draft.groups.filter((group) => group.confirmed).length;
  return {
    confirmed,
    remaining: draft.groups.length - confirmed,
    total: draft.groups.length,
  };
}

export function roleReviewStorageKey(
  value: Pick<
    RoleReviewGroup | RoleReviewGroupDraft,
    "phase" | "model" | "scenario" | "character" | "role_epoch_sha256" | "group_sha256"
  >,
): string {
  return `${ROLE_REVIEW_STORAGE_PREFIX}${[
    value.phase,
    value.model,
    value.scenario,
    value.character,
    value.role_epoch_sha256,
    value.group_sha256,
  ].join(":")}`;
}

export function assertRoleReviewDraft(draft: RoleReviewDraft, catalog: RoleReviewCatalog): void {
  assertDraftBinding(draft, catalog);
}

function createGroupDraft(group: RoleReviewGroup, planSha256: string): RoleReviewGroupDraft {
  return {
    id: group.id,
    phase: group.line === null ? "anchor" : "line",
    model: group.model,
    scenario: group.scenario,
    character: group.character,
    line: group.line?.id ?? null,
    role_epoch_sha256: group.role_epoch_sha256,
    group_sha256: group.group_sha256,
    plan_sha256: planSha256,
    role_reopen_reason: null,
    candidate_group_change_reason: null,
    heard_candidate_ids: [],
    selected_candidate_id: group.provisional_candidate_id,
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
  assertDraftBinding(draft, catalog);
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
    throw new Error(`role review group がありません: ${groupId}`);
  }
  return { ...draft, groups };
}

function assertDraftBinding(draft: RoleReviewDraft, catalog: RoleReviewCatalog): void {
  if (
    draft.format_version !== 1 ||
    draft.protocol !== "role-review-draft-v1" ||
    draft.phase !== catalog.phase
  ) {
    throw new Error("role review draft root が現在のbundleと一致しません。");
  }
  if (
    draft.plan_sha256 !== catalog.planSha256 ||
    draft.candidate_set_sha256 !== catalog.candidateSetSha256
  ) {
    throw new Error("role review draftのplanまたはcandidate setが現在のbundleと一致しません。");
  }
  if (draft.groups.length !== catalog.groups.length) {
    throw new Error("role review draft のgroup数が現在のbundleと一致しません。");
  }
  for (const [index, groupDraft] of draft.groups.entries()) {
    const group = catalog.groups[index];
    if (!group) {
      throw new Error("role review draft のgroup順が現在のbundleと一致しません。");
    }
    assertGroupBinding(groupDraft, group, catalog);
    assertGroupState(groupDraft, group);
  }
  const reopenRoles = new Set<string>();
  for (const [index, request] of draft.role_reopen_requests.entries()) {
    if (
      typeof request !== "object" ||
      request === null ||
      typeof request.model !== "string" ||
      typeof request.character !== "string" ||
      typeof request.role_epoch_sha256 !== "string" ||
      !SHA_PATTERN.test(request.role_epoch_sha256) ||
      typeof request.reason !== "string" ||
      request.reason.trim().length === 0 ||
      request.reason !== request.reason.trim()
    ) {
      throw new Error(`role_reopen_requests[${index}] が不正です。`);
    }
    const key = JSON.stringify([request.model, request.character]);
    if (reopenRoles.has(key)) {
      throw new Error(`role reopen request が重複しています: ${key}`);
    }
    reopenRoles.add(key);
    const current = catalog.groups.find(
      (group) => group.model === request.model && group.character === request.character,
    );
    if (!current || current.role_epoch_sha256 !== request.role_epoch_sha256) {
      throw new Error(`role reopen request が現在のrole epochと一致しません: ${key}`);
    }
    const roleDrafts = draft.groups.filter(
      (group) => group.model === request.model && group.character === request.character,
    );
    if (
      roleDrafts.length === 0 ||
      roleDrafts.some((group) => group.role_reopen_reason !== request.reason)
    ) {
      throw new Error(`role reopen request とgroup記録が一致しません: ${key}`);
    }
  }
  for (const group of draft.groups) {
    if (
      group.role_reopen_reason !== null &&
      !reopenRoles.has(JSON.stringify([group.model, group.character]))
    ) {
      throw new Error(
        `group reopen reason に対応するrole requestがありません: ${group.model}/${group.character}`,
      );
    }
  }
}

function assertGroupBinding(
  draft: RoleReviewGroupDraft,
  group: RoleReviewGroup,
  catalog: RoleReviewCatalog,
): void {
  const identityMatches =
    draft.id === group.id &&
    draft.phase === catalog.phase &&
    draft.model === group.model &&
    draft.scenario === group.scenario &&
    draft.character === group.character &&
    draft.line === (group.line?.id ?? null) &&
    draft.role_epoch_sha256 === group.role_epoch_sha256 &&
    draft.group_sha256 === group.group_sha256;
  if (!identityMatches) {
    throw new RoleReopenRequiredError(
      group.model,
      group.character,
      "group identity、role epoch、またはgroup hashが変化しています。",
    );
  }
  if (draft.plan_sha256 !== catalog.planSha256) {
    throw new Error("保存済みgroupのplan SHA-256が現在のbundleと一致しません。");
  }
}

function assertGroupState(draft: RoleReviewGroupDraft, group: RoleReviewGroup): void {
  nullableReason(draft.role_reopen_reason, "role_reopen_reason");
  nullableReason(draft.candidate_group_change_reason, "candidate_group_change_reason");
  if (draft.role_reopen_reason !== null && draft.candidate_group_change_reason !== null) {
    throw new Error("role reopenとcandidate group changeは同時に記録できません。");
  }
  const heard = new Set<string>();
  for (const candidateId of draft.heard_candidate_ids) {
    assertCandidate(group, candidateId);
    if (heard.has(candidateId)) {
      throw new Error(`heard candidate が重複しています: ${candidateId}`);
    }
    heard.add(candidateId);
  }
  const ordered = group.candidate_ids.filter((candidateId) => heard.has(candidateId));
  if (
    ordered.length !== draft.heard_candidate_ids.length ||
    ordered.some((candidateId, index) => candidateId !== draft.heard_candidate_ids[index])
  ) {
    throw new Error("heard_candidate_ids はbundleのcandidate順が必要です。");
  }
  assertCandidate(group, draft.selected_candidate_id);
  assertRubric(draft.rubric, "role review rubric", false);
  if (typeof draft.confirmed !== "boolean") {
    throw new Error("role review group.confirmed は boolean が必要です。");
  }
  if (draft.confirmed) {
    assertConfirmationAllowed(draft, group);
  }
}

function assertConfirmationAllowed(draft: RoleReviewGroupDraft, group: RoleReviewGroup): void {
  if (!isRoleReviewRubricComplete(draft.rubric)) {
    throw new Error("確認には全判断基準の明示入力が必要です。");
  }
  if (!draft.heard_candidate_ids.includes(draft.selected_candidate_id)) {
    throw new Error("選択候補を聴いてから確認してください。");
  }
  const required = requiredHeardCount(group, draft);
  if (draft.heard_candidate_ids.length < required) {
    throw new Error(
      `この判断には異なる候補を${required}件以上聴く必要があります: heard=${draft.heard_candidate_ids.length}`,
    );
  }
}

function assertCandidate(group: RoleReviewGroup, candidateId: string): void {
  if (!group.candidate_ids.includes(candidateId)) {
    throw new Error(`candidate がgroupにありません: ${candidateId}`);
  }
}

function listRoleReviewRecords(storage: RoleReviewStorage): readonly {
  readonly key: string;
  readonly identity: ReturnType<typeof parseStorageKey>;
}[] {
  const result: Array<{ key: string; identity: ReturnType<typeof parseStorageKey> }> = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key?.startsWith(ROLE_REVIEW_STORAGE_PREFIX)) {
      result.push({ key, identity: parseStorageKey(key) });
    }
  }
  return result;
}

function readRelevantStoredGroups(
  storage: RoleReviewStorage,
  currentRoles: ReadonlyMap<string, RoleReviewGroup>,
): readonly {
  readonly key: string;
  readonly identity: ReturnType<typeof parseStorageKey>;
  readonly group: RoleReviewGroupDraft;
}[] {
  const result: Array<{
    key: string;
    identity: ReturnType<typeof parseStorageKey>;
    group: RoleReviewGroupDraft;
  }> = [];
  for (const item of listRoleReviewRecords(storage)) {
    if (!currentRoles.has(JSON.stringify([item.identity.model, item.identity.character]))) {
      continue;
    }
    const raw = storage.getItem(item.key);
    if (raw === null) {
      throw new Error(`列挙中にrole review storage recordが消失しました: ${item.key}`);
    }
    const group = validateStoredGroup(parseStoredJson(raw, item.key), "保存済みrole review group");
    if (roleReviewStorageKey(group) !== item.key) {
      throw new Error(`role review storage keyとrecord identityが一致しません: ${item.key}`);
    }
    result.push({ ...item, group });
  }
  return result;
}

function storedGroupCoordinate(
  value: Pick<
    RoleReviewGroup | RoleReviewGroupDraft,
    "phase" | "model" | "scenario" | "character" | "line"
  >,
): string {
  return JSON.stringify([
    value.phase,
    value.model,
    value.scenario,
    value.character,
    value.line === null || typeof value.line === "string" ? value.line : value.line.id,
  ]);
}

function parseStorageKey(key: string): {
  readonly phase: "anchor" | "line";
  readonly model: string;
  readonly scenario: string;
  readonly character: string;
  readonly roleEpochSha256: string;
  readonly groupSha256: string;
} {
  const parts = key.slice(ROLE_REVIEW_STORAGE_PREFIX.length).split(":");
  if (parts.length !== 6) {
    throw new Error(`role review storage key が不正です: ${key}`);
  }
  const [phase, model, scenario, character, roleEpochSha256, groupSha256] = parts;
  if (phase !== "anchor" && phase !== "line") {
    throw new Error(`role review storage phase が不正です: ${key}`);
  }
  if (!model || !scenario || !character) {
    throw new Error(`role review storage identity が不正です: ${key}`);
  }
  if (!SHA_PATTERN.test(roleEpochSha256!) || !SHA_PATTERN.test(groupSha256!)) {
    throw new Error(`role review storage hash が不正です: ${key}`);
  }
  return {
    phase,
    model,
    scenario,
    character,
    roleEpochSha256: roleEpochSha256!,
    groupSha256: groupSha256!,
  };
}

function removeStoredRole(storage: RoleReviewStorage, model: string, character: string): void {
  const keys = listRoleReviewRecords(storage)
    .filter(({ identity }) => identity.model === model && identity.character === character)
    .map(({ key }) => key);
  for (const key of keys) {
    storage.removeItem(key);
  }
}

function parseStoredJson(raw: string, key: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error(`role review storage JSON を解析できません。明示的に再開してください: ${key}`);
  }
}

function validateStoredGroup(value: unknown, label: string): RoleReviewGroupDraft {
  const object = exactObject(value, GROUP_DRAFT_KEYS, label);
  const phase = object.phase;
  if (phase !== "anchor" && phase !== "line") {
    throw new Error(`${label}.phase は anchor または line が必要です。`);
  }
  for (const key of ["model", "scenario", "character"] as const) {
    if (typeof object[key] !== "string" || object[key].length === 0) {
      throw new Error(`${label}.${key} は非空文字列が必要です。`);
    }
  }
  if (typeof object.id !== "string" || !SHA_PATTERN.test(object.id)) {
    throw new Error(`${label}.id は小文字 SHA-256 が必要です。`);
  }
  if (object.line !== null && (typeof object.line !== "string" || object.line.length === 0)) {
    throw new Error(`${label}.line は非空文字列または null が必要です。`);
  }
  for (const key of [
    "role_epoch_sha256",
    "group_sha256",
    "plan_sha256",
    "selected_candidate_id",
  ] as const) {
    if (typeof object[key] !== "string" || !SHA_PATTERN.test(object[key])) {
      throw new Error(`${label}.${key} は小文字 SHA-256 が必要です。`);
    }
  }
  const roleReopenReason = nullableReason(object.role_reopen_reason, `${label}.role_reopen_reason`);
  const candidateGroupChangeReason = nullableReason(
    object.candidate_group_change_reason,
    `${label}.candidate_group_change_reason`,
  );
  const heard = shaArray(object.heard_candidate_ids, `${label}.heard_candidate_ids`);
  assertRubric(object.rubric, `${label}.rubric`, false);
  if (typeof object.confirmed !== "boolean") {
    throw new Error(`${label}.confirmed は boolean が必要です。`);
  }
  return {
    id: object.id,
    phase,
    model: object.model,
    scenario: object.scenario,
    character: object.character,
    line: object.line,
    role_epoch_sha256: object.role_epoch_sha256,
    group_sha256: object.group_sha256,
    plan_sha256: object.plan_sha256,
    role_reopen_reason: roleReopenReason,
    candidate_group_change_reason: candidateGroupChangeReason,
    heard_candidate_ids: heard,
    selected_candidate_id: object.selected_candidate_id,
    rubric: object.rubric,
    confirmed: object.confirmed,
  } as RoleReviewGroupDraft;
}

function assertRubric(
  value: unknown,
  label: string,
  requireComplete: boolean,
): asserts value is RoleReviewRubric {
  const rubric = exactObject(value, RUBRIC_KEYS, label);
  for (const key of [
    "content",
    "prompt_leakage",
    "reading",
    "pitch_accent",
    "gender",
    "age",
    "archetype",
    "voice_identity",
    "delivery",
  ] as const) {
    if (rubric[key] !== null && !isRubricResult(rubric[key])) {
      throw new Error(`${label}.${key} は pass / fail / not_applicable / null が必要です。`);
    }
  }
  if (rubric.naturalness_quality !== null && !isScore(rubric.naturalness_quality)) {
    throw new Error(`${label}.naturalness_quality は1..5の整数またはnullが必要です。`);
  }
  if (typeof rubric.notes !== "string") {
    throw new Error(`${label}.notes は文字列が必要です。`);
  }
  if (requireComplete && !isRoleReviewRubricComplete(rubric as unknown as RoleReviewRubric)) {
    throw new Error(`${label} は全判断基準の明示入力が必要です。`);
  }
}

function exactObject(
  value: unknown,
  expectedKeys: readonly string[],
  label: string,
): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} は object が必要です。`);
  }
  const object = value as Record<string, unknown>;
  const actual = Object.keys(object).sort(compareText);
  const expected = [...expectedKeys].sort(compareText);
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${label} のkeyがexact contractと一致しません: ${actual.join(",")}`);
  }
  return object;
}

function shaArray(value: unknown, label: string): readonly string[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} は配列が必要です。`);
  }
  return value.map((item, index) => {
    if (typeof item !== "string" || !SHA_PATTERN.test(item)) {
      throw new Error(`${label}[${index}] は小文字 SHA-256 が必要です。`);
    }
    return item;
  });
}

function nullableReason(value: unknown, label: string): string | null {
  if (value === null) {
    return null;
  }
  if (typeof value !== "string" || value.trim().length === 0 || value !== value.trim()) {
    throw new Error(`${label} は前後空白のない非空文字列またはnullが必要です。`);
  }
  return value;
}

function isRubricResult(value: unknown): value is RubricResult {
  return value === "pass" || value === "fail" || value === "not_applicable";
}

function isScore(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 5;
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
