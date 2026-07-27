import { describe, expect, it } from "vite-plus/test";

import { calculateRtfStatistics, collectGenerationParameterSets } from "./detail-page-model";

describe("calculateRtfStatistics", () => {
  it("音声時間で重み付けした平均と範囲を返す", () => {
    const result = calculateRtfStatistics([
      { duration_sec: 1, rtf: 0.2 },
      { duration_sec: 3, rtf: 0.6 },
    ]);

    expect(result?.weightedMean).toBeCloseTo(0.5);
    expect(result?.minimum).toBe(0.2);
    expect(result?.maximum).toBe(0.6);
  });

  it("clip がなければ未計測、0秒 clip は fail fast する", () => {
    expect(calculateRtfStatistics([])).toBeNull();
    expect(() => calculateRtfStatistics([{ duration_sec: 0, rtf: 0.2 }])).toThrow("正の音声時間");
  });
});

describe("collectGenerationParameterSets", () => {
  it("同じ生成パラメータを clip 数つきで集約する", () => {
    expect(
      collectGenerationParameterSets([
        { gen_params: { seed: 1, temperature: 0.7 } },
        { gen_params: { seed: 1, temperature: 0.7 } },
        { gen_params: { seed: 2 } },
      ]),
    ).toEqual([
      {
        parameters: { seed: 1, temperature: 0.7 },
        clipCount: 2,
      },
      {
        parameters: { seed: 2 },
        clipCount: 1,
      },
    ]);
  });
});
