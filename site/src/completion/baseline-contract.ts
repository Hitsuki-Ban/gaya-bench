import { canonicalJson, assertCanonicalJsonBytes } from "@/lib/canonical-json";
import type { DirectoryFile, ObjectUrlFactory } from "@/lib/local-directory";
import { sha256Hex, sha256Text } from "@/lib/sha256";
import { loadCurateCatalog } from "@/curate/catalog";

import {
  baselineGroupKey,
  compareBaselineGroups,
  type BaselineCatalog,
  type BaselineExportCandidate,
  type BaselineGate,
  type BaselineGroup,
} from "./baseline-types";

export const BASELINE_PLAN_MARKER = "completion-plan.sha256";
export const BASELINE_SOURCE_MAP_FILE = "phase-b-source-map-v1.json";
export const BASELINE_SOURCE_MAP_MARKER = "phase-b-source-map-v1.sha256";
export const BASELINE_GROUP_COUNT = 363;

const SOURCE_MAP_ROOT_KEYS = [
  "format_version",
  "protocol",
  "plan_sha256",
  "anchor_selection_sha256",
  "candidate_set_sha256",
  "groups",
] as const;
const SOURCE_MAP_GROUP_KEYS = [
  "model",
  "scenario",
  "line",
  "variant",
  "role_epoch_sha256",
  "source_run_id",
] as const;
const REQUIRED_ROOT_FILES = [
  "manifest-v4.json",
  "candidate-set.json",
  "candidate-set.sha256",
  BASELINE_PLAN_MARKER,
  BASELINE_SOURCE_MAP_FILE,
  BASELINE_SOURCE_MAP_MARKER,
] as const;

interface BaselineSourceGroup {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly role_epoch_sha256: string;
  readonly source_run_id: string;
}

export interface BaselineSourceMap {
  readonly format_version: 1;
  readonly protocol: "phase-b-source-map-v1";
  readonly plan_sha256: string;
  readonly anchor_selection_sha256: string;
  readonly candidate_set_sha256: string;
  readonly groups: readonly BaselineSourceGroup[];
}

export async function loadBaselineCatalog(
  selectedFiles: readonly DirectoryFile[],
  objectUrls?: ObjectUrlFactory,
): Promise<BaselineCatalog> {
  const files = indexDirectoryFiles(selectedFiles);
  const sourceMapBytes = await requiredFile(files, BASELINE_SOURCE_MAP_FILE).arrayBuffer();
  const sourceMapMarkerBytes = await requiredFile(files, BASELINE_SOURCE_MAP_MARKER).arrayBuffer();
  const planMarkerBytes = await requiredFile(files, BASELINE_PLAN_MARKER).arrayBuffer();
  assertCanonicalJsonBytes(sourceMapBytes, BASELINE_SOURCE_MAP_FILE);
  const sourceMap = validateBaselineSourceMap(parseJson(sourceMapBytes, BASELINE_SOURCE_MAP_FILE));
  const sourceMapSha256 = await sha256Hex(sourceMapBytes);
  if (parseShaMarker(sourceMapMarkerBytes, BASELINE_SOURCE_MAP_MARKER) !== sourceMapSha256) {
    throw new Error(`${BASELINE_SOURCE_MAP_MARKER} がsource map SHA-256と一致しません。`);
  }
  if (parseShaMarker(planMarkerBytes, BASELINE_PLAN_MARKER) !== sourceMap.plan_sha256) {
    throw new Error(`${BASELINE_PLAN_MARKER} がsource mapのfrozen plan SHA-256と一致しません。`);
  }

  const curateCatalog =
    objectUrls === undefined
      ? await loadCurateCatalog(selectedFiles)
      : await loadCurateCatalog(selectedFiles, objectUrls);
  try {
    if (curateCatalog.manifestCurationCount !== 0 || curateCatalog.manifestFailureCount !== 0) {
      throw new Error("Phase B listening manifest は curations 0 / failures 0 が必要です。");
    }
    if (curateCatalog.candidateSetSha256 !== sourceMap.candidate_set_sha256) {
      throw new Error("source mapのcandidate-set SHA-256がlistening bundleと一致しません。");
    }
    if (curateCatalog.groups.length !== BASELINE_GROUP_COUNT) {
      throw new Error(
        `Phase B listening bundle は${BASELINE_GROUP_COUNT} groupが必要です: ${curateCatalog.groups.length}`,
      );
    }

    const sourceByGroup = new Map(
      sourceMap.groups.map((group) => [baselineGroupKey(group), group]),
    );
    const groups = await Promise.all(
      curateCatalog.groups.map(async (group): Promise<BaselineGroup> => {
        const key = baselineGroupKey(group);
        const source = sourceByGroup.get(key);
        if (!source) {
          throw new Error(`source mapにlistening groupがありません: ${key}`);
        }
        const rawExportCandidates = curateCatalog.exportCandidatesByGroup.get(key);
        if (!rawExportCandidates || rawExportCandidates.length < 3) {
          throw new Error(`Phase B groupはmechanical-pass candidateが3件以上必要です: ${key}`);
        }
        const exportCandidates: BaselineExportCandidate[] = rawExportCandidates.map(
          (candidate, index) => ({
            takeId: candidate.takeId,
            path: nonEmptyText(candidate.path, `${key}.candidates[${index}].path`),
            audioSha256: sha(candidate.audioSha256, `${key}.candidates[${index}].audio_sha256`),
            gate: validateGate(candidate.gate, `${key}.candidates[${index}].gate`),
          }),
        );
        const groupSha256 = await sha256Text(
          canonicalJson(
            {
              model: group.model,
              scenario: group.scenario,
              line: group.line,
              variant: group.variant,
              scenario_title: group.scenarioTitle,
              text: group.lineText,
              delivery: group.delivery,
              role_epoch_sha256: source.role_epoch_sha256,
              source_run_id: source.source_run_id,
              candidates: exportCandidates.map((candidate) => ({
                take_id: candidate.takeId,
                path: candidate.path,
                audio_sha256: candidate.audioSha256,
                gate: candidate.gate,
              })),
            },
            "Phase B group identity",
          ),
        );
        return {
          ...group,
          roleEpochSha256: source.role_epoch_sha256,
          sourceRunId: source.source_run_id,
          groupSha256,
          exportCandidates,
        };
      }),
    );
    groups.sort(compareBaselineGroups);
    if (
      groups.length !== sourceMap.groups.length ||
      groups.some(
        (group, index) => baselineGroupKey(group) !== baselineGroupKey(sourceMap.groups[index]!),
      )
    ) {
      throw new Error("source mapとlistening catalogのgroup集合・canonical順が一致しません。");
    }
    assertExactBundleFiles(files, groups);

    return {
      planSha256: sourceMap.plan_sha256,
      anchorSelectionSha256: sourceMap.anchor_selection_sha256,
      candidateSetSha256: sourceMap.candidate_set_sha256,
      groups,
      dispose: () => curateCatalog.dispose(),
    };
  } catch (reason: unknown) {
    curateCatalog.dispose();
    throw reason;
  }
}

export function validateBaselineSourceMap(value: unknown): BaselineSourceMap {
  const root = exactObject(value, SOURCE_MAP_ROOT_KEYS, "Phase B source map");
  if (root.format_version !== 1) {
    throw new Error("Phase B source map.format_version は 1 が必要です。");
  }
  if (root.protocol !== "phase-b-source-map-v1") {
    throw new Error("Phase B source map.protocol は phase-b-source-map-v1 が必要です。");
  }
  const planSha256 = sha(root.plan_sha256, "Phase B source map.plan_sha256");
  const anchorSelectionSha256 = sha(
    root.anchor_selection_sha256,
    "Phase B source map.anchor_selection_sha256",
  );
  const candidateSetSha256 = sha(
    root.candidate_set_sha256,
    "Phase B source map.candidate_set_sha256",
  );
  if (!Array.isArray(root.groups) || root.groups.length !== BASELINE_GROUP_COUNT) {
    throw new Error(`Phase B source map.groups はexactly ${BASELINE_GROUP_COUNT}件が必要です。`);
  }
  const seen = new Set<string>();
  const groups = root.groups.map((value, index): BaselineSourceGroup => {
    const field = `Phase B source map.groups[${index}]`;
    const group = exactObject(value, SOURCE_MAP_GROUP_KEYS, field);
    const normalized = {
      model: pathSegment(group.model, `${field}.model`),
      scenario: pathSegment(group.scenario, `${field}.scenario`),
      line: pathSegment(group.line, `${field}.line`),
      variant: pathSegment(group.variant, `${field}.variant`),
      role_epoch_sha256: sha(group.role_epoch_sha256, `${field}.role_epoch_sha256`),
      source_run_id: pathSegment(group.source_run_id, `${field}.source_run_id`),
    };
    const key = baselineGroupKey(normalized);
    if (seen.has(key)) {
      throw new Error(`Phase B source map groupが重複しています: ${key}`);
    }
    seen.add(key);
    return normalized;
  });
  const sorted = [...groups].sort(compareBaselineGroups);
  if (groups.some((group, index) => baselineGroupKey(group) !== baselineGroupKey(sorted[index]!))) {
    throw new Error("Phase B source map.groups はcanonical順が必要です。");
  }
  return {
    format_version: 1,
    protocol: "phase-b-source-map-v1",
    plan_sha256: planSha256,
    anchor_selection_sha256: anchorSelectionSha256,
    candidate_set_sha256: candidateSetSha256,
    groups,
  };
}

function validateGate(value: unknown, label: string): BaselineGate {
  const gate = exactObject(value, ["mechanical", "content", "policy_version"], label);
  if (gate.mechanical !== "pass") {
    throw new Error(`${label}.mechanical は pass が必要です。`);
  }
  if (gate.content !== "pass" && gate.content !== "review_required") {
    throw new Error(`${label}.content は pass または review_required が必要です。`);
  }
  if (gate.policy_version !== "take-gates-v2") {
    throw new Error(`${label}.policy_version は take-gates-v2 が必要です。`);
  }
  return {
    mechanical: "pass",
    content: gate.content,
    policy_version: "take-gates-v2",
  };
}

function assertExactBundleFiles(
  files: ReadonlyMap<string, DirectoryFile>,
  groups: readonly BaselineGroup[],
): void {
  const expected = new Set<string>(REQUIRED_ROOT_FILES);
  for (const group of groups) {
    for (const candidate of group.exportCandidates) {
      expected.add(localAudioPath(group, candidate));
    }
  }
  const actualPaths = [...files.keys()].sort(compareText);
  const expectedPaths = [...expected].sort(compareText);
  if (
    actualPaths.length !== expectedPaths.length ||
    actualPaths.some((path, index) => path !== expectedPaths[index])
  ) {
    throw new Error(
      `Phase B bundle file setがexact contractと一致しません: expected=${expectedPaths.length}, actual=${actualPaths.length}`,
    );
  }
}

function localAudioPath(group: BaselineGroup, candidate: BaselineExportCandidate): string {
  const prefix = `audio/takes/${group.model}/${group.scenario}/${group.line}/${group.variant}/`;
  const suffix = `-${candidate.audioSha256}.opus`;
  if (!candidate.path.startsWith(prefix) || !candidate.path.endsWith(suffix)) {
    throw new Error(`candidate pathをPhase B local audioへ解決できません: ${candidate.path}`);
  }
  const sourceName = candidate.path.slice(prefix.length, -suffix.length);
  if (!/^take-[0-9]{4}$/.test(sourceName)) {
    throw new Error(`candidate pathのtake indexが不正です: ${candidate.path}`);
  }
  return `audio/${group.model}/${group.scenario}/${group.line}/${group.variant}/${sourceName}.opus`;
}

function indexDirectoryFiles(
  selectedFiles: readonly DirectoryFile[],
): ReadonlyMap<string, DirectoryFile> {
  if (selectedFiles.length === 0) {
    throw new Error("Phase B listening bundle folderが空です。");
  }
  const result = new Map<string, DirectoryFile>();
  let root: string | null = null;
  for (const file of selectedFiles) {
    const normalized = file.webkitRelativePath.replaceAll("\\", "/");
    const parts = normalized.split("/");
    if (
      parts.length < 2 ||
      parts.some((part) => part.length === 0 || part === "." || part === "..")
    ) {
      throw new Error(`Phase B bundle file pathが不正です: ${file.webkitRelativePath}`);
    }
    if (root === null) {
      root = parts[0]!;
    } else if (root !== parts[0]) {
      throw new Error("複数のPhase B bundle rootが混在しています。");
    }
    const relative = parts.slice(1).join("/");
    if (result.has(relative)) {
      throw new Error(`Phase B bundle file pathが重複しています: ${relative}`);
    }
    result.set(relative, file);
  }
  return result;
}

function requiredFile(files: ReadonlyMap<string, DirectoryFile>, path: string): DirectoryFile {
  const file = files.get(path);
  if (!file) {
    throw new Error(`Phase B bundleに${path}がありません。`);
  }
  return file;
}

function parseJson(bytes: ArrayBuffer, label: string): unknown {
  const source = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  try {
    return JSON.parse(source);
  } catch {
    throw new Error(`${label} は正しいJSONが必要です。`);
  }
}

function parseShaMarker(bytes: ArrayBuffer, label: string): string {
  if (bytes.byteLength !== 64) {
    throw new Error(`${label} は改行なし64-byte SHA-256 ASCIIが必要です。`);
  }
  const value = new TextDecoder("ascii", { fatal: true }).decode(bytes);
  return sha(value, label);
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

function pathSegment(value: unknown, label: string): string {
  const text = nonEmptyText(value, label);
  if (text === "." || text === ".." || text.includes("/") || text.includes("\\")) {
    throw new Error(`${label} は安全なpath segmentが必要です。`);
  }
  return text;
}

function nonEmptyText(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} は空でない文字列が必要です。`);
  }
  return value;
}

function sha(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${label} は完全な小文字SHA-256が必要です。`);
  }
  return value;
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
