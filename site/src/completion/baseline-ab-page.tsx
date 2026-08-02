import { Check, ChevronLeft, ChevronRight, Headphones, LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import { baselineAbResultFromDraft, createBaselineAbDraft } from "./baseline-ab-model";
import {
  finalizeLocalListening,
  loadLocalListeningDraft,
  localCandidateAudioUrl,
  saveLocalListeningDraft,
  type BaselineAbDraft,
  type BaselineAbListeningBootstrap,
} from "./local-listening-session";

const LABELS = ["A", "B", "C"] as const;

export function BaselineAbPage({
  bootstrap,
}: {
  readonly bootstrap: BaselineAbListeningBootstrap;
}) {
  const revisionRef = useRef(bootstrap.revision);
  const [draft, setDraft] = useState<BaselineAbDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [finalized, setFinalized] = useState(bootstrap.finalized);
  const [error, setError] = useState<string | null>(null);

  const persist = useCallback(
    async (next: BaselineAbDraft) => {
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
    void loadLocalListeningDraft<BaselineAbDraft>(bootstrap).then(
      (saved) => {
        if (!active) return;
        if (saved) {
          revisionRef.current = saved.revision;
          setDraft(saved.draft);
          return;
        }
        void persist(createBaselineAbDraft(bootstrap));
      },
      (reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : String(reason));
      },
    );
    return () => {
      active = false;
    };
  }, [bootstrap, persist]);

  if (error) return <p className="p-6 text-sm text-destructive">{error}</p>;
  if (!draft) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <LoaderCircle className="size-4 animate-spin" /> 正在读取听测目录…
      </div>
    );
  }

  const group = bootstrap.bundle.groups[draft.current_index]!;
  const result = draft.groups[draft.current_index]!;
  const decided = draft.groups.filter((item) => item.choice !== null).length;
  const complete = decided === draft.groups.length;
  const allHeard = result.heard_candidate_ids.length === group.candidates.length;

  const move = (index: number) => {
    if (!saving && index >= 0 && index < draft.groups.length) {
      void persist({ ...draft, current_index: index });
    }
  };
  const markHeard = (candidateId: string) => {
    if (saving || result.heard_candidate_ids.includes(candidateId)) return;
    const groups = draft.groups.map((item, index) =>
      index === draft.current_index
        ? { ...item, heard_candidate_ids: [...item.heard_candidate_ids, candidateId] }
        : item,
    );
    void persist({ ...draft, groups });
  };
  const judge = (choice: string) => {
    if (saving || !allHeard) return;
    const groups = draft.groups.map((item, index) =>
      index === draft.current_index ? { ...item, choice } : item,
    );
    const nextIndex = groups.findIndex(
      (item, index) => index > draft.current_index && item.choice === null,
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
  const finalize = async () => {
    if (!complete || saving || finalized) return;
    setSaving(true);
    try {
      await finalizeLocalListening(
        bootstrap,
        revisionRef.current,
        baselineAbResultFromDraft(draft),
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
          <h1 className="text-base font-semibold">{bootstrap.bundle.title}</h1>
          <p className="text-xs text-muted-foreground">
            {draft.current_index + 1} / {draft.groups.length} · 已判断 {decided}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {saving ? <LoaderCircle className="size-4 animate-spin text-muted-foreground" /> : null}
          <Button disabled={!complete || saving || finalized} onClick={finalize} size="sm">
            {finalized ? "结果已保存" : "完成听测"}
          </Button>
        </div>
      </header>

      <Card className="border-primary/35 bg-primary/5 p-3 text-sm">
        <div className="flex items-start gap-2">
          <Headphones className="mt-0.5 size-4 shrink-0" />
          <p>
            <strong>本组只判断：</strong>
            {group.focus}
          </p>
        </div>
      </Card>

      <Card className="grid gap-3 p-3 sm:p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary">{trackLabel(group.track)}</Badge>
          <Badge variant="outline">{group.model}</Badge>
          <span className="text-xs text-muted-foreground">
            {group.scenario} · {group.line}
          </span>
        </div>
        <p className="rounded-md border bg-muted/25 p-3 text-base leading-relaxed">{group.text}</p>

        <div
          className={`grid gap-2 ${group.candidates.length === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2"}`}
        >
          {group.candidates.map((candidate, index) => {
            const heard = result.heard_candidate_ids.includes(candidate.id);
            return (
              <div className="grid gap-2 rounded-md border p-3" key={candidate.id}>
                <div className="flex items-center justify-between">
                  <span className="font-semibold">候选 {LABELS[index]}</span>
                  {heard ? (
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <Check className="size-3" />
                      已听完
                    </span>
                  ) : null}
                </div>
                <audio
                  className="h-10 w-full"
                  controls
                  onEnded={() => markHeard(candidate.id)}
                  preload="metadata"
                  src={localCandidateAudioUrl(candidate.id)}
                />
              </div>
            );
          })}
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {group.candidates.map((candidate, index) => (
            <Button
              disabled={!allHeard || saving || finalized}
              key={candidate.id}
              onClick={() => judge(candidate.id)}
              variant={result.choice === candidate.id ? "default" : "outline"}
            >
              选 {LABELS[index]}
            </Button>
          ))}
          <Button
            disabled={!allHeard || saving || finalized}
            onClick={() => judge("no_preference")}
            variant={result.choice === "no_preference" ? "default" : "outline"}
          >
            差不多
          </Button>
          <Button
            disabled={!allHeard || saving || finalized}
            onClick={() => judge("none_acceptable")}
            variant={result.choice === "none_acceptable" ? "destructive" : "outline"}
          >
            都不行
          </Button>
        </div>
        {!allHeard ? (
          <p className="text-center text-xs text-muted-foreground">听完全部候选后即可选择</p>
        ) : null}
        <textarea
          aria-label="备注"
          className="min-h-14 w-full resize-y rounded-md border bg-background p-2 text-sm"
          disabled={saving || finalized}
          maxLength={500}
          onBlur={() => {
            if (!saving) void persist(draft);
          }}
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
          <ChevronLeft className="size-4" />
          上一项
        </Button>
        <select
          aria-label="选择听测项"
          className="h-8 max-w-48 rounded-md border bg-background px-2 text-xs"
          disabled={saving}
          onChange={(event) => move(Number(event.target.value))}
          value={draft.current_index}
        >
          {draft.groups.map((item, index) => (
            <option key={item.id} value={index}>
              {index + 1}. {item.choice === null ? "未判断" : "已判断"}
            </option>
          ))}
        </select>
        <Button
          disabled={draft.current_index === draft.groups.length - 1 || saving}
          onClick={() => move(draft.current_index + 1)}
          size="sm"
          variant="outline"
        >
          下一项
          <ChevronRight className="size-4" />
        </Button>
      </nav>
      <p className="text-center text-xs text-muted-foreground">{bootstrap.bundle.instructions}</p>
    </main>
  );
}

function trackLabel(track: string): string {
  if (track === "irodori-caption") return "演技文案";
  if (track === "supertonic-speed") return "语速";
  if (track === "cosyvoice-reading") return "日语读法";
  return track;
}
