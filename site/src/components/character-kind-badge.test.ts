import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { CHARACTER_KIND_LABELS, CharacterKindBadge } from "./character-kind-badge";

describe("CharacterKindBadge", () => {
  it("human は表示しない", () => {
    expect(renderToStaticMarkup(createElement(CharacterKindBadge, { kind: "human" }))).toBe("");
  });

  it.each(["machine", "creature", "spirit"] as const)("%s は可視ラベルを表示する", (kind) => {
    expect(renderToStaticMarkup(createElement(CharacterKindBadge, { kind }))).toContain(
      CHARACTER_KIND_LABELS[kind],
    );
  });
});
