import { canonicalJson, assertCanonicalJsonBytes } from "@/lib/canonical-json";
import type { DirectoryFile, ObjectUrlFactory } from "@/lib/local-directory";
import { sha256Hex, sha256Text } from "@/lib/sha256";

import type {
  RoleCoverage,
  RoleReviewBundle,
  RoleReviewCandidate,
  RoleReviewCatalog,
  RoleReviewConditioning,
  RoleReviewCoverage,
  RoleReviewGroup,
  RoleReviewInputGroup,
  RoleReviewLine,
  RoleReviewPhase,
  RoleReviewQc,
  RoleReviewRole,
} from "./types";

export const ROLE_REVIEW_BUNDLE_FILE = "role-review-v1.json";
export const ROLE_REVIEW_MODEL_IDS = [
  "aivisspeech-kohaku",
  "chatterbox-multilingual-v3",
  "cosyvoice3-0.5b-2512",
  "gpt-sovits-v2-pro-plus",
  "irodori-tts-600m-v3-voicedesign",
  "qwen3-tts-12hz-1.7b",
  "supertonic-3",
  "voxcpm2",
] as const;

const SHA_PATTERN = /^[0-9a-f]{64}$/;
const SAFE_SEGMENT_PATTERN = /^[a-z0-9][a-z0-9-]*$/;
const AUDIO_PATH_PATTERN = /\.(?:flac|mp3|opus|wav)$/;
const ROLE_REVIEW_MODEL_ID_SET = new Set<string>(ROLE_REVIEW_MODEL_IDS);
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
  "role_epoch_sha256",
  "role",
  "conditioning",
  "coverage",
  "comparison_required",
  "comparison_reasons",
  "candidate_ids",
  "provisional_candidate_id",
  "candidates",
] as const;
const LINE_KEYS = ["id", "text", "delivery"] as const;
const ROLE_KEYS = ["name", "kind", "gender", "age", "archetype", "voice", "personality"] as const;
const CONDITIONING_KEYS = ["method", "summary"] as const;
const COVERAGE_KEYS = ["gender", "age", "archetype"] as const;
const CANDIDATE_KEYS = ["id", "attempt", "seed", "audio_path", "audio_sha256", "qc"] as const;
const QC_KEYS = ["mechanical", "content", "notes"] as const;

export async function loadRoleReviewCatalog(
  files: readonly DirectoryFile[],
  objectUrls: ObjectUrlFactory = browserObjectUrls,
): Promise<RoleReviewCatalog> {
  const directory = indexDirectory(files);
  const bundleFile = directory.get(ROLE_REVIEW_BUNDLE_FILE);
  if (!bundleFile) {
    throw new Error(`${ROLE_REVIEW_BUNDLE_FILE} はbundle直下にexactly 1件必要です。`);
  }

  const bundleBytes = await bundleFile.arrayBuffer();
  assertCanonicalJsonBytes(bundleBytes, ROLE_REVIEW_BUNDLE_FILE);
  const source = new TextDecoder("utf-8", { fatal: true }).decode(bundleBytes);
  let decoded: unknown;
  try {
    decoded = JSON.parse(source);
  } catch {
    throw new Error(`${ROLE_REVIEW_BUNDLE_FILE} は正しい JSON ではありません。`);
  }
  const bundle = validateBundle(decoded);
  const groupHashes = await Promise.all(
    bundle.groups.map((group) =>
      sha256Text(canonicalJson(group, `${ROLE_REVIEW_BUNDLE_FILE} group`)),
    ),
  );

  const referencedPaths = new Set<string>([ROLE_REVIEW_BUNDLE_FILE]);
  const candidateIds = new Set<string>();
  for (const group of bundle.groups) {
    for (const candidate of group.candidates) {
      if (candidateIds.has(candidate.id)) {
        throw new Error(`candidate id がbundle内で重複しています: ${candidate.id}`);
      }
      candidateIds.add(candidate.id);
      referencedPaths.add(candidate.audio_path);
    }
  }
  const actualPaths = [...directory.keys()].sort(compareText);
  const expectedPaths = [...referencedPaths].sort(compareText);
  if (
    actualPaths.length !== expectedPaths.length ||
    actualPaths.some((path, index) => path !== expectedPaths[index])
  ) {
    throw new Error(
      `bundle file set が参照と一致しません: expected=${expectedPaths.join(",")}, actual=${actualPaths.join(",")}`,
    );
  }

  await Promise.all(
    bundle.groups.flatMap((group) =>
      group.candidates.map(async (candidate) => {
        const file = directory.get(candidate.audio_path);
        if (!file) {
          throw new Error(`候補音声がありません: ${candidate.audio_path}`);
        }
        const actual = await sha256Hex(await file.arrayBuffer());
        if (actual !== candidate.audio_sha256) {
          throw new Error(
            `候補音声 SHA-256 が一致しません: ${candidate.audio_path} expected=${candidate.audio_sha256} actual=${actual}`,
          );
        }
      }),
    ),
  );

  const urls: string[] = [];
  try {
    const groups = bundle.groups
      .map((group, index): RoleReviewGroup => {
        const candidates = group.candidates.map((candidate, candidateIndex) => {
          const file = directory.get(candidate.audio_path);
          if (!file) {
            throw new Error(`検証済み候補音声を解決できません: ${candidate.audio_path}`);
          }
          const url = objectUrls.create(file);
          urls.push(url);
          return {
            ...candidate,
            label: blindLabel(candidateIndex),
            audio: {
              key: `role-review:${bundle.candidate_set_sha256}:${candidate.id}`,
              url,
            },
          };
        });
        return {
          ...group,
          phase: bundle.phase,
          group_sha256: groupHashes[index]!,
          candidates,
        };
      })
      .sort(compareGroups);

    let disposed = false;
    return {
      phase: bundle.phase,
      planSha256: bundle.plan_sha256,
      candidateSetSha256: bundle.candidate_set_sha256,
      groups,
      dispose() {
        if (disposed) {
          return;
        }
        disposed = true;
        for (const url of urls) {
          objectUrls.revoke(url);
        }
      },
    };
  } catch (reason: unknown) {
    for (const url of urls) {
      objectUrls.revoke(url);
    }
    throw reason;
  }
}

export function validateRoleReviewBundle(value: unknown): RoleReviewBundle {
  return validateBundle(value);
}

function validateBundle(value: unknown): RoleReviewBundle {
  const root = exactObject(value, ROOT_KEYS, "role review bundle");
  if (root.format_version !== 1) {
    throw new Error("role review bundle.format_version は 1 が必要です。");
  }
  if (root.protocol !== "role-review-v1") {
    throw new Error("role review bundle.protocol は role-review-v1 が必要です。");
  }
  const phase = reviewPhase(root.phase, "role review bundle.phase");
  const planSha = sha(root.plan_sha256, "role review bundle.plan_sha256");
  const candidateSetSha = sha(root.candidate_set_sha256, "role review bundle.candidate_set_sha256");
  if (!Array.isArray(root.groups) || root.groups.length === 0) {
    throw new Error("role review bundle.groups は1件以上が必要です。");
  }

  const groupIds = new Set<string>();
  const roleIdentities = new Map<string, string>();
  const groups = root.groups.map((item, index) => {
    const group = validateGroup(item, phase, `role review bundle.groups[${index}]`);
    if (groupIds.has(group.id)) {
      throw new Error(`group id が重複しています: ${group.id}`);
    }
    groupIds.add(group.id);
    const identityKey = JSON.stringify([group.model, group.character]);
    const identity = canonicalJson(
      {
        scenario: group.scenario,
        epoch: group.role_epoch_sha256,
        role: group.role,
        conditioning: group.conditioning,
        coverage: group.coverage,
      },
      "role identity",
    );
    const previous = roleIdentities.get(identityKey);
    if (previous !== undefined && previous !== identity) {
      throw new Error(
        `同一model/characterのrole identityがbundle内で一致しません: ${group.model}/${group.character}`,
      );
    }
    roleIdentities.set(identityKey, identity);
    return group;
  });
  return {
    format_version: 1,
    protocol: "role-review-v1",
    phase,
    plan_sha256: planSha,
    candidate_set_sha256: candidateSetSha,
    groups,
  };
}

function validateGroup(
  value: unknown,
  phase: RoleReviewPhase,
  label: string,
): RoleReviewInputGroup {
  const group = exactObject(value, GROUP_KEYS, label);
  const id = sha(group.id, `${label}.id`);
  const model = modelId(group.model, `${label}.model`);
  const scenario = safeSegment(group.scenario, `${label}.scenario`);
  const character = safeSegment(group.character, `${label}.character`);
  const line = validateLine(group.line, phase, `${label}.line`);
  const roleEpoch = sha(group.role_epoch_sha256, `${label}.role_epoch_sha256`);
  const role = validateRole(group.role, `${label}.role`);
  const conditioning = validateConditioning(group.conditioning, `${label}.conditioning`);
  const coverage = validateCoverage(group.coverage, `${label}.coverage`);
  if (typeof group.comparison_required !== "boolean") {
    throw new Error(`${label}.comparison_required は boolean が必要です。`);
  }
  const reasons = uniqueTextArray(group.comparison_reasons, `${label}.comparison_reasons`);
  if (group.comparison_required !== reasons.length > 0) {
    throw new Error(`${label}.comparison_required と comparison_reasons の有無が一致しません。`);
  }
  if (phase === "anchor" && !group.comparison_required) {
    throw new Error(`${label}.comparison_required は anchor phase で true が必要です。`);
  }
  if (!Array.isArray(group.candidates)) {
    throw new Error(`${label}.candidates は配列が必要です。`);
  }
  if (phase === "anchor" && group.candidates.length < 3) {
    throw new Error(`${label}.candidates は anchor phase で3件以上が必要です。`);
  }
  if (phase === "line" && group.candidates.length < 2) {
    throw new Error(`${label}.candidates は line phase で2件以上が必要です。`);
  }
  const candidates = group.candidates.map((candidate, index) =>
    validateCandidate(candidate, `${label}.candidates[${index}]`),
  );
  for (let index = 1; index < candidates.length; index += 1) {
    if (candidates[index - 1]!.attempt >= candidates[index]!.attempt) {
      throw new Error(`${label}.candidates は一意な attempt 昇順が必要です。`);
    }
  }
  const candidateIds = shaArray(group.candidate_ids, `${label}.candidate_ids`);
  const actualIds = candidates.map((candidate) => candidate.id);
  if (
    candidateIds.length !== actualIds.length ||
    candidateIds.some((candidateId, index) => candidateId !== actualIds[index])
  ) {
    throw new Error(`${label}.candidate_ids は candidates のexactなid順が必要です。`);
  }
  const provisional = sha(group.provisional_candidate_id, `${label}.provisional_candidate_id`);
  if (!candidateIds.includes(provisional)) {
    throw new Error(`${label}.provisional_candidate_id が candidates を参照していません。`);
  }
  return {
    id,
    model,
    scenario,
    character,
    line,
    role_epoch_sha256: roleEpoch,
    role,
    conditioning,
    coverage,
    comparison_required: group.comparison_required,
    comparison_reasons: reasons,
    candidate_ids: candidateIds,
    provisional_candidate_id: provisional,
    candidates,
  };
}

function validateLine(
  value: unknown,
  phase: RoleReviewPhase,
  label: string,
): RoleReviewLine | null {
  if (phase === "anchor") {
    if (value !== null) {
      throw new Error(`${label} は anchor phase で null が必要です。`);
    }
    return null;
  }
  const line = exactObject(value, LINE_KEYS, label);
  return {
    id: safeSegment(line.id, `${label}.id`),
    text: nonEmptyText(line.text, `${label}.text`),
    delivery: nonEmptyText(line.delivery, `${label}.delivery`),
  };
}

function validateRole(value: unknown, label: string): RoleReviewRole {
  const role = exactObject(value, ROLE_KEYS, label);
  const kind = enumValue(
    role.kind,
    ["human", "machine", "creature", "spirit"] as const,
    `${label}.kind`,
  );
  const gender = enumValue(role.gender, ["female", "male", "neutral"] as const, `${label}.gender`);
  const age = enumValue(
    role.age,
    ["child", "teen", "young_adult", "adult", "middle_aged", "elderly"] as const,
    `${label}.age`,
  );
  return {
    name: nonEmptyText(role.name, `${label}.name`),
    kind,
    gender,
    age,
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

function validateCoverage(value: unknown, label: string): RoleReviewCoverage {
  const coverage = exactObject(value, COVERAGE_KEYS, label);
  return {
    gender: coverageValue(coverage.gender, `${label}.gender`),
    age: coverageValue(coverage.age, `${label}.age`),
    archetype: coverageValue(coverage.archetype, `${label}.archetype`),
  };
}

function validateCandidate(value: unknown, label: string): RoleReviewCandidate {
  const candidate = exactObject(value, CANDIDATE_KEYS, label);
  const qc = validateQc(candidate.qc, `${label}.qc`);
  if (qc.mechanical !== "pass") {
    throw new Error(`${label}.qc.mechanical は role review candidate で pass が必要です。`);
  }
  return {
    id: sha(candidate.id, `${label}.id`),
    attempt: positiveInteger(candidate.attempt, `${label}.attempt`),
    seed: nonNegativeInteger(candidate.seed, `${label}.seed`),
    audio_path: audioPath(candidate.audio_path, `${label}.audio_path`),
    audio_sha256: sha(candidate.audio_sha256, `${label}.audio_sha256`),
    qc,
  };
}

function validateQc(value: unknown, label: string): RoleReviewQc {
  const qc = exactObject(value, QC_KEYS, label);
  if (qc.mechanical !== "pass" && qc.mechanical !== "fail") {
    throw new Error(`${label}.mechanical は pass または fail が必要です。`);
  }
  if (qc.content !== "not_checked" && qc.content !== "pass" && qc.content !== "review_required") {
    throw new Error(
      `${label}.content は not_checked / pass / review_required のいずれかが必要です。`,
    );
  }
  return {
    mechanical: qc.mechanical,
    content: qc.content,
    notes: stringArray(qc.notes, `${label}.notes`),
  };
}

function indexDirectory(files: readonly DirectoryFile[]): Map<string, DirectoryFile> {
  if (files.length === 0) {
    throw new Error("role review bundle folder が空です。");
  }
  const result = new Map<string, DirectoryFile>();
  let root: string | null = null;
  for (const file of files) {
    const normalized = file.webkitRelativePath.replaceAll("\\", "/");
    const parts = normalized.split("/");
    if (
      parts.length < 2 ||
      parts.some((part) => part.length === 0 || part === "." || part === "..")
    ) {
      throw new Error(`bundle file path が不正です: ${file.webkitRelativePath}`);
    }
    if (root === null) {
      root = parts[0]!;
    } else if (root !== parts[0]) {
      throw new Error("複数のbundle rootが混在しています。");
    }
    const relative = parts.slice(1).join("/");
    if (result.has(relative)) {
      throw new Error(`bundle file path が重複しています: ${relative}`);
    }
    result.set(relative, file);
  }
  return result;
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

function sha(value: unknown, label: string): string {
  if (typeof value !== "string" || !SHA_PATTERN.test(value)) {
    throw new Error(`${label} は完全な小文字 SHA-256 が必要です。`);
  }
  return value;
}

function shaArray(value: unknown, label: string): readonly string[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} は配列が必要です。`);
  }
  const result = value.map((item, index) => sha(item, `${label}[${index}]`));
  if (new Set(result).size !== result.length) {
    throw new Error(`${label} は一意である必要があります。`);
  }
  return result;
}

function uniqueTextArray(value: unknown, label: string): readonly string[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} は配列が必要です。`);
  }
  const result = value.map((item, index) => nonEmptyText(item, `${label}[${index}]`));
  if (new Set(result).size !== result.length) {
    throw new Error(`${label} は一意である必要があります。`);
  }
  return result;
}

function stringArray(value: unknown, label: string): readonly string[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} は配列が必要です。`);
  }
  return value.map((item, index) => {
    if (typeof item !== "string") {
      throw new Error(`${label}[${index}] は文字列が必要です。`);
    }
    return item;
  });
}

function nonEmptyText(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim().length === 0 || value !== value.trim()) {
    throw new Error(`${label} は前後空白のない非空文字列が必要です。`);
  }
  return value;
}

function safeSegment(value: unknown, label: string): string {
  const result = nonEmptyText(value, label);
  if (!SAFE_SEGMENT_PATTERN.test(result)) {
    throw new Error(`${label} は lowercase kebab-case id が必要です。`);
  }
  return result;
}

function modelId(value: unknown, label: string): string {
  const result = nonEmptyText(value, label);
  if (!ROLE_REVIEW_MODEL_ID_SET.has(result)) {
    throw new Error(`${label} は現在の8モデルIDのexact setに含まれる必要があります: ${result}`);
  }
  return result;
}

function audioPath(value: unknown, label: string): string {
  const result = nonEmptyText(value, label);
  if (
    result.includes("\\") ||
    result.startsWith("/") ||
    result.split("/").some((part) => part.length === 0 || part === "." || part === "..") ||
    !AUDIO_PATH_PATTERN.test(result)
  ) {
    throw new Error(`${label} はbundle相対の安全な音声pathが必要です。`);
  }
  return result;
}

function positiveInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new Error(`${label} は1以上の安全な整数が必要です。`);
  }
  return value;
}

function nonNegativeInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${label} は0以上の安全な整数が必要です。`);
  }
  return value;
}

function enumValue<const T extends readonly string[]>(
  value: unknown,
  values: T,
  label: string,
): T[number] {
  if (typeof value !== "string" || !values.includes(value)) {
    throw new Error(`${label} は ${values.join(" | ")} のいずれかが必要です。`);
  }
  return value;
}

function reviewPhase(value: unknown, label: string): RoleReviewPhase {
  return enumValue(value, ["anchor", "line"] as const, label);
}

function coverageValue(value: unknown, label: string): RoleCoverage {
  return enumValue(value, ["exact", "approximate", "neutral"] as const, label);
}

function compareGroups(left: RoleReviewInputGroup, right: RoleReviewInputGroup): number {
  return (
    compareText(left.model, right.model) ||
    compareText(left.scenario, right.scenario) ||
    compareText(left.character, right.character) ||
    compareText(left.line?.id ?? "", right.line?.id ?? "") ||
    compareText(left.id, right.id)
  );
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function blindLabel(index: number): string {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
}

const browserObjectUrls: ObjectUrlFactory = {
  create(file) {
    if (!(file instanceof Blob)) {
      throw new Error("候補音声が browser File ではありません。");
    }
    return URL.createObjectURL(file);
  },
  revoke(url) {
    URL.revokeObjectURL(url);
  },
};
