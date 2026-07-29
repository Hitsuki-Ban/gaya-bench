import { sha256Hex, sha256Text } from "@/lib/sha256";
import { assertCanonicalJsonBytes } from "@/lib/canonical-json";
import type { DirectoryFile, ObjectUrlFactory } from "@/lib/local-directory";
import type { PilotCandidatePresentation, PilotCatalog, PilotCatalogGroup } from "@/pilot/types";

const PILOT_SET_NAME = "pilot-set.json";
const SHA_PATTERN = /^[0-9a-f]{64}$/;
const MODELS = ["qwen3-tts-12hz-1.7b", "irodori-tts-600m-v3-voicedesign", "voxcpm2"] as const;
const SCENARIOS = ["battlefield-camp", "dungeon-entrance"] as const;
const FEATURE_NAMES = [
  "duration_sec",
  "mora_per_second",
  "pause_sec",
  "voiced_ratio",
  "f0_semitone_std",
  "energy_median_dbfs",
] as const;
const FEATURE_SPECS = [
  ["duration_sec", "content.prosody.duration_sec"],
  ["mora_per_second", "content.prosody.active_mora_per_sec"],
  ["pause_sec", "content.prosody.pause.internal_total_sec"],
  ["voiced_ratio", "content.prosody.f0.voiced_ratio"],
  ["f0_semitone_std", "content.prosody.f0.semitone_std"],
  ["energy_median_dbfs", "content.prosody.energy.median_dbfs"],
] as const;
const ACTIVE_SPEECH_REASON = "active_speech_sec が 0 または不正です。";
const ROOT_KEYS = [
  "format_version",
  "protocol",
  "generated_at",
  "design",
  "lines",
  "groups",
  "candidates",
];
const DESIGN_KEYS = [
  "models",
  "scenarios",
  "line_count",
  "takes_per_group",
  "seed_base",
  "feature_specs",
];
const FEATURE_SPEC_KEYS = ["name", "source"];
const LINE_KEYS = ["scenario", "line", "scenario_title", "text", "reading", "delivery"];
const GROUP_KEYS = ["group_id", "model", "scenario", "line", "variant", "candidate_ids"];
const CANDIDATE_KEYS = [
  "candidate_id",
  "model",
  "scenario",
  "line",
  "variant",
  "take_index",
  "take_id",
  "status",
  "gates",
  "features",
  "audio",
];
const GATE_KEYS = [
  "mechanical",
  "content",
  "policy_version",
  "primary_reject_rule",
  "reject_reason",
];
const AUDIO_KEYS = ["path", "sha256"];

type ModelId = (typeof MODELS)[number];
type ScenarioId = (typeof SCENARIOS)[number];
interface PilotLine {
  readonly scenario: ScenarioId;
  readonly line: string;
  readonly scenario_title: string;
  readonly text: string;
  readonly reading: string;
  readonly delivery: string;
}

interface PilotGroup {
  readonly group_id: string;
  readonly model: ModelId;
  readonly scenario: ScenarioId;
  readonly line: string;
  readonly variant: string;
  readonly candidate_ids: readonly string[];
}

interface PilotCandidate {
  readonly candidate_id: string;
  readonly model: ModelId;
  readonly scenario: ScenarioId;
  readonly line: string;
  readonly variant: string;
  readonly take_index: number;
  readonly take_id: string;
  readonly audio: {
    readonly path: string;
    readonly sha256: string;
  };
}

interface PilotSet {
  readonly lines: readonly PilotLine[];
  readonly groups: readonly PilotGroup[];
  readonly candidates: readonly PilotCandidate[];
}

export async function loadPilotCatalog(
  selectedFiles: Iterable<DirectoryFile>,
  objectUrls: ObjectUrlFactory = browserObjectUrls,
): Promise<PilotCatalog> {
  const files = indexDirectoryFiles(selectedFiles);
  const pilotSetFile = requiredFile(files, PILOT_SET_NAME);
  const pilotSetBytes = await pilotSetFile.arrayBuffer();
  assertCanonicalJsonBytes(pilotSetBytes, PILOT_SET_NAME);
  const pilotSetSha256 = await sha256Hex(pilotSetBytes);
  const pilotSet = parsePilotSet(pilotSetBytes);
  await assertOpaqueIdentities(pilotSet);

  const expectedAudioPaths = new Set(pilotSet.candidates.map((candidate) => candidate.audio.path));
  const expectedPaths = new Set([PILOT_SET_NAME, ...expectedAudioPaths]);
  const actualPaths = [...files.keys()];
  if (
    actualPaths.length !== expectedPaths.size ||
    actualPaths.some((path) => !expectedPaths.has(path))
  ) {
    throw new Error("pilot bundle の file 集合が pilot-set と一致しません。");
  }

  const audioFiles = new Map<string, DirectoryFile>();
  await Promise.all(
    pilotSet.candidates.map(async (candidate) => {
      const file = requiredFile(files, candidate.audio.path);
      const actualSha256 = await sha256Hex(await file.arrayBuffer());
      if (actualSha256 !== candidate.audio.sha256) {
        throw new Error(
          `音声 SHA-256 が pilot-set と一致しません: ${candidate.audio.path} (expected ${candidate.audio.sha256}, actual ${actualSha256})`,
        );
      }
      audioFiles.set(candidate.candidate_id, file);
    }),
  );

  return createCatalog(pilotSet, pilotSetSha256, audioFiles, objectUrls);
}

function parsePilotSet(bytes: ArrayBuffer): PilotSet {
  const decoded = parseJson(bytes);
  const document = exactObject(decoded, ROOT_KEYS, "pilot-set");
  if (document.format_version !== 1) {
    throw new Error("pilot-set.format_version は 1 である必要があります。");
  }
  if (document.protocol !== "n3-pilot-v1") {
    throw new Error("pilot-set.protocol は n3-pilot-v1 である必要があります。");
  }
  nonEmptyText(document.generated_at, "pilot-set.generated_at");
  validateDesign(document.design);
  const lines = validateLines(document.lines);
  const candidates = validateCandidates(document.candidates, lines);
  const groups = validateGroups(document.groups, lines, candidates);
  validateCompleteDesign(lines, groups, candidates);
  return { lines, groups, candidates };
}

function validateDesign(value: unknown): void {
  const design = exactObject(value, DESIGN_KEYS, "pilot-set.design");
  exactStringTuple(design.models, MODELS, "pilot-set.design.models");
  exactStringTuple(design.scenarios, SCENARIOS, "pilot-set.design.scenarios");
  if (design.line_count !== 24) {
    throw new Error("pilot-set.design.line_count は 24 である必要があります。");
  }
  if (design.takes_per_group !== 3) {
    throw new Error("pilot-set.design.takes_per_group は 3 である必要があります。");
  }
  if (design.seed_base !== 103) {
    throw new Error("pilot-set.design.seed_base は 103 である必要があります。");
  }
  const specs = array(design.feature_specs, "pilot-set.design.feature_specs");
  if (specs.length !== FEATURE_NAMES.length) {
    throw new Error("pilot-set.design.feature_specs は 6 feature を含む必要があります。");
  }
  for (const [index, value] of specs.entries()) {
    const spec = exactObject(value, FEATURE_SPEC_KEYS, `pilot-set.design.feature_specs[${index}]`);
    const expected = FEATURE_SPECS[index]!;
    if (spec.name !== expected[0] || spec.source !== expected[1]) {
      throw new Error(
        `pilot-set.design.feature_specs[${index}] が固定 N3 protocol と一致しません。`,
      );
    }
  }
}

function validateLines(value: unknown): readonly PilotLine[] {
  const values = array(value, "pilot-set.lines");
  if (values.length !== 24) {
    throw new Error("pilot-set.lines は 24 件である必要があります。");
  }
  const keys = new Set<string>();
  const lines = values.map((value, index) => {
    const label = `pilot-set.lines[${index}]`;
    const line = exactObject(value, LINE_KEYS, label);
    const scenario = enumValue(line.scenario, SCENARIOS, `${label}.scenario`);
    const lineId = pathSegment(line.line, `${label}.line`);
    const key = lineKey({ scenario, line: lineId });
    if (keys.has(key)) {
      throw new Error(`pilot-set.lines の line が重複しています: ${key}`);
    }
    keys.add(key);
    return {
      scenario,
      line: lineId,
      scenario_title: nonEmptyText(line.scenario_title, `${label}.scenario_title`),
      text: nonEmptyText(line.text, `${label}.text`),
      reading: nonEmptyText(line.reading, `${label}.reading`),
      delivery: nonEmptyText(line.delivery, `${label}.delivery`),
    };
  });
  if (new Set(lines.map((line) => line.scenario)).size !== SCENARIOS.length) {
    throw new Error("pilot-set.lines の scenario coverage が不正です。");
  }
  if (lines.some((line, index) => index > 0 && compareLineTuple(lines[index - 1]!, line) >= 0)) {
    throw new Error("pilot-set.lines は scenario/line 順である必要があります。");
  }
  return lines;
}

function validateCandidates(
  value: unknown,
  lines: readonly PilotLine[],
): readonly PilotCandidate[] {
  const values = array(value, "pilot-set.candidates");
  if (values.length !== 216) {
    throw new Error("pilot-set.candidates は 216 件である必要があります。");
  }
  const lineKeys = new Set(lines.map(lineKey));
  const candidateIds = new Set<string>();
  const takeIds = new Set<string>();
  const candidates = values.map((value, index) => {
    const label = `pilot-set.candidates[${index}]`;
    const candidate = exactObject(value, CANDIDATE_KEYS, label);
    const candidateId = sha(candidate.candidate_id, `${label}.candidate_id`);
    if (candidateIds.has(candidateId)) {
      throw new Error(`pilot-set candidate_id が重複しています: ${candidateId}`);
    }
    candidateIds.add(candidateId);
    const model = enumValue(candidate.model, MODELS, `${label}.model`);
    const scenario = enumValue(candidate.scenario, SCENARIOS, `${label}.scenario`);
    const line = pathSegment(candidate.line, `${label}.line`);
    if (!lineKeys.has(lineKey({ scenario, line }))) {
      throw new Error(`${label} が未知の line を参照しています。`);
    }
    const variant = pathSegment(candidate.variant, `${label}.variant`);
    if (variant !== "dry") {
      throw new Error(`${label}.variant は dry である必要があります。`);
    }
    const takeIndex = positiveInteger(candidate.take_index, `${label}.take_index`);
    if (takeIndex > 3) {
      throw new Error(`${label}.take_index は 1..3 である必要があります。`);
    }
    const takeId = sha(candidate.take_id, `${label}.take_id`);
    if (takeIds.has(takeId)) {
      throw new Error(`pilot-set take_id が重複しています: ${takeId}`);
    }
    takeIds.add(takeId);
    validateGate(candidate.status, candidate.gates, label);
    validateFeatures(candidate.features, `${label}.features`);
    const audio = exactObject(candidate.audio, AUDIO_KEYS, `${label}.audio`);
    const expectedPath = `audio/${candidateId}.opus`;
    if (audio.path !== expectedPath) {
      throw new Error(`${label}.audio.path は ${expectedPath} である必要があります。`);
    }
    return {
      candidate_id: candidateId,
      model,
      scenario,
      line,
      variant,
      take_index: takeIndex,
      take_id: takeId,
      audio: {
        path: expectedPath,
        sha256: sha(audio.sha256, `${label}.audio.sha256`),
      },
    };
  });
  if (
    candidates.some(
      (candidate, index) =>
        index > 0 && compareCandidateTuple(candidates[index - 1]!, candidate) >= 0,
    )
  ) {
    throw new Error("pilot-set.candidates の順序が不正です。");
  }
  return candidates;
}

function validateGate(statusValue: unknown, value: unknown, candidateLabel: string): void {
  const status = enumValue(
    statusValue,
    ["eligible", "hard_rejected"] as const,
    `${candidateLabel}.status`,
  );
  const gate = exactObject(value, GATE_KEYS, `${candidateLabel}.gates`);
  const mechanical = enumValue(
    gate.mechanical,
    ["pass", "reject"] as const,
    `${candidateLabel}.gates.mechanical`,
  );
  const content = enumValue(
    gate.content,
    ["pass", "review_required", "reject", "not_run"] as const,
    `${candidateLabel}.gates.content`,
  );
  nonEmptyText(gate.policy_version, `${candidateLabel}.gates.policy_version`);
  const rule = gate.primary_reject_rule;
  const reason = gate.reject_reason;

  if (status === "eligible") {
    if (
      mechanical !== "pass" ||
      (content !== "pass" && content !== "review_required") ||
      rule !== null ||
      reason !== null
    ) {
      throw new Error(`${candidateLabel} eligible gate の状態が不正です。`);
    }
    return;
  }
  if (
    mechanical === "pass" &&
    content === "reject" &&
    rule === "explicit_reading_mismatch" &&
    reason === null
  ) {
    return;
  }
  if (
    mechanical === "reject" &&
    content === "not_run" &&
    (rule === "mechanical_audio" || rule === "active_speech_nonpositive") &&
    typeof reason === "string" &&
    reason.length > 0 &&
    (rule === "active_speech_nonpositive") === (reason === ACTIVE_SPEECH_REASON)
  ) {
    return;
  }
  throw new Error(`${candidateLabel} hard_rejected gate の状態が不正です。`);
}

function validateFeatures(value: unknown, label: string): void {
  const features = exactObject(value, FEATURE_NAMES, label);
  for (const name of FEATURE_NAMES) {
    const feature = features[name];
    if (feature !== null && (typeof feature !== "number" || !Number.isFinite(feature))) {
      throw new Error(`${label}.${name} は有限数または null である必要があります。`);
    }
  }
}

function validateGroups(
  value: unknown,
  lines: readonly PilotLine[],
  candidates: readonly PilotCandidate[],
): readonly PilotGroup[] {
  const values = array(value, "pilot-set.groups");
  if (values.length !== 72) {
    throw new Error("pilot-set.groups は 72 件である必要があります。");
  }
  const lineKeys = new Set(lines.map(lineKey));
  const candidatesById = new Map(
    candidates.map((candidate) => [candidate.candidate_id, candidate]),
  );
  const groupIds = new Set<string>();
  const groupSlots = new Set<string>();
  const referencedCandidateIds = new Set<string>();
  const groups = values.map((value, index) => {
    const label = `pilot-set.groups[${index}]`;
    const group = exactObject(value, GROUP_KEYS, label);
    const groupId = sha(group.group_id, `${label}.group_id`);
    if (groupIds.has(groupId)) {
      throw new Error(`pilot-set group_id が重複しています: ${groupId}`);
    }
    groupIds.add(groupId);
    const model = enumValue(group.model, MODELS, `${label}.model`);
    const scenario = enumValue(group.scenario, SCENARIOS, `${label}.scenario`);
    const line = pathSegment(group.line, `${label}.line`);
    if (!lineKeys.has(lineKey({ scenario, line }))) {
      throw new Error(`${label} が未知の line を参照しています。`);
    }
    const variant = pathSegment(group.variant, `${label}.variant`);
    if (variant !== "dry") {
      throw new Error(`${label}.variant は dry である必要があります。`);
    }
    const slot = JSON.stringify([model, scenario, line]);
    if (groupSlots.has(slot)) {
      throw new Error(`pilot-set group slot が重複しています: ${slot}`);
    }
    groupSlots.add(slot);
    const candidateIds = array(group.candidate_ids, `${label}.candidate_ids`);
    if (candidateIds.length !== 3) {
      throw new Error(`${label}.candidate_ids は 3 件である必要があります。`);
    }
    const takeIndexes = new Set<number>();
    const validatedIds = candidateIds.map((value, candidateIndex) => {
      const candidateId = sha(value, `${label}.candidate_ids[${candidateIndex}]`);
      if (referencedCandidateIds.has(candidateId)) {
        throw new Error(`candidate_id が複数 group から参照されています: ${candidateId}`);
      }
      referencedCandidateIds.add(candidateId);
      const candidate = candidatesById.get(candidateId);
      if (!candidate) {
        throw new Error(`${label} が未知の candidate_id を参照しています: ${candidateId}`);
      }
      if (
        candidate.model !== model ||
        candidate.scenario !== scenario ||
        candidate.line !== line ||
        candidate.variant !== variant
      ) {
        throw new Error(`${label} と candidate tuple が一致しません: ${candidateId}`);
      }
      if (takeIndexes.has(candidate.take_index)) {
        throw new Error(`${label} の take_index が重複しています: ${candidate.take_index}`);
      }
      takeIndexes.add(candidate.take_index);
      return candidateId;
    });
    if (
      validatedIds.some(
        (candidateId, candidateIndex) =>
          candidateIndex > 0 && validatedIds[candidateIndex - 1]! >= candidateId,
      )
    ) {
      throw new Error(`${label}.candidate_ids は candidate_id 順である必要があります。`);
    }
    return {
      group_id: groupId,
      model,
      scenario,
      line,
      variant,
      candidate_ids: validatedIds,
    };
  });
  if (groups.some((group, index) => index > 0 && groups[index - 1]!.group_id >= group.group_id)) {
    throw new Error("pilot-set.groups は opaque group_id 順である必要があります。");
  }
  return groups;
}

function validateCompleteDesign(
  lines: readonly PilotLine[],
  groups: readonly PilotGroup[],
  candidates: readonly PilotCandidate[],
): void {
  const expectedSlots = new Set(
    MODELS.flatMap((model) =>
      lines.map((line) => JSON.stringify([model, line.scenario, line.line])),
    ),
  );
  const actualSlots = new Set(
    groups.map((group) => JSON.stringify([group.model, group.scenario, group.line])),
  );
  if (
    actualSlots.size !== expectedSlots.size ||
    [...expectedSlots].some((slot) => !actualSlots.has(slot))
  ) {
    throw new Error("pilot-set.groups は model × line の完全な直積である必要があります。");
  }
  const referenced = new Set(groups.flatMap((group) => [...group.candidate_ids]));
  if (
    referenced.size !== candidates.length ||
    candidates.some((candidate) => !referenced.has(candidate.candidate_id))
  ) {
    throw new Error("pilot-set.groups は全 candidate をちょうど 1 回参照する必要があります。");
  }
}

async function assertOpaqueIdentities(pilotSet: PilotSet): Promise<void> {
  await Promise.all([
    ...pilotSet.candidates.map(async (candidate) => {
      const expected = await sha256Text(
        `{"protocol":"n3-pilot-v1","take_id":"${candidate.take_id}"}`,
      );
      if (candidate.candidate_id !== expected) {
        throw new Error(`pilot candidate_id が take_id と一致しません: ${candidate.candidate_id}`);
      }
    }),
    ...pilotSet.groups.map(async (group) => {
      const expected = await sha256Text(
        `{"line":"${group.line}","model":"${group.model}","protocol":"n3-pilot-v1","scenario":"${group.scenario}","variant":"${group.variant}"}`,
      );
      if (group.group_id !== expected) {
        throw new Error(`pilot group_id が identity と一致しません: ${group.group_id}`);
      }
    }),
  ]);
}

function createCatalog(
  pilotSet: PilotSet,
  pilotSetSha256: string,
  audioFiles: ReadonlyMap<string, DirectoryFile>,
  objectUrls: ObjectUrlFactory,
): PilotCatalog {
  const linesByKey = new Map(pilotSet.lines.map((line) => [lineKey(line), line]));
  const candidatesById = new Map(
    pilotSet.candidates.map((candidate) => [candidate.candidate_id, candidate]),
  );
  const urls: string[] = [];
  try {
    const groups = pilotSet.groups.map((group): PilotCatalogGroup => {
      const line = linesByKey.get(lineKey(group));
      if (!line) {
        throw new Error(`検証済み line を解決できません: ${lineKey(group)}`);
      }
      const candidates = group.candidate_ids.map(
        (candidateId, index): PilotCandidatePresentation => {
          const candidate = candidatesById.get(candidateId);
          const file = audioFiles.get(candidateId);
          if (!candidate || !file) {
            throw new Error(`検証済み candidate を解決できません: ${candidateId}`);
          }
          const url = objectUrls.create(file);
          urls.push(url);
          return {
            candidateId,
            label: blindLabel(index),
            audio: {
              key: `pilot:${pilotSetSha256}:${candidateId}`,
              url,
            },
          };
        },
      );
      return {
        groupId: group.group_id,
        presentation: {
          lineText: line.text,
          reading: line.reading,
          delivery: line.delivery,
          candidates,
        },
      };
    });
    let disposed = false;
    return {
      pilotSetSha256,
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
  } catch (error) {
    for (const url of urls) {
      objectUrls.revoke(url);
    }
    throw error;
  }
}

function indexDirectoryFiles(
  selectedFiles: Iterable<DirectoryFile>,
): ReadonlyMap<string, DirectoryFile> {
  const files = [...selectedFiles];
  if (files.length === 0) {
    throw new Error("pilot bundle のファイルが選択されていません。");
  }
  let root: string | null = null;
  const byPath = new Map<string, DirectoryFile>();
  for (const file of files) {
    const relativePath = file.webkitRelativePath;
    if (relativePath.length === 0 || relativePath.includes("\\")) {
      throw new Error(`webkitRelativePath が不正です: ${relativePath || file.name}`);
    }
    const segments = relativePath.split("/");
    if (
      segments.length < 2 ||
      segments.some((segment) => segment.length === 0 || segment === "." || segment === "..")
    ) {
      throw new Error(`webkitRelativePath が pilot bundle root を表していません: ${relativePath}`);
    }
    if (file.name !== segments.at(-1)) {
      throw new Error(`file name と webkitRelativePath が一致しません: ${relativePath}`);
    }
    if (root === null) {
      root = segments[0]!;
    } else if (segments[0] !== root) {
      throw new Error(`複数の pilot bundle root が選択されています: ${root}, ${segments[0]}`);
    }
    const pathWithinRoot = segments.slice(1).join("/");
    if (byPath.has(pathWithinRoot)) {
      throw new Error(`pilot bundle 内の path が重複しています: ${pathWithinRoot}`);
    }
    byPath.set(pathWithinRoot, file);
  }
  return byPath;
}

function requiredFile(files: ReadonlyMap<string, DirectoryFile>, path: string): DirectoryFile {
  const file = files.get(path);
  if (!file) {
    throw new Error(`pilot bundle に必須ファイルがありません: ${path}`);
  }
  return file;
}

function parseJson(bytes: ArrayBuffer): unknown {
  let source: string;
  try {
    source = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new Error("pilot-set.json は正しい UTF-8 ではありません。");
  }
  try {
    return JSON.parse(source) as unknown;
  } catch {
    throw new Error("pilot-set.json を JSON として解析できません。");
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

function array(value: unknown, label: string): readonly unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} は配列である必要があります。`);
  }
  return value;
}

function exactStringTuple(value: unknown, expected: readonly string[], label: string): void {
  const actual = array(value, label);
  if (actual.length !== expected.length || actual.some((item, index) => item !== expected[index])) {
    throw new Error(`${label} が固定値と一致しません。`);
  }
}

function nonEmptyText(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} は空でない文字列である必要があります。`);
  }
  return value;
}

function pathSegment(value: unknown, label: string): string {
  const segment = nonEmptyText(value, label);
  if (!/^[a-z0-9][a-z0-9._-]*$/.test(segment)) {
    throw new Error(`${label} は安全な path segment である必要があります。`);
  }
  return segment;
}

function sha(value: unknown, label: string): string {
  if (typeof value !== "string" || !SHA_PATTERN.test(value)) {
    throw new Error(`${label} は完全な小文字 SHA-256 である必要があります。`);
  }
  return value;
}

function positiveInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new Error(`${label} は正の安全な整数である必要があります。`);
  }
  return value;
}

function enumValue<T extends string>(value: unknown, allowed: readonly T[], label: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    throw new Error(`${label} が許可された値ではありません。`);
  }
  return value as T;
}

function lineKey(value: { readonly scenario: string; readonly line: string }): string {
  return JSON.stringify([value.scenario, value.line]);
}

function compareLineTuple(left: PilotLine, right: PilotLine): number {
  return compareText(left.scenario, right.scenario) || compareText(left.line, right.line);
}

function compareCandidateTuple(left: PilotCandidate, right: PilotCandidate): number {
  return (
    compareText(left.model, right.model) ||
    compareText(left.scenario, right.scenario) ||
    compareText(left.line, right.line) ||
    compareText(left.variant, right.variant) ||
    left.take_index - right.take_index
  );
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function blindLabel(index: number): "A" | "B" | "C" {
  const labels = ["A", "B", "C"] as const;
  const label = labels[index];
  if (!label) {
    throw new Error(`blind candidate index が範囲外です: ${index}`);
  }
  return label;
}

const browserObjectUrls: ObjectUrlFactory = {
  create(file) {
    return URL.createObjectURL(file as unknown as Blob);
  },
  revoke(url) {
    URL.revokeObjectURL(url);
  },
};
