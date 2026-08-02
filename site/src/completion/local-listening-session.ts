import type { BaselineCatalog, BaselineDraft, BaselineGate, BaselineGroup } from "./baseline-types";
import type { RoleReviewBundle, RoleReviewDecision, RoleReviewDraft } from "./types";

const API_ROOT = "/__gaya-listening";

interface LocalListeningBootstrapBase {
  readonly format_version: 1;
  readonly protocol: "gaya-listening-session-v1";
  readonly mutation_token: string;
  readonly revision: number;
  readonly finalized: boolean;
}

export interface AnchorListeningBootstrap extends LocalListeningBootstrapBase {
  readonly workflow: "role-review-anchor-v2";
  readonly bundle: RoleReviewBundle;
  readonly output: {
    readonly directory_name: string;
    readonly draft_file: "role-review-anchor-draft-v2.json";
    readonly decision_file: "role-review-anchor-decision-v2.json";
  };
}

interface SerializedBaselineCandidate {
  readonly label?: string;
  readonly take_id: string;
  readonly path: string;
  readonly audio_sha256: string;
  readonly gate: BaselineGate;
}

interface SerializedBaselineGroup {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly character: string;
  readonly role_identity_sha256: string;
  readonly reference_voice: string | null;
  readonly role: {
    readonly name: string;
    readonly kind: string;
    readonly gender: string;
    readonly age: string;
    readonly archetype: string;
    readonly voice: string;
    readonly personality: string;
  };
  readonly scene_setting: string;
  readonly scenario_title: string;
  readonly line_text: string;
  readonly reading: string | null;
  readonly situation: string;
  readonly emotion: string;
  readonly intensity: number;
  readonly delivery: string;
  readonly role_epoch_sha256: string;
  readonly source_run_id: string;
  readonly minimum_eligible_candidates: number;
  readonly group_sha256: string;
  readonly candidates: readonly SerializedBaselineCandidate[];
  readonly export_candidates: readonly SerializedBaselineCandidate[];
}

interface SerializedBaselineBundle {
  readonly format_version: 1;
  readonly protocol: "role-baseline-listening-v1";
  readonly plan_sha256: string;
  readonly anchor_selection_sha256: string;
  readonly candidate_set_sha256: string;
  readonly groups: readonly SerializedBaselineGroup[];
}

export interface BaselineListeningBootstrap extends LocalListeningBootstrapBase {
  readonly workflow: "role-baseline-v1";
  readonly bundle: SerializedBaselineBundle;
  readonly output: {
    readonly directory_name: string;
    readonly draft_file: "role-baseline-draft-v1.json";
    readonly decision_file: "role-baseline-decision-v1.json";
  };
}

export interface QualityReviewBundleGroup {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly scenario_title: string;
  readonly text: string;
  readonly delivery: string;
  readonly role: {
    readonly name: string;
    readonly kind: string;
    readonly gender: string;
    readonly age: string;
    readonly archetype: string;
    readonly voice: string;
    readonly personality: string;
  };
  readonly take_id: string;
  readonly audio_path: string;
  readonly audio_sha256: string;
  readonly expected_gender: "female" | "male";
  readonly median_f0_hz: number | null;
  readonly signal:
    | "gender_f0_unavailable"
    | "gender_f0_below_expected"
    | "gender_f0_above_expected";
}

export interface QualityReviewBundle {
  readonly format_version: 1;
  readonly protocol: "role-quality-review-bundle-v1";
  readonly plan_sha256: string;
  readonly decision_sha256: string;
  readonly manifest_sha256: string;
  readonly quality_signals_sha256: string;
  readonly groups: readonly QualityReviewBundleGroup[];
}

export interface QualityReviewListeningBootstrap extends LocalListeningBootstrapBase {
  readonly workflow: "role-quality-review-v1";
  readonly bundle: QualityReviewBundle;
  readonly output: {
    readonly directory_name: string;
    readonly draft_file: "role-quality-review-draft-v1.json";
    readonly decision_file: "role-quality-review-result-v1.json";
  };
}

export interface QualityReviewGroupResult {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
  readonly take_id: string;
  readonly heard: boolean;
  readonly result: "match" | "mismatch" | null;
  readonly notes: string;
}

export interface QualityReviewDraft {
  readonly format_version: 1;
  readonly protocol: "role-quality-review-draft-v1";
  readonly plan_sha256: string;
  readonly decision_sha256: string;
  readonly manifest_sha256: string;
  readonly quality_signals_sha256: string;
  readonly groups: readonly QualityReviewGroupResult[];
  readonly current_index: number;
}

export interface BaselineAbCandidate {
  readonly id: string;
  readonly variant: string;
  readonly audio_path: string;
  readonly audio_sha256: string;
}

export interface BaselineAbBundleGroup {
  readonly id: string;
  readonly track: string;
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly text: string;
  readonly focus: string;
  readonly candidates: readonly BaselineAbCandidate[];
}

export interface BaselineAbBundle {
  readonly format_version: 1;
  readonly protocol: "baseline-quality-ab-bundle-v1";
  readonly study_id: string;
  readonly title: string;
  readonly instructions: string;
  readonly groups: readonly BaselineAbBundleGroup[];
}

export interface BaselineAbListeningBootstrap extends LocalListeningBootstrapBase {
  readonly workflow: "baseline-quality-ab-v1";
  readonly bundle: BaselineAbBundle;
  readonly output: {
    readonly directory_name: string;
    readonly draft_file: "baseline-quality-ab-draft-v1.json";
    readonly decision_file: "baseline-quality-ab-result-v1.json";
  };
}

export interface BaselineAbGroupResult {
  readonly id: string;
  readonly heard_candidate_ids: readonly string[];
  readonly choice: string | null;
  readonly notes: string;
}

export interface BaselineAbDraft {
  readonly format_version: 1;
  readonly protocol: "baseline-quality-ab-draft-v1";
  readonly study_id: string;
  readonly groups: readonly BaselineAbGroupResult[];
  readonly current_index: number;
}

export type LocalListeningBootstrap =
  | AnchorListeningBootstrap
  | BaselineListeningBootstrap
  | QualityReviewListeningBootstrap
  | BaselineAbListeningBootstrap;

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
  if (typeof output.directory_name !== "string") {
    throw new Error("本地听测服务的结果目录信息无效。");
  }
  if (bootstrap.workflow === "role-review-anchor-v2") {
    if (
      output.draft_file !== "role-review-anchor-draft-v2.json" ||
      output.decision_file !== "role-review-anchor-decision-v2.json"
    ) {
      throw new Error("角色声音听测的结果文件契约无效。");
    }
    return bootstrap as unknown as AnchorListeningBootstrap;
  }
  if (bootstrap.workflow === "role-baseline-v1") {
    const bundle = bootstrap.bundle as Record<string, unknown>;
    if (
      output.draft_file !== "role-baseline-draft-v1.json" ||
      output.decision_file !== "role-baseline-decision-v1.json" ||
      bundle.format_version !== 1 ||
      bundle.protocol !== "role-baseline-listening-v1" ||
      !Array.isArray(bundle.groups)
    ) {
      throw new Error("全量基线听测的启动契约无效。");
    }
    return bootstrap as unknown as BaselineListeningBootstrap;
  }
  if (bootstrap.workflow === "role-quality-review-v1") {
    const bundle = bootstrap.bundle as Record<string, unknown>;
    if (
      output.draft_file !== "role-quality-review-draft-v1.json" ||
      output.decision_file !== "role-quality-review-result-v1.json" ||
      bundle.format_version !== 1 ||
      bundle.protocol !== "role-quality-review-bundle-v1" ||
      !Array.isArray(bundle.groups)
    ) {
      throw new Error("角色一致性定向复核的启动契约无效。");
    }
    return bootstrap as unknown as QualityReviewListeningBootstrap;
  }
  if (bootstrap.workflow === "baseline-quality-ab-v1") {
    const bundle = bootstrap.bundle as Record<string, unknown>;
    if (
      output.draft_file !== "baseline-quality-ab-draft-v1.json" ||
      output.decision_file !== "baseline-quality-ab-result-v1.json" ||
      bundle.format_version !== 1 ||
      bundle.protocol !== "baseline-quality-ab-bundle-v1" ||
      !Array.isArray(bundle.groups)
    ) {
      throw new Error("基线质量盲听的启动契约无效。");
    }
    return bootstrap as unknown as BaselineAbListeningBootstrap;
  }
  throw new Error("本地听测服务返回了未支持的 workflow。");
}

export function createLocalBaselineCatalog(bootstrap: BaselineListeningBootstrap): BaselineCatalog {
  const groups: BaselineGroup[] = bootstrap.bundle.groups.map((group) => ({
    model: group.model,
    scenario: group.scenario,
    line: group.line,
    variant: group.variant,
    character: group.character,
    roleIdentitySha256: group.role_identity_sha256,
    referenceVoice: group.reference_voice,
    role: group.role,
    sceneSetting: group.scene_setting,
    scenarioTitle: group.scenario_title,
    lineText: group.line_text,
    reading: group.reading,
    situation: group.situation,
    emotion: group.emotion,
    intensity: group.intensity,
    delivery: group.delivery,
    roleEpochSha256: group.role_epoch_sha256,
    sourceRunId: group.source_run_id,
    minimumEligibleCandidates: group.minimum_eligible_candidates,
    groupSha256: group.group_sha256,
    candidates: group.candidates.map((candidate) => {
      if (typeof candidate.label !== "string") {
        throw new Error(`候选 ${candidate.take_id} 缺少盲听标签。`);
      }
      return {
        label: candidate.label,
        takeId: candidate.take_id,
        audio: {
          key: `baseline:${bootstrap.bundle.candidate_set_sha256}:${candidate.take_id}`,
          url: localCandidateAudioUrl(candidate.take_id),
        },
        gateContent: candidate.gate.content,
      };
    }),
    exportCandidates: group.export_candidates.map((candidate) => ({
      takeId: candidate.take_id,
      path: candidate.path,
      audioSha256: candidate.audio_sha256,
      gate: candidate.gate,
    })),
  }));
  return {
    planSha256: bootstrap.bundle.plan_sha256,
    anchorSelectionSha256: bootstrap.bundle.anchor_selection_sha256,
    candidateSetSha256: bootstrap.bundle.candidate_set_sha256,
    groups,
    dispose() {},
  };
}

export function localCandidateAudioUrl(candidateId: string): string {
  return `${API_ROOT}/audio/${encodeURIComponent(candidateId)}`;
}

export async function loadLocalListeningDraft<
  Draft extends RoleReviewDraft | BaselineDraft | QualityReviewDraft | BaselineAbDraft,
>(
  bootstrap: LocalListeningBootstrap,
): Promise<{ readonly revision: number; readonly draft: Draft } | null> {
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
  return result as unknown as { readonly revision: number; readonly draft: Draft };
}

export async function saveLocalListeningDraft(
  bootstrap: LocalListeningBootstrap,
  revision: number,
  draft: RoleReviewDraft | BaselineDraft | QualityReviewDraft | BaselineAbDraft,
): Promise<LocalListeningSaved> {
  return mutation(`${API_ROOT}/draft`, "PUT", bootstrap, { revision, draft });
}

export async function finalizeLocalListening(
  bootstrap: LocalListeningBootstrap,
  revision: number,
  decision: RoleReviewDecision | Record<string, unknown>,
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
    // Non-JSON responses are represented by the HTTP status text.
  }
  return new Error(`本地听测服务请求失败：${detail}`);
}
