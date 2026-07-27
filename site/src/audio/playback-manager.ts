export type PlaybackStatus = "idle" | "loading" | "playing" | "paused" | "error";

export interface AudioClip {
  key: string;
  url: string;
}

export interface AudioPlayerState {
  currentClipKey: string | null;
  status: PlaybackStatus;
  error: Error | null;
}

type AudioEventType = "ended" | "error";
type AudioEventListener = () => void;
type StateListener = () => void;

export interface AudioLike {
  src: string;
  readonly error: { readonly message: string } | null;
  play(): Promise<void>;
  pause(): void;
  load(): void;
  removeAttribute(name: "src"): void;
  addEventListener(type: AudioEventType, listener: AudioEventListener): void;
  removeEventListener(type: AudioEventType, listener: AudioEventListener): void;
}

const IDLE_STATE: AudioPlayerState = {
  currentClipKey: null,
  status: "idle",
  error: null,
};

export class PlaybackManager {
  readonly getSnapshot = (): AudioPlayerState => this.state;

  readonly subscribe = (listener: StateListener): (() => void) => {
    this.assertActive();
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly play = async (clip: AudioClip): Promise<void> => {
    this.assertActive();
    this.assertClip(clip);

    const isCurrentClip = this.currentClip?.key === clip.key && this.currentClip.url === clip.url;

    if (isCurrentClip && (this.state.status === "playing" || this.state.status === "loading")) {
      return;
    }

    await this.startPlayback(clip, !isCurrentClip || this.state.status === "error");
  };

  readonly toggle = async (clip: AudioClip): Promise<void> => {
    this.assertActive();
    this.assertClip(clip);

    const isCurrentClip = this.currentClip?.key === clip.key && this.currentClip.url === clip.url;

    if (isCurrentClip && (this.state.status === "playing" || this.state.status === "loading")) {
      this.requestToken += 1;
      this.audio.pause();
      this.updateState({
        currentClipKey: clip.key,
        status: "paused",
        error: null,
      });
      return;
    }

    await this.play(clip);
  };

  readonly stop = (): void => {
    this.assertActive();
    this.requestToken += 1;
    this.currentClip = null;
    this.clearMedia();
    this.updateState(IDLE_STATE);
  };

  private state: AudioPlayerState = IDLE_STATE;
  private currentClip: AudioClip | null = null;
  private requestToken = 0;
  private disposed = false;
  private readonly audio: AudioLike;
  private readonly listeners = new Set<StateListener>();

  private readonly handleEnded = (): void => {
    if (this.disposed || this.currentClip === null) {
      return;
    }

    this.requestToken += 1;
    this.currentClip = null;
    this.clearMedia();
    this.updateState(IDLE_STATE);
  };

  private readonly handleAudioError = (): void => {
    if (this.disposed || this.currentClip === null) {
      return;
    }

    const message = this.audio.error?.message;
    const error =
      message === undefined || message.length === 0
        ? new Error("Audio element reported an error.")
        : new Error(message);
    this.failCurrentPlayback(error);
  };

  constructor(audio: AudioLike) {
    this.audio = audio;
    audio.addEventListener("ended", this.handleEnded);
    audio.addEventListener("error", this.handleAudioError);
  }

  dispose(): void {
    if (this.disposed) {
      return;
    }

    this.disposed = true;
    this.requestToken += 1;
    this.currentClip = null;
    this.audio.removeEventListener("ended", this.handleEnded);
    this.audio.removeEventListener("error", this.handleAudioError);
    this.clearMedia();
    this.state = IDLE_STATE;
    this.listeners.clear();
  }

  private async startPlayback(clip: AudioClip, loadClip: boolean): Promise<void> {
    const token = this.requestToken + 1;
    this.requestToken = token;

    if (loadClip) {
      this.audio.pause();
      this.audio.src = clip.url;
      this.audio.load();
    }

    this.currentClip = clip;
    this.updateState({
      currentClipKey: clip.key,
      status: "loading",
      error: null,
    });

    try {
      await this.audio.play();
    } catch (reason: unknown) {
      if (this.isCurrentRequest(token, clip)) {
        const error =
          reason instanceof Error ? reason : new Error(`Audio playback failed: ${String(reason)}`);
        this.failCurrentPlayback(error);
      }
      return;
    }

    if (this.isCurrentRequest(token, clip)) {
      this.updateState({
        currentClipKey: clip.key,
        status: "playing",
        error: null,
      });
    }
  }

  private failCurrentPlayback(error: Error): void {
    const failedClip = this.currentClip;
    if (failedClip === null) {
      return;
    }

    this.requestToken += 1;
    this.currentClip = null;
    this.clearMedia();
    this.currentClip = failedClip;
    this.updateState({
      currentClipKey: failedClip.key,
      status: "error",
      error,
    });
  }

  private isCurrentRequest(token: number, clip: AudioClip): boolean {
    return (
      !this.disposed &&
      token === this.requestToken &&
      this.currentClip?.key === clip.key &&
      this.currentClip.url === clip.url
    );
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
