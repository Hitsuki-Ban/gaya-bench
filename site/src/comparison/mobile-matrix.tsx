import type { KeyboardEvent } from "react";
import { Link } from "react-router";

import { CharacterKindBadge } from "@/components/character-kind-badge";
import { Badge } from "@/components/ui/badge";
import type { ComparisonProjection } from "@/filters";
import { DIFFICULTY_LABELS, EMOTION_LABELS } from "@/ui-labels";

import { MatrixCell } from "./matrix-cell";
import { ScenarioContextLink } from "./scenario-context-link";
import { resolveCursor, type ComparisonModel, type Coordinate } from "./model";
import type { ComparisonController } from "./use-comparison-controller";

interface MobileMatrixProps {
  controller: ComparisonController;
  model: ComparisonModel;
  projection: ComparisonProjection;
  search: string;
}

export function MobileMatrix({ controller, model, projection, search }: MobileMatrixProps) {
  const visibleModels = projection.models;
  const cursor = resolveCursor(model, controller.cursor, projection);
  const selectedModel = visibleModels.find(({ id }) => id === cursor?.modelId);
  const playingCoordinate =
    controller.player.currentClipKey === null
      ? undefined
      : model.getCoordinateForCandidateKey(controller.player.currentClipKey);
  if (cursor === null) {
    return null;
  }
  if (!selectedModel) {
    throw new Error(`mobile matrix の投影 cursor を解決できません: ${cursor.modelId}`);
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
    <div className="space-y-4">
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
            id={`mobile-model-tab-${item.id}`}
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

      <Link
        className="inline-flex text-xs text-primary underline-offset-4 hover:underline"
        to={{ pathname: `/models/${selectedModel.id}`, search }}
      >
        {selectedModel.name} の詳細を見る
      </Link>

      <div
        aria-labelledby={`mobile-model-tab-${selectedModel.id}`}
        className="space-y-3"
        id="mobile-comparison-list"
        role="tabpanel"
      >
        {projection.rows.map(({ row, rowIndex }, displayRowIndex) => {
          const coordinate: Coordinate = { rowIndex, modelId: selectedModel.id };
          const previous = projection.rows[displayRowIndex - 1]?.row;
          const isScenarioStart =
            previous === undefined || previous.scenario.id !== row.scenario.id;
          const isCharacterStart = isScenarioStart || previous.character.id !== row.character.id;
          const isCurrent =
            playingCoordinate?.rowIndex === rowIndex &&
            playingCoordinate.modelId === selectedModel.id;
          return (
            <article
              className={[
                "relative rounded-lg border bg-card p-4",
                isScenarioStart
                  ? "before:pointer-events-none before:absolute before:inset-x-0 before:top-0 before:h-0.5 before:bg-primary before:content-['']"
                  : "",
                cursor.rowIndex === rowIndex ? "border-primary/70" : "",
                isCurrent ? "shadow-[inset_3px_0_0_var(--primary)]" : "",
              ].join(" ")}
              data-current={isCurrent}
              data-cursor={cursor.rowIndex === rowIndex}
              data-mobile-card=""
              key={`${row.scenario.id}/${row.line.id}`}
            >
              <ScenarioContextLink density="card" scenario={row.scenario} search={search} />
              {isCharacterStart ? (
                <div className="flex flex-wrap items-center gap-1.5">
                  <p className="text-xs font-semibold text-primary">{row.character.name}</p>
                  <CharacterKindBadge kind={row.character.kind} />
                </div>
              ) : null}
              <p className="mt-1 text-sm leading-6">{row.line.text}</p>
              <div className="my-3 flex flex-wrap gap-1.5">
                <Badge className="font-mono text-[9px]" variant="outline">
                  {EMOTION_LABELS[row.line.emotion]}
                </Badge>
                {row.line.difficulty === "hard" ? (
                  <Badge className="font-mono text-[9px]" variant="destructive">
                    {DIFFICULTY_LABELS[row.line.difficulty]}
                  </Badge>
                ) : null}
              </div>
              <MatrixCell
                accessibleLabel={`${row.scenario.title} / ${row.character.name}「${row.line.text}」`}
                cell={model.getCell(coordinate)}
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
