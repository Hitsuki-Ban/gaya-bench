import { Pause, Play, TriangleAlert } from "lucide-react";

import { useAudioPlayer } from "@/audio/audio-provider";
import { Button } from "@/components/ui/button";
import { clipKey, lineByKey, modelById, scenarioById, type Clip } from "@/data";
import { resolveAudioUrl } from "@/lib/audio-url";

interface ClipButtonProps {
  clip: Clip;
}

export function ClipButton({ clip }: ClipButtonProps) {
  const player = useAudioPlayer();
  const key = clipKey(clip);
  const isCurrent = player.currentClipKey === key;
  const isPlaying = isCurrent && (player.status === "playing" || player.status === "loading");
  const line = lineByKey.get(`${clip.scenario}/${clip.line}`)!;
  const scenario = scenarioById.get(clip.scenario)!;
  const character = scenario.characters.find((item) => item.id === line.character)!;
  const model = modelById.get(clip.model)!;

  return (
    <div className="space-y-2">
      <Button
        aria-label={`${character.name}「${line.text}」${model.name} を${isPlaying ? "停止" : "再生"}`}
        className="w-full justify-between font-mono"
        onClick={() => void player.toggle({ key, url: resolveAudioUrl(clip.path) })}
        variant={isCurrent ? "default" : "outline"}
      >
        <span className="flex items-center gap-2">
          {isPlaying ? (
            <Pause aria-hidden="true" className="size-3.5" />
          ) : (
            <Play aria-hidden="true" className="size-3.5" />
          )}
          {model.name}
        </span>
        <span>{clip.duration_sec.toFixed(2)}s</span>
      </Button>
      {isCurrent && player.status === "error" ? (
        <p className="flex items-center gap-2 text-xs text-destructive" role="alert">
          <TriangleAlert aria-hidden="true" className="size-3.5" />
          {player.error?.message ?? "音声を再生できません。"}
        </p>
      ) : null}
    </div>
  );
}
