import {
  AudioLines,
  GitCompareArrows,
  FlaskConical,
  FolderCheck,
  ListChecks,
  LoaderCircle,
  Scale,
  SlidersHorizontal,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router";

import { AudioProvider } from "@/audio/audio-provider";
import { Button } from "@/components/ui/button";

const navigation = [
  { to: "/", label: "比較", icon: SlidersHorizontal, end: true },
  { to: "/ab", label: "A/B", icon: FlaskConical, end: false },
  { to: "/curate", label: "策展", icon: FolderCheck, end: true },
  {
    to: "/curate/baseline",
    label: "Baseline",
    icon: GitCompareArrows,
    end: false,
  },
  { to: "/pilot", label: "Pilot", icon: ListChecks, end: false },
  { to: "/credits", label: "クレジット", icon: Scale, end: false },
] as const;

export function AppLayout() {
  const location = useLocation();

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex min-h-14 max-w-[1480px] items-center gap-6 px-4 sm:px-6">
          <NavLink
            className="flex items-center gap-3"
            to={{ pathname: "/", search: location.search }}
          >
            <span className="grid size-9 place-items-center rounded-md border border-primary/35 bg-primary/10 text-primary">
              <AudioLines aria-hidden="true" className="size-5" />
            </span>
            <span>
              <span className="block font-mono text-sm font-semibold tracking-[0.22em] text-foreground">
                GAYA BENCH
              </span>
              <span className="hidden text-[11px] text-muted-foreground sm:block">
                日本語 TTS ボイス比較
              </span>
            </span>
          </NavLink>

          <nav aria-label="メインナビゲーション" className="ml-auto flex items-center gap-1">
            {navigation.map(({ to, label, icon: Icon, end }) => (
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
                end={end}
                key={to}
                to={{ pathname: to, search: location.search }}
              >
                <Icon aria-hidden="true" className="size-4" />
                <span className="hidden sm:inline">{label}</span>
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-[1480px] px-4 py-5 sm:px-6 sm:py-6">
        <MachineGeneratedAudioNotice />
        <AudioProvider fallback={<AudioBootShell />}>
          <Outlet />
        </AudioProvider>
      </main>
    </div>
  );
}

export function MachineGeneratedAudioNotice() {
  return (
    <p
      className="mb-5 rounded-md border border-primary/25 bg-primary/6 px-3 py-2 text-xs leading-5 text-muted-foreground"
      role="note"
    >
      掲載音声は AI テキスト読み上げ（TTS）により機械生成されています。
    </p>
  );
}

function AudioBootShell() {
  return (
    <section
      aria-busy="true"
      aria-labelledby="audio-boot-heading"
      aria-live="polite"
      className="rounded-md border bg-card p-6 sm:p-8"
    >
      <p className="font-mono text-[10px] tracking-[0.18em] text-primary uppercase">Audio system</p>
      <div className="mt-3 flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold" id="audio-boot-heading">
            音声プレーヤーを準備中
          </h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            比較データを表示する前に、単一再生プレーヤーを初期化しています。
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
