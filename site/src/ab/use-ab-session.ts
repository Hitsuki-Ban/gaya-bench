import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAudioPlayer, usePlaybackManager } from "@/audio/audio-provider";
import { benchmarkData } from "@/data";
import { resolveAudioUrl } from "@/lib/audio-url";
import {
  buildBlindCatalog,
  datasetIdentity,
  MIN_MODEL_APPEARANCES,
  rankModels,
  selectNextMatch,
  type BlindCatalog,
  type BlindVote,
  type DatasetIdentity,
  type PresentedMatch,
} from "./model";
import { AB_STORAGE_KEY, readVotes, resetVotes, writeVotes, type VoteStorage } from "./storage";

export type CandidateSide = "left" | "right";
export type VoteChoice = CandidateSide | "tie";
export type AbSessionKind = "ready" | "complete" | "unavailable" | "error";

export interface PreviousVote {
  readonly leftModelName: string;
  readonly rightModelName: string;
  readonly choice: "A" | "B" | "引き分け";
}

interface SessionData {
  readonly votes: readonly BlindVote[];
  readonly rawSnapshot: string | null;
  readonly presentation: PresentedMatch | null;
  readonly previousVote: PreviousVote | null;
  readonly error: string | null;
  readonly notice: string;
}

const catalog = buildBlindCatalog(benchmarkData);
const dataset = datasetIdentity(benchmarkData);

export function useAbSession() {
  const player = useAudioPlayer();
  const manager = usePlaybackManager();
  const storageRef = useRef<VoteStorage | null>(null);
  const comparisonRef = useRef<HTMLDivElement>(null);
  const committingRef = useRef(false);
  const [isCommitting, setIsCommitting] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [session, setSession] = useState<SessionData>(() =>
    initializeSession(catalog, dataset, storageRef),
  );

  const kind = sessionKind(catalog, session);
  const rankings = useMemo(() => {
    const detailed = rankModels(catalog.models, session.votes);
    return detailed.map((ranking) => ({
      modelId: ranking.modelId,
      modelName: ranking.model.name,
      appearances: ranking.appearances,
      wins: ranking.wins,
      ties: ranking.ties,
      losses: ranking.losses,
      scoreRate: ranking.rate,
      rank: ranking.rank,
      requiredAppearances: Math.max(0, MIN_MODEL_APPEARANCES - ranking.appearances),
    }));
  }, [session.votes]);

  const presentation = useMemo(() => {
    if (session.presentation === null) {
      return null;
    }
    const { match, left, right } = session.presentation;
    const character = match.scenario.characters.find(({ id }) => id === match.line.character);
    if (!character) {
      throw new Error(
        `A/B match の line が存在しない character を参照しています: ${match.scenario.id}/${match.line.id}`,
      );
    }
    const leftKey = audioKey(match.id, "left");
    const rightKey = audioKey(match.id, "right");
    return {
      scenarioTitle: match.scenario.title,
      characterName: character.name,
      lineText: match.line.text,
      emotion: match.line.emotion,
      difficulty: match.line.difficulty,
      delivery: match.line.delivery,
      leftStatus: candidateStatus(player.currentClipKey, player.status, leftKey),
      rightStatus: candidateStatus(player.currentClipKey, player.status, rightKey),
      left,
      right,
      leftKey,
      rightKey,
    };
  }, [player.currentClipKey, player.status, session.presentation]);

  useEffect(() => {
    manager.stop();
    return () => {
      manager.stop();
    };
  }, [manager]);

  useEffect(() => {
    if (session.presentation !== null) {
      comparisonRef.current?.focus();
    }
  }, [session.presentation]);

  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (event.key !== AB_STORAGE_KEY && event.key !== null) {
        return;
      }
      manager.stop();
      try {
        const storage = browserStorage();
        storageRef.current = storage;
        const stored = readVotes(storage, catalog, dataset);
        setSession({
          votes: stored.votes,
          rawSnapshot: stored.rawSnapshot,
          presentation: selectNextMatch(catalog, stored.votes),
          previousVote: null,
          error: null,
          notice: "別のタブで更新された投票結果を読み込みました。",
        });
      } catch {
        setSession((current) => ({
          ...current,
          presentation: null,
          previousVote: null,
          error: storageErrorMessage(),
          notice: "",
        }));
      }
    };

    window.addEventListener("storage", handleStorage);
    return () => {
      window.removeEventListener("storage", handleStorage);
    };
  }, [manager]);

  const playCandidate = useCallback(
    async (side: CandidateSide) => {
      if (presentation === null || kind !== "ready") {
        return;
      }
      const candidate = side === "left" ? presentation.left : presentation.right;
      const key = side === "left" ? presentation.leftKey : presentation.rightKey;
      try {
        await player.toggle({ key, url: resolveAudioUrl(candidate.clip.path) });
      } catch {
        setSession((current) => ({
          ...current,
          notice: "音声を再生できませんでした。もう一度お試しください。",
        }));
      }
    },
    [kind, player, presentation],
  );

  const castVote = useCallback(
    (choice: VoteChoice) => {
      if (kind !== "ready" || session.presentation === null || committingRef.current) {
        return;
      }

      const currentPresentation = session.presentation;
      const previousVote: PreviousVote = {
        leftModelName: modelName(currentPresentation.left.modelId),
        rightModelName: modelName(currentPresentation.right.modelId),
        choice: voteChoiceLabel(choice),
      };
      committingRef.current = true;
      setIsCommitting(true);
      try {
        const storage = storageRef.current ?? browserStorage();
        storageRef.current = storage;
        const latestRaw = storage.getItem(AB_STORAGE_KEY);
        if (latestRaw !== session.rawSnapshot) {
          throw new Error("A/B 投票が別のタブで更新されています。");
        }

        const vote: BlindVote = {
          matchId: currentPresentation.match.id,
          modelIds: [
            currentPresentation.match.first.modelId,
            currentPresentation.match.second.modelId,
          ],
          winnerModelId:
            choice === "tie"
              ? null
              : choice === "left"
                ? currentPresentation.left.modelId
                : currentPresentation.right.modelId,
        };
        const votes = [...session.votes, vote];
        const rawSnapshot = writeVotes(storage, { version: 1, dataset, votes });
        manager.stop();
        const nextPresentation = selectNextMatch(catalog, votes);
        setSession({
          votes,
          rawSnapshot,
          presentation: nextPresentation,
          previousVote,
          error: null,
          notice:
            nextPresentation === null
              ? "投票を保存しました。すべての比較が完了しました。"
              : "投票を保存しました。次の比較へ進みました。",
        });
      } catch {
        manager.stop();
        setSession((current) => ({
          ...current,
          presentation: null,
          error: storageErrorMessage(),
          notice: "",
        }));
      } finally {
        committingRef.current = false;
        setIsCommitting(false);
      }
    },
    [kind, manager, session],
  );

  const reset = useCallback(() => {
    if (isResetting) {
      return;
    }
    setIsResetting(true);
    manager.stop();
    try {
      const storage = storageRef.current ?? browserStorage();
      storageRef.current = storage;
      resetVotes(storage);
      setSession({
        votes: [],
        rawSnapshot: null,
        presentation: selectNextMatch(catalog, []),
        previousVote: null,
        error: null,
        notice: "ローカル結果をリセットしました。",
      });
    } catch {
      setSession((current) => ({
        ...current,
        presentation: null,
        error: storageErrorMessage(),
        notice: "",
      }));
    } finally {
      setIsResetting(false);
    }
  }, [isResetting, manager]);

  return {
    kind,
    error: session.error ?? "",
    notice: session.notice,
    modelCount: catalog.models.length,
    totalMatches: catalog.matches.length,
    remainingMatches: catalog.matches.length - session.votes.length,
    votesCount: session.votes.length,
    rankings,
    previousVote: session.previousVote,
    presentation: presentation!,
    comparisonRef,
    isCommitting,
    isResetting,
    playCandidate,
    castVote,
    reset,
  };
}

function initializeSession(
  currentCatalog: BlindCatalog,
  currentDataset: DatasetIdentity,
  storageRef: { current: VoteStorage | null },
): SessionData {
  try {
    const storage = browserStorage();
    storageRef.current = storage;
    const stored = readVotes(storage, currentCatalog, currentDataset);
    return {
      votes: stored.votes,
      rawSnapshot: stored.rawSnapshot,
      presentation: selectNextMatch(currentCatalog, stored.votes),
      previousVote: null,
      error: null,
      notice: "",
    };
  } catch {
    return {
      votes: [],
      rawSnapshot: null,
      presentation: null,
      previousVote: null,
      error: storageErrorMessage(),
      notice: "",
    };
  }
}

function sessionKind(currentCatalog: BlindCatalog, session: SessionData): AbSessionKind {
  if (session.error !== null) {
    return "error";
  }
  if (currentCatalog.matches.length === 0) {
    return "unavailable";
  }
  if (session.presentation === null) {
    return "complete";
  }
  return "ready";
}

function browserStorage(): VoteStorage {
  return window.localStorage;
}

function audioKey(matchId: string, side: CandidateSide): string {
  return JSON.stringify(["blind", matchId, side]);
}

function candidateStatus(
  currentClipKey: string | null,
  status: "idle" | "loading" | "playing" | "paused" | "error",
  key: string,
) {
  return currentClipKey === key ? status : "idle";
}

function modelName(modelId: string): string {
  const model = catalog.models.find(({ id }) => id === modelId);
  if (!model) {
    throw new Error(`A/B candidate の model が存在しません: ${modelId}`);
  }
  return model.name;
}

function voteChoiceLabel(choice: VoteChoice): PreviousVote["choice"] {
  if (choice === "left") {
    return "A";
  }
  if (choice === "right") {
    return "B";
  }
  return "引き分け";
}

function storageErrorMessage(): string {
  return "保存データが壊れているか、現在の比較データと一致しないか、ブラウザが保存を許可していません。リセットして再開してください。";
}
