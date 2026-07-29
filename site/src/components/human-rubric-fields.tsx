import { Button } from "@/components/ui/button";

export interface HumanRubricDraft {
  readonly content_correct: boolean | null;
  readonly intent_match: number | null;
  readonly character_naturalness: number | null;
  readonly adoptable: boolean | null;
}

export function HumanRubricFields({
  onChange,
  value,
}: {
  onChange: (value: HumanRubricDraft) => void;
  value: HumanRubricDraft;
}) {
  const setField = (field: keyof HumanRubricDraft, fieldValue: boolean | number | null) =>
    onChange({ ...value, [field]: fieldValue });

  return (
    <>
      <BooleanRubric
        label="内容は正しい"
        onChange={(next) => setField("content_correct", next)}
        value={value.content_correct}
      />
      <ScaleRubric
        label="意図一致"
        onChange={(next) => setField("intent_match", next)}
        value={value.intent_match}
      />
      <ScaleRubric
        label="役として自然"
        onChange={(next) => setField("character_naturalness", next)}
        value={value.character_naturalness}
      />
      <BooleanRubric
        label="採用可能"
        onChange={(next) => setField("adoptable", next)}
        value={value.adoptable}
      />
    </>
  );
}

function BooleanRubric({
  label,
  onChange,
  value,
}: {
  label: string;
  onChange: (value: boolean | null) => void;
  value: boolean | null;
}) {
  return (
    <fieldset>
      <legend className="mb-2 text-sm font-medium">{label}</legend>
      <div className="flex flex-wrap gap-2">
        <ChoiceButton active={value === true} label="はい" onClick={() => onChange(true)} />
        <ChoiceButton active={value === false} label="いいえ" onClick={() => onChange(false)} />
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
      <legend className="mb-2 text-sm font-medium">{label}</legend>
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
