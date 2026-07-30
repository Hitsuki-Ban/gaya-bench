import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  Headphones,
  LoaderCircle,
  Pause,
  Play,
  RotateCcw,
  Trophy,
} from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { useAudioProgress } from "@/audio/audio-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { WaveformProgress } from "@/components/waveform-progress";
import { DIFFICULTY_LABELS, EMOTION_LABELS } from "@/ui-labels";
import { resolveAbShortcut, type AbShortcut } from "./keyboard";
import { useAbSession, type CandidateSide, type PreviousVote } from "./use-ab-session";

export function AbComparison() {
  const session = useAbSession();
  const audioProgress = useAudioProgress();
  const [sessionMemo, setSessionMemo] = useState("");
  const terminalRef = useRef<HTMLDivElement>(null);
  const previousKindRef = useRef(session.kind);

  useEffect(() => {
    if (
      previousKindRef.current === "ready" &&
      (session.kind === "complete" || session.kind === "error")
    ) {
      terminalRef.current?.focus();
    }
    previousKindRef.current = session.kind;
  }, [session.kind]);

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const shortcut = resolveAbShortcut(event.nativeEvent);
    if (shortcut === null || session.kind !== "ready") {
      return;
    }
    event.preventDefault();
    runShortcut(shortcut, session.playCandidate, session.castVote);
  };

  return (
    <div className="space-y-6">
      {session.kind === "unavailable" || session.kind === "error" ? null : (
        <SessionProgress votesCount={session.votesCount} totalMatches={session.totalMatches} />
      )}

      {session.kind === "error" ? (
        <div
          className="outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          ref={terminalRef}
          tabIndex={-1}
        >
          <StorageError error={session.error} onReset={session.reset} />
        </div>
      ) : session.kind === "unavailable" ? (
        <UnavailableState modelCount={session.modelCount} />
      ) : session.kind === "complete" ? (
        <div
          className="space-y-4 outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          ref={terminalRef}
          tabIndex={-1}
        >
          {session.previousVote === null ? null : (
            <PreviousVoteNotice previousVote={session.previousVote} />
          )}
          <CompleteState total={session.totalMatches} />
        </div>
      ) : (
        <section
          aria-label="匿名音声の比較"
          className="space-y-4 rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          onKeyDown={handleKeyDown}
          ref={session.comparisonRef}
          tabIndex={0}
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-2">
              <Badge>{session.votesCount + 1} 問目</Badge>
              <Badge variant="outline">残り {session.remainingMatches}</Badge>
            </div>
            <p className="text-xs text-muted-foreground">モデル名は投票中の候補に表示されません</p>
          </div>

          {session.previousVote === null ? null : (
            <PreviousVoteNotice previousVote={session.previousVote} />
          )}

          <Card className="bg-primary/[0.035] ring-primary/25">
            <CardHeader>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary">{session.presentation.scenarioTitle}</Badge>
                <Badge variant="outline">{session.presentation.characterName}</Badge>
                <Badge variant="outline">{EMOTION_LABELS[session.presentation.emotion]}</Badge>
                {session.presentation.difficulty === "hard" ? (
                  <Badge variant="destructive">
                    {DIFFICULTY_LABELS[session.presentation.difficulty]}
                  </Badge>
                ) : null}
              </div>
              <CardTitle className="mt-2 text-xl leading-8 sm:text-2xl">
                {session.presentation.lineText}
              </CardTitle>
              <CardDescription>{session.presentation.delivery}</CardDescription>
            </CardHeader>
          </Card>

          <div className="grid items-stretch gap-4 lg:grid-cols-2">
            <CandidateCard
              currentTime={audioProgress.currentTime}
              fallbackDuration={session.presentation.left.candidate.duration_sec}
              label="A"
              onPlay={() => void session.playCandidate("left")}
              progressDuration={audioProgress.duration}
              shortcut="ArrowLeft"
              status={session.presentation.leftStatus}
            />
            <CandidateCard
              currentTime={audioProgress.currentTime}
              fallbackDuration={session.presentation.right.candidate.duration_sec}
              label="B"
              onPlay={() => void session.playCandidate("right")}
              progressDuration={audioProgress.duration}
              shortcut="ArrowRight"
              status={session.presentation.rightStatus}
            />
          </div>

          <div className="grid items-stretch gap-4 lg:grid-cols-[minmax(0,1.25fr)_minmax(18rem,0.75fr)]">
            <Card>
              <CardHeader>
                <CardTitle>どちらが好みですか？</CardTitle>
                <CardDescription>
                  必要なだけ聴き比べてから選んでください。保存後は次の比較へ自動で進みます。
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-2 sm:grid-cols-3">
                <VoteButton
                  disabled={session.isCommitting}
                  label="A が好み"
                  number="1"
                  onClick={() => session.castVote("left")}
                />
                <VoteButton
                  disabled={session.isCommitting}
                  label="引き分け"
                  number="2"
                  onClick={() => session.castVote("tie")}
                />
                <VoteButton
                  disabled={session.isCommitting}
                  label="B が好み"
                  number="3"
                  onClick={() => session.castVote("right")}
                />
              </CardContent>
            </Card>

            <SessionMemo
              onChange={setSessionMemo}
              onClear={() => setSessionMemo("")}
              value={sessionMemo}
            />
          </div>

          <KeyboardGuide />
        </section>
      )}

      {session.kind === "unavailable" || session.kind === "error" ? null : (
        <>
          <p aria-live="polite" className="min-h-5 text-sm text-muted-foreground" role="status">
            {session.notice}
          </p>

          <RankingCard rankings={session.rankings} votesCount={session.votesCount} />

          <div className="flex justify-end">
            <Button disabled={session.isResetting} onClick={session.reset} variant="destructive">
              {session.isResetting ? (
                <LoaderCircle
                  aria-hidden="true"
                  className="animate-spin"
                  data-icon="inline-start"
                />
              ) : (
                <RotateCcw aria-hidden="true" data-icon="inline-start" />
              )}
              ローカル結果をリセット
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

function SessionProgress({
  totalMatches,
  votesCount,
}: {
  totalMatches: number;
  votesCount: number;
}) {
  const ratio = votesCount / totalMatches;
  return (
    <div className="rounded-lg border border-primary/25 bg-primary/[0.035] px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-3 text-sm">
        <span className="font-medium">セッション進捗</span>
        <span className="font-mono text-primary">
          {votesCount} / {totalMatches}
        </span>
      </div>
      <div
        aria-label="セッション進捗"
        aria-valuemax={totalMatches}
        aria-valuemin={0}
        aria-valuenow={votesCount}
        className="h-2 overflow-hidden rounded-full bg-primary/15"
        role="progressbar"
      >
        <div
          aria-hidden="true"
          className="h-full rounded-full bg-primary transition-[width] duration-300 motion-reduce:transition-none"
          style={{ width: `${ratio * 100}%` }}
        />
      </div>
    </div>
  );
}

function PreviousVoteNotice({ previousVote }: { previousVote: PreviousVote }) {
  return (
    <aside
      aria-label="前回の投票"
      className="rounded-lg border border-primary/30 bg-primary/[0.04] px-4 py-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold tracking-[0.16em] text-primary">前回の投票</p>
        <p className="text-sm">
          選択: <strong className="text-primary">{previousVote.choice}</strong>
        </p>
      </div>
      <dl className="mt-2 grid gap-x-5 gap-y-1 font-mono text-xs sm:grid-cols-2">
        <div className="flex min-w-0 gap-2">
          <dt className="shrink-0 text-primary">A</dt>
          <dd className="min-w-0 break-words text-muted-foreground">
            {previousVote.leftModelName}
          </dd>
        </div>
        <div className="flex min-w-0 gap-2">
          <dt className="shrink-0 text-primary">B</dt>
          <dd className="min-w-0 break-words text-muted-foreground">
            {previousVote.rightModelName}
          </dd>
        </div>
      </dl>
    </aside>
  );
}

function CandidateCard({
  currentTime,
  fallbackDuration,
  label,
  progressDuration,
  shortcut,
  status,
  onPlay,
}: {
  currentTime: number;
  fallbackDuration: number;
  label: "A" | "B";
  progressDuration: number;
  shortcut: "ArrowLeft" | "ArrowRight";
  status: "idle" | "loading" | "playing" | "paused" | "error";
  onPlay: () => void;
}) {
  const isActive = status === "loading" || status === "playing" || status === "paused";
  const duration = isActive && progressDuration > 0 ? progressDuration : fallbackDuration;
  const elapsed = isActive && duration > 0 ? Math.min(Math.max(currentTime, 0), duration) : 0;
  const ratio = duration > 0 ? elapsed / duration : 0;
  return (
    <Card
      className={isActive ? "h-full bg-primary/[0.035] ring-primary/60" : "h-full ring-primary/20"}
    >
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <span className="grid size-12 place-items-center rounded-full border border-primary/35 bg-primary/[0.06] font-mono text-2xl font-semibold text-primary">
            {label}
          </span>
          <Badge variant={isActive ? "default" : status === "error" ? "destructive" : "outline"}>
            {candidateStatusLabel(status)}
          </Badge>
        </div>
        <CardTitle className="mt-2">候補 {label}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col">
        <div className="mb-4 rounded-md border border-primary/20 bg-background/45 px-3 py-3">
          <WaveformProgress className="h-16" ratio={ratio} />
          <div className="mt-2 flex justify-between font-mono text-[10px] text-muted-foreground">
            <span>{isActive ? formatTime(elapsed) : "0:00.0"}</span>
            <span>{formatTime(duration)}</span>
          </div>
        </div>
        <Button
          aria-keyshortcuts={shortcut}
          className="h-12 w-full text-base"
          onClick={onPlay}
          variant={isActive ? "secondary" : "outline"}
        >
          {status === "loading" || status === "playing" ? (
            <Pause aria-hidden="true" />
          ) : (
            <Play aria-hidden="true" />
          )}
          {candidateActionLabel(label, status)}
          <kbd className="ml-auto rounded border border-current/20 px-1.5 py-0.5 font-mono text-[10px]">
            {shortcut === "ArrowLeft" ? "←" : "→"}
          </kbd>
        </Button>
        {status === "error" ? (
          <p className="mt-2 text-xs text-destructive">
            この候補を再生できませんでした。もう一度お試しください。
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function SessionMemo({
  onChange,
  onClear,
  value,
}: {
  onChange: (value: string) => void;
  onClear: () => void;
  value: string;
}) {
  return (
    <Card className="ring-primary/20">
      <CardHeader className="gap-2">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="text-base">セッションメモ（このタブのみ）</CardTitle>
          <Button disabled={value.length === 0} onClick={onClear} size="sm" variant="ghost">
            クリア
          </Button>
        </div>
        <CardDescription>気づいた音の特徴を一時的に書き留められます。</CardDescription>
      </CardHeader>
      <CardContent>
        <textarea
          aria-label="セッションメモ"
          className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 min-h-24 w-full resize-y rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-3"
          onChange={(event) => onChange(event.currentTarget.value)}
          placeholder="例: A は子音が明瞭、B は距離感が自然"
          value={value}
        />
      </CardContent>
    </Card>
  );
}

function VoteButton({
  disabled,
  label,
  number,
  onClick,
}: {
  disabled: boolean;
  label: string;
  number: "1" | "2" | "3";
  onClick: () => void;
}) {
  return (
    <Button
      aria-keyshortcuts={number}
      className="h-11 justify-between px-4"
      disabled={disabled}
      onClick={onClick}
      variant="outline"
    >
      <span>{label}</span>
      <kbd className="rounded border border-current/20 px-1.5 py-0.5 font-mono text-[10px]">
        {number}
      </kbd>
    </Button>
  );
}

function KeyboardGuide() {
  return (
    <aside className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-dashed px-4 py-3 text-xs text-muted-foreground">
      <span className="flex items-center gap-2 font-medium text-foreground">
        <Headphones aria-hidden="true" className="size-4 text-primary" />
        キーボード
      </span>
      <span className="flex items-center gap-1.5">
        <ArrowLeft aria-hidden="true" className="size-3.5" />
        <ArrowRight aria-hidden="true" className="size-3.5" />
        A / B を再生
      </span>
      <span>
        <kbd className="font-mono text-foreground">1</kbd> A<span aria-hidden="true"> · </span>
        <kbd className="font-mono text-foreground">2</kbd> 引き分け
        <span aria-hidden="true"> · </span>
        <kbd className="font-mono text-foreground">3</kbd> B
      </span>
      <span>この比較枠にフォーカスがある間だけ有効です</span>
    </aside>
  );
}

function StorageError({ error, onReset }: { error: string; onReset: () => void }) {
  return (
    <Card aria-live="assertive" className="border-destructive/40" role="alert">
      <CardHeader>
        <Badge variant="destructive">
          <AlertTriangle aria-hidden="true" />
          保存エラー
        </Badge>
        <CardTitle>ローカル結果を読み書きできません</CardTitle>
        <CardDescription>{error}</CardDescription>
      </CardHeader>
      <CardContent>
        <Button onClick={onReset} variant="destructive">
          <RotateCcw aria-hidden="true" data-icon="inline-start" />
          ローカル結果をリセット
        </Button>
      </CardContent>
    </Card>
  );
}

function UnavailableState({ modelCount }: { modelCount: number }) {
  return (
    <Card className="border-dashed">
      <CardHeader>
        <Badge variant="outline">比較データ不足</Badge>
        <CardTitle>A/B 比較には共通のセリフを持つ 2 モデル以上が必要です</CardTitle>
        <CardDescription>
          現在のデータには {modelCount} モデルが登録されています。同じセリフの dry
          音声が複数モデルで揃うと、ここから匿名比較を開始できます。
        </CardDescription>
      </CardHeader>
    </Card>
  );
}

function CompleteState({ total }: { total: number }) {
  return (
    <Card className="bg-primary/[0.04] ring-primary/40">
      <CardHeader>
        <Badge variant="secondary">
          <Check aria-hidden="true" />
          完了
        </Badge>
        <CardTitle>すべての比較に投票しました</CardTitle>
        <CardDescription>
          {total} 件の匿名比較が完了しました。下のランキングで今回の傾向を確認できます。
        </CardDescription>
      </CardHeader>
    </Card>
  );
}

function RankingCard({
  rankings,
  votesCount,
}: {
  rankings: ReturnType<typeof useAbSession>["rankings"];
  votesCount: number;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Trophy aria-hidden="true" className="size-5 text-primary" />
          <CardTitle>自分の耳ランキング</CardTitle>
        </div>
        <CardDescription>
          勝ち 1 点・引き分け 0.5 点で集計します。モデルごとに 5 票集まるまで勝率と順位は隠します。
        </CardDescription>
      </CardHeader>
      <CardContent>
        {votesCount === 0 ? (
          <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
            最初の投票後に集計が始まります。
          </p>
        ) : (
          <ol className="divide-y">
            {rankings.map((ranking) => (
              <li className="flex items-center gap-4 py-3" key={ranking.modelId}>
                <span className="w-8 shrink-0 text-center font-mono text-sm text-primary">
                  {ranking.rank === null ? "—" : ranking.rank}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{ranking.modelName}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {ranking.scoreRate === null
                      ? `${ranking.appearances} 票`
                      : `${ranking.appearances} 票 · ${ranking.wins}勝 ${ranking.ties}分 ${ranking.losses}敗`}
                  </p>
                </div>
                <span className="shrink-0 font-mono text-sm">
                  {ranking.scoreRate === null
                    ? `あと ${ranking.requiredAppearances} 票`
                    : `${(ranking.scoreRate * 100).toFixed(1)}%`}
                </span>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

function runShortcut(
  shortcut: AbShortcut,
  playCandidate: (side: CandidateSide) => Promise<void>,
  castVote: (choice: CandidateSide | "tie") => void,
) {
  if (shortcut === "play-left" || shortcut === "play-right") {
    void playCandidate(shortcut === "play-left" ? "left" : "right");
    return;
  }
  if (shortcut === "vote-tie") {
    castVote("tie");
    return;
  }
  castVote(shortcut === "vote-left" ? "left" : "right");
}

function candidateStatusLabel(status: "idle" | "loading" | "playing" | "paused" | "error") {
  if (status === "loading") {
    return "読込中";
  }
  if (status === "playing") {
    return "再生中";
  }
  if (status === "paused") {
    return "一時停止";
  }
  if (status === "error") {
    return "再生失敗";
  }
  return "未再生";
}

function candidateActionLabel(
  label: "A" | "B",
  status: "idle" | "loading" | "playing" | "paused" | "error",
) {
  if (status === "loading" || status === "playing") {
    return `候補 ${label} を一時停止`;
  }
  if (status === "paused") {
    return `候補 ${label} を再開`;
  }
  return `候補 ${label} を再生`;
}

function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}
