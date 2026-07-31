export type SceneMixerStatus = "idle" | "loading" | "playing" | "error";

export interface SceneMixerTrack {
  key: string;
  url: string;
}

export interface SceneMixRequest {
  key: string;
  tracks: SceneMixerTrack[];
}

export interface SceneMixerState {
  sessionId: number;
  currentMixKey: string | null;
  status: SceneMixerStatus;
  voiceCount: number;
  error: Error | null;
}

type StoreListener = () => void;
type TimerHandle = ReturnType<typeof setTimeout>;

interface ActiveVoice {
  source: AudioBufferSourceNode;
  panner: StereoPannerNode;
  gain: GainNode;
}

const INITIAL_STATE: SceneMixerState = {
  sessionId: 0,
  currentMixKey: null,
  status: "idle",
  voiceCount: 0,
  error: null,
};

const MIN_LANES = 3;
const MAX_LANES = 6;
const INITIAL_START_LEAD_SECONDS = 0.05;
const INITIAL_START_SPREAD_SECONDS = 0.5;
const RESTART_START_LEAD_SECONDS = 0.02;
const VOICE_FADE_IN_SECONDS = 0.03;
const MIN_RESTART_DELAY_MS = 400;
const RESTART_DELAY_RANGE_MS = 1_200;
const MAX_MIX_GAIN = 0.72;
const MIN_DISTANCE_FACTOR = 0.45;
const DISTANCE_FACTOR_RANGE = 0.55;
const MAX_ABSOLUTE_PAN = 0.75;

export class SceneMixerManager {
  readonly getSnapshot = (): SceneMixerState => this.state;

  readonly subscribe = (listener: StoreListener): (() => void) => {
    this.assertActive();
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly start = async (request: SceneMixRequest): Promise<void> => {
    this.assertActive();
    this.assertRequest(request);
    const selectedTracks = this.selectTracks(request.tracks);

    const context = this.getOrCreateAudioContext();
    this.sessionToken += 1;
    const token = this.sessionToken;
    this.clearSessionResources();

    const controller = new AbortController();
    this.fetchController = controller;
    const sessionId = this.nextSessionId + 1;
    this.nextSessionId = sessionId;

    const resume = context.resume();
    this.updateState({
      sessionId,
      currentMixKey: request.key,
      status: "loading",
      voiceCount: selectedTracks.length,
      error: null,
    });

    try {
      await resume;
      const buffers = await Promise.all(
        selectedTracks.map((track) => this.loadTrack(context, track, controller.signal)),
      );
      if (!this.isCurrentSession(token)) {
        return;
      }

      this.installOutputGraph(context);
      const initialAnchor = context.currentTime + INITIAL_START_LEAD_SECONDS;
      for (const [index, buffer] of buffers.entries()) {
        const initialOffset =
          ((index + Math.random()) / buffers.length) * INITIAL_START_SPREAD_SECONDS;
        this.scheduleVoice(
          context,
          buffer,
          selectedTracks.length,
          initialAnchor + initialOffset,
          token,
        );
      }
      this.updateState({
        sessionId,
        currentMixKey: request.key,
        status: "playing",
        voiceCount: selectedTracks.length,
        error: null,
      });
    } catch (reason: unknown) {
      if (!this.isCurrentSession(token)) {
        return;
      }

      this.clearSessionResources();
      this.updateState({
        sessionId,
        currentMixKey: request.key,
        status: "error",
        voiceCount: 0,
        error:
          reason instanceof Error ? reason : new Error(`Scene mixer failed: ${String(reason)}`),
      });
    }
  };

  readonly stop = async (): Promise<void> => {
    this.assertActive();
    this.sessionToken += 1;
    this.clearSessionResources();
    this.updateState({
      sessionId: this.state.sessionId,
      currentMixKey: null,
      status: "idle",
      voiceCount: 0,
      error: null,
    });

    if (this.audioContext !== null) {
      await this.audioContext.suspend();
    }
  };

  private state: SceneMixerState = INITIAL_STATE;
  private nextSessionId = 0;
  private sessionToken = 0;
  private disposed = false;
  private audioContext: AudioContext | null = null;
  private fetchController: AbortController | null = null;
  private masterGain: GainNode | null = null;
  private compressor: DynamicsCompressorNode | null = null;
  private readonly activeVoices = new Set<ActiveVoice>();
  private readonly restartTimers = new Set<TimerHandle>();
  private readonly listeners = new Set<StoreListener>();

  async dispose(): Promise<void> {
    if (this.disposed) {
      return;
    }

    this.disposed = true;
    this.sessionToken += 1;
    this.clearSessionResources();
    this.state = {
      sessionId: this.state.sessionId,
      currentMixKey: null,
      status: "idle",
      voiceCount: 0,
      error: null,
    };
    this.listeners.clear();

    const context = this.audioContext;
    this.audioContext = null;
    if (context !== null) {
      await context.close();
    }
  }

  private getOrCreateAudioContext(): AudioContext {
    if (this.audioContext === null) {
      this.audioContext = new AudioContext();
    }
    return this.audioContext;
  }

  private async loadTrack(
    context: AudioContext,
    track: SceneMixerTrack,
    signal: AbortSignal,
  ): Promise<AudioBuffer> {
    const response = await fetch(track.url, { signal });
    if (!response.ok) {
      throw new Error(`Scene mixer fetch failed for ${track.key}: HTTP ${response.status}`);
    }
    return context.decodeAudioData(await response.arrayBuffer());
  }

  private installOutputGraph(context: AudioContext): void {
    const masterGain = context.createGain();
    masterGain.gain.value = 1;
    const compressor = context.createDynamicsCompressor();
    compressor.threshold.value = -18;
    compressor.knee.value = 24;
    compressor.ratio.value = 8;
    compressor.attack.value = 0.003;
    compressor.release.value = 0.25;
    masterGain.connect(compressor);
    compressor.connect(context.destination);
    this.masterGain = masterGain;
    this.compressor = compressor;
  }

  private scheduleVoice(
    context: AudioContext,
    buffer: AudioBuffer,
    laneCount: number,
    startTime: number,
    token: number,
  ): void {
    const masterGain = this.masterGain;
    if (masterGain === null || !this.isCurrentSession(token)) {
      return;
    }

    const source = context.createBufferSource();
    const panner = context.createStereoPanner();
    const gain = context.createGain();
    source.buffer = buffer;
    panner.pan.value = (Math.random() * 2 - 1) * MAX_ABSOLUTE_PAN;
    const distanceGain =
      (MAX_MIX_GAIN / laneCount) * (MIN_DISTANCE_FACTOR + Math.random() * DISTANCE_FACTOR_RANGE);
    gain.gain.setValueAtTime(0, startTime);
    gain.gain.linearRampToValueAtTime(distanceGain, startTime + VOICE_FADE_IN_SECONDS);
    source.connect(panner);
    panner.connect(gain);
    gain.connect(masterGain);

    const activeVoice: ActiveVoice = { source, panner, gain };
    this.activeVoices.add(activeVoice);
    source.onended = () => {
      this.activeVoices.delete(activeVoice);
      source.disconnect();
      panner.disconnect();
      gain.disconnect();
      if (!this.isCurrentSession(token)) {
        return;
      }

      const delay = MIN_RESTART_DELAY_MS + Math.random() * RESTART_DELAY_RANGE_MS;
      const timer = setTimeout(() => {
        this.restartTimers.delete(timer);
        if (this.isCurrentSession(token)) {
          this.scheduleVoice(
            context,
            buffer,
            laneCount,
            context.currentTime + RESTART_START_LEAD_SECONDS,
            token,
          );
        }
      }, delay);
      this.restartTimers.add(timer);
    };
    source.start(startTime);
  }

  private clearSessionResources(): void {
    this.fetchController?.abort();
    this.fetchController = null;

    for (const timer of this.restartTimers) {
      clearTimeout(timer);
    }
    this.restartTimers.clear();

    for (const voice of this.activeVoices) {
      voice.source.onended = null;
      voice.source.stop();
      voice.source.disconnect();
      voice.panner.disconnect();
      voice.gain.disconnect();
    }
    this.activeVoices.clear();

    this.masterGain?.disconnect();
    this.compressor?.disconnect();
    this.masterGain = null;
    this.compressor = null;
  }

  private isCurrentSession(token: number): boolean {
    return !this.disposed && token === this.sessionToken;
  }

  private updateState(state: SceneMixerState): void {
    this.state = state;
    for (const listener of this.listeners) {
      listener();
    }
  }

  private assertActive(): void {
    if (this.disposed) {
      throw new Error("SceneMixerManager has been disposed.");
    }
  }

  private assertRequest(request: SceneMixRequest): void {
    if (request.key.length === 0) {
      throw new Error("Scene mix key must not be empty.");
    }
    if (request.tracks.length < MIN_LANES) {
      throw new Error("Scene mix candidate pool must contain at least 3 tracks.");
    }

    const trackKeys = new Set<string>();
    for (const track of request.tracks) {
      if (track.key.length === 0) {
        throw new Error("Scene mixer track key must not be empty.");
      }
      if (track.url.length === 0) {
        throw new Error("Scene mixer track URL must not be empty.");
      }
      if (trackKeys.has(track.key)) {
        throw new Error(`Scene mixer track key must be unique: ${track.key}`);
      }
      trackKeys.add(track.key);
    }
  }

  private selectTracks(tracks: SceneMixerTrack[]): SceneMixerTrack[] {
    const maximumLaneCount = Math.min(MAX_LANES, tracks.length);
    const laneCount = MIN_LANES + Math.floor(Math.random() * (maximumLaneCount - MIN_LANES + 1));
    const shuffled = [...tracks];
    for (let index = 0; index < laneCount; index += 1) {
      const selectedIndex = index + Math.floor(Math.random() * (shuffled.length - index));
      [shuffled[index], shuffled[selectedIndex]] = [shuffled[selectedIndex]!, shuffled[index]!];
    }
    return shuffled.slice(0, laneCount);
  }
}

export function createBrowserSceneMixerManager(): SceneMixerManager {
  return new SceneMixerManager();
}
