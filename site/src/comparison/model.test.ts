import { describe, expect, it } from "vite-plus/test";

import type {
  ArtifactOutcome,
  BenchmarkData,
  Candidate,
  Character,
  Line,
  Model,
  Scenario,
} from "../data/types";
import type { ComparisonProjection } from "../filters";
import {
  buildColumnQueue,
  buildComparisonModel,
  buildRowQueue,
  moveCursor,
  resolveCursor,
} from "./model";

describe("comparison model v4 outcomes", () => {
  it("selected/skipped/uncurated/failure を cell に保持し、undefined は group 不在だけに使う", () => {
    const model = buildComparisonModel(fixture());

    expect(model.models.map(({ id }) => id)).toEqual([
      "alpha",
      "beta",
      "gamma",
      "delta",
      "epsilon",
    ]);
    expect(model.getCell({ rowIndex: 1, modelId: "alpha" })?.kind).toBe("selected");
    expect(model.getCell({ rowIndex: 1, modelId: "beta" })?.kind).toBe("skipped");
    expect(model.getCell({ rowIndex: 1, modelId: "gamma" })?.kind).toBe("uncurated");
    expect(model.getCell({ rowIndex: 1, modelId: "delta" })?.kind).toBe("failure");
    expect(model.getCell({ rowIndex: 1, modelId: "epsilon" })).toBeUndefined();
    expect(
      model.getCoordinateForCandidateKey(JSON.stringify(["alpha", "sample", "speaker-002", "dry"])),
    ).toEqual({ rowIndex: 1, modelId: "alpha" });
  });

  it("selected が一件もない manifest model を通常比較から除外する", () => {
    const data = fixture();
    data.manifest.models.push(model("failure-only"));
    data.outcomes.push(failure("failure-only", "speaker-002"));

    expect(buildComparisonModel(data).models.some(({ id }) => id === "failure-only")).toBe(false);
  });

  it("row queue は selected だけを再生し、他四態/group 不在を skip 数へ含める", () => {
    const model = buildComparisonModel(fixture());
    const queue = buildRowQueue(model, { rowIndex: 1, modelId: "alpha" }, projection(model));
    expect(queue.items.map(({ candidate }) => candidate.model)).toEqual(["alpha"]);
    expect(queue.skippedCount).toBe(4);
  });

  it("cursor 移動と column queue は投影された座標だけを使う", () => {
    const model = buildComparisonModel(fixture());
    const visible = projection(model);
    expect(moveCursor(model, { rowIndex: 0, modelId: "alpha" }, "right", visible)).toEqual({
      rowIndex: 0,
      modelId: "beta",
    });
    expect(resolveCursor(model, null, visible)).toEqual({ rowIndex: 0, modelId: "alpha" });
    expect(buildColumnQueue(model, { rowIndex: 0, modelId: "alpha" }, visible).items).toHaveLength(
      2,
    );
  });

  it("初期 cursor は表示範囲の最初の再生可能セルへ置く", () => {
    const model = buildComparisonModel(fixture());
    const sparse: ComparisonProjection = {
      rows: [{ row: model.rows[1]!, rowIndex: 1 }],
      models: [model.models[4]!, model.models[0]!],
      rowIndexes: new Set([1]),
      modelIds: new Set(["epsilon", "alpha"]),
      key: "sparse",
    };

    expect(resolveCursor(model, null, sparse)).toEqual({ rowIndex: 1, modelId: "alpha" });
  });
});

interface MutableBenchmarkData extends Omit<BenchmarkData, "manifest" | "outcomes"> {
  manifest: {
    format_version: 4;
    generated_at: string;
    candidate_set_sha256: string;
    models: Model[];
    candidates: Candidate[];
    curations: BenchmarkData["manifest"]["curations"];
    failures: BenchmarkData["manifest"]["failures"];
  };
  outcomes: ArtifactOutcome[];
}

function fixture(): MutableBenchmarkData {
  const ids = ["alpha", "beta", "gamma", "delta", "epsilon"];
  const firstRow = ids.map((id) => selected(id, "speaker-001"));
  const secondRow: ArtifactOutcome[] = [
    selected("alpha", "speaker-002"),
    skipped("beta", "speaker-002"),
    uncurated("gamma", "speaker-002"),
    failure("delta", "speaker-002"),
  ];
  const outcomes = [...firstRow, ...secondRow];
  return {
    manifest: {
      format_version: 4,
      generated_at: "2026-07-30T00:00:00Z",
      candidate_set_sha256: "d".repeat(64),
      models: ids.map(model),
      candidates: outcomes.flatMap((outcome) => {
        if (outcome.kind === "selected") {
          return [outcome.candidate];
        }
        if (outcome.kind === "failure") {
          return [];
        }
        return [...outcome.candidates];
      }),
      curations: outcomes.flatMap((outcome) =>
        outcome.kind === "selected" || outcome.kind === "skipped" ? [outcome.curation] : [],
      ),
      failures: outcomes.flatMap((outcome) =>
        outcome.kind === "failure" ? [outcome.failure] : [],
      ),
    },
    scenarios: [scenario()],
    outcomes,
    credits: { model_sources: [], reference_voices: [] },
  };
}

function selected(
  modelId: string,
  lineId: string,
): ArtifactOutcome & { readonly kind: "selected" } {
  const item = candidate(modelId, lineId);
  const outcomeGroup = group(modelId, lineId);
  return {
    kind: "selected",
    group: outcomeGroup,
    candidate: item,
    curation: {
      ...outcomeGroup,
      decision: "selected",
      take_id: item.take_id,
      curation_sha256: "c".repeat(64),
    },
  };
}

function skipped(modelId: string, lineId: string): ArtifactOutcome {
  const item = candidate(modelId, lineId);
  const outcomeGroup = group(modelId, lineId);
  return {
    kind: "skipped",
    group: outcomeGroup,
    candidates: [item],
    curation: {
      ...outcomeGroup,
      decision: "skipped",
      curation_sha256: "c".repeat(64),
    },
  };
}

function uncurated(modelId: string, lineId: string): ArtifactOutcome {
  return {
    kind: "uncurated",
    group: group(modelId, lineId),
    candidates: [candidate(modelId, lineId)],
  };
}

function failure(modelId: string, lineId: string): ArtifactOutcome {
  const outcomeGroup = group(modelId, lineId);
  return {
    kind: "failure",
    group: outcomeGroup,
    failure: { ...outcomeGroup, reason: "no_eligible_take" },
  };
}

function group(model: string, line: string) {
  return { model, scenario: "sample", line, variant: "dry" };
}

function candidate(modelId: string, lineId: string): Candidate {
  return {
    ...group(modelId, lineId),
    take_index: 1,
    take_id: "a".repeat(64),
    path: `audio/takes/${modelId}/sample/${lineId}/dry/take-0001-${"b".repeat(64)}.opus`,
    duration_sec: 1,
    sha256: "b".repeat(64),
    generation_input_sha256: "c".repeat(64),
    gen_params: {
      seed: 1,
      recipe_version: "test-v1",
      sampling: {},
      requested: {},
      realized: {},
    },
    rtf: 0.5,
    loudness: {
      source: "encoded_opus",
      i_lufs: -18,
      tp_dbtp: -1,
      shortfall: false,
    },
    gate: {
      mechanical: "pass",
      content: "review_required",
      policy_version: "test-v1",
    },
  };
}

function model(id: string): Model {
  return {
    id,
    name: id,
    version: "1",
    license_note: "",
    capabilities: {
      emotion: false,
      voice_prompt: false,
      clone: false,
      nonverbal: false,
      reading: false,
    },
  };
}

function scenario(): Scenario {
  const character: Character = {
    id: "speaker",
    name: "Speaker",
    kind: "human",
    gender: "neutral",
    age: "adult",
    voice: "clear",
  };
  const lines: Line[] = ["speaker-001", "speaker-002"].map((id) => ({
    id,
    character: character.id,
    text: id,
    emotion: "neutral",
    intensity: 2,
    delivery: "自然に",
    difficulty: "standard",
    loop_ok: true,
    final_intonation: "fall",
  }));
  return {
    format_version: 1,
    id: "sample",
    title: "Sample",
    locale: "ja",
    scene: { setting: "Test" },
    characters: [character],
    lines,
  };
}

function projection(model: ReturnType<typeof buildComparisonModel>): ComparisonProjection {
  return {
    rows: model.rows.map((row, rowIndex) => ({ row, rowIndex })),
    models: model.models,
    rowIndexes: new Set(model.rows.map((_row, index) => index)),
    modelIds: new Set(model.models.map(({ id }) => id)),
    key: "all",
  };
}
