/// <reference types="node" />

import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vite-plus/test";

import { loadBenchmarkData } from "../../scripts/gaya-data-plugin.ts";
import {
  benchmarkData,
  candidateKey,
  getOutcomesForScenario,
  lineByKey,
  manifestModelById,
  modelCreditById,
  modelById,
  referenceVoiceById,
  selectedCandidates,
} from "./index";

const temporaryRoots: string[] = [];

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("virtual:gaya-data integration", () => {
  it("固定 release を strict v4 / selected-only index として公開する", () => {
    expect(benchmarkData.manifest.format_version).toBe(4);
    expect(benchmarkData.manifest.candidates).toHaveLength(220);
    expect(selectedCandidates).toHaveLength(166);
    expect(benchmarkData.outcomes.filter(({ kind }) => kind === "skipped")).toHaveLength(54);
    expect(benchmarkData.outcomes.filter(({ kind }) => kind === "failure")).toHaveLength(161);
    expect(manifestModelById.has("dummy")).toBe(true);
    expect(modelById.has("dummy")).toBe(false);
    expect(benchmarkData.credits.model_sources).toHaveLength(benchmarkData.manifest.models.length);
    expect(benchmarkData.credits.reference_voices).toHaveLength(5);
    expect(modelCreditById.get("dummy")?.sources).toEqual([]);
    expect(modelCreditById.get("chatterbox-multilingual-v3")?.sources.length).toBeGreaterThan(0);
    expect(referenceVoiceById.has("tsukuyomi-corpus-94")).toBe(true);
    expect(lineByKey.has("market-day/fruit-vendor-001")).toBe(true);
    expect(candidateKey(selectedCandidates[0]!)).toContain('"dry"');
    expect(getOutcomesForScenario("market-day").length).toBeGreaterThan(0);
    expect(() => getOutcomesForScenario("missing")).toThrow("未知の scenario id");
  });
});

describe("loadBenchmarkData v4", () => {
  it("selected exact join、skipped、uncurated、logical failure を四態へ投影する", () => {
    const data = loadBenchmarkData(createFixture());

    expect(data.outcomes.map(({ kind }) => kind)).toEqual([
      "selected",
      "skipped",
      "uncurated",
      "failure",
    ]);
    const selected = data.outcomes.find(({ kind }) => kind === "selected");
    expect(selected?.kind === "selected" ? selected.candidate.take_index : null).toBe(2);
    expect(data.scenarios[0]?.characters[0]?.kind).toBe("human");
    expect(data.scenarios[0]?.lines[0]).toMatchObject({
      intensity: 2,
      difficulty: "standard",
      loop_ok: true,
      final_intonation: "fall",
    });
    expect(data.credits.model_sources[0]?.sources[0]).toMatchObject({
      kind: "code",
      repository: "owner/repository",
      revision: "a".repeat(40),
    });
    expect(data.credits.reference_voices[0]?.id).toBe("sample-voice");
  });

  it("selected curation の take_id 欠落と同 group 外参照を拒否し、先頭候補へ fallback しない", () => {
    const missing = validManifest();
    delete missing.curations[0]!.take_id;
    expect(() => loadBenchmarkData(createFixture(missing))).toThrow(
      "manifest curations[0] の項目が一致しません",
    );

    const wrong = validManifest();
    wrong.curations[0]!.take_id = "f".repeat(64);
    expect(() => loadBenchmarkData(createFixture(wrong))).toThrow(
      "selected curation が同一 group の take を参照していません",
    );
  });

  it("v4 exact keys、candidate provenance/path、candidate/failure 互斥を強制する", () => {
    const legacy = validManifest();
    Object.assign(legacy, { clips: [] });
    expect(() => loadBenchmarkData(createFixture(legacy))).toThrow("manifest の項目が一致");

    const badTake = validManifest();
    badTake.candidates[0]!.take_id = "a".repeat(64);
    expect(() => loadBenchmarkData(createFixture(badTake))).toThrow("take_id が provenance と一致");

    const conflict = validManifest();
    conflict.failures[0] = {
      model: "model",
      scenario: "sample",
      line: "speaker-001",
      variant: "dry",
      reason: "no_eligible_take",
    };
    expect(() => loadBenchmarkData(createFixture(conflict))).toThrow(
      "candidate/failure group が競合",
    );
  });

  it("model provenance の欠落・candidate 間の不一致を fail fast する", () => {
    const missing = validManifest();
    missing.candidates[0]!.gen_params.requested = {};
    expect(() => loadBenchmarkData(createFixture(missing))).toThrow(
      "コード・ウェイト provenance がありません",
    );

    const inconsistent = validManifest();
    inconsistent.candidates[1]!.gen_params.requested.upstream_revision = "b".repeat(40);
    expect(() => loadBenchmarkData(createFixture(inconsistent))).toThrow(
      "candidate 間で provenance が一致しません",
    );
  });

  it("AivisSpeechの固定Engine・コハクprovenanceをcreditsへ投影する", () => {
    const manifest = manifestForModel("aivisspeech-kohaku", {
      engine: "AivisSpeech Engine",
      engine_manifest_uuid: "1b4a5014-d9fd-11ee-b97d-83c170a68ed3",
      engine_manifest_version: "0.13.1",
      engine_version: "1.2.0",
      model_license: "ACML-1.0",
      model_name: "コハク",
      model_sha256: "3f5c08b52bb8a64efd361268580c81510f96c927cd6905aa7dbae6851333270a",
      model_uuid: "22e8ed77-94fe-4ef2-871f-a86f94e9a579",
      model_version: "1.1.0",
    });

    const sources = loadBenchmarkData(createFixture(manifest)).credits.model_sources[0]?.sources;

    expect(sources).toEqual([
      {
        kind: "code",
        label: "AivisSpeech Engine 1.2.0",
        repository: "Aivis-Project/AivisSpeech-Engine",
        revision: "1.2.0",
        url: "https://github.com/Aivis-Project/AivisSpeech-Engine/releases/tag/1.2.0",
      },
      {
        kind: "weights",
        label: "コハク 1.1.0",
        repository: "AivisHub/aivm-models/22e8ed77-94fe-4ef2-871f-a86f94e9a579",
        revision: "1.1.0@sha256:3f5c08b52bb8a64efd361268580c81510f96c927cd6905aa7dbae6851333270a",
        url: "https://hub.aivis-project.com/aivm-models/" + "22e8ed77-94fe-4ef2-871f-a86f94e9a579",
      },
    ]);

    manifest.candidates[0]!.gen_params.requested.model_sha256 = "f".repeat(64);
    expect(() => loadBenchmarkData(createFixture(manifest))).toThrow("AivisSpeech model_sha256");
  });

  it("Irodoriの固定code・VoiceDesign checkpointをcreditsへ投影する", () => {
    const manifest = manifestForModel("irodori-tts-600m-v3-voicedesign", {
      checkpoint: "Aratako/Irodori-TTS-600M-v3-VoiceDesign",
      checkpoint_revision: "e863a3a93e652e09afeff3e84823a206a0a60314",
      upstream_revision: "eaf74d6a19138f743acb5b71a445fd25a57db987",
    });

    const sources = loadBenchmarkData(createFixture(manifest)).credits.model_sources[0]?.sources;

    expect(sources).toEqual([
      {
        kind: "code",
        label: "コード",
        repository: "Aratako/Irodori-TTS",
        revision: "eaf74d6a19138f743acb5b71a445fd25a57db987",
        url:
          "https://github.com/Aratako/Irodori-TTS/tree/" +
          "eaf74d6a19138f743acb5b71a445fd25a57db987",
      },
      {
        kind: "weights",
        label: "VoiceDesign ウェイト",
        repository: "Aratako/Irodori-TTS-600M-v3-VoiceDesign",
        revision: "e863a3a93e652e09afeff3e84823a206a0a60314",
        url:
          "https://huggingface.co/Aratako/Irodori-TTS-600M-v3-VoiceDesign/tree/" +
          "e863a3a93e652e09afeff3e84823a206a0a60314",
      },
    ]);

    delete manifest.candidates[0]!.gen_params.requested.checkpoint_revision;
    expect(() => loadBenchmarkData(createFixture(manifest))).toThrow("Irodori checkpoint_revision");
  });

  it("reference voice metadata を exact validation し scenario 参照と結合する", () => {
    const unknownReference = validScenario().replace(
      "voice: Clear",
      "voice: Clear\n    reference_voice: missing-voice",
    );
    expect(() => loadBenchmarkData(createFixture(validManifest(), unknownReference))).toThrow(
      "存在しない reference_voice",
    );

    const unknownKey = validVoiceMetadata().replace(
      "format_version: 1",
      "format_version: 1\nlegacy_credits: true",
    );
    expect(() =>
      loadBenchmarkData(createFixture(validManifest(), validScenario(), unknownKey)),
    ).toThrow("reference voice metadata");

    const duplicate = validVoiceMetadata().replaceAll("sample-voice-2", "sample-voice");
    expect(() =>
      loadBenchmarkData(createFixture(validManifest(), validScenario(), duplicate)),
    ).toThrow("voice id が重複");
  });

  it.each([
    ["generated_at", (manifest: MutableManifest) => (manifest.generated_at = "")],
    ["model.name", (manifest: MutableManifest) => (manifest.models[0]!.name = "")],
    ["model.version", (manifest: MutableManifest) => (manifest.models[0]!.version = "")],
    [
      "recipe_version",
      (manifest: MutableManifest) => (manifest.candidates[0]!.gen_params.recipe_version = ""),
    ],
    [
      "gate.policy_version",
      (manifest: MutableManifest) => (manifest.candidates[0]!.gate.policy_version = ""),
    ],
  ])("%s の空文字を拒否する", (_label, mutate) => {
    const manifest = validManifest();
    mutate(manifest);
    expect(() => loadBenchmarkData(createFixture(manifest))).toThrow("空でない文字列");
  });

  it("不足ファイルと壊れた YAML を fail fast する", () => {
    const missingRoot = createEmptyRoot();
    expect(() => loadBenchmarkData(missingRoot)).toThrow("manifest を読み込めません");

    const brokenRoot = createFixture(validManifest(), "format_version: [");
    expect(() => loadBenchmarkData(brokenRoot)).toThrow("scenario YAML を解析できません");
  });
});

interface MutableManifest {
  format_version: number;
  generated_at: string;
  candidate_set_sha256: string;
  models: Array<{
    id: string;
    name: string;
    version: string;
    license_note: string;
    capabilities: Record<string, boolean>;
  }>;
  candidates: MutableCandidate[];
  curations: Array<{
    model: string;
    scenario: string;
    line: string;
    variant: string;
    decision: string;
    take_id?: string;
    curation_sha256: string;
  }>;
  failures: Array<{
    model: string;
    scenario: string;
    line: string;
    variant: string;
    reason: string;
  }>;
}

interface MutableCandidate {
  model: string;
  scenario: string;
  line: string;
  variant: string;
  take_index: number;
  take_id: string;
  path: string;
  duration_sec: number;
  sha256: string;
  generation_input_sha256: string;
  gen_params: {
    seed: number | null;
    recipe_version: string;
    sampling: Record<string, unknown>;
    requested: Record<string, unknown>;
    realized: Record<string, unknown>;
  };
  rtf: number;
  loudness: {
    source: string;
    i_lufs: number;
    tp_dbtp: number;
    shortfall: boolean;
  };
  gate: {
    mechanical: string;
    content: string;
    policy_version: string;
  };
}

function createEmptyRoot(): string {
  const root = mkdtempSync(path.join(tmpdir(), "gaya-data-"));
  temporaryRoots.push(root);
  return root;
}

function createFixture(
  manifest = validManifest(),
  scenario = validScenario(),
  voiceMetadata = validVoiceMetadata(),
): string {
  const root = createEmptyRoot();
  mkdirSync(path.join(root, "data"), { recursive: true });
  mkdirSync(path.join(root, "scenarios"), { recursive: true });
  mkdirSync(path.join(root, "assets", "voices"), { recursive: true });
  writeFileSync(path.join(root, "data", "manifest.json"), JSON.stringify(manifest), "utf8");
  writeFileSync(path.join(root, "scenarios", "sample.yaml"), scenario, "utf8");
  writeFileSync(path.join(root, "assets", "voices", "metadata.yaml"), voiceMetadata, "utf8");
  return root;
}

function validManifest(): MutableManifest {
  const selectedFirst = candidate("dry", 1, "1");
  const selectedSecond = candidate("dry", 2, "2");
  const skipped = candidate("skipped", 1, "3");
  const uncurated = candidate("uncurated", 1, "4");
  return {
    format_version: 4,
    generated_at: "2026-07-30T00:00:00Z",
    candidate_set_sha256: "d".repeat(64),
    models: [
      {
        id: "model",
        name: "Model",
        version: "1",
        license_note: "MIT",
        capabilities: {
          emotion: false,
          voice_prompt: false,
          clone: false,
          nonverbal: false,
          reading: false,
        },
      },
    ],
    candidates: [selectedFirst, selectedSecond, skipped, uncurated],
    curations: [
      {
        model: "model",
        scenario: "sample",
        line: "speaker-001",
        variant: "dry",
        decision: "selected",
        take_id: selectedSecond.take_id,
        curation_sha256: "c".repeat(64),
      },
      {
        model: "model",
        scenario: "sample",
        line: "speaker-001",
        variant: "skipped",
        decision: "skipped",
        curation_sha256: "c".repeat(64),
      },
    ],
    failures: [
      {
        model: "model",
        scenario: "sample",
        line: "speaker-001",
        variant: "failed",
        reason: "no_eligible_take",
      },
    ],
  };
}

function manifestForModel(modelId: string, requested: Record<string, unknown>): MutableManifest {
  const manifest = validManifest();
  manifest.models[0]!.id = modelId;
  for (const candidate of manifest.candidates) {
    candidate.model = modelId;
    candidate.path = candidate.path.replace("audio/takes/model/", `audio/takes/${modelId}/`);
    candidate.gen_params.requested = structuredClone(requested);
  }
  for (const curation of manifest.curations) {
    curation.model = modelId;
  }
  for (const failure of manifest.failures) {
    failure.model = modelId;
  }
  return manifest;
}

function candidate(variant: string, takeIndex: number, marker: string): MutableCandidate {
  const generationInputSha = marker.repeat(64);
  const audioSha = (Number(marker) + 4).toString().repeat(64);
  const takeId = createHash("sha256")
    .update(
      JSON.stringify({
        final_opus_sha256: audioSha,
        generation_input_sha256: generationInputSha,
      }),
    )
    .digest("hex");
  return {
    model: "model",
    scenario: "sample",
    line: "speaker-001",
    variant,
    take_index: takeIndex,
    take_id: takeId,
    path:
      `audio/takes/model/sample/speaker-001/${variant}/` +
      `take-${String(takeIndex).padStart(4, "0")}-${audioSha}.opus`,
    duration_sec: 1,
    sha256: audioSha,
    generation_input_sha256: generationInputSha,
    gen_params: {
      seed: takeIndex,
      recipe_version: "seed-only-v1",
      sampling: {},
      requested: {
        upstream_repository: "owner/repository",
        upstream_revision: "a".repeat(40),
      },
      realized: {},
    },
    rtf: 0.1,
    loudness: {
      source: "encoded_opus",
      i_lufs: -18,
      tp_dbtp: -1,
      shortfall: false,
    },
    gate: {
      mechanical: "pass",
      content: "review_required",
      policy_version: "take-gates-v2",
    },
  };
}

function validScenario(): string {
  return `format_version: 1
id: sample
title: Sample
locale: ja
scene:
  setting: Test
characters:
  - id: speaker
    name: Speaker
    gender: neutral
    age: adult
    voice: Clear
lines:
  - id: speaker-001
    character: speaker
    text: Hello
    emotion: neutral
    delivery: Plain
`;
}

function validVoiceMetadata(): string {
  const voice = (id: string, marker: string) => `  - id: ${id}
    file: ${id}/reference.wav
    sha256: ${marker.repeat(64)}
    duration_sec: 12
    language: ja
    transcript: Sample
    transcript_rights:
      license: CC0
      evidence_url: https://example.com/transcript
      credit_text: Transcript credit
    source:
      title: Sample source ${id}
      speaker: Speaker
      download_page: https://example.com/source
      files:
        - label: source.wav
          url: https://example.com/source.wav
          sha256: ${marker.repeat(64)}
    rights:
      license: CC BY 4.0
      verified_on: "2026-07-30"
      voice_synthesis_evidence_url: https://example.com/synthesis
      commercial_use_evidence_url: https://example.com/commercial
      redistribution:
        status: allowed_with_conditions
        evidence_url: https://example.com/redistribution
        notes: Credit required
    credit_text: Voice credit
    voice:
      gender: neutral
      age: adult
      notes: Test voice
    processing:
      source_member: source.wav
      source_sha256: ${marker.repeat(64)}
      summary: Converted to WAV.
`;
  return `format_version: 1
voices:
${voice("sample-voice", "a")}${voice("sample-voice-2", "b")}${voice("sample-voice-3", "c")}${voice("sample-voice-4", "d")}${voice("sample-voice-5", "e")}`;
}
