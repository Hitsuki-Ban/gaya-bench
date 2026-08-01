import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import {
  createServer as createHttpServer,
  request as createHttpRequest,
  type Server,
} from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vite-plus/test";

import {
  BUNDLE_FILE,
  assertListeningDirectoryBoundaries,
  canonicalJsonBytes,
  createListeningRuntime,
  DRAFT_FILE,
  FINAL_FILE,
  MUTATION_TOKEN_HEADER,
  LISTENING_PROTOCOL,
  LISTENING_STATE_DIR,
  LISTENING_WORKFLOW,
  SESSION_FILE,
  SITE_ROOT,
  validateDecisionDocument,
  validateListeningBundle,
} from "./listening-app-server.ts";

const TOKEN = "f".repeat(64);
const CLI_SCRIPT = fileURLToPath(new URL("./listening-app.ts", import.meta.url));

describe("listening bundle validation", () => {
  it("canonical JSON、全音声SHA、exact file setを検証する", async () => {
    await withFixture(async ({ bundleRoot, bundle }) => {
      const validated = await validateListeningBundle(bundleRoot);
      expect(validated.candidates.size).toBe(424);

      writeFileSync(
        path.join(bundleRoot, BUNDLE_FILE),
        `${canonicalJsonBytes(bundle).toString()}\n`,
      );
      await expect(validateListeningBundle(bundleRoot)).rejects.toThrow("canonical JSON");
    });
  });

  it("音声SHA改ざん、path traversal、任意extra fileを拒否する", async () => {
    await withFixture(async ({ bundleRoot, bundle }) => {
      const firstCandidate = firstBundleCandidate(bundle);
      writeFileSync(path.join(bundleRoot, firstCandidate.audio_path), "tampered");
      await expect(validateListeningBundle(bundleRoot)).rejects.toThrow("SHA-256");
    });

    await withFixture(async ({ bundleRoot, bundle }) => {
      firstBundleCandidate(bundle).audio_path = "audio/../escape.wav";
      writeBundle(bundleRoot, bundle);
      await expect(validateListeningBundle(bundleRoot)).rejects.toThrow("安全なrelative POSIX");
    });

    await withFixture(async ({ bundleRoot }) => {
      writeFileSync(path.join(bundleRoot, "unexpected.txt"), "unexpected");
      await expect(validateListeningBundle(bundleRoot)).rejects.toThrow("file set");
    });
  });

  it("model内のrole座標重複とmodel間の座標差を拒否する", async () => {
    await withFixture(async ({ bundleRoot, bundle }) => {
      bundle.groups[1]!.scenario = bundle.groups[0]!.scenario;
      bundle.groups[1]!.character = bundle.groups[0]!.character;
      writeBundle(bundleRoot, bundle);
      await expect(validateListeningBundle(bundleRoot)).rejects.toThrow("role座標が重複");
    });

    await withFixture(async ({ bundleRoot, bundle }) => {
      bundle.groups[53]!.scenario = "scene-999";
      bundle.groups.sort((left, right) =>
        [left.model, left.scenario, left.character]
          .join("/")
          .localeCompare([right.model, right.scenario, right.character].join("/"), "en"),
      );
      writeBundle(bundleRoot, bundle);
      await expect(validateListeningBundle(bundleRoot)).rejects.toThrow("同じ53 role座標集合");
    });
  });

  it("topupのattempt 5..8を受理し、重複attemptを拒否する", async () => {
    await withFixture(async ({ bundleRoot, bundle }) => {
      for (const group of bundle.groups) {
        group.candidates.forEach((candidate, index) => {
          candidate.attempt = index + 5;
        });
      }
      writeBundle(bundleRoot, bundle);
      await expect(validateListeningBundle(bundleRoot)).resolves.toBeDefined();

      bundle.groups[0]!.candidates[1]!.attempt = 5;
      writeBundle(bundleRoot, bundle);
      await expect(validateListeningBundle(bundleRoot)).rejects.toThrow("一意な昇順正整数");
    });
  });

  it("bundle/output相互包含とsite directoryとの重複を拒否する", () => {
    const bundle = path.join(tmpdir(), "bundle");
    const output = path.join(tmpdir(), "output");
    expect(() => assertListeningDirectoryBoundaries(bundle, path.join(bundle, "results"))).toThrow(
      "互いに独立",
    );
    expect(() => assertListeningDirectoryBoundaries(path.dirname(output), output)).toThrow(
      "互いに独立",
    );
    expect(() => assertListeningDirectoryBoundaries(SITE_ROOT, output)).toThrow("bundle は site");
    expect(() => assertListeningDirectoryBoundaries(bundle, SITE_ROOT)).toThrow("output は site");
  });

  it("四候補すべて使用不可のdecisionは理由を必須にして候補を偽造しない", async () => {
    await withFixture(async ({ bundleRoot, bundle }) => {
      const validated = await validateListeningBundle(bundleRoot);
      const decision = makeDecision(bundle);
      const first = (
        decision.groups as Array<{
          no_usable_candidate: boolean;
          selected_candidate_id: string | null;
          rubric: Record<string, unknown>;
        }>
      )[0]!;
      first.no_usable_candidate = true;
      first.selected_candidate_id = null;
      expect(() => validateDecisionDocument(decision, validated)).toThrow("使用不可な理由");

      first.rubric.gender = "fail";
      expect(() => validateDecisionDocument(decision, validated)).not.toThrow();
    });
  });

  it("性別不一致のselected anchorをdecisionとして拒否する", async () => {
    await withFixture(async ({ bundleRoot, bundle }) => {
      const validated = await validateListeningBundle(bundleRoot);
      const decision = makeDecision(bundle);
      const first = (decision.groups as Array<{ rubric: Record<string, unknown> }>)[0]!;
      first.rubric.gender = "fail";

      expect(() => validateDecisionDocument(decision, validated)).toThrow("gender=pass");
    });
  });
});

describe("listening REST API", () => {
  it("Origin/token、revision、finalize gate、atomic result、final freezeを強制する", async () => {
    await withFixture(async ({ root, bundleRoot, outputRoot, bundle }) => {
      const port = await unusedPort();
      const runtime = await createListeningRuntime({
        bundleDirectory: bundleRoot,
        outputDirectory: outputRoot,
        port,
        mutationToken: TOKEN,
      });
      const server = createHttpServer((request, response) => runtime.api.handle(request, response));
      await listen(server, port);
      const origin = `http://127.0.0.1:${port}`;
      const draft = makeDraft(bundle);
      try {
        const bootstrap = await fetch(`${origin}/__gaya-listening/bootstrap`);
        expect(bootstrap.status).toBe(200);
        const bootstrapBody = (await bootstrap.json()) as Record<string, unknown>;
        expect(bootstrapBody.mutation_token).toBe(TOKEN);
        expect(bootstrapBody).toMatchObject({
          format_version: 1,
          protocol: "gaya-listening-session-v1",
          workflow: "role-review-anchor-v2",
          revision: 0,
          finalized: false,
        });
        expect(JSON.stringify(bootstrapBody)).not.toContain(root);

        const emptyDraft = await fetch(`${origin}/__gaya-listening/draft`);
        expect(emptyDraft.status).toBe(204);

        const wrongOrigin = await putDraft(origin, draft, 0, "http://localhost:4173", TOKEN);
        expect(wrongOrigin.status).toBe(403);
        const wrongToken = await putDraft(origin, draft, 0, origin, "0".repeat(64));
        expect(wrongToken.status).toBe(403);

        const saved = await putDraft(origin, draft, 0, origin, TOKEN);
        expect(saved.status).toBe(200);
        expect(await saved.json()).toMatchObject({ revision: 1, saved_at: expect.any(String) });
        assertCanonicalResult(outputRoot, DRAFT_FILE, draft, false);

        const stale = await putDraft(origin, draft, 0, origin, TOKEN);
        expect(stale.status).toBe(409);

        const incomplete = makeDecision(bundle);
        firstDecisionGroup(incomplete).heard_candidate_ids.pop();
        const gated = await finalize(origin, incomplete, 1);
        expect(gated.status).toBe(400);
        expect(await gated.text()).toContain("全4候補");

        const invalidApplicableResult = makeDecision(bundle);
        const invalidRubric = (
          invalidApplicableResult.groups as Array<{ rubric: Record<string, unknown> }>
        )[0]!.rubric;
        invalidRubric.content = "not_applicable";
        const rejectedApplicableResult = await finalize(origin, invalidApplicableResult, 1);
        expect(rejectedApplicableResult.status).toBe(400);
        expect(await rejectedApplicableResult.text()).toContain("rubric.content が不正");

        const decision = makeDecision(bundle);
        const unbound = await finalize(origin, decision, 1);
        expect(unbound.status).toBe(409);
        expect(await unbound.text()).toContain("確認済みではありません");

        const completedDraft = makeCompletedDraft(bundle);
        const completedSaved = await putDraft(origin, completedDraft, 1, origin, TOKEN);
        expect(completedSaved.status).toBe(200);
        expect(await completedSaved.json()).toMatchObject({ revision: 2 });
        assertCanonicalResult(outputRoot, DRAFT_FILE, completedDraft, false);

        const mismatched = structuredClone(decision);
        (mismatched.groups as Array<Record<string, unknown>>)[0]!.selected_candidate_id =
          bundle.groups[0]!.candidate_ids[1];
        const rejectedMismatch = await finalize(origin, mismatched, 2);
        expect(rejectedMismatch.status).toBe(409);
        expect(await rejectedMismatch.text()).toContain("保存済みdraft");

        const finalized = await finalize(origin, decision, 2);
        expect(finalized.status, await finalized.clone().text()).toBe(200);
        assertCanonicalResult(outputRoot, FINAL_FILE, decision, true);

        const frozen = await putDraft(origin, draft, 2, origin, TOKEN);
        expect(frozen.status).toBe(409);

        const candidate = firstBundleCandidate(bundle);
        const ranged = await fetch(`${origin}/__gaya-listening/audio/${candidate.id}`, {
          headers: { Range: "bytes=1-3" },
        });
        expect(ranged.status).toBe(206);
        expect(Buffer.from(await ranged.arrayBuffer())).toEqual(
          Buffer.from(candidate.audio_contents!).subarray(1, 4),
        );
        const head = await fetch(`${origin}/__gaya-listening/audio/${candidate.id}`, {
          method: "HEAD",
        });
        expect(head.status).toBe(200);
        expect(await head.text()).toBe("");
      } finally {
        await close(server);
      }
    });
  });

  it("write-once finalのmarkerだけが中断した場合は検証済みJSONから復旧する", async () => {
    await withFixture(async ({ bundleRoot, outputRoot, bundle }) => {
      const decision = makeDecision(bundle);
      writeFileSync(path.join(outputRoot, FINAL_FILE), canonicalJsonBytes(decision));
      const port = await unusedPort();

      await expect(
        createListeningRuntime({
          bundleDirectory: bundleRoot,
          outputDirectory: outputRoot,
          port,
          mutationToken: TOKEN,
        }),
      ).rejects.toThrow("保存済みdraft");

      writeFileSync(
        path.join(outputRoot, DRAFT_FILE),
        canonicalJsonBytes(makeCompletedDraft(bundle)),
      );
      const runtime = await createListeningRuntime({
        bundleDirectory: bundleRoot,
        outputDirectory: outputRoot,
        port,
        mutationToken: TOKEN,
      });

      expect(runtime.api.snapshot().finalized).toBe(true);
      assertCanonicalResult(outputRoot, FINAL_FILE, decision, true);
    });
  });

  it("shutdown開始後の遅延mutationを拒否して既存queueだけをdrainする", async () => {
    await withFixture(async ({ bundleRoot, outputRoot, bundle }) => {
      const port = await unusedPort();
      const runtime = await createListeningRuntime({
        bundleDirectory: bundleRoot,
        outputDirectory: outputRoot,
        port,
        mutationToken: TOKEN,
      });
      const server = createHttpServer((request, response) => runtime.api.handle(request, response));
      await listen(server, port);
      const origin = `http://127.0.0.1:${port}`;
      try {
        const delayed = beginDelayedPutDraft(origin, makeDraft(bundle), 0);
        await new Promise<void>((resolve) => setImmediate(resolve));

        const shutdown = await fetch(`${origin}/__gaya-listening/shutdown`, {
          method: "POST",
          headers: {
            Origin: origin,
            [MUTATION_TOKEN_HEADER]: TOKEN,
            "Content-Length": "0",
          },
        });
        expect(shutdown.status).toBe(200);

        delayed.finish();
        const rejected = await delayed.response;
        expect(rejected.status).toBe(409);
        expect(rejected.body).toContain("停止処理中");
        expect(runtime.api.snapshot().revision).toBe(0);
      } finally {
        await close(server);
      }
    });
  });

  it("detached CLIがprogrammatic Viteをreadyにしてstatus/stopできる", async () => {
    await withFixture(async ({ bundleRoot, outputRoot }) => {
      const port = await unusedPort();
      try {
        const started = spawnSync(
          process.execPath,
          [
            CLI_SCRIPT,
            "start",
            "--bundle",
            bundleRoot,
            "--output",
            outputRoot,
            "--port",
            String(port),
          ],
          { encoding: "utf8", timeout: 30_000 },
        );
        expect(started.status, processOutput(started)).toBe(0);
        expect(started.stdout).toContain("internal.html#/completion");

        const health = await fetch(`http://127.0.0.1:${port}/__gaya-listening/health`);
        expect(health.status).toBe(200);
        expect(await health.json()).toMatchObject({
          status: "ok",
          session_id: expect.any(String),
          revision: 0,
          finalized: false,
          shutting_down: false,
        });
        const page = await fetch(`http://127.0.0.1:${port}/internal.html#/completion`);
        expect(page.status).toBe(200);
        const stateLeak = await fetch(
          `http://127.0.0.1:${port}/@fs/${SESSION_FILE.replaceAll("\\", "/")}`,
        );
        expect(stateLeak.status).toBe(403);

        const status = spawnSync(process.execPath, [CLI_SCRIPT, "status"], {
          encoding: "utf8",
          timeout: 10_000,
        });
        expect(status.status, processOutput(status)).toBe(0);
        expect(status.stdout).toContain("活動中");
        expect(status.stdout).toContain(bundleRoot);
        expect(status.stdout).toContain(outputRoot);
        expect(status.stdout).toContain("revision: 0");
        expect(status.stdout).toContain("finalized: no");
      } finally {
        const stopped = spawnSync(process.execPath, [CLI_SCRIPT, "stop"], {
          encoding: "utf8",
          timeout: 20_000,
        });
        expect(stopped.status, processOutput(stopped)).toBe(0);
      }
    });
  }, 60_000);

  it("session identityを検証できない生存PIDはkillせず停止失敗にする", async () => {
    await withFixture(async ({ bundleRoot, outputRoot }) => {
      mkdirSync(LISTENING_STATE_DIR, { recursive: true });
      const port = await unusedPort();
      const session = {
        format_version: 1,
        protocol: LISTENING_PROTOCOL,
        workflow: LISTENING_WORKFLOW,
        state: "ready",
        id: "unverified-session",
        pid: process.pid,
        port,
        origin: `http://127.0.0.1:${port}`,
        mutation_token: TOKEN,
        started_at: new Date().toISOString(),
        bundle: bundleRoot,
        output: outputRoot,
      };
      writeFileSync(SESSION_FILE, canonicalJsonBytes(session));
      try {
        const stopped = spawnSync(process.execPath, [CLI_SCRIPT, "stop"], {
          encoding: "utf8",
          timeout: 10_000,
        });
        expect(stopped.status, processOutput(stopped)).toBe(1);
        expect(stopped.stderr).toContain("同一listening daemonだと検証できません");
        expect(existsSync(SESSION_FILE)).toBe(true);
      } finally {
        if (existsSync(SESSION_FILE)) {
          unlinkSync(SESSION_FILE);
        }
      }
    });
  });
});

interface Fixture {
  readonly root: string;
  readonly bundleRoot: string;
  readonly outputRoot: string;
  readonly bundle: TestBundle;
}

interface TestCandidate extends Record<string, unknown> {
  id: string;
  audio_path: string;
  audio_sha256: string;
  audio_contents?: string;
}

interface TestGroup extends Record<string, unknown> {
  id: string;
  model: string;
  scenario: string;
  character: string;
  role_epoch_sha256: string;
  candidate_ids: string[];
  candidates: TestCandidate[];
}

interface TestBundle extends Record<string, unknown> {
  format_version: number;
  protocol: string;
  phase: string;
  plan_sha256: string;
  candidate_set_sha256: string;
  groups: TestGroup[];
}

async function withFixture(run: (fixture: Fixture) => Promise<void>): Promise<void> {
  const root = mkdtempSync(path.join(tmpdir(), "gaya-listening-server-"));
  try {
    const bundleRoot = path.join(root, "bundle");
    const outputRoot = path.join(root, "output");
    mkdirSync(path.join(bundleRoot, "audio"), { recursive: true });
    mkdirSync(outputRoot);
    const bundle = makeBundle(bundleRoot);
    await run({ root, bundleRoot, outputRoot, bundle });
  } finally {
    rmSync(root, { force: true, recursive: true });
  }
}

function makeBundle(bundleRoot: string): TestBundle {
  const groups = Array.from({ length: 106 }, (_, groupIndex): TestGroup => {
    const modelIndex = groupIndex % 53;
    const candidates = Array.from({ length: 4 }, (_, candidateIndex): TestCandidate => {
      const id = hash(`candidate:${groupIndex}:${candidateIndex}`);
      const audioContents = `RIFF-test-audio:${groupIndex}:${candidateIndex}`;
      const audioPath = `audio/${id}.wav`;
      writeFileSync(path.join(bundleRoot, ...audioPath.split("/")), audioContents);
      return {
        id,
        attempt: candidateIndex + 1,
        seed: groupIndex * 10 + candidateIndex,
        audio_path: audioPath,
        audio_sha256: hash(audioContents),
        audio_contents: audioContents,
        qc: { mechanical: "pass", content: "not_checked", notes: [] },
      };
    });
    return {
      id: hash(`group:${groupIndex}`),
      model: groupIndex < 53 ? "irodori-tts-600m-v3-voicedesign" : "qwen3-tts-12hz-1.7b",
      scenario: `scene-${String(modelIndex).padStart(3, "0")}`,
      character: `character-${String(modelIndex).padStart(3, "0")}`,
      anchor_text: `アンカー ${groupIndex}`,
      line: null,
      role_epoch_sha256: hash(`epoch:${groupIndex}`),
      role: {
        name: `役 ${groupIndex}`,
        kind: "human",
        gender: "neutral",
        age: "adult",
        archetype: "guard",
        voice: "clear",
        personality: "calm",
      },
      conditioning: { method: "anchor", summary: "strict anchor conditioning" },
      coverage: { gender: "neutral", age: "exact", archetype: "exact" },
      comparison_required: true,
      comparison_reasons: ["role_match", "same_role_voice_identity", "anchor_audio_quality"],
      candidate_ids: candidates.map((candidate) => candidate.id),
      candidates,
    };
  });
  const bundle: TestBundle = {
    format_version: 2,
    protocol: "role-review-v2",
    phase: "anchor",
    plan_sha256: hash("plan"),
    candidate_set_sha256: hash("candidate-set"),
    groups,
  };
  writeBundle(bundleRoot, bundle);
  return bundle;
}

function writeBundle(bundleRoot: string, bundle: TestBundle): void {
  const diskBundle = structuredClone(bundle);
  for (const group of diskBundle.groups) {
    for (const candidate of group.candidates) {
      delete candidate.audio_contents;
    }
  }
  writeFileSync(path.join(bundleRoot, BUNDLE_FILE), canonicalJsonBytes(diskBundle));
}

function makeDraft(bundle: TestBundle): Record<string, unknown> {
  return {
    format_version: 2,
    protocol: "role-review-draft-v2",
    phase: "anchor",
    plan_sha256: bundle.plan_sha256,
    candidate_set_sha256: bundle.candidate_set_sha256,
    current_group_id: bundle.groups[0]!.id,
    groups: bundle.groups.map((group) => ({
      id: group.id,
      model: group.model,
      scenario: group.scenario,
      character: group.character,
      line: null,
      role_epoch_sha256: group.role_epoch_sha256,
      group_sha256: groupHash(group),
      heard_candidate_ids: [],
      selected_candidate_id: null,
      no_usable_candidate: false,
      rubric: emptyRubric(),
      confirmed: false,
    })),
  };
}

function makeCompletedDraft(bundle: TestBundle): Record<string, unknown> {
  const decision = makeDecision(bundle);
  return {
    ...decision,
    protocol: "role-review-draft-v2",
    current_group_id: bundle.groups.at(-1)!.id,
  };
}

function makeDecision(bundle: TestBundle): Record<string, unknown> {
  return {
    format_version: 2,
    protocol: "role-review-decision-v2",
    phase: "anchor",
    plan_sha256: bundle.plan_sha256,
    candidate_set_sha256: bundle.candidate_set_sha256,
    groups: bundle.groups.map((group) => ({
      id: group.id,
      model: group.model,
      scenario: group.scenario,
      character: group.character,
      line: null,
      role_epoch_sha256: group.role_epoch_sha256,
      group_sha256: groupHash(group),
      heard_candidate_ids: [...group.candidate_ids],
      selected_candidate_id: group.candidate_ids[0],
      no_usable_candidate: false,
      rubric: completeRubric(),
      confirmed: true,
    })),
  };
}

function groupHash(group: TestGroup): string {
  const diskGroup = structuredClone(group);
  for (const candidate of diskGroup.candidates) {
    delete candidate.audio_contents;
  }
  return hash(canonicalJsonBytes(diskGroup));
}

function emptyRubric(): Record<string, unknown> {
  return {
    content: null,
    prompt_leakage: null,
    reading: null,
    pitch_accent: null,
    gender: null,
    age: null,
    archetype: null,
    voice_identity: null,
    delivery: null,
    naturalness_quality: null,
    notes: "",
  };
}

function completeRubric(): Record<string, unknown> {
  return {
    content: "pass",
    prompt_leakage: "pass",
    reading: "pass",
    pitch_accent: "pass",
    gender: "pass",
    age: "pass",
    archetype: "pass",
    voice_identity: "not_applicable",
    delivery: "not_applicable",
    naturalness_quality: 4,
    notes: "",
  };
}

function firstBundleCandidate(bundle: TestBundle): TestCandidate {
  return bundle.groups[0]!.candidates[0]!;
}

function firstDecisionGroup(decision: Record<string, unknown>): {
  heard_candidate_ids: string[];
} {
  return (decision.groups as Array<{ heard_candidate_ids: string[] }>)[0]!;
}

async function putDraft(
  origin: string,
  draft: Record<string, unknown>,
  revision: number,
  requestOrigin: string,
  token: string,
): Promise<Response> {
  return fetch(`${origin}/__gaya-listening/draft`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Origin: requestOrigin,
      [MUTATION_TOKEN_HEADER]: token,
    },
    body: JSON.stringify({ revision, draft }),
  });
}

async function finalize(
  origin: string,
  decision: Record<string, unknown>,
  revision: number,
): Promise<Response> {
  return fetch(`${origin}/__gaya-listening/finalize`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Origin: origin,
      [MUTATION_TOKEN_HEADER]: TOKEN,
    },
    body: JSON.stringify({ revision, decision }),
  });
}

function beginDelayedPutDraft(
  origin: string,
  draft: Record<string, unknown>,
  revision: number,
): {
  readonly finish: () => void;
  readonly response: Promise<{ readonly status: number; readonly body: string }>;
} {
  const body = JSON.stringify({ revision, draft });
  let finish = () => undefined;
  const response = new Promise<{ readonly status: number; readonly body: string }>(
    (resolve, reject) => {
      const request = createHttpRequest(
        `${origin}/__gaya-listening/draft`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "Content-Length": Buffer.byteLength(body),
            Origin: origin,
            [MUTATION_TOKEN_HEADER]: TOKEN,
          },
        },
        (incoming) => {
          const chunks: Buffer[] = [];
          incoming.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
          incoming.on("end", () =>
            resolve({
              status: incoming.statusCode ?? 0,
              body: Buffer.concat(chunks).toString("utf8"),
            }),
          );
        },
      );
      request.once("error", reject);
      request.write(body.slice(0, 1));
      finish = () => {
        request.end(body.slice(1));
      };
    },
  );
  return { finish: () => finish(), response };
}

function assertCanonicalResult(
  outputRoot: string,
  filename: string,
  expected: Record<string, unknown>,
  hasMarker: boolean,
): void {
  const bytes = readFileSync(path.join(outputRoot, filename));
  expect(bytes).toEqual(canonicalJsonBytes(expected));
  const markerPath = path.join(outputRoot, filename.replace(/\.json$/, ".sha256"));
  if (hasMarker) {
    expect(readFileSync(markerPath, "ascii")).toBe(`${hash(bytes)}\n`);
  } else {
    expect(() => readFileSync(markerPath)).toThrow();
  }
}

function hash(value: string | NodeJS.ArrayBufferView): string {
  return createHash("sha256").update(value).digest("hex");
}

async function unusedPort(): Promise<number> {
  const server = createHttpServer();
  await listen(server, 0);
  const address = server.address();
  if (address === null || typeof address === "string") {
    await close(server);
    throw new Error("test portを取得できません。");
  }
  const port = address.port;
  await close(server);
  return port;
}

function listen(server: Server, port: number): Promise<void> {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
}

function close(server: Server): Promise<void> {
  return new Promise((resolve, reject) => {
    server.close((reason) => (reason ? reject(reason) : resolve()));
  });
}

function processOutput(result: {
  readonly error?: Error;
  readonly stderr: string | null;
  readonly stdout: string | null;
}): string {
  return [result.error?.message, result.stdout, result.stderr]
    .filter((value): value is string => Boolean(value))
    .join("\n");
}
