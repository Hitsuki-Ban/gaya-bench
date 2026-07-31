import {
  completionGroupKey,
  type CompletionCatalog,
  type CompletionDraft,
  type CompletionGroupDraft,
  type CompletionRubric,
} from "./types";

export const COMPLETION_STORAGE_KEY = "gaya-bench:baseline-completion:v1";

export interface CompletionStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

const EMPTY_RUBRIC: CompletionRubric = {
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

export function createCompletionDraft(catalog: CompletionCatalog): CompletionDraft {
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

export function readCompletionDraft(
  storage: CompletionStorage,
  catalog: CompletionCatalog,
): CompletionDraft {
  const raw = storage.getItem(COMPLETION_STORAGE_KEY);
  if (raw === null) {
    return createCompletionDraft(catalog);
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw);
  } catch {
    throw new Error("補録 draft を JSON として解析できません。明示的にリセットしてください。");
  }
  return validateDraft(decoded, catalog);
}

export function writeCompletionDraft(
  storage: CompletionStorage,
  catalog: CompletionCatalog,
  draft: CompletionDraft,
): string {
  const validated = validateDraft(draft, catalog);
  const raw = JSON.stringify(validated);
  storage.setItem(COMPLETION_STORAGE_KEY, raw);
  return raw;
}

export function resetCompletionDraft(storage: CompletionStorage): void {
  storage.removeItem(COMPLETION_STORAGE_KEY);
}

export function updateCompletionRubric(
  draft: CompletionDraft,
  targetGroupKey: string,
  takeId: string,
  rubric: CompletionRubric,
): CompletionDraft {
  assertRubric(rubric, "rubric", false);
  let found = false;
  const groups = draft.groups.map((group) => {
    if (completionGroupKey(group) !== targetGroupKey) {
      return group;
    }
    found = true;
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
  if (!found) {
    throw new Error(`rubric 対象の group が存在しません: ${targetGroupKey}`);
  }
  return { ...draft, groups };
}

export function setCompletionDecision(
  draft: CompletionDraft,
  targetGroupKey: string,
  takeId: string,
): CompletionDraft {
  let found = false;
  const groups = draft.groups.map((group) => {
    if (completionGroupKey(group) !== targetGroupKey) {
      return group;
    }
    found = true;
    const decision = { type: "selected" as const, take_id: takeId };
    assertDecisionAllowed(group, decision);
    return { ...group, decision };
  });
  if (!found) {
    throw new Error(`decision 対象の group が存在しません: ${targetGroupKey}`);
  }
  return { ...draft, groups };
}

export function clearCompletionDecision(
  draft: CompletionDraft,
  targetGroupKey: string,
): CompletionDraft {
  let found = false;
  const groups = draft.groups.map((group) => {
    if (completionGroupKey(group) !== targetGroupKey) {
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

export function isCompletionRubricComplete(rubric: CompletionRubric): rubric is CompletionRubric & {
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

function validateDraft(value: unknown, catalog: CompletionCatalog): CompletionDraft {
  const draft = exactObject(value, ["version", "candidate_set_sha256", "groups"], "補録 draft");
  if (draft.version !== 1) {
    throw new Error("補録 draft.version は 1 が必要です。");
  }
  if (draft.candidate_set_sha256 !== catalog.candidateSetSha256) {
    throw new Error(
      "保存済み補録 draft は現在の candidate-set と一致しません。明示的にリセットしてください。",
    );
  }
  if (!Array.isArray(draft.groups) || draft.groups.length !== catalog.groups.length) {
    throw new Error("補録 draft の group 集合が現在の catalog と一致しません。");
  }
  const groups = draft.groups.map((value, index) => {
    const group = validateGroup(value, `補録 draft.groups[${index}]`);
    const expected = catalog.groups[index];
    if (!expected || completionGroupKey(group) !== completionGroupKey(expected)) {
      throw new Error(
        `補録 draft の group 順序が catalog と一致しません: ${completionGroupKey(group)}`,
      );
    }
    const expectedTakeIds = [...expected.candidates]
      .map((candidate) => candidate.takeId)
      .sort(compareText);
    const actualTakeIds = group.candidates.map((candidate) => candidate.take_id);
    if (
      actualTakeIds.length !== expectedTakeIds.length ||
      actualTakeIds.some((takeId, candidateIndex) => takeId !== expectedTakeIds[candidateIndex])
    ) {
      throw new Error(
        `補録 draft の candidate 集合が catalog と一致しません: ${completionGroupKey(group)}`,
      );
    }
    assertDecisionAllowed(group, group.decision);
    return group;
  });
  return {
    version: 1,
    candidate_set_sha256: draft.candidate_set_sha256,
    groups,
  };
}

function validateGroup(value: unknown, label: string): CompletionGroupDraft {
  const group = exactObject(
    value,
    ["model", "scenario", "line", "variant", "candidates", "decision"],
    label,
  );
  for (const key of ["model", "scenario", "line", "variant"] as const) {
    if (typeof group[key] !== "string" || group[key].length === 0) {
      throw new Error(`${label}.${key} は空でない文字列が必要です。`);
    }
  }
  if (!Array.isArray(group.candidates)) {
    throw new Error(`${label}.candidates は配列が必要です。`);
  }
  const candidates = group.candidates.map((value, index) => {
    const candidate = exactObject(value, ["take_id", "rubric"], `${label}.candidates[${index}]`);
    const takeId = sha(candidate.take_id, `${label}.candidates[${index}].take_id`);
    assertRubric(candidate.rubric, `${label}.candidates[${index}].rubric`, false);
    return { take_id: takeId, rubric: candidate.rubric };
  });
  if (
    candidates.some(
      (candidate, index) =>
        index > 0 && compareText(candidates[index - 1]!.take_id, candidate.take_id) >= 0,
    )
  ) {
    throw new Error(`${label}.candidates は一意な take_id 順が必要です。`);
  }
  let decision: CompletionGroupDraft["decision"] = null;
  if (group.decision !== null) {
    const raw = exactObject(group.decision, ["type", "take_id"], `${label}.decision`);
    if (raw.type !== "selected") {
      throw new Error(`${label}.decision.type は selected が必要です。`);
    }
    decision = {
      type: "selected",
      take_id: sha(raw.take_id, `${label}.decision.take_id`),
    };
  }
  return {
    model: group.model,
    scenario: group.scenario,
    line: group.line,
    variant: group.variant,
    candidates,
    decision,
  } as CompletionGroupDraft;
}

function assertDecisionAllowed(
  group: CompletionGroupDraft,
  decision: CompletionGroupDraft["decision"],
): void {
  if (decision === null) {
    return;
  }
  for (const candidate of group.candidates) {
    assertRubric(candidate.rubric, `candidate ${candidate.take_id}.rubric`, true);
  }
  if (!group.candidates.some((candidate) => candidate.take_id === decision.take_id)) {
    throw new Error(`selected candidate が group に存在しません: ${decision.take_id}`);
  }
}

function assertRubric(
  value: unknown,
  label: string,
  requireComplete: boolean,
): asserts value is CompletionRubric {
  const rubric = exactObject(
    value,
    [
      "content_correct",
      "prompt_leakage",
      "reading_correct",
      "accent_naturalness",
      "role_match",
      "delivery_match",
      "audio_quality",
      "adoptable",
      "notes",
    ],
    label,
  );
  for (const key of [
    "content_correct",
    "prompt_leakage",
    "reading_correct",
    "adoptable",
  ] as const) {
    if (rubric[key] !== null && typeof rubric[key] !== "boolean") {
      throw new Error(`${label}.${key} は bool または null が必要です。`);
    }
  }
  for (const key of [
    "accent_naturalness",
    "role_match",
    "delivery_match",
    "audio_quality",
  ] as const) {
    if (rubric[key] !== null && !isScore(rubric[key])) {
      throw new Error(`${label}.${key} は 1..5 の整数または null が必要です。`);
    }
  }
  if (typeof rubric.notes !== "string") {
    throw new Error(`${label}.notes は文字列が必要です。`);
  }
  if (requireComplete && !isCompletionRubricComplete(rubric as unknown as CompletionRubric)) {
    throw new Error(`${label} は全必須項目の入力が必要です。`);
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
  const actual = Object.keys(object).sort();
  const expected = [...expectedKeys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${label} の key が不正です: ${actual.join(",")}`);
  }
  return object;
}

function sha(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${label} は完全な小文字 SHA-256 が必要です。`);
  }
  return value;
}

function isScore(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 5;
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
