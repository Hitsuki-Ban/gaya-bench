import { useSyncExternalStore } from "react";

import type { AudioProgress } from "./playback-manager";
import { usePlaybackManager } from "./use-audio-player";

export function useAudioProgress(): AudioProgress {
  const manager = usePlaybackManager();
  return useSyncExternalStore(
    manager.subscribeProgress,
    manager.getProgressSnapshot,
    manager.getProgressSnapshot,
  );
}
