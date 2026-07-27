import type { Character, Clip, GenerationFailure, JsonValue, Line, Scenario } from "@/data";

export interface ScenarioLineEntry {
  readonly line: Line;
  readonly character: Character;
  readonly clips: readonly Clip[];
  readonly failures: readonly GenerationFailure[];
}

export interface ModelClipEntry {
  readonly clip: Clip;
  readonly scenario: Scenario;
  readonly line: Line;
  readonly character: Character;
}

export interface ModelFailureEntry {
  readonly failure: GenerationFailure;
  readonly scenario: Scenario;
  readonly line: Line;
  readonly character: Character;
}

export interface RtfStatistics {
  readonly weightedMean: number;
  readonly minimum: number;
  readonly maximum: number;
}

export interface GenerationParameterSet {
  readonly parameters: { readonly [key: string]: JsonValue };
  readonly clipCount: number;
}

export function buildScenarioLineEntries(
  scenario: Scenario,
  clips: readonly Clip[],
  failures: readonly GenerationFailure[],
): readonly ScenarioLineEntry[] {
  const characterById = new Map(scenario.characters.map((character) => [character.id, character]));
  const clipsByLineId = new Map(scenario.lines.map((line) => [line.id, [] as Clip[]]));
  const failuresByLineId = new Map(
    scenario.lines.map((line) => [line.id, [] as GenerationFailure[]]),
  );

  for (const clip of clips) {
    const lineClips = clipsByLineId.get(clip.line);
    if (!lineClips) {
      throw new Error(`scenario ${scenario.id} に line ${clip.line} が存在しません。`);
    }
    lineClips.push(clip);
  }
  for (const failure of failures) {
    const lineFailures = failuresByLineId.get(failure.line);
    if (!lineFailures) {
      throw new Error(`scenario ${scenario.id} に line ${failure.line} が存在しません。`);
    }
    lineFailures.push(failure);
  }

  return scenario.lines.map((line) => {
    const character = characterById.get(line.character);
    if (!character) {
      throw new Error(`scenario ${scenario.id} に character ${line.character} が存在しません。`);
    }
    return {
      line,
      character,
      clips: clipsByLineId.get(line.id)!,
      failures: failuresByLineId.get(line.id)!,
    };
  });
}

export function buildModelClipEntries(
  modelId: string,
  clips: readonly Clip[],
  scenarios: readonly Scenario[],
): readonly ModelClipEntry[] {
  const scenarioIndex = buildScenarioIndex(scenarios);

  const entries: ModelClipEntry[] = [];
  for (const clip of clips) {
    if (clip.model !== modelId) {
      continue;
    }

    const { scenario, line, character } = resolveLineContext(
      scenarioIndex,
      clip.scenario,
      clip.line,
      "clip",
    );
    entries.push({ clip, scenario, line, character });
  }

  return entries;
}

export function buildModelFailureEntries(
  modelId: string,
  failures: readonly GenerationFailure[],
  scenarios: readonly Scenario[],
): readonly ModelFailureEntry[] {
  const scenarioIndex = buildScenarioIndex(scenarios);
  const entries: ModelFailureEntry[] = [];

  for (const failure of failures) {
    if (failure.model !== modelId) {
      continue;
    }
    const { scenario, line, character } = resolveLineContext(
      scenarioIndex,
      failure.scenario,
      failure.line,
      "failure",
    );
    entries.push({ failure, scenario, line, character });
  }

  return entries;
}

export function calculateRtfStatistics(
  clips: readonly Pick<Clip, "duration_sec" | "rtf">[],
): RtfStatistics | null {
  if (clips.length === 0) {
    return null;
  }

  let totalDuration = 0;
  let weightedRtf = 0;
  let minimum = Number.POSITIVE_INFINITY;
  let maximum = Number.NEGATIVE_INFINITY;

  for (const clip of clips) {
    if (clip.duration_sec <= 0) {
      throw new Error("RTF の集計には正の音声時間が必要です。");
    }
    totalDuration += clip.duration_sec;
    weightedRtf += clip.rtf * clip.duration_sec;
    minimum = Math.min(minimum, clip.rtf);
    maximum = Math.max(maximum, clip.rtf);
  }

  return {
    weightedMean: weightedRtf / totalDuration,
    minimum,
    maximum,
  };
}

export function collectGenerationParameterSets(
  clips: readonly Pick<Clip, "gen_params">[],
): readonly GenerationParameterSet[] {
  const sets = new Map<
    string,
    {
      readonly parameters: { readonly [key: string]: JsonValue };
      clipCount: number;
    }
  >();

  for (const { gen_params: parameters } of clips) {
    const key = JSON.stringify(parameters);
    const existing = sets.get(key);
    if (existing) {
      existing.clipCount += 1;
    } else {
      sets.set(key, { parameters, clipCount: 1 });
    }
  }

  return [...sets.values()];
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
  artifactKind: "clip" | "failure",
): { readonly scenario: Scenario; readonly line: Line; readonly character: Character } {
  const indexedScenario = scenarioIndex.get(scenarioId);
  if (!indexedScenario) {
    throw new Error(`${artifactKind} が未知の scenario ${scenarioId} を参照しています。`);
  }
  const line = indexedScenario.lineById.get(lineId);
  if (!line) {
    throw new Error(`${artifactKind} が未知の line ${scenarioId}/${lineId} を参照しています。`);
  }
  const character = indexedScenario.characterById.get(line.character);
  if (!character) {
    throw new Error(
      `line ${scenarioId}/${lineId} が未知の character ${line.character} を参照しています。`,
    );
  }
  return { scenario: indexedScenario.scenario, line, character };
}
