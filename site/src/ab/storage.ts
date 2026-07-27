import type { BlindCatalog, BlindVote, DatasetIdentity } from "./model";

export const AB_STORAGE_KEY = "gaya-bench:ab-votes";

export interface StoredVotes {
  readonly version: 1;
  readonly dataset: DatasetIdentity;
  readonly votes: readonly BlindVote[];
}

export interface ReadVotesResult extends StoredVotes {
  readonly rawSnapshot: string | null;
}

export interface VoteStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function readVotes(
  storage: VoteStorage,
  catalog: BlindCatalog,
  dataset: DatasetIdentity,
): ReadVotesResult {
  const rawSnapshot = storage.getItem(AB_STORAGE_KEY);
  if (rawSnapshot === null) {
    return {
      version: 1,
      dataset,
      votes: [],
      rawSnapshot,
    };
  }
  return {
    ...decodeStoredVotes(rawSnapshot, catalog, dataset),
    rawSnapshot,
  };
}

export function writeVotes(storage: VoteStorage, state: StoredVotes): string {
  const rawSnapshot = encodeStoredVotes(state);
  storage.setItem(AB_STORAGE_KEY, rawSnapshot);
  return rawSnapshot;
}

export function resetVotes(storage: VoteStorage): void {
  storage.removeItem(AB_STORAGE_KEY);
}

export function encodeStoredVotes(state: StoredVotes): string {
  assertExactKeys(state, ["version", "dataset", "votes"], "保存データ");
  if (state.version !== 1) {
    throw new Error(`保存データの version は 1 である必要があります: ${String(state.version)}`);
  }
  assertDatasetShape(state.dataset);
  if (!Array.isArray(state.votes)) {
    throw new Error("保存データの votes は配列である必要があります。");
  }

  const matchIds = new Set<string>();
  for (const vote of state.votes) {
    assertVoteShape(vote, matchIds);
    matchIds.add(vote.matchId);
  }
  return JSON.stringify(state);
}

export function decodeStoredVotes(
  raw: string,
  catalog: BlindCatalog,
  dataset: DatasetIdentity,
): StoredVotes {
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw);
  } catch {
    throw new Error("A/B 投票の保存データを JSON として解析できません。");
  }

  assertExactKeys(decoded, ["version", "dataset", "votes"], "保存データ");
  if (decoded.version !== 1) {
    throw new Error(`保存データの version は 1 である必要があります: ${String(decoded.version)}`);
  }
  assertDatasetShape(decoded.dataset);
  if (
    decoded.dataset.formatVersion !== dataset.formatVersion ||
    decoded.dataset.generatedAt !== dataset.generatedAt
  ) {
    throw new Error("A/B 投票の保存データは現在の dataset と一致しません。");
  }
  if (!Array.isArray(decoded.votes)) {
    throw new Error("保存データの votes は配列である必要があります。");
  }

  const matchesById = new Map(catalog.matches.map((match) => [match.id, match]));
  if (matchesById.size !== catalog.matches.length) {
    throw new Error("A/B catalog の match id が重複しています。");
  }

  const matchIds = new Set<string>();
  const votes = decoded.votes.map((value) => {
    assertVoteShape(value, matchIds);
    const match = matchesById.get(value.matchId);
    if (!match) {
      throw new Error(`保存された投票の A/B match が存在しません: ${value.matchId}`);
    }
    if (value.modelIds[0] !== match.first.modelId || value.modelIds[1] !== match.second.modelId) {
      throw new Error(`保存された投票の model pair が A/B match と一致しません: ${value.matchId}`);
    }
    matchIds.add(value.matchId);
    return value;
  });

  return {
    version: 1,
    dataset: {
      formatVersion: decoded.dataset.formatVersion,
      generatedAt: decoded.dataset.generatedAt,
    },
    votes,
  };
}

function assertDatasetShape(value: unknown): asserts value is DatasetIdentity {
  assertExactKeys(value, ["formatVersion", "generatedAt"], "dataset");
  if (value.formatVersion !== 2) {
    throw new Error(
      `dataset の formatVersion は 2 である必要があります: ${String(value.formatVersion)}`,
    );
  }
  if (typeof value.generatedAt !== "string") {
    throw new Error("dataset の generatedAt は文字列である必要があります。");
  }
}

function assertVoteShape(
  value: unknown,
  matchIds: ReadonlySet<string>,
): asserts value is BlindVote {
  assertExactKeys(value, ["matchId", "modelIds", "winnerModelId"], "vote");
  if (typeof value.matchId !== "string") {
    throw new Error("vote の matchId は文字列である必要があります。");
  }
  if (matchIds.has(value.matchId)) {
    throw new Error(`保存データの matchId が重複しています: ${value.matchId}`);
  }
  if (
    !Array.isArray(value.modelIds) ||
    value.modelIds.length !== 2 ||
    typeof value.modelIds[0] !== "string" ||
    typeof value.modelIds[1] !== "string"
  ) {
    throw new Error("vote の modelIds は 2 件の文字列である必要があります。");
  }
  if (value.modelIds[0] === value.modelIds[1]) {
    throw new Error("vote の modelIds に同じ model は指定できません。");
  }
  if (value.modelIds[0] >= value.modelIds[1]) {
    throw new Error(`vote の modelIds は canonical 順である必要があります: ${value.matchId}`);
  }
  if (
    value.winnerModelId !== null &&
    (typeof value.winnerModelId !== "string" ||
      (value.winnerModelId !== value.modelIds[0] && value.winnerModelId !== value.modelIds[1]))
  ) {
    throw new Error(`vote の winnerModelId が modelIds に含まれていません: ${value.matchId}`);
  }
}

function assertExactKeys(
  value: unknown,
  expectedKeys: readonly string[],
  label: string,
): asserts value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} は object である必要があります。`);
  }
  const actualKeys = Object.keys(value).sort();
  const sortedExpectedKeys = [...expectedKeys].sort();
  if (
    actualKeys.length !== sortedExpectedKeys.length ||
    actualKeys.some((key, index) => key !== sortedExpectedKeys[index])
  ) {
    throw new Error(`${label}の key が不正です: ${actualKeys.join(",")}`);
  }
}
