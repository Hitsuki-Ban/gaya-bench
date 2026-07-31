import { canonicalJson } from "@/lib/canonical-json";

import { assertBaselineDraft, isBaselineRubricComplete } from "./baseline-storage";
import {
  baselineGroupKey,
  compareBaselineGroups,
  type BaselineCatalog,
  type BaselineDraft,
} from "./baseline-types";

const ROOT_KEYS = [
  "format_version",
  "protocol",
  "plan_sha256",
  "anchor_selection_sha256",
  "candidate_set_sha256",
  "groups",
] as const;
const GROUP_KEYS = [
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
const AUTHORITY_KEYS = [
  "type",
  "policy_version",
  "reviewer",
  "minimum_eligible_candidates",
] as const;
const CANDIDATE_KEYS = ["take_id", "path", "audio_sha256", "gate", "rubric"] as const;
const GATE_KEYS = ["mechanical", "content", "policy_version"] as const;
const RUBRIC_KEYS = [
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
const DECISION_KEYS = ["type", "take_id"] as const;

export function buildBaselineDecisionJson(catalog: BaselineCatalog, draft: BaselineDraft): string {
  assertBaselineDraft(draft, catalog);
  if (draft.groups.some((group) => group.decision === null)) {
    throw new Error("role baseline decision のexportには全groupの明示選択が必要です。");
  }
  const document = {
    format_version: 1,
    protocol: "role-baseline-decision-v1",
    plan_sha256: catalog.planSha256,
    anchor_selection_sha256: catalog.anchorSelectionSha256,
    candidate_set_sha256: catalog.candidateSetSha256,
    groups: catalog.groups.map((group, groupIndex) => {
      const groupDraft = draft.groups[groupIndex]!;
      const rubricByTake = new Map(
        groupDraft.candidates.map((candidate) => [candidate.take_id, candidate.rubric]),
      );
      return {
        model: group.model,
        scenario: group.scenario,
        line: group.line,
        variant: group.variant,
        role_epoch_sha256: group.roleEpochSha256,
        group_sha256: group.groupSha256,
        authority: {
          type: "best_available",
          policy_version: "missing-slot-best-of-n-v1",
          reviewer: "owner",
          minimum_eligible_candidates: 3,
        },
        candidates: group.exportCandidates.map((candidate) => {
          const rubric = rubricByTake.get(candidate.takeId);
          if (!rubric || !isBaselineRubricComplete(rubric)) {
            throw new Error(`candidate rubricが未完了です: ${candidate.takeId}`);
          }
          return {
            take_id: candidate.takeId,
            path: candidate.path,
            audio_sha256: candidate.audioSha256,
            gate: candidate.gate,
            rubric,
          };
        }),
        decision: groupDraft.decision,
      };
    }),
  };
  validateBaselineDecision(document);
  return canonicalJson(document, "role baseline decision");
}

export function downloadBaselineDecisionJson(contents: string): void {
  const url = URL.createObjectURL(new Blob([contents], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "role-baseline-decision.json";
  document.body.append(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    globalThis.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

export function validateBaselineDecision(value: unknown): void {
  const root = exactObject(value, ROOT_KEYS, "role baseline decision");
  if (root.format_version !== 1) {
    throw new Error("role baseline decision.format_version は1が必要です。");
  }
  if (root.protocol !== "role-baseline-decision-v1") {
    throw new Error("旧decision protocolを拒否しました。role-baseline-decision-v1 が必要です。");
  }
  sha(root.plan_sha256, "role baseline decision.plan_sha256");
  sha(root.anchor_selection_sha256, "role baseline decision.anchor_selection_sha256");
  sha(root.candidate_set_sha256, "role baseline decision.candidate_set_sha256");
  if (!Array.isArray(root.groups) || root.groups.length === 0) {
    throw new Error("role baseline decision.groups は1件以上必要です。");
  }
  const seenGroups = new Set<string>();
  const seenTakeIds = new Set<string>();
  const seenPaths = new Set<string>();
  const groups = root.groups.map((rawGroup, groupIndex) => {
    const label = `role baseline decision.groups[${groupIndex}]`;
    const group = exactObject(rawGroup, GROUP_KEYS, label);
    for (const key of ["model", "scenario", "line", "variant"] as const) {
      pathSegment(group[key], `${label}.${key}`);
    }
    sha(group.role_epoch_sha256, `${label}.role_epoch_sha256`);
    sha(group.group_sha256, `${label}.group_sha256`);
    const groupKey = baselineGroupKey(
      group as {
        model: string;
        scenario: string;
        line: string;
        variant: string;
      },
    );
    if (seenGroups.has(groupKey)) {
      throw new Error(`role baseline decision groupが重複しています: ${groupKey}`);
    }
    seenGroups.add(groupKey);

    const authority = exactObject(group.authority, AUTHORITY_KEYS, `${label}.authority`);
    if (
      authority.type !== "best_available" ||
      authority.policy_version !== "missing-slot-best-of-n-v1" ||
      authority.reviewer !== "owner" ||
      authority.minimum_eligible_candidates !== 3
    ) {
      throw new Error(`${label}.authority がbest-available exact contractと一致しません。`);
    }
    if (!Array.isArray(group.candidates) || group.candidates.length < 3) {
      throw new Error(`${label}.candidates は3件以上必要です。`);
    }
    const localTakeIds = new Set<string>();
    const candidates = group.candidates.map((rawCandidate, candidateIndex) => {
      const candidateLabel = `${label}.candidates[${candidateIndex}]`;
      const candidate = exactObject(rawCandidate, CANDIDATE_KEYS, candidateLabel);
      const takeId = sha(candidate.take_id, `${candidateLabel}.take_id`);
      const path = nonEmptyText(candidate.path, `${candidateLabel}.path`);
      sha(candidate.audio_sha256, `${candidateLabel}.audio_sha256`);
      if (localTakeIds.has(takeId) || seenTakeIds.has(takeId)) {
        throw new Error(`role baseline take_idが重複しています: ${takeId}`);
      }
      if (seenPaths.has(path)) {
        throw new Error(`role baseline candidate pathが重複しています: ${path}`);
      }
      localTakeIds.add(takeId);
      seenTakeIds.add(takeId);
      seenPaths.add(path);
      validateGate(candidate.gate, `${candidateLabel}.gate`);
      validateRubric(candidate.rubric, `${candidateLabel}.rubric`);
      return { take_id: takeId };
    });
    const decision = exactObject(group.decision, DECISION_KEYS, `${label}.decision`);
    if (decision.type !== "selected") {
      throw new Error(`${label}.decision.type はselectedが必要です。`);
    }
    const selectedTakeId = sha(decision.take_id, `${label}.decision.take_id`);
    if (!localTakeIds.has(selectedTakeId)) {
      throw new Error(`${label}.decision.take_id がgroup candidatesにありません。`);
    }
    return {
      model: group.model as string,
      scenario: group.scenario as string,
      line: group.line as string,
      variant: group.variant as string,
      candidates,
    };
  });
  const sorted = [...groups].sort(compareBaselineGroups);
  if (groups.some((group, index) => baselineGroupKey(group) !== baselineGroupKey(sorted[index]!))) {
    throw new Error("role baseline decision.groups はcanonical順が必要です。");
  }
}

function validateGate(value: unknown, label: string): void {
  const gate = exactObject(value, GATE_KEYS, label);
  if (
    gate.mechanical !== "pass" ||
    (gate.content !== "pass" && gate.content !== "review_required") ||
    gate.policy_version !== "take-gates-v2"
  ) {
    throw new Error(`${label} がtake-gates-v2 exact contractと一致しません。`);
  }
}

function validateRubric(value: unknown, label: string): void {
  const rubric = exactObject(value, RUBRIC_KEYS, label);
  for (const key of [
    "content_correct",
    "prompt_leakage",
    "reading_correct",
    "adoptable",
  ] as const) {
    if (typeof rubric[key] !== "boolean") {
      throw new Error(`${label}.${key} はbooleanが必要です。`);
    }
  }
  for (const key of [
    "accent_naturalness",
    "role_match",
    "delivery_match",
    "audio_quality",
  ] as const) {
    if (!isScore(rubric[key])) {
      throw new Error(`${label}.${key} は1..5の整数が必要です。`);
    }
  }
  if (typeof rubric.notes !== "string") {
    throw new Error(`${label}.notes は文字列が必要です。`);
  }
}

function exactObject(
  value: unknown,
  expectedKeys: readonly string[],
  label: string,
): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} はobjectが必要です。`);
  }
  const object = value as Record<string, unknown>;
  const actual = Object.keys(object).sort(compareText);
  const expected = [...expectedKeys].sort(compareText);
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${label} のkeyがexact contractと一致しません: ${actual.join(",")}`);
  }
  return object;
}

function pathSegment(value: unknown, label: string): string {
  const text = nonEmptyText(value, label);
  if (text === "." || text === ".." || text.includes("/") || text.includes("\\")) {
    throw new Error(`${label} は安全なpath segmentが必要です。`);
  }
  return text;
}

function nonEmptyText(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} は空でない文字列が必要です。`);
  }
  return value;
}

function sha(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${label} は完全な小文字SHA-256が必要です。`);
  }
  return value;
}

function isScore(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 5;
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
