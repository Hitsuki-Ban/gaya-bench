import { describe, expect, it } from "vite-plus/test";

import {
  benchmarkData,
  getOutcomesForScenario,
  playableModels,
  type ArtifactOutcome,
  type Model,
  type PublishedCandidate,
  type Scenario,
} from "@/data";

import { buildSceneMixerOptions } from "./scene-mixer-model";

describe("buildSceneMixerOptions", () => {
  it("公開データの全15 scene × 全 model に3件以上の再生可能 clip を構築する", () => {
    const pairs: string[] = [];
    const modelCount = benchmarkData.release.models.length;

    for (const scenario of benchmarkData.scenarios) {
      const options = buildSceneMixerOptions(
        scenario,
        getOutcomesForScenario(scenario.id),
        playableModels,
      );
      expect(options).toHaveLength(modelCount);
      for (const option of options) {
        expect(option.candidates.length).toBeGreaterThanOrEqual(3);
        expect(option.candidates.every(({ variant }) => variant === "dry")).toBe(true);
        expect(
          option.candidates.every(({ line }) =>
            scenario.lines.some((item) => item.id === line && item.loop_ok),
          ),
        ).toBe(true);
        pairs.push(`${scenario.id}/${option.model.id}`);
      }
    }

    expect(new Set(pairs).size).toBe(benchmarkData.scenarios.length * modelCount);
  });

  it("selected dry かつ loop_ok の clip だけを line 順で採用する", () => {
    const scenario = fixtureScenario();
    const model = fixtureModel();
    const outcomes: ArtifactOutcome[] = [
      selected(model.id, scenario.id, "line-3", "dry"),
      selected(model.id, scenario.id, "line-off", "dry"),
      selected(model.id, scenario.id, "line-1", "dry"),
      selected(model.id, scenario.id, "line-2", "dry"),
      selected(model.id, scenario.id, "line-1", "scene"),
      { kind: "skipped", group: group(model.id, scenario.id, "line-off", "skipped") },
    ];

    const [option] = buildSceneMixerOptions(scenario, outcomes, [model]);

    expect(option?.candidates.map(({ line }) => line)).toEqual(["line-1", "line-2", "line-3"]);
  });

  it("3件未満、未知参照、candidate/group 不一致を明示的に拒否する", () => {
    const scenario = fixtureScenario();
    const model = fixtureModel();
    const two = [
      selected(model.id, scenario.id, "line-1", "dry"),
      selected(model.id, scenario.id, "line-2", "dry"),
    ];
    expect(() => buildSceneMixerOptions(scenario, two, [model])).toThrow("3件以上");

    expect(() =>
      buildSceneMixerOptions(
        scenario,
        [{ kind: "skipped", group: group("missing", scenario.id, "line-1", "dry") }],
        [model],
      ),
    ).toThrow("未知の model");

    const mismatch = selected(model.id, scenario.id, "line-1", "dry");
    expect(() =>
      buildSceneMixerOptions(
        scenario,
        [
          {
            ...mismatch,
            candidate: { ...mismatch.candidate, line: "line-2" },
          },
        ],
        [model],
      ),
    ).toThrow("candidate.line");
  });
});

function fixtureModel(): Model {
  return {
    id: "model-a",
    name: "Model A",
    version: "1",
    license_note: "test",
    capabilities: {
      emotion: true,
      voice_prompt: false,
      clone: false,
      nonverbal: false,
      reading: true,
    },
  };
}

function fixtureScenario(): Scenario {
  return {
    format_version: 1,
    id: "scene-a",
    title: "Scene A",
    locale: "ja",
    scene: { setting: "test" },
    characters: [
      {
        id: "npc",
        name: "NPC",
        kind: "human",
        gender: "neutral",
        age: "adult",
        voice: "test",
      },
    ],
    lines: [
      line("line-1", true),
      line("line-2", true),
      line("line-3", true),
      line("line-off", false),
    ],
  };
}

function line(id: string, loopOk: boolean): Scenario["lines"][number] {
  return {
    id,
    character: "npc",
    text: id,
    emotion: "neutral",
    intensity: 2,
    delivery: "test",
    difficulty: "standard",
    loop_ok: loopOk,
    final_intonation: "fall",
  };
}

function group(model: string, scenario: string, lineId: string, variant: string) {
  return { model, scenario, line: lineId, variant };
}

function selected(
  model: string,
  scenario: string,
  lineId: string,
  variant: string,
): Extract<ArtifactOutcome, { readonly kind: "selected" }> {
  const artifactGroup = group(model, scenario, lineId, variant);
  const candidate: PublishedCandidate = {
    ...artifactGroup,
    path: `audio/takes/${model}/${scenario}/${lineId}/${variant}/take.opus`,
    duration_sec: 1,
    rtf: 1,
    reference_conditioning: { kind: "none" },
    gate: { content: "pass" },
    role_quality: null,
  };
  return { kind: "selected", group: artifactGroup, candidate };
}
