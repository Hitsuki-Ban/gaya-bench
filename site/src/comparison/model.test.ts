import { describe, expect, it } from "vite-plus/test";

import type { BenchmarkData, Character, Clip, Line, Model, Scenario } from "../data/types";
import type { ComparisonProjection } from "../filters";
import {
  buildColumnQueue,
  buildComparisonModel,
  buildRowQueue,
  moveCursor,
  resolveCursor,
  type ComparisonModel,
} from "./model";

describe("buildComparisonModel", () => {
  it("scenario → character → line の順序と manifest の model 順序を保つ", () => {
    const data = smallFixture();
    const model = buildComparisonModel(data);

    expect(
      model.rows.map(({ scenario, character, line }) => [scenario.id, character.id, line.id]),
    ).toEqual([
      ["market", "vendor", "vendor-1"],
      ["market", "vendor", "vendor-2"],
      ["market", "guard", "guard-1"],
      ["inn", "keeper", "keeper-1"],
    ]);
    expect(model.models.map(({ id }) => id)).toEqual(["alpha", "beta", "gamma"]);
    expect(model.rows[0]?.scenario).toBe(data.scenarios[0]);
    expect(model.rows[0]?.character).toBe(data.scenarios[0]?.characters[0]);
    expect(model.rows[0]?.line).toBe(data.scenarios[0]?.lines[1]);
  });

  it("cell の成功、生成失敗、未生成を O(1) index で区別する", () => {
    const data = smallFixture();
    data.manifest.failures.push({
      model: "gamma",
      scenario: "market",
      line: "vendor-1",
      variant: "dry",
      reason: "generation_failed",
    });
    const model = buildComparisonModel(data);

    expect(model.getCell({ rowIndex: 0, modelId: "alpha" })).toMatchObject({
      kind: "success",
      clip: { path: "audio/alpha/market/vendor-1.opus" },
    });
    expect(model.getCell({ rowIndex: 0, modelId: "gamma" })).toEqual({
      kind: "failure",
      failure: data.manifest.failures[0],
    });
    expect(model.getCell({ rowIndex: 2, modelId: "alpha" })).toBeUndefined();
    expect(
      model.getCoordinateForClipKey(JSON.stringify(["gamma", "market", "vendor-2", "dry"])),
    ).toEqual({ rowIndex: 1, modelId: "gamma" });
    expect(model.getCoordinateForClipKey("missing")).toBeUndefined();
  });

  it("84 行 × 10 model × 840 clips を構築する", () => {
    const model = buildComparisonModel(syntheticFixture(84, 10));

    expect(model.rows).toHaveLength(84);
    expect(model.models).toHaveLength(10);
    for (let rowIndex = 0; rowIndex < 84; rowIndex += 1) {
      for (let modelIndex = 0; modelIndex < 10; modelIndex += 1) {
        expect(model.getCell({ rowIndex, modelId: `model-${modelIndex}` })).toMatchObject({
          kind: "success",
          clip: { line: `line-${rowIndex}` },
        });
      }
    }
  });

  it("重複 cell clip と dry 以外の variant を fail fast で拒否する", () => {
    const duplicate = smallFixture();
    duplicate.manifest.clips.push({ ...duplicate.manifest.clips[0]! });
    expect(() => buildComparisonModel(duplicate)).toThrow(
      "比較マトリクスの cell 結果が重複しています",
    );

    const nonDry = smallFixture();
    nonDry.manifest.clips[0]!.variant = "scene";
    expect(() => buildComparisonModel(nonDry)).toThrow(
      "比較マトリクスは dry variant のみを受け付けます",
    );

    const conflict = smallFixture();
    const clip = conflict.manifest.clips[0]!;
    conflict.manifest.failures.push({
      model: clip.model,
      scenario: clip.scenario,
      line: clip.line,
      variant: clip.variant,
      reason: "generation_failed",
    });
    expect(() => buildComparisonModel(conflict)).toThrow(
      "比較マトリクスの cell 結果が重複しています",
    );
  });
});

describe("cursor navigation", () => {
  it("四方向へ非環状に移動し、clip のない cell も飛ばさない", () => {
    const model = buildComparisonModel(smallFixture());
    const visible = projection(model);
    const missingCell = { rowIndex: 0, modelId: "gamma" };

    expect(moveCursor(model, missingCell, "right", visible)).toBe(missingCell);
    expect(moveCursor(model, missingCell, "left", visible)).toEqual({
      rowIndex: 0,
      modelId: "beta",
    });
    expect(moveCursor(model, missingCell, "up", visible)).toBe(missingCell);
    expect(moveCursor(model, missingCell, "down", visible)).toEqual({
      rowIndex: 1,
      modelId: "gamma",
    });

    const lastCell = { rowIndex: model.rows.length - 1, modelId: "alpha" };
    expect(moveCursor(model, lastCell, "left", visible)).toBe(lastCell);
    expect(moveCursor(model, lastCell, "down", visible)).toBe(lastCell);
  });

  it("非表示列を横移動から除外する", () => {
    const model = buildComparisonModel(smallFixture());
    const visible = projection(model, ["alpha", "gamma"]);

    expect(moveCursor(model, { rowIndex: 0, modelId: "alpha" }, "right", visible)).toEqual({
      rowIndex: 0,
      modelId: "gamma",
    });
    expect(moveCursor(model, { rowIndex: 0, modelId: "gamma" }, "left", visible)).toEqual({
      rowIndex: 0,
      modelId: "alpha",
    });
  });

  it("filter で非表示になった行を上下移動から除外する", () => {
    const model = buildComparisonModel(smallFixture());
    const filtered = projection(model, undefined, [0, 2, 3]);

    expect(moveCursor(model, { rowIndex: 0, modelId: "alpha" }, "down", filtered)).toEqual({
      rowIndex: 2,
      modelId: "alpha",
    });
    expect(resolveCursor(model, { rowIndex: 1, modelId: "alpha" }, filtered)).toEqual({
      rowIndex: 2,
      modelId: "alpha",
    });
    expect(resolveCursor(model, { rowIndex: 3, modelId: "alpha" }, filtered)).toEqual({
      rowIndex: 3,
      modelId: "alpha",
    });
  });

  it("列の可視性変更後、右側を優先して合法 cursor を確定する", () => {
    const model = buildComparisonModel(smallFixture());
    const cursor = { rowIndex: 1, modelId: "beta" };

    expect(resolveCursor(model, cursor, projection(model, ["alpha", "gamma"]))).toEqual({
      rowIndex: 1,
      modelId: "gamma",
    });
    expect(resolveCursor(model, cursor, projection(model, ["alpha"]))).toEqual({
      rowIndex: 1,
      modelId: "alpha",
    });
    expect(resolveCursor(model, null, projection(model, ["beta"]))).toEqual({
      rowIndex: 0,
      modelId: "beta",
    });
    expect(resolveCursor(model, cursor, projection(model, ["alpha"], []))).toBeNull();
  });
});

describe("playback queues", () => {
  it("row queue は現在 model から可視列末尾までの clip と skip 数を返す", () => {
    const data = smallFixture();
    data.manifest.failures.push({
      model: "gamma",
      scenario: "market",
      line: "vendor-1",
      variant: "dry",
      reason: "generation_failed",
    });
    const model = buildComparisonModel(data);
    const queue = buildRowQueue(
      model,
      { rowIndex: 0, modelId: "alpha" },
      projection(model, ["alpha", "gamma"]),
    );

    expect(queue.items.map(({ coordinate }) => coordinate)).toEqual([
      { rowIndex: 0, modelId: "alpha" },
    ]);
    expect(queue.items[0]?.clip.model).toBe("alpha");
    expect(queue.skippedCount).toBe(1);

    const laterQueue = buildRowQueue(model, { rowIndex: 1, modelId: "beta" }, projection(model));
    expect(laterQueue.items.map(({ coordinate }) => coordinate)).toEqual([
      { rowIndex: 1, modelId: "gamma" },
    ]);
    expect(laterQueue.skippedCount).toBe(1);
  });

  it("column queue は現在行から同じ scenario の末尾までに限定する", () => {
    const model = buildComparisonModel(smallFixture());
    const queue = buildColumnQueue(model, { rowIndex: 0, modelId: "alpha" }, projection(model));

    expect(queue.items.map(({ coordinate }) => coordinate)).toEqual([
      { rowIndex: 0, modelId: "alpha" },
      { rowIndex: 1, modelId: "alpha" },
    ]);
    expect(queue.skippedCount).toBe(1);
    expect(queue.items.every(({ clip }) => clip.scenario === "market")).toBe(true);
  });

  it("column queue は filter 後の表示行だけを再生する", () => {
    const model = buildComparisonModel(smallFixture());
    const queue = buildColumnQueue(
      model,
      { rowIndex: 0, modelId: "alpha" },
      projection(model, undefined, [0, 2, 3]),
    );

    expect(queue.items.map(({ coordinate }) => coordinate)).toEqual([
      { rowIndex: 0, modelId: "alpha" },
    ]);
    expect(queue.skippedCount).toBe(1);
  });
});

function projection(
  model: ComparisonModel,
  modelIds: readonly string[] = model.models.map(({ id }) => id),
  rowIndexes: readonly number[] = model.rows.map((_, rowIndex) => rowIndex),
): ComparisonProjection {
  const selectedModelIds = new Set(modelIds);
  const selectedRowIndexes = new Set(rowIndexes);
  return {
    rows: rowIndexes.map((rowIndex) => ({ row: model.rows[rowIndex]!, rowIndex })),
    models: model.models.filter(({ id }) => selectedModelIds.has(id)),
    rowIndexes: selectedRowIndexes,
    modelIds: selectedModelIds,
    key: JSON.stringify([rowIndexes, modelIds]),
  };
}

interface MutableManifest {
  format_version: 3;
  generated_at: string;
  models: Model[];
  clips: MutableClip[];
  failures: MutableGenerationFailure[];
}

interface MutableClip extends Omit<Clip, "variant"> {
  variant: string;
}

interface MutableGenerationFailure {
  model: string;
  scenario: string;
  line: string;
  variant: string;
  reason: "generation_failed";
}

interface MutableBenchmarkData extends Omit<BenchmarkData, "manifest"> {
  manifest: MutableManifest;
}

function smallFixture(): MutableBenchmarkData {
  const vendor = character("vendor", "商人");
  const guard = character("guard", "衛兵");
  const keeper = character("keeper", "宿屋主人");
  const marketLines = [
    line("guard-1", "guard", "通行の邪魔だ。"),
    line("vendor-1", "vendor", "安いよ！"),
    line("vendor-2", "vendor", "見ていって！"),
  ];
  const innLines = [line("keeper-1", "keeper", "いらっしゃい。")];
  const scenarios = [
    scenario("market", [vendor, guard], marketLines),
    scenario("inn", [keeper], innLines),
  ];
  const models = [ttsModel("alpha"), ttsModel("beta"), ttsModel("gamma")];
  const clips = [
    clip("alpha", "market", "vendor-1"),
    clip("beta", "market", "vendor-1"),
    clip("alpha", "market", "vendor-2"),
    clip("gamma", "market", "vendor-2"),
    clip("beta", "market", "guard-1"),
    clip("gamma", "market", "guard-1"),
    clip("alpha", "inn", "keeper-1"),
    clip("beta", "inn", "keeper-1"),
    clip("gamma", "inn", "keeper-1"),
  ];
  return {
    manifest: {
      format_version: 3,
      generated_at: "2026-07-28T00:00:00Z",
      models,
      clips,
      failures: [],
    },
    scenarios,
  };
}

function syntheticFixture(rowCount: number, modelCount: number): BenchmarkData {
  const speaker = character("speaker", "話者");
  const lines = Array.from({ length: rowCount }, (_, index) =>
    line(`line-${index}`, "speaker", `台詞 ${index}`),
  );
  const models = Array.from({ length: modelCount }, (_, index) => ttsModel(`model-${index}`));
  const clips = lines.flatMap((fixtureLine) =>
    models.map((fixtureModel) => clip(fixtureModel.id, "synthetic", fixtureLine.id)),
  );
  return {
    manifest: {
      format_version: 3,
      generated_at: "2026-07-28T00:00:00Z",
      models,
      clips,
      failures: [],
    },
    scenarios: [scenario("synthetic", [speaker], lines)],
  };
}

function scenario(id: string, characters: readonly Character[], lines: readonly Line[]): Scenario {
  return {
    format_version: 1,
    id,
    title: id,
    locale: "ja",
    scene: { setting: "テスト" },
    characters,
    lines,
  };
}

function character(id: string, name: string): Character {
  return {
    id,
    name,
    kind: "human",
    gender: "neutral",
    age: "adult",
    voice: "自然な声",
  };
}

function line(id: string, characterId: string, text: string): Line {
  return {
    id,
    character: characterId,
    text,
    emotion: "neutral",
    intensity: 2,
    delivery: "自然に",
    difficulty: "standard",
    loop_ok: true,
    final_intonation: "fall",
  };
}

function ttsModel(id: string): Model {
  return {
    id,
    name: id,
    version: "1",
    license_note: "テスト",
    capabilities: {
      emotion: false,
      voice_prompt: false,
      clone: false,
      nonverbal: false,
      reading: false,
    },
  };
}

function clip(model: string, scenarioId: string, lineId: string): MutableClip {
  return {
    model,
    scenario: scenarioId,
    line: lineId,
    variant: "dry",
    path: `audio/${model}/${scenarioId}/${lineId}.opus`,
    duration_sec: 1,
    sha256: `${model}-${scenarioId}-${lineId}`,
    gen_params: {},
    rtf: 0.1,
    loudness: {
      source: "encoded_opus",
      i_lufs: -18,
      tp_dbtp: -1,
      shortfall: false,
    },
  };
}
