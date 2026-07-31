const WAVEFORM_HEIGHTS = [
  24, 46, 72, 38, 86, 58, 32, 64, 94, 52, 28, 78, 44, 88, 62, 34, 70, 48, 82, 40, 68, 30, 56, 76,
] as const;

export function WaveformProgress({ className = "", ratio }: { className?: string; ratio: number }) {
  const progress = Number.isFinite(ratio) ? Math.min(Math.max(ratio, 0), 1) : 0;

  return (
    <div
      aria-hidden="true"
      className={`flex items-center gap-px overflow-hidden ${className}`}
      data-slot="waveform"
    >
      {WAVEFORM_HEIGHTS.map((height, index) => {
        const elapsed = (index + 1) / WAVEFORM_HEIGHTS.length <= progress;
        return (
          <span
            className={[
              "h-full min-w-0 flex-1 rounded-full",
              elapsed ? "bg-primary" : "bg-primary/18",
            ].join(" ")}
            data-progress={elapsed ? "elapsed" : "future"}
            data-slot="waveform-bar"
            key={`${index}-${height}`}
            style={{ height: `${height}%` }}
          />
        );
      })}
    </div>
  );
}
