import {
  baselineGroupKey,
  type BaselineCatalog,
  type BaselineDraft,
  type BaselineGroup,
  type BaselineGroupDraft,
  type BaselineRubric,
} from "./baseline-types";

export const BASELINE_STORAGE_PREFIX = "gaya-bench:role-baseline:v1:group:";
export const LEGACY_BASELINE_STORAGE_KEY = "gaya-bench:baseline-completion:v1";

const GROUP_KEYS = [
  "model",
  "scenario",
  "line",
  "variant",
  "role_epoch_sha256",
  "group_sha256",
  "plan_sha256",
  "anchor_selection_sha256",
  "candidate_set_sha256",
  "revalidation_reason",
  "candidates",
  "decision",
] as const;
const RUBRIC_KEYS = [
  "content_correct",
  "prompt_leakage",
  "reading_correct",
  "accent_naturalness",
  "role_match",
  "delivery_match",
  "audio_quality",
  "adoptable",
  "notes",
] as const;
const SHA_PATTERN = /^[0-9a-f]{64}$/;

export interface BaselineStorage {
  readonly length: number;
  key(index: number): string | null;
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export const EMPTY_BASELINE_RUBRIC: BaselineRubric = {
  content_correct: null,
  prompt_leakage: null,
  reading_correct: null,
  accent_naturalness: null,
  role_match: null,
  delivery_match: null,
  audio_quality: null,
  adoptable: null,
  notes: "",
};

export function createBaselineDraft(catalog: BaselineCatalog): BaselineDraft {
  return {
    format_version: 1,
    protocol: "role-baseline-draft-v1",
    plan_sha256: catalog.planSha256,
    anchor_selection_sha256: catalog.anchorSelectionSha256,
    candidate_set_sha256: catalog.candidateSetSha256,
    groups: catalog.groups.map((group) => createGroupDraft(catalog, group, null)),
  };
}

export function readBaselineDraft(
  storage: BaselineStorage,
  catalog: BaselineCatalog,
): BaselineDraft {
  if (storage.getItem(LEGACY_BASELINE_STORAGE_KEY) !== null) {
    throw new Error("旧baseline-completion draftを拒否しました。明示的にリセットしてください。");
  }
  const currentCoordinates = new Set(catalog.groups.map(baselineGroupKey));
  const records = listRecords(storage).map(({ key, raw }) => {
    const group = validateStoredGroup(parseJson(raw, key), "保存済みPhase B group");
    if (key !== baselineStorageKey(group)) {
      throw new Error(`Phase B storage keyがrecord bindingと一致しません: ${key}`);
    }
    return { key, group };
  });
  for (const record of records) {
    if (!currentCoordinates.has(baselineGroupKey(record.group))) {
      storage.removeItem(record.key);
    }
  }

  const groups = catalog.groups.map((group) => {
    const coordinateRecords = records.filter(
      (record) => baselineGroupKey(record.group) === baselineGroupKey(group),
    );
    const exact = coordinateRecords.find((record) =>
      groupBindingMatches(record.group, catalog, group, true),
    );
    if (exact) {
      removeOtherRecords(storage, coordinateRecords, exact.key);
      assertGroupState(exact.group, group);
      return exact.group;
    }
    const migratable = coordinateRecords.filter((record) =>
      groupBindingMatches(record.group, catalog, group, false),
    );
    if (migratable.length > 1) {
      throw new Error(`candidate-set再束縛元が曖昧です: ${baselineGroupKey(group)}`);
    }
    if (migratable.length === 1) {
      removeOtherRecords(storage, coordinateRecords, null);
      const rebound = {
        ...migratable[0]!.group,
        candidate_set_sha256: catalog.candidateSetSha256,
      };
      assertGroupState(rebound, group);
      storage.setItem(baselineStorageKey(rebound), JSON.stringify(rebound));
      return rebound;
    }
    removeOtherRecords(storage, coordinateRecords, null);
    const fresh = createGroupDraft(
      catalog,
      group,
      coordinateRecords.length > 0
        ? "line candidate groupまたはrole epochが変化したため、この行を再評価してください。"
        : null,
    );
    if (coordinateRecords.length > 0) {
      storage.setItem(baselineStorageKey(fresh), JSON.stringify(fresh));
    }
    return fresh;
  });
  return {
    format_version: 1,
    protocol: "role-baseline-draft-v1",
    plan_sha256: catalog.planSha256,
    anchor_selection_sha256: catalog.anchorSelectionSha256,
    candidate_set_sha256: catalog.candidateSetSha256,
    groups,
  };
}

export function writeBaselineDraft(
  storage: BaselineStorage,
  catalog: BaselineCatalog,
  draft: BaselineDraft,
): void {
  assertBaselineDraft(draft, catalog);
  for (const group of draft.groups) {
    storage.setItem(baselineStorageKey(group), JSON.stringify(group));
  }
}

export function resetBaselineDraft(
  storage: BaselineStorage,
  catalog: BaselineCatalog,
): BaselineDraft {
  const coordinates = new Set(catalog.groups.map(baselineGroupKey));
  for (const { key, raw } of listRecords(storage)) {
    const group = validateStoredGroup(parseJson(raw, key), "保存済みPhase B group");
    if (coordinates.has(baselineGroupKey(group))) {
      storage.removeItem(key);
    }
  }
  storage.removeItem(LEGACY_BASELINE_STORAGE_KEY);
  return createBaselineDraft(catalog);
}

export function updateBaselineRubric(
  catalog: BaselineCatalog,
  draft: BaselineDraft,
  groupKey: string,
  takeId: string,
  rubric: BaselineRubric,
): BaselineDraft {
  assertRubric(rubric, "Phase B rubric", false);
  return updateGroup(catalog, draft, groupKey, (groupDraft, group) => {
    if (!group.candidates.some((candidate) => candidate.takeId === takeId)) {
      throw new Error(`rubric対象candidateがありません: ${takeId}`);
    }
    return {
      ...groupDraft,
      candidates: groupDraft.candidates.map((candidate) =>
        candidate.take_id === takeId ? { ...candidate, rubric } : candidate,
      ),
      decision: null,
    };
  });
}

export function selectBaselineCandidate(
  catalog: BaselineCatalog,
  draft: BaselineDraft,
  groupKey: string,
  takeId: string,
): BaselineDraft {
  return updateGroup(catalog, draft, groupKey, (groupDraft) => {
    assertDecisionAllowed(groupDraft, takeId);
    return {
      ...groupDraft,
      revalidation_reason: null,
      decision: { type: "selected", take_id: takeId },
    };
  });
}

export function clearBaselineDecision(
  catalog: BaselineCatalog,
  draft: BaselineDraft,
  groupKey: string,
): BaselineDraft {
  return updateGroup(catalog, draft, groupKey, (groupDraft) => ({
    ...groupDraft,
    decision: null,
  }));
}

export function isBaselineRubricComplete(rubric: BaselineRubric): rubric is BaselineRubric & {
  readonly content_correct: boolean;
  readonly prompt_leakage: boolean;
  readonly reading_correct: boolean;
  readonly accent_naturalness: number;
  readonly role_match: number;
  readonly delivery_match: number;
  readonly audio_quality: number;
  readonly adoptable: boolean;
} {
  return (
    typeof rubric.content_correct === "boolean" &&
    typeof rubric.prompt_leakage === "boolean" &&
    typeof rubric.reading_correct === "boolean" &&
    isScore(rubric.accent_naturalness) &&
    isScore(rubric.role_match) &&
    isScore(rubric.delivery_match) &&
    isScore(rubric.audio_quality) &&
    typeof rubric.adoptable === "boolean" &&
    typeof rubric.notes === "string"
  );
}

export function summarizeBaselineDraft(draft: BaselineDraft): {
  readonly selected: number;
  readonly remaining: number;
  readonly total: number;
} {
  const selected = draft.groups.filter((group) => group.decision !== null).length;
  return { selected, remaining: draft.groups.length - selected, total: draft.groups.length };
}

export function assertBaselineDraft(draft: BaselineDraft, catalog: BaselineCatalog): void {
  if (
    draft.format_version !== 1 ||
    draft.protocol !== "role-baseline-draft-v1" ||
    draft.plan_sha256 !== catalog.planSha256 ||
    draft.anchor_selection_sha256 !== catalog.anchorSelectionSha256 ||
    draft.candidate_set_sha256 !== catalog.candidateSetSha256 ||
    draft.groups.length !== catalog.groups.length
  ) {
    throw new Error("Phase B draft rootが現在のplan/anchor/candidate-setと一致しません。");
  }
  for (const [index, groupDraft] of draft.groups.entries()) {
    const group = catalog.groups[index];
    if (!group || !groupBindingMatches(groupDraft, catalog, group, true)) {
      throw new Error(`Phase B draft group bindingが一致しません: ${index}`);
    }
    assertGroupState(groupDraft, group);
  }
}

export function baselineStorageKey(group: BaselineGroupDraft): string {
  return `${BASELINE_STORAGE_PREFIX}${[
    group.plan_sha256,
    group.anchor_selection_sha256,
    group.candidate_set_sha256,
    group.model,
    group.scenario,
    group.line,
    group.variant,
    group.role_epoch_sha256,
    group.group_sha256,
  ]
    .map(encodeURIComponent)
    .join(":")}`;
}

function createGroupDraft(
  catalog: BaselineCatalog,
  group: BaselineGroup,
  reason: string | null,
): BaselineGroupDraft {
  return {
    model: group.model,
    scenario: group.scenario,
    line: group.line,
    variant: group.variant,
    role_epoch_sha256: group.roleEpochSha256,
    group_sha256: group.groupSha256,
    plan_sha256: catalog.planSha256,
    anchor_selection_sha256: catalog.anchorSelectionSha256,
    candidate_set_sha256: catalog.candidateSetSha256,
    revalidation_reason: reason,
    candidates: group.exportCandidates.map((candidate) => ({
      take_id: candidate.takeId,
      rubric: { ...EMPTY_BASELINE_RUBRIC },
    })),
    decision: null,
  };
}

function updateGroup(
  catalog: BaselineCatalog,
  draft: BaselineDraft,
  groupKey: string,
  update: (groupDraft: BaselineGroupDraft, group: BaselineGroup) => BaselineGroupDraft,
): BaselineDraft {
  assertBaselineDraft(draft, catalog);
  let found = false;
  const groups = draft.groups.map((groupDraft, index) => {
    if (baselineGroupKey(groupDraft) !== groupKey) {
      return groupDraft;
    }
    found = true;
    const next = update(groupDraft, catalog.groups[index]!);
    assertGroupState(next, catalog.groups[index]!);
    return next;
  });
  if (!found) {
    throw new Error(`Phase B groupがありません: ${groupKey}`);
  }
  return { ...draft, groups };
}

function groupBindingMatches(
  draft: BaselineGroupDraft,
  catalog: BaselineCatalog,
  group: BaselineGroup,
  includeCandidateSet: boolean,
): boolean {
  return (
    baselineGroupKey(draft) === baselineGroupKey(group) &&
    draft.plan_sha256 === catalog.planSha256 &&
    draft.anchor_selection_sha256 === catalog.anchorSelectionSha256 &&
    (!includeCandidateSet || draft.candidate_set_sha256 === catalog.candidateSetSha256) &&
    draft.role_epoch_sha256 === group.roleEpochSha256 &&
    draft.group_sha256 === group.groupSha256
  );
}

function assertGroupState(draft: BaselineGroupDraft, group: BaselineGroup): void {
  nullableReason(draft.revalidation_reason, "revalidation_reason");
  const expected = group.exportCandidates.map((candidate) => candidate.takeId);
  const actual = draft.candidates.map((candidate) => candidate.take_id);
  if (
    actual.length !== expected.length ||
    actual.some((takeId, index) => takeId !== expected[index])
  ) {
    throw new Error(`Phase B draft candidate集合が一致しません: ${baselineGroupKey(group)}`);
  }
  for (const candidate of draft.candidates) {
    assertRubric(candidate.rubric, `candidate ${candidate.take_id}.rubric`, false);
  }
  if (draft.decision !== null) {
    assertDecisionAllowed(draft, draft.decision.take_id);
  }
}

function assertDecisionAllowed(group: BaselineGroupDraft, takeId: string): void {
  if (group.candidates.some((candidate) => !isBaselineRubricComplete(candidate.rubric))) {
    throw new Error("選択前にgroup内の全candidate rubricを入力してください。");
  }
  if (!group.candidates.some((candidate) => candidate.take_id === takeId)) {
    throw new Error(`selected candidateがgroupにありません: ${takeId}`);
  }
}

function validateStoredGroup(value: unknown, label: string): BaselineGroupDraft {
  const group = exactObject(value, GROUP_KEYS, label);
  for (const key of ["model", "scenario", "line", "variant"] as const) {
    pathSegment(group[key], `${label}.${key}`);
  }
  for (const key of [
    "role_epoch_sha256",
    "group_sha256",
    "plan_sha256",
    "anchor_selection_sha256",
    "candidate_set_sha256",
  ] as const) {
    sha(group[key], `${label}.${key}`);
  }
  const reason = nullableReason(group.revalidation_reason, `${label}.revalidation_reason`);
  if (!Array.isArray(group.candidates) || group.candidates.length === 0) {
    throw new Error(`${label}.candidates は1件以上の配列が必要です。`);
  }
  const candidates = group.candidates.map((value, index) => {
    const candidate = exactObject(value, ["take_id", "rubric"], `${label}.candidates[${index}]`);
    const takeId = sha(candidate.take_id, `${label}.candidates[${index}].take_id`);
    assertRubric(candidate.rubric, `${label}.candidates[${index}].rubric`, false);
    return { take_id: takeId, rubric: candidate.rubric };
  });
  if (new Set(candidates.map((candidate) => candidate.take_id)).size !== candidates.length) {
    throw new Error(`${label}.candidates のtake_idが重複しています。`);
  }
  let decision: BaselineGroupDraft["decision"] = null;
  if (group.decision !== null) {
    const selected = exactObject(group.decision, ["type", "take_id"], `${label}.decision`);
    if (selected.type !== "selected") {
      throw new Error(`${label}.decision.type は selected が必要です。`);
    }
    decision = {
      type: "selected",
      take_id: sha(selected.take_id, `${label}.decision.take_id`),
    };
  }
  return {
    model: group.model,
    scenario: group.scenario,
    line: group.line,
    variant: group.variant,
    role_epoch_sha256: group.role_epoch_sha256,
    group_sha256: group.group_sha256,
    plan_sha256: group.plan_sha256,
    anchor_selection_sha256: group.anchor_selection_sha256,
    candidate_set_sha256: group.candidate_set_sha256,
    revalidation_reason: reason,
    candidates,
    decision,
  } as BaselineGroupDraft;
}

function assertRubric(
  value: unknown,
  label: string,
  complete: boolean,
): asserts value is BaselineRubric {
  const rubric = exactObject(value, RUBRIC_KEYS, label);
  for (const key of [
    "content_correct",
    "prompt_leakage",
    "reading_correct",
    "adoptable",
  ] as const) {
    if (rubric[key] !== null && typeof rubric[key] !== "boolean") {
      throw new Error(`${label}.${key} はbooleanまたはnullが必要です。`);
    }
  }
  for (const key of [
    "accent_naturalness",
    "role_match",
    "delivery_match",
    "audio_quality",
  ] as const) {
    if (rubric[key] !== null && !isScore(rubric[key])) {
      throw new Error(`${label}.${key} は1..5またはnullが必要です。`);
    }
  }
  if (typeof rubric.notes !== "string") {
    throw new Error(`${label}.notes は文字列が必要です。`);
  }
  if (complete && !isBaselineRubricComplete(rubric as unknown as BaselineRubric)) {
    throw new Error(`${label} は全項目の入力が必要です。`);
  }
}

function listRecords(storage: BaselineStorage): readonly { key: string; raw: string }[] {
  const records: Array<{ key: string; raw: string }> = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (!key?.startsWith(BASELINE_STORAGE_PREFIX)) {
      continue;
    }
    const raw = storage.getItem(key);
    if (raw === null) {
      throw new Error(`列挙中にPhase B storage recordが消失しました: ${key}`);
    }
    records.push({ key, raw });
  }
  return records;
}

function removeOtherRecords(
  storage: BaselineStorage,
  records: readonly { key: string }[],
  retainedKey: string | null,
): void {
  for (const record of records) {
    if (record.key !== retainedKey) {
      storage.removeItem(record.key);
    }
  }
}

function parseJson(raw: string, key: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error(`Phase B storage JSONを解析できません: ${key}`);
  }
}

function exactObject(
  value: unknown,
  expectedKeys: readonly string[],
  label: string,
): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} はobjectが必要です。`);
  }
  const object = value as Record<string, unknown>;
  const actual = Object.keys(object).sort(compareText);
  const expected = [...expectedKeys].sort(compareText);
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${label} のkeyがexact contractと一致しません: ${actual.join(",")}`);
  }
  return object;
}

function nullableReason(value: unknown, label: string): string | null {
  if (value === null) {
    return null;
  }
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} は非空文字列またはnullが必要です。`);
  }
  return value;
}

function pathSegment(value: unknown, label: string): string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value === "." ||
    value === ".." ||
    value.includes("/") ||
    value.includes("\\")
  ) {
    throw new Error(`${label} は安全なpath segmentが必要です。`);
  }
  return value;
}

function sha(value: unknown, label: string): string {
  if (typeof value !== "string" || !SHA_PATTERN.test(value)) {
    throw new Error(`${label} は完全な小文字SHA-256が必要です。`);
  }
  return value;
}

function isScore(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 5;
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
