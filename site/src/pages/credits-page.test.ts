import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { describe, expect, it } from "vite-plus/test";

import { benchmarkData, modelCreditById } from "@/data";

import { CreditsPage } from "./credits-page";

describe("CreditsPage", () => {
  it("manifest の全 model と metadata の全 reference voice を表示する", () => {
    const markup = renderToStaticMarkup(createElement(CreditsPage));

    for (const model of benchmarkData.manifest.models) {
      expect(markup).toContain(`data-model-id="${model.id}"`);
      expect(markup).toContain(model.name);
      expect(markup).toContain(model.license_note);
      for (const source of modelCreditById.get(model.id)!.sources) {
        expect(markup).toContain(source.url);
        expect(markup).toContain(`${source.repository}@${source.revision}`);
      }
    }
    for (const voice of benchmarkData.credits.reference_voices) {
      expect(markup).toContain(`data-reference-voice-id="${voice.id}"`);
      expect(markup).toContain(voice.source.title);
      expect(markup).toContain(voice.credit_text.split("\n")[0]);
      expect(markup).toContain(voice.transcript_rights.credit_text.split("\n")[0]);
    }
  });

  it("project license、release identity、免責を同じ dossier に収録する", () => {
    const markup = renderToStaticMarkup(createElement(CreditsPage));

    expect(markup).toContain("CC BY 4.0");
    expect(markup).toContain("MIT License");
    expect(markup).toContain(benchmarkData.manifest.candidate_set_sha256);
    expect(markup).toContain("公開音声はAI生成です");
    expect(markup).not.toContain("Issue #17");
    expect(markup).not.toContain("公開前に統合");
  });
});
