import { ChevronDown, RotateCcw, SlidersHorizontal } from "lucide-react";

import { CHARACTER_KIND_LABELS } from "@/components/character-kind-badge";
import { Button } from "@/components/ui/button";
import {
  benchmarkData,
  type Age,
  type CharacterKind,
  type Difficulty,
  type Emotion,
  type Gender,
} from "@/data";
import {
  CHARACTER_KIND_ORDER,
  encodeFilterState,
  toggleFilterValue,
  updateScenarioFilter,
  type FilterState,
} from "@/filters";

const characterKindOptions = CHARACTER_KIND_ORDER.map((value) => ({
  value,
  label: CHARACTER_KIND_LABELS[value],
}));

const genderOptions = [
  { value: "female", label: "女性" },
  { value: "male", label: "男性" },
  { value: "neutral", label: "中性" },
] as const satisfies readonly { value: Gender; label: string }[];

const ageOptions = [
  { value: "child", label: "子ども" },
  { value: "teen", label: "10代" },
  { value: "young_adult", label: "若年" },
  { value: "adult", label: "成人" },
  { value: "middle_aged", label: "中年" },
  { value: "elderly", label: "高齢" },
] as const satisfies readonly { value: Age; label: string }[];

const emotionOptions = [
  { value: "neutral", label: "neutral" },
  { value: "cheerful", label: "cheerful" },
  { value: "angry", label: "angry" },
  { value: "sad", label: "sad" },
  { value: "fearful", label: "fearful" },
  { value: "surprised", label: "surprised" },
  { value: "tired", label: "tired" },
  { value: "drunk", label: "drunk" },
  { value: "whisper", label: "whisper" },
  { value: "shout", label: "shout" },
  { value: "laughing", label: "laughing" },
  { value: "pain", label: "pain" },
] as const satisfies readonly { value: Emotion; label: string }[];

const difficultyOptions = [
  { value: "standard", label: "standard" },
  { value: "hard", label: "hard" },
] as const satisfies readonly { value: Difficulty; label: string }[];

interface FilterToolbarProps {
  state: FilterState;
  filteredRows: number;
  totalRows: number;
  onChange: (state: FilterState) => void;
  onReset: () => void;
}

export function FilterToolbar({
  state,
  filteredRows,
  totalRows,
  onChange,
  onReset,
}: FilterToolbarProps) {
  const isDefault = encodeFilterState(state, benchmarkData) === "";

  return (
    <details className="group rounded-md border bg-card">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5 outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
        <span>
          <span className="flex items-center gap-2 text-sm font-semibold">
            <SlidersHorizontal aria-hidden="true" className="size-4 text-primary" />
            比較フィルタ
          </span>
          <span className="mt-0.5 block text-xs text-muted-foreground" aria-live="polite">
            {filteredRows}/{totalRows} セリフを表示。選択状態は URL で共有できます。
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-1 whitespace-nowrap text-xs text-muted-foreground">
          詳細
          <ChevronDown
            aria-hidden="true"
            className="size-4 transition-transform group-open:rotate-180 motion-reduce:transition-none"
          />
        </span>
      </summary>

      <div className="border-t px-3 pt-2 pb-3">
        <div className="mb-2 flex justify-end">
          <Button disabled={isDefault} onClick={onReset} size="sm" variant="ghost">
            <RotateCcw aria-hidden="true" data-icon="inline-start" />
            すべて解除
          </Button>
        </div>
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          <label className="space-y-1 text-xs font-medium">
            <span>シナリオ</span>
            <select
              className="min-h-8 w-full rounded border bg-background px-2 text-sm"
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
          </label>

          <FilterCheckboxGroup
            legend="キャラ区分"
            onToggle={(value) =>
              onChange(toggleFilterValue(state, "kind", value as CharacterKind, benchmarkData))
            }
            options={characterKindOptions}
            selected={state.kind}
          />
          <FilterCheckboxGroup
            legend="性別"
            onToggle={(value) =>
              onChange(toggleFilterValue(state, "gender", value as Gender, benchmarkData))
            }
            options={genderOptions}
            selected={state.gender}
          />
          <FilterCheckboxGroup
            legend="年齢"
            onToggle={(value) =>
              onChange(toggleFilterValue(state, "age", value as Age, benchmarkData))
            }
            options={ageOptions}
            selected={state.age}
          />
          <FilterCheckboxGroup
            legend="感情"
            onToggle={(value) =>
              onChange(toggleFilterValue(state, "emotion", value as Emotion, benchmarkData))
            }
            options={emotionOptions}
            selected={state.emotion}
          />
          <FilterCheckboxGroup
            legend="難易度"
            onToggle={(value) =>
              onChange(toggleFilterValue(state, "difficulty", value as Difficulty, benchmarkData))
            }
            options={difficultyOptions}
            selected={state.difficulty}
          />
          <FilterCheckboxGroup
            legend="モデル列"
            onToggle={(value) => onChange(toggleFilterValue(state, "model", value, benchmarkData))}
            options={benchmarkData.manifest.models.map(({ id, name }) => ({
              value: id,
              label: name,
            }))}
            selected={state.model}
          />
        </div>
      </div>
    </details>
  );
}

function FilterCheckboxGroup({
  legend,
  onToggle,
  options,
  selected,
}: {
  legend: string;
  onToggle: (value: string) => void;
  options: readonly { readonly value: string; readonly label: string }[];
  selected: ReadonlySet<string>;
}) {
  return (
    <fieldset className="rounded border bg-background/55 px-2 py-1.5">
      <legend className="px-1 text-xs font-medium">{legend}</legend>
      <div className="flex flex-wrap gap-x-2.5 gap-y-1">
        {options.map(({ value, label }) => {
          const checked = selected.has(value);
          return (
            <label
              className="flex min-h-6 cursor-pointer items-center gap-1.5 text-xs text-muted-foreground has-checked:text-foreground"
              key={value}
            >
              <input
                checked={checked}
                disabled={checked && selected.size === 1}
                onChange={() => onToggle(value)}
                type="checkbox"
              />
              {label}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
