import { describe, expect, it } from "vite-plus/test";

import { buildBaselineCurationJson } from "./export";
import {
  BASELINE_CURATION_STORAGE_KEY,
  createBaselineCurationDraft,
  readBaselineCurationDraft,
  setBaselineGroupDecision,
  updateBaselineCandidateRubric,
  writeBaselineCurationDraft,
  type BaselineCurationStorage,
} from "./storage";
import type { BaselineCatalog, BaselineCurationDraft } from "./types";

const TAKE_ID = "a".repeat(64);
const CANDIDATE_SET_SHA = "b".repeat(64);
const REFERENCE_SHA = "c".repeat(64);
const AUDIO_SHA = "d".repeat(64);
const GROUP_KEY = JSON.stringify(["dummy", "scene", "line", "dry"]);

describe("baseline curation storage / export", () => {
  it("draftをcandidate set SHAとbaseline reference SHAの両方に拘束する", () => {
    const catalog = makeCatalog();
    const storage = new MemoryStorage();
    const draft = createBaselineCurationDraft(catalog);

    expect(draft.groups).toHaveLength(1);
    expect(catalog.auditedNoCandidateCount).toBe(380);
    writeBaselineCurationDraft(storage, catalog, draft);
    expect(readBaselineCurationDraft(storage, catalog)).toEqual(draft);

    const raw = JSON.parse(
      storage.getItem(BASELINE_CURATION_STORAGE_KEY)!,
    ) as BaselineCurationDraft;
    storage.setItem(
      BASELINE_CURATION_STORAGE_KEY,
      JSON.stringify({ ...raw, candidate_set_sha256: "e".repeat(64) }),
    );
    expect(() => readBaselineCurationDraft(storage, catalog)).toThrow(
      "candidate-set と一致しません",
    );

    storage.setItem(
      BASELINE_CURATION_STORAGE_KEY,
      JSON.stringify({
        ...raw,
        baseline_reference_sha256: "e".repeat(64),
      }),
    );
    expect(() => readBaselineCurationDraft(storage, catalog)).toThrow(
      "baseline-reference と一致しません",
    );
  });

  it("selectedは完全rubricかつcontent_correct/adoptable trueだけ許可する", () => {
    const draft = createBaselineCurationDraft(makeCatalog());
    expect(() =>
      setBaselineGroupDecision(draft, GROUP_KEY, {
        type: "selected",
        take_id: TAKE_ID,
      }),
    ).toThrow("全項目");

    const rejected = updateBaselineCandidateRubric(draft, GROUP_KEY, TAKE_ID, {
      content_correct: false,
      intent_match: 5,
      character_naturalness: 5,
      adoptable: true,
    });
    expect(() =>
      setBaselineGroupDecision(rejected, GROUP_KEY, {
        type: "selected",
        take_id: TAKE_ID,
      }),
    ).toThrow("content_correct=true");
    expect(
      setBaselineGroupDecision(rejected, GROUP_KEY, { type: "skipped" }).groups[0]!.decision,
    ).toEqual({ type: "skipped" });

    const accepted = updateBaselineCandidateRubric(draft, GROUP_KEY, TAKE_ID, completeRubric());
    expect(
      setBaselineGroupDecision(accepted, GROUP_KEY, {
        type: "selected",
        take_id: TAKE_ID,
      }).groups[0]!.decision,
    ).toEqual({ type: "selected", take_id: TAKE_ID });
  });

  it("全curatable group完了後だけexact baseline-curation-v1をcanonical exportする", () => {
    const catalog = makeCatalog();
    const initial = createBaselineCurationDraft(catalog);
    expect(() => buildBaselineCurationJson(catalog, initial)).toThrow("全ての策展可能 group");

    const scored = updateBaselineCandidateRubric(initial, GROUP_KEY, TAKE_ID, completeRubric());
    const decided = setBaselineGroupDecision(scored, GROUP_KEY, {
      type: "selected",
      take_id: TAKE_ID,
    });
    const source = buildBaselineCurationJson(catalog, decided);

    expect(source.endsWith("\n")).toBe(false);
    expect(JSON.parse(source)).toEqual({
      baseline_reference_sha256: REFERENCE_SHA,
      candidate_set_sha256: CANDIDATE_SET_SHA,
      format_version: 1,
      groups: [
        {
          candidates: [
            {
              audio_sha256: AUDIO_SHA,
              path: `audio/takes/dummy/scene/line/dry/take-0001-${AUDIO_SHA}.opus`,
              rubric: completeRubric(),
              take_id: TAKE_ID,
            },
          ],
          decision: { take_id: TAKE_ID, type: "selected" },
          line: "line",
          model: "dummy",
          scenario: "scene",
          variant: "dry",
        },
      ],
      rubric_version: "baseline-curation-v1",
    });
    expect(Object.keys(JSON.parse(source) as object)).toEqual([
      "baseline_reference_sha256",
      "candidate_set_sha256",
      "format_version",
      "groups",
      "rubric_version",
    ]);
  });
});

class MemoryStorage implements BaselineCurationStorage {
  private readonly values = new Map<string, string>();

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

function makeCatalog(): BaselineCatalog {
  return {
    candidateSetSha256: CANDIDATE_SET_SHA,
    baselineReferenceSha256: REFERENCE_SHA,
    auditedNoCandidateCount: 380,
    groups: [
      {
        model: "dummy",
        scenario: "scene",
        line: "line",
        variant: "dry",
        scenarioTitle: "シーン",
        lineText: "台詞",
        delivery: "演技",
        candidate: {
          label: "A",
          takeId: TAKE_ID,
          audio: { key: "candidate", url: "blob:candidate" },
          gateContent: "pass",
        },
        candidateSha256: AUDIO_SHA,
        reference: {
          audio: { key: "reference", url: "blob:reference" },
          publicPath: "audio/dummy/scene/line/dry.opus",
          sha256: "f".repeat(64),
          comparison: "different",
        },
      },
    ],
    exportCandidatesByGroup: new Map([
      [
        GROUP_KEY,
        [
          {
            takeId: TAKE_ID,
            path: `audio/takes/dummy/scene/line/dry/take-0001-${AUDIO_SHA}.opus`,
            audioSha256: AUDIO_SHA,
          },
        ],
      ],
    ]),
    dispose() {},
  };
}

function completeRubric() {
  return {
    content_correct: true,
    intent_match: 4,
    character_naturalness: 3,
    adoptable: true,
  } as const;
}
