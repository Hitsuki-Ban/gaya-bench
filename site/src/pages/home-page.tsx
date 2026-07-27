import { AudioWaveform, Database, Gauge, Rows3 } from "lucide-react";
import { Link } from "react-router";

import { ClipButton } from "@/components/clip-button";
import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { benchmarkData, getClipsForScenario } from "@/data";

const scenariosWithClips = benchmarkData.scenarios.filter(
  (scenario) => getClipsForScenario(scenario.id).length > 0,
);

export function HomePage() {
  return (
    <div className="space-y-8">
      <PageIntro
        aside={
          <div className="grid min-w-64 grid-cols-3 gap-px overflow-hidden rounded-lg border bg-border">
            <Metric icon={Rows3} label="scenes" value={scenariosWithClips.length} />
            <Metric
              icon={AudioWaveform}
              label="clips"
              value={benchmarkData.manifest.clips.length}
            />
            <Metric icon={Database} label="models" value={benchmarkData.manifest.models.length} />
          </div>
        }
        description="同じセリフを同じ条件で聴き、モデル間の違いを短い操作で確認するためのベンチマークです。現在はサイト骨組みとローカル dummy 音声を表示しています。"
        eyebrow="Comparison matrix / scaffold"
        title="聴き比べの摩擦を、最小に。"
      />

      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">収録シナリオ</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            manifest にクリップがある 2 シナリオを表示
          </p>
        </div>
        <Badge variant="outline">
          <Gauge aria-hidden="true" data-icon="inline-start" />
          -18 LUFS / mono / 48kHz
        </Badge>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {scenariosWithClips.map((scenario) => {
          const clips = getClipsForScenario(scenario.id);
          return (
            <Card key={scenario.id}>
              <CardHeader className="border-b">
                <div>
                  <Badge variant="secondary">{scenario.locale.toUpperCase()}</Badge>
                  <CardTitle className="mt-3 text-xl">{scenario.title}</CardTitle>
                  <CardDescription className="mt-1">
                    {scenario.characters.length} characters · {scenario.lines.length} lines
                  </CardDescription>
                </div>
                <CardAction>
                  <Link
                    className="text-xs text-primary underline-offset-4 hover:underline"
                    to={`/scenario/${scenario.id}`}
                  >
                    詳細を見る
                  </Link>
                </CardAction>
              </CardHeader>
              <CardContent className="space-y-4">
                <p className="line-clamp-2 text-sm leading-6 text-muted-foreground">
                  {scenario.scene.setting}
                </p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {clips.slice(0, 2).map((clip) => (
                    <ClipButton clip={clip} key={`${clip.scenario}/${clip.line}/${clip.model}`} />
                  ))}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

interface MetricProps {
  icon: typeof Rows3;
  label: string;
  value: number;
}

function Metric({ icon: Icon, label, value }: MetricProps) {
  return (
    <div className="bg-card px-4 py-3 text-center">
      <Icon aria-hidden="true" className="mx-auto size-4 text-primary" />
      <p className="mt-2 font-mono text-lg text-foreground">{value}</p>
      <p className="font-mono text-[10px] tracking-wider text-muted-foreground uppercase">
        {label}
      </p>
    </div>
  );
}
