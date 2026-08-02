import { TriangleAlert } from "lucide-react";

import type { PublishedCandidate } from "@/data";

export function AutomaticQualityBadge({
  candidate,
  compact = false,
}: {
  candidate: PublishedCandidate;
  compact?: boolean;
}) {
  const contentReview = candidate.gate.content === "review_required";
  const roleReview = candidate.role_quality?.status === "review_required";
  if (!contentReview && !roleReview) {
    return null;
  }

  const reasons = [
    contentReview ? "発音または抑揚" : null,
    roleReview ? "指定された役柄・声の性別との一致" : null,
  ].filter((reason): reason is string => reason !== null);

  return (
    <span
      aria-label="自動品質チェック: 要確認"
      className="inline-flex shrink-0 items-center gap-1 rounded border border-amber-500/45 bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-medium text-amber-700 dark:text-amber-300"
      data-visual-intent="warning"
      title={`自動判定で${reasons.join("、")}に確認候補があります。公開後の対象聴取で確認します。`}
    >
      <TriangleAlert aria-hidden="true" className="size-2.5" />
      {compact ? "要確認" : "自動QC: 要確認"}
    </span>
  );
}
