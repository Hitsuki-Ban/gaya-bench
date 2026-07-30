import type { BenchmarkData, Line, Model, PublishedCandidate, Scenario } from "../data/types";

export const MIN_MODEL_APPEARANCES = 5;

export interface DatasetIdentity {
  readonly formatVersion: 4;
  readonly generatedAt: string;
  readonly candidateSetSha256: string;
}

export interface BlindCandidate {
  readonly modelId: string;
  readonly candidate: PublishedCandidate;
}

export interface BlindMatch {
  readonly id: string;
  readonly pairId: string;
  readonly scenario: Scenario;
  readonly line: Line;
  readonly variant: "dry";
  readonly first: BlindCandidate;
  readonly second: BlindCandidate;
}

export interface BlindCatalog {
  readonly matches: readonly BlindMatch[];
  readonly models: readonly Model[];
}

export interface BlindVote {
  readonly matchId: string;
  readonly modelIds: readonly [string, string];
  readonly winnerModelId: string | null;
}

export interface PresentedMatch {
  readonly match: BlindMatch;
  readonly left: BlindCandidate;
  readonly right: BlindCandidate;
}

export interface ModelRanking {
  readonly model: Model;
  readonly modelId: string;
  readonly appearances: number;
  readonly wins: number;
  readonly ties: number;
  readonly losses: number;
  readonly score: number;
  readonly rate: number | null;
  readonly rank: number | null;
}

export function datasetIdentity(data: BenchmarkData): DatasetIdentity {
  return {
    formatVersion: data.release.format_version,
    generatedAt: data.release.generated_at,
    candidateSetSha256: data.release.candidate_set_sha256,
  };
}

export function buildBlindCatalog(data: BenchmarkData): BlindCatalog {
  const modelsById = uniqueModels(data.release.models);
  const linesByKey = new Map<string, { readonly scenario: Scenario; readonly line: Line }>();
  const scenarioIds = new Set<string>();

  for (const scenario of data.scenarios) {
    if (scenarioIds.has(scenario.id)) {
      throw new Error(`scenario id が重複しています: ${scenario.id}`);
    }
    scenarioIds.add(scenario.id);

    const lineIds = new Set<string>();
    for (const line of scenario.lines) {
      if (lineIds.has(line.id)) {
        throw new Error(`line id が scenario 内で重複しています: ${scenario.id}/${line.id}`);
      }
      lineIds.add(line.id);
      linesByKey.set(lineKey(scenario.id, line.id), { scenario, line });
    }
  }

  const candidatesByLine = new Map<string, PublishedCandidate[]>();
  const candidateKeys = new Set<string>();
  const selectedModelIds = new Set<string>();
  for (const outcome of data.outcomes) {
    if (outcome.kind !== "selected") {
      continue;
    }
    const candidate = outcome.candidate;
    const key = candidateKey(candidate);
    if (candidateKeys.has(key)) {
      throw new Error(
        `A/B の selected candidate が重複しています: ${candidate.model}/${candidate.scenario}/${candidate.line}/${candidate.variant}`,
      );
    }
    candidateKeys.add(key);

    if (!modelsById.has(candidate.model)) {
      throw new Error(`candidate が存在しない model を参照しています: ${candidate.model}`);
    }

    const matchedLine = linesByKey.get(lineKey(candidate.scenario, candidate.line));
    if (!matchedLine) {
      throw new Error(
        `candidate が存在しない scenario/line を参照しています: ${candidate.scenario}/${candidate.line}`,
      );
    }

    if (candidate.variant !== "dry") {
      continue;
    }
    selectedModelIds.add(candidate.model);

    const candidates = candidatesByLine.get(lineKey(candidate.scenario, candidate.line));
    if (candidates) {
      candidates.push(candidate);
    } else {
      candidatesByLine.set(lineKey(candidate.scenario, candidate.line), [candidate]);
    }
  }

  const matches: BlindMatch[] = [];
  for (const scenario of data.scenarios) {
    for (const line of scenario.lines) {
      const lineCandidates = candidatesByLine.get(lineKey(scenario.id, line.id));
      if (!lineCandidates) {
        continue;
      }
      const candidates = [...lineCandidates].sort((left, right) =>
        compareIds(left.model, right.model),
      );
      for (let firstIndex = 0; firstIndex < candidates.length; firstIndex += 1) {
        for (let secondIndex = firstIndex + 1; secondIndex < candidates.length; secondIndex += 1) {
          const firstCandidate = candidates[firstIndex]!;
          const secondCandidate = candidates[secondIndex]!;
          const modelIds = [firstCandidate.model, secondCandidate.model] as const;
          matches.push({
            id: matchId(scenario.id, line.id, modelIds),
            pairId: pairId(modelIds),
            scenario,
            line,
            variant: "dry",
            first: { modelId: firstCandidate.model, candidate: firstCandidate },
            second: { modelId: secondCandidate.model, candidate: secondCandidate },
          });
        }
      }
    }
  }

  return {
    matches,
    models: data.release.models.filter((model) => selectedModelIds.has(model.id)),
  };
}

export function selectNextMatch(
  catalog: BlindCatalog,
  votes: readonly BlindVote[],
  rng: () => number = Math.random,
): PresentedMatch | null {
  const matchesById = indexMatches(catalog.matches);
  const votedMatchIds = new Set<string>();
  const pairVoteCounts = new Map<string, number>();
  for (const vote of votes) {
    const match = assertCatalogVote(vote, matchesById, votedMatchIds);
    votedMatchIds.add(vote.matchId);
    pairVoteCounts.set(match.pairId, (pairVoteCounts.get(match.pairId) ?? 0) + 1);
  }

  const remainingByPair = new Map<string, BlindMatch[]>();
  for (const match of catalog.matches) {
    if (votedMatchIds.has(match.id)) {
      continue;
    }
    const pairMatches = remainingByPair.get(match.pairId);
    if (pairMatches) {
      pairMatches.push(match);
    } else {
      remainingByPair.set(match.pairId, [match]);
    }
  }
  if (remainingByPair.size === 0) {
    return null;
  }

  let minimumVotes = Number.POSITIVE_INFINITY;
  for (const pair of remainingByPair.keys()) {
    minimumVotes = Math.min(minimumVotes, pairVoteCounts.get(pair) ?? 0);
  }
  const leastVotedPairs = [...remainingByPair.keys()].filter(
    (pair) => (pairVoteCounts.get(pair) ?? 0) === minimumVotes,
  );
  const selectedPair = leastVotedPairs[randomIndex(leastVotedPairs.length, rng)]!;
  const pairMatches = remainingByPair.get(selectedPair)!;
  const match = pairMatches[randomIndex(pairMatches.length, rng)]!;
  const firstOnLeft = randomUnit(rng) < 0.5;

  return {
    match,
    left: firstOnLeft ? match.first : match.second,
    right: firstOnLeft ? match.second : match.first,
  };
}

export function rankModels(
  models: readonly Model[],
  votes: readonly BlindVote[],
): readonly ModelRanking[] {
  const modelsById = uniqueModels(models);
  const totals = new Map<
    string,
    { appearances: number; wins: number; ties: number; losses: number; score: number }
  >(models.map(({ id }) => [id, { appearances: 0, wins: 0, ties: 0, losses: 0, score: 0 }]));
  const matchIds = new Set<string>();

  for (const vote of votes) {
    assertStandaloneVote(vote, modelsById, matchIds);
    matchIds.add(vote.matchId);

    const first = totals.get(vote.modelIds[0])!;
    const second = totals.get(vote.modelIds[1])!;
    first.appearances += 1;
    second.appearances += 1;
    if (vote.winnerModelId === null) {
      first.ties += 1;
      second.ties += 1;
      first.score += 0.5;
      second.score += 0.5;
    } else if (vote.winnerModelId === vote.modelIds[0]) {
      first.wins += 1;
      second.losses += 1;
      first.score += 1;
    } else {
      second.wins += 1;
      first.losses += 1;
      second.score += 1;
    }
  }

  const ranked = models.map((model, sourceIndex) => {
    const total = totals.get(model.id)!;
    return {
      model,
      modelId: model.id,
      appearances: total.appearances,
      wins: total.wins,
      ties: total.ties,
      losses: total.losses,
      score: total.score,
      rate: total.appearances >= MIN_MODEL_APPEARANCES ? total.score / total.appearances : null,
      rank: null,
      sourceIndex,
    };
  });
  ranked.sort((left, right) => {
    if (left.rate === null && right.rate === null) {
      return left.sourceIndex - right.sourceIndex;
    }
    if (left.rate === null) {
      return 1;
    }
    if (right.rate === null) {
      return -1;
    }
    return right.rate - left.rate || left.sourceIndex - right.sourceIndex;
  });

  let previousRate: number | null = null;
  let previousRank = 0;
  return ranked.map(({ sourceIndex: _sourceIndex, ...entry }, index) => {
    if (entry.rate === null) {
      return entry;
    }
    const rank = previousRate !== null && entry.rate === previousRate ? previousRank : index + 1;
    previousRate = entry.rate;
    previousRank = rank;
    return { ...entry, rank };
  });
}

function uniqueModels(models: readonly Model[]): ReadonlyMap<string, Model> {
  const modelsById = new Map<string, Model>();
  for (const model of models) {
    if (modelsById.has(model.id)) {
      throw new Error(`model id が重複しています: ${model.id}`);
    }
    modelsById.set(model.id, model);
  }
  return modelsById;
}

function indexMatches(matches: readonly BlindMatch[]): ReadonlyMap<string, BlindMatch> {
  const matchesById = new Map<string, BlindMatch>();
  for (const match of matches) {
    if (matchesById.has(match.id)) {
      throw new Error(`A/B match id が重複しています: ${match.id}`);
    }
    matchesById.set(match.id, match);
  }
  return matchesById;
}

function assertCatalogVote(
  vote: BlindVote,
  matchesById: ReadonlyMap<string, BlindMatch>,
  votedMatchIds: ReadonlySet<string>,
): BlindMatch {
  if (votedMatchIds.has(vote.matchId)) {
    throw new Error(`同じ A/B match へ複数回投票できません: ${vote.matchId}`);
  }
  const match = matchesById.get(vote.matchId);
  if (!match) {
    throw new Error(`投票先の A/B match が存在しません: ${vote.matchId}`);
  }
  const expectedModelIds = matchModelIds(match);
  if (vote.modelIds[0] !== expectedModelIds[0] || vote.modelIds[1] !== expectedModelIds[1]) {
    throw new Error(`投票の model pair が A/B match と一致しません: ${vote.matchId}`);
  }
  assertWinner(vote);
  return match;
}

function assertStandaloneVote(
  vote: BlindVote,
  modelsById: ReadonlyMap<string, Model>,
  matchIds: ReadonlySet<string>,
): void {
  if (matchIds.has(vote.matchId)) {
    throw new Error(`同じ A/B match へ複数回投票できません: ${vote.matchId}`);
  }
  if (vote.modelIds[0] === vote.modelIds[1]) {
    throw new Error(`投票の model pair に同じ model は指定できません: ${vote.modelIds[0]}`);
  }
  if (compareIds(vote.modelIds[0], vote.modelIds[1]) >= 0) {
    throw new Error(`投票の model pair は canonical 順で指定してください: ${vote.matchId}`);
  }
  for (const modelId of vote.modelIds) {
    if (!modelsById.has(modelId)) {
      throw new Error(`投票が存在しない model を参照しています: ${modelId}`);
    }
  }
  assertWinner(vote);
}

function assertWinner(vote: BlindVote): void {
  if (
    vote.winnerModelId !== null &&
    vote.winnerModelId !== vote.modelIds[0] &&
    vote.winnerModelId !== vote.modelIds[1]
  ) {
    throw new Error(`投票の winner が model pair に含まれていません: ${vote.matchId}`);
  }
}

function randomIndex(length: number, rng: () => number): number {
  return Math.floor(randomUnit(rng) * length);
}

function compareIds(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function randomUnit(rng: () => number): number {
  const value = rng();
  if (!Number.isFinite(value) || value < 0 || value >= 1) {
    throw new Error(`乱数は 0 以上 1 未満である必要があります: ${value}`);
  }
  return value;
}

function matchModelIds(match: BlindMatch): readonly [string, string] {
  return [match.first.modelId, match.second.modelId];
}

function lineKey(scenarioId: string, lineId: string): string {
  return JSON.stringify([scenarioId, lineId]);
}

function candidateKey(candidate: PublishedCandidate): string {
  return JSON.stringify([candidate.model, candidate.scenario, candidate.line, candidate.variant]);
}

function pairId(modelIds: readonly [string, string]): string {
  return JSON.stringify(modelIds);
}

function matchId(scenarioId: string, lineId: string, modelIds: readonly [string, string]): string {
  return JSON.stringify([scenarioId, lineId, "dry", ...modelIds]);
}
