import { sha256Hex, sha256Text } from "@/lib/sha256";
import type { DirectoryFile, ObjectUrlFactory } from "@/lib/local-directory";
import {
  compareGroupTuple,
  groupKey,
  type CurateCandidatePresentation,
  type CurateCatalog,
  type CurateGroup,
  type ExportCandidate,
  type GateContent,
} from "./types";

const MANIFEST_NAME = "manifest-v4.json";
const CANDIDATE_SET_NAME = "candidate-set.json";
const CANDIDATE_SET_SHA_NAME = "candidate-set.sha256";
const SHA_PATTERN = /^[0-9a-f]{64}$/;
const ROOT_KEYS = [
  "format_version",
  "generated_at",
  "candidate_set_sha256",
  "models",
  "candidates",
  "curations",
  "failures",
];
const CANDIDATE_SET_KEYS = [
  "format_version",
  "scenario_sha256",
  "lines",
  "models",
  "candidates",
  "failures",
];
const LINE_KEYS = ["scenario", "line", "scenario_title", "text", "delivery"];
const MODEL_KEYS = ["id", "name", "version", "license_note", "capabilities"];
const MODEL_OPTIONAL_KEYS = ["conditioning"];
const CONDITIONING_KEYS = ["mode", "base_model"];
const CONDITIONING_MODES = ["human-reference", "text-only"];
const CAPABILITY_KEYS = ["emotion", "voice_prompt", "clone", "nonverbal", "reading"];
const CANDIDATE_KEYS = [
  "model",
  "scenario",
  "line",
  "variant",
  "take_index",
  "take_id",
  "path",
  "duration_sec",
  "sha256",
  "generation_input_sha256",
  "gen_params",
  "rtf",
  "loudness",
  "gate",
];
const GROUP_KEYS = ["model", "scenario", "line", "variant"] as const;

interface V4Model {
  readonly id: string;
  readonly name: string;
  readonly version: string;
  readonly license_note: string;
  readonly capabilities: Readonly<Record<string, boolean>>;
  /** 条件バリアント列 (#201) のみ持つ optional field。 */
  readonly conditioning?: {
    readonly mode: string;
    readonly base_model: string;
  };
}

interface V4Candidate {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly take_index: number;
  readonly take_id: string;
  readonly path: string;
  readonly duration_sec: number;
  readonly sha256: string;
  readonly generation_input_sha256: string;
  readonly gen_params: Readonly<Record<string, unknown>>;
  readonly rtf: number;
  readonly loudness: Readonly<Record<string, unknown>>;
  readonly gate: {
    readonly mechanical: "pass";
    readonly content: GateContent;
    readonly policy_version: string;
  };
}

interface V4Failure {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly reason: "no_eligible_take" | "test_only_adapter";
}

interface V4Line {
  readonly scenario: string;
  readonly line: string;
  readonly scenario_title: string;
  readonly text: string;
  readonly delivery: string;
}

interface CandidateSet {
  readonly format_version: 4;
  readonly scenario_sha256: string;
  readonly lines: readonly V4Line[];
  readonly models: readonly V4Model[];
  readonly candidates: readonly V4Candidate[];
  readonly failures: readonly V4Failure[];
}

interface ManifestV4 {
  readonly format_version: 4;
  readonly generated_at: string;
  readonly candidate_set_sha256: string;
  readonly models: readonly V4Model[];
  readonly candidates: readonly V4Candidate[];
  readonly curations: readonly unknown[];
  readonly failures: readonly V4Failure[];
}

export async function loadCurateCatalog(
  selectedFiles: Iterable<DirectoryFile>,
  objectUrls: ObjectUrlFactory = browserObjectUrls,
): Promise<CurateCatalog> {
  const files = indexDirectoryFiles(selectedFiles);
  const manifestFile = requiredFile(files, MANIFEST_NAME);
  const candidateSetFile = requiredFile(files, CANDIDATE_SET_NAME);
  const candidateSetShaFile = requiredFile(files, CANDIDATE_SET_SHA_NAME);
  const [manifestBytes, candidateSetBytes, candidateSetShaBytes] = await Promise.all([
    manifestFile.arrayBuffer(),
    candidateSetFile.arrayBuffer(),
    candidateSetShaFile.arrayBuffer(),
  ]);

  const candidateSetSha256 = await sha256Hex(candidateSetBytes);
  const markerSha256 = parseCandidateSetSha(candidateSetShaBytes);
  const manifest = parseManifest(manifestBytes);
  if (candidateSetSha256 !== markerSha256) {
    throw new Error(
      `candidate-set.json の SHA-256 が candidate-set.sha256 と一致しません: expected ${markerSha256}, actual ${candidateSetSha256}`,
    );
  }
  if (candidateSetSha256 !== manifest.candidate_set_sha256) {
    throw new Error(
      `candidate-set.json の SHA-256 が manifest.candidate_set_sha256 と一致しません: expected ${manifest.candidate_set_sha256}, actual ${candidateSetSha256}`,
    );
  }

  const candidateSet = parseCandidateSet(candidateSetBytes);
  assertCandidateSetMatchesManifest(candidateSet, manifest);
  await assertTakeIdentities(candidateSet.candidates);

  const audioFiles = new Map<V4Candidate, DirectoryFile>();
  await Promise.all(
    candidateSet.candidates.map(async (candidate) => {
      const localPath = localAudioPath(candidate);
      const file = requiredFile(files, localPath);
      const actualSha = await sha256Hex(await file.arrayBuffer());
      if (actualSha !== candidate.sha256) {
        throw new Error(
          `音声 SHA-256 が candidate と一致しません: ${localPath} (expected ${candidate.sha256}, actual ${actualSha})`,
        );
      }
      audioFiles.set(candidate, file);
    }),
  );

  return createCatalog(
    candidateSet,
    candidateSetSha256,
    manifest.curations.length,
    manifest.failures.length,
    audioFiles,
    objectUrls,
  );
}

function indexDirectoryFiles(
  selectedFiles: Iterable<DirectoryFile>,
): ReadonlyMap<string, DirectoryFile> {
  const files = [...selectedFiles];
  if (files.length === 0) {
    throw new Error("run root のファイルが選択されていません。");
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
      throw new Error(`webkitRelativePath が run root を表していません: ${relativePath}`);
    }
    if (file.name !== segments.at(-1)) {
      throw new Error(`file name と webkitRelativePath が一致しません: ${relativePath}`);
    }
    if (root === null) {
      root = segments[0]!;
    } else if (segments[0] !== root) {
      throw new Error(`複数の run root が選択されています: ${root}, ${segments[0]}`);
    }

    const pathWithinRoot = segments.slice(1).join("/");
    if (byPath.has(pathWithinRoot)) {
      throw new Error(`run root 内の path が重複しています: ${pathWithinRoot}`);
    }
    byPath.set(pathWithinRoot, file);
  }
  return byPath;
}

function requiredFile(files: ReadonlyMap<string, DirectoryFile>, path: string): DirectoryFile {
  const file = files.get(path);
  if (!file) {
    throw new Error(`run root に必須ファイルがありません: ${path}`);
  }
  return file;
}

function parseManifest(bytes: ArrayBuffer): ManifestV4 {
  const decoded = parseJson(bytes, MANIFEST_NAME);
  const manifest = exactObject(decoded, ROOT_KEYS, "manifest");
  if (manifest.format_version !== 4) {
    throw new Error("manifest.format_version は 4 である必要があります。");
  }
  nonEmptyText(manifest.generated_at, "manifest.generated_at");
  sha(manifest.candidate_set_sha256, "manifest.candidate_set_sha256");
  const models = validateModels(manifest.models, "manifest.models");
  const candidates = validateCandidates(manifest.candidates, "manifest.candidates", models);
  validateFailures(manifest.failures, "manifest.failures", models, candidates);
  validateCurations(manifest.curations, "manifest.curations", candidates);
  return manifest as unknown as ManifestV4;
}

function parseCandidateSet(bytes: ArrayBuffer): CandidateSet {
  const decoded = parseJson(bytes, CANDIDATE_SET_NAME);
  const document = exactObject(decoded, CANDIDATE_SET_KEYS, "candidate-set");
  if (document.format_version !== 4) {
    throw new Error("candidate-set.format_version は 4 である必要があります。");
  }
  sha(document.scenario_sha256, "candidate-set.scenario_sha256");
  const models = validateModels(document.models, "candidate-set.models");
  const candidates = validateCandidates(document.candidates, "candidate-set.candidates", models);
  const failures = validateFailures(
    document.failures,
    "candidate-set.failures",
    models,
    candidates,
  );
  validateLines(document.lines, "candidate-set.lines", candidates, failures);
  if (candidates.length === 0) {
    throw new Error("candidate-set に策展可能な candidate がありません。");
  }
  return document as unknown as CandidateSet;
}

function parseCandidateSetSha(bytes: ArrayBuffer): string {
  const raw = new Uint8Array(bytes);
  if (raw.length !== 64) {
    throw new Error(
      "candidate-set.sha256 は改行なしの小文字 SHA-256 ASCII 64 bytes である必要があります。",
    );
  }
  const value = String.fromCharCode(...raw);
  if (!SHA_PATTERN.test(value)) {
    throw new Error(
      "candidate-set.sha256 は改行なしの小文字 SHA-256 ASCII 64 bytes である必要があります。",
    );
  }
  return value;
}

function parseJson(bytes: ArrayBuffer, label: string): unknown {
  let source: string;
  try {
    source = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new Error(`${label} は正しい UTF-8 ではありません。`);
  }
  try {
    return JSON.parse(source) as unknown;
  } catch {
    throw new Error(`${label} を JSON として解析できません。`);
  }
}

function validateModels(value: unknown, label: string): readonly V4Model[] {
  const models = array(value, label);
  const ids = new Set<string>();
  for (const [index, item] of models.entries()) {
    const model = exactObject(item, MODEL_KEYS, `${label}[${index}]`, MODEL_OPTIONAL_KEYS);
    const id = pathSegment(model.id, `${label}[${index}].id`);
    if (model.conditioning !== undefined) {
      const conditioning = exactObject(
        model.conditioning,
        CONDITIONING_KEYS,
        `${label}[${index}].conditioning`,
      );
      const mode = conditioning.mode;
      if (typeof mode !== "string" || !CONDITIONING_MODES.includes(mode)) {
        throw new Error(`${label}[${index}].conditioning.mode が不正です: ${String(mode)}`);
      }
      pathSegment(conditioning.base_model, `${label}[${index}].conditioning.base_model`);
    }
    if (ids.has(id)) {
      throw new Error(`model id が重複しています: ${id}`);
    }
    ids.add(id);
    nonEmptyText(model.name, `${label}[${index}].name`);
    nonEmptyText(model.version, `${label}[${index}].version`);
    if (typeof model.license_note !== "string") {
      throw new Error(`${label}[${index}].license_note は文字列である必要があります。`);
    }
    const capabilities = exactObject(
      model.capabilities,
      CAPABILITY_KEYS,
      `${label}[${index}].capabilities`,
    );
    if (Object.values(capabilities).some((capability) => typeof capability !== "boolean")) {
      throw new Error(`${label}[${index}].capabilities は bool である必要があります。`);
    }
  }
  return models as unknown as readonly V4Model[];
}

function validateCandidates(
  value: unknown,
  label: string,
  models: readonly V4Model[],
): readonly V4Candidate[] {
  const candidates = array(value, label);
  const modelIds = new Set(models.map((model) => model.id));
  const takeIds = new Set<string>();
  const slots = new Set<string>();
  for (const [index, item] of candidates.entries()) {
    const field = `${label}[${index}]`;
    const candidate = exactObject(item, CANDIDATE_KEYS, field);
    const group = GROUP_KEYS.map((key) => pathSegment(candidate[key], `${field}.${key}`));
    if (!modelIds.has(group[0]!)) {
      throw new Error(`${field} が未知の model を参照しています: ${group[0]}`);
    }
    const takeIndex = positiveInteger(candidate.take_index, `${field}.take_index`);
    const takeId = sha(candidate.take_id, `${field}.take_id`);
    const audioSha = sha(candidate.sha256, `${field}.sha256`);
    sha(candidate.generation_input_sha256, `${field}.generation_input_sha256`);
    const slot = JSON.stringify([...group, takeIndex]);
    if (slots.has(slot)) {
      throw new Error(`同じ group の take_index が重複しています: ${slot}`);
    }
    if (takeIds.has(takeId)) {
      throw new Error(`take_id が重複しています: ${takeId}`);
    }
    slots.add(slot);
    takeIds.add(takeId);

    const expectedPath = `audio/takes/${group.join("/")}/take-${String(takeIndex).padStart(4, "0")}-${audioSha}.opus`;
    if (candidate.path !== expectedPath) {
      throw new Error(`${field}.path が v4 field から再構成できません。`);
    }
    nonNegativeFinite(candidate.duration_sec, `${field}.duration_sec`);
    nonNegativeFinite(candidate.rtf, `${field}.rtf`);
    validateGenParams(candidate.gen_params, `${field}.gen_params`);
    validateLoudness(candidate.loudness, `${field}.loudness`);
    validateGate(candidate.gate, `${field}.gate`);
  }
  return candidates as unknown as readonly V4Candidate[];
}

function validateGenParams(value: unknown, label: string): void {
  const params = exactObject(
    value,
    ["seed", "recipe_version", "sampling", "requested", "realized"],
    label,
  );
  if (
    params.seed !== null &&
    (typeof params.seed !== "number" || !Number.isSafeInteger(params.seed))
  ) {
    throw new Error(`${label}.seed は安全な整数または null である必要があります。`);
  }
  nonEmptyText(params.recipe_version, `${label}.recipe_version`);
  for (const key of ["sampling", "requested", "realized"] as const) {
    if (!isPlainObject(params[key])) {
      throw new Error(`${label}.${key} は object である必要があります。`);
    }
    assertJsonValue(params[key], `${label}.${key}`);
  }
}

function validateLoudness(value: unknown, label: string): void {
  const loudness = exactObject(value, ["source", "i_lufs", "tp_dbtp", "shortfall"], label);
  if (loudness.source !== "encoded_opus") {
    throw new Error(`${label}.source は encoded_opus である必要があります。`);
  }
  finiteNumber(loudness.i_lufs, `${label}.i_lufs`);
  finiteNumber(loudness.tp_dbtp, `${label}.tp_dbtp`);
  if (typeof loudness.shortfall !== "boolean") {
    throw new Error(`${label}.shortfall は bool である必要があります。`);
  }
}

function validateGate(value: unknown, label: string): void {
  const gate = exactObject(value, ["mechanical", "content", "policy_version"], label);
  if (
    gate.mechanical !== "pass" ||
    (gate.content !== "pass" && gate.content !== "review_required")
  ) {
    throw new Error(`${label} は eligible gate である必要があります。`);
  }
  nonEmptyText(gate.policy_version, `${label}.policy_version`);
}

function validateFailures(
  value: unknown,
  label: string,
  models: readonly V4Model[],
  candidates: readonly V4Candidate[],
): readonly V4Failure[] {
  const failures = array(value, label);
  const modelIds = new Set(models.map((model) => model.id));
  const candidateGroups = new Set(candidates.map(groupKey));
  const failureGroups = new Set<string>();
  for (const [index, item] of failures.entries()) {
    const field = `${label}[${index}]`;
    const failure = exactObject(item, [...GROUP_KEYS, "reason"], field);
    const group = {
      model: pathSegment(failure.model, `${field}.model`),
      scenario: pathSegment(failure.scenario, `${field}.scenario`),
      line: pathSegment(failure.line, `${field}.line`),
      variant: pathSegment(failure.variant, `${field}.variant`),
    };
    if (!modelIds.has(group.model)) {
      throw new Error(`${field} が未知の model を参照しています: ${group.model}`);
    }
    if (failure.reason !== "no_eligible_take" && failure.reason !== "test_only_adapter") {
      throw new Error(
        `${field}.reason は no_eligible_take または test_only_adapter である必要があります。`,
      );
    }
    if (failure.reason === "test_only_adapter" && group.model !== "dummy") {
      throw new Error(`${field}.reason=test_only_adapter は model=dummy が必要です。`);
    }
    const key = groupKey(group);
    if (failureGroups.has(key)) {
      throw new Error(`failure group が重複しています: ${key}`);
    }
    if (candidateGroups.has(key)) {
      throw new Error(`candidate と failure の group が競合しています: ${key}`);
    }
    failureGroups.add(key);
  }
  return failures as unknown as readonly V4Failure[];
}

function validateLines(
  value: unknown,
  label: string,
  candidates: readonly V4Candidate[],
  failures: readonly V4Failure[],
): readonly V4Line[] {
  const lines = array(value, label);
  const lineKeys = new Set<string>();
  const scenarioTitles = new Map<string, string>();
  let previous: { readonly scenario: string; readonly line: string } | null = null;

  for (const [index, item] of lines.entries()) {
    const field = `${label}[${index}]`;
    const line = exactObject(item, LINE_KEYS, field);
    const identity = {
      scenario: pathSegment(line.scenario, `${field}.scenario`),
      line: pathSegment(line.line, `${field}.line`),
    };
    if (previous) {
      const order = compareLineTuple(previous, identity);
      if (order === 0) {
        throw new Error(`${label} の scenario/line が重複しています: ${lineKey(identity)}`);
      }
      if (order > 0) {
        throw new Error(`${label} は (scenario, line) の昇順である必要があります。`);
      }
    }
    previous = identity;

    const scenarioTitle = nonEmptyText(line.scenario_title, `${field}.scenario_title`);
    const knownTitle = scenarioTitles.get(identity.scenario);
    if (knownTitle !== undefined && knownTitle !== scenarioTitle) {
      throw new Error(
        `${label} の同一 scenario で scenario_title が一致しません: ${identity.scenario}`,
      );
    }
    scenarioTitles.set(identity.scenario, scenarioTitle);
    nonEmptyText(line.text, `${field}.text`);
    nonEmptyText(line.delivery, `${field}.delivery`);
    lineKeys.add(lineKey(identity));
  }

  const referencedLineKeys = new Set(
    [...candidates, ...failures].map((artifact) => lineKey(artifact)),
  );
  for (const referenced of referencedLineKeys) {
    if (!lineKeys.has(referenced)) {
      throw new Error(`${label} に candidate/failure の参照行がありません: ${referenced}`);
    }
  }
  for (const present of lineKeys) {
    if (!referencedLineKeys.has(present)) {
      throw new Error(
        `${label} に candidate/failure から参照されない余分な行があります: ${present}`,
      );
    }
  }
  return lines as unknown as readonly V4Line[];
}

function validateCurations(
  value: unknown,
  label: string,
  candidates: readonly V4Candidate[],
): void {
  const curations = array(value, label);
  const candidateGroups = new Set(candidates.map(groupKey));
  const candidateGroupByTake = new Map(
    candidates.map((candidate) => [candidate.take_id, groupKey(candidate)]),
  );
  const groups = new Set<string>();
  for (const [index, item] of curations.entries()) {
    const field = `${label}[${index}]`;
    if (!isPlainObject(item)) {
      throw new Error(`${field} は object である必要があります。`);
    }
    const decision = item.decision;
    const keys =
      decision === "selected"
        ? [...GROUP_KEYS, "decision", "take_id", "curation_sha256"]
        : [...GROUP_KEYS, "decision", "curation_sha256"];
    const curation = exactObject(item, keys, field);
    if (decision !== "selected" && decision !== "skipped") {
      throw new Error(`${field}.decision が不正です。`);
    }
    const group = {
      model: pathSegment(curation.model, `${field}.model`),
      scenario: pathSegment(curation.scenario, `${field}.scenario`),
      line: pathSegment(curation.line, `${field}.line`),
      variant: pathSegment(curation.variant, `${field}.variant`),
    };
    const key = groupKey(group);
    if (groups.has(key)) {
      throw new Error(`curation group が重複しています: ${key}`);
    }
    groups.add(key);
    sha(curation.curation_sha256, `${field}.curation_sha256`);
    if (decision === "selected") {
      const takeId = sha(curation.take_id, `${field}.take_id`);
      if (candidateGroupByTake.get(takeId) !== key) {
        throw new Error(`${field} が同一 group の candidate を参照していません。`);
      }
    } else if (!candidateGroups.has(key)) {
      throw new Error(`${field} に対応する candidate group がありません。`);
    }
  }
}

function assertCandidateSetMatchesManifest(candidateSet: CandidateSet, manifest: ManifestV4): void {
  const candidateSubset = {
    format_version: candidateSet.format_version,
    models: candidateSet.models,
    candidates: candidateSet.candidates,
    failures: candidateSet.failures,
  };
  const manifestSubset = {
    format_version: manifest.format_version,
    models: manifest.models,
    candidates: manifest.candidates,
    failures: manifest.failures,
  };
  if (!structurallyEqual(candidateSubset, manifestSubset)) {
    throw new Error(
      "candidate-set.json の内容が manifest-v4.json の candidate subset と一致しません。",
    );
  }
}

async function assertTakeIdentities(candidates: readonly V4Candidate[]): Promise<void> {
  await Promise.all(
    candidates.map(async (candidate) => {
      const expected = await sha256Text(
        `{"final_opus_sha256":"${candidate.sha256}","generation_input_sha256":"${candidate.generation_input_sha256}"}`,
      );
      if (candidate.take_id !== expected) {
        throw new Error(
          `take_id が generation input と audio SHA-256 に一致しません: ${candidate.take_id}`,
        );
      }
    }),
  );
}

function localAudioPath(candidate: V4Candidate): string {
  return `audio/${candidate.model}/${candidate.scenario}/${candidate.line}/${candidate.variant}/take-${String(candidate.take_index).padStart(4, "0")}.opus`;
}

async function createCatalog(
  candidateSet: CandidateSet,
  candidateSetSha256: string,
  manifestCurationCount: number,
  manifestFailureCount: number,
  audioFiles: ReadonlyMap<V4Candidate, DirectoryFile>,
  objectUrls: ObjectUrlFactory,
): Promise<CurateCatalog> {
  const urls: string[] = [];
  try {
    const digestEntries = new Map<string, { candidate: V4Candidate; digest: string }>();
    await Promise.all(
      candidateSet.candidates.map(async (candidate) => {
        const digest = await sha256Text(candidateSetSha256 + candidate.take_id);
        digestEntries.set(candidate.take_id, { candidate, digest });
      }),
    );
    return createCatalogAfterDigests();

    function createCatalogAfterDigests(): CurateCatalog {
      const candidatesByGroup = new Map<string, V4Candidate[]>();
      const linesByKey = new Map(candidateSet.lines.map((line) => [lineKey(line), line]));
      for (const candidate of candidateSet.candidates) {
        const key = groupKey(candidate);
        const candidates = candidatesByGroup.get(key) ?? [];
        candidates.push(candidate);
        candidatesByGroup.set(key, candidates);
      }

      const groups = [...candidatesByGroup.values()]
        .map((candidates): CurateGroup => {
          const first = candidates[0]!;
          const line = linesByKey.get(lineKey(first));
          if (!line) {
            throw new Error(
              `candidate-set.lines から検証済み行を解決できません: ${lineKey(first)}`,
            );
          }
          const ordered = candidates
            .map((candidate) => digestEntries.get(candidate.take_id)!)
            .sort(
              (left, right) =>
                compareText(left.digest, right.digest) ||
                compareText(left.candidate.take_id, right.candidate.take_id),
            );
          const presentations = ordered.map(({ candidate }, index): CurateCandidatePresentation => {
            const file = audioFiles.get(candidate);
            if (!file) {
              throw new Error(`検証済み音声を解決できません: ${candidate.take_id}`);
            }
            const url = objectUrls.create(file);
            urls.push(url);
            return {
              label: blindLabel(index),
              takeId: candidate.take_id,
              audio: {
                key: `curate:${candidateSetSha256}:${candidate.take_id}`,
                url,
              },
              gateContent: candidate.gate.content,
            };
          });
          return {
            model: first.model,
            scenario: first.scenario,
            line: first.line,
            variant: first.variant,
            scenarioTitle: line.scenario_title,
            lineText: line.text,
            delivery: line.delivery,
            candidates: presentations,
          };
        })
        .sort(compareGroupTuple);
      const exportCandidatesByGroup = new Map<string, readonly ExportCandidate[]>();
      for (const [key, candidates] of candidatesByGroup) {
        exportCandidatesByGroup.set(
          key,
          [...candidates]
            .sort((left, right) => compareText(left.take_id, right.take_id))
            .map((candidate) => ({
              takeId: candidate.take_id,
              path: candidate.path,
              audioSha256: candidate.sha256,
              gate: candidate.gate,
            })),
        );
      }
      let disposed = false;
      return {
        candidateSetSha256,
        manifestCurationCount,
        manifestFailureCount,
        groups,
        exportCandidatesByGroup,
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
    }
  } catch (error) {
    for (const url of urls) {
      objectUrls.revoke(url);
    }
    throw error;
  }
}

function blindLabel(index: number): string {
  let value = index + 1;
  let label = "";
  while (value > 0) {
    value -= 1;
    label = String.fromCharCode(65 + (value % 26)) + label;
    value = Math.floor(value / 26);
  }
  return label;
}

function exactObject(
  value: unknown,
  expectedKeys: readonly string[],
  label: string,
  optionalKeys: readonly string[] = [],
): Record<string, unknown> {
  if (!isPlainObject(value)) {
    throw new Error(`${label} は object である必要があります。`);
  }
  const optional = new Set(optionalKeys);
  const actual = Object.keys(value)
    .filter((key) => !optional.has(key))
    .sort();
  const expected = [...expectedKeys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${label} の key が v4 契約と一致しません: ${actual.join(",")}`);
  }
  return value;
}

function array(value: unknown, label: string): readonly unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} は配列である必要があります。`);
  }
  return value;
}

function nonEmptyText(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} は空でない文字列である必要があります。`);
  }
  return value;
}

function pathSegment(value: unknown, label: string): string {
  const segment = nonEmptyText(value, label);
  if (segment === "." || segment === ".." || segment.includes("/") || segment.includes("\\")) {
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
    throw new Error(`${label} は 1 以上の安全な整数である必要があります。`);
  }
  return value;
}

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} は有限数である必要があります。`);
  }
  return value;
}

function nonNegativeFinite(value: unknown, label: string): number {
  const result = finiteNumber(value, label);
  if (result < 0) {
    throw new Error(`${label} は非負数である必要があります。`);
  }
  return result;
}

function assertJsonValue(value: unknown, label: string): void {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertJsonValue(item, `${label}[${index}]`));
    return;
  }
  if (isPlainObject(value)) {
    for (const [key, item] of Object.entries(value)) {
      assertJsonValue(item, `${label}.${key}`);
    }
    return;
  }
  throw new Error(`${label} は有限な JSON 値である必要があります。`);
}

function structurallyEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) {
    return true;
  }
  if (Array.isArray(left) && Array.isArray(right)) {
    return (
      left.length === right.length &&
      left.every((value, index) => structurallyEqual(value, right[index]))
    );
  }
  if (isPlainObject(left) && isPlainObject(right)) {
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

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function compareLineTuple(
  left: { readonly scenario: string; readonly line: string },
  right: { readonly scenario: string; readonly line: string },
): number {
  return compareText(left.scenario, right.scenario) || compareText(left.line, right.line);
}

function lineKey(value: { readonly scenario: string; readonly line: string }): string {
  return `${value.scenario}/${value.line}`;
}

const browserObjectUrls: ObjectUrlFactory = {
  create(file) {
    if (!(file instanceof Blob)) {
      throw new Error("選択音声が browser File ではありません。");
    }
    return URL.createObjectURL(file);
  },
  revoke(url) {
    URL.revokeObjectURL(url);
  },
};
