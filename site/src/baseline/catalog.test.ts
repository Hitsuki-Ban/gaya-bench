import { describe, expect, it } from "vite-plus/test";

import { canonicalJson } from "@/lib/canonical-json";
import type { DirectoryFile, ObjectUrlFactory } from "@/lib/local-directory";
import { sha256Hex, sha256Text } from "@/lib/sha256";
import { loadBaselineCatalog } from "./catalog";

const INPUT_SHA = "a".repeat(64);

describe("loadBaselineCatalog", () => {
  it("pipeline assemble実出力のplan/provenance/source-runs/candidate.pathを完全検証する", async () => {
    const fixture = await makeFixture();
    const urls = new FakeObjectUrls();
    const catalog = await loadBaselineCatalog(fixture.files, urls);

    expect(
      fixture.files.some((file) =>
        file.webkitRelativePath.includes("/audio/takes/model-0/scene/line/v000/"),
      ),
    ).toBe(true);
    expect(
      fixture.files.some(
        (file) => file.webkitRelativePath === "run/audio/model-0/scene/line/v000/take-0001.opus",
      ),
    ).toBe(false);
    expect(catalog.candidateSetSha256).toBe(fixture.candidateSetSha256);
    expect(catalog.baselineReferenceSha256).toBe(fixture.baselineReferenceSha256);
    expect(catalog.groups).toHaveLength(1);
    expect(catalog.auditedNoCandidateCount).toBe(380);
    expect(catalog.groups[0]).toMatchObject({
      candidateSha256: fixture.candidateAudioSha256,
      reference: {
        sha256: fixture.candidateAudioSha256,
        comparison: "identical",
      },
    });
    expect(urls.created).toHaveLength(2);

    catalog.dispose();
    catalog.dispose();
    expect(urls.revoked).toEqual(urls.created);
  });

  it("candidate.pathの実Fileを内部curate view越しにもBlob authorityとして渡す", async () => {
    const fixture = await makeFixture();
    const files = await Promise.all(
      fixture.files.map(async (file) => {
        const browserFile = new File([await file.arrayBuffer()], file.name);
        Object.defineProperty(browserFile, "webkitRelativePath", {
          value: file.webkitRelativePath,
        });
        return browserFile;
      }),
    );
    const urls = new BlobCheckingObjectUrls();
    const catalog = await loadBaselineCatalog(files, urls);

    expect(urls.created).toHaveLength(2);
    catalog.dispose();
    expect(urls.revoked).toEqual(urls.created);
  });

  it("baseline-reference raw canonical bytes と marker SHA の不一致を拒否する", async () => {
    const fixture = await makeFixture();
    await expect(
      loadBaselineCatalog(
        replaceFile(fixture.files, "baseline-reference.sha256", "e".repeat(64)),
        new FakeObjectUrls(),
      ),
    ).rejects.toThrow("生バイトSHAと一致しません");

    const withNewline = `${fixture.baselineReferenceSource}\n`;
    const synchronized = replaceFile(
      replaceFile(fixture.files, "baseline-reference.json", withNewline),
      "baseline-reference.sha256",
      await digest(withNewline),
    );
    await expect(loadBaselineCatalog(synchronized, new FakeObjectUrls())).rejects.toThrow(
      "canonical JSON",
    );
  });

  it("reference の extra key、重複 group、candidate SHA staleを拒否する", async () => {
    const fixture = await makeFixture();
    const original = JSON.parse(fixture.baselineReferenceSource) as BaselineReferenceFixture;

    const extraKey = structuredClone(original);
    (
      extraKey.references[0] as BaselineReferenceFixture["references"][number] & {
        extra: boolean;
      }
    ).extra = true;
    await expect(
      loadBaselineCatalog(await replaceBaselineReference(fixture, extraKey), new FakeObjectUrls()),
    ).rejects.toThrow("キー構成が不正");

    const duplicate = structuredClone(original);
    duplicate.references[1] = structuredClone(duplicate.references[0]!);
    await expect(
      loadBaselineCatalog(await replaceBaselineReference(fixture, duplicate), new FakeObjectUrls()),
    ).rejects.toThrow("重複group");

    const stale = structuredClone(original);
    stale.references[0]!.candidate_sha256 = "f".repeat(64);
    stale.references[0]!.comparison = "different";
    await expect(
      loadBaselineCatalog(await replaceBaselineReference(fixture, stale), new FakeObjectUrls()),
    ).rejects.toThrow("candidateと一致しません");
  });

  it("不足・余分なファイルと旧reference音声のSHA不一致を拒否しURLをリークしない", async () => {
    const fixture = await makeFixture();

    const missing = fixture.files.filter(
      (file) => !file.webkitRelativePath.endsWith("/reference/model-6/scene/line/v053.opus"),
    );
    const missingUrls = new FakeObjectUrls();
    await expect(loadBaselineCatalog(missing, missingUrls)).rejects.toThrow("fileがありません");
    expect(missingUrls.revoked).toEqual(missingUrls.created);

    const extra = [...fixture.files, new MemoryFile("run/unexpected.txt", "extra")];
    const extraUrls = new FakeObjectUrls();
    await expect(loadBaselineCatalog(extra, extraUrls)).rejects.toThrow("余分なfile");
    expect(extraUrls.revoked).toEqual(extraUrls.created);

    const changed = fixture.files.map((file) =>
      file.webkitRelativePath.endsWith("/reference/model-0/scene/line/v000.opus")
        ? new MemoryFile(file.webkitRelativePath, "changed")
        : file,
    );
    const changedUrls = new FakeObjectUrls();
    await expect(loadBaselineCatalog(changed, changedUrls)).rejects.toThrow(
      "file SHAが一致しません",
    );
    expect(changedUrls.revoked).toEqual(changedUrls.created);
  });

  it("inventoryがsource-runsを含む全fileのmissing/extra/byte tamperを拒否する", async () => {
    const fixture = await makeFixture();
    const missing = fixture.files.filter(
      (file) => file.webkitRelativePath !== "run/source-runs/model-0/qc-report.json",
    );
    await expect(loadBaselineCatalog(missing, new FakeObjectUrls())).rejects.toThrow(
      "要求するfileがありません",
    );

    const extra = [
      ...fixture.files,
      new MemoryFile("run/source-runs/model-0/untracked.json", "{}"),
    ];
    await expect(loadBaselineCatalog(extra, new FakeObjectUrls())).rejects.toThrow(
      "inventory.json にない余分なfile",
    );

    const tampered = replacePath(
      fixture.files,
      "run/source-runs/model-0/qc-report.json",
      '{"tampered":true}',
    );
    await expect(loadBaselineCatalog(tampered, new FakeObjectUrls())).rejects.toThrow(
      "file SHAが一致しません",
    );
  });
});

interface BaselineReferenceFixture {
  format_version: 1;
  source_manifest_sha256: string;
  candidate_set_sha256: string;
  references: Array<{
    model: string;
    scenario: string;
    line: string;
    variant: string;
    public_path: string;
    legacy_sha256: string;
    local_path: string;
    candidate_sha256: string | null;
    comparison: "identical" | "different" | "no_candidate";
  }>;
}

class MemoryFile implements DirectoryFile {
  readonly name: string;
  readonly webkitRelativePath: string;
  private readonly contents: string;

  constructor(webkitRelativePath: string, contents: string) {
    this.webkitRelativePath = webkitRelativePath;
    this.contents = contents;
    this.name = webkitRelativePath.split("/").at(-1)!;
  }

  async arrayBuffer(): Promise<ArrayBuffer> {
    return new TextEncoder().encode(this.contents).buffer;
  }
}

class FakeObjectUrls implements ObjectUrlFactory {
  readonly created: string[] = [];
  readonly revoked: string[] = [];

  create(_file: DirectoryFile): string {
    const url = `blob:baseline-${this.created.length}`;
    this.created.push(url);
    return url;
  }

  revoke(url: string): void {
    this.revoked.push(url);
  }
}

class BlobCheckingObjectUrls extends FakeObjectUrls {
  override create(file: DirectoryFile): string {
    expect(file).toBeInstanceOf(Blob);
    return super.create(file);
  }
}

async function makeFixture(): Promise<{
  files: readonly MemoryFile[];
  candidateSetSha256: string;
  candidateAudioSha256: string;
  baselineReferenceSource: string;
  baselineReferenceSha256: string;
}> {
  const models = Array.from({ length: 7 }, (_, index) => ({
    id: `model-${index}`,
    name: `Model ${index}`,
    version: "1",
    license_note: "",
    capabilities: {
      emotion: false,
      voice_prompt: false,
      clone: false,
      nonverbal: false,
      reading: false,
    },
  }));
  const groupCounts = [55, 55, 55, 54, 54, 54, 54];
  const groups = models.flatMap((model, modelIndex) =>
    Array.from({ length: groupCounts[modelIndex]! }, (_, variantIndex) => ({
      model: model.id,
      scenario: "scene",
      line: "line",
      variant: `v${String(variantIndex).padStart(3, "0")}`,
    })),
  );
  const candidateContents = "candidate-audio";
  const candidateAudioSha256 = await digest(candidateContents);
  const takeId = await sha256Text(
    `{"final_opus_sha256":"${candidateAudioSha256}","generation_input_sha256":"${INPUT_SHA}"}`,
  );
  const candidate = {
    model: "model-0",
    scenario: "scene",
    line: "line",
    variant: "v000",
    take_index: 1,
    take_id: takeId,
    path: `audio/takes/model-0/scene/line/v000/take-0001-${candidateAudioSha256}.opus`,
    duration_sec: 1,
    sha256: candidateAudioSha256,
    generation_input_sha256: INPUT_SHA,
    gen_params: {
      seed: 1,
      recipe_version: "baseline-v1",
      sampling: {},
      requested: {},
      realized: {},
    },
    rtf: 0.5,
    loudness: {
      source: "encoded_opus",
      i_lufs: -18,
      tp_dbtp: -1,
      shortfall: false,
    },
    gate: {
      mechanical: "pass",
      content: "pass",
      policy_version: "take-gate-v1",
    },
  };
  const failures = groups.slice(1).map((group) => ({
    ...group,
    reason: "no_eligible_take",
  }));
  const line = {
    scenario: "scene",
    line: "line",
    scenario_title: "保存時のシーン",
    text: "保存時の台詞",
    delivery: "保存時の演技指示",
  };
  const candidateSet = {
    format_version: 4,
    scenario_sha256: "c".repeat(64),
    lines: [line],
    models,
    candidates: [candidate],
    failures,
  };
  const candidateSetSource = JSON.stringify(candidateSet);
  const candidateSetSha256 = await digest(candidateSetSource);
  const manifestSource = JSON.stringify({
    format_version: 4,
    generated_at: "2026-07-29T00:00:00Z",
    candidate_set_sha256: candidateSetSha256,
    models,
    candidates: [candidate],
    curations: [],
    failures,
  });

  const referenceContents = await Promise.all(
    groups.map(async (group, index) => {
      const contents =
        index === 0 ? candidateContents : `legacy-reference-${group.model}-${group.variant}`;
      return {
        ...group,
        contents,
        sha256: await digest(contents),
      };
    }),
  );
  const baselineReference: BaselineReferenceFixture = {
    format_version: 1,
    source_manifest_sha256: "d".repeat(64),
    candidate_set_sha256: candidateSetSha256,
    references: referenceContents.map((reference, index) => ({
      model: reference.model,
      scenario: reference.scenario,
      line: reference.line,
      variant: reference.variant,
      public_path:
        `audio/${reference.model}/${reference.scenario}/` +
        `${reference.line}/${reference.variant}.opus`,
      legacy_sha256: reference.sha256,
      local_path:
        `reference/${reference.model}/${reference.scenario}/` +
        `${reference.line}/${reference.variant}.opus`,
      candidate_sha256: index === 0 ? candidateAudioSha256 : null,
      comparison: index === 0 ? "identical" : "no_candidate",
    })),
  };
  const baselineReferenceSource = canonicalJson(baselineReference, "baseline-reference fixture");
  const baselineReferenceSha256 = await digest(baselineReferenceSource);
  const plan = {
    format_version: 1,
    plan_version: "baseline-plan-v1",
    source: {
      manifest_path: "data/manifest.json",
      manifest_sha256: "d".repeat(64),
      scenario_sha256: "c".repeat(64),
    },
    models,
    groups: referenceContents.map((reference) => ({
      model: reference.model,
      scenario: reference.scenario,
      line: reference.line,
      variant: reference.variant,
      legacy: {
        path:
          `audio/${reference.model}/${reference.scenario}/` +
          `${reference.line}/${reference.variant}.opus`,
        sha256: reference.sha256,
      },
    })),
    excluded_failures: [],
  };
  const planSource = canonicalJson(plan, "baseline-plan fixture");
  const planSha256 = await digest(planSource);

  const sourceRunFiles: MemoryFile[] = [];
  const provenanceRuns = [];
  for (const model of models) {
    const modelGroups = groups.filter((group) => group.model === model.id);
    const modelCandidates = model.id === "model-0" ? [candidate] : [];
    const modelFailures = failures.filter((failure) => failure.model === model.id);
    const sourceCandidateSet = {
      format_version: 4,
      scenario_sha256: "c".repeat(64),
      lines: [line],
      models: [model],
      candidates: modelCandidates,
      failures: modelFailures,
    };
    const sourceCandidateSetSource = canonicalFixture(sourceCandidateSet);
    const sourceCandidateSetSha256 = await digest(sourceCandidateSetSource);
    const sourceManifestSource = canonicalFixture({
      format_version: 4,
      generated_at: "2026-07-29T00:00:00Z",
      candidate_set_sha256: sourceCandidateSetSha256,
      models: [model],
      candidates: modelCandidates,
      curations: [],
      failures: modelFailures,
    });
    const runId = `run-${model.id}`;
    const candidateWav = "candidate-wav";
    const candidateSidecar = JSON.stringify({
      format_version: 1,
      run_id: runId,
      model: "model-0",
      scenario: "scene",
      line: "line",
      variant: "v000",
      take_index: 1,
      take_id: takeId,
      generation_input_sha256: INPUT_SHA,
      wav_sha256: await digest(candidateWav),
      opus_sha256: candidateAudioSha256,
      duration_sec: 1,
      generation_seconds: 1,
      rtf: 0.5,
      take: {
        seed: 1,
        recipe_version: "baseline-v1",
        sampling: {},
      },
      gen_params: { requested: {}, realized: {} },
      postprocess: {},
      toolchain: {
        ffmpeg_version: "8",
        ffprobe_version: "8",
        libopus_encoder: true,
      },
      loudness: {},
    });
    const attempts = await Promise.all(
      modelGroups.map(async (group, index) => {
        if (model.id === "model-0" && index === 0) {
          return {
            ...group,
            take_index: 1,
            take_id: takeId,
            generation_input_sha256: INPUT_SHA,
            generation: {
              status: "succeeded",
              seed: 1,
              sampling: {},
              rtf: 0.5,
            },
            audio: {
              wav_path: "audio/model-0/scene/line/v000/take-0001.wav",
              wav_sha256: await digest(candidateWav),
              opus_path: "audio/model-0/scene/line/v000/take-0001.opus",
              opus_sha256: candidateAudioSha256,
              sidecar_sha256: await digest(candidateSidecar),
            },
            gates: { mechanical: "pass", content: "pass" },
            features: { status: "unscored" },
            status: "eligible",
          };
        }
        return {
          ...group,
          take_index: 1,
          generation_input_sha256: INPUT_SHA,
          generation: {
            status: "failed",
            seed: 1,
            sampling: {},
            error: "fixture failure",
          },
          status: "generation_failed",
        };
      }),
    );
    const ledgerSource = JSON.stringify({
      format_version: 1,
      run_id: runId,
      created_at: "2026-07-29T00:00:00Z",
      source: {
        scenario_sha256: "c".repeat(64),
        model: model.id,
        takes: 1,
        seed_base: 1,
        recipe_version: "baseline-v1",
        groups: modelGroups,
      },
      attempts,
    });
    const qcAttempts = attempts.map((attempt) => {
      if (attempt.status === "generation_failed") {
        return {
          model: attempt.model,
          scenario: attempt.scenario,
          line: attempt.line,
          variant: attempt.variant,
          take_index: attempt.take_index,
          status: "generation_failed",
          gates: null,
          mechanical: { status: "not_run" },
          content: { status: "not_run" },
        };
      }
      return {
        model: attempt.model,
        scenario: attempt.scenario,
        line: attempt.line,
        variant: attempt.variant,
        take_index: attempt.take_index,
        take_id: "take_id" in attempt ? attempt.take_id : "",
        status: "eligible",
        gates: { mechanical: "pass", content: "pass" },
        mechanical: {
          status: "pass",
          duration_sec: 1,
          wav: {
            codec: "pcm_s16le",
            sample_rate_hz: 48_000,
            channels: 1,
          },
          opus: {
            codec: "opus",
            sample_rate_hz: 48_000,
            channels: 1,
          },
          loudness: {
            source: "encoded_opus",
            i_lufs: -18,
            tp_dbtp: -1,
            shortfall: false,
          },
          generation_params: { requested: {}, realized: {} },
          sidecar_provenance: {
            generation_seconds: 1,
            postprocess: {},
            toolchain: {},
            loudness: {},
          },
        },
        content: {
          status: "pass",
          review_reason: null,
          expected_reading: {
            text: "保存時の台詞",
            source: "text",
            normalized: "保存時の台詞",
            authoritative: true,
            ambiguous_terms: [],
          },
          asr: {
            text: "保存時の台詞",
            normalized_reading: "保存時の台詞",
            average_log_probability: null,
          },
          reading: {
            character_error_rate: 0,
            reading_mismatch: false,
          },
          prosody: {},
        },
      };
    });
    const eligibleCount = attempts.filter((attempt) => attempt.status === "eligible").length;
    const generationFailedCount = attempts.length - eligibleCount;
    const ledgerPath = `F:/artifacts/takes/${runId}/ledger.json`;
    const qcSource = JSON.stringify({
      format_version: 2,
      generated_at: "2026-07-29T00:00:00Z",
      gate_policy_version: "take-gates-v2",
      run_id: runId,
      source: {
        ledger: ledgerPath,
        scenario_sha256: "c".repeat(64),
        model: model.id,
        recipe_version: "baseline-v1",
      },
      runtime: { status: "not_required" },
      summary: {
        attempt_count: attempts.length,
        eligible: eligibleCount,
        hard_rejected: 0,
        blocked: 0,
        generation_failed: generationFailedCount,
        planned: 0,
        generated: 0,
        pending: 0,
        content_review_required: 0,
      },
      attempts: qcAttempts,
    });
    provenanceRuns.push({
      model: model.id,
      run_id: runId,
      ledger_path: ledgerPath,
      ledger_sha256: await digest(ledgerSource),
      qc_report_sha256: await digest(qcSource),
      manifest_sha256: await digest(sourceManifestSource),
      candidate_set_sha256: sourceCandidateSetSha256,
    });
    const prefix = `run/source-runs/${model.id}`;
    sourceRunFiles.push(
      new MemoryFile(`${prefix}/ledger.json`, ledgerSource),
      new MemoryFile(`${prefix}/qc-report.json`, qcSource),
      new MemoryFile(`${prefix}/manifest-v4.json`, sourceManifestSource),
      new MemoryFile(`${prefix}/candidate-set.json`, sourceCandidateSetSource),
      new MemoryFile(`${prefix}/candidate-set.sha256`, sourceCandidateSetSha256),
    );
    if (model.id === "model-0") {
      sourceRunFiles.push(
        new MemoryFile(`${prefix}/audio/model-0/scene/line/v000/take-0001.wav`, candidateWav),
        new MemoryFile(`${prefix}/audio/model-0/scene/line/v000/take-0001.opus`, candidateContents),
        new MemoryFile(`${prefix}/audio/model-0/scene/line/v000/take-0001.json`, candidateSidecar),
      );
    }
  }
  const provenanceSource = canonicalJson(
    {
      format_version: 1,
      plan_sha256: planSha256,
      runs: provenanceRuns,
    },
    "baseline-provenance fixture",
  );
  const provenanceSha256 = await digest(provenanceSource);
  const bundleFiles = [
    new MemoryFile("run/manifest-v4.json", manifestSource),
    new MemoryFile("run/candidate-set.json", candidateSetSource),
    new MemoryFile("run/candidate-set.sha256", candidateSetSha256),
    new MemoryFile("run/baseline-reference.json", baselineReferenceSource),
    new MemoryFile("run/baseline-reference.sha256", baselineReferenceSha256),
    new MemoryFile("run/baseline-plan.json", planSource),
    new MemoryFile("run/baseline-plan.sha256", planSha256),
    new MemoryFile("run/baseline-provenance.json", provenanceSource),
    new MemoryFile("run/baseline-provenance.sha256", provenanceSha256),
    new MemoryFile(`run/${candidate.path}`, candidateContents),
    ...referenceContents.map(
      (reference) =>
        new MemoryFile(
          `run/reference/${reference.model}/${reference.scenario}/` +
            `${reference.line}/${reference.variant}.opus`,
          reference.contents,
        ),
    ),
    ...sourceRunFiles,
  ];
  const inventoryEntries = await Promise.all(
    bundleFiles.map(async (file) => ({
      path: file.webkitRelativePath.slice("run/".length),
      sha256: await digestBytes(await file.arrayBuffer()),
    })),
  );
  inventoryEntries.sort((left, right) =>
    left.path < right.path ? -1 : left.path > right.path ? 1 : 0,
  );
  const inventorySource = canonicalFixture({
    format_version: 1,
    files: inventoryEntries,
  });
  const inventorySha256 = await digest(inventorySource);

  return {
    candidateSetSha256,
    candidateAudioSha256,
    baselineReferenceSource,
    baselineReferenceSha256,
    files: [
      ...bundleFiles,
      new MemoryFile("run/baseline-bundle-inventory.json", inventorySource),
      new MemoryFile("run/baseline-bundle-inventory.sha256", `${inventorySha256}\n`),
    ],
  };
}

function replaceFile(
  files: readonly MemoryFile[],
  name: string,
  contents: string,
): readonly MemoryFile[] {
  return files.map((file) =>
    file.name === name ? new MemoryFile(file.webkitRelativePath, contents) : file,
  );
}

function replacePath(
  files: readonly MemoryFile[],
  path: string,
  contents: string,
): readonly MemoryFile[] {
  return files.map((file) =>
    file.webkitRelativePath === path ? new MemoryFile(file.webkitRelativePath, contents) : file,
  );
}

async function replaceBaselineReference(
  fixture: Awaited<ReturnType<typeof makeFixture>>,
  baselineReference: BaselineReferenceFixture,
): Promise<readonly MemoryFile[]> {
  const source = canonicalJson(baselineReference, "baseline-reference fixture");
  return replaceFile(
    replaceFile(fixture.files, "baseline-reference.json", source),
    "baseline-reference.sha256",
    await digest(source),
  );
}

async function digest(contents: string): Promise<string> {
  return sha256Hex(new TextEncoder().encode(contents));
}

async function digestBytes(contents: ArrayBuffer): Promise<string> {
  return sha256Hex(contents);
}

function canonicalFixture(value: unknown): string {
  return JSON.stringify(sortFixture(value));
}

function sortFixture(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortFixture);
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, entry]) => [key, sortFixture(entry)]),
    );
  }
  return value;
}
