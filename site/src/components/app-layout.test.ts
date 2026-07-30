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

  it("ローカル音声選定 route をナビゲーションから開ける", () => {
    const markup = renderToStaticMarkup(
      createElement(MemoryRouter, null, createElement(AppLayout)),
    );

    expect(markup).toContain('href="/curate"');
    expect(markup).toContain("音声選定");
  });

  it("pre-gate pilot route をナビゲーションから開ける", () => {
    const markup = renderToStaticMarkup(
      createElement(MemoryRouter, null, createElement(AppLayout)),
    );

    expect(markup).toContain('href="/pilot"');
    expect(markup).toContain("事前確認");
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
      ["/curate", "音声選定"],
      ["/pilot", "事前確認"],
      ["/credits", "クレジット"],
    ]) {
      expect(markup).toContain(`aria-label="${label}"`);
      expect(markup).toContain(`href="${href}"`);
    }
  });
});
