import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vite-plus/test";

import { AppLayout, MachineGeneratedAudioNotice } from "@/components/app-layout";

describe("MachineGeneratedAudioNotice", () => {
  it("discloses machine-generated TTS audio in every layout", () => {
    const markup = renderToStaticMarkup(createElement(MachineGeneratedAudioNotice));

    expect(markup).toContain('role="note"');
    expect(markup).toContain("掲載音声は AI テキスト読み上げ（TTS）により機械生成されています。");
    expect(markup).toContain("品質注記は自動判定であり、人手確認は順次実施中です。");
  });

  it("開発者向け route を公開ナビゲーションに表示しない", () => {
    const markup = renderToStaticMarkup(
      createElement(MemoryRouter, null, createElement(AppLayout)),
    );

    expect(markup).not.toContain('href="/curate"');
    expect(markup).not.toContain('href="/pilot"');
    expect(markup).not.toContain("音声選定");
    expect(markup).not.toContain("事前確認");
  });

  it("320px 幅でも全ナビゲーションを識別できる", () => {
    const markup = renderToStaticMarkup(
      createElement(MemoryRouter, null, createElement(AppLayout)),
    );

    expect(markup).toContain('aria-label="比較トップ"');
    expect(markup).toContain('class="hidden min-[360px]:block"');

    for (const [href, label] of [
      ["/", "比較"],
      ["/ab", "A/B"],
      ["/credits", "クレジット"],
    ]) {
      expect(markup).toContain(`aria-label="${label}"`);
      expect(markup).toContain(`href="${href}"`);
    }
  });
});
