import type { Age, BenchmarkData, CharacterKind, Difficulty, Emotion, Gender } from "../data/types";
import type { FilterState, FilterValueByKey, MultiFilterKey } from "./types";

export const CHARACTER_KIND_ORDER = [
  "human",
  "machine",
  "creature",
  "spirit",
] as const satisfies readonly CharacterKind[];
export const GENDER_ORDER = ["female", "male", "neutral"] as const satisfies readonly Gender[];
export const AGE_ORDER = [
  "child",
  "teen",
  "young_adult",
  "adult",
  "middle_aged",
  "elderly",
] as const satisfies readonly Age[];
export const EMOTION_ORDER = [
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
] as const satisfies readonly Emotion[];
export const DIFFICULTY_ORDER = ["standard", "hard"] as const satisfies readonly Difficulty[];

export function createDefaultFilterState(data: BenchmarkData): FilterState {
  const { modelIds } = getDataFilterValues(data);
  return {
    scenario: null,
    showEmpty: false,
    kind: new Set(CHARACTER_KIND_ORDER),
    gender: new Set(GENDER_ORDER),
    age: new Set(AGE_ORDER),
    emotion: new Set(EMOTION_ORDER),
    difficulty: new Set(DIFFICULTY_ORDER),
    model: new Set(modelIds),
  };
}

export function updateEmptyFilter(state: FilterState, showEmpty: boolean): FilterState {
  return { ...state, showEmpty };
}

export function updateScenarioFilter(
  state: FilterState,
  scenario: string | null,
  data: BenchmarkData,
): FilterState {
  const { scenarioIds } = getDataFilterValues(data);
  if (scenario !== null && !scenarioIds.includes(scenario)) {
    throw new Error(`存在しない scenario は選択できません: ${scenario}`);
  }
  return { ...state, scenario };
}

export function toggleFilterValue<K extends MultiFilterKey>(
  state: FilterState,
  key: K,
  value: FilterValueByKey[K],
  data: BenchmarkData,
): FilterState {
  const selected = state[key] as ReadonlySet<FilterValueByKey[K]>;
  if (selected.has(value)) {
    if (selected.size === 1) {
      throw new Error(`${key} filter は最低 1 件を選択する必要があります。`);
    }
    return updateFilterValues(
      state,
      key,
      [...selected].filter((selectedValue) => selectedValue !== value),
      data,
    );
  }
  return updateFilterValues(state, key, [...selected, value], data);
}

export function updateFilterValues<K extends MultiFilterKey>(
  state: FilterState,
  key: K,
  values: Iterable<FilterValueByKey[K]>,
  data: BenchmarkData,
): FilterState {
  const allowedValues = getAllowedValues(key, data);
  const requestedValues = new Set<string>(values);
  if (requestedValues.size === 0) {
    throw new Error(`${key} filter は最低 1 件を選択する必要があります。`);
  }
  for (const value of requestedValues) {
    if (!allowedValues.includes(value)) {
      throw new Error(`${key} filter に存在しない値が含まれています: ${value}`);
    }
  }

  const orderedValues = new Set(allowedValues.filter((value) => requestedValues.has(value)));
  return replaceFilterSet(state, key, orderedValues);
}

export function getDataFilterValues(data: BenchmarkData): {
  readonly scenarioIds: readonly string[];
  readonly modelIds: readonly string[];
} {
  const scenarioIds = uniqueIds(
    data.scenarios.map(({ id }) => id),
    "scenario",
  );
  const modelIds = uniqueIds(
    data.manifest.models
      .filter((model) =>
        data.outcomes.some(
          (outcome) => outcome.kind === "selected" && outcome.group.model === model.id,
        ),
      )
      .map(({ id }) => id),
    "model",
  );
  if (modelIds.length === 0) {
    throw new Error("filter state の構築には 1 件以上の model が必要です。");
  }
  return { scenarioIds, modelIds };
}

export function getAllowedValues(key: MultiFilterKey, data: BenchmarkData): readonly string[] {
  if (key === "kind") {
    return CHARACTER_KIND_ORDER;
  }
  if (key === "gender") {
    return GENDER_ORDER;
  }
  if (key === "age") {
    return AGE_ORDER;
  }
  if (key === "emotion") {
    return EMOTION_ORDER;
  }
  if (key === "difficulty") {
    return DIFFICULTY_ORDER;
  }
  return getDataFilterValues(data).modelIds;
}

function replaceFilterSet(
  state: FilterState,
  key: MultiFilterKey,
  values: ReadonlySet<string>,
): FilterState {
  if (key === "kind") {
    return {
      ...state,
      kind: new Set(CHARACTER_KIND_ORDER.filter((value) => values.has(value))),
    };
  }
  if (key === "gender") {
    return { ...state, gender: new Set(GENDER_ORDER.filter((value) => values.has(value))) };
  }
  if (key === "age") {
    return { ...state, age: new Set(AGE_ORDER.filter((value) => values.has(value))) };
  }
  if (key === "emotion") {
    return { ...state, emotion: new Set(EMOTION_ORDER.filter((value) => values.has(value))) };
  }
  if (key === "difficulty") {
    return {
      ...state,
      difficulty: new Set(DIFFICULTY_ORDER.filter((value) => values.has(value))),
    };
  }
  return { ...state, model: new Set(values) };
}

function uniqueIds(ids: readonly string[], kind: "scenario" | "model"): readonly string[] {
  const seen = new Set<string>();
  for (const id of ids) {
    if (seen.has(id)) {
      throw new Error(`${kind} id が重複しています: ${id}`);
    }
    seen.add(id);
  }
  return ids;
}
