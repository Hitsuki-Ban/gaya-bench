import { CheckCircle2, ChevronLeft, ChevronRight, Headphones, LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import {
  finalizeLocalListening,
  loadLocalListeningDraft,
  localCandidateAudioUrl,
  saveLocalListeningDraft,
  type QualityReviewDraft,
  type QualityReviewListeningBootstrap,
} from "./local-listening-session";
import { createQualityReviewDraft, qualityReviewResultFromDraft } from "./quality-review-model";

export function QualityReviewPage({
  bootstrap,
}: {
  readonly bootstrap: QualityReviewListeningBootstrap;
}) {
  const revisionRef = useRef(bootstrap.revision);
  const [draft, setDraft] = useState<QualityReviewDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [finalized, setFinalized] = useState(bootstrap.finalized);
  const [error, setError] = useState<string | null>(null);

  const persist = useCallback(
    async (next: QualityReviewDraft) => {
      setDraft(next);
      setSaving(true);
      try {
        const saved = await saveLocalListeningDraft(bootstrap, revisionRef.current, next);
        revisionRef.current = saved.revision;
      } catch (reason: unknown) {
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setSaving(false);
      }
    },
    [bootstrap],
  );

  useEffect(() => {
    let active = true;
    void loadLocalListeningDraft<QualityReviewDraft>(bootstrap).then(
      (saved) => {
        if (!active) return;
        if (saved) {
          revisionRef.current = saved.revision;
          setDraft(saved.draft);
          return;
        }
        const fresh = createQualityReviewDraft(bootstrap);
        void persist(fresh);
      },
      (reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : String(reason));
      },
    );
    return () => {
      active = false;
    };
  }, [bootstrap, persist]);

  if (error) {
    return <p className="p-6 text-sm text-destructive">{error}</p>;
  }
  if (!draft) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <LoaderCircle className="size-4 animate-spin" /> 正在读取复核目录…
      </div>
    );
  }

  const group = bootstrap.bundle.groups[draft.current_index]!;
  const result = draft.groups[draft.current_index]!;
  const decided = draft.groups.filter((item) => item.result !== null).length;
  const mismatches = draft.groups.filter((item) => item.result === "mismatch").length;
  const complete = decided === draft.groups.length;

  const move = (index: number) => {
    if (saving || index < 0 || index >= draft.groups.length) return;
    void persist({ ...draft, current_index: index });
  };
  const markHeard = () => {
    if (saving || result.heard) return;
    const groups = draft.groups.map((item, index) =>
      index === draft.current_index ? { ...item, heard: true } : item,
    );
    void persist({ ...draft, groups });
  };
  const judge = (value: "match" | "mismatch") => {
    if (saving || !result.heard) return;
    const groups = draft.groups.map((item, index) =>
      index === draft.current_index ? { ...item, result: value } : item,
    );
    const nextIndex = groups.findIndex(
      (item, index) => index > draft.current_index && item.result === null,
    );
    void persist({
      ...draft,
      groups,
      current_index: nextIndex >= 0 ? nextIndex : draft.current_index,
    });
  };
  const updateNotes = (notes: string) => {
    setDraft({
      ...draft,
      groups: draft.groups.map((item, index) =>
        index === draft.current_index ? { ...item, notes } : item,
      ),
    });
  };
  const saveNotes = () => {
    if (!saving) void persist(draft);
  };
  const finalize = async () => {
    if (!complete || saving || finalized) return;
    setSaving(true);
    try {
      await finalizeLocalListening(
        bootstrap,
        revisionRef.current,
        qualityReviewResultFromDraft(draft),
      );
      setFinalized(true);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="mx-auto grid w-full max-w-4xl gap-3 p-3 sm:p-5">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-base font-semibold">角色声音定向复核</h1>
          <p className="text-xs text-muted-foreground">
            {draft.current_index + 1} / {draft.groups.length} · 已判断 {decided} · 不符合{" "}
            {mismatches}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {saving ? <LoaderCircle className="size-4 animate-spin text-muted-foreground" /> : null}
          <Button disabled={!complete || saving || finalized} size="sm" onClick={finalize}>
            {finalized ? "结果已保存" : "完成复核"}
          </Button>
        </div>
      </header>

      <Card className="border-primary/35 bg-primary/5 p-3 text-sm">
        <div className="flex items-start gap-2">
          <Headphones className="mt-0.5 size-4 shrink-0" />
          <p>
            <strong>只判断：</strong>这个声音是否符合角色指定的性别和整体声线。
            台词内容、发音和音质不在本轮判断范围。
          </p>
        </div>
      </Card>

      <Card className="grid gap-3 p-3 sm:p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary">{group.model}</Badge>
          <Badge variant="outline">{group.scenario_title}</Badge>
          <span className="text-sm font-medium">{group.role.name}</span>
          <span className="text-xs text-muted-foreground">
            {genderLabel(group.expected_gender)} · {ageLabel(group.role.age)} ·{" "}
            {group.role.archetype}
          </span>
        </div>
        <div className="rounded-md border bg-muted/25 p-3">
          <p className="text-base leading-relaxed">{group.text}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            声线：{group.role.voice}　语气：{group.delivery}
          </p>
        </div>

        <audio
          className="h-10 w-full"
          controls
          key={group.take_id}
          onEnded={markHeard}
          preload="metadata"
          src={localCandidateAudioUrl(group.take_id)}
        />

        <div className="grid grid-cols-2 gap-2">
          <Button
            disabled={!result.heard || saving || finalized}
            onClick={() => judge("match")}
            variant={result.result === "match" ? "default" : "outline"}
          >
            <CheckCircle2 className="size-4" /> 符合角色
          </Button>
          <Button
            disabled={!result.heard || saving || finalized}
            onClick={() => judge("mismatch")}
            variant={result.result === "mismatch" ? "destructive" : "outline"}
          >
            不符合
          </Button>
        </div>
        {!result.heard ? (
          <p className="text-center text-xs text-muted-foreground">完整播放后即可判断</p>
        ) : null}
        <textarea
          aria-label="备注"
          className="min-h-16 w-full resize-y rounded-md border bg-background p-2 text-sm"
          disabled={saving || finalized}
          maxLength={500}
          onBlur={saveNotes}
          onChange={(event) => updateNotes(event.target.value)}
          placeholder="备注（可选）"
          value={result.notes}
        />
      </Card>

      <nav className="flex items-center justify-between gap-2">
        <Button
          disabled={draft.current_index === 0 || saving}
          onClick={() => move(draft.current_index - 1)}
          size="sm"
          variant="outline"
        >
          <ChevronLeft className="size-4" /> 上一项
        </Button>
        <select
          aria-label="选择复核项"
          className="h-8 max-w-48 rounded-md border bg-background px-2 text-xs"
          disabled={saving}
          onChange={(event) => move(Number(event.target.value))}
          value={draft.current_index}
        >
          {draft.groups.map((item, index) => (
            <option key={`${item.model}/${item.scenario}/${item.line}`} value={index}>
              {index + 1}.{" "}
              {item.result === null ? "未判断" : item.result === "match" ? "符合" : "不符合"}
            </option>
          ))}
        </select>
        <Button
          disabled={draft.current_index === draft.groups.length - 1 || saving}
          onClick={() => move(draft.current_index + 1)}
          size="sm"
          variant="outline"
        >
          下一项 <ChevronRight className="size-4" />
        </Button>
      </nav>
    </main>
  );
}

function genderLabel(gender: "female" | "male"): string {
  return gender === "female" ? "女性" : "男性";
}

function ageLabel(age: string): string {
  const labels: Readonly<Record<string, string>> = {
    adult: "成年",
    child: "儿童",
    elderly: "老年",
    middle_aged: "中年",
    teen: "少年",
    young_adult: "青年",
  };
  return labels[age] ?? age;
}
