import { describe, expect, it } from "vite-plus/test";

import { buildPilotDecisionJson } from "@/pilot/export";
import {
  PILOT_STORAGE_KEY,
  createPilotDecisionDraft,
  readPilotDecisionDraft,
  setPilotGroupDecision,
  updatePilotCandidateRubric,
  writePilotDecisionDraft,
  type PilotStorage,
} from "@/pilot/storage";
import type { PilotCatalog, PilotRubric } from "@/pilot/types";

const COMPLETE: PilotRubric = {
  content_correct: true,
  intent_match: 5,
  character_naturalness: 4,
  adoptable: false,
};

describe("pilot decision storage", () => {
  it("corrupt / stale payload を silent reset せず拒否する", () => {
    const catalog = makeCatalog(1);
    const storage = new MemoryStorage();
    storage.value = "{";
    expect(() => readPilotDecisionDraft(storage, catalog)).toThrow("明示的にリセット");
    expect(storage.value).toBe("{");

    storage.value = JSON.stringify({
      ...createPilotDecisionDraft(catalog),
      pilot_set_sha256: "e".repeat(64),
    });
    expect(() => readPilotDecisionDraft(storage, catalog)).toThrow(
      "現在の pilot-set と一致しません",
    );
    expect(storage.value).not.toBeNull();
  });

  it("group/candidate 集合と blind order の tamper を拒否する", () => {
    const catalog = makeCatalog(1);
    const storage = new MemoryStorage();
    const draft = createPilotDecisionDraft(catalog);
    storage.value = JSON.stringify({
      ...draft,
      groups: [
        {
          ...draft.groups[0]!,
          candidates: [...draft.groups[0]!.candidates].reverse(),
        },
      ],
    });

    expect(() => readPilotDecisionDraft(storage, catalog)).toThrow("集合と盲検順");
  });

  it("全 candidate rubric 完了後だけ selected / skipped を許可する", () => {
    const catalog = makeCatalog(1);
    let draft = createPilotDecisionDraft(catalog);
    const group = draft.groups[0]!;
    expect(() =>
      setPilotGroupDecision(draft, group.group_id, {
        type: "selected",
        candidate_id: group.candidates[0]!.candidate_id,
      }),
    ).toThrow("全項目");

    draft = completeGroup(draft, 0);
    draft = setPilotGroupDecision(draft, group.group_id, {
      type: "selected",
      candidate_id: group.candidates[0]!.candidate_id,
    });
    expect(draft.groups[0]!.decision).toEqual({
      type: "selected",
      candidate_id: group.candidates[0]!.candidate_id,
    });

    const storage = new MemoryStorage();
    writePilotDecisionDraft(storage, catalog, draft);
    expect(storage.keys).toEqual([PILOT_STORAGE_KEY]);
  });
});

describe("pilot decision export", () => {
  it("exact DecisionV1 を recursive ASCII key 順・no-time/no-newline で固定する", () => {
    const catalog = makeCatalog(1);
    let draft = completeGroup(createPilotDecisionDraft(catalog), 0);
    const group = draft.groups[0]!;
    draft = setPilotGroupDecision(draft, group.group_id, {
      type: "selected",
      candidate_id: group.candidates[0]!.candidate_id,
    });

    const first = buildPilotDecisionJson(catalog, draft);
    expect(first).toBe(buildPilotDecisionJson(catalog, draft));
    expect(first).toBe(
      `{"format_version":1,"groups":[{"candidates":[{"candidate_id":"${hex(3)}","rubric":{"adoptable":false,"character_naturalness":4,"content_correct":true,"intent_match":5}},{"candidate_id":"${hex(2)}","rubric":{"adoptable":false,"character_naturalness":4,"content_correct":true,"intent_match":5}},{"candidate_id":"${hex(1)}","rubric":{"adoptable":false,"character_naturalness":4,"content_correct":true,"intent_match":5}}],"decision":{"candidate_id":"${hex(3)}","type":"selected"},"group_id":"${hex(101)}"}],"pilot_set_sha256":"${"d".repeat(64)}","rubric_version":"n3-pilot-human-v1"}`,
    );
    expect(first).not.toContain("\n");
    expect(first).not.toContain("generated_at");
    expect(first).not.toContain("model");
    expect(first).not.toContain("take_index");
  });

  it("全 group の決定がない export を拒否する", () => {
    const catalog = makeCatalog(2);
    let draft = completeGroup(createPilotDecisionDraft(catalog), 0);
    draft = setPilotGroupDecision(draft, draft.groups[0]!.group_id, { type: "skipped" });

    expect(() => buildPilotDecisionJson(catalog, draft)).toThrow("全 group");
  });
});

class MemoryStorage implements PilotStorage {
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

function completeGroup(
  initial: ReturnType<typeof createPilotDecisionDraft>,
  groupIndex: number,
): ReturnType<typeof createPilotDecisionDraft> {
  let draft = initial;
  const group = draft.groups[groupIndex]!;
  for (const candidate of group.candidates) {
    draft = updatePilotCandidateRubric(draft, group.group_id, candidate.candidate_id, COMPLETE);
  }
  return draft;
}

function makeCatalog(groupCount: number): PilotCatalog {
  let candidateNumber = 0;
  return {
    pilotSetSha256: "d".repeat(64),
    groups: Array.from({ length: groupCount }, (_, groupIndex) => {
      const candidates = Array.from({ length: 3 }, (_, candidateIndex) => {
        candidateNumber += 1;
        return {
          candidateId: hex(candidateNumber),
          label: ["C", "B", "A"][candidateIndex] as "A" | "B" | "C",
          audio: {
            key: `pilot-${candidateNumber}`,
            url: `blob:pilot-${candidateNumber}`,
          },
        };
      }).reverse();
      return {
        groupId: hex(101 + groupIndex),
        presentation: {
          lineText: `台詞 ${groupIndex}`,
          reading: `せりふ ${groupIndex}`,
          delivery: `自然に ${groupIndex}`,
          candidates,
        },
      };
    }),
    dispose() {},
  };
}

function hex(value: number): string {
  return value.toString(16).padStart(64, "0");
}
