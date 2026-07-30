import { memo, type KeyboardEvent } from "react";

import { useAudioProgress } from "@/audio/audio-provider";
import { AutomaticQualityBadge } from "@/components/automatic-quality-badge";
import { WaveformProgress } from "@/components/waveform-progress";
import type { PlaybackStatus } from "@/audio/playback-manager";
import type { Model } from "@/data";

import type { ComparisonCell, Coordinate, NavigationDirection } from "./model";
import { coordinateKey, focusCoordinate } from "./matrix-focus";

interface MatrixCellProps {
  cell: ComparisonCell | undefined;
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
  cell,
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
  const candidate = cell?.kind === "selected" ? cell.candidate : undefined;
  const generationFailure = cell?.kind === "failure" ? cell.failure : undefined;
  const unavailableLabel =
    cell === undefined || cell.kind === "skipped" || cell.kind === "uncurated"
      ? "未収録"
      : undefined;
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
      aria-disabled={cell?.kind !== "selected"}
      aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown Space Enter Escape"
      aria-label={`${accessibleLabel}・${model.name}・${
        unavailableLabel ??
        (generationFailure
          ? "生成失敗"
          : isError
            ? "再生エラー、再試行"
            : isPlaying
              ? "停止"
              : isPaused
                ? "再開"
                : "再生")
      }`}
      className={[
        "group relative flex min-h-10 w-full items-center justify-between overflow-hidden rounded border px-2 py-1.5 text-left font-mono text-[11px] transition-[border-color,background-color,color] motion-reduce:transition-none",
        "focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
        unavailableLabel
          ? "border-transparent bg-transparent text-muted-foreground/45"
          : generationFailure
            ? "border-destructive/55 bg-destructive/10 text-destructive"
            : "border-border bg-card text-foreground hover:border-primary/55 hover:bg-primary/5",
        isCursor ? "ring-2 ring-primary/75" : "",
        isCurrent ? "border-primary bg-primary/12 text-primary" : "",
        isError ? "border-destructive bg-destructive/10 text-destructive" : "",
      ].join(" ")}
      data-matrix-coordinate={coordinateKey(coordinate)}
      onClick={() => {
        if (cell?.kind === "selected") {
          selectAndToggle(coordinate);
        }
      }}
      onKeyDown={handleKeyDown}
      tabIndex={isCursor ? 0 : -1}
      type="button"
    >
      {unavailableLabel ? (
        <span className="relative z-10" title={unavailableLabel}>
          —
        </span>
      ) : (
        <span className="relative z-10 flex min-w-0 items-center gap-2">
          <span aria-hidden="true" className="w-3 shrink-0 text-center">
            {isPlaying ? "Ⅱ" : generationFailure ? "!" : "▶"}
          </span>
          <span className="truncate">
            {generationFailure ? "生成失敗" : isError ? "再生エラー" : model.name}
          </span>
          {candidate ? <AutomaticQualityBadge candidate={candidate} compact /> : null}
        </span>
      )}
      {candidate && !isCurrent ? (
        <span className="relative z-10 ml-2 shrink-0 text-[10px] text-current/70">
          {candidate.duration_sec.toFixed(2)}s
        </span>
      ) : null}
      {isCurrent && candidate ? (
        <CellPlaybackProgress fallbackDuration={candidate.duration_sec} />
      ) : null}
      {generationFailure ? (
        <span className="relative z-10 ml-2 shrink-0 text-[9px] text-current/75">再生成待ち</span>
      ) : null}
    </button>
  );
});

function CellPlaybackProgress({ fallbackDuration }: { fallbackDuration: number }) {
  const progress = useAudioProgress();
  const duration = progress.duration > 0 ? progress.duration : fallbackDuration;
  const ratio = duration > 0 ? Math.min(progress.currentTime / duration, 1) : 0;

  return (
    <>
      <span className="absolute inset-x-1.5 bottom-1 z-0">
        <WaveformProgress className="h-4 opacity-70" ratio={ratio} />
      </span>
      <span className="relative z-10 ml-2 shrink-0 bg-card/85 px-1 text-[10px] text-primary">
        {formatCompactTime(progress.currentTime)}
      </span>
    </>
  );
}

function formatCompactTime(seconds: number): string {
  const safeSeconds = Number.isFinite(seconds) && seconds >= 0 ? seconds : 0;
  const minutes = Math.floor(safeSeconds / 60);
  return `${minutes}:${(safeSeconds % 60).toFixed(1).padStart(4, "0")}`;
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
