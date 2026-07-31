import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router";

import type { Scenario } from "@/data";

export function ScenarioContextLink({
  density,
  scenario,
  search,
}: {
  density: "compact" | "card";
  scenario: Scenario;
  search: string;
}) {
  return (
    <Link
      aria-label={`${scenario.title}の場面詳細を見る`}
      className={[
        "group/scenario flex min-w-0 items-start gap-2 rounded-sm border-l-2 border-primary/70 bg-primary/[0.055] text-left",
        "outline-none transition-colors hover:bg-primary/[0.1] focus-visible:ring-2 focus-visible:ring-ring/60 motion-reduce:transition-none",
        density === "card" ? "mb-3 px-2.5 py-2" : "mb-2 px-2 py-1.5",
      ].join(" ")}
      to={{ pathname: `/scenario/${scenario.id}`, search }}
    >
      <span className="mt-0.5 shrink-0 rounded border border-primary/45 bg-primary/10 px-1.5 py-0.5 font-mono text-[9px] tracking-wider text-primary">
        場面
      </span>
      <span
        className={[
          "min-w-0 flex-1 break-words font-semibold leading-5 text-foreground group-hover/scenario:text-primary",
          density === "card" ? "text-sm" : "text-xs",
        ].join(" ")}
      >
        {scenario.title}
      </span>
      <ArrowUpRight
        aria-hidden="true"
        className="mt-0.5 size-3.5 shrink-0 text-muted-foreground group-hover/scenario:text-primary"
      />
    </Link>
  );
}
