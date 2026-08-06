/// <reference types="node" />

import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vite-plus/test";

import { loadBenchmarkData } from "../../scripts/gaya-data-plugin.ts";

const temporaryRoots: string[] = [];

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("virtual:gaya-data integration", () => {
  it("固定 release を strict v4 / selected-only index として公開する", async () => {
    const {
      benchmarkData,
      candidateKey,
      getOutcomesForScenario,
      lineByKey,
      modelCreditById,
      modelById,
      referenceVoiceById,
      releaseModelById,
      selectedCandidates,
    } = await import("./index");

    expect(benchmarkData.release.format_version).toBe(4);
    const expectedSlots = benchmarkData.release.models.length * 161;
    expect(selectedCandidates).toHaveLength(expectedSlots);
    expect(benchmarkData.outcomes.filter(({ kind }) => kind === "skipped")).toHaveLength(0);
    expect(benchmarkData.outcomes.filter(({ kind }) => kind === "failure")).toHaveLength(0);
    expect(
      benchmarkData.generation_profiles.reduce(
        (count, profile) => count + profile.candidate_count,
        0,
      ),
    ).toBe(expectedSlots);
    expect("manifest" in benchmarkData).toBe(false);
    expect(Object.keys(selectedCandidates[0]!).sort()).toEqual([
      "duration_sec",
      "gate",
      "line",
      "model",
      "path",
      "reference_conditioning",
      "role_quality",
      "rtf",
      "scenario",
      "variant",
    ]);
    expect(releaseModelById.has("dummy")).toBe(false);
    expect(modelById.has("dummy")).toBe(false);
    expect(benchmarkData.credits.model_sources).toHaveLength(benchmarkData.release.models.length);
    expect(benchmarkData.credits.reference_voices).toHaveLength(5);
    expect(modelCreditById.has("dummy")).toBe(false);
    expect(modelCreditById.get("aivisspeech-kohaku")?.sources).toHaveLength(2);
    expect(modelCreditById.get("irodori-tts-600m-v3-voicedesign")?.sources).toHaveLength(2);
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
    expect(selected?.kind === "selected" ? selected.candidate.path : null).toContain("take-0002");
    expect(selected?.kind === "selected" ? selected.candidate.role_quality : null).toMatchObject({
      expected_gender: "female",
      status: "pass",
    });
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

  it("Irodori v4-Smallの固定code・checkpointをcreditsへ投影する", () => {
    const manifest = manifestForModel("irodori-tts-v4-small", {
      checkpoint: "Aratako/Irodori-TTS-v4-Small",
      checkpoint_revision: "e4aaac4df355ff560dcd35e0dae272c3a759317b",
      upstream_revision: "8ca3acb58ab4e19ad6d594aaed6bafe3e88f7f71",
    });

    const sources = loadBenchmarkData(createFixture(manifest)).credits.model_sources[0]?.sources;

    expect(sources).toEqual([
      {
        kind: "code",
        label: "コード",
        repository: "Aratako/Irodori-TTS",
        revision: "8ca3acb58ab4e19ad6d594aaed6bafe3e88f7f71",
        url:
          "https://github.com/Aratako/Irodori-TTS/tree/" +
          "8ca3acb58ab4e19ad6d594aaed6bafe3e88f7f71",
      },
      {
        kind: "weights",
        label: "v4-Small ウェイト",
        repository: "Aratako/Irodori-TTS-v4-Small",
        revision: "e4aaac4df355ff560dcd35e0dae272c3a759317b",
        url:
          "https://huggingface.co/Aratako/Irodori-TTS-v4-Small/tree/" +
          "e4aaac4df355ff560dcd35e0dae272c3a759317b",
      },
    ]);

    manifest.candidates[0]!.gen_params.requested.checkpoint = "Aratako/Irodori-TTS-600M-v3";
    expect(() => loadBenchmarkData(createFixture(manifest))).toThrow("Irodori v4 checkpoint");
  });

  it("Irodori v4-Smallの方式・参照 receipt はsite側の model 分岐なしで編入される", () => {
    const manifest = manifestForModel("irodori-tts-v4-small", {
      checkpoint: "Aratako/Irodori-TTS-v4-Small",
      checkpoint_revision: "e4aaac4df355ff560dcd35e0dae272c3a759317b",
      upstream_revision: "8ca3acb58ab4e19ad6d594aaed6bafe3e88f7f71",
    });
    manifest.models[0]!.capabilities.voice_prompt = true;
    manifest.models[0]!.capabilities.clone = true;
    manifest.candidates[1]!.gen_params.realized = {
      reference_control: "character-stable-reference-audio-v1",
      reference_source: "selected-role-anchor",
      reference_voice: null,
      reference_sha256: "b".repeat(64),
      reference_caption: "若い成人の男性。低く落ち着いた男性の声。",
      reference_text: "Sample",
      selected_anchor: validSelectedAnchor("b".repeat(64)),
    };

    const data = loadBenchmarkData(createFixture(manifest));
    const model = data.release.models.find(({ id }) => id === "irodori-tts-v4-small");

    // 方式バッジは capabilities からのみ導出される (#178)。
    expect(model?.capabilities.voice_prompt).toBe(true);
    expect(selectedOutcome(data).reference_conditioning).toEqual({
      kind: "model_generated_reference",
      inference_reference_sha256: "b".repeat(64),
      source_kind: "selected-role-anchor",
    });
  });

  it("条件バリアント列を base model の provenance で credits へ投影する", () => {
    const manifest = variantManifest();

    const data = loadBenchmarkData(
      createFixture(
        manifest,
        validScenario(),
        validVoiceMetadata(),
        validQualitySignals("irodori-tts-v4-small--ref"),
      ),
    );

    expect(data.release.models.map(({ id }) => id)).toEqual([
      "irodori-tts-v4-small--ref",
      "irodori-tts-v4-small--text",
    ]);
    expect(data.release.models[0]?.conditioning).toEqual({
      mode: "human-reference",
      base_model: "irodori-tts-v4-small",
    });
    expect(data.release.models[1]?.conditioning).toEqual({
      mode: "text-only",
      base_model: "irodori-tts-v4-small",
    });
    // base id ではなく variant id で credits を引けること、中身は base の provenance であること。
    expect(data.credits.model_sources.map(({ model }) => model)).toEqual([
      "irodori-tts-v4-small--ref",
      "irodori-tts-v4-small--text",
    ]);
    for (const credit of data.credits.model_sources) {
      expect(credit.sources.map(({ kind }) => kind)).toEqual(["code", "weights"]);
      expect(credit.sources[1]?.repository).toBe("Aratako/Irodori-TTS-v4-Small");
    }
  });

  it("単方式モデルの manifest は conditioning なしのまま受け入れる", () => {
    const data = loadBenchmarkData(createFixture());

    expect(data.release.models[0]?.conditioning).toBeUndefined();
    expect("conditioning" in data.release.models[0]!).toBe(false);
  });

  it.each([
    {
      name: "未知の内部 key",
      mutate: (manifest: MutableManifest) => {
        manifest.models[0]!.conditioning = {
          mode: "human-reference",
          base_model: "irodori-tts-v4-small",
          note: "extra",
        };
      },
      message: "manifest models[0].conditioning の項目が一致しません",
    },
    {
      name: "未知の mode",
      mutate: (manifest: MutableManifest) => {
        manifest.models[0]!.conditioning = {
          mode: "voice-design",
          base_model: "irodori-tts-v4-small",
        };
      },
      message: "manifest models[0].conditioning.mode が許可された値ではありません",
    },
    {
      name: "空の base_model",
      mutate: (manifest: MutableManifest) => {
        manifest.models[0]!.conditioning = { mode: "human-reference", base_model: "" };
      },
      message: "manifest models[0].conditioning.base_model は安全な path segment",
    },
    {
      name: "自分自身を指す base_model",
      mutate: (manifest: MutableManifest) => {
        manifest.models[0]!.conditioning = {
          mode: "human-reference",
          base_model: "irodori-tts-v4-small--ref",
        };
      },
      message: "base_model は自分自身を指せません",
    },
    {
      name: "同一 base の mode 重複",
      mutate: (manifest: MutableManifest) => {
        manifest.models[1]!.conditioning = {
          mode: "human-reference",
          base_model: "irodori-tts-v4-small",
        };
      },
      message: "manifest model conditioning が重複しています",
    },
  ])("不正な conditioning ($name) を build 時に拒否する", ({ mutate, message }) => {
    const manifest = variantManifest();
    mutate(manifest);
    expect(() =>
      loadBenchmarkData(
        createFixture(
          manifest,
          validScenario(),
          validVoiceMetadata(),
          validQualitySignals("irodori-tts-v4-small--ref"),
        ),
      ),
    ).toThrow(message);
  });

  it("同一 base model の列が離れている manifest を拒否する", () => {
    const manifest = variantManifest();
    const [variantRef, variantText] = [manifest.models[0]!, manifest.models[1]!];
    const single = structuredClone(variantRef);
    single.id = "aivisspeech-kohaku";
    single.name = "Single";
    delete single.conditioning;
    manifest.models = [variantRef, single, variantText];

    expect(() =>
      loadBenchmarkData(
        createFixture(
          manifest,
          validScenario(),
          validVoiceMetadata(),
          validQualitySignals("irodori-tts-v4-small--ref"),
        ),
      ),
    ).toThrow("条件バリアント列が隣接していません: irodori-tts-v4-small");
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

  describe("reference conditioning projection", () => {
    it.each([
      {
        name: "direct clone",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_voice: "sample-voice",
          reference_sha256: "a".repeat(64),
          reference_selection_source: "character.reference_voice",
        },
        expected: {
          kind: "human_reference",
          voice_id: "sample-voice",
          asset_sha256: "a".repeat(64),
          inference_reference_sha256: "a".repeat(64),
          selection_source: "character.reference_voice",
        },
      },
      {
        name: "CosyVoice direct clone with reference length receipt",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_voice: "sample-voice",
          reference_sha256: "a".repeat(64),
          reference_selection_source: "adapter.assignment:sample/speaker",
          reference_samples: 537_000,
          reference_duration_sec: 11.1875,
        },
        expected: {
          kind: "human_reference",
          voice_id: "sample-voice",
          asset_sha256: "a".repeat(64),
          inference_reference_sha256: "a".repeat(64),
          selection_source: "adapter.assignment:sample/speaker",
        },
      },
      {
        name: "GPT-SoVITS clip",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_voice: "sample-voice",
          reference_source_sha256: "a".repeat(64),
          reference_clip_sha256: "b".repeat(64),
          reference_selection_source: "adapter.assignment:sample/speaker",
          reference_clip_frame_count: 240_000,
          reference_clip_start_frame: 0,
        },
        expected: {
          kind: "human_reference",
          voice_id: "sample-voice",
          asset_sha256: "a".repeat(64),
          inference_reference_sha256: "b".repeat(64),
          selection_source: "adapter.assignment:sample/speaker",
        },
      },
      {
        name: "VoxCPM asset",
        capabilities: { voice_prompt: true, clone: true },
        realized: {
          reference_kind: "asset",
          reference_voice: "sample-voice",
          reference_sha256: "a".repeat(64),
          reference_selection_source: "character.reference_voice",
        },
        expected: {
          kind: "human_reference",
          voice_id: "sample-voice",
          asset_sha256: "a".repeat(64),
          inference_reference_sha256: "a".repeat(64),
          selection_source: "character.reference_voice",
        },
      },
      {
        name: "VoxCPM voice design",
        capabilities: { voice_prompt: true, clone: true },
        realized: {
          reference_kind: "voice_design",
          reference_voice: null,
          reference_sha256: "b".repeat(64),
          reference_selection_source: "adapter.voice_design",
        },
        expected: {
          kind: "model_generated_reference",
          inference_reference_sha256: "b".repeat(64),
          source_kind: "voice_design",
        },
      },
      {
        name: "Qwen voice asset",
        capabilities: { voice_prompt: true, clone: true },
        realized: {
          character_identity: { scenario: "sample", character: "speaker" },
          reference_control: "voice_asset",
          reference_source_id: "sample-voice",
          reference_sha256: "a".repeat(64),
          reference_text: "Sample",
        },
        expected: {
          kind: "human_reference",
          voice_id: "sample-voice",
          asset_sha256: "a".repeat(64),
          inference_reference_sha256: "a".repeat(64),
          selection_source: "voice_asset",
        },
      },
      {
        name: "Qwen selected voice design anchor",
        capabilities: { voice_prompt: true, clone: true },
        realized: {
          character_identity: { scenario: "sample", character: "speaker" },
          reference_control: "selected_voice_design_anchor",
          reference_source_id: "f".repeat(64),
          reference_sha256: "b".repeat(64),
          reference_text: "Sample",
          selected_anchor: validSelectedAnchor("b".repeat(64)),
        },
        expected: {
          kind: "model_generated_reference",
          inference_reference_sha256: "b".repeat(64),
          source_kind: "selected_voice_design_anchor",
        },
      },
      {
        name: "Irodori voice asset",
        capabilities: { voice_prompt: true, clone: true },
        realized: {
          reference_control: "character-stable-reference-audio-v1",
          reference_source: "voice-asset",
          reference_voice: "sample-voice",
          reference_sha256: "a".repeat(64),
          reference_caption: null,
          reference_text: null,
        },
        expected: {
          kind: "human_reference",
          voice_id: "sample-voice",
          asset_sha256: "a".repeat(64),
          inference_reference_sha256: "a".repeat(64),
          selection_source: "voice-asset",
        },
      },
      {
        name: "Irodori selected role anchor",
        capabilities: { voice_prompt: true, clone: true },
        realized: {
          reference_control: "character-stable-reference-audio-v1",
          reference_source: "selected-role-anchor",
          reference_voice: null,
          reference_sha256: "b".repeat(64),
          reference_caption: "Role caption",
          reference_text: "Sample",
          selected_anchor: validSelectedAnchor("b".repeat(64)),
        },
        expected: {
          kind: "model_generated_reference",
          inference_reference_sha256: "b".repeat(64),
          source_kind: "selected-role-anchor",
        },
      },
      {
        name: "preset none",
        capabilities: { voice_prompt: false, clone: false },
        realized: {},
        expected: { kind: "none" },
      },
    ])("$name の selected realized だけを compact contract へ投影する", (testCase) => {
      const manifest = manifestWithSelectedReference(testCase.capabilities, testCase.realized);
      const selected = selectedOutcome(loadBenchmarkData(createFixture(manifest)));

      expect(selected.reference_conditioning).toEqual(testCase.expected);
    });

    it("requested-only は参照使用を推測せず none とする", () => {
      const manifest = manifestWithSelectedReference({ voice_prompt: true, clone: true }, {});
      manifest.candidates[1]!.gen_params.requested.reference_voice = "sample-voice";
      manifest.candidates[1]!.gen_params.requested.reference_sha256 = "a".repeat(64);

      expect(
        selectedOutcome(loadBenchmarkData(createFixture(manifest))).reference_conditioning,
      ).toEqual({ kind: "none" });
    });

    it("非選択 candidate の realized は公開 contract へ投影しない", () => {
      const manifest = manifestWithSelectedReference({ voice_prompt: false, clone: false }, {});
      manifest.candidates[0]!.gen_params.realized = {
        reference_kind: "unknown",
      };

      expect(
        selectedOutcome(loadBenchmarkData(createFixture(manifest))).reference_conditioning,
      ).toEqual({ kind: "none" });
    });

    it.each([
      {
        name: "unknown discriminator",
        capabilities: { voice_prompt: true, clone: true },
        realized: { reference_kind: "unknown" },
        message: "未知の tag",
      },
      {
        name: "partial direct receipt",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_voice: "sample-voice",
          reference_sha256: "a".repeat(64),
        },
        message: "reference_selection_source",
      },
      {
        name: "CosyVoice reference length receipt missing duration",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_voice: "sample-voice",
          reference_sha256: "a".repeat(64),
          reference_selection_source: "adapter.assignment:sample/speaker",
          reference_samples: 537_000,
        },
        message: "reference_duration_sec",
      },
      {
        name: "CosyVoice reference length receipt missing samples",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_voice: "sample-voice",
          reference_sha256: "a".repeat(64),
          reference_selection_source: "adapter.assignment:sample/speaker",
          reference_duration_sec: 11.1875,
        },
        message: "reference_samples",
      },
      {
        name: "CosyVoice invalid reference samples",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_voice: "sample-voice",
          reference_sha256: "a".repeat(64),
          reference_selection_source: "adapter.assignment:sample/speaker",
          reference_samples: 0,
          reference_duration_sec: 11.1875,
        },
        message: "reference_samples は1以上の整数",
      },
      {
        name: "CosyVoice invalid reference duration",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_voice: "sample-voice",
          reference_sha256: "a".repeat(64),
          reference_selection_source: "adapter.assignment:sample/speaker",
          reference_samples: 537_000,
          reference_duration_sec: 0,
        },
        message: "reference_duration_sec は0より大きい",
      },
      {
        name: "GPT reference clip receipt missing start frame",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_voice: "sample-voice",
          reference_source_sha256: "a".repeat(64),
          reference_clip_sha256: "b".repeat(64),
          reference_selection_source: "adapter.assignment:sample/speaker",
          reference_clip_frame_count: 240_000,
        },
        message: "reference_clip_start_frame",
      },
      {
        name: "GPT reference clip receipt missing frame count",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_voice: "sample-voice",
          reference_source_sha256: "a".repeat(64),
          reference_clip_sha256: "b".repeat(64),
          reference_selection_source: "adapter.assignment:sample/speaker",
          reference_clip_start_frame: 0,
        },
        message: "reference_clip_frame_count",
      },
      {
        name: "conflicting discriminators",
        capabilities: { voice_prompt: true, clone: true },
        realized: {
          reference_kind: "asset",
          reference_source: "voice-asset",
        },
        message: "receipt が競合",
      },
      {
        name: "unknown voice id",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_voice: "missing-voice",
          reference_sha256: "a".repeat(64),
          reference_selection_source: "character.reference_voice",
        },
        message: "未知の参照音声",
      },
      {
        name: "catalog hash mismatch",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_voice: "sample-voice",
          reference_sha256: "b".repeat(64),
          reference_selection_source: "character.reference_voice",
        },
        message: "catalog と一致",
      },
      {
        name: "preset with reference",
        capabilities: { voice_prompt: false, clone: false },
        realized: {
          reference_voice: "sample-voice",
          reference_sha256: "a".repeat(64),
          reference_selection_source: "character.reference_voice",
        },
        message: "プリセット話者",
      },
      {
        name: "clone without reference",
        capabilities: { voice_prompt: false, clone: true },
        realized: {},
        message: "human_reference ではありません",
      },
      {
        name: "clone with model-generated reference",
        capabilities: { voice_prompt: false, clone: true },
        realized: {
          reference_kind: "voice_design",
          reference_voice: null,
          reference_sha256: "b".repeat(64),
          reference_selection_source: "adapter.voice_design",
        },
        message: "human_reference ではありません",
      },
      {
        name: "selected anchor audio hash mismatch",
        capabilities: { voice_prompt: true, clone: true },
        realized: {
          character_identity: { scenario: "sample", character: "speaker" },
          reference_control: "selected_voice_design_anchor",
          reference_source_id: "f".repeat(64),
          reference_sha256: "b".repeat(64),
          reference_text: "Sample",
          selected_anchor: validSelectedAnchor("c".repeat(64)),
        },
        message: "reference_sha256 と一致",
      },
      {
        name: "Qwen anchor id mismatch",
        capabilities: { voice_prompt: true, clone: true },
        realized: {
          character_identity: { scenario: "sample", character: "speaker" },
          reference_control: "selected_voice_design_anchor",
          reference_source_id: "e".repeat(64),
          reference_sha256: "b".repeat(64),
          reference_text: "Sample",
          selected_anchor: validSelectedAnchor("b".repeat(64)),
        },
        message: "anchor_id と一致",
      },
      {
        name: "human receipt with selected anchor",
        capabilities: { voice_prompt: true, clone: true },
        realized: {
          character_identity: { scenario: "sample", character: "speaker" },
          reference_control: "voice_asset",
          reference_source_id: "sample-voice",
          reference_sha256: "a".repeat(64),
          reference_text: "Sample",
          selected_anchor: validSelectedAnchor("a".repeat(64)),
        },
        message: "selected_anchor はこの参照音声 receipt",
      },
      {
        name: "Irodori contract version mismatch",
        capabilities: { voice_prompt: true, clone: true },
        realized: {
          reference_control: "old-contract",
          reference_source: "voice-asset",
          reference_voice: "sample-voice",
          reference_sha256: "a".repeat(64),
          reference_caption: null,
          reference_text: null,
        },
        message: "character-stable-reference-audio-v1",
      },
      {
        name: "unknown reference field",
        capabilities: { voice_prompt: true, clone: true },
        realized: { reference_fallback: "sample-voice" },
        message: "未知の参照音声 field",
      },
    ])("$name を build 時に拒否する", (testCase) => {
      const manifest = manifestWithSelectedReference(testCase.capabilities, testCase.realized);
      expect(() => loadBenchmarkData(createFixture(manifest))).toThrow(testCase.message);
    });
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

  it("公開UIで扱えない variant と生成レシピを build 時に拒否する", () => {
    const variant = validManifest();
    variant.candidates[0]!.variant = "wet";
    expect(() => loadBenchmarkData(createFixture(variant))).toThrow(
      "manifest candidates[0].variant が許可された値ではありません",
    );

    const failureVariant = validManifest();
    failureVariant.failures[0]!.variant = "wet";
    expect(() => loadBenchmarkData(createFixture(failureVariant))).toThrow(
      "manifest failures[0].variant が許可された値ではありません",
    );

    const recipe = validManifest();
    recipe.candidates[0]!.gen_params.recipe_version = "seed-only-v2";
    expect(() => loadBenchmarkData(createFixture(recipe))).toThrow(
      "manifest candidates[0].gen_params.recipe_version が許可された値ではありません",
    );
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
    conditioning?: Record<string, unknown>;
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
  qualitySignals = validQualitySignals(manifest.models[0]!.id),
): string {
  const root = createEmptyRoot();
  mkdirSync(path.join(root, "data"), { recursive: true });
  mkdirSync(path.join(root, "scenarios"), { recursive: true });
  mkdirSync(path.join(root, "assets", "voices"), { recursive: true });
  writeFileSync(path.join(root, "data", "manifest.json"), JSON.stringify(manifest), "utf8");
  writeFileSync(
    path.join(root, "data", "quality-signals.json"),
    JSON.stringify(qualitySignals),
    "utf8",
  );
  writeFileSync(path.join(root, "scenarios", "sample.yaml"), scenario, "utf8");
  writeFileSync(path.join(root, "assets", "voices", "metadata.yaml"), voiceMetadata, "utf8");
  return root;
}

function validQualitySignals(model = "model") {
  return {
    format_version: 1,
    protocol: "role-quality-signals-v1",
    plan_sha256: "a".repeat(64),
    decision_sha256: "b".repeat(64),
    groups: [
      {
        model,
        scenario: "sample",
        line: "speaker-001",
        variant: "dry",
        protocol: "role-gender-f0-soft-v1",
        expected_gender: "female",
        median_f0_hz: 200,
        status: "pass",
        signal: null,
        qc_report_sha256: "c".repeat(64),
      },
    ],
  };
}

function validManifest(): MutableManifest {
  const selectedFirst = candidate("speaker-001", 1, "1");
  const selectedSecond = candidate("speaker-001", 2, "2");
  const skipped = candidate("speaker-002", 1, "3");
  const uncurated = candidate("speaker-003", 1, "4");
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
        line: "speaker-002",
        variant: "dry",
        decision: "skipped",
        curation_sha256: "c".repeat(64),
      },
    ],
    failures: [
      {
        model: "model",
        scenario: "sample",
        line: "speaker-004",
        variant: "dry",
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

/**
 * 条件バリアント列 (#201) の manifest fixture。
 * 同一 checkpoint の base model から `--ref` / `--text` の 2 列を派生させる。
 */
function variantManifest(): MutableManifest {
  const baseModel = "irodori-tts-v4-small";
  const manifest = manifestForModel(`${baseModel}--ref`, {
    checkpoint: "Aratako/Irodori-TTS-v4-Small",
    checkpoint_revision: "e4aaac4df355ff560dcd35e0dae272c3a759317b",
    upstream_revision: "8ca3acb58ab4e19ad6d594aaed6bafe3e88f7f71",
  });
  manifest.models[0]!.name = "Irodori-TTS v4-Small（見本あり）";
  manifest.models[0]!.conditioning = { mode: "human-reference", base_model: baseModel };

  const textModelId = `${baseModel}--text`;
  const textModel = structuredClone(manifest.models[0]!);
  textModel.id = textModelId;
  textModel.name = "Irodori-TTS v4-Small（見本なし）";
  textModel.conditioning = { mode: "text-only", base_model: baseModel };
  manifest.models.push(textModel);

  const takeIdByGroup = new Map<string, string>();
  const textCandidates = manifest.candidates.map((source, index) => {
    const candidate = retargetCandidate(source, textModelId, index + 1);
    takeIdByGroup.set(`${candidate.line}/${candidate.take_index}`, candidate.take_id);
    return candidate;
  });
  manifest.candidates.push(...textCandidates);
  manifest.curations.push(
    ...manifest.curations.map((curation) => ({
      ...structuredClone(curation),
      model: textModelId,
      ...(curation.take_id === undefined
        ? {}
        : { take_id: takeIdByGroup.get(`${curation.line}/2`)! }),
    })),
  );
  manifest.failures.push(
    ...manifest.failures.map((failure) => ({ ...structuredClone(failure), model: textModelId })),
  );
  return manifest;
}

function retargetCandidate(
  source: MutableCandidate,
  modelId: string,
  seed: number,
): MutableCandidate {
  const audioSha = seed.toString(16).padStart(64, "0");
  const generationInputSha = (seed + 0x1000).toString(16).padStart(64, "0");
  const takeId = createHash("sha256")
    .update(
      JSON.stringify({
        final_opus_sha256: audioSha,
        generation_input_sha256: generationInputSha,
      }),
    )
    .digest("hex");
  return {
    ...structuredClone(source),
    model: modelId,
    sha256: audioSha,
    generation_input_sha256: generationInputSha,
    take_id: takeId,
    path:
      `audio/takes/${modelId}/${source.scenario}/${source.line}/${source.variant}/` +
      `take-${String(source.take_index).padStart(4, "0")}-${audioSha}.opus`,
  };
}

function manifestWithSelectedReference(
  capabilities: { voice_prompt: boolean; clone: boolean },
  realized: Record<string, unknown>,
): MutableManifest {
  const manifest = validManifest();
  manifest.models[0]!.capabilities.voice_prompt = capabilities.voice_prompt;
  manifest.models[0]!.capabilities.clone = capabilities.clone;
  manifest.candidates[1]!.gen_params.realized = structuredClone(realized);
  return manifest;
}

function selectedOutcome(data: ReturnType<typeof loadBenchmarkData>) {
  const outcome = data.outcomes.find(({ kind }) => kind === "selected");
  if (!outcome || outcome.kind !== "selected") {
    throw new Error("fixture selected outcome がありません。");
  }
  return outcome.candidate;
}

function validSelectedAnchor(referenceSha: string): Record<string, unknown> {
  return {
    anchor_selection_sha256: "1".repeat(64),
    anchor_plan_sha256: "2".repeat(64),
    anchor_candidate_set_sha256: "3".repeat(64),
    anchor_id: "f".repeat(64),
    anchor_attempt: 1,
    anchor_seed: 0,
    anchor_audio_sha256: referenceSha,
    anchor_text_sha256: "4".repeat(64),
    anchor_decision_sha256: "5".repeat(64),
    role_identity_sha256: "6".repeat(64),
    role_epoch_sha256: "7".repeat(64),
  };
}

function candidate(line: string, takeIndex: number, marker: string): MutableCandidate {
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
    line,
    variant: "dry",
    take_index: takeIndex,
    take_id: takeId,
    path:
      `audio/takes/model/sample/${line}/dry/` +
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
  - id: speaker-002
    character: speaker
    text: Skip
    emotion: neutral
    delivery: Plain
  - id: speaker-003
    character: speaker
    text: Pending
    emotion: neutral
    delivery: Plain
  - id: speaker-004
    character: speaker
    text: Failure
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
