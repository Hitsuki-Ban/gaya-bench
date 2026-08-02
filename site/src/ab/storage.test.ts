import { describe, expect, it } from "vite-plus/test";

import type { BlindCatalog, BlindVote, DatasetIdentity } from "./model";
import {
  AB_STORAGE_KEY,
  decodeStoredVotes,
  encodeStoredVotes,
  readVotes,
  resetVotes,
  writeVotes,
  type VoteStorage,
} from "./storage";

describe("A/B storage v4 identity", () => {
  it("format4 / generatedAt / candidateSetSha256 が一致する vote だけ復元する", () => {
    const vote = validVote();
    const raw = encodeStoredVotes({ version: 1, dataset: DATASET, votes: [vote] });

    expect(decodeStoredVotes(raw, CATALOG, DATASET)).toEqual({
      version: 1,
      dataset: DATASET,
      votes: [vote],
    });
  });

  it("candidate_set_sha256 が変わった保存データを拒否する", () => {
    const raw = encodeStoredVotes({
      version: 1,
      dataset: { ...DATASET, candidateSetSha256: "e".repeat(64) },
      votes: [],
    });
    expect(() => decodeStoredVotes(raw, CATALOG, DATASET)).toThrow("現在の dataset と一致しません");
  });

  it("v3 identity と candidateSetSha256 欠落を migration せず拒否する", () => {
    const v3 = JSON.stringify({
      version: 1,
      dataset: { formatVersion: 3, generatedAt: DATASET.generatedAt },
      votes: [],
    });
    expect(() => decodeStoredVotes(v3, CATALOG, DATASET)).toThrow("datasetの key が不正");

    expect(() =>
      encodeStoredVotes({
        version: 1,
        dataset: {
          formatVersion: 4,
          generatedAt: DATASET.generatedAt,
          candidateSetSha256: "invalid",
        },
        votes: [],
      }),
    ).toThrow("完全な小文字 SHA-256");
  });

  it("storage read/write/reset を同じ key で行う", () => {
    const storage = new MemoryStorage();
    expect(readVotes(storage, CATALOG, DATASET).votes).toEqual([]);
    const raw = writeVotes(storage, {
      version: 1,
      dataset: DATASET,
      votes: [validVote()],
    });
    expect(storage.getItem(AB_STORAGE_KEY)).toBe(raw);
    expect(readVotes(storage, CATALOG, DATASET).votes).toHaveLength(1);
    resetVotes(storage);
    expect(storage.getItem(AB_STORAGE_KEY)).toBeNull();
  });
});

const DATASET: DatasetIdentity = {
  formatVersion: 4,
  generatedAt: "2026-07-30T00:00:00Z",
  candidateSetSha256: "d".repeat(64),
};

const CATALOG: BlindCatalog = {
  models: [
    {
      id: "alpha",
      name: "Alpha",
      version: "1",
      license_note: "",
      capabilities: {
        emotion: false,
        voice_prompt: false,
        clone: false,
        nonverbal: false,
        reading: false,
      },
    },
    {
      id: "beta",
      name: "Beta",
      version: "1",
      license_note: "",
      capabilities: {
        emotion: false,
        voice_prompt: false,
        clone: false,
        nonverbal: false,
        reading: false,
      },
    },
  ],
  matches: [
    {
      id: '["sample","speaker-001","dry","alpha","beta"]',
      pairId: '["alpha","beta"]',
      scenario: {
        format_version: 1,
        id: "sample",
        title: "Sample",
        locale: "ja",
        scene: { setting: "Test" },
        characters: [],
        lines: [],
      },
      line: {
        id: "speaker-001",
        character: "speaker",
        text: "台詞",
        emotion: "neutral",
        intensity: 2,
        delivery: "自然に",
        difficulty: "standard",
        loop_ok: true,
        final_intonation: "fall",
      },
      variant: "dry",
      first: { modelId: "alpha", candidate: { ...candidate("alpha"), role_quality: null } },
      second: { modelId: "beta", candidate: { ...candidate("beta"), role_quality: null } },
    },
  ],
};

function validVote(): BlindVote {
  return {
    matchId: CATALOG.matches[0]!.id,
    modelIds: ["alpha", "beta"],
    winnerModelId: "alpha",
  };
}

function candidate(model: string) {
  return {
    model,
    scenario: "sample",
    line: "speaker-001",
    variant: "dry",
    take_index: 1,
    take_id: "a".repeat(64),
    path: `audio/takes/${model}/sample/speaker-001/dry/take-0001-${"b".repeat(64)}.opus`,
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
      source: "encoded_opus" as const,
      i_lufs: -18,
      tp_dbtp: -1,
      shortfall: false,
    },
    gate: {
      mechanical: "pass" as const,
      content: "review_required" as const,
      policy_version: "test-v1",
    },
  };
}

class MemoryStorage implements VoteStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}
