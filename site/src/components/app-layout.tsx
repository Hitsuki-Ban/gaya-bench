import { AudioLines, FlaskConical, Scale, SlidersHorizontal } from "lucide-react";
import { NavLink, Outlet } from "react-router";

const navigation = [
  { to: "/", label: "比較", icon: SlidersHorizontal, end: true },
  { to: "/ab", label: "A/B", icon: FlaskConical, end: false },
  { to: "/credits", label: "クレジット", icon: Scale, end: false },
] as const;

export function AppLayout() {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex min-h-16 max-w-[1480px] items-center gap-6 px-4 sm:px-6">
          <NavLink className="flex items-center gap-3" to="/">
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
                to={to}
              >
                <Icon aria-hidden="true" className="size-4" />
                <span className="hidden sm:inline">{label}</span>
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-[1480px] px-4 py-8 sm:px-6">
        <Outlet />
      </main>
    </div>
  );
}
