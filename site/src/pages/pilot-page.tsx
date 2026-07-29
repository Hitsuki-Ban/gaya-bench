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
import { HumanRubricFields } from "@/components/human-rubric-fields";
import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { loadPilotCatalog } from "@/pilot/catalog";
import { buildPilotDecisionJson, downloadPilotDecisionJson } from "@/pilot/export";
import {
  clearPilotGroupDecision,
  createPilotDecisionDraft,
  isPilotRubricComplete,
  readPilotDecisionDraft,
  resetPilotDecisionDraft,
  setPilotGroupDecision,
  updatePilotCandidateRubric,
  writePilotDecisionDraft,
} from "@/pilot/storage";
import type {
  PilotCatalog,
  PilotDecisionDraft,
  PilotGroupDraft,
  PilotGroupPresentation,
  PilotRubric,
} from "@/pilot/types";

export function PilotPage() {
  const player = useAudioPlayer();
  const playbackManager = usePlaybackManager();
  const catalogRef = useRef<PilotCatalog | null>(null);
  const loadTokenRef = useRef(0);
  const [catalog, setCatalog] = useState<PilotCatalog | null>(null);
  const [draft, setDraft] = useState<PilotDecisionDraft | null>(null);
  const [groupIndex, setGroupIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState(
    "pilot bundle を選択すると、検証後にブラインド評価を開始します。",
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
    setNotice("pilot set と全候補音声を検証しています…");
    playbackManager.stop();
    catalogRef.current?.dispose();
    catalogRef.current = null;
    setCatalog(null);
    setDraft(null);
    try {
      const loaded = await loadPilotCatalog(files);
      if (loadToken !== loadTokenRef.current) {
        loaded.dispose();
        return;
      }
      catalogRef.current = loaded;
      setCatalog(loaded);
      setGroupIndex(0);
      try {
        setDraft(readPilotDecisionDraft(localStorage, loaded));
        setNotice(`${loaded.groups.length} group のブラインド評価を読み込みました。`);
      } catch (reason: unknown) {
        setError(errorMessage(reason));
        setNotice(
          "保存済み draft を拒否しました。内容を維持したまま明示的なリセットを待っています。",
        );
      }
    } catch {
      if (loadToken !== loadTokenRef.current) {
        return;
      }
      setError(
        "pilot bundle の完全性検証に失敗しました。bundle を再生成してから選択し直してください。",
      );
      setNotice("pilot bundle を読み込めませんでした。");
    } finally {
      if (loadToken === loadTokenRef.current) {
        setBusy(false);
      }
    }
  };

  const commitDraft = (next: PilotDecisionDraft, message: string) => {
    if (!catalog) {
      return;
    }
    try {
      writePilotDecisionDraft(localStorage, catalog, next);
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
      resetPilotDecisionDraft(localStorage);
      setDraft(createPilotDecisionDraft(catalog));
      setError(null);
      setNotice("この pilot set のローカル draft を明示的にリセットしました。");
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    }
  };

  const handleExport = () => {
    if (!catalog || !draft) {
      return;
    }
    try {
      downloadPilotDecisionJson(buildPilotDecisionJson(catalog, draft));
      setError(null);
      setNotice("全 group の deterministic pilot-decision.json をダウンロードしました。");
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    }
  };

  const progress = draft ? summarizeDraft(draft) : null;
  const catalogGroup = catalog?.groups[groupIndex];
  const groupDraft = draft?.groups[groupIndex];

  return (
    <div className="space-y-5">
      <PageIntro
        description="N=3 pilot bundle の候補を A/B/C のまま評価し、全候補 rubric と各 group の判断を pilot set に拘束して保存します。"
        eyebrow="Pre-gate pilot"
        title="Pilot ブラインド評価"
      />

      <Card className="ring-primary/25">
        <CardHeader>
          <CardTitle>pilot bundle を選択</CardTitle>
          <CardDescription>
            pilot-set.json と audio/ を含む bundle root
            を選択してください。ファイルはサーバーへ送信されません。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/80">
            <FolderOpen aria-hidden="true" className="size-4" />
            {busy ? "検証中…" : "pilot bundle を選択"}
            <input
              accept=".json,.opus"
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
        </CardContent>
      </Card>

      {error ? (
        <Card aria-live="assertive" className="border-destructive/40" role="alert">
          <CardHeader>
            <Badge variant="destructive">
              <AlertTriangle aria-hidden="true" />
              拒否
            </Badge>
            <CardTitle>Pilot データを使用できません</CardTitle>
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

      {catalog && draft && progress && catalogGroup && groupDraft ? (
        <>
          <PilotSummary
            current={groupIndex}
            onExport={handleExport}
            onReset={handleReset}
            progress={progress}
            total={catalog.groups.length}
          />
          <PilotGroupEditor
            draft={groupDraft}
            group={catalogGroup.presentation}
            onClearDecision={() =>
              commitDraft(
                clearPilotGroupDecision(draft, catalogGroup.groupId),
                "group を未評価へ戻しました。",
              )
            }
            onDecision={(decision) =>
              commitDraft(
                setPilotGroupDecision(draft, catalogGroup.groupId, decision),
                decision.type === "skipped" ? "group を skip しました。" : "候補を選択しました。",
              )
            }
            onNext={() => setGroupIndex((index) => Math.min(index + 1, catalog.groups.length - 1))}
            onPrevious={() => setGroupIndex((index) => Math.max(index - 1, 0))}
            onRubric={(candidateId, rubric) =>
              commitDraft(
                updatePilotCandidateRubric(draft, catalogGroup.groupId, candidateId, rubric),
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

function PilotSummary({
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
  const decided = progress.selected + progress.skipped;
  return (
    <Card>
      <CardContent className="grid gap-4 pt-1 lg:grid-cols-[1fr_auto] lg:items-center">
        <div>
          <div className="flex flex-wrap gap-2">
            <Badge>
              {current + 1} / {total}
            </Badge>
            <Badge variant="outline">選択 {progress.selected}</Badge>
            <Badge variant="outline">skip {progress.skipped}</Badge>
            <Badge variant="outline">未評価 {progress.undecided}</Badge>
          </div>
          <div
            aria-label="pilot 評価進捗"
            aria-valuemax={total}
            aria-valuemin={0}
            aria-valuenow={decided}
            className="mt-3 h-2 overflow-hidden rounded-full bg-primary/15"
            role="progressbar"
          >
            <div
              className="h-full rounded-full bg-primary"
              style={{ width: `${(decided / total) * 100}%` }}
            />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button disabled={progress.undecided > 0} onClick={onExport} variant="outline">
            <Download aria-hidden="true" />
            pilot-decision.json
          </Button>
          <Button onClick={onReset} variant="destructive">
            <RotateCcw aria-hidden="true" />
            draft をリセット
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function PilotGroupEditor({
  draft,
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
  draft: PilotGroupDraft;
  group: PilotGroupPresentation;
  onClearDecision: () => void;
  onDecision: (
    decision:
      | { readonly type: "selected"; readonly candidate_id: string }
      | { readonly type: "skipped" },
  ) => void;
  onNext: () => void;
  onPrevious: () => void;
  onRubric: (candidateId: string, rubric: PilotRubric) => void;
  player: Pick<AudioPlayer, "currentClipKey" | "status" | "toggle">;
  position: number;
  total: number;
}) {
  const allComplete = draft.candidates.every((candidate) =>
    isPilotRubricComplete(candidate.rubric),
  );
  const selectedCandidateId =
    draft.decision?.type === "selected" ? draft.decision.candidate_id : null;
  const selectedLabel =
    selectedCandidateId === null
      ? null
      : group.candidates.find((candidate) => candidate.candidateId === selectedCandidateId)?.label;

  return (
    <section aria-labelledby="pilot-group-heading" className="space-y-4">
      <Card className="bg-primary/[0.035] ring-primary/25">
        <CardHeader>
          <CardTitle className="text-xl leading-8" id="pilot-group-heading">
            {group.lineText}
          </CardTitle>
          <CardDescription>
            <span className="block">読み: {group.reading}</span>
            <span className="mt-1 block">演技指示: {group.delivery}</span>
          </CardDescription>
        </CardHeader>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        {group.candidates.map((candidate, index) => {
          const candidateDraft = draft.candidates[index]!;
          return (
            <Card
              className={
                draft.decision?.type === "selected" &&
                draft.decision.candidate_id === candidate.candidateId
                  ? "ring-primary/70"
                  : "ring-primary/20"
              }
              key={candidate.candidateId}
            >
              <CardHeader>
                <span className="grid size-12 place-items-center rounded-full border border-primary/35 bg-primary/[0.06] font-mono text-2xl font-semibold text-primary">
                  {candidate.label}
                </span>
                <CardTitle>候補 {candidate.label}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-5">
                <PilotPlayButton candidate={candidate} player={player} />
                <HumanRubricFields
                  onChange={(rubric) => onRubric(candidateDraft.candidate_id, rubric)}
                  value={candidateDraft.rubric}
                />
                <Button
                  disabled={!allComplete}
                  onClick={() =>
                    onDecision({
                      type: "selected",
                      candidate_id: candidateDraft.candidate_id,
                    })
                  }
                >
                  <Check aria-hidden="true" />
                  候補 {candidate.label} を選択
                </Button>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>group の判断</CardTitle>
          <CardDescription>
            A/B/C 全候補の rubric が揃うと、1候補の選択または group の skip ができます。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Button
            disabled={!allComplete}
            onClick={() => onDecision({ type: "skipped" })}
            variant="outline"
          >
            <SkipForward aria-hidden="true" />
            group を skip
          </Button>
          {draft.decision ? (
            <>
              <Badge variant="secondary">
                {draft.decision.type === "skipped"
                  ? "評価済み: skipped"
                  : `評価済み: 候補 ${selectedLabel}`}
              </Badge>
              <Button onClick={onClearDecision} variant="ghost">
                未評価に戻す
              </Button>
            </>
          ) : (
            <Badge variant="outline">未評価</Badge>
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

function PilotPlayButton({
  candidate,
  player,
}: {
  candidate: PilotGroupPresentation["candidates"][number];
  player: Pick<AudioPlayer, "currentClipKey" | "status" | "toggle">;
}) {
  const status = player.currentClipKey === candidate.audio.key ? player.status : "idle";
  const active = status === "loading" || status === "playing" || status === "paused";
  return (
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
  );
}

function summarizeDraft(draft: PilotDecisionDraft) {
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
    undecided: draft.groups.length - selected - skipped,
  };
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
