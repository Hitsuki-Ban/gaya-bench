import { isRubricComplete } from "@/curate/storage";
import {
  compareGroupTuple,
  groupKey,
  type CandidateDraft,
  type CurateDecision,
  type GroupDraft,
  type Rubric,
} from "@/curate/types";
import type { BaselineCatalog, BaselineCurationDraft } from "@/baseline/types";

export const BASELINE_CURATION_STORAGE_KEY = "gaya-bench:baseline-curation:v1";

export interface BaselineCurationStorage {
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

export function createBaselineCurationDraft(catalog: BaselineCatalog): BaselineCurationDraft {
  return {
    version: 1,
    candidate_set_sha256: catalog.candidateSetSha256,
    baseline_reference_sha256: catalog.baselineReferenceSha256,
    groups: catalog.groups.map((group) => ({
      model: group.model,
      scenario: group.scenario,
      line: group.line,
      variant: group.variant,
      candidates: [
        {
          take_id: group.candidate.takeId,
          rubric: { ...EMPTY_RUBRIC },
        },
      ],
      decision: null,
    })),
  };
}

export function readBaselineCurationDraft(
  storage: BaselineCurationStorage,
  catalog: BaselineCatalog,
): BaselineCurationDraft {
  const raw = storage.getItem(BASELINE_CURATION_STORAGE_KEY);
  if (raw === null) {
    return createBaselineCurationDraft(catalog);
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw);
  } catch {
    throw new Error(
      "baseline 策展 draft を JSON として解析できません。明示的にリセットしてください。",
    );
  }
  return validateDraft(decoded, catalog);
}

export function writeBaselineCurationDraft(
  storage: BaselineCurationStorage,
  catalog: BaselineCatalog,
  draft: BaselineCurationDraft,
): string {
  const validated = validateDraft(draft, catalog);
  const raw = JSON.stringify(validated);
  storage.setItem(BASELINE_CURATION_STORAGE_KEY, raw);
  return raw;
}

export function resetBaselineCurationDraft(storage: BaselineCurationStorage): void {
  storage.removeItem(BASELINE_CURATION_STORAGE_KEY);
}

export function updateBaselineCandidateRubric(
  draft: BaselineCurationDraft,
  targetGroupKey: string,
  takeId: string,
  rubric: Rubric,
): BaselineCurationDraft {
  assertRubric(rubric, "rubric", false);
  let foundGroup = false;
  const groups = draft.groups.map((group) => {
    if (groupKey(group) !== targetGroupKey) {
      return group;
    }
    foundGroup = true;
    if (group.candidates.length !== 1 || group.candidates[0]!.take_id !== takeId) {
      throw new Error(`rubric 対象の baseline candidate が存在しません: ${takeId}`);
    }
    const next: GroupDraft = {
      ...group,
      candidates: [{ ...group.candidates[0]!, rubric }],
    };
    assertDecisionAllowed(next, group.decision);
    return next;
  });
  if (!foundGroup) {
    throw new Error(`rubric 対象の baseline group が存在しません: ${targetGroupKey}`);
  }
  return { ...draft, groups };
}

export function setBaselineGroupDecision(
  draft: BaselineCurationDraft,
  targetGroupKey: string,
  decision: CurateDecision,
): BaselineCurationDraft {
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
    throw new Error(`decision 対象の baseline group が存在しません: ${targetGroupKey}`);
  }
  return { ...draft, groups };
}

export function clearBaselineGroupDecision(
  draft: BaselineCurationDraft,
  targetGroupKey: string,
): BaselineCurationDraft {
  let found = false;
  const groups = draft.groups.map((group) => {
    if (groupKey(group) !== targetGroupKey) {
      return group;
    }
    found = true;
    return { ...group, decision: null };
  });
  if (!found) {
    throw new Error(`decision 対象の baseline group が存在しません: ${targetGroupKey}`);
  }
  return { ...draft, groups };
}

function validateDraft(value: unknown, catalog: BaselineCatalog): BaselineCurationDraft {
  const draft = exactObject(
    value,
    ["version", "candidate_set_sha256", "baseline_reference_sha256", "groups"],
    "baseline draft",
  );
  if (draft.version !== 1) {
    throw new Error("baseline draft.version は 1 である必要があります。");
  }
  if (draft.candidate_set_sha256 !== catalog.candidateSetSha256) {
    throw new Error(
      "保存済み baseline draft は現在の candidate-set と一致しません。明示的にリセットしてください。",
    );
  }
  if (draft.baseline_reference_sha256 !== catalog.baselineReferenceSha256) {
    throw new Error(
      "保存済み baseline draft は現在の baseline-reference と一致しません。明示的にリセットしてください。",
    );
  }
  if (!Array.isArray(draft.groups) || draft.groups.length !== catalog.groups.length) {
    throw new Error("baseline draft の group 集合が現在の catalog と一致しません。");
  }

  const expectedGroups = new Map(catalog.groups.map((group) => [groupKey(group), group]));
  const seenGroups = new Set<string>();
  const groups = draft.groups.map((item, index) => {
    const group = validateGroupDraft(item, `baseline draft.groups[${index}]`);
    const key = groupKey(group);
    const catalogGroup = expectedGroups.get(key);
    if (!catalogGroup || seenGroups.has(key)) {
      throw new Error(`baseline draft に未知または重複 group があります: ${key}`);
    }
    seenGroups.add(key);
    if (
      group.candidates.length !== 1 ||
      group.candidates[0]!.take_id !== catalogGroup.candidate.takeId
    ) {
      throw new Error(`baseline draft の candidate 集合が catalog と一致しません: ${key}`);
    }
    assertDecisionAllowed(group, group.decision);
    return group;
  });
  if (
    groups.some((group, index) => index > 0 && compareGroupTuple(groups[index - 1]!, group) >= 0)
  ) {
    throw new Error("baseline draft の group は canonical tuple 順である必要があります。");
  }
  return {
    version: 1,
    candidate_set_sha256: draft.candidate_set_sha256,
    baseline_reference_sha256: draft.baseline_reference_sha256,
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
  const candidates = group.candidates.map((item, index) =>
    validateCandidateDraft(item, `${label}.candidates[${index}]`),
  );
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
  const takeId = sha(candidate.take_id, `${label}.take_id`);
  assertRubric(candidate.rubric, `${label}.rubric`, false);
  return {
    take_id: takeId,
    rubric: candidate.rubric,
  } as CandidateDraft;
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
    sha(decision.take_id, `${label}.take_id`);
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
  if (group.candidates.length !== 1) {
    throw new Error("baseline group は策展可能な candidate が 1 件必要です。");
  }
  const candidate = group.candidates[0]!;
  assertRubric(candidate.rubric, `candidate ${candidate.take_id}.rubric`, true);
  if (decision.type === "skipped") {
    return;
  }
  if (decision.take_id !== candidate.take_id) {
    throw new Error(`selected candidate が group に存在しません: ${decision.take_id}`);
  }
  if (candidate.rubric.content_correct !== true || candidate.rubric.adoptable !== true) {
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

function sha(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${label} は完全な小文字 SHA-256 である必要があります。`);
  }
  return value;
}
