import { useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";

import { AudioPlayerContext, type AudioPlayer } from "./audio-context";
import { PlaybackManager } from "./playback-manager";

interface AudioProviderProps {
  children: ReactNode;
}

interface AudioProviderContentProps extends AudioProviderProps {
  manager: PlaybackManager;
}

function AudioProviderContent({ manager, children }: AudioProviderContentProps) {
  const state = useSyncExternalStore(manager.subscribe, manager.getSnapshot, manager.getSnapshot);
  const value = useMemo<AudioPlayer>(
    () => ({
      ...state,
      play: manager.play,
      toggle: manager.toggle,
      stop: manager.stop,
    }),
    [manager, state],
  );

  return <AudioPlayerContext.Provider value={value}>{children}</AudioPlayerContext.Provider>;
}

export function AudioProvider({ children }: AudioProviderProps) {
  const [manager, setManager] = useState<PlaybackManager | null>(null);

  useEffect(() => {
    const mountedManager = new PlaybackManager(new Audio());
    setManager(mountedManager);

    return () => {
      mountedManager.dispose();
    };
  }, []);

  if (manager === null) {
    return null;
  }

  return <AudioProviderContent manager={manager}>{children}</AudioProviderContent>;
}
