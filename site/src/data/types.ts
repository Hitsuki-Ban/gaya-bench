export type Locale = "ja" | "en";
export type Gender = "female" | "male" | "neutral";
export type Age = "child" | "teen" | "young_adult" | "adult" | "middle_aged" | "elderly";
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

export interface Scene {
  readonly setting: string;
  readonly acoustics?: string;
  readonly listener?: string;
}

export interface Character {
  readonly id: string;
  readonly name: string;
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

export interface Clip {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly path: string;
  readonly duration_sec: number;
  readonly sha256: string;
  readonly gen_params: { readonly [key: string]: JsonValue };
  readonly rtf: number;
}

export type GenerationFailureReason = "generation_failed";

export interface GenerationFailure {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly reason: GenerationFailureReason;
}

export interface Manifest {
  readonly format_version: 2;
  readonly generated_at: string;
  readonly models: readonly Model[];
  readonly clips: readonly Clip[];
  readonly failures: readonly GenerationFailure[];
}

export interface BenchmarkData {
  readonly manifest: Manifest;
  readonly scenarios: readonly Scenario[];
}
