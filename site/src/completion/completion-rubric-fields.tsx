import { AlertCircle, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";

import { roleReviewProblemCount, rubricHasProblems } from "./storage";
import type { RoleReviewRubric } from "./types";

const PROBLEMS = [
  { field: "content", label: "内容有缺漏" },
  { field: "prompt_leakage", label: "提示词被读出" },
  { field: "reading", label: "读错或漏读" },
  { field: "pitch_accent", label: "日语音调不准" },
  { field: "gender", label: "性别不符" },
  { field: "age", label: "年龄不符" },
  { field: "archetype", label: "角色感觉不符" },
] as const;

export function CompletionRubricFields({
  disabled = false,
  onChange,
  subjectLabel = null,
  value,
}: {
  disabled?: boolean;
  onChange: (value: RoleReviewRubric) => void;
  subjectLabel?: string | null;
  value: RoleReviewRubric;
}) {
  const [open, setOpen] = useState(() => rubricHasProblems(value));
  const [notes, setNotes] = useState(value.notes);
  const notesTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestValueRef = useRef(value);
  const latestOnChangeRef = useRef(onChange);
  latestValueRef.current = value;
  latestOnChangeRef.current = onChange;
  const selectedCount = roleReviewProblemCount(value);
  const hasQualityProblem = value.naturalness_quality !== null && value.naturalness_quality <= 3;

  useEffect(() => {
    setNotes(value.notes);
  }, [value.notes]);

  useEffect(
    () => () => {
      if (notesTimerRef.current !== null) {
        clearTimeout(notesTimerRef.current);
      }
    },
    [],
  );

  const commit = (next: RoleReviewRubric) => {
    if (notesTimerRef.current !== null) {
      clearTimeout(notesTimerRef.current);
      notesTimerRef.current = null;
    }
    setNotes(next.notes);
    onChange(next);
  };

  const scheduleNotes = (nextNotes: string) => {
    setNotes(nextNotes);
    if (notesTimerRef.current !== null) {
      clearTimeout(notesTimerRef.current);
    }
    notesTimerRef.current = setTimeout(() => {
      notesTimerRef.current = null;
      latestOnChangeRef.current({ ...latestValueRef.current, notes: nextNotes });
    }, 500);
  };

  return (
    <section
      className="rounded-md border border-border bg-background/55 p-3"
      data-role-review-rubric
    >
      <Button
        aria-expanded={open}
        className="w-full justify-between"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        type="button"
        variant={selectedCount > 0 ? "destructive" : "outline"}
      >
        <span className="flex items-center gap-2">
          <AlertCircle aria-hidden="true" />
          {subjectLabel === null
            ? "先选择一条，再标问题"
            : selectedCount > 0
              ? `${subjectLabel}：已标记 ${selectedCount} 个问题`
              : `标记${subjectLabel}的问题`}
        </span>
        <ChevronDown
          aria-hidden="true"
          className={open ? "rotate-180 transition-transform" : "transition-transform"}
        />
      </Button>

      {open ? (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap gap-2" aria-label="候选问题">
            {PROBLEMS.map(({ field, label }) => {
              const selected = value[field] === "fail";
              return (
                <Button
                  aria-pressed={selected}
                  disabled={disabled}
                  key={field}
                  onClick={() =>
                    commit({
                      ...value,
                      notes,
                      [field]: selected ? null : "fail",
                    })
                  }
                  size="sm"
                  type="button"
                  variant={selected ? "destructive" : "outline"}
                >
                  {label}
                </Button>
              );
            })}
            <Button
              aria-pressed={hasQualityProblem}
              disabled={disabled}
              onClick={() =>
                commit({
                  ...value,
                  notes,
                  naturalness_quality: hasQualityProblem ? null : 2,
                })
              }
              size="sm"
              type="button"
              variant={hasQualityProblem ? "destructive" : "outline"}
            >
              棒读或音质差
            </Button>
          </div>

          {hasQualityProblem ? (
            <fieldset className="flex items-center gap-2">
              <legend className="sr-only">问题严重程度</legend>
              <span className="text-xs text-muted-foreground">严重程度</span>
              {[1, 2, 3].map((score) => (
                <Button
                  aria-label={`${score}，${score === 1 ? "严重" : score === 2 ? "明显" : "轻微"}`}
                  aria-pressed={value.naturalness_quality === score}
                  disabled={disabled}
                  key={score}
                  onClick={() => commit({ ...value, notes, naturalness_quality: score })}
                  size="xs"
                  type="button"
                  variant={value.naturalness_quality === score ? "default" : "outline"}
                >
                  {score === 1 ? "严重" : score === 2 ? "明显" : "轻微"}
                </Button>
              ))}
            </fieldset>
          ) : null}

          <label className="block">
            <span className="text-xs text-muted-foreground">补充说明（可选）</span>
            <input
              className="mt-1.5 h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
              disabled={disabled}
              onBlur={() => {
                if (notes !== value.notes) {
                  commit({ ...value, notes });
                }
              }}
              onChange={(event) => scheduleNotes(event.currentTarget.value)}
              placeholder="只写标签无法表达的内容"
              value={notes}
            />
          </label>
        </div>
      ) : null}
    </section>
  );
}
