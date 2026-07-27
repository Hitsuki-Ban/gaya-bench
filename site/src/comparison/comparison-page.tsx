import { AudioWaveform, Columns3, ListVideo, Pause, Play, Rows3, Square } from "lucide-react";
import { useMemo } from "react";

import { useAudioProgress } from "@/audio/audio-provider";
import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { benchmarkData, clipKey } from "@/data";

import { DesktopMatrix } from "./desktop-matrix";
import { buildComparisonModel } from "./model";
import { MobileMatrix } from "./mobile-matrix";
import { useComparisonController, type SequenceDirection } from "./use-comparison-controller";
import { useMediaQuery } from "./use-media-query";

const comparisonModel = buildComparisonModel(benchmarkData);

export function ComparisonPage() {
  const controller = useComparisonController(comparisonModel);
  const isDesktop = useMediaQuery("(min-width: 768px)");
  const visibleModels = useMemo(
    () => comparisonModel.models.filter(({ id }) => controller.visibleModelIds.has(id)),
    [controller.visibleModelIds],
  );

  return (
    <div className="space-y-6">
      <PageIntro
        aside={
          <div className="grid min-w-64 grid-cols-3 gap-px overflow-hidden rounded-lg border bg-border">
            <Metric label="lines" value={comparisonModel.rows.length} />
            <Metric label="clips" value={benchmarkData.manifest.clips.length} />
            <Metric label="models" value={comparisonModel.models.length} />
          </div>
        }
        description="行はセリフ、列はモデル。方向キーで比較対象を移動し、そのまま再生できます。未生成セルも含め、全シナリオを同じ座標系で確認します。"
        eyebrow="Comparison matrix / Tactical Console"
        title="聴き比べの摩擦を、最小に。"
      />

      <MatrixToolbar
        controller={controller}
        modelCount={comparisonModel.models.length}
        visibleModelCount={visibleModels.length}
      />

      {controller.cursor === null ? (
        <div className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">
          比較できる行またはモデルがありません。
        </div>
      ) : isDesktop ? (
        <DesktopMatrix controller={controller} model={comparisonModel} />
      ) : (
        <MobileMatrix controller={controller} model={comparisonModel} />
      )}

      <KeyboardHelp isDesktop={isDesktop} />
      <Transport controller={controller} />
    </div>
  );
}

function MatrixToolbar({
  controller,
  modelCount,
  visibleModelCount,
}: {
  controller: ReturnType<typeof useComparisonController>;
  modelCount: number;
  visibleModelCount: number;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-card p-3 lg:flex-row lg:items-center lg:justify-between">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">
          <AudioWaveform aria-hidden="true" data-icon="inline-start" />
          -18 LUFS / mono / 48kHz
        </Badge>
        <details className="relative">
          <summary className="flex min-h-9 cursor-pointer list-none items-center gap-2 rounded-md border px-3 text-xs text-muted-foreground hover:text-foreground">
            <Columns3 aria-hidden="true" className="size-3.5" />
            列表示 {visibleModelCount}/{modelCount}
          </summary>
          <div className="absolute top-11 left-0 z-40 min-w-64 space-y-2 rounded-lg border bg-popover p-3 shadow-xl">
            {comparisonModel.models.map((model) => {
              const checked = controller.visibleModelIds.has(model.id);
              return (
                <label
                  className="flex min-h-10 cursor-pointer items-center gap-3 rounded px-2 text-sm hover:bg-muted"
                  key={model.id}
                >
                  <input
                    checked={checked}
                    disabled={checked && visibleModelCount === 1}
                    onChange={(event) =>
                      controller.setModelVisible(model.id, event.currentTarget.checked)
                    }
                    type="checkbox"
                  />
                  <span>{model.name}</span>
                </label>
              );
            })}
          </div>
        </details>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[10px] tracking-wider text-muted-foreground uppercase">
          Continuous
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
      className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border bg-muted/35 px-4 py-3 text-xs text-muted-foreground"
      id="matrix-keyboard-help"
    >
      <span className="font-mono text-[10px] tracking-wider text-foreground uppercase">
        Shortcuts
      </span>
      {isDesktop ? (
        <>
          <Shortcut keys="← →" label="モデル移動 + 再生" />
          <Shortcut keys="↑ ↓" label="セリフ移動 + 再生" />
        </>
      ) : (
        <Shortcut keys="← → / ↑ ↓" label="モデル / セリフ移動" />
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

function Transport({ controller }: { controller: ReturnType<typeof useComparisonController> }) {
  const progress = useAudioProgress();
  const cursor = controller.cursor;
  if (cursor === null) {
    return null;
  }

  const row = comparisonModel.rows[cursor.rowIndex]!;
  const model = comparisonModel.models.find(({ id }) => id === cursor.modelId);
  if (!model) {
    throw new Error(`transport の model が存在しません: ${cursor.modelId}`);
  }
  const clip = comparisonModel.getClip(cursor);
  const duration = progress.duration > 0 ? progress.duration : (clip?.duration_sec ?? 0);
  const ratio = duration > 0 ? Math.min(progress.currentTime / duration, 1) : 0;
  const isCurrentClip = clip !== undefined && controller.player.currentClipKey === clipKey(clip);
  const isPlaying =
    isCurrentClip &&
    (controller.player.status === "playing" || controller.player.status === "loading");

  return (
    <div className="sticky bottom-4 z-30 rounded-lg border border-primary/30 bg-background/95 p-3 shadow-2xl shadow-black/50 backdrop-blur">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm">
            <span className="font-semibold text-primary">{row.character.name}</span>
            <span className="mx-2 text-border">/</span>
            {row.line.text}
          </p>
          <p className="mt-1 font-mono text-[10px] text-muted-foreground">
            {row.scenario.title} · {model.name} · {cursor.rowIndex + 1}/
            {comparisonModel.rows.length}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            aria-label={isPlaying ? "現在のクリップを一時停止" : "現在のクリップを再生"}
            disabled={clip === undefined}
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
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-150"
              style={{ width: `${ratio * 100}%` }}
            />
          </div>
          <div className="mt-1.5 flex justify-between font-mono text-[10px] text-muted-foreground">
            <span>{formatTime(progress.currentTime)}</span>
            <span>
              {controller.sequence
                ? `${controller.sequence.itemIndex + 1}/${controller.sequence.queue.items.length} · skip ${controller.sequence.queue.skippedCount}`
                : controller.player.status}
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

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-card px-4 py-3 text-center">
      <p className="font-mono text-lg text-foreground">{value}</p>
      <p className="font-mono text-[10px] tracking-wider text-muted-foreground uppercase">
        {label}
      </p>
    </div>
  );
}

function formatTime(seconds: number): string {
  const safeSeconds = Number.isFinite(seconds) && seconds >= 0 ? seconds : 0;
  const minutes = Math.floor(safeSeconds / 60);
  return `${minutes}:${(safeSeconds % 60).toFixed(1).padStart(4, "0")}`;
}
