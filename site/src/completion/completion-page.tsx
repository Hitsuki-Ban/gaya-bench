import { AlertTriangle, Check, Download, FolderOpen, Pause, Play, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useAudioPlayer, usePlaybackManager } from "@/audio/audio-provider";
import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { loadCurateCatalog } from "@/curate/catalog";
import type { CurateCandidatePresentation } from "@/curate/types";

import { CompletionRubricFields } from "./completion-rubric-fields";
import { assertCompletionCatalogContract, COMPLETION_PLAN_MARKER } from "./contract";
import { buildCompletionDecisionJson, downloadCompletionDecisionJson } from "./export";
import {
  clearCompletionDecision,
  createCompletionDraft,
  isCompletionRubricComplete,
  readCompletionDraft,
  resetCompletionDraft,
  setCompletionDecision,
  updateCompletionRubric,
  writeCompletionDraft,
} from "./storage";
import {
  completionGroupKey,
  type CompletionCatalog,
  type CompletionDraft,
  type CompletionRubric,
} from "./types";

export function CompletionPage() {
  const player = useAudioPlayer();
  const playbackManager = usePlaybackManager();
  const catalogRef = useRef<CompletionCatalog | null>(null);
  const loadTokenRef = useRef(0);
  const [catalog, setCatalog] = useState<CompletionCatalog | null>(null);
  const [draft, setDraft] = useState<CompletionDraft | null>(null);
  const [groupIndex, setGroupIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState(
    "補録 listening bundle を選ぶと、全候補を検証して聴取を開始します。",
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
    setNotice("manifest、candidate set、全候補音声を検証しています…");
    playbackManager.stop();
    catalogRef.current?.dispose();
    catalogRef.current = null;
    setCatalog(null);
    setDraft(null);
    try {
      const planSha256 = await readCompletionPlanMarker(files);
      const loaded = await loadCurateCatalog(files);
      if (loadToken !== loadTokenRef.current) {
        loaded.dispose();
        return;
      }
      try {
        assertCompletionCatalogContract(loaded, planSha256);
      } catch (reason: unknown) {
        loaded.dispose();
        throw reason;
      }
      catalogRef.current = loaded;
      setCatalog(loaded);
      setGroupIndex(0);
      try {
        setDraft(readCompletionDraft(localStorage, loaded));
        setNotice(
          `${loaded.groups.length} 項目を読み込みました。候補セット ${loaded.candidateSetSha256.slice(0, 12)}…`,
        );
      } catch (reason: unknown) {
        setError(errorMessage(reason));
        setNotice("保存済み draft を拒否しました。明示的なリセットを待っています。");
      }
    } catch (reason: unknown) {
      if (loadToken !== loadTokenRef.current) {
        return;
      }
      setError(errorMessage(reason));
      setNotice("補録 listening bundle を読み込めませんでした。");
    } finally {
      if (loadToken === loadTokenRef.current) {
        setBusy(false);
      }
    }
  };

  const commitDraft = (next: CompletionDraft, message: string) => {
    if (!catalog) {
      return;
    }
    try {
      writeCompletionDraft(localStorage, catalog, next);
      setDraft(next);
      setError(null);
      setNotice(message);
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    }
  };

  const handleReset = () => {
    if (!catalog) {
      return;
    }
    resetCompletionDraft(localStorage);
    setDraft(createCompletionDraft(catalog));
    setError(null);
    setNotice("この候補セットの補録 draft を明示的にリセットしました。");
  };

  const handleExport = () => {
    if (!catalog || !draft) {
      return;
    }
    try {
      downloadCompletionDecisionJson(buildCompletionDecisionJson(catalog, draft));
      setError(null);
      setNotice("全45項目の補録 decision を保存しました。");
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    }
  };

  const progress = draft ? summarizeDraft(draft) : null;
  const group = catalog?.groups[groupIndex];
  const groupDraft = draft?.groups[groupIndex];

  return (
    <div className="space-y-5" data-baseline-completion-ui="v1">
      <PageIntro
        description="未公開45スロットを、各3件以上の候補から1件ずつ補います。問題点を正直に記録しながら、必ず現在の最良候補を選びます。"
        eyebrow="Issue #174 · best available"
        title="全モデル基準線を補録する"
      />

      <CompletionJudgmentCriteria />

      <Card className="ring-primary/25">
        <CardHeader>
          <CardTitle>補録 listening bundle を選択</CardTitle>
          <CardDescription>
            manifest-v4.json、candidate-set.json、candidate-set.sha256、audio/
            を含む専用フォルダーを選択してください。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/80">
            <FolderOpen aria-hidden="true" className="size-4" />
            {busy ? "検証中…" : "補録フォルダーを選択"}
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
            <span className="font-mono text-xs text-muted-foreground">
              SHA {catalog.candidateSetSha256.slice(0, 16)}…
            </span>
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
            <CardTitle>補録データを使用できません</CardTitle>
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
          <CompletionSummary
            current={groupIndex}
            onExport={handleExport}
            onReset={handleReset}
            progress={progress}
            total={catalog.groups.length}
          />
          <CompletionGroupEditor
            candidateState={groupDraft.candidates}
            decision={groupDraft.decision}
            group={group}
            onClearDecision={() =>
              commitDraft(
                clearCompletionDecision(draft, completionGroupKey(groupDraft)),
                "項目を未選択へ戻しました。",
              )
            }
            onDecision={(takeId) =>
              commitDraft(
                setCompletionDecision(draft, completionGroupKey(groupDraft), takeId),
                "現在の最良候補を選択しました。",
              )
            }
            onNext={() => setGroupIndex((index) => Math.min(index + 1, catalog.groups.length - 1))}
            onPrevious={() => setGroupIndex((index) => Math.max(index - 1, 0))}
            onRubric={(takeId, rubric) =>
              commitDraft(
                updateCompletionRubric(draft, completionGroupKey(groupDraft), takeId, rubric),
                "評価をローカル保存しました。",
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

async function readCompletionPlanMarker(files: readonly File[]): Promise<string> {
  const markers = files.filter((file) => {
    const relative = file.webkitRelativePath.replaceAll("\\", "/");
    const parts = relative.split("/");
    return parts.length === 2 && parts[1] === COMPLETION_PLAN_MARKER;
  });
  if (markers.length !== 1) {
    throw new Error(`${COMPLETION_PLAN_MARKER} はbundle直下にexactly 1件必要です。`);
  }
  return markers[0]!.text();
}

export function CompletionJudgmentCriteria() {
  return (
    <Card className="border-primary/35 bg-primary/[0.035]">
      <CardHeader>
        <CardTitle>今回の判断基準：欠項を「best available」で必ず補う</CardTitle>
        <CardDescription>
          通常の採否判定ではありません。全候補の欠点を記録したうえで、相対的に最良の1件を選びます。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm leading-6 text-muted-foreground">
        <p>
          <span className="font-medium text-foreground">最優先:</span>{" "}
          台詞の欠落・追加・反復、語や漢字の誤読、台詞にない感情名・話し方・メタ文の漏洩を個別に記録します。
        </p>
        <p>
          <span className="font-medium text-foreground">日本語:</span>{" "}
          語の読みが理論上正しくても、厳密な音調・アクセントが不自然なら、その評価を1〜5へ反映します。
        </p>
        <p>
          <span className="font-medium text-foreground">比較軸:</span>{" "}
          役柄・声線、感情・強度・演技、自然さ・音質を独立して比較します。単純な品質理由も採用可否とメモへ残します。
        </p>
        <p className="rounded-md border border-primary/25 bg-background p-3 text-foreground">
          各 group の全候補を最後まで評価してから、現在もっとも良い1件を必ず選択してください。
          完全合格がなくても skip はしません。content_correct=false や adoptable=false
          の候補を選んでも、 それは「絶対合格」ではなく「3件以上の中の最良」を意味します。
        </p>
      </CardContent>
    </Card>
  );
}

function CompletionSummary({
  current,
  onExport,
  onReset,
  progress,
  total,
}: {
  current: number;
  onExport: () => void;
  onReset: () => void;
  progress: ReturnType<typeof summarizeDraft>;
  total: number;
}) {
  return (
    <Card className="sticky top-[4.5rem] z-10 bg-background/95 backdrop-blur">
      <CardContent className="grid gap-4 pt-1 lg:grid-cols-[1fr_auto] lg:items-center">
        <div>
          <div className="flex flex-wrap gap-2">
            <Badge>
              {current + 1} / {total}
            </Badge>
            <Badge variant="outline">選択 {progress.selected}</Badge>
            <Badge variant="outline">未選択 {progress.remaining}</Badge>
          </div>
          <div
            aria-label="補録聴取の進捗"
            aria-valuemax={total}
            aria-valuemin={0}
            aria-valuenow={progress.selected}
            className="mt-3 h-2 overflow-hidden rounded-full bg-primary/15"
            role="progressbar"
          >
            <div
              className="h-full rounded-full bg-primary"
              style={{ width: `${(progress.selected / total) * 100}%` }}
            />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button disabled={progress.remaining !== 0} onClick={onExport}>
            <Download aria-hidden="true" />
            45件の決定を保存
          </Button>
          <Button onClick={onReset} variant="destructive">
            <RotateCcw aria-hidden="true" />
            一時保存をリセット
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function CompletionGroupEditor({
  candidateState,
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
  candidateState: CompletionDraft["groups"][number]["candidates"];
  decision: CompletionDraft["groups"][number]["decision"];
  group: CompletionCatalog["groups"][number];
  onClearDecision: () => void;
  onDecision: (takeId: string) => void;
  onNext: () => void;
  onPrevious: () => void;
  onRubric: (takeId: string, rubric: CompletionRubric) => void;
  player: ReturnType<typeof useAudioPlayer>;
  position: number;
  total: number;
}) {
  const draftsByTake = new Map(candidateState.map((candidate) => [candidate.take_id, candidate]));
  const allComplete = candidateState.every((candidate) =>
    isCompletionRubricComplete(candidate.rubric),
  );
  const selectedLabel =
    decision === null
      ? null
      : group.candidates.find((candidate) => candidate.takeId === decision.take_id)?.label;

  return (
    <section className="space-y-5">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">{group.model}</Badge>
            <Badge variant="outline">
              {position + 1} / {total}
            </Badge>
            <Badge variant="outline">候補 {group.candidates.length}件</Badge>
          </div>
          <CardTitle>{group.scenarioTitle}</CardTitle>
          <CardDescription>
            {group.scenario} / {group.line}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-lg font-medium leading-8">{group.lineText}</p>
          <p className="rounded-md border bg-muted/35 p-3 text-sm leading-6">
            <span className="font-medium">演技指示:</span> {group.delivery}
          </p>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        {group.candidates.map((candidate) => {
          const candidateDraft = draftsByTake.get(candidate.takeId);
          if (!candidateDraft) {
            throw new Error(`candidate draft がありません: ${candidate.takeId}`);
          }
          return (
            <CompletionCandidateEditor
              candidate={candidate}
              draft={candidateDraft.rubric}
              key={candidate.takeId}
              onRubric={(rubric) => onRubric(candidate.takeId, rubric)}
              onSelect={() => onDecision(candidate.takeId)}
              player={player}
              selected={decision?.take_id === candidate.takeId}
              selectionDisabled={!allComplete}
            />
          );
        })}
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 pt-1">
          {!allComplete ? (
            <Badge variant="outline">全候補の必須評価を埋めると選択できます</Badge>
          ) : null}
          {decision ? (
            <>
              <Badge>選択中: 候補 {selectedLabel}</Badge>
              <Button onClick={onClearDecision} variant="ghost">
                未選択に戻す
              </Button>
            </>
          ) : (
            <Badge variant="outline">未選択</Badge>
          )}
        </CardContent>
      </Card>

      <div className="flex justify-between gap-3">
        <Button disabled={position === 0} onClick={onPrevious} variant="outline">
          前の項目
        </Button>
        <Button disabled={position + 1 >= total} onClick={onNext} variant="outline">
          次の項目
        </Button>
      </div>
    </section>
  );
}

function CompletionCandidateEditor({
  candidate,
  draft,
  onRubric,
  onSelect,
  player,
  selected,
  selectionDisabled,
}: {
  candidate: CurateCandidatePresentation;
  draft: CompletionRubric;
  onRubric: (rubric: CompletionRubric) => void;
  onSelect: () => void;
  player: ReturnType<typeof useAudioPlayer>;
  selected: boolean;
  selectionDisabled: boolean;
}) {
  const status = player.currentClipKey === candidate.audio.key ? player.status : "idle";
  const active = status === "loading" || status === "playing" || status === "paused";
  return (
    <Card className={selected ? "ring-2 ring-primary/70" : "ring-primary/20"}>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <span className="grid size-12 place-items-center rounded-full border border-primary/35 bg-primary/[0.06] font-mono text-2xl font-semibold text-primary">
            {candidate.label}
          </span>
          <div className="flex flex-wrap justify-end gap-2">
            {candidate.gateContent === "review_required" ? (
              <Badge variant="destructive">自動QC: 要確認</Badge>
            ) : (
              <Badge variant="outline">自動QC通過</Badge>
            )}
            {selected ? <Badge>現在の最良</Badge> : null}
          </div>
        </div>
        <CardTitle>候補 {candidate.label}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <Button
          className="w-full"
          onClick={() => void player.toggle(candidate.audio)}
          variant={active ? "secondary" : "outline"}
        >
          {status === "loading" || status === "playing" ? (
            <Pause aria-hidden="true" />
          ) : (
            <Play aria-hidden="true" />
          )}
          {active ? "一時停止 / 再開" : "再生"}
        </Button>

        <CompletionRubricFields onChange={onRubric} value={draft} />

        <Button className="w-full" disabled={selectionDisabled} onClick={onSelect}>
          <Check aria-hidden="true" />
          候補 {candidate.label} を現在の最良として選択
        </Button>
      </CardContent>
    </Card>
  );
}

function summarizeDraft(draft: CompletionDraft) {
  const selected = draft.groups.filter((group) => group.decision !== null).length;
  return { selected, remaining: draft.groups.length - selected };
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
