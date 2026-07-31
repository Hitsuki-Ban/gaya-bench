import { CheckCheck } from "lucide-react";

import { Button } from "@/components/ui/button";

import type { BaselineRubric } from "./baseline-types";

export function BaselineRubricFields({
  onChange,
  value,
}: {
  readonly onChange: (value: BaselineRubric) => void;
  readonly value: BaselineRubric;
}) {
  const setField = <Field extends keyof BaselineRubric>(
    field: Field,
    fieldValue: BaselineRubric[Field],
  ) => onChange({ ...value, [field]: fieldValue });

  return (
    <div className="space-y-4" data-baseline-rubric>
      <Button
        className="w-full"
        onClick={() =>
          onChange({
            ...value,
            content_correct: true,
            prompt_leakage: false,
            reading_correct: true,
            accent_naturalness: 4,
            role_match: 4,
            delivery_match: 4,
            audio_quality: 4,
            adoptable: true,
          })
        }
        type="button"
        variant="outline"
      >
        <CheckCheck aria-hidden="true" />
        基準を満たす状態から入力
      </Button>

      <BooleanField
        falseLabel="不一致"
        help="台詞の欠落・追加・反復も含め、原文どおりか"
        label="台詞内容"
        onChange={(next) => setField("content_correct", next)}
        trueLabel="正しい"
        value={value.content_correct}
      />
      <BooleanField
        falseLabel="漏洩なし"
        help="感情名・話し方・メタ文など、提示語が音声へ出ていないか"
        label="提示語・メタ文漏洩"
        onChange={(next) => setField("prompt_leakage", next)}
        trueLabel="漏洩あり"
        value={value.prompt_leakage}
      />
      <BooleanField
        falseLabel="誤読あり"
        help="語・漢字・文脈上の読みが正しいか"
        label="語・漢字読み"
        onChange={(next) => setField("reading_correct", next)}
        trueLabel="正しい"
        value={value.reading_correct}
      />
      <ScoreField
        help="理論上の発音だけでなく、厳密な日本語音調・アクセント"
        label="日本語音調・アクセント"
        onChange={(next) => setField("accent_naturalness", next)}
        value={value.accent_naturalness}
      />
      <ScoreField
        help="Gender / Age / Archetype / Voice identity を役柄指定と照合"
        label="役柄一致"
        onChange={(next) => setField("role_match", next)}
        value={value.role_match}
      />
      <ScoreField
        help="感情・強度・行ごとの演技指示への一致"
        label="Delivery"
        onChange={(next) => setField("delivery_match", next)}
        value={value.delivery_match}
      />
      <ScoreField
        help="棒読み感、自然さ、ノイズ、破綻を含む総合品質"
        label="Naturalness / audio quality"
        onChange={(next) => setField("audio_quality", next)}
        value={value.audio_quality}
      />
      <BooleanField
        falseLabel="採用不可"
        help="上記を総合し、このbaseline候補として公開できるか"
        label="Baseline採用可否"
        onChange={(next) => setField("adoptable", next)}
        trueLabel="採用可能"
        value={value.adoptable}
      />

      <label className="block">
        <span className="text-sm font-medium">問題点メモ（任意）</span>
        <textarea
          className="mt-2 min-h-20 w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
          onChange={(event) => setField("notes", event.currentTarget.value)}
          placeholder="誤読、音調、役柄、演技、ノイズなど"
          value={value.notes}
        />
      </label>
    </div>
  );
}

function BooleanField({
  falseLabel,
  help,
  label,
  onChange,
  trueLabel,
  value,
}: {
  readonly falseLabel: string;
  readonly help: string;
  readonly label: string;
  readonly onChange: (value: boolean) => void;
  readonly trueLabel: string;
  readonly value: boolean | null;
}) {
  return (
    <fieldset>
      <legend className="text-sm font-medium">{label}</legend>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{help}</p>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <Button
          aria-pressed={value === true}
          onClick={() => onChange(true)}
          size="sm"
          type="button"
          variant={value === true ? "default" : "outline"}
        >
          {trueLabel}
        </Button>
        <Button
          aria-pressed={value === false}
          onClick={() => onChange(false)}
          size="sm"
          type="button"
          variant={value === false ? "default" : "outline"}
        >
          {falseLabel}
        </Button>
      </div>
    </fieldset>
  );
}

function ScoreField({
  help,
  label,
  onChange,
  value,
}: {
  readonly help: string;
  readonly label: string;
  readonly onChange: (value: number) => void;
  readonly value: number | null;
}) {
  return (
    <fieldset>
      <legend className="text-sm font-medium">{label}</legend>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">
        {help}（1=不適合 / 5=非常に適合）
      </p>
      <div className="mt-2 grid grid-cols-5 gap-2">
        {[1, 2, 3, 4, 5].map((score) => (
          <Button
            aria-pressed={value === score}
            key={score}
            onClick={() => onChange(score)}
            size="sm"
            type="button"
            variant={value === score ? "default" : "outline"}
          >
            {score}
          </Button>
        ))}
      </div>
    </fieldset>
  );
}
