import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vite-plus/test";

import { InternalLayout } from "@/internal/internal-layout";

describe("InternalLayout", () => {
  it("ローカル専用表示と人手評価 route を明示する", () => {
    const markup = renderToStaticMarkup(
      createElement(MemoryRouter, null, createElement(InternalLayout)),
    );

    expect(markup).toContain('data-internal-ui="gaya-bench-internal-ui-v1"');
    expect(markup).toContain("公開サイトに含まれないローカル専用");
    expect(markup).toContain('href="/curate"');
    expect(markup).toContain('href="/completion"');
    expect(markup).toContain('href="/pilot"');
    expect(markup).toContain("音声選定");
    expect(markup).toContain("役柄確認");
    expect(markup).toContain("事前確認");
    expect(markup).toContain("data-global-sticky-header");
    expect(markup).toContain("min-h-(--gaya-header-height)");
    expect(markup).toContain("z-20");
  });
});
