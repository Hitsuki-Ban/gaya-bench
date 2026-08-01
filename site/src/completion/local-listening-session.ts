import type { RoleReviewBundle, RoleReviewDecision, RoleReviewDraft } from "./types";

const API_ROOT = "/__gaya-listening";

export interface LocalListeningBootstrap {
  readonly format_version: 1;
  readonly protocol: "gaya-listening-session-v1";
  readonly workflow: "role-review-anchor-v2";
  readonly bundle: RoleReviewBundle;
  readonly mutation_token: string;
  readonly revision: number;
  readonly finalized: boolean;
  readonly output: {
    readonly directory_name: string;
    readonly draft_file: "role-review-anchor-draft-v2.json";
    readonly decision_file: "role-review-anchor-decision-v2.json";
  };
}

export interface LocalListeningSaved {
  readonly revision: number;
  readonly saved_at: string;
}

export async function loadLocalListeningBootstrap(): Promise<LocalListeningBootstrap> {
  const value = await requestJson(`${API_ROOT}/bootstrap`);
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("本地听测服务返回了无效的启动信息。");
  }
  const bootstrap = value as Record<string, unknown>;
  if (
    bootstrap.format_version !== 1 ||
    bootstrap.protocol !== "gaya-listening-session-v1" ||
    bootstrap.workflow !== "role-review-anchor-v2" ||
    typeof bootstrap.mutation_token !== "string" ||
    bootstrap.mutation_token.length < 32 ||
    !Number.isSafeInteger(bootstrap.revision) ||
    (bootstrap.revision as number) < 0 ||
    typeof bootstrap.finalized !== "boolean" ||
    typeof bootstrap.bundle !== "object" ||
    bootstrap.bundle === null ||
    typeof bootstrap.output !== "object" ||
    bootstrap.output === null
  ) {
    throw new Error("本地听测服务的启动信息与当前契约不一致。");
  }
  const output = bootstrap.output as Record<string, unknown>;
  if (
    typeof output.directory_name !== "string" ||
    output.draft_file !== "role-review-anchor-draft-v2.json" ||
    output.decision_file !== "role-review-anchor-decision-v2.json"
  ) {
    throw new Error("本地听测服务的结果目录信息无效。");
  }
  return bootstrap as unknown as LocalListeningBootstrap;
}

export function localCandidateAudioUrl(candidateId: string): string {
  return `${API_ROOT}/audio/${encodeURIComponent(candidateId)}`;
}

export async function loadLocalListeningDraft(
  bootstrap: LocalListeningBootstrap,
): Promise<{ readonly revision: number; readonly draft: RoleReviewDraft } | null> {
  const response = await fetch(`${API_ROOT}/draft`, { headers: sessionHeaders(bootstrap) });
  if (response.status === 204) {
    return null;
  }
  if (!response.ok) {
    throw await responseError(response);
  }
  const value = (await response.json()) as unknown;
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("本地听测服务返回了无效草稿。");
  }
  const result = value as Record<string, unknown>;
  if (
    !Number.isSafeInteger(result.revision) ||
    typeof result.draft !== "object" ||
    result.draft === null
  ) {
    throw new Error("本地听测草稿响应与当前契约不一致。");
  }
  return result as unknown as { readonly revision: number; readonly draft: RoleReviewDraft };
}

export async function saveLocalListeningDraft(
  bootstrap: LocalListeningBootstrap,
  revision: number,
  draft: RoleReviewDraft,
): Promise<LocalListeningSaved> {
  return mutation(`${API_ROOT}/draft`, "PUT", bootstrap, { revision, draft });
}

export async function finalizeLocalListening(
  bootstrap: LocalListeningBootstrap,
  revision: number,
  decision: RoleReviewDecision,
): Promise<LocalListeningSaved> {
  return mutation(`${API_ROOT}/finalize`, "POST", bootstrap, { revision, decision });
}

async function mutation(
  url: string,
  method: "PUT" | "POST",
  bootstrap: LocalListeningBootstrap,
  body: unknown,
): Promise<LocalListeningSaved> {
  const response = await fetch(url, {
    method,
    headers: {
      ...sessionHeaders(bootstrap),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  const value = (await response.json()) as unknown;
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("本地听测服务没有返回保存状态。");
  }
  const result = value as Record<string, unknown>;
  if (!Number.isSafeInteger(result.revision) || typeof result.saved_at !== "string") {
    throw new Error("本地听测服务的保存状态无效。");
  }
  return result as unknown as LocalListeningSaved;
}

function sessionHeaders(bootstrap: LocalListeningBootstrap): Record<string, string> {
  return { "X-Gaya-Listening-Token": bootstrap.mutation_token };
}

async function requestJson(url: string): Promise<unknown> {
  const response = await fetch(url);
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json() as Promise<unknown>;
}

async function responseError(response: Response): Promise<Error> {
  let detail = `${response.status} ${response.statusText}`;
  try {
    const value = (await response.json()) as unknown;
    if (
      typeof value === "object" &&
      value !== null &&
      !Array.isArray(value) &&
      typeof (value as Record<string, unknown>).error === "string"
    ) {
      detail = (value as Record<string, string>).error!;
    }
  } catch {
    // The status text is the complete error when the service did not return JSON.
  }
  return new Error(`本地听测服务请求失败：${detail}`);
}
