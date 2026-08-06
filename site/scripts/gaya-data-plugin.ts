import { readFileSync, readdirSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";

import { parse } from "yaml";
import type { Plugin } from "vite-plus";

import type {
  ArtifactOutcome,
  BenchmarkData,
  Candidate,
  Curation,
  GenerationFailure,
  Manifest,
  Model,
  ModelCredit,
  ModelGenerationProfile,
  ModelSourceKind,
  ModelSourceLink,
  PublishedCandidate,
  ReferenceConditioning,
  ReferenceVoiceCredit,
  ReleaseMetadata,
  Scenario,
} from "../src/data/types.ts";
import { isSafeClipPath } from "../src/lib/clip-path.ts";

const VIRTUAL_MODULE_ID = "virtual:gaya-data";
const RESOLVED_VIRTUAL_MODULE_ID = `\0${VIRTUAL_MODULE_ID}`;
const ID_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

const MANIFEST_KEYS = [
  "format_version",
  "generated_at",
  "candidate_set_sha256",
  "models",
  "candidates",
  "curations",
  "failures",
] as const;
const QUALITY_SIGNALS_KEYS = [
  "format_version",
  "protocol",
  "plan_sha256",
  "decision_sha256",
  "groups",
] as const;
const QUALITY_SIGNAL_GROUP_KEYS = [
  "model",
  "scenario",
  "line",
  "variant",
  "protocol",
  "expected_gender",
  "median_f0_hz",
  "status",
  "signal",
  "qc_report_sha256",
] as const;
const MODEL_KEYS = ["id", "name", "version", "license_note", "capabilities"] as const;
const MODEL_OPTIONAL_KEYS = ["conditioning"] as const;
const CONDITIONING_KEYS = ["mode", "base_model"] as const;
const CONDITIONING_MODES = new Set<string>(["human-reference", "text-only"]);
const CAPABILITY_KEYS = ["emotion", "voice_prompt", "clone", "nonverbal", "reading"] as const;
const CANDIDATE_KEYS = [
  "model",
  "scenario",
  "line",
  "variant",
  "take_index",
  "take_id",
  "path",
  "duration_sec",
  "sha256",
  "generation_input_sha256",
  "gen_params",
  "rtf",
  "loudness",
  "gate",
] as const;
const GENERATION_PARAMETER_KEYS = [
  "seed",
  "recipe_version",
  "sampling",
  "requested",
  "realized",
] as const;
const LOUDNESS_KEYS = ["source", "i_lufs", "tp_dbtp", "shortfall"] as const;
const GATE_KEYS = ["mechanical", "content", "policy_version"] as const;
const SELECTED_ANCHOR_KEYS = [
  "anchor_selection_sha256",
  "anchor_plan_sha256",
  "anchor_candidate_set_sha256",
  "anchor_id",
  "anchor_attempt",
  "anchor_seed",
  "anchor_audio_sha256",
  "anchor_text_sha256",
  "anchor_decision_sha256",
  "role_identity_sha256",
  "role_epoch_sha256",
] as const;
const KNOWN_REFERENCE_RECEIPT_KEYS = new Set([
  "reference_control",
  "reference_source",
  "reference_kind",
  "reference_voice",
  "reference_sha256",
  "reference_selection_source",
  "reference_source_id",
  "reference_source_sha256",
  "reference_clip_sha256",
  "reference_text",
  "reference_caption",
  "reference_samples",
  "reference_duration_sec",
  "reference_clip_frame_count",
  "reference_clip_start_frame",
]);
const GROUP_KEYS = ["model", "scenario", "line", "variant"] as const;
const SELECTED_CURATION_KEYS = [...GROUP_KEYS, "decision", "take_id", "curation_sha256"] as const;
const SKIPPED_CURATION_KEYS = [...GROUP_KEYS, "decision", "curation_sha256"] as const;
const FAILURE_KEYS = ["model", "scenario", "line", "variant", "reason"] as const;
const SCENARIO_REQUIRED_KEYS = [
  "format_version",
  "id",
  "title",
  "locale",
  "scene",
  "characters",
  "lines",
] as const;
const SCENARIO_OPTIONAL_KEYS = ["tags"] as const;
const SCENE_REQUIRED_KEYS = ["setting"] as const;
const SCENE_OPTIONAL_KEYS = ["acoustics", "listener"] as const;
const CHARACTER_REQUIRED_KEYS = ["id", "name", "gender", "age", "voice"] as const;
const CHARACTER_OPTIONAL_KEYS = ["kind", "archetype", "personality", "reference_voice"] as const;
const LINE_REQUIRED_KEYS = ["id", "character", "text", "emotion", "delivery"] as const;
const LINE_OPTIONAL_KEYS = [
  "reading",
  "intensity",
  "situation",
  "difficulty",
  "loop_ok",
  "final_intonation",
] as const;
const VOICE_METADATA_KEYS = ["format_version", "voices"] as const;
const REFERENCE_VOICE_KEYS = [
  "id",
  "file",
  "sha256",
  "duration_sec",
  "language",
  "transcript",
  "transcript_rights",
  "source",
  "rights",
  "credit_text",
  "voice",
  "processing",
] as const;
const TRANSCRIPT_RIGHTS_KEYS = ["license", "evidence_url", "credit_text"] as const;
const VOICE_SOURCE_KEYS = ["title", "speaker", "download_page", "files"] as const;
const VOICE_SOURCE_FILE_KEYS = ["label", "url", "sha256"] as const;
const VOICE_RIGHTS_KEYS = [
  "license",
  "verified_on",
  "voice_synthesis_evidence_url",
  "commercial_use_evidence_url",
  "redistribution",
] as const;
const REDISTRIBUTION_KEYS = ["status", "evidence_url", "notes"] as const;
const VOICE_PROFILE_KEYS = ["gender", "age", "notes"] as const;
const VOICE_PROCESSING_KEYS = ["source_member", "source_sha256", "summary"] as const;
const AIVIS_MODEL_ID = "aivisspeech-kohaku";
const AIVIS_ENGINE_VERSION = "1.2.0";
const AIVIS_ENGINE_MANIFEST_VERSION = "0.13.1";
const AIVIS_ENGINE_MANIFEST_UUID = "1b4a5014-d9fd-11ee-b97d-83c170a68ed3";
const AIVIS_MODEL_NAME = "コハク";
const AIVIS_MODEL_UUID = "22e8ed77-94fe-4ef2-871f-a86f94e9a579";
const AIVIS_MODEL_VERSION = "1.1.0";
const AIVIS_MODEL_SHA256 = "3f5c08b52bb8a64efd361268580c81510f96c927cd6905aa7dbae6851333270a";
const AIVIS_MODEL_LICENSE = "ACML-1.0";
const IRODORI_MODEL_ID = "irodori-tts-600m-v3-voicedesign";
const IRODORI_UPSTREAM_REPOSITORY = "Aratako/Irodori-TTS";
const IRODORI_UPSTREAM_REVISION = "eaf74d6a19138f743acb5b71a445fd25a57db987";
const IRODORI_CHECKPOINT = "Aratako/Irodori-TTS-600M-v3-VoiceDesign";
const IRODORI_CHECKPOINT_REVISION = "e863a3a93e652e09afeff3e84823a206a0a60314";
const IRODORI_V4_MODEL_ID = "irodori-tts-v4-small";
const IRODORI_V4_UPSTREAM_REVISION = "8ca3acb58ab4e19ad6d594aaed6bafe3e88f7f71";
const IRODORI_V4_CHECKPOINT = "Aratako/Irodori-TTS-v4-Small";
const IRODORI_V4_CHECKPOINT_REVISION = "e4aaac4df355ff560dcd35e0dae272c3a759317b";

interface ModelSourceField {
  readonly repository: string;
  readonly revision: string;
  readonly label: string;
  readonly kind: ModelSourceKind;
  readonly host: "github" | "huggingface";
}

const MODEL_SOURCE_FIELDS: readonly ModelSourceField[] = [
  {
    repository: "upstream_repository",
    revision: "upstream_revision",
    label: "コード",
    kind: "code",
    host: "github",
  },
  {
    repository: "weights_repository",
    revision: "weights_revision",
    label: "公式ウェイト",
    kind: "weights",
    host: "huggingface",
  },
  {
    repository: "base_model",
    revision: "base_revision",
    label: "Base ウェイト",
    kind: "weights",
    host: "huggingface",
  },
  {
    repository: "voice_design_model",
    revision: "voice_design_revision",
    label: "VoiceDesign ウェイト",
    kind: "weights",
    host: "huggingface",
  },
  {
    repository: "perth_repository",
    revision: "perth_revision",
    label: "PerTh 依存",
    kind: "related",
    host: "github",
  },
  {
    repository: "matcha_repository",
    revision: "matcha_revision",
    label: "Matcha-TTS 依存",
    kind: "related",
    host: "github",
  },
  {
    repository: "sdk_repository",
    revision: "sdk_revision",
    label: "SDK",
    kind: "related",
    host: "github",
  },
] as const;

const LOCALES = new Set(["ja", "en"]);
const GENDERS = new Set(["female", "male", "neutral"]);
const AGES = new Set(["child", "teen", "young_adult", "adult", "middle_aged", "elderly"]);
const CHARACTER_KINDS = new Set(["human", "machine", "creature", "spirit"]);
const EMOTIONS = new Set([
  "neutral",
  "cheerful",
  "angry",
  "sad",
  "fearful",
  "surprised",
  "tired",
  "drunk",
  "whisper",
  "shout",
  "laughing",
  "pain",
]);
const DIFFICULTIES = new Set(["standard", "hard"]);
const FINAL_INTONATIONS = new Set(["fall", "rise", "free"]);
const LOUDNESS_SOURCES = new Set(["encoded_opus"]);
const FAILURE_REASONS = new Set(["no_eligible_take", "test_only_adapter"]);
const CONTENT_GATE_RESULTS = new Set(["pass", "review_required"]);
const QUALITY_SIGNAL_STATUSES = new Set(["pass", "review_required", "not_applicable"]);
const QUALITY_SIGNAL_VALUES = new Set([
  "gender_f0_unavailable",
  "gender_f0_below_expected",
  "gender_f0_above_expected",
]);
const VARIANTS = new Set(["dry"]);
const RECIPE_VERSIONS = new Set(["seed-only-v1", "fixed-single-v1"]);
const REDISTRIBUTION_STATUSES = new Set(["prohibited", "allowed_with_conditions"]);
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const SHA1_PATTERN = /^[0-9a-f]{40}$/;
const REPOSITORY_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

type WatchFile = (file: string) => void;
type UnknownRecord = Record<string, unknown>;
type QualitySignalGroup = {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly protocol: "role-gender-f0-soft-v1";
  readonly expected_gender: "female" | "male" | "neutral";
  readonly median_f0_hz: number | null;
  readonly status: "pass" | "review_required" | "not_applicable";
  readonly signal:
    | "gender_f0_unavailable"
    | "gender_f0_below_expected"
    | "gender_f0_above_expected"
    | null;
  readonly qc_report_sha256: string;
};

type QualitySignals = {
  readonly format_version: 1;
  readonly protocol: "role-quality-signals-v1";
  readonly plan_sha256: string;
  readonly decision_sha256: string;
  readonly groups: readonly QualitySignalGroup[];
};

export class GayaDataError extends Error {
  override readonly name = "GayaDataError";
}

export interface GayaDataPluginOptions {
  readonly repositoryRoot: string;
}

export function gayaDataPlugin({ repositoryRoot }: GayaDataPluginOptions): Plugin {
  const absoluteRepositoryRoot = path.resolve(repositoryRoot);

  function loadAndWatch(addWatchFile: WatchFile): BenchmarkData {
    return loadBenchmarkData(absoluteRepositoryRoot, addWatchFile);
  }

  return {
    name: "gaya-data",
    enforce: "pre",
    buildStart() {
      loadAndWatch((file) => this.addWatchFile(file));
    },
    resolveId(id) {
      if (id === VIRTUAL_MODULE_ID) {
        return RESOLVED_VIRTUAL_MODULE_ID;
      }
    },
    load(id) {
      if (id !== RESOLVED_VIRTUAL_MODULE_ID) {
        return;
      }

      const data = loadAndWatch((file) => this.addWatchFile(file));
      return [
        `const benchmarkData = ${JSON.stringify(data)};`,
        "export { benchmarkData };",
        "export default benchmarkData;",
      ].join("\n");
    },
  };
}

export function loadBenchmarkData(repositoryRoot: string, watchFile?: WatchFile): BenchmarkData {
  const manifestPath = path.join(repositoryRoot, "data", "manifest.json");
  const qualitySignalsPath = path.join(repositoryRoot, "data", "quality-signals.json");
  const scenariosDirectory = path.join(repositoryRoot, "scenarios");
  const voiceMetadataPath = path.join(repositoryRoot, "assets", "voices", "metadata.yaml");
  watchFile?.(manifestPath);
  watchFile?.(qualitySignalsPath);
  watchFile?.(voiceMetadataPath);

  const manifest = loadManifest(manifestPath);
  const qualitySignals = loadQualitySignals(qualitySignalsPath);
  const scenarios = loadScenarios(scenariosDirectory, watchFile);
  const referenceVoices = loadReferenceVoices(voiceMetadataPath);
  validateReferences(manifest, scenarios, referenceVoices);

  return {
    release: projectReleaseMetadata(manifest),
    scenarios,
    outcomes: projectOutcomes(manifest, qualitySignals, referenceVoices),
    generation_profiles: projectGenerationProfiles(manifest),
    credits: {
      model_sources: projectModelCredits(manifest),
      reference_voices: referenceVoices,
    },
  };
}

function projectReleaseMetadata(manifest: Manifest): ReleaseMetadata {
  return {
    format_version: manifest.format_version,
    generated_at: manifest.generated_at,
    candidate_set_sha256: manifest.candidate_set_sha256,
    models: manifest.models,
  };
}

function projectPublishedCandidate(
  candidate: Candidate,
  roleQuality: PublishedCandidate["role_quality"],
  model: Model,
  referenceVoices: ReadonlyMap<string, ReferenceVoiceCredit>,
): PublishedCandidate {
  return {
    model: candidate.model,
    scenario: candidate.scenario,
    line: candidate.line,
    variant: candidate.variant,
    path: candidate.path,
    duration_sec: candidate.duration_sec,
    rtf: candidate.rtf,
    reference_conditioning: projectReferenceConditioning(
      candidate.gen_params.realized,
      model,
      referenceVoices,
      `selected candidate ${candidate.take_id}`,
    ),
    gate: { content: candidate.gate.content },
    role_quality: roleQuality,
  };
}

function projectReferenceConditioning(
  realized: Candidate["gen_params"]["realized"],
  model: Model,
  referenceVoices: ReadonlyMap<string, ReferenceVoiceCredit>,
  label: string,
): ReferenceConditioning {
  validateKnownReferenceReceiptKeys(realized, label);

  const hasReferenceKind = "reference_kind" in realized;
  const hasReferenceSource = "reference_source" in realized;
  const qwenReferenceControl =
    realized.reference_control === "voice_asset" ||
    realized.reference_control === "selected_voice_design_anchor";
  const hasGptHash = "reference_source_sha256" in realized || "reference_clip_sha256" in realized;
  if ("reference_control" in realized && !hasReferenceSource && !qwenReferenceControl) {
    throw new GayaDataError(`${label}.realized.reference_control が未知の tag です。`);
  }
  const primaryReceipts = [
    hasReferenceKind,
    hasReferenceSource,
    qwenReferenceControl,
    hasGptHash,
  ].filter(Boolean).length;
  if (primaryReceipts > 1) {
    throw new GayaDataError(`${label}.realized の参照音声 receipt が競合しています。`);
  }

  let conditioning: ReferenceConditioning;
  if (hasReferenceKind) {
    conditioning = projectVoxReference(realized, referenceVoices, label);
  } else if (hasReferenceSource) {
    conditioning = projectIrodoriReference(realized, referenceVoices, label);
  } else if (qwenReferenceControl) {
    conditioning = projectQwenReference(realized, referenceVoices, label);
  } else if (hasGptHash) {
    conditioning = projectGptReference(realized, referenceVoices, label);
  } else if (hasReferenceReceipt(realized)) {
    conditioning = projectDirectCloneReference(realized, referenceVoices, label);
  } else {
    conditioning = { kind: "none" };
  }

  assertReferenceFamily(model, conditioning, label);
  return conditioning;
}

function projectDirectCloneReference(
  realized: Candidate["gen_params"]["realized"],
  referenceVoices: ReadonlyMap<string, ReferenceVoiceCredit>,
  label: string,
): ReferenceConditioning {
  assertAllowedReferenceReceiptKeys(
    realized,
    [
      "reference_voice",
      "reference_sha256",
      "reference_selection_source",
      "reference_samples",
      "reference_duration_sec",
    ],
    label,
  );
  const hasReferenceLength =
    "reference_samples" in realized || "reference_duration_sec" in realized;
  if (hasReferenceLength) {
    assertPositiveInteger(realized.reference_samples, `${label}.realized.reference_samples`);
    assertNonNegativeFiniteNumber(
      realized.reference_duration_sec,
      `${label}.realized.reference_duration_sec`,
    );
    if (realized.reference_duration_sec === 0) {
      throw new GayaDataError(
        `${label}.realized.reference_duration_sec は0より大きい値が必要です。`,
      );
    }
  }
  const voiceId = requiredReferenceString(
    realized.reference_voice,
    `${label}.realized.reference_voice`,
  );
  const referenceSha = requiredReferenceSha(
    realized.reference_sha256,
    `${label}.realized.reference_sha256`,
  );
  const selectionSource = requiredReferenceString(
    realized.reference_selection_source,
    `${label}.realized.reference_selection_source`,
  );
  assertAbsent(realized, "selected_anchor", label);
  assertCatalogReference(referenceVoices, voiceId, referenceSha, label);
  return {
    kind: "human_reference",
    voice_id: voiceId,
    asset_sha256: referenceSha,
    inference_reference_sha256: referenceSha,
    selection_source: selectionSource,
  };
}

function projectGptReference(
  realized: Candidate["gen_params"]["realized"],
  referenceVoices: ReadonlyMap<string, ReferenceVoiceCredit>,
  label: string,
): ReferenceConditioning {
  assertAllowedReferenceReceiptKeys(
    realized,
    [
      "reference_voice",
      "reference_source_sha256",
      "reference_clip_sha256",
      "reference_selection_source",
      "reference_clip_frame_count",
      "reference_clip_start_frame",
    ],
    label,
  );
  const hasClipFrames =
    "reference_clip_frame_count" in realized || "reference_clip_start_frame" in realized;
  if (hasClipFrames) {
    assertPositiveInteger(
      realized.reference_clip_frame_count,
      `${label}.realized.reference_clip_frame_count`,
    );
    if (
      typeof realized.reference_clip_start_frame !== "number" ||
      !Number.isSafeInteger(realized.reference_clip_start_frame) ||
      realized.reference_clip_start_frame < 0
    ) {
      throw new GayaDataError(
        `${label}.realized.reference_clip_start_frame は0以上の整数が必要です。`,
      );
    }
  }
  const voiceId = requiredReferenceString(
    realized.reference_voice,
    `${label}.realized.reference_voice`,
  );
  const sourceSha = requiredReferenceSha(
    realized.reference_source_sha256,
    `${label}.realized.reference_source_sha256`,
  );
  const clipSha = requiredReferenceSha(
    realized.reference_clip_sha256,
    `${label}.realized.reference_clip_sha256`,
  );
  const selectionSource = requiredReferenceString(
    realized.reference_selection_source,
    `${label}.realized.reference_selection_source`,
  );
  assertAbsent(realized, "reference_sha256", label);
  assertAbsent(realized, "selected_anchor", label);
  assertCatalogReference(referenceVoices, voiceId, sourceSha, label);
  return {
    kind: "human_reference",
    voice_id: voiceId,
    asset_sha256: sourceSha,
    inference_reference_sha256: clipSha,
    selection_source: selectionSource,
  };
}

function projectVoxReference(
  realized: Candidate["gen_params"]["realized"],
  referenceVoices: ReadonlyMap<string, ReferenceVoiceCredit>,
  label: string,
): ReferenceConditioning {
  assertAllowedReferenceReceiptKeys(
    realized,
    ["reference_kind", "reference_voice", "reference_sha256", "reference_selection_source"],
    label,
  );
  if (realized.reference_kind !== "asset" && realized.reference_kind !== "voice_design") {
    throw new GayaDataError(`${label}.realized.reference_kind が未知の tag です。`);
  }
  const referenceSha = requiredReferenceSha(
    realized.reference_sha256,
    `${label}.realized.reference_sha256`,
  );
  const selectionSource = requiredReferenceString(
    realized.reference_selection_source,
    `${label}.realized.reference_selection_source`,
  );
  assertAbsent(realized, "selected_anchor", label);

  if (realized.reference_kind === "asset") {
    const voiceId = requiredReferenceString(
      realized.reference_voice,
      `${label}.realized.reference_voice`,
    );
    assertCatalogReference(referenceVoices, voiceId, referenceSha, label);
    return {
      kind: "human_reference",
      voice_id: voiceId,
      asset_sha256: referenceSha,
      inference_reference_sha256: referenceSha,
      selection_source: selectionSource,
    };
  }

  if (realized.reference_voice !== null) {
    throw new GayaDataError(
      `${label}.realized.reference_voice は voice_design では null が必要です。`,
    );
  }
  return {
    kind: "model_generated_reference",
    inference_reference_sha256: referenceSha,
    source_kind: "voice_design",
  };
}

function projectQwenReference(
  realized: Candidate["gen_params"]["realized"],
  referenceVoices: ReadonlyMap<string, ReferenceVoiceCredit>,
  label: string,
): ReferenceConditioning {
  assertAllowedReferenceReceiptKeys(
    realized,
    [
      "reference_control",
      "reference_source_id",
      "reference_sha256",
      "reference_text",
      "selected_anchor",
    ],
    label,
  );
  const referenceSha = requiredReferenceSha(
    realized.reference_sha256,
    `${label}.realized.reference_sha256`,
  );
  const sourceId = requiredReferenceString(
    realized.reference_source_id,
    `${label}.realized.reference_source_id`,
  );
  requiredReferenceString(realized.reference_text, `${label}.realized.reference_text`);
  assertRecord(realized.character_identity, `${label}.realized.character_identity`);
  assertAbsent(realized, "reference_voice", label);
  assertAbsent(realized, "reference_selection_source", label);

  if (realized.reference_control === "voice_asset") {
    assertAbsent(realized, "selected_anchor", label);
    assertCatalogReference(referenceVoices, sourceId, referenceSha, label);
    return {
      kind: "human_reference",
      voice_id: sourceId,
      asset_sha256: referenceSha,
      inference_reference_sha256: referenceSha,
      selection_source: "voice_asset",
    };
  }

  const selectedAnchor = validateSelectedAnchor(realized.selected_anchor, label, referenceSha);
  if (sourceId !== selectedAnchor.anchor_id) {
    throw new GayaDataError(
      `${label}.realized.reference_source_id が selected_anchor.anchor_id と一致しません。`,
    );
  }
  return {
    kind: "model_generated_reference",
    inference_reference_sha256: referenceSha,
    source_kind: "selected_voice_design_anchor",
  };
}

function projectIrodoriReference(
  realized: Candidate["gen_params"]["realized"],
  referenceVoices: ReadonlyMap<string, ReferenceVoiceCredit>,
  label: string,
): ReferenceConditioning {
  assertAllowedReferenceReceiptKeys(
    realized,
    [
      "reference_control",
      "reference_source",
      "reference_voice",
      "reference_sha256",
      "reference_caption",
      "reference_text",
      "selected_anchor",
    ],
    label,
  );
  if (realized.reference_control !== "character-stable-reference-audio-v1") {
    throw new GayaDataError(
      `${label}.realized.reference_control は character-stable-reference-audio-v1 が必要です。`,
    );
  }
  if (
    realized.reference_source !== "voice-asset" &&
    realized.reference_source !== "selected-role-anchor"
  ) {
    throw new GayaDataError(`${label}.realized.reference_source が未知の tag です。`);
  }
  const referenceSha = requiredReferenceSha(
    realized.reference_sha256,
    `${label}.realized.reference_sha256`,
  );
  assertAbsent(realized, "reference_source_id", label);
  assertAbsent(realized, "reference_selection_source", label);

  if (realized.reference_source === "voice-asset") {
    const voiceId = requiredReferenceString(
      realized.reference_voice,
      `${label}.realized.reference_voice`,
    );
    if (realized.reference_caption !== null || realized.reference_text !== null) {
      throw new GayaDataError(`${label}.realized の voice-asset caption/text は null が必要です。`);
    }
    assertAbsent(realized, "selected_anchor", label);
    assertCatalogReference(referenceVoices, voiceId, referenceSha, label);
    return {
      kind: "human_reference",
      voice_id: voiceId,
      asset_sha256: referenceSha,
      inference_reference_sha256: referenceSha,
      selection_source: "voice-asset",
    };
  }

  if (realized.reference_voice !== null) {
    throw new GayaDataError(
      `${label}.realized.reference_voice は selected-role-anchor では null が必要です。`,
    );
  }
  requiredReferenceString(realized.reference_caption, `${label}.realized.reference_caption`);
  requiredReferenceString(realized.reference_text, `${label}.realized.reference_text`);
  validateSelectedAnchor(realized.selected_anchor, label, referenceSha);
  return {
    kind: "model_generated_reference",
    inference_reference_sha256: referenceSha,
    source_kind: "selected-role-anchor",
  };
}

function validateSelectedAnchor(
  value: unknown,
  label: string,
  referenceSha: string,
): {
  readonly anchor_id: string;
} {
  const anchorLabel = `${label}.realized.selected_anchor`;
  assertRecord(value, anchorLabel);
  assertExactKeys(value, SELECTED_ANCHOR_KEYS, [], anchorLabel);
  for (const key of SELECTED_ANCHOR_KEYS) {
    if (key === "anchor_attempt") {
      assertPositiveInteger(value[key], `${anchorLabel}.${key}`);
    } else if (key === "anchor_seed") {
      if (typeof value[key] !== "number" || !Number.isSafeInteger(value[key]) || value[key] < 0) {
        throw new GayaDataError(`${anchorLabel}.${key} は0以上の整数が必要です。`);
      }
    } else {
      assertSha256(value[key], `${anchorLabel}.${key}`);
    }
  }
  if (value.anchor_audio_sha256 !== referenceSha) {
    throw new GayaDataError(
      `${anchorLabel}.anchor_audio_sha256 が reference_sha256 と一致しません。`,
    );
  }
  return { anchor_id: value.anchor_id as string };
}

function assertCatalogReference(
  referenceVoices: ReadonlyMap<string, ReferenceVoiceCredit>,
  voiceId: string,
  assetSha: string,
  label: string,
): void {
  const voice = referenceVoices.get(voiceId);
  if (!voice) {
    throw new GayaDataError(`${label}.realized が未知の参照音声を参照しています: ${voiceId}`);
  }
  if (voice.sha256 !== assetSha) {
    throw new GayaDataError(
      `${label}.realized の参照音声 SHA-256 が catalog と一致しません: ${voiceId}`,
    );
  }
}

function assertReferenceFamily(
  model: Model,
  conditioning: ReferenceConditioning,
  label: string,
): void {
  if (model.capabilities.voice_prompt) {
    return;
  }
  if (model.capabilities.clone) {
    if (conditioning.kind !== "human_reference") {
      throw new GayaDataError(
        `${label} は参照音声からの声クローンですが human_reference ではありません。`,
      );
    }
    return;
  }
  if (conditioning.kind !== "none") {
    throw new GayaDataError(`${label} はプリセット話者ですが参照音声を使用しています。`);
  }
}

function validateKnownReferenceReceiptKeys(
  realized: Candidate["gen_params"]["realized"],
  label: string,
): void {
  for (const key of Object.keys(realized)) {
    if (key.startsWith("reference_") && !KNOWN_REFERENCE_RECEIPT_KEYS.has(key)) {
      throw new GayaDataError(`${label}.realized に未知の参照音声 field があります: ${key}`);
    }
  }
}

function assertAllowedReferenceReceiptKeys(
  realized: Candidate["gen_params"]["realized"],
  allowedKeys: readonly string[],
  label: string,
): void {
  const allowed = new Set(allowedKeys);
  for (const key of Object.keys(realized)) {
    if ((key.startsWith("reference_") || key === "selected_anchor") && !allowed.has(key)) {
      throw new GayaDataError(
        `${label}.realized.${key} はこの参照音声 receipt では使用できません。`,
      );
    }
  }
}

function hasReferenceReceipt(realized: Candidate["gen_params"]["realized"]): boolean {
  return (
    "selected_anchor" in realized ||
    Object.keys(realized).some((key) => key.startsWith("reference_"))
  );
}

function requiredReferenceString(value: unknown, label: string): string {
  assertNonEmptyString(value, label);
  return value;
}

function requiredReferenceSha(value: unknown, label: string): string {
  assertSha256(value, label);
  return value;
}

function assertAbsent(
  realized: Candidate["gen_params"]["realized"],
  key: string,
  label: string,
): void {
  if (key in realized) {
    throw new GayaDataError(`${label}.realized.${key} はこの参照音声 receipt では使用できません。`);
  }
}

function projectGenerationProfiles(manifest: Manifest): readonly ModelGenerationProfile[] {
  const candidatesByTakeId = new Map(
    manifest.candidates.map((candidate) => [candidate.take_id, candidate]),
  );
  const profiles = new Map<string, ModelGenerationProfile>();

  for (const curation of manifest.curations) {
    if (curation.decision !== "selected") {
      continue;
    }
    const candidate = candidatesByTakeId.get(curation.take_id);
    if (!candidate) {
      throw new GayaDataError(
        `生成 profile の selected candidate が存在しません: ${curation.take_id}`,
      );
    }
    const key = JSON.stringify([
      candidate.model,
      candidate.gen_params.recipe_version,
      candidate.gen_params.sampling,
      candidate.gen_params.requested,
    ]);
    const existing = profiles.get(key);
    if (existing) {
      profiles.set(key, {
        ...existing,
        candidate_count: existing.candidate_count + 1,
      });
    } else {
      profiles.set(key, {
        model: candidate.model,
        recipe_version: candidate.gen_params.recipe_version,
        sampling: candidate.gen_params.sampling,
        requested: candidate.gen_params.requested,
        candidate_count: 1,
      });
    }
  }
  return [...profiles.values()];
}

function loadManifest(manifestPath: string): Manifest {
  const source = readTextFile(manifestPath, "manifest");
  let value: unknown;
  try {
    value = JSON.parse(source);
  } catch (error) {
    throw new GayaDataError(`manifest JSON を解析できません: ${manifestPath}`, {
      cause: error,
    });
  }

  assertRecord(value, "manifest");
  assertExactKeys(value, MANIFEST_KEYS, [], "manifest");
  assertManifestVersion(value.format_version);
  assertNonEmptyString(value.generated_at, "manifest generated_at");
  assertSha256(value.candidate_set_sha256, "manifest candidate_set_sha256");
  assertArray(value.models, "manifest models");
  assertArray(value.candidates, "manifest candidates");
  assertArray(value.curations, "manifest curations");
  assertArray(value.failures, "manifest failures");

  const models = value.models.map((model, index) => validateModel(model, index));
  const candidates = value.candidates.map((candidate, index) =>
    validateCandidate(candidate, index),
  );
  const curations = value.curations.map((curation, index) => validateCuration(curation, index));
  const failures = value.failures.map((failure, index) => validateFailure(failure, index));
  assertUnique(
    models.map((model) => model.id),
    "manifest model id",
  );
  assertConditioningColumns(models);
  assertUnique(
    candidates.map((candidate) => candidate.take_id),
    "manifest candidate take_id",
  );
  assertUnique(
    candidates.map((candidate) =>
      JSON.stringify([artifactKeyTuple(candidate), candidate.take_index]),
    ),
    "manifest candidate slot",
  );
  assertUnique(curations.map(artifactKeyTuple), "manifest curation group");
  assertUnique(
    failures.map((failure) => artifactKeyTuple(failure)),
    "manifest failure group",
  );
  const modelIds = new Set(models.map((model) => model.id));
  assertManifestGroups(candidates, curations, failures, modelIds);

  return {
    format_version: 4,
    generated_at: value.generated_at,
    candidate_set_sha256: value.candidate_set_sha256,
    models,
    candidates,
    curations,
    failures,
  };
}

function loadQualitySignals(qualitySignalsPath: string): QualitySignals {
  const source = readTextFile(qualitySignalsPath, "quality signals");
  let value: unknown;
  try {
    value = JSON.parse(source);
  } catch (error) {
    throw new GayaDataError(`quality signals JSON を解析できません: ${qualitySignalsPath}`, {
      cause: error,
    });
  }
  assertRecord(value, "quality signals");
  assertExactKeys(value, QUALITY_SIGNALS_KEYS, [], "quality signals");
  if (value.format_version !== 1 || value.protocol !== "role-quality-signals-v1") {
    throw new GayaDataError("quality signals protocol が不正です。");
  }
  assertSha256(value.plan_sha256, "quality signals plan_sha256");
  assertSha256(value.decision_sha256, "quality signals decision_sha256");
  assertArray(value.groups, "quality signals groups");
  const groups = value.groups.map((raw, index): QualitySignalGroup => {
    const label = `quality signals groups[${index}]`;
    assertRecord(raw, label);
    assertExactKeys(raw, QUALITY_SIGNAL_GROUP_KEYS, [], label);
    for (const field of GROUP_KEYS) {
      assertNonEmptyString(raw[field], `${label}.${field}`);
    }
    if (raw.protocol !== "role-gender-f0-soft-v1") {
      throw new GayaDataError(`${label}.protocol が不正です。`);
    }
    if (typeof raw.expected_gender !== "string" || !GENDERS.has(raw.expected_gender)) {
      throw new GayaDataError(`${label}.expected_gender が不正です。`);
    }
    if (typeof raw.status !== "string" || !QUALITY_SIGNAL_STATUSES.has(raw.status)) {
      throw new GayaDataError(`${label}.status が不正です。`);
    }
    if (raw.median_f0_hz !== null) {
      assertFiniteNumber(raw.median_f0_hz, `${label}.median_f0_hz`);
      if ((raw.median_f0_hz as number) <= 0) {
        throw new GayaDataError(`${label}.median_f0_hz が不正です。`);
      }
    }
    if (
      raw.signal !== null &&
      (typeof raw.signal !== "string" || !QUALITY_SIGNAL_VALUES.has(raw.signal))
    ) {
      throw new GayaDataError(`${label}.signal が不正です。`);
    }
    const median = raw.median_f0_hz as number | null;
    const expectedGender = raw.expected_gender as QualitySignalGroup["expected_gender"];
    let expectedStatus: QualitySignalGroup["status"] = "pass";
    let expectedSignal: QualitySignalGroup["signal"] = null;
    if (expectedGender === "neutral") {
      expectedStatus = "not_applicable";
    } else if (median === null) {
      expectedStatus = "review_required";
      expectedSignal = "gender_f0_unavailable";
    } else if (expectedGender === "female" && median < 165) {
      expectedStatus = "review_required";
      expectedSignal = "gender_f0_below_expected";
    } else if (expectedGender === "male" && median > 180) {
      expectedStatus = "review_required";
      expectedSignal = "gender_f0_above_expected";
    }
    if (raw.status !== expectedStatus || raw.signal !== expectedSignal) {
      throw new GayaDataError(`${label} がF0 policyと一致しません。`);
    }
    assertSha256(raw.qc_report_sha256, `${label}.qc_report_sha256`);
    return raw as QualitySignalGroup;
  });
  assertUnique(groups.map(artifactKeyTuple), "quality signal group");
  return {
    format_version: 1,
    protocol: "role-quality-signals-v1",
    plan_sha256: value.plan_sha256,
    decision_sha256: value.decision_sha256,
    groups,
  };
}

function validateModel(value: unknown, index: number): Model {
  const label = `manifest models[${index}]`;
  assertRecord(value, label);
  assertExactKeys(value, MODEL_KEYS, MODEL_OPTIONAL_KEYS, label);
  assertPathSegment(value.id, `${label}.id`);
  assertNonEmptyString(value.name, `${label}.name`);
  assertNonEmptyString(value.version, `${label}.version`);
  assertNonEmptyString(value.license_note, `${label}.license_note`);
  if (value.conditioning !== undefined) {
    validateConditioning(value.conditioning, `${label}.conditioning`, value.id as string);
  }

  assertRecord(value.capabilities, `${label}.capabilities`);
  assertExactKeys(value.capabilities, CAPABILITY_KEYS, [], `${label}.capabilities`);
  for (const key of CAPABILITY_KEYS) {
    assertBoolean(value.capabilities[key], `${label}.capabilities.${key}`);
  }

  return value as unknown as Model;
}

/**
 * 条件バリアント列 (#201) の列集合契約。
 *
 * 同一 base model の列は (1) `mode` が重複せず (2) models[] 上で隣接している必要がある。
 * site の比較マトリクスは隣接列を base model 名でグループ化するため、
 * この不変条件を build 時に固定する。
 */
function assertConditioningColumns(models: readonly Model[]): void {
  assertUnique(
    models.flatMap((model) =>
      model.conditioning
        ? [JSON.stringify([model.conditioning.base_model, model.conditioning.mode])]
        : [],
    ),
    "manifest model conditioning",
  );
  const seenBaseModels = new Set<string>();
  let previousBaseModel: string | null = null;
  for (const model of models) {
    const baseModel = model.conditioning?.base_model ?? null;
    if (baseModel !== previousBaseModel && baseModel !== null) {
      if (seenBaseModels.has(baseModel)) {
        throw new GayaDataError(
          `manifest models の条件バリアント列が隣接していません: ${baseModel}`,
        );
      }
      seenBaseModels.add(baseModel);
    }
    previousBaseModel = baseModel;
  }
}

/**
 * 条件バリアント列 (#201) の optional `conditioning` field を検証する。
 * 単方式モデルは field ごと存在しないため、旧 manifest もそのまま通る。
 */
function validateConditioning(value: unknown, label: string, modelId: string): void {
  assertRecord(value, label);
  assertExactKeys(value, CONDITIONING_KEYS, [], label);
  assertEnum(value.mode, CONDITIONING_MODES, `${label}.mode`);
  assertPathSegment(value.base_model, `${label}.base_model`);
  if (value.base_model === modelId) {
    throw new GayaDataError(`${label}.base_model は自分自身を指せません: ${modelId}`);
  }
}

function validateCandidate(value: unknown, index: number): Candidate {
  const label = `manifest candidates[${index}]`;
  assertRecord(value, label);
  assertExactKeys(value, CANDIDATE_KEYS, [], label);
  for (const key of GROUP_KEYS) {
    assertPathSegment(value[key], `${label}.${key}`);
  }
  const model = value.model as string;
  const scenario = value.scenario as string;
  const line = value.line as string;
  const variant = value.variant as string;
  assertEnum(variant, VARIANTS, `${label}.variant`);
  assertPositiveInteger(value.take_index, `${label}.take_index`);
  assertSha256(value.take_id, `${label}.take_id`);
  assertSha256(value.sha256, `${label}.sha256`);
  assertSha256(value.generation_input_sha256, `${label}.generation_input_sha256`);
  const expectedTakeId = createHash("sha256")
    .update(
      JSON.stringify({
        final_opus_sha256: value.sha256,
        generation_input_sha256: value.generation_input_sha256,
      }),
    )
    .digest("hex");
  if (value.take_id !== expectedTakeId) {
    throw new GayaDataError(`${label}.take_id が provenance と一致しません。`);
  }
  assertNonNegativeFiniteNumber(value.duration_sec, `${label}.duration_sec`);
  assertNonNegativeFiniteNumber(value.rtf, `${label}.rtf`);
  assertRecord(value.gen_params, `${label}.gen_params`);
  assertExactKeys(value.gen_params, GENERATION_PARAMETER_KEYS, [], `${label}.gen_params`);
  if (
    value.gen_params.seed !== null &&
    (typeof value.gen_params.seed !== "number" || !Number.isSafeInteger(value.gen_params.seed))
  ) {
    throw new GayaDataError(`${label}.gen_params.seed は整数または null が必要です。`);
  }
  assertNonEmptyString(value.gen_params.recipe_version, `${label}.gen_params.recipe_version`);
  assertEnum(
    value.gen_params.recipe_version,
    RECIPE_VERSIONS,
    `${label}.gen_params.recipe_version`,
  );
  for (const key of ["sampling", "requested", "realized"] as const) {
    assertRecord(value.gen_params[key], `${label}.gen_params.${key}`);
  }
  assertRecord(value.loudness, `${label}.loudness`);
  assertExactKeys(value.loudness, LOUDNESS_KEYS, [], `${label}.loudness`);
  assertEnum(value.loudness.source, LOUDNESS_SOURCES, `${label}.loudness.source`);
  assertFiniteNumber(value.loudness.i_lufs, `${label}.loudness.i_lufs`);
  assertFiniteNumber(value.loudness.tp_dbtp, `${label}.loudness.tp_dbtp`);
  assertBoolean(value.loudness.shortfall, `${label}.loudness.shortfall`);
  assertString(value.path, `${label}.path`);
  assertRelativeClipPath(value.path, `${label}.path`);
  const expectedPath =
    `audio/takes/${model}/${scenario}/${line}/${variant}/` +
    `take-${String(value.take_index).padStart(4, "0")}-${value.sha256}.opus`;
  if (value.path !== expectedPath) {
    throw new GayaDataError(`${label}.path が field から再構成できません。`);
  }
  assertRecord(value.gate, `${label}.gate`);
  assertExactKeys(value.gate, GATE_KEYS, [], `${label}.gate`);
  if (value.gate.mechanical !== "pass") {
    throw new GayaDataError(`${label}.gate.mechanical は pass が必要です。`);
  }
  assertEnum(value.gate.content, CONTENT_GATE_RESULTS, `${label}.gate.content`);
  assertNonEmptyString(value.gate.policy_version, `${label}.gate.policy_version`);

  return value as unknown as Candidate;
}

function validateCuration(value: unknown, index: number): Curation {
  const label = `manifest curations[${index}]`;
  assertRecord(value, label);
  if (value.decision === "selected") {
    assertExactKeys(value, SELECTED_CURATION_KEYS, [], label);
    assertSha256(value.take_id, `${label}.take_id`);
  } else if (value.decision === "skipped") {
    assertExactKeys(value, SKIPPED_CURATION_KEYS, [], label);
  } else {
    throw new GayaDataError(`${label}.decision が許可された値ではありません。`);
  }
  for (const key of GROUP_KEYS) {
    assertPathSegment(value[key], `${label}.${key}`);
  }
  assertSha256(value.curation_sha256, `${label}.curation_sha256`);
  return value as unknown as Curation;
}

function validateFailure(value: unknown, index: number): GenerationFailure {
  const label = `manifest failures[${index}]`;
  assertRecord(value, label);
  assertExactKeys(value, FAILURE_KEYS, [], label);
  for (const key of GROUP_KEYS) {
    assertPathSegment(value[key], `${label}.${key}`);
  }
  assertEnum(value.variant, VARIANTS, `${label}.variant`);
  assertEnum(value.reason, FAILURE_REASONS, `${label}.reason`);
  if (value.reason === "test_only_adapter" && value.model !== "dummy") {
    throw new GayaDataError(`${label}.reason=test_only_adapter は model=dummy が必要です。`);
  }

  return value as unknown as GenerationFailure;
}

function loadScenarios(scenariosDirectory: string, watchFile?: WatchFile): readonly Scenario[] {
  let entries;
  try {
    entries = readdirSync(scenariosDirectory, { withFileTypes: true });
  } catch (error) {
    throw new GayaDataError(`scenario ディレクトリを読み込めません: ${scenariosDirectory}`, {
      cause: error,
    });
  }

  const scenarioFiles = entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".yaml"))
    .map((entry) => path.join(scenariosDirectory, entry.name))
    .sort((left, right) => left.localeCompare(right, "en"));
  if (scenarioFiles.length === 0) {
    throw new GayaDataError(`scenario YAML がありません: ${scenariosDirectory}`);
  }

  const scenarios = scenarioFiles.map((scenarioPath) => {
    watchFile?.(scenarioPath);
    return loadScenario(scenarioPath);
  });
  assertUnique(
    scenarios.map((scenario) => scenario.id),
    "scenario id",
  );
  return scenarios;
}

function loadScenario(scenarioPath: string): Scenario {
  const source = readTextFile(scenarioPath, "scenario");
  let value: unknown;
  try {
    value = parse(source);
  } catch (error) {
    throw new GayaDataError(`scenario YAML を解析できません: ${scenarioPath}`, {
      cause: error,
    });
  }

  const label = `scenario ${scenarioPath}`;
  assertRecord(value, label);
  assertExactKeys(value, SCENARIO_REQUIRED_KEYS, SCENARIO_OPTIONAL_KEYS, label);
  assertScenarioVersion(value.format_version, `${label}.format_version`);
  assertId(value.id, `${label}.id`);

  const filenameId = path.basename(scenarioPath, ".yaml");
  if (value.id !== filenameId) {
    throw new GayaDataError(
      `${label}.id はファイル名と一致する必要があります: ${value.id} != ${filenameId}`,
    );
  }

  assertString(value.title, `${label}.title`);
  assertEnum(value.locale, LOCALES, `${label}.locale`);
  if ("tags" in value) {
    assertStringArray(value.tags, `${label}.tags`);
  }
  validateScene(value.scene, label);
  assertArray(value.characters, `${label}.characters`);
  assertArray(value.lines, `${label}.lines`);
  if (value.characters.length === 0) {
    throw new GayaDataError(`${label}.characters は1件以上必要です。`);
  }
  if (value.lines.length === 0) {
    throw new GayaDataError(`${label}.lines は1件以上必要です。`);
  }

  const characters = value.characters.map((character, index) =>
    validateCharacter(character, label, index),
  );
  const characterIds = characters.map((character) => character.id);
  assertUnique(characterIds, `${label} character id`);
  const characterIdSet = new Set(characterIds);

  const lines = value.lines.map((line, index) => validateLine(line, label, index, characterIdSet));
  assertUnique(
    lines.map((line) => line.id),
    `${label} line id`,
  );

  return {
    ...value,
    format_version: 1,
    locale: value.locale as Scenario["locale"],
    scene: value.scene as Scenario["scene"],
    characters,
    lines,
  } as unknown as Scenario;
}

function validateScene(value: unknown, scenarioLabel: string): void {
  const label = `${scenarioLabel}.scene`;
  assertRecord(value, label);
  assertExactKeys(value, SCENE_REQUIRED_KEYS, SCENE_OPTIONAL_KEYS, label);
  assertString(value.setting, `${label}.setting`);
  for (const key of SCENE_OPTIONAL_KEYS) {
    if (key in value) {
      assertString(value[key], `${label}.${key}`);
    }
  }
}

function validateCharacter(
  value: unknown,
  scenarioLabel: string,
  index: number,
): Scenario["characters"][number] {
  const label = `${scenarioLabel}.characters[${index}]`;
  assertRecord(value, label);
  assertExactKeys(value, CHARACTER_REQUIRED_KEYS, CHARACTER_OPTIONAL_KEYS, label);
  assertId(value.id, `${label}.id`);
  assertString(value.name, `${label}.name`);
  if ("kind" in value) {
    assertEnum(value.kind, CHARACTER_KINDS, `${label}.kind`);
  }
  assertEnum(value.gender, GENDERS, `${label}.gender`);
  assertEnum(value.age, AGES, `${label}.age`);
  assertString(value.voice, `${label}.voice`);
  for (const key of ["archetype", "personality"] as const) {
    if (key in value) {
      assertString(value[key], `${label}.${key}`);
    }
  }
  if ("reference_voice" in value && value.reference_voice !== null) {
    assertString(value.reference_voice, `${label}.reference_voice`);
  }

  return {
    ...value,
    kind: "kind" in value ? value.kind : "human",
  } as unknown as Scenario["characters"][number];
}

function validateLine(
  value: unknown,
  scenarioLabel: string,
  index: number,
  characterIds: ReadonlySet<string>,
): Scenario["lines"][number] {
  const label = `${scenarioLabel}.lines[${index}]`;
  assertRecord(value, label);
  assertExactKeys(value, LINE_REQUIRED_KEYS, LINE_OPTIONAL_KEYS, label);
  assertId(value.id, `${label}.id`);
  assertId(value.character, `${label}.character`);
  if (!characterIds.has(value.character)) {
    throw new GayaDataError(
      `${label}.character が存在しない character を参照しています: ${value.character}`,
    );
  }
  assertString(value.text, `${label}.text`);
  if (value.text.length < 1 || value.text.length > 60) {
    throw new GayaDataError(`${label}.text は1〜60文字が必要です。`);
  }
  if ("reading" in value && value.reading !== null) {
    assertString(value.reading, `${label}.reading`);
  }
  assertEnum(value.emotion, EMOTIONS, `${label}.emotion`);
  assertString(value.delivery, `${label}.delivery`);
  if ("situation" in value) {
    assertString(value.situation, `${label}.situation`);
  }
  if ("intensity" in value) {
    if (!Number.isInteger(value.intensity) || ![1, 2, 3].includes(value.intensity as number)) {
      throw new GayaDataError(`${label}.intensity は1、2、3のいずれかが必要です。`);
    }
  }
  if ("difficulty" in value) {
    assertEnum(value.difficulty, DIFFICULTIES, `${label}.difficulty`);
  }
  if ("loop_ok" in value) {
    assertBoolean(value.loop_ok, `${label}.loop_ok`);
  }
  if ("final_intonation" in value) {
    assertEnum(value.final_intonation, FINAL_INTONATIONS, `${label}.final_intonation`);
  }

  return {
    ...value,
    intensity: "intensity" in value ? value.intensity : 2,
    difficulty: "difficulty" in value ? value.difficulty : "standard",
    loop_ok: "loop_ok" in value ? value.loop_ok : true,
    final_intonation: "final_intonation" in value ? value.final_intonation : "fall",
  } as unknown as Scenario["lines"][number];
}

function loadReferenceVoices(metadataPath: string): readonly ReferenceVoiceCredit[] {
  const source = readTextFile(metadataPath, "reference voice metadata");
  let value: unknown;
  try {
    value = parse(source);
  } catch (error) {
    throw new GayaDataError(`reference voice metadata YAML を解析できません: ${metadataPath}`, {
      cause: error,
    });
  }

  const label = `reference voice metadata ${metadataPath}`;
  assertRecord(value, label);
  assertExactKeys(value, VOICE_METADATA_KEYS, [], label);
  if (value.format_version !== 1) {
    throw new GayaDataError(`${label}.format_version は1が必要です。`);
  }
  assertArray(value.voices, `${label}.voices`);
  if (value.voices.length < 5) {
    throw new GayaDataError(`${label}.voices は5件以上必要です。`);
  }

  const voices = value.voices.map((voice, index) =>
    validateReferenceVoice(voice, `${label}.voices[${index}]`),
  );
  assertUnique(
    voices.map((voice) => voice.id),
    `${label} voice id`,
  );
  return voices;
}

function validateReferenceVoice(value: unknown, label: string): ReferenceVoiceCredit {
  assertRecord(value, label);
  assertExactKeys(value, REFERENCE_VOICE_KEYS, [], label);
  assertId(value.id, `${label}.id`);
  assertString(value.file, `${label}.file`);
  if (value.file !== `${value.id}/reference.wav`) {
    throw new GayaDataError(`${label}.file は id/reference.wav が必要です。`);
  }
  assertSha256(value.sha256, `${label}.sha256`);
  assertFiniteNumber(value.duration_sec, `${label}.duration_sec`);
  if ((value.duration_sec as number) < 10 || (value.duration_sec as number) > 20) {
    throw new GayaDataError(`${label}.duration_sec は10〜20秒が必要です。`);
  }
  if (value.language !== "ja") {
    throw new GayaDataError(`${label}.language は ja が必要です。`);
  }
  assertNonEmptyString(value.transcript, `${label}.transcript`);

  assertRecord(value.transcript_rights, `${label}.transcript_rights`);
  assertExactKeys(
    value.transcript_rights,
    TRANSCRIPT_RIGHTS_KEYS,
    [],
    `${label}.transcript_rights`,
  );
  assertNonEmptyString(value.transcript_rights.license, `${label}.transcript_rights.license`);
  assertHttpsUrl(value.transcript_rights.evidence_url, `${label}.transcript_rights.evidence_url`);
  assertNonEmptyString(
    value.transcript_rights.credit_text,
    `${label}.transcript_rights.credit_text`,
  );

  assertRecord(value.source, `${label}.source`);
  assertExactKeys(value.source, VOICE_SOURCE_KEYS, [], `${label}.source`);
  assertNonEmptyString(value.source.title, `${label}.source.title`);
  assertNonEmptyString(value.source.speaker, `${label}.source.speaker`);
  assertHttpsUrl(value.source.download_page, `${label}.source.download_page`);
  assertArray(value.source.files, `${label}.source.files`);
  if (value.source.files.length === 0) {
    throw new GayaDataError(`${label}.source.files は1件以上必要です。`);
  }
  for (const [index, file] of value.source.files.entries()) {
    const fileLabel = `${label}.source.files[${index}]`;
    assertRecord(file, fileLabel);
    assertExactKeys(file, VOICE_SOURCE_FILE_KEYS, [], fileLabel);
    assertNonEmptyString(file.label, `${fileLabel}.label`);
    assertHttpsUrl(file.url, `${fileLabel}.url`);
    assertSha256(file.sha256, `${fileLabel}.sha256`);
  }

  assertRecord(value.rights, `${label}.rights`);
  assertExactKeys(value.rights, VOICE_RIGHTS_KEYS, [], `${label}.rights`);
  assertNonEmptyString(value.rights.license, `${label}.rights.license`);
  assertIsoDate(value.rights.verified_on, `${label}.rights.verified_on`);
  assertHttpsUrl(
    value.rights.voice_synthesis_evidence_url,
    `${label}.rights.voice_synthesis_evidence_url`,
  );
  assertHttpsUrl(
    value.rights.commercial_use_evidence_url,
    `${label}.rights.commercial_use_evidence_url`,
  );
  assertRecord(value.rights.redistribution, `${label}.rights.redistribution`);
  assertExactKeys(
    value.rights.redistribution,
    REDISTRIBUTION_KEYS,
    [],
    `${label}.rights.redistribution`,
  );
  assertEnum(
    value.rights.redistribution.status,
    REDISTRIBUTION_STATUSES,
    `${label}.rights.redistribution.status`,
  );
  assertHttpsUrl(
    value.rights.redistribution.evidence_url,
    `${label}.rights.redistribution.evidence_url`,
  );
  assertNonEmptyString(value.rights.redistribution.notes, `${label}.rights.redistribution.notes`);

  assertNonEmptyString(value.credit_text, `${label}.credit_text`);

  assertRecord(value.voice, `${label}.voice`);
  assertExactKeys(value.voice, VOICE_PROFILE_KEYS, [], `${label}.voice`);
  assertEnum(value.voice.gender, GENDERS, `${label}.voice.gender`);
  assertEnum(value.voice.age, AGES, `${label}.voice.age`);
  assertNonEmptyString(value.voice.notes, `${label}.voice.notes`);

  assertRecord(value.processing, `${label}.processing`);
  assertExactKeys(value.processing, VOICE_PROCESSING_KEYS, [], `${label}.processing`);
  assertNonEmptyString(value.processing.source_member, `${label}.processing.source_member`);
  assertSha256(value.processing.source_sha256, `${label}.processing.source_sha256`);
  assertNonEmptyString(value.processing.summary, `${label}.processing.summary`);

  return value as unknown as ReferenceVoiceCredit;
}

function projectModelCredits(manifest: Manifest): readonly ModelCredit[] {
  return manifest.models.map((model) => {
    const candidates = manifest.candidates.filter((candidate) => candidate.model === model.id);
    if (model.id === "dummy") {
      if (candidates.length > 0) {
        throw new GayaDataError("dummy model は candidate を持てません。");
      }
      return { model: model.id, sources: [] };
    }
    if (candidates.length === 0) {
      throw new GayaDataError(
        `model ${model.id} の provenance を取得できる candidate がありません。`,
      );
    }

    // 条件バリアント列は base model と同一 checkpoint なので、provenance 契約は base id で引く。
    const provenanceId = model.conditioning?.base_model ?? model.id;
    const sources = extractModelSources(candidates[0]!, model.id, provenanceId);
    if (sources.length === 0) {
      throw new GayaDataError(`model ${model.id} のコード・ウェイト provenance がありません。`);
    }
    const expected = JSON.stringify(sources);
    for (const candidate of candidates.slice(1)) {
      const actual = JSON.stringify(extractModelSources(candidate, model.id, provenanceId));
      if (actual !== expected) {
        throw new GayaDataError(`model ${model.id} の candidate 間で provenance が一致しません。`);
      }
    }
    return { model: model.id, sources };
  });
}

function extractModelSources(
  candidate: Candidate,
  modelId: string,
  provenanceId: string = modelId,
): readonly ModelSourceLink[] {
  const requested = candidate.gen_params.requested;
  assertRecord(requested, `model ${modelId} requested provenance`);
  if (provenanceId === AIVIS_MODEL_ID) {
    return extractAivisSources(requested);
  }
  if (provenanceId === IRODORI_MODEL_ID) {
    return extractIrodoriSources(requested);
  }
  if (provenanceId === IRODORI_V4_MODEL_ID) {
    return extractIrodoriV4Sources(requested);
  }

  const sources: ModelSourceLink[] = [];
  for (const field of MODEL_SOURCE_FIELDS) {
    const repositoryValue = requested[field.repository];
    const revisionValue = requested[field.revision];
    const repositoryPresent = repositoryValue !== undefined;
    const revisionPresent = revisionValue !== undefined;
    if (repositoryPresent !== revisionPresent) {
      throw new GayaDataError(
        `model ${modelId} の ${field.repository}/${field.revision} は対で必要です。`,
      );
    }
    if (!repositoryPresent) {
      continue;
    }
    assertRepository(repositoryValue, `model ${modelId} ${field.repository}`);
    assertSha1(revisionValue, `model ${modelId} ${field.revision}`);
    const baseUrl = field.host === "github" ? "https://github.com" : "https://huggingface.co";
    sources.push({
      kind: field.kind,
      label: field.label,
      repository: repositoryValue,
      revision: revisionValue,
      url: `${baseUrl}/${repositoryValue}/tree/${revisionValue}`,
    });
  }
  return sources;
}

function extractAivisSources(requested: UnknownRecord): readonly ModelSourceLink[] {
  assertExactValue(requested.engine, "AivisSpeech Engine", "AivisSpeech engine");
  assertExactValue(requested.engine_version, AIVIS_ENGINE_VERSION, "AivisSpeech engine_version");
  assertExactValue(
    requested.engine_manifest_version,
    AIVIS_ENGINE_MANIFEST_VERSION,
    "AivisSpeech engine_manifest_version",
  );
  assertExactValue(
    requested.engine_manifest_uuid,
    AIVIS_ENGINE_MANIFEST_UUID,
    "AivisSpeech engine_manifest_uuid",
  );
  assertExactValue(requested.model_name, AIVIS_MODEL_NAME, "AivisSpeech model_name");
  assertExactValue(requested.model_uuid, AIVIS_MODEL_UUID, "AivisSpeech model_uuid");
  assertExactValue(requested.model_version, AIVIS_MODEL_VERSION, "AivisSpeech model_version");
  assertExactValue(requested.model_sha256, AIVIS_MODEL_SHA256, "AivisSpeech model_sha256");
  assertExactValue(requested.model_license, AIVIS_MODEL_LICENSE, "AivisSpeech model_license");
  return [
    {
      kind: "code",
      label: `AivisSpeech Engine ${AIVIS_ENGINE_VERSION}`,
      repository: "Aivis-Project/AivisSpeech-Engine",
      revision: AIVIS_ENGINE_VERSION,
      url: `https://github.com/Aivis-Project/AivisSpeech-Engine/releases/tag/${AIVIS_ENGINE_VERSION}`,
    },
    {
      kind: "weights",
      label: `${AIVIS_MODEL_NAME} ${AIVIS_MODEL_VERSION}`,
      repository: `AivisHub/aivm-models/${AIVIS_MODEL_UUID}`,
      revision: `${AIVIS_MODEL_VERSION}@sha256:${AIVIS_MODEL_SHA256}`,
      url: `https://hub.aivis-project.com/aivm-models/${AIVIS_MODEL_UUID}`,
    },
  ];
}

function extractIrodoriSources(requested: UnknownRecord): readonly ModelSourceLink[] {
  assertExactValue(
    requested.upstream_revision,
    IRODORI_UPSTREAM_REVISION,
    "Irodori upstream_revision",
  );
  assertExactValue(requested.checkpoint, IRODORI_CHECKPOINT, "Irodori checkpoint");
  assertExactValue(
    requested.checkpoint_revision,
    IRODORI_CHECKPOINT_REVISION,
    "Irodori checkpoint_revision",
  );
  return [
    {
      kind: "code",
      label: "コード",
      repository: IRODORI_UPSTREAM_REPOSITORY,
      revision: IRODORI_UPSTREAM_REVISION,
      url: `https://github.com/${IRODORI_UPSTREAM_REPOSITORY}/tree/${IRODORI_UPSTREAM_REVISION}`,
    },
    {
      kind: "weights",
      label: "VoiceDesign ウェイト",
      repository: IRODORI_CHECKPOINT,
      revision: IRODORI_CHECKPOINT_REVISION,
      url: `https://huggingface.co/${IRODORI_CHECKPOINT}/tree/${IRODORI_CHECKPOINT_REVISION}`,
    },
  ];
}

function extractIrodoriV4Sources(requested: UnknownRecord): readonly ModelSourceLink[] {
  assertExactValue(
    requested.upstream_revision,
    IRODORI_V4_UPSTREAM_REVISION,
    "Irodori v4 upstream_revision",
  );
  assertExactValue(requested.checkpoint, IRODORI_V4_CHECKPOINT, "Irodori v4 checkpoint");
  assertExactValue(
    requested.checkpoint_revision,
    IRODORI_V4_CHECKPOINT_REVISION,
    "Irodori v4 checkpoint_revision",
  );
  return [
    {
      kind: "code",
      label: "コード",
      repository: IRODORI_UPSTREAM_REPOSITORY,
      revision: IRODORI_V4_UPSTREAM_REVISION,
      url: `https://github.com/${IRODORI_UPSTREAM_REPOSITORY}/tree/${IRODORI_V4_UPSTREAM_REVISION}`,
    },
    {
      kind: "weights",
      label: "v4-Small ウェイト",
      repository: IRODORI_V4_CHECKPOINT,
      revision: IRODORI_V4_CHECKPOINT_REVISION,
      url: `https://huggingface.co/${IRODORI_V4_CHECKPOINT}/tree/${IRODORI_V4_CHECKPOINT_REVISION}`,
    },
  ];
}

function assertExactValue(value: unknown, expected: string, label: string): void {
  if (value !== expected) {
    throw new GayaDataError(`${label} は ${expected} が必要です。`);
  }
}

function validateReferences(
  manifest: Manifest,
  scenarios: readonly Scenario[],
  referenceVoices: readonly ReferenceVoiceCredit[],
): void {
  const modelIds = new Set(manifest.models.map((model) => model.id));
  const referenceVoiceIds = new Set(referenceVoices.map((voice) => voice.id));
  const linesByScenario = new Map(
    scenarios.map((scenario) => [scenario.id, new Set(scenario.lines.map((line) => line.id))]),
  );

  for (const scenario of scenarios) {
    for (const character of scenario.characters) {
      if (character.reference_voice && !referenceVoiceIds.has(character.reference_voice)) {
        throw new GayaDataError(
          `scenario ${scenario.id} の character ${character.id} が存在しない reference_voice を参照しています: ${character.reference_voice}`,
        );
      }
    }
  }

  for (const candidate of manifest.candidates) {
    const key = artifactKeyTuple(candidate);
    if (!modelIds.has(candidate.model)) {
      throw new GayaDataError(
        `candidate ${key} が存在しない model を参照しています: ${candidate.model}`,
      );
    }
    const lineIds = linesByScenario.get(candidate.scenario);
    if (!lineIds) {
      throw new GayaDataError(
        `candidate ${key} が存在しない scenario を参照しています: ${candidate.scenario}`,
      );
    }
    if (!lineIds.has(candidate.line)) {
      throw new GayaDataError(
        `candidate ${key} が存在しない line を参照しています: ${candidate.line}`,
      );
    }
  }

  for (const failure of manifest.failures) {
    const key = artifactKeyTuple(failure);
    if (!modelIds.has(failure.model)) {
      throw new GayaDataError(
        `failure ${key} が存在しない model を参照しています: ${failure.model}`,
      );
    }
    const lineIds = linesByScenario.get(failure.scenario);
    if (!lineIds) {
      throw new GayaDataError(
        `failure ${key} が存在しない scenario を参照しています: ${failure.scenario}`,
      );
    }
    if (!lineIds.has(failure.line)) {
      throw new GayaDataError(`failure ${key} が存在しない line を参照しています: ${failure.line}`);
    }
  }
}

function assertRecord(value: unknown, label: string): asserts value is UnknownRecord {
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    Object.getPrototypeOf(value) !== Object.prototype
  ) {
    throw new GayaDataError(`${label} は object が必要です。`);
  }
}

function assertArray(value: unknown, label: string): asserts value is unknown[] {
  if (!Array.isArray(value)) {
    throw new GayaDataError(`${label} は配列が必要です。`);
  }
}

function assertExactKeys(
  value: UnknownRecord,
  required: readonly string[],
  optional: readonly string[],
  label: string,
): void {
  const keys = Object.keys(value);
  const allowed = new Set([...required, ...optional]);
  const missing = required.filter((key) => !Object.hasOwn(value, key));
  const unknown = keys.filter((key) => !allowed.has(key));
  if (missing.length > 0 || unknown.length > 0) {
    const details = [
      missing.length > 0 ? `不足: ${missing.join(", ")}` : "",
      unknown.length > 0 ? `未知: ${unknown.join(", ")}` : "",
    ]
      .filter(Boolean)
      .join("; ");
    throw new GayaDataError(`${label} の項目が一致しません (${details})。`);
  }
}

function assertManifestVersion(value: unknown): asserts value is 4 {
  if (value !== 4) {
    throw new GayaDataError(`manifest format_version は4が必要です。`);
  }
}

function assertScenarioVersion(value: unknown, label: string): asserts value is 1 {
  if (value !== 1) {
    throw new GayaDataError(`${label} は1が必要です。`);
  }
}

function assertString(value: unknown, label: string): asserts value is string {
  if (typeof value !== "string") {
    throw new GayaDataError(`${label} は文字列が必要です。`);
  }
}

function assertBoolean(value: unknown, label: string): asserts value is boolean {
  if (typeof value !== "boolean") {
    throw new GayaDataError(`${label} は bool が必要です。`);
  }
}

function assertNonEmptyString(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  if (value.length === 0) {
    throw new GayaDataError(`${label} は空でない文字列が必要です。`);
  }
}

function assertId(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  if (!ID_PATTERN.test(value)) {
    throw new GayaDataError(`${label} は kebab-case id が必要です。`);
  }
}

function assertPathSegment(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  if (
    value.length === 0 ||
    value === "." ||
    value === ".." ||
    value.includes("/") ||
    value.includes("\\")
  ) {
    throw new GayaDataError(`${label} は安全な path segment が必要です。`);
  }
}

function assertSha256(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  if (!SHA256_PATTERN.test(value)) {
    throw new GayaDataError(`${label} は完全な小文字 SHA-256 が必要です。`);
  }
}

function assertSha1(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  if (!SHA1_PATTERN.test(value)) {
    throw new GayaDataError(`${label} は完全な小文字 Git SHA-1 が必要です。`);
  }
}

function assertRepository(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  if (!REPOSITORY_PATTERN.test(value)) {
    throw new GayaDataError(`${label} は owner/repository が必要です。`);
  }
}

function assertHttpsUrl(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch (error) {
    throw new GayaDataError(`${label} は有効な HTTPS URL が必要です。`, { cause: error });
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
    throw new GayaDataError(`${label} は有効な HTTPS URL が必要です。`);
  }
}

function assertIsoDate(value: unknown, label: string): asserts value is string {
  assertString(value, label);
  if (
    !ISO_DATE_PATTERN.test(value) ||
    new Date(`${value}T00:00:00.000Z`).toISOString().slice(0, 10) !== value
  ) {
    throw new GayaDataError(`${label} は有効な YYYY-MM-DD が必要です。`);
  }
}

function assertPositiveInteger(value: unknown, label: string): asserts value is number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new GayaDataError(`${label} は1以上の整数が必要です。`);
  }
}

function assertEnum(
  value: unknown,
  allowed: ReadonlySet<string>,
  label: string,
): asserts value is string {
  assertString(value, label);
  if (!allowed.has(value)) {
    throw new GayaDataError(`${label} が許可された値ではありません: ${value}`);
  }
}

function assertStringArray(value: unknown, label: string): asserts value is string[] {
  assertArray(value, label);
  for (const [index, item] of value.entries()) {
    assertString(item, `${label}[${index}]`);
  }
}

function assertNonNegativeFiniteNumber(value: unknown, label: string): void {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new GayaDataError(`${label} は0以上の有限数が必要です。`);
  }
}

function assertFiniteNumber(value: unknown, label: string): void {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new GayaDataError(`${label} は有限数である必要があります`);
  }
}

function assertUnique(values: readonly string[], label: string): void {
  const seen = new Set<string>();
  for (const value of values) {
    if (seen.has(value)) {
      throw new GayaDataError(`${label} が重複しています: ${value}`);
    }
    seen.add(value);
  }
}

function assertRelativeClipPath(value: string, label: string): void {
  if (!isSafeClipPath(value)) {
    throw new GayaDataError(`${label} は安全な相対パスが必要です: ${value}`);
  }
}

function artifactKeyTuple(
  artifact: Pick<
    Candidate | Curation | GenerationFailure,
    "model" | "scenario" | "line" | "variant"
  >,
): string {
  return JSON.stringify([artifact.model, artifact.scenario, artifact.line, artifact.variant]);
}

function assertManifestGroups(
  candidates: readonly Candidate[],
  curations: readonly Curation[],
  failures: readonly GenerationFailure[],
  modelIds: ReadonlySet<string>,
): void {
  const candidatesByGroup = new Map<string, Candidate[]>();
  const candidatesByTakeId = new Map(candidates.map((candidate) => [candidate.take_id, candidate]));
  for (const candidate of candidates) {
    if (!modelIds.has(candidate.model)) {
      throw new GayaDataError(`candidate が未知の model を参照しています: ${candidate.model}`);
    }
    const key = artifactKeyTuple(candidate);
    const groupCandidates = candidatesByGroup.get(key);
    if (groupCandidates) {
      groupCandidates.push(candidate);
    } else {
      candidatesByGroup.set(key, [candidate]);
    }
  }
  for (const curation of curations) {
    const key = artifactKeyTuple(curation);
    const groupCandidates = candidatesByGroup.get(key);
    if (!groupCandidates) {
      throw new GayaDataError(`curation に対応する candidate group がありません: ${key}`);
    }
    if (curation.decision === "selected") {
      const selected = candidatesByTakeId.get(curation.take_id);
      if (!selected || artifactKeyTuple(selected) !== key) {
        throw new GayaDataError(
          `selected curation が同一 group の take を参照していません: ${key}`,
        );
      }
    }
  }
  const candidateKeys = new Set(candidatesByGroup.keys());
  for (const failure of failures) {
    if (!modelIds.has(failure.model)) {
      throw new GayaDataError(`failure が未知の model を参照しています: ${failure.model}`);
    }
    const key = artifactKeyTuple(failure);
    if (candidateKeys.has(key)) {
      throw new GayaDataError(`manifest の candidate/failure group が競合しています: ${key}`);
    }
  }
}

function projectOutcomes(
  manifest: Manifest,
  qualitySignals: QualitySignals,
  referenceVoices: readonly ReferenceVoiceCredit[],
): readonly ArtifactOutcome[] {
  const modelsById = new Map(manifest.models.map((model) => [model.id, model]));
  const referenceVoicesById = new Map(referenceVoices.map((voice) => [voice.id, voice]));
  const candidatesByGroup = new Map<
    string,
    {
      readonly group: {
        readonly model: string;
        readonly scenario: string;
        readonly line: string;
        readonly variant: string;
      };
      readonly candidates: Candidate[];
    }
  >();
  for (const candidate of manifest.candidates) {
    const key = artifactKeyTuple(candidate);
    const entry = candidatesByGroup.get(key);
    if (entry) {
      entry.candidates.push(candidate);
    } else {
      candidatesByGroup.set(key, {
        group: {
          model: candidate.model,
          scenario: candidate.scenario,
          line: candidate.line,
          variant: candidate.variant,
        },
        candidates: [candidate],
      });
    }
  }
  const curationsByGroup = new Map(
    manifest.curations.map((curation) => [artifactKeyTuple(curation), curation]),
  );
  const qualitySignalsByGroup = new Map(
    qualitySignals.groups.map((signal) => [artifactKeyTuple(signal), signal]),
  );
  const consumedQualitySignals = new Set<string>();
  const outcomes: ArtifactOutcome[] = [];
  for (const [key, { group, candidates }] of candidatesByGroup) {
    const curation = curationsByGroup.get(key);
    if (!curation) {
      outcomes.push({ kind: "uncurated", group });
      continue;
    }
    if (curation.decision === "skipped") {
      outcomes.push({ kind: "skipped", group });
      continue;
    }
    const candidate = candidates.find((item) => item.take_id === curation.take_id);
    if (!candidate) {
      throw new GayaDataError(`selected curation の take_id が candidate に存在しません: ${key}`);
    }
    const signal = qualitySignalsByGroup.get(key);
    if (signal) {
      consumedQualitySignals.add(key);
    }
    const model = modelsById.get(candidate.model);
    if (!model) {
      throw new GayaDataError(
        `selected candidate が未知の model を参照しています: ${candidate.model}`,
      );
    }
    outcomes.push({
      kind: "selected",
      group,
      candidate: projectPublishedCandidate(
        candidate,
        signal
          ? {
              expected_gender: signal.expected_gender,
              median_f0_hz: signal.median_f0_hz,
              status: signal.status,
              signal: signal.signal,
            }
          : null,
        model,
        referenceVoicesById,
      ),
    });
  }
  for (const failure of manifest.failures) {
    outcomes.push({
      kind: "failure",
      group: {
        model: failure.model,
        scenario: failure.scenario,
        line: failure.line,
        variant: failure.variant,
      },
      failure,
    });
  }
  if (consumedQualitySignals.size !== qualitySignalsByGroup.size) {
    const extras = [...qualitySignalsByGroup.keys()].filter(
      (key) => !consumedQualitySignals.has(key),
    );
    throw new GayaDataError(`quality signal がselected groupに存在しません: ${extras.join(", ")}`);
  }
  return outcomes;
}

function readTextFile(file: string, label: string): string {
  try {
    return readFileSync(file, "utf8");
  } catch (error) {
    throw new GayaDataError(`${label} を読み込めません: ${file}`, {
      cause: error,
    });
  }
}
