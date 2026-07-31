import { createContext } from "react";

import type { AudioSessionCoordinator } from "./audio-session-coordinator";
import type { AudioClip, AudioPlayerState, PlaybackManager } from "./playback-manager";
import type { SceneMixerManager } from "./scene-mixer-manager";

export interface AudioPlayer extends AudioPlayerState {
  play(clip: AudioClip): Promise<void>;
  toggle(clip: AudioClip): Promise<void>;
  stop(): void;
}

export interface SceneMixerRuntime {
  readonly coordinator: AudioSessionCoordinator;
  readonly manager: SceneMixerManager;
}

export const PlaybackManagerContext = createContext<PlaybackManager | null>(null);
export const SceneMixerContext = createContext<SceneMixerRuntime | null>(null);
