import { readFileSync, readdirSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";

import { parse } from "yaml";
import type { Plugin } from "vite-plus";

import type {
  ArtifactOutcome,
  BenchmarkData,
  Candidate,
  Curation,
  GenerationFailure,
  Manifest,
  Model,
  Scenario,
} from "../src/data/types.ts";
import { isSafeClipPath } from "../src/lib/clip-path.ts";

const VIRTUAL_MODULE_ID = "virtual:gaya-data";
const RESOLVED_VIRTUAL_MODULE_ID = `\0${VIRTUAL_MODULE_ID}`;
const ID_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

const MANIFEST_KEYS = [
  "format_version",
  "generated_at",
  "candidate_set_sha256",
  "models",
  "candidates",
  "curations",
  "failures",
] as const;
const MODEL_KEYS = ["id", "name", "version", "license_note", "capabilities"] as const;
const CAPABILITY_KEYS = ["emotion", "voice_prompt", "clone", "nonverbal", "reading"] as const;
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
] as const;
const GENERATION_PARAMETER_KEYS = [
  "seed",
  "recipe_version",
  "sampling",
  "requested",
  "realized",
] as const;
const LOUDNESS_KEYS = ["source", "i_lufs", "tp_dbtp", "shortfall"] as const;
const GATE_KEYS = ["mechanical", "content", "policy_version"] as const;
const GROUP_KEYS = ["model", "scenario", "line", "variant"] as const;
const SELECTED_CURATION_KEYS = [...GROUP_KEYS, "decision", "take_id", "curation_sha256"] as const;
const SKIPPED_CURATION_KEYS = [...GROUP_KEYS, "decision", "curation_sha256"] as const;
const FAILURE_KEYS = ["model", "scenario", "line", "variant", "reason"] as const;
const SCENARIO_REQUIRED_KEYS = [
  "format_version",
  "id",
  "title",
  "locale",
  "scene",
  "characters",
  "lines",
] as const;
const SCENARIO_OPTIONAL_KEYS = ["tags"] as const;
const SCENE_REQUIRED_KEYS = ["setting"] as const;
const SCENE_OPTIONAL_KEYS = ["acoustics", "listener"] as const;
const CHARACTER_REQUIRED_KEYS = ["id", "name", "gender", "age", "voice"] as const;
const CHARACTER_OPTIONAL_KEYS = ["kind", "archetype", "personality", "reference_voice"] as const;
const LINE_REQUIRED_KEYS = ["id", "character", "text", "emotion", "delivery"] as const;
const LINE_OPTIONAL_KEYS = [
  "reading",
  "intensity",
  "situation",
  "difficulty",
  "loop_ok",
  "final_intonation",
] as const;

const LOCALES = new Set(["ja", "en"]);
const GENDERS = new Set(["female", "male", "neutral"]);
const AGES = new Set(["child", "teen", "young_adult", "adult", "middle_aged", "elderly"]);
const CHARACTER_KINDS = new Set(["human", "machine", "creature", "spirit"]);
const EMOTIONS = new Set([
  "neutral",
  "cheerful",
  "angry",
  "sad",
  "fearful",
  "surprised",
  "tired",
  "drunk",
  "whisper",
  "shout",
  "laughing",
  "pain",
]);
const DIFFICULTIES = new Set(["standard", "hard"]);
const FINAL_INTONATIONS = new Set(["fall", "rise", "free"]);
const LOUDNESS_SOURCES = new Set(["encoded_opus"]);
const FAILURE_REASONS = new Set(["no_eligible_take", "test_only_adapter"]);
const CONTENT_GATE_RESULTS = new Set(["pass", "review_required"]);
const SHA256_PATTERN = /^[0-9a-f]{64}$/;

type WatchFile = (file: string) => void;
type UnknownRecord = Record<string, unknown>;

export class GayaDataError extends Error {
  override readonly name = "GayaDataError";
}

export interface GayaDataPluginOptions {
  readonly repositoryRoot: string;
}

export function gayaDataPlugin({ repositoryRoot }: GayaDataPluginOptions): Plugin {
  const absoluteRepositoryRoot = path.resolve(repositoryRoot);

  function loadAndWatch(addWatchFile: WatchFile): BenchmarkData {
    return loadBenchmarkData(absoluteRepositoryRoot, addWatchFile);
  }

  return {
    name: "gaya-data",
    enforce: "pre",
    buildStart() {
      loadAndWatch((file) => this.addWatchFile(file));
    },
    resolveId(id) {
      if (id === VIRTUAL_MODULE_ID) {
        return RESOLVED_VIRTUAL_MODULE_ID;
      }
    },
    load(id) {
      if (id !== RESOLVED_VIRTUAL_MODULE_ID) {
        return;
      }

      const data = loadAndWatch((file) => this.addWatchFile(file));
      return [
        `const benchmarkData = ${JSON.stringify(data)};`,
        "export { benchmarkData };",
        "export default benchmarkData;",
      ].join("\n");
    },
  };
}

export function loadBenchmarkData(repositoryRoot: string, watchFile?: WatchFile): BenchmarkData {
  const manifestPath = path.join(repositoryRoot, "data", "manifest.json");
  const scenariosDirectory = path.join(repositoryRoot, "scenarios");
  watchFile?.(manifestPath);

  const manifest = loadManifest(manifestPath);
  const scenarios = loadScenarios(scenariosDirectory, watchFile);
  validateReferences(manifest, scenarios);

  return { manifest, scenarios, outcomes: projectOutcomes(manifest) };
}

function loadManifest(manifestPath: string): Manifest {
  const source = readTextFile(manifestPath, "manifest");
  let value: unknown;
  try {
    value = JSON.parse(source);
  } catch (error) {
    throw new GayaDataError(`manifest JSON を解析できません: ${manifestPath}`, {
      cause: error,
    });
  }

  assertRecord(value, "manifest");
  assertExactKeys(value, MANIFEST_KEYS, [], "manifest");
  assertManifestVersion(value.format_version);
  assertNonEmptyString(value.generated_at, "manifest generated_at");
  assertSha256(value.candidate_set_sha256, "manifest candidate_set_sha256");
  assertArray(value.models, "manifest models");
  assertArray(value.candidates, "manifest candidates");
  assertArray(value.curations, "manifest curations");
  assertArray(value.failures, "manifest failures");

  const models = value.models.map((model, index) => validateModel(model, index));
  const candidates = value.candidates.map((candidate, index) =>
    validateCandidate(candidate, index),
  );
  const curations = value.curations.map((curation, index) => validateCuration(curation, index));
  const failures = value.failures.map((failure, index) => validateFailure(failure, index));
  assertUnique(
    models.map((model) => model.id),
    "manifest model id",
  );
  assertUnique(
    candidates.map((candidate) => candidate.take_id),
    "manifest candidate take_id",
  );
  assertUnique(
    candidates.map((candidate) =>
      JSON.stringify([artifactKeyTuple(candidate), candidate.take_index]),
    ),
    "manifest candidate slot",
  );
  assertUnique(curations.map(artifactKeyTuple), "manifest curation group");
  assertUnique(
    failures.map((failure) => artifactKeyTuple(failure)),
    "manifest failure group",
  );
  const modelIds = new Set(models.map((model) => model.id));
  assertManifestGroups(candidates, curations, failures, modelIds);

  return {
    format_version: 4,
    generated_at: value.generated_at,
    candidate_set_sha256: value.candidate_set_sha256,
    models,
    candidates,
    curations,
    failures,
  };
}

function validateModel(value: unknown, index: number): Model {
  const label = `manifest models[${index}]`;
  assertRecord(value, label);
  assertExactKeys(value, MODEL_KEYS, [], label);
  assertPathSegment(value.id, `${label}.id`);
  assertNonEmptyString(value.name, `${label}.name`);
  assertNonEmptyString(value.version, `${label}.version`);
  assertString(value.license_note, `${label}.license_note`);

  assertRecord(value.capabilities, `${label}.capabilities`);
  assertExactKeys(value.capabilities, CAPABILITY_KEYS, [], `${label}.capabilities`);
  for (const key of CAPABILITY_KEYS) {
    assertBoolean(value.capabilities[key], `${label}.capabilities.${key}`);
  }

  return value as unknown as Model;
}

function validateCandidate(value: unknown, index: number): Candidate {
  const label = `manifest candidates[${index}]`;
  assertRecord(value, label);
  assertExactKeys(value, CANDIDATE_KEYS, [], label);
  for (const key of GROUP_KEYS) {
    assertPathSegment(value[key], `${label}.${key}`);
  }
  const model = value.model as string;
  const scenario = value.scenario as string;
  const line = value.line as string;
  const variant = value.variant as string;
  assertPositiveInteger(value.take_index, `${label}.take_index`);
  assertSha256(value.take_id, `${label}.take_id`);
  assertSha256(value.sha256, `${label}.sha256`);
  assertSha256(value.generation_input_sha256, `${label}.generation_input_sha256`);
  const expectedTakeId = createHash("sha256")
    .update(
      JSON.stringify({
        final_opus_sha256: value.sha256,
        generation_input_sha256: value.generation_input_sha256,
      }),
    )
    .digest("hex");
  if (value.take_id !== expectedTakeId) {
    throw new GayaDataError(`${label}.take_id が provenance と一致しません。`);
  }
  assertNonNegativeFiniteNumber(value.duration_sec, `${label}.duration_sec`);
  assertNonNegativeFiniteNumber(value.rtf, `${label}.rtf`);
  assertRecord(value.gen_params, `${label}.gen_params`);
  assertExactKeys(value.gen_params, GENERATION_PARAMETER_KEYS, [], `${label}.gen_params`);
  if (
    value.gen_params.seed !== null &&
    (typeof value.gen_params.seed !== "number" || !Number.isSafeInteger(value.gen_params.seed))
  ) {
    throw new GayaDataError(`${label}.gen_params.seed は整数または null が必要です。`);
  }
  assertNonEmptyString(value.gen_params.recipe_version, `${label}.gen_params.recipe_version`);
  for (const key of ["sampling", "requested", "realized"] as const) {
    assertRecord(value.gen_params[key], `${label}.gen_params.${key}`);
  }
  assertRecord(value.loudness, `${label}.loudness`);
  assertExactKeys(value.loudness, LOUDNESS_KEYS, [], `${label}.loudness`);
  assertEnum(value.loudness.source, LOUDNESS_SOURCES, `${label}.loudness.source`);
  assertFiniteNumber(value.loudness.i_lufs, `${label}.loudness.i_lufs`);
  assertFiniteNumber(value.loudness.tp_dbtp, `${label}.loudness.tp_dbtp`);
  assertBoolean(value.loudness.shortfall, `${label}.loudness.shortfall`);
  assertString(value.path, `${label}.path`);
  assertRelativeClipPath(value.path, `${label}.path`);
  const expectedPath =
    `audio/takes/${model}/${scenario}/${line}/${variant}/` +
    `take-${String(value.take_index).padStart(4, "0")}-${value.sha256}.opus`;
  if (value.path !== expectedPath) {
    throw new GayaDataError(`${label}.path が field から再構成できません。`);
  }
  assertRecord(value.gate, `${label}.gate`);
  assertExactKeys(value.gate, GATE_KEYS, [], `${label}.gate`);
  if (value.gate.mechanical !== "pass") {
    throw new GayaDataError(`${label}.gate.mechanical は pass が必要です。`);
  }
  assertEnum(value.gate.content, CONTENT_GATE_RESULTS, `${label}.gate.content`);
  assertNonEmptyString(value.gate.policy_version, `${label}.gate.policy_version`);

  return value as unknown as Candidate;
}

function validateCuration(value: unknown, index: number): Curation {
  const label = `manifest curations[${index}]`;
  assertRecord(value, label);
  if (value.decision === "selected") {
    assertExactKeys(value, SELECTED_CURATION_KEYS, [], label);
    assertSha256(value.take_id, `${label}.take_id`);
  } else if (value.decision === "skipped") {
    assertExactKeys(value, SKIPPED_CURATION_KEYS, [], label);
  } else {
    throw new GayaDataError(`${label}.decision が許可された値ではありません。`);
  }
  for (const key of GROUP_KEYS) {
    assertPathSegment(value[key], `${label}.${key}`);
  }
  assertSha256(value.curation_sha256, `${label}.curation_sha256`);
  return value as unknown as Curation;
}

function validateFailure(value: unknown, index: number): GenerationFailure {
  const label = `manifest failures[${index}]`;
  assertRecord(value, label);
  assertExactKeys(value, FAILURE_KEYS, [], label);
  for (const key of GROUP_KEYS) {
    assertPathSegment(value[key], `${label}.${key}`);
  }
  assertEnum(value.reason, FAILURE_REASONS, `${label}.reason`);
  if (value.reason === "test_only_adapter" && value.model !== "dummy") {
    throw new GayaDataError(`${label}.reason=test_only_adapter は model=dummy が必要です。`);
  }

  return value as unknown as GenerationFailure;
}

function loadScenarios(scenariosDirectory: string, watchFile?: WatchFile): readonly Scenario[] {
  let entries;
  try {
    entries = readdirSync(scenariosDirectory, { withFileTypes: true });
  } catch (error) {
    throw new GayaDataError(`scenario ディレクトリを読み込めません: ${scenariosDirectory}`, {
      cause: error,
    });
  }

  const scenarioFiles = entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".yaml"))
    .map((entry) => path.join(scenariosDirectory, entry.name))
    .sort((left, right) => left.localeCompare(right, "en"));
  if (scenarioFiles.length === 0) {
    throw new GayaDataError(`scenario YAML がありません: ${scenariosDirectory}`);
  }

  const scenarios = scenarioFiles.map((scenarioPath) => {
    watchFile?.(scenarioPath);
    return loadScenario(scenarioPath);
  });
  assertUnique(
    scenarios.map((scenario) => scenario.id),
    "scenario id",
  );
  return scenarios;
}

function loadScenario(scenarioPath: string): Scenario {
  const source = readTextFile(scenarioPath, "scenario");
  let value: unknown;
  try {
    value = parse(source);
  } catch (error) {
    throw new GayaDataError(`scenario YAML を解析できません: ${scenarioPath}`, {
      cause: error,
    });
  }

  const label = `scenario ${scenarioPath}`;
  assertRecord(value, label);
  assertExactKeys(value, SCENARIO_REQUIRED_KEYS, SCENARIO_OPTIONAL_KEYS, label);
  assertScenarioVersion(value.format_version, `${label}.format_version`);
  assertId(value.id, `${label}.id`);

  const filenameId = path.basename(scenarioPath, ".yaml");
  if (value.id !== filenameId) {
    throw new GayaDataError(
      `${label}.id はファイル名と一致する必要があります: ${value.id} != ${filenameId}`,
    );
  }

  assertString(value.title, `${label}.title`);
  assertEnum(value.locale, LOCALES, `${label}.locale`);
  if ("tags" in value) {
    assertStringArray(value.tags, `${label}.tags`);
  }
  validateScene(value.scene, label);
  assertArray(value.characters, `${label}.characters`);
  assertArray(value.lines, `${label}.lines`);
  if (value.characters.length === 0) {
    throw new GayaDataError(`${label}.characters は1件以上必要です。`);
  }
  if (value.lines.length === 0) {
    throw new GayaDataError(`${label}.lines は1件以上必要です。`);
  }

  const characters = value.characters.map((character, index) =>
    validateCharacter(character, label, index),
  );
  const characterIds = characters.map((character) => character.id);
  assertUnique(characterIds, `${label} character id`);
  const characterIdSet = new Set(characterIds);

  const lines = value.lines.map((line, index) => validateLine(line, label, index, characterIdSet));
  assertUnique(
    lines.map((line) => line.id),
    `${label} line id`,
  );

  return {
    ...value,
    format_version: 1,
    locale: value.locale as Scenario["locale"],
    scene: value.scene as Scenario["scene"],
    characters,
    lines,
  } as unknown as Scenario;
}

function validateScene(value: unknown, scenarioLabel: string): void {
  const label = `${scenarioLabel}.scene`;
  assertRecord(value, label);
  assertExactKeys(value, SCENE_REQUIRED_KEYS, SCENE_OPTIONAL_KEYS, label);
  assertString(value.setting, `${label}.setting`);
  for (const key of SCENE_OPTIONAL_KEYS) {
    if (key in value) {
      assertString(value[key], `${label}.${key}`);
    }
  }
}

function validateCharacter(
  value: unknown,
  scenarioLabel: string,
  index: number,
): Scenario["characters"][number] {
  const label = `${scenarioLabel}.characters[${index}]`;
  assertRecord(value, label);
  assertExactKeys(value, CHARACTER_REQUIRED_KEYS, CHARACTER_OPTIONAL_KEYS, label);
  assertId(value.id, `${label}.id`);
  assertString(value.name, `${label}.name`);
  if ("kind" in value) {
    assertEnum(value.kind, CHARACTER_KINDS, `${label}.kind`);
  }
  assertEnum(value.gender, GENDERS, `${label}.gender`);
  assertEnum(value.age, AGES, `${label}.age`);
  assertString(value.voice, `${label}.voice`);
  for (const key of ["archetype", "personality"] as const) {
    if (key in value) {
      assertString(value[key], `${label}.${key}`);
    }
  }
  if ("reference_voice" in value && value.reference_voice !== null) {
    assertString(value.reference_voice, `${label}.reference_voice`);
  }

  return {
    ...value,
    kind: "kind" in value ? value.kind : "human",
  } as unknown as Scenario["characters"][number];
}

function validateLine(
  value: unknown,
  scenarioLabel: string,
  index: number,
  characterIds: ReadonlySet<string>,
): Scenario["lines"][number] {
  const label = `${scenarioLabel}.lines[${index}]`;
  assertRecord(value, label);
  assertExactKeys(value, LINE_REQUIRED_KEYS, LINE_OPTIONAL_KEYS, label);
  assertId(value.id, `${label}.id`);
  assertId(value.character, `${label}.character`);
  if (!characterIds.has(value.character)) {
    throw new GayaDataError(
      `${label}.character が存在しない character を参照しています: ${value.character}`,
    );
  }
  assertString(value.text, `${label}.text`);
  if (value.text.length < 1 || value.text.length > 60) {
    throw new GayaDataError(`${label}.text は1〜60文字が必要です。`);
  }
  if ("reading" in value && value.reading !== null) {
    assertString(value.reading, `${label}.reading`);
  }
  assertEnum(value.emotion, EMOTIONS, `${label}.emotion`);
  assertString(value.delivery, `${label}.delivery`);
  if ("situation" in value) {
    assertString(value.situation, `${label}.situation`);
  }
  if ("intensity" in value) {
    if (!Number.isInteger(value.intensity) || ![1, 2, 3].includes(value.intensity as number)) {
      throw new GayaDataError(`${label}.intensity は1、2、3のいずれかが必要です。`);
    }
  }
  if ("difficulty" in value) {
    assertEnum(value.difficulty, DIFFICULTIES, `${label}.difficulty`);
  }
  if ("loop_ok" in value) {
    assertBoolean(value.loop_ok, `${label}.loop_ok`);
  }
  if ("final_intonation" in value) {
    assertEnum(value.final_intonation, FINAL_INTONATIONS, `${label}.final_intonation`);
  }

  return {
    ...value,
    intensity: "intensity" in value ? value.intensity : 2,
    difficulty: "difficulty" in value ? value.difficulty : "standard",
    loop_ok: "loop_ok" in value ? value.loop_ok : true,
    final_intonation: "final_intonation" in value ? value.final_intonation : "fall",
  } as unknown as Scenario["lines"][number];
}

function validateReferences(manifest: Manifest, scenarios: readonly Scenario[]): void {
  const modelIds = new Set(manifest.models.map((model) => model.id));
  const linesByScenario = new Map(
    scenarios.map((scenario) => [scenario.id, new Set(scenario.lines.map((line) => line.id))]),
  );

  for (const candidate of manifest.candidates) {
    const key = artifactKeyTuple(candidate);
    if (!modelIds.has(candidate.model)) {
      throw new GayaDataError(
        `candidate ${key} が存在しない model を参照しています: ${candidate.model}`,
      );
    }
    const lineIds = linesByScenario.get(candidate.scenario);
    if (!lineIds) {
      throw new GayaDataError(
        `candidate ${key} が存在しない scenario を参照しています: ${candidate.scenario}`,
      );
    }
    if (!lineIds.has(candidate.line)) {
      throw new GayaDataError(
        `candidate ${key} が存在しない line を参照しています: ${candidate.line}`,
      );
    }
  }

  for (const failure of manifest.failures) {
    const key = artifactKeyTuple(failure);
    if (!modelIds.has(failure.model)) {
      throw new GayaDataError(
        `failure ${key} が存在しない model を参照しています: ${failure.model}`,
      );
    }
    const lineIds = linesByScenario.get(failure.scenario);
    if (!lineIds) {
      throw new GayaDataError(
        `failure ${key} が存在しない scenario を参照しています: ${failure.scenario}`,
      );
    }
    if (!lineIds.has(failure.line)) {
      throw new GayaDataError(`failure ${key} が存在しない line を参照しています: ${failure.line}`);
    }
  }
}

function assertRecord(value: unknown, label: string): asserts value is UnknownRecord {
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    Object.getPrototypeOf(value) !== Object.prototype
  ) {
    throw new GayaDataError(`${label} は object が必要です。`);
  }
}

function assertArray(value: unknown, label: string): asserts value is unknown[] {
  if (!Array.isArray(value)) {
    throw new GayaDataError(`${label} は配列が必要です。`);
  }
}

function assertExactKeys(
  value: UnknownRecord,
  required: readonly string[],
  optional: readonly string[],
  label: string,
): void {
  const keys = Object.keys(value);
  const allowed = new Set([...required, ...optional]);
  const missing = required.filter((key) => !Object.hasOwn(value, key));
  const unknown = keys.filter((key) => !allowed.has(key));
  if (missing.length > 0 || unknown.length > 0) {
    const details = [
      missing.length > 0 ? `不足: ${missing.join(", ")}` : "",
      unknown.length > 0 ? `未知: ${unknown.join(", ")}` : "",
    ]
      .filter(Boolean)
      .join("; ");
    throw new GayaDataError(`${label} の項目が一致しません (${details})。`);
  }
}

function assertManifestVersion(value: unknown): asserts value is 4 {
  if (value !== 4) {
    throw new GayaDataError(`manifest format_version は4が必要です。`);
  }
}

function assertScenarioVersion(value: unknown, label: string): asserts value is 1 {
  if (value !== 1) {
    throw new GayaDataError(`${label} は1が必要です。`);
  }
}

function assertString(value: unknown, label: string): asserts value is string {
  if (typeof value !== "string") {
    throw new GayaDataError(`${label} は文字列が必要です。`);
  }
}

function assertBoolean(value: unknown, label: string): asserts value is boolean {
  if (typeof value !== "boolean") {
    throw new GayaDataError(`${label} は bool が必要です。`);
  }
}

function assertNonEmptyString(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  if (value.length === 0) {
    throw new GayaDataError(`${label} は空でない文字列が必要です。`);
  }
}

function assertId(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  if (!ID_PATTERN.test(value)) {
    throw new GayaDataError(`${label} は kebab-case id が必要です。`);
  }
}

function assertPathSegment(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  if (
    value.length === 0 ||
    value === "." ||
    value === ".." ||
    value.includes("/") ||
    value.includes("\\")
  ) {
    throw new GayaDataError(`${label} は安全な path segment が必要です。`);
  }
}

function assertSha256(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  if (!SHA256_PATTERN.test(value)) {
    throw new GayaDataError(`${label} は完全な小文字 SHA-256 が必要です。`);
  }
}

function assertPositiveInteger(value: unknown, label: string): asserts value is number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new GayaDataError(`${label} は1以上の整数が必要です。`);
  }
}

function assertEnum(
  value: unknown,
  allowed: ReadonlySet<string>,
  label: string,
): asserts value is string {
  assertString(value, label);
  if (!allowed.has(value)) {
    throw new GayaDataError(`${label} が許可された値ではありません: ${value}`);
  }
}

function assertStringArray(value: unknown, label: string): asserts value is string[] {
  assertArray(value, label);
  for (const [index, item] of value.entries()) {
    assertString(item, `${label}[${index}]`);
  }
}

function assertNonNegativeFiniteNumber(value: unknown, label: string): void {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new GayaDataError(`${label} は0以上の有限数が必要です。`);
  }
}

function assertFiniteNumber(value: unknown, label: string): void {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new GayaDataError(`${label} は有限数である必要があります`);
  }
}

function assertUnique(values: readonly string[], label: string): void {
  const seen = new Set<string>();
  for (const value of values) {
    if (seen.has(value)) {
      throw new GayaDataError(`${label} が重複しています: ${value}`);
    }
    seen.add(value);
  }
}

function assertRelativeClipPath(value: string, label: string): void {
  if (!isSafeClipPath(value)) {
    throw new GayaDataError(`${label} は安全な相対パスが必要です: ${value}`);
  }
}

function artifactKeyTuple(
  artifact: Pick<
    Candidate | Curation | GenerationFailure,
    "model" | "scenario" | "line" | "variant"
  >,
): string {
  return JSON.stringify([artifact.model, artifact.scenario, artifact.line, artifact.variant]);
}

function assertManifestGroups(
  candidates: readonly Candidate[],
  curations: readonly Curation[],
  failures: readonly GenerationFailure[],
  modelIds: ReadonlySet<string>,
): void {
  const candidatesByGroup = new Map<string, Candidate[]>();
  const candidatesByTakeId = new Map(candidates.map((candidate) => [candidate.take_id, candidate]));
  for (const candidate of candidates) {
    if (!modelIds.has(candidate.model)) {
      throw new GayaDataError(`candidate が未知の model を参照しています: ${candidate.model}`);
    }
    const key = artifactKeyTuple(candidate);
    const groupCandidates = candidatesByGroup.get(key);
    if (groupCandidates) {
      groupCandidates.push(candidate);
    } else {
      candidatesByGroup.set(key, [candidate]);
    }
  }
  for (const curation of curations) {
    const key = artifactKeyTuple(curation);
    const groupCandidates = candidatesByGroup.get(key);
    if (!groupCandidates) {
      throw new GayaDataError(`curation に対応する candidate group がありません: ${key}`);
    }
    if (curation.decision === "selected") {
      const selected = candidatesByTakeId.get(curation.take_id);
      if (!selected || artifactKeyTuple(selected) !== key) {
        throw new GayaDataError(
          `selected curation が同一 group の take を参照していません: ${key}`,
        );
      }
    }
  }
  const candidateKeys = new Set(candidatesByGroup.keys());
  for (const failure of failures) {
    if (!modelIds.has(failure.model)) {
      throw new GayaDataError(`failure が未知の model を参照しています: ${failure.model}`);
    }
    const key = artifactKeyTuple(failure);
    if (candidateKeys.has(key)) {
      throw new GayaDataError(`manifest の candidate/failure group が競合しています: ${key}`);
    }
  }
}

function projectOutcomes(manifest: Manifest): readonly ArtifactOutcome[] {
  const candidatesByGroup = new Map<
    string,
    {
      readonly group: {
        readonly model: string;
        readonly scenario: string;
        readonly line: string;
        readonly variant: string;
      };
      readonly candidates: Candidate[];
    }
  >();
  for (const candidate of manifest.candidates) {
    const key = artifactKeyTuple(candidate);
    const entry = candidatesByGroup.get(key);
    if (entry) {
      entry.candidates.push(candidate);
    } else {
      candidatesByGroup.set(key, {
        group: {
          model: candidate.model,
          scenario: candidate.scenario,
          line: candidate.line,
          variant: candidate.variant,
        },
        candidates: [candidate],
      });
    }
  }
  const curationsByGroup = new Map(
    manifest.curations.map((curation) => [artifactKeyTuple(curation), curation]),
  );
  const outcomes: ArtifactOutcome[] = [];
  for (const [key, { group, candidates }] of candidatesByGroup) {
    const curation = curationsByGroup.get(key);
    if (!curation) {
      outcomes.push({ kind: "uncurated", group, candidates });
      continue;
    }
    if (curation.decision === "skipped") {
      outcomes.push({ kind: "skipped", group, candidates, curation });
      continue;
    }
    const candidate = candidates.find((item) => item.take_id === curation.take_id);
    if (!candidate) {
      throw new GayaDataError(`selected curation の take_id が candidate に存在しません: ${key}`);
    }
    outcomes.push({ kind: "selected", group, candidate, curation });
  }
  for (const failure of manifest.failures) {
    outcomes.push({
      kind: "failure",
      group: {
        model: failure.model,
        scenario: failure.scenario,
        line: failure.line,
        variant: failure.variant,
      },
      failure,
    });
  }
  return outcomes;
}

function readTextFile(file: string, label: string): string {
  try {
    return readFileSync(file, "utf8");
  } catch (error) {
    throw new GayaDataError(`${label} を読み込めません: ${file}`, {
      cause: error,
    });
  }
}
