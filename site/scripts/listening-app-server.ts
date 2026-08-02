import { createHash, randomBytes, randomUUID } from "node:crypto";
import {
  appendFileSync,
  closeSync,
  createReadStream,
  existsSync,
  lstatSync,
  openSync,
  readFileSync,
  readdirSync,
  renameSync,
  unlinkSync,
  writeSync,
  fsyncSync,
} from "node:fs";
import { open, readFile, rename, stat, unlink } from "node:fs/promises";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createServer, type Plugin, type ViteDevServer } from "vite-plus";

import { assertCanonicalJsonBytes } from "../src/lib/canonical-json.ts";

export const LISTENING_HOST = "127.0.0.1";
export const LISTENING_PROTOCOL = "gaya-listening-session-v1";
export const ANCHOR_WORKFLOW = "role-review-anchor-v2";
export const BASELINE_WORKFLOW = "role-baseline-v1";
export const QUALITY_REVIEW_WORKFLOW = "role-quality-review-v1";
export type ListeningWorkflow =
  | typeof ANCHOR_WORKFLOW
  | typeof BASELINE_WORKFLOW
  | typeof QUALITY_REVIEW_WORKFLOW;
export const ANCHOR_BUNDLE_FILE = "role-review-v2.json";
export const ANCHOR_DRAFT_FILE = "role-review-anchor-draft-v2.json";
export const ANCHOR_FINAL_FILE = "role-review-anchor-decision-v2.json";
export const BASELINE_DRAFT_FILE = "role-baseline-draft-v1.json";
export const BASELINE_FINAL_FILE = "role-baseline-decision-v1.json";
export const QUALITY_REVIEW_BUNDLE_FILE = "role-quality-review-bundle-v1.json";
export const QUALITY_REVIEW_DRAFT_FILE = "role-quality-review-draft-v1.json";
export const QUALITY_REVIEW_FINAL_FILE = "role-quality-review-result-v1.json";
export const MUTATION_TOKEN_HEADER = "x-gaya-listening-token";
export const BODY_LIMIT_BYTES = 4 * 1024 * 1024;

export const SITE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPOSITORY_ROOT = path.resolve(SITE_ROOT, "..");
export const LISTENING_STATE_DIR = path.join(REPOSITORY_ROOT, ".gaya-listening-app");
export const SESSION_FILE = path.join(LISTENING_STATE_DIR, "session.json");
export const LOG_FILE = path.join(LISTENING_STATE_DIR, "server.log");

const ROOT_KEYS = [
  "format_version",
  "protocol",
  "phase",
  "plan_sha256",
  "candidate_set_sha256",
  "groups",
] as const;
const GROUP_KEYS = [
  "id",
  "model",
  "scenario",
  "character",
  "anchor_text",
  "line",
  "role_epoch_sha256",
  "role",
  "conditioning",
  "coverage",
  "comparison_required",
  "comparison_reasons",
  "candidate_ids",
  "candidates",
] as const;
const ROLE_KEYS = ["name", "kind", "gender", "age", "archetype", "voice", "personality"] as const;
const CONDITIONING_KEYS = ["method", "summary"] as const;
const COVERAGE_KEYS = ["gender", "age", "archetype"] as const;
const CANDIDATE_KEYS = ["id", "attempt", "seed", "audio_path", "audio_sha256", "qc"] as const;
const QC_KEYS = ["mechanical", "content", "notes"] as const;
const DRAFT_ROOT_KEYS = [
  "format_version",
  "protocol",
  "phase",
  "plan_sha256",
  "candidate_set_sha256",
  "groups",
  "current_group_id",
] as const;
const DECISION_ROOT_KEYS = [
  "format_version",
  "protocol",
  "phase",
  "plan_sha256",
  "candidate_set_sha256",
  "groups",
] as const;
const DRAFT_GROUP_KEYS = [
  "id",
  "model",
  "scenario",
  "character",
  "line",
  "role_epoch_sha256",
  "group_sha256",
  "heard_candidate_ids",
  "selected_candidate_id",
  "no_usable_candidate",
  "rubric",
  "confirmed",
] as const;
const DECISION_GROUP_KEYS = [
  "id",
  "model",
  "scenario",
  "character",
  "line",
  "role_epoch_sha256",
  "group_sha256",
  "heard_candidate_ids",
  "selected_candidate_id",
  "no_usable_candidate",
  "rubric",
  "confirmed",
] as const;
const RUBRIC_KEYS = [
  "content",
  "prompt_leakage",
  "reading",
  "pitch_accent",
  "gender",
  "age",
  "archetype",
  "voice_identity",
  "delivery",
  "naturalness_quality",
  "notes",
] as const;
const SHA_PATTERN = /^[0-9a-f]{64}$/;
const SAFE_SEGMENT_PATTERN = /^[a-z0-9][a-z0-9-]*$/;
const RANGE_PATTERN = /^bytes=(\d*)-(\d*)$/;
const REVIEW_MODELS = ["irodori-tts-600m-v3-voicedesign", "qwen3-tts-12hz-1.7b"] as const;
const COMPARISON_REASONS = [
  "role_match",
  "same_role_voice_identity",
  "anchor_audio_quality",
] as const;

export interface ValidatedBundle {
  readonly workflow: ListeningWorkflow;
  readonly document: Record<string, unknown>;
  readonly root: string;
  readonly bundleSha256: string;
  readonly candidates: ReadonlyMap<string, ValidatedCandidate>;
  readonly groupBindings: readonly GroupBinding[];
}

interface ValidatedCandidate {
  readonly id: string;
  readonly path: string;
  readonly absolutePath: string;
  readonly size: number;
  readonly contentType: string;
}

interface GroupBinding {
  readonly id: string;
  readonly model: string;
  readonly scenario: string;
  readonly character: string;
  readonly roleEpochSha256: string;
  readonly groupSha256: string;
  readonly candidateIds: readonly string[];
}

export interface ListeningSessionState {
  readonly protocol: typeof LISTENING_PROTOCOL;
  readonly workflow: ListeningWorkflow;
  readonly state: "starting" | "ready";
  readonly id: string;
  readonly pid: number;
  readonly port: number;
  readonly origin: string;
  readonly mutation_token: string;
  readonly started_at: string;
  readonly bundle: string;
  readonly output: string;
  readonly authority_plan: string | null;
  readonly expected_plan_sha256: string | null;
}

export interface ListeningRuntime {
  readonly bundle: ValidatedBundle;
  readonly outputRoot: string;
  readonly origin: string;
  readonly mutationToken: string;
  readonly sessionId: string;
  readonly authorityPlanPath: string | null;
  readonly expectedPlanSha256: string | null;
  readonly api: ListeningApi;
}

export interface ListeningApi {
  handle(request: IncomingMessage, response: ServerResponse): void;
  snapshot(): { readonly revision: number; readonly finalized: boolean };
}

export class HttpError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "HttpError";
    this.status = status;
  }
}

export async function validateListeningBundle(
  workflow: ListeningWorkflow,
  bundleDirectory: string,
  expectedPlanSha256: string | null,
): Promise<ValidatedBundle> {
  if (workflow === QUALITY_REVIEW_WORKFLOW) {
    if (expectedPlanSha256 !== null) {
      throw new Error(`${QUALITY_REVIEW_WORKFLOW} は外部authority planを受け付けません。`);
    }
    return validateQualityReviewBundle(bundleDirectory);
  }
  if (workflow === BASELINE_WORKFLOW) {
    if (expectedPlanSha256 === null) {
      throw new Error(`${BASELINE_WORKFLOW} は外部authority plan SHA-256が必要です。`);
    }
    return validateBaselineListeningBundle(bundleDirectory, expectedPlanSha256);
  }
  if (expectedPlanSha256 !== null) {
    throw new Error(`${ANCHOR_WORKFLOW} はauthority planを受け付けません。`);
  }
  requireAbsoluteDirectory(bundleDirectory, "bundle");
  const root = path.resolve(bundleDirectory);
  const bundlePath = path.join(root, ANCHOR_BUNDLE_FILE);
  const raw = await readFile(bundlePath).catch((reason: unknown) => {
    throw new Error(`${ANCHOR_BUNDLE_FILE} を読めません: ${errorMessage(reason)}`);
  });
  assertCanonicalArtifactBytes(raw, ANCHOR_BUNDLE_FILE);
  let decoded: unknown;
  try {
    decoded = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
  } catch (reason: unknown) {
    throw new Error(`${ANCHOR_BUNDLE_FILE} は正しいUTF-8 JSONが必要です: ${errorMessage(reason)}`);
  }
  const document = validateBundleDocument(decoded);
  const actualPaths = listBundleFiles(root);
  const candidates = new Map<string, ValidatedCandidate>();
  const referencedPaths = new Set<string>([ANCHOR_BUNDLE_FILE]);
  const groupBindings: GroupBinding[] = [];
  for (const [groupIndex, rawGroup] of (document.groups as unknown[]).entries()) {
    const group = rawGroup as Record<string, unknown>;
    const candidateIds = group.candidate_ids as string[];
    groupBindings.push({
      id: group.id as string,
      model: group.model as string,
      scenario: group.scenario as string,
      character: group.character as string,
      roleEpochSha256: group.role_epoch_sha256 as string,
      groupSha256: sha256(canonicalJsonBytes(group, `groups[${groupIndex}]`)),
      candidateIds,
    });
    for (const candidateValue of group.candidates as Record<string, unknown>[]) {
      const id = candidateValue.id as string;
      const relativePath = candidateValue.audio_path as string;
      if (candidates.has(id)) {
        throw new Error(`candidate id がbundle内で重複しています: ${id}`);
      }
      if (referencedPaths.has(relativePath)) {
        throw new Error(`candidate audio_path がbundle内で重複しています: ${relativePath}`);
      }
      const absolutePath = safeBundleChild(root, relativePath, `candidate ${id}.audio_path`);
      const file = await stat(absolutePath).catch((reason: unknown) => {
        throw new Error(`候補音声がありません: ${relativePath}: ${errorMessage(reason)}`);
      });
      if (!file.isFile()) {
        throw new Error(`候補音声は通常ファイルが必要です: ${relativePath}`);
      }
      const audio = await readFile(absolutePath);
      const actualSha256 = sha256(audio);
      const expectedSha256 = candidateValue.audio_sha256 as string;
      if (actualSha256 !== expectedSha256) {
        throw new Error(
          `候補音声 SHA-256 が一致しません: ${relativePath} expected=${expectedSha256} actual=${actualSha256}`,
        );
      }
      referencedPaths.add(relativePath);
      candidates.set(id, {
        id,
        path: relativePath,
        absolutePath,
        size: file.size,
        contentType: audioContentType(relativePath),
      });
    }
  }
  const expectedPaths = [...referencedPaths].sort(compareText);
  if (
    actualPaths.length !== expectedPaths.length ||
    actualPaths.some((value, index) => value !== expectedPaths[index])
  ) {
    throw new Error(
      `bundle file set がexact contractと一致しません: expected=${expectedPaths.length}, actual=${actualPaths.length}`,
    );
  }
  return {
    workflow,
    document,
    root,
    bundleSha256: sha256(raw),
    candidates,
    groupBindings,
  };
}

export async function createListeningRuntime(options: {
  readonly workflow: ListeningWorkflow;
  readonly bundleDirectory: string;
  readonly outputDirectory: string;
  readonly authorityPlanPath: string | null;
  readonly expectedPlanSha256: string | null;
  readonly port: number;
  readonly mutationToken?: string;
  readonly sessionId?: string;
  readonly onShutdown?: () => void;
}): Promise<ListeningRuntime> {
  if (!Number.isInteger(options.port) || options.port < 1 || options.port > 65_535) {
    throw new Error("port は1..65535の整数が必要です。");
  }
  requireAbsoluteDirectory(options.bundleDirectory, "bundle");
  requireAbsoluteDirectory(options.outputDirectory, "output");
  const bundleRoot = path.resolve(options.bundleDirectory);
  const outputRoot = path.resolve(options.outputDirectory);
  assertListeningDirectoryBoundaries(bundleRoot, outputRoot);
  const authority = await verifyListeningPlanAuthority({
    workflow: options.workflow,
    authorityPlanPath: options.authorityPlanPath,
    expectedPlanSha256: options.expectedPlanSha256,
    bundleRoot,
    outputRoot,
  });
  const bundle = await validateListeningBundle(
    options.workflow,
    options.bundleDirectory,
    authority?.sha256 ?? null,
  );
  const origin = `http://${LISTENING_HOST}:${options.port}`;
  const mutationToken = options.mutationToken ?? randomBytes(32).toString("hex");
  const sessionId = options.sessionId ?? randomUUID();
  const api = await createListeningApi({
    bundle,
    outputRoot,
    origin,
    mutationToken,
    sessionId,
    authorityPlanPath: authority?.path ?? null,
    expectedPlanSha256: authority?.sha256 ?? null,
    onShutdown: options.onShutdown,
  });
  return {
    bundle,
    outputRoot,
    origin,
    mutationToken,
    sessionId,
    authorityPlanPath: authority?.path ?? null,
    expectedPlanSha256: authority?.sha256 ?? null,
    api,
  };
}

export function assertListeningDirectoryBoundaries(bundleRoot: string, outputRoot: string): void {
  if (directoriesOverlap(bundleRoot, outputRoot)) {
    throw new Error("bundle と output は互いに独立したdirectoryが必要です。");
  }
  if (directoriesOverlap(SITE_ROOT, bundleRoot)) {
    throw new Error("bundle は site directory と重ならない場所に置く必要があります。");
  }
  if (directoriesOverlap(SITE_ROOT, outputRoot)) {
    throw new Error("output は site directory と重ならない場所に置く必要があります。");
  }
}

export async function readListeningPlanAuthority(options: {
  readonly authorityPlanPath: string;
  readonly bundleRoot: string;
  readonly outputRoot: string;
}): Promise<{ readonly path: string; readonly sha256: string }> {
  if (!path.isAbsolute(options.authorityPlanPath)) {
    throw new Error(`--authority-plan は絶対pathが必要です: ${options.authorityPlanPath}`);
  }
  const authorityPlanPath = path.resolve(options.authorityPlanPath);
  let info;
  try {
    info = lstatSync(authorityPlanPath);
  } catch (reason: unknown) {
    throw new Error(
      `--authority-plan fileがありません: ${authorityPlanPath}: ${errorMessage(reason)}`,
    );
  }
  if (!info.isFile() || info.isSymbolicLink()) {
    throw new Error("--authority-plan は既存の通常fileが必要です。");
  }
  assertAuthorityPlanBoundary(authorityPlanPath, options.bundleRoot, options.outputRoot);
  const raw = await readFile(authorityPlanPath);
  assertCanonicalArtifactBytes(raw, "--authority-plan");
  return { path: authorityPlanPath, sha256: sha256(raw) };
}

export function assertAuthorityPlanBoundary(
  authorityPlanPath: string,
  bundleRoot: string,
  outputRoot: string,
): void {
  const authority = path.resolve(authorityPlanPath);
  for (const [label, boundary] of [
    ["bundle", bundleRoot],
    ["output", outputRoot],
    ["site", SITE_ROOT],
    ["listening session", LISTENING_STATE_DIR],
  ] as const) {
    if (pathIsInside(authority, boundary)) {
      throw new Error(`--authority-plan は ${label} boundary の外に置く必要があります。`);
    }
  }
}

async function verifyListeningPlanAuthority(options: {
  readonly workflow: ListeningWorkflow;
  readonly authorityPlanPath: string | null;
  readonly expectedPlanSha256: string | null;
  readonly bundleRoot: string;
  readonly outputRoot: string;
}): Promise<{ readonly path: string; readonly sha256: string } | null> {
  if (options.workflow === ANCHOR_WORKFLOW || options.workflow === QUALITY_REVIEW_WORKFLOW) {
    if (options.authorityPlanPath !== null || options.expectedPlanSha256 !== null) {
      throw new Error(`${options.workflow} は--authority-planを受け付けません。`);
    }
    return null;
  }
  if (options.authorityPlanPath === null || options.expectedPlanSha256 === null) {
    throw new Error(`${BASELINE_WORKFLOW} は--authority-planとexpected plan SHA-256が必要です。`);
  }
  if (!SHA_PATTERN.test(options.expectedPlanSha256)) {
    throw new Error("expected plan SHA-256は64桁の小文字hexが必要です。");
  }
  const authority = await readListeningPlanAuthority({
    authorityPlanPath: options.authorityPlanPath,
    bundleRoot: options.bundleRoot,
    outputRoot: options.outputRoot,
  });
  if (authority.sha256 !== options.expectedPlanSha256) {
    throw new Error(
      `authority plan SHA-256が起動時のexpected値と一致しません: expected=${options.expectedPlanSha256} actual=${authority.sha256}`,
    );
  }
  return authority;
}

function directoriesOverlap(left: string, right: string): boolean {
  const resolvedLeft = pathComparisonKey(left);
  const resolvedRight = pathComparisonKey(right);
  return (
    resolvedLeft === resolvedRight ||
    resolvedLeft.startsWith(`${resolvedRight}${path.sep}`) ||
    resolvedRight.startsWith(`${resolvedLeft}${path.sep}`)
  );
}

function pathIsInside(candidate: string, directory: string): boolean {
  const resolvedCandidate = pathComparisonKey(candidate);
  const resolvedDirectory = pathComparisonKey(directory);
  return (
    resolvedCandidate === resolvedDirectory ||
    resolvedCandidate.startsWith(`${resolvedDirectory}${path.sep}`)
  );
}

function pathComparisonKey(value: string): string {
  const resolved = path.resolve(value);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

export async function createListeningApi(options: {
  readonly bundle: ValidatedBundle;
  readonly outputRoot: string;
  readonly origin: string;
  readonly mutationToken: string;
  readonly sessionId: string;
  readonly authorityPlanPath: string | null;
  readonly expectedPlanSha256: string | null;
  readonly onShutdown?: () => void;
}): Promise<ListeningApi> {
  const files = workflowFiles(options.bundle.workflow);
  const draftPath = path.join(options.outputRoot, files.draft);
  const finalPath = path.join(options.outputRoot, files.final);
  const draft = await loadExistingDraft(draftPath, (value) =>
    validateWorkflowDraft(value, options.bundle),
  );
  const final = await loadExistingFinal(finalPath, (value) =>
    validateWorkflowDecision(value, options.bundle),
  );
  if (final !== null) {
    if (draft === null) {
      throw new Error("final decisionの復元には対応する保存済みdraftが必要です。");
    }
    const expectedDecision = decisionFromWorkflowDraft(draft, options.bundle);
    try {
      validateWorkflowDecision(expectedDecision, options.bundle);
    } catch {
      throw new Error("final decisionに対応するdraftは全groupの判断が完了していません。");
    }
    if (!canonicalJsonBytes(final).equals(canonicalJsonBytes(expectedDecision))) {
      throw new Error("final decisionと保存済みdraftの内容が一致しません。");
    }
  }
  let currentDraft = draft;
  let revision = draft === null ? 0 : 1;
  let finalized = final !== null;
  let shuttingDown = false;
  let mutationTail = Promise.resolve();

  function serialize<T>(operation: () => Promise<T>): Promise<T> {
    const result = mutationTail.then(operation, operation);
    mutationTail = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  async function route(request: IncomingMessage, response: ServerResponse): Promise<void> {
    const url = new URL(request.url ?? "/", options.origin);
    const method = request.method ?? "GET";
    if (url.pathname === "/__gaya-listening/health" && method === "GET") {
      sendJson(response, 200, {
        status: "ok",
        protocol: LISTENING_PROTOCOL,
        workflow: options.bundle.workflow,
        session_id: options.sessionId,
        authority_plan: options.authorityPlanPath,
        expected_plan_sha256: options.expectedPlanSha256,
        revision,
        finalized,
        shutting_down: shuttingDown,
      });
      return;
    }
    if (url.pathname === "/__gaya-listening/bootstrap" && method === "GET") {
      sendJson(response, 200, {
        format_version: 1,
        protocol: LISTENING_PROTOCOL,
        workflow: options.bundle.workflow,
        bundle: options.bundle.document,
        mutation_token: options.mutationToken,
        revision,
        finalized,
        output: {
          directory_name: path.basename(options.outputRoot),
          draft_file: files.draft,
          decision_file: files.final,
        },
      });
      return;
    }
    if (url.pathname === "/__gaya-listening/draft" && method === "GET") {
      if (currentDraft === null) {
        response.statusCode = 204;
        response.setHeader("Cache-Control", "no-store");
        response.end();
        return;
      }
      sendJson(response, 200, { revision, draft: currentDraft });
      return;
    }
    const candidateMatch = /^\/__gaya-listening\/audio\/([0-9a-f]{64})$/.exec(url.pathname);
    if (candidateMatch && (method === "GET" || method === "HEAD")) {
      serveAudio(request, response, requiredCandidate(options.bundle, candidateMatch[1]!));
      return;
    }
    if (url.pathname === "/__gaya-listening/draft" && method === "PUT") {
      assertMutationRequest(request, options.origin, options.mutationToken);
      const body = exactObject(
        await readJsonBody(request),
        ["revision", "draft"],
        "draft PUT body",
      );
      const requestedRevision = nonNegativeInteger(body.revision, "draft PUT body.revision");
      const validated = requestValidation(() => validateWorkflowDraft(body.draft, options.bundle));
      if (shuttingDown) {
        throw new HttpError(409, "listening app は停止処理中です。");
      }
      await serialize(async () => {
        if (finalized) {
          throw new HttpError(409, "finalize後はdraftを変更できません。");
        }
        if (requestedRevision !== revision) {
          throw new HttpError(409, `draft revision が一致しません: expected=${revision}`);
        }
        await writeCanonicalDraft(draftPath, validated);
        currentDraft = validated;
        revision += 1;
      });
      sendJson(response, 200, { revision, saved_at: new Date().toISOString() });
      return;
    }
    if (url.pathname === "/__gaya-listening/finalize" && method === "POST") {
      assertMutationRequest(request, options.origin, options.mutationToken);
      const body = exactObject(
        await readJsonBody(request),
        ["revision", "decision"],
        "finalize body",
      );
      const requestedRevision = nonNegativeInteger(body.revision, "finalize body.revision");
      const decision = requestValidation(() =>
        validateWorkflowDecision(body.decision, options.bundle),
      );
      if (shuttingDown) {
        throw new HttpError(409, "listening app は停止処理中です。");
      }
      await serialize(async () => {
        if (finalized) {
          throw new HttpError(409, "decision は既にfinalizeされています。");
        }
        if (requestedRevision !== revision) {
          throw new HttpError(409, `draft revision が一致しません: expected=${revision}`);
        }
        if (currentDraft === null) {
          throw new HttpError(409, "保存済みdraftがありません。");
        }
        const expectedDecision = decisionFromWorkflowDraft(currentDraft, options.bundle);
        try {
          validateWorkflowDecision(expectedDecision, options.bundle);
        } catch {
          throw new HttpError(409, "保存済みdraftは全groupの判断が完了していません。");
        }
        if (!canonicalJsonBytes(decision).equals(canonicalJsonBytes(expectedDecision))) {
          throw new HttpError(409, "decisionが保存済みdraftの内容と一致しません。");
        }
        await writeCanonicalFinal(finalPath, decision);
        finalized = true;
      });
      sendJson(response, 200, {
        revision,
        saved_at: new Date().toISOString(),
      });
      return;
    }
    if (url.pathname === "/__gaya-listening/shutdown" && method === "POST") {
      assertMutationRequest(request, options.origin, options.mutationToken);
      assertEmptyBody(request);
      if (shuttingDown) {
        throw new HttpError(409, "listening app は既に停止処理中です。");
      }
      shuttingDown = true;
      await mutationTail;
      sendJson(response, 200, { shutting_down: true });
      setImmediate(options.onShutdown ?? (() => undefined));
      return;
    }
    if (url.pathname.startsWith("/__gaya-listening/")) {
      sendJson(response, 404, { error: "listening API route がありません。" });
      return;
    }
    throw new HttpError(404, "not an API request");
  }

  return {
    handle(request, response) {
      void route(request, response).catch((reason: unknown) => {
        if (
          reason instanceof HttpError &&
          reason.status === 404 &&
          reason.message === "not an API request"
        ) {
          response.statusCode = 404;
          response.end();
          return;
        }
        const status = reason instanceof HttpError ? reason.status : 500;
        sendJson(response, status, {
          error:
            status === 500
              ? `listening server error: ${errorMessage(reason)}`
              : errorMessage(reason),
        });
      });
    },
    snapshot: () => ({ revision, finalized }),
  };
}

export async function runListeningServer(options: {
  readonly workflow: ListeningWorkflow;
  readonly bundleDirectory: string;
  readonly outputDirectory: string;
  readonly authorityPlanPath: string | null;
  readonly expectedPlanSha256: string | null;
  readonly port: number;
}): Promise<void> {
  let shutdownCallback = () => undefined;
  const runtime = await createListeningRuntime({
    ...options,
    onShutdown: () => shutdownCallback(),
  });
  const sessionId = runtime.sessionId;
  const startedAt = new Date().toISOString();
  const baseSession = {
    protocol: LISTENING_PROTOCOL,
    workflow: options.workflow,
    id: sessionId,
    pid: process.pid,
    port: options.port,
    origin: runtime.origin,
    mutation_token: runtime.mutationToken,
    started_at: startedAt,
    bundle: runtime.bundle.root,
    output: runtime.outputRoot,
    authority_plan: runtime.authorityPlanPath,
    expected_plan_sha256: runtime.expectedPlanSha256,
  } as const;
  writeNewSession({ ...baseSession, state: "starting" });
  let vite: ViteDevServer | null = null;
  let stopping = false;

  const cleanupSession = () => removeOwnedSession(sessionId);
  const shutdown = async (exitCode = 0): Promise<void> => {
    if (stopping) {
      return;
    }
    stopping = true;
    try {
      await vite?.close();
    } finally {
      cleanupSession();
      process.exitCode = exitCode;
    }
  };
  shutdownCallback = () => void shutdown();
  const plugin: Plugin = {
    name: "gaya-listening-local-api",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (!(request.url ?? "").startsWith("/__gaya-listening/")) {
          next();
          return;
        }
        runtime.api.handle(request, response);
      });
    },
  };
  const signal = (name: NodeJS.Signals) => {
    appendServerLog(`Received ${name}; shutting down listening app.`);
    console.log(`Received ${name}; shutting down listening app.`);
    void shutdown();
  };
  process.once("SIGINT", () => signal("SIGINT"));
  process.once("SIGTERM", () => signal("SIGTERM"));
  process.once("SIGHUP", () => signal("SIGHUP"));
  process.once("exit", cleanupSession);
  process.once("uncaughtException", (reason) => {
    cleanupSession();
    appendServerLog(`uncaughtException: ${errorMessage(reason)}`);
    console.error(reason);
    process.exit(1);
  });
  process.once("unhandledRejection", (reason) => {
    cleanupSession();
    appendServerLog(`unhandledRejection: ${errorMessage(reason)}`);
    console.error(reason);
    process.exit(1);
  });
  try {
    vite = await createServer({
      root: SITE_ROOT,
      configFile: path.join(SITE_ROOT, "vite.config.ts"),
      appType: "spa",
      define: {
        "import.meta.env.VITE_GAYA_LISTENING_APP": JSON.stringify("true"),
      },
      logLevel: "info",
      plugins: [plugin],
      server: {
        fs: {
          allow: [SITE_ROOT],
        },
        host: LISTENING_HOST,
        port: options.port,
        strictPort: true,
        open: false,
      },
    });
    await vite.listen();
    if (stopping) {
      await vite.close();
      return;
    }
    writeSessionAtomic({ ...baseSession, state: "ready" });
    appendServerLog(`Gaya listening app: ${runtime.origin}/internal.html#/completion`);
    console.log(`Gaya listening app: ${runtime.origin}/internal.html#/completion`);
  } catch (reason: unknown) {
    await shutdown(1);
    throw reason;
  }
}

interface WorkflowFiles {
  readonly draft: string;
  readonly final: string;
}

function workflowFiles(workflow: ListeningWorkflow): WorkflowFiles {
  if (workflow === ANCHOR_WORKFLOW) {
    return { draft: ANCHOR_DRAFT_FILE, final: ANCHOR_FINAL_FILE };
  }
  if (workflow === QUALITY_REVIEW_WORKFLOW) {
    return { draft: QUALITY_REVIEW_DRAFT_FILE, final: QUALITY_REVIEW_FINAL_FILE };
  }
  return { draft: BASELINE_DRAFT_FILE, final: BASELINE_FINAL_FILE };
}

const QUALITY_REVIEW_ROOT_KEYS = [
  "format_version",
  "protocol",
  "plan_sha256",
  "decision_sha256",
  "manifest_sha256",
  "quality_signals_sha256",
  "groups",
] as const;
const QUALITY_REVIEW_GROUP_KEYS = [
  "model",
  "scenario",
  "line",
  "variant",
  "scenario_title",
  "text",
  "delivery",
  "role",
  "take_id",
  "audio_path",
  "audio_sha256",
  "expected_gender",
  "median_f0_hz",
  "signal",
] as const;
const QUALITY_REVIEW_ROLE_KEYS = [
  "name",
  "kind",
  "gender",
  "age",
  "archetype",
  "voice",
  "personality",
] as const;
const QUALITY_REVIEW_GROUP_COUNT = 145;

async function validateQualityReviewBundle(bundleDirectory: string): Promise<ValidatedBundle> {
  requireAbsoluteDirectory(bundleDirectory, "bundle");
  const root = path.resolve(bundleDirectory);
  const bundlePath = path.join(root, QUALITY_REVIEW_BUNDLE_FILE);
  const markerName = QUALITY_REVIEW_BUNDLE_FILE.replace(".json", ".sha256");
  const raw = await readFile(bundlePath).catch((reason: unknown) => {
    throw new Error(`${QUALITY_REVIEW_BUNDLE_FILE} を読めません: ${errorMessage(reason)}`);
  });
  assertCanonicalArtifactBytes(raw, QUALITY_REVIEW_BUNDLE_FILE);
  const bundleSha256 = sha256(raw);
  if (readShaMarker(root, markerName) !== bundleSha256) {
    throw new Error(`${markerName} がbundle SHA-256と一致しません。`);
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
  } catch (reason: unknown) {
    throw new Error(
      `${QUALITY_REVIEW_BUNDLE_FILE} は正しいUTF-8 JSONが必要です: ${errorMessage(reason)}`,
    );
  }
  const document = exactObject(decoded, QUALITY_REVIEW_ROOT_KEYS, "quality review bundle");
  if (document.format_version !== 1 || document.protocol !== "role-quality-review-bundle-v1") {
    throw new Error("quality review bundle protocolが不正です。");
  }
  for (const field of [
    "plan_sha256",
    "decision_sha256",
    "manifest_sha256",
    "quality_signals_sha256",
  ] as const) {
    sha(document[field], `quality review bundle.${field}`);
  }
  if (!Array.isArray(document.groups) || document.groups.length !== QUALITY_REVIEW_GROUP_COUNT) {
    throw new Error(`quality review bundle groupsはexact ${QUALITY_REVIEW_GROUP_COUNT}件です。`);
  }
  const candidates = new Map<string, ValidatedCandidate>();
  const groupBindings: GroupBinding[] = [];
  const referencedPaths = new Set<string>([QUALITY_REVIEW_BUNDLE_FILE, markerName]);
  const coordinates = new Set<string>();
  for (const [index, value] of document.groups.entries()) {
    const label = `quality review bundle.groups[${index}]`;
    const group = exactObject(value, QUALITY_REVIEW_GROUP_KEYS, label);
    const model = nonEmptyText(group.model, `${label}.model`);
    const scenario = nonEmptyText(group.scenario, `${label}.scenario`);
    const line = nonEmptyText(group.line, `${label}.line`);
    const variant = nonEmptyText(group.variant, `${label}.variant`);
    for (const field of ["scenario_title", "text", "delivery"] as const) {
      nonEmptyText(group[field], `${label}.${field}`);
    }
    const role = exactObject(group.role, QUALITY_REVIEW_ROLE_KEYS, `${label}.role`);
    for (const field of QUALITY_REVIEW_ROLE_KEYS) {
      nonEmptyText(role[field], `${label}.role.${field}`);
    }
    const takeId = sha(group.take_id, `${label}.take_id`);
    const audioSha256 = sha(group.audio_sha256, `${label}.audio_sha256`);
    const relativePath = nonEmptyText(group.audio_path, `${label}.audio_path`);
    if (relativePath !== `audio/${takeId}.opus`) {
      throw new Error(`${label}.audio_pathがtake_idと一致しません。`);
    }
    const expectedGender = enumValue(
      group.expected_gender,
      ["female", "male"],
      `${label}.expected_gender`,
    );
    const median = group.median_f0_hz;
    if (
      median !== null &&
      (typeof median !== "number" || !Number.isFinite(median) || median <= 0)
    ) {
      throw new Error(`${label}.median_f0_hzが不正です。`);
    }
    const expectedSignal =
      median === null
        ? "gender_f0_unavailable"
        : expectedGender === "female" && median < 165
          ? "gender_f0_below_expected"
          : expectedGender === "male" && median > 180
            ? "gender_f0_above_expected"
            : null;
    if (expectedSignal === null || group.signal !== expectedSignal) {
      throw new Error(`${label}.signalがsoft F0 policyと一致しません。`);
    }
    const coordinate = JSON.stringify([model, scenario, line, variant]);
    if (coordinates.has(coordinate) || candidates.has(takeId)) {
      throw new Error(`${label}が重複しています。`);
    }
    coordinates.add(coordinate);
    const absolutePath = safeBundleChild(root, relativePath, `${label}.audio_path`);
    const info = await stat(absolutePath).catch((reason: unknown) => {
      throw new Error(`quality review audioがありません: ${errorMessage(reason)}`);
    });
    if (!info.isFile()) {
      throw new Error(`${label}.audio_pathは通常fileが必要です。`);
    }
    const audio = await readFile(absolutePath);
    if (sha256(audio) !== audioSha256) {
      throw new Error(`${label}.audio_sha256が実fileと一致しません。`);
    }
    referencedPaths.add(relativePath);
    candidates.set(takeId, {
      id: takeId,
      path: relativePath,
      absolutePath,
      size: info.size,
      contentType: audioContentType(relativePath),
    });
    groupBindings.push({
      id: coordinate,
      model,
      scenario,
      character: nonEmptyText(role.name, `${label}.role.name`),
      roleEpochSha256: document.plan_sha256 as string,
      groupSha256: sha256(canonicalJsonBytes(group, label)),
      candidateIds: [takeId],
    });
  }
  const actualPaths = listBundleFiles(root);
  const expectedPaths = [...referencedPaths].sort(compareText);
  if (
    actualPaths.length !== expectedPaths.length ||
    actualPaths.some((value, index) => value !== expectedPaths[index])
  ) {
    throw new Error("quality review bundle file setがexact contractと一致しません。 ");
  }
  return {
    workflow: QUALITY_REVIEW_WORKFLOW,
    document,
    root,
    bundleSha256,
    candidates,
    groupBindings,
  };
}

const BASELINE_METADATA_FILES = [
  "manifest-v4.json",
  "candidate-set.json",
  "candidate-set.sha256",
  "completion-plan.json",
  "completion-plan.sha256",
  "role-anchor-selection-v1.json",
  "role-anchor-selection-v1.sha256",
  "phase-b-source-map-v1.json",
  "phase-b-source-map-v1.sha256",
] as const;
const BASELINE_GROUP_COUNT = 597;
const BASELINE_SOURCE_ROOT_KEYS = [
  "format_version",
  "protocol",
  "plan_sha256",
  "anchor_selection_sha256",
  "candidate_set_sha256",
  "groups",
] as const;
const BASELINE_SOURCE_GROUP_KEYS = [
  "model",
  "scenario",
  "line",
  "variant",
  "character",
  "role_identity_sha256",
  "reference_voice",
  "role",
  "scene_setting",
  "reading",
  "situation",
  "emotion",
  "intensity",
  "role_epoch_sha256",
  "source_run_id",
  "minimum_eligible_candidates",
] as const;
const BASELINE_MANIFEST_KEYS = [
  "format_version",
  "generated_at",
  "candidate_set_sha256",
  "models",
  "candidates",
  "curations",
  "failures",
] as const;
const BASELINE_CANDIDATE_SET_KEYS = [
  "format_version",
  "scenario_sha256",
  "models",
  "lines",
  "candidates",
  "failures",
] as const;
const BASELINE_LINE_KEYS = ["scenario", "line", "scenario_title", "text", "delivery"] as const;
const BASELINE_TAKE_KEYS = [
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
const BASELINE_GATE_KEYS = ["mechanical", "content", "policy_version"] as const;
const BASELINE_ROLE_KEYS = [
  "name",
  "kind",
  "gender",
  "age",
  "archetype",
  "voice",
  "personality",
] as const;
const BASELINE_MODELS = [
  "aivisspeech-kohaku",
  "chatterbox-multilingual-v3",
  "cosyvoice3-0.5b-2512",
  "gpt-sovits-v2-pro-plus",
  "irodori-tts-600m-v3-voicedesign",
  "qwen3-tts-12hz-1.7b",
  "supertonic-3",
  "voxcpm2",
] as const;
const BASELINE_MODEL_GROUP_COUNTS = new Map<string, number>([
  ["aivisspeech-kohaku", 25],
  ["chatterbox-multilingual-v3", 13],
  ["cosyvoice3-0.5b-2512", 14],
  ["gpt-sovits-v2-pro-plus", 37],
  ["irodori-tts-600m-v3-voicedesign", 161],
  ["qwen3-tts-12hz-1.7b", 161],
  ["supertonic-3", 25],
  ["voxcpm2", 161],
]);
const BASELINE_ANCHOR_MODELS = new Set(["irodori-tts-600m-v3-voicedesign", "qwen3-tts-12hz-1.7b"]);

async function validateBaselineListeningBundle(
  bundleDirectory: string,
  expectedPlanSha256: string,
): Promise<ValidatedBundle> {
  requireAbsoluteDirectory(bundleDirectory, "bundle");
  const root = path.resolve(bundleDirectory);
  const planBytes = await readFile(path.join(root, "completion-plan.json")).catch(
    (reason: unknown) => {
      throw new Error(`completion-plan.json を読めません: ${errorMessage(reason)}`);
    },
  );
  const planSha256 = sha256(planBytes);
  if (planSha256 !== expectedPlanSha256) {
    throw new Error(
      `埋め込みcompletion-plan SHA-256が外部authorityと一致しません: expected=${expectedPlanSha256} actual=${planSha256}`,
    );
  }
  const plan = await readCanonicalObject(root, "completion-plan.json", [
    "format_version",
    "protocol",
    "base",
    "sources",
    "models",
    "roles",
    "anchor_authority",
    "phase_b",
  ]);
  const anchorSelection = await readCanonicalObject(root, "role-anchor-selection-v1.json", [
    "format_version",
    "protocol",
    "plan_sha256",
    "candidate_set_sha256",
    "groups",
  ]);
  const manifest = await readCanonicalObject(root, "manifest-v4.json", BASELINE_MANIFEST_KEYS);
  const candidateSetRaw = await readCanonicalObject(
    root,
    "candidate-set.json",
    BASELINE_CANDIDATE_SET_KEYS,
  );
  const sourceMap = await readCanonicalObject(
    root,
    "phase-b-source-map-v1.json",
    BASELINE_SOURCE_ROOT_KEYS,
  );
  if (readShaMarker(root, "completion-plan.sha256") !== planSha256) {
    throw new Error("completion-plan SHA-256 markerが埋め込みplanと一致しません。");
  }
  const anchorSelectionBytes = await readFile(path.join(root, "role-anchor-selection-v1.json"));
  const anchorSelectionSha256 = sha256(anchorSelectionBytes);
  if (readShaMarker(root, "role-anchor-selection-v1.sha256") !== anchorSelectionSha256) {
    throw new Error("role-anchor-selection SHA-256 markerが埋め込みselectionと一致しません。");
  }
  const authorities = validateBaselineAuthorities({
    plan,
    planSha256,
    anchorSelection,
    anchorSelectionSha256,
    sourceMap,
  });
  if (manifest.format_version !== 4 || candidateSetRaw.format_version !== 4) {
    throw new Error("Phase B manifest/candidate-set はformat_version=4が必要です。");
  }
  if (
    !Array.isArray(manifest.curations) ||
    manifest.curations.length !== 0 ||
    !Array.isArray(manifest.failures) ||
    manifest.failures.length !== 0 ||
    !Array.isArray(candidateSetRaw.failures) ||
    candidateSetRaw.failures.length !== 0
  ) {
    throw new Error("Phase B listening bundle はcurations 0 / failures 0が必要です。");
  }
  const candidateSetBytes = await readFile(path.join(root, "candidate-set.json"));
  const candidateSetSha256 = sha256(candidateSetBytes);
  if (
    readShaMarker(root, "candidate-set.sha256") !== candidateSetSha256 ||
    manifest.candidate_set_sha256 !== candidateSetSha256
  ) {
    throw new Error("candidate-set SHA-256がmanifest/markerと一致しません。");
  }
  if (
    sourceMap.format_version !== 1 ||
    sourceMap.protocol !== "phase-b-source-map-v1" ||
    sourceMap.candidate_set_sha256 !== candidateSetSha256
  ) {
    throw new Error("Phase B source map rootが現在のcandidate setと一致しません。");
  }
  const sourceMapBytes = await readFile(path.join(root, "phase-b-source-map-v1.json"));
  if (readShaMarker(root, "phase-b-source-map-v1.sha256") !== sha256(sourceMapBytes)) {
    throw new Error("Phase B source map SHA-256 markerが一致しません。");
  }
  if (
    canonicalJsonBytes({
      format_version: candidateSetRaw.format_version,
      models: candidateSetRaw.models,
      candidates: candidateSetRaw.candidates,
      failures: candidateSetRaw.failures,
    }).compare(
      canonicalJsonBytes({
        format_version: manifest.format_version,
        models: manifest.models,
        candidates: manifest.candidates,
        failures: manifest.failures,
      }),
    ) !== 0
  ) {
    throw new Error("candidate-setのcandidate subsetがmanifest-v4と一致しません。");
  }
  validateBaselineBundleModels(candidateSetRaw.models, authorities.modelRevisions);
  if (!Array.isArray(candidateSetRaw.lines) || !Array.isArray(candidateSetRaw.candidates)) {
    throw new Error("Phase B candidate-setのlines/candidatesは配列が必要です。");
  }
  const lines = new Map<string, Record<string, unknown>>();
  for (const [index, value] of candidateSetRaw.lines.entries()) {
    const line = exactObject(value, BASELINE_LINE_KEYS, `candidate-set.lines[${index}]`);
    const scenario = safeSegment(line.scenario, `candidate-set.lines[${index}].scenario`);
    const lineId = safeSegment(line.line, `candidate-set.lines[${index}].line`);
    const key = `${scenario}/${lineId}`;
    if (lines.has(key)) {
      throw new Error(`candidate-set lineが重複しています: ${key}`);
    }
    nonEmptyText(line.scenario_title, `candidate-set.lines[${index}].scenario_title`);
    nonEmptyText(line.text, `candidate-set.lines[${index}].text`);
    nonEmptyText(line.delivery, `candidate-set.lines[${index}].delivery`);
    lines.set(key, line);
  }
  const takesByGroup = new Map<string, Array<Record<string, unknown>>>();
  const candidates = new Map<string, ValidatedCandidate>();
  const referencedPaths = new Set<string>(BASELINE_METADATA_FILES);
  for (const [index, value] of candidateSetRaw.candidates.entries()) {
    const take = exactObject(value, BASELINE_TAKE_KEYS, `candidate-set.candidates[${index}]`);
    const model = safePathSegment(take.model, `candidate-set.candidates[${index}].model`);
    const scenario = safeSegment(take.scenario, `candidate-set.candidates[${index}].scenario`);
    const line = safeSegment(take.line, `candidate-set.candidates[${index}].line`);
    const variant = safeSegment(take.variant, `candidate-set.candidates[${index}].variant`);
    const takeIndex = positiveInteger(
      take.take_index,
      `candidate-set.candidates[${index}].take_index`,
    );
    const takeId = sha(take.take_id, `candidate-set.candidates[${index}].take_id`);
    const audioSha256 = sha(take.sha256, `candidate-set.candidates[${index}].sha256`);
    const inputSha256 = sha(
      take.generation_input_sha256,
      `candidate-set.candidates[${index}].generation_input_sha256`,
    );
    const expectedTakeId = sha256(
      Buffer.from(
        `{"final_opus_sha256":"${audioSha256}","generation_input_sha256":"${inputSha256}"}`,
      ),
    );
    if (takeId !== expectedTakeId || candidates.has(takeId)) {
      throw new Error(`Phase B take identityが不正または重複しています: ${takeId}`);
    }
    const gate = exactObject(take.gate, BASELINE_GATE_KEYS, `candidate ${takeId}.gate`);
    if (
      gate.mechanical !== "pass" ||
      (gate.content !== "pass" && gate.content !== "review_required") ||
      gate.policy_version !== "take-gates-v2"
    ) {
      throw new Error(`candidate ${takeId}.gateがPhase B contractと一致しません。`);
    }
    const artifactPrefix = `audio/takes/${model}/${scenario}/${line}/${variant}/take-${String(takeIndex).padStart(4, "0")}-`;
    if (take.path !== `${artifactPrefix}${audioSha256}.opus`) {
      throw new Error(`candidate ${takeId}.pathがPhase B artifact identityと一致しません。`);
    }
    const localPath = `audio/${model}/${scenario}/${line}/${variant}/take-${String(takeIndex).padStart(4, "0")}.opus`;
    const absolutePath = safeBundleChild(root, localPath, `candidate ${takeId}.audio`);
    const file = await stat(absolutePath).catch((reason: unknown) => {
      throw new Error(`候補音声がありません: ${localPath}: ${errorMessage(reason)}`);
    });
    if (!file.isFile() || sha256(await readFile(absolutePath)) !== audioSha256) {
      throw new Error(`候補音声SHA-256が一致しません: ${localPath}`);
    }
    referencedPaths.add(localPath);
    candidates.set(takeId, {
      id: takeId,
      path: localPath,
      absolutePath,
      size: file.size,
      contentType: "audio/ogg",
    });
    const groupKey = baselineCoordinate({ model, scenario, line, variant });
    takesByGroup.set(groupKey, [...(takesByGroup.get(groupKey) ?? []), take]);
  }
  if (!Array.isArray(sourceMap.groups) || sourceMap.groups.length !== BASELINE_GROUP_COUNT) {
    throw new Error(`Phase B source map.groupsはexactly ${BASELINE_GROUP_COUNT}件が必要です。`);
  }
  const serializedGroups: Record<string, unknown>[] = [];
  let previousCoordinate = "";
  const seenCoordinates = new Set<string>();
  for (const [index, value] of sourceMap.groups.entries()) {
    const source = exactObject(value, BASELINE_SOURCE_GROUP_KEYS, `source-map.groups[${index}]`);
    const model = safePathSegment(source.model, `source-map.groups[${index}].model`);
    const scenario = safeSegment(source.scenario, `source-map.groups[${index}].scenario`);
    const line = safeSegment(source.line, `source-map.groups[${index}].line`);
    const variant = safeSegment(source.variant, `source-map.groups[${index}].variant`);
    const character = safeSegment(source.character, `source-map.groups[${index}].character`);
    const roleIdentitySha256 = sha(
      source.role_identity_sha256,
      `source-map.groups[${index}].role_identity_sha256`,
    );
    const referenceVoice = nullableNonEmptyText(
      source.reference_voice,
      `source-map.groups[${index}].reference_voice`,
    );
    const role = validateBaselineRole(source.role, `source-map.groups[${index}].role`);
    const sceneSetting = nonEmptyText(
      source.scene_setting,
      `source-map.groups[${index}].scene_setting`,
    );
    const reading = nullableNonEmptyText(source.reading, `source-map.groups[${index}].reading`);
    const situation = nonEmptyText(source.situation, `source-map.groups[${index}].situation`);
    const emotion = nonEmptyText(source.emotion, `source-map.groups[${index}].emotion`);
    const intensity = positiveInteger(source.intensity, `source-map.groups[${index}].intensity`);
    const coordinate = baselineCoordinate({ model, scenario, line, variant });
    if (
      seenCoordinates.has(coordinate) ||
      (previousCoordinate !== "" && coordinate < previousCoordinate)
    ) {
      throw new Error("Phase B source map.groupsは重複のないcanonical順が必要です。");
    }
    seenCoordinates.add(coordinate);
    previousCoordinate = coordinate;
    const roleEpochSha256 = sha(source.role_epoch_sha256, `${coordinate}.role_epoch_sha256`);
    const sourceRunId = safePathSegment(source.source_run_id, `${coordinate}.source_run_id`);
    const minimum = positiveInteger(
      source.minimum_eligible_candidates,
      `${coordinate}.minimum_eligible_candidates`,
    );
    const target = authorities.targets.get(coordinate);
    const roleSnapshot = authorities.roles.get(JSON.stringify([scenario, character]));
    if (!target || target.minimum !== minimum || !roleSnapshot) {
      throw new Error(`Phase B source map groupがplan target/policyと一致しません: ${coordinate}`);
    }
    const actualRoleSnapshot = {
      scenario,
      character,
      role,
      reference_voice: referenceVoice,
      scene_setting: sceneSetting,
      role_identity_sha256: roleIdentitySha256,
    };
    if (!canonicalJsonBytes(actualRoleSnapshot).equals(canonicalJsonBytes(roleSnapshot))) {
      throw new Error(`Phase B source map role snapshotがplanと一致しません: ${coordinate}`);
    }
    const groupTakes = [...(takesByGroup.get(coordinate) ?? [])].sort((left, right) =>
      compareText(left.take_id as string, right.take_id as string),
    );
    if (groupTakes.length < minimum) {
      throw new Error(`Phase B group候補数がminimum未満です: ${coordinate}`);
    }
    const lineDocument = lines.get(`${scenario}/${line}`);
    if (!lineDocument) {
      throw new Error(`Phase B line metadataがありません: ${scenario}/${line}`);
    }
    const exportCandidates = groupTakes.map((take) => ({
      take_id: take.take_id,
      path: take.path,
      audio_sha256: take.sha256,
      gate: take.gate,
    }));
    const expectedRoleEpoch = expectedBaselineRoleEpoch({
      model,
      scenario,
      character,
      roleIdentitySha256,
      referenceVoice,
      planSha256,
      anchorSelectionSha256,
      modelRevision: authorities.modelRevisions.get(model)!,
      anchorEpochs: authorities.anchorEpochs,
    });
    if (roleEpochSha256 !== expectedRoleEpoch) {
      throw new Error(
        `Phase B source map role epochがplan/anchor authorityと一致しません: ${coordinate}`,
      );
    }
    for (const take of groupTakes) {
      validateBaselineCandidateProvenance(take, {
        model,
        scenario,
        line,
        variant,
        roleEpochSha256,
        planSha256,
        anchorSelectionSha256,
        anchorPlanSha256: authorities.anchorPlanSha256,
      });
    }
    const groupSha256 = sha256(
      canonicalJsonBytes({
        model,
        scenario,
        line,
        variant,
        character,
        role_identity_sha256: roleIdentitySha256,
        reference_voice: referenceVoice,
        role,
        scene_setting: sceneSetting,
        reading,
        situation,
        emotion,
        intensity,
        scenario_title: lineDocument.scenario_title,
        text: lineDocument.text,
        delivery: lineDocument.delivery,
        role_epoch_sha256: roleEpochSha256,
        source_run_id: sourceRunId,
        minimum_eligible_candidates: minimum,
        candidates: exportCandidates,
      }),
    );
    const presented = [...exportCandidates]
      .sort((left, right) => {
        const compared = compareText(
          sha256(Buffer.from(candidateSetSha256 + (left.take_id as string))),
          sha256(Buffer.from(candidateSetSha256 + (right.take_id as string))),
        );
        return compared || compareText(left.take_id as string, right.take_id as string);
      })
      .map((candidate, candidateIndex) => ({
        ...candidate,
        label: blindLabel(candidateIndex),
      }));
    serializedGroups.push({
      model,
      scenario,
      line,
      variant,
      character,
      role_identity_sha256: roleIdentitySha256,
      reference_voice: referenceVoice,
      role,
      scene_setting: sceneSetting,
      scenario_title: lineDocument.scenario_title,
      line_text: lineDocument.text,
      reading,
      situation,
      emotion,
      intensity,
      delivery: lineDocument.delivery,
      role_epoch_sha256: roleEpochSha256,
      source_run_id: sourceRunId,
      minimum_eligible_candidates: minimum,
      group_sha256: groupSha256,
      candidates: presented,
      export_candidates: exportCandidates,
    });
  }
  if (
    takesByGroup.size !== seenCoordinates.size ||
    [...takesByGroup.keys()].some((key) => !seenCoordinates.has(key))
  ) {
    throw new Error("Phase B source mapとcandidate group集合が一致しません。");
  }
  if (seenCoordinates.size !== authorities.targets.size) {
    throw new Error("Phase B source mapとplan targetのexact集合が一致しません。");
  }
  const actualPaths = listBundleFiles(root);
  const expectedPaths = [...referencedPaths].sort(compareText);
  if (
    actualPaths.length !== expectedPaths.length ||
    actualPaths.some((value, index) => value !== expectedPaths[index])
  ) {
    throw new Error(
      `Phase B bundle file setがexact contractと一致しません: expected=${expectedPaths.length}, actual=${actualPaths.length}`,
    );
  }
  const document = {
    format_version: 1,
    protocol: "role-baseline-listening-v1",
    plan_sha256: planSha256,
    anchor_selection_sha256: anchorSelectionSha256,
    candidate_set_sha256: candidateSetSha256,
    groups: serializedGroups,
  };
  return {
    workflow: BASELINE_WORKFLOW,
    document,
    root,
    bundleSha256: sha256(sourceMapBytes),
    candidates,
    groupBindings: [],
  };
}

interface BaselineAuthorities {
  readonly modelRevisions: ReadonlyMap<string, string>;
  readonly roles: ReadonlyMap<string, Record<string, unknown>>;
  readonly targets: ReadonlyMap<string, { readonly minimum: number }>;
  readonly anchorEpochs: ReadonlyMap<string, string>;
  readonly anchorPlanSha256: string;
}

function validateBaselineAuthorities(options: {
  readonly plan: Record<string, unknown>;
  readonly planSha256: string;
  readonly anchorSelection: Record<string, unknown>;
  readonly anchorSelectionSha256: string;
  readonly sourceMap: Record<string, unknown>;
}): BaselineAuthorities {
  const { plan, planSha256, anchorSelection, anchorSelectionSha256, sourceMap } = options;
  if (plan.format_version !== 2 || plan.protocol !== "role-baseline-plan-v2") {
    throw new Error("completion-planはrole-baseline-plan-v2が必要です。");
  }
  if (
    sourceMap.plan_sha256 !== planSha256 ||
    sourceMap.anchor_selection_sha256 !== anchorSelectionSha256
  ) {
    throw new Error("source mapが埋め込みplan/anchor selection authorityと一致しません。");
  }
  exactObject(
    plan.base,
    [
      "manifest_sha256",
      "git_blob",
      "candidate_set_sha256",
      "selection_sha256",
      "inherited_groups",
      "final_groups",
    ],
    "completion-plan.base",
  );
  exactObject(
    plan.sources,
    ["scenario_registry_sha256", "scenario_files", "voice_registry_path", "voice_registry_sha256"],
    "completion-plan.sources",
  );
  const anchorAuthority = exactObject(
    plan.anchor_authority,
    ["source_plan_sha256", "candidate_set_sha256", "selection_sha256"],
    "completion-plan.anchor_authority",
  );
  const anchorPlanSha256 = sha(
    anchorAuthority.source_plan_sha256,
    "completion-plan.anchor_authority.source_plan_sha256",
  );
  if (anchorAuthority.selection_sha256 !== anchorSelectionSha256) {
    throw new Error("completion-plan anchor selection SHAが埋め込みselectionと一致しません。");
  }
  if (
    anchorSelection.format_version !== 1 ||
    anchorSelection.protocol !== "role-anchor-selection-v1" ||
    anchorSelection.plan_sha256 !== anchorPlanSha256 ||
    anchorSelection.candidate_set_sha256 !== anchorAuthority.candidate_set_sha256
  ) {
    throw new Error("埋め込みanchor selection rootがcompletion-planと一致しません。");
  }

  if (!Array.isArray(plan.models) || plan.models.length !== BASELINE_MODELS.length) {
    throw new Error("completion-plan.modelsはexactly 8件が必要です。");
  }
  const modelRevisions = new Map<string, string>();
  for (const [index, value] of plan.models.entries()) {
    const model = exactObject(value, ["id", "revision"], `completion-plan.models[${index}]`);
    const id = safePathSegment(model.id, `completion-plan.models[${index}].id`);
    const revision = nonEmptyText(model.revision, `completion-plan.models[${index}].revision`);
    if (id !== BASELINE_MODELS[index] || modelRevisions.has(id)) {
      throw new Error("completion-plan.modelsが固定8 modelのcanonical順と一致しません。");
    }
    modelRevisions.set(id, revision);
  }

  if (!Array.isArray(plan.roles) || plan.roles.length !== 58) {
    throw new Error("completion-plan.rolesはexactly 58件が必要です。");
  }
  const roles = new Map<string, Record<string, unknown>>();
  for (const [index, value] of plan.roles.entries()) {
    const label = `completion-plan.roles[${index}]`;
    const snapshot = exactObject(
      value,
      ["scenario", "character", "role", "reference_voice", "scene_setting", "role_identity_sha256"],
      label,
    );
    const scenario = safeSegment(snapshot.scenario, `${label}.scenario`);
    const character = safeSegment(snapshot.character, `${label}.character`);
    const role = validateBaselineRole(snapshot.role, `${label}.role`);
    const referenceVoice = nullableNonEmptyText(
      snapshot.reference_voice,
      `${label}.reference_voice`,
    );
    const sceneSetting = nonEmptyText(snapshot.scene_setting, `${label}.scene_setting`);
    const roleIdentitySha256 = sha(snapshot.role_identity_sha256, `${label}.role_identity_sha256`);
    const identity = {
      scenario,
      character,
      role,
      reference_voice: referenceVoice,
      scene_setting: sceneSetting,
    };
    if (sha256(canonicalJsonBytes(identity)) !== roleIdentitySha256) {
      throw new Error(`${label}.role identity SHAが不正です。`);
    }
    const key = JSON.stringify([scenario, character]);
    if (roles.has(key)) throw new Error(`completion-plan roleが重複しています: ${key}`);
    roles.set(key, { ...identity, role_identity_sha256: roleIdentitySha256 });
  }

  const phaseB = exactObject(
    plan.phase_b,
    ["model_policies", "targets"],
    "completion-plan.phase_b",
  );
  if (!Array.isArray(phaseB.model_policies) || phaseB.model_policies.length !== 8) {
    throw new Error("completion-plan.phase_b.model_policiesはexactly 8件が必要です。");
  }
  const minimums = new Map<string, number>();
  for (const [index, value] of phaseB.model_policies.entries()) {
    const label = `completion-plan.phase_b.model_policies[${index}]`;
    const policy = exactObject(
      value,
      ["model", "takes", "minimum_eligible_candidates", "seed_policy", "primary_seed_base"],
      label,
    );
    const model = safePathSegment(policy.model, `${label}.model`);
    if (model !== BASELINE_MODELS[index] || minimums.has(model)) {
      throw new Error("completion-plan model policiesが固定8 modelのcanonical順と一致しません。");
    }
    const takes = positiveInteger(policy.takes, `${label}.takes`);
    const minimum = positiveInteger(policy.minimum_eligible_candidates, `${label}.minimum`);
    const expectedTakes = model === "aivisspeech-kohaku" ? 1 : 4;
    const expectedMinimum = model === "aivisspeech-kohaku" ? 1 : 3;
    if (takes !== expectedTakes || minimum !== expectedMinimum) {
      throw new Error(`${label} takes/minimumが固定Phase B policyと一致しません。`);
    }
    if (model === "aivisspeech-kohaku") {
      if (policy.seed_policy !== "none" || policy.primary_seed_base !== null) {
        throw new Error(`${label} seed policyが不正です。`);
      }
    } else if (
      policy.seed_policy !== "derived-sha256-v1" ||
      typeof policy.primary_seed_base !== "number" ||
      !Number.isSafeInteger(policy.primary_seed_base)
    ) {
      throw new Error(`${label} seed policyが不正です。`);
    }
    minimums.set(model, minimum);
  }
  if (!Array.isArray(phaseB.targets) || phaseB.targets.length !== BASELINE_GROUP_COUNT) {
    throw new Error(
      `completion-plan.phase_b.targetsはexactly ${BASELINE_GROUP_COUNT}件が必要です。`,
    );
  }
  const targets = new Map<string, { minimum: number }>();
  const targetCounts = new Map<string, number>();
  let previousTarget = "";
  for (const [index, value] of phaseB.targets.entries()) {
    const label = `completion-plan.phase_b.targets[${index}]`;
    const target = exactObject(value, ["model", "scenario", "line", "variant"], label);
    const coordinate = baselineCoordinate({
      model: safePathSegment(target.model, `${label}.model`),
      scenario: safeSegment(target.scenario, `${label}.scenario`),
      line: safeSegment(target.line, `${label}.line`),
      variant: safeSegment(target.variant, `${label}.variant`),
    });
    const model = JSON.parse(coordinate)[0] as string;
    const minimum = minimums.get(model);
    if (!minimum || targets.has(coordinate) || (previousTarget && coordinate < previousTarget)) {
      throw new Error(
        "completion-plan targetsが固定model policyの重複なしcanonical集合ではありません。",
      );
    }
    targets.set(coordinate, { minimum });
    targetCounts.set(model, (targetCounts.get(model) ?? 0) + 1);
    previousTarget = coordinate;
  }
  for (const [model, expectedCount] of BASELINE_MODEL_GROUP_COUNTS) {
    if (targetCounts.get(model) !== expectedCount) {
      throw new Error(`completion-plan target分布が固定597 groupと一致しません: ${model}`);
    }
  }

  const anchorEpochs = validateEmbeddedAnchorSelection(anchorSelection, roles, modelRevisions);
  const expectedAnchorKeys = new Set<string>();
  for (const [roleKey, role] of roles) {
    if (role.reference_voice !== null) continue;
    const [scenario, character] = JSON.parse(roleKey) as [string, string];
    for (const model of BASELINE_ANCHOR_MODELS) {
      expectedAnchorKeys.add(JSON.stringify([model, scenario, character]));
    }
  }
  if (
    anchorEpochs.size !== expectedAnchorKeys.size ||
    [...expectedAnchorKeys].some((key) => !anchorEpochs.has(key))
  ) {
    throw new Error("anchor selection group集合がplanのno-reference role対象と一致しません。");
  }
  return { modelRevisions, roles, targets, anchorEpochs, anchorPlanSha256 };
}

function validateEmbeddedAnchorSelection(
  selection: Record<string, unknown>,
  roles: ReadonlyMap<string, Record<string, unknown>>,
  modelRevisions: ReadonlyMap<string, string>,
): ReadonlyMap<string, string> {
  if (!Array.isArray(selection.groups) || selection.groups.length !== 106) {
    throw new Error("anchor selection.groupsはexactly 106件が必要です。");
  }
  const keys = [
    "model",
    "model_revision",
    "scenario",
    "character",
    "role_identity",
    "role_identity_sha256",
    "review_role_epoch_sha256",
    "role_epoch_sha256",
    "anchor_id",
    "attempt",
    "seed",
    "audio_path",
    "audio_sha256",
    "anchor_text",
    "anchor_text_sha256",
    "decision",
    "decision_sha256",
  ] as const;
  const epochs = new Map<string, string>();
  let previous = "";
  for (const [index, value] of selection.groups.entries()) {
    const label = `anchor selection.groups[${index}]`;
    const group = exactObject(value, keys, label);
    const model = safePathSegment(group.model, `${label}.model`);
    const scenario = safeSegment(group.scenario, `${label}.scenario`);
    const character = safeSegment(group.character, `${label}.character`);
    if (!BASELINE_ANCHOR_MODELS.has(model) || group.model_revision !== modelRevisions.get(model)) {
      throw new Error(`${label} model/revisionがcompletion-planと一致しません。`);
    }
    const role = roles.get(JSON.stringify([scenario, character]));
    if (!role || role.reference_voice !== null)
      throw new Error(`${label} roleがplan対象ではありません。`);
    const roleIdentity = exactObject(
      group.role_identity,
      ["scenario", "character", "role", "reference_voice", "scene_setting"],
      `${label}.role_identity`,
    );
    const expectedIdentity = { ...role };
    delete expectedIdentity.role_identity_sha256;
    if (!canonicalJsonBytes(roleIdentity).equals(canonicalJsonBytes(expectedIdentity))) {
      throw new Error(`${label} role identityがcompletion-planと一致しません。`);
    }
    const roleIdentitySha = sha(group.role_identity_sha256, `${label}.role_identity_sha256`);
    if (roleIdentitySha !== role.role_identity_sha256)
      throw new Error(`${label} role SHAが不正です。`);
    const reviewEpoch = sha(group.review_role_epoch_sha256, `${label}.review_role_epoch_sha256`);
    const epoch = sha(group.role_epoch_sha256, `${label}.role_epoch_sha256`);
    const anchorId = sha(group.anchor_id, `${label}.anchor_id`);
    const audioSha = sha(group.audio_sha256, `${label}.audio_sha256`);
    positiveInteger(group.attempt, `${label}.attempt`);
    if (typeof group.seed !== "number" || !Number.isSafeInteger(group.seed))
      throw new Error(`${label}.seedが不正です。`);
    nonEmptyText(group.audio_path, `${label}.audio_path`);
    const anchorText = nonEmptyText(group.anchor_text, `${label}.anchor_text`);
    if (sha256(Buffer.from(anchorText, "utf8")) !== group.anchor_text_sha256)
      throw new Error(`${label}.anchor text SHAが不正です。`);
    const decision = exactObject(group.decision, DECISION_GROUP_KEYS, `${label}.decision`);
    const decisionSha = sha(group.decision_sha256, `${label}.decision_sha256`);
    if (
      sha256(canonicalJsonBytes(decision)) !== decisionSha ||
      decision.model !== model ||
      decision.scenario !== scenario ||
      decision.character !== character ||
      decision.role_epoch_sha256 !== reviewEpoch ||
      decision.selected_candidate_id !== anchorId
    ) {
      throw new Error(`${label}.decisionがselected anchorと一致しません。`);
    }
    const expectedEpoch = sha256(
      canonicalJsonBytes({
        protocol: "selected-role-epoch-v1",
        model,
        model_revision: group.model_revision,
        scenario,
        character,
        role_identity_sha256: roleIdentitySha,
        review_role_epoch_sha256: reviewEpoch,
        anchor_id: anchorId,
        audio_sha256: audioSha,
        decision_sha256: decisionSha,
      }),
    );
    if (epoch !== expectedEpoch) throw new Error(`${label}.role epochが不正です。`);
    const key = JSON.stringify([model, scenario, character]);
    if (epochs.has(key) || (previous && key < previous))
      throw new Error("anchor selection groupsがcanonical順ではありません。");
    epochs.set(key, epoch);
    previous = key;
  }
  return epochs;
}

function validateBaselineBundleModels(
  value: unknown,
  revisions: ReadonlyMap<string, string>,
): void {
  if (!Array.isArray(value) || value.length !== BASELINE_MODELS.length) {
    throw new Error("Phase B candidate-set modelsはexactly 8件が必要です。");
  }
  value.forEach((raw, index) => {
    if (typeof raw !== "object" || raw === null || Array.isArray(raw))
      throw new Error("candidate-set modelが不正です。");
    const model = raw as Record<string, unknown>;
    if (
      model.id !== BASELINE_MODELS[index] ||
      model.version !== revisions.get(BASELINE_MODELS[index]!)
    ) {
      throw new Error("candidate-set model/revisionがcompletion-planの固定8 modelと一致しません。");
    }
  });
}

function validateBaselineRole(value: unknown, label: string): Record<string, string> {
  const role = exactObject(value, BASELINE_ROLE_KEYS, label);
  return Object.fromEntries(
    BASELINE_ROLE_KEYS.map((key) => [key, nonEmptyText(role[key], `${label}.${key}`)]),
  );
}

function expectedBaselineRoleEpoch(options: {
  readonly model: string;
  readonly scenario: string;
  readonly character: string;
  readonly roleIdentitySha256: string;
  readonly referenceVoice: string | null;
  readonly planSha256: string;
  readonly anchorSelectionSha256: string;
  readonly modelRevision: string;
  readonly anchorEpochs: ReadonlyMap<string, string>;
}): string {
  const anchor = options.anchorEpochs.get(
    JSON.stringify([options.model, options.scenario, options.character]),
  );
  if (anchor) return anchor;
  return sha256(
    canonicalJsonBytes({
      protocol: "phase-b-role-epoch-v1",
      plan_sha256: options.planSha256,
      model: options.model,
      model_revision: options.modelRevision,
      scenario: options.scenario,
      character: options.character,
      role_identity_sha256: options.roleIdentitySha256,
      reference_voice: options.referenceVoice,
      anchor_selection_sha256: BASELINE_ANCHOR_MODELS.has(options.model)
        ? options.anchorSelectionSha256
        : null,
    }),
  );
}

function validateBaselineCandidateProvenance(
  take: Record<string, unknown>,
  expected: {
    readonly model: string;
    readonly scenario: string;
    readonly line: string;
    readonly variant: string;
    readonly roleEpochSha256: string;
    readonly planSha256: string;
    readonly anchorSelectionSha256: string;
    readonly anchorPlanSha256: string;
  },
): void {
  if (
    typeof take.gen_params !== "object" ||
    take.gen_params === null ||
    Array.isArray(take.gen_params)
  ) {
    throw new Error(`candidate ${String(take.take_id)} gen_paramsが不正です。`);
  }
  const params = take.gen_params as Record<string, unknown>;
  const requested = objectValue(params.requested, "candidate gen_params.requested");
  const realized = objectValue(params.realized, "candidate gen_params.realized");
  const requestProvenance = exactObject(
    requested.phase_b_provenance,
    [
      "protocol",
      "plan_sha256",
      "run_kind",
      "supersedes_run_id",
      "anchor_selection_sha256",
      "anchor_plan_sha256",
      "target_group",
    ],
    "candidate requested.phase_b_provenance",
  );
  const realizedProvenance = exactObject(
    realized.phase_b_provenance,
    [
      "protocol",
      "plan_sha256",
      "run_kind",
      "supersedes_run_id",
      "anchor_selection_sha256",
      "anchor_plan_sha256",
      "target_group",
    ],
    "candidate realized.phase_b_provenance",
  );
  if (!canonicalJsonBytes(requestProvenance).equals(canonicalJsonBytes(realizedProvenance))) {
    throw new Error(
      `candidate ${String(take.take_id)} requested/realized Phase B provenanceが一致しません。`,
    );
  }
  const targetGroup = exactObject(
    requestProvenance.target_group,
    ["model", "scenario", "line", "variant", "role_epoch_sha256"],
    "candidate phase_b_provenance.target_group",
  );
  const anchorBound = BASELINE_ANCHOR_MODELS.has(expected.model);
  if (
    requestProvenance.protocol !== "phase-b-generation-v2" ||
    requestProvenance.plan_sha256 !== expected.planSha256 ||
    (requestProvenance.run_kind !== "primary" && requestProvenance.run_kind !== "topup") ||
    (requestProvenance.run_kind === "primary"
      ? requestProvenance.supersedes_run_id !== null
      : typeof requestProvenance.supersedes_run_id !== "string") ||
    requestProvenance.anchor_selection_sha256 !==
      (anchorBound ? expected.anchorSelectionSha256 : null) ||
    requestProvenance.anchor_plan_sha256 !== (anchorBound ? expected.anchorPlanSha256 : null) ||
    !canonicalJsonBytes(targetGroup).equals(
      canonicalJsonBytes({
        model: expected.model,
        scenario: expected.scenario,
        line: expected.line,
        variant: expected.variant,
        role_epoch_sha256: expected.roleEpochSha256,
      }),
    )
  ) {
    throw new Error(
      `candidate ${String(take.take_id)} Phase B provenanceがplan/anchor/targetと一致しません。`,
    );
  }
}

function objectValue(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error(`${label}はobjectが必要です。`);
  return value as Record<string, unknown>;
}

function nullableNonEmptyText(value: unknown, label: string): string | null {
  if (value === null) return null;
  return nonEmptyText(value, label);
}

async function readCanonicalObject(
  root: string,
  name: string,
  keys: readonly string[],
): Promise<Record<string, unknown>> {
  const raw = await readFile(path.join(root, name)).catch((reason: unknown) => {
    throw new Error(`${name}を読めません: ${errorMessage(reason)}`);
  });
  assertCanonicalArtifactBytes(raw, name);
  let decoded: unknown;
  try {
    decoded = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
  } catch (reason: unknown) {
    throw new Error(`${name}は正しいUTF-8 JSONが必要です: ${errorMessage(reason)}`);
  }
  return exactObject(decoded, keys, name);
}

function readShaMarker(root: string, name: string): string {
  const raw = readFileSync(path.join(root, name));
  if (raw.length !== 64) {
    throw new Error(`${name}は改行なし64-byte SHA-256が必要です。`);
  }
  return sha(raw.toString("ascii"), name);
}

function baselineCoordinate(value: {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
}): string {
  return JSON.stringify([value.model, value.scenario, value.line, value.variant]);
}

function blindLabel(index: number): string {
  let value = index + 1;
  let label = "";
  while (value > 0) {
    value -= 1;
    label = String.fromCharCode(65 + (value % 26)) + label;
    value = Math.floor(value / 26);
  }
  return label;
}

const BASELINE_DRAFT_ROOT_KEYS = [
  "format_version",
  "protocol",
  "plan_sha256",
  "anchor_selection_sha256",
  "candidate_set_sha256",
  "groups",
] as const;
const BASELINE_DRAFT_GROUP_KEYS = [
  "model",
  "scenario",
  "line",
  "variant",
  "role_epoch_sha256",
  "group_sha256",
  "plan_sha256",
  "anchor_selection_sha256",
  "candidate_set_sha256",
  "revalidation_reason",
  "heard_candidate_ids",
  "candidates",
  "decision",
] as const;
const BASELINE_DRAFT_CANDIDATE_KEYS = ["take_id", "rubric"] as const;
const BASELINE_RUBRIC_KEYS = [
  "content_correct",
  "prompt_leakage",
  "reading_correct",
  "accent_naturalness",
  "role_match",
  "delivery_match",
  "audio_quality",
  "adoptable",
  "notes",
] as const;
const BASELINE_DECISION_ROOT_KEYS = BASELINE_DRAFT_ROOT_KEYS;
const BASELINE_DECISION_GROUP_KEYS = [
  "model",
  "scenario",
  "line",
  "variant",
  "role_epoch_sha256",
  "group_sha256",
  "authority",
  "candidates",
  "decision",
] as const;
const BASELINE_AUTHORITY_KEYS = [
  "type",
  "policy_version",
  "reviewer",
  "minimum_eligible_candidates",
] as const;
const BASELINE_DECISION_CANDIDATE_KEYS = [
  "take_id",
  "path",
  "audio_sha256",
  "gate",
  "rubric",
] as const;
const QUALITY_REVIEW_DRAFT_ROOT_KEYS = [
  "format_version",
  "protocol",
  "plan_sha256",
  "decision_sha256",
  "manifest_sha256",
  "quality_signals_sha256",
  "groups",
  "current_index",
] as const;
const QUALITY_REVIEW_RESULT_ROOT_KEYS = QUALITY_REVIEW_DRAFT_ROOT_KEYS.filter(
  (key) => key !== "current_index",
);
const QUALITY_REVIEW_RESULT_GROUP_KEYS = [
  "model",
  "scenario",
  "line",
  "variant",
  "take_id",
  "heard",
  "result",
  "notes",
] as const;

function validateWorkflowDraft(value: unknown, bundle: ValidatedBundle): Record<string, unknown> {
  if (bundle.workflow === ANCHOR_WORKFLOW) {
    return validateDraftDocument(value, bundle);
  }
  return bundle.workflow === QUALITY_REVIEW_WORKFLOW
    ? validateQualityReviewDraft(value, bundle)
    : validateBaselineDraftDocument(value, bundle);
}

function validateWorkflowDecision(
  value: unknown,
  bundle: ValidatedBundle,
): Record<string, unknown> {
  if (bundle.workflow === ANCHOR_WORKFLOW) {
    return validateDecisionDocument(value, bundle);
  }
  return bundle.workflow === QUALITY_REVIEW_WORKFLOW
    ? validateQualityReviewResult(value, bundle)
    : validateBaselineDecisionDocument(value, bundle);
}

function decisionFromWorkflowDraft(
  draft: Record<string, unknown>,
  bundle: ValidatedBundle,
): Record<string, unknown> {
  if (bundle.workflow === ANCHOR_WORKFLOW) {
    return decisionFromDraft(draft);
  }
  return bundle.workflow === QUALITY_REVIEW_WORKFLOW
    ? qualityReviewResultFromDraft(draft, bundle)
    : baselineDecisionFromDraft(draft, bundle);
}

function validateQualityReviewDraft(
  value: unknown,
  bundle: ValidatedBundle,
): Record<string, unknown> {
  if (bundle.workflow !== QUALITY_REVIEW_WORKFLOW) {
    throw new Error("quality review draftにはrole-quality-review-v1 bundleが必要です。");
  }
  const root = exactObject(value, QUALITY_REVIEW_DRAFT_ROOT_KEYS, "quality review draft");
  assertQualityReviewRoot(root, "role-quality-review-draft-v1", bundle);
  const groups = validateQualityReviewResultGroups(root.groups, bundle, false);
  const currentIndex = nonNegativeInteger(root.current_index, "quality review.current_index");
  if (currentIndex >= groups.length) {
    throw new Error("quality review.current_indexがgroup範囲外です。");
  }
  return root;
}

function validateQualityReviewResult(
  value: unknown,
  bundle: ValidatedBundle,
): Record<string, unknown> {
  if (bundle.workflow !== QUALITY_REVIEW_WORKFLOW) {
    throw new Error("quality review resultにはrole-quality-review-v1 bundleが必要です。");
  }
  const root = exactObject(value, QUALITY_REVIEW_RESULT_ROOT_KEYS, "quality review result");
  assertQualityReviewRoot(root, "role-quality-review-result-v1", bundle);
  validateQualityReviewResultGroups(root.groups, bundle, true);
  return root;
}

function validateQualityReviewResultGroups(
  value: unknown,
  bundle: ValidatedBundle,
  complete: boolean,
): readonly Record<string, unknown>[] {
  const bundleGroups = bundle.document.groups as Record<string, unknown>[];
  if (!Array.isArray(value) || value.length !== bundleGroups.length) {
    throw new Error(`quality review groupsはexact ${bundleGroups.length}件が必要です。`);
  }
  return value.map((raw, index) => {
    const label = `quality review.groups[${index}]`;
    const group = exactObject(raw, QUALITY_REVIEW_RESULT_GROUP_KEYS, label);
    const binding = bundleGroups[index]!;
    for (const key of ["model", "scenario", "line", "variant", "take_id"] as const) {
      if (group[key] !== binding[key]) {
        throw new Error(`${label}がbundle bindingと一致しません。`);
      }
    }
    if (typeof group.heard !== "boolean") {
      throw new Error(`${label}.heardはbooleanが必要です。`);
    }
    if (group.result !== null && group.result !== "match" && group.result !== "mismatch") {
      throw new Error(`${label}.resultが不正です。`);
    }
    if (typeof group.notes !== "string" || group.notes.length > 500) {
      throw new Error(`${label}.notesは500文字以内の文字列が必要です。`);
    }
    if ((group.result !== null && group.heard !== true) || (complete && group.result === null)) {
      throw new Error(`${label}は完全再生後の明示判断が必要です。`);
    }
    return group;
  });
}

function assertQualityReviewRoot(
  root: Record<string, unknown>,
  protocol: string,
  bundle: ValidatedBundle,
): void {
  if (
    root.format_version !== 1 ||
    root.protocol !== protocol ||
    root.plan_sha256 !== bundle.document.plan_sha256 ||
    root.decision_sha256 !== bundle.document.decision_sha256 ||
    root.manifest_sha256 !== bundle.document.manifest_sha256 ||
    root.quality_signals_sha256 !== bundle.document.quality_signals_sha256
  ) {
    throw new Error("quality review rootがbundleと一致しません。 ");
  }
}

function qualityReviewResultFromDraft(
  draft: Record<string, unknown>,
  bundle: ValidatedBundle,
): Record<string, unknown> {
  validateQualityReviewDraft(draft, bundle);
  const groups = draft.groups as Record<string, unknown>[];
  return {
    format_version: 1,
    protocol: "role-quality-review-result-v1",
    plan_sha256: draft.plan_sha256,
    decision_sha256: draft.decision_sha256,
    manifest_sha256: draft.manifest_sha256,
    quality_signals_sha256: draft.quality_signals_sha256,
    groups: groups.map((group) => ({ ...group })),
  };
}

export function validateBaselineDraftDocument(
  value: unknown,
  bundle: ValidatedBundle,
): Record<string, unknown> {
  if (bundle.workflow !== BASELINE_WORKFLOW) {
    throw new Error("role-baseline draftにはrole-baseline-v1 bundleが必要です。");
  }
  const root = exactObject(value, BASELINE_DRAFT_ROOT_KEYS, "role baseline draft");
  assertBaselineRoot(root, "role-baseline-draft-v1", bundle, "role baseline draft");
  const bundleGroups = bundle.document.groups as Record<string, unknown>[];
  if (!Array.isArray(root.groups) || root.groups.length !== bundleGroups.length) {
    throw new Error(`role baseline draft.groupsはexactly ${bundleGroups.length}件が必要です。`);
  }
  root.groups.forEach((value, index) => {
    const group = exactObject(value, BASELINE_DRAFT_GROUP_KEYS, `baseline draft.groups[${index}]`);
    const binding = bundleGroups[index]!;
    assertBaselineGroupBinding(group, binding, root, `baseline draft.groups[${index}]`);
    if (group.revalidation_reason !== null) {
      nonEmptyText(
        group.revalidation_reason,
        `baseline draft.groups[${index}].revalidation_reason`,
      );
    }
    const exportCandidates = binding.export_candidates as Record<string, unknown>[];
    const expectedTakeIds = exportCandidates.map((candidate) => candidate.take_id as string);
    const heardCandidateIds = shaArray(
      group.heard_candidate_ids,
      `baseline draft.groups[${index}].heard_candidate_ids`,
      true,
    );
    if (
      heardCandidateIds.some((takeId) => !expectedTakeIds.includes(takeId)) ||
      expectedTakeIds
        .filter((takeId) => heardCandidateIds.includes(takeId))
        .some((takeId, heardIndex) => takeId !== heardCandidateIds[heardIndex])
    ) {
      throw new Error(
        `baseline draft.groups[${index}].heard_candidate_idsがbundle順と一致しません。`,
      );
    }
    if (!Array.isArray(group.candidates) || group.candidates.length !== exportCandidates.length) {
      throw new Error(`baseline draft.groups[${index}].candidatesがbundleと一致しません。`);
    }
    group.candidates.forEach((candidateValue, candidateIndex) => {
      const candidate = exactObject(
        candidateValue,
        BASELINE_DRAFT_CANDIDATE_KEYS,
        `baseline draft.groups[${index}].candidates[${candidateIndex}]`,
      );
      if (candidate.take_id !== exportCandidates[candidateIndex]!.take_id) {
        throw new Error(`baseline draft.groups[${index}]のcandidate順がbundleと一致しません。`);
      }
      validateBaselineRubric(candidate.rubric, false, `candidate ${String(candidate.take_id)}`);
    });
    if (group.decision !== null) {
      if (heardCandidateIds.length !== expectedTakeIds.length) {
        throw new Error(`baseline draft.groups[${index}]は全candidateの完全再生が必要です。`);
      }
      const decision = exactObject(group.decision, ["type", "take_id"], "baseline draft decision");
      if (
        decision.type !== "selected" ||
        !exportCandidates.some((candidate) => candidate.take_id === decision.take_id)
      ) {
        throw new Error(`baseline draft.groups[${index}].decisionがbundle候補と一致しません。`);
      }
      for (const candidate of group.candidates as Record<string, unknown>[]) {
        validateBaselineRubric(candidate.rubric, true, `candidate ${String(candidate.take_id)}`);
      }
    }
  });
  return root;
}

export function validateBaselineDecisionDocument(
  value: unknown,
  bundle: ValidatedBundle,
): Record<string, unknown> {
  if (bundle.workflow !== BASELINE_WORKFLOW) {
    throw new Error("role-baseline decisionにはrole-baseline-v1 bundleが必要です。");
  }
  const root = exactObject(value, BASELINE_DECISION_ROOT_KEYS, "role baseline decision");
  assertBaselineRoot(root, "role-baseline-decision-v1", bundle, "role baseline decision");
  const bundleGroups = bundle.document.groups as Record<string, unknown>[];
  if (!Array.isArray(root.groups) || root.groups.length !== bundleGroups.length) {
    throw new Error(`role baseline decision.groupsはexactly ${bundleGroups.length}件が必要です。`);
  }
  root.groups.forEach((value, index) => {
    const group = exactObject(
      value,
      BASELINE_DECISION_GROUP_KEYS,
      `baseline decision.groups[${index}]`,
    );
    const binding = bundleGroups[index]!;
    for (const key of [
      "model",
      "scenario",
      "line",
      "variant",
      "role_epoch_sha256",
      "group_sha256",
    ] as const) {
      if (group[key] !== binding[key]) {
        throw new Error(`baseline decision.groups[${index}]がbundle bindingと一致しません。`);
      }
    }
    const authority = exactObject(
      group.authority,
      BASELINE_AUTHORITY_KEYS,
      `baseline decision.groups[${index}].authority`,
    );
    if (
      authority.type !== "best_available" ||
      authority.policy_version !== "missing-slot-best-of-n-v1" ||
      authority.reviewer !== "owner" ||
      authority.minimum_eligible_candidates !== binding.minimum_eligible_candidates
    ) {
      throw new Error(`baseline decision.groups[${index}].authorityがbundleと一致しません。`);
    }
    const exportCandidates = binding.export_candidates as Record<string, unknown>[];
    if (!Array.isArray(group.candidates) || group.candidates.length !== exportCandidates.length) {
      throw new Error(`baseline decision.groups[${index}].candidatesがbundleと一致しません。`);
    }
    group.candidates.forEach((candidateValue, candidateIndex) => {
      const candidate = exactObject(
        candidateValue,
        BASELINE_DECISION_CANDIDATE_KEYS,
        `baseline decision.groups[${index}].candidates[${candidateIndex}]`,
      );
      const expected = exportCandidates[candidateIndex]!;
      for (const key of ["take_id", "path", "audio_sha256"] as const) {
        if (candidate[key] !== expected[key]) {
          throw new Error(`baseline decision.groups[${index}] candidateがbundleと一致しません。`);
        }
      }
      if (!canonicalJsonBytes(candidate.gate).equals(canonicalJsonBytes(expected.gate))) {
        throw new Error(
          `baseline decision.groups[${index}] candidate gateがbundleと一致しません。`,
        );
      }
      validateBaselineRubric(candidate.rubric, true, `candidate ${String(candidate.take_id)}`);
    });
    const decision = exactObject(group.decision, ["type", "take_id"], "baseline decision decision");
    if (
      decision.type !== "selected" ||
      !exportCandidates.some((candidate) => candidate.take_id === decision.take_id)
    ) {
      throw new Error(`baseline decision.groups[${index}]は明示的なbundle候補選択が必要です。`);
    }
  });
  return root;
}

function baselineDecisionFromDraft(
  draft: Record<string, unknown>,
  bundle: ValidatedBundle,
): Record<string, unknown> {
  validateBaselineDraftDocument(draft, bundle);
  const bundleGroups = bundle.document.groups as Record<string, unknown>[];
  const draftGroups = draft.groups as Record<string, unknown>[];
  return {
    format_version: 1,
    protocol: "role-baseline-decision-v1",
    plan_sha256: bundle.document.plan_sha256,
    anchor_selection_sha256: bundle.document.anchor_selection_sha256,
    candidate_set_sha256: bundle.document.candidate_set_sha256,
    groups: bundleGroups.map((group, index) => {
      const draftGroup = draftGroups[index]!;
      if (draftGroup.decision === null) {
        throw new Error(`baseline draft.groups[${index}]の明示選択がありません。`);
      }
      const rubricByTake = new Map(
        (draftGroup.candidates as Record<string, unknown>[]).map((candidate) => [
          candidate.take_id,
          candidate.rubric,
        ]),
      );
      return {
        model: group.model,
        scenario: group.scenario,
        line: group.line,
        variant: group.variant,
        role_epoch_sha256: group.role_epoch_sha256,
        group_sha256: group.group_sha256,
        authority: {
          type: "best_available",
          policy_version: "missing-slot-best-of-n-v1",
          reviewer: "owner",
          minimum_eligible_candidates: group.minimum_eligible_candidates,
        },
        candidates: (group.export_candidates as Record<string, unknown>[]).map((candidate) => ({
          ...candidate,
          rubric: rubricByTake.get(candidate.take_id),
        })),
        decision: draftGroup.decision,
      };
    }),
  };
}

function assertBaselineRoot(
  root: Record<string, unknown>,
  protocol: string,
  bundle: ValidatedBundle,
  label: string,
): void {
  if (
    root.format_version !== 1 ||
    root.protocol !== protocol ||
    root.plan_sha256 !== bundle.document.plan_sha256 ||
    root.anchor_selection_sha256 !== bundle.document.anchor_selection_sha256 ||
    root.candidate_set_sha256 !== bundle.document.candidate_set_sha256
  ) {
    throw new Error(`${label} rootがPhase B bundleと一致しません。`);
  }
}

function assertBaselineGroupBinding(
  group: Record<string, unknown>,
  binding: Record<string, unknown>,
  root: Record<string, unknown>,
  label: string,
): void {
  for (const key of [
    "model",
    "scenario",
    "line",
    "variant",
    "role_epoch_sha256",
    "group_sha256",
  ] as const) {
    if (group[key] !== binding[key]) {
      throw new Error(`${label}がPhase B bundle groupと一致しません。`);
    }
  }
  for (const key of ["plan_sha256", "anchor_selection_sha256", "candidate_set_sha256"] as const) {
    if (group[key] !== root[key]) {
      throw new Error(`${label}.${key}がdraft rootと一致しません。`);
    }
  }
}

function validateBaselineRubric(value: unknown, complete: boolean, label: string): void {
  const rubric = exactObject(value, BASELINE_RUBRIC_KEYS, `${label}.rubric`);
  for (const key of [
    "content_correct",
    "prompt_leakage",
    "reading_correct",
    "adoptable",
  ] as const) {
    if (typeof rubric[key] !== "boolean" && (complete || rubric[key] !== null)) {
      throw new Error(`${label}.rubric.${key}はboolean${complete ? "" : "またはnull"}が必要です。`);
    }
  }
  for (const key of [
    "accent_naturalness",
    "role_match",
    "delivery_match",
    "audio_quality",
  ] as const) {
    if (
      rubric[key] !== null || complete
        ? typeof rubric[key] !== "number" ||
          !Number.isInteger(rubric[key]) ||
          rubric[key] < 1 ||
          rubric[key] > 5
        : false
    ) {
      throw new Error(`${label}.rubric.${key}は1..5${complete ? "" : "またはnull"}が必要です。`);
    }
  }
  if (typeof rubric.notes !== "string") {
    throw new Error(`${label}.rubric.notesは文字列が必要です。`);
  }
}

export function validateDraftDocument(
  value: unknown,
  bundle: ValidatedBundle,
): Record<string, unknown> {
  const root = exactObject(value, DRAFT_ROOT_KEYS, "role review draft");
  assertBoundRoot(root, "role-review-draft-v2", bundle, "role review draft");
  const groups = boundGroups(root.groups, bundle, DRAFT_GROUP_KEYS, "draft");
  if (
    typeof root.current_group_id !== "string" ||
    !bundle.groupBindings.some((binding) => binding.id === root.current_group_id)
  ) {
    throw new Error("draft.current_group_id はbundle group idが必要です。");
  }
  for (const [index, group] of groups.entries()) {
    const binding = bundle.groupBindings[index]!;
    validateHeardAndSelected(group, binding, `draft.groups[${index}]`, false);
    validateRubric(group.rubric, `draft.groups[${index}].rubric`, false);
    if (typeof group.confirmed !== "boolean") {
      throw new Error(`draft.groups[${index}].confirmed はbooleanが必要です。`);
    }
    if (group.confirmed) {
      validateHeardAndSelected(group, binding, `draft.groups[${index}]`, true);
      validateRubric(group.rubric, `draft.groups[${index}].rubric`, true);
      validateNoUsableReason(group, `draft.groups[${index}]`);
      validateSelectedGender(group, `draft.groups[${index}]`);
    }
  }
  return root;
}

export function validateDecisionDocument(
  value: unknown,
  bundle: ValidatedBundle,
): Record<string, unknown> {
  const root = exactObject(value, DECISION_ROOT_KEYS, "role review decision");
  assertBoundRoot(root, "role-review-decision-v2", bundle, "role review decision");
  const groups = boundGroups(root.groups, bundle, DECISION_GROUP_KEYS, "decision");
  for (const [index, group] of groups.entries()) {
    const binding = bundle.groupBindings[index]!;
    validateHeardAndSelected(group, binding, `decision.groups[${index}]`, true);
    validateRubric(group.rubric, `decision.groups[${index}].rubric`, true);
    validateNoUsableReason(group, `decision.groups[${index}]`);
    validateSelectedGender(group, `decision.groups[${index}]`);
    if (group.confirmed !== true) {
      throw new Error(`decision.groups[${index}].confirmed はtrueが必要です。`);
    }
  }
  return root;
}

export function canonicalJsonBytes(value: unknown, label = "value"): Buffer {
  try {
    return Buffer.from(JSON.stringify(sortCanonical(value)), "utf8");
  } catch (reason: unknown) {
    throw new Error(`${label} をcanonical JSONにできません: ${errorMessage(reason)}`);
  }
}

export async function writeCanonicalDraft(pathname: string, value: unknown): Promise<void> {
  const bytes = canonicalJsonBytes(value, path.basename(pathname));
  await atomicReplace(pathname, bytes);
}

export async function writeCanonicalFinal(pathname: string, value: unknown): Promise<void> {
  const bytes = canonicalJsonBytes(value, path.basename(pathname));
  const marker = Buffer.from(`${sha256(bytes)}\n`, "ascii");
  await atomicReplace(pathname, bytes);
  await atomicReplace(`${pathname.slice(0, -path.extname(pathname).length)}.sha256`, marker);
}

function validateBundleDocument(value: unknown): Record<string, unknown> {
  const root = exactObject(value, ROOT_KEYS, "role review bundle");
  if (root.format_version !== 2 || root.protocol !== "role-review-v2" || root.phase !== "anchor") {
    throw new Error(
      "role review bundle root は format_version=2 / protocol=role-review-v2 / phase=anchor が必要です。",
    );
  }
  sha(root.plan_sha256, "role review bundle.plan_sha256");
  sha(root.candidate_set_sha256, "role review bundle.candidate_set_sha256");
  if (!Array.isArray(root.groups) || root.groups.length !== 106) {
    throw new Error(`role review bundle.groups はexactly 106件が必要です。`);
  }
  const groupIds = new Set<string>();
  const modelCounts = new Map<string, number>();
  const coordinatesByModel = new Map<string, Set<string>>(
    REVIEW_MODELS.map((model) => [model, new Set<string>()]),
  );
  const coordinates: Array<readonly [string, string, string, string]> = [];
  for (const [groupIndex, groupValue] of root.groups.entries()) {
    const label = `role review bundle.groups[${groupIndex}]`;
    const group = exactObject(groupValue, GROUP_KEYS, label);
    const id = sha(group.id, `${label}.id`);
    if (groupIds.has(id)) {
      throw new Error(`group id が重複しています: ${id}`);
    }
    groupIds.add(id);
    const model = enumValue(group.model, REVIEW_MODELS, `${label}.model`);
    const scenario = safeSegment(group.scenario, `${label}.scenario`);
    const character = safeSegment(group.character, `${label}.character`);
    modelCounts.set(model, (modelCounts.get(model) ?? 0) + 1);
    const coordinate = `${scenario}/${character}`;
    const modelCoordinates = coordinatesByModel.get(model)!;
    if (modelCoordinates.has(coordinate)) {
      throw new Error(`同一model内でrole座標が重複しています: ${model}/${coordinate}`);
    }
    modelCoordinates.add(coordinate);
    coordinates.push([model, scenario, character, id]);
    nonEmptyText(group.anchor_text, `${label}.anchor_text`);
    if (group.line !== null) {
      throw new Error(`${label}.line はanchor phaseでnullが必要です。`);
    }
    sha(group.role_epoch_sha256, `${label}.role_epoch_sha256`);
    const role = validateRole(group.role, `${label}.role`);
    validateConditioning(group.conditioning, `${label}.conditioning`);
    validateCoverage(group.coverage, role.gender, `${label}.coverage`);
    if (group.comparison_required !== true) {
      throw new Error(`${label}.comparison_required はtrueが必要です。`);
    }
    exactTextArray(group.comparison_reasons, COMPARISON_REASONS, `${label}.comparison_reasons`);
    if (!Array.isArray(group.candidates) || group.candidates.length !== 4) {
      throw new Error(`${label}.candidates はexactly 4件が必要です。`);
    }
    const candidates = group.candidates.map((candidate, index) =>
      validateCandidate(candidate, `${label}.candidates[${index}]`),
    );
    for (const [candidateIndex, candidate] of candidates.entries()) {
      if (candidateIndex > 0 && candidate.attempt <= candidates[candidateIndex - 1]!.attempt) {
        throw new Error(`${label}.candidates attempt は4件の一意な昇順正整数が必要です。`);
      }
    }
    const candidateIds = shaArray(group.candidate_ids, `${label}.candidate_ids`, false);
    if (
      candidateIds.length !== 4 ||
      candidateIds.some((candidateId, index) => candidateId !== candidates[index]!.id)
    ) {
      throw new Error(`${label}.candidate_ids は4 candidatesのexactなid順が必要です。`);
    }
  }
  for (const model of REVIEW_MODELS) {
    if (modelCounts.get(model) !== 53) {
      throw new Error(`role review bundle は各model 53 groupが必要です: ${model}`);
    }
  }
  const expectedCoordinates = coordinatesByModel.get(REVIEW_MODELS[0])!;
  const comparedCoordinates = coordinatesByModel.get(REVIEW_MODELS[1])!;
  if (
    expectedCoordinates.size !== comparedCoordinates.size ||
    [...expectedCoordinates].some((coordinate) => !comparedCoordinates.has(coordinate))
  ) {
    throw new Error("role review bundle は両modelで同じ53 role座標集合が必要です。");
  }
  const sortedCoordinates = [...coordinates].sort((left, right) => {
    for (let index = 0; index < 3; index += 1) {
      const compared = compareText(left[index]!, right[index]!);
      if (compared !== 0) {
        return compared;
      }
    }
    return 0;
  });
  if (coordinates.some((coordinate, index) => coordinate[3] !== sortedCoordinates[index]![3])) {
    throw new Error(
      "role review bundle.groups はmodel/scenario/characterのcanonical順が必要です。",
    );
  }
  return root;
}

function validateCandidate(
  value: unknown,
  label: string,
): {
  readonly id: string;
  readonly attempt: number;
} {
  const candidate = exactObject(value, CANDIDATE_KEYS, label);
  const id = sha(candidate.id, `${label}.id`);
  const attempt = positiveInteger(candidate.attempt, `${label}.attempt`);
  nonNegativeInteger(candidate.seed, `${label}.seed`);
  const audioPath = safeAudioPath(candidate.audio_path, `${label}.audio_path`);
  if (audioPath !== `audio/${id}.wav`) {
    throw new Error(`${label}.audio_path はexactly audio/<candidate-id>.wavが必要です。`);
  }
  sha(candidate.audio_sha256, `${label}.audio_sha256`);
  const qc = exactObject(candidate.qc, QC_KEYS, `${label}.qc`);
  if (qc.mechanical !== "pass") {
    throw new Error(`${label}.qc.mechanical はpassが必要です。`);
  }
  if (qc.content !== "not_checked" && qc.content !== "pass" && qc.content !== "review_required") {
    throw new Error(`${label}.qc.content が不正です。`);
  }
  stringArray(qc.notes, `${label}.qc.notes`);
  return { id, attempt };
}

function validateRole(value: unknown, label: string): { readonly gender: string } {
  const role = exactObject(value, ROLE_KEYS, label);
  nonEmptyText(role.name, `${label}.name`);
  enumValue(role.kind, ["human", "machine", "creature", "spirit"], `${label}.kind`);
  const gender = enumValue(role.gender, ["female", "male", "neutral"], `${label}.gender`);
  enumValue(
    role.age,
    ["child", "teen", "young_adult", "adult", "middle_aged", "elderly"],
    `${label}.age`,
  );
  nonEmptyText(role.archetype, `${label}.archetype`);
  nonEmptyText(role.voice, `${label}.voice`);
  nonEmptyText(role.personality, `${label}.personality`);
  return { gender };
}

function validateConditioning(value: unknown, label: string): void {
  const conditioning = exactObject(value, CONDITIONING_KEYS, label);
  nonEmptyText(conditioning.method, `${label}.method`);
  nonEmptyText(conditioning.summary, `${label}.summary`);
}

function validateCoverage(value: unknown, gender: string, label: string): void {
  const coverage = exactObject(value, COVERAGE_KEYS, label);
  const values = COVERAGE_KEYS.map((key) =>
    enumValue(coverage[key], ["exact", "neutral"], `${label}.${key}`),
  );
  if (
    values[0] !== (gender === "neutral" ? "neutral" : "exact") ||
    values[1] !== "exact" ||
    values[2] !== "exact"
  ) {
    throw new Error(`${label} がroleの指定範囲と一致しません。`);
  }
}

function assertBoundRoot(
  root: Record<string, unknown>,
  protocol: string,
  bundle: ValidatedBundle,
  label: string,
): void {
  if (root.format_version !== 2 || root.protocol !== protocol || root.phase !== "anchor") {
    throw new Error(`${label} root contractが不正です。`);
  }
  if (
    root.plan_sha256 !== bundle.document.plan_sha256 ||
    root.candidate_set_sha256 !== bundle.document.candidate_set_sha256
  ) {
    throw new Error(`${label} のplan/candidate setがbundleと一致しません。`);
  }
}

function boundGroups(
  value: unknown,
  bundle: ValidatedBundle,
  keys: readonly string[],
  kind: string,
): Record<string, unknown>[] {
  if (!Array.isArray(value) || value.length !== 106) {
    throw new Error(`${kind}.groups はexactly 106件が必要です。`);
  }
  return value.map((groupValue, index) => {
    const group = exactObject(groupValue, keys, `${kind}.groups[${index}]`);
    const binding = bundle.groupBindings[index]!;
    if (
      group.id !== binding.id ||
      group.model !== binding.model ||
      group.scenario !== binding.scenario ||
      group.character !== binding.character ||
      group.line !== null ||
      group.role_epoch_sha256 !== binding.roleEpochSha256 ||
      group.group_sha256 !== binding.groupSha256
    ) {
      throw new Error(`${kind}.groups[${index}] がbundle group bindingと一致しません。`);
    }
    return group;
  });
}

function validateHeardAndSelected(
  group: Record<string, unknown>,
  binding: GroupBinding,
  label: string,
  requireAll: boolean,
): void {
  const heard = shaArray(group.heard_candidate_ids, `${label}.heard_candidate_ids`, true);
  if (heard.some((id) => !binding.candidateIds.includes(id))) {
    throw new Error(`${label}.heard_candidate_ids がbundle外candidateを含みます。`);
  }
  const orderedHeard = binding.candidateIds.filter((candidateId) => heard.includes(candidateId));
  if (orderedHeard.some((candidateId, index) => candidateId !== heard[index])) {
    throw new Error(`${label}.heard_candidate_ids はbundle順が必要です。`);
  }
  if (typeof group.no_usable_candidate !== "boolean") {
    throw new Error(`${label}.no_usable_candidate はbooleanが必要です。`);
  }
  if (group.no_usable_candidate) {
    if (group.selected_candidate_id !== null) {
      throw new Error(`${label} はno_usable_candidateとselected_candidate_idを併用できません。`);
    }
  } else if (!requireAll && group.selected_candidate_id === null) {
    return;
  } else {
    const selected = sha(group.selected_candidate_id, `${label}.selected_candidate_id`);
    if (!binding.candidateIds.includes(selected)) {
      throw new Error(`${label}.selected_candidate_id がbundle candidateを参照していません。`);
    }
  }
  if (requireAll) {
    if (
      heard.length !== 4 ||
      heard.some((candidateId, index) => candidateId !== binding.candidateIds[index])
    ) {
      throw new Error(`${label}.heard_candidate_ids は全4候補のbundle順が必要です。`);
    }
  }
}

function validateNoUsableReason(group: Record<string, unknown>, label: string): void {
  if (group.no_usable_candidate !== true) {
    return;
  }
  const rubric = group.rubric as Record<string, unknown>;
  const hasFailedField = RUBRIC_KEYS.slice(0, 7).some((key) => rubric[key] === "fail");
  const hasQualityProblem =
    typeof rubric.naturalness_quality === "number" && rubric.naturalness_quality <= 3;
  const hasNotes = typeof rubric.notes === "string" && rubric.notes.trim().length > 0;
  if (!hasFailedField && !hasQualityProblem && !hasNotes) {
    throw new Error(`${label} は四候補が使用不可な理由をrubricに記録する必要があります。`);
  }
}

function validateSelectedGender(group: Record<string, unknown>, label: string): void {
  if (group.no_usable_candidate === true) {
    return;
  }
  const rubric = group.rubric as Record<string, unknown>;
  if (rubric.gender !== "pass") {
    throw new Error(
      `${label} はselected anchorのgender=passが必要です。性別不一致なら四候補とも使用不可にしてください。`,
    );
  }
}

function validateRubric(value: unknown, label: string, complete: boolean): void {
  const rubric = exactObject(value, RUBRIC_KEYS, label);
  for (const key of RUBRIC_KEYS.slice(0, 7)) {
    const result = rubric[key];
    if ((!complete && result === null) || result === "pass" || result === "fail") {
      continue;
    }
    throw new Error(`${label}.${key} が不正です。`);
  }
  for (const key of ["voice_identity", "delivery"] as const) {
    const invalid = complete
      ? rubric[key] !== "not_applicable"
      : rubric[key] !== "not_applicable" && rubric[key] !== null;
    if (invalid) {
      throw new Error(
        `${label}.${key} はnot_applicable${complete ? "" : "またはnull"}が必要です。`,
      );
    }
  }
  if (!complete && rubric.naturalness_quality === null) {
    // In-progress draft.
  } else if (
    typeof rubric.naturalness_quality !== "number" ||
    !Number.isInteger(rubric.naturalness_quality) ||
    rubric.naturalness_quality < 1 ||
    rubric.naturalness_quality > 5
  ) {
    throw new Error(
      `${label}.naturalness_quality は1..5${complete ? "" : "またはnull"}が必要です。`,
    );
  }
  if (typeof rubric.notes !== "string") {
    throw new Error(`${label}.notes は文字列が必要です。`);
  }
}

function decisionFromDraft(draft: Record<string, unknown>): Record<string, unknown> {
  return {
    format_version: 2,
    protocol: "role-review-decision-v2",
    phase: "anchor",
    plan_sha256: draft.plan_sha256,
    candidate_set_sha256: draft.candidate_set_sha256,
    groups: draft.groups,
  };
}

async function loadExistingDraft(
  pathname: string,
  validate: (value: unknown) => Record<string, unknown>,
): Promise<Record<string, unknown> | null> {
  if (!existsSync(pathname)) {
    return null;
  }
  return loadCanonicalDocument(pathname, validate);
}

async function loadExistingFinal(
  pathname: string,
  validate: (value: unknown) => Record<string, unknown>,
): Promise<Record<string, unknown> | null> {
  const markerPath = `${pathname.slice(0, -path.extname(pathname).length)}.sha256`;
  const resultExists = existsSync(pathname);
  const markerExists = existsSync(markerPath);
  if (!resultExists && !markerExists) {
    return null;
  }
  if (!resultExists) {
    throw new Error(`final JSONなしでSHA markerだけが存在します: ${path.basename(pathname)}`);
  }
  const document = await loadCanonicalDocument(pathname, validate);
  const raw = await readFile(pathname);
  const expectedMarker = Buffer.from(`${sha256(raw)}\n`, "ascii");
  if (!markerExists) {
    await atomicReplace(markerPath, expectedMarker);
    return document;
  }
  const marker = await readFile(markerPath);
  if (!marker.equals(expectedMarker)) {
    throw new Error(`${path.basename(pathname)} のSHA markerが一致しません。`);
  }
  return document;
}

async function loadCanonicalDocument(
  pathname: string,
  validate: (value: unknown) => Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const raw = await readFile(pathname);
  assertCanonicalArtifactBytes(raw, path.basename(pathname));
  let decoded: unknown;
  try {
    decoded = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
  } catch (reason: unknown) {
    throw new Error(`${path.basename(pathname)} が不正です: ${errorMessage(reason)}`);
  }
  return validate(decoded);
}

function assertCanonicalArtifactBytes(raw: Buffer, label: string): void {
  assertCanonicalJsonBytes(
    raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer,
    label,
  );
}

async function atomicReplace(pathname: string, bytes: Buffer): Promise<void> {
  const pending = path.join(
    path.dirname(pathname),
    `.${path.basename(pathname)}.${process.pid}.${randomUUID()}.pending`,
  );
  try {
    const handle = await open(pending, "wx", 0o600);
    try {
      await handle.writeFile(bytes);
      await handle.sync();
    } finally {
      await handle.close();
    }
    await rename(pending, pathname);
  } catch (reason: unknown) {
    await unlink(pending).catch(() => undefined);
    throw new Error(`atomic renameに失敗しました: ${pathname}: ${errorMessage(reason)}`);
  }
}

function serveAudio(
  request: IncomingMessage,
  response: ServerResponse,
  candidate: ValidatedCandidate,
): void {
  response.setHeader("Accept-Ranges", "bytes");
  response.setHeader("Content-Type", candidate.contentType);
  response.setHeader("Cache-Control", "private, no-store");
  const rangeHeader = request.headers.range;
  if (rangeHeader === undefined) {
    response.statusCode = 200;
    response.setHeader("Content-Length", candidate.size);
    if (request.method === "HEAD") {
      response.end();
      return;
    }
    createReadStream(candidate.absolutePath).pipe(response);
    return;
  }
  const range = parseRange(rangeHeader, candidate.size);
  if (range === null) {
    response.statusCode = 416;
    response.setHeader("Content-Range", `bytes */${candidate.size}`);
    response.end();
    return;
  }
  response.statusCode = 206;
  response.setHeader("Content-Range", `bytes ${range.start}-${range.end}/${candidate.size}`);
  response.setHeader("Content-Length", range.end - range.start + 1);
  if (request.method === "HEAD") {
    response.end();
    return;
  }
  createReadStream(candidate.absolutePath, range).pipe(response);
}

function parseRange(
  value: string,
  size: number,
): { readonly start: number; readonly end: number } | null {
  const match = RANGE_PATTERN.exec(value);
  if (!match || size === 0 || (match[1] === "" && match[2] === "")) {
    return null;
  }
  if (match[1] === "") {
    const suffix = Number(match[2]);
    if (!Number.isSafeInteger(suffix) || suffix <= 0) {
      return null;
    }
    return { start: Math.max(0, size - suffix), end: size - 1 };
  }
  const start = Number(match[1]);
  const requestedEnd = match[2] === "" ? size - 1 : Number(match[2]);
  if (
    !Number.isSafeInteger(start) ||
    !Number.isSafeInteger(requestedEnd) ||
    start < 0 ||
    start >= size ||
    requestedEnd < start
  ) {
    return null;
  }
  return { start, end: Math.min(requestedEnd, size - 1) };
}

async function readJsonBody(request: IncomingMessage): Promise<unknown> {
  const declared = request.headers["content-length"];
  if (declared !== undefined) {
    const length = Number(declared);
    if (!Number.isSafeInteger(length) || length < 0 || length > BODY_LIMIT_BYTES) {
      throw new HttpError(413, `request body は${BODY_LIMIT_BYTES} bytes以下が必要です。`);
    }
  }
  const chunks: Buffer[] = [];
  let length = 0;
  for await (const chunk of request) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    length += bytes.length;
    if (length > BODY_LIMIT_BYTES) {
      throw new HttpError(413, `request body は${BODY_LIMIT_BYTES} bytes以下が必要です。`);
    }
    chunks.push(bytes);
  }
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks)));
  } catch (reason: unknown) {
    throw new HttpError(400, `request body は正しいUTF-8 JSONが必要です: ${errorMessage(reason)}`);
  }
}

function assertMutationRequest(request: IncomingMessage, origin: string, token: string): void {
  if (request.headers.origin !== origin) {
    throw new HttpError(403, `Origin は ${origin} と完全一致する必要があります。`);
  }
  if (request.headers[MUTATION_TOKEN_HEADER] !== token) {
    throw new HttpError(403, "mutation token が一致しません。");
  }
}

function requestValidation<T>(validate: () => T): T {
  try {
    return validate();
  } catch (reason: unknown) {
    if (reason instanceof HttpError) {
      throw reason;
    }
    throw new HttpError(400, errorMessage(reason));
  }
}

function assertEmptyBody(request: IncomingMessage): void {
  const length = request.headers["content-length"];
  if (length !== undefined && length !== "0") {
    throw new HttpError(400, "shutdown request body は空である必要があります。");
  }
}

function sendJson(response: ServerResponse, status: number, value: unknown): void {
  if (response.headersSent) {
    response.destroy();
    return;
  }
  const bytes = canonicalJsonBytes(value, "API response");
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.setHeader("Content-Length", bytes.length);
  response.setHeader("Cache-Control", "no-store");
  response.end(bytes);
}

function writeNewSession(session: ListeningSessionState): void {
  const bytes = canonicalJsonBytes(session, "session");
  const descriptor = openSync(SESSION_FILE, "wx", 0o600);
  try {
    writeSync(descriptor, bytes);
    fsyncSync(descriptor);
  } finally {
    closeSync(descriptor);
  }
}

function writeSessionAtomic(session: ListeningSessionState): void {
  const pending = path.join(LISTENING_STATE_DIR, `.session.${process.pid}.${randomUUID()}.pending`);
  const descriptor = openSync(pending, "wx", 0o600);
  try {
    writeSync(descriptor, canonicalJsonBytes(session, "session"));
    fsyncSync(descriptor);
  } finally {
    closeSync(descriptor);
  }
  try {
    renameSync(pending, SESSION_FILE);
  } catch (reason: unknown) {
    if (existsSync(pending)) {
      unlinkSync(pending);
    }
    throw reason;
  }
}

function removeOwnedSession(sessionId: string): void {
  try {
    const value = JSON.parse(readFileSync(SESSION_FILE, "utf8")) as { readonly id?: unknown };
    if (value.id === sessionId) {
      unlinkSync(SESSION_FILE);
    }
  } catch (reason: unknown) {
    const code = (reason as NodeJS.ErrnoException).code;
    if (code !== "ENOENT") {
      console.error(`session cleanup failed: ${errorMessage(reason)}`);
    }
  }
}

function listBundleFiles(root: string): string[] {
  const files: string[] = [];
  const visit = (directory: string) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name);
      const relative = path.relative(root, absolute).replaceAll("\\", "/");
      if (entry.isSymbolicLink()) {
        throw new Error(`bundleにsymbolic linkは使用できません: ${relative}`);
      }
      if (entry.isDirectory()) {
        visit(absolute);
      } else if (entry.isFile()) {
        files.push(relative);
      } else {
        throw new Error(`bundleに通常file/directory以外があります: ${relative}`);
      }
    }
  };
  visit(root);
  return files.sort(compareText);
}

function safeBundleChild(root: string, relative: string, label: string): string {
  safeAudioPath(relative, label);
  const absolute = path.resolve(root, ...relative.split("/"));
  if (!absolute.startsWith(`${root}${path.sep}`)) {
    throw new Error(`${label} がbundle root外を指しています。`);
  }
  return absolute;
}

function safeAudioPath(value: unknown, label: string): string {
  const text = nonEmptyText(value, label);
  if (
    text.includes("\\") ||
    path.posix.isAbsolute(text) ||
    !text.startsWith("audio/") ||
    !/\.(?:flac|mp3|opus|wav)$/.test(text) ||
    text.split("/").some((segment) => segment === "" || segment === "." || segment === "..") ||
    path.posix.normalize(text) !== text
  ) {
    throw new Error(`${label} は安全なrelative POSIX audio pathが必要です。`);
  }
  return text;
}

function requireAbsoluteDirectory(value: string, label: string): void {
  if (!path.isAbsolute(value)) {
    throw new Error(`${label} は絶対pathが必要です: ${value}`);
  }
  let info;
  try {
    info = lstatSync(value);
  } catch (reason: unknown) {
    throw new Error(`${label} directoryがありません: ${value}: ${errorMessage(reason)}`);
  }
  if (!info.isDirectory() || info.isSymbolicLink()) {
    throw new Error(`${label} は既存の通常directoryが必要です: ${value}`);
  }
}

function requiredCandidate(bundle: ValidatedBundle, id: string): ValidatedCandidate {
  const candidate = bundle.candidates.get(id);
  if (!candidate) {
    throw new HttpError(404, "candidate id がbundleにありません。");
  }
  return candidate;
}

function exactObject(
  value: unknown,
  keys: readonly string[],
  label: string,
): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new HttpError(400, `${label} はobjectが必要です。`);
  }
  const object = value as Record<string, unknown>;
  const actual = Object.keys(object).sort(compareText);
  const expected = [...keys].sort(compareText);
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new HttpError(400, `${label} のkeyがexact contractと一致しません: ${actual.join(",")}`);
  }
  return object;
}

function sha(value: unknown, label: string): string {
  if (typeof value !== "string" || !SHA_PATTERN.test(value)) {
    throw new Error(`${label} は完全な小文字SHA-256が必要です。`);
  }
  return value;
}

function safeSegment(value: unknown, label: string): string {
  const text = nonEmptyText(value, label);
  if (!SAFE_SEGMENT_PATTERN.test(text)) {
    throw new Error(`${label} は安全なpath segmentが必要です。`);
  }
  return text;
}

function safePathSegment(value: unknown, label: string): string {
  const text = nonEmptyText(value, label);
  if (text === "." || text === ".." || text.includes("/") || text.includes("\\")) {
    throw new Error(`${label} は安全なpath segmentが必要です。`);
  }
  return text;
}

function nonEmptyText(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0 || value !== value.trim()) {
    throw new Error(`${label} は前後空白のない非空文字列が必要です。`);
  }
  return value;
}

function positiveInteger(value: unknown, label: string): number {
  const result = nonNegativeInteger(value, label);
  if (result === 0) {
    throw new Error(`${label} は1以上が必要です。`);
  }
  return result;
}

function nonNegativeInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${label} は0以上の安全な整数が必要です。`);
  }
  return value;
}

function shaArray(value: unknown, label: string, allowEmpty: boolean): string[] {
  if (!Array.isArray(value) || (!allowEmpty && value.length === 0)) {
    throw new Error(`${label} は${allowEmpty ? "" : "1件以上の"}配列が必要です。`);
  }
  const values = value.map((item, index) => sha(item, `${label}[${index}]`));
  if (new Set(values).size !== values.length) {
    throw new Error(`${label} に重複があります。`);
  }
  return values;
}

function stringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error(`${label} は文字列配列が必要です。`);
  }
  return value as string[];
}

function exactTextArray(value: unknown, expected: readonly string[], label: string): void {
  if (
    !Array.isArray(value) ||
    value.length !== expected.length ||
    value.some((item, index) => item !== expected[index])
  ) {
    throw new Error(`${label} はexactな固定順が必要です。`);
  }
}

function enumValue(value: unknown, values: readonly string[], label: string): string {
  if (typeof value !== "string" || !values.includes(value)) {
    throw new Error(`${label} が許可値と一致しません。`);
  }
  return value;
}

function sortCanonical(value: unknown): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error("non-finite number");
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(sortCanonical);
  }
  if (typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => compareText(left, right))
        .map(([key, child]) => [key, sortCanonical(child)]),
    );
  }
  throw new Error(`unsupported JSON value: ${typeof value}`);
}

function sha256(value: NodeJS.ArrayBufferView): string {
  return createHash("sha256").update(value).digest("hex");
}

function audioContentType(relative: string): string {
  const extension = path.extname(relative);
  const contentTypes: Record<string, string> = {
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
  };
  return contentTypes[extension]!;
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function appendServerLog(message: string): void {
  try {
    appendFileSync(LOG_FILE, `[${new Date().toISOString()}] ${message}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
  } catch {
    // The session state remains the source of truth if diagnostics cannot be written.
  }
}

function parseServerArguments(argv: readonly string[]): {
  readonly workflow: ListeningWorkflow;
  readonly bundleDirectory: string;
  readonly outputDirectory: string;
  readonly authorityPlanPath: string | null;
  readonly expectedPlanSha256: string | null;
  readonly port: number;
} {
  const values = new Map<string, string>();
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (
      (key !== "--workflow" &&
        key !== "--bundle" &&
        key !== "--output" &&
        key !== "--authority-plan" &&
        key !== "--expected-plan-sha256" &&
        key !== "--port") ||
      value === undefined
    ) {
      throw new Error(
        "usage: listening-app-server.ts --workflow <role-review-anchor-v2|role-baseline-v1> --bundle <absolute-dir> --output <absolute-dir> [--authority-plan <absolute-canonical-plan.json> --expected-plan-sha256 <sha256>] --port <port>",
      );
    }
    if (values.has(key)) {
      throw new Error(`option が重複しています: ${key}`);
    }
    values.set(key, value);
  }
  const bundleDirectory = values.get("--bundle");
  const outputDirectory = values.get("--output");
  const portValue = values.get("--port");
  const workflowValue = values.get("--workflow");
  if (
    workflowValue === undefined ||
    bundleDirectory === undefined ||
    outputDirectory === undefined ||
    portValue === undefined
  ) {
    throw new Error("--workflow / --bundle / --output / --port は必須です。");
  }
  const workflow = parseWorkflow(workflowValue);
  const authorityPlanPath = values.get("--authority-plan") ?? null;
  const expectedPlanSha256 = values.get("--expected-plan-sha256") ?? null;
  if (
    workflow === BASELINE_WORKFLOW &&
    (authorityPlanPath === null || expectedPlanSha256 === null)
  ) {
    throw new Error(`${BASELINE_WORKFLOW} は--authority-planが必要です。`);
  }
  if (
    (workflow === ANCHOR_WORKFLOW || workflow === QUALITY_REVIEW_WORKFLOW) &&
    (authorityPlanPath !== null || expectedPlanSha256 !== null)
  ) {
    throw new Error(`${workflow} は--authority-planを受け付けません。`);
  }
  const port = Number(portValue);
  if (!Number.isInteger(port)) {
    throw new Error("--port は整数が必要です。");
  }
  return {
    workflow,
    bundleDirectory,
    outputDirectory,
    authorityPlanPath,
    expectedPlanSha256,
    port,
  };
}

export function parseWorkflow(value: string): ListeningWorkflow {
  if (value === BASELINE_WORKFLOW) {
    throw new Error(`${BASELINE_WORKFLOW} は廃止済みの全量聴取workflowであり、起動できません。`);
  }
  if (value !== ANCHOR_WORKFLOW && value !== QUALITY_REVIEW_WORKFLOW) {
    throw new Error(
      `--workflow は${ANCHOR_WORKFLOW} / ${QUALITY_REVIEW_WORKFLOW}のいずれかを明示してください: ${value}`,
    );
  }
  return value;
}

const invokedPath = process.argv[1] === undefined ? "" : path.resolve(process.argv[1]);
if (invokedPath === fileURLToPath(import.meta.url)) {
  runListeningServer(parseServerArguments(process.argv.slice(2))).catch((reason: unknown) => {
    appendServerLog(`startup failed: ${errorMessage(reason)}`);
    console.error(errorMessage(reason));
    process.exitCode = 1;
  });
}
