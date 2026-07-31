import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { describe, expect, it } from "vite-plus/test";

import type { ModelCapabilities } from "@/data";

import { ModelMethodBadge } from "./model-method-badge";

describe("ModelMethodBadge", () => {
  it("voice_prompt を最優先に生成方式を一意に導出する", () => {
    const markup = renderToStaticMarkup(
      createElement(ModelMethodBadge, {
        capabilities: capabilities({ voice_prompt: true, clone: true }),
      }),
    );

    expect(markup).toContain("テキスト指示で声を生成");
    expect(markup).not.toContain("参照音声からの声クローン");
  });

  it.each([
    [{ voice_prompt: true }, "テキスト指示で声を生成"],
    [{ clone: true }, "参照音声からの声クローン"],
    [{}, "プリセット話者"],
  ] as const)("生成方式を利用者向け文言で表示する", (overrides, label) => {
    const markup = renderToStaticMarkup(
      createElement(ModelMethodBadge, { capabilities: capabilities(overrides) }),
    );

    expect(markup).toContain(label);
    expect(markup).toContain(`aria-label="生成方式: ${label}"`);
  });

  it("compact 表示でも完全な方式名を可視表示する", () => {
    const markup = renderToStaticMarkup(
      createElement(ModelMethodBadge, {
        capabilities: capabilities({ clone: true }),
        compact: true,
      }),
    );

    expect(markup).toContain(">参照音声からの声クローン<");
    expect(markup).toContain('aria-label="生成方式: 参照音声からの声クローン"');
  });
});

function capabilities(overrides: Partial<ModelCapabilities> = {}): ModelCapabilities {
  return {
    emotion: false,
    voice_prompt: false,
    clone: false,
    nonverbal: false,
    reading: false,
    ...overrides,
  };
}
