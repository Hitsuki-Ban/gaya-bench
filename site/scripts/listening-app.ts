import {
  closeSync,
  existsSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  unlinkSync,
} from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  ANCHOR_DRAFT_FILE,
  ANCHOR_FINAL_FILE,
  BASELINE_DRAFT_FILE,
  BASELINE_FINAL_FILE,
  LISTENING_PROTOCOL,
  LISTENING_STATE_DIR,
  ANCHOR_WORKFLOW,
  BASELINE_WORKFLOW,
  QUALITY_REVIEW_DRAFT_FILE,
  QUALITY_REVIEW_FINAL_FILE,
  QUALITY_REVIEW_WORKFLOW,
  LOG_FILE,
  MUTATION_TOKEN_HEADER,
  SESSION_FILE,
  type ListeningSessionState,
  type ListeningWorkflow,
  parseWorkflow,
  readListeningPlanAuthority,
} from "./listening-app-server.ts";

const SERVER_SCRIPT = fileURLToPath(new URL("./listening-app-server.ts", import.meta.url));
const DEFAULT_PORT = 4173;
const START_TIMEOUT_MS = 60_000;

async function main(argv: readonly string[]): Promise<void> {
  const command = argv[0];
  if (command === "start") {
    await start(parseStartArguments(argv.slice(1)));
    return;
  }
  if (command === "status") {
    requireNoArguments(argv.slice(1), "status");
    await status();
    return;
  }
  if (command === "stop") {
    requireNoArguments(argv.slice(1), "stop");
    await stop();
    return;
  }
  throw new Error("usage: listening-app.ts <start|status|stop>");
}

async function start(options: {
  readonly workflow: ListeningWorkflow;
  readonly bundle: string;
  readonly output: string;
  readonly authorityPlan: string | null;
  readonly port: number;
}): Promise<void> {
  requireAbsoluteDirectory(options.bundle, "--bundle");
  requireAbsoluteDirectory(options.output, "--output");
  if (options.workflow === BASELINE_WORKFLOW && options.authorityPlan === null) {
    throw new Error(`${BASELINE_WORKFLOW} は--authority-planが必要です。`);
  }
  if (
    (options.workflow === ANCHOR_WORKFLOW || options.workflow === QUALITY_REVIEW_WORKFLOW) &&
    options.authorityPlan !== null
  ) {
    throw new Error(`${options.workflow} は--authority-planを受け付けません。`);
  }
  const authority =
    options.authorityPlan === null
      ? null
      : await readListeningPlanAuthority({
          authorityPlanPath: options.authorityPlan,
          bundleRoot: path.resolve(options.bundle),
          outputRoot: path.resolve(options.output),
        });
  mkdirSync(LISTENING_STATE_DIR, { recursive: true });
  if (existsSync(SESSION_FILE)) {
    const existing = readSession();
    const health = existing === null ? null : await fetchHealth(existing);
    if (health !== null || (existing !== null && isProcessRunning(existing.pid))) {
      throw new Error(
        `listening app の活動中sessionが既にあります: ${existing!.origin}/internal.html#/completion`,
      );
    }
    throw new Error(
      "staleなlistening sessionがあります。`vp run listening:stop` で明示的に清理してください。",
    );
  }

  const child = launchDaemon([
    SERVER_SCRIPT,
    "--workflow",
    options.workflow,
    "--bundle",
    path.resolve(options.bundle),
    "--output",
    path.resolve(options.output),
    ...(authority === null
      ? []
      : ["--authority-plan", authority.path, "--expected-plan-sha256", authority.sha256]),
    "--port",
    String(options.port),
  ]);

  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (existsSync(SESSION_FILE)) {
      const session = readSession();
      if (session?.state === "ready" && (await fetchHealth(session)) !== null) {
        console.log(
          `Gaya listening app を開始しました: ${session.origin}/internal.html#/completion`,
        );
        return;
      }
    }
    if (child.pid !== undefined && !isProcessRunning(child.pid)) {
      throw new Error(`listening daemon がready前に終了しました。\n${readLogTail()}`);
    }
    await delay(100);
  }
  const logTail = readLogTail();
  const cleanupMessage = await terminateSpawnedDaemon(child.pid);
  throw new Error(
    `listening daemon が${START_TIMEOUT_MS}ms以内にreadyになりませんでした。${cleanupMessage}\n${logTail}`,
  );
}

function launchDaemon(arguments_: readonly string[]): { readonly pid: number | undefined } {
  if (process.platform === "win32") {
    return launchWindowsDaemon(arguments_);
  }
  const logDescriptor = openSync(LOG_FILE, "a", 0o600);
  try {
    const child = spawn(process.execPath, arguments_, {
      cwd: path.dirname(SERVER_SCRIPT),
      detached: true,
      stdio: ["ignore", logDescriptor, logDescriptor],
    });
    child.unref();
    return { pid: child.pid };
  } finally {
    closeSync(logDescriptor);
  }
}

function launchWindowsDaemon(arguments_: readonly string[]): { readonly pid: number } {
  const commandLine = [process.execPath, ...arguments_].map(quoteWindowsArgument).join(" ");
  const script = [
    "$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly",
    "$startup.ShowWindow = 0",
    `$created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=${powershellLiteral(commandLine)}; CurrentDirectory=${powershellLiteral(path.dirname(SERVER_SCRIPT))}; ProcessStartupInformation=$startup}`,
    'if ($created.ReturnValue -ne 0) { throw "Win32_Process.Create failed: $($created.ReturnValue)" }',
    "[Console]::Out.Write([string]$created.ProcessId)",
  ].join("; ");
  const encoded = Buffer.from(script, "utf16le").toString("base64");
  const launched = spawnSync(
    "powershell.exe",
    ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
    {
      encoding: "utf8",
      timeout: 10_000,
      windowsHide: true,
    },
  );
  if (launched.error || launched.status !== 0) {
    throw new Error(
      `Windows listening daemonの起動に失敗しました: ${launched.error ? errorMessage(launched.error) : launched.stderr.trim()}`,
    );
  }
  const pid = Number(launched.stdout.trim());
  if (!Number.isSafeInteger(pid) || pid <= 0) {
    throw new Error(`Win32_Process.Create が不正なPIDを返しました: ${launched.stdout.trim()}`);
  }
  return { pid };
}

function quoteWindowsArgument(value: string): string {
  if (value.length > 0 && !/[\s"]/.test(value)) {
    return value;
  }
  let quoted = '"';
  let backslashes = 0;
  for (const character of value) {
    if (character === "\\") {
      backslashes += 1;
      continue;
    }
    if (character === '"') {
      quoted += `${"\\".repeat(backslashes * 2 + 1)}"`;
    } else {
      quoted += `${"\\".repeat(backslashes)}${character}`;
    }
    backslashes = 0;
  }
  return `${quoted}${"\\".repeat(backslashes * 2)}"`;
}

function powershellLiteral(value: string): string {
  return `'${value.replaceAll("'", "''")}'`;
}

async function terminateSpawnedDaemon(pid: number | undefined): Promise<string> {
  if (pid === undefined || !isProcessRunning(pid)) {
    return "";
  }
  try {
    process.kill(pid);
  } catch (reason: unknown) {
    return ` 起動したpid=${pid}の回収にも失敗しました: ${errorMessage(reason)}`;
  }
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline && isProcessRunning(pid)) {
    await delay(50);
  }
  if (isProcessRunning(pid)) {
    return ` 起動したpid=${pid}を5秒以内に回収できませんでした。`;
  }
  const session = readSession();
  if (session?.pid === pid) {
    unlinkSync(SESSION_FILE);
  }
  return ` 起動したpid=${pid}は回収しました。`;
}

async function status(): Promise<void> {
  if (!existsSync(SESSION_FILE)) {
    console.log("Gaya listening app は停止しています。");
    return;
  }
  const session = readSession();
  if (session === null) {
    throw new Error(
      "listening session.json が不正です（stale）。`vp run listening:stop` で清理してください。",
    );
  }
  const health = await fetchHealth(session);
  if (health === null && session.state === "starting" && isProcessRunning(session.pid)) {
    console.log(`Gaya listening app は起動中です: ${session.origin} (pid=${session.pid})`);
    return;
  }
  if (health === null) {
    throw new Error(
      `Gaya listening app session はstaleです (pid=${session.pid}, origin=${session.origin})。` +
        " `vp run listening:stop` で清理してください。",
    );
  }
  console.log(
    [
      `Gaya listening app は活動中です: ${session.origin}/internal.html#/completion (pid=${session.pid})`,
      `  bundle: ${session.bundle}`,
      `  output: ${session.output}`,
      `  workflow: ${session.workflow}`,
      ...(session.authority_plan === null
        ? []
        : [
            `  authority plan: ${session.authority_plan}`,
            `  expected plan SHA-256: ${session.expected_plan_sha256}`,
          ]),
      `  draft: ${path.join(session.output, resultFiles(session.workflow).draft)}`,
      `  revision: ${String(health.revision)}`,
      `  finalized: ${health.finalized === true ? "yes" : "no"}`,
      `  decision: ${path.join(session.output, resultFiles(session.workflow).final)}`,
    ].join("\n"),
  );
}

async function stop(): Promise<void> {
  if (!existsSync(SESSION_FILE)) {
    console.log("Gaya listening app は既に停止しています。");
    return;
  }
  const session = readSession();
  if (session === null) {
    unlinkSync(SESSION_FILE);
    console.log("staleなlistening session stateを清理しました。結果fileは保持しています。");
    return;
  }
  const health = await fetchHealth(session);
  if (health === null) {
    if (isProcessRunning(session.pid)) {
      throw new Error(
        `sessionのPID ${session.pid} は存在しますが、同一listening daemonだと検証できません。` +
          " 無関係processを停止しないため自動killせず、session stateを保持します。",
      );
    }
    removeSessionIfOwned(session.id);
    console.log("staleなlistening session stateを清理しました。結果fileは保持しています。");
    return;
  }
  const response = await fetch(`${session.origin}/__gaya-listening/shutdown`, {
    method: "POST",
    headers: {
      Origin: session.origin,
      [MUTATION_TOKEN_HEADER]: session.mutation_token,
      "Content-Length": "0",
    },
    signal: AbortSignal.timeout(2_000),
  }).catch((reason: unknown) => {
    throw new Error(`shutdown requestに失敗しました: ${errorMessage(reason)}`);
  });
  if (!response.ok) {
    throw new Error(
      `shutdown requestが拒否されました: HTTP ${response.status} ${await response.text()}`,
    );
  }
  await waitForStop(session);
  console.log("Gaya listening app を停止しました。結果fileは保持しています。");
}

async function waitForStop(session: ListeningSessionState): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (!existsSync(SESSION_FILE)) {
      return;
    }
    await delay(100);
  }
  if (!isProcessRunning(session.pid)) {
    removeSessionIfOwned(session.id);
    return;
  }
  throw new Error("daemonの停止またはsession stateの清理を10秒以内に確認できませんでした。");
}

function removeSessionIfOwned(sessionId: string): void {
  const current = readSession();
  if (current?.id === sessionId) {
    unlinkSync(SESSION_FILE);
  }
}

function parseStartArguments(argv: readonly string[]): {
  readonly workflow: ListeningWorkflow;
  readonly bundle: string;
  readonly output: string;
  readonly authorityPlan: string | null;
  readonly port: number;
} {
  const options = new Map<string, string>();
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (
      (key !== "--workflow" &&
        key !== "--bundle" &&
        key !== "--output" &&
        key !== "--authority-plan" &&
        key !== "--port") ||
      value === undefined
    ) {
      throw new Error(
        "usage: vp run listening:start --workflow <role-review-anchor-v2|role-quality-review-v1> --bundle <absolute-dir> --output <absolute-dir> [--port 4173]",
      );
    }
    if (options.has(key)) {
      throw new Error(`option が重複しています: ${key}`);
    }
    options.set(key, value);
  }
  const bundle = options.get("--bundle");
  const output = options.get("--output");
  const workflowValue = options.get("--workflow");
  if (workflowValue === undefined || bundle === undefined || output === undefined) {
    throw new Error("--workflow / --bundle / --output は必須です。");
  }
  const workflow = parseWorkflow(workflowValue);
  const authorityPlan = options.get("--authority-plan") ?? null;
  if (workflow === BASELINE_WORKFLOW && authorityPlan === null) {
    throw new Error(`${BASELINE_WORKFLOW} は--authority-planが必要です。`);
  }
  if (
    (workflow === ANCHOR_WORKFLOW || workflow === QUALITY_REVIEW_WORKFLOW) &&
    authorityPlan !== null
  ) {
    throw new Error(`${workflow} は--authority-planを受け付けません。`);
  }
  const portText = options.get("--port");
  const port = portText === undefined ? DEFAULT_PORT : Number(portText);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error("--port は1..65535の整数が必要です。");
  }
  return { workflow, bundle, output, authorityPlan, port };
}

function readSession(): ListeningSessionState | null {
  try {
    const value = JSON.parse(readFileSync(SESSION_FILE, "utf8")) as Record<string, unknown>;
    if (
      value.protocol !== LISTENING_PROTOCOL ||
      (value.workflow !== ANCHOR_WORKFLOW &&
        value.workflow !== BASELINE_WORKFLOW &&
        value.workflow !== QUALITY_REVIEW_WORKFLOW) ||
      (value.state !== "starting" && value.state !== "ready") ||
      typeof value.id !== "string" ||
      typeof value.pid !== "number" ||
      !Number.isInteger(value.pid) ||
      typeof value.port !== "number" ||
      !Number.isInteger(value.port) ||
      value.origin !== `http://127.0.0.1:${value.port}` ||
      typeof value.mutation_token !== "string" ||
      !/^[0-9a-f]{64}$/.test(value.mutation_token) ||
      typeof value.started_at !== "string" ||
      typeof value.bundle !== "string" ||
      typeof value.output !== "string" ||
      !validSessionAuthority(value)
    ) {
      return null;
    }
    return value as unknown as ListeningSessionState;
  } catch {
    return null;
  }
}

async function fetchHealth(
  session: ListeningSessionState,
): Promise<Record<string, unknown> | null> {
  try {
    const response = await fetch(`${session.origin}/__gaya-listening/health`, {
      signal: AbortSignal.timeout(1_500),
    });
    if (!response.ok) {
      return null;
    }
    const health = (await response.json()) as Record<string, unknown>;
    if (
      health.status !== "ok" ||
      health.protocol !== LISTENING_PROTOCOL ||
      health.workflow !== session.workflow ||
      health.session_id !== session.id ||
      health.authority_plan !== session.authority_plan ||
      health.expected_plan_sha256 !== session.expected_plan_sha256 ||
      !Number.isSafeInteger(health.revision) ||
      (health.revision as number) < 0 ||
      typeof health.finalized !== "boolean" ||
      typeof health.shutting_down !== "boolean"
    ) {
      return null;
    }
    return health;
  } catch {
    return null;
  }
}

function validSessionAuthority(value: Record<string, unknown>): boolean {
  if (value.workflow === ANCHOR_WORKFLOW || value.workflow === QUALITY_REVIEW_WORKFLOW) {
    return value.authority_plan === null && value.expected_plan_sha256 === null;
  }
  return (
    typeof value.authority_plan === "string" &&
    path.isAbsolute(value.authority_plan) &&
    typeof value.expected_plan_sha256 === "string" &&
    /^[0-9a-f]{64}$/.test(value.expected_plan_sha256)
  );
}

function resultFiles(workflow: ListeningWorkflow): {
  readonly draft: string;
  readonly final: string;
} {
  if (workflow === ANCHOR_WORKFLOW) {
    return { draft: ANCHOR_DRAFT_FILE, final: ANCHOR_FINAL_FILE };
  }
  return workflow === QUALITY_REVIEW_WORKFLOW
    ? { draft: QUALITY_REVIEW_DRAFT_FILE, final: QUALITY_REVIEW_FINAL_FILE }
    : { draft: BASELINE_DRAFT_FILE, final: BASELINE_FINAL_FILE };
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

function requireNoArguments(argv: readonly string[], command: string): void {
  if (argv.length !== 0) {
    throw new Error(`${command} はargumentを受理しません。`);
  }
}

function isProcessRunning(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function readLogTail(): string {
  if (!existsSync(LOG_FILE)) {
    return "(server.log はありません)";
  }
  const contents = readFileSync(LOG_FILE, "utf8");
  return contents.slice(Math.max(0, contents.length - 8_192));
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

main(process.argv.slice(2)).catch((reason: unknown) => {
  console.error(errorMessage(reason));
  process.exitCode = 1;
});
