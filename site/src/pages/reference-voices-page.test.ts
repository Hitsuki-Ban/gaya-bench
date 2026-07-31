import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { describe, expect, it } from "vite-plus/test";

import { benchmarkData } from "@/data";

import { ReferenceVoicesPage } from "./reference-voices-page";

describe("ReferenceVoicesPage", () => {
  it("metadata の全素材を話者名と声質で紹介する", () => {
    const markup = renderPage();

    expect(benchmarkData.credits.reference_voices).toHaveLength(5);
    for (const voice of benchmarkData.credits.reference_voices) {
      expect(markup).toContain(`id="${voice.id}"`);
      expect(markup).toContain(voice.source.speaker);
      expect(markup).toContain(voice.source.title);
      expect(markup).toContain(voice.voice.notes);
      expect(markup).toContain(voice.rights.license);
      expect(markup).toContain(voice.rights.redistribution.notes);
      expect(markup).toContain(voice.source.download_page);
      expect(markup).not.toContain(`>${voice.id}<`);
    }
  });

  it("原音声を埋め込まず credits の権利詳細へリンクする", () => {
    const markup = renderPage();

    expect(markup).not.toContain("<audio");
    expect(markup).not.toContain("<source");
    for (const voice of benchmarkData.credits.reference_voices) {
      expect(markup).not.toContain(voice.file);
    }
    expect(markup).toContain('href="/credits#voices"');
    expect(markup).toContain("原音声ファイルは本サイトでは配布・再生しません");
  });
});

function renderPage(): string {
  return renderToStaticMarkup(
    createElement(MemoryRouter, null, createElement(ReferenceVoicesPage)),
  );
}
