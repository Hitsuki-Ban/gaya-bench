import type { Character, Clip, JsonValue, Line, Scenario } from "@/data";

export interface ScenarioLineEntry {
  readonly line: Line;
  readonly character: Character;
  readonly clips: readonly Clip[];
}

export interface ModelClipEntry {
  readonly clip: Clip;
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
): readonly ScenarioLineEntry[] {
  const characterById = new Map(scenario.characters.map((character) => [character.id, character]));
  const clipsByLineId = new Map(scenario.lines.map((line) => [line.id, [] as Clip[]]));

  for (const clip of clips) {
    const lineClips = clipsByLineId.get(clip.line);
    if (!lineClips) {
      throw new Error(`scenario ${scenario.id} に line ${clip.line} が存在しません。`);
    }
    lineClips.push(clip);
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
    };
  });
}

export function buildModelClipEntries(
  modelId: string,
  clips: readonly Clip[],
  scenarios: readonly Scenario[],
): readonly ModelClipEntry[] {
  const scenarioIndex = new Map<
    string,
    {
      readonly scenario: Scenario;
      readonly lineById: ReadonlyMap<string, Line>;
      readonly characterById: ReadonlyMap<string, Character>;
    }
  >();

  for (const scenario of scenarios) {
    scenarioIndex.set(scenario.id, {
      scenario,
      lineById: new Map(scenario.lines.map((line) => [line.id, line])),
      characterById: new Map(scenario.characters.map((character) => [character.id, character])),
    });
  }

  const entries: ModelClipEntry[] = [];
  for (const clip of clips) {
    if (clip.model !== modelId) {
      continue;
    }

    const indexedScenario = scenarioIndex.get(clip.scenario);
    if (!indexedScenario) {
      throw new Error(`clip が未知の scenario ${clip.scenario} を参照しています。`);
    }
    const line = indexedScenario.lineById.get(clip.line);
    if (!line) {
      throw new Error(`clip が未知の line ${clip.scenario}/${clip.line} を参照しています。`);
    }
    const character = indexedScenario.characterById.get(line.character);
    if (!character) {
      throw new Error(
        `line ${clip.scenario}/${clip.line} が未知の character ${line.character} を参照しています。`,
      );
    }
    entries.push({ clip, scenario: indexedScenario.scenario, line, character });
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
