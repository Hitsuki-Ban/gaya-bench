import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import { describe, expect, it } from "vite-plus/test";

import { referenceVoiceById, type PublishedCandidate } from "@/data";

import { ReferenceConditioningBadge } from "./reference-conditioning-badge";

type ReferenceConditioning = PublishedCandidate["reference_conditioning"];

describe("ReferenceConditioningBadge", () => {
  it("参照なしとモデル生成参照をリンクなしで表示する", () => {
    const none = render({ kind: "none" });
    const generated = render({
      kind: "model_generated_reference",
      inference_reference_sha256: "a".repeat(64),
      source_kind: "voice_design",
    });

    expect(none).toContain("参照音声：なし");
    expect(none).not.toContain("<a");
    expect(generated).toContain("参照: モデル生成");
    expect(generated).not.toContain("<a");
  });

  it("収録音声は話者名を表示し紹介ページへリンクする", () => {
    const voice = referenceVoiceById.values().next().value;
    if (!voice) {
      throw new Error("reference voice fixture がありません");
    }

    const markup = render({
      kind: "human_reference",
      voice_id: voice.id,
      asset_sha256: voice.sha256,
      inference_reference_sha256: voice.sha256,
      selection_source: "character",
    });

    expect(markup).toContain(`参照: 収録音声（${voice.source.speaker}）`);
    expect(markup).toContain(`href="/reference-voices#${voice.id}"`);
  });

  it("matrix から指定された roving tab index を link に反映する", () => {
    const voice = referenceVoiceById.values().next().value;
    if (!voice) {
      throw new Error("reference voice fixture がありません");
    }

    const markup = render(
      {
        kind: "human_reference",
        voice_id: voice.id,
        asset_sha256: voice.sha256,
        inference_reference_sha256: voice.sha256,
        selection_source: "character",
      },
      -1,
    );

    expect(markup).toContain('tabindex="-1"');
  });

  it("未知の参照音声 ID を拒否する", () => {
    expect(() =>
      render({
        kind: "human_reference",
        voice_id: "unknown-reference",
        asset_sha256: "a".repeat(64),
        inference_reference_sha256: "a".repeat(64),
        selection_source: "character",
      }),
    ).toThrow("参照音声の表示情報がありません");
  });
});

function render(conditioning: ReferenceConditioning, tabIndex?: 0 | -1): string {
  return renderToStaticMarkup(
    createElement(
      MemoryRouter,
      null,
      createElement(ReferenceConditioningBadge, { conditioning, tabIndex }),
    ),
  );
}
