import { createElement, type ComponentProps } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { AudioProvider } from "./audio-provider-component";

describe("AudioProvider", () => {
  it("effect 前の初回 render では明示的な fallback だけを表示する", () => {
    const props: ComponentProps<typeof AudioProvider> = {
      children: createElement("p", null, "audio ready"),
      fallback: createElement("button", { disabled: true }, "音声準備中"),
    };
    const markup = renderToStaticMarkup(createElement(AudioProvider, props));

    expect(markup).toContain("音声準備中");
    expect(markup).toContain('disabled=""');
    expect(markup).not.toContain("audio ready");
  });
});
