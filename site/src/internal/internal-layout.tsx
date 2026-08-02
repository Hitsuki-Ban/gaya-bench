import { AudioLines, FolderCheck, ListChecks, ListTodo, LoaderCircle } from "lucide-react";
import { NavLink, Outlet } from "react-router";

import { AudioProvider } from "@/audio/audio-provider";
import { Button } from "@/components/ui/button";

const navigation = [
  { to: "/curate", label: "音频筛选", icon: FolderCheck },
  { to: "/completion", label: "角色听测", icon: ListTodo },
  { to: "/pilot", label: "预检", icon: ListChecks },
] as const;

export function InternalLayout({ listeningMode = false }: { listeningMode?: boolean }) {
  return (
    <div
      className="min-h-screen"
      data-internal-ui="gaya-bench-internal-ui-v1"
      data-listening-mode={listeningMode}
      lang={listeningMode ? "zh-CN" : undefined}
    >
      <header
        className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur"
        data-global-sticky-header
      >
        <div className="mx-auto flex min-h-(--gaya-header-height) max-w-[1480px] items-center gap-6 px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <span className="grid size-9 place-items-center rounded-md border border-primary/35 bg-primary/10 text-primary">
              <AudioLines aria-hidden="true" className="size-5" />
            </span>
            <span>
              <span className="block font-mono text-sm font-semibold tracking-[0.18em] text-foreground">
                GAYA BENCH
              </span>
              <span className="block text-[11px] text-muted-foreground">
                {listeningMode ? "本地听测工作台" : "本地听测工具"}
              </span>
            </span>
          </div>

          {listeningMode ? null : (
            <nav aria-label="本地工具导航" className="ml-auto flex items-center gap-1">
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
          )}
        </div>
      </header>

      <main className="mx-auto max-w-[1480px] px-3 py-3 sm:px-5 sm:py-4">
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
      <p className="font-mono text-[10px] tracking-[0.18em] text-primary uppercase">本地音频</p>
      <div className="mt-3 flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold" id="internal-audio-boot-heading">
            正在准备播放器
          </h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">正在初始化单一音频播放器。</p>
        </div>
        <Button disabled variant="outline">
          <LoaderCircle
            aria-hidden="true"
            className="animate-spin motion-reduce:animate-none"
            data-icon="inline-start"
          />
          准备音频
        </Button>
      </div>
    </section>
  );
}
