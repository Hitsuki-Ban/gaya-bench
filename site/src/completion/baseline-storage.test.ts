import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import {
  BaselineDesktopCriteriaSticky,
  BaselineJudgmentCriteria,
  BaselineMobileCriteriaSummary,
} from "./baseline-page";
import { validateBaselineSourceMap } from "./baseline-contract";
import { buildBaselineDecisionJson, validateBaselineDecision } from "./baseline-export";
import {
  createBaselineDraft,
  LEGACY_BASELINE_STORAGE_KEY,
  readBaselineDraft,
  writeBaselineDraft,
  type BaselineStorage,
} from "./baseline-storage";
import type {
  BaselineCatalog,
  BaselineDraft,
  BaselineGroup,
  BaselineRubric,
} from "./baseline-types";

const COMPLETE_RUBRIC: BaselineRubric = {
  content_correct: true,
  prompt_leakage: false,
  reading_correct: true,
  accent_naturalness: 4,
  role_match: 4,
  delivery_match: 4,
  audio_quality: 4,
  adoptable: true,
  notes: "checked",
};

describe("Phase B group-local storage", () => {
  it("candidate-set aggregate変更では同一groupだけ保存し、変更groupの旧状態を復活させない", () => {
    const storage = new MemoryStorage();
    const initialCatalog = makeCatalog("a", [makeGroup(0, "1"), makeGroup(1, "2")]);
    const initialDraft = completeDraft(createBaselineDraft(initialCatalog));
    writeBaselineDraft(storage, initialCatalog, initialDraft);

    const changedCatalog = makeCatalog("b", [makeGroup(0, "1"), makeGroup(1, "3")]);
    const changedDraft = readBaselineDraft(storage, changedCatalog);
    expect(changedDraft.groups[0]!.decision).toEqual(initialDraft.groups[0]!.decision);
    expect(changedDraft.groups[0]!.candidates[0]!.rubric.notes).toBe("checked");
    expect(changedDraft.groups[1]!.decision).toBeNull();
    expect(changedDraft.groups[1]!.revalidation_reason).toContain("再評価");

    const reverted = readBaselineDraft(storage, initialCatalog);
    expect(reverted.groups[1]!.decision).toBeNull();
    expect(reverted.groups[1]!.candidates[0]!.rubric.content_correct).toBeNull();
  });

  it("plan・anchor・role epochの変更を該当groupだけ再評価にする", () => {
    const storage = new MemoryStorage();
    const catalog = makeCatalog("a", [makeGroup(0, "1"), makeGroup(1, "2")]);
    writeBaselineDraft(storage, catalog, completeDraft(createBaselineDraft(catalog)));

    const epochChanged = makeCatalog("a", [
      makeGroup(0, "1"),
      { ...makeGroup(1, "2"), roleEpochSha256: "9".repeat(64) },
    ]);
    const restored = readBaselineDraft(storage, epochChanged);
    expect(restored.groups[0]!.decision).not.toBeNull();
    expect(restored.groups[1]!.decision).toBeNull();

    const planChanged = { ...epochChanged, planSha256: "8".repeat(64) };
    expect(readBaselineDraft(storage, planChanged).groups.every((group) => !group.decision)).toBe(
      true,
    );
  });

  it("旧global storageとrecord内容に一致しないalias keyを明示拒否する", () => {
    const catalog = makeCatalog("a", [makeGroup(0, "1")]);
    const legacy = new MemoryStorage();
    legacy.setItem(LEGACY_BASELINE_STORAGE_KEY, "{}");
    expect(() => readBaselineDraft(legacy, catalog)).toThrow("旧baseline-completion");

    const aliased = new MemoryStorage();
    const draft = createBaselineDraft(catalog);
    aliased.setItem("gaya-bench:role-baseline:v1:group:alias", JSON.stringify(draft.groups[0]));
    expect(() => readBaselineDraft(aliased, catalog)).toThrow("storage keyがrecord bindingと一致");
  });
});

describe("role-baseline-decision-v1 export / UI", () => {
  it("旧listening source-map protocolを変換せず拒否する", () => {
    expect(() =>
      validateBaselineSourceMap({
        format_version: 1,
        protocol: "baseline-completion-decision-v1",
        plan_sha256: "a".repeat(64),
        anchor_selection_sha256: "b".repeat(64),
        candidate_set_sha256: "c".repeat(64),
        groups: [],
      }),
    ).toThrow("phase-b-source-map-v1");
  });

  it("source-map groupのminimum=1を受理し、未知fieldをexact contractで拒否する", () => {
    const sourceMap = makeSourceMap();
    expect(validateBaselineSourceMap(sourceMap).groups[0]!.minimum_eligible_candidates).toBe(1);

    const withUnknownGroupField = structuredClone(sourceMap);
    (withUnknownGroupField.groups[0] as Record<string, unknown>).unexpected = true;
    expect(() => validateBaselineSourceMap(withUnknownGroupField)).toThrow("exact contract");
  });

  it("新protocolへ全rubricとrole epochをexact exportし旧protocolを拒否する", () => {
    const catalog = makeCatalog("a", [makeGroup(0, "1")]);
    const decision = JSON.parse(
      buildBaselineDecisionJson(catalog, completeDraft(createBaselineDraft(catalog))),
    ) as Record<string, unknown>;
    expect(decision.protocol).toBe("role-baseline-decision-v1");
    expect(decision.anchor_selection_sha256).toBe(catalog.anchorSelectionSha256);
    const groups = decision.groups as Array<Record<string, unknown>>;
    expect(groups[0]).toMatchObject({
      role_epoch_sha256: catalog.groups[0]!.roleEpochSha256,
      group_sha256: catalog.groups[0]!.groupSha256,
      authority: {
        type: "best_available",
        minimum_eligible_candidates: 3,
      },
    });
    expect((groups[0]!.candidates as Array<Record<string, unknown>>)[0]).toMatchObject({
      rubric: {
        content_correct: true,
        prompt_leakage: false,
        role_match: 4,
      },
    });
    const missingGroupHash = structuredClone(groups[0]!);
    delete missingGroupHash.group_sha256;
    expect(() =>
      validateBaselineDecision({
        ...decision,
        groups: [missingGroupHash],
      }),
    ).toThrow("exact contract");
    expect(() =>
      validateBaselineDecision({
        ...decision,
        protocol: "baseline-completion-decision-v1",
      }),
    ).toThrow("旧decision protocol");
  });

  it("minimum=1でもOwnerの明示選択を要求し、group閾値をauthorityへ出力する", () => {
    const catalog = makeCatalog("a", [makeGroup(0, "1", 1, 1)]);
    const empty = createBaselineDraft(catalog);
    expect(empty.groups[0]!.decision).toBeNull();
    expect(() => buildBaselineDecisionJson(catalog, empty)).toThrow("明示選択");

    const decision = JSON.parse(buildBaselineDecisionJson(catalog, completeDraft(empty))) as {
      groups: Array<Record<string, unknown>>;
    };
    expect(decision.groups[0]).toMatchObject({
      authority: {
        type: "best_available",
        reviewer: "owner",
        minimum_eligible_candidates: 1,
      },
    });
    expect(decision.groups[0]!.candidates).toHaveLength(1);

    const belowDeclaredMinimum = structuredClone(decision);
    (
      belowDeclaredMinimum.groups[0]!.authority as Record<string, unknown>
    ).minimum_eligible_candidates = 2;
    expect(() => validateBaselineDecision(belowDeclaredMinimum)).toThrow(
      "minimum_eligible_candidates以上",
    );
  });

  it("desktop/mobile双方で判断基準を常時明示する", () => {
    const group = makeGroup(0, "1");
    const markup = [
      renderToStaticMarkup(createElement(BaselineJudgmentCriteria)),
      renderToStaticMarkup(createElement(BaselineMobileCriteriaSummary, { group })),
    ].join("");
    for (const text of [
      "data-baseline-judgment-criteria",
      "data-baseline-mobile-criteria",
      "内容 / 漏洩 / 読み / 音調",
      "Gender / Age / Archetype / Voice identity",
      "Delivery / Naturalness",
      "Baseline採用可否 (adoptable)",
      "採用可否",
      "初期候補も全件",
    ]) {
      expect(markup).toContain(text);
    }
  });

  it("scroll後もdesktop/mobile stickyを共通header offsetの下へ固定する", () => {
    const desktop = renderToStaticMarkup(createElement(BaselineDesktopCriteriaSticky));
    const mobile = renderToStaticMarkup(
      createElement(BaselineMobileCriteriaSummary, { group: makeGroup(0, "1") }),
    );

    expect(desktop).toContain("data-baseline-desktop-criteria-sticky");
    expect(desktop).toContain("top-[calc(var(--gaya-sticky-header-offset)+1rem)]");
    expect(desktop).toContain("z-10");
    expect(mobile).toContain("top-(--gaya-sticky-header-offset)");
    expect(mobile).toContain("z-10");
    expect(mobile).not.toContain("top-0");
  });
});

function completeDraft(draft: BaselineDraft): BaselineDraft {
  return {
    ...draft,
    groups: draft.groups.map((group) => ({
      ...group,
      candidates: group.candidates.map((candidate) => ({
        ...candidate,
        rubric: COMPLETE_RUBRIC,
      })),
      decision: {
        type: "selected",
        take_id: group.candidates[0]!.take_id,
      },
    })),
  };
}

function makeCatalog(candidateSet: string, groups: readonly BaselineGroup[]): BaselineCatalog {
  return {
    planSha256: "a".repeat(64),
    anchorSelectionSha256: "b".repeat(64),
    candidateSetSha256: candidateSet.repeat(64),
    groups,
    dispose() {},
  };
}

function makeGroup(
  index: number,
  version: string,
  minimumEligibleCandidates = 3,
  candidateCount = 3,
): BaselineGroup {
  const candidates = Array.from({ length: candidateCount }, (_, offset) => {
    const candidateIndex = offset + 1;
    const takeId = `${index + 1}${candidateIndex}${version}`.padEnd(64, "0");
    const audioSha = `${candidateIndex}${index + 1}${version}`.padEnd(64, "0");
    return {
      takeId,
      path:
        `audio/takes/dummy/scene-${index}/line-${index}/dry/` +
        `take-${String(candidateIndex).padStart(4, "0")}-${audioSha}.opus`,
      audioSha256: audioSha,
      gate: {
        mechanical: "pass" as const,
        content: "pass" as const,
        policy_version: "take-gates-v2" as const,
      },
    };
  });
  return {
    model: "dummy",
    scenario: `scene-${index}`,
    line: `line-${index}`,
    variant: "dry",
    scenarioTitle: `Scene ${index}`,
    lineText: `Line ${index}`,
    delivery: "natural",
    roleEpochSha256: `${index + 3}${version}`.padEnd(64, "0"),
    sourceRunId: `run-${version}`,
    minimumEligibleCandidates,
    groupSha256: `${index + 5}${version}`.padEnd(64, "0"),
    candidates: candidates.map((candidate, candidateIndex) => ({
      label: String.fromCharCode(65 + candidateIndex),
      takeId: candidate.takeId,
      audio: { key: candidate.takeId, url: `blob:${candidate.takeId}` },
      gateContent: candidate.gate.content,
    })),
    exportCandidates: candidates,
  };
}

function makeSourceMap(): {
  format_version: number;
  protocol: string;
  plan_sha256: string;
  anchor_selection_sha256: string;
  candidate_set_sha256: string;
  groups: Array<Record<string, unknown>>;
} {
  return {
    format_version: 1,
    protocol: "phase-b-source-map-v1",
    plan_sha256: "a".repeat(64),
    anchor_selection_sha256: "b".repeat(64),
    candidate_set_sha256: "c".repeat(64),
    groups: Array.from({ length: 597 }, (_, index) => ({
      model: "dummy",
      scenario: `scene-${String(index).padStart(3, "0")}`,
      line: `line-${String(index).padStart(3, "0")}`,
      variant: "dry",
      role_epoch_sha256: "d".repeat(64),
      source_run_id: `run-${index}`,
      minimum_eligible_candidates: index === 0 ? 1 : 3,
    })),
  };
}

class MemoryStorage implements BaselineStorage {
  private readonly values = new Map<string, string>();

  get length(): number {
    return this.values.size;
  }

  key(index: number): string | null {
    return [...this.values.keys()][index] ?? null;
  }

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}
