import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { MachineGeneratedAudioNotice } from "@/components/app-layout";

describe("MachineGeneratedAudioNotice", () => {
  it("discloses machine-generated TTS audio in every layout", () => {
    const markup = renderToStaticMarkup(createElement(MachineGeneratedAudioNotice));

    expect(markup).toContain('role="note"');
    expect(markup).toContain("掲載音声は AI テキスト読み上げ（TTS）により機械生成されています。");
  });
});
