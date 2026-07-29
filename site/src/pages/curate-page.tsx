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
  const [notice, setNotice] = useState("run root を選択すると、検証後に策展を開始します。");

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
          `${loaded.groups.length} group を読み込みました。candidate set ${loaded.candidateSetSha256.slice(0, 12)}…`,
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
      setNotice("run root を読み込めませんでした。");
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
      setNotice("決定済み group を deterministic curation.json としてダウンロードしました。");
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
        description="ローカル run の eligible take を匿名で確認し、rubric と採否を candidate set に拘束して保存します。ブラウザーは repository を変更しません。"
        eyebrow="Local take curation"
        title="テイク策展"
      />

      <Card className="ring-primary/25">
        <CardHeader>
          <CardTitle>run root を選択</CardTitle>
          <CardDescription>
            manifest-v4.json、candidate-set.json、candidate-set.sha256、audio/ を含む run
            rootを選択してください。ファイルはサーバーへ送信されません。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/80">
            <FolderOpen aria-hidden="true" className="size-4" />
            {busy ? "検証中…" : "run root を選択"}
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
            <CardTitle>策展データを使用できません</CardTitle>
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
              commitDraft(
                clearGroupDecision(draft, groupKey(group)),
                "group を未策展へ戻しました。",
              )
            }
            onDecision={(decision) =>
              commitDraft(
                setGroupDecision(draft, groupKey(group), decision),
                decision.type === "skipped"
                  ? "group を skip しました。"
                  : "candidate を選択しました。",
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
            <Badge variant="outline">skip {progress.skipped}</Badge>
            <Badge variant="outline">未策展 {progress.uncurated}</Badge>
          </div>
          <div
            aria-label="策展進捗"
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
            curation.json
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
            <Badge variant="outline">{group.candidates.length} candidates</Badge>
          </div>
          <CardTitle className="mt-2 text-xl leading-8" id="curation-group-heading">
            {group.lineText}
          </CardTitle>
          <CardDescription>{group.delivery}</CardDescription>
        </CardHeader>
      </Card>

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
          <CardTitle>group の判断</CardTitle>
          <CardDescription>
            全 candidate の rubric が揃うと選択または skip
            できます。選択には内容正解かつ採用可が必要です。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Button
            disabled={!allComplete}
            onClick={() => onDecision({ type: "skipped" })}
            variant="outline"
          >
            <SkipForward aria-hidden="true" />
            全候補を skip
          </Button>
          {decision ? (
            <>
              <Badge variant="secondary">
                {decision.type === "skipped"
                  ? "策展済み: skipped"
                  : `策展済み: 候補 ${selectedLabel}`}
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
  const setField = (field: keyof Rubric, value: boolean | number | null) =>
    onRubric(candidate.takeId, { ...draft.rubric, [field]: value } as Rubric);

  return (
    <Card className={selected ? "ring-primary/70" : "ring-primary/20"}>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <span className="grid size-12 place-items-center rounded-full border border-primary/35 bg-primary/[0.06] font-mono text-2xl font-semibold text-primary">
            {candidate.label}
          </span>
          <div className="flex gap-2">
            {candidate.gateContent === "review_required" ? (
              <Badge variant="destructive">内容確認必須</Badge>
            ) : (
              <Badge variant="outline">gate pass</Badge>
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

        <BooleanRubric
          label="内容は正しい"
          onChange={(value) => setField("content_correct", value)}
          value={draft.rubric.content_correct}
        />
        <ScaleRubric
          label="意図一致"
          onChange={(value) => setField("intent_match", value)}
          value={draft.rubric.intent_match}
        />
        <ScaleRubric
          label="役として自然"
          onChange={(value) => setField("character_naturalness", value)}
          value={draft.rubric.character_naturalness}
        />
        <BooleanRubric
          label="採用可能"
          onChange={(value) => setField("adoptable", value)}
          value={draft.rubric.adoptable}
        />

        <Button disabled={selectionDisabled} onClick={onSelect}>
          <Check aria-hidden="true" />
          候補 {candidate.label} を選択
        </Button>
      </CardContent>
    </Card>
  );
}

function BooleanRubric({
  label,
  onChange,
  value,
}: {
  label: string;
  onChange: (value: boolean | null) => void;
  value: boolean | null;
}) {
  return (
    <fieldset>
      <legend className="mb-2 text-sm font-medium">{label}</legend>
      <div className="flex flex-wrap gap-2">
        <ChoiceButton active={value === true} label="はい" onClick={() => onChange(true)} />
        <ChoiceButton active={value === false} label="いいえ" onClick={() => onChange(false)} />
        <ChoiceButton active={value === null} label="未入力" onClick={() => onChange(null)} />
      </div>
    </fieldset>
  );
}

function ScaleRubric({
  label,
  onChange,
  value,
}: {
  label: string;
  onChange: (value: number | null) => void;
  value: number | null;
}) {
  return (
    <fieldset>
      <legend className="mb-2 text-sm font-medium">{label}</legend>
      <div className="flex flex-wrap gap-2">
        {[1, 2, 3, 4, 5].map((rating) => (
          <ChoiceButton
            active={value === rating}
            key={rating}
            label={String(rating)}
            onClick={() => onChange(rating)}
          />
        ))}
        <ChoiceButton active={value === null} label="未入力" onClick={() => onChange(null)} />
      </div>
    </fieldset>
  );
}

function ChoiceButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <Button
      aria-pressed={active}
      onClick={onClick}
      size="sm"
      type="button"
      variant={active ? "secondary" : "outline"}
    >
      {label}
    </Button>
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
