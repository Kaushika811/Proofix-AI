import { useEffect, useState } from "react";

/** `unknown` = never measured. Renders no arc and no percentage. */
type Tone = "ok" | "warn" | "bad" | "primary" | "unknown";

interface Props {
  /**
   * 0-100, or `null` when the axis was never measured.
   *
   * A skipped security re-scan arrived here as `0` and drew an empty red ring
   * labelled "0%" — indistinguishable from a scan that ran and found the code
   * indefensible. Absence now draws no arc and says so.
   */
  value: number | null; // 0-100
  label: string;
  size?: number;
  tone?: Tone;
  animate?: boolean;
}

const toneClass: Record<Tone, { text: string; stroke: string }> = {
  ok: { text: "text-status-completed", stroke: "stroke-status-completed" },
  warn: { text: "text-status-retry", stroke: "stroke-status-retry" },
  bad: { text: "text-status-failed", stroke: "stroke-status-failed" },
  primary: { text: "text-primary", stroke: "stroke-primary" },
  unknown: { text: "text-ink-soft", stroke: "stroke-border" },
};

export function ProgressRing({ value, label, size = 72, tone = "primary", animate = true }: Props) {
  const measured = value !== null;
  // The track still draws, so the layout is stable and the axis is visibly
  // present — it simply has no reading.
  const [v, setV] = useState(animate ? 0 : (value ?? 0));
  useEffect(() => {
    if (!animate) {
      setV(value ?? 0);
      return;
    }
    const t = window.setTimeout(() => setV(value ?? 0), 60);
    return () => window.clearTimeout(t);
  }, [value, animate]);

  const stroke = 6;
  const r = (size - stroke) / 2;
  const C = 2 * Math.PI * r;
  const offset = C - (Math.max(0, Math.min(100, v)) / 100) * C;
  const c = toneClass[tone];

  return (
    <div className="flex flex-col items-center gap-1.5">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            strokeWidth={stroke}
            className="stroke-border"
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={C}
            strokeDashoffset={measured ? offset : C}
            className={`${c.stroke} transition-[stroke-dashoffset] duration-500 ease-out`}
          />
        </svg>
        <span
          className={`absolute inset-0 flex items-center justify-center font-mono text-[13px] font-semibold ${c.text}`}
        >
          {measured ? `${Math.round(v)}%` : "—"}
        </span>
      </div>
      <div className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">{label}</div>
    </div>
  );
}
