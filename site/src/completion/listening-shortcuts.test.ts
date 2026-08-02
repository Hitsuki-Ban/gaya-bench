import { describe, expect, it } from "vite-plus/test";

import {
  candidateShortcutIndex,
  candidateShortcutLabel,
  isListeningShortcutInteractiveTarget,
} from "./listening-shortcuts";

const EVENT = {
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  target: null,
} as const;

describe("listening candidate shortcuts", () => {
  it("候補数に合わせて1..Nだけを選ぶ", () => {
    expect(candidateShortcutIndex({ ...EVENT, key: "1" }, 1)).toBe(0);
    expect(candidateShortcutIndex({ ...EVENT, key: "3" }, 3)).toBe(2);
    expect(candidateShortcutIndex({ ...EVENT, key: "4" }, 3)).toBeNull();
    expect(candidateShortcutLabel(1)).toBe("1");
    expect(candidateShortcutLabel(6)).toBe("1–6");
  });

  it("修飾キーとinput/select/details内の操作を奪わない", () => {
    expect(candidateShortcutIndex({ ...EVENT, ctrlKey: true, key: "1" }, 3)).toBeNull();
    for (const selector of ["input", "select", "details"]) {
      const target = {
        closest(received: string) {
          return received.includes(selector) ? this : null;
        },
      } as unknown as EventTarget;
      expect(isListeningShortcutInteractiveTarget(target)).toBe(true);
      expect(candidateShortcutIndex({ ...EVENT, key: "1", target }, 3)).toBeNull();
    }
  });
});
