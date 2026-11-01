import { Check, X } from "lucide-react";

interface Props {
  ok: boolean;
  size?: "sm" | "md";
  className?: string;
}

/**
 * Minimalist outlined status icon (thin ring + thin glyph).
 * - ok=true  -> green outlined check
 * - ok=false -> red outlined X
 */
export function StatusIcon({ ok, size = "md", className = "" }: Props) {
  const box = size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4";
  const glyph = size === "sm" ? "h-2 w-2" : "h-2.5 w-2.5";
  const tone = ok
    ? "border-status-completed text-status-completed"
    : "border-status-failed text-status-failed";
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center rounded-full border-2 bg-transparent ${tone} ${box} ${className}`}
    >
      {ok ? <Check className={glyph} strokeWidth={2} /> : <X className={glyph} strokeWidth={2} />}
    </span>
  );
}
