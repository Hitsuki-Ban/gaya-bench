import {
  compareGroupTuple,
  groupKey,
  type CandidateDraft,
  type CurateCatalog,
  type CurateDecision,
  type CurationDraft,
  type GroupDraft,
  type Rubric,
} from "./types";

export const CURATION_STORAGE_KEY = "gaya-bench:take-curation:v1";

export interface CurationStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

const EMPTY_RUBRIC: Rubric = {
  content_correct: null,
  intent_match: null,
  character_naturalness: null,
  adoptable: null,
};

export function createCurationDraft(catalog: CurateCatalog): CurationDraft {
  return {
    version: 1,
    candidate_set_sha256: catalog.candidateSetSha256,
    groups: catalog.groups.map((group) => ({
      model: group.model,
      scenario: group.scenario,
      line: group.line,
      variant: group.variant,
      candidates: [...group.candidates]
        .sort((left, right) => compareText(left.takeId, right.takeId))
        .map((candidate) => ({
          take_id: candidate.takeId,
          rubric: { ...EMPTY_RUBRIC },
        })),
      decision: null,
    })),
  };
}

export function readCurationDraft(storage: CurationStorage, catalog: CurateCatalog): CurationDraft {
  const raw = storage.getItem(CURATION_STORAGE_KEY);
  if (raw === null) {
    return createCurationDraft(catalog);
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw);
  } catch {
    throw new Error("策展 draft を JSON として解析できません。明示的にリセットしてください。");
  }
  return validateDraft(decoded, catalog);
}

export function writeCurationDraft(
  storage: CurationStorage,
  catalog: CurateCatalog,
  draft: CurationDraft,
): string {
  const validated = validateDraft(draft, catalog);
  const raw = JSON.stringify(validated);
  storage.setItem(CURATION_STORAGE_KEY, raw);
  return raw;
}

export function resetCurationDraft(storage: CurationStorage): void {
  storage.removeItem(CURATION_STORAGE_KEY);
}

export function updateCandidateRubric(
  draft: CurationDraft,
  targetGroupKey: string,
  takeId: string,
  rubric: Rubric,
): CurationDraft {
  assertRubric(rubric, "rubric", false);
  const groups = draft.groups.map((group) => {
    if (groupKey(group) !== targetGroupKey) {
      return group;
    }
    if (!group.candidates.some((candidate) => candidate.take_id === takeId)) {
      throw new Error(`rubric 対象の candidate が存在しません: ${takeId}`);
    }
    const candidates = group.candidates.map((candidate) =>
      candidate.take_id === takeId ? { ...candidate, rubric } : candidate,
    );
    const next = { ...group, candidates };
    assertDecisionAllowed(next, group.decision);
    return next;
  });
  if (!draft.groups.some((group) => groupKey(group) === targetGroupKey)) {
    throw new Error(`rubric 対象の group が存在しません: ${targetGroupKey}`);
  }
  return { ...draft, groups };
}

export function setGroupDecision(
  draft: CurationDraft,
  targetGroupKey: string,
  decision: CurateDecision,
): CurationDraft {
  let found = false;
  const groups = draft.groups.map((group) => {
    if (groupKey(group) !== targetGroupKey) {
      return group;
    }
    found = true;
    assertDecisionShape(decision, "decision");
    assertDecisionAllowed(group, decision);
    return { ...group, decision };
  });
  if (!found) {
    throw new Error(`decision 対象の group が存在しません: ${targetGroupKey}`);
  }
  return { ...draft, groups };
}

export function clearGroupDecision(draft: CurationDraft, targetGroupKey: string): CurationDraft {
  let found = false;
  const groups = draft.groups.map((group) => {
    if (groupKey(group) !== targetGroupKey) {
      return group;
    }
    found = true;
    return { ...group, decision: null };
  });
  if (!found) {
    throw new Error(`decision 対象の group が存在しません: ${targetGroupKey}`);
  }
  return { ...draft, groups };
}

export function isRubricComplete(rubric: Rubric): rubric is {
  readonly content_correct: boolean;
  readonly intent_match: number;
  readonly character_naturalness: number;
  readonly adoptable: boolean;
} {
  return (
    typeof rubric.content_correct === "boolean" &&
    Number.isInteger(rubric.intent_match) &&
    rubric.intent_match !== null &&
    rubric.intent_match >= 1 &&
    rubric.intent_match <= 5 &&
    Number.isInteger(rubric.character_naturalness) &&
    rubric.character_naturalness !== null &&
    rubric.character_naturalness >= 1 &&
    rubric.character_naturalness <= 5 &&
    typeof rubric.adoptable === "boolean"
  );
}

function validateDraft(value: unknown, catalog: CurateCatalog): CurationDraft {
  const draft = exactObject(value, ["version", "candidate_set_sha256", "groups"], "draft");
  if (draft.version !== 1) {
    throw new Error("策展 draft の version は 1 である必要があります。");
  }
  if (draft.candidate_set_sha256 !== catalog.candidateSetSha256) {
    throw new Error(
      "保存済み策展 draft は現在の candidate-set と一致しません。明示的にリセットしてください。",
    );
  }
  if (!Array.isArray(draft.groups)) {
    throw new Error("策展 draft.groups は配列である必要があります。");
  }
  if (draft.groups.length !== catalog.groups.length) {
    throw new Error("策展 draft の group 集合が現在の catalog と一致しません。");
  }

  const expectedGroups = new Map(catalog.groups.map((group) => [groupKey(group), group]));
  const seenGroups = new Set<string>();
  const groups = draft.groups.map((item, index) => {
    const group = validateGroupDraft(item, `draft.groups[${index}]`);
    const key = groupKey(group);
    const catalogGroup = expectedGroups.get(key);
    if (!catalogGroup || seenGroups.has(key)) {
      throw new Error(`策展 draft に未知または重複 group があります: ${key}`);
    }
    seenGroups.add(key);
    const expectedTakeIds = [...catalogGroup.candidates]
      .map((candidate) => candidate.takeId)
      .sort(compareText);
    const actualTakeIds = group.candidates.map((candidate) => candidate.take_id);
    if (
      actualTakeIds.length !== expectedTakeIds.length ||
      actualTakeIds.some((takeId, takeIndex) => takeId !== expectedTakeIds[takeIndex])
    ) {
      throw new Error(`策展 draft の candidate 集合が catalog と一致しません: ${key}`);
    }
    assertDecisionAllowed(group, group.decision);
    return group;
  });
  if (
    groups.some((group, index) => index > 0 && compareGroupTuple(groups[index - 1]!, group) >= 0)
  ) {
    throw new Error("策展 draft の group は canonical tuple 順である必要があります。");
  }
  return {
    version: 1,
    candidate_set_sha256: draft.candidate_set_sha256,
    groups,
  };
}

function validateGroupDraft(value: unknown, label: string): GroupDraft {
  const group = exactObject(
    value,
    ["model", "scenario", "line", "variant", "candidates", "decision"],
    label,
  );
  for (const key of ["model", "scenario", "line", "variant"] as const) {
    if (typeof group[key] !== "string" || group[key].length === 0) {
      throw new Error(`${label}.${key} は空でない文字列である必要があります。`);
    }
  }
  if (!Array.isArray(group.candidates)) {
    throw new Error(`${label}.candidates は配列である必要があります。`);
  }
  const seenTakeIds = new Set<string>();
  const candidates = group.candidates.map((item, index) => {
    const candidate = validateCandidateDraft(item, `${label}.candidates[${index}]`);
    if (seenTakeIds.has(candidate.take_id)) {
      throw new Error(`${label} の take_id が重複しています: ${candidate.take_id}`);
    }
    seenTakeIds.add(candidate.take_id);
    return candidate;
  });
  if (
    candidates.some(
      (candidate, index) =>
        index > 0 && compareText(candidates[index - 1]!.take_id, candidate.take_id) >= 0,
    )
  ) {
    throw new Error(`${label}.candidates は take_id 順である必要があります。`);
  }
  if (group.decision !== null) {
    assertDecisionShape(group.decision, `${label}.decision`);
  }
  return {
    model: group.model,
    scenario: group.scenario,
    line: group.line,
    variant: group.variant,
    candidates,
    decision: group.decision,
  } as GroupDraft;
}

function validateCandidateDraft(value: unknown, label: string): CandidateDraft {
  const candidate = exactObject(value, ["take_id", "rubric"], label);
  if (typeof candidate.take_id !== "string" || !/^[0-9a-f]{64}$/.test(candidate.take_id)) {
    throw new Error(`${label}.take_id は完全な小文字 SHA-256 である必要があります。`);
  }
  assertRubric(candidate.rubric, `${label}.rubric`, false);
  return candidate as unknown as CandidateDraft;
}

function assertRubric(
  value: unknown,
  label: string,
  requireComplete: boolean,
): asserts value is Rubric {
  const rubric = exactObject(
    value,
    ["content_correct", "intent_match", "character_naturalness", "adoptable"],
    label,
  );
  for (const key of ["content_correct", "adoptable"] as const) {
    if (rubric[key] !== null && typeof rubric[key] !== "boolean") {
      throw new Error(`${label}.${key} は bool または null である必要があります。`);
    }
  }
  for (const key of ["intent_match", "character_naturalness"] as const) {
    const score = rubric[key];
    if (
      score !== null &&
      (typeof score !== "number" || !Number.isInteger(score) || score < 1 || score > 5)
    ) {
      throw new Error(`${label}.${key} は 1..5 の整数または null である必要があります。`);
    }
  }
  if (requireComplete && !isRubricComplete(rubric as unknown as Rubric)) {
    throw new Error(`${label} は全項目の入力が必要です。`);
  }
}

function assertDecisionShape(value: unknown, label: string): asserts value is CurateDecision {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} は object である必要があります。`);
  }
  const decision = value as Record<string, unknown>;
  if (decision.type === "selected") {
    exactObject(decision, ["type", "take_id"], label);
    if (typeof decision.take_id !== "string" || !/^[0-9a-f]{64}$/.test(decision.take_id)) {
      throw new Error(`${label}.take_id は完全な小文字 SHA-256 である必要があります。`);
    }
    return;
  }
  exactObject(decision, ["type"], label);
  if (decision.type !== "skipped") {
    throw new Error(`${label}.type は selected または skipped である必要があります。`);
  }
}

function assertDecisionAllowed(group: GroupDraft, decision: CurateDecision | null): void {
  if (decision === null) {
    return;
  }
  for (const candidate of group.candidates) {
    assertRubric(candidate.rubric, `candidate ${candidate.take_id}.rubric`, true);
  }
  if (decision.type === "skipped") {
    return;
  }
  const selected = group.candidates.find((candidate) => candidate.take_id === decision.take_id);
  if (!selected) {
    throw new Error(`selected candidate が group に存在しません: ${decision.take_id}`);
  }
  if (selected.rubric.content_correct !== true || selected.rubric.adoptable !== true) {
    throw new Error("selected candidate は content_correct=true かつ adoptable=true が必要です。");
  }
}

function exactObject(
  value: unknown,
  expectedKeys: readonly string[],
  label: string,
): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} は object である必要があります。`);
  }
  const object = value as Record<string, unknown>;
  const actual = Object.keys(object).sort();
  const expected = [...expectedKeys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${label} の key が不正です: ${actual.join(",")}`);
  }
  return object;
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
