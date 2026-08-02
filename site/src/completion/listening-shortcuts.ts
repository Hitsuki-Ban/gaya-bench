export interface CandidateShortcutEvent {
  readonly key: string;
  readonly ctrlKey: boolean;
  readonly metaKey: boolean;
  readonly altKey: boolean;
  readonly target: EventTarget | null;
}

const INTERACTIVE_TARGET_SELECTOR = [
  "input",
  "textarea",
  "select",
  "option",
  "details",
  "summary",
  "button",
  "a",
  "[contenteditable='true']",
  "[role='button']",
].join(",");

export function candidateShortcutIndex(
  event: CandidateShortcutEvent,
  candidateCount: number,
): number | null {
  if (
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    isListeningShortcutInteractiveTarget(event.target) ||
    !/^[1-9]$/.test(event.key)
  ) {
    return null;
  }
  const index = Number(event.key) - 1;
  return index < candidateCount ? index : null;
}

export function isListeningShortcutInteractiveTarget(target: EventTarget | null): boolean {
  if (target === null || typeof target !== "object" || !("closest" in target)) {
    return false;
  }
  const closest = (target as { readonly closest?: unknown }).closest;
  return (
    typeof closest === "function" && Boolean(closest.call(target, INTERACTIVE_TARGET_SELECTOR))
  );
}

export function candidateShortcutLabel(candidateCount: number): string {
  if (!Number.isInteger(candidateCount) || candidateCount < 1 || candidateCount > 9) {
    throw new Error("candidate shortcut countは1..9が必要です。");
  }
  return candidateCount === 1 ? "1" : `1–${candidateCount}`;
}
