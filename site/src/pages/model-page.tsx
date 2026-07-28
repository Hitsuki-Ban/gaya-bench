import { Check, Gauge, Minus } from "lucide-react";
import { Link, useLocation, useParams } from "react-router";

import { ClipButton } from "@/components/clip-button";
import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { benchmarkData, modelById } from "@/data";
import {
  buildModelClipEntries,
  buildModelFailureEntries,
  calculateRtfStatistics,
  collectGenerationParameterSets,
} from "@/pages/detail-page-model";
import { NotFoundPage } from "@/pages/not-found-page";

const capabilityEntries = [
  ["emotion", "感情"],
  ["voice_prompt", "声質プロンプト"],
  ["clone", "クローン"],
  ["nonverbal", "非言語音"],
  ["reading", "読み指定"],
] as const;

export function ModelPage() {
  const { id } = useParams();
  const { search } = useLocation();
  const model = id ? modelById.get(id) : undefined;
  if (!model) {
    return <NotFoundPage />;
  }

  const clipEntries = buildModelClipEntries(
    model.id,
    benchmarkData.manifest.clips,
    benchmarkData.scenarios,
  );
  const clips = clipEntries.map(({ clip }) => clip);
  const failureEntries = buildModelFailureEntries(
    model.id,
    benchmarkData.manifest.failures,
    benchmarkData.scenarios,
  );
  const rtf = calculateRtfStatistics(clips);
  const parameterSets = collectGenerationParameterSets(clips);

  return (
    <div className="space-y-5">
      <PageIntro
        aside={
          <Link
            className="text-sm text-primary underline-offset-4 hover:underline"
            to={{ pathname: "/", search }}
          >
            比較へ戻る
          </Link>
        }
        description={`${model.version} · ${model.license_note}`}
        eyebrow={`Model / ${model.id}`}
        title={model.name}
      />

      <div className="grid gap-4 lg:grid-cols-[1.15fr_0.85fr]">
        <Card>
          <CardHeader>
            <CardTitle>対応機能</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 sm:grid-cols-2">
            {capabilityEntries.map(([key, label]) => {
              const supported = model.capabilities[key];
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
            <Row label="license" value={model.license_note} />
            <Row label="clips" value={String(clipEntries.length)} />
            <Row label="failures" value={String(failureEntries.length)} />
            <Row
              label="variants"
              value={
                clips.length === 0
                  ? failureEntries.length > 0
                    ? "成功なし"
                    : "未生成"
                  : [...new Set(clips.map(({ variant }) => variant))].join(", ")
              }
            />
          </CardContent>
        </Card>
      </div>

      <section aria-labelledby="rtf-heading" className="space-y-3">
        <SectionHeading id="rtf-heading" title="生成速度" />
        <Card>
          <CardContent className="grid gap-4 pt-1 sm:grid-cols-[auto_1fr] sm:items-center">
            <div className="flex items-center gap-3">
              <div className="rounded-md bg-primary/10 p-3 text-primary">
                <Gauge aria-hidden="true" className="size-6" />
              </div>
              <div>
                <p className="font-mono text-[10px] tracking-wider text-muted-foreground uppercase">
                  duration-weighted RTF
                </p>
                <p className="mt-1 font-mono text-2xl text-foreground">
                  {rtf ? rtf.weightedMean.toFixed(3) : "未計測"}
                </p>
              </div>
            </div>
            <div className="text-sm leading-6 text-muted-foreground sm:border-l sm:pl-4">
              {rtf ? (
                <>
                  音声時間で重み付けした平均です。範囲は{" "}
                  <span className="font-mono text-foreground">
                    {rtf.minimum.toFixed(3)}–{rtf.maximum.toFixed(3)}
                  </span>
                  。RTF 1.0 は、音声1秒の生成に1秒かかる速度を示します。
                </>
              ) : (
                "生成済みクリップがないため、RTF はまだ計測されていません。"
              )}
            </div>
          </CardContent>
        </Card>
      </section>

      <section aria-labelledby="parameters-heading" className="space-y-3">
        <SectionHeading id="parameters-heading" title="生成パラメータ" />
        {parameterSets.length > 0 ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {parameterSets.map(({ parameters, clipCount }, index) => (
              <Card key={JSON.stringify(parameters)} size="sm">
                <CardHeader>
                  <CardTitle className="flex items-center justify-between gap-3">
                    <span>設定 {index + 1}</span>
                    <Badge variant="outline">{clipCount} clips</Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <pre className="overflow-x-auto rounded-md bg-background p-3 font-mono text-xs leading-5 text-muted-foreground">
                    {JSON.stringify(parameters, null, 2)}
                  </pre>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
            生成済みクリップがないため、パラメータは記録されていません。
          </p>
        )}
      </section>

      <section aria-labelledby="clips-heading" className="space-y-3">
        <SectionHeading count={clipEntries.length} id="clips-heading" title="全生成クリップ" />
        {clipEntries.length > 0 ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {clipEntries.map(({ clip, scenario, line, character }) => (
              <Card key={`${clip.scenario}/${clip.line}/${clip.variant}`} size="sm">
                <CardHeader>
                  <div className="flex flex-wrap gap-2">
                    <Badge
                      render={
                        <Link
                          to={{
                            pathname: `/scenario/${scenario.id}`,
                            search,
                          }}
                        />
                      }
                      variant="secondary"
                    >
                      {scenario.title}
                    </Badge>
                    <Badge variant="outline">{character.name}</Badge>
                    <Badge variant="outline">{line.emotion}</Badge>
                    <Badge variant="outline">{clip.variant}</Badge>
                  </div>
                  <CardTitle className="mt-2 leading-7">{line.text}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <p className="text-xs leading-5 text-muted-foreground">{line.delivery}</p>
                  <ClipButton clip={clip} />
                  <div className="flex justify-between font-mono text-[10px] text-muted-foreground">
                    <span>RTF {clip.rtf.toFixed(3)}</span>
                    <span>{clip.duration_sec.toFixed(2)}s</span>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
            {failureEntries.length > 0
              ? "成功したクリップはありません。記録された生成失敗を下記に表示します。"
              : "このモデルのクリップは未生成です。"}
          </p>
        )}
      </section>

      <section aria-labelledby="failures-heading" className="space-y-3">
        <SectionHeading count={failureEntries.length} id="failures-heading" title="生成失敗" />
        {failureEntries.length > 0 ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {failureEntries.map(({ failure, scenario, line, character }) => (
              <Card key={`${failure.scenario}/${failure.line}/${failure.variant}`} size="sm">
                <CardHeader>
                  <div className="flex flex-wrap gap-2">
                    <Badge
                      render={
                        <Link
                          to={{
                            pathname: `/scenario/${scenario.id}`,
                            search,
                          }}
                        />
                      }
                      variant="secondary"
                    >
                      {scenario.title}
                    </Badge>
                    <Badge variant="outline">{character.name}</Badge>
                    <Badge variant="outline">{failure.variant}</Badge>
                    <Badge variant="destructive">再生成待ち</Badge>
                  </div>
                  <CardTitle className="mt-2 leading-7">{line.text}</CardTitle>
                </CardHeader>
                <CardContent className="text-xs leading-5 text-muted-foreground">
                  {model.name} の生成は完了しませんでした。この結果は再生・連続再生・A/B
                  比較の対象外です。
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
            記録された生成失敗はありません。
          </p>
        )}
      </section>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[6rem_1fr] gap-3 border-b pb-2 last:border-0">
      <span className="font-mono text-xs text-muted-foreground">{label}</span>
      <span className="text-right text-xs break-words">{value}</span>
    </div>
  );
}

function SectionHeading({ count, id, title }: { count?: number; id: string; title: string }) {
  return (
    <div className="flex items-end justify-between gap-3 border-b pb-2">
      <h2 className="text-lg font-semibold" id={id}>
        {title}
      </h2>
      {count === undefined ? null : (
        <span className="font-mono text-xs text-muted-foreground">{count}</span>
      )}
    </div>
  );
}
