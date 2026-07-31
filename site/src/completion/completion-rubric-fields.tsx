import { CheckCheck } from "lucide-react";

import { Button } from "@/components/ui/button";

import type { RoleReviewPhase, RoleReviewRubric, RubricResult } from "./types";

const CRITERIA = [
  {
    field: "content",
    label: "内容",
    help: "台詞の欠落・追加・反復がない",
  },
  {
    field: "prompt_leakage",
    label: "提示語漏洩",
    help: "感情名・話し方・メタ文が音声へ漏れていない",
  },
  {
    field: "reading",
    label: "漢字読み",
    help: "語の読みと文脈上の読みが正しい",
  },
  {
    field: "pitch_accent",
    label: "Pitch accent",
    help: "理論上の読みだけでなく、厳密な日本語音調が正しい",
  },
  {
    field: "gender",
    label: "Gender",
    help: "指定された性別の声として聞こえる",
  },
  {
    field: "age",
    label: "Age",
    help: "指定された年齢帯の声として聞こえる",
  },
  {
    field: "archetype",
    label: "Archetype",
    help: "役柄・職能・存在種別の印象が一致する",
  },
  {
    field: "voice_identity",
    label: "Voice identity",
    help: "同じ役柄のanchor・前後行と同一人物に聞こえる",
  },
  {
    field: "delivery",
    label: "Delivery",
    help: "行ごとの演技指示、感情、強度に一致する",
  },
] as const satisfies readonly {
  readonly field: keyof RoleReviewRubric;
  readonly label: string;
  readonly help: string;
}[];

export function CompletionRubricFields({
  onChange,
  phase,
  value,
}: {
  onChange: (value: RoleReviewRubric) => void;
  phase: RoleReviewPhase;
  value: RoleReviewRubric;
}) {
  const setField = <Field extends keyof RoleReviewRubric>(
    field: Field,
    fieldValue: RoleReviewRubric[Field],
  ) => onChange({ ...value, [field]: fieldValue });

  const markBaselinePass = () => {
    onChange({
      ...value,
      content: "pass",
      prompt_leakage: "pass",
      reading: "pass",
      pitch_accent: "pass",
      gender: "pass",
      age: "pass",
      archetype: "pass",
      voice_identity: phase === "anchor" ? "not_applicable" : "pass",
      delivery: phase === "anchor" ? "not_applicable" : "pass",
      naturalness_quality: 4,
    });
  };

  return (
    <div className="space-y-4" data-role-review-rubric>
      <Button className="w-full" onClick={markBaselinePass} type="button" variant="outline">
        <CheckCheck aria-hidden="true" />
        基準を満たす状態から入力
      </Button>

      {CRITERIA.map((criterion) => (
        <ResultField
          help={criterion.help}
          key={criterion.field}
          label={criterion.label}
          onChange={(next) => setField(criterion.field, next)}
          value={value[criterion.field] as RubricResult | null}
        />
      ))}

      <fieldset>
        <legend className="text-sm font-medium">自然度・音質</legend>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          ノイズ、破綻、棒読み感を含む総合品質（1=悪い / 5=良い）
        </p>
        <div className="mt-2 grid grid-cols-5 gap-2">
          {[1, 2, 3, 4, 5].map((score) => (
            <Button
              aria-pressed={value.naturalness_quality === score}
              key={score}
              onClick={() => setField("naturalness_quality", score)}
              size="sm"
              type="button"
              variant={value.naturalness_quality === score ? "default" : "outline"}
            >
              {score}
            </Button>
          ))}
        </div>
      </fieldset>

      <label className="block">
        <span className="text-sm font-medium">問題点メモ（任意）</span>
        <textarea
          className="mt-2 min-h-20 w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
          onChange={(event) => setField("notes", event.currentTarget.value)}
          placeholder="誤読、音調、声線、同一性、演技、ノイズなど"
          value={value.notes}
        />
      </label>
    </div>
  );
}

function ResultField({
  help,
  label,
  onChange,
  value,
}: {
  help: string;
  label: string;
  onChange: (value: RubricResult | null) => void;
  value: RubricResult | null;
}) {
  return (
    <fieldset>
      <legend className="text-sm font-medium">{label}</legend>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{help}</p>
      <div className="mt-2 grid grid-cols-3 gap-2">
        {[
          ["pass", "適合"],
          ["fail", "不適合"],
          ["not_applicable", "対象外"],
        ].map(([result, labelText]) => (
          <Button
            aria-pressed={value === result}
            key={result}
            onClick={() => onChange(result as RubricResult)}
            size="sm"
            type="button"
            variant={value === result ? "default" : "outline"}
          >
            {labelText}
          </Button>
        ))}
      </div>
    </fieldset>
  );
}
