import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { CompletionRubricFields } from "./completion-rubric-fields";
import {
  createRoleReviewCatalog,
  ROLE_REVIEW_CANDIDATE_COUNT,
  ROLE_REVIEW_GROUP_COUNT,
  validateRoleReviewBundle,
} from "./contract";
import { EMPTY_ROLE_REVIEW_RUBRIC } from "./storage";
import { loadLocalListeningBootstrap } from "./local-listening-session";
import { createQualityReviewDraft, qualityReviewResultFromDraft } from "./quality-review-model";
import type { QualityReviewListeningBootstrap } from "./local-listening-session";
import type { RoleReviewBundle } from "./types";

describe("role-review-v2 contract", () => {
  it("106组×4候选、anchor文本与盲听URL组成唯一当前契约", async () => {
    const bundle = makeRoleReviewBundle();
    const catalog = await createRoleReviewCatalog(
      bundle,
      (candidateId) => `/__gaya-listening/audio/${candidateId}`,
    );

    expect(catalog.groups).toHaveLength(ROLE_REVIEW_GROUP_COUNT);
    expect(catalog.groups[0]!.anchor_text).toBe(
      "そらにはくもがうかび、とおくでかぜのおとがきこえます。",
    );
    expect(catalog.groups[0]!.candidates).toHaveLength(ROLE_REVIEW_CANDIDATE_COUNT);
    expect(catalog.groups[0]!.candidates.map((candidate) => candidate.label)).toEqual([
      "A",
      "B",
      "C",
      "D",
    ]);
    expect(catalog.groups[0]!.candidates[0]!.audio.url).toMatch(
      /^\/__gaya-listening\/audio\/[0-9a-f]{64}$/,
    );
  });

  it("旧v1、provisional默认选择与缺失anchor文本均明确拒绝", () => {
    const bundle = makeRoleReviewBundle();
    expect(() =>
      validateRoleReviewBundle({ ...bundle, format_version: 1, protocol: "role-review-v1" }),
    ).toThrow("format_version=2");
    expect(() =>
      validateRoleReviewBundle({
        ...bundle,
        groups: [
          { ...bundle.groups[0], provisional_candidate_id: bundle.groups[0]!.candidate_ids[0] },
          ...bundle.groups.slice(1),
        ],
      }),
    ).toThrow("exact contract");
    expect(() =>
      validateRoleReviewBundle({
        ...bundle,
        groups: [{ ...bundle.groups[0], anchor_text: "" }, ...bundle.groups.slice(1)],
      }),
    ).toThrow("anchor_text");
  });

  it("每个模型必须exact 53组且每组attempt为四个递增正整数", () => {
    const bundle = makeRoleReviewBundle();
    const unevenGroups = bundle.groups
      .map((group, index) =>
        index === 0
          ? {
              ...group,
              model: "qwen3-tts-12hz-1.7b" as const,
              scenario: "scene-99",
            }
          : group,
      )
      .sort((left, right) =>
        [left.model, left.scenario, left.character]
          .join("/")
          .localeCompare([right.model, right.scenario, right.character].join("/"), "en"),
      );
    expect(() => validateRoleReviewBundle({ ...bundle, groups: unevenGroups })).toThrow(
      "各model 53 group",
    );
    const topupGroups = bundle.groups.map((group) => ({
      ...group,
      candidates: group.candidates.map((candidate, candidateIndex) => ({
        ...candidate,
        attempt: candidateIndex + 5,
      })),
    }));
    expect(() => validateRoleReviewBundle({ ...bundle, groups: topupGroups })).not.toThrow();
    expect(() =>
      validateRoleReviewBundle({
        ...bundle,
        groups: topupGroups.map((group, index) =>
          index === 0
            ? {
                ...group,
                candidates: group.candidates.map((candidate, candidateIndex) =>
                  candidateIndex === 1 ? { ...candidate, attempt: 5 } : candidate,
                ),
              }
            : group,
        ),
      }),
    ).toThrow("一意な昇順正整数");
  });

  it("同一modelのrole座標重複とmodel間の座標差を拒否する", () => {
    const bundle = makeRoleReviewBundle();
    const duplicate = bundle.groups.map((group, index) =>
      index === 1
        ? {
            ...group,
            scenario: bundle.groups[0]!.scenario,
            character: bundle.groups[0]!.character,
          }
        : group,
    );
    expect(() => validateRoleReviewBundle({ ...bundle, groups: duplicate })).toThrow(
      "role座標が重複",
    );

    const mismatched = bundle.groups
      .map((group, index) => (index === 53 ? { ...group, scenario: "scene-99" } : group))
      .sort((left, right) =>
        [left.model, left.scenario, left.character]
          .join("/")
          .localeCompare([right.model, right.scenario, right.character].join("/"), "en"),
      );
    expect(() => validateRoleReviewBundle({ ...bundle, groups: mismatched })).toThrow(
      "同じ53 role座標集合",
    );
  });
});

describe("中文紧凑问题入口", () => {
  it("只展示听测人能理解的问题标签，不出现跨行或演技术语", () => {
    const markup = renderToStaticMarkup(
      createElement(CompletionRubricFields, {
        subjectLabel: "候选 A",
        value: { ...EMPTY_ROLE_REVIEW_RUBRIC, gender: "fail" },
        onChange() {},
      }),
    );
    for (const text of ["候选 A：已标记 1 个问题", "内容有缺漏", "提示词被读出", "角色感觉不符"]) {
      expect(markup).toContain(text);
    }
    expect(markup).not.toContain("Voice identity");
    expect(markup).not.toContain("Delivery");
    expect(markup).not.toContain("前後行");
  });

  it("未选择候选时问题入口保持可见但明确不可填写", () => {
    const markup = renderToStaticMarkup(
      createElement(CompletionRubricFields, {
        subjectLabel: null,
        disabled: true,
        value: EMPTY_ROLE_REVIEW_RUBRIC,
        onChange() {},
      }),
    );

    expect(markup).toContain("先选择一条，再标问题");
    expect(markup).toContain("disabled");
  });
});

describe("native listening workflow route", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("只接受显式角色定向复核workflow及其固定结果文件名", async () => {
    const bundle = makeQualityReviewBundle();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              bundle: {
                ...bundle,
              },
              finalized: false,
              format_version: 1,
              mutation_token: "d".repeat(64),
              output: {
                decision_file: "role-quality-review-result-v1.json",
                directory_name: "results",
                draft_file: "role-quality-review-draft-v1.json",
              },
              protocol: "gaya-listening-session-v1",
              revision: 0,
              workflow: "role-quality-review-v1",
            }),
            { headers: { "Content-Type": "application/json" } },
          ),
      ),
    );

    await expect(loadLocalListeningBootstrap()).resolves.toMatchObject({
      workflow: "role-quality-review-v1",
      output: { decision_file: "role-quality-review-result-v1.json" },
    });
  });

  it("定向复核草稿只绑定角色判断，不生成发布选择", () => {
    const bootstrap: QualityReviewListeningBootstrap = {
      bundle: makeQualityReviewBundle(),
      finalized: false,
      format_version: 1,
      mutation_token: "d".repeat(64),
      output: {
        decision_file: "role-quality-review-result-v1.json",
        directory_name: "results",
        draft_file: "role-quality-review-draft-v1.json",
      },
      protocol: "gaya-listening-session-v1",
      revision: 0,
      workflow: "role-quality-review-v1",
    };
    const draft = createQualityReviewDraft(bootstrap);
    const result = qualityReviewResultFromDraft({
      ...draft,
      groups: draft.groups.map((group) => ({ ...group, heard: true, result: "match" as const })),
    });

    expect(draft.groups).toHaveLength(1);
    expect(result).toMatchObject({ protocol: "role-quality-review-result-v1" });
    expect(result).not.toHaveProperty("current_index");
    expect(result).not.toHaveProperty("selected_candidate_id");
  });

  it("workflow省略や結果文件别名を拒绝する", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              bundle: {},
              finalized: false,
              format_version: 1,
              mutation_token: "d".repeat(64),
              output: {
                decision_file: "role-baseline-decision.json",
                directory_name: "results",
                draft_file: "role-baseline-draft-v1.json",
              },
              protocol: "gaya-listening-session-v1",
              revision: 0,
            }),
          ),
      ),
    );

    await expect(loadLocalListeningBootstrap()).rejects.toThrow("workflow");
  });
});

function makeQualityReviewBundle(): QualityReviewListeningBootstrap["bundle"] {
  return {
    format_version: 1,
    protocol: "role-quality-review-bundle-v1",
    plan_sha256: "a".repeat(64),
    decision_sha256: "b".repeat(64),
    manifest_sha256: "c".repeat(64),
    quality_signals_sha256: "e".repeat(64),
    groups: [
      {
        model: "model.1",
        scenario: "scene",
        line: "line",
        variant: "dry",
        scenario_title: "场景",
        text: "台词",
        delivery: "自然",
        role: {
          name: "受付嬢",
          kind: "human",
          gender: "female",
          age: "young_adult",
          archetype: "受付",
          voice: "明亮的女声",
          personality: "亲切",
        },
        take_id: "f".repeat(64),
        audio_path: `audio/${"f".repeat(64)}.opus`,
        audio_sha256: "0".repeat(64),
        expected_gender: "female",
        median_f0_hz: 140,
        signal: "gender_f0_below_expected",
      },
    ],
  };
}

export function makeRoleReviewBundle(): RoleReviewBundle {
  let candidateSequence = 1;
  const groups = Array.from({ length: ROLE_REVIEW_GROUP_COUNT }, (_, groupIndex) => {
    const model =
      groupIndex < ROLE_REVIEW_GROUP_COUNT / 2
        ? "irodori-tts-600m-v3-voicedesign"
        : "qwen3-tts-12hz-1.7b";
    const roleIndex = groupIndex % (ROLE_REVIEW_GROUP_COUNT / 2);
    const candidates = Array.from({ length: ROLE_REVIEW_CANDIDATE_COUNT }, (_, candidateIndex) => {
      const id = shaValue(candidateSequence++);
      return {
        id,
        attempt: candidateIndex + 1,
        seed: groupIndex * 10 + candidateIndex,
        audio_path: `audio/${id}.wav`,
        audio_sha256: shaValue(10_000 + groupIndex * 10 + candidateIndex),
        qc: { mechanical: "pass" as const, content: "not_checked" as const, notes: [] },
      };
    });
    return {
      id: shaValue(20_000 + groupIndex),
      model,
      scenario: `scene-${String(roleIndex).padStart(2, "0")}`,
      character: `role-${String(roleIndex).padStart(2, "0")}`,
      line: null,
      anchor_text:
        model === "irodori-tts-600m-v3-voicedesign"
          ? "そらにはくもがうかび、とおくでかぜのおとがきこえます。"
          : "さて、きょうもいちにちをはじめましょう。",
      role_epoch_sha256: shaValue(30_000 + groupIndex),
      role: {
        name: `受付 ${roleIndex}`,
        kind: "human" as const,
        gender: "female" as const,
        age: "young_adult" as const,
        archetype: "受付",
        voice: "落ち着いた明瞭な声",
        personality: "親切で冷静",
      },
      conditioning: {
        method: model.startsWith("irodori")
          ? "caption-anchor-then-reference"
          : "voice-design-anchor-then-clone",
        summary: "完整角色信息生成并固定角色声音。",
      },
      coverage: { gender: "exact" as const, age: "exact" as const, archetype: "exact" as const },
      comparison_required: true as const,
      comparison_reasons: ["role_match", "same_role_voice_identity", "anchor_audio_quality"],
      candidate_ids: candidates.map((candidate) => candidate.id),
      candidates,
    };
  });
  return {
    format_version: 2,
    protocol: "role-review-v2",
    phase: "anchor",
    plan_sha256: "a".repeat(64),
    candidate_set_sha256: "b".repeat(64),
    groups,
  };
}

function shaValue(value: number): string {
  return value.toString(16).padStart(64, "0");
}
