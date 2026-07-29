import { benchmarkData } from "virtual:gaya-data";

import type { ArtifactOutcome, Candidate, Line, Model, Scenario } from "./types";

export { benchmarkData };
export type {
  Age,
  ArtifactGroup,
  ArtifactOutcome,
  BenchmarkData,
  Candidate,
  Character,
  CharacterKind,
  Curation,
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
  SelectedCuration,
  SkippedCuration,
} from "./types";

export const scenarioById: ReadonlyMap<string, Scenario> = new Map(
  benchmarkData.scenarios.map((scenario) => [scenario.id, scenario]),
);

export const manifestModelById: ReadonlyMap<string, Model> = new Map(
  benchmarkData.manifest.models.map((model) => [model.id, model]),
);

export const modelById: ReadonlyMap<string, Model> = new Map(
  [...manifestModelById.values()]
    .filter((model) =>
      benchmarkData.outcomes.some(
        (outcome) => outcome.kind === "selected" && outcome.candidate.model === model.id,
      ),
    )
    .map((model) => [model.id, model]),
);

export const playableModels: readonly Model[] = [...modelById.values()];

export const lineByKey: ReadonlyMap<string, Line> = new Map(
  benchmarkData.scenarios.flatMap((scenario) =>
    scenario.lines.map((line) => [`${scenario.id}/${line.id}`, line]),
  ),
);

const outcomesByScenario = new Map<string, ArtifactOutcome[]>(
  benchmarkData.scenarios.map((scenario) => [scenario.id, []]),
);

for (const outcome of benchmarkData.outcomes) {
  outcomesByScenario.get(outcome.group.scenario)!.push(outcome);
}

export const selectedCandidates: readonly Candidate[] = benchmarkData.outcomes.flatMap((outcome) =>
  outcome.kind === "selected" ? [outcome.candidate] : [],
);

export function candidateKey(candidate: Candidate): string {
  return JSON.stringify([candidate.model, candidate.scenario, candidate.line, candidate.variant]);
}

export function getOutcomesForScenario(id: string): readonly ArtifactOutcome[] {
  const outcomes = outcomesByScenario.get(id);
  if (!outcomes) {
    throw new Error(`未知の scenario id です: ${id}`);
  }
  return outcomes;
}
