import type { BenchmarkData, Character, Clip, Line, Model, Scenario } from "../data/types";
import type { ComparisonProjection } from "../filters";

export interface ComparisonRow {
  readonly scenario: Scenario;
  readonly character: Character;
  readonly line: Line;
}

export interface Coordinate {
  readonly rowIndex: number;
  readonly modelId: string;
}

export type NavigationDirection = "left" | "right" | "up" | "down";

export interface QueueItem {
  readonly coordinate: Coordinate;
  readonly clip: Clip;
}

export interface PlaybackQueue {
  readonly items: readonly QueueItem[];
  readonly skippedCount: number;
}

export interface ComparisonModel {
  readonly rows: readonly ComparisonRow[];
  readonly models: readonly Model[];
  getClip(coordinate: Coordinate): Clip | undefined;
  getCoordinateForClipKey(key: string): Coordinate | undefined;
}

export function buildComparisonModel(data: BenchmarkData): ComparisonModel {
  const modelIds = uniqueModelIds(data.manifest.models);
  const scenarioIds = new Set<string>();
  const rowIndexByLine = new Map<string, number>();
  const rows: ComparisonRow[] = [];

  for (const scenario of data.scenarios) {
    if (scenarioIds.has(scenario.id)) {
      throw new Error(`scenario id が重複しています: ${scenario.id}`);
    }
    scenarioIds.add(scenario.id);

    const charactersById = new Map<string, Character>();
    const linesByCharacter = new Map<string, Line[]>();
    for (const character of scenario.characters) {
      if (charactersById.has(character.id)) {
        throw new Error(
          `character id が scenario 内で重複しています: ${scenario.id}/${character.id}`,
        );
      }
      charactersById.set(character.id, character);
      linesByCharacter.set(character.id, []);
    }

    const lineIds = new Set<string>();
    for (const line of scenario.lines) {
      if (lineIds.has(line.id)) {
        throw new Error(`line id が scenario 内で重複しています: ${scenario.id}/${line.id}`);
      }
      lineIds.add(line.id);

      const characterLines = linesByCharacter.get(line.character);
      if (!characterLines) {
        throw new Error(
          `line が存在しない character を参照しています: ${scenario.id}/${line.id} -> ${line.character}`,
        );
      }
      characterLines.push(line);
    }

    for (const character of scenario.characters) {
      const characterLines = linesByCharacter.get(character.id);
      if (!characterLines) {
        throw new Error(
          `character の行グループを構築できませんでした: ${scenario.id}/${character.id}`,
        );
      }
      for (const line of characterLines) {
        const rowIndex = rows.length;
        rows.push({ scenario, character, line });
        rowIndexByLine.set(lineKey(scenario.id, line.id), rowIndex);
      }
    }
  }

  const clipsByRow = new Map<number, Map<string, Clip>>();
  const coordinateByClipKey = new Map<string, Coordinate>();
  for (const clip of data.manifest.clips) {
    if (clip.variant !== "dry") {
      throw new Error(
        `比較マトリクスは dry variant のみを受け付けます: ${clip.model}/${clip.scenario}/${clip.line}/${clip.variant}`,
      );
    }
    if (!modelIds.has(clip.model)) {
      throw new Error(`clip が存在しない model を参照しています: ${clip.model}`);
    }

    const rowIndex = rowIndexByLine.get(lineKey(clip.scenario, clip.line));
    if (rowIndex === undefined) {
      throw new Error(
        `clip が存在しない scenario/line を参照しています: ${clip.scenario}/${clip.line}`,
      );
    }

    let clipsByModel = clipsByRow.get(rowIndex);
    if (!clipsByModel) {
      clipsByModel = new Map<string, Clip>();
      clipsByRow.set(rowIndex, clipsByModel);
    }
    if (clipsByModel.has(clip.model)) {
      throw new Error(
        `比較マトリクスの cell clip が重複しています: ${clip.scenario}/${clip.line}/${clip.model}`,
      );
    }
    clipsByModel.set(clip.model, clip);
    coordinateByClipKey.set(comparisonClipKey(clip), {
      rowIndex,
      modelId: clip.model,
    });
  }

  return {
    rows,
    models: data.manifest.models,
    getClip(coordinate) {
      assertCoordinate(rows, modelIds, coordinate);
      return clipsByRow.get(coordinate.rowIndex)?.get(coordinate.modelId);
    },
    getCoordinateForClipKey(key) {
      return coordinateByClipKey.get(key);
    },
  };
}

export function resolveCursor(
  model: ComparisonModel,
  cursor: Coordinate | null,
  projection: ComparisonProjection,
): Coordinate | null {
  if (projection.rows.length === 0 || projection.models.length === 0) {
    return null;
  }
  if (cursor === null) {
    return {
      rowIndex: projection.rows[0]!.rowIndex,
      modelId: projection.models[0]!.id,
    };
  }

  assertCoordinate(model.rows, getModelIds(model), cursor);
  const rowIndex = resolveRowIndex(cursor.rowIndex, projection);
  const modelId = resolveModelId(model, cursor.modelId, projection.modelIds);
  return { rowIndex, modelId };
}

export function moveCursor(
  model: ComparisonModel,
  cursor: Coordinate,
  direction: NavigationDirection,
  projection: ComparisonProjection,
): Coordinate {
  assertProjectedCoordinate(model, projection, cursor);
  const visibleColumnIndex = projection.models.findIndex(({ id }) => id === cursor.modelId);
  if (visibleColumnIndex < 0) {
    throw new Error(`cursor の model は非表示です: ${cursor.modelId}`);
  }
  const visibleRowIndex = projection.rows.findIndex(({ rowIndex }) => rowIndex === cursor.rowIndex);
  if (visibleRowIndex < 0) {
    throw new Error(`cursor の row は非表示です: ${cursor.rowIndex}`);
  }

  if (direction === "up") {
    const previousRow = projection.rows[visibleRowIndex - 1];
    return previousRow ? { ...cursor, rowIndex: previousRow.rowIndex } : cursor;
  }
  if (direction === "down") {
    const nextRow = projection.rows[visibleRowIndex + 1];
    return nextRow ? { ...cursor, rowIndex: nextRow.rowIndex } : cursor;
  }

  const nextColumnIndex = direction === "left" ? visibleColumnIndex - 1 : visibleColumnIndex + 1;
  const nextModel = projection.models[nextColumnIndex];
  return nextModel ? { rowIndex: cursor.rowIndex, modelId: nextModel.id } : cursor;
}

export function buildRowQueue(
  model: ComparisonModel,
  cursor: Coordinate,
  projection: ComparisonProjection,
): PlaybackQueue {
  assertProjectedCoordinate(model, projection, cursor);
  const startIndex = projection.models.findIndex(({ id }) => id === cursor.modelId);
  if (startIndex < 0) {
    throw new Error(`cursor の model は非表示です: ${cursor.modelId}`);
  }

  const items: QueueItem[] = [];
  let skippedCount = 0;
  for (const visibleModel of projection.models.slice(startIndex)) {
    const coordinate = { rowIndex: cursor.rowIndex, modelId: visibleModel.id };
    const clip = model.getClip(coordinate);
    if (clip) {
      items.push({ coordinate, clip });
    } else {
      skippedCount += 1;
    }
  }
  return { items, skippedCount };
}

export function buildColumnQueue(
  model: ComparisonModel,
  cursor: Coordinate,
  projection: ComparisonProjection,
): PlaybackQueue {
  assertProjectedCoordinate(model, projection, cursor);
  const scenarioId = model.rows[cursor.rowIndex]!.scenario.id;
  const items: QueueItem[] = [];
  let skippedCount = 0;
  const startIndex = projection.rows.findIndex(({ rowIndex }) => rowIndex === cursor.rowIndex);
  if (startIndex < 0) {
    throw new Error(`cursor の row は非表示です: ${cursor.rowIndex}`);
  }

  for (
    let visibleRowIndex = startIndex;
    visibleRowIndex < projection.rows.length;
    visibleRowIndex += 1
  ) {
    const rowIndex = projection.rows[visibleRowIndex]!.rowIndex;
    if (model.rows[rowIndex]!.scenario.id !== scenarioId) {
      break;
    }
    const coordinate = { rowIndex, modelId: cursor.modelId };
    const clip = model.getClip(coordinate);
    if (clip) {
      items.push({ coordinate, clip });
    } else {
      skippedCount += 1;
    }
  }
  return { items, skippedCount };
}

function uniqueModelIds(models: readonly Model[]): ReadonlySet<string> {
  const modelIds = new Set<string>();
  for (const model of models) {
    if (modelIds.has(model.id)) {
      throw new Error(`model id が重複しています: ${model.id}`);
    }
    modelIds.add(model.id);
  }
  return modelIds;
}

function getModelIds(model: ComparisonModel): ReadonlySet<string> {
  return new Set(model.models.map(({ id }) => id));
}

function assertCoordinate(
  rows: readonly ComparisonRow[],
  modelIds: ReadonlySet<string>,
  coordinate: Coordinate,
): void {
  if (
    !Number.isInteger(coordinate.rowIndex) ||
    coordinate.rowIndex < 0 ||
    coordinate.rowIndex >= rows.length
  ) {
    throw new Error(`cursor の rowIndex が範囲外です: ${coordinate.rowIndex}`);
  }
  if (!modelIds.has(coordinate.modelId)) {
    throw new Error(`cursor の model id が存在しません: ${coordinate.modelId}`);
  }
}

function assertProjectedCoordinate(
  model: ComparisonModel,
  projection: ComparisonProjection,
  coordinate: Coordinate,
): void {
  assertCoordinate(model.rows, getModelIds(model), coordinate);
  if (!projection.rowIndexes.has(coordinate.rowIndex)) {
    throw new Error(`cursor の row は非表示です: ${coordinate.rowIndex}`);
  }
  if (!projection.modelIds.has(coordinate.modelId)) {
    throw new Error(`cursor の model は非表示です: ${coordinate.modelId}`);
  }
}

function resolveRowIndex(currentRowIndex: number, projection: ComparisonProjection): number {
  if (projection.rowIndexes.has(currentRowIndex)) {
    return currentRowIndex;
  }
  return (
    projection.rows.find(({ rowIndex }) => rowIndex >= currentRowIndex)?.rowIndex ??
    projection.rows.at(-1)!.rowIndex
  );
}

function resolveModelId(
  model: ComparisonModel,
  currentModelId: string,
  visibleModelIds: ReadonlySet<string>,
): string {
  if (visibleModelIds.has(currentModelId)) {
    return currentModelId;
  }

  const currentModelIndex = model.models.findIndex(({ id }) => id === currentModelId);
  const modelOnRight = model.models
    .slice(currentModelIndex + 1)
    .find(({ id }) => visibleModelIds.has(id));
  if (modelOnRight) {
    return modelOnRight.id;
  }

  const modelOnLeft = model.models
    .slice(0, currentModelIndex)
    .findLast(({ id }) => visibleModelIds.has(id));
  if (!modelOnLeft) {
    throw new Error("表示中の model から cursor を解決できませんでした。");
  }
  return modelOnLeft.id;
}

function lineKey(scenarioId: string, lineId: string): string {
  return JSON.stringify([scenarioId, lineId]);
}

function comparisonClipKey(clip: Clip): string {
  return JSON.stringify([clip.model, clip.scenario, clip.line, clip.variant]);
}
