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

import { useAudioPlayer, usePlaybackManager } from "@/audio/audio-provider";
import { PageIntro } from "@/components/page-intro";
import { HumanRubricFields } from "@/components/human-rubric-fields";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { loadCurateCatalog } from "@/curate/catalog";
import { buildCurationJson, downloadCurationJson } from "@/curate/export";
import {
  clearGroupDecision,
  createCurationDraft,
  isRubricComplete,
  readCurationDraft,
  resetCurationDraft,
  setGroupDecision,
  updateCandidateRubric,
  writeCurationDraft,
} from "@/curate/storage";
import {
  groupKey,
  type CandidateDraft,
  type CurateCandidatePresentation,
  type CurateCatalog,
  type CurationDraft,
  type Rubric,
} from "@/curate/types";

export function CuratePage() {
  const player = useAudioPlayer();
  const playbackManager = usePlaybackManager();
  const catalogRef = useRef<CurateCatalog | null>(null);
  const loadTokenRef = useRef(0);
  const [catalog, setCatalog] = useState<CurateCatalog | null>(null);
  const [draft, setDraft] = useState<CurationDraft | null>(null);
  const [groupIndex, setGroupIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("生成フォルダーを選ぶと、検証後に音声選定を開始します。");

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
      const loaded = await loadCurateCatalog(files);
      if (loadToken !== loadTokenRef.current) {
        loaded.dispose();
        return;
      }
      catalogRef.current = loaded;
      setCatalog(loaded);
      setGroupIndex(0);
      try {
        setDraft(readCurationDraft(localStorage, loaded));
        setNotice(
          `${loaded.groups.length} 項目を読み込みました。候補セット ${loaded.candidateSetSha256.slice(0, 12)}…`,
        );
      } catch (reason: unknown) {
        setError(errorMessage(reason));
        setNotice(
          "保存済み draft を拒否しました。内容を維持したまま明示的なリセットを待っています。",
        );
      }
    } catch (reason: unknown) {
      if (loadToken !== loadTokenRef.current) {
        return;
      }
      setError(errorMessage(reason));
      setNotice("生成フォルダーを読み込めませんでした。");
    } finally {
      if (loadToken === loadTokenRef.current) {
        setBusy(false);
      }
    }
  };

  const commitDraft = (next: CurationDraft, message: string) => {
    if (!catalog) {
      return;
    }
    try {
      writeCurationDraft(localStorage, catalog, next);
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
    try {
      resetCurationDraft(localStorage);
      setDraft(createCurationDraft(catalog));
      setError(null);
      setNotice("この candidate set のローカル draft を明示的にリセットしました。");
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    }
  };

  const handleExport = () => {
    if (!catalog || !draft) {
      return;
    }
    try {
      downloadCurationJson(buildCurationJson(catalog, draft));
      setError(null);
      setNotice("決定済み項目を curation.json としてダウンロードしました。");
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
        description="候補音声を匿名で確認し、ページに示した基準で採否を記録します。ファイルは外部へ送信されません。"
        eyebrow="ローカル音声選定"
        title="公開する音声を選ぶ"
      />

      <Card className="ring-primary/25">
        <CardHeader>
          <CardTitle>生成フォルダーを選択</CardTitle>
          <CardDescription>
            manifest-v4.json、candidate-set.json、candidate-set.sha256、audio/
            を含むフォルダーを選択してください。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/80">
            <FolderOpen aria-hidden="true" className="size-4" />
            {busy ? "検証中…" : "生成フォルダーを選択"}
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
            <CardTitle>音声選定データを使用できません</CardTitle>
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
          <CurationSummary
            current={groupIndex}
            onExport={handleExport}
            onReset={handleReset}
            progress={progress}
            total={catalog.groups.length}
          />
          <GroupEditor
            candidateState={groupDraft.candidates}
            decision={groupDraft.decision}
            group={group}
            onClearDecision={() =>
              commitDraft(clearGroupDecision(draft, groupKey(group)), "項目を未判断へ戻しました。")
            }
            onDecision={(decision) =>
              commitDraft(
                setGroupDecision(draft, groupKey(group), decision),
                decision.type === "skipped" ? "項目を見送りました。" : "候補を選択しました。",
              )
            }
            onNext={() => setGroupIndex((index) => Math.min(index + 1, catalog.groups.length - 1))}
            onPrevious={() => setGroupIndex((index) => Math.max(index - 1, 0))}
            onRubric={(takeId, rubric) =>
              commitDraft(
                updateCandidateRubric(draft, groupKey(group), takeId, rubric),
                "rubric をローカル保存しました。",
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

function CurationSummary({
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
    <Card>
      <CardContent className="grid gap-4 pt-1 lg:grid-cols-[1fr_auto] lg:items-center">
        <div>
          <div className="flex flex-wrap gap-2">
            <Badge>
              {current + 1} / {total}
            </Badge>
            <Badge variant="outline">選択 {progress.selected}</Badge>
            <Badge variant="outline">見送り {progress.skipped}</Badge>
            <Badge variant="outline">未判断 {progress.uncurated}</Badge>
          </div>
          <div
            aria-label="音声選定の進捗"
            aria-valuemax={total}
            aria-valuemin={0}
            aria-valuenow={progress.selected + progress.skipped}
            className="mt-3 h-2 overflow-hidden rounded-full bg-primary/15"
            role="progressbar"
          >
            <div
              className="h-full rounded-full bg-primary"
              style={{ width: `${((progress.selected + progress.skipped) / total) * 100}%` }}
            />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={onExport} variant="outline">
            <Download aria-hidden="true" />
            選定結果を保存
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

function GroupEditor({
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
  candidateState: readonly CandidateDraft[];
  decision: CurationDraft["groups"][number]["decision"];
  group: CurateCatalog["groups"][number];
  onClearDecision: () => void;
  onDecision: (decision: { type: "selected"; take_id: string } | { type: "skipped" }) => void;
  onNext: () => void;
  onPrevious: () => void;
  onRubric: (takeId: string, rubric: Rubric) => void;
  player: ReturnType<typeof useAudioPlayer>;
  position: number;
  total: number;
}) {
  const draftsByTake = new Map(candidateState.map((candidate) => [candidate.take_id, candidate]));
  const allComplete = candidateState.every((candidate) => isRubricComplete(candidate.rubric));
  const selectedLabel =
    decision?.type === "selected"
      ? group.candidates.find((candidate) => candidate.takeId === decision.take_id)?.label
      : null;

  return (
    <section className="space-y-4" aria-labelledby="curation-group-heading">
      <Card className="bg-primary/[0.035] ring-primary/25">
        <CardHeader>
          <div className="flex flex-wrap gap-2">
            <Badge>{group.model}</Badge>
            <Badge variant="secondary">{group.scenarioTitle}</Badge>
            <Badge variant="outline">{group.variant}</Badge>
            <Badge variant="outline">候補 {group.candidates.length}</Badge>
          </div>
          <CardTitle className="mt-2 text-xl leading-8" id="curation-group-heading">
            {group.lineText}
          </CardTitle>
          <CardDescription>{group.delivery}</CardDescription>
        </CardHeader>
      </Card>

      <CurationJudgmentCriteria candidateCount={group.candidates.length} />

      <div className="grid gap-4 xl:grid-cols-2">
        {group.candidates.map((candidate) => {
          const candidateDraft = draftsByTake.get(candidate.takeId)!;
          return (
            <CandidateEditor
              candidate={candidate}
              draft={candidateDraft}
              key={candidate.takeId}
              onRubric={onRubric}
              onSelect={() => onDecision({ type: "selected", take_id: candidate.takeId })}
              player={player}
              selectionDisabled={
                !allComplete ||
                candidateDraft.rubric.content_correct !== true ||
                candidateDraft.rubric.adoptable !== true
              }
              selected={decision?.type === "selected" && decision.take_id === candidate.takeId}
            />
          );
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>この項目の判断</CardTitle>
          <CardDescription>
            全候補の判断基準を入力すると、選択または見送りに進めます。選択には内容正解かつ採用可が必要です。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Button
            disabled={!allComplete}
            onClick={() => onDecision({ type: "skipped" })}
            variant="outline"
          >
            <SkipForward aria-hidden="true" />
            全候補を見送る
          </Button>
          {decision ? (
            <>
              <Badge variant="secondary">
                {decision.type === "skipped"
                  ? "判断済み: 見送り"
                  : `判断済み: 候補 ${selectedLabel}`}
              </Badge>
              <Button onClick={onClearDecision} variant="ghost">
                未判断に戻す
              </Button>
            </>
          ) : (
            <Badge variant="outline">未判断</Badge>
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

export function CurationJudgmentCriteria({ candidateCount }: { candidateCount: number }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>今回の判断基準</CardTitle>
        <CardDescription>
          表示された台詞と演技指示を基準に、各項目を独立して判断してください。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm text-muted-foreground">
        <p>
          <span className="font-medium text-foreground">内容は正しい:</span>{" "}
          欠落・追加・反復がなく、発音と厳密な日本語の音調・アクセントまで正しい場合だけ
          「はい」。語の読みが理論上正しくても、音調・アクセントが不正確なら「いいえ」です。
        </p>
        <p>
          <span className="font-medium text-foreground">意図一致:</span>{" "}
          表示された感情、強度、話し方にどれだけ合うかを 1〜5 で評価します。
        </p>
        <p>
          <span className="font-medium text-foreground">役として自然:</span> 表示された場面と役柄の
          RPG モブとして、声色・間・テンポが自然かを 1〜5 で評価します。
        </p>
        <p>
          <span className="font-medium text-foreground">採用可能:</span>{" "}
          音割れ、ノイズ、不自然な無音、途切れ、機械的な崩れなどを含む総合品質の判断です。
          内容や感情が正しくても、単純な品質理由で使えない場合は「いいえ」にして group を skip
          します。内容の判定とは独立して評価してください。
        </p>
        <p>
          <span className="font-medium text-foreground">提示語の漏洩:</span>{" "}
          台詞にない感情名、話し方、演技指示、メタ文が音声に混ざって聞こえた場合は、
          「内容は正しい」「採用可能」をともに「いいえ」にして skip します。
        </p>
        <p className="rounded-md border border-primary/20 bg-primary/[0.04] p-3 text-foreground">
          {candidateCount === 1 ? (
            <>
              この group は候補が1件です。「内容は正しい」と「採用可能」がともに
              「はい」のときだけ候補を選択し、それ以外は skip してください。
            </>
          ) : (
            <>
              この group は候補が{candidateCount}件です。全候補を個別に評価し、「内容は正しい」と
              「採用可能」がともに「はい」の候補から、最も適した1件だけを選択してください。
              該当候補がなければ全候補を見送ってください。
            </>
          )}
        </p>
      </CardContent>
    </Card>
  );
}

function CandidateEditor({
  candidate,
  draft,
  onRubric,
  onSelect,
  player,
  selected,
  selectionDisabled,
}: {
  candidate: CurateCandidatePresentation;
  draft: CandidateDraft;
  onRubric: (takeId: string, rubric: Rubric) => void;
  onSelect: () => void;
  player: ReturnType<typeof useAudioPlayer>;
  selected: boolean;
  selectionDisabled: boolean;
}) {
  const status = player.currentClipKey === candidate.audio.key ? player.status : "idle";
  const active = status === "loading" || status === "playing" || status === "paused";
  return (
    <Card className={selected ? "ring-primary/70" : "ring-primary/20"}>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <span className="grid size-12 place-items-center rounded-full border border-primary/35 bg-primary/[0.06] font-mono text-2xl font-semibold text-primary">
            {candidate.label}
          </span>
          <div className="flex gap-2">
            {candidate.gateContent === "review_required" ? (
              <Badge variant="destructive">自動QC: 要確認</Badge>
            ) : (
              <Badge variant="outline">自動QC通過</Badge>
            )}
            {selected ? <Badge>選択中</Badge> : null}
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

        <HumanRubricFields
          onChange={(rubric) => onRubric(candidate.takeId, rubric)}
          value={draft.rubric}
        />

        <Button disabled={selectionDisabled} onClick={onSelect}>
          <Check aria-hidden="true" />
          候補 {candidate.label} を選択
        </Button>
      </CardContent>
    </Card>
  );
}

function summarizeDraft(draft: CurationDraft) {
  let selected = 0;
  let skipped = 0;
  for (const group of draft.groups) {
    if (group.decision?.type === "selected") {
      selected += 1;
    } else if (group.decision?.type === "skipped") {
      skipped += 1;
    }
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
