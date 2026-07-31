import { useEffect, useState, type ReactNode } from "react";

import { AudioSessionCoordinator } from "./audio-session-coordinator";
import { PlaybackManagerContext, SceneMixerContext, type SceneMixerRuntime } from "./audio-context";
import { PlaybackManager } from "./playback-manager";
import { createBrowserSceneMixerManager } from "./scene-mixer-manager";

interface AudioProviderProps {
  children: ReactNode;
  fallback: ReactNode;
}

export function AudioProvider({ children, fallback }: AudioProviderProps) {
  const [runtime, setRuntime] = useState<
    (SceneMixerRuntime & { readonly playbackManager: PlaybackManager }) | null
  >(null);

  useEffect(() => {
    const playbackManager = new PlaybackManager(new Audio());
    const manager = createBrowserSceneMixerManager();
    const coordinator = new AudioSessionCoordinator(playbackManager, manager);
    const stopWhenHidden = () => {
      if (document.hidden) {
        void coordinator.stopSceneMix();
      }
    };
    document.addEventListener("visibilitychange", stopWhenHidden);
    setRuntime({ coordinator, manager, playbackManager });

    return () => {
      document.removeEventListener("visibilitychange", stopWhenHidden);
      coordinator.dispose();
      void manager.dispose();
      playbackManager.dispose();
    };
  }, []);

  if (runtime === null) {
    return fallback;
  }

  return (
    <PlaybackManagerContext.Provider value={runtime.playbackManager}>
      <SceneMixerContext.Provider value={runtime}>{children}</SceneMixerContext.Provider>
    </PlaybackManagerContext.Provider>
  );
}
