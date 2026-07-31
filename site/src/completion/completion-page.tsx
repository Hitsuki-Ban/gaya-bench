import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  FolderOpen,
  Headphones,
  Pause,
  Play,
  RotateCcw,
  Split,
  UserRound,
} from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { useAudioPlayer, usePlaybackManager } from "@/audio/audio-provider";
import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { BaselineCompletionPage } from "./baseline-page";
import { CompletionRubricFields } from "./completion-rubric-fields";
import { candidateKeyboardShortcut } from "./candidate-shortcut";
import { loadRoleReviewCatalog } from "./contract";
import { buildRoleReviewDecisionJson, downloadRoleReviewDecisionJson } from "./export";
import {
  applyRoleReviewPlaybackCompletion,
  clearRoleReviewConfirmation,
  confirmRoleReviewGroup,
  isRoleReviewRubricComplete,
  readRoleReviewDraft,
  recoverRoleReviewDraft,
  reopenRole,
  requiredHeardCount,
  resetRoleReviewDraft,
  RoleReopenRequiredError,
  selectRoleReviewCandidate,
  summarizeRoleReviewDraft,
  updateRoleReviewRubric,
  writeRoleReviewDraft,
} from "./storage";
import {
  roleKey,
  type RoleReviewCatalog,
  type RoleReviewDraft,
  type RoleReviewGroup,
  type RoleReviewGroupDraft,
  type RoleReviewQc,
  type RoleReviewRubric,
} from "./types";

interface ReopenTarget {
  readonly model: string;
  readonly character: string;
  readonly reason: string;
}

export function CompletionPage() {
  const [mode, setMode] = useState<"baseline" | "role-review">("baseline");

  return (
    <div className="space-y-5">
      <nav
        aria-label="听测workflow"
        className="grid grid-cols-2 gap-2 rounded-lg border bg-muted/35 p-2"
      >
        <Button
          aria-pressed={mode === "baseline"}
          onClick={() => setMode("baseline")}
          type="button"
          variant={mode === "baseline" ? "default" : "ghost"}
        >
          Phase B · 欠項baseline
        </Button>
        <Button
          aria-pressed={mode === "role-review"}
          onClick={() => setMode("role-review")}
          type="button"
          variant={mode === "role-review" ? "default" : "ghost"}
        >
          Phase A · 役柄continuity
        </Button>
      </nav>
      {mode === "baseline" ? <BaselineCompletionPage /> : <RoleReviewCompletionPage />}
    </div>
  );
}

export function RoleReviewCompletionPage() {
  const player = useAudioPlayer();
  const playbackManager = usePlaybackManager();
  const catalogRef = useRef<RoleReviewCatalog | null>(null);
  const handledPlaybackSessionRef = useRef<number | null>(null);
  const loadTokenRef = useRef(0);
  const [catalog, setCatalog] = useState<RoleReviewCatalog | null>(null);
  const [draft, setDraft] = useState<RoleReviewDraft | null>(null);
  const [groupIndex, setGroupIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reopenTarget, setReopenTarget] = useState<ReopenTarget | null>(null);
  const [notice, setNotice] = useState(
    "role-review-v1 bundle を選ぶと、契約・hash・全候補音声を検証します。",
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
    setReopenTarget(null);
    setNotice("role-review-v1、group identity、全候補音声を検証しています…");
    playbackManager.stop();
    catalogRef.current?.dispose();
    catalogRef.current = null;
    setCatalog(null);
    setDraft(null);
    let loaded: RoleReviewCatalog | null = null;
    try {
      loaded = await loadRoleReviewCatalog(files);
      if (loadToken !== loadTokenRef.current) {
        loaded.dispose();
        return;
      }
      let restored: RoleReviewDraft;
      try {
        restored = readRoleReviewDraft(localStorage, loaded);
      } catch (reason: unknown) {
        if (!(reason instanceof RoleReopenRequiredError)) {
          throw reason;
        }
        catalogRef.current = loaded;
        setCatalog(loaded);
        setGroupIndex(0);
        handleFailure(reason);
        setNotice(
          "bundleは検証済みです。保存済み記録と衝突した役柄だけを明示的にreopenしてください。",
        );
        return;
      }
      catalogRef.current = loaded;
      setCatalog(loaded);
      setDraft(restored);
      setGroupIndex(0);
      setNotice(
        restored.role_reopen_requests.length > 0
          ? `${loaded.groups.length} groupを読み込み、epoch変更のあった${restored.role_reopen_requests.length}役だけを再開しました。`
          : `${loaded.groups.length} groupを読み込みました。candidate set ${loaded.candidateSetSha256.slice(0, 12)}…`,
      );
    } catch (reason: unknown) {
      if (loaded !== null && catalogRef.current !== loaded) {
        loaded.dispose();
      }
      if (loadToken !== loadTokenRef.current) {
        return;
      }
      handleFailure(reason);
      setNotice("bundleを拒否しました。修正済みbundleを選び直してください。");
    } finally {
      if (loadToken === loadTokenRef.current) {
        setBusy(false);
      }
    }
  };

  const handleFailure = (reason: unknown) => {
    setError(errorMessage(reason));
    if (reason instanceof RoleReopenRequiredError) {
      setReopenTarget({
        model: reason.model,
        character: reason.character,
        reason: reason.message,
      });
    }
  };

  const commitDraft = (next: RoleReviewDraft, message: string) => {
    if (!catalog) {
      return;
    }
    try {
      writeRoleReviewDraft(localStorage, catalog, next);
      setDraft(next);
      setError(null);
      setReopenTarget(null);
      setNotice(message);
    } catch (reason: unknown) {
      handleFailure(reason);
    }
  };

  useEffect(() => {
    const completion = player.completion;
    if (completion === null || handledPlaybackSessionRef.current === completion.sessionId) {
      return;
    }
    handledPlaybackSessionRef.current = completion.sessionId;
    if (!catalog || !draft) {
      return;
    }
    try {
      const next = applyRoleReviewPlaybackCompletion(catalog, draft, completion);
      if (next === draft) {
        return;
      }
      writeRoleReviewDraft(localStorage, catalog, next);
      setDraft(next);
      setError(null);
      setReopenTarget(null);
      setNotice("最後まで再生した候補をheardとして記録しました。");
    } catch (reason: unknown) {
      handleFailure(reason);
    }
  }, [catalog, draft, player.completion]);

  const handleReset = () => {
    if (!catalog) {
      return;
    }
    const next = resetRoleReviewDraft(localStorage, catalog);
    setDraft(next);
    setGroupIndex(0);
    setError(null);
    setReopenTarget(null);
    setNotice("現在のbundleに属する一時保存だけをリセットしました。");
  };

  const handleReopen = () => {
    if (!catalog || !reopenTarget) {
      return;
    }
    try {
      const reason = `identity不一致を明示reopen: ${reopenTarget.reason}`;
      const next =
        draft === null
          ? recoverRoleReviewDraft(
              localStorage,
              catalog,
              reopenTarget.model,
              reopenTarget.character,
              reason,
            )
          : reopenRole(
              localStorage,
              catalog,
              draft,
              reopenTarget.model,
              reopenTarget.character,
              reason,
            );
      commitDraft(next, "対象役柄の既存確認を失効させ、role epochから再開しました。");
    } catch (reason: unknown) {
      handleFailure(reason);
    }
  };

  const handleExport = () => {
    if (!catalog || !draft) {
      return;
    }
    try {
      downloadRoleReviewDecisionJson(buildRoleReviewDecisionJson(catalog, draft), catalog.phase);
      setError(null);
      setReopenTarget(null);
      setNotice(`${draft.groups.length} groupのrole review decisionを保存しました。`);
    } catch (reason: unknown) {
      handleFailure(reason);
    }
  };

  const group = catalog?.groups[groupIndex];
  const groupDraft = draft?.groups[groupIndex];
  const progress = draft ? summarizeRoleReviewDraft(draft) : null;

  return (
    <div className="space-y-5" data-role-review-ui="role-continuity-timeline-v1">
      <PageIntro
        description="性別・年齢・役柄をanchorで固定し、同じ人物の声が行をまたいで続くかを確認します。初期表示の候補も、必ず人が聴いて確定します。"
        eyebrow="Issue #174 · role review"
        title="役柄の連続性を基準線へ固定する"
      />

      <MobilePersistentSummary group={group ?? null} />

      <Card className="border-primary/35">
        <CardHeader>
          <CardTitle>role-review-v1 bundle を選択</CardTitle>
          <CardDescription>
            bundle直下の role-review-v1.json と、そこからexactに参照される音声だけを読み込みます。
            旧completion bundleは受け付けません。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <label className="inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground outline-none hover:bg-primary/85 focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2 focus-within:ring-offset-background">
            <FolderOpen aria-hidden="true" className="size-4" />
            {busy ? "検証中…" : "role review folderを選択"}
            <input
              accept=".json,.flac,.mp3,.opus,.wav"
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
              {catalog.phase.toUpperCase()} · SHA {catalog.candidateSetSha256.slice(0, 16)}…
            </span>
          ) : null}
        </CardContent>
      </Card>

      {error ? (
        <Card aria-live="assertive" className="border-destructive/55" role="alert">
          <CardHeader>
            <Badge variant="destructive">
              <AlertTriangle aria-hidden="true" />
              拒否
            </Badge>
            <CardTitle>role reviewデータを使用できません</CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
          {reopenTarget ? (
            <CardContent>
              <Button onClick={handleReopen} variant="destructive">
                <Split aria-hidden="true" />
                この役柄だけを明示的にreopen
              </Button>
            </CardContent>
          ) : null}
        </Card>
      ) : null}

      {catalog && draft && group && groupDraft && progress ? (
        <>
          <RoleReviewProgressPanel
            current={groupIndex}
            onExport={handleExport}
            onReset={handleReset}
            phase={catalog.phase}
            progress={progress}
          />
          <PhaseLedger phase={catalog.phase} />
          <RoleReviewWorkspace
            catalog={catalog}
            draft={draft}
            group={group}
            groupDraft={groupDraft}
            groupIndex={groupIndex}
            onClear={() =>
              commitDraft(
                clearRoleReviewConfirmation(catalog, draft, group.id),
                "明示確認を解除しました。",
              )
            }
            onConfirm={() =>
              commitDraft(
                confirmRoleReviewGroup(catalog, draft, group.id),
                "現在のrole epochとgroup hashへ判断を固定しました。",
              )
            }
            onNavigate={setGroupIndex}
            onNext={() => setGroupIndex((index) => Math.min(index + 1, catalog.groups.length - 1))}
            onPrevious={() => setGroupIndex((index) => Math.max(index - 1, 0))}
            onRubric={(rubric) =>
              commitDraft(
                updateRoleReviewRubric(catalog, draft, group.id, rubric),
                "判断基準をローカル保存しました。",
              )
            }
            onSelect={(candidateId) =>
              commitDraft(
                selectRoleReviewCandidate(catalog, draft, group.id, candidateId),
                "候補を仮選択しました。明示確認までは未確定です。",
              )
            }
            player={player}
          />
        </>
      ) : null}

      <p aria-live="polite" className="min-h-5 text-sm text-muted-foreground" role="status">
        {notice}
      </p>
    </div>
  );
}

export function RoleReviewProgressPanel({
  current,
  onExport,
  onReset,
  phase,
  progress,
}: {
  current: number;
  onExport: () => void;
  onReset: () => void;
  phase: RoleReviewCatalog["phase"];
  progress: ReturnType<typeof summarizeRoleReviewDraft>;
}) {
  return (
    <Card className="bg-background/95 backdrop-blur lg:sticky lg:top-[4.5rem] lg:z-10">
      <CardContent className="grid gap-4 pt-1 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap gap-2">
            <Badge>
              {current + 1} / {progress.total}
            </Badge>
            <Badge variant="outline">
              {phase === "anchor" ? "Anchor" : "Lines"} · 確認 {progress.confirmed}
            </Badge>
            <Badge variant="outline">残り {progress.remaining}</Badge>
          </div>
          <div
            aria-label="role review進捗"
            aria-valuemax={progress.total}
            aria-valuemin={0}
            aria-valuenow={progress.confirmed}
            className="mt-3 h-2 overflow-hidden rounded-full bg-primary/15"
            role="progressbar"
          >
            <div
              className="gaya-progress h-full rounded-full bg-primary transition-[width] duration-150 motion-reduce:transition-none"
              style={{
                width:
                  progress.total === 0 ? "0%" : `${(progress.confirmed / progress.total) * 100}%`,
              }}
            />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button disabled={progress.remaining !== 0} onClick={onExport}>
            <Download aria-hidden="true" />
            {progress.total}件をexport
          </Button>
          <Button onClick={onReset} variant="destructive">
            <RotateCcw aria-hidden="true" />
            このbundleをリセット
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function PhaseLedger({ phase }: { phase: RoleReviewCatalog["phase"] }) {
  return (
    <nav aria-label="公開までのphase ledger" className="overflow-hidden rounded-md border bg-card">
      <ol className="grid grid-cols-3">
        {[
          { key: "anchor", index: "01", label: "Anchor" },
          { key: "line", index: "02", label: "Lines" },
          { key: "release", index: "03", label: "Release" },
        ].map((item) => {
          const current = item.key === phase;
          const passed = phase === "line" && item.key === "anchor";
          return (
            <li
              aria-current={current ? "step" : undefined}
              className={[
                "min-w-0 border-r px-3 py-3 last:border-r-0 sm:px-4",
                current ? "bg-primary/[0.08] text-primary" : "text-muted-foreground",
              ].join(" ")}
              key={item.key}
            >
              <span className="block font-mono text-[10px] tracking-[0.16em]">{item.index}</span>
              <span className="mt-1 flex items-center gap-1.5 text-sm font-semibold">
                {passed ? (
                  <CheckCircle2 aria-hidden="true" className="size-3.5 text-emerald-400" />
                ) : null}
                {item.label}
              </span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function RoleReviewWorkspace({
  catalog,
  draft,
  group,
  groupDraft,
  groupIndex,
  onClear,
  onConfirm,
  onNavigate,
  onNext,
  onPrevious,
  onRubric,
  onSelect,
  player,
}: {
  catalog: RoleReviewCatalog;
  draft: RoleReviewDraft;
  group: RoleReviewGroup;
  groupDraft: RoleReviewGroupDraft;
  groupIndex: number;
  onClear: () => void;
  onConfirm: () => void;
  onNavigate: (index: number) => void;
  onNext: () => void;
  onPrevious: () => void;
  onRubric: (rubric: RoleReviewRubric) => void;
  onSelect: (candidateId: string) => void;
  player: ReturnType<typeof useAudioPlayer>;
}) {
  const sameRole = catalog.groups
    .map((item, index) => ({ group: item, index, draft: draft.groups[index]! }))
    .filter((item) => roleKey(item.group) === roleKey(group));
  const required = requiredHeardCount(group, groupDraft);
  const canConfirm =
    isRoleReviewRubricComplete(groupDraft.rubric) &&
    groupDraft.heard_candidate_ids.includes(groupDraft.selected_candidate_id) &&
    groupDraft.heard_candidate_ids.length >= required;

  const playCandidate = (candidateIndex: number) => {
    const candidate = group.candidates[candidateIndex];
    if (!candidate) {
      return;
    }
    void player.toggle(candidate.audio);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (isEditableTarget(event.target)) {
      return;
    }
    if (/^[1-9]$/.test(event.key)) {
      const candidateIndex = Number(event.key) - 1;
      const candidate = group.candidates[candidateIndex];
      if (!candidate) {
        return;
      }
      event.preventDefault();
      if (event.altKey) {
        onSelect(candidate.id);
      } else {
        playCandidate(candidateIndex);
      }
      return;
    }
    if (event.key.toLowerCase() === "j") {
      event.preventDefault();
      onPrevious();
    }
    if (event.key.toLowerCase() === "k") {
      event.preventDefault();
      onNext();
    }
  };

  return (
    <section
      className="grid min-w-0 items-start gap-4 outline-none focus-visible:ring-2 focus-visible:ring-ring lg:grid-cols-[15rem_minmax(0,1fr)_18rem]"
      data-role-review-workspace
      onKeyDown={handleKeyDown}
      tabIndex={0}
    >
      <div className="min-w-0 lg:sticky lg:top-52">
        <div className="hidden lg:block">
          <RolePassport group={group} />
        </div>
        <details className="rounded-md border bg-card lg:hidden">
          <summary className="cursor-pointer px-4 py-3 text-sm font-semibold">
            役柄passportを開く
          </summary>
          <div className="border-t p-4">
            <RolePassport group={group} />
          </div>
        </details>
      </div>

      <div className="min-w-0 space-y-4">
        <RoleContinuityTimeline
          currentIndex={groupIndex}
          items={sameRole}
          onNavigate={onNavigate}
          reopenReasons={draft.role_reopen_requests
            .filter((item) => item.model === group.model && item.character === group.character)
            .map((item) => item.reason)}
        />

        <Card>
          <CardHeader>
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">{group.model}</Badge>
              <Badge variant="outline">{group.scenario}</Badge>
              <Badge variant="outline">
                heard {groupDraft.heard_candidate_ids.length} / {required}+
              </Badge>
              {groupDraft.confirmed ? (
                <Badge className="border-emerald-400/35 bg-emerald-400/10 text-emerald-300">
                  <CheckCircle2 aria-hidden="true" />
                  明示確認済み
                </Badge>
              ) : (
                <Badge variant="outline">未確認</Badge>
              )}
            </div>
            <CardTitle>{group.line?.text ?? `${group.role.name} のrole anchor`}</CardTitle>
            <CardDescription>
              {group.line
                ? `${group.line.id} · 演技指示: ${group.line.delivery}`
                : "この役柄の声線を固定するためのanchor候補です。"}
            </CardDescription>
          </CardHeader>
          <CandidateGroupChangeNotice reason={groupDraft.candidate_group_change_reason} />
          {group.comparison_required ? (
            <CardContent>
              <div className="rounded-md border border-destructive/45 bg-destructive/[0.07] p-3 text-sm">
                <p className="font-medium text-destructive">比較必須 · 異なる候補を2件以上</p>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
                  {group.comparison_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            </CardContent>
          ) : null}
        </Card>

        <div className="grid min-w-0 gap-3 sm:grid-cols-2">
          {group.candidates.map((candidate, candidateIndex) => {
            const selected = groupDraft.selected_candidate_id === candidate.id;
            const heard = groupDraft.heard_candidate_ids.includes(candidate.id);
            const provisional = group.provisional_candidate_id === candidate.id;
            const shortcut = candidateKeyboardShortcut(candidateIndex);
            const status = player.currentClipKey === candidate.audio.key ? player.status : "idle";
            const active = status === "loading" || status === "playing" || status === "paused";
            return (
              <Card
                className={selected ? "ring-2 ring-primary/75" : "ring-primary/15"}
                data-candidate-selected={selected ? "true" : "false"}
                key={candidate.id}
              >
                <CardHeader className="gap-3">
                  <div className="flex items-start justify-between gap-2">
                    <span className="grid size-10 shrink-0 place-items-center rounded-full border border-primary/40 bg-primary/[0.07] font-mono text-xl font-semibold text-primary">
                      {candidate.label}
                    </span>
                    <div className="flex flex-wrap justify-end gap-1.5">
                      {heard ? (
                        <Badge className="border-emerald-400/35 bg-emerald-400/10 text-emerald-300">
                          heard
                        </Badge>
                      ) : null}
                      <CandidateQcBadge qc={candidate.qc} />
                    </div>
                  </div>
                  <CardTitle className="text-base">
                    候補 {candidate.label} · attempt {candidate.attempt}
                  </CardTitle>
                  <CardDescription className="font-mono text-[11px]">
                    seed {candidate.seed}
                  </CardDescription>
                  {provisional ? (
                    <p className="rounded-md border border-primary/30 bg-primary/[0.05] px-2.5 py-2 text-xs leading-5">
                      初期表示候補。機械的な正解・推奨を意味しません。
                    </p>
                  ) : null}
                </CardHeader>
                <CardContent className="space-y-2">
                  <Button
                    className="w-full"
                    onClick={() => playCandidate(candidateIndex)}
                    variant={active ? "secondary" : "outline"}
                  >
                    {status === "loading" || status === "playing" ? (
                      <Pause aria-hidden="true" />
                    ) : (
                      <Play aria-hidden="true" />
                    )}
                    {active ? "一時停止 / 再開" : shortcut === null ? "再生" : `再生 [${shortcut}]`}
                  </Button>
                  <Button
                    className="w-full"
                    onClick={() => onSelect(candidate.id)}
                    variant={selected ? "default" : "ghost"}
                  >
                    <Check aria-hidden="true" />
                    {selected
                      ? "仮選択中"
                      : shortcut === null
                        ? "仮選択"
                        : `仮選択 [Alt+${shortcut}]`}
                  </Button>
                </CardContent>
              </Card>
            );
          })}
        </div>

        <Card>
          <CardHeader>
            <CardTitle>このgroupの判断記録</CardTitle>
            <CardDescription>
              全基準を明示し、候補を最後まで再生してからrole epochへ固定します。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <CompletionRubricFields
              onChange={onRubric}
              phase={catalog.phase}
              value={groupDraft.rubric}
            />
          </CardContent>
        </Card>

        <Card className={groupDraft.confirmed ? "border-emerald-400/35" : "border-primary/35"}>
          <CardContent className="space-y-3 pt-1">
            <p className="text-sm leading-6 text-muted-foreground">
              {canConfirm
                ? "確認可能です。現在の候補、role epoch、group hashへ判断を固定します。"
                : `全基準を入力し、選択候補を含む異なる候補を${required}件以上最後まで再生してください。`}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button disabled={!canConfirm || groupDraft.confirmed} onClick={onConfirm}>
                <CheckCircle2 aria-hidden="true" />
                このgroupを明示確認
              </Button>
              {groupDraft.confirmed ? (
                <Button onClick={onClear} variant="outline">
                  確認を解除
                </Button>
              ) : null}
            </div>
          </CardContent>
        </Card>

        <div className="flex justify-between gap-3">
          <Button disabled={groupIndex === 0} onClick={onPrevious} variant="outline">
            <ChevronLeft aria-hidden="true" />
            前 [J]
          </Button>
          <Button
            disabled={groupIndex + 1 >= catalog.groups.length}
            onClick={onNext}
            variant="outline"
          >
            次 [K]
            <ChevronRight aria-hidden="true" />
          </Button>
        </div>
      </div>

      <div className="min-w-0 lg:sticky lg:top-52">
        <div className="hidden lg:block">
          <CompletionJudgmentCriteria />
        </div>
        <details className="rounded-md border bg-card lg:hidden">
          <summary className="cursor-pointer px-4 py-3 text-sm font-semibold">
            判断基準の詳細を開く
          </summary>
          <div className="border-t p-4">
            <CompletionJudgmentCriteria />
          </div>
        </details>
      </div>
    </section>
  );
}

export function CandidateGroupChangeNotice({ reason }: { reason: string | null }) {
  if (reason === null) {
    return null;
  }
  return (
    <CardContent>
      <div
        className="rounded-md border border-destructive/55 bg-destructive/[0.08] p-3 text-sm"
        data-candidate-group-change
      >
        <p className="font-semibold text-destructive">候補group変更・要再評価</p>
        <p className="mt-1 leading-6 text-muted-foreground">{reason}</p>
      </div>
    </CardContent>
  );
}

export function CandidateQcBadge({ qc }: { qc: RoleReviewQc }) {
  if (qc.mechanical === "fail") {
    return <Badge variant="destructive">Mechanical fail</Badge>;
  }
  if (qc.content === "review_required") {
    return <Badge variant="destructive">Content要確認</Badge>;
  }
  if (qc.content === "not_checked") {
    return <Badge variant="outline">Content未確認</Badge>;
  }
  return (
    <Badge className="border-emerald-400/35 bg-emerald-400/10 text-emerald-300">QC pass</Badge>
  );
}

export function RolePassport({ group }: { group: RoleReviewGroup }) {
  const role = group.role;
  const childReferenceApproximation =
    role.gender === "male" &&
    (role.age === "child" || role.age === "teen") &&
    group.coverage.gender === "exact" &&
    group.coverage.age === "approximate";
  return (
    <Card data-role-passport>
      <CardHeader>
        <p className="font-mono text-[10px] tracking-[0.18em] text-primary uppercase">
          Role passport
        </p>
        <CardTitle className="flex items-center gap-2">
          <UserRound aria-hidden="true" className="size-5 text-primary" />
          {role.name}
        </CardTitle>
        <CardDescription>
          {group.scenario} / {group.character}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-2 gap-y-2">
          <dt className="text-muted-foreground">Gender</dt>
          <dd>{genderLabel(role.gender)}</dd>
          <dt className="text-muted-foreground">Age</dt>
          <dd>{ageLabel(role.age)}</dd>
          <dt className="text-muted-foreground">Archetype</dt>
          <dd>{role.archetype}</dd>
          <dt className="text-muted-foreground">Kind</dt>
          <dd>{role.kind}</dd>
        </dl>

        <div className="space-y-2 border-t pt-3">
          <PassportText label="声質" value={role.voice} />
          <PassportText label="人格" value={role.personality} />
        </div>

        <div className="space-y-2 border-t pt-3">
          <p className="font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
            Conditioning coverage
          </p>
          <CoverageRow label="gender" value={group.coverage.gender} />
          <CoverageRow label="age" value={group.coverage.age} />
          <CoverageRow label="archetype" value={group.coverage.archetype} />
          {childReferenceApproximation ? (
            <p className="rounded-md border border-primary/30 bg-primary/[0.05] p-2 text-xs leading-5">
              成人男性reference: gender exact / age approximate
            </p>
          ) : null}
        </div>

        <div className="space-y-1 border-t pt-3">
          <p className="font-medium">{group.conditioning.method}</p>
          <p className="text-xs leading-5 text-muted-foreground">{group.conditioning.summary}</p>
        </div>
      </CardContent>
    </Card>
  );
}

export function CompletionJudgmentCriteria() {
  const criteria = [
    ["内容 / 漏洩", "台詞の欠落・追加・反復と、提示語・メタ文の音声漏洩"],
    ["漢字読み", "文脈上の正しい読み。漢字の誤読を独立して確認"],
    ["厳密pitch accent", "理論上読めていても、日本語の音調が違えば不適合"],
    ["Gender / Age", "指定性別と年齢帯。coverageがapproximateなら特に比較"],
    ["Archetype", "職能・種別・役柄としての声線"],
    ["Voice identity", "anchorと同役の前後行が同じ人物として連続するか"],
    ["Delivery", "感情、強度、話速、声量、語尾の演技指示"],
    ["自然度 / 音質", "棒読み、ノイズ、破綻を含む総合品質"],
  ] as const;
  return (
    <Card className="border-primary/35" data-judgment-panel>
      <CardHeader>
        <p className="font-mono text-[10px] tracking-[0.18em] text-primary uppercase">
          Always visible
        </p>
        <CardTitle className="flex items-center gap-2">
          <Headphones aria-hidden="true" className="size-5 text-primary" />
          現在の判断基準
        </CardTitle>
        <CardDescription>初期表示候補も人が確認する。skipは作りません。</CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="space-y-3">
          {criteria.map(([label, help], index) => (
            <li className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-2" key={label}>
              <span className="font-mono text-xs text-primary">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span>
                <span className="block text-sm font-medium">{label}</span>
                <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">{help}</span>
              </span>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function RoleContinuityTimeline({
  currentIndex,
  items,
  onNavigate,
  reopenReasons,
}: {
  currentIndex: number;
  items: readonly {
    readonly group: RoleReviewGroup;
    readonly index: number;
    readonly draft: RoleReviewGroupDraft;
  }[];
  onNavigate: (index: number) => void;
  reopenReasons: readonly string[];
}) {
  const first = items[0];
  if (!first) {
    throw new Error("role timeline item がありません。");
  }
  return (
    <Card data-role-continuity-timeline>
      <CardHeader>
        <p className="font-mono text-[10px] tracking-[0.18em] text-primary uppercase">
          Role continuity timeline
        </p>
        <CardTitle className="text-base">{first.group.role.name} · 同役の連続確認</CardTitle>
        <CardDescription className="font-mono text-[11px]">
          EPOCH {first.group.role_epoch_sha256.slice(0, 16)}…
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="relative space-y-2 pl-5 before:absolute before:top-1 before:bottom-1 before:left-[0.42rem] before:w-px before:bg-border">
          <div className="relative text-xs text-muted-foreground before:absolute before:top-1 before:-left-[1.13rem] before:size-2 before:rounded-full before:bg-primary">
            role epoch start
          </div>
          {reopenReasons.map((reason) => (
            <div
              className="relative rounded-md border border-destructive/45 bg-destructive/[0.06] p-2 text-xs leading-5 before:absolute before:top-3 before:-left-[1.19rem] before:size-2.5 before:rounded-full before:bg-destructive"
              data-role-reopen-event
              key={reason}
            >
              <span className="font-medium text-destructive">REOPEN</span> {reason}
            </div>
          ))}
          {items.map((item, roleIndex) => (
            <button
              aria-current={item.index === currentIndex ? "step" : undefined}
              className={[
                "relative block w-full rounded-md border px-3 py-2 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring",
                item.index === currentIndex
                  ? "border-primary/50 bg-primary/[0.07]"
                  : "bg-background hover:border-primary/35",
                "before:absolute before:top-3 before:-left-[1.19rem] before:size-2.5 before:rounded-full",
                item.draft.candidate_group_change_reason
                  ? "before:bg-destructive"
                  : item.draft.confirmed
                    ? "before:bg-emerald-400"
                    : "before:bg-muted-foreground",
              ].join(" ")}
              key={item.group.id}
              onClick={() => onNavigate(item.index)}
              type="button"
            >
              <span className="font-mono text-[10px] text-muted-foreground">
                {String(roleIndex + 1).padStart(2, "0")}
              </span>
              <span
                className={
                  item.draft.candidate_group_change_reason ? "ml-2 text-destructive" : "ml-2"
                }
              >
                {item.group.line?.id ?? "anchor"} ·{" "}
                {item.draft.candidate_group_change_reason
                  ? "候補変更・要再評価"
                  : item.draft.confirmed
                    ? "確認済み"
                    : "未確認"}
              </span>
            </button>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

export function MobilePersistentSummary({ group }: { group: RoleReviewGroup | null }) {
  return (
    <aside
      className="sticky top-16 z-10 rounded-md border border-primary/35 bg-background/95 p-3 backdrop-blur lg:hidden"
      data-mobile-role-summary
    >
      <p className="text-xs leading-5 text-muted-foreground">
        現在の基準: 内容・漏洩 / 漢字読み / 厳密pitch accent / 役柄 / 同一性 / 演技 / 音質
      </p>
      {group ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Badge variant="outline">Gender {genderLabel(group.role.gender)}</Badge>
          <Badge variant="outline">Age {ageLabel(group.role.age)}</Badge>
          <Badge variant="outline">Archetype {group.role.archetype}</Badge>
        </div>
      ) : null}
    </aside>
  );
}

function CoverageRow({
  label,
  value,
}: {
  label: string;
  value: RoleReviewGroup["coverage"]["gender"];
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs text-muted-foreground">{label}</span>
      <Badge variant="outline">{value}</Badge>
    </div>
  );
}

function PassportText({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-xs leading-5">{value}</p>
    </div>
  );
}

function genderLabel(value: RoleReviewGroup["role"]["gender"]): string {
  return { female: "女性", male: "男性", neutral: "中性" }[value];
}

function ageLabel(value: RoleReviewGroup["role"]["age"]): string {
  return {
    child: "子供",
    teen: "十代",
    young_adult: "若年成人",
    adult: "成人",
    middle_aged: "中年",
    elderly: "高齢",
  }[value];
}

function isEditableTarget(target: EventTarget): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    target instanceof HTMLButtonElement
  );
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
