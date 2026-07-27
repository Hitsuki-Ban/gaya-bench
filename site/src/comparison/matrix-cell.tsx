import { memo, type KeyboardEvent } from "react";

import { useAudioProgress } from "@/audio/audio-provider";
import type { PlaybackStatus } from "@/audio/playback-manager";
import type { Clip, Model } from "@/data";
import { resolveAudioUrl } from "@/lib/audio-url";

import type { Coordinate, NavigationDirection } from "./model";
import { coordinateKey, focusCoordinate } from "./matrix-focus";

interface MatrixCellProps {
  clip: Clip | undefined;
  coordinate: Coordinate;
  isCursor: boolean;
  isCurrent: boolean;
  model: Model;
  status: PlaybackStatus;
  accessibleLabel: string;
  navigate: (direction: NavigationDirection) => Coordinate | null;
  selectAndToggle: (coordinate: Coordinate) => void;
  startOrStopSequence: () => void;
  stop: () => void;
  toggleFocused: () => void;
}

export const MatrixCell = memo(function MatrixCell({
  clip,
  coordinate,
  isCursor,
  isCurrent,
  model,
  status,
  accessibleLabel,
  navigate,
  selectAndToggle,
  startOrStopSequence,
  stop,
  toggleFocused,
}: MatrixCellProps) {
  const isPlaying = isCurrent && (status === "playing" || status === "loading");
  const isPaused = isCurrent && status === "paused";
  const isError = isCurrent && status === "error";

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>): void {
    const direction = keyToDirection(event.key);
    if (direction) {
      event.preventDefault();
      const next = navigate(direction);
      if (next) {
        focusCoordinate(next);
      }
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      startOrStopSequence();
      return;
    }
    if (event.key === " ") {
      event.preventDefault();
      toggleFocused();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      stop();
    }
  }

  return (
    <button
      aria-disabled={clip === undefined}
      aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown Space Enter Escape"
      aria-label={`${accessibleLabel}・${model.name}・${
        clip === undefined
          ? "未生成"
          : isError
            ? "再生エラー、再試行"
            : isPlaying
              ? "停止"
              : isPaused
                ? "再開"
                : "再生"
      }`}
      className={[
        "group relative flex min-h-11 w-full items-center justify-between overflow-hidden rounded border px-2.5 py-2 text-left font-mono text-xs transition-[border-color,background-color,color]",
        "focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
        clip === undefined
          ? "border-dashed border-border/70 bg-muted/30 text-muted-foreground"
          : "border-border bg-card text-foreground hover:border-primary/55 hover:bg-primary/5",
        isCursor ? "ring-1 ring-accent/80" : "",
        isCurrent ? "border-primary bg-primary/12 text-primary" : "",
        isError ? "border-destructive bg-destructive/10 text-destructive" : "",
      ].join(" ")}
      data-audio-url={clip ? resolveAudioUrl(clip.path) : undefined}
      data-matrix-coordinate={coordinateKey(coordinate)}
      onClick={() => selectAndToggle(coordinate)}
      onKeyDown={handleKeyDown}
      tabIndex={isCursor ? 0 : -1}
      type="button"
    >
      <span className="relative z-10 flex items-center gap-2">
        <span aria-hidden="true" className="w-3 text-center">
          {isPlaying ? "Ⅱ" : clip === undefined ? "—" : "▶"}
        </span>
        <span className="truncate">
          {clip === undefined ? "未生成" : isError ? "再生エラー" : model.name}
        </span>
      </span>
      {clip ? (
        <span className="relative z-10 ml-2 shrink-0 text-[10px] text-current/70">
          {clip.duration_sec.toFixed(2)}s
        </span>
      ) : null}
      {isCurrent && clip ? <CellProgress fallbackDuration={clip.duration_sec} /> : null}
    </button>
  );
});

function CellProgress({ fallbackDuration }: { fallbackDuration: number }) {
  const progress = useAudioProgress();
  const duration = progress.duration > 0 ? progress.duration : fallbackDuration;
  const ratio = duration > 0 ? Math.min(progress.currentTime / duration, 1) : 0;

  return (
    <span
      aria-hidden="true"
      className="absolute inset-y-0 left-0 bg-primary/14 transition-[width] duration-150"
      style={{ width: `${ratio * 100}%` }}
    />
  );
}

function keyToDirection(key: string): NavigationDirection | null {
  if (key === "ArrowLeft") {
    return "left";
  }
  if (key === "ArrowRight") {
    return "right";
  }
  if (key === "ArrowUp") {
    return "up";
  }
  if (key === "ArrowDown") {
    return "down";
  }
  return null;
}
