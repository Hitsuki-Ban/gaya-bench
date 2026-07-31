import { memo, useRef } from "react";
import { Link } from "react-router";

import { CharacterKindBadge } from "@/components/character-kind-badge";
import { Badge } from "@/components/ui/badge";
import type { ComparisonProjection } from "@/filters";
import { DIFFICULTY_LABELS, EMOTION_LABELS } from "@/ui-labels";

import { MatrixCell } from "./matrix-cell";
import { ScenarioContextLink } from "./scenario-context-link";
import type { ComparisonController } from "./use-comparison-controller";
import type { ComparisonModel, ComparisonRow, Coordinate } from "./model";

interface DesktopMatrixProps {
  controller: ComparisonController;
  model: ComparisonModel;
  projection: ComparisonProjection;
  search: string;
}

export function DesktopMatrix({ controller, model, projection, search }: DesktopMatrixProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const visibleModels = projection.models;
  const playingCoordinate =
    controller.player.currentClipKey === null
      ? undefined
      : model.getCoordinateForCandidateKey(controller.player.currentClipKey);
  return (
    <div
      className="max-h-[70vh] overflow-auto rounded-md border bg-background shadow-2xl shadow-black/20"
      ref={scrollRef}
    >
      <table
        aria-colcount={visibleModels.length + 1}
        aria-describedby="matrix-keyboard-help"
        aria-label="TTS モデル比較マトリクス"
        aria-rowcount={projection.rows.length + 1}
        className="w-full min-w-max table-fixed border-separate border-spacing-0"
        role="grid"
        style={{ minWidth: `${300 + visibleModels.length * 132}px` }}
      >
        <thead className="sticky top-0 z-20 bg-background/98 backdrop-blur">
          <tr role="row">
            <th
              className="sticky left-0 z-30 w-[300px] border-r border-b bg-background px-3 py-2.5 text-left"
              role="columnheader"
            >
              <span className="font-mono text-[10px] tracking-[0.16em] text-muted-foreground uppercase">
                キャラクター / セリフ
              </span>
            </th>
            {visibleModels.map((item) => (
              <th
                className={[
                  "w-[132px] border-r border-b bg-background px-2 py-2 text-left last:border-r-0",
                  playingCoordinate?.modelId === item.id ? "bg-primary/12" : "",
                ].join(" ")}
                data-model-column-current={playingCoordinate?.modelId === item.id}
                key={item.id}
                role="columnheader"
              >
                <Link
                  className="block truncate font-mono text-xs font-semibold hover:text-primary"
                  to={{ pathname: `/models/${item.id}`, search }}
                >
                  {item.name}
                </Link>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  <CapabilityBadge active={item.capabilities.emotion} label="E" title="感情" />
                  <CapabilityBadge
                    active={item.capabilities.voice_prompt}
                    label="P"
                    title="声質プロンプト"
                  />
                  <CapabilityBadge active={item.capabilities.clone} label="C" title="クローン" />
                  <CapabilityBadge
                    active={item.capabilities.nonverbal}
                    label="N"
                    title="非言語音"
                  />
                  <CapabilityBadge active={item.capabilities.reading} label="R" title="読み指定" />
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {projection.rows.map(({ row, rowIndex }, displayRowIndex) => {
            const previous = projection.rows[displayRowIndex - 1]?.row;
            return (
              <MatrixRow
                cursorModelId={
                  controller.cursor?.rowIndex === rowIndex ? controller.cursor.modelId : null
                }
                isCharacterStart={
                  previous === undefined ||
                  previous.scenario.id !== row.scenario.id ||
                  previous.character.id !== row.character.id
                }
                isScenarioStart={previous === undefined || previous.scenario.id !== row.scenario.id}
                key={`${row.scenario.id}/${row.line.id}`}
                model={model}
                navigate={controller.navigate}
                playingModelId={
                  playingCoordinate?.rowIndex === rowIndex ? playingCoordinate.modelId : null
                }
                playingStatus={
                  playingCoordinate?.rowIndex === rowIndex ? controller.player.status : "idle"
                }
                row={row}
                displayRowIndex={displayRowIndex}
                rowIndex={rowIndex}
                selectAndToggle={controller.selectAndToggle}
                startOrStopSequence={controller.startOrStopSequence}
                stop={controller.stop}
                toggleFocused={controller.toggleFocused}
                visibleModelIds={projection.modelIds}
                search={search}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

interface MatrixRowProps {
  cursorModelId: string | null;
  displayRowIndex: number;
  isCharacterStart: boolean;
  isScenarioStart: boolean;
  model: ComparisonModel;
  navigate: ComparisonController["navigate"];
  playingModelId: string | null;
  playingStatus: ComparisonController["player"]["status"];
  row: ComparisonRow;
  rowIndex: number;
  selectAndToggle: ComparisonController["selectAndToggle"];
  startOrStopSequence: ComparisonController["startOrStopSequence"];
  stop: ComparisonController["stop"];
  toggleFocused: ComparisonController["toggleFocused"];
  visibleModelIds: ReadonlySet<string>;
  search: string;
}

const MatrixRow = memo(function MatrixRow({
  cursorModelId,
  displayRowIndex,
  isCharacterStart,
  isScenarioStart,
  model,
  navigate,
  playingModelId,
  playingStatus,
  row,
  rowIndex,
  selectAndToggle,
  startOrStopSequence,
  stop,
  toggleFocused,
  visibleModelIds,
  search,
}: MatrixRowProps) {
  const visibleModels = model.models.filter(({ id }) => visibleModelIds.has(id));
  const isActiveRow = cursorModelId !== null || playingModelId !== null;

  return (
    <tr
      aria-rowindex={displayRowIndex + 2}
      className={["group/row", isActiveRow ? "bg-primary/[0.065]" : ""].join(" ")}
      data-active={isActiveRow}
      data-matrix-row=""
      role="row"
    >
      <th
        className={[
          "sticky left-0 z-10 w-[300px] border-r border-b bg-background px-3 py-2.5 text-left align-top",
          isScenarioStart ? "border-t-2" : "",
          isActiveRow
            ? "bg-[#18140d] shadow-[inset_0_1px_0_rgba(245,166,35,0.55),inset_0_-1px_0_rgba(245,166,35,0.4)]"
            : "",
        ].join(" ")}
        role="rowheader"
        style={isScenarioStart ? { borderTopColor: "var(--primary)" } : undefined}
      >
        <ScenarioContextLink density="compact" scenario={row.scenario} search={search} />
        {isCharacterStart ? (
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <p className="text-xs font-semibold text-primary">{row.character.name}</p>
            <CharacterKindBadge kind={row.character.kind} />
          </div>
        ) : null}
        <p className="text-sm leading-6 text-foreground">{row.line.text}</p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <Badge className="font-mono text-[9px]" variant="outline">
            {EMOTION_LABELS[row.line.emotion]}
          </Badge>
          {row.line.difficulty === "hard" ? (
            <Badge className="font-mono text-[9px]" variant="destructive">
              {DIFFICULTY_LABELS[row.line.difficulty]}
            </Badge>
          ) : null}
          <span className="text-[9px] text-muted-foreground">強さ {row.line.intensity}</span>
        </div>
      </th>
      {visibleModels.map((item) => {
        const coordinate: Coordinate = { rowIndex, modelId: item.id };
        return (
          <td
            className={[
              "w-[132px] border-r border-b p-1 align-middle last:border-r-0",
              isScenarioStart ? "border-t-2" : "",
              playingModelId === item.id
                ? "bg-primary/[0.085] shadow-[inset_0_1px_0_rgba(245,166,35,0.55),inset_0_-1px_0_rgba(245,166,35,0.4)]"
                : isActiveRow
                  ? "shadow-[inset_0_1px_0_rgba(245,166,35,0.4),inset_0_-1px_0_rgba(245,166,35,0.28)]"
                  : "",
            ].join(" ")}
            data-model-column-current={playingModelId === item.id}
            key={item.id}
            role="gridcell"
            style={isScenarioStart ? { borderTopColor: "var(--primary)" } : undefined}
          >
            <MatrixCell
              accessibleLabel={`${row.scenario.title} / ${row.character.name}「${row.line.text}」`}
              cell={model.getCell(coordinate)}
              coordinate={coordinate}
              isCurrent={playingModelId === item.id}
              isCursor={cursorModelId === item.id}
              model={item}
              navigate={navigate}
              selectAndToggle={selectAndToggle}
              startOrStopSequence={startOrStopSequence}
              status={playingModelId === item.id ? playingStatus : "idle"}
              stop={stop}
              toggleFocused={toggleFocused}
            />
          </td>
        );
      })}
    </tr>
  );
});

function CapabilityBadge({
  active,
  label,
  title,
}: {
  active: boolean;
  label: string;
  title: string;
}) {
  return (
    <span
      aria-label={`${title}: ${active ? "対応" : "非対応"}`}
      className={[
        "grid size-5 place-items-center rounded border font-mono text-[9px]",
        active
          ? "border-primary/55 bg-primary/10 text-primary"
          : "border-border text-muted-foreground/55",
      ].join(" ")}
      data-capability-supported={active}
      title={`${title}: ${active ? "対応" : "非対応"}`}
    >
      {label}
    </span>
  );
}
