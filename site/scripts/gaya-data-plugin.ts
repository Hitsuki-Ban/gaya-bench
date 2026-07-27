import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { parse } from "yaml";
import type { Plugin } from "vite-plus";

import type { BenchmarkData, Clip, Manifest, Model, Scenario } from "../src/data/types.ts";
import { isSafeClipPath } from "../src/lib/clip-path.ts";

const VIRTUAL_MODULE_ID = "virtual:gaya-data";
const RESOLVED_VIRTUAL_MODULE_ID = `\0${VIRTUAL_MODULE_ID}`;
const ID_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

const MANIFEST_KEYS = ["format_version", "generated_at", "models", "clips"] as const;
const MODEL_KEYS = ["id", "name", "version", "license_note", "capabilities"] as const;
const CAPABILITY_KEYS = ["emotion", "voice_prompt", "clone", "nonverbal", "reading"] as const;
const CLIP_KEYS = [
  "model",
  "scenario",
  "line",
  "variant",
  "path",
  "duration_sec",
  "sha256",
  "gen_params",
  "rtf",
] as const;
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
const CHARACTER_OPTIONAL_KEYS = ["archetype", "personality", "reference_voice"] as const;
const LINE_REQUIRED_KEYS = ["id", "character", "text", "emotion", "delivery"] as const;
const LINE_OPTIONAL_KEYS = ["reading", "intensity", "situation", "difficulty", "loop_ok"] as const;

const LOCALES = new Set(["ja", "en"]);
const GENDERS = new Set(["female", "male", "neutral"]);
const AGES = new Set(["child", "teen", "young_adult", "adult", "middle_aged", "elderly"]);
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

  return { manifest, scenarios };
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
  assertVersion(value.format_version, "manifest format_version");
  assertString(value.generated_at, "manifest generated_at");
  assertArray(value.models, "manifest models");
  assertArray(value.clips, "manifest clips");

  const models = value.models.map((model, index) => validateModel(model, index));
  const clips = value.clips.map((clip, index) => validateClip(clip, index));
  assertUnique(
    models.map((model) => model.id),
    "manifest model id",
  );
  assertUnique(
    clips.map((clip) => clipKeyTuple(clip)),
    "manifest clip key",
  );

  return {
    format_version: 1,
    generated_at: value.generated_at,
    models,
    clips,
  };
}

function validateModel(value: unknown, index: number): Model {
  const label = `manifest models[${index}]`;
  assertRecord(value, label);
  assertExactKeys(value, MODEL_KEYS, [], label);
  for (const key of ["id", "name", "version", "license_note"] as const) {
    assertString(value[key], `${label}.${key}`);
  }

  assertRecord(value.capabilities, `${label}.capabilities`);
  assertExactKeys(value.capabilities, CAPABILITY_KEYS, [], `${label}.capabilities`);
  for (const key of CAPABILITY_KEYS) {
    assertBoolean(value.capabilities[key], `${label}.capabilities.${key}`);
  }

  return value as unknown as Model;
}

function validateClip(value: unknown, index: number): Clip {
  const label = `manifest clips[${index}]`;
  assertRecord(value, label);
  assertExactKeys(value, CLIP_KEYS, [], label);
  for (const key of ["model", "scenario", "line", "variant", "path", "sha256"] as const) {
    assertString(value[key], `${label}.${key}`);
  }
  assertNonNegativeFiniteNumber(value.duration_sec, `${label}.duration_sec`);
  assertNonNegativeFiniteNumber(value.rtf, `${label}.rtf`);
  assertRecord(value.gen_params, `${label}.gen_params`);
  assertString(value.path, `${label}.path`);
  assertRelativeClipPath(value.path, `${label}.path`);

  return value as unknown as Clip;
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
  assertVersion(value.format_version, `${label}.format_version`);
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

  return value as unknown as Scenario["characters"][number];
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

  return {
    ...value,
    intensity: "intensity" in value ? value.intensity : 2,
    difficulty: "difficulty" in value ? value.difficulty : "standard",
    loop_ok: "loop_ok" in value ? value.loop_ok : true,
  } as unknown as Scenario["lines"][number];
}

function validateReferences(manifest: Manifest, scenarios: readonly Scenario[]): void {
  const modelIds = new Set(manifest.models.map((model) => model.id));
  const linesByScenario = new Map(
    scenarios.map((scenario) => [scenario.id, new Set(scenario.lines.map((line) => line.id))]),
  );

  for (const clip of manifest.clips) {
    const key = clipKeyTuple(clip);
    if (!modelIds.has(clip.model)) {
      throw new GayaDataError(`clip ${key} が存在しない model を参照しています: ${clip.model}`);
    }
    const lineIds = linesByScenario.get(clip.scenario);
    if (!lineIds) {
      throw new GayaDataError(
        `clip ${key} が存在しない scenario を参照しています: ${clip.scenario}`,
      );
    }
    if (!lineIds.has(clip.line)) {
      throw new GayaDataError(`clip ${key} が存在しない line を参照しています: ${clip.line}`);
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
    throw new GayaDataError(`${label} の項目が v1 と一致しません (${details})。`);
  }
}

function assertVersion(value: unknown, label: string): asserts value is 1 {
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

function assertId(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  if (!ID_PATTERN.test(value)) {
    throw new GayaDataError(`${label} は kebab-case id が必要です。`);
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

function clipKeyTuple(clip: Clip): string {
  return JSON.stringify([clip.model, clip.scenario, clip.line, clip.variant]);
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
