import type {
  Age,
  CharacterKind,
  ConditioningMode,
  Difficulty,
  Emotion,
  Gender,
} from "../data/types";

export const FILTER_QUERY_KEYS = [
  "scenario",
  "empty",
  "kind",
  "gender",
  "age",
  "emotion",
  "difficulty",
  "model",
  "conditioning",
] as const;

export type FilterQueryKey = (typeof FILTER_QUERY_KEYS)[number];
export type MultiFilterKey = Exclude<FilterQueryKey, "scenario" | "empty">;

export interface FilterState {
  readonly scenario: string | null;
  readonly showEmpty: boolean;
  readonly kind: ReadonlySet<CharacterKind>;
  readonly gender: ReadonlySet<Gender>;
  readonly age: ReadonlySet<Age>;
  readonly emotion: ReadonlySet<Emotion>;
  readonly difficulty: ReadonlySet<Difficulty>;
  readonly model: ReadonlySet<string>;
  /** 条件バリアント列の表示 mode。両方選択が既定 (= すべて)。 */
  readonly conditioning: ReadonlySet<ConditioningMode>;
}

export interface FilterValueByKey {
  readonly kind: CharacterKind;
  readonly gender: Gender;
  readonly age: Age;
  readonly emotion: Emotion;
  readonly difficulty: Difficulty;
  readonly model: string;
  readonly conditioning: ConditioningMode;
}

export type FilterQueryIssueCode =
  | "unknown_key"
  | "repeated_scenario"
  | "repeated_empty"
  | "empty_value"
  | "unknown_value";

export interface FilterQueryIssue {
  readonly code: FilterQueryIssueCode;
  readonly key: string;
  readonly value?: string;
  readonly message: string;
}

export type FilterQueryResult =
  | {
      readonly ok: true;
      readonly state: FilterState;
      readonly canonicalSearch: string;
    }
  | {
      readonly ok: false;
      readonly issues: readonly FilterQueryIssue[];
    };
