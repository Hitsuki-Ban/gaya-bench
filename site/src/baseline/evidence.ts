import { assertCanonicalJsonBytes } from "@/lib/canonical-json";
import { isSafeClipPath } from "@/lib/clip-path";
import type { DirectoryFile } from "@/lib/local-directory";
import { sha256Hex } from "@/lib/sha256";
import { compareGroupTuple, groupKey } from "@/curate/types";

const INVENTORY_NAME = "baseline-bundle-inventory.json";
const INVENTORY_MARKER_NAME = "baseline-bundle-inventory.sha256";
const PLAN_NAME = "baseline-plan.json";
const PLAN_MARKER_NAME = "baseline-plan.sha256";
const HEX_64 = /^[0-9a-f]{64}$/;
const EXPECTED_MODEL_COUNT = 7;
const EXPECTED_GROUP_COUNT = 381;
const INVENTORY_ROOT_KEYS = ["files", "format_version"] as const;
const INVENTORY_ITEM_KEYS = ["path", "sha256"] as const;
const PLAN_ROOT_KEYS = [
  "excluded_failures",
  "format_version",
  "groups",
  "models",
  "plan_version",
  "source",
] as const;
const PLAN_SOURCE_KEYS = ["manifest_path", "manifest_sha256", "scenario_sha256"] as const;
const PLAN_GROUP_KEYS = ["legacy", "line", "model", "scenario", "variant"] as const;
const PLAN_LEGACY_KEYS = ["path", "sha256"] as const;
const MODEL_KEYS = ["capabilities", "id", "license_note", "name", "version"] as const;
const CAPABILITY_KEYS = ["clone", "emotion", "nonverbal", "reading", "voice_prompt"] as const;
const GROUP_KEYS = ["line", "model", "scenario", "variant"] as const;

interface GroupIdentity {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
}

export interface EvidenceReference extends GroupIdentity {
  readonly public_path: string;
  readonly legacy_sha256: string;
}

export async function validateBaselineEvidence(
  files: ReadonlyMap<string, DirectoryFile>,
  candidateSetDocument: Readonly<Record<string, unknown>>,
  references: readonly EvidenceReference[],
  sourceManifestSha256: string,
): Promise<ReadonlySet<string>> {
  const inventoryBytes = await readBytes(files, INVENTORY_NAME);
  const inventoryMarkerBytes = await readBytes(files, INVENTORY_MARKER_NAME);
  assertCanonicalJsonBytes(inventoryBytes, INVENTORY_NAME);
  const inventorySha256 = await sha256Hex(inventoryBytes);
  if (parseInventoryMarker(inventoryMarkerBytes) !== inventorySha256) {
    throw new Error(`${INVENTORY_MARKER_NAME} が${INVENTORY_NAME}のraw SHAと一致しません。`);
  }

  const inventory = parseInventory(decodeJson(inventoryBytes, INVENTORY_NAME));
  const expectedPaths = new Set([
    INVENTORY_NAME,
    INVENTORY_MARKER_NAME,
    ...inventory.map((entry) => entry.path),
  ]);
  assertExactDirectoryCoverage(files, expectedPaths);
  await Promise.all(
    inventory.map(async (entry) => {
      const actual = await sha256Hex(await readBytes(files, entry.path));
      if (actual !== entry.sha256) {
        throw new Error(`${INVENTORY_NAME} のfile SHAが一致しません: ${entry.path}`);
      }
    }),
  );

  const planBytes = await readBytes(files, PLAN_NAME);
  const planMarkerBytes = await readBytes(files, PLAN_MARKER_NAME);
  assertCanonicalJsonBytes(planBytes, PLAN_NAME);
  const planSha256 = await sha256Hex(planBytes);
  if (parseRawMarker(planMarkerBytes, PLAN_MARKER_NAME) !== planSha256) {
    throw new Error(`${PLAN_MARKER_NAME} が${PLAN_NAME}のraw SHAと一致しません。`);
  }
  parsePlan(
    decodeJson(planBytes, PLAN_NAME),
    candidateSetDocument,
    references,
    sourceManifestSha256,
  );
  return expectedPaths;
}

function parseInventory(
  value: unknown,
): readonly { readonly path: string; readonly sha256: string }[] {
  const root = exactObject(value, INVENTORY_ROOT_KEYS, INVENTORY_NAME);
  if (root.format_version !== 1 || !Array.isArray(root.files)) {
    throw new Error(`${INVENTORY_NAME} はformat_version=1とfiles配列が必要です。`);
  }
  const entries = root.files.map((value, index) => {
    const label = `${INVENTORY_NAME}.files[${index}]`;
    const entry = exactObject(value, INVENTORY_ITEM_KEYS, label);
    const path = canonicalBundlePath(entry.path, `${label}.path`);
    if (path === INVENTORY_NAME || path === INVENTORY_MARKER_NAME) {
      throw new Error(`${label}.path はinventory自身を含められません。`);
    }
    return {
      path,
      sha256: sha(entry.sha256, `${label}.sha256`),
    };
  });
  const foldedPaths = new Set<string>();
  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index]!;
    if (index > 0 && entries[index - 1]!.path >= entry.path) {
      throw new Error(`${INVENTORY_NAME}.files は重複なしのordinal path昇順である必要があります。`);
    }
    const folded = entry.path.toLocaleLowerCase("en-US");
    if (foldedPaths.has(folded)) {
      throw new Error(
        `${INVENTORY_NAME}.files に大小文字折り畳みpath競合があります: ${entry.path}`,
      );
    }
    foldedPaths.add(folded);
  }
  return entries;
}

function parsePlan(
  value: unknown,
  candidateSetDocument: Readonly<Record<string, unknown>>,
  references: readonly EvidenceReference[],
  sourceManifestSha256: string,
): void {
  const root = exactObject(value, PLAN_ROOT_KEYS, PLAN_NAME);
  if (root.format_version !== 1 || root.plan_version !== "baseline-plan-v1") {
    throw new Error(`${PLAN_NAME} はformat_version=1 / plan_version=baseline-plan-v1が必要です。`);
  }
  const source = exactObject(root.source, PLAN_SOURCE_KEYS, `${PLAN_NAME}.source`);
  canonicalBundlePath(source.manifest_path, `${PLAN_NAME}.source.manifest_path`);
  if (sha(source.manifest_sha256, `${PLAN_NAME}.source.manifest_sha256`) !== sourceManifestSha256) {
    throw new Error(`${PLAN_NAME} のsource manifest SHAがbaseline-referenceと一致しません。`);
  }
  if (
    sha(source.scenario_sha256, `${PLAN_NAME}.source.scenario_sha256`) !==
    candidateSetDocument.scenario_sha256
  ) {
    throw new Error(
      `${PLAN_NAME}.source.scenario_sha256 がaggregate candidate-setと一致しません。`,
    );
  }

  if (!Array.isArray(root.models) || root.models.length !== EXPECTED_MODEL_COUNT) {
    throw new Error(`${PLAN_NAME}.models は${EXPECTED_MODEL_COUNT}件である必要があります。`);
  }
  const models = root.models.map((value, index) => {
    const label = `${PLAN_NAME}.models[${index}]`;
    const model = exactObject(value, MODEL_KEYS, label);
    const id = pathSegment(model.id, `${label}.id`);
    nonEmptyString(model.name, `${label}.name`);
    nonEmptyString(model.version, `${label}.version`);
    if (typeof model.license_note !== "string") {
      throw new Error(`${label}.license_note は文字列である必要があります。`);
    }
    const capabilities = exactObject(model.capabilities, CAPABILITY_KEYS, `${label}.capabilities`);
    if (Object.values(capabilities).some((entry) => typeof entry !== "boolean")) {
      throw new Error(`${label}.capabilities はboolである必要があります。`);
    }
    return id;
  });
  if (
    new Set(models).size !== models.length ||
    models.some((model, index) => index > 0 && models[index - 1]! >= model)
  ) {
    throw new Error(`${PLAN_NAME}.models は重複なしのmodel id昇順である必要があります。`);
  }
  if (
    !Array.isArray(candidateSetDocument.models) ||
    !structurallyEqual(root.models, candidateSetDocument.models)
  ) {
    throw new Error(`${PLAN_NAME}.models がaggregate candidate-setと一致しません。`);
  }

  if (!Array.isArray(root.groups) || root.groups.length !== EXPECTED_GROUP_COUNT) {
    throw new Error(`${PLAN_NAME}.groups は${EXPECTED_GROUP_COUNT}件である必要があります。`);
  }
  const modelSet = new Set(models);
  const planGroups = root.groups.map((value, index) => {
    const label = `${PLAN_NAME}.groups[${index}]`;
    const group = exactObject(value, PLAN_GROUP_KEYS, label);
    const identity = identityFrom(group, label);
    if (!modelSet.has(identity.model)) {
      throw new Error(`${label} が未知のmodelを参照しています。`);
    }
    const legacy = exactObject(group.legacy, PLAN_LEGACY_KEYS, `${label}.legacy`);
    return {
      ...identity,
      publicPath: canonicalBundlePath(legacy.path, `${label}.legacy.path`),
      legacySha256: sha(legacy.sha256, `${label}.legacy.sha256`),
    };
  });
  assertSortedUniqueGroups(planGroups, `${PLAN_NAME}.groups`);
  if (
    planGroups.some((group, index) => {
      const reference = references[index];
      return (
        !reference ||
        groupKey(group) !== groupKey(reference) ||
        group.publicPath !== reference.public_path ||
        group.legacySha256 !== reference.legacy_sha256
      );
    })
  ) {
    throw new Error(
      `${PLAN_NAME}.groups がbaseline-referenceのgroup/legacy bindingと一致しません。`,
    );
  }

  if (!Array.isArray(root.excluded_failures)) {
    throw new Error(`${PLAN_NAME}.excluded_failures は配列である必要があります。`);
  }
  const excluded = root.excluded_failures.map((value, index) => {
    const label = `${PLAN_NAME}.excluded_failures[${index}]`;
    const failure = exactObject(value, [...GROUP_KEYS, "reason"], label);
    const identity = identityFrom(failure, label);
    if (!modelSet.has(identity.model)) {
      throw new Error(`${label} が未知のmodelを参照しています。`);
    }
    nonEmptyString(failure.reason, `${label}.reason`);
    return identity;
  });
  assertSortedUniqueGroups(excluded, `${PLAN_NAME}.excluded_failures`);
  const planKeys = new Set(planGroups.map(groupKey));
  if (excluded.some((failure) => planKeys.has(groupKey(failure)))) {
    throw new Error(`${PLAN_NAME} のgroupsとexcluded_failuresが競合しています。`);
  }
}

function assertExactDirectoryCoverage(
  files: ReadonlyMap<string, DirectoryFile>,
  expected: ReadonlySet<string>,
): void {
  for (const path of expected) {
    if (!files.has(path)) {
      throw new Error(`${INVENTORY_NAME} が要求するfileがありません: ${path}`);
    }
  }
  for (const path of files.keys()) {
    if (!expected.has(path)) {
      throw new Error(`${INVENTORY_NAME} にない余分なfileがあります: ${path}`);
    }
  }
}

async function readBytes(
  files: ReadonlyMap<string, DirectoryFile>,
  path: string,
): Promise<ArrayBuffer> {
  const file = files.get(path);
  if (!file) throw new Error(`必須証拠ファイルがありません: ${path}`);
  return file.arrayBuffer();
}

function decodeJson(bytes: ArrayBuffer, label: string): unknown {
  let source: string;
  try {
    source = new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(bytes));
  } catch {
    throw new Error(`${label} はUTF-8として読めません。`);
  }
  try {
    return JSON.parse(source);
  } catch {
    throw new Error(`${label} は有効なJSONではありません。`);
  }
}

function parseInventoryMarker(bytes: ArrayBuffer): string {
  let source: string;
  try {
    source = new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(bytes));
  } catch {
    throw new Error(`${INVENTORY_MARKER_NAME} はUTF-8として読めません。`);
  }
  if (!/^[0-9a-f]{64}\n$/.test(source)) {
    throw new Error(`${INVENTORY_MARKER_NAME} は小文字64桁SHAと末尾改行が必要です。`);
  }
  return source.slice(0, -1);
}

function parseRawMarker(bytes: ArrayBuffer, label: string): string {
  let source: string;
  try {
    source = new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(bytes));
  } catch {
    throw new Error(`${label} はUTF-8として読めません。`);
  }
  if (!HEX_64.test(source)) {
    throw new Error(`${label} は改行なし小文字64桁SHAである必要があります。`);
  }
  return source;
}

function identityFrom(record: Readonly<Record<string, unknown>>, label: string): GroupIdentity {
  return {
    model: pathSegment(record.model, `${label}.model`),
    scenario: pathSegment(record.scenario, `${label}.scenario`),
    line: pathSegment(record.line, `${label}.line`),
    variant: pathSegment(record.variant, `${label}.variant`),
  };
}

function assertSortedUniqueGroups(groups: readonly GroupIdentity[], label: string): void {
  for (let index = 1; index < groups.length; index += 1) {
    if (compareGroupTuple(groups[index - 1], groups[index]) >= 0) {
      throw new Error(`${label} は重複なしのgroup tuple昇順である必要があります。`);
    }
  }
}

function canonicalBundlePath(value: unknown, label: string): string {
  const path = nonEmptyString(value, label);
  if (!isSafeClipPath(path)) {
    throw new Error(`${label} はcanonical POSIX relative pathである必要があります。`);
  }
  return path;
}

function exactObject(
  value: unknown,
  expectedKeys: readonly string[],
  label: string,
): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error(`${label} はオブジェクトである必要があります。`);
  }
  const actual = Object.keys(value).sort();
  const expected = [...expectedKeys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${label} のキー構成が不正です。`);
  }
  return value;
}

function pathSegment(value: unknown, label: string): string {
  const result = nonEmptyString(value, label);
  if (result === "." || result === ".." || result.includes("/") || result.includes("\\")) {
    throw new Error(`${label} は安全なpath segmentである必要があります。`);
  }
  return result;
}

function nonEmptyString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} は空でない文字列である必要があります。`);
  }
  return value;
}

function sha(value: unknown, label: string): string {
  if (typeof value !== "string" || !HEX_64.test(value)) {
    throw new Error(`${label} は小文字64桁SHAである必要があります。`);
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function structurallyEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) && Array.isArray(right)) {
    return (
      left.length === right.length &&
      left.every((value, index) => structurallyEqual(value, right[index]))
    );
  }
  if (isRecord(left) && isRecord(right)) {
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return (
      leftKeys.length === rightKeys.length &&
      leftKeys.every(
        (key, index) => key === rightKeys[index] && structurallyEqual(left[key], right[key]),
      )
    );
  }
  return false;
}
