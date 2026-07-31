import { AudioLines, FolderCheck, ListChecks, ListTodo, LoaderCircle } from "lucide-react";
import { NavLink, Outlet } from "react-router";

import { AudioProvider } from "@/audio/audio-provider";
import { Button } from "@/components/ui/button";

const navigation = [
  { to: "/curate", label: "音声選定", icon: FolderCheck },
  { to: "/completion", label: "基準線補録", icon: ListTodo },
  { to: "/pilot", label: "事前確認", icon: ListChecks },
] as const;

export function InternalLayout() {
  return (
    <div data-internal-ui="gaya-bench-internal-ui-v1" className="min-h-screen">
      <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex min-h-14 max-w-[1480px] items-center gap-6 px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <span className="grid size-9 place-items-center rounded-md border border-primary/35 bg-primary/10 text-primary">
              <AudioLines aria-hidden="true" className="size-5" />
            </span>
            <span>
              <span className="block font-mono text-sm font-semibold tracking-[0.18em] text-foreground">
                GAYA BENCH
              </span>
              <span className="block text-[11px] text-muted-foreground">ローカル評価ツール</span>
            </span>
          </div>

          <nav aria-label="ローカル評価ナビゲーション" className="ml-auto flex items-center gap-1">
            {navigation.map(({ to, label, icon: Icon }) => (
              <NavLink
                aria-label={label}
                className={({ isActive }) =>
                  [
                    "flex min-h-9 items-center gap-2 rounded-md px-3 text-sm transition-colors",
                    isActive
                      ? "bg-primary/12 text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  ].join(" ")
                }
                key={to}
                to={to}
              >
                <Icon aria-hidden="true" className="size-4" />
                <span className="hidden sm:inline">{label}</span>
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-[1480px] px-4 py-5 sm:px-6 sm:py-6">
        <p
          className="mb-5 rounded-md border border-primary/25 bg-primary/6 px-3 py-2 text-xs leading-5 text-muted-foreground"
          role="note"
        >
          この画面は公開サイトに含まれないローカル専用の人手評価ツールです。
        </p>
        <AudioProvider fallback={<AudioBootShell />}>
          <Outlet />
        </AudioProvider>
      </main>
    </div>
  );
}

function AudioBootShell() {
  return (
    <section
      aria-busy="true"
      aria-labelledby="internal-audio-boot-heading"
      aria-live="polite"
      className="rounded-md border bg-card p-6 sm:p-8"
    >
      <p className="font-mono text-[10px] tracking-[0.18em] text-primary uppercase">Local audio</p>
      <div className="mt-3 flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold" id="internal-audio-boot-heading">
            評価用プレーヤーを準備中
          </h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            ローカル音声を読み込む前に、単一再生プレーヤーを初期化しています。
          </p>
        </div>
        <Button disabled variant="outline">
          <LoaderCircle
            aria-hidden="true"
            className="animate-spin motion-reduce:animate-none"
            data-icon="inline-start"
          />
          音声準備中
        </Button>
      </div>
    </section>
  );
}
