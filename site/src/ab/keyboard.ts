export type AbShortcut = "play-left" | "play-right" | "vote-left" | "vote-tie" | "vote-right";

export interface AbKeyboardInput {
  readonly key: string;
  readonly repeat: boolean;
  readonly isComposing: boolean;
  readonly altKey: boolean;
  readonly ctrlKey: boolean;
  readonly metaKey: boolean;
  readonly shiftKey: boolean;
  readonly target: EventTarget | null;
}

export function resolveAbShortcut(input: AbKeyboardInput): AbShortcut | null {
  if (
    input.repeat ||
    input.isComposing ||
    input.altKey ||
    input.ctrlKey ||
    input.metaKey ||
    input.shiftKey ||
    isEditableTarget(input.target)
  ) {
    return null;
  }

  if (input.key === "ArrowLeft") {
    return "play-left";
  }
  if (input.key === "ArrowRight") {
    return "play-right";
  }
  if (input.key === "1") {
    return "vote-left";
  }
  if (input.key === "2") {
    return "vote-tie";
  }
  if (input.key === "3") {
    return "vote-right";
  }
  return null;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (target === null) {
    return false;
  }
  const element = target as EventTarget & {
    readonly tagName?: unknown;
    readonly isContentEditable?: unknown;
  };
  const tagName = typeof element.tagName === "string" ? element.tagName.toUpperCase() : "";
  return (
    tagName === "INPUT" ||
    tagName === "TEXTAREA" ||
    tagName === "SELECT" ||
    element.isContentEditable === true
  );
}
