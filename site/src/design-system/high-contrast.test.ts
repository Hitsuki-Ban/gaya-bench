import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { describe, expect, it } from "vite-plus/test";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { WaveformProgress } from "@/components/waveform-progress";

const stylesheet = readFileSync(new URL("../index.css", import.meta.url), "utf8");
const forcedColors = stylesheet.slice(stylesheet.indexOf("@media (forced-colors: active)"));
const comparisonPage = readFileSync(
  new URL("../comparison/comparison-page.tsx", import.meta.url),
  "utf8",
);
const scenarioPage = readFileSync(new URL("../pages/scenario-page.tsx", import.meta.url), "utf8");

describe("high contrast design system", () => {
  it("システム色を意味の組で使い、色保持を対のある塗り要素だけに限定する", () => {
    expect(forcedColors).toContain("@media (forced-colors: active)");
    for (const systemColor of [
      "Canvas",
      "CanvasText",
      "ButtonFace",
      "ButtonText",
      "ButtonBorder",
      "Highlight",
      "HighlightText",
      "Field",
      "FieldText",
      "GrayText",
      "LinkText",
      "Mark",
      "MarkText",
    ]) {
      expect(forcedColors).toContain(systemColor);
    }
    expect(forcedColors.match(/forced-color-adjust\s*:\s*none/g)).toHaveLength(1);
    expect(forcedColors).not.toMatch(/:root\s*\{[^}]*forced-color-adjust\s*:\s*none/s);
    expect(forcedColors).toContain('a[href][aria-current="page"]:not([data-navigation-brand])');
    for (const compensatedSelector of [
      '[data-slot="button"][data-variant="default"]',
      '[data-slot="button"][data-variant="destructive"]',
      '[data-slot="badge"][data-variant="default"]',
      '[role="tab"][aria-selected="true"]',
      '[aria-current="page"]',
      '[data-matrix-coordinate][data-current="true"]',
    ]) {
      expect(forcedColors).toContain(compensatedSelector);
    }
  });

  it("focus・選択・disabled・error・progressを非色差だけでも識別できる", () => {
    for (const contract of [
      ":focus-visible {",
      '[aria-pressed="true"]',
      '[aria-selected="true"]',
      '[aria-current="page"]',
      '[aria-disabled="true"]',
      '[data-selected="true"]',
      '[data-current="true"]',
      '[data-playback-status="error"]',
      '[data-slot="button"][data-playback-status="error"]',
      '[data-matrix-coordinate][data-playback-status="error"]',
      '[data-visual-intent="error"]',
      '[data-visual-intent="unavailable"]',
      '[data-slot="file-trigger"]:has(input:focus-visible)',
      '[data-slot="file-trigger"]:has(input:disabled)',
      '[role="progressbar"]',
      '[role="progressbar"] > .gaya-progress',
      '[data-slot="waveform-bar"][data-progress="elapsed"]',
      '[data-slot="waveform-bar"][data-progress="future"]',
    ]) {
      expect(forcedColors).toContain(contract);
    }
    expect(forcedColors).toContain("outline: 3px solid Highlight !important");
    expect(forcedColors).toContain("border-style: dashed !important");
    expect(forcedColors).toContain("border: 2px double Mark");
    expect(forcedColors).toContain("opacity: 1 !important");
    expect(forcedColors).toContain("accent-color: auto !important");
  });

  it("公開画面のerrorとunavailable状態が外観hookを公開する", () => {
    expect(comparisonPage).toContain('data-visual-intent="error"');
    expect(scenarioPage).toContain(
      'data-visual-intent={outcome.kind === "failure" ? "error" : "unavailable"}',
    );
  });

  it("共有primitiveとwaveformが安定した外観hookを公開する", () => {
    const button = renderToStaticMarkup(createElement(Button, null, "再生"));
    const badge = renderToStaticMarkup(createElement(Badge, { variant: "secondary" }, "状態"));
    const waveform = renderToStaticMarkup(createElement(WaveformProgress, { ratio: 0.5 }));

    expect(button).toContain('data-slot="button"');
    expect(button).toContain('data-variant="default"');
    expect(badge).toContain('data-slot="badge"');
    expect(badge).toContain('data-variant="secondary"');
    expect(waveform).toContain('data-slot="waveform"');
    expect(waveform.match(/data-progress="elapsed"/g)).toHaveLength(12);
    expect(waveform.match(/data-progress="future"/g)).toHaveLength(12);
  });

  it("通常モードの本文と操作境界が必要なコントラストを持つ", () => {
    const background = rootColor("background");
    const card = rootColor("card");
    const input = rootColor("input");

    expect(contrastRatio(rootColor("foreground"), background)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(rootColor("muted-foreground"), background)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(rootColor("primary"), background)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(rootColor("destructive"), background)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(input, background)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(input, card)).toBeGreaterThanOrEqual(3);
  });
});

function rootColor(name: string): string {
  const root = /:root\s*\{(?<tokens>[\s\S]*?)\}/.exec(stylesheet)?.groups?.tokens;
  const value = new RegExp(`--${name}:\\s*(#[0-9a-f]{6});`, "i").exec(root ?? "")?.[1];
  if (!value) {
    throw new Error(`:root に hex color token --${name} がありません。`);
  }
  return value;
}

function contrastRatio(left: string, right: string): number {
  const leftLuminance = luminance(left);
  const rightLuminance = luminance(right);
  return (
    (Math.max(leftLuminance, rightLuminance) + 0.05) /
    (Math.min(leftLuminance, rightLuminance) + 0.05)
  );
}

function luminance(hex: string): number {
  const value = Number.parseInt(hex.slice(1), 16);
  const channels = [value >> 16, (value >> 8) & 0xff, value & 0xff].map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0]! + 0.7152 * channels[1]! + 0.0722 * channels[2]!;
}
