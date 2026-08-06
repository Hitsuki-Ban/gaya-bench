import { Check, Gauge, Minus } from "lucide-react";
import { Link, useLocation, useParams } from "react-router";

import { ClipButton } from "@/components/clip-button";
import { ModelConditioningCard } from "@/components/model-conditioning-card";
import { ModelMethodBadge } from "@/components/model-method-badge";
import { PageIntro } from "@/components/page-intro";
import { ReferenceConditioningBadge } from "@/components/reference-conditioning-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { benchmarkData, generationProfilesByModel, modelById, playableModels } from "@/data";
import {
  buildModelCandidateEntries,
  buildModelOutcomeEntries,
  calculateRtfStatistics,
} from "@/pages/detail-page-model";
import { NotFoundPage } from "@/pages/not-found-page";
import { EMOTION_LABELS } from "@/ui-labels";

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
  const generationProfiles = generationProfilesByModel.get(model.id) ?? [];

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
        description="対応機能・生成条件と、このモデルの公開音声を確認できます。"
        eyebrow="モデル"
        title={model.name}
      />

      <ModelConditioningCard model={model} models={playableModels} search={search} />

      <div className="grid gap-4 lg:grid-cols-[1.15fr_0.85fr]">
        <Card>
          <CardHeader>
            <CardTitle>対応機能</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-primary/35 bg-primary/6 px-3 py-2">
              <span className="text-sm font-medium">生成方式</span>
              <ModelMethodBadge capabilities={model.capabilities} />
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
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
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>生成情報</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <Row label="バージョン" value={model.version} />
            <Row label="ライセンス" value={model.license_note} />
            <Row label="公開音声" value={String(candidateEntries.length)} />
            <Row label="未収録・失敗" value={String(nonSelectedEntries.length)} />
            <Row
              label="音声形式"
              value={[...new Set(candidates.map(({ variant }) => variantLabel(variant)))].join(
                ", ",
              )}
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
                  音声時間加重 RTF
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
        <SectionHeading id="parameters-heading" title="生成条件" />
        {generationProfiles.length > 0 ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {generationProfiles.map((profile, index) => (
              <Card key={`${profile.model}:${profile.recipe_version}:${index}`} size="sm">
                <CardHeader>
                  <CardTitle className="flex items-center justify-between gap-3">
                    <span>{generationProfileLabel(profile.recipe_version, index)}</span>
                    <Badge variant="outline">公開音声 {profile.candidate_count}</Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm text-muted-foreground">
                  <p>{generationRecipeDescription(profile.recipe_version)}</p>
                  <details className="rounded-md border bg-background px-3 py-2">
                    <summary className="cursor-pointer font-medium text-foreground">
                      サンプリング設定を表示
                    </summary>
                    <pre className="mt-3 overflow-x-auto border-t pt-3 font-mono text-xs leading-5">
                      {JSON.stringify(profile.sampling, null, 2)}
                    </pre>
                  </details>
                  <details className="rounded-md border bg-background px-3 py-2">
                    <summary className="cursor-pointer font-medium text-foreground">
                      モデル設定の詳細を表示
                    </summary>
                    <pre className="mt-3 max-h-96 overflow-auto border-t pt-3 font-mono text-xs leading-5">
                      {JSON.stringify(profile.requested, null, 2)}
                    </pre>
                  </details>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
            生成済みクリップがないため、生成条件は記録されていません。
          </p>
        )}
      </section>

      <section aria-labelledby="selected-heading" className="space-y-3">
        <SectionHeading count={candidateEntries.length} id="selected-heading" title="公開音声" />
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
                    <Badge variant="outline">{EMOTION_LABELS[line.emotion]}</Badge>
                  </div>
                  <CardTitle className="mt-2 leading-7">{line.text}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <p className="text-xs leading-5 text-muted-foreground">{line.delivery}</p>
                  <ClipButton candidate={candidate} />
                  <ReferenceConditioningBadge conditioning={candidate.reference_conditioning} />
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
            このモデルの公開音声はありません。
          </p>
        )}
      </section>

      <section aria-labelledby="outcomes-heading" className="space-y-3">
        <SectionHeading
          count={nonSelectedEntries.length}
          id="outcomes-heading"
          title="未収録・生成失敗"
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
                    <Badge variant={outcome.kind === "failure" ? "destructive" : "secondary"}>
                      {outcomeLabel(outcome.kind)}
                    </Badge>
                  </div>
                  <CardTitle className="mt-2 leading-7">{line.text}</CardTitle>
                </CardHeader>
                <CardContent className="text-xs leading-5 text-muted-foreground">
                  このセリフの公開音声はありません。
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
            未収録・生成失敗はありません。
          </p>
        )}
      </section>
    </div>
  );
}

function generationProfileLabel(recipeVersion: string, index: number): string {
  if (recipeVersion === "seed-only-v1" || recipeVersion === "fixed-single-v1") {
    return `設定 ${index + 1}`;
  }
  throw new Error(`表示説明が未定義の生成レシピです: ${recipeVersion}`);
}

function generationRecipeDescription(recipeVersion: string): string {
  if (recipeVersion === "seed-only-v1") {
    return "モデルごとの固定設定を使い、音声ごとに再現可能なシードを割り当てています。";
  }
  if (recipeVersion === "fixed-single-v1") {
    return "モデルごとの固定設定を使い、シード指定なしで生成しています。";
  }
  throw new Error(`表示説明が未定義の生成レシピです: ${recipeVersion}`);
}

function variantLabel(variant: string): string {
  if (variant === "dry") {
    return "無加工";
  }
  throw new Error(`表示ラベルが未定義の音声形式です: ${variant}`);
}

function outcomeLabel(kind: "skipped" | "uncurated" | "failure"): string {
  return {
    skipped: "未収録",
    uncurated: "準備中",
    failure: "生成失敗",
  }[kind];
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[6rem_1fr] gap-3 border-b pb-2 last:border-0">
      <span className="font-mono text-xs text-muted-foreground">{label}</span>
      <span className="min-w-0 text-right text-xs [overflow-wrap:anywhere]">{value}</span>
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
