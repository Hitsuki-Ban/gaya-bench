import { useEffect, useState, type ReactNode } from "react";

import { PlaybackManagerContext } from "./audio-context";
import { PlaybackManager } from "./playback-manager";

interface AudioProviderProps {
  children: ReactNode;
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

  return (
    <PlaybackManagerContext.Provider value={manager}>{children}</PlaybackManagerContext.Provider>
  );
}
