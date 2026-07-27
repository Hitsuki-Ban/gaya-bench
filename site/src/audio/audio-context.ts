import { createContext } from "react";

import type { AudioClip, AudioPlayerState } from "./playback-manager";

export interface AudioPlayer extends AudioPlayerState {
  play(clip: AudioClip): Promise<void>;
  toggle(clip: AudioClip): Promise<void>;
  stop(): void;
}

export const AudioPlayerContext = createContext<AudioPlayer | null>(null);
