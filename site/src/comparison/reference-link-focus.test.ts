import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { describe, expect, it, vi } from "vite-plus/test";

import type { ArtifactOutcome, Character, Line, Model, Scenario } from "@/data/types";
import type { ComparisonProjection } from "@/filters";

import { DesktopMatrix } from "./desktop-matrix";
import { MobileMatrix } from "./mobile-matrix";
import type { ComparisonModel, ComparisonRow, Coordinate } from "./model";
import type { ComparisonController } from "./use-comparison-controller";

vi.mock("@/data", () => ({
  referenceVoiceById: new Map([
    [
      "sample-voice",
      {
        source: { speaker: "サンプル話者" },
      },
    ],
  ]),
}));

describe("comparison reference link focus", () => {
  it.each([
    ["desktop", DesktopMatrix],
    ["mobile", MobileMatrix],
  ] as const)("%s matrix は現在 cell の参照 link だけを Tab 順に入れる", (_name, Matrix) => {
    const fixture = createFixture();
    const markup = renderToStaticMarkup(
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

    expect(referenceLinkTabIndexes(markup)).toEqual(["0", "-1"]);
  });
});

function referenceLinkTabIndexes(markup: string): string[] {
  return [...markup.matchAll(/<a(?=[^>]*href="\/reference-voices#sample-voice")[^>]*>/g)].map(
    ([tag]) => {
      const value = /tabindex="(-?\d+)"/.exec(tag)?.[1];
      if (value === undefined) {
        throw new Error(`参照 link に明示 tabIndex がありません: ${tag}`);
      }
      return value;
    },
  );
}

function createFixture(): {
  readonly controller: ComparisonController;
  readonly model: ComparisonModel;
  readonly projection: ComparisonProjection;
} {
  const modelInfo: Model = {
    id: "sample-model",
    name: "Sample Model",
    version: "1",
    license_note: "Test",
    capabilities: {
      emotion: false,
      voice_prompt: false,
      clone: true,
      nonverbal: false,
      reading: false,
    },
  };
  const character: Character = {
    id: "speaker",
    name: "話者",
    kind: "human",
    gender: "female",
    age: "adult",
    voice: "明瞭",
  };
  const lines: readonly Line[] = [
    createLine("speaker-001", "一つ目。"),
    createLine("speaker-002", "二つ目。"),
  ];
  const scenario: Scenario = {
    format_version: 1,
    id: "sample",
    title: "サンプル",
    locale: "ja",
    scene: { setting: "試験場" },
    characters: [character],
    lines,
  };
  const rows: readonly ComparisonRow[] = lines.map((line) => ({
    scenario,
    character,
    line,
  }));
  const outcomes: readonly ArtifactOutcome[] = lines.map((line) => {
    const group = {
      model: modelInfo.id,
      scenario: scenario.id,
      line: line.id,
      variant: "dry",
    };
    return {
      kind: "selected",
      group,
      candidate: {
        ...group,
        path: `audio/takes/${modelInfo.id}/${scenario.id}/${line.id}/dry/sample.opus`,
        duration_sec: 1,
        rtf: 0.1,
        reference_conditioning: {
          kind: "human_reference",
          voice_id: "sample-voice",
          asset_sha256: "a".repeat(64),
          inference_reference_sha256: "a".repeat(64),
          selection_source: "fixture",
        },
        gate: { content: "pass" },
      },
    };
  });
  const comparisonModel: ComparisonModel = {
    rows,
    models: [modelInfo],
    getCell({ rowIndex, modelId }) {
      return modelId === modelInfo.id ? outcomes[rowIndex] : undefined;
    },
    getCoordinateForCandidateKey() {
      return undefined;
    },
  };
  const projection: ComparisonProjection = {
    rows: rows.map((row, rowIndex) => ({ row, rowIndex })),
    models: [modelInfo],
    rowIndexes: new Set([0, 1]),
    modelIds: new Set([modelInfo.id]),
    key: "fixture",
  };
  const cursor: Coordinate = { rowIndex: 0, modelId: modelInfo.id };
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

function createLine(id: string, text: string): Line {
  return {
    id,
    character: "speaker",
    text,
    emotion: "neutral",
    intensity: 1,
    delivery: "自然に。",
    difficulty: "standard",
    loop_ok: true,
    final_intonation: "fall",
  };
}
