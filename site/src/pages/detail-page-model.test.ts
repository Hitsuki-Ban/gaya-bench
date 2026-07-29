import { describe, expect, it } from "vite-plus/test";

import type { Character, Clip, GenerationFailure, Line, Scenario } from "@/data";
import {
  buildModelFailureEntries,
  buildScenarioLineEntries,
  calculateRtfStatistics,
  collectGenerationParameterSets,
} from "./detail-page-model";

describe("detail generation results", () => {
  it("scenario line ごとに成功、生成失敗、未生成を分離する", () => {
    const fixtureScenario = scenario();
    const entries = buildScenarioLineEntries(fixtureScenario, [clip()], [failure()]);

    expect(entries[0]).toMatchObject({
      line: { id: "line-1" },
      clips: [{ model: "alpha" }],
      failures: [{ model: "beta", reason: "generation_failed" }],
    });
    expect(entries[1]).toMatchObject({
      line: { id: "line-2" },
      clips: [],
      failures: [],
    });
  });

  it("model の生成失敗へ scenario、line、character を関連付ける", () => {
    const entries = buildModelFailureEntries("beta", [failure()], [scenario()]);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      failure: { model: "beta", reason: "generation_failed" },
      scenario: { id: "scenario" },
      line: { id: "line-1" },
      character: { id: "speaker" },
    });
  });
});

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

function scenario(): Scenario {
  const speaker: Character = {
    id: "speaker",
    name: "話者",
    kind: "human",
    gender: "neutral",
    age: "adult",
    voice: "自然な声",
  };
  return {
    format_version: 1,
    id: "scenario",
    title: "Scenario",
    locale: "ja",
    scene: { setting: "Test" },
    characters: [speaker],
    lines: [line("line-1"), line("line-2")],
  };
}

function line(id: string): Line {
  return {
    id,
    character: "speaker",
    text: id,
    emotion: "neutral",
    intensity: 2,
    delivery: "自然に",
    difficulty: "standard",
    loop_ok: true,
    final_intonation: "fall",
  };
}

function clip(): Clip {
  return {
    model: "alpha",
    scenario: "scenario",
    line: "line-1",
    variant: "dry",
    path: "audio/alpha/scenario/line-1.opus",
    duration_sec: 1,
    sha256: "hash",
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

function failure(): GenerationFailure {
  return {
    model: "beta",
    scenario: "scenario",
    line: "line-1",
    variant: "dry",
    reason: "generation_failed",
  };
}
