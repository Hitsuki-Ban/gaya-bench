import type { ComparisonModel, ComparisonRow } from "../comparison/model";
import type { Model } from "../data/types";
import {
  AGE_ORDER,
  CHARACTER_KIND_ORDER,
  CONDITIONING_MODE_ORDER,
  DIFFICULTY_ORDER,
  EMOTION_ORDER,
  GENDER_ORDER,
} from "./schema";
import type { FilterState } from "./types";

export interface ComparisonProjection {
  readonly rows: readonly {
    readonly row: ComparisonRow;
    readonly rowIndex: number;
  }[];
  readonly models: readonly Model[];
  readonly rowIndexes: ReadonlySet<number>;
  readonly modelIds: ReadonlySet<string>;
  readonly key: string;
}

export function projectComparisonModel(
  model: ComparisonModel,
  state: FilterState,
): ComparisonProjection {
  assertProjectionState(model, state);

  const matchingRows = model.rows.flatMap((row, rowIndex) =>
    rowMatches(row, state) ? [{ row, rowIndex }] : [],
  );
  const selectedModels = model.models.filter(
    (item) => state.model.has(item.id) && conditioningMatches(item, state),
  );
  const models = state.showEmpty
    ? selectedModels
    : selectedModels.filter(({ id }) =>
        matchingRows.some(
          ({ rowIndex }) => model.getCell({ rowIndex, modelId: id })?.kind === "selected",
        ),
      );
  const rows = state.showEmpty
    ? matchingRows
    : matchingRows.filter(({ rowIndex }) =>
        models.some(({ id }) => model.getCell({ rowIndex, modelId: id })?.kind === "selected"),
      );
  const rowIndexes = new Set(rows.map(({ rowIndex }) => rowIndex));
  const modelIds = new Set(models.map(({ id }) => id));

  return {
    rows,
    models,
    rowIndexes,
    modelIds,
    key: createProjectionKey(model, state),
  };
}

/**
 * 条件フィルタは **条件バリアント列だけ** を絞り込む。
 * `conditioning` を持たない単方式モデル (プリセット/クローン) は常に表示する。
 */
function conditioningMatches(item: Model, state: FilterState): boolean {
  return item.conditioning === undefined || state.conditioning.has(item.conditioning.mode);
}

function rowMatches(row: ComparisonRow, state: FilterState): boolean {
  return (
    (state.scenario === null || row.scenario.id === state.scenario) &&
    state.kind.has(row.character.kind) &&
    state.gender.has(row.character.gender) &&
    state.age.has(row.character.age) &&
    state.emotion.has(row.line.emotion) &&
    state.difficulty.has(row.line.difficulty)
  );
}

function assertProjectionState(model: ComparisonModel, state: FilterState): void {
  assertSelectedValues("kind", state.kind, CHARACTER_KIND_ORDER);
  assertSelectedValues("gender", state.gender, GENDER_ORDER);
  assertSelectedValues("age", state.age, AGE_ORDER);
  assertSelectedValues("emotion", state.emotion, EMOTION_ORDER);
  assertSelectedValues("difficulty", state.difficulty, DIFFICULTY_ORDER);
  assertSelectedValues("conditioning", state.conditioning, CONDITIONING_MODE_ORDER);
  assertSelectedValues(
    "model",
    state.model,
    model.models.map(({ id }) => id),
  );
  if (state.scenario !== null) {
    const scenarioIds = new Set(model.rows.map(({ scenario }) => scenario.id));
    if (!scenarioIds.has(state.scenario)) {
      throw new Error(`scenario filter に存在しない値が含まれています: ${state.scenario}`);
    }
  }
}

function assertSelectedValues(
  key: string,
  selected: ReadonlySet<string>,
  allowedValues: readonly string[],
): void {
  if (selected.size === 0) {
    throw new Error(`${key} filter は最低 1 件を選択する必要があります。`);
  }
  const allowed = new Set(allowedValues);
  for (const value of selected) {
    if (!allowed.has(value)) {
      throw new Error(`${key} filter に存在しない値が含まれています: ${value}`);
    }
  }
}

function createProjectionKey(model: ComparisonModel, state: FilterState): string {
  return JSON.stringify([
    state.scenario,
    CHARACTER_KIND_ORDER.filter((value) => state.kind.has(value)),
    GENDER_ORDER.filter((value) => state.gender.has(value)),
    AGE_ORDER.filter((value) => state.age.has(value)),
    EMOTION_ORDER.filter((value) => state.emotion.has(value)),
    DIFFICULTY_ORDER.filter((value) => state.difficulty.has(value)),
    model.models
      .filter((item) => state.model.has(item.id) && conditioningMatches(item, state))
      .map(({ id }) => id),
    state.showEmpty,
  ]);
}
