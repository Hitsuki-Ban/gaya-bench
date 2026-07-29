import type {
  PilotCandidateDraft,
  PilotCatalog,
  PilotDecisionDraft,
  PilotGroupDecision,
  PilotGroupDraft,
  PilotRubric,
} from "@/pilot/types";

export const PILOT_STORAGE_KEY = "gaya-bench:n3-pilot-decision:v1";

export interface PilotStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

const EMPTY_RUBRIC: PilotRubric = {
  content_correct: null,
  intent_match: null,
  character_naturalness: null,
  adoptable: null,
};

export function createPilotDecisionDraft(catalog: PilotCatalog): PilotDecisionDraft {
  return {
    version: 1,
    pilot_set_sha256: catalog.pilotSetSha256,
    groups: catalog.groups.map((group) => ({
      group_id: group.groupId,
      candidates: group.presentation.candidates.map((candidate) => ({
        candidate_id: candidate.candidateId,
        rubric: { ...EMPTY_RUBRIC },
      })),
      decision: null,
    })),
  };
}

export function readPilotDecisionDraft(
  storage: PilotStorage,
  catalog: PilotCatalog,
): PilotDecisionDraft {
  const raw = storage.getItem(PILOT_STORAGE_KEY);
  if (raw === null) {
    return createPilotDecisionDraft(catalog);
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw);
  } catch {
    throw new Error("pilot draft を JSON として解析できません。明示的にリセットしてください。");
  }
  return validateDraft(decoded, catalog);
}

export function writePilotDecisionDraft(
  storage: PilotStorage,
  catalog: PilotCatalog,
  draft: PilotDecisionDraft,
): string {
  const validated = validateDraft(draft, catalog);
  const raw = JSON.stringify(validated);
  storage.setItem(PILOT_STORAGE_KEY, raw);
  return raw;
}

export function resetPilotDecisionDraft(storage: PilotStorage): void {
  storage.removeItem(PILOT_STORAGE_KEY);
}

export function updatePilotCandidateRubric(
  draft: PilotDecisionDraft,
  groupId: string,
  candidateId: string,
  rubric: PilotRubric,
): PilotDecisionDraft {
  assertRubric(rubric, "rubric", false);
  let foundGroup = false;
  const groups = draft.groups.map((group) => {
    if (group.group_id !== groupId) {
      return group;
    }
    foundGroup = true;
    if (!group.candidates.some((candidate) => candidate.candidate_id === candidateId)) {
      throw new Error(`rubric 対象の candidate が存在しません: ${candidateId}`);
    }
    const next = {
      ...group,
      candidates: group.candidates.map((candidate) =>
        candidate.candidate_id === candidateId ? { ...candidate, rubric } : candidate,
      ),
    };
    assertDecisionAllowed(next, group.decision);
    return next;
  });
  if (!foundGroup) {
    throw new Error(`rubric 対象の group が存在しません: ${groupId}`);
  }
  return { ...draft, groups };
}

export function setPilotGroupDecision(
  draft: PilotDecisionDraft,
  groupId: string,
  decision: PilotGroupDecision,
): PilotDecisionDraft {
  let found = false;
  const groups = draft.groups.map((group) => {
    if (group.group_id !== groupId) {
      return group;
    }
    found = true;
    assertDecisionShape(decision, "decision");
    assertDecisionAllowed(group, decision);
    return { ...group, decision };
  });
  if (!found) {
    throw new Error(`decision 対象の group が存在しません: ${groupId}`);
  }
  return { ...draft, groups };
}

export function clearPilotGroupDecision(
  draft: PilotDecisionDraft,
  groupId: string,
): PilotDecisionDraft {
  let found = false;
  const groups = draft.groups.map((group) => {
    if (group.group_id !== groupId) {
      return group;
    }
    found = true;
    return { ...group, decision: null };
  });
  if (!found) {
    throw new Error(`decision 対象の group が存在しません: ${groupId}`);
  }
  return { ...draft, groups };
}

export function isPilotRubricComplete(rubric: PilotRubric): rubric is {
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

function validateDraft(value: unknown, catalog: PilotCatalog): PilotDecisionDraft {
  const draft = exactObject(value, ["version", "pilot_set_sha256", "groups"], "pilot draft");
  if (draft.version !== 1) {
    throw new Error("pilot draft.version は 1 である必要があります。");
  }
  if (draft.pilot_set_sha256 !== catalog.pilotSetSha256) {
    throw new Error(
      "保存済み pilot draft は現在の pilot-set と一致しません。明示的にリセットしてください。",
    );
  }
  if (!Array.isArray(draft.groups)) {
    throw new Error("pilot draft.groups は配列である必要があります。");
  }
  if (draft.groups.length !== catalog.groups.length) {
    throw new Error("pilot draft の group 集合が現在の catalog と一致しません。");
  }
  const groups = draft.groups.map((value, index) => {
    const group = validateGroupDraft(value, `pilot draft.groups[${index}]`);
    const expected = catalog.groups[index];
    if (!expected || group.group_id !== expected.groupId) {
      throw new Error(`pilot draft の group 順序が catalog と一致しません: ${group.group_id}`);
    }
    const expectedCandidates = expected.presentation.candidates.map(
      (candidate) => candidate.candidateId,
    );
    const actualCandidates = group.candidates.map((candidate) => candidate.candidate_id);
    if (
      actualCandidates.length !== expectedCandidates.length ||
      actualCandidates.some(
        (candidateId, candidateIndex) => candidateId !== expectedCandidates[candidateIndex],
      )
    ) {
      throw new Error(
        `pilot draft の candidate 集合と盲検順が catalog と一致しません: ${group.group_id}`,
      );
    }
    assertDecisionAllowed(group, group.decision);
    return group;
  });
  return {
    version: 1,
    pilot_set_sha256: draft.pilot_set_sha256,
    groups,
  };
}

function validateGroupDraft(value: unknown, label: string): PilotGroupDraft {
  const group = exactObject(value, ["group_id", "candidates", "decision"], label);
  const groupId = sha(group.group_id, `${label}.group_id`);
  if (!Array.isArray(group.candidates)) {
    throw new Error(`${label}.candidates は配列である必要があります。`);
  }
  const seen = new Set<string>();
  const candidates = group.candidates.map((value, index) => {
    const candidate = validateCandidateDraft(value, `${label}.candidates[${index}]`);
    if (seen.has(candidate.candidate_id)) {
      throw new Error(`${label} の candidate_id が重複しています: ${candidate.candidate_id}`);
    }
    seen.add(candidate.candidate_id);
    return candidate;
  });
  if (group.decision !== null) {
    assertDecisionShape(group.decision, `${label}.decision`);
  }
  return {
    group_id: groupId,
    candidates,
    decision: group.decision,
  } as PilotGroupDraft;
}

function validateCandidateDraft(value: unknown, label: string): PilotCandidateDraft {
  const candidate = exactObject(value, ["candidate_id", "rubric"], label);
  const candidateId = sha(candidate.candidate_id, `${label}.candidate_id`);
  assertRubric(candidate.rubric, `${label}.rubric`, false);
  return {
    candidate_id: candidateId,
    rubric: candidate.rubric,
  } as PilotCandidateDraft;
}

function assertRubric(
  value: unknown,
  label: string,
  requireComplete: boolean,
): asserts value is PilotRubric {
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
  if (requireComplete && !isPilotRubricComplete(rubric as unknown as PilotRubric)) {
    throw new Error(`${label} は全項目の入力が必要です。`);
  }
}

function assertDecisionShape(value: unknown, label: string): asserts value is PilotGroupDecision {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} は object である必要があります。`);
  }
  const decision = value as Record<string, unknown>;
  if (decision.type === "selected") {
    exactObject(decision, ["type", "candidate_id"], label);
    sha(decision.candidate_id, `${label}.candidate_id`);
    return;
  }
  exactObject(decision, ["type"], label);
  if (decision.type !== "skipped") {
    throw new Error(`${label}.type は selected または skipped である必要があります。`);
  }
}

function assertDecisionAllowed(group: PilotGroupDraft, decision: PilotGroupDecision | null): void {
  if (decision === null) {
    return;
  }
  for (const candidate of group.candidates) {
    assertRubric(candidate.rubric, `candidate ${candidate.candidate_id}.rubric`, true);
  }
  if (
    decision.type === "selected" &&
    !group.candidates.some((candidate) => candidate.candidate_id === decision.candidate_id)
  ) {
    throw new Error(`selected candidate が group に存在しません: ${decision.candidate_id}`);
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
