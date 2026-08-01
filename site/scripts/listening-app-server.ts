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

export const LISTENING_HOST = "127.0.0.1";
export const LISTENING_PROTOCOL = "gaya-listening-session-v1";
export const LISTENING_WORKFLOW = "role-review-anchor-v2";
export const BUNDLE_FILE = "role-review-v2.json";
export const DRAFT_FILE = "role-review-anchor-draft-v2.json";
export const FINAL_FILE = "role-review-anchor-decision-v2.json";
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
  readonly workflow: typeof LISTENING_WORKFLOW;
  readonly state: "starting" | "ready";
  readonly id: string;
  readonly pid: number;
  readonly port: number;
  readonly origin: string;
  readonly mutation_token: string;
  readonly started_at: string;
  readonly bundle: string;
  readonly output: string;
}

export interface ListeningRuntime {
  readonly bundle: ValidatedBundle;
  readonly outputRoot: string;
  readonly origin: string;
  readonly mutationToken: string;
  readonly sessionId: string;
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

export async function validateListeningBundle(bundleDirectory: string): Promise<ValidatedBundle> {
  requireAbsoluteDirectory(bundleDirectory, "bundle");
  const root = path.resolve(bundleDirectory);
  const bundlePath = path.join(root, BUNDLE_FILE);
  const raw = await readFile(bundlePath).catch((reason: unknown) => {
    throw new Error(`${BUNDLE_FILE} を読めません: ${errorMessage(reason)}`);
  });
  let decoded: unknown;
  try {
    decoded = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
  } catch (reason: unknown) {
    throw new Error(`${BUNDLE_FILE} は正しいUTF-8 JSONが必要です: ${errorMessage(reason)}`);
  }
  const canonical = canonicalJsonBytes(decoded, BUNDLE_FILE);
  if (!raw.equals(canonical)) {
    throw new Error(`${BUNDLE_FILE} はcanonical JSON bytesが必要です。`);
  }
  const document = validateBundleDocument(decoded);
  const actualPaths = listBundleFiles(root);
  const candidates = new Map<string, ValidatedCandidate>();
  const referencedPaths = new Set<string>([BUNDLE_FILE]);
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
    document,
    root,
    bundleSha256: sha256(raw),
    candidates,
    groupBindings,
  };
}

export async function createListeningRuntime(options: {
  readonly bundleDirectory: string;
  readonly outputDirectory: string;
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
  const bundle = await validateListeningBundle(options.bundleDirectory);
  const origin = `http://${LISTENING_HOST}:${options.port}`;
  const mutationToken = options.mutationToken ?? randomBytes(32).toString("hex");
  const sessionId = options.sessionId ?? randomUUID();
  const api = await createListeningApi({
    bundle,
    outputRoot,
    origin,
    mutationToken,
    sessionId,
    onShutdown: options.onShutdown,
  });
  return { bundle, outputRoot, origin, mutationToken, sessionId, api };
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

function directoriesOverlap(left: string, right: string): boolean {
  const resolvedLeft = path.resolve(left);
  const resolvedRight = path.resolve(right);
  return (
    resolvedLeft === resolvedRight ||
    resolvedLeft.startsWith(`${resolvedRight}${path.sep}`) ||
    resolvedRight.startsWith(`${resolvedLeft}${path.sep}`)
  );
}

export async function createListeningApi(options: {
  readonly bundle: ValidatedBundle;
  readonly outputRoot: string;
  readonly origin: string;
  readonly mutationToken: string;
  readonly sessionId: string;
  readonly onShutdown?: () => void;
}): Promise<ListeningApi> {
  const draftPath = path.join(options.outputRoot, DRAFT_FILE);
  const finalPath = path.join(options.outputRoot, FINAL_FILE);
  const draft = await loadExistingDraft(draftPath, (value) =>
    validateDraftDocument(value, options.bundle),
  );
  const final = await loadExistingFinal(finalPath, (value) =>
    validateDecisionDocument(value, options.bundle),
  );
  if (final !== null) {
    if (draft === null) {
      throw new Error("final decisionの復元には対応する保存済みdraftが必要です。");
    }
    const expectedDecision = decisionFromDraft(draft);
    try {
      validateDecisionDocument(expectedDecision, options.bundle);
    } catch {
      throw new Error("final decisionに対応するdraftは全106 groupが確認済みではありません。");
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
        workflow: LISTENING_WORKFLOW,
        session_id: options.sessionId,
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
        workflow: LISTENING_WORKFLOW,
        bundle: options.bundle.document,
        mutation_token: options.mutationToken,
        revision,
        finalized,
        output: {
          directory_name: path.basename(options.outputRoot),
          draft_file: DRAFT_FILE,
          decision_file: FINAL_FILE,
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
      const validated = requestValidation(() => validateDraftDocument(body.draft, options.bundle));
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
        validateDecisionDocument(body.decision, options.bundle),
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
        const expectedDecision = decisionFromDraft(currentDraft);
        try {
          validateDecisionDocument(expectedDecision, options.bundle);
        } catch {
          throw new HttpError(409, "保存済みdraftは全106 groupが確認済みではありません。");
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
  readonly bundleDirectory: string;
  readonly outputDirectory: string;
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
    workflow: LISTENING_WORKFLOW,
    id: sessionId,
    pid: process.pid,
    port: options.port,
    origin: runtime.origin,
    mutation_token: runtime.mutationToken,
    started_at: startedAt,
    bundle: runtime.bundle.root,
    output: runtime.outputRoot,
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
  let decoded: unknown;
  try {
    decoded = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
  } catch (reason: unknown) {
    throw new Error(`${path.basename(pathname)} が不正です: ${errorMessage(reason)}`);
  }
  if (!raw.equals(canonicalJsonBytes(decoded, path.basename(pathname)))) {
    throw new Error(`${path.basename(pathname)} はcanonical JSON bytesが必要です。`);
  }
  return validate(decoded);
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
  readonly bundleDirectory: string;
  readonly outputDirectory: string;
  readonly port: number;
} {
  const values = new Map<string, string>();
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if ((key !== "--bundle" && key !== "--output" && key !== "--port") || value === undefined) {
      throw new Error(
        "usage: listening-app-server.ts --bundle <absolute-dir> --output <absolute-dir> --port <port>",
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
  if (bundleDirectory === undefined || outputDirectory === undefined || portValue === undefined) {
    throw new Error("--bundle / --output / --port は必須です。");
  }
  const port = Number(portValue);
  if (!Number.isInteger(port)) {
    throw new Error("--port は整数が必要です。");
  }
  return { bundleDirectory, outputDirectory, port };
}

const invokedPath = process.argv[1] === undefined ? "" : path.resolve(process.argv[1]);
if (invokedPath === fileURLToPath(import.meta.url)) {
  runListeningServer(parseServerArguments(process.argv.slice(2))).catch((reason: unknown) => {
    appendServerLog(`startup failed: ${errorMessage(reason)}`);
    console.error(errorMessage(reason));
    process.exitCode = 1;
  });
}
