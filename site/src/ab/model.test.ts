import { describe, expect, it } from "vite-plus/test";

import type { BenchmarkData, Character, Clip, Line, Model, Scenario } from "../data/types";
import {
  buildBlindCatalog,
  MIN_MODEL_APPEARANCES,
  rankModels,
  selectNextMatch,
  type BlindMatch,
  type BlindVote,
} from "./model";

describe("buildBlindCatalog", () => {
  it("0 / 1 model では候補を作らず、2 / 3 model では同一台詞の nC2 候補を作る", () => {
    expect(buildBlindCatalog(fixture(0, 2)).matches).toHaveLength(0);
    expect(buildBlindCatalog(fixture(1, 2)).matches).toHaveLength(0);
    expect(buildBlindCatalog(fixture(2, 2)).matches).toHaveLength(2);
    expect(buildBlindCatalog(fixture(3, 2)).matches).toHaveLength(6);
  });

  it("match は同じ scenario / line / dry と canonical な無順序 model pair で構成する", () => {
    const catalog = buildBlindCatalog(fixture(3, 1, ["gamma", "alpha", "beta"]));

    expect(
      catalog.matches.map((match) => ({
        id: match.id,
        pairId: match.pairId,
        line: match.line.id,
        variant: match.variant,
        models: [match.first.modelId, match.second.modelId],
      })),
    ).toEqual([
      {
        id: '["scenario","line-0","dry","alpha","beta"]',
        pairId: '["alpha","beta"]',
        line: "line-0",
        variant: "dry",
        models: ["alpha", "beta"],
      },
      {
        id: '["scenario","line-0","dry","alpha","gamma"]',
        pairId: '["alpha","gamma"]',
        line: "line-0",
        variant: "dry",
        models: ["alpha", "gamma"],
      },
      {
        id: '["scenario","line-0","dry","beta","gamma"]',
        pairId: '["beta","gamma"]',
        line: "line-0",
        variant: "dry",
        models: ["beta", "gamma"],
      },
    ]);
  });

  it("dry 以外の variant は候補から明示的に除外する", () => {
    const data = fixture(2, 1);
    data.manifest.clips.push({
      ...data.manifest.clips[0]!,
      variant: "scene",
      path: "audio/alpha/scenario/line-0-scene.opus",
    });

    const catalog = buildBlindCatalog(data);

    expect(catalog.matches).toHaveLength(1);
    expect(catalog.matches[0]?.first.clip.variant).toBe("dry");
    expect(catalog.matches[0]?.second.clip.variant).toBe("dry");
  });

  it("生成失敗は A/B 候補に含めない", () => {
    const data = fixture(2, 1);
    const removed = data.manifest.clips.pop()!;
    data.manifest.failures.push({
      model: removed.model,
      scenario: removed.scenario,
      line: removed.line,
      variant: removed.variant,
      reason: "generation_failed",
    });

    expect(buildBlindCatalog(data).matches).toHaveLength(0);
  });

  it("重複 cell と model / scenario / line の不正参照を fail fast で拒否する", () => {
    const duplicate = fixture(2, 1);
    duplicate.manifest.clips.push({ ...duplicate.manifest.clips[0]! });
    expect(() => buildBlindCatalog(duplicate)).toThrow("A/B の cell clip が重複しています");

    const missingModel = fixture(2, 1);
    missingModel.manifest.clips[0]!.model = "missing";
    expect(() => buildBlindCatalog(missingModel)).toThrow(
      "clip が存在しない model を参照しています",
    );

    const missingLine = fixture(2, 1);
    missingLine.manifest.clips[0]!.line = "missing";
    expect(() => buildBlindCatalog(missingLine)).toThrow(
      "clip が存在しない scenario/line を参照しています",
    );

    const invalidNonDry = fixture(2, 1);
    invalidNonDry.manifest.clips.push({
      ...invalidNonDry.manifest.clips[0]!,
      model: "missing",
      variant: "scene",
    });
    expect(() => buildBlindCatalog(invalidNonDry)).toThrow(
      "clip が存在しない model を参照しています",
    );
  });
});

describe("selectNextMatch", () => {
  it("最少投票 pair から未投票 match を選び、tie も pair の履歴 1 票に数える", () => {
    const catalog = buildBlindCatalog(fixture(3, 2));
    const firstAlphaBeta = catalog.matches.find(
      ({ pairId, line }) => pairId === '["alpha","beta"]' && line.id === "line-0",
    )!;
    const votes = [vote(firstAlphaBeta, null)];

    const selected = selectNextMatch(catalog, votes, sequenceRng([0, 0, 0]));

    expect(selected?.match.pairId).toBe('["alpha","gamma"]');
    expect(selected?.match.line.id).toBe("line-0");
  });

  it("乱数の先頭値で先頭 pair / match と canonical first を左へ提示する", () => {
    const catalog = buildBlindCatalog(fixture(3, 2));

    const selected = selectNextMatch(catalog, [], sequenceRng([0, 0, 0]));

    expect(selected?.match.pairId).toBe('["alpha","beta"]');
    expect(selected?.match.line.id).toBe("line-0");
    expect(selected?.left.modelId).toBe("alpha");
    expect(selected?.right.modelId).toBe("beta");
  });

  it("乱数の末尾値で末尾 pair / match と canonical second を左へ提示する", () => {
    const catalog = buildBlindCatalog(fixture(3, 2));

    const selected = selectNextMatch(catalog, [], sequenceRng([0.999_999, 0.999_999, 0.999_999]));

    expect(selected?.match.pairId).toBe('["beta","gamma"]');
    expect(selected?.match.line.id).toBe("line-1");
    expect(selected?.left.modelId).toBe("gamma");
    expect(selected?.right.modelId).toBe("beta");
  });

  it("全 match への投票完了後は null を返す", () => {
    const catalog = buildBlindCatalog(fixture(2, 1));

    expect(selectNextMatch(catalog, [vote(catalog.matches[0]!, "alpha")], () => 0)).toBeNull();
  });

  it("match の重複投票、不正参照、pair / winner 不一致、不正 RNG を拒否する", () => {
    const catalog = buildBlindCatalog(fixture(2, 1));
    const match = catalog.matches[0]!;
    const validVote = vote(match, "alpha");

    expect(() => selectNextMatch(catalog, [validVote, validVote], () => 0)).toThrow(
      "同じ A/B match へ複数回投票できません",
    );
    expect(() => selectNextMatch(catalog, [{ ...validVote, matchId: "missing" }], () => 0)).toThrow(
      "投票先の A/B match が存在しません",
    );
    expect(() =>
      selectNextMatch(catalog, [{ ...validVote, modelIds: ["alpha", "missing"] }], () => 0),
    ).toThrow("投票の model pair が A/B match と一致しません");
    expect(() =>
      selectNextMatch(catalog, [{ ...validVote, winnerModelId: "missing" }], () => 0),
    ).toThrow("投票の winner が model pair に含まれていません");
    expect(() => selectNextMatch(catalog, [], () => 1)).toThrow(
      "乱数は 0 以上 1 未満である必要があります",
    );
  });
});

describe("rankModels", () => {
  it("4 appearances までは rate / rank を隠し、5 appearances で公開する", () => {
    const models = fixture(2, 0).manifest.models;
    const fourVotes = Array.from({ length: MIN_MODEL_APPEARANCES - 1 }, (_, index) =>
      standaloneVote(`match-${index}`, "alpha"),
    );

    expect(
      rankModels(models, fourVotes).map(({ modelId, appearances, rate, rank }) => ({
        modelId,
        appearances,
        rate,
        rank,
      })),
    ).toEqual([
      { modelId: "alpha", appearances: 4, rate: null, rank: null },
      { modelId: "beta", appearances: 4, rate: null, rank: null },
    ]);

    const ranking = rankModels(models, [...fourVotes, standaloneVote("match-4", "alpha")]);
    expect(ranking.map(({ modelId, rate, rank }) => ({ modelId, rate, rank }))).toEqual([
      { modelId: "alpha", rate: 1, rank: 1 },
      { modelId: "beta", rate: 0, rank: 2 },
    ]);
  });

  it("勝ち 1 / 引き分け 0.5 / 負け 0 を集計し、同率は同じ competition rank にする", () => {
    const models = fixture(2, 0).manifest.models;
    const votes = Array.from({ length: MIN_MODEL_APPEARANCES }, (_, index) =>
      standaloneVote(`match-${index}`, null),
    );

    const ranking = rankModels(models, votes);

    expect(
      ranking.map(({ modelId, appearances, wins, ties, losses, score, rate, rank }) => ({
        modelId,
        appearances,
        wins,
        ties,
        losses,
        score,
        rate,
        rank,
      })),
    ).toEqual([
      {
        modelId: "alpha",
        appearances: 5,
        wins: 0,
        ties: 5,
        losses: 0,
        score: 2.5,
        rate: 0.5,
        rank: 1,
      },
      {
        modelId: "beta",
        appearances: 5,
        wins: 0,
        ties: 5,
        losses: 0,
        score: 2.5,
        rate: 0.5,
        rank: 1,
      },
    ]);
  });

  it("重複 match、非 canonical pair、未知 model、不正 winner を拒否する", () => {
    const models = fixture(2, 0).manifest.models;
    const validVote = standaloneVote("match", "alpha");

    expect(() => rankModels(models, [validVote, validVote])).toThrow(
      "同じ A/B match へ複数回投票できません",
    );
    expect(() => rankModels(models, [{ ...validVote, modelIds: ["beta", "alpha"] }])).toThrow(
      "投票の model pair は canonical 順",
    );
    expect(() => rankModels(models, [{ ...validVote, modelIds: ["alpha", "missing"] }])).toThrow(
      "投票が存在しない model を参照しています",
    );
    expect(() => rankModels(models, [{ ...validVote, winnerModelId: "missing" }])).toThrow(
      "投票の winner が model pair に含まれていません",
    );
  });
});

function vote(match: BlindMatch, winnerModelId: string | null): BlindVote {
  return {
    matchId: match.id,
    modelIds: [match.first.modelId, match.second.modelId],
    winnerModelId,
  };
}

function standaloneVote(matchId: string, winnerModelId: string | null): BlindVote {
  return {
    matchId,
    modelIds: ["alpha", "beta"],
    winnerModelId,
  };
}

function sequenceRng(values: readonly number[]): () => number {
  let index = 0;
  return () => {
    const value = values[index];
    if (value === undefined) {
      throw new Error("テスト乱数が不足しています。");
    }
    index += 1;
    return value;
  };
}

interface MutableManifest {
  format_version: 3;
  generated_at: string;
  models: Model[];
  clips: MutableClip[];
  failures: MutableGenerationFailure[];
}

interface MutableClip extends Omit<Clip, "model" | "line" | "variant"> {
  model: string;
  line: string;
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

function fixture(
  modelCount: number,
  lineCount: number,
  requestedModelIds = ["alpha", "beta", "gamma"],
): MutableBenchmarkData {
  const modelIds = requestedModelIds.slice(0, modelCount);
  const models = modelIds.map(ttsModel);
  const speaker = character("speaker");
  const lines = Array.from({ length: lineCount }, (_, index) => line(`line-${index}`));
  const fixtureScenario = scenario("scenario", speaker, lines);
  return {
    manifest: {
      format_version: 3,
      generated_at: "2026-07-28T00:00:00Z",
      models,
      clips: lines.flatMap((fixtureLine) =>
        models.map((model) => clip(model.id, fixtureScenario.id, fixtureLine.id)),
      ),
      failures: [],
    },
    scenarios: [fixtureScenario],
  };
}

function scenario(id: string, speaker: Character, lines: readonly Line[]): Scenario {
  return {
    format_version: 1,
    id,
    title: id,
    locale: "ja",
    scene: { setting: "テスト" },
    characters: [speaker],
    lines,
  };
}

function character(id: string): Character {
  return {
    id,
    name: id,
    kind: "human",
    gender: "neutral",
    age: "adult",
    voice: "自然な声",
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
