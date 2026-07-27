import { benchmarkData } from "virtual:gaya-data";

import type { Clip, GenerationFailure, Line, Model, Scenario } from "./types";

export { benchmarkData };
export type {
  Age,
  BenchmarkData,
  Character,
  Clip,
  Difficulty,
  Emotion,
  Gender,
  GenerationFailure,
  GenerationFailureReason,
  JsonPrimitive,
  JsonValue,
  Line,
  Locale,
  Manifest,
  Model,
  ModelCapabilities,
  Scenario,
  Scene,
} from "./types";

export const scenarioById: ReadonlyMap<string, Scenario> = new Map(
  benchmarkData.scenarios.map((scenario) => [scenario.id, scenario]),
);

export const modelById: ReadonlyMap<string, Model> = new Map(
  benchmarkData.manifest.models.map((model) => [model.id, model]),
);

export const lineByKey: ReadonlyMap<string, Line> = new Map(
  benchmarkData.scenarios.flatMap((scenario) =>
    scenario.lines.map((line) => [`${scenario.id}/${line.id}`, line]),
  ),
);

const clipsByScenario = new Map<string, Clip[]>(
  benchmarkData.scenarios.map((scenario) => [scenario.id, []]),
);
const failuresByScenario = new Map<string, GenerationFailure[]>(
  benchmarkData.scenarios.map((scenario) => [scenario.id, []]),
);

for (const clip of benchmarkData.manifest.clips) {
  clipsByScenario.get(clip.scenario)!.push(clip);
}
for (const failure of benchmarkData.manifest.failures) {
  failuresByScenario.get(failure.scenario)!.push(failure);
}

export function clipKey(clip: Clip): string {
  return JSON.stringify([clip.model, clip.scenario, clip.line, clip.variant]);
}

export function getClipsForScenario(id: string): readonly Clip[] {
  const clips = clipsByScenario.get(id);
  if (!clips) {
    throw new Error(`未知の scenario id です: ${id}`);
  }
  return clips;
}

export function getFailuresForScenario(id: string): readonly GenerationFailure[] {
  const failures = failuresByScenario.get(id);
  if (!failures) {
    throw new Error(`未知の scenario id です: ${id}`);
  }
  return failures;
}
