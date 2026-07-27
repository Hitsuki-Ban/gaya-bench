import { useContext, useMemo, useSyncExternalStore } from "react";

import { PlaybackManagerContext, type AudioPlayer } from "./audio-context";
import type { PlaybackManager } from "./playback-manager";

export function usePlaybackManager(): PlaybackManager {
  const manager = useContext(PlaybackManagerContext);
  if (manager === null) {
    throw new Error("Audio hooks must be used within AudioProvider.");
  }
  return manager;
}

export function useAudioPlayer(): AudioPlayer {
  const manager = usePlaybackManager();
  const state = useSyncExternalStore(manager.subscribe, manager.getSnapshot, manager.getSnapshot);

  return useMemo(
    () => ({
      ...state,
      play: manager.play,
      toggle: manager.toggle,
      stop: manager.stop,
    }),
    [manager, state],
  );
}
