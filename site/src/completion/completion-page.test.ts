import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { canonicalJson } from "@/lib/canonical-json";
import type { DirectoryFile, ObjectUrlFactory } from "@/lib/local-directory";
import { sha256Hex } from "@/lib/sha256";

import { candidateKeyboardShortcut } from "./candidate-shortcut";
import {
  CandidateGroupChangeNotice,
  CandidateQcBadge,
  CompletionJudgmentCriteria,
  MobilePersistentSummary,
  RolePassport,
  RoleReviewProgressPanel,
} from "./completion-page";
import { loadRoleReviewCatalog, ROLE_REVIEW_MODEL_IDS, validateRoleReviewBundle } from "./contract";
import { makeRoleReviewGroup } from "./storage.test";

describe("role-review-v1 contract", () => {
  it("canonical exact bundle、参照集合、audio SHAを検証してgroup hashを付与する", async () => {
    const fixture = await makeBundleFixture();
    const created: string[] = [];
    const revoked: string[] = [];
    const urls: ObjectUrlFactory = {
      create(file) {
        const url = `blob:${file.webkitRelativePath}`;
        created.push(url);
        return url;
      },
      revoke(url) {
        revoked.push(url);
      },
    };

    const catalog = await loadRoleReviewCatalog(fixture.files, urls);
    expect(catalog.phase).toBe("anchor");
    expect(catalog.groups).toHaveLength(1);
    expect(catalog.groups[0]!.group_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(catalog.groups[0]!.candidates).toHaveLength(4);
    expect(catalog.groups[0]!.candidates[0]!.qc).toEqual({
      mechanical: "pass",
      content: "not_checked",
      notes: [],
    });
    expect(Array.isArray(catalog.groups[0]!.candidates[0]!.qc.notes)).toBe(true);
    expect(created).toHaveLength(4);

    catalog.dispose();
    expect(revoked).toEqual(created);
  });

  it("Phase Aのeligible候補が3件でもanchor bundleとして受け入れる", async () => {
    const fixture = await makeBundleFixture(3);

    const bundle = validateRoleReviewBundle(fixture.bundle);

    expect(bundle.groups[0]!.candidates).toHaveLength(3);
  });

  it("top-up merge後に4件を超えるanchor候補も全件読み込む", async () => {
    const fixture = await makeBundleFixture(5);

    const catalog = await loadRoleReviewCatalog(fixture.files, noopUrls);

    expect(catalog.groups[0]!.candidates.map((candidate) => candidate.label)).toEqual([
      "A",
      "B",
      "C",
      "D",
      "E",
    ]);
    catalog.dispose();
  });

  it("anchor候補が3件未満なら明示拒否する", async () => {
    const fixture = await makeBundleFixture(2);

    expect(() => validateRoleReviewBundle(fixture.bundle)).toThrow("anchor phase で3件以上");
  });

  it("旧completion protocolを明示拒否する", () => {
    expect(() =>
      validateRoleReviewBundle({
        format_version: 1,
        protocol: "baseline-completion-decision-v1",
        candidate_set_sha256: "a".repeat(64),
        groups: [],
      }),
    ).toThrow("keyがexact contract");
  });

  it("余分なfieldを含むbundleを拒否する", async () => {
    const fixture = await makeBundleFixture();
    expect(() =>
      validateRoleReviewBundle({
        ...fixture.bundle,
        legacy_completion: true,
      }),
    ).toThrow("exact contract");
  });

  it("候補音声hash不一致を拒否する", async () => {
    const fixture = await makeBundleFixture();
    const files = fixture.files.map((file) =>
      file.webkitRelativePath.endsWith("candidate-1.opus")
        ? new MemoryDirectoryFile(
            file.webkitRelativePath,
            new TextEncoder().encode("tampered audio"),
          )
        : file,
    );
    await expect(loadRoleReviewCatalog(files, noopUrls)).rejects.toThrow(
      "候補音声 SHA-256 が一致しません",
    );
  });

  it("candidate_idsがcandidatesのexact順でなければ拒否する", async () => {
    const fixture = await makeBundleFixture();
    const group = fixture.bundle.groups[0]!;
    expect(() =>
      validateRoleReviewBundle({
        ...fixture.bundle,
        groups: [
          {
            ...group,
            candidate_ids: [...group.candidate_ids].reverse(),
          },
        ],
      }),
    ).toThrow("exactなid順");
  });

  it("旧content値reviewを受け入れない", async () => {
    const fixture = await makeBundleFixture();
    const group = fixture.bundle.groups[0]!;
    const first = group.candidates[0]!;

    expect(() =>
      validateRoleReviewBundle({
        ...fixture.bundle,
        groups: [
          {
            ...group,
            candidates: [
              {
                ...first,
                qc: { ...first.qc, content: "review" },
              },
              ...group.candidates.slice(1),
            ],
          },
        ],
      }),
    ).toThrow("not_checked / pass / review_required");
  });

  it("旧string notesを配列へ変換せず拒否する", async () => {
    const fixture = await makeBundleFixture();
    const group = fixture.bundle.groups[0]!;
    const first = group.candidates[0]!;

    expect(() =>
      validateRoleReviewBundle({
        ...fixture.bundle,
        groups: [
          {
            ...group,
            candidates: [
              {
                ...first,
                qc: { ...first.qc, notes: "legacy note" },
              },
              ...group.candidates.slice(1),
            ],
          },
        ],
      }),
    ).toThrow(".notes は配列が必要");
  });

  it("mechanical fail候補をrole review bundleには含めない", async () => {
    const fixture = await makeBundleFixture();
    const group = fixture.bundle.groups[0]!;
    const first = group.candidates[0]!;

    expect(() =>
      validateRoleReviewBundle({
        ...fixture.bundle,
        groups: [
          {
            ...group,
            candidates: [
              {
                ...first,
                qc: { ...first.qc, mechanical: "fail" },
              },
              ...group.candidates.slice(1),
            ],
          },
        ],
      }),
    ).toThrow("role review candidate で pass");
  });

  it("group.idのSHAとanchorの明示比較を必須にする", async () => {
    const fixture = await makeBundleFixture();
    const source = fixture.bundle.groups[0]!;

    expect(() =>
      validateRoleReviewBundle({
        ...fixture.bundle,
        groups: [{ ...source, id: "semantic-group-id" }],
      }),
    ).toThrow("小文字 SHA-256");
    expect(() =>
      validateRoleReviewBundle({
        ...fixture.bundle,
        groups: [
          {
            ...source,
            comparison_required: false,
            comparison_reasons: [],
          },
        ],
      }),
    ).toThrow("anchor phase で true");
  });

  it("現在の8モデルIDをexactに受け入れる", async () => {
    const fixture = await makeBundleFixture();
    const source = fixture.bundle.groups[0]!;

    for (const model of ROLE_REVIEW_MODEL_IDS) {
      expect(() =>
        validateRoleReviewBundle({
          ...fixture.bundle,
          groups: [{ ...source, model }],
        }),
      ).not.toThrow();
    }
  });

  it.each([
    "../qwen3-tts-12hz-1.7b",
    "qwen3-tts-12hz-1.7b/..",
    "qwen3-tts-12hz-1.7b/audio",
    "qwen3-tts-12hz-1.7b\\audio",
    "/qwen3-tts-12hz-1.7b",
    "C:\\qwen3-tts-12hz-1.7b",
    "..",
    "%2e%2e",
  ])("危険または未知のmodel IDを拒否する: %s", async (model) => {
    const fixture = await makeBundleFixture();
    const source = fixture.bundle.groups[0]!;

    expect(() =>
      validateRoleReviewBundle({
        ...fixture.bundle,
        groups: [{ ...source, model }],
      }),
    ).toThrow("exact set");
  });
});

describe("Role Continuity Timeline surface", () => {
  it("判断基準を漏れなく日語で明示する", () => {
    const markup = renderToStaticMarkup(createElement(CompletionJudgmentCriteria));
    for (const text of [
      "現在の判断基準",
      "内容 / 漏洩",
      "漢字読み",
      "厳密pitch accent",
      "Gender / Age",
      "Archetype",
      "Voice identity",
      "Delivery",
      "自然度 / 音質",
      "初期表示候補も人が確認",
    ]) {
      expect(markup).toContain(text);
    }
  });

  it("進捗総数を106/363へhard-codeせず現在bundleから描画する", () => {
    const markup = renderToStaticMarkup(
      createElement(RoleReviewProgressPanel, {
        current: 2,
        onExport() {},
        onReset() {},
        phase: "line",
        progress: { confirmed: 2, remaining: 5, total: 7 },
      }),
    );
    expect(markup).toContain("3 / 7");
    expect(markup).toContain("7件をexport");
    expect(markup).not.toContain("106");
    expect(markup).not.toContain("363");
  });

  it("desktop passportと判断panel、mobile恒显role/基準摘要を持つ", () => {
    const group = makeRoleReviewGroup();
    const markup = [
      renderToStaticMarkup(createElement(RolePassport, { group })),
      renderToStaticMarkup(createElement(CompletionJudgmentCriteria)),
      renderToStaticMarkup(createElement(MobilePersistentSummary, { group })),
    ].join("");

    expect(markup).toContain("data-role-passport");
    expect(markup).toContain("data-judgment-panel");
    expect(markup).toContain("data-mobile-role-summary");
    expect(markup).toContain("Gender 女性");
    expect(markup).toContain("Age 若年成人");
    expect(markup).toContain("Archetype 受付");
    expect(markup).toContain("厳密pitch accent");
  });

  it("成人男性refを男童/teenへ使うcoverageをexact/approximateで明示する", () => {
    const source = makeRoleReviewGroup();
    const group = {
      ...source,
      role: {
        ...source.role,
        name: "新聞少年",
        gender: "male" as const,
        age: "teen" as const,
      },
      coverage: {
        ...source.coverage,
        gender: "exact" as const,
        age: "approximate" as const,
      },
    };
    const markup = renderToStaticMarkup(createElement(RolePassport, { group }));
    expect(markup).toContain("成人男性reference: gender exact / age approximate");
  });

  it("candidate group変更をrole reopenと混同せず再評価対象として明示する", () => {
    const markup = renderToStaticMarkup(
      createElement(CandidateGroupChangeNotice, {
        reason: "candidate groupが変化したため、このgroupだけを再評価してください。",
      }),
    );

    expect(markup).toContain("data-candidate-group-change");
    expect(markup).toContain("候補group変更・要再評価");
    expect(markup).toContain("このgroupだけを再評価");
    expect(markup).not.toContain("REOPEN");
  });

  it("4件超の候補にも単一数字shortcutを割り当て、範囲外の表示は作らない", () => {
    expect(Array.from({ length: 5 }, (_, index) => candidateKeyboardShortcut(index))).toEqual([
      "1",
      "2",
      "3",
      "4",
      "5",
    ]);
    expect(candidateKeyboardShortcut(8)).toBe("9");
    expect(candidateKeyboardShortcut(9)).toBeNull();
  });

  it("content not_checkedを人审passと誤表示しない", () => {
    const markup = renderToStaticMarkup(
      createElement(CandidateQcBadge, {
        qc: {
          mechanical: "pass",
          content: "not_checked",
          notes: [],
        },
      }),
    );

    expect(markup).toContain("Content未確認");
    expect(markup).not.toContain("QC pass");
  });
});

async function makeBundleFixture(candidateCount = 4) {
  const audio = await Promise.all(
    Array.from({ length: candidateCount }, (_, index) => index + 1).map(async (attempt) => {
      const bytes = new TextEncoder().encode(`candidate audio ${attempt}`);
      return {
        path: `audio/candidate-${attempt}.opus`,
        bytes,
        sha256: await sha256Hex(bytes),
      };
    }),
  );
  const candidates = audio.map((item, index) => ({
    id: String.fromCharCode(97 + index).repeat(64),
    attempt: index + 1,
    seed: 1000 + index,
    audio_path: item.path,
    audio_sha256: item.sha256,
    qc: {
      mechanical: "pass" as const,
      content: "not_checked" as const,
      notes: [] as readonly string[],
    },
  }));
  const bundle = {
    format_version: 1 as const,
    protocol: "role-review-v1" as const,
    phase: "anchor" as const,
    plan_sha256: "1".repeat(64),
    candidate_set_sha256: "2".repeat(64),
    groups: [
      {
        id: "9".repeat(64),
        model: "qwen3-tts-12hz-1.7b",
        scenario: "scene-a",
        character: "character-a",
        line: null,
        role_epoch_sha256: "3".repeat(64),
        role: {
          name: "受付嬢",
          kind: "human" as const,
          gender: "female" as const,
          age: "young_adult" as const,
          archetype: "受付",
          voice: "滑舌明瞭で落ち着いた声",
          personality: "事務的だが親身",
        },
        conditioning: {
          method: "voice design anchor",
          summary: "完全な役柄情報からanchorを生成",
        },
        coverage: {
          gender: "exact" as const,
          age: "exact" as const,
          archetype: "exact" as const,
        },
        comparison_required: true,
        comparison_reasons: ["anchor候補のvoice identityを比較"] as readonly string[],
        candidate_ids: candidates.map((candidate) => candidate.id),
        provisional_candidate_id: candidates[0]!.id,
        candidates,
      },
    ],
  };
  const files: DirectoryFile[] = [
    new MemoryDirectoryFile(
      `fixture/${"role-review-v1.json"}`,
      new TextEncoder().encode(canonicalJson(bundle, "test role review bundle")),
    ),
    ...audio.map((item) => new MemoryDirectoryFile(`fixture/${item.path}`, item.bytes)),
  ];
  return { bundle, files };
}

class MemoryDirectoryFile implements DirectoryFile {
  readonly name: string;
  readonly webkitRelativePath: string;
  private readonly bytes: Uint8Array;

  constructor(webkitRelativePath: string, bytes: Uint8Array) {
    this.webkitRelativePath = webkitRelativePath;
    this.bytes = bytes;
    this.name = webkitRelativePath.split("/").at(-1)!;
  }

  async arrayBuffer(): Promise<ArrayBuffer> {
    return this.bytes.slice().buffer;
  }
}

const noopUrls: ObjectUrlFactory = {
  create(file) {
    return `blob:${file.webkitRelativePath}`;
  },
  revoke() {},
};
