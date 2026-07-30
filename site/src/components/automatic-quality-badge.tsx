import { TriangleAlert } from "lucide-react";

import type { Candidate } from "@/data";

export function AutomaticQualityBadge({
  candidate,
  compact = false,
}: {
  candidate: Candidate;
  compact?: boolean;
}) {
  if (candidate.gate.content === "pass") {
    return null;
  }

  return (
    <span
      aria-label="自動品質チェック: 要確認"
      className="inline-flex shrink-0 items-center gap-1 rounded border border-amber-500/45 bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-medium text-amber-700 dark:text-amber-300"
      title="自動判定で発音または抑揚に確認候補があります。人手確認は順次実施中です。"
    >
      <TriangleAlert aria-hidden="true" className="size-2.5" />
      {compact ? "要確認" : "自動QC: 要確認"}
    </span>
  );
}
