import type { ArtifactOutcome, Character, Line, PublishedCandidate, Scenario } from "@/data";

export interface ScenarioLineEntry {
  readonly line: Line;
  readonly character: Character;
  readonly outcomes: readonly ArtifactOutcome[];
}

export interface ModelCandidateEntry {
  readonly candidate: PublishedCandidate;
  readonly scenario: Scenario;
  readonly line: Line;
  readonly character: Character;
}

export interface ModelOutcomeEntry {
  readonly outcome: Exclude<ArtifactOutcome, { readonly kind: "selected" }>;
  readonly scenario: Scenario;
  readonly line: Line;
  readonly character: Character;
}

export interface RtfStatistics {
  readonly weightedMean: number;
  readonly minimum: number;
  readonly maximum: number;
}

export function buildScenarioLineEntries(
  scenario: Scenario,
  outcomes: readonly ArtifactOutcome[],
): readonly ScenarioLineEntry[] {
  const characterById = new Map(scenario.characters.map((character) => [character.id, character]));
  const outcomesByLineId = new Map(
    scenario.lines.map((line) => [line.id, [] as ArtifactOutcome[]]),
  );

  for (const outcome of outcomes) {
    const lineOutcomes = outcomesByLineId.get(outcome.group.line);
    if (!lineOutcomes) {
      throw new Error(
        `scenario ${scenario.id} に outcome line ${outcome.group.line} が存在しません。`,
      );
    }
    lineOutcomes.push(outcome);
  }

  return scenario.lines.map((line) => {
    const character = characterById.get(line.character);
    if (!character) {
      throw new Error(`scenario ${scenario.id} に character ${line.character} が存在しません。`);
    }
    return {
      line,
      character,
      outcomes: outcomesByLineId.get(line.id)!,
    };
  });
}

export function buildModelCandidateEntries(
  modelId: string,
  outcomes: readonly ArtifactOutcome[],
  scenarios: readonly Scenario[],
): readonly ModelCandidateEntry[] {
  const scenarioIndex = buildScenarioIndex(scenarios);
  const entries: ModelCandidateEntry[] = [];
  for (const outcome of outcomes) {
    if (outcome.kind !== "selected" || outcome.group.model !== modelId) {
      continue;
    }
    const { scenario, line, character } = resolveLineContext(
      scenarioIndex,
      outcome.group.scenario,
      outcome.group.line,
    );
    entries.push({ candidate: outcome.candidate, scenario, line, character });
  }
  return entries;
}

export function buildModelOutcomeEntries(
  modelId: string,
  outcomes: readonly ArtifactOutcome[],
  scenarios: readonly Scenario[],
): readonly ModelOutcomeEntry[] {
  const scenarioIndex = buildScenarioIndex(scenarios);
  const entries: ModelOutcomeEntry[] = [];
  for (const outcome of outcomes) {
    if (outcome.kind === "selected" || outcome.group.model !== modelId) {
      continue;
    }
    const { scenario, line, character } = resolveLineContext(
      scenarioIndex,
      outcome.group.scenario,
      outcome.group.line,
    );
    entries.push({ outcome, scenario, line, character });
  }
  return entries;
}

export function calculateRtfStatistics(
  candidates: readonly Pick<PublishedCandidate, "duration_sec" | "rtf">[],
): RtfStatistics | null {
  if (candidates.length === 0) {
    return null;
  }

  let totalDuration = 0;
  let weightedRtf = 0;
  let minimum = Number.POSITIVE_INFINITY;
  let maximum = Number.NEGATIVE_INFINITY;

  for (const candidate of candidates) {
    if (candidate.duration_sec <= 0) {
      throw new Error("RTF の集計には正の音声時間が必要です。");
    }
    totalDuration += candidate.duration_sec;
    weightedRtf += candidate.rtf * candidate.duration_sec;
    minimum = Math.min(minimum, candidate.rtf);
    maximum = Math.max(maximum, candidate.rtf);
  }

  return {
    weightedMean: weightedRtf / totalDuration,
    minimum,
    maximum,
  };
}

interface IndexedScenario {
  readonly scenario: Scenario;
  readonly lineById: ReadonlyMap<string, Line>;
  readonly characterById: ReadonlyMap<string, Character>;
}

function buildScenarioIndex(scenarios: readonly Scenario[]): ReadonlyMap<string, IndexedScenario> {
  return new Map(
    scenarios.map((scenario) => [
      scenario.id,
      {
        scenario,
        lineById: new Map(scenario.lines.map((line) => [line.id, line])),
        characterById: new Map(scenario.characters.map((character) => [character.id, character])),
      },
    ]),
  );
}

function resolveLineContext(
  scenarioIndex: ReadonlyMap<string, IndexedScenario>,
  scenarioId: string,
  lineId: string,
): { readonly scenario: Scenario; readonly line: Line; readonly character: Character } {
  const indexedScenario = scenarioIndex.get(scenarioId);
  if (!indexedScenario) {
    throw new Error(`outcome が未知の scenario ${scenarioId} を参照しています。`);
  }
  const line = indexedScenario.lineById.get(lineId);
  if (!line) {
    throw new Error(`outcome が未知の line ${scenarioId}/${lineId} を参照しています。`);
  }
  const character = indexedScenario.characterById.get(line.character);
  if (!character) {
    throw new Error(
      `line ${scenarioId}/${lineId} が未知の character ${line.character} を参照しています。`,
    );
  }
  return { scenario: indexedScenario.scenario, line, character };
}
