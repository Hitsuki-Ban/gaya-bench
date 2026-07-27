import { useMemo, useRef, type KeyboardEvent } from "react";

import { Badge } from "@/components/ui/badge";

import { MatrixCell } from "./matrix-cell";
import type { ComparisonModel, Coordinate } from "./model";
import type { ComparisonController } from "./use-comparison-controller";
import { useVisibleAudioPrefetch } from "./visible-audio-prefetch";

interface MobileMatrixProps {
  controller: ComparisonController;
  model: ComparisonModel;
}

export function MobileMatrix({ controller, model }: MobileMatrixProps) {
  const scopeRef = useRef<HTMLDivElement>(null);
  const visibleModels = useMemo(
    () => model.models.filter(({ id }) => controller.visibleModelIds.has(id)),
    [controller.visibleModelIds, model.models],
  );
  const cursor = controller.cursor;
  const selectedModel = visibleModels.find(({ id }) => id === cursor?.modelId);
  const playingCoordinate =
    controller.player.currentClipKey === null
      ? undefined
      : model.getCoordinateForClipKey(controller.player.currentClipKey);
  useVisibleAudioPrefetch(scopeRef, null, selectedModel?.id ?? "");

  if (cursor === null) {
    return null;
  }
  if (!selectedModel) {
    throw new Error(`mobile matrix の model が表示対象にありません: ${cursor.modelId}`);
  }

  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number): void {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    event.preventDefault();
    const nextIndex = event.key === "ArrowLeft" ? index - 1 : index + 1;
    const next = visibleModels[nextIndex];
    if (!next) {
      return;
    }
    controller.selectModel(next.id);
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLElement>(`[data-model-tab="${CSS.escape(next.id)}"]`)?.focus();
    });
  }

  return (
    <div className="space-y-4" ref={scopeRef}>
      <div
        aria-label="比較モデル"
        className="grid grid-cols-2 gap-2 rounded-lg border bg-card p-2"
        role="tablist"
      >
        {visibleModels.map((item, index) => (
          <button
            aria-controls="mobile-comparison-list"
            aria-selected={selectedModel.id === item.id}
            className={[
              "min-h-11 rounded border px-3 py-2 text-left font-mono text-xs",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
              selectedModel.id === item.id
                ? "border-primary bg-primary/12 text-primary"
                : "border-border bg-background text-muted-foreground",
            ].join(" ")}
            data-model-tab={item.id}
            key={item.id}
            onClick={() => controller.selectModel(item.id)}
            onKeyDown={(event) => handleTabKeyDown(event, index)}
            role="tab"
            tabIndex={selectedModel.id === item.id ? 0 : -1}
            type="button"
          >
            {item.name}
          </button>
        ))}
      </div>

      <div className="space-y-3" id="mobile-comparison-list" role="tabpanel">
        {model.rows.map((row, rowIndex) => {
          const coordinate: Coordinate = { rowIndex, modelId: selectedModel.id };
          const previous = model.rows[rowIndex - 1];
          const isScenarioStart =
            previous === undefined || previous.scenario.id !== row.scenario.id;
          const isCharacterStart = isScenarioStart || previous.character.id !== row.character.id;
          const isCurrent =
            playingCoordinate?.rowIndex === rowIndex &&
            playingCoordinate.modelId === selectedModel.id;
          return (
            <article
              className={[
                "rounded-lg border bg-card p-4",
                cursor.rowIndex === rowIndex ? "border-accent/70" : "",
                isCurrent ? "shadow-[inset_3px_0_0_var(--primary)]" : "",
              ].join(" ")}
              key={`${row.scenario.id}/${row.line.id}`}
            >
              {isScenarioStart ? (
                <p className="mb-2 font-mono text-[10px] tracking-[0.14em] text-primary uppercase">
                  {row.scenario.title}
                </p>
              ) : null}
              {isCharacterStart ? (
                <p className="text-xs font-semibold text-accent">{row.character.name}</p>
              ) : null}
              <p className="mt-1 text-sm leading-6">{row.line.text}</p>
              <div className="my-3 flex flex-wrap gap-1.5">
                <Badge className="font-mono text-[9px]" variant="outline">
                  {row.line.emotion}
                </Badge>
                <Badge className="font-mono text-[9px]" variant="secondary">
                  {row.line.difficulty}
                </Badge>
              </div>
              <MatrixCell
                accessibleLabel={`${row.character.name}「${row.line.text}」`}
                clip={model.getClip(coordinate)}
                coordinate={coordinate}
                isCurrent={isCurrent}
                isCursor={cursor.rowIndex === rowIndex}
                model={selectedModel}
                navigate={controller.navigate}
                selectAndToggle={controller.selectAndToggle}
                startOrStopSequence={controller.startOrStopSequence}
                status={isCurrent ? controller.player.status : "idle"}
                stop={controller.stop}
                toggleFocused={controller.toggleFocused}
              />
            </article>
          );
        })}
      </div>
    </div>
  );
}
