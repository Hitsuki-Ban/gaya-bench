import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { describe, expect, it, vi } from "vite-plus/test";

import type {
  ArtifactOutcome,
  BenchmarkData,
  ConditioningMode,
  Model,
  PublishedCandidate,
  Scenario,
} from "@/data/types";
import { createDefaultFilterState, updateFilterValues, type FilterState } from "@/filters";

const singleModeData = benchmarkFixture([ttsModel("preset", "プリセット話者モデル")]);
const variantData = benchmarkFixture([
  ttsModel("preset", "プリセット話者モデル"),
  variantModel("human-reference"),
  variantModel("text-only"),
]);

// `@/data` は build 時の virtual module なので、fixture の release で差し替える。
const dataModule = { benchmarkData: variantData, playableModels: variantData.release.models };
vi.mock("@/data", () => dataModule);

const { FilterToolbar } = await import("./filter-toolbar");

describe("FilterToolbar の条件フィルタ", () => {
  it("既定は「すべて」で、3 択の radio を出す", () => {
    const markup = render(createDefaultFilterState(variantData));

    expect(markup).toContain("条件");
    expect(markup).toContain('type="radio"');
    expect(markup).toContain('value="all"');
    expect(markup).toContain('value="human-reference"');
    expect(markup).toContain('value="text-only"');
    expect(checkedRadioValue(markup)).toBe("all");
  });

  it("選択中の条件を radio の checked として反映する", () => {
    const state = updateFilterValues(
      createDefaultFilterState(variantData),
      "conditioning",
      ["text-only"],
      variantData,
    );

    expect(checkedRadioValue(render(state))).toBe("text-only");
  });

  it("条件バリアントのない release では条件フィルタを出さない", () => {
    dataModule.benchmarkData = singleModeData;
    dataModule.playableModels = singleModeData.release.models;
    try {
      const markup = render(createDefaultFilterState(singleModeData));

      expect(markup).not.toContain('type="radio"');
      expect(markup).not.toContain("見本あり");
    } finally {
      dataModule.benchmarkData = variantData;
      dataModule.playableModels = variantData.release.models;
    }
  });
});

function render(state: FilterState): string {
  return renderToStaticMarkup(
    createElement(FilterToolbar, {
      state,
      filteredRows: 1,
      totalRows: 1,
      onChange: () => undefined,
      onReset: () => undefined,
    }),
  );
}

function checkedRadioValue(markup: string): string | undefined {
  return [...markup.matchAll(/<input[^>]*type="radio"[^>]*>/g)]
    .map(([tag]) => tag)
    .filter((tag) => tag.includes("checked"))
    .map((tag) => /value="([^"]+)"/.exec(tag)?.[1])
    .at(0);
}

function benchmarkFixture(models: readonly Model[]): BenchmarkData {
  const scenario: Scenario = {
    format_version: 1,
    id: "market",
    title: "市場",
    locale: "ja",
    scene: { setting: "試験場" },
    characters: [
      {
        id: "speaker",
        name: "話者",
        kind: "human",
        gender: "female",
        age: "adult",
        voice: "明瞭",
      },
    ],
    lines: [
      {
        id: "speaker-001",
        character: "speaker",
        text: "一つ目。",
        emotion: "neutral",
        intensity: 1,
        delivery: "自然に。",
        difficulty: "standard",
        loop_ok: true,
        final_intonation: "fall",
      },
    ],
  };
  const outcomes: ArtifactOutcome[] = models.map((model) => {
    const group = { model: model.id, scenario: "market", line: "speaker-001", variant: "dry" };
    const candidate: PublishedCandidate = {
      ...group,
      path: `audio/takes/${model.id}/market/speaker-001/dry/sample.opus`,
      duration_sec: 1,
      rtf: 0.1,
      reference_conditioning: { kind: "none" },
      role_quality: null,
      gate: { content: "pass" },
    };
    return { kind: "selected", group, candidate };
  });
  return {
    release: {
      format_version: 4,
      generated_at: "2026-07-30T00:00:00Z",
      candidate_set_sha256: "d".repeat(64),
      models,
    },
    scenarios: [scenario],
    outcomes,
    generation_profiles: [],
    credits: { model_sources: [], reference_voices: [] },
  };
}

function ttsModel(id: string, name: string): Model {
  return {
    id,
    name,
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

function variantModel(mode: ConditioningMode): Model {
  const suffix = mode === "human-reference" ? "ref" : "text";
  const label = mode === "human-reference" ? "見本あり" : "見本なし";
  return {
    ...ttsModel(`base-model--${suffix}`, `Base Model（${label}）`),
    conditioning: { mode, base_model: "base-model" },
  };
}
