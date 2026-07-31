import { useContext, useMemo, useSyncExternalStore } from "react";

import { SceneMixerContext } from "./audio-context";
import type { SceneMixerState, SceneMixRequest } from "./scene-mixer-manager";

export interface SceneMixerController extends SceneMixerState {
  readonly start: (request: SceneMixRequest) => Promise<void>;
  readonly stop: () => Promise<void>;
}

export function useSceneMixer(): SceneMixerController {
  const runtime = useContext(SceneMixerContext);
  if (runtime === null) {
    throw new Error("Scene mixer hooks must be used within AudioProvider.");
  }
  const state = useSyncExternalStore(
    runtime.manager.subscribe,
    runtime.manager.getSnapshot,
    runtime.manager.getSnapshot,
  );

  return useMemo(
    () => ({
      ...state,
      start: runtime.coordinator.startSceneMix,
      stop: runtime.coordinator.stopSceneMix,
    }),
    [runtime, state],
  );
}
