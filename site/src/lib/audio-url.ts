import { isSafeClipPath } from "@/lib/clip-path";

function requiredAudioBase() {
  const value = import.meta.env.VITE_AUDIO_BASE;
  if (!value) {
    throw new Error("VITE_AUDIO_BASE が設定されていません。");
  }
  return value;
}

export function assertAudioBaseConfigured(
  audioBase = requiredAudioBase(),
  origin = window.location.origin,
) {
  const isAbsolutePath =
    audioBase.startsWith("/") && !audioBase.startsWith("//") && !audioBase.includes("\\");
  const isHttpUrl = /^https?:\/\//i.test(audioBase);
  if (!isAbsolutePath && !isHttpUrl) {
    throw new Error(`VITE_AUDIO_BASE は http(s) URL または絶対パスが必要です: ${audioBase}`);
  }

  const base = new URL(audioBase.endsWith("/") ? audioBase : `${audioBase}/`, origin);
  if (base.protocol !== "http:" && base.protocol !== "https:") {
    throw new Error(`VITE_AUDIO_BASE は http(s) URL または絶対パスが必要です: ${audioBase}`);
  }
  return base;
}

export function resolveAudioUrl(
  clipPath: string,
  audioBase = requiredAudioBase(),
  origin = window.location.origin,
) {
  if (!isSafeClipPath(clipPath)) {
    throw new Error(`不正な clip path です: ${clipPath}`);
  }

  const base = assertAudioBaseConfigured(audioBase, origin);
  return new URL(clipPath, base).href;
}
