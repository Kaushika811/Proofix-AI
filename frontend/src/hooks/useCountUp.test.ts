// @vitest-environment jsdom
/**
 * Reduced motion (B-F06).
 *
 * `styles.css` has a global `prefers-reduced-motion` block that neutralises CSS
 * animations and transitions. It cannot reach two things this app does in
 * JavaScript, which is what these pin:
 *
 *   - `requestAnimationFrame` count-ups are not animations as far as CSS is
 *     concerned, so they ran regardless of the setting.
 *   - `window.scrollTo({behavior: "smooth"})` beats the stylesheet's
 *     `scroll-behavior: auto`, because the explicit option wins.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { prefersReducedMotion, scrollBehavior } from "./useCountUp";

function setReducedMotion(reduce: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: reduce && query.includes("prefers-reduced-motion"),
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("prefersReducedMotion", () => {
  it("is true when the viewer asked for less motion", () => {
    setReducedMotion(true);
    expect(prefersReducedMotion()).toBe(true);
  });

  it("is false by default", () => {
    setReducedMotion(false);
    expect(prefersReducedMotion()).toBe(false);
  });

  it("is read at call time, so a change mid-session is honoured", () => {
    setReducedMotion(false);
    expect(prefersReducedMotion()).toBe(false);
    setReducedMotion(true);
    expect(prefersReducedMotion()).toBe(true);
  });
});

describe("scrollBehavior", () => {
  it("jumps rather than glides when motion is reduced", () => {
    setReducedMotion(true);
    expect(scrollBehavior()).toBe("auto");
  });

  it("glides otherwise", () => {
    setReducedMotion(false);
    expect(scrollBehavior()).toBe("smooth");
  });
});
