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
  ANCHOR_BUNDLE_FILE,
  ANCHOR_DRAFT_FILE,
  ANCHOR_FINAL_FILE,
  ANCHOR_WORKFLOW,
  BASELINE_DRAFT_FILE,
  BASELINE_FINAL_FILE,
  BASELINE_WORKFLOW,
  assertAuthorityPlanBoundary,
  assertListeningDirectoryBoundaries,
  canonicalJsonBytes,
  createListeningRuntime,
  MUTATION_TOKEN_HEADER,
  LISTENING_PROTOCOL,
  LISTENING_STATE_DIR,
  readListeningPlanAuthority,
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
      const validated = await validateListeningBundle(ANCHOR_WORKFLOW, bundleRoot, null);
      expect(validated.candidates.size).toBe(424);

      writeFileSync(
        path.join(bundleRoot, ANCHOR_BUNDLE_FILE),
        `${canonicalJsonBytes(bundle).toString()}\n`,
      );
      await expect(validateListeningBundle(ANCHOR_WORKFLOW, bundleRoot, null)).rejects.toThrow(
        "canonical JSON",
      );
    });
  });

  it("音声SHA改ざん、path traversal、任意extra fileを拒否する", async () => {
    await withFixture(async ({ bundleRoot, bundle }) => {
      const firstCandidate = firstBundleCandidate(bundle);
      writeFileSync(path.join(bundleRoot, firstCandidate.audio_path), "tampered");
      await expect(validateListeningBundle(ANCHOR_WORKFLOW, bundleRoot, null)).rejects.toThrow(
        "SHA-256",
      );
    });

    await withFixture(async ({ bundleRoot, bundle }) => {
      firstBundleCandidate(bundle).audio_path = "audio/../escape.wav";
      writeBundle(bundleRoot, bundle);
      await expect(validateListeningBundle(ANCHOR_WORKFLOW, bundleRoot, null)).rejects.toThrow(
        "安全なrelative POSIX",
      );
    });

    await withFixture(async ({ bundleRoot }) => {
      writeFileSync(path.join(bundleRoot, "unexpected.txt"), "unexpected");
      await expect(validateListeningBundle(ANCHOR_WORKFLOW, bundleRoot, null)).rejects.toThrow(
        "file set",
      );
    });
  }, 15_000);

  it("model内のrole座標重複とmodel間の座標差を拒否する", async () => {
    await withFixture(async ({ bundleRoot, bundle }) => {
      bundle.groups[1]!.scenario = bundle.groups[0]!.scenario;
      bundle.groups[1]!.character = bundle.groups[0]!.character;
      writeBundle(bundleRoot, bundle);
      await expect(validateListeningBundle(ANCHOR_WORKFLOW, bundleRoot, null)).rejects.toThrow(
        "role座標が重複",
      );
    });

    await withFixture(async ({ bundleRoot, bundle }) => {
      bundle.groups[53]!.scenario = "scene-999";
      bundle.groups.sort((left, right) =>
        [left.model, left.scenario, left.character]
          .join("/")
          .localeCompare([right.model, right.scenario, right.character].join("/"), "en"),
      );
      writeBundle(bundleRoot, bundle);
      await expect(validateListeningBundle(ANCHOR_WORKFLOW, bundleRoot, null)).rejects.toThrow(
        "同じ53 role座標集合",
      );
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
      await expect(
        validateListeningBundle(ANCHOR_WORKFLOW, bundleRoot, null),
      ).resolves.toBeDefined();

      bundle.groups[0]!.candidates[1]!.attempt = 5;
      writeBundle(bundleRoot, bundle);
      await expect(validateListeningBundle(ANCHOR_WORKFLOW, bundleRoot, null)).rejects.toThrow(
        "一意な昇順正整数",
      );
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

  it.runIf(process.platform === "win32")(
    "Windows pathのdrive/directory大小文字が違ってもauthority boundaryを拒否する",
    () => {
      const bundle = path.join(tmpdir(), "Authority-Bundle");
      const output = path.join(tmpdir(), "Authority-Output");
      expect(() =>
        assertListeningDirectoryBoundaries(swapPathCase(bundle), path.join(bundle, "results")),
      ).toThrow("互いに独立");
      expect(() =>
        assertAuthorityPlanBoundary(swapPathCase(path.join(bundle, "plan.json")), bundle, output),
      ).toThrow("bundle boundary");
      expect(() =>
        assertAuthorityPlanBoundary(swapPathCase(path.join(output, "plan.json")), bundle, output),
      ).toThrow("output boundary");
      expect(() =>
        assertAuthorityPlanBoundary(
          swapPathCase(path.join(SITE_ROOT, "plan.json")),
          bundle,
          output,
        ),
      ).toThrow("site boundary");
      expect(() =>
        assertAuthorityPlanBoundary(
          swapPathCase(path.join(LISTENING_STATE_DIR, "plan.json")),
          bundle,
          output,
        ),
      ).toThrow("listening session boundary");
      expect(() =>
        assertAuthorityPlanBoundary(path.join(tmpdir(), "independent-plan.json"), bundle, output),
      ).not.toThrow();
    },
  );

  it("Phase B authority planをbundle/output/site stateから分離しcanonical bytesで固定する", async () => {
    await withBaselineFixture(
      async ({ bundleRoot, outputRoot, authorityPlanPath, expectedPlanSha256 }) => {
        await expect(validateListeningBundle(BASELINE_WORKFLOW, bundleRoot, null)).rejects.toThrow(
          "外部authority plan",
        );
        await expect(
          readListeningPlanAuthority({
            authorityPlanPath: path.join(bundleRoot, "completion-plan.json"),
            bundleRoot,
            outputRoot,
          }),
        ).rejects.toThrow("bundle boundary");

        const nonCanonical = path.join(path.dirname(authorityPlanPath), "noncanonical-plan.json");
        writeFileSync(nonCanonical, `${readFileSync(authorityPlanPath, "utf8")}\n`);
        await expect(
          readListeningPlanAuthority({
            authorityPlanPath: nonCanonical,
            bundleRoot,
            outputRoot,
          }),
        ).rejects.toThrow("canonical JSON bytes");

        writeFileSync(path.join(bundleRoot, "manifest-v4.json"), "not-json");
        writeFileSync(path.join(bundleRoot, "completion-plan.json"), canonicalJsonBytes({}));
        await expect(
          validateListeningBundle(BASELINE_WORKFLOW, bundleRoot, expectedPlanSha256),
        ).rejects.toThrow("外部authorityと一致しません");
      },
    );
  }, 30_000);

  it("Python canonical JSONの1.0をraw artifactの正規表現として受理する", async () => {
    await withBaselineFixture(async ({ bundleRoot, expectedPlanSha256 }) => {
      const manifestPath = path.join(bundleRoot, "manifest-v4.json");
      const manifest = readFileSync(manifestPath, "utf8");
      const pythonCanonicalFloat = manifest.replace('"duration_sec":1,', '"duration_sec":1.0,');
      expect(pythonCanonicalFloat).not.toBe(manifest);
      writeFileSync(manifestPath, pythonCanonicalFloat);

      await expect(
        validateListeningBundle(BASELINE_WORKFLOW, bundleRoot, expectedPlanSha256),
      ).resolves.toBeDefined();
    });
  }, 30_000);

  it("anchor workflowはauthority plan指定を拒否する", async () => {
    await withFixture(async ({ root, bundleRoot, outputRoot }) => {
      const authorityPlanPath = path.join(root, "authority-plan.json");
      writeFileSync(authorityPlanPath, canonicalJsonBytes({ format_version: 1 }));
      const rejected = spawnSync(
        process.execPath,
        [
          CLI_SCRIPT,
          "start",
          "--workflow",
          ANCHOR_WORKFLOW,
          "--bundle",
          bundleRoot,
          "--output",
          outputRoot,
          "--authority-plan",
          authorityPlanPath,
        ],
        { encoding: "utf8", timeout: 10_000 },
      );
      expect(rejected.status).toBe(1);
      expect(rejected.stderr).toContain("受け付けません");
    });
  });

  it("四候補すべて使用不可のdecisionは理由を必須にして候補を偽造しない", async () => {
    await withFixture(async ({ bundleRoot, bundle }) => {
      const validated = await validateListeningBundle(ANCHOR_WORKFLOW, bundleRoot, null);
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
      const validated = await validateListeningBundle(ANCHOR_WORKFLOW, bundleRoot, null);
      const decision = makeDecision(bundle);
      const first = (decision.groups as Array<{ rubric: Record<string, unknown> }>)[0]!;
      first.rubric.gender = "fail";

      expect(() => validateDecisionDocument(decision, validated)).toThrow("gender=pass");
    });
  });

  it("Phase Bのcandidate自己申告provenanceとdummy単一model planを拒否する", async () => {
    await withBaselineFixture(async ({ bundleRoot, expectedPlanSha256 }) => {
      const candidateSet = JSON.parse(
        readFileSync(path.join(bundleRoot, "candidate-set.json"), "utf8"),
      ) as Record<string, unknown>;
      const manifest = JSON.parse(
        readFileSync(path.join(bundleRoot, "manifest-v4.json"), "utf8"),
      ) as Record<string, unknown>;
      const candidate = (candidateSet.candidates as Record<string, unknown>[])[0]!;
      const manifestCandidate = (manifest.candidates as Record<string, unknown>[])[0]!;
      const tamper = (value: Record<string, unknown>) => {
        const params = value.gen_params as Record<string, unknown>;
        const requested = params.requested as Record<string, unknown>;
        const provenance = requested.phase_b_provenance as Record<string, unknown>;
        provenance.plan_sha256 = "0".repeat(64);
      };
      tamper(candidate);
      tamper(manifestCandidate);
      const candidateSetBytes = canonicalJsonBytes(candidateSet);
      const candidateSetSha = hash(candidateSetBytes);
      manifest.candidate_set_sha256 = candidateSetSha;
      const sourceMap = JSON.parse(
        readFileSync(path.join(bundleRoot, "phase-b-source-map-v1.json"), "utf8"),
      ) as Record<string, unknown>;
      sourceMap.candidate_set_sha256 = candidateSetSha;
      const sourceMapBytes = canonicalJsonBytes(sourceMap);
      writeFileSync(path.join(bundleRoot, "candidate-set.json"), candidateSetBytes);
      writeFileSync(path.join(bundleRoot, "candidate-set.sha256"), candidateSetSha);
      writeFileSync(path.join(bundleRoot, "manifest-v4.json"), canonicalJsonBytes(manifest));
      writeFileSync(path.join(bundleRoot, "phase-b-source-map-v1.json"), sourceMapBytes);
      writeFileSync(path.join(bundleRoot, "phase-b-source-map-v1.sha256"), hash(sourceMapBytes));
      await expect(
        validateListeningBundle(BASELINE_WORKFLOW, bundleRoot, expectedPlanSha256),
      ).rejects.toThrow("provenance");
    });

    await withBaselineFixture(async ({ bundleRoot }) => {
      const plan = JSON.parse(
        readFileSync(path.join(bundleRoot, "completion-plan.json"), "utf8"),
      ) as Record<string, unknown>;
      (plan.models as Record<string, unknown>[])[0]!.id = "dummy-0.5";
      const planBytes = canonicalJsonBytes(plan);
      const planSha = hash(planBytes);
      const sourceMap = JSON.parse(
        readFileSync(path.join(bundleRoot, "phase-b-source-map-v1.json"), "utf8"),
      ) as Record<string, unknown>;
      sourceMap.plan_sha256 = planSha;
      const sourceMapBytes = canonicalJsonBytes(sourceMap);
      writeFileSync(path.join(bundleRoot, "completion-plan.json"), planBytes);
      writeFileSync(path.join(bundleRoot, "completion-plan.sha256"), planSha);
      writeFileSync(path.join(bundleRoot, "phase-b-source-map-v1.json"), sourceMapBytes);
      writeFileSync(path.join(bundleRoot, "phase-b-source-map-v1.sha256"), hash(sourceMapBytes));
      await expect(validateListeningBundle(BASELINE_WORKFLOW, bundleRoot, planSha)).rejects.toThrow(
        "固定8 model",
      );
    });
  }, 30_000);
});

describe("listening REST API", () => {
  it("role-baseline-v1を明示してPhase B bundleを検証・保存・自動回収可能なfinalへ固定する", async () => {
    await withBaselineFixture(
      async ({ bundleRoot, outputRoot, authorityPlanPath, expectedPlanSha256 }) => {
        const port = await unusedPort();
        const runtime = await createListeningRuntime({
          workflow: BASELINE_WORKFLOW,
          bundleDirectory: bundleRoot,
          outputDirectory: outputRoot,
          authorityPlanPath,
          expectedPlanSha256,
          port,
          mutationToken: TOKEN,
        });
        const server = createHttpServer((request, response) =>
          runtime.api.handle(request, response),
        );
        await listen(server, port);
        const origin = `http://127.0.0.1:${port}`;
        try {
          const bootstrapResponse = await fetch(`${origin}/__gaya-listening/bootstrap`);
          const bootstrap = (await bootstrapResponse.json()) as Record<string, unknown>;
          expect(bootstrap).toMatchObject({
            workflow: BASELINE_WORKFLOW,
            output: {
              draft_file: BASELINE_DRAFT_FILE,
              decision_file: BASELINE_FINAL_FILE,
            },
          });
          const health = (await (
            await fetch(`${origin}/__gaya-listening/health`)
          ).json()) as Record<string, unknown>;
          expect(health).toMatchObject({
            authority_plan: authorityPlanPath,
            expected_plan_sha256: expectedPlanSha256,
          });
          const bundle = bootstrap.bundle as Record<string, unknown>;
          const draft = makeBaselineDraft(bundle);
          const unheard = structuredClone(draft);
          (unheard.groups as Record<string, unknown>[])[0]!.heard_candidate_ids = [];
          const rejectedUnheard = await putDraft(origin, unheard, 0, origin, TOKEN);
          expect(rejectedUnheard.status).toBe(400);
          expect(await rejectedUnheard.text()).toContain("完全再生");
          const saved = await putDraft(origin, draft, 0, origin, TOKEN);
          expect(saved.status, await saved.clone().text()).toBe(200);
          assertCanonicalResult(outputRoot, BASELINE_DRAFT_FILE, draft, false);

          const decision = makeBaselineDecision(bundle, draft);
          const finalized = await finalize(origin, decision, 1);
          expect(finalized.status, await finalized.clone().text()).toBe(200);
          assertCanonicalResult(outputRoot, BASELINE_FINAL_FILE, decision, true);

          const candidateId = (
            (bundle.groups as Record<string, unknown>[])[0]!.candidates as Record<string, unknown>[]
          )[0]!.take_id as string;
          expect((await fetch(`${origin}/__gaya-listening/audio/${candidateId}`)).status).toBe(200);
        } finally {
          await close(server);
        }
      },
    );
  }, 30_000);

  it("startはworkflow省略やbundle種別の取り違えをfail fastする", async () => {
    await withFixture(async ({ bundleRoot, outputRoot }) => {
      const missing = spawnSync(
        process.execPath,
        [CLI_SCRIPT, "start", "--bundle", bundleRoot, "--output", outputRoot],
        { encoding: "utf8", timeout: 10_000 },
      );
      expect(missing.status).toBe(1);
      expect(missing.stderr).toContain("--workflow");
      const missingAuthority = spawnSync(
        process.execPath,
        [
          CLI_SCRIPT,
          "start",
          "--workflow",
          BASELINE_WORKFLOW,
          "--bundle",
          bundleRoot,
          "--output",
          outputRoot,
        ],
        { encoding: "utf8", timeout: 10_000 },
      );
      expect(missingAuthority.status).toBe(1);
      expect(missingAuthority.stderr).toContain("--authority-plan");
      await expect(
        validateListeningBundle(BASELINE_WORKFLOW, bundleRoot, "0".repeat(64)),
      ).rejects.toThrow("completion-plan.json");
    });
  });

  it("Origin/token、revision、finalize gate、atomic result、final freezeを強制する", async () => {
    await withFixture(async ({ root, bundleRoot, outputRoot, bundle }) => {
      const port = await unusedPort();
      const runtime = await createListeningRuntime({
        workflow: ANCHOR_WORKFLOW,
        bundleDirectory: bundleRoot,
        outputDirectory: outputRoot,
        authorityPlanPath: null,
        expectedPlanSha256: null,
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
        assertCanonicalResult(outputRoot, ANCHOR_DRAFT_FILE, draft, false);

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
        expect(await unbound.text()).toContain("判断が完了していません");

        const completedDraft = makeCompletedDraft(bundle);
        const completedSaved = await putDraft(origin, completedDraft, 1, origin, TOKEN);
        expect(completedSaved.status).toBe(200);
        expect(await completedSaved.json()).toMatchObject({ revision: 2 });
        assertCanonicalResult(outputRoot, ANCHOR_DRAFT_FILE, completedDraft, false);

        const mismatched = structuredClone(decision);
        (mismatched.groups as Array<Record<string, unknown>>)[0]!.selected_candidate_id =
          bundle.groups[0]!.candidate_ids[1];
        const rejectedMismatch = await finalize(origin, mismatched, 2);
        expect(rejectedMismatch.status).toBe(409);
        expect(await rejectedMismatch.text()).toContain("保存済みdraft");

        const finalized = await finalize(origin, decision, 2);
        expect(finalized.status, await finalized.clone().text()).toBe(200);
        assertCanonicalResult(outputRoot, ANCHOR_FINAL_FILE, decision, true);

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
      writeFileSync(path.join(outputRoot, ANCHOR_FINAL_FILE), canonicalJsonBytes(decision));
      const port = await unusedPort();

      await expect(
        createListeningRuntime({
          workflow: ANCHOR_WORKFLOW,
          bundleDirectory: bundleRoot,
          outputDirectory: outputRoot,
          authorityPlanPath: null,
          expectedPlanSha256: null,
          port,
          mutationToken: TOKEN,
        }),
      ).rejects.toThrow("保存済みdraft");

      writeFileSync(
        path.join(outputRoot, ANCHOR_DRAFT_FILE),
        canonicalJsonBytes(makeCompletedDraft(bundle)),
      );
      const runtime = await createListeningRuntime({
        workflow: ANCHOR_WORKFLOW,
        bundleDirectory: bundleRoot,
        outputDirectory: outputRoot,
        authorityPlanPath: null,
        expectedPlanSha256: null,
        port,
        mutationToken: TOKEN,
      });

      expect(runtime.api.snapshot().finalized).toBe(true);
      assertCanonicalResult(outputRoot, ANCHOR_FINAL_FILE, decision, true);
    });
  });

  it("shutdown開始後の遅延mutationを拒否して既存queueだけをdrainする", async () => {
    await withFixture(async ({ bundleRoot, outputRoot, bundle }) => {
      const port = await unusedPort();
      const runtime = await createListeningRuntime({
        workflow: ANCHOR_WORKFLOW,
        bundleDirectory: bundleRoot,
        outputDirectory: outputRoot,
        authorityPlanPath: null,
        expectedPlanSha256: null,
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
            "--workflow",
            ANCHOR_WORKFLOW,
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

  it("Phase B start/status/health/sessionを外部authority plan pathとSHAへ固定する", async () => {
    await withBaselineFixture(
      async ({ bundleRoot, outputRoot, authorityPlanPath, expectedPlanSha256 }) => {
        const port = await unusedPort();
        try {
          const started = spawnSync(
            process.execPath,
            [
              CLI_SCRIPT,
              "start",
              "--workflow",
              BASELINE_WORKFLOW,
              "--bundle",
              bundleRoot,
              "--output",
              outputRoot,
              "--authority-plan",
              authorityPlanPath,
              "--port",
              String(port),
            ],
            { encoding: "utf8", timeout: 30_000 },
          );
          expect(started.status, processOutput(started)).toBe(0);

          const session = JSON.parse(readFileSync(SESSION_FILE, "utf8")) as Record<string, unknown>;
          expect(session).toMatchObject({
            workflow: BASELINE_WORKFLOW,
            authority_plan: authorityPlanPath,
            expected_plan_sha256: expectedPlanSha256,
          });
          const health = await fetch(`http://127.0.0.1:${port}/__gaya-listening/health`);
          expect(await health.json()).toMatchObject({
            authority_plan: authorityPlanPath,
            expected_plan_sha256: expectedPlanSha256,
          });

          const status = spawnSync(process.execPath, [CLI_SCRIPT, "status"], {
            encoding: "utf8",
            timeout: 10_000,
          });
          expect(status.status, processOutput(status)).toBe(0);
          expect(status.stdout).toContain(`authority plan: ${authorityPlanPath}`);
          expect(status.stdout).toContain(`expected plan SHA-256: ${expectedPlanSha256}`);
        } finally {
          const stopped = spawnSync(process.execPath, [CLI_SCRIPT, "stop"], {
            encoding: "utf8",
            timeout: 20_000,
          });
          expect(stopped.status, processOutput(stopped)).toBe(0);
        }
      },
    );
  }, 60_000);

  it("session identityを検証できない生存PIDはkillせず停止失敗にする", async () => {
    await withFixture(async ({ bundleRoot, outputRoot }) => {
      mkdirSync(LISTENING_STATE_DIR, { recursive: true });
      const port = await unusedPort();
      const session = {
        format_version: 1,
        protocol: LISTENING_PROTOCOL,
        workflow: ANCHOR_WORKFLOW,
        state: "ready",
        id: "unverified-session",
        pid: process.pid,
        port,
        origin: `http://127.0.0.1:${port}`,
        mutation_token: TOKEN,
        started_at: new Date().toISOString(),
        bundle: bundleRoot,
        output: outputRoot,
        authority_plan: null,
        expected_plan_sha256: null,
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

async function withBaselineFixture(
  run: (fixture: {
    readonly bundleRoot: string;
    readonly outputRoot: string;
    readonly authorityPlanPath: string;
    readonly expectedPlanSha256: string;
  }) => Promise<void>,
): Promise<void> {
  const root = mkdtempSync(path.join(tmpdir(), "gaya-listening-baseline-"));
  try {
    const bundleRoot = path.join(root, "bundle");
    const outputRoot = path.join(root, "output");
    mkdirSync(bundleRoot);
    mkdirSync(outputRoot);
    const modelCounts = new Map([
      ["aivisspeech-kohaku", 25],
      ["chatterbox-multilingual-v3", 13],
      ["cosyvoice3-0.5b-2512", 14],
      ["gpt-sovits-v2-pro-plus", 37],
      ["irodori-tts-600m-v3-voicedesign", 161],
      ["qwen3-tts-12hz-1.7b", 161],
      ["supertonic-3", 25],
      ["voxcpm2", 161],
    ]);
    const revisions = new Map([...modelCounts.keys()].map((model) => [model, `revision-${model}`]));
    const roles = Array.from({ length: 58 }, (_, index) => {
      const scenario = `scene-role-${String(index).padStart(3, "0")}`;
      const character = `character-${String(index).padStart(3, "0")}`;
      const role = {
        age: "adult",
        archetype: "测试角色",
        gender: index % 2 === 0 ? "male" : "female",
        kind: "human",
        name: `角色 ${index}`,
        personality: "沉着",
        voice: "清晰自然的声音",
      };
      const identity = {
        scenario,
        character,
        role,
        reference_voice: index < 53 ? null : `reference-${index}`,
        scene_setting: `测试场景 ${index}`,
      };
      return { ...identity, role_identity_sha256: hash(canonicalJsonBytes(identity)) };
    });
    const anchorPlanSha = hash("anchor-plan");
    const anchorCandidateSha = hash("anchor-candidate-set");
    const anchorGroups = [...modelCounts.keys()]
      .filter(
        (model) => model === "irodori-tts-600m-v3-voicedesign" || model === "qwen3-tts-12hz-1.7b",
      )
      .flatMap((model) =>
        roles.slice(0, 53).map((role) => {
          const anchorId = hash(`anchor:${model}:${role.scenario}:${role.character}`);
          const reviewEpoch = hash(`review-epoch:${model}:${role.scenario}:${role.character}`);
          const audioSha = hash(`anchor-audio:${model}:${role.scenario}:${role.character}`);
          const decision = {
            character: role.character,
            confirmed: true,
            group_sha256: hash(`anchor-group:${model}:${role.scenario}:${role.character}`),
            heard_candidate_ids: [anchorId],
            id: hash(`anchor-decision:${model}:${role.scenario}:${role.character}`),
            line: null,
            model,
            no_usable_candidate: false,
            role_epoch_sha256: reviewEpoch,
            rubric: completeRubric(),
            scenario: role.scenario,
            selected_candidate_id: anchorId,
          };
          const decisionSha = hash(canonicalJsonBytes(decision));
          const roleEpoch = hash(
            canonicalJsonBytes({
              anchor_id: anchorId,
              audio_sha256: audioSha,
              decision_sha256: decisionSha,
              model,
              model_revision: revisions.get(model),
              protocol: "selected-role-epoch-v1",
              review_role_epoch_sha256: reviewEpoch,
              role_identity_sha256: role.role_identity_sha256,
              scenario: role.scenario,
              character: role.character,
            }),
          );
          const roleIdentity = { ...role } as Record<string, unknown>;
          delete roleIdentity.role_identity_sha256;
          const anchorText = `锚点 ${model} ${role.character}`;
          return {
            anchor_id: anchorId,
            anchor_text: anchorText,
            anchor_text_sha256: hash(anchorText),
            attempt: 1,
            audio_path: `audio/${anchorId}.wav`,
            audio_sha256: audioSha,
            character: role.character,
            decision,
            decision_sha256: decisionSha,
            model,
            model_revision: revisions.get(model),
            review_role_epoch_sha256: reviewEpoch,
            role_epoch_sha256: roleEpoch,
            role_identity: roleIdentity,
            role_identity_sha256: role.role_identity_sha256,
            scenario: role.scenario,
            seed: 1,
          };
        }),
      )
      .sort((left, right) =>
        [left.model, left.scenario, left.character]
          .join("/")
          .localeCompare([right.model, right.scenario, right.character].join("/"), "en"),
      );
    const anchorSelection = {
      candidate_set_sha256: anchorCandidateSha,
      format_version: 1,
      groups: anchorGroups,
      plan_sha256: anchorPlanSha,
      protocol: "role-anchor-selection-v1",
    };
    const anchorSelectionBytes = canonicalJsonBytes(anchorSelection);
    const anchorSelectionSha = hash(anchorSelectionBytes);
    const targetCoordinates = [...modelCounts.entries()].flatMap(([model, count]) =>
      Array.from({ length: count }, (_, index) => ({
        model,
        scenario: roles[index % roles.length]!.scenario,
        line: `line-${String(index).padStart(3, "0")}`,
        variant: "dry",
      })).sort((left, right) =>
        [left.model, left.scenario, left.line, left.variant]
          .join("/")
          .localeCompare([right.model, right.scenario, right.line, right.variant].join("/"), "en"),
      ),
    );
    const plan = {
      anchor_authority: {
        candidate_set_sha256: anchorCandidateSha,
        selection_sha256: anchorSelectionSha,
        source_plan_sha256: anchorPlanSha,
      },
      base: {
        candidate_set_sha256: hash("base-set"),
        final_groups: 1288,
        git_blob: "a".repeat(40),
        inherited_groups: 691,
        manifest_sha256: hash("base-manifest"),
        selection_sha256: hash("base-selection"),
      },
      format_version: 2,
      models: [...modelCounts.keys()].map((id) => ({ id, revision: revisions.get(id) })),
      phase_b: {
        model_policies: [...modelCounts.keys()].map((model) => ({
          minimum_eligible_candidates: model === "aivisspeech-kohaku" ? 1 : 3,
          model,
          primary_seed_base: model === "aivisspeech-kohaku" ? null : 104,
          seed_policy: model === "aivisspeech-kohaku" ? "none" : "derived-sha256-v1",
          takes: model === "aivisspeech-kohaku" ? 1 : 4,
        })),
        targets: targetCoordinates,
      },
      protocol: "role-baseline-plan-v2",
      roles,
      sources: {
        scenario_files: [],
        scenario_registry_sha256: hash("scenario-registry"),
        voice_registry_path: "assets/voices/metadata.yaml",
        voice_registry_sha256: hash("voice-registry"),
      },
    };
    const planBytes = canonicalJsonBytes(plan);
    const planSha = hash(planBytes);
    const authorityPlanPath = path.join(root, "authority-plan.json");
    writeFileSync(authorityPlanPath, planBytes);
    const anchorEpochs = new Map(
      anchorGroups.map((group) => [
        JSON.stringify([group.model, group.scenario, group.character]),
        group.role_epoch_sha256,
      ]),
    );
    const models = [...modelCounts.keys()].map((id) => ({
      id,
      name: id,
      version: revisions.get(id),
      license_note: "",
      capabilities: {},
    }));
    const lines: Record<string, unknown>[] = [];
    const lineKeys = new Set<string>();
    const candidates: Record<string, unknown>[] = [];
    const sourceGroups: Record<string, unknown>[] = [];
    for (const [index, target] of targetCoordinates.entries()) {
      const { model, scenario, line, variant } = target;
      const role = roles.find((item) => item.scenario === scenario)!;
      const anchorEpoch = anchorEpochs.get(JSON.stringify([model, scenario, role.character]));
      const roleEpoch =
        anchorEpoch ??
        hash(
          canonicalJsonBytes({
            anchor_selection_sha256:
              model === "irodori-tts-600m-v3-voicedesign" || model === "qwen3-tts-12hz-1.7b"
                ? anchorSelectionSha
                : null,
            character: role.character,
            model,
            model_revision: revisions.get(model),
            plan_sha256: planSha,
            protocol: "phase-b-role-epoch-v1",
            reference_voice: role.reference_voice,
            role_identity_sha256: role.role_identity_sha256,
            scenario,
          }),
        );
      const provenance = {
        anchor_plan_sha256:
          model === "irodori-tts-600m-v3-voicedesign" || model === "qwen3-tts-12hz-1.7b"
            ? anchorPlanSha
            : null,
        anchor_selection_sha256:
          model === "irodori-tts-600m-v3-voicedesign" || model === "qwen3-tts-12hz-1.7b"
            ? anchorSelectionSha
            : null,
        plan_sha256: planSha,
        protocol: "phase-b-generation-v2",
        run_kind: "primary",
        supersedes_run_id: null,
        target_group: { line, model, role_epoch_sha256: roleEpoch, scenario, variant },
      };
      const lineKey = `${scenario}/${line}`;
      if (!lineKeys.has(lineKey)) {
        lineKeys.add(lineKey);
        lines.push({
          delivery: "自然に読む",
          line,
          scenario,
          scenario_title: `Scene ${scenario}`,
          text: `台詞 ${line}`,
        });
      }
      const candidateCount = model === "aivisspeech-kohaku" ? 1 : 3;
      for (let takeIndex = 1; takeIndex <= candidateCount; takeIndex += 1) {
        const audio = Buffer.from(`opus:${model}:${index}:${takeIndex}`);
        const audioSha = hash(audio);
        const inputSha = hash(`input:${index}:${takeIndex}`);
        const takeId = hash(
          `{"final_opus_sha256":"${audioSha}","generation_input_sha256":"${inputSha}"}`,
        );
        const localAudio = path.join(
          bundleRoot,
          "audio",
          model,
          scenario,
          line,
          variant,
          `take-${String(takeIndex).padStart(4, "0")}.opus`,
        );
        mkdirSync(path.dirname(localAudio), { recursive: true });
        writeFileSync(localAudio, audio);
        candidates.push({
          duration_sec: 1,
          gate: { content: "pass", mechanical: "pass", policy_version: "take-gates-v2" },
          gen_params: {
            realized: { phase_b_provenance: provenance },
            recipe_version: "test-v1",
            requested: { phase_b_provenance: provenance },
            sampling: {},
            seed: model === "aivisspeech-kohaku" ? null : index * 10 + takeIndex,
          },
          generation_input_sha256: inputSha,
          line,
          loudness: { i_lufs: -18, shortfall: false, source: "encoded_opus", tp_dbtp: -1 },
          model,
          path: `audio/takes/${model}/${scenario}/${line}/${variant}/take-${String(takeIndex).padStart(4, "0")}-${audioSha}.opus`,
          rtf: 0.1,
          scenario,
          sha256: audioSha,
          take_id: takeId,
          take_index: takeIndex,
          variant,
        });
      }
      sourceGroups.push({
        character: role.character,
        emotion: "neutral",
        intensity: 2,
        line,
        minimum_eligible_candidates: model === "aivisspeech-kohaku" ? 1 : 3,
        model,
        reading: null,
        reference_voice: role.reference_voice,
        role: role.role,
        role_epoch_sha256: roleEpoch,
        role_identity_sha256: role.role_identity_sha256,
        scenario,
        scene_setting: role.scene_setting,
        situation: "正在向附近的人说话。",
        source_run_id: "20260802T010439Z-run-001",
        variant,
      });
    }
    const candidateSet = {
      candidates,
      failures: [],
      format_version: 4,
      lines,
      models,
      scenario_sha256: hash("scenarios"),
    };
    const candidateSetBytes = canonicalJsonBytes(candidateSet);
    const candidateSetSha = hash(candidateSetBytes);
    const manifest = {
      candidate_set_sha256: candidateSetSha,
      candidates,
      curations: [],
      failures: [],
      format_version: 4,
      generated_at: "2026-08-02T00:00:00Z",
      models,
    };
    const sourceMap = {
      anchor_selection_sha256: anchorSelectionSha,
      candidate_set_sha256: candidateSetSha,
      format_version: 1,
      groups: sourceGroups,
      plan_sha256: planSha,
      protocol: "phase-b-source-map-v1",
    };
    const sourceBytes = canonicalJsonBytes(sourceMap);
    writeFileSync(path.join(bundleRoot, "candidate-set.json"), candidateSetBytes);
    writeFileSync(path.join(bundleRoot, "candidate-set.sha256"), candidateSetSha);
    writeFileSync(path.join(bundleRoot, "completion-plan.json"), planBytes);
    writeFileSync(path.join(bundleRoot, "completion-plan.sha256"), planSha);
    writeFileSync(path.join(bundleRoot, "manifest-v4.json"), canonicalJsonBytes(manifest));
    writeFileSync(path.join(bundleRoot, "role-anchor-selection-v1.json"), anchorSelectionBytes);
    writeFileSync(path.join(bundleRoot, "role-anchor-selection-v1.sha256"), anchorSelectionSha);
    writeFileSync(path.join(bundleRoot, "phase-b-source-map-v1.json"), sourceBytes);
    writeFileSync(path.join(bundleRoot, "phase-b-source-map-v1.sha256"), hash(sourceBytes));
    await run({
      bundleRoot,
      outputRoot,
      authorityPlanPath,
      expectedPlanSha256: planSha,
    });
  } finally {
    rmSync(root, { force: true, recursive: true });
  }
}

function makeBaselineDraft(bundle: Record<string, unknown>): Record<string, unknown> {
  const rubric = {
    accent_naturalness: 4,
    adoptable: true,
    audio_quality: 4,
    content_correct: true,
    delivery_match: 4,
    notes: "",
    prompt_leakage: false,
    reading_correct: true,
    role_match: 4,
  };
  return {
    anchor_selection_sha256: bundle.anchor_selection_sha256,
    candidate_set_sha256: bundle.candidate_set_sha256,
    format_version: 1,
    groups: (bundle.groups as Record<string, unknown>[]).map((group) => ({
      anchor_selection_sha256: bundle.anchor_selection_sha256,
      candidate_set_sha256: bundle.candidate_set_sha256,
      candidates: (group.export_candidates as Record<string, unknown>[]).map((candidate) => ({
        rubric,
        take_id: candidate.take_id,
      })),
      decision: {
        take_id: (group.export_candidates as Record<string, unknown>[])[0]!.take_id,
        type: "selected",
      },
      group_sha256: group.group_sha256,
      heard_candidate_ids: (group.export_candidates as Record<string, unknown>[]).map(
        (candidate) => candidate.take_id,
      ),
      line: group.line,
      model: group.model,
      plan_sha256: bundle.plan_sha256,
      revalidation_reason: null,
      role_epoch_sha256: group.role_epoch_sha256,
      scenario: group.scenario,
      variant: group.variant,
    })),
    plan_sha256: bundle.plan_sha256,
    protocol: "role-baseline-draft-v1",
  };
}

function makeBaselineDecision(
  bundle: Record<string, unknown>,
  draft: Record<string, unknown>,
): Record<string, unknown> {
  const draftGroups = draft.groups as Record<string, unknown>[];
  return {
    anchor_selection_sha256: bundle.anchor_selection_sha256,
    candidate_set_sha256: bundle.candidate_set_sha256,
    format_version: 1,
    groups: (bundle.groups as Record<string, unknown>[]).map((group, index) => ({
      authority: {
        minimum_eligible_candidates: group.minimum_eligible_candidates,
        policy_version: "missing-slot-best-of-n-v1",
        reviewer: "owner",
        type: "best_available",
      },
      candidates: (group.export_candidates as Record<string, unknown>[]).map((candidate) => ({
        ...candidate,
        rubric: (draftGroups[index]!.candidates as Record<string, unknown>[])[0]!.rubric,
      })),
      decision: draftGroups[index]!.decision,
      group_sha256: group.group_sha256,
      line: group.line,
      model: group.model,
      role_epoch_sha256: group.role_epoch_sha256,
      scenario: group.scenario,
      variant: group.variant,
    })),
    plan_sha256: bundle.plan_sha256,
    protocol: "role-baseline-decision-v1",
  };
}

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
  writeFileSync(path.join(bundleRoot, ANCHOR_BUNDLE_FILE), canonicalJsonBytes(diskBundle));
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

function swapPathCase(value: string): string {
  return value.replace(/[a-z]/gi, (character) =>
    character === character.toLowerCase() ? character.toUpperCase() : character.toLowerCase(),
  );
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
