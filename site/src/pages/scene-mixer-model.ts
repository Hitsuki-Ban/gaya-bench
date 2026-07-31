import type { ArtifactOutcome, Model, PublishedCandidate, Scenario } from "@/data";

export interface SceneMixerOption {
  readonly key: string;
  readonly model: Model;
  readonly candidates: readonly PublishedCandidate[];
}

const MINIMUM_VOICES = 3;

export function buildSceneMixerOptions(
  scenario: Scenario,
  outcomes: readonly ArtifactOutcome[],
  models: readonly Model[],
): readonly SceneMixerOption[] {
  if (models.length === 0) {
    throw new Error(`scene mixer に利用可能な model がありません: ${scenario.id}`);
  }

  const modelById = uniqueIndex(models, "model", (model) => model.id);
  const lineById = uniqueIndex(scenario.lines, "line", (line) => line.id);
  const lineOrder = new Map(scenario.lines.map((line, index) => [line.id, index]));
  const candidatesByModel = new Map(models.map((model) => [model.id, [] as PublishedCandidate[]]));
  const selectedGroups = new Set<string>();

  for (const outcome of outcomes) {
    const { group } = outcome;
    if (group.scenario !== scenario.id) {
      throw new Error(
        `scene mixer outcome の scenario が一致しません: ${group.scenario} != ${scenario.id}`,
      );
    }
    if (!modelById.has(group.model)) {
      throw new Error(`scene mixer outcome が未知の model を参照しています: ${group.model}`);
    }
    const line = lineById.get(group.line);
    if (!line) {
      throw new Error(`scene mixer outcome が未知の line を参照しています: ${group.line}`);
    }
    if (outcome.kind !== "selected") {
      continue;
    }

    assertCandidateMatchesGroup(outcome.candidate, group);
    const groupKey = JSON.stringify([group.model, group.scenario, group.line, group.variant]);
    if (selectedGroups.has(groupKey)) {
      throw new Error(`scene mixer selected group が重複しています: ${groupKey}`);
    }
    selectedGroups.add(groupKey);

    if (group.variant === "dry" && line.loop_ok) {
      candidatesByModel.get(group.model)!.push(outcome.candidate);
    }
  }

  return models.map((model) => {
    const candidates = candidatesByModel.get(model.id)!;
    candidates.sort((left, right) => lineOrder.get(left.line)! - lineOrder.get(right.line)!);
    if (candidates.length < MINIMUM_VOICES) {
      throw new Error(
        `scene mixer には model ごとに loop_ok の selected dry clip が3件以上必要です: ${scenario.id}/${model.id} (${candidates.length})`,
      );
    }
    return {
      key: JSON.stringify([scenario.id, model.id, "scene-mix"]),
      model,
      candidates,
    };
  });
}

function uniqueIndex<T>(
  values: readonly T[],
  kind: "line" | "model",
  getId: (value: T) => string,
): ReadonlyMap<string, T> {
  const result = new Map<string, T>();
  for (const value of values) {
    const id = getId(value);
    if (result.has(id)) {
      throw new Error(`scene mixer ${kind} id が重複しています: ${id}`);
    }
    result.set(id, value);
  }
  return result;
}

function assertCandidateMatchesGroup(
  candidate: PublishedCandidate,
  group: ArtifactOutcome["group"],
): void {
  for (const key of ["model", "scenario", "line", "variant"] as const) {
    if (candidate[key] !== group[key]) {
      throw new Error(`scene mixer candidate.${key} が group と一致しません。`);
    }
  }
}
