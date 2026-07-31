import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  FolderOpen,
  Pause,
  Play,
  RotateCcw,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useAudioPlayer, usePlaybackManager } from "@/audio/audio-provider";
import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { loadBaselineCatalog } from "./baseline-contract";
import { buildBaselineDecisionJson, downloadBaselineDecisionJson } from "./baseline-export";
import { BaselineRubricFields } from "./baseline-rubric-fields";
import {
  clearBaselineDecision,
  isBaselineRubricComplete,
  readBaselineDraft,
  resetBaselineDraft,
  selectBaselineCandidate,
  summarizeBaselineDraft,
  updateBaselineRubric,
  writeBaselineDraft,
} from "./baseline-storage";
import {
  baselineGroupKey,
  type BaselineCatalog,
  type BaselineDraft,
  type BaselineGroup,
} from "./baseline-types";

export function BaselineCompletionPage() {
  const player = useAudioPlayer();
  const playbackManager = usePlaybackManager();
  const catalogRef = useRef<BaselineCatalog | null>(null);
  const loadTokenRef = useRef(0);
  const [catalog, setCatalog] = useState<BaselineCatalog | null>(null);
  const [draft, setDraft] = useState<BaselineDraft | null>(null);
  const [groupIndex, setGroupIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState(
    "Phase B listening bundleを選ぶと、plan・anchor・candidate set・role epochを検証します。",
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
    setNotice("Phase B exact bundleと全候補音声を検証しています…");
    playbackManager.stop();
    catalogRef.current?.dispose();
    catalogRef.current = null;
    setCatalog(null);
    setDraft(null);
    let loaded: BaselineCatalog | null = null;
    try {
      loaded = await loadBaselineCatalog(files);
      if (loadToken !== loadTokenRef.current) {
        loaded.dispose();
        return;
      }
      catalogRef.current = loaded;
      setCatalog(loaded);
      try {
        const restored = readBaselineDraft(localStorage, loaded);
        setDraft(restored);
        setGroupIndex(firstUnselectedIndex(restored));
        const progress = summarizeBaselineDraft(restored);
        setNotice(
          `${loaded.groups.length} groupを読み込みました。${progress.selected}件の判断を復元しました。`,
        );
      } catch (reason: unknown) {
        setError(errorMessage(reason));
        setGroupIndex(0);
        setNotice(
          "bundleは検証済みですが、保存済み状態を拒否しました。画面のresetで明示的に初期化してください。",
        );
      }
    } catch (reason: unknown) {
      if (loaded !== null && catalogRef.current !== loaded) {
        loaded.dispose();
      }
      if (loadToken !== loadTokenRef.current) {
        return;
      }
      setError(errorMessage(reason));
      setNotice("Phase B bundleを拒否しました。修正済みbundleを選び直してください。");
    } finally {
      if (loadToken === loadTokenRef.current) {
        setBusy(false);
      }
    }
  };

  const commitDraft = (next: BaselineDraft, message: string) => {
    if (!catalog) {
      return;
    }
    try {
      writeBaselineDraft(localStorage, catalog, next);
      setDraft(next);
      setError(null);
      setNotice(message);
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    }
  };

  const reset = () => {
    if (!catalog) {
      return;
    }
    try {
      const next = resetBaselineDraft(localStorage, catalog);
      setDraft(next);
      setGroupIndex(0);
      setError(null);
      setNotice("現在のPhase B bundleに対するローカル判断を初期化しました。");
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    }
  };

  const exportDecision = () => {
    if (!catalog || !draft) {
      return;
    }
    try {
      downloadBaselineDecisionJson(buildBaselineDecisionJson(catalog, draft));
      setError(null);
      setNotice(`${draft.groups.length} groupのrole baseline decisionを保存しました。`);
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    }
  };

  const group = catalog?.groups[groupIndex] ?? null;
  const groupDraft = draft?.groups[groupIndex] ?? null;
  const progress = draft ? summarizeBaselineDraft(draft) : null;

  return (
    <div className="space-y-5" data-baseline-ui="role-baseline-decision-v1">
      <PageIntro
        description="欠項をbest-of-Nで補うPhase B専用です。各候補について内容・読み・役柄・演技・自然度を記録し、公開する1件を選びます。"
        eyebrow="Issue #174 · Phase B baseline"
        title="全モデルの欠項baselineを人の耳で確定する"
      />

      <BaselineMobileCriteriaSummary group={group} />

      <Card className="border-primary/35">
        <CardHeader>
          <CardTitle>Phase B listening bundle を選択</CardTitle>
          <CardDescription>
            producerが出力した6 metadata fileと全audioだけをexactに読み込みます。旧completion
            protocolや不足・余分なfileは受け付けません。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <label className="inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground outline-none hover:bg-primary/85 focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2 focus-within:ring-offset-background">
            <FolderOpen aria-hidden="true" className="size-4" />
            {busy ? "検証中…" : "Phase B folderを選択"}
            <input
              accept=".json,.sha256,.opus"
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
              PLAN {catalog.planSha256.slice(0, 10)}… · ANCHOR{" "}
              {catalog.anchorSelectionSha256.slice(0, 10)}… · SET{" "}
              {catalog.candidateSetSha256.slice(0, 10)}…
            </span>
          ) : null}
          {catalog ? (
            <Button onClick={reset} size="sm" variant="outline">
              <RotateCcw aria-hidden="true" />
              reset
            </Button>
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
            <CardTitle>Phase Bデータまたは保存済み判断を使用できません</CardTitle>
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
                  選択済み {progress.selected} · 残り {progress.remaining}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={groupIndex === 0}
                  onClick={() => setGroupIndex((index) => Math.max(0, index - 1))}
                  size="sm"
                  variant="outline"
                >
                  <ChevronLeft aria-hidden="true" />前
                </Button>
                <Button
                  disabled={groupIndex === catalog.groups.length - 1}
                  onClick={() =>
                    setGroupIndex((index) => Math.min(catalog.groups.length - 1, index + 1))
                  }
                  size="sm"
                  variant="outline"
                >
                  次
                  <ChevronRight aria-hidden="true" />
                </Button>
                <Button disabled={progress.remaining !== 0} onClick={exportDecision} size="sm">
                  <Download aria-hidden="true" />
                  {progress.total}件をexport
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
              onDraft={commitDraft}
              onNavigate={setGroupIndex}
              player={player}
            />
            <aside className="hidden lg:block">
              <BaselineDesktopCriteriaSticky />
            </aside>
          </div>
        </>
      ) : null}

      <p aria-live="polite" className="min-h-5 text-sm text-muted-foreground" role="status">
        {notice}
      </p>
    </div>
  );
}

function BaselineWorkspace({
  catalog,
  draft,
  group,
  groupIndex,
  onDraft,
  onNavigate,
  player,
}: {
  readonly catalog: BaselineCatalog;
  readonly draft: BaselineDraft;
  readonly group: BaselineGroup;
  readonly groupIndex: number;
  readonly onDraft: (draft: BaselineDraft, message: string) => void;
  readonly onNavigate: (index: number) => void;
  readonly player: ReturnType<typeof useAudioPlayer>;
}) {
  const groupDraft = draft.groups[groupIndex]!;
  const key = baselineGroupKey(group);
  const complete = groupDraft.candidates.every((candidate) =>
    isBaselineRubricComplete(candidate.rubric),
  );

  return (
    <main className="space-y-5">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{group.model}</Badge>
            <Badge variant="secondary">{group.scenario}</Badge>
            <Badge variant="secondary">
              {group.line} · {group.variant}
            </Badge>
            {groupDraft.decision ? (
              <Badge>
                <CheckCircle2 aria-hidden="true" />
                選択済み
              </Badge>
            ) : null}
          </div>
          <CardTitle>{group.scenarioTitle}</CardTitle>
          <CardDescription className="text-base leading-7 text-foreground">
            {group.lineText}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p>
            <span className="font-medium">Delivery:</span> {group.delivery}
          </p>
          <p className="break-all font-mono text-xs text-muted-foreground">
            ROLE EPOCH {group.roleEpochSha256} · SOURCE {group.sourceRunId}
          </p>
          {groupDraft.revalidation_reason ? (
            <p
              className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-amber-950 dark:text-amber-100"
              data-baseline-revalidation
            >
              {groupDraft.revalidation_reason}
            </p>
          ) : null}
          <label className="block">
            <span className="text-xs font-medium text-muted-foreground">groupへ移動</span>
            <select
              className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
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
        </CardContent>
      </Card>

      {group.candidates.map((candidate) => {
        const candidateDraft = groupDraft.candidates.find(
          (item) => item.take_id === candidate.takeId,
        );
        if (!candidateDraft) {
          throw new Error(`candidate draftがありません: ${candidate.takeId}`);
        }
        const selected = groupDraft.decision?.take_id === candidate.takeId;
        const status = player.currentClipKey === candidate.audio.key ? player.status : "idle";
        const active = status === "playing" || status === "loading" || status === "paused";
        return (
          <Card
            className={selected ? "border-primary ring-1 ring-primary/30" : ""}
            key={candidate.takeId}
          >
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Badge>{candidate.label}</Badge>
                  <span className="font-mono text-xs text-muted-foreground">
                    {candidate.takeId.slice(0, 12)}…
                  </span>
                  {candidate.gateContent === "review_required" ? (
                    <Badge variant="destructive">content review required</Badge>
                  ) : (
                    <Badge variant="outline">gate pass</Badge>
                  )}
                </div>
                <Button
                  aria-label={`候補${candidate.label}を再生`}
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
                    ? "一時停止"
                    : status === "loading"
                      ? "読込中"
                      : status === "paused"
                        ? "再開"
                        : "再生"}
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-5">
              <BaselineRubricFields
                onChange={(rubric) =>
                  onDraft(
                    updateBaselineRubric(catalog, draft, key, candidate.takeId, rubric),
                    `候補${candidate.label}のrubricを保存しました。`,
                  )
                }
                value={candidateDraft.rubric}
              />
              <Button
                className="w-full"
                disabled={!complete}
                onClick={() =>
                  onDraft(
                    selected
                      ? clearBaselineDecision(catalog, draft, key)
                      : selectBaselineCandidate(catalog, draft, key, candidate.takeId),
                    selected
                      ? "このgroupの選択を解除しました。"
                      : `候補${candidate.label}をbaselineとして選択しました。`,
                  )
                }
                type="button"
                variant={selected ? "default" : "outline"}
              >
                <CheckCircle2 aria-hidden="true" />
                {selected ? "選択を解除" : "この候補を公開baselineに選ぶ"}
              </Button>
            </CardContent>
          </Card>
        );
      })}
      {!complete ? (
        <p className="text-sm text-muted-foreground">
          group内の全候補について全判断項目を入力すると、公開baselineを選択できます。
        </p>
      ) : null}
    </main>
  );
}

export function BaselineJudgmentCriteria() {
  return (
    <Card data-baseline-judgment-criteria>
      <CardHeader>
        <CardTitle>現在の判断基準</CardTitle>
        <CardDescription>候補を選ぶ前に、group内の全候補を同じ基準で評価します。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm leading-6">
        <Criterion label="内容 / 漏洩 / 読み / 音調">
          台詞内容、提示語・メタ文漏洩、語・漢字読み、厳密な日本語音調・アクセント。
        </Criterion>
        <Criterion label="Gender / Age / Archetype / Voice identity">
          受付嬢なら女性の受付役として聞こえるかなど、role epochの人物指定全体に照合。
        </Criterion>
        <Criterion label="Delivery / Naturalness">
          感情・強度・演技指示、棒読み感、自然さ、ノイズや音声破綻を照合。
        </Criterion>
        <Criterion label="Baseline採用可否 (adoptable)">
          上記を総合し、この候補を公開baselineとして実際に採用できるかを必ず決める。
        </Criterion>
        <p className="rounded-md bg-muted p-3 text-xs text-muted-foreground">
          gate passは採用判定ではありません。初期候補も全件、人が聴いてrubricを記録します。
        </p>
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
      className="sticky top-(--gaya-sticky-header-offset) z-10 rounded-md border bg-background/95 p-3 shadow-sm backdrop-blur lg:hidden"
      data-baseline-mobile-criteria
    >
      <p className="text-xs font-semibold">
        常時確認: 内容/漏洩/読み/音調 · 役柄 · 演技/自然度 · 採用可否
      </p>
      <p className="mt-1 truncate text-xs text-muted-foreground">
        Gender / Age / Archetype / Voice identity · adoptable
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

function firstUnselectedIndex(draft: BaselineDraft): number {
  const index = draft.groups.findIndex((group) => group.decision === null);
  return index === -1 ? 0 : index;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
