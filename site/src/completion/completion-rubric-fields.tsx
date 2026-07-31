import { Button } from "@/components/ui/button";

import type { CompletionRubric } from "./types";

export function CompletionRubricFields({
  onChange,
  value,
}: {
  onChange: (value: CompletionRubric) => void;
  value: CompletionRubric;
}) {
  const setField = (field: keyof CompletionRubric, fieldValue: boolean | number | string | null) =>
    onChange({ ...value, [field]: fieldValue });

  return (
    <div className="space-y-5">
      <BooleanRubric
        falseLabel="不正"
        label="台詞内容"
        onChange={(next) => setField("content_correct", next)}
        trueLabel="正しい"
        value={value.content_correct}
      />
      <BooleanRubric
        falseLabel="なし"
        label="提示語・メタ文の漏洩"
        onChange={(next) => setField("prompt_leakage", next)}
        trueLabel="あり"
        value={value.prompt_leakage}
      />
      <BooleanRubric
        falseLabel="誤読あり"
        label="語・漢字の読み"
        onChange={(next) => setField("reading_correct", next)}
        trueLabel="正しい"
        value={value.reading_correct}
      />
      <ScaleRubric
        label="日本語の音調・アクセント"
        onChange={(next) => setField("accent_naturalness", next)}
        value={value.accent_naturalness}
      />
      <ScaleRubric
        label="役柄・声線の一致"
        onChange={(next) => setField("role_match", next)}
        value={value.role_match}
      />
      <ScaleRubric
        label="感情・強度・演技の一致"
        onChange={(next) => setField("delivery_match", next)}
        value={value.delivery_match}
      />
      <ScaleRubric
        label="自然さ・音質"
        onChange={(next) => setField("audio_quality", next)}
        value={value.audio_quality}
      />
      <BooleanRubric
        falseLabel="不可"
        label="単独で採用可能"
        onChange={(next) => setField("adoptable", next)}
        trueLabel="可能"
        value={value.adoptable}
      />
      <label className="block">
        <span className="mb-2 block text-sm font-medium">問題点メモ（任意）</span>
        <textarea
          className="min-h-20 w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
          onChange={(event) => setField("notes", event.currentTarget.value)}
          placeholder="誤読、音調、声線、演技、ノイズなど"
          value={value.notes}
        />
      </label>
    </div>
  );
}

function BooleanRubric({
  falseLabel,
  label,
  onChange,
  trueLabel,
  value,
}: {
  falseLabel: string;
  label: string;
  onChange: (value: boolean | null) => void;
  trueLabel: string;
  value: boolean | null;
}) {
  return (
    <fieldset>
      <legend className="mb-2 text-sm font-medium">{label}</legend>
      <div className="flex flex-wrap gap-2">
        <ChoiceButton active={value === true} label={trueLabel} onClick={() => onChange(true)} />
        <ChoiceButton active={value === false} label={falseLabel} onClick={() => onChange(false)} />
        <ChoiceButton active={value === null} label="未入力" onClick={() => onChange(null)} />
      </div>
    </fieldset>
  );
}

function ScaleRubric({
  label,
  onChange,
  value,
}: {
  label: string;
  onChange: (value: number | null) => void;
  value: number | null;
}) {
  return (
    <fieldset>
      <legend className="mb-2 text-sm font-medium">
        {label} <span className="font-normal text-muted-foreground">（1=悪い / 5=良い）</span>
      </legend>
      <div className="flex flex-wrap gap-2">
        {[1, 2, 3, 4, 5].map((rating) => (
          <ChoiceButton
            active={value === rating}
            key={rating}
            label={String(rating)}
            onClick={() => onChange(rating)}
          />
        ))}
        <ChoiceButton active={value === null} label="未入力" onClick={() => onChange(null)} />
      </div>
    </fieldset>
  );
}

function ChoiceButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <Button
      aria-pressed={active}
      onClick={onClick}
      size="sm"
      type="button"
      variant={active ? "default" : "outline"}
    >
      {label}
    </Button>
  );
}
