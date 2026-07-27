import { createContext } from "react";

import type { AudioClip, AudioPlayerState, PlaybackManager } from "./playback-manager";

export interface AudioPlayer extends AudioPlayerState {
  play(clip: AudioClip): Promise<void>;
  toggle(clip: AudioClip): Promise<void>;
  stop(): void;
}

export const PlaybackManagerContext = createContext<PlaybackManager | null>(null);
