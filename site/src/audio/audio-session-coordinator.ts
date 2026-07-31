import type { PlaybackManager } from "./playback-manager";
import type { SceneMixerManager, SceneMixRequest } from "./scene-mixer-manager";

export class AudioSessionCoordinator {
  private disposed = false;
  private readonly playbackManager: PlaybackManager;
  private readonly sceneMixerManager: SceneMixerManager;
  private readonly unsubscribePlayback: () => void;

  constructor(playbackManager: PlaybackManager, sceneMixerManager: SceneMixerManager) {
    this.playbackManager = playbackManager;
    this.sceneMixerManager = sceneMixerManager;
    this.unsubscribePlayback = playbackManager.subscribe(this.handlePlaybackState);
    this.handlePlaybackState();
  }

  readonly startSceneMix = async (request: SceneMixRequest): Promise<void> => {
    this.assertActive();
    this.playbackManager.stop();
    await this.sceneMixerManager.start(request);
  };

  readonly stopSceneMix = async (): Promise<void> => {
    this.assertActive();
    await this.sceneMixerManager.stop();
  };

  dispose(): void {
    if (this.disposed) {
      return;
    }

    this.disposed = true;
    this.unsubscribePlayback();
  }

  private readonly handlePlaybackState = (): void => {
    const status = this.playbackManager.getSnapshot().status;
    if (status === "loading" || status === "playing") {
      void this.sceneMixerManager.stop();
    }
  };

  private assertActive(): void {
    if (this.disposed) {
      throw new Error("AudioSessionCoordinator has been disposed.");
    }
  }
}
