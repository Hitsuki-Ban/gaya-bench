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
import {
  buildBlindCatalog,
  datasetIdentity,
  rankModels,
  selectNextMatch,
  type BlindVote,
} from "./model";

describe("buildBlindCatalog v4", () => {
  it("selected outcome だけから同一 line の canonical model pair を作る", () => {
    const data = fixture(["gamma", "alpha", "beta"]);
    data.outcomes.push(skippedOutcome("alpha"), uncuratedOutcome("beta"), failureOutcome("gamma"));

    const catalog = buildBlindCatalog(data);

    expect(catalog.matches).toHaveLength(3);
    expect(catalog.matches.map(({ pairId }) => pairId)).toEqual([
      '["alpha","beta"]',
      '["alpha","gamma"]',
      '["beta","gamma"]',
    ]);
    expect(catalog.matches[0]?.first.candidate.variant).toBe("dry");
    expect(JSON.stringify(catalog)).not.toContain("skipped");
    expect(JSON.stringify(catalog)).not.toContain("uncurated");
    expect(JSON.stringify(catalog)).not.toContain("no_eligible_take");
  });

  it("selected candidate の model / scenario / line 参照を fail fast する", () => {
    const missingModel = fixture(["alpha", "beta"]);
    const selected = missingModel.outcomes[0]!;
    if (selected.kind !== "selected") {
      throw new Error("fixture selected outcome がありません。");
    }
    missingModel.outcomes[0] = {
      ...selected,
      candidate: { ...selected.candidate, model: "missing" },
    };
    expect(() => buildBlindCatalog(missingModel)).toThrow("存在しない model");

    const missingLine = fixture(["alpha", "beta"]);
    const other = missingLine.outcomes[0]!;
    if (other.kind !== "selected") {
      throw new Error("fixture selected outcome がありません。");
    }
    missingLine.outcomes[0] = {
      ...other,
      candidate: { ...other.candidate, line: "missing" },
    };
    expect(() => buildBlindCatalog(missingLine)).toThrow("存在しない scenario/line");
  });
});

describe("A/B session model", () => {
  it("dataset identity を format4 / generated_at / candidate set SHA に拘束する", () => {
    expect(datasetIdentity(fixture(["alpha", "beta"]))).toEqual({
      formatVersion: 4,
      generatedAt: "2026-07-30T00:00:00Z",
      candidateSetSha256: "d".repeat(64),
    });
  });

  it("未投票 match を提示し、全投票後は null を返す", () => {
    const catalog = buildBlindCatalog(fixture(["alpha", "beta"]));
    const presented = selectNextMatch(catalog, [], () => 0);
    expect(presented?.left.modelId).toBe("alpha");
    const match = catalog.matches[0]!;
    const vote: BlindVote = {
      matchId: match.id,
      modelIds: [match.first.modelId, match.second.modelId],
      winnerModelId: match.first.modelId,
    };
    expect(selectNextMatch(catalog, [vote], () => 0)).toBeNull();
  });

  it("勝敗と引分を集計し、5 appearances 未満は順位を出さない", () => {
    const models = fixture(["alpha", "beta"]).manifest.models;
    const votes: BlindVote[] = Array.from({ length: 5 }, (_, index) => ({
      matchId: `match-${index}`,
      modelIds: ["alpha", "beta"],
      winnerModelId: index === 4 ? null : "alpha",
    }));
    const ranking = rankModels(models, votes);
    expect(ranking.map(({ modelId, rank, score }) => ({ modelId, rank, score }))).toEqual([
      { modelId: "alpha", rank: 1, score: 4.5 },
      { modelId: "beta", rank: 2, score: 0.5 },
    ]);
  });
});

interface MutableBenchmarkData extends Omit<BenchmarkData, "outcomes"> {
  outcomes: ArtifactOutcome[];
}

function fixture(modelIds: readonly string[]): MutableBenchmarkData {
  const models = modelIds.map(model);
  const candidates = modelIds.map((modelId) => candidate(modelId));
  const curations = candidates.map((item) => ({
    model: item.model,
    scenario: item.scenario,
    line: item.line,
    variant: item.variant,
    decision: "selected" as const,
    take_id: item.take_id,
    curation_sha256: "c".repeat(64),
  }));
  return {
    manifest: {
      format_version: 4,
      generated_at: "2026-07-30T00:00:00Z",
      candidate_set_sha256: "d".repeat(64),
      models,
      candidates,
      curations,
      failures: [],
    },
    scenarios: [scenario()],
    outcomes: candidates.map((item, index) => ({
      kind: "selected",
      group: group(item.model, "dry"),
      candidate: item,
      curation: curations[index]!,
    })),
    credits: { model_sources: [], reference_voices: [] },
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

function candidate(modelId: string, variant = "dry"): Candidate {
  return {
    ...group(modelId, variant),
    take_index: 1,
    take_id: `${modelId.charCodeAt(0).toString(16).padStart(2, "0")}`.repeat(32),
    path: `audio/takes/${modelId}/sample/speaker-001/${variant}/take-0001-${"b".repeat(64)}.opus`,
    duration_sec: 1,
    sha256: "b".repeat(64),
    generation_input_sha256: "a".repeat(64),
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
      policy_version: "test-gate-v1",
    },
  };
}

function skippedOutcome(modelId: string): ArtifactOutcome {
  const item = candidate(modelId, "skipped");
  const outcomeGroup = group(modelId, "skipped");
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

function uncuratedOutcome(modelId: string): ArtifactOutcome {
  const item = candidate(modelId, "uncurated");
  return {
    kind: "uncurated",
    group: group(modelId, "uncurated"),
    candidates: [item],
  };
}

function failureOutcome(modelId: string): ArtifactOutcome {
  const outcomeGroup = group(modelId, "failed");
  return {
    kind: "failure",
    group: outcomeGroup,
    failure: { ...outcomeGroup, reason: "no_eligible_take" },
  };
}

function group(modelId: string, variant: string) {
  return {
    model: modelId,
    scenario: "sample",
    line: "speaker-001",
    variant,
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
  const line: Line = {
    id: "speaker-001",
    character: character.id,
    text: "台詞",
    emotion: "neutral",
    intensity: 2,
    delivery: "自然に",
    difficulty: "standard",
    loop_ok: true,
    final_intonation: "fall",
  };
  return {
    format_version: 1,
    id: "sample",
    title: "Sample",
    locale: "ja",
    scene: { setting: "Test" },
    characters: [character],
    lines: [line],
  };
}
