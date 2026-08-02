import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  LockKeyhole,
  Pause,
  Play,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { useAudioPlayer, usePlaybackManager } from "@/audio/audio-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { buildBaselineDecisionJson } from "./baseline-export";
import { baselineInteractionLocks, navigateBaselineGroup } from "./baseline-interactions";
import { candidateShortcutIndex, candidateShortcutLabel } from "./listening-shortcuts";
import {
  applyBaselinePlaybackCompletion,
  assertBaselineDraft,
  clearBaselineDecision,
  createBaselineDraft,
  isBaselineRubricComplete,
  selectBaselineCandidate,
  summarizeBaselineDraft,
  updateBaselineRubric,
} from "./baseline-storage";
import {
  createLocalBaselineCatalog,
  finalizeLocalListening,
  loadLocalListeningDraft,
  saveLocalListeningDraft,
  type BaselineListeningBootstrap,
} from "./local-listening-session";
import {
  baselineGroupKey,
  type BaselineCandidateDraft,
  type BaselineCandidatePresentation,
  type BaselineCatalog,
  type BaselineDraft,
  type BaselineGroup,
  type BaselineRubric,
} from "./baseline-types";

const PASS_RUBRIC: BaselineRubric = {
  content_correct: true,
  prompt_leakage: false,
  reading_correct: true,
  accent_naturalness: 4,
  role_match: 4,
  delivery_match: 4,
  audio_quality: 4,
  adoptable: true,
  notes: "",
};

export function BaselineCompletionPage({
  bootstrap,
}: {
  readonly bootstrap: BaselineListeningBootstrap;
}) {
  const player = useAudioPlayer();
  const playbackManager = usePlaybackManager();
  const catalogRef = useRef<BaselineCatalog | null>(null);
  const draftRef = useRef<BaselineDraft | null>(null);
  const revisionRef = useRef(bootstrap.revision);
  const saveTailRef = useRef<Promise<void>>(Promise.resolve());
  const saveSequenceRef = useRef(0);
  const saveFailureRef = useRef<Error | null>(null);
  const submissionRef = useRef(false);
  const handledPlaybackSessionRef = useRef<number | null>(null);
  const [catalog, setCatalog] = useState<BaselineCatalog | null>(null);
  const [draft, setDraft] = useState<BaselineDraft | null>(null);
  const [groupIndex, setGroupIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("正在读取已验证的全量基线听测数据…");
  const [saving, setSaving] = useState(true);
  const [finalized, setFinalized] = useState(bootstrap.finalized);
  const [submitting, setSubmitting] = useState(false);

  const fail = useCallback((reason: unknown) => {
    const failure = reason instanceof Error ? reason : new Error(String(reason));
    saveFailureRef.current = failure;
    setSaving(false);
    setError(failure.message);
  }, []);

  const persistDraft = useCallback(
    (next: BaselineDraft, message: string): Promise<void> => {
      const currentCatalog = catalogRef.current;
      if (!currentCatalog || submissionRef.current || saveFailureRef.current) {
        return Promise.resolve();
      }
      assertBaselineDraft(next, currentCatalog);
      draftRef.current = next;
      setDraft(next);
      setNotice(message);
      setSaving(true);
      const sequence = saveSequenceRef.current + 1;
      saveSequenceRef.current = sequence;
      const task = saveTailRef.current.then(async () => {
        if (saveFailureRef.current) return;
        try {
          const saved = await saveLocalListeningDraft(bootstrap, revisionRef.current, next);
          revisionRef.current = saved.revision;
          if (sequence === saveSequenceRef.current) setSaving(false);
        } catch (reason: unknown) {
          fail(reason);
        }
      });
      saveTailRef.current = task;
      return task;
    },
    [bootstrap, fail],
  );

  const commitDraft = useCallback(
    (update: (current: BaselineDraft) => BaselineDraft, message: string) => {
      if (finalized || submissionRef.current || saveFailureRef.current) return;
      const current = draftRef.current;
      if (!current) return;
      const next = update(current);
      const addedSelection =
        summarizeBaselineDraft(next).selected > summarizeBaselineDraft(current).selected;
      void persistDraft(next, message);
      if (addedSelection) {
        const nextIndex = next.groups.findIndex((group) => group.decision === null);
        if (nextIndex >= 0) {
          playbackManager.stop();
          setGroupIndex(nextIndex);
        }
      }
    },
    [finalized, persistDraft, playbackManager],
  );

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const loadedCatalog = createLocalBaselineCatalog(bootstrap);
        const stored = await loadLocalListeningDraft<BaselineDraft>(bootstrap);
        const loadedDraft = stored?.draft ?? createBaselineDraft(loadedCatalog);
        assertBaselineDraft(loadedDraft, loadedCatalog);
        if (!active) return;
        catalogRef.current = loadedCatalog;
        draftRef.current = loadedDraft;
        revisionRef.current = stored?.revision ?? bootstrap.revision;
        setCatalog(loadedCatalog);
        setDraft(loadedDraft);
        setGroupIndex(firstUnselectedIndex(loadedDraft));
        const progress = summarizeBaselineDraft(loadedDraft);
        setNotice(
          stored ? `已恢复 ${progress.selected} 组判断。` : `已载入 ${progress.total} 组候选。`,
        );
        if (bootstrap.finalized) {
          setSaving(false);
          setFinalized(true);
        } else if (stored) {
          setSaving(false);
        } else {
          await persistDraft(loadedDraft, `已载入 ${progress.total} 组候选。`);
        }
      } catch (reason: unknown) {
        if (active) fail(reason);
      }
    };
    void load();
    return () => {
      active = false;
      playbackManager.stop();
      catalogRef.current?.dispose();
    };
  }, [bootstrap, fail, persistDraft, playbackManager]);

  useEffect(() => {
    const completion = player.completion;
    if (
      completion === null ||
      handledPlaybackSessionRef.current === completion.sessionId ||
      finalized ||
      submissionRef.current
    ) {
      return;
    }
    handledPlaybackSessionRef.current = completion.sessionId;
    const currentCatalog = catalogRef.current;
    const currentDraft = draftRef.current;
    if (!currentCatalog || !currentDraft) return;
    try {
      const next = applyBaselinePlaybackCompletion(currentCatalog, currentDraft, completion);
      if (next !== currentDraft) void persistDraft(next, "已记录完整播放。");
    } catch (reason: unknown) {
      fail(reason);
    }
  }, [fail, finalized, persistDraft, player.completion]);

  const finalize = async () => {
    if (finalized || submissionRef.current) return;
    const currentCatalog = catalogRef.current;
    const currentDraft = draftRef.current;
    if (!currentCatalog || !currentDraft) return;
    const progress = summarizeBaselineDraft(currentDraft);
    if (progress.remaining !== 0) {
      setNotice(`还有 ${progress.remaining} 组未选择，暂时不能锁定。`);
      return;
    }
    submissionRef.current = true;
    setSubmitting(true);
    setNotice("正在等待最后一笔草稿保存…");
    try {
      await saveTailRef.current;
      if (saveFailureRef.current) {
        setNotice("草稿保存失败，未锁定结果。请先处理页面上的错误。");
        return;
      }
      const latest = draftRef.current;
      if (!latest || summarizeBaselineDraft(latest).remaining !== 0) {
        throw new Error("最新草稿仍有未选择的项目，未锁定结果。");
      }
      assertBaselineDraft(latest, currentCatalog);
      const decision = JSON.parse(buildBaselineDecisionJson(currentCatalog, latest)) as Record<
        string,
        unknown
      >;
      const saved = await finalizeLocalListening(bootstrap, revisionRef.current, decision);
      revisionRef.current = saved.revision;
      setFinalized(true);
      setNotice(`全部 ${progress.total} 组已完成，最终结果已锁定并写入指定目录。`);
    } catch (reason: unknown) {
      fail(reason);
    } finally {
      submissionRef.current = false;
      setSubmitting(false);
    }
  };

  const group = catalog?.groups[groupIndex] ?? null;
  const groupDraft = draft?.groups[groupIndex] ?? null;
  const progress = draft ? summarizeBaselineDraft(draft) : null;
  const { mutationLocked, navigationLocked, playbackLocked } = baselineInteractionLocks({
    finalized,
    submitting,
    hasError: error !== null,
  });
  const navigate = useCallback(
    (index: number) => {
      navigateBaselineGroup({
        index,
        submitting: submissionRef.current,
        stopPlayback: playbackManager.stop,
        setIndex: setGroupIndex,
      });
    },
    [playbackManager],
  );

  return (
    <div
      className="mx-auto max-w-[1240px] space-y-3 pb-20"
      data-baseline-ui="role-baseline-decision-v1"
      lang="zh-CN"
    >
      <header className="sticky top-(--gaya-sticky-header-offset) z-10 rounded-md border bg-background/96 px-3 py-2 shadow-lg backdrop-blur">
        <div className="flex items-center justify-between gap-3">
          <div>
            <strong>全量基线候选听测</strong>
            <span className="ml-2 text-xs text-muted-foreground">
              {groupIndex + 1} / {progress?.total ?? 0} · 已选 {progress?.selected ?? 0}
            </span>
          </div>
          <Badge variant={finalized ? "default" : "outline"}>
            {finalized ? (
              <LockKeyhole aria-hidden="true" />
            ) : saving || submitting ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : (
              <CheckCircle2 aria-hidden="true" />
            )}
            {finalized ? "结果已锁定" : submitting ? "锁定中" : saving ? "保存中" : "已保存"}
          </Badge>
        </div>
      </header>

      <BaselineMobileCriteriaSummary group={group} />

      {error ? (
        <Card aria-live="assertive" className="border-destructive/55" role="alert">
          <CardHeader>
            <Badge variant="destructive">
              <AlertTriangle aria-hidden="true" />
              拒否
            </Badge>
            <CardTitle>无法使用听测数据或已保存结果</CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      {catalog && draft && group && groupDraft && progress ? (
        <>
          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-6">
              <div>
                <p className="font-mono text-xs text-muted-foreground">
                  GROUP {groupIndex + 1} / {progress.total}
                </p>
                <p className="mt-1 text-sm">
                  本组 {group.candidates.length} 条候选 · 剩余 {progress.remaining} 组
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={groupIndex === 0 || navigationLocked}
                  onClick={() => navigate(Math.max(0, groupIndex - 1))}
                  size="sm"
                  variant="outline"
                >
                  <ChevronLeft aria-hidden="true" />
                  上一组
                </Button>
                <Button
                  disabled={groupIndex === catalog.groups.length - 1 || navigationLocked}
                  onClick={() => navigate(Math.min(catalog.groups.length - 1, groupIndex + 1))}
                  size="sm"
                  variant="outline"
                >
                  下一组
                  <ChevronRight aria-hidden="true" />
                </Button>
              </div>
            </CardContent>
          </Card>

          <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
            <BaselineWorkspace
              catalog={catalog}
              draft={draft}
              group={group}
              groupIndex={groupIndex}
              mutationLocked={mutationLocked}
              navigationLocked={navigationLocked}
              playbackLocked={playbackLocked}
              onDraft={commitDraft}
              onNavigate={navigate}
              player={player}
            />
            <aside className="hidden lg:block">
              <BaselineDesktopCriteriaSticky />
            </aside>
          </div>

          <Card className={progress.remaining === 0 && !finalized ? "border-primary/60" : ""}>
            <CardContent className="flex flex-col gap-3 pt-6 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="font-medium">
                  {progress.remaining === 0 ? "全部项目已选择" : `还剩 ${progress.remaining} 组`}
                </p>
                <p className="text-xs text-muted-foreground">完成后请手动锁定。锁定后不能修改。</p>
              </div>
              <Button
                disabled={progress.remaining !== 0 || mutationLocked || saving}
                onClick={() => void finalize()}
                type="button"
              >
                {submitting ? (
                  <LoaderCircle aria-hidden="true" className="animate-spin" />
                ) : (
                  <LockKeyhole aria-hidden="true" />
                )}
                完成听测并锁定结果
              </Button>
            </CardContent>
          </Card>
        </>
      ) : null}

      <p aria-live="polite" className="min-h-5 text-sm text-muted-foreground" role="status">
        {notice} · 结果目录：{bootstrap.output.directory_name}
      </p>
    </div>
  );
}

function BaselineWorkspace({
  catalog,
  draft,
  group,
  groupIndex,
  mutationLocked,
  navigationLocked,
  playbackLocked,
  onDraft,
  onNavigate,
  player,
}: {
  readonly catalog: BaselineCatalog;
  readonly draft: BaselineDraft;
  readonly group: BaselineGroup;
  readonly groupIndex: number;
  readonly mutationLocked: boolean;
  readonly navigationLocked: boolean;
  readonly playbackLocked: boolean;
  readonly onDraft: (update: (current: BaselineDraft) => BaselineDraft, message: string) => void;
  readonly onNavigate: (index: number) => void;
  readonly player: ReturnType<typeof useAudioPlayer>;
}) {
  const groupDraft = draft.groups[groupIndex]!;
  const key = baselineGroupKey(group);
  const heardAll = groupDraft.heard_candidate_ids.length === group.candidates.length;
  const rubricsComplete = groupDraft.candidates.every((candidate) =>
    isBaselineRubricComplete(candidate.rubric),
  );
  const readyToSelect = heardAll && rubricsComplete;
  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (playbackLocked) return;
    const candidateIndex = candidateShortcutIndex(event, group.candidates.length);
    if (candidateIndex === null) return;
    event.preventDefault();
    void player.toggle(group.candidates[candidateIndex]!.audio);
  };

  return (
    <main className="space-y-3 outline-none" onKeyDown={handleKeyDown} tabIndex={0}>
      <BaselineEvidence group={group} selected={groupDraft.decision !== null} />
      {groupDraft.revalidation_reason ? (
        <p
          className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm text-amber-950 dark:text-amber-100"
          data-baseline-revalidation
        >
          {groupDraft.revalidation_reason}
        </p>
      ) : null}
      <label className="block rounded-md border bg-card p-3">
        <span className="text-xs font-medium text-muted-foreground">跳转到项目</span>
        <select
          className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
          disabled={navigationLocked}
          onChange={(event) => onNavigate(Number(event.currentTarget.value))}
          value={groupIndex}
        >
          {catalog.groups.map((item, index) => (
            <option key={baselineGroupKey(item)} value={index}>
              {index + 1}. {item.model} / {item.scenario} / {item.line} / {item.variant}
            </option>
          ))}
        </select>
      </label>

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          完整播放 {groupDraft.heard_candidate_ids.length}/{group.candidates.length}
        </span>
        <span title="数字键会播放或暂停对应候选；在输入框或选单中不会触发">
          快捷键 {candidateShortcutLabel(group.candidates.length)}：播放/暂停
        </span>
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        {group.candidates.map((candidate) => {
          const candidateDraft = groupDraft.candidates.find(
            (item) => item.take_id === candidate.takeId,
          );
          if (!candidateDraft) throw new Error(`candidate draftがありません: ${candidate.takeId}`);
          return (
            <BaselineCandidateCard
              candidate={candidate}
              candidateDraft={candidateDraft}
              heard={groupDraft.heard_candidate_ids.includes(candidate.takeId)}
              key={candidate.takeId}
              mutationLocked={mutationLocked}
              onRubric={(rubric) =>
                onDraft(
                  (current) =>
                    updateBaselineRubric(catalog, current, key, candidate.takeId, rubric),
                  `已保存候选 ${candidate.label} 的判断。`,
                )
              }
              onSelect={() =>
                onDraft(
                  (current) => {
                    const currentGroup = current.groups.find(
                      (item) => baselineGroupKey(item) === key,
                    );
                    if (!currentGroup) throw new Error(`Phase B groupがありません: ${key}`);
                    return currentGroup.decision?.take_id === candidate.takeId
                      ? clearBaselineDecision(catalog, current, key)
                      : selectBaselineCandidate(catalog, current, key, candidate.takeId);
                  },
                  groupDraft.decision?.take_id === candidate.takeId
                    ? "已取消这组的选择。"
                    : `已选择候选 ${candidate.label} 作为发布版本。`,
                )
              }
              player={player}
              playbackLocked={playbackLocked}
              readyToSelect={readyToSelect}
              selected={groupDraft.decision?.take_id === candidate.takeId}
            />
          );
        })}
      </div>
      {!readyToSelect ? (
        <p className="text-sm text-muted-foreground">
          请先把本组每条候选完整播放到结束，并完成每条判断。
        </p>
      ) : null}
    </main>
  );
}

function BaselineEvidence({
  group,
  selected,
}: {
  readonly group: BaselineGroup;
  readonly selected: boolean;
}) {
  return (
    <Card data-baseline-evidence>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{group.model}</Badge>
          <Badge variant="secondary">{group.scenario}</Badge>
          <Badge variant="secondary">
            {group.line} · {group.variant}
          </Badge>
          {selected ? (
            <Badge>
              <CheckCircle2 aria-hidden="true" />
              已选
            </Badge>
          ) : null}
        </div>
        <CardTitle>
          {group.role.name} · {group.scenarioTitle}
        </CardTitle>
        <CardDescription className="text-base leading-6 text-foreground">
          {group.lineText}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="grid gap-2 rounded-md bg-muted/60 p-3 sm:grid-cols-2">
          <Evidence label="角色">
            {genderLabel(group.role.gender)} · {ageLabel(group.role.age)} ·{" "}
            {kindLabel(group.role.kind)}
          </Evidence>
          <Evidence label="身份">{group.role.archetype}</Evidence>
          <Evidence label="声音" wide>
            {group.role.voice}
          </Evidence>
          <Evidence label="读法" wide>
            {group.reading ?? "无单独读法标注"}
          </Evidence>
          <Evidence label="处境" wide>
            {group.situation}
          </Evidence>
          <Evidence label="情绪 / 强度">
            {group.emotion} / {group.intensity}
          </Evidence>
          <Evidence label="说法要求" wide>
            {group.delivery}
          </Evidence>
        </div>
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">场景与性格</summary>
          <div className="mt-2 space-y-2">
            <p>
              <span className="font-medium text-foreground">场景：</span>
              {group.sceneSetting}
            </p>
            <p>
              <span className="font-medium text-foreground">性格：</span>
              {group.role.personality}
            </p>
          </div>
        </details>
        <p className="rounded-md border px-3 py-2 text-xs font-medium">
          本轮不判断前后行是否为同一人；只判断当前这一条。
        </p>
      </CardContent>
    </Card>
  );
}

function Evidence({
  children,
  label,
  wide = false,
}: {
  readonly children: ReactNode;
  readonly label: string;
  readonly wide?: boolean;
}) {
  return (
    <p className={wide ? "sm:col-span-2" : ""}>
      <span className="font-medium">{label}：</span>
      {children}
    </p>
  );
}

function BaselineCandidateCard({
  candidate,
  candidateDraft,
  heard,
  mutationLocked,
  onRubric,
  onSelect,
  player,
  playbackLocked,
  readyToSelect,
  selected,
}: {
  readonly candidate: BaselineCandidatePresentation;
  readonly candidateDraft: BaselineCandidateDraft;
  readonly heard: boolean;
  readonly mutationLocked: boolean;
  readonly onRubric: (rubric: BaselineRubric) => void;
  readonly onSelect: () => void;
  readonly player: ReturnType<typeof useAudioPlayer>;
  readonly playbackLocked: boolean;
  readonly readyToSelect: boolean;
  readonly selected: boolean;
}) {
  const [showProblems, setShowProblems] = useState(false);
  const status = player.currentClipKey === candidate.audio.key ? player.status : "idle";
  const active = status === "playing" || status === "loading" || status === "paused";
  const complete = isBaselineRubricComplete(candidateDraft.rubric);
  const summary = complete ? problemSummary(candidateDraft.rubric) : null;
  return (
    <Card className={selected ? "border-primary ring-1 ring-primary/30" : ""}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Badge>{candidate.label}</Badge>
            <span
              className={
                heard ? "text-xs font-medium text-primary" : "text-xs text-muted-foreground"
              }
            >
              {heard ? "已完整播放" : "请播放到结束"}
            </span>
          </div>
          <Button
            aria-label={`播放候选 ${candidate.label}`}
            disabled={playbackLocked}
            onClick={() => void player.toggle(candidate.audio)}
            size="sm"
            variant={active ? "default" : "outline"}
          >
            {status === "playing" || status === "loading" ? (
              <Pause aria-hidden="true" />
            ) : (
              <Play aria-hidden="true" />
            )}
            {status === "playing"
              ? "暂停"
              : status === "loading"
                ? "载入中"
                : status === "paused"
                  ? "继续"
                  : "播放"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {!showProblems && !complete ? (
          <div className="grid grid-cols-2 gap-2">
            <Button
              disabled={!heard || mutationLocked}
              onClick={() => onRubric(PASS_RUBRIC)}
              size="sm"
              type="button"
            >
              没有明显问题
            </Button>
            <Button
              disabled={!heard || mutationLocked}
              onClick={() => {
                onRubric(PASS_RUBRIC);
                setShowProblems(true);
              }}
              size="sm"
              type="button"
              variant="outline"
            >
              发现问题
            </Button>
          </div>
        ) : null}
        {complete && !showProblems ? (
          <div className="flex items-center justify-between gap-2 rounded-md bg-muted/60 px-3 py-2 text-xs">
            <span>{summary}</span>
            <Button
              disabled={mutationLocked}
              onClick={() => setShowProblems(true)}
              size="xs"
              type="button"
              variant="ghost"
            >
              修改
            </Button>
          </div>
        ) : null}
        {showProblems ? (
          <div className="space-y-2">
            <CompactBaselineRubricFields
              disabled={mutationLocked}
              onChange={onRubric}
              value={candidateDraft.rubric}
            />
            <Button
              className="w-full"
              disabled={!complete}
              onClick={() => setShowProblems(false)}
              size="sm"
              type="button"
              variant="secondary"
            >
              收起问题项
            </Button>
          </div>
        ) : null}
        <Button
          className="w-full"
          disabled={!readyToSelect || mutationLocked}
          onClick={onSelect}
          type="button"
          variant={selected ? "default" : "outline"}
        >
          <CheckCircle2 aria-hidden="true" />
          {selected ? "取消选择" : "选择这条发布"}
        </Button>
      </CardContent>
    </Card>
  );
}

function CompactBaselineRubricFields({
  disabled,
  onChange,
  value,
}: {
  readonly disabled: boolean;
  readonly onChange: (value: BaselineRubric) => void;
  readonly value: BaselineRubric;
}) {
  const set = <Key extends keyof BaselineRubric>(key: Key, next: BaselineRubric[Key]) =>
    onChange({ ...value, [key]: next });
  return (
    <div className="space-y-2 text-xs" data-baseline-rubric>
      <CompactBoolean
        label="内容正确"
        disabled={disabled}
        onChange={(next) => set("content_correct", next)}
        value={value.content_correct}
      />
      <CompactBoolean
        falseLabel="没有"
        disabled={disabled}
        label="提示词被读出"
        onChange={(next) => set("prompt_leakage", next)}
        trueLabel="有"
        value={value.prompt_leakage}
      />
      <CompactBoolean
        label="汉字读法正确"
        disabled={disabled}
        onChange={(next) => set("reading_correct", next)}
        value={value.reading_correct}
      />
      <CompactScore
        label="日语音调"
        disabled={disabled}
        onChange={(next) => set("accent_naturalness", next)}
        value={value.accent_naturalness}
      />
      <CompactScore
        label="角色符合"
        disabled={disabled}
        onChange={(next) => set("role_match", next)}
        value={value.role_match}
      />
      <CompactScore
        label="说法符合"
        disabled={disabled}
        onChange={(next) => set("delivery_match", next)}
        value={value.delivery_match}
      />
      <CompactScore
        label="整体音质"
        disabled={disabled}
        onChange={(next) => set("audio_quality", next)}
        value={value.audio_quality}
      />
      <CompactBoolean
        falseLabel="不能"
        disabled={disabled}
        label="可以发布"
        onChange={(next) => set("adoptable", next)}
        trueLabel="可以"
        value={value.adoptable}
      />
      <label className="flex items-center gap-2">
        <span className="w-20 shrink-0">备注（可选）</span>
        <input
          className="h-8 min-w-0 flex-1 rounded-md border bg-background px-2"
          disabled={disabled}
          onChange={(event) => set("notes", event.currentTarget.value)}
          placeholder="误读、角色、音质等"
          value={value.notes}
        />
      </label>
    </div>
  );
}

function CompactBoolean({
  disabled,
  falseLabel = "不对",
  label,
  onChange,
  trueLabel = "正确",
  value,
}: {
  readonly disabled: boolean;
  readonly falseLabel?: string;
  readonly label: string;
  readonly onChange: (value: boolean) => void;
  readonly trueLabel?: string;
  readonly value: boolean | null;
}) {
  return (
    <div className="grid grid-cols-[minmax(5rem,1fr)_4rem_4rem] items-center gap-1">
      <span>{label}</span>
      <Button
        aria-pressed={value === true}
        disabled={disabled}
        onClick={() => onChange(true)}
        size="xs"
        type="button"
        variant={value === true ? "default" : "outline"}
      >
        {trueLabel}
      </Button>
      <Button
        aria-pressed={value === false}
        disabled={disabled}
        onClick={() => onChange(false)}
        size="xs"
        type="button"
        variant={value === false ? "destructive" : "outline"}
      >
        {falseLabel}
      </Button>
    </div>
  );
}

function CompactScore({
  disabled,
  label,
  onChange,
  value,
}: {
  readonly disabled: boolean;
  readonly label: string;
  readonly onChange: (value: number) => void;
  readonly value: number | null;
}) {
  return (
    <div className="grid grid-cols-[minmax(5rem,1fr)_repeat(5,2rem)] items-center gap-1">
      <span title="1=明显不符合，5=非常符合">{label}</span>
      {[1, 2, 3, 4, 5].map((score) => (
        <Button
          aria-label={`${label} ${score}分`}
          aria-pressed={value === score}
          disabled={disabled}
          key={score}
          onClick={() => onChange(score)}
          size="icon-xs"
          type="button"
          variant={value === score ? "default" : "outline"}
        >
          {score}
        </Button>
      ))}
    </div>
  );
}

export function BaselineJudgmentCriteria() {
  return (
    <Card data-baseline-judgment-criteria>
      <CardHeader>
        <CardTitle>本轮判断标准</CardTitle>
        <CardDescription>每条都要实际听完，再选一条发布。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm leading-6">
        <Criterion label="内容、提示词、读法">
          是否照原文朗读；是否把提示词读出来；汉字读法和日语音调是否正确。
        </Criterion>
        <Criterion label="角色是否合适">性别、年龄、身份和声音感觉是否符合当前角色。</Criterion>
        <Criterion label="说法与整体质量">
          是否符合情绪和强度要求；是否棒读；是否有噪声或声音破损。
        </Criterion>
        <Criterion label="能否发布">综合以上问题，判断这条声音能否作为公开版本。</Criterion>
        <p className="rounded-md bg-muted p-3 text-xs font-medium">不判断前后行是否为同一人。</p>
      </CardContent>
    </Card>
  );
}

export function BaselineDesktopCriteriaSticky() {
  return (
    <div
      className="sticky top-[calc(var(--gaya-sticky-header-offset)+1rem)] z-10"
      data-baseline-desktop-criteria-sticky
    >
      <BaselineJudgmentCriteria />
    </div>
  );
}

export function BaselineMobileCriteriaSummary({ group }: { readonly group: BaselineGroup | null }) {
  return (
    <div
      className="sticky top-[calc(var(--gaya-sticky-header-offset)+3.25rem)] z-10 rounded-md border bg-background/95 p-3 shadow-sm backdrop-blur lg:hidden"
      data-baseline-mobile-criteria
    >
      <p className="text-xs font-semibold">判断：内容/提示词/读法 · 角色 · 说法/质量 · 能否发布</p>
      <p className="mt-1 truncate text-xs text-muted-foreground">
        每条实际听完；不判断前后行是否同一人
        {group ? ` · ${group.model} / ${group.scenario} / ${group.line}` : ""}
      </p>
    </div>
  );
}

function Criterion({ children, label }: { readonly children: string; readonly label: string }) {
  return (
    <section>
      <h3 className="font-medium">{label}</h3>
      <p className="text-muted-foreground">{children}</p>
    </section>
  );
}

function problemSummary(rubric: BaselineRubric): string {
  const problems: string[] = [];
  if (!rubric.content_correct) problems.push("内容");
  if (rubric.prompt_leakage) problems.push("提示词泄露");
  if (!rubric.reading_correct) problems.push("读法");
  if ((rubric.accent_naturalness ?? 5) < 4) problems.push("日语音调");
  if ((rubric.role_match ?? 5) < 4) problems.push("角色");
  if ((rubric.delivery_match ?? 5) < 4) problems.push("说法");
  if ((rubric.audio_quality ?? 5) < 4) problems.push("音质");
  if (!rubric.adoptable) problems.push("不可发布");
  if (rubric.notes.trim()) problems.push(`备注：${rubric.notes.trim()}`);
  return problems.length === 0 ? "未发现明显问题" : `问题：${problems.join("、")}`;
}

function genderLabel(value: string): string {
  return (
    ({ female: "女性", male: "男性", neutral: "中性" } as Record<string, string>)[value] ?? value
  );
}

function ageLabel(value: string): string {
  return (
    (
      {
        child: "儿童",
        teen: "少年",
        young_adult: "青年",
        adult: "成年",
        middle_aged: "中年",
        elderly: "老年",
      } as Record<string, string>
    )[value] ?? value
  );
}

function kindLabel(value: string): string {
  return (
    (
      {
        human: "人类",
        machine: "机械",
        creature: "生物",
        spirit: "精灵",
      } as Record<string, string>
    )[value] ?? value
  );
}

function firstUnselectedIndex(draft: BaselineDraft): number {
  const index = draft.groups.findIndex((group) => group.decision === null);
  return index === -1 ? 0 : index;
}
