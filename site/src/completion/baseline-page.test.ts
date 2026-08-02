import { describe, expect, it, vi } from "vite-plus/test";

import { baselineInteractionLocks, navigateBaselineGroup } from "./baseline-interactions";

describe("Phase B page interaction boundaries", () => {
  it("finalize後は変更だけを固定し、移動と再生は読み取り専用で残す", () => {
    expect(
      baselineInteractionLocks({ finalized: true, submitting: false, hasError: false }),
    ).toEqual({
      mutationLocked: true,
      navigationLocked: false,
      playbackLocked: false,
    });
    expect(
      baselineInteractionLocks({ finalized: false, submitting: true, hasError: false }),
    ).toEqual({
      mutationLocked: true,
      navigationLocked: true,
      playbackLocked: true,
    });
  });

  it("すべてのgroup移動で音声を停止してからindexを変える", () => {
    const calls: string[] = [];
    const changed = navigateBaselineGroup({
      index: 8,
      submitting: false,
      stopPlayback: () => calls.push("stop"),
      setIndex: (index) => calls.push(`navigate:${index}`),
    });
    expect(changed).toBe(true);
    expect(calls).toEqual(["stop", "navigate:8"]);

    const stopPlayback = vi.fn();
    const setIndex = vi.fn();
    expect(navigateBaselineGroup({ index: 2, submitting: true, stopPlayback, setIndex })).toBe(
      false,
    );
    expect(stopPlayback).not.toHaveBeenCalled();
    expect(setIndex).not.toHaveBeenCalled();
  });
});
