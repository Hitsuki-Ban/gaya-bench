import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { buildCurationJson, downloadCurationJson } from "./export";
import {
  CURATION_STORAGE_KEY,
  createCurationDraft,
  readCurationDraft,
  setGroupDecision,
  updateCandidateRubric,
  writeCurationDraft,
  type CurationStorage,
} from "./storage";
import { groupKey, type CurateCatalog, type CurateGroup, type Rubric } from "./types";

const COMPLETE: Rubric = {
  content_correct: true,
  intent_match: 5,
  character_naturalness: 4,
  adoptable: true,
};

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("curation storage", () => {
  it("stale/corrupt payload を silent reset せず拒否する", () => {
    const catalog = makeCatalog(["line-a"]);
    const storage = new MemoryStorage();
    storage.value = "{";
    expect(() => readCurationDraft(storage, catalog)).toThrow("明示的にリセット");
    expect(storage.value).toBe("{");

    storage.value = JSON.stringify({
      ...createCurationDraft(catalog),
      candidate_set_sha256: "e".repeat(64),
    });
    expect(() => readCurationDraft(storage, catalog)).toThrow("candidate-set と一致しません");
    expect(storage.value).not.toBeNull();
  });

  it("selected / skipped / 未策展の三態を区別し、選択制約を強制する", () => {
    const catalog = makeCatalog(["line-a", "line-b", "line-c"]);
    let draft = createCurationDraft(catalog);
    const firstKey = groupKey(draft.groups[0]!);
    const secondKey = groupKey(draft.groups[1]!);
    const firstTake = draft.groups[0]!.candidates[0]!.take_id;
    const secondTake = draft.groups[1]!.candidates[0]!.take_id;

    expect(() =>
      setGroupDecision(draft, firstKey, { type: "selected", take_id: firstTake }),
    ).toThrow("全項目");
    draft = updateCandidateRubric(draft, firstKey, firstTake, COMPLETE);
    draft = setGroupDecision(draft, firstKey, { type: "selected", take_id: firstTake });
    draft = updateCandidateRubric(draft, secondKey, secondTake, COMPLETE);
    draft = setGroupDecision(draft, secondKey, { type: "skipped" });

    expect(draft.groups.map((group) => group.decision?.type ?? null)).toEqual([
      "selected",
      "skipped",
      null,
    ]);
    expect(() =>
      updateCandidateRubric(draft, firstKey, firstTake, {
        ...COMPLETE,
        adoptable: false,
      }),
    ).toThrow("adoptable=true");
  });

  it("group/candidate 集合を catalog と exact に照合する", () => {
    const catalog = makeCatalog(["line-a"]);
    const storage = new MemoryStorage();
    const draft = createCurationDraft(catalog);
    const malformed = {
      ...draft,
      groups: [
        {
          ...draft.groups[0]!,
          candidates: [],
        },
      ],
    };
    storage.value = JSON.stringify(malformed);

    expect(() => readCurationDraft(storage, catalog)).toThrow("candidate 集合");
  });

  it("固定 versioned key へ書く", () => {
    const catalog = makeCatalog(["line-a"]);
    const storage = new MemoryStorage();
    const draft = createCurationDraft(catalog);

    writeCurationDraft(storage, catalog, draft);
    expect(storage.keys).toEqual([CURATION_STORAGE_KEY]);
  });
});

describe("curation export", () => {
  it("再帰 ASCII key 順、take_id 順、compact/no-time/no-newline の同一 bytes を返す", () => {
    const catalog = makeCatalog(["line-a"]);
    let draft = createCurationDraft(catalog);
    const key = groupKey(draft.groups[0]!);
    const takeId = draft.groups[0]!.candidates[0]!.take_id;
    draft = updateCandidateRubric(draft, key, takeId, COMPLETE);
    draft = setGroupDecision(draft, key, { type: "selected", take_id: takeId });

    const first = buildCurationJson(catalog, draft);
    const second = buildCurationJson(catalog, draft);
    expect(first).toBe(second);
    expect(first).toBe(
      `{"candidate_set_sha256":"${"d".repeat(64)}","format_version":1,"groups":[{"candidates":[{"audio_sha256":"${"b".repeat(64)}","path":"audio/takes/model/scene/line-a/dry/take-0001-${"b".repeat(64)}.opus","rubric":{"adoptable":true,"character_naturalness":4,"content_correct":true,"intent_match":5},"take_id":"${"a".repeat(64)}"}],"decision":{"take_id":"${"a".repeat(64)}","type":"selected"},"line":"line-a","model":"model","scenario":"scene","variant":"dry"}],"rubric_version":"take-curation-v1"}`,
    );
    expect(first).not.toContain("\n");
    expect(first).not.toContain("generated_at");
  });

  it("未策展 group を除外し、decision なしは export 不可", () => {
    const catalog = makeCatalog(["line-a", "line-b"]);
    let draft = createCurationDraft(catalog);
    expect(() => buildCurationJson(catalog, draft)).toThrow("1 件以上");

    const first = draft.groups[0]!;
    draft = updateCandidateRubric(draft, groupKey(first), first.candidates[0]!.take_id, COMPLETE);
    draft = setGroupDecision(draft, groupKey(first), { type: "skipped" });
    const exported = JSON.parse(buildCurationJson(catalog, draft)) as { groups: unknown[] };
    expect(exported.groups).toHaveLength(1);
  });

  it("anchorをDOMへ接続し、click後のtaskでBlob URLを破棄する", () => {
    vi.useFakeTimers();
    const events: string[] = [];
    const anchor = {
      href: "",
      download: "",
      click() {
        events.push("click");
      },
      remove() {
        events.push("remove");
      },
    };
    vi.stubGlobal("document", {
      createElement: vi.fn(() => anchor),
      body: {
        append: vi.fn(() => events.push("append")),
      },
    });
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:curation");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {
      events.push("revoke");
    });

    downloadCurationJson("{}");

    expect(anchor).toMatchObject({
      href: "blob:curation",
      download: "curation.json",
    });
    expect(events).toEqual(["append", "click", "remove"]);
    expect(revoke).not.toHaveBeenCalled();

    vi.runAllTimers();
    expect(events).toEqual(["append", "click", "remove", "revoke"]);
  });
});

class MemoryStorage implements CurationStorage {
  value: string | null = null;
  readonly keys: string[] = [];

  getItem(): string | null {
    return this.value;
  }

  setItem(key: string, value: string): void {
    this.keys.push(key);
    this.value = value;
  }

  removeItem(): void {
    this.value = null;
  }
}

function makeCatalog(lines: readonly string[]): CurateCatalog {
  const groups = lines.map(
    (line, index): CurateGroup => ({
      model: "model",
      scenario: "scene",
      line,
      variant: "dry",
      scenarioTitle: "Scene",
      lineText: line,
      delivery: "自然に",
      candidates: [
        {
          label: "A",
          takeId: String.fromCharCode(97 + index).repeat(64),
          audio: { key: `audio-${line}`, url: `blob:${line}` },
          gateContent: "pass",
        },
      ],
    }),
  );
  return {
    candidateSetSha256: "d".repeat(64),
    groups,
    exportCandidatesByGroup: new Map(
      groups.map((group) => [
        groupKey(group),
        [
          {
            takeId: group.candidates[0]!.takeId,
            path: `audio/takes/model/scene/${group.line}/dry/take-0001-${"b".repeat(64)}.opus`,
            audioSha256: "b".repeat(64),
          },
        ],
      ]),
    ),
    dispose() {},
  };
}
