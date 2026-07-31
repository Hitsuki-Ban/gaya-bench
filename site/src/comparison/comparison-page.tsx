import { AlertTriangle, AudioWaveform, ListVideo, Pause, Play, Rows3, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { useAudioProgress } from "@/audio/audio-provider";
import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { benchmarkData, candidateKey, selectedCandidates } from "@/data";
import {
  decodeFilterQuery,
  encodeFilterState,
  projectComparisonModel,
  resetNarrowingFilters,
  type ComparisonProjection,
  type FilterQueryIssue,
  type FilterState,
} from "@/filters";

import { DesktopMatrix } from "./desktop-matrix";
import { FilterToolbar } from "./filter-toolbar";
import { focusCoordinate } from "./matrix-focus";
import { buildComparisonModel, type Coordinate } from "./model";
import { MobileMatrix } from "./mobile-matrix";
import { ScenarioSelector } from "./scenario-selector";
import { useComparisonController, type SequenceDirection } from "./use-comparison-controller";
import { useMediaQuery } from "./use-media-query";

const comparisonModel = buildComparisonModel(benchmarkData);
const capabilityLegend = [
  ["E", "感情"],
  ["P", "声質プロンプト"],
  ["C", "クローン"],
  ["N", "非言語音"],
  ["R", "読み指定"],
] as const;

export function ComparisonPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawSearch = searchParams.toString();
  const result = useMemo(
    () => decodeFilterQuery(new URLSearchParams(rawSearch), benchmarkData),
    [rawSearch],
  );

  useEffect(() => {
    const currentSearch = rawSearch.length === 0 ? "" : `?${rawSearch}`;
    if (result.ok && result.canonicalSearch !== currentSearch) {
      setSearchParams(new URLSearchParams(result.canonicalSearch), {
        preventScrollReset: true,
        replace: true,
      });
    }
  }, [rawSearch, result, setSearchParams]);

  const resetFilters = useCallback(() => {
    setSearchParams(new URLSearchParams(), { preventScrollReset: true });
  }, [setSearchParams]);

  if (!result.ok) {
    return <InvalidFilterQuery issues={result.issues} onReset={resetFilters} />;
  }

  return (
    <FilteredComparisonPage
      canonicalSearch={result.canonicalSearch}
      onChange={(state) =>
        setSearchParams(new URLSearchParams(encodeFilterState(state, benchmarkData)), {
          preventScrollReset: true,
        })
      }
      state={result.state}
    />
  );
}

function FilteredComparisonPage({
  canonicalSearch,
  onChange,
  state,
}: {
  canonicalSearch: string;
  onChange: (state: FilterState) => void;
  state: FilterState;
}) {
  const projection = useMemo(() => projectComparisonModel(comparisonModel, state), [state]);
  const controller = useComparisonController(comparisonModel, projection);
  const isDesktop = useMediaQuery("(min-width: 768px)");
  const [hasPlaybackStarted, setHasPlaybackStarted] = useState(false);
  useEffect(() => {
    if (controller.player.currentClipKey !== null) {
      setHasPlaybackStarted(true);
    }
  }, [controller.player.currentClipKey]);
  const filteredSelectedCount = useMemo(() => countProjectionSelected(projection), [projection]);
  const firstPlayableCoordinate = useMemo(
    () => findFirstPlayableCoordinate(projection),
    [projection],
  );
  const playFirst = useCallback(() => {
    if (!firstPlayableCoordinate) {
      return;
    }
    controller.selectAndToggle(firstPlayableCoordinate);
    focusCoordinate(firstPlayableCoordinate);
  }, [controller, firstPlayableCoordinate]);
  const resetNarrowing = useCallback(
    () => onChange(resetNarrowingFilters(state, benchmarkData)),
    [onChange, state],
  );

  return (
    <div className="space-y-4">
      <PageIntro
        aside={
          <div className="flex w-full flex-col gap-2 lg:w-auto lg:min-w-80">
            <Button disabled={firstPlayableCoordinate === null} onClick={playFirst} size="lg">
              <Play aria-hidden="true" data-icon="inline-start" />
              まず聴いてみる
            </Button>
            <div className="grid grid-cols-3 gap-px overflow-hidden rounded-md border bg-border">
              <Metric
                label="セリフ"
                value={`${projection.rows.length}/${comparisonModel.rows.length}`}
              />
              <Metric
                label="音声"
                value={`${filteredSelectedCount}/${selectedCandidates.length}`}
              />
              <Metric
                label="モデル"
                value={`${projection.models.length}/${comparisonModel.models.length}`}
              />
            </div>
          </div>
        }
        description="同じセリフをモデルごとに、その場で再生して比べられます。"
        eyebrow="聴き比べ"
        title="日本語TTSのゲームガヤ演技を聴き比べる"
      />

      <ScenarioSelector onChange={onChange} search={canonicalSearch} state={state} />

      <FilterToolbar
        filteredRows={projection.rows.length}
        onChange={onChange}
        onReset={resetNarrowing}
        state={state}
        totalRows={comparisonModel.rows.length}
      />

      <MatrixToolbar controller={controller} visibleModelCount={projection.models.length} />

      {controller.cursor === null ? (
        <div className="rounded-lg border border-dashed p-8 text-center">
          <p className="font-semibold">条件に一致するセリフがありません。</p>
          <p className="mt-2 text-sm text-muted-foreground">
            シナリオまたはキャラクター・感情・難易度の条件を緩めてください。
          </p>
          <Button className="mt-4" onClick={resetNarrowing} variant="outline">
            絞り込みを戻す
          </Button>
        </div>
      ) : isDesktop ? (
        <DesktopMatrix
          controller={controller}
          model={comparisonModel}
          projection={projection}
          search={canonicalSearch}
        />
      ) : (
        <MobileMatrix
          controller={controller}
          model={comparisonModel}
          projection={projection}
          search={canonicalSearch}
        />
      )}

      <KeyboardHelp isDesktop={isDesktop} />
      {isDesktop || hasPlaybackStarted ? (
        <Transport controller={controller} projection={projection} />
      ) : null}
    </div>
  );
}

function MatrixToolbar({
  controller,
  visibleModelCount,
}: {
  controller: ReturnType<typeof useComparisonController>;
  visibleModelCount: number;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-md border bg-card px-3 py-2 lg:flex-row lg:items-center lg:justify-between">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">
          <AudioWaveform aria-hidden="true" data-icon="inline-start" />
          <span title="-18 LUFS 目標 / モノラル / 48 kHz">比較用に音量調整済み</span>
        </Badge>
        <Badge variant="secondary">表示モデル {visibleModelCount}</Badge>
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer text-foreground">対応機能の記号</summary>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 rounded border bg-background px-2 py-1.5">
            {capabilityLegend.map(([key, label]) => (
              <span className="flex items-center gap-1.5" key={key}>
                <span className="grid size-5 place-items-center rounded border border-primary/55 bg-primary/10 font-mono text-[9px] text-primary">
                  {key}
                </span>
                {label}
              </span>
            ))}
          </div>
        </details>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[10px] tracking-wider text-muted-foreground uppercase">
          連続再生
        </span>
        <DirectionButton
          active={controller.direction === "row"}
          direction="row"
          icon={Rows3}
          label="行方向"
          setDirection={controller.setDirection}
        />
        <DirectionButton
          active={controller.direction === "column"}
          direction="column"
          icon={ListVideo}
          label="列方向"
          setDirection={controller.setDirection}
        />
        <Button
          className="min-w-32"
          onClick={controller.startOrStopSequence}
          variant={controller.sequence ? "default" : "outline"}
        >
          {controller.sequence ? (
            <Square aria-hidden="true" data-icon="inline-start" />
          ) : (
            <Play aria-hidden="true" data-icon="inline-start" />
          )}
          {controller.sequence ? "連続停止" : "連続再生"}
        </Button>
      </div>
    </div>
  );
}

function DirectionButton({
  active,
  direction,
  icon: Icon,
  label,
  setDirection,
}: {
  active: boolean;
  direction: SequenceDirection;
  icon: typeof Rows3;
  label: string;
  setDirection: (direction: SequenceDirection) => void;
}) {
  return (
    <Button
      aria-pressed={active}
      onClick={() => setDirection(direction)}
      size="sm"
      variant={active ? "secondary" : "ghost"}
    >
      <Icon aria-hidden="true" data-icon="inline-start" />
      {label}
    </Button>
  );
}

function KeyboardHelp({ isDesktop }: { isDesktop: boolean }) {
  return (
    <div
      className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-md border bg-muted/35 px-3 py-2 text-xs text-muted-foreground"
      id="matrix-keyboard-help"
    >
      <span className="font-mono text-[10px] tracking-wider text-foreground uppercase">
        キー操作
      </span>
      {isDesktop ? (
        <>
          <Shortcut keys="← →" label="モデル移動" />
          <Shortcut keys="↑ ↓" label="セリフ移動" />
        </>
      ) : (
        <Shortcut keys="← →" label="モデルタブ移動" />
      )}
      <Shortcut keys="Space" label="再生 / 一時停止" />
      <Shortcut keys="Enter" label="連続再生 / 停止" />
      <Shortcut keys="Esc" label="全停止" />
    </div>
  );
}

function Shortcut({ keys, label }: { keys: string; label: string }) {
  return (
    <span className="flex items-center gap-2">
      <kbd className="rounded border bg-background px-1.5 py-0.5 font-mono text-[10px] text-foreground">
        {keys}
      </kbd>
      {label}
    </span>
  );
}

function Transport({
  controller,
  projection,
}: {
  controller: ReturnType<typeof useComparisonController>;
  projection: ComparisonProjection;
}) {
  const progress = useAudioProgress();
  const cursor = controller.cursor;
  if (cursor === null) {
    return null;
  }
  if (!projection.rowIndexes.has(cursor.rowIndex) || !projection.modelIds.has(cursor.modelId)) {
    return null;
  }

  const row = comparisonModel.rows[cursor.rowIndex]!;
  const model = comparisonModel.models.find(({ id }) => id === cursor.modelId);
  if (!model) {
    throw new Error(`transport の model が存在しません: ${cursor.modelId}`);
  }
  const cell = comparisonModel.getCell(cursor);
  const candidate = cell?.kind === "selected" ? cell.candidate : undefined;
  const duration = progress.duration > 0 ? progress.duration : (candidate?.duration_sec ?? 0);
  const ratio = duration > 0 ? Math.min(progress.currentTime / duration, 1) : 0;
  const isCurrentClip =
    candidate !== undefined && controller.player.currentClipKey === candidateKey(candidate);
  const isPlaying =
    isCurrentClip &&
    (controller.player.status === "playing" || controller.player.status === "loading");
  const visibleRowPosition = projection.rows.findIndex(
    ({ rowIndex }) => rowIndex === cursor.rowIndex,
  );

  return (
    <div className="sticky bottom-3 z-30 rounded-md border border-primary/45 bg-background/96 p-3 shadow-2xl shadow-black/50 backdrop-blur">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm">
            <span className="font-semibold text-primary">{row.character.name}</span>
            <span className="mx-2 text-border">/</span>
            {row.line.text}
          </p>
          <p className="mt-1 font-mono text-[10px] text-muted-foreground">
            {row.scenario.title} · {model.name} · {visibleRowPosition + 1}/{projection.rows.length}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            aria-label={isPlaying ? "現在のクリップを一時停止" : "現在のクリップを再生"}
            disabled={candidate === undefined}
            onClick={controller.toggleFocused}
            size="sm"
          >
            {isPlaying ? (
              <Pause aria-hidden="true" data-icon="inline-start" />
            ) : (
              <Play aria-hidden="true" data-icon="inline-start" />
            )}
            Space
          </Button>
          <Button onClick={controller.stop} size="sm" variant="outline">
            <Square aria-hidden="true" data-icon="inline-start" />
            停止
          </Button>
        </div>

        <div className="w-full lg:w-72">
          <div
            aria-label="現在のクリップの再生位置"
            aria-valuemax={Math.max(duration, 0)}
            aria-valuemin={0}
            aria-valuenow={Math.min(progress.currentTime, Math.max(duration, 0))}
            className="h-1.5 overflow-hidden rounded-full bg-muted"
            role="progressbar"
          >
            <div
              className="gaya-progress h-full rounded-full bg-primary transition-[width] duration-150"
              style={{ width: `${ratio * 100}%` }}
            />
          </div>
          <div className="mt-1.5 flex justify-between font-mono text-[10px] text-muted-foreground">
            <span>{formatTime(progress.currentTime)}</span>
            <span>
              {controller.sequence
                ? `${controller.sequence.itemIndex + 1}/${controller.sequence.queue.items.length} · 未収録 ${controller.sequence.queue.skippedCount}`
                : playbackStatusLabel(controller.player.status)}
            </span>
            <span>{formatTime(duration)}</span>
          </div>
        </div>
      </div>
      {controller.player.status === "error" ? (
        <p className="mt-2 text-xs text-destructive" role="alert">
          {controller.player.error?.message ?? "音声を再生できません。"}
        </p>
      ) : null}
    </div>
  );
}

function InvalidFilterQuery({
  issues,
  onReset,
}: {
  issues: readonly FilterQueryIssue[];
  onReset: () => void;
}) {
  return (
    <div className="space-y-6">
      <PageIntro
        description="URL のフィルタ指定を読み取れませんでした。値を確認するか、初期状態へ戻してください。"
        eyebrow="聴き比べ / フィルタ指定エラー"
        title="共有リンクの条件が無効です。"
      />
      <div
        className="rounded-lg border border-destructive/40 bg-destructive/5 p-5"
        data-visual-intent="error"
      >
        <h2 className="flex items-center gap-2 text-sm font-semibold text-destructive">
          <AlertTriangle aria-hidden="true" className="size-4" />
          フィルタ指定を確認してください
        </h2>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          {issues.map((issue, index) => (
            <li key={`${issue.code}/${issue.key}/${issue.value ?? ""}/${index}`}>
              {issue.message}
            </li>
          ))}
        </ul>
        <Button className="mt-4" onClick={onReset} variant="outline">
          フィルタを初期化
        </Button>
      </div>
    </div>
  );
}

function countProjectionSelected(projection: ComparisonProjection): number {
  let count = 0;
  for (const { rowIndex } of projection.rows) {
    for (const model of projection.models) {
      if (comparisonModel.getCell({ rowIndex, modelId: model.id })?.kind === "selected") {
        count += 1;
      }
    }
  }
  return count;
}

function findFirstPlayableCoordinate(projection: ComparisonProjection): Coordinate | null {
  for (const { rowIndex } of projection.rows) {
    for (const model of projection.models) {
      const coordinate = { rowIndex, modelId: model.id };
      if (comparisonModel.getCell(coordinate)?.kind === "selected") {
        return coordinate;
      }
    }
  }
  return null;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-card px-4 py-3 text-center">
      <p className="font-mono text-lg text-foreground">{value}</p>
      <p className="text-[10px] tracking-wider text-muted-foreground">{label}</p>
    </div>
  );
}

function playbackStatusLabel(
  status: ReturnType<typeof useComparisonController>["player"]["status"],
) {
  return {
    idle: "停止中",
    loading: "読込中",
    playing: "再生中",
    paused: "一時停止",
    error: "再生エラー",
  }[status];
}

function formatTime(seconds: number): string {
  const safeSeconds = Number.isFinite(seconds) && seconds >= 0 ? seconds : 0;
  const minutes = Math.floor(safeSeconds / 60);
  return `${minutes}:${(safeSeconds % 60).toFixed(1).padStart(4, "0")}`;
}
