/// <reference types="node" />

import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vite-plus/test";

import { GayaDataError, loadBenchmarkData } from "../../scripts/gaya-data-plugin.ts";
import {
  benchmarkData,
  clipKey,
  getClipsForScenario,
  lineByKey,
  modelById,
  scenarioById,
} from "./index";

const temporaryRoots: string[] = [];

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("virtual:gaya-data integration", () => {
  it("実データから安定した index と selector を公開する", () => {
    expect(benchmarkData.manifest.clips).toHaveLength(12);
    expect(scenarioById.has("market-day")).toBe(true);
    expect(modelById.get("dummy")?.name).toBe("Dummy Beep");
    expect(lineByKey.has("market-day/fruit-vendor-001")).toBe(true);

    const clips = getClipsForScenario("market-day");
    expect(clips).toHaveLength(6);
    expect(clipKey(clips[0]!)).toBe(
      JSON.stringify(["dummy", "market-day", "fruit-vendor-001", "dry"]),
    );
    expect(() => getClipsForScenario("missing")).toThrow("未知の scenario id です: missing");
  });
});

describe("loadBenchmarkData", () => {
  it("schema 明示の line defaults だけを補完する", () => {
    const root = createFixture();
    const data = loadBenchmarkData(root);

    expect(data.scenarios[0]?.lines[0]).toMatchObject({
      id: "speaker-001",
      intensity: 2,
      difficulty: "standard",
      loop_ok: true,
    });
    expect(data.manifest.clips).toHaveLength(1);
  });

  it("manifest の未知フィールドを拒否する", () => {
    const root = createFixture();
    const manifest = validManifest();
    Object.assign(manifest, { legacy_format: true });
    writeManifest(root, manifest);

    expect(() => loadBenchmarkData(root)).toThrowError(
      new GayaDataError("manifest の項目が v1 と一致しません (未知: legacy_format)。"),
    );
  });

  it("重複 model id と clip key を拒否する", () => {
    const root = createFixture();
    const manifest = validManifest();
    manifest.models.push({ ...manifest.models[0]! });
    writeManifest(root, manifest);

    expect(() => loadBenchmarkData(root)).toThrow("manifest model id が重複しています");

    manifest.models.pop();
    manifest.clips.push({ ...manifest.clips[0]! });
    writeManifest(root, manifest);
    expect(() => loadBenchmarkData(root)).toThrow("manifest clip key が重複しています");
  });

  it("scenario のファイル名、character 参照、clip 参照を検証する", () => {
    const root = createFixture({ scenarioFilename: "wrong-name.yaml" });
    expect(() => loadBenchmarkData(root)).toThrow("id はファイル名と一致");

    const characterRoot = createFixture({
      scenario: validScenario().replace("character: speaker", "character: missing"),
    });
    expect(() => loadBenchmarkData(characterRoot)).toThrow("存在しない character を参照");

    const clipRoot = createFixture();
    const manifest = validManifest();
    manifest.clips[0]!.line = "missing";
    writeManifest(clipRoot, manifest);
    expect(() => loadBenchmarkData(clipRoot)).toThrow("存在しない line を参照");
  });

  it.each([
    "https://example.com/audio.opus",
    "/absolute/audio.opus",
    "../outside.opus",
    String.raw`audio\..\outside.opus`,
    String.raw`audio\dummy\clip.opus`,
    "audio//clip.opus",
    "audio/./clip.opus",
    "audio/%2e%2e/clip.opus",
  ])("安全でない clip path を拒否する: %s", (clipPath) => {
    const root = createFixture();
    const manifest = validManifest();
    manifest.clips[0]!.path = clipPath;
    writeManifest(root, manifest);

    expect(() => loadBenchmarkData(root)).toThrow("安全な相対パス");
  });

  it("不足ファイルと壊れた YAML を fail fast する", () => {
    const missingRoot = createEmptyRoot();
    expect(() => loadBenchmarkData(missingRoot)).toThrow("manifest を読み込めません");

    const brokenRoot = createFixture({ scenario: "format_version: [" });
    expect(() => loadBenchmarkData(brokenRoot)).toThrow("scenario YAML を解析できません");
  });
});

interface FixtureOptions {
  readonly scenario?: string;
  readonly scenarioFilename?: string;
}

interface MutableModel {
  id: string;
  name: string;
  version: string;
  license_note: string;
  capabilities: {
    emotion: boolean;
    voice_prompt: boolean;
    clone: boolean;
    nonverbal: boolean;
    reading: boolean;
  };
}

interface MutableClip {
  model: string;
  scenario: string;
  line: string;
  variant: string;
  path: string;
  duration_sec: number;
  sha256: string;
  gen_params: Record<string, never>;
  rtf: number;
}

interface MutableManifest {
  format_version: number;
  generated_at: string;
  models: MutableModel[];
  clips: MutableClip[];
}

function createEmptyRoot(): string {
  const root = mkdtempSync(path.join(tmpdir(), "gaya-data-"));
  temporaryRoots.push(root);
  return root;
}

function createFixture(options: FixtureOptions = {}): string {
  const root = createEmptyRoot();
  mkdirSync(path.join(root, "data"), { recursive: true });
  mkdirSync(path.join(root, "scenarios"), { recursive: true });
  writeManifest(root, validManifest());
  writeFileSync(
    path.join(root, "scenarios", options.scenarioFilename ?? "sample.yaml"),
    options.scenario ?? validScenario(),
    "utf8",
  );
  return root;
}

function writeManifest(root: string, manifest: MutableManifest): void {
  mkdirSync(path.join(root, "data"), { recursive: true });
  writeFileSync(path.join(root, "data", "manifest.json"), JSON.stringify(manifest), "utf8");
}

function validManifest(): MutableManifest {
  return {
    format_version: 1,
    generated_at: "2026-07-28T00:00:00Z",
    models: [
      {
        id: "model",
        name: "Model",
        version: "1",
        license_note: "test",
        capabilities: {
          emotion: false,
          voice_prompt: false,
          clone: false,
          nonverbal: false,
          reading: false,
        },
      },
    ],
    clips: [
      {
        model: "model",
        scenario: "sample",
        line: "speaker-001",
        variant: "dry",
        path: "audio/model/sample/speaker-001-dry.opus",
        duration_sec: 1,
        sha256: "hash",
        gen_params: {},
        rtf: 0.1,
      },
    ],
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
