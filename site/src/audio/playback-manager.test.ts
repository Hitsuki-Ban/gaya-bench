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

type AudioEventType = "ended" | "error" | "timeupdate" | "durationchange";

class FakeAudio implements AudioLike {
  src = "";
  currentTime = 0;
  duration = Number.NaN;
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
    for (const listener of this.listenerSnapshot(type)) {
      listener();
    }
  }

  listenerSnapshot(type: AudioEventType): Array<() => void> {
    return [...(this.listeners.get(type) ?? [])];
  }

  listenerCount(type: AudioEventType): number {
    return this.listeners.get(type)?.size ?? 0;
  }
}

const CLIP_A: AudioClip = { key: "a", url: "https://audio.example/a.mp3" };
const CLIP_B: AudioClip = { key: "b", url: "https://audio.example/b.mp3" };

describe("PlaybackManager", () => {
  it("reuses one audio instance and assigns monotonically increasing sessions", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);

    const playA = manager.play(CLIP_A);
    expect(manager.getSnapshot()).toMatchObject({
      sessionId: 1,
      currentClipKey: "a",
      status: "loading",
      completion: null,
    });

    const playB = manager.play(CLIP_B);
    expect(audio.src).toBe(CLIP_B.url);
    expect(audio.pauseCalls).toBe(3);
    expect(audio.loadCalls).toBe(3);
    expect(manager.getSnapshot()).toMatchObject({
      sessionId: 2,
      currentClipKey: "b",
      status: "loading",
      completion: null,
    });

    audio.playRequests[1].resolve();
    await playB;
    audio.playRequests[0].resolve();
    await playA;

    expect(manager.getSnapshot()).toEqual({
      sessionId: 2,
      currentClipKey: "b",
      status: "playing",
      error: null,
      completion: null,
    });
  });

  it("distinguishes natural completion from an explicit stop", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);

    const playA = manager.play(CLIP_A);
    audio.playRequests[0].resolve();
    await playA;
    audio.emit("ended");

    expect(manager.getSnapshot()).toEqual({
      sessionId: 1,
      currentClipKey: null,
      status: "idle",
      error: null,
      completion: {
        sessionId: 1,
        clipKey: "a",
        termination: "ended",
      },
    });

    const playB = manager.play(CLIP_B);
    audio.playRequests[1].resolve();
    await playB;
    manager.stop();

    expect(manager.getSnapshot()).toEqual({
      sessionId: 2,
      currentClipKey: null,
      status: "idle",
      error: null,
      completion: {
        sessionId: 2,
        clipKey: "b",
        termination: "stopped",
      },
    });
  });

  it("pauses and resumes the same clip without creating a new session or reloading", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);

    const initialPlay = manager.play(CLIP_A);
    audio.playRequests[0].resolve();
    await initialPlay;

    await manager.toggle(CLIP_A);
    expect(manager.getSnapshot()).toMatchObject({
      sessionId: 1,
      status: "paused",
    });
    expect(audio.loadCalls).toBe(1);

    const resumedPlay = manager.toggle(CLIP_A);
    audio.playRequests[1].resolve();
    await resumedPlay;
    expect(manager.getSnapshot()).toMatchObject({
      sessionId: 1,
      status: "playing",
    });
    expect(audio.loadCalls).toBe(1);
  });

  it("publishes time updates through the progress store only", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);
    const play = manager.play(CLIP_A);
    audio.playRequests[0].resolve();
    await play;

    let stateNotifications = 0;
    let progressNotifications = 0;
    const unsubscribeState = manager.subscribe(() => {
      stateNotifications += 1;
    });
    const unsubscribeProgress = manager.subscribeProgress(() => {
      progressNotifications += 1;
    });

    audio.currentTime = 1.25;
    audio.duration = 4.5;
    audio.emit("durationchange");
    expect(manager.getProgressSnapshot()).toEqual({
      currentTime: 1.25,
      duration: 4.5,
    });

    audio.currentTime = 2.75;
    audio.emit("timeupdate");
    expect(manager.getProgressSnapshot()).toEqual({
      currentTime: 2.75,
      duration: 4.5,
    });
    expect(stateNotifications).toBe(0);
    expect(progressNotifications).toBe(2);

    audio.emit("timeupdate");
    expect(progressNotifications).toBe(2);
    unsubscribeState();
    unsubscribeProgress();
  });

  it("ignores stale play resolution, rejection, and ended callbacks", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);

    const playA = manager.play(CLIP_A);
    const staleEnded = audio.listenerSnapshot("ended")[0];
    const playB = manager.play(CLIP_B);
    audio.playRequests[1].resolve();
    await playB;

    audio.playRequests[0].reject(new Error("stale failure"));
    await playA;
    staleEnded();

    expect(manager.getSnapshot()).toEqual({
      sessionId: 2,
      currentClipKey: "b",
      status: "playing",
      error: null,
      completion: null,
    });
  });

  it("attributes audio element errors to the active session and clears progress", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);

    const play = manager.play(CLIP_A);
    audio.playRequests[0].resolve();
    await play;
    audio.currentTime = 1;
    audio.duration = 3;
    audio.emit("timeupdate");

    audio.error = { message: "media decode failed" };
    audio.emit("error");

    expect(manager.getSnapshot()).toMatchObject({
      sessionId: 1,
      currentClipKey: "a",
      status: "error",
      completion: {
        sessionId: 1,
        clipKey: "a",
        termination: "error",
      },
    });
    expect(manager.getSnapshot().error?.message).toBe("media decode failed");
    expect(manager.getProgressSnapshot()).toEqual({
      currentTime: 0,
      duration: 0,
    });
    expect(audio.src).toBe("");
  });

  it("records a rejected play promise as an error termination", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);
    const rejection = new Error("autoplay denied");

    const play = manager.play(CLIP_A);
    audio.playRequests[0].reject(rejection);
    await play;

    expect(manager.getSnapshot()).toEqual({
      sessionId: 1,
      currentClipKey: "a",
      status: "error",
      error: rejection,
      completion: {
        sessionId: 1,
        clipKey: "a",
        termination: "error",
      },
    });
    expect(audio.src).toBe("");
  });

  it("clears media, progress, and every listener when disposed", async () => {
    const audio = new FakeAudio();
    const manager = new PlaybackManager(audio);
    const play = manager.play(CLIP_A);
    audio.currentTime = 1;
    audio.duration = 2;
    audio.emit("timeupdate");

    manager.dispose();

    expect(audio.listenerCount("ended")).toBe(0);
    expect(audio.listenerCount("error")).toBe(0);
    expect(audio.listenerCount("timeupdate")).toBe(0);
    expect(audio.listenerCount("durationchange")).toBe(0);
    expect(audio.src).toBe("");
    expect(manager.getProgressSnapshot()).toEqual({
      currentTime: 0,
      duration: 0,
    });
    expect(manager.getSnapshot()).toEqual({
      sessionId: 1,
      currentClipKey: null,
      status: "idle",
      error: null,
      completion: {
        sessionId: 1,
        clipKey: "a",
        termination: "stopped",
      },
    });

    audio.playRequests[0].resolve();
    await play;
    expect(manager.getSnapshot().status).toBe("idle");
    await expect(manager.play(CLIP_B)).rejects.toThrow("PlaybackManager has been disposed.");
  });
});
