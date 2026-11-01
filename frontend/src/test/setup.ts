/**
 * jsdom shims for the component suites.
 *
 * jsdom implements no layout and no Web Animations API, and the workspace uses
 * both: the execution journal measures agent headers to anchor the active card,
 * and waits on running animations before scrolling. None of that is what the
 * component tests are asserting, so it is stubbed rather than worked around in
 * the components themselves — the production code must keep its real
 * choreography.
 *
 * Loaded via `test.setupFiles`, and harmless in the node-environment suites,
 * which never touch `document`.
 */
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

if (typeof document !== "undefined") {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.getAnimations = vi.fn(() => []);
  (document as unknown as { getAnimations: () => unknown[] }).getAnimations = vi.fn(() => []);

  // Used by the chart/visualization components to size themselves.
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;

  // rAF and its cancel must be stubbed as a *pair*. Replacing only `request`
  // left `cancelAnimationFrame` unable to clear the timeout it returned, so
  // `useCountUp`'s animation loop outlived unmount and kept calling `setState`
  // after the environment was torn down. Passing a real timestamp matters too:
  // the hook computes progress as `now - performance.now()`, and a constant 0
  // makes that always negative, so the loop never terminates.
  window.requestAnimationFrame = ((cb: FrameRequestCallback) =>
    setTimeout(
      () => cb(performance.now()),
      0,
    ) as unknown as number) as typeof requestAnimationFrame;
  window.cancelAnimationFrame = ((handle: number) =>
    clearTimeout(
      handle as unknown as ReturnType<typeof setTimeout>,
    )) as typeof cancelAnimationFrame;

  window.matchMedia ??= ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;

  window.scrollTo = vi.fn();

  // Unmount between tests. Left mounted, the workspace's polling interval keeps
  // firing after the environment is torn down and reports as an unhandled
  // "window is not defined".
  afterEach(cleanup);
}
