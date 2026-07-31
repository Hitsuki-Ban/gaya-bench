import { describe, expect, it, vi } from "vite-plus/test";

import { AudioSessionCoordinator } from "./audio-session-coordinator";
import { PlaybackManager, type AudioLike } from "./playback-manager";
import type { SceneMixerManager, SceneMixRequest } from "./scene-mixer-manager";

type AudioEventType = "ended" | "error" | "timeupdate" | "durationchange";

interface Deferred {
  promise: Promise<void>;
  resolve(): void;
}

function deferred(): Deferred {
  let resolvePromise!: () => void;
  const promise = new Promise<void>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

class FakeAudio implements AudioLike {
  src = "";
  currentTime = 0;
  duration = Number.NaN;
  error: { message: string } | null = null;
  readonly playRequests: Deferred[] = [];

  play(): Promise<void> {
    const request = deferred();
    this.playRequests.push(request);
    return request.promise;
  }

  pause(): void {}

  load(): void {}

  removeAttribute(name: "src"): void {
    if (name === "src") {
      this.src = "";
    }
  }

  addEventListener(_type: AudioEventType, _listener: () => void): void {}

  removeEventListener(_type: AudioEventType, _listener: () => void): void {}
}

class FakeSceneMixer {
  readonly starts: SceneMixRequest[] = [];
  stopCalls = 0;
  readonly events: string[];

  constructor(events: string[] = []) {
    this.events = events;
  }

  async start(request: SceneMixRequest): Promise<void> {
    this.events.push("mixer.start");
    this.starts.push(request);
  }

  async stop(): Promise<void> {
    this.events.push("mixer.stop");
    this.stopCalls += 1;
  }
}

const MIX: SceneMixRequest = {
  key: "tavern/dummy",
  tracks: [
    { key: "a", url: "https://audio.example/a.opus" },
    { key: "b", url: "https://audio.example/b.opus" },
    { key: "c", url: "https://audio.example/c.opus" },
  ],
};

describe("AudioSessionCoordinator", () => {
  it("stops single playback before starting a scene mix", async () => {
    const events: string[] = [];
    const playback = new PlaybackManager(new FakeAudio());
    vi.spyOn(playback, "stop").mockImplementation(() => {
      events.push("playback.stop");
    });
    const mixer = new FakeSceneMixer(events);
    const coordinator = new AudioSessionCoordinator(
      playback,
      mixer as unknown as SceneMixerManager,
    );

    await coordinator.startSceneMix(MIX);

    expect(events).toEqual(["playback.stop", "mixer.start"]);
    expect(mixer.starts).toEqual([MIX]);
  });

  it("stops the mixer when provider code directly starts PlaybackManager", async () => {
    const audio = new FakeAudio();
    const playback = new PlaybackManager(audio);
    const mixer = new FakeSceneMixer();
    new AudioSessionCoordinator(playback, mixer as unknown as SceneMixerManager);

    const playing = playback.play({ key: "single", url: "https://audio.example/single.opus" });
    expect(playback.getSnapshot().status).toBe("loading");
    expect(mixer.stopCalls).toBe(1);

    audio.playRequests[0].resolve();
    await playing;
    expect(playback.getSnapshot().status).toBe("playing");
    expect(mixer.stopCalls).toBe(2);
  });

  it("stops explicitly and unsubscribes from PlaybackManager when disposed", async () => {
    const audio = new FakeAudio();
    const playback = new PlaybackManager(audio);
    const mixer = new FakeSceneMixer();
    const coordinator = new AudioSessionCoordinator(
      playback,
      mixer as unknown as SceneMixerManager,
    );

    await coordinator.stopSceneMix();
    expect(mixer.stopCalls).toBe(1);
    coordinator.dispose();
    expect(mixer.stopCalls).toBe(1);

    const playing = playback.play({ key: "single", url: "https://audio.example/single.opus" });
    audio.playRequests[0].resolve();
    await playing;
    expect(mixer.stopCalls).toBe(1);
    await expect(coordinator.startSceneMix(MIX)).rejects.toThrow("disposed");
  });
});
