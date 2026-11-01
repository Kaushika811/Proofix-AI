import { useEffect, useRef, useState } from "react";

/**
 * Whether the viewer asked for less motion.
 *
 * Exported because CSS cannot cover every case. The global
 * `prefers-reduced-motion` block in `styles.css` neutralises animations and
 * transitions, but it has no reach into two things this app does in
 * JavaScript: `requestAnimationFrame` count-ups, which are not animations as
 * far as CSS is concerned, and `window.scrollTo({behavior: "smooth"})`, where
 * the explicit option beats the stylesheet's `scroll-behavior: auto`.
 *
 * Read at call time rather than cached: the setting can change while the page
 * is open.
 */
export const prefersReducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** `"auto"` when the viewer asked for less motion, so scrolling jumps. */
export const scrollBehavior = (): ScrollBehavior => (prefersReducedMotion() ? "auto" : "smooth");

/**
 * Animate a numeric value from 0 → target once, on mount or when the
 * target changes. Respects `prefers-reduced-motion`.
 */
export function useCountUp(target: number, duration = 500): number {
  const [value, setValue] = useState(prefersReducedMotion() ? target : 0);
  const rafRef = useRef<number | null>(null);
  const targetRef = useRef(target);

  useEffect(() => {
    if (!Number.isFinite(target)) return;
    if (prefersReducedMotion()) {
      setValue(target);
      return;
    }
    targetRef.current = target;
    const from = 0;
    const start = performance.now();

    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      // easeOutCubic
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(from + (target - from) * eased);
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [target, duration]);

  return value;
}
