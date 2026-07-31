import { describe, expect, it } from "vite-plus/test";

import {
  applyRoleReviewPlaybackCompletion,
  confirmRoleReviewGroup,
  createRoleReviewDraft,
  markRoleReviewCandidateHeard,
  readRoleReviewDraft,
  recoverRoleReviewDraft,
  reopenRole,
  requiredHeardCount,
  ROLE_REVIEW_STORAGE_PREFIX,
  RoleReopenRequiredError,
  roleReviewStorageKey,
  selectRoleReviewCandidate,
  updateRoleReviewRubric,
  writeRoleReviewDraft,
  type RoleReviewStorage,
} from "./storage";
import type {
  RoleReviewCandidatePresentation,
  RoleReviewCatalog,
  RoleReviewDraft,
  RoleReviewGroup,
  RoleReviewRubric,
} from "./types";

describe("role review storage", () => {
  it("comparison_requiredでは異なる2候補をheardにするまで確認を拒否する", () => {
    const catalog = makeRoleReviewCatalog({
      groups: [makeRoleReviewGroup({ comparisonRequired: true })],
    });
    const group = catalog.groups[0]!;
    let draft = createRoleReviewDraft(catalog);
    draft = updateRoleReviewRubric(catalog, draft, group.id, completeRoleReviewRubric());
    draft = markRoleReviewCandidateHeard(catalog, draft, group.id, group.provisional_candidate_id);

    expect(requiredHeardCount(group, draft.groups[0]!)).toBe(2);
    expect(() => confirmRoleReviewGroup(catalog, draft, group.id)).toThrow("2件以上");

    draft = markRoleReviewCandidateHeard(catalog, draft, group.id, group.candidate_ids[1]!);
    expect(confirmRoleReviewGroup(catalog, draft, group.id).groups[0]!.confirmed).toBe(true);
  });

  it("provisionalを拒否して改選した場合も2候補heardを要求する", () => {
    const catalog = makeRoleReviewCatalog();
    const group = catalog.groups[0]!;
    const changed = group.candidate_ids[1]!;
    let draft = createRoleReviewDraft(catalog);
    draft = updateRoleReviewRubric(catalog, draft, group.id, completeRoleReviewRubric());
    draft = selectRoleReviewCandidate(catalog, draft, group.id, changed);
    draft = markRoleReviewCandidateHeard(catalog, draft, group.id, changed);

    expect(requiredHeardCount(group, draft.groups[0]!)).toBe(2);
    expect(() => confirmRoleReviewGroup(catalog, draft, group.id)).toThrow("2件以上");
  });

  it("phase/model/scenario/character/epoch/group hashで保存を分離する", () => {
    const catalog = makeRoleReviewCatalog();
    const storage = new MemoryRoleReviewStorage();
    writeRoleReviewDraft(storage, catalog, createRoleReviewDraft(catalog));

    const key = roleReviewStorageKey(catalog.groups[0]!);
    expect(key).toContain(
      `${ROLE_REVIEW_STORAGE_PREFIX}line:model-a:scene-a:character-a:${"e".repeat(64)}:${"f".repeat(64)}`,
    );
    expect(storage.getItem(key)).not.toBeNull();
    expect(storage.getItem("gaya-bench:baseline-completion:v1")).toBeNull();
  });

  it("epoch変更時は同じmodel+characterだけを全phaseから失効しreopenを記録する", () => {
    const roleA = makeRoleReviewGroup({
      id: "1".repeat(64),
      character: "character-a",
      candidateCharacters: ["1", "2", "3", "4"],
      epochCharacter: "a",
      groupCharacter: "b",
    });
    const roleB = makeRoleReviewGroup({
      id: "2".repeat(64),
      character: "character-b",
      candidateCharacters: ["5", "6", "7", "8"],
      epochCharacter: "c",
      groupCharacter: "d",
    });
    const oldCatalog = makeRoleReviewCatalog({ groups: [roleA, roleB] });
    const storage = new MemoryRoleReviewStorage();
    const confirmed = confirmAllGroups(oldCatalog);
    writeRoleReviewDraft(storage, oldCatalog, confirmed);

    const oldAnchorA = {
      ...confirmed.groups[0]!,
      id: "9".repeat(64),
      phase: "anchor" as const,
      line: null,
      group_sha256: "9".repeat(64),
    };
    storage.setItem(roleReviewStorageKey(oldAnchorA), JSON.stringify(oldAnchorA));

    const nextRoleA = {
      ...roleA,
      role_epoch_sha256: "0".repeat(64),
      group_sha256: "1".repeat(64),
    };
    const nextCatalog = makeRoleReviewCatalog({ groups: [nextRoleA, roleB] });
    const restored = readRoleReviewDraft(storage, nextCatalog);

    expect(restored.groups[0]!.confirmed).toBe(false);
    expect(restored.groups[1]!.confirmed).toBe(true);
    expect(restored.role_reopen_requests).toEqual([
      {
        model: "model-a",
        character: "character-a",
        role_epoch_sha256: "0".repeat(64),
        reason: "読み込んだbundleでrole epochが変化したため、当該役柄だけを再開しました。",
      },
    ]);
    const storedRoleA = [...storage.keys()].filter(
      (key) => key.includes(":character-a:") && key.startsWith(ROLE_REVIEW_STORAGE_PREFIX),
    );
    expect(storedRoleA).toEqual([roleReviewStorageKey(nextRoleA)]);
    expect(storage.getItem(roleReviewStorageKey(roleB))).not.toBeNull();

    const afterReload = readRoleReviewDraft(storage, nextCatalog);
    expect(afterReload.role_reopen_requests).toEqual(restored.role_reopen_requests);
  });

  it("後続操作のidentity不一致は自動補正せず明示reopenを要求する", () => {
    const catalog = makeRoleReviewCatalog();
    const storage = new MemoryRoleReviewStorage();
    const draft = createRoleReviewDraft(catalog);
    const inconsistent: RoleReviewDraft = {
      ...draft,
      groups: [
        {
          ...draft.groups[0]!,
          role_epoch_sha256: "0".repeat(64),
          confirmed: true,
        },
      ],
    };

    expect(() =>
      selectRoleReviewCandidate(
        catalog,
        inconsistent,
        catalog.groups[0]!.id,
        catalog.groups[0]!.candidate_ids[1]!,
      ),
    ).toThrow(RoleReopenRequiredError);

    const reopened = reopenRole(
      storage,
      catalog,
      inconsistent,
      "model-a",
      "character-a",
      "identity不一致を確認したため",
    );
    expect(reopened.groups[0]).toMatchObject({
      role_epoch_sha256: "e".repeat(64),
      confirmed: false,
      selected_candidate_id: catalog.groups[0]!.provisional_candidate_id,
    });
    expect(reopened.role_reopen_requests[0]?.reason).toBe("identity不一致を確認したため");
  });

  it("aggregateの別groupだけが変化しても未変更groupの確認を保持する", () => {
    const roleA = makeRoleReviewGroup({
      id: "1".repeat(64),
      character: "character-a",
      groupCharacter: "a",
      candidateCharacters: ["1", "2", "3", "4"],
    });
    const roleB = makeRoleReviewGroup({
      id: "2".repeat(64),
      character: "character-b",
      groupCharacter: "b",
      candidateCharacters: ["5", "6", "7", "8"],
    });
    const catalog = makeRoleReviewCatalog({ groups: [roleA, roleB] });
    const storage = new MemoryRoleReviewStorage();
    writeRoleReviewDraft(storage, catalog, confirmAllGroups(catalog));
    const changedRoleB = { ...roleB, group_sha256: "c".repeat(64) };
    const nextCatalog = {
      ...makeRoleReviewCatalog({ groups: [roleA, changedRoleB] }),
      candidateSetSha256: "9".repeat(64),
    };

    const restored = readRoleReviewDraft(storage, nextCatalog);
    expect(restored.groups[0]!.confirmed).toBe(true);
    expect(restored.groups[1]).toMatchObject({
      confirmed: false,
      candidate_group_change_reason:
        "candidate groupが変化したため、このgroupだけを再評価してください。",
      role_reopen_reason: null,
    });
    expect(restored.role_reopen_requests).toEqual([]);
    expect(storage.getItem(roleReviewStorageKey(roleB))).toBeNull();
    expect(storage.getItem(roleReviewStorageKey(changedRoleB))).not.toBeNull();

    let reevaluated = updateRoleReviewRubric(
      nextCatalog,
      restored,
      changedRoleB.id,
      completeRoleReviewRubric(),
    );
    reevaluated = markRoleReviewCandidateHeard(
      nextCatalog,
      reevaluated,
      changedRoleB.id,
      changedRoleB.provisional_candidate_id,
    );
    reevaluated = confirmRoleReviewGroup(nextCatalog, reevaluated, changedRoleB.id);
    expect(reevaluated.groups[1]).toMatchObject({
      confirmed: true,
      candidate_group_change_reason: null,
    });
  });

  it("現在groupの変更だけを失効し旧hashへ戻しても古い確認を復活させない", () => {
    const original = makeRoleReviewGroup({ groupCharacter: "a" });
    const catalog = makeRoleReviewCatalog({ groups: [original] });
    const storage = new MemoryRoleReviewStorage();
    writeRoleReviewDraft(storage, catalog, confirmAllGroups(catalog));

    const changed = { ...original, group_sha256: "b".repeat(64) };
    const changedCatalog = {
      ...makeRoleReviewCatalog({ groups: [changed] }),
      candidateSetSha256: "3".repeat(64),
    };
    const changedDraft = readRoleReviewDraft(storage, changedCatalog);
    expect(changedDraft.groups[0]).toMatchObject({
      confirmed: false,
      candidate_group_change_reason:
        "candidate groupが変化したため、このgroupだけを再評価してください。",
    });
    expect(storage.getItem(roleReviewStorageKey(original))).toBeNull();

    writeRoleReviewDraft(storage, changedCatalog, confirmAllGroups(changedCatalog));
    const returnedCatalog = {
      ...catalog,
      candidateSetSha256: "4".repeat(64),
    };
    const returned = readRoleReviewDraft(storage, returnedCatalog);
    expect(returned.groups[0]).toMatchObject({
      confirmed: false,
      candidate_group_change_reason:
        "candidate groupが変化したため、このgroupだけを再評価してください。",
    });
    expect(storage.getItem(roleReviewStorageKey(changed))).toBeNull();
  });

  it("旧group record schemaを移行せず拒否する", () => {
    const catalog = makeRoleReviewCatalog();
    const storage = new MemoryRoleReviewStorage();
    const draft = createRoleReviewDraft(catalog);
    const key = roleReviewStorageKey(draft.groups[0]!);
    storage.setItem(
      key,
      JSON.stringify({
        ...draft.groups[0],
        candidate_set_sha256: catalog.candidateSetSha256,
      }),
    );

    expect(() => readRoleReviewDraft(storage, catalog)).toThrow("exact contract");
  });

  it("heardはendedだけを記録しerrorと即時stopでは解锁しない", () => {
    const catalog = makeRoleReviewCatalog();
    const draft = createRoleReviewDraft(catalog);
    const clipKey = catalog.groups[0]!.candidates[0]!.audio.key;
    const stopped = applyRoleReviewPlaybackCompletion(catalog, draft, {
      sessionId: 1,
      clipKey,
      termination: "stopped",
    });
    const failed = applyRoleReviewPlaybackCompletion(catalog, stopped, {
      sessionId: 2,
      clipKey,
      termination: "error",
    });
    expect(failed.groups[0]!.heard_candidate_ids).toEqual([]);

    const ended = applyRoleReviewPlaybackCompletion(catalog, failed, {
      sessionId: 3,
      clipKey,
      termination: "ended",
    });
    expect(ended.groups[0]!.heard_candidate_ids).toEqual([catalog.groups[0]!.candidate_ids[0]]);
  });

  it("load期identity错误可显式恢复该role并保留其他group", () => {
    const roleA = makeRoleReviewGroup({
      id: "1".repeat(64),
      character: "character-a",
      candidateCharacters: ["1", "2", "3", "4"],
    });
    const roleB = makeRoleReviewGroup({
      id: "2".repeat(64),
      character: "character-b",
      candidateCharacters: ["5", "6", "7", "8"],
    });
    const catalog = makeRoleReviewCatalog({ groups: [roleA, roleB] });
    const storage = new MemoryRoleReviewStorage();
    writeRoleReviewDraft(storage, catalog, confirmAllGroups(catalog));

    const recovered = recoverRoleReviewDraft(
      storage,
      catalog,
      roleA.model,
      roleA.character,
      "load期identity不一致を明示reopen",
    );
    expect(recovered.groups[0]!.confirmed).toBe(false);
    expect(recovered.groups[1]!.confirmed).toBe(true);
    expect(recovered.role_reopen_requests[0]?.reason).toBe("load期identity不一致を明示reopen");
  });
});

export class MemoryRoleReviewStorage implements RoleReviewStorage {
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

  keys(): IterableIterator<string> {
    return this.values.keys();
  }
}

export function makeRoleReviewCatalog({
  phase = "line",
  groups = [makeRoleReviewGroup()],
}: {
  phase?: "anchor" | "line";
  groups?: readonly RoleReviewGroup[];
} = {}): RoleReviewCatalog {
  return {
    phase,
    planSha256: "1".repeat(64),
    candidateSetSha256: "2".repeat(64),
    groups,
    dispose() {},
  };
}

export function makeRoleReviewGroup({
  id = "9".repeat(64),
  model = "model-a",
  scenario = "scene-a",
  character = "character-a",
  line = {
    id: "line-a",
    text: "受付へようこそ",
    delivery: "落ち着いて丁寧に話す",
  },
  comparisonRequired = false,
  candidateCharacters = ["a", "b", "c", "d"],
  epochCharacter = "e",
  groupCharacter = "f",
}: {
  id?: string;
  model?: string;
  scenario?: string;
  character?: string;
  line?: RoleReviewGroup["line"];
  comparisonRequired?: boolean;
  candidateCharacters?: readonly string[];
  epochCharacter?: string;
  groupCharacter?: string;
} = {}): RoleReviewGroup {
  const candidates = candidateCharacters.map(
    (characterValue, index): RoleReviewCandidatePresentation => ({
      id: characterValue.repeat(64),
      attempt: index + 1,
      seed: 100 + index,
      audio_path: `audio/${id}-${index + 1}.opus`,
      audio_sha256: String((index + 3) % 10).repeat(64),
      qc: { mechanical: "pass", content: "pass", notes: [] },
      label: String.fromCharCode(65 + index),
      audio: { key: `${id}-${index + 1}`, url: `blob:${id}-${index + 1}` },
    }),
  );
  return {
    id,
    phase: line === null ? "anchor" : "line",
    model,
    scenario,
    character,
    line,
    role_epoch_sha256: epochCharacter.repeat(64),
    group_sha256: groupCharacter.repeat(64),
    role: {
      name: "受付嬢",
      kind: "human",
      gender: "female",
      age: "young_adult",
      archetype: "受付",
      voice: "滑舌明瞭で落ち着いた声",
      personality: "事務的だが親身",
    },
    conditioning: {
      method: "role anchor",
      summary: "役柄情報から同一人物のanchorを固定",
    },
    coverage: {
      gender: "exact",
      age: "exact",
      archetype: "exact",
    },
    comparison_required: comparisonRequired,
    comparison_reasons: comparisonRequired ? ["role mismatchの再確認"] : [],
    candidate_ids: candidates.map((candidate) => candidate.id),
    provisional_candidate_id: candidates[0]!.id,
    candidates,
  };
}

export function completeRoleReviewRubric(): RoleReviewRubric {
  return {
    content: "pass",
    prompt_leakage: "pass",
    reading: "pass",
    pitch_accent: "pass",
    gender: "pass",
    age: "pass",
    archetype: "pass",
    voice_identity: "pass",
    delivery: "pass",
    naturalness_quality: 4,
    notes: "",
  };
}

export function confirmAllGroups(catalog: RoleReviewCatalog): RoleReviewDraft {
  let draft = createRoleReviewDraft(catalog);
  for (const group of catalog.groups) {
    draft = updateRoleReviewRubric(catalog, draft, group.id, completeRoleReviewRubric());
    draft = markRoleReviewCandidateHeard(catalog, draft, group.id, group.candidate_ids[0]!);
    if (group.comparison_required) {
      draft = markRoleReviewCandidateHeard(catalog, draft, group.id, group.candidate_ids[1]!);
    }
    draft = confirmRoleReviewGroup(catalog, draft, group.id);
  }
  return draft;
}
