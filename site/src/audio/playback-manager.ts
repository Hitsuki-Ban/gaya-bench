export type PlaybackStatus = "idle" | "loading" | "playing" | "paused" | "error";
export type PlaybackTermination = "ended" | "error" | "stopped";

export interface AudioClip {
  key: string;
  url: string;
}

export interface PlaybackCompletion {
  sessionId: number;
  clipKey: string;
  termination: PlaybackTermination;
}

export interface AudioPlayerState {
  sessionId: number;
  currentClipKey: string | null;
  status: PlaybackStatus;
  error: Error | null;
  completion: PlaybackCompletion | null;
}

export interface AudioProgress {
  currentTime: number;
  duration: number;
}

type AudioEventType = "ended" | "error" | "timeupdate" | "durationchange";
type AudioEventListener = () => void;
type StoreListener = () => void;

export interface AudioLike {
  src: string;
  currentTime: number;
  readonly duration: number;
  readonly error: { readonly message: string } | null;
  play(): Promise<void>;
  pause(): void;
  load(): void;
  removeAttribute(name: "src"): void;
  addEventListener(type: AudioEventType, listener: AudioEventListener): void;
  removeEventListener(type: AudioEventType, listener: AudioEventListener): void;
}

const INITIAL_STATE: AudioPlayerState = {
  sessionId: 0,
  currentClipKey: null,
  status: "idle",
  error: null,
  completion: null,
};

const EMPTY_PROGRESS: AudioProgress = {
  currentTime: 0,
  duration: 0,
};

interface SessionListeners {
  ended: AudioEventListener;
  error: AudioEventListener;
  timeupdate: AudioEventListener;
  durationchange: AudioEventListener;
}

export class PlaybackManager {
  readonly getSnapshot = (): AudioPlayerState => this.state;

  readonly getProgressSnapshot = (): AudioProgress => this.progress;

  readonly subscribe = (listener: StoreListener): (() => void) => {
    this.assertActive();
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly subscribeProgress = (listener: StoreListener): (() => void) => {
    this.assertActive();
    this.progressListeners.add(listener);
    return () => {
      this.progressListeners.delete(listener);
    };
  };

  readonly play = async (clip: AudioClip): Promise<void> => {
    this.assertActive();
    this.assertClip(clip);

    const isCurrentClip = this.isSameClip(this.currentClip, clip);
    if (isCurrentClip && (this.state.status === "playing" || this.state.status === "loading")) {
      return;
    }

    if (isCurrentClip && this.state.status === "paused") {
      await this.startPlayback(clip, false, this.state.sessionId);
      return;
    }

    if (this.currentClip !== null) {
      this.terminateCurrent("stopped", null);
    }

    this.nextSessionId += 1;
    await this.startPlayback(clip, true, this.nextSessionId);
  };

  readonly toggle = async (clip: AudioClip): Promise<void> => {
    this.assertActive();
    this.assertClip(clip);

    const isCurrentClip = this.isSameClip(this.currentClip, clip);
    if (isCurrentClip && (this.state.status === "playing" || this.state.status === "loading")) {
      this.requestToken += 1;
      this.removeSessionListeners();
      this.audio.pause();
      this.updateState({
        sessionId: this.state.sessionId,
        currentClipKey: clip.key,
        status: "paused",
        error: null,
        completion: null,
      });
      return;
    }

    await this.play(clip);
  };

  readonly stop = (): void => {
    this.assertActive();

    if (this.currentClip !== null) {
      this.terminateCurrent("stopped", null);
      return;
    }

    this.requestToken += 1;
    this.removeSessionListeners();
    this.clearMedia();
    this.updateProgress(EMPTY_PROGRESS);
    this.updateState({
      sessionId: this.state.sessionId,
      currentClipKey: null,
      status: "idle",
      error: null,
      completion: this.state.completion,
    });
  };

  private state: AudioPlayerState = INITIAL_STATE;
  private progress: AudioProgress = EMPTY_PROGRESS;
  private currentClip: AudioClip | null = null;
  private requestToken = 0;
  private nextSessionId = 0;
  private disposed = false;
  private sessionListeners: SessionListeners | null = null;
  private readonly audio: AudioLike;
  private readonly listeners = new Set<StoreListener>();
  private readonly progressListeners = new Set<StoreListener>();

  constructor(audio: AudioLike) {
    this.audio = audio;
  }

  dispose(): void {
    if (this.disposed) {
      return;
    }

    const completion =
      this.currentClip === null
        ? this.state.completion
        : {
            sessionId: this.state.sessionId,
            clipKey: this.currentClip.key,
            termination: "stopped" as const,
          };

    this.disposed = true;
    this.requestToken += 1;
    this.currentClip = null;
    this.removeSessionListeners();
    this.clearMedia();
    this.state = {
      sessionId: this.state.sessionId,
      currentClipKey: null,
      status: "idle",
      error: null,
      completion,
    };
    this.progress = EMPTY_PROGRESS;
    this.listeners.clear();
    this.progressListeners.clear();
  }

  private async startPlayback(
    clip: AudioClip,
    loadClip: boolean,
    sessionId: number,
  ): Promise<void> {
    const token = this.requestToken + 1;
    this.requestToken = token;
    this.currentClip = clip;
    this.installSessionListeners(token, sessionId, clip);
    this.updateState({
      sessionId,
      currentClipKey: clip.key,
      status: "loading",
      error: null,
      completion: null,
    });

    if (loadClip) {
      this.audio.pause();
      this.updateProgress(EMPTY_PROGRESS);
      this.audio.src = clip.url;
      this.audio.load();
    }

    try {
      await this.audio.play();
    } catch (reason: unknown) {
      if (this.isCurrentRequest(token, sessionId, clip)) {
        const error =
          reason instanceof Error ? reason : new Error(`Audio playback failed: ${String(reason)}`);
        this.terminateCurrent("error", error);
      }
      return;
    }

    if (this.isCurrentRequest(token, sessionId, clip)) {
      this.updateState({
        sessionId,
        currentClipKey: clip.key,
        status: "playing",
        error: null,
        completion: null,
      });
    }
  }

  private installSessionListeners(token: number, sessionId: number, clip: AudioClip): void {
    this.removeSessionListeners();

    const listeners: SessionListeners = {
      ended: () => {
        if (this.isCurrentRequest(token, sessionId, clip)) {
          this.terminateCurrent("ended", null);
        }
      },
      error: () => {
        if (!this.isCurrentRequest(token, sessionId, clip)) {
          return;
        }

        const message = this.audio.error?.message;
        const error =
          message === undefined || message.length === 0
            ? new Error("Audio element reported an error.")
            : new Error(message);
        this.terminateCurrent("error", error);
      },
      timeupdate: () => {
        if (this.isCurrentRequest(token, sessionId, clip)) {
          this.readProgress();
        }
      },
      durationchange: () => {
        if (this.isCurrentRequest(token, sessionId, clip)) {
          this.readProgress();
        }
      },
    };

    this.sessionListeners = listeners;
    this.audio.addEventListener("ended", listeners.ended);
    this.audio.addEventListener("error", listeners.error);
    this.audio.addEventListener("timeupdate", listeners.timeupdate);
    this.audio.addEventListener("durationchange", listeners.durationchange);
  }

  private removeSessionListeners(): void {
    if (this.sessionListeners === null) {
      return;
    }

    this.audio.removeEventListener("ended", this.sessionListeners.ended);
    this.audio.removeEventListener("error", this.sessionListeners.error);
    this.audio.removeEventListener("timeupdate", this.sessionListeners.timeupdate);
    this.audio.removeEventListener("durationchange", this.sessionListeners.durationchange);
    this.sessionListeners = null;
  }

  private terminateCurrent(termination: PlaybackTermination, error: Error | null): void {
    const clip = this.currentClip;
    if (clip === null) {
      return;
    }

    const sessionId = this.state.sessionId;
    this.requestToken += 1;
    this.currentClip = null;
    this.removeSessionListeners();
    this.clearMedia();
    this.updateProgress(EMPTY_PROGRESS);
    this.updateState({
      sessionId,
      currentClipKey: termination === "error" ? clip.key : null,
      status: termination === "error" ? "error" : "idle",
      error,
      completion: {
        sessionId,
        clipKey: clip.key,
        termination,
      },
    });
  }

  private isCurrentRequest(token: number, sessionId: number, clip: AudioClip): boolean {
    return (
      !this.disposed &&
      token === this.requestToken &&
      sessionId === this.state.sessionId &&
      this.isSameClip(this.currentClip, clip)
    );
  }

  private isSameClip(left: AudioClip | null, right: AudioClip): boolean {
    return left?.key === right.key && left.url === right.url;
  }

  private readProgress(): void {
    this.updateProgress({
      currentTime: this.normalizeMediaTime(this.audio.currentTime),
      duration: this.normalizeMediaTime(this.audio.duration),
    });
  }

  private normalizeMediaTime(value: number): number {
    return Number.isFinite(value) && value >= 0 ? value : 0;
  }

  private clearMedia(): void {
    this.audio.pause();
    this.audio.removeAttribute("src");
    this.audio.load();
  }

  private updateState(state: AudioPlayerState): void {
    this.state = state;
    for (const listener of this.listeners) {
      listener();
    }
  }

  private updateProgress(progress: AudioProgress): void {
    if (
      progress.currentTime === this.progress.currentTime &&
      progress.duration === this.progress.duration
    ) {
      return;
    }

    this.progress = progress;
    for (const listener of this.progressListeners) {
      listener();
    }
  }

  private assertActive(): void {
    if (this.disposed) {
      throw new Error("PlaybackManager has been disposed.");
    }
  }

  private assertClip(clip: AudioClip): void {
    if (clip.key.length === 0) {
      throw new Error("Audio clip key must not be empty.");
    }
    if (clip.url.length === 0) {
      throw new Error("Audio clip URL must not be empty.");
    }
  }
}
