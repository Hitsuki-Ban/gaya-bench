import { useContext } from "react";

import { AudioPlayerContext, type AudioPlayer } from "./audio-context";

export function useAudioPlayer(): AudioPlayer {
  const player = useContext(AudioPlayerContext);
  if (player === null) {
    throw new Error("useAudioPlayer must be used within AudioProvider.");
  }
  return player;
}
