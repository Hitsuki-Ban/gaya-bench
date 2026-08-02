import { canonicalJson } from "@/lib/canonical-json";
import { sha256Text } from "@/lib/sha256";

import type {
  RoleCoverage,
  RoleReviewBundle,
  RoleReviewCandidate,
  RoleReviewCatalog,
  RoleReviewConditioning,
  RoleReviewCoverage,
  RoleReviewGroup,
  RoleReviewInputGroup,
  RoleReviewQc,
  RoleReviewRole,
} from "./types";

export const ROLE_REVIEW_BUNDLE_FILE = "role-review-v2.json";
export const ROLE_REVIEW_GROUP_COUNT = 106;
export const ROLE_REVIEW_CANDIDATE_COUNT = 4;
export const ROLE_REVIEW_MODEL_IDS = [
  "irodori-tts-600m-v3-voicedesign",
  "qwen3-tts-12hz-1.7b",
] as const;

const SHA_PATTERN = /^[0-9a-f]{64}$/;
const SAFE_SEGMENT_PATTERN = /^[a-z0-9][a-z0-9-]*$/;
const AUDIO_PATH_PATTERN = /^audio\/[0-9a-f]{64}\.wav$/;
const MODEL_IDS = new Set<string>(ROLE_REVIEW_MODEL_IDS);
const COMPARISON_REASONS = [
  "role_match",
  "same_role_voice_identity",
  "anchor_audio_quality",
] as const;
const ROOT_KEYS = [
  "format_version",
  "protocol",
  "phase",
  "plan_sha256",
  "candidate_set_sha256",
  "groups",
] as const;
const GROUP_KEYS = [
  "id",
  "model",
  "scenario",
  "character",
  "line",
  "anchor_text",
  "role_epoch_sha256",
  "role",
  "conditioning",
  "coverage",
  "comparison_required",
  "comparison_reasons",
  "candidate_ids",
  "candidates",
] as const;
const ROLE_KEYS = ["name", "kind", "gender", "age", "archetype", "voice", "personality"];
const CONDITIONING_KEYS = ["method", "summary"];
const COVERAGE_KEYS = ["gender", "age", "archetype"];
const CANDIDATE_KEYS = ["id", "attempt", "seed", "audio_path", "audio_sha256", "qc"];
const QC_KEYS = ["mechanical", "content", "notes"];

export async function createRoleReviewCatalog(
  value: unknown,
  audioUrl: (candidateId: string) => string,
): Promise<RoleReviewCatalog> {
  const bundle = validateRoleReviewBundle(value);
  const groupHashes = await Promise.all(
    bundle.groups.map((group) =>
      sha256Text(canonicalJson(group, `${ROLE_REVIEW_BUNDLE_FILE} group`)),
    ),
  );
  const groups = bundle.groups.map(
    (group, index): RoleReviewGroup => ({
      ...group,
      group_sha256: groupHashes[index]!,
      candidates: group.candidates.map((candidate, candidateIndex) => ({
        ...candidate,
        label: String.fromCharCode(65 + candidateIndex),
        audio: {
          key: `role-review:${bundle.candidate_set_sha256}:${candidate.id}`,
          url: audioUrl(candidate.id),
        },
      })),
    }),
  );
  return {
    phase: "anchor",
    planSha256: bundle.plan_sha256,
    candidateSetSha256: bundle.candidate_set_sha256,
    groups,
  };
}

export function validateRoleReviewBundle(value: unknown): RoleReviewBundle {
  const root = exactObject(value, ROOT_KEYS, "role review bundle");
  if (root.format_version !== 2 || root.protocol !== "role-review-v2") {
    throw new Error("role review bundle は format_version=2 / role-review-v2 が必要です。");
  }
  if (root.phase !== "anchor") {
    throw new Error("role review bundle.phase は anchor が必要です。");
  }
  const planSha = sha(root.plan_sha256, "role review bundle.plan_sha256");
  const candidateSetSha = sha(root.candidate_set_sha256, "role review bundle.candidate_set_sha256");
  if (!Array.isArray(root.groups) || root.groups.length !== ROLE_REVIEW_GROUP_COUNT) {
    throw new Error(`role review bundle.groups はexactly ${ROLE_REVIEW_GROUP_COUNT}件が必要です。`);
  }

  const groupIds = new Set<string>();
  const candidateIds = new Set<string>();
  const audioPaths = new Set<string>();
  const modelCounts = new Map<string, number>();
  const coordinatesByModel = new Map<string, Set<string>>(
    ROLE_REVIEW_MODEL_IDS.map((model) => [model, new Set<string>()]),
  );
  const groups = root.groups.map((item, index) => {
    const group = validateGroup(item, `role review bundle.groups[${index}]`);
    if (groupIds.has(group.id)) {
      throw new Error(`group id が重複しています: ${group.id}`);
    }
    groupIds.add(group.id);
    modelCounts.set(group.model, (modelCounts.get(group.model) ?? 0) + 1);
    const coordinate = `${group.scenario}/${group.character}`;
    const modelCoordinates = coordinatesByModel.get(group.model)!;
    if (modelCoordinates.has(coordinate)) {
      throw new Error(`同一model内でrole座標が重複しています: ${group.model}/${coordinate}`);
    }
    modelCoordinates.add(coordinate);
    for (const candidate of group.candidates) {
      if (candidateIds.has(candidate.id)) {
        throw new Error(`candidate id が重複しています: ${candidate.id}`);
      }
      if (audioPaths.has(candidate.audio_path)) {
        throw new Error(`candidate audio_path が重複しています: ${candidate.audio_path}`);
      }
      candidateIds.add(candidate.id);
      audioPaths.add(candidate.audio_path);
    }
    return group;
  });
  const sorted = [...groups].sort(compareGroups);
  if (groups.some((group, index) => group.id !== sorted[index]!.id)) {
    throw new Error(
      "role review bundle.groups は model/scenario/character のcanonical順が必要です。",
    );
  }
  for (const model of ROLE_REVIEW_MODEL_IDS) {
    if (modelCounts.get(model) !== ROLE_REVIEW_GROUP_COUNT / ROLE_REVIEW_MODEL_IDS.length) {
      throw new Error(`role review bundle は各model 53 groupが必要です: ${model}`);
    }
  }
  const expectedCoordinates = coordinatesByModel.get(ROLE_REVIEW_MODEL_IDS[0])!;
  const comparedCoordinates = coordinatesByModel.get(ROLE_REVIEW_MODEL_IDS[1])!;
  if (
    expectedCoordinates.size !== comparedCoordinates.size ||
    [...expectedCoordinates].some((coordinate) => !comparedCoordinates.has(coordinate))
  ) {
    throw new Error("role review bundle は両modelで同じ53 role座標集合が必要です。");
  }
  return {
    format_version: 2,
    protocol: "role-review-v2",
    phase: "anchor",
    plan_sha256: planSha,
    candidate_set_sha256: candidateSetSha,
    groups,
  };
}

function validateGroup(value: unknown, label: string): RoleReviewInputGroup {
  const group = exactObject(value, GROUP_KEYS, label);
  const id = sha(group.id, `${label}.id`);
  const model = modelId(group.model, `${label}.model`);
  const scenario = safeSegment(group.scenario, `${label}.scenario`);
  const character = safeSegment(group.character, `${label}.character`);
  if (group.line !== null) {
    throw new Error(`${label}.line は anchor で null が必要です。`);
  }
  const anchorText = nonEmptyText(group.anchor_text, `${label}.anchor_text`);
  const roleEpoch = sha(group.role_epoch_sha256, `${label}.role_epoch_sha256`);
  const role = validateRole(group.role, `${label}.role`);
  const conditioning = validateConditioning(group.conditioning, `${label}.conditioning`);
  const coverage = validateCoverage(group.coverage, role, `${label}.coverage`);
  if (group.comparison_required !== true) {
    throw new Error(`${label}.comparison_required は true が必要です。`);
  }
  const reasons = exactTextArray(
    group.comparison_reasons,
    COMPARISON_REASONS,
    `${label}.comparison_reasons`,
  );
  if (!Array.isArray(group.candidates) || group.candidates.length !== ROLE_REVIEW_CANDIDATE_COUNT) {
    throw new Error(`${label}.candidates はexactly ${ROLE_REVIEW_CANDIDATE_COUNT}件が必要です。`);
  }
  const candidates = group.candidates.map((candidate, index) =>
    validateCandidate(candidate, `${label}.candidates[${index}]`),
  );
  for (const [index, candidate] of candidates.entries()) {
    if (index > 0 && candidate.attempt <= candidates[index - 1]!.attempt) {
      throw new Error(`${label}.candidates attempt は4件の一意な昇順正整数が必要です。`);
    }
  }
  const candidateIds = shaArray(group.candidate_ids, `${label}.candidate_ids`);
  if (
    candidateIds.length !== candidates.length ||
    candidateIds.some((candidateId, index) => candidateId !== candidates[index]!.id)
  ) {
    throw new Error(`${label}.candidate_ids は candidates のexactなid順が必要です。`);
  }
  return {
    id,
    model,
    scenario,
    character,
    line: null,
    anchor_text: anchorText,
    role_epoch_sha256: roleEpoch,
    role,
    conditioning,
    coverage,
    comparison_required: true,
    comparison_reasons: reasons,
    candidate_ids: candidateIds,
    candidates,
  };
}

function validateRole(value: unknown, label: string): RoleReviewRole {
  const role = exactObject(value, ROLE_KEYS, label);
  return {
    name: nonEmptyText(role.name, `${label}.name`),
    kind: enumValue(
      role.kind,
      ["human", "machine", "creature", "spirit"] as const,
      `${label}.kind`,
    ),
    gender: enumValue(role.gender, ["female", "male", "neutral"] as const, `${label}.gender`),
    age: enumValue(
      role.age,
      ["child", "teen", "young_adult", "adult", "middle_aged", "elderly"] as const,
      `${label}.age`,
    ),
    archetype: nonEmptyText(role.archetype, `${label}.archetype`),
    voice: nonEmptyText(role.voice, `${label}.voice`),
    personality: nonEmptyText(role.personality, `${label}.personality`),
  };
}

function validateConditioning(value: unknown, label: string): RoleReviewConditioning {
  const conditioning = exactObject(value, CONDITIONING_KEYS, label);
  return {
    method: nonEmptyText(conditioning.method, `${label}.method`),
    summary: nonEmptyText(conditioning.summary, `${label}.summary`),
  };
}

function validateCoverage(value: unknown, role: RoleReviewRole, label: string): RoleReviewCoverage {
  const coverage = exactObject(value, COVERAGE_KEYS, label);
  const result = {
    gender: coverageValue(coverage.gender, `${label}.gender`),
    age: coverageValue(coverage.age, `${label}.age`),
    archetype: coverageValue(coverage.archetype, `${label}.archetype`),
  };
  const expectedGender = role.gender === "neutral" ? "neutral" : "exact";
  if (result.gender !== expectedGender || result.age !== "exact" || result.archetype !== "exact") {
    throw new Error(`${label} がroleの指定範囲と一致しません。`);
  }
  return result;
}

function validateCandidate(value: unknown, label: string): RoleReviewCandidate {
  const candidate = exactObject(value, CANDIDATE_KEYS, label);
  const id = sha(candidate.id, `${label}.id`);
  const attempt = positiveInteger(candidate.attempt, `${label}.attempt`);
  const audioPath = nonEmptyText(candidate.audio_path, `${label}.audio_path`);
  if (!AUDIO_PATH_PATTERN.test(audioPath) || audioPath !== `audio/${id}.wav`) {
    throw new Error(`${label}.audio_path は candidate id 由来の安全なWAV pathが必要です。`);
  }
  return {
    id,
    attempt,
    seed: nonNegativeInteger(candidate.seed, `${label}.seed`),
    audio_path: audioPath,
    audio_sha256: sha(candidate.audio_sha256, `${label}.audio_sha256`),
    qc: validateQc(candidate.qc, `${label}.qc`),
  };
}

function validateQc(value: unknown, label: string): RoleReviewQc {
  const qc = exactObject(value, QC_KEYS, label);
  if (qc.mechanical !== "pass") {
    throw new Error(`${label}.mechanical は pass が必要です。`);
  }
  const content = enumValue(
    qc.content,
    ["not_checked", "pass", "review_required"] as const,
    `${label}.content`,
  );
  if (!Array.isArray(qc.notes) || qc.notes.some((note) => typeof note !== "string")) {
    throw new Error(`${label}.notes は文字列配列が必要です。`);
  }
  return { mechanical: "pass", content, notes: qc.notes as readonly string[] };
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

function modelId(value: unknown, label: string): (typeof ROLE_REVIEW_MODEL_IDS)[number] {
  const result = nonEmptyText(value, label);
  if (!MODEL_IDS.has(result)) {
    throw new Error(`${label} は Anchor review のexact model setが必要です。`);
  }
  return result as (typeof ROLE_REVIEW_MODEL_IDS)[number];
}

function safeSegment(value: unknown, label: string): string {
  const result = nonEmptyText(value, label);
  if (!SAFE_SEGMENT_PATTERN.test(result)) {
    throw new Error(`${label} は安全なkebab-case segmentが必要です。`);
  }
  return result;
}

function sha(value: unknown, label: string): string {
  if (typeof value !== "string" || !SHA_PATTERN.test(value)) {
    throw new Error(`${label} は完全な小文字SHA-256が必要です。`);
  }
  return value;
}

function shaArray(value: unknown, label: string): readonly string[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} は配列が必要です。`);
  }
  return value.map((item, index) => sha(item, `${label}[${index}]`));
}

function exactTextArray<const Expected extends readonly string[]>(
  value: unknown,
  expected: Expected,
  label: string,
): Expected {
  if (!Array.isArray(value) || value.length !== expected.length) {
    throw new Error(`${label} はexactな固定配列が必要です。`);
  }
  if (value.some((item, index) => item !== expected[index])) {
    throw new Error(`${label} はexactな固定順が必要です。`);
  }
  return expected;
}

function nonEmptyText(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim().length === 0 || value !== value.trim()) {
    throw new Error(`${label} は前後空白のない非空文字列が必要です。`);
  }
  return value;
}

function nonNegativeInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${label} は0以上の整数が必要です。`);
  }
  return value;
}

function positiveInteger(value: unknown, label: string): number {
  const result = nonNegativeInteger(value, label);
  if (result === 0) {
    throw new Error(`${label} は1以上の整数が必要です。`);
  }
  return result;
}

function enumValue<const Values extends readonly string[]>(
  value: unknown,
  allowed: Values,
  label: string,
): Values[number] {
  if (typeof value !== "string" || !allowed.includes(value)) {
    throw new Error(`${label} が許可された値ではありません。`);
  }
  return value as Values[number];
}

function coverageValue(value: unknown, label: string): RoleCoverage {
  return enumValue(value, ["exact", "neutral"] as const, label);
}

function compareGroups(left: RoleReviewInputGroup, right: RoleReviewInputGroup): number {
  for (const key of ["model", "scenario", "character"] as const) {
    const result = compareText(left[key], right[key]);
    if (result !== 0) {
      return result;
    }
  }
  return 0;
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
