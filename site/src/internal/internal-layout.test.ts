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
    expect(markup).toContain("本地听测工具");
    expect(markup).toContain('href="/curate"');
    expect(markup).toContain('href="/completion"');
    expect(markup).toContain('href="/pilot"');
    expect(markup).toContain("音频筛选");
    expect(markup).toContain("角色听测");
    expect(markup).toContain("预检");
    expect(markup).toContain("data-global-sticky-header");
    expect(markup).toContain("min-h-(--gaya-header-height)");
    expect(markup).toContain("z-20");
  });

  it("listening modeは中国語の単一taskだけを表示する", () => {
    const markup = renderToStaticMarkup(
      createElement(MemoryRouter, null, createElement(InternalLayout, { listeningMode: true })),
    );

    expect(markup).toContain('data-listening-mode="true"');
    expect(markup).toContain('lang="zh-CN"');
    expect(markup).toContain("本地听测工作台");
    expect(markup).not.toContain("本地工具导航");
    expect(markup).not.toContain('href="/curate"');
    expect(markup).not.toContain('href="/pilot"');
  });
});
