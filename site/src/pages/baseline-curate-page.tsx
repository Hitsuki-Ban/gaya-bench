import {
  AlertTriangle,
  Check,
  Download,
  FolderOpen,
  Pause,
  Play,
  RotateCcw,
  SkipForward,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { AudioPlayer } from "@/audio/audio-provider";
import { useAudioPlayer, usePlaybackManager } from "@/audio/audio-provider";
import { loadBaselineCatalog } from "@/baseline/catalog";
import { buildBaselineCurationJson, downloadBaselineCurationJson } from "@/baseline/export";
import {
  clearBaselineGroupDecision,
  createBaselineCurationDraft,
  readBaselineCurationDraft,
  resetBaselineCurationDraft,
  setBaselineGroupDecision,
  updateBaselineCandidateRubric,
  writeBaselineCurationDraft,
} from "@/baseline/storage";
import { baselineSelectionStatus } from "@/baseline/selection";
import type { BaselineCatalog, BaselineCurationDraft, BaselineGroup } from "@/baseline/types";
import { HumanRubricFields } from "@/components/human-rubric-fields";
import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { isRubricComplete } from "@/curate/storage";
import type { CandidateDraft, CurateDecision, Rubric } from "@/curate/types";
import { groupKey } from "@/curate/types";

export function BaselineCuratePage() {
  const player = useAudioPlayer();
  const playbackManager = usePlaybackManager();
  const catalogRef = useRef<BaselineCatalog | null>(null);
  const loadTokenRef = useRef(0);
  const [catalog, setCatalog] = useState<BaselineCatalog | null>(null);
  const [draft, setDraft] = useState<BaselineCurationDraft | null>(null);
  const [groupIndex, setGroupIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState(
    "baseline run root を選択すると、完全性検証後に評価を開始します。",
  );

  useEffect(() => {
    return () => {
      loadTokenRef.current += 1;
      playbackManager.stop();
      catalogRef.current?.dispose();
    };
  }, [playbackManager]);

  const loadDirectory = async (files: readonly File[]) => {
    const loadToken = loadTokenRef.current + 1;
    loadTokenRef.current = loadToken;
    setBusy(true);
    setError(null);
    setNotice("candidate set、baseline reference、全音声を検証しています…");
    playbackManager.stop();
    catalogRef.current?.dispose();
    catalogRef.current = null;
    setCatalog(null);
    setDraft(null);
    try {
      const loaded = await loadBaselineCatalog(files);
      if (loadToken !== loadTokenRef.current) {
        loaded.dispose();
        return;
      }
      catalogRef.current = loaded;
      setCatalog(loaded);
      setGroupIndex(0);
      try {
        setDraft(readBaselineCurationDraft(localStorage, loaded));
        setNotice(
          `${loaded.groups.length} curatable group を読み込みました。candidate0 audit: ${loaded.auditedNoCandidateCount} group。`,
        );
      } catch (reason: unknown) {
        setError(errorMessage(reason));
        setNotice("保存済み baseline draft を拒否しました。明示的なリセットを待っています。");
      }
    } catch (reason: unknown) {
      if (loadToken !== loadTokenRef.current) return;
      setError(errorMessage(reason));
      setNotice("baseline run root を読み込めませんでした。");
    } finally {
      if (loadToken === loadTokenRef.current) setBusy(false);
    }
  };

  const commitDraft = (next: BaselineCurationDraft, message: string) => {
    if (!catalog) return;
    try {
      writeBaselineCurationDraft(localStorage, catalog, next);
      setDraft(next);
      setError(null);
      setNotice(message);
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    }
  };

  const handleReset = () => {
    if (!catalog) return;
    try {
      resetBaselineCurationDraft(localStorage);
      setDraft(createBaselineCurationDraft(catalog));
      setError(null);
      setNotice("この2つのSHAに拘束された baseline draft をリセットしました。");
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    }
  };

  const handleExport = () => {
    if (!catalog || !draft) return;
    try {
      downloadBaselineCurationJson(buildBaselineCurationJson(catalog, draft));
      setError(null);
      setNotice("baseline-curation.json をダウンロードしました。");
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    }
  };

  const progress = draft ? summarizeDraft(draft) : null;
  const group = catalog?.groups[groupIndex];
  const groupDraft = draft?.groups[groupIndex];

  return (
    <div className="space-y-5">
      <PageIntro
        description="新しい baseline candidate を現行公開 reference と同じ group で聴き比べ、新候補だけを rubric 評価します。SHA が一致しても自動採用しません。"
        eyebrow="Baseline curation"
        title="Baseline 策展"
      />

      <Card className="ring-primary/25">
        <CardHeader>
          <CardTitle>baseline run root を選択</CardTitle>
          <CardDescription>
            manifest-v4.json、candidate-set、baseline-reference、audio/、reference/
            を含むディレクトリを選択してください。ファイルはサーバーへ送信されません。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/80">
            <FolderOpen aria-hidden="true" className="size-4" />
            {busy ? "検証中…" : "baseline run root を選択"}
            <input
              accept=".json,.opus,.sha256"
              className="sr-only"
              disabled={busy}
              multiple
              onChange={(event) => {
                const files = event.currentTarget.files
                  ? Array.from(event.currentTarget.files)
                  : [];
                event.currentTarget.value = "";
                void loadDirectory(files);
              }}
              type="file"
              {...({ webkitdirectory: "" } as { webkitdirectory: string })}
            />
          </label>
          {catalog ? (
            <div className="space-y-1 font-mono text-xs text-muted-foreground">
              <div>candidate set {catalog.candidateSetSha256}</div>
              <div>baseline reference {catalog.baselineReferenceSha256}</div>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {error ? (
        <Card aria-live="assertive" className="border-destructive/40" role="alert">
          <CardHeader>
            <Badge variant="destructive">
              <AlertTriangle aria-hidden="true" />
              拒否
            </Badge>
            <CardTitle>Baseline 策展データを使用できません</CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
          {catalog ? (
            <CardContent>
              <Button onClick={handleReset} variant="destructive">
                <RotateCcw aria-hidden="true" />
                保存済み draft をリセット
              </Button>
            </CardContent>
          ) : null}
        </Card>
      ) : null}

      {catalog && draft && progress && group && groupDraft ? (
        <>
          <BaselineSummary
            auditedNoCandidateCount={catalog.auditedNoCandidateCount}
            current={groupIndex}
            onExport={handleExport}
            onReset={handleReset}
            progress={progress}
            total={catalog.groups.length}
          />
          <BaselineGroupEditor
            candidateDraft={groupDraft.candidates[0]!}
            decision={groupDraft.decision}
            group={group}
            onClearDecision={() =>
              commitDraft(
                clearBaselineGroupDecision(draft, groupKey(group)),
                "group を未策展へ戻しました。",
              )
            }
            onDecision={(decision) =>
              commitDraft(
                setBaselineGroupDecision(draft, groupKey(group), decision),
                decision.type === "selected"
                  ? "新 baseline candidate を選択しました。"
                  : "group を skip しました。",
              )
            }
            onNext={() => setGroupIndex((index) => Math.min(index + 1, catalog.groups.length - 1))}
            onPrevious={() => setGroupIndex((index) => Math.max(index - 1, 0))}
            onRubric={(rubric) =>
              commitDraft(
                updateBaselineCandidateRubric(
                  draft,
                  groupKey(group),
                  group.candidate.takeId,
                  rubric,
                ),
                "新 baseline candidate の rubric を保存しました。",
              )
            }
            player={player}
            position={groupIndex}
            total={catalog.groups.length}
          />
        </>
      ) : null}

      <p aria-live="polite" className="min-h-5 text-sm text-muted-foreground" role="status">
        {notice}
      </p>
    </div>
  );
}

function BaselineSummary({
  auditedNoCandidateCount,
  current,
  onExport,
  onReset,
  progress,
  total,
}: {
  auditedNoCandidateCount: number;
  current: number;
  onExport: () => void;
  onReset: () => void;
  progress: ReturnType<typeof summarizeDraft>;
  total: number;
}) {
  return (
    <Card>
      <CardContent className="grid gap-4 pt-1 lg:grid-cols-[1fr_auto] lg:items-center">
        <div className="flex flex-wrap gap-2">
          <Badge>
            {current + 1} / {total}
          </Badge>
          <Badge variant="outline">選択 {progress.selected}</Badge>
          <Badge variant="outline">skip {progress.skipped}</Badge>
          <Badge variant="outline">未策展 {progress.uncurated}</Badge>
          <Badge variant="secondary">candidate0 audit {auditedNoCandidateCount}</Badge>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button disabled={progress.uncurated > 0} onClick={onExport} variant="outline">
            <Download aria-hidden="true" />
            baseline-curation.json
          </Button>
          <Button onClick={onReset} variant="destructive">
            <RotateCcw aria-hidden="true" />
            draft をリセット
          </Button>
        </div>
        {progress.uncurated > 0 ? (
          <p className="text-xs text-muted-foreground lg:col-span-2">
            export まで残り {progress.uncurated} group の判断が必要です。
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function BaselineGroupEditor({
  candidateDraft,
  decision,
  group,
  onClearDecision,
  onDecision,
  onNext,
  onPrevious,
  onRubric,
  player,
  position,
  total,
}: {
  candidateDraft: CandidateDraft;
  decision: CurateDecision | null;
  group: BaselineGroup;
  onClearDecision: () => void;
  onDecision: (decision: CurateDecision) => void;
  onNext: () => void;
  onPrevious: () => void;
  onRubric: (rubric: Rubric) => void;
  player: Pick<AudioPlayer, "currentClipKey" | "status" | "toggle">;
  position: number;
  total: number;
}) {
  const complete = isRubricComplete(candidateDraft.rubric);
  const selectable =
    complete &&
    candidateDraft.rubric.content_correct === true &&
    candidateDraft.rubric.adoptable === true;
  const statusMessage = baselineSelectionStatus(candidateDraft.rubric);
  const comparisonMessage =
    group.reference.comparison === "identical"
      ? "音声 SHA は一致しています。自動選択はしません。rubric を明示的に入力してください。"
      : "音声 SHA は異なります。新候補を rubric で明示的に評価してください。";

  return (
    <section aria-labelledby="baseline-group-heading" className="space-y-4">
      <Card className="bg-primary/[0.035] ring-primary/25">
        <CardHeader>
          <div className="flex flex-wrap gap-2">
            <Badge>{group.model}</Badge>
            <Badge variant="secondary">{group.scenarioTitle}</Badge>
            <Badge variant="outline">{group.variant}</Badge>
          </div>
          <CardTitle className="mt-2 text-xl leading-8" id="baseline-group-heading">
            {group.lineText}
          </CardTitle>
          <CardDescription>{group.delivery}</CardDescription>
        </CardHeader>
      </Card>

      <Card
        className={
          group.reference.comparison === "identical"
            ? "border-emerald-600/40"
            : "border-amber-600/40"
        }
      >
        <CardContent className="flex flex-wrap items-center gap-3 pt-1">
          <Badge variant={group.reference.comparison === "identical" ? "secondary" : "destructive"}>
            {group.reference.comparison === "identical" ? "SHA 一致" : "SHA 相違"}
          </Badge>
          <p className="text-sm">{comparisonMessage}</p>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="ring-primary/20">
          <CardHeader>
            <Badge variant="outline">比較専用・評価対象外</Badge>
            <CardTitle>現行公開 reference</CardTitle>
            <CardDescription className="space-y-1 font-mono text-xs">
              <span className="block">SHA {group.reference.sha256}</span>
              <span className="block">{group.reference.publicPath}</span>
            </CardDescription>
          </CardHeader>
          <CardContent>
            <BaselinePlayButton
              clip={group.reference.audio}
              label="現行公開 reference を再生"
              player={player}
            />
          </CardContent>
        </Card>

        <Card className={decision?.type === "selected" ? "ring-primary/70" : "ring-primary/20"}>
          <CardHeader>
            <Badge>rubric 評価対象</Badge>
            <CardTitle>新 baseline candidate {group.candidate.label}</CardTitle>
            <CardDescription className="font-mono text-xs">
              SHA {group.candidateSha256}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <BaselinePlayButton
              clip={group.candidate.audio}
              label="新 baseline candidate を再生"
              player={player}
            />
            <HumanRubricFields onChange={onRubric} value={candidateDraft.rubric} />
            <p className="text-xs text-muted-foreground">{statusMessage}</p>
            <Button
              disabled={!selectable}
              onClick={() =>
                onDecision({
                  type: "selected",
                  take_id: group.candidate.takeId,
                })
              }
            >
              <Check aria-hidden="true" />
              新 baseline candidate を選択
            </Button>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>group の判断</CardTitle>
          <CardDescription>
            新候補の rubric 全項目を入力した後、採用または skip を明示してください。現行 reference
            は比較用で、採否の候補ではありません。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Button
            disabled={!complete}
            onClick={() => onDecision({ type: "skipped" })}
            variant="outline"
          >
            <SkipForward aria-hidden="true" />
            group を skip
          </Button>
          {decision ? (
            <>
              <Badge variant="secondary">
                {decision.type === "selected" ? "策展済み: 新候補 selected" : "策展済み: skipped"}
              </Badge>
              <Button onClick={onClearDecision} variant="ghost">
                未策展に戻す
              </Button>
            </>
          ) : (
            <Badge variant="outline">未策展</Badge>
          )}
        </CardContent>
      </Card>

      <div className="flex justify-between gap-3">
        <Button disabled={position === 0} onClick={onPrevious} variant="outline">
          前の group
        </Button>
        <Button disabled={position + 1 >= total} onClick={onNext} variant="outline">
          次の group
        </Button>
      </div>
    </section>
  );
}

function BaselinePlayButton({
  clip,
  label,
  player,
}: {
  clip: BaselineGroup["candidate"]["audio"];
  label: string;
  player: Pick<AudioPlayer, "currentClipKey" | "status" | "toggle">;
}) {
  const status = player.currentClipKey === clip.key ? player.status : "idle";
  const active = status === "loading" || status === "playing" || status === "paused";
  return (
    <Button
      className="w-full"
      onClick={() => void player.toggle(clip)}
      variant={active ? "secondary" : "outline"}
    >
      {status === "loading" || status === "playing" ? (
        <Pause aria-hidden="true" />
      ) : (
        <Play aria-hidden="true" />
      )}
      {active ? "一時停止 / 再開" : label}
    </Button>
  );
}

function summarizeDraft(draft: BaselineCurationDraft) {
  let selected = 0;
  let skipped = 0;
  for (const group of draft.groups) {
    if (group.decision?.type === "selected") selected += 1;
    if (group.decision?.type === "skipped") skipped += 1;
  }
  return {
    selected,
    skipped,
    uncurated: draft.groups.length - selected - skipped,
  };
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
