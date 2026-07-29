import { Check, Gauge, Minus } from "lucide-react";
import { Link, useLocation, useParams } from "react-router";

import { ClipButton } from "@/components/clip-button";
import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { benchmarkData, modelById } from "@/data";
import {
  buildModelCandidateEntries,
  buildModelOutcomeEntries,
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

  const candidateEntries = buildModelCandidateEntries(
    model.id,
    benchmarkData.outcomes,
    benchmarkData.scenarios,
  );
  const candidates = candidateEntries.map(({ candidate }) => candidate);
  const nonSelectedEntries = buildModelOutcomeEntries(
    model.id,
    benchmarkData.outcomes,
    benchmarkData.scenarios,
  );
  const rtf = calculateRtfStatistics(candidates);
  const parameterSets = collectGenerationParameterSets(candidates);

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
            <Row label="selected" value={String(candidateEntries.length)} />
            <Row label="non-selected" value={String(nonSelectedEntries.length)} />
            <Row
              label="variants"
              value={[...new Set(candidates.map(({ variant }) => variant))].join(", ")}
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
            {parameterSets.map(({ parameters, candidateCount }, index) => (
              <Card key={JSON.stringify(parameters)} size="sm">
                <CardHeader>
                  <CardTitle className="flex items-center justify-between gap-3">
                    <span>設定 {index + 1}</span>
                    <Badge variant="outline">{candidateCount} selected</Badge>
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

      <section aria-labelledby="selected-heading" className="space-y-3">
        <SectionHeading
          count={candidateEntries.length}
          id="selected-heading"
          title="公開 selected 音声"
        />
        {candidateEntries.length > 0 ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {candidateEntries.map(({ candidate, scenario, line, character }) => (
              <Card key={`${candidate.scenario}/${candidate.line}/${candidate.variant}`} size="sm">
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
                    <Badge variant="outline">{candidate.variant}</Badge>
                  </div>
                  <CardTitle className="mt-2 leading-7">{line.text}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <p className="text-xs leading-5 text-muted-foreground">{line.delivery}</p>
                  <ClipButton candidate={candidate} />
                  <div className="flex justify-between font-mono text-[10px] text-muted-foreground">
                    <span>RTF {candidate.rtf.toFixed(3)}</span>
                    <span>{candidate.duration_sec.toFixed(2)}s</span>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
            このモデルには selected candidate がありません。
          </p>
        )}
      </section>

      <section aria-labelledby="outcomes-heading" className="space-y-3">
        <SectionHeading
          count={nonSelectedEntries.length}
          id="outcomes-heading"
          title="非 selected outcome"
        />
        {nonSelectedEntries.length > 0 ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {nonSelectedEntries.map(({ outcome, scenario, line, character }) => (
              <Card
                key={`${outcome.group.scenario}/${outcome.group.line}/${outcome.group.variant}`}
                size="sm"
              >
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
                    <Badge variant="outline">{outcome.group.variant}</Badge>
                    <Badge variant={outcome.kind === "failure" ? "destructive" : "secondary"}>
                      {outcome.kind}
                    </Badge>
                  </div>
                  <CardTitle className="mt-2 leading-7">{line.text}</CardTitle>
                </CardHeader>
                <CardContent className="text-xs leading-5 text-muted-foreground">
                  この outcome は再生・連続再生・A/B 比較の対象外です。
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
            非 selected outcome はありません。
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
