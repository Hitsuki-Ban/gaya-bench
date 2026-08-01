import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Circle,
  Headphones,
  Info,
  LoaderCircle,
  LockKeyhole,
  Pause,
  Play,
  Save,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

import { useAudioPlayer, usePlaybackManager } from "@/audio/audio-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { CompletionRubricFields } from "./completion-rubric-fields";
import { createRoleReviewCatalog } from "./contract";
import { buildRoleReviewDecision } from "./export";
import {
  finalizeLocalListening,
  loadLocalListeningBootstrap,
  loadLocalListeningDraft,
  localCandidateAudioUrl,
  saveLocalListeningDraft,
  type LocalListeningBootstrap,
} from "./local-listening-session";
import {
  applyRoleReviewPlaybackCompletion,
  completeAnchorRubric,
  confirmRoleReviewGroup,
  createRoleReviewDraft,
  markRoleReviewNoUsableCandidate,
  parseRoleReviewDraft,
  requiredHeardCount,
  roleReviewProblemCount,
  rubricHasProblems,
  selectRoleReviewCandidate,
  setCurrentRoleReviewGroup,
  summarizeRoleReviewDraft,
  updateRoleReviewRubric,
} from "./storage";
import type {
  RoleReviewCatalog,
  RoleReviewDraft,
  RoleReviewGroup,
  RoleReviewGroupDraft,
  RoleReviewRubric,
} from "./types";

type SaveState =
  | { readonly kind: "loading" }
  | { readonly kind: "saving" }
  | { readonly kind: "saved"; readonly at: string }
  | { readonly kind: "failed"; readonly message: string }
  | { readonly kind: "finalized" };

export function CompletionPage() {
  return <RoleReviewCompletionPage />;
}

export function RoleReviewCompletionPage() {
  const player = useAudioPlayer();
  const playbackManager = usePlaybackManager();
  const bootstrapRef = useRef<LocalListeningBootstrap | null>(null);
  const catalogRef = useRef<RoleReviewCatalog | null>(null);
  const draftRef = useRef<RoleReviewDraft | null>(null);
  const revisionRef = useRef(0);
  const saveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const saveSequenceRef = useRef(0);
  const saveFailureRef = useRef<Error | null>(null);
  const submissionRef = useRef(false);
  const handledPlaybackSessionRef = useRef<number | null>(null);
  const [bootstrap, setBootstrap] = useState<LocalListeningBootstrap | null>(null);
  const [catalog, setCatalog] = useState<RoleReviewCatalog | null>(null);
  const [draft, setDraft] = useState<RoleReviewDraft | null>(null);
  const [saveState, setSaveState] = useState<SaveState>({ kind: "loading" });
  const [notice, setNotice] = useState("正在读取指定的听测目录…");
  const [fatalError, setFatalError] = useState<string | null>(null);
  const [finalized, setFinalized] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const fail = useCallback((reason: unknown) => {
    const error = reason instanceof Error ? reason : new Error(String(reason));
    saveFailureRef.current = error;
    setFatalError(error.message);
    setSaveState({ kind: "failed", message: error.message });
  }, []);

  const persistDraft = useCallback(
    (next: RoleReviewDraft, message: string): Promise<void> => {
      draftRef.current = next;
      setDraft(next);
      setNotice(message);
      setSaveState({ kind: "saving" });
      const saveSequence = saveSequenceRef.current + 1;
      saveSequenceRef.current = saveSequence;
      const task = saveQueueRef.current.then(async () => {
        if (saveFailureRef.current) {
          return;
        }
        const session = bootstrapRef.current;
        if (!session) {
          fail(new Error("本地听测会话尚未准备完成。"));
          return;
        }
        try {
          const saved = await saveLocalListeningDraft(session, revisionRef.current, next);
          revisionRef.current = saved.revision;
          if (saveSequence === saveSequenceRef.current) {
            setSaveState({ kind: "saved", at: saved.saved_at });
          }
        } catch (reason: unknown) {
          fail(reason);
        }
      });
      saveQueueRef.current = task;
      return task;
    },
    [fail],
  );

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const session = await loadLocalListeningBootstrap();
        const loadedCatalog = await createRoleReviewCatalog(session.bundle, localCandidateAudioUrl);
        const stored = await loadLocalListeningDraft(session);
        if (!active) {
          return;
        }
        const loadedDraft = stored
          ? parseRoleReviewDraft(stored.draft, loadedCatalog)
          : createRoleReviewDraft(loadedCatalog);
        bootstrapRef.current = session;
        catalogRef.current = loadedCatalog;
        draftRef.current = loadedDraft;
        revisionRef.current = stored?.revision ?? session.revision;
        setBootstrap(session);
        setCatalog(loadedCatalog);
        setDraft(loadedDraft);
        setFinalized(session.finalized);
        setNotice(
          stored
            ? `已恢复 ${summarizeRoleReviewDraft(loadedDraft).confirmed} 个已确认项目。`
            : `已载入 ${loadedCatalog.groups.length} 组候选。`,
        );
        if (session.finalized) {
          setSaveState({ kind: "finalized" });
        } else if (stored) {
          setSaveState({ kind: "saved", at: "已从结果目录恢复" });
        } else {
          await persistDraft(loadedDraft, `已载入 ${loadedCatalog.groups.length} 组候选。`);
        }
      } catch (reason: unknown) {
        if (active) {
          fail(reason);
        }
      }
    };
    void load();
    return () => {
      active = false;
      playbackManager.stop();
    };
  }, [fail, persistDraft, playbackManager]);

  useEffect(() => {
    const completion = player.completion;
    if (
      completion === null ||
      handledPlaybackSessionRef.current === completion.sessionId ||
      finalized ||
      submitting
    ) {
      return;
    }
    handledPlaybackSessionRef.current = completion.sessionId;
    const currentCatalog = catalogRef.current;
    const currentDraft = draftRef.current;
    if (!currentCatalog || !currentDraft) {
      return;
    }
    try {
      const next = applyRoleReviewPlaybackCompletion(currentCatalog, currentDraft, completion);
      if (next !== currentDraft) {
        void persistDraft(next, "已记录完整播放。请选择最合适的候选。");
      }
    } catch (reason: unknown) {
      fail(reason);
    }
  }, [fail, finalized, persistDraft, player.completion, submitting]);

  if (fatalError) {
    return <ListeningFailure message={fatalError} />;
  }
  if (!bootstrap || !catalog || !draft) {
    return <ListeningLoading />;
  }

  const groupIndex = catalog.groups.findIndex((group) => group.id === draft.current_group_id);
  const group = catalog.groups[groupIndex];
  const groupDraft = draft.groups[groupIndex];
  if (!group || !groupDraft) {
    return <ListeningFailure message="草稿指向了不存在的听测项目。" />;
  }
  const progress = summarizeRoleReviewDraft(draft);

  const navigate = (index: number) => {
    if (finalized || submissionRef.current) {
      return;
    }
    const target = catalog.groups[index];
    if (!target) {
      return;
    }
    playbackManager.stop();
    void persistDraft(
      setCurrentRoleReviewGroup(catalog, draftRef.current!, target.id),
      `已打开第 ${index + 1} 组。`,
    );
  };

  const updateRubric = (rubric: RoleReviewRubric) => {
    if (finalized || submissionRef.current) {
      return;
    }
    void persistDraft(
      updateRoleReviewRubric(catalog, draftRef.current!, group.id, rubric),
      "问题标记已进入草稿。",
    );
  };

  const selectCandidate = (candidateId: string) => {
    if (finalized || submissionRef.current) {
      return;
    }
    const currentDraft = draftRef.current!;
    const currentGroupDraft = currentDraft.groups[groupIndex]!;
    if (currentGroupDraft.selected_candidate_id === candidateId) {
      return;
    }
    const replacingSelection =
      currentGroupDraft.selected_candidate_id !== null || currentGroupDraft.no_usable_candidate;
    const clearedProblems =
      replacingSelection &&
      (roleReviewProblemCount(currentGroupDraft.rubric) > 0 ||
        currentGroupDraft.rubric.notes.trim().length > 0);
    void persistDraft(
      selectRoleReviewCandidate(catalog, currentDraft, group.id, candidateId),
      clearedProblems
        ? "已改选候选；上一条候选的问题标记已清空。"
        : replacingSelection
          ? "已改选候选；最终确认前仍可修改。"
          : "已选择候选；现在可按需标记这条候选的问题。",
    );
  };

  const markNoUsableCandidate = () => {
    if (finalized || submissionRef.current) {
      return;
    }
    const currentGroupDraft = draftRef.current!.groups[groupIndex]!;
    if (currentGroupDraft.no_usable_candidate) {
      return;
    }
    const clearedProblems =
      roleReviewProblemCount(currentGroupDraft.rubric) > 0 ||
      currentGroupDraft.rubric.notes.trim().length > 0;
    void persistDraft(
      markRoleReviewNoUsableCandidate(catalog, draftRef.current!, group.id),
      clearedProblems
        ? "已改为整组不可用；之前的问题标记已清空，请重新标记原因。"
        : "已标记四条都不可用；请至少标记一个原因。",
    );
  };

  const confirm = async () => {
    if (finalized || submissionRef.current) {
      return;
    }
    const currentDraft = draftRef.current!;
    const currentGroupDraft = currentDraft.groups[groupIndex]!;
    const reason = confirmationBlockReason(group, currentGroupDraft);
    if (reason) {
      setNotice(reason);
      return;
    }
    submissionRef.current = true;
    setSubmitting(true);
    try {
      let next = confirmRoleReviewGroup(
        catalog,
        currentDraft,
        group.id,
        completeAnchorRubric(currentGroupDraft.rubric),
      );
      const nextProgress = summarizeRoleReviewDraft(next);
      if (nextProgress.confirmed === nextProgress.total) {
        await persistDraft(next, "全部判断已确认，正在写入最终结果…");
        await saveQueueRef.current;
        if (saveFailureRef.current) {
          return;
        }
        const saved = await finalizeLocalListening(
          bootstrap,
          revisionRef.current,
          buildRoleReviewDecision(catalog, next),
        );
        revisionRef.current = saved.revision;
        setFinalized(true);
        setSaveState({ kind: "finalized" });
        setNotice(`全部 ${nextProgress.total} 组已完成，最终结果已写入指定目录。`);
        return;
      }
      const nextIndex = nextUnconfirmedIndex(next, groupIndex);
      next = setCurrentRoleReviewGroup(catalog, next, catalog.groups[nextIndex]!.id);
      await persistDraft(next, `第 ${groupIndex + 1} 组已确认，自动进入下一组。`);
    } catch (reason: unknown) {
      fail(reason);
    } finally {
      submissionRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <div
      className="mx-auto max-w-[1240px] space-y-3 pb-24"
      data-role-review-ui="anchor-review-v2"
      lang="zh-CN"
    >
      <ProgressStrip
        current={groupIndex}
        finalized={finalized}
        progress={progress}
        saveState={saveState}
      />

      {finalized ? <CompletedBanner decisionFile={bootstrap.output.decision_file} /> : null}

      <EvidencePanel group={group} />

      <RoleReviewWorkspace
        finalized={finalized}
        group={group}
        groupDraft={groupDraft}
        isFinalConfirmation={!groupDraft.confirmed && progress.total - progress.confirmed === 1}
        onConfirm={() => void confirm()}
        onMarkNoUsable={markNoUsableCandidate}
        onNavigate={navigate}
        onRubric={updateRubric}
        onSelect={selectCandidate}
        player={player}
        submitting={submitting}
        total={catalog.groups.length}
        index={groupIndex}
      />

      <p aria-live="polite" className="min-h-5 text-xs text-muted-foreground" role="status">
        {notice}
      </p>
    </div>
  );
}

function ProgressStrip({
  current,
  finalized,
  progress,
  saveState,
}: {
  current: number;
  finalized: boolean;
  progress: ReturnType<typeof summarizeRoleReviewDraft>;
  saveState: SaveState;
}) {
  return (
    <header className="sticky top-(--gaya-sticky-header-offset) z-10 -mx-1 rounded-md border bg-background/96 px-3 py-2 shadow-lg shadow-black/10 backdrop-blur">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <strong className="text-sm text-foreground">角色声音四选一</strong>
            <Badge>
              {current + 1} / {progress.total}
            </Badge>
            <span className="text-muted-foreground">已确认 {progress.confirmed}</span>
            {progress.withProblems > 0 ? (
              <span className="text-destructive">有问题 {progress.withProblems}</span>
            ) : null}
          </div>
          <div
            aria-label="已确认项目"
            aria-valuemax={progress.total}
            aria-valuemin={0}
            aria-valuenow={progress.confirmed}
            className="mt-2 h-1.5 overflow-hidden rounded-full bg-primary/15"
            role="progressbar"
          >
            <div
              className="gaya-progress h-full rounded-full bg-primary transition-[width] duration-150 motion-reduce:transition-none"
              style={{ width: `${(progress.confirmed / progress.total) * 100}%` }}
            />
          </div>
        </div>
        <SaveIndicator finalized={finalized} state={saveState} />
      </div>
    </header>
  );
}

function EvidencePanel({ group }: { group: RoleReviewGroup }) {
  return (
    <section className="grid gap-3 rounded-md border border-primary/35 bg-card p-3 lg:grid-cols-[minmax(14rem,0.8fr)_minmax(20rem,1.4fr)]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="font-mono text-[10px] tracking-[0.16em] text-primary uppercase">
            角色要求
          </span>
          <h1 className="text-lg font-semibold">{group.role.name}</h1>
          <span className="text-xs text-muted-foreground">
            {genderLabel(group.role.gender)} · {ageLabel(group.role.age)} · {group.role.archetype}
          </span>
        </div>
        <p className="mt-2 text-sm leading-6">{group.role.voice}</p>
        <p className="text-xs leading-5 text-muted-foreground">{group.role.personality}</p>
      </div>
      <div className="min-w-0 rounded-md border bg-background/70 px-3 py-2.5">
        <p className="text-[10px] tracking-[0.14em] text-muted-foreground uppercase">应读文本</p>
        <p className="mt-1 text-base font-medium leading-7" lang="ja">
          {group.anchor_text}
        </p>
        <div className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-muted-foreground">
          <Info aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
          <span>
            本轮只看：性别必须符合；年龄和角色感觉大致符合；没有读错、提示词泄漏或明显音质问题。无需判断情绪表演。
          </span>
        </div>
      </div>
    </section>
  );
}

function RoleReviewWorkspace({
  finalized,
  group,
  groupDraft,
  index,
  isFinalConfirmation,
  onConfirm,
  onMarkNoUsable,
  onNavigate,
  onRubric,
  onSelect,
  player,
  submitting,
  total,
}: {
  finalized: boolean;
  group: RoleReviewGroup;
  groupDraft: RoleReviewGroupDraft;
  index: number;
  isFinalConfirmation: boolean;
  onConfirm: () => void;
  onMarkNoUsable: () => void;
  onNavigate: (index: number) => void;
  onRubric: (rubric: RoleReviewRubric) => void;
  onSelect: (candidateId: string) => void;
  player: ReturnType<typeof useAudioPlayer>;
  submitting: boolean;
  total: number;
}) {
  const locked = finalized || submitting;
  const heardCount = groupDraft.heard_candidate_ids.length;
  const selectedCandidate = group.candidates.find(
    (candidate) => candidate.id === groupDraft.selected_candidate_id,
  );
  const blockReason = confirmationBlockReason(group, groupDraft);
  const playCandidate = (candidateIndex: number) => {
    if (locked) {
      return;
    }
    const candidate = group.candidates[candidateIndex];
    if (candidate) {
      void player.toggle(candidate.audio);
    }
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (
      isEditableTarget(event.target) ||
      locked ||
      event.ctrlKey ||
      event.metaKey ||
      event.altKey
    ) {
      return;
    }
    const digitMatch = /^Digit([1-4])$/.exec(event.code);
    if (digitMatch) {
      const candidateIndex = Number(digitMatch[1]) - 1;
      const candidate = group.candidates[candidateIndex]!;
      event.preventDefault();
      if (event.shiftKey) {
        onSelect(candidate.id);
      } else {
        playCandidate(candidateIndex);
      }
    }
  };

  return (
    <section className="space-y-3 outline-none" onKeyDown={handleKeyDown} tabIndex={0}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">
          <strong>听完四条，再选择结果</strong>
          <span className="ml-2 text-xs text-muted-foreground">完整听过 {heardCount}/4</span>
        </p>
        <span className="text-[11px] text-muted-foreground" title="数字键播放；Shift + 数字键选择">
          快捷键 1–4
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4" aria-label="四个盲听候选" role="group">
        {group.candidates.map((candidate, candidateIndex) => {
          const heard = groupDraft.heard_candidate_ids.includes(candidate.id);
          const selected = groupDraft.selected_candidate_id === candidate.id;
          const playing =
            player.currentClipKey === candidate.audio.key &&
            (player.status === "playing" || player.status === "loading");
          const playbackFailed =
            player.currentClipKey === candidate.audio.key && player.status === "error";
          return (
            <article
              className={[
                "rounded-md border p-2 transition-colors",
                selected ? "border-primary bg-primary/[0.08]" : "bg-card",
                playing ? "ring-2 ring-primary/55" : "",
              ].join(" ")}
              data-active={playing}
              data-candidate={candidate.label}
              data-selected={selected}
              key={candidate.id}
            >
              <button
                aria-label={`${playing ? "暂停" : "播放"}候选 ${candidate.label}`}
                className="flex min-h-16 w-full items-center justify-center gap-2 rounded-sm bg-secondary px-3 text-left outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
                disabled={locked}
                onClick={() => playCandidate(candidateIndex)}
                type="button"
              >
                <span className="grid size-8 place-items-center rounded-full bg-background text-primary">
                  {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
                </span>
                <span>
                  <span className="block text-lg font-semibold">{candidate.label}</span>
                  <span className="block text-[11px] text-muted-foreground">
                    {playing
                      ? "播放中"
                      : playbackFailed
                        ? "播放失败"
                        : heard
                          ? "已完整听过"
                          : "尚未听完"}
                  </span>
                </span>
              </button>
              {playbackFailed ? (
                <p
                  className="mt-1 text-center text-[11px] text-destructive"
                  role="alert"
                  title={player.error?.message}
                >
                  播放失败，请重试
                </p>
              ) : null}
              <button
                aria-pressed={selected}
                className={[
                  "mt-2 flex h-8 w-full items-center justify-center gap-1.5 rounded-sm border text-xs font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  selected
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-background hover:bg-muted",
                ].join(" ")}
                disabled={locked}
                onClick={() => onSelect(candidate.id)}
                type="button"
              >
                {selected ? <CheckCircle2 aria-hidden="true" /> : <Circle aria-hidden="true" />}
                {selected ? "已选择" : `选择 ${candidate.label}`}
              </button>
            </article>
          );
        })}
      </div>

      <Button
        aria-pressed={groupDraft.no_usable_candidate}
        className="w-full"
        disabled={locked}
        onClick={onMarkNoUsable}
        type="button"
        variant={groupDraft.no_usable_candidate ? "destructive" : "outline"}
      >
        <AlertTriangle aria-hidden="true" />
        {groupDraft.no_usable_candidate ? "已标记：四条都不可用" : "四条都不符合角色或质量要求"}
      </Button>

      <CompletionRubricFields
        disabled={locked || (selectedCandidate === undefined && !groupDraft.no_usable_candidate)}
        key={`${group.id}:${selectedCandidate?.id ?? (groupDraft.no_usable_candidate ? "blocked" : "none")}`}
        onChange={onRubric}
        subjectLabel={
          selectedCandidate
            ? `候选 ${selectedCandidate.label}`
            : groupDraft.no_usable_candidate
              ? "整组"
              : null
        }
        value={groupDraft.rubric}
      />

      <details className="rounded-md border bg-card px-3 py-2 text-xs text-muted-foreground">
        <summary className="cursor-pointer select-none">技术信息</summary>
        <dl className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-[auto_1fr]">
          <dt>模型</dt>
          <dd className="break-all">{group.model}</dd>
          <dt>场景 / 角色</dt>
          <dd>
            {group.scenario} / {group.character}
          </dd>
          <dt>生成方式</dt>
          <dd>{group.conditioning.summary}</dd>
        </dl>
      </details>

      <div className="fixed inset-x-0 bottom-0 z-20 border-t bg-background/96 px-3 py-2 backdrop-blur">
        <div className="mx-auto flex max-w-[1240px] items-center gap-2">
          <Button
            aria-label="上一组"
            disabled={index === 0 || locked}
            onClick={() => onNavigate(index - 1)}
            size="icon-lg"
            type="button"
            variant="outline"
          >
            <ChevronLeft aria-hidden="true" />
          </Button>
          <Button
            aria-label="下一组"
            disabled={index === total - 1 || locked}
            onClick={() => onNavigate(index + 1)}
            size="icon-lg"
            type="button"
            variant="outline"
          >
            <ChevronRight aria-hidden="true" />
          </Button>
          <div className="min-w-0 flex-1 text-right text-xs text-muted-foreground">
            {finalized
              ? "结果已锁定"
              : submitting
                ? "正在保存并确认…"
                : (blockReason ??
                  (isFinalConfirmation
                    ? `${confirmationSummary(group, groupDraft)} · 完成后锁定，不能再修改`
                    : confirmationSummary(group, groupDraft)))}
          </div>
          <Button
            aria-disabled={Boolean(blockReason) || finalized}
            className="min-w-[12rem]"
            disabled={submitting || finalized}
            onClick={onConfirm}
            size="lg"
            type="button"
          >
            {finalized ? (
              <LockKeyhole aria-hidden="true" />
            ) : submitting ? (
              <LoaderCircle
                aria-hidden="true"
                className="animate-spin motion-reduce:animate-none"
              />
            ) : (
              <Check aria-hidden="true" />
            )}
            {finalized
              ? "已完成"
              : submitting
                ? "正在确认"
                : isFinalConfirmation
                  ? "确认本组并完成听测"
                  : groupDraft.confirmed
                    ? "更新并进入下一组"
                    : "确认并进入下一组"}
          </Button>
        </div>
      </div>
    </section>
  );
}

function SaveIndicator({ finalized, state }: { finalized: boolean; state: SaveState }) {
  if (finalized || state.kind === "finalized") {
    return (
      <span
        aria-atomic="true"
        aria-live="polite"
        className="flex shrink-0 items-center gap-1.5 text-xs text-emerald-300"
        role="status"
      >
        <LockKeyhole aria-hidden="true" />
        最终结果已保存
      </span>
    );
  }
  if (state.kind === "loading" || state.kind === "saving") {
    return (
      <span
        aria-atomic="true"
        aria-live="polite"
        className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground"
        role="status"
      >
        <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
        正在保存
      </span>
    );
  }
  if (state.kind === "failed") {
    return (
      <span
        aria-atomic="true"
        aria-live="polite"
        className="flex shrink-0 items-center gap-1.5 text-xs text-destructive"
        role="status"
      >
        <AlertTriangle aria-hidden="true" />
        保存失败
      </span>
    );
  }
  return (
    <span
      aria-atomic="true"
      aria-live="polite"
      className="flex shrink-0 items-center gap-1.5 text-xs text-emerald-300"
      role="status"
    >
      <Save aria-hidden="true" />
      {state.at === "已从结果目录恢复" ? "草稿已恢复" : `已保存 ${displaySavedAt(state.at)}`}
    </span>
  );
}

function CompletedBanner({ decisionFile }: { decisionFile: string }) {
  return (
    <section className="flex items-center gap-3 rounded-md border border-emerald-400/40 bg-emerald-400/10 p-3">
      <CheckCircle2 aria-hidden="true" className="size-5 text-emerald-300" />
      <div>
        <p className="font-medium">本轮听测已完成</p>
        <p className="text-xs text-muted-foreground">
          最终结果已写入 {decisionFile}，页面现为只读。
        </p>
      </div>
    </section>
  );
}

function ListeningLoading() {
  return (
    <section
      className="mx-auto flex min-h-[50vh] max-w-xl items-center justify-center text-center"
      lang="zh-CN"
    >
      <div>
        <Headphones aria-hidden="true" className="mx-auto size-8 text-primary" />
        <h1 className="mt-3 text-lg font-semibold">正在准备听测</h1>
        <p className="mt-1 text-sm text-muted-foreground">校验候选集并恢复指定结果目录中的草稿。</p>
      </div>
    </section>
  );
}

function ListeningFailure({ message }: { message: string }) {
  return (
    <section
      className="mx-auto max-w-2xl rounded-md border border-destructive/55 bg-destructive/[0.06] p-5"
      lang="zh-CN"
      role="alert"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-destructive" />
        <div>
          <h1 className="font-semibold">听测环境未能启动</h1>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">{message}</p>
          <p className="mt-3 text-xs text-muted-foreground">
            请停止当前本地会话，修正目录或数据后重新启动。
          </p>
        </div>
      </div>
    </section>
  );
}

function confirmationBlockReason(
  group: RoleReviewGroup,
  draft: RoleReviewGroupDraft,
): string | null {
  const missing = requiredHeardCount(group) - draft.heard_candidate_ids.length;
  if (missing > 0) {
    return `还需完整听 ${missing} 条`;
  }
  if (draft.selected_candidate_id === null) {
    if (!draft.no_usable_candidate) {
      return "请选择一个候选，或标记四条都不可用";
    }
    if (!rubricHasProblems(draft.rubric)) {
      return "请标记整组不可用的原因";
    }
  }
  return null;
}

function confirmationSummary(group: RoleReviewGroup, draft: RoleReviewGroupDraft): string {
  const selected = group.candidates.find(
    (candidate) => candidate.id === draft.selected_candidate_id,
  );
  const problems = roleReviewProblemCount(draft.rubric);
  if (draft.no_usable_candidate) {
    return `四条都不可用 · ${problems > 0 ? `${problems} 个问题` : "已填写说明"}`;
  }
  return `${selected ? `候选 ${selected.label}` : "未选择"} · ${problems > 0 ? `${problems} 个问题` : "无明显问题"}`;
}

function nextUnconfirmedIndex(draft: RoleReviewDraft, current: number): number {
  for (let offset = 1; offset <= draft.groups.length; offset += 1) {
    const index = (current + offset) % draft.groups.length;
    if (!draft.groups[index]!.confirmed) {
      return index;
    }
  }
  throw new Error("找不到下一条未确认项目。");
}

function displaySavedAt(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString("zh-CN", { hour12: false });
}

function genderLabel(value: RoleReviewGroup["role"]["gender"]): string {
  return { female: "女性", male: "男性", neutral: "中性" }[value];
}

function ageLabel(value: RoleReviewGroup["role"]["age"]): string {
  return {
    child: "儿童",
    teen: "少年",
    young_adult: "青年",
    adult: "成年",
    middle_aged: "中年",
    elderly: "老年",
  }[value];
}

function isEditableTarget(target: EventTarget): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
}
