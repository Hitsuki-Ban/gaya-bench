import { ArrowUpRight, ChevronDown, Clapperboard } from "lucide-react";
import { Link } from "react-router";

import { benchmarkData, scenarioById, type Scenario } from "@/data";
import { updateScenarioFilter, type FilterState } from "@/filters";

interface ScenarioSelectorProps {
  state: FilterState;
  search: string;
  onChange: (state: FilterState) => void;
}

export function ScenarioSelector({ state, search, onChange }: ScenarioSelectorProps) {
  const selectedScenario = resolveSelectedScenario(state.scenario);

  return (
    <section
      aria-labelledby="scenario-selector-title"
      className="relative overflow-hidden rounded-lg border border-primary/45 bg-card px-4 py-4 shadow-xl shadow-black/20 before:absolute before:inset-x-0 before:top-0 before:h-px before:bg-gradient-to-r before:from-primary before:via-primary/45 before:to-transparent md:px-5"
    >
      <div className="grid min-w-0 gap-4 md:grid-cols-[minmax(0,1fr)_minmax(18rem,30rem)] md:items-end md:gap-8">
        <div className="min-w-0">
          <p className="flex items-center gap-2 font-mono text-[10px] tracking-[0.16em] text-primary uppercase">
            <Clapperboard aria-hidden="true" className="size-3.5" />
            比較する場面
          </p>
          <h2 className="mt-1 text-lg font-semibold tracking-tight" id="scenario-selector-title">
            シナリオを選ぶ
          </h2>
          <p
            className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground"
            id="scenario-selection-summary"
          >
            {selectedScenario === null
              ? `全${benchmarkData.scenarios.length}シナリオを通して、同じ台詞をモデルごとに比較します。`
              : selectedScenario.scene.setting}
          </p>
          {selectedScenario === null ? null : (
            <Link
              className="mt-2 inline-flex min-h-8 items-center gap-1.5 text-xs font-medium text-primary underline-offset-4 hover:underline focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              to={{ pathname: `/scenario/${selectedScenario.id}`, search }}
            >
              {selectedScenario.title}の場面詳細
              <ArrowUpRight aria-hidden="true" className="size-3.5" />
            </Link>
          )}
        </div>

        <label className="block min-w-0" htmlFor="scenario-selector">
          <span className="mb-1.5 block text-xs font-semibold text-foreground">現在の場面</span>
          <span className="relative block min-w-0">
            <select
              aria-describedby="scenario-selection-summary"
              className="min-h-12 w-full min-w-0 max-w-full appearance-none rounded-md border border-primary/55 bg-background px-3 pr-10 text-sm font-semibold text-foreground shadow-sm outline-none transition-colors hover:border-primary focus-visible:ring-3 focus-visible:ring-ring/45 motion-reduce:transition-none"
              id="scenario-selector"
              onChange={(event) =>
                onChange(
                  updateScenarioFilter(
                    state,
                    event.currentTarget.value.length === 0 ? null : event.currentTarget.value,
                    benchmarkData,
                  ),
                )
              }
              value={state.scenario ?? ""}
            >
              <option value="">すべてのシナリオ</option>
              {benchmarkData.scenarios.map((scenario) => (
                <option key={scenario.id} value={scenario.id}>
                  {scenario.title}
                </option>
              ))}
            </select>
            <ChevronDown
              aria-hidden="true"
              className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-primary"
            />
          </span>
        </label>
      </div>
    </section>
  );
}

function resolveSelectedScenario(scenarioId: string | null): Scenario | null {
  if (scenarioId === null) {
    return null;
  }
  const scenario = scenarioById.get(scenarioId);
  if (scenario === undefined) {
    throw new Error(`選択中の scenario が存在しません: ${scenarioId}`);
  }
  return scenario;
}
