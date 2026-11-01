import { useMemo } from "react";
import { useCountUp } from "@/hooks/useCountUp";

interface Props {
  /** Full display value, e.g. "1,240 files" or "3.2s" or "87%". */
  value: string;
  /** Optional override — a specific numeric target to count to. */
  target?: number;
  duration?: number;
  className?: string;
}

/**
 * Parses the leading number out of a display string and animates it from 0
 * to its final value on mount. The rest of the string (units, suffixes)
 * renders unchanged. Falls back to the raw value if no number is found.
 *
 * Examples:
 *   "1,240 files"  → animates 0 → 1240, renders "1,240 files"
 *   "3.2s"         → animates 0 → 3.2,  renders "3.2s"
 *   "87%"          → animates 0 → 87,   renders "87%"
 */
export function AnimatedNumber({ value, target, duration = 500, className }: Props) {
  const parsed = useMemo(() => {
    const match = value.match(/^(-?\d[\d,]*\.?\d*)(.*)$/);
    if (!match) return null;
    const raw = match[1].replace(/,/g, "");
    const num = Number(raw);
    if (!Number.isFinite(num)) return null;
    const hasComma = match[1].includes(",");
    const decimals = raw.includes(".") ? raw.split(".")[1].length : 0;
    return { num, hasComma, decimals, suffix: match[2] };
  }, [value]);

  const goal = target ?? parsed?.num ?? 0;
  const current = useCountUp(goal, duration);

  if (!parsed) return <span className={className}>{value}</span>;

  const formatted = parsed.hasComma
    ? current.toLocaleString(undefined, {
        minimumFractionDigits: parsed.decimals,
        maximumFractionDigits: parsed.decimals,
      })
    : parsed.decimals === 0
      ? Math.round(current).toString()
      : current.toFixed(parsed.decimals);

  return (
    <span className={className}>
      {formatted}
      {parsed.suffix}
    </span>
  );
}
