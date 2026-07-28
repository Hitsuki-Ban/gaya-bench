import { describe, expect, it } from "vite-plus/test";

import type { BenchmarkData, Character, Clip, Line, Model, Scenario } from "../data/types";
import { buildBlindCatalog, datasetIdentity, type BlindCatalog, type BlindVote } from "./model";
import {
  AB_STORAGE_KEY,
  decodeStoredVotes,
  encodeStoredVotes,
  readVotes,
  resetVotes,
  writeVotes,
  type StoredVotes,
  type VoteStorage,
} from "./storage";

describe("A/B vote storage", () => {
  it("key がない場合だけ現在 dataset の空投票を返す", () => {
    const { catalog, dataset } = context();
    const storage = new MemoryStorage();

    expect(readVotes(storage, catalog, dataset)).toEqual({
      version: 1,
      dataset,
      votes: [],
      rawSnapshot: null,
    });
    expect(storage.readKeys).toEqual([AB_STORAGE_KEY]);
  });

  it("固定 key へ書き込み、rawSnapshot を保ったまま読み、reset で削除する", () => {
    const { catalog, dataset } = context();
    const storage = new MemoryStorage();
    const state = storedVotes(catalog, dataset);

    const rawSnapshot = writeVotes(storage, state);
    expect(storage.values.get(AB_STORAGE_KEY)).toBe(rawSnapshot);
    expect(readVotes(storage, catalog, dataset)).toEqual({
      ...state,
      rawSnapshot,
    });

    resetVotes(storage);
    expect(storage.values.has(AB_STORAGE_KEY)).toBe(false);
    expect(storage.removedKeys).toEqual([AB_STORAGE_KEY]);
  });

  it("version 1 payload を正確に encode / decode する", () => {
    const { catalog, dataset } = context();
    const state = storedVotes(catalog, dataset);

    expect(decodeStoredVotes(encodeStoredVotes(state), catalog, dataset)).toEqual(state);
  });

  it("root / dataset / vote の不足 key と余分な key を厳格に拒否する", () => {
    const { catalog, dataset } = context();
    const state = storedVotes(catalog, dataset);
    const payload = plainPayload(state);

    expect(() =>
      decodeStoredVotes(JSON.stringify({ ...payload, extra: true }), catalog, dataset),
    ).toThrow("保存データの key が不正です");

    const { votes: _votes, ...missingRoot } = payload;
    expect(() => decodeStoredVotes(JSON.stringify(missingRoot), catalog, dataset)).toThrow(
      "保存データの key が不正です",
    );

    expect(() =>
      decodeStoredVotes(
        JSON.stringify({
          ...payload,
          dataset: { ...payload.dataset, extra: true },
        }),
        catalog,
        dataset,
      ),
    ).toThrow("datasetの key が不正です");

    expect(() =>
      decodeStoredVotes(
        JSON.stringify({
          ...payload,
          votes: [{ ...payload.votes[0]!, side: "left" }],
        }),
        catalog,
        dataset,
      ),
    ).toThrow("voteの key が不正です");
  });

  it("不正 JSON、version、dataset 不一致を fail fast で拒否する", () => {
    const { catalog, dataset } = context();
    const payload = plainPayload(storedVotes(catalog, dataset));

    expect(() => decodeStoredVotes("{", catalog, dataset)).toThrow(
      "保存データを JSON として解析できません",
    );
    expect(() =>
      decodeStoredVotes(JSON.stringify({ ...payload, version: 2 }), catalog, dataset),
    ).toThrow("保存データの version は 1");
    expect(() =>
      decodeStoredVotes(
        JSON.stringify({
          ...payload,
          dataset: { ...dataset, generatedAt: "別 dataset" },
        }),
        catalog,
        dataset,
      ),
    ).toThrow("現在の dataset と一致しません");
    expect(() =>
      decodeStoredVotes(
        JSON.stringify({
          ...payload,
          dataset: { ...dataset, formatVersion: 2 },
        }),
        catalog,
        dataset,
      ),
    ).toThrow("dataset の formatVersion は 3");
  });

  it("存在しない match、非 canonical / 不一致 pair、不正 winner、match 重複を拒否する", () => {
    const { catalog, dataset } = context();
    const payload = plainPayload(storedVotes(catalog, dataset));
    const validVote = payload.votes[0]!;

    expect(() =>
      decodeStoredVotes(
        JSON.stringify({
          ...payload,
          votes: [{ ...validVote, matchId: "missing" }],
        }),
        catalog,
        dataset,
      ),
    ).toThrow("保存された投票の A/B match が存在しません");
    expect(() =>
      decodeStoredVotes(
        JSON.stringify({
          ...payload,
          votes: [{ ...validVote, modelIds: ["beta", "alpha"] }],
        }),
        catalog,
        dataset,
      ),
    ).toThrow("modelIds は canonical 順");
    expect(() =>
      decodeStoredVotes(
        JSON.stringify({
          ...payload,
          votes: [{ ...validVote, modelIds: ["alpha", "gamma"] }],
        }),
        catalog,
        dataset,
      ),
    ).toThrow("model pair が A/B match と一致しません");
    expect(() =>
      decodeStoredVotes(
        JSON.stringify({
          ...payload,
          votes: [{ ...validVote, winnerModelId: "missing" }],
        }),
        catalog,
        dataset,
      ),
    ).toThrow("winnerModelId が modelIds に含まれていません");
    expect(() =>
      decodeStoredVotes(
        JSON.stringify({
          ...payload,
          votes: [validVote, validVote],
        }),
        catalog,
        dataset,
      ),
    ).toThrow("保存データの matchId が重複しています");
  });

  it("encode も malformed state と重複 match を拒否する", () => {
    const { catalog, dataset } = context();
    const state = storedVotes(catalog, dataset);
    const malformed = { ...state, extra: true } as unknown as StoredVotes;
    const duplicate = {
      ...state,
      votes: [state.votes[0]!, state.votes[0]!],
    };

    expect(() => encodeStoredVotes(malformed)).toThrow("保存データの key が不正です");
    expect(() => encodeStoredVotes(duplicate)).toThrow("保存データの matchId が重複しています");
  });

  it("Storage API の get / set / remove 例外を隠さず伝播する", () => {
    const { catalog, dataset } = context();
    const state = storedVotes(catalog, dataset);

    expect(() => readVotes(throwingStorage("get"), catalog, dataset)).toThrow("get failure");
    expect(() => writeVotes(throwingStorage("set"), state)).toThrow("set failure");
    expect(() => resetVotes(throwingStorage("remove"))).toThrow("remove failure");
  });
});

class MemoryStorage implements VoteStorage {
  readonly values = new Map<string, string>();
  readonly readKeys: string[] = [];
  readonly removedKeys: string[] = [];

  getItem(key: string): string | null {
    this.readKeys.push(key);
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.removedKeys.push(key);
    this.values.delete(key);
  }
}

function throwingStorage(method: "get" | "set" | "remove"): VoteStorage {
  return {
    getItem() {
      if (method === "get") {
        throw new Error("get failure");
      }
      return null;
    },
    setItem() {
      if (method === "set") {
        throw new Error("set failure");
      }
    },
    removeItem() {
      if (method === "remove") {
        throw new Error("remove failure");
      }
    },
  };
}

function storedVotes(
  catalog: BlindCatalog,
  dataset: ReturnType<typeof datasetIdentity>,
): StoredVotes {
  const match = catalog.matches[0]!;
  const vote: BlindVote = {
    matchId: match.id,
    modelIds: [match.first.modelId, match.second.modelId],
    winnerModelId: match.first.modelId,
  };
  return {
    version: 1,
    dataset,
    votes: [vote],
  };
}

function plainPayload(state: StoredVotes): {
  version: number;
  dataset: { formatVersion: number; generatedAt: string };
  votes: Array<{ matchId: string; modelIds: string[]; winnerModelId: string | null }>;
} {
  return JSON.parse(JSON.stringify(state)) as {
    version: number;
    dataset: { formatVersion: number; generatedAt: string };
    votes: Array<{ matchId: string; modelIds: string[]; winnerModelId: string | null }>;
  };
}

function context(): {
  catalog: BlindCatalog;
  dataset: ReturnType<typeof datasetIdentity>;
} {
  const data = fixture();
  return {
    catalog: buildBlindCatalog(data),
    dataset: datasetIdentity(data),
  };
}

function fixture(): BenchmarkData {
  const models = ["alpha", "beta", "gamma"].map(ttsModel);
  const speaker = character();
  const fixtureLine = line();
  const fixtureScenario = scenario(speaker, fixtureLine);
  return {
    manifest: {
      format_version: 3,
      generated_at: "2026-07-28T00:00:00Z",
      models,
      clips: models.map((model) => clip(model.id)),
      failures: [],
    },
    scenarios: [fixtureScenario],
  };
}

function scenario(speaker: Character, fixtureLine: Line): Scenario {
  return {
    format_version: 1,
    id: "scenario",
    title: "scenario",
    locale: "ja",
    scene: { setting: "テスト" },
    characters: [speaker],
    lines: [fixtureLine],
  };
}

function character(): Character {
  return {
    id: "speaker",
    name: "話者",
    kind: "human",
    gender: "neutral",
    age: "adult",
    voice: "自然な声",
  };
}

function line(): Line {
  return {
    id: "line",
    character: "speaker",
    text: "テスト",
    emotion: "neutral",
    intensity: 2,
    delivery: "自然に",
    difficulty: "standard",
    loop_ok: true,
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

function clip(model: string): Clip {
  return {
    model,
    scenario: "scenario",
    line: "line",
    variant: "dry",
    path: `audio/${model}/scenario/line.opus`,
    duration_sec: 1,
    sha256: model,
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
