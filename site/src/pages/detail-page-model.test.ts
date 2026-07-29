import { describe, expect, it } from "vite-plus/test";

import type { ArtifactOutcome, Candidate, Character, Line, Scenario } from "@/data";
import {
  buildModelCandidateEntries,
  buildModelOutcomeEntries,
  buildScenarioLineEntries,
  calculateRtfStatistics,
  collectGenerationParameterSets,
} from "./detail-page-model";

describe("detail page v4 projection", () => {
  it("scenario line へ四態 outcome を束ねる", () => {
    const outcomes = fixtureOutcomes();
    const entries = buildScenarioLineEntries(scenario(), outcomes);
    expect(entries[0]?.outcomes.map(({ kind }) => kind)).toEqual([
      "selected",
      "skipped",
      "uncurated",
      "failure",
    ]);
  });

  it("model detail は selected candidate と非 selected outcome を分離する", () => {
    const outcomes = fixtureOutcomes();
    expect(buildModelCandidateEntries("alpha", outcomes, [scenario()])).toHaveLength(1);
    expect(
      buildModelOutcomeEntries("alpha", outcomes, [scenario()]).map(({ outcome }) => outcome.kind),
    ).toEqual(["skipped", "uncurated", "failure"]);
  });

  it("selected candidate の RTF と generation parameter set を集計する", () => {
    const first = candidate();
    const second = { ...candidate(), duration_sec: 3, rtf: 2 };
    expect(calculateRtfStatistics([first, second])).toEqual({
      weightedMean: 1.625,
      minimum: 0.5,
      maximum: 2,
    });
    expect(collectGenerationParameterSets([first, second])).toEqual([
      { parameters: first.gen_params, candidateCount: 2 },
    ]);
  });

  it("未知の scenario / line 参照と非正 duration を拒否する", () => {
    const selected = fixtureOutcomes()[0]!;
    expect(() =>
      buildModelCandidateEntries(
        "alpha",
        [{ ...selected, group: { ...selected.group, line: "missing" } }],
        [scenario()],
      ),
    ).toThrow("未知の line");
    expect(() => calculateRtfStatistics([{ duration_sec: 0, rtf: 1 }])).toThrow("正の音声時間");
  });
});

function fixtureOutcomes(): ArtifactOutcome[] {
  const item = candidate();
  const group = {
    model: "alpha",
    scenario: "sample",
    line: "speaker-001",
    variant: "dry",
  };
  return [
    {
      kind: "selected",
      group,
      candidate: item,
      curation: {
        ...group,
        decision: "selected",
        take_id: item.take_id,
        curation_sha256: "c".repeat(64),
      },
    },
    {
      kind: "skipped",
      group: { ...group, variant: "skipped" },
      candidates: [{ ...item, variant: "skipped" }],
      curation: {
        ...group,
        variant: "skipped",
        decision: "skipped",
        curation_sha256: "c".repeat(64),
      },
    },
    {
      kind: "uncurated",
      group: { ...group, variant: "uncurated" },
      candidates: [{ ...item, variant: "uncurated" }],
    },
    {
      kind: "failure",
      group: { ...group, variant: "failed" },
      failure: { ...group, variant: "failed", reason: "no_eligible_take" },
    },
  ];
}

function candidate(): Candidate {
  return {
    model: "alpha",
    scenario: "sample",
    line: "speaker-001",
    variant: "dry",
    take_index: 1,
    take_id: "a".repeat(64),
    path: `audio/takes/alpha/sample/speaker-001/dry/take-0001-${"b".repeat(64)}.opus`,
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
