import { conditioningAccessibleLabel, conditioningModeLabel } from "@/data/conditioning";
import type { Conditioning } from "@/data/types";

/**
 * 条件バリアント (#201) のチップ。
 *
 * 色は補助表現で、文言 (見本あり / 見本なし) と title / aria-label に必ず条件が入る。
 */
export function ConditioningBadge({
  conditioning,
  className,
}: {
  conditioning: Conditioning;
  className?: string;
}) {
  const isHumanReference = conditioning.mode === "human-reference";
  const accessibleLabel = conditioningAccessibleLabel(conditioning);

  return (
    <span
      className={[
        "inline-flex max-w-full items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[9px] leading-3 whitespace-nowrap",
        isHumanReference
          ? "border-[#15803d]/60 bg-[#15803d]/15 text-[#4ade80]"
          : "border-[#b45309]/60 bg-[#b45309]/15 text-[#fbbf24]",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
      data-conditioning-mode={conditioning.mode}
      title={accessibleLabel}
    >
      <span aria-hidden="true" className="text-[8px]">
        {isHumanReference ? "◆" : "◇"}
      </span>
      <span className="sr-only">条件: </span>
      {conditioningModeLabel(conditioning.mode)}
    </span>
  );
}
