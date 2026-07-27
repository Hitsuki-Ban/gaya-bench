import { describe, expect, it } from "vite-plus/test";

import { assertAudioBaseConfigured, resolveAudioUrl } from "./audio-url";

describe("resolveAudioUrl", () => {
  it("ローカル base と manifest path を結合する", () => {
    expect(resolveAudioUrl("audio/dummy/tavern/line.opus", "/", "https://bench.example")).toBe(
      "https://bench.example/audio/dummy/tavern/line.opus",
    );
  });

  it("R2 base のサブパスを保持する", () => {
    expect(
      resolveAudioUrl(
        "audio/dummy/tavern/line.opus",
        "https://cdn.example/assets",
        "https://bench.example",
      ),
    ).toBe("https://cdn.example/assets/audio/dummy/tavern/line.opus");
  });

  it.each(["/audio/x.opus", "../x.opus", "audio/../x.opus", "https://evil.example/x.opus"])(
    "絶対 path と traversal を拒否する: %s",
    (clipPath) => {
      expect(() => resolveAudioUrl(clipPath, "/", "https://bench.example")).toThrow(
        "不正な clip path",
      );
    },
  );

  it.each([
    "audio\\dummy\\clip.opus",
    "audio//clip.opus",
    "audio/./clip.opus",
    "audio/%2e%2e/clip.opus",
  ])("不正な path 表現を拒否する: %s", (clipPath) => {
    expect(() => resolveAudioUrl(clipPath, "/", "https://bench.example")).toThrow(
      "不正な clip path",
    );
  });

  it("http(s) 以外の base を拒否する", () => {
    expect(() => assertAudioBaseConfigured("file:///tmp/audio", "https://bench.example")).toThrow(
      "VITE_AUDIO_BASE",
    );
  });

  it("相対 base を拒否する", () => {
    expect(() => assertAudioBaseConfigured("cdn/audio", "https://bench.example")).toThrow(
      "VITE_AUDIO_BASE",
    );
  });

  it("外部 origin に解釈される絶対 path を拒否する", () => {
    expect(() =>
      assertAudioBaseConfigured(String.raw`/\evil.example/audio`, "https://bench.example"),
    ).toThrow("VITE_AUDIO_BASE");
  });
});
