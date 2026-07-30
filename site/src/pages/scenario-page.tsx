import { AudioWaveform, Ear, MapPinned, Volume2 } from "lucide-react";
import { Link, useLocation, useParams } from "react-router";

import { CharacterKindBadge } from "@/components/character-kind-badge";
import { ClipButton } from "@/components/clip-button";
import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getOutcomesForScenario,
  releaseModelById,
  scenarioById,
  type ArtifactOutcome,
} from "@/data";
import { buildScenarioLineEntries } from "@/pages/detail-page-model";
import { NotFoundPage } from "@/pages/not-found-page";
import { AGE_LABELS, DIFFICULTY_LABELS, EMOTION_LABELS, GENDER_LABELS } from "@/ui-labels";

export function ScenarioPage() {
  const { id } = useParams();
  const { search } = useLocation();
  const scenario = id ? scenarioById.get(id) : undefined;
  if (!scenario) {
    return <NotFoundPage />;
  }

  const outcomes = getOutcomesForScenario(scenario.id);
  const lineEntries = buildScenarioLineEntries(scenario, outcomes);

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
        description={scenario.scene.setting}
        eyebrow="シナリオ"
        title={scenario.title}
      />

      <section aria-labelledby="scene-heading" className="space-y-3">
        <SectionHeading
          count={
            1 + Number(Boolean(scenario.scene.acoustics)) + Number(Boolean(scenario.scene.listener))
          }
          id="scene-heading"
          title="シーン環境"
        />
        <div className="grid gap-3 md:grid-cols-3">
          <SceneFact icon={MapPinned} label="設定" value={scenario.scene.setting} />
          {scenario.scene.acoustics ? (
            <SceneFact icon={Volume2} label="音響" value={scenario.scene.acoustics} />
          ) : null}
          {scenario.scene.listener ? (
            <SceneFact icon={Ear} label="リスナー位置" value={scenario.scene.listener} />
          ) : null}
        </div>
        {scenario.tags && scenario.tags.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {scenario.tags.map((tag) => (
              <Badge key={tag} variant="outline">
                {tag}
              </Badge>
            ))}
          </div>
        ) : null}
      </section>

      <aside
        className="flex gap-3 rounded-md border border-primary/30 bg-primary/5 p-4"
        role="note"
      >
        <AudioWaveform aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-primary" />
        <div>
          <h2 className="text-sm font-semibold">音量を揃えて比較できます</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            モデルごとの音量差を抑えています。音質・発音・演技を中心に聴き比べてください。
          </p>
          <details className="mt-2 text-xs text-muted-foreground">
            <summary className="cursor-pointer">試聴条件の詳細</summary>
            <p className="mt-2 leading-5">
              -18 LUFS 目標 / peak -1 dBTP / mono / 48kHz
              へ正規化しています。距離感や残響は加えていません。
            </p>
          </details>
        </div>
      </aside>

      <section aria-labelledby="characters-heading" className="space-y-3">
        <SectionHeading
          count={scenario.characters.length}
          id="characters-heading"
          title="登場キャラクター"
        />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {scenario.characters.map((character) => (
            <Card key={character.id} size="sm">
              <CardHeader>
                <div className="flex flex-wrap gap-2">
                  <CharacterKindBadge kind={character.kind} />
                  <Badge variant="secondary">{GENDER_LABELS[character.gender]}</Badge>
                  <Badge variant="outline">{AGE_LABELS[character.age]}</Badge>
                  {character.archetype ? (
                    <Badge variant="outline">{character.archetype}</Badge>
                  ) : null}
                </div>
                <CardTitle className="mt-2">{character.name}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm leading-6">
                <CharacterFact label="声質" value={character.voice} />
                {character.personality ? (
                  <CharacterFact label="人物像" value={character.personality} />
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section aria-labelledby="lines-heading" className="space-y-3">
        <SectionHeading
          count={lineEntries.length}
          id="lines-heading"
          title="全セリフと生成クリップ"
        />
        <div className="grid gap-4 lg:grid-cols-2">
          {lineEntries.map(({ line, character, outcomes: lineOutcomes }, lineIndex) => {
            return (
              <Card key={line.id} size="sm">
                <CardHeader>
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="secondary">{character.name}</Badge>
                      <Badge variant="outline">{EMOTION_LABELS[line.emotion]}</Badge>
                      <Badge variant="outline">強度 {line.intensity}</Badge>
                      {line.difficulty === "hard" ? (
                        <Badge variant="destructive">{DIFFICULTY_LABELS[line.difficulty]}</Badge>
                      ) : null}
                    </div>
                    <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                      {String(lineIndex + 1).padStart(2, "0")}
                    </span>
                  </div>
                  <CardTitle className="mt-2 leading-7">{line.text}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="space-y-1 border-l-2 border-border pl-3 text-xs leading-5 text-muted-foreground">
                    <p>{line.delivery}</p>
                    {line.situation ? <p>{line.situation}</p> : null}
                  </div>
                  {lineOutcomes.length > 0 ? (
                    <div className="space-y-2">
                      {lineOutcomes.map((outcome) => (
                        <ScenarioOutcome
                          key={`${outcome.group.model}/${outcome.group.variant}`}
                          outcome={outcome}
                        />
                      ))}
                    </div>
                  ) : null}
                  {lineOutcomes.length === 0 ? (
                    <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">
                      未収録
                    </p>
                  ) : null}
                </CardContent>
              </Card>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function ScenarioOutcome({ outcome }: { outcome: ArtifactOutcome }) {
  const model = releaseModelById.get(outcome.group.model);
  if (!model) {
    throw new Error(`outcome が未知の model を参照しています: ${outcome.group.model}`);
  }
  if (outcome.kind === "selected") {
    return <ClipButton candidate={outcome.candidate} />;
  }
  const presentation = {
    skipped: {
      label: "未収録",
      detail: "公開音声はありません。",
      className: "border-border bg-muted/20 text-muted-foreground",
    },
    uncurated: {
      label: "準備中",
      detail: "公開準備を進めています。",
      className: "border-border bg-muted/20 text-muted-foreground",
    },
    failure: {
      label: "生成失敗",
      detail: "再生成待ちです。",
      className: "border-destructive/40 bg-destructive/5 text-destructive",
    },
  }[outcome.kind];
  return (
    <div className={`rounded-md border p-3 text-xs ${presentation.className}`}>
      <p className="font-medium">{presentation.label}</p>
      <p className="mt-1 text-muted-foreground">
        {model.name} · {presentation.detail}
      </p>
    </div>
  );
}

function SceneFact({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof MapPinned;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-md border bg-card p-4">
      <div className="flex items-center gap-2 text-xs font-medium text-primary">
        <Icon aria-hidden="true" className="size-4" />
        {label}
      </div>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">{value}</p>
    </div>
  );
}

function CharacterFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="font-mono text-[10px] tracking-wider text-primary uppercase">{label}</p>
      <p className="mt-1 text-muted-foreground">{value}</p>
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
