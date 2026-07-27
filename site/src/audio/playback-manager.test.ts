import { describe, expect, it } from "vite-plus/test";

import { PlaybackManager, type AudioClip, type AudioLike } from "./playback-manager";

interface Deferred {
  promise: Promise<void>;
  resolve(): void;
  reject(reason: unknown): void;
}

function deferred(): Deferred {
  let resolvePromise!: () => void;
  let rejectPromise!: (reason: unknown) => void;
  const promise = new Promise<void>((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return {
    promise,
    resolve: resolvePromise,
    reject: rejectPromise,
  };
}

type AudioEventType = "ended" | "error";

class FakeAudio implements AudioLike {
  src = "";
  error: { message: string } | null = null;
  pauseCalls = 0;
  loadCalls = 0;
  readonly playRequests: Deferred[] = [];

  private readonly listeners = new Map<AudioEventType, Set<() => void>>();

  play(): Promise<void> {
    const request = deferred();
    this.playRequests.push(request);
    return request.promise;
  }

  pause(): void {
    this.pauseCalls += 1;
  }

  load(): void {
    this.loadCalls += 1;
  }

  removeAttribute(name: "src"): void {
    if (name === "src") {
      this.src = "";
    }
  }

  addEventListener(type: AudioEventType, listener: () => void): void {
    const listeners = this.listeners.get(type) ?? new Set<() => void>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: AudioEventType, listener: () => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  emit(type: AudioEventType): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener();
    }
  }

  listenerCount(type: AudioEventType): number {
    return this.listeners.get(type)?.size ?? 0;
  }
}

const CLIP_A: AudioClip = { key: "a", url: "https://audio.example/a.mp3" };
const CLIP_B: AudioClip = { key: "b", url: "https://audio.example/b.mp3" };

describe("PlaybackManager", () => {
  it("reuses one audio instance and loads B after pausing A", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);

    const playA = manager.play(CLIP_A);
    expect(manager.getSnapshot()).toMatchObject({
      currentClipKey: "a",
      status: "loading",
    });

    const playB = manager.play(CLIP_B);
    expect(audio.src).toBe(CLIP_B.url);
    expect(audio.pauseCalls).toBe(2);
    expect(audio.loadCalls).toBe(2);
    expect(manager.getSnapshot()).toMatchObject({
      currentClipKey: "b",
      status: "loading",
    });

    audio.playRequests[1].resolve();
    await playB;
    expect(manager.getSnapshot().status).toBe("playing");

    audio.playRequests[0].resolve();
    await playA;
    expect(manager.getSnapshot()).toMatchObject({
      currentClipKey: "b",
      status: "playing",
      error: null,
    });
  });

  it("ignores a stale rejection after a newer request starts", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);

    const playA = manager.play(CLIP_A);
    const playB = manager.play(CLIP_B);
    audio.playRequests[1].resolve();
    await playB;

    audio.playRequests[0].reject(new Error("stale failure"));
    await playA;

    expect(manager.getSnapshot()).toEqual({
      currentClipKey: "b",
      status: "playing",
      error: null,
    });
  });

  it("toggles the current clip without reloading it", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);

    const initialPlay = manager.play(CLIP_A);
    audio.playRequests[0].resolve();
    await initialPlay;

    await manager.toggle(CLIP_A);
    expect(manager.getSnapshot().status).toBe("paused");
    expect(audio.loadCalls).toBe(1);

    const resumedPlay = manager.toggle(CLIP_A);
    audio.playRequests[1].resolve();
    await resumedPlay;
    expect(manager.getSnapshot().status).toBe("playing");
    expect(audio.loadCalls).toBe(1);
  });

  it("cleans up ended playback and attributes audio errors to the clip", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);

    const playA = manager.play(CLIP_A);
    audio.playRequests[0].resolve();
    await playA;
    audio.emit("ended");

    expect(manager.getSnapshot()).toEqual({
      currentClipKey: null,
      status: "idle",
      error: null,
    });
    expect(audio.src).toBe("");

    const playB = manager.play(CLIP_B);
    audio.playRequests[1].resolve();
    await playB;
    audio.error = { message: "media decode failed" };
    audio.emit("error");

    expect(manager.getSnapshot()).toMatchObject({
      currentClipKey: "b",
      status: "error",
    });
    expect(manager.getSnapshot().error?.message).toBe("media decode failed");
    expect(audio.src).toBe("");
  });

  it("exposes a rejected play promise as an error state", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);
    const rejection = new Error("autoplay denied");

    const play = manager.play(CLIP_A);
    audio.playRequests[0].reject(rejection);
    await play;

    expect(manager.getSnapshot()).toEqual({
      currentClipKey: "a",
      status: "error",
      error: rejection,
    });
    expect(audio.src).toBe("");
  });

  it("pauses, clears media, and removes listeners when disposed", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);
    const play = manager.play(CLIP_A);

    manager.dispose();

    expect(audio.listenerCount("ended")).toBe(0);
    expect(audio.listenerCount("error")).toBe(0);
    expect(audio.src).toBe("");
    expect(manager.getSnapshot()).toEqual({
      currentClipKey: null,
      status: "idle",
      error: null,
    });

    audio.playRequests[0].resolve();
    await play;
    expect(manager.getSnapshot().status).toBe("idle");
    await expect(manager.play(CLIP_B)).rejects.toThrow("PlaybackManager has been disposed.");
  });
});
