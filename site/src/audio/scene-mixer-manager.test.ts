import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { createBrowserSceneMixerManager, type SceneMixRequest } from "./scene-mixer-manager";

interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
}

function deferred<T>(): Deferred<T> {
  let resolvePromise!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

class FakeAudioParam {
  value = 0;
  readonly scheduledValues: Array<{ value: number; time: number; ramp: boolean }> = [];

  setValueAtTime(value: number, time: number): void {
    this.value = value;
    this.scheduledValues.push({ value, time, ramp: false });
  }

  linearRampToValueAtTime(value: number, time: number): void {
    this.value = value;
    this.scheduledValues.push({ value, time, ramp: true });
  }
}

class FakeAudioNode {
  readonly connections: unknown[] = [];
  disconnectCalls = 0;

  connect(target: unknown): unknown {
    this.connections.push(target);
    return target;
  }

  disconnect(): void {
    this.disconnectCalls += 1;
  }
}

class FakeGainNode extends FakeAudioNode {
  readonly gain = new FakeAudioParam();
}

class FakeStereoPannerNode extends FakeAudioNode {
  readonly pan = new FakeAudioParam();
}

class FakeDynamicsCompressorNode extends FakeAudioNode {
  readonly threshold = new FakeAudioParam();
  readonly knee = new FakeAudioParam();
  readonly ratio = new FakeAudioParam();
  readonly attack = new FakeAudioParam();
  readonly release = new FakeAudioParam();
}

class FakeAudioBufferSourceNode extends FakeAudioNode {
  buffer: unknown = null;
  loop = false;
  onended: (() => void) | null = null;
  readonly starts: number[] = [];
  stopCalls = 0;

  start(time: number): void {
    this.starts.push(time);
  }

  stop(): void {
    this.stopCalls += 1;
  }

  finish(): void {
    this.onended?.();
  }
}

class FakeAudioContext {
  currentTime = 12.5;
  readonly destination = new FakeAudioNode();
  readonly sources: FakeAudioBufferSourceNode[] = [];
  readonly gains: FakeGainNode[] = [];
  readonly panners: FakeStereoPannerNode[] = [];
  readonly compressors: FakeDynamicsCompressorNode[] = [];
  readonly decoded: ArrayBuffer[] = [];
  resumeCalls = 0;
  suspendCalls = 0;
  closeCalls = 0;
  decodeFailureAt: number | null = null;

  async resume(): Promise<void> {
    this.resumeCalls += 1;
  }

  async suspend(): Promise<void> {
    this.suspendCalls += 1;
  }

  async close(): Promise<void> {
    this.closeCalls += 1;
  }

  async decodeAudioData(bytes: ArrayBuffer): Promise<AudioBuffer> {
    const index = this.decoded.length;
    this.decoded.push(bytes);
    if (this.decodeFailureAt === index) {
      throw new Error(`decode failed: ${index}`);
    }
    return { index } as unknown as AudioBuffer;
  }

  createBufferSource(): AudioBufferSourceNode {
    const node = new FakeAudioBufferSourceNode();
    this.sources.push(node);
    return node as unknown as AudioBufferSourceNode;
  }

  createGain(): GainNode {
    const node = new FakeGainNode();
    this.gains.push(node);
    return node as unknown as GainNode;
  }

  createStereoPanner(): StereoPannerNode {
    const node = new FakeStereoPannerNode();
    this.panners.push(node);
    return node as unknown as StereoPannerNode;
  }

  createDynamicsCompressor(): DynamicsCompressorNode {
    const node = new FakeDynamicsCompressorNode();
    this.compressors.push(node);
    return node as unknown as DynamicsCompressorNode;
  }
}

const MIX: SceneMixRequest = {
  key: "tavern/dummy",
  tracks: [
    { key: "barmaid", url: "https://audio.example/barmaid.opus" },
    { key: "guest-a", url: "https://audio.example/guest-a.opus" },
    { key: "guest-b", url: "https://audio.example/guest-b.opus" },
  ],
};

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("SceneMixerManager", () => {
  it("creates and resumes AudioContext synchronously, then starts lanes with a bounded stagger", async () => {
    const context = new FakeAudioContext();
    const { audioContextConstructor, fetchMock } = installBrowser(context);
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const manager = createBrowserSceneMixerManager();

    const starting = manager.start(MIX);

    expect(audioContextConstructor).toHaveBeenCalledTimes(1);
    expect(context.resumeCalls).toBe(1);
    expect(manager.getSnapshot()).toEqual({
      sessionId: 1,
      currentMixKey: MIX.key,
      status: "loading",
      voiceCount: 3,
      error: null,
    });

    await starting;

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(context.decoded).toHaveLength(3);
    expect(context.sources).toHaveLength(3);
    const startTimes = context.sources.map((source) => source.starts[0]!);
    expect(new Set(startTimes).size).toBe(3);
    expect(startTimes).toEqual([...startTimes].sort((left, right) => left - right));
    expect(Math.min(...startTimes)).toBeGreaterThanOrEqual(12.55);
    expect(Math.max(...startTimes)).toBeLessThan(13.05);
    expect(Math.max(...startTimes) - Math.min(...startTimes)).toBeLessThan(0.5);
    expect(context.sources.every((source) => !source.loop)).toBe(true);
    expect(context.panners.every((panner) => Math.abs(panner.pan.value) <= 0.75)).toBe(true);
    expect(
      context.gains.slice(1).every((gain) => gain.gain.value <= 0.72 / MIX.tracks.length),
    ).toBe(true);
    for (const [index, gain] of context.gains.slice(1).entries()) {
      expect(gain.gain.scheduledValues[0]).toEqual({
        value: 0,
        time: startTimes[index],
        ramp: false,
      });
      expect(gain.gain.scheduledValues[1]?.time).toBeCloseTo(startTimes[index]! + 0.03);
      expect(gain.gain.scheduledValues[1]).toMatchObject({ ramp: true });
    }
    expect(context.compressors).toHaveLength(1);
    expect(context.gains[0].connections).toEqual([context.compressors[0]]);
    expect(context.compressors[0].connections).toEqual([context.destination]);
    expect(manager.getSnapshot()).toEqual({
      sessionId: 1,
      currentMixKey: MIX.key,
      status: "playing",
      voiceCount: 3,
      error: null,
    });
  });

  it("rejects candidate pools below 3 before creating AudioContext", async () => {
    const context = new FakeAudioContext();
    const { audioContextConstructor } = installBrowser(context);
    const manager = createBrowserSceneMixerManager();

    await expect(manager.start({ key: "too-few", tracks: MIX.tracks.slice(0, 2) })).rejects.toThrow(
      "at least 3",
    );
    expect(audioContextConstructor).not.toHaveBeenCalled();
  });

  it("selects at most 6 unique lanes from a larger candidate pool", async () => {
    const context = new FakeAudioContext();
    const { fetchMock } = installBrowser(context);
    vi.spyOn(Math, "random").mockReturnValue(0.999);
    const manager = createBrowserSceneMixerManager();
    const tracks = Array.from({ length: 20 }, (_, index) => ({
      key: `track-${index}`,
      url: `https://audio.example/${index}.opus`,
    }));

    await manager.start({ key: "large-pool", tracks });

    expect(manager.getSnapshot().voiceCount).toBe(6);
    expect(fetchMock).toHaveBeenCalledTimes(6);
    expect(context.decoded).toHaveLength(6);
    expect(context.sources).toHaveLength(6);
    expect(new Set(fetchMock.mock.calls.map(([url]) => requestUrl(url))).size).toBe(6);
  });

  it("schedules a fresh one-shot source after a randomized post-end gap", async () => {
    vi.useFakeTimers();
    const context = new FakeAudioContext();
    installBrowser(context);
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const manager = createBrowserSceneMixerManager();
    await manager.start(MIX);

    const ended = context.sources[0];
    ended.finish();
    expect(context.sources).toHaveLength(3);
    expect(ended.disconnectCalls).toBe(1);

    vi.advanceTimersByTime(999);
    expect(context.sources).toHaveLength(3);
    vi.advanceTimersByTime(1);
    expect(context.sources).toHaveLength(4);
    expect(context.sources[3].starts).toEqual([12.52]);
    expect(context.sources[3].loop).toBe(false);
  });

  it.each(["fetch", "decode"] as const)(
    "moves the whole mix to error when one %s fails",
    async (failure) => {
      const context = new FakeAudioContext();
      if (failure === "decode") {
        context.decodeFailureAt = 1;
      }
      const { fetchMock, signals } = installBrowser(context, failure === "fetch" ? 1 : null);
      const manager = createBrowserSceneMixerManager();

      await manager.start(MIX);

      expect(fetchMock).toHaveBeenCalledTimes(3);
      expect(context.sources).toHaveLength(0);
      expect(signals.every((signal) => signal.aborted)).toBe(true);
      expect(manager.getSnapshot()).toMatchObject({
        sessionId: 1,
        currentMixKey: MIX.key,
        status: "error",
        voiceCount: 0,
      });
      expect(manager.getSnapshot().error?.message).toContain(`${failure} failed`);
    },
  );

  it("aborts superseded fetches and ignores stale completions", async () => {
    const context = new FakeAudioContext();
    const oldResponses = MIX.tracks.map(() => deferred<Response>());
    const newResponses = MIX.tracks.map(() => deferred<Response>());
    const signals: AbortSignal[] = [];
    let oldIndex = 0;
    let newIndex = 0;
    installAudioContext(context);
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string | URL | Request, init?: RequestInit) => {
        if (init?.signal !== undefined && init.signal !== null) {
          signals.push(init.signal);
        }
        return requestUrl(url).includes("new-")
          ? newResponses[newIndex++].promise
          : oldResponses[oldIndex++].promise;
      }),
    );
    const manager = createBrowserSceneMixerManager();
    const oldStart = manager.start(MIX);
    await Promise.resolve();
    const nextMix: SceneMixRequest = {
      key: "market/dummy",
      tracks: MIX.tracks.map((track) => ({
        key: `new-${track.key}`,
        url: track.url.replace("/", "/new-"),
      })),
    };
    const newStart = manager.start(nextMix);
    await Promise.resolve();

    for (const response of newResponses) {
      response.resolve(okResponse());
    }
    await newStart;
    expect(manager.getSnapshot()).toMatchObject({
      sessionId: 2,
      currentMixKey: nextMix.key,
      status: "playing",
    });
    expect(context.sources).toHaveLength(3);

    for (const response of oldResponses) {
      response.resolve(okResponse());
    }
    await oldStart;
    expect(signals.slice(0, 3).every((signal) => signal.aborted)).toBe(true);
    expect(context.sources).toHaveLength(3);
    expect(manager.getSnapshot()).toMatchObject({
      sessionId: 2,
      currentMixKey: nextMix.key,
      status: "playing",
    });
  });

  it("stop clears fetches, timers, sources and suspends; dispose closes the context", async () => {
    vi.useFakeTimers();
    const context = new FakeAudioContext();
    const { signals } = installBrowser(context);
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const manager = createBrowserSceneMixerManager();
    await manager.start(MIX);
    context.sources[0].finish();

    await manager.stop();

    expect(signals.every((signal) => signal.aborted)).toBe(true);
    expect(context.sources.slice(1, 3).every((source) => source.stopCalls === 1)).toBe(true);
    expect(context.suspendCalls).toBe(1);
    expect(manager.getSnapshot()).toEqual({
      sessionId: 1,
      currentMixKey: null,
      status: "idle",
      voiceCount: 0,
      error: null,
    });
    vi.runAllTimers();
    expect(context.sources).toHaveLength(3);

    await manager.dispose();
    expect(context.closeCalls).toBe(1);
    await expect(manager.start(MIX)).rejects.toThrow("disposed");
  });
});

function installBrowser(
  context: FakeAudioContext,
  failedFetchIndex: number | null = null,
): {
  audioContextConstructor: ReturnType<typeof vi.fn>;
  fetchMock: ReturnType<typeof vi.fn>;
  signals: AbortSignal[];
} {
  const audioContextConstructor = installAudioContext(context);
  const signals: AbortSignal[] = [];
  let fetchIndex = 0;
  const fetchMock = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
    const index = fetchIndex;
    fetchIndex += 1;
    if (init?.signal !== undefined && init.signal !== null) {
      signals.push(init.signal);
    }
    if (failedFetchIndex === index) {
      return {
        ok: false,
        status: 503,
        arrayBuffer: async () => new ArrayBuffer(0),
      } as Response;
    }
    return okResponse();
  });
  vi.stubGlobal("fetch", fetchMock);
  return { audioContextConstructor, fetchMock, signals };
}

function installAudioContext(context: FakeAudioContext): ReturnType<typeof vi.fn> {
  const audioContextConstructor = vi.fn(function () {
    return context;
  });
  vi.stubGlobal("AudioContext", audioContextConstructor);
  return audioContextConstructor;
}

function okResponse(): Response {
  return {
    ok: true,
    status: 200,
    arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer,
  } as Response;
}

function requestUrl(input: string | URL | Request): string {
  if (typeof input === "string") {
    return input;
  }
  return input instanceof URL ? input.href : input.url;
}
