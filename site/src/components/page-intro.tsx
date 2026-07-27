import type { ReactNode } from "react";

interface PageIntroProps {
  eyebrow: string;
  title: string;
  description: string;
  aside?: ReactNode;
}

export function PageIntro({ eyebrow, title, description, aside }: PageIntroProps) {
  return (
    <div className="flex flex-col gap-5 border-b pb-7 lg:flex-row lg:items-end lg:justify-between">
      <div className="max-w-3xl">
        <p className="mb-3 font-mono text-xs tracking-[0.2em] text-primary uppercase">{eyebrow}</p>
        <h1 className="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
          {title}
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-7 text-muted-foreground">{description}</p>
      </div>
      {aside}
    </div>
  );
}
