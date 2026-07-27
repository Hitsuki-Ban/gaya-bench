import { Check, Minus } from "lucide-react";
import { Link, useParams } from "react-router";

import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { benchmarkData, modelById } from "@/data";
import { NotFoundPage } from "@/pages/not-found-page";

const capabilityLabels = {
  emotion: "感情",
  voice_prompt: "声質プロンプト",
  clone: "クローン",
  nonverbal: "非言語音",
  reading: "読み",
} as const;

export function ModelPage() {
  const { id } = useParams();
  const model = id ? modelById.get(id) : undefined;
  if (!model) {
    return <NotFoundPage />;
  }
  const clips = benchmarkData.manifest.clips.filter((clip) => clip.model === model.id);

  return (
    <div className="space-y-7">
      <PageIntro
        aside={
          <Link className="text-sm text-primary underline-offset-4 hover:underline" to="/">
            比較へ戻る
          </Link>
        }
        description={model.license_note}
        eyebrow={`Model / ${model.id}`}
        title={model.name}
      />
      <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
        <Card>
          <CardHeader>
            <CardTitle>Capability profile</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 sm:grid-cols-2">
            {Object.entries(capabilityLabels).map(([key, label]) => {
              const supported = model.capabilities[key as keyof typeof model.capabilities];
              return (
                <div
                  className="flex items-center justify-between rounded-md border px-3 py-2"
                  key={key}
                >
                  <span className="text-sm">{label}</span>
                  <Badge variant={supported ? "default" : "outline"}>
                    {supported ? <Check aria-hidden="true" /> : <Minus aria-hidden="true" />}
                    {supported ? "対応" : "非対応"}
                  </Badge>
                </div>
              );
            })}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>生成情報</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <Row label="version" value={model.version} />
            <Row label="clips" value={String(clips.length)} />
            <Row label="variant" value={clips[0]?.variant ?? "—"} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between border-b pb-2 last:border-0">
      <span className="font-mono text-xs text-muted-foreground">{label}</span>
      <span className="font-mono text-xs">{value}</span>
    </div>
  );
}
