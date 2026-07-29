export type Locale = "ja" | "en";
export type Gender = "female" | "male" | "neutral";
export type Age = "child" | "teen" | "young_adult" | "adult" | "middle_aged" | "elderly";
export type CharacterKind = "human" | "machine" | "creature" | "spirit";
export type Emotion =
  | "neutral"
  | "cheerful"
  | "angry"
  | "sad"
  | "fearful"
  | "surprised"
  | "tired"
  | "drunk"
  | "whisper"
  | "shout"
  | "laughing"
  | "pain";
export type Difficulty = "standard" | "hard";
export type FinalIntonation = "fall" | "rise" | "free";

export interface Scene {
  readonly setting: string;
  readonly acoustics?: string;
  readonly listener?: string;
}

export interface Character {
  readonly id: string;
  readonly name: string;
  readonly kind: CharacterKind;
  readonly gender: Gender;
  readonly age: Age;
  readonly archetype?: string;
  readonly voice: string;
  readonly personality?: string;
  readonly reference_voice?: string | null;
}

export interface Line {
  readonly id: string;
  readonly character: string;
  readonly text: string;
  readonly reading?: string | null;
  readonly emotion: Emotion;
  readonly intensity: 1 | 2 | 3;
  readonly delivery: string;
  readonly situation?: string;
  readonly difficulty: Difficulty;
  readonly loop_ok: boolean;
  readonly final_intonation: FinalIntonation;
}

export interface Scenario {
  readonly format_version: 1;
  readonly id: string;
  readonly title: string;
  readonly locale: Locale;
  readonly tags?: readonly string[];
  readonly scene: Scene;
  readonly characters: readonly Character[];
  readonly lines: readonly Line[];
}

export interface ModelCapabilities {
  readonly emotion: boolean;
  readonly voice_prompt: boolean;
  readonly clone: boolean;
  readonly nonverbal: boolean;
  readonly reading: boolean;
}

export interface Model {
  readonly id: string;
  readonly name: string;
  readonly version: string;
  readonly license_note: string;
  readonly capabilities: ModelCapabilities;
}

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue =
  | JsonPrimitive
  | { readonly [key: string]: JsonValue }
  | readonly JsonValue[];

export interface Candidate {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly take_index: number;
  readonly take_id: string;
  readonly path: string;
  readonly duration_sec: number;
  readonly sha256: string;
  readonly generation_input_sha256: string;
  readonly gen_params: { readonly [key: string]: JsonValue };
  readonly rtf: number;
  readonly loudness: {
    readonly source: "encoded_opus";
    readonly i_lufs: number;
    readonly tp_dbtp: number;
    readonly shortfall: boolean;
  };
  readonly gate: {
    readonly mechanical: "pass";
    readonly content: "pass" | "review_required";
    readonly policy_version: string;
  };
}

interface CurationBase {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly curation_sha256: string;
}

export interface SelectedCuration extends CurationBase {
  readonly decision: "selected";
  readonly take_id: string;
}

export interface SkippedCuration extends CurationBase {
  readonly decision: "skipped";
}

export type Curation = SelectedCuration | SkippedCuration;

export type GenerationFailureReason = "no_eligible_take" | "test_only_adapter";

export interface GenerationFailure {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly reason: GenerationFailureReason;
}

export interface Manifest {
  readonly format_version: 4;
  readonly generated_at: string;
  readonly candidate_set_sha256: string;
  readonly models: readonly Model[];
  readonly candidates: readonly Candidate[];
  readonly curations: readonly Curation[];
  readonly failures: readonly GenerationFailure[];
}

export interface ArtifactGroup {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
}

export type ArtifactOutcome =
  | {
      readonly kind: "selected";
      readonly group: ArtifactGroup;
      readonly candidate: Candidate;
      readonly curation: SelectedCuration;
    }
  | {
      readonly kind: "skipped";
      readonly group: ArtifactGroup;
      readonly candidates: readonly Candidate[];
      readonly curation: SkippedCuration;
    }
  | {
      readonly kind: "uncurated";
      readonly group: ArtifactGroup;
      readonly candidates: readonly Candidate[];
    }
  | {
      readonly kind: "failure";
      readonly group: ArtifactGroup;
      readonly failure: GenerationFailure;
    };

export interface BenchmarkData {
  readonly manifest: Manifest;
  readonly scenarios: readonly Scenario[];
  readonly outcomes: readonly ArtifactOutcome[];
}
