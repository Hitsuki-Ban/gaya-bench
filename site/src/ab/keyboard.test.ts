import { describe, expect, it } from "vite-plus/test";

import { resolveAbShortcut, type AbKeyboardInput } from "./keyboard";

describe("resolveAbShortcut", () => {
  it.each([
    ["ArrowLeft", "play-left"],
    ["ArrowRight", "play-right"],
    ["1", "vote-left"],
    ["2", "vote-tie"],
    ["3", "vote-right"],
  ] as const)("%s を比較操作へ割り当てる", (key, expected) => {
    expect(resolveAbShortcut(keyboardInput(key))).toBe(expected);
  });

  it("未割り当てキー、修飾キー、長押し、変換中を無視する", () => {
    expect(resolveAbShortcut(keyboardInput("Enter"))).toBeNull();
    expect(resolveAbShortcut({ ...keyboardInput("1"), ctrlKey: true })).toBeNull();
    expect(resolveAbShortcut({ ...keyboardInput("1"), repeat: true })).toBeNull();
    expect(resolveAbShortcut({ ...keyboardInput("1"), isComposing: true })).toBeNull();
  });

  it("編集可能な要素からの数字キーを無視する", () => {
    const input = { tagName: "input" } as unknown as EventTarget;
    const editable = { isContentEditable: true } as unknown as EventTarget;

    expect(resolveAbShortcut({ ...keyboardInput("1"), target: input })).toBeNull();
    expect(resolveAbShortcut({ ...keyboardInput("2"), target: editable })).toBeNull();
  });
});

function keyboardInput(key: string): AbKeyboardInput {
  return {
    key,
    repeat: false,
    isComposing: false,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    target: null,
  };
}
