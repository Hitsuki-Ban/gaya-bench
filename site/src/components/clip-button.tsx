import { Pause, Play, TriangleAlert } from "lucide-react";

import { useAudioPlayer, useAudioProgress } from "@/audio/audio-provider";
import { AutomaticQualityBadge } from "@/components/automatic-quality-badge";
import { Button } from "@/components/ui/button";
import { candidateKey, lineByKey, modelById, scenarioById, type Candidate } from "@/data";
import { resolveAudioUrl } from "@/lib/audio-url";

interface ClipButtonProps {
  candidate: Candidate;
}

export function ClipButton({ candidate }: ClipButtonProps) {
  const player = useAudioPlayer();
  const key = candidateKey(candidate);
  const isCurrent = player.currentClipKey === key;
  const isPlaying = isCurrent && (player.status === "playing" || player.status === "loading");
  const line = lineByKey.get(`${candidate.scenario}/${candidate.line}`);
  const scenario = scenarioById.get(candidate.scenario);
  const model = modelById.get(candidate.model);
  if (!line || !scenario || !model) {
    throw new Error(
      `selected candidate の参照を解決できません: ${candidate.model}/${candidate.scenario}/${candidate.line}`,
    );
  }
  const character = scenario.characters.find((item) => item.id === line.character);
  if (!character) {
    throw new Error(
      `selected candidate の character を解決できません: ${candidate.scenario}/${candidate.line}`,
    );
  }

  return (
    <div className="space-y-2">
      <Button
        aria-label={`${character.name}「${line.text}」${model.name} を${isPlaying ? "停止" : "再生"}`}
        className="relative w-full justify-between overflow-hidden font-mono"
        onClick={() => void player.toggle({ key, url: resolveAudioUrl(candidate.path) })}
        variant={isCurrent ? "default" : "outline"}
      >
        <span className="relative z-10 flex items-center gap-2">
          {isPlaying ? (
            <Pause aria-hidden="true" className="size-3.5" />
          ) : (
            <Play aria-hidden="true" className="size-3.5" />
          )}
          {model.name}
          <AutomaticQualityBadge candidate={candidate} compact />
        </span>
        {isCurrent ? (
          <ClipProgress fallbackDuration={candidate.duration_sec} />
        ) : (
          <span className="relative z-10">{candidate.duration_sec.toFixed(2)}s</span>
        )}
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

function ClipProgress({ fallbackDuration }: { fallbackDuration: number }) {
  const progress = useAudioProgress();
  const duration = progress.duration > 0 ? progress.duration : fallbackDuration;
  const ratio = duration > 0 ? Math.min(progress.currentTime / duration, 1) : 0;

  return (
    <>
      <span className="relative z-10">
        {progress.currentTime.toFixed(1)} / {duration.toFixed(1)}s
      </span>
      <span
        aria-hidden="true"
        className="gaya-progress absolute inset-y-0 left-0 bg-primary-foreground/10 transition-[width] duration-150"
        style={{ width: `${ratio * 100}%` }}
      />
    </>
  );
}
