const WAVEFORM_HEIGHTS = [
  24, 46, 72, 38, 86, 58, 32, 64, 94, 52, 28, 78, 44, 88, 62, 34, 70, 48, 82, 40, 68, 30, 56, 76,
] as const;

export function WaveformProgress({ className = "", ratio }: { className?: string; ratio: number }) {
  const progress = Number.isFinite(ratio) ? Math.min(Math.max(ratio, 0), 1) : 0;

  return (
    <div aria-hidden="true" className={`flex items-center gap-px overflow-hidden ${className}`}>
      {WAVEFORM_HEIGHTS.map((height, index) => (
        <span
          className={[
            "h-full min-w-0 flex-1 rounded-full",
            (index + 1) / WAVEFORM_HEIGHTS.length <= progress ? "bg-primary" : "bg-primary/18",
          ].join(" ")}
          key={`${index}-${height}`}
          style={{ height: `${height}%` }}
        />
      ))}
    </div>
  );
}
