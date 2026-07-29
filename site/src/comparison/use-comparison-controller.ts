import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { useAudioPlayer, usePlaybackManager } from "@/audio/audio-provider";
import { candidateKey } from "@/data";
import type { ComparisonProjection } from "@/filters";
import { resolveAudioUrl } from "@/lib/audio-url";

import {
  buildColumnQueue,
  buildRowQueue,
  moveCursor,
  resolveCursor,
  type ComparisonModel,
  type Coordinate,
  type NavigationDirection,
  type PlaybackQueue,
  type QueueItem,
} from "./model";
import { focusCoordinate, followSequenceFocus } from "./matrix-focus";

const SEQUENCE_GAP_MS = 250;

export type SequenceDirection = "row" | "column";
export type SequencePhase = "playing" | "gap";

export interface SequenceState {
  readonly direction: SequenceDirection;
  readonly phase: SequencePhase;
  readonly queue: PlaybackQueue;
  readonly itemIndex: number;
  readonly sessionId: number;
}

export interface ComparisonController {
  readonly cursor: Coordinate | null;
  readonly direction: SequenceDirection;
  readonly sequence: SequenceState | null;
  readonly visibleModelIds: ReadonlySet<string>;
  readonly player: ReturnType<typeof useAudioPlayer>;
  readonly navigate: (direction: NavigationDirection) => Coordinate | null;
  readonly selectModel: (modelId: string) => void;
  readonly selectAndToggle: (coordinate: Coordinate) => void;
  readonly setDirection: (direction: SequenceDirection) => void;
  readonly startOrStopSequence: () => void;
  readonly stop: () => void;
  readonly toggleFocused: () => void;
}

export function useComparisonController(
  model: ComparisonModel,
  projection: ComparisonProjection,
): ComparisonController {
  const manager = usePlaybackManager();
  const player = useAudioPlayer();
  const [cursor, setCursor] = useState<Coordinate | null>(() => {
    const playingCoordinate =
      player.currentClipKey === null
        ? undefined
        : model.getCoordinateForCandidateKey(player.currentClipKey);
    return resolveCursor(model, playingCoordinate ?? null, projection);
  });
  const [direction, setDirectionState] = useState<SequenceDirection>("row");
  const [sequence, setSequenceState] = useState<SequenceState | null>(null);
  const cursorRef = useRef(cursor);
  const directionRef = useRef(direction);
  const projectionRef = useRef(projection);
  const sequenceRef = useRef<SequenceState | null>(null);
  const timerRef = useRef<number | null>(null);
  const sequenceTokenRef = useRef(0);

  const updateSequence = useCallback((next: SequenceState | null) => {
    sequenceRef.current = next;
    setSequenceState(next);
  }, []);

  const updateCursor = useCallback((next: Coordinate | null) => {
    const current = cursorRef.current;
    if (current?.rowIndex === next?.rowIndex && current?.modelId === next?.modelId) {
      return;
    }
    cursorRef.current = next;
    setCursor(next);
  }, []);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const cancelSequence = useCallback(
    (stopPlayback: boolean) => {
      sequenceTokenRef.current += 1;
      clearTimer();
      updateSequence(null);
      if (stopPlayback) {
        manager.stop();
      }
    },
    [clearTimer, manager, updateSequence],
  );

  useLayoutEffect(() => {
    const previousProjection = projectionRef.current;
    projectionRef.current = projection;
    if (previousProjection.key === projection.key) {
      return;
    }

    const hadSequence = sequenceRef.current !== null;
    cancelSequence(hadSequence);

    if (!hadSequence) {
      const currentClipKey = manager.getSnapshot().currentClipKey;
      const playingCoordinate =
        currentClipKey === null ? undefined : model.getCoordinateForCandidateKey(currentClipKey);
      if (
        playingCoordinate !== undefined &&
        (!projection.rowIndexes.has(playingCoordinate.rowIndex) ||
          !projection.modelIds.has(playingCoordinate.modelId))
      ) {
        manager.stop();
      }
    }

    const nextCursor = resolveCursor(model, cursorRef.current, projection);
    updateCursor(nextCursor);
    if (
      document.activeElement instanceof HTMLElement &&
      document.activeElement.dataset.matrixCoordinate !== undefined
    ) {
      if (nextCursor === null) {
        document.activeElement.blur();
      } else {
        focusCoordinate(nextCursor);
      }
    }
  }, [cancelSequence, manager, model, projection, updateCursor]);

  const playQueueItem = useCallback(
    (queue: PlaybackQueue, itemIndex: number, sequenceDirection: SequenceDirection): void => {
      const item = queue.items[itemIndex];
      if (item === undefined) {
        throw new Error(`連続再生 queue の item がありません: ${itemIndex}`);
      }

      updateCursor(item.coordinate);
      followSequenceFocus(item.coordinate);
      const promise = manager.play(toAudioClip(item));
      const sessionId = manager.getSnapshot().sessionId;
      updateSequence({
        direction: sequenceDirection,
        phase: "playing",
        queue,
        itemIndex,
        sessionId,
      });
      void promise;
    },
    [manager, updateCursor, updateSequence],
  );

  useEffect(() => {
    const activeSequence = sequenceRef.current;
    const completion = player.completion;
    if (
      activeSequence === null ||
      completion === null ||
      completion.sessionId !== activeSequence.sessionId
    ) {
      return;
    }

    if (completion.termination !== "ended") {
      cancelSequence(false);
      return;
    }

    const nextIndex = activeSequence.itemIndex + 1;
    if (nextIndex >= activeSequence.queue.items.length) {
      cancelSequence(false);
      return;
    }

    const token = sequenceTokenRef.current + 1;
    sequenceTokenRef.current = token;
    updateSequence({ ...activeSequence, phase: "gap" });
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      if (sequenceTokenRef.current !== token) {
        return;
      }
      playQueueItem(activeSequence.queue, nextIndex, activeSequence.direction);
    }, SEQUENCE_GAP_MS);
  }, [cancelSequence, playQueueItem, player.completion, updateSequence]);

  useEffect(
    () => () => {
      sequenceTokenRef.current += 1;
      clearTimer();
      if (sequenceRef.current !== null) {
        manager.stop();
      }
    },
    [clearTimer, manager],
  );

  const navigate = useCallback(
    (navigationDirection: NavigationDirection): Coordinate | null => {
      const current = cursorRef.current;
      if (current === null) {
        return null;
      }
      const next = moveCursor(model, current, navigationDirection, projectionRef.current);
      if (next.rowIndex === current.rowIndex && next.modelId === current.modelId) {
        return current;
      }

      cancelSequence(false);
      updateCursor(next);
      const cell = model.getCell(next);
      if (cell?.kind === "selected") {
        void manager.play(toAudioClip({ coordinate: next, candidate: cell.candidate }));
      } else {
        manager.stop();
      }
      return next;
    },
    [cancelSequence, manager, model, updateCursor],
  );

  const selectAndToggle = useCallback(
    (coordinate: Coordinate) => {
      cancelSequence(false);
      updateCursor(coordinate);
      const cell = model.getCell(coordinate);
      if (cell?.kind === "selected") {
        void manager.toggle(toAudioClip({ coordinate, candidate: cell.candidate }));
      } else {
        manager.stop();
      }
    },
    [cancelSequence, manager, model, updateCursor],
  );

  const toggleFocused = useCallback(() => {
    const current = cursorRef.current;
    if (current === null) {
      return;
    }
    const cell = model.getCell(current);
    if (cell?.kind !== "selected") {
      cancelSequence(true);
      return;
    }

    const activeSequence = sequenceRef.current;
    if (activeSequence === null) {
      void manager.toggle(toAudioClip({ coordinate: current, candidate: cell.candidate }));
      return;
    }

    if (activeSequence.phase === "gap") {
      cancelSequence(false);
      void manager.play(toAudioClip({ coordinate: current, candidate: cell.candidate }));
      return;
    }

    void manager.toggle(toAudioClip({ coordinate: current, candidate: cell.candidate }));
  }, [cancelSequence, manager, model]);

  const startOrStopSequence = useCallback(() => {
    if (sequenceRef.current !== null) {
      cancelSequence(true);
      return;
    }
    const current = cursorRef.current;
    if (current === null) {
      return;
    }

    const queue =
      directionRef.current === "row"
        ? buildRowQueue(model, current, projectionRef.current)
        : buildColumnQueue(model, current, projectionRef.current);
    if (queue.items.length === 0) {
      manager.stop();
      return;
    }

    sequenceTokenRef.current += 1;
    playQueueItem(queue, 0, directionRef.current);
  }, [cancelSequence, manager, model, playQueueItem]);

  const setDirection = useCallback(
    (nextDirection: SequenceDirection) => {
      if (nextDirection === directionRef.current) {
        return;
      }
      cancelSequence(true);
      directionRef.current = nextDirection;
      setDirectionState(nextDirection);
    },
    [cancelSequence],
  );

  const selectModel = useCallback(
    (modelId: string) => {
      if (!projectionRef.current.modelIds.has(modelId)) {
        throw new Error(`非表示 model は選択できません: ${modelId}`);
      }
      cancelSequence(true);
      const current = cursorRef.current;
      updateCursor(
        current === null
          ? resolveCursor(model, null, projectionRef.current)
          : { ...current, modelId },
      );
    },
    [cancelSequence, model, updateCursor],
  );

  const stop = useCallback(() => cancelSequence(true), [cancelSequence]);

  return useMemo(
    () => ({
      cursor,
      direction,
      sequence,
      visibleModelIds: projection.modelIds,
      player,
      navigate,
      selectModel,
      selectAndToggle,
      setDirection,
      startOrStopSequence,
      stop,
      toggleFocused,
    }),
    [
      cursor,
      direction,
      navigate,
      player,
      selectAndToggle,
      selectModel,
      sequence,
      setDirection,
      startOrStopSequence,
      stop,
      toggleFocused,
      projection.modelIds,
    ],
  );
}

function toAudioClip(item: QueueItem) {
  return {
    key: candidateKey(item.candidate),
    url: resolveAudioUrl(item.candidate.path),
  };
}
