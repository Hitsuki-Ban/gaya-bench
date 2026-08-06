import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { describe, expect, it, vi } from "vite-plus/test";

import type {
  ArtifactOutcome,
  Character,
  ConditioningMode,
  Line,
  Model,
  Scenario,
} from "@/data/types";
import type { ComparisonProjection } from "@/filters";

import { DesktopMatrix } from "./desktop-matrix";
import { MobileMatrix } from "./mobile-matrix";
import type { ComparisonModel, ComparisonRow, Coordinate } from "./model";
import type { ComparisonController } from "./use-comparison-controller";

vi.mock("@/data", () => ({ referenceVoiceById: new Map() }));

const PRESET: Model = ttsModel("preset", "プリセット話者モデル");
const REFERENCE_VARIANT: Model = variantModel("human-reference");
const TEXT_VARIANT: Model = variantModel("text-only");

describe("comparison matrix conditioning columns", () => {
  it("desktop は variant 2 列を base model 名でグループ化する", () => {
    const markup = render(DesktopMatrix);

    // グループ見出しは base 名 (接尾辞なし) で 2 列にまたがる。
    expect(markup).toMatch(
      /<th[^>]*colspan="2"[^>]*data-model-group="base-model"[^>]*>[\s\S]*?Base Model[\s\S]*?<\/th>/i,
    );
    expect(markup).toMatch(/<th[^>]*colspan="1"[^>]*data-model-group="preset"/i);
    expect(markup).toContain('data-model-group="preset"');
    expect(markup).toContain('title="Base Model"');
    // 見出しは truncate せず折り返す。
    expect(markup).not.toContain("block truncate font-mono");
  });

  it("desktop の各 variant 列に条件チップと詳細 link を出す", () => {
    const markup = render(DesktopMatrix);

    expect(markup).toContain('data-conditioning-mode="human-reference"');
    expect(markup).toContain('data-conditioning-mode="text-only"');
    expect(markup).toContain("見本あり");
    expect(markup).toContain("見本なし");
    expect(markup).toContain('aria-label="Base Model（見本あり） の詳細"');
    expect(markup).toContain('href="/models/base-model--ref"');
    expect(markup).toContain('href="/models/base-model--text"');
    // 単方式モデルは従来どおり見出し自体が詳細 link。
    expect(markup).toContain('href="/models/preset"');
  });

  it("チップは色だけでなく文言と title で条件を示す", () => {
    const markup = render(DesktopMatrix);

    expect(markup).toContain('title="条件: 見本あり（収録素材を見本にして生成）"');
    expect(markup).toContain("条件: ");
  });

  it("mobile のモデルタブに条件チップを並べる", () => {
    const markup = render(MobileMatrix);

    expect(markup).toContain('data-model-tab="base-model--ref"');
    expect(markup).toContain('data-conditioning-mode="human-reference"');
    expect(markup).toContain('title="Base Model（見本あり）"');
    // 単方式モデルのタブにはチップを出さない。
    const presetTab = /<button[^>]*data-model-tab="preset"[^>]*>([\s\S]*?)<\/button>/.exec(markup);
    expect(presetTab?.[1]).not.toContain("data-conditioning-mode");
  });
});

function render(Matrix: typeof DesktopMatrix | typeof MobileMatrix): string {
  const fixture = createFixture();
  return renderToStaticMarkup(
    createElement(
      MemoryRouter,
      null,
      createElement(Matrix, {
        controller: fixture.controller,
        model: fixture.model,
        projection: fixture.projection,
        search: "",
      }),
    ),
  );
}

function createFixture(): {
  readonly controller: ComparisonController;
  readonly model: ComparisonModel;
  readonly projection: ComparisonProjection;
} {
  const models = [PRESET, REFERENCE_VARIANT, TEXT_VARIANT];
  const character: Character = {
    id: "speaker",
    name: "話者",
    kind: "human",
    gender: "female",
    age: "adult",
    voice: "明瞭",
  };
  const line: Line = {
    id: "speaker-001",
    character: "speaker",
    text: "一つ目。",
    emotion: "neutral",
    intensity: 1,
    delivery: "自然に。",
    difficulty: "standard",
    loop_ok: true,
    final_intonation: "fall",
  };
  const scenario: Scenario = {
    format_version: 1,
    id: "sample",
    title: "サンプル",
    locale: "ja",
    scene: { setting: "試験場" },
    characters: [character],
    lines: [line],
  };
  const rows: readonly ComparisonRow[] = [{ scenario, character, line }];
  const cells = new Map<string, ArtifactOutcome>(
    models.map((item) => {
      const group = { model: item.id, scenario: scenario.id, line: line.id, variant: "dry" };
      return [
        item.id,
        {
          kind: "selected",
          group,
          candidate: {
            ...group,
            path: `audio/takes/${item.id}/${scenario.id}/${line.id}/dry/sample.opus`,
            duration_sec: 1,
            rtf: 0.1,
            reference_conditioning: { kind: "none" },
            role_quality: null,
            gate: { content: "pass" },
          },
        },
      ];
    }),
  );
  const comparisonModel: ComparisonModel = {
    rows,
    models,
    getCell({ rowIndex, modelId }) {
      return rowIndex === 0 ? cells.get(modelId) : undefined;
    },
    getCoordinateForCandidateKey() {
      return undefined;
    },
  };
  const projection: ComparisonProjection = {
    rows: [{ row: rows[0]!, rowIndex: 0 }],
    models,
    rowIndexes: new Set([0]),
    modelIds: new Set(models.map(({ id }) => id)),
    key: "fixture",
  };
  const cursor: Coordinate = { rowIndex: 0, modelId: PRESET.id };
  const controller = {
    cursor,
    direction: "row",
    sequence: null,
    visibleModelIds: projection.modelIds,
    player: { currentClipKey: null },
    navigate: () => cursor,
    selectModel: () => undefined,
    selectAndToggle: () => undefined,
    setDirection: () => undefined,
    startOrStopSequence: () => undefined,
    stop: () => undefined,
    toggleFocused: () => undefined,
  } as unknown as ComparisonController;

  return { controller, model: comparisonModel, projection };
}

function ttsModel(id: string, name: string): Model {
  return {
    id,
    name,
    version: "1",
    license_note: "テスト",
    capabilities: {
      emotion: true,
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
    capabilities: {
      emotion: true,
      voice_prompt: true,
      clone: false,
      nonverbal: false,
      reading: false,
    },
    conditioning: { mode, base_model: "base-model" },
  };
}
