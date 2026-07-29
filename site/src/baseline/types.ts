import type { AudioClip } from "@/audio/playback-manager";
import type {
  CurateCandidatePresentation,
  CurateDecision,
  ExportCandidate,
  GroupDraft,
  Rubric,
} from "@/curate/types";

export type BaselineComparison = "identical" | "different";

export interface BaselineReferencePresentation {
  readonly audio: AudioClip;
  readonly publicPath: string;
  readonly sha256: string;
  readonly comparison: BaselineComparison;
}

export interface BaselineGroup {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly scenarioTitle: string;
  readonly lineText: string;
  readonly delivery: string;
  readonly candidate: CurateCandidatePresentation;
  readonly candidateSha256: string;
  readonly reference: BaselineReferencePresentation;
}

export interface BaselineCatalog {
  readonly candidateSetSha256: string;
  readonly baselineReferenceSha256: string;
  readonly groups: readonly BaselineGroup[];
  readonly exportCandidatesByGroup: ReadonlyMap<string, readonly ExportCandidate[]>;
  readonly auditedNoCandidateCount: number;
  dispose(): void;
}

export interface BaselineCurationDraft {
  readonly version: 1;
  readonly candidate_set_sha256: string;
  readonly baseline_reference_sha256: string;
  readonly groups: readonly GroupDraft[];
}

export type BaselineRubric = Rubric;
export type BaselineDecision = CurateDecision;
