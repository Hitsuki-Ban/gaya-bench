import { LoaderCircle, Pause, Play, RadioTower, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useSceneMixer } from "@/audio/audio-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { candidateKey, playableModels, type ArtifactOutcome, type Scenario } from "@/data";
import { resolveAudioUrl } from "@/lib/audio-url";

import { buildSceneMixerOptions } from "./scene-mixer-model";

interface SceneMixerPanelProps {
  readonly outcomes: readonly ArtifactOutcome[];
  readonly scenario: Scenario;
}

export function SceneMixerPanel({ outcomes, scenario }: SceneMixerPanelProps) {
  const options = useMemo(
    () => buildSceneMixerOptions(scenario, outcomes, playableModels),
    [outcomes, scenario],
  );
  const firstOption = options[0];
  if (!firstOption) {
    throw new Error(`scene mixer option がありません: ${scenario.id}`);
  }
  const [selectedModelId, setSelectedModelId] = useState(firstOption.model.id);
  const selectedOption = options.find(({ model }) => model.id === selectedModelId);
  if (!selectedOption) {
    throw new Error(`scene mixer model が現在の scene に存在しません: ${selectedModelId}`);
  }

  const mixer = useSceneMixer();
  const { start, stop } = mixer;
  const tracks = useMemo(
    () =>
      selectedOption.candidates.map((candidate) => ({
        key: candidateKey(candidate),
        url: resolveAudioUrl(candidate.path),
      })),
    [selectedOption],
  );
  const isCurrent = mixer.currentMixKey === selectedOption.key;
  const isLoading = isCurrent && mixer.status === "loading";
  const isPlaying = isCurrent && mixer.status === "playing";
  const isActive = isLoading || isPlaying;

  useEffect(() => {
    return () => {
      void stop();
    };
  }, [scenario.id, stop]);

  const toggle = () => {
    if (isActive) {
      void stop();
      return;
    }
    void start({ key: selectedOption.key, tracks });
  };

  return (
    <section aria-labelledby="scene-mixer-heading">
      <Card className="overflow-hidden border-primary/35 bg-primary/5">
        <CardHeader className="gap-3 border-b border-primary/20">
          <div className="flex flex-wrap items-center gap-2">
            <Badge>ガヤ用途</Badge>
            <Badge variant="outline">Web Audio</Badge>
            <Badge variant="secondary">{selectedOption.candidates.length} clips</Badge>
          </div>
          <div className="flex items-start gap-3">
            <span className="grid size-10 shrink-0 place-items-center rounded-md bg-primary/12 text-primary">
              <RadioTower aria-hidden="true" className="size-5" />
            </span>
            <div>
              <h2 className="text-base leading-snug font-medium" id="scene-mixer-heading">
                シーンの喧騒を聴く
              </h2>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">
                同じモデルの3〜6声を、位置と距離、発話間隔を変えながら重ねて再生します。
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 pt-4">
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
            <label className="grid gap-1.5 text-sm font-medium" htmlFor="scene-mixer-model">
              比較するモデル
              <select
                className="min-h-10 w-full rounded-md border border-input bg-background px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                id="scene-mixer-model"
                onChange={(event) => {
                  void stop();
                  setSelectedModelId(event.target.value);
                }}
                value={selectedModelId}
              >
                {options.map(({ model }) => (
                  <option key={model.id} value={model.id}>
                    {model.name}
                  </option>
                ))}
              </select>
            </label>
            <Button
              aria-label={`${selectedOption.model.name} のシーン喧騒を${isActive ? "停止" : "再生"}`}
              className="w-full sm:min-w-44"
              onClick={toggle}
              variant={isActive ? "outline" : "default"}
            >
              {isLoading ? (
                <LoaderCircle
                  aria-hidden="true"
                  className="animate-spin motion-reduce:animate-none"
                />
              ) : isPlaying ? (
                <Pause aria-hidden="true" />
              ) : (
                <Play aria-hidden="true" />
              )}
              {isLoading ? "準備を中止" : isPlaying ? "再生を停止" : "シーンを再生"}
            </Button>
          </div>

          <div className="rounded-md border bg-background/70 p-3 text-xs leading-5 text-muted-foreground">
            <p className="font-medium text-foreground">聴きどころ</p>
            <p className="mt-1">
              単独の美しさではなく、声を重ねたときの聞き分けやすさ、声質の偏り、発音の崩れ、
              耳障りな重なりを確認してください。配置は再生ごとに変わります。
            </p>
          </div>

          <p aria-live="polite" className="text-xs text-muted-foreground">
            {isLoading
              ? `${mixer.voiceCount}声を準備中…`
              : isPlaying
                ? `${mixer.voiceCount}声で再生中 · 単独試聴を始めると自動停止します。`
                : "停止中 · 再生はこのページを離れたとき、または画面を隠したときにも止まります。"}
          </p>
          {isCurrent && mixer.status === "error" ? (
            <p className="flex items-start gap-2 text-xs text-destructive" role="alert">
              <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {mixer.error?.message ?? "シーンの音声を再生できません。"}
            </p>
          ) : null}
        </CardContent>
      </Card>
    </section>
  );
}
