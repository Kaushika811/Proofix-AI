/**
 * Client-side error reporting seam.
 *
 * Forwards React error-boundary failures to whatever monitoring provider is
 * registered on `window.__errorReporter` (Sentry, a custom collector, or
 * nothing at all). No-ops safely during SSR and when no reporter is installed.
 */
type ErrorReportOptions = {
  mechanism?: "manual" | "onerror" | "unhandledrejection" | "react_error_boundary";
  handled?: boolean;
  severity?: "error" | "warning" | "info";
};

type ErrorReporter = {
  captureException?: (
    error: unknown,
    context?: Record<string, unknown>,
    options?: ErrorReportOptions,
  ) => void;
};

declare global {
  interface Window {
    __errorReporter?: ErrorReporter;
  }
}

export function reportError(error: unknown, context: Record<string, unknown> = {}) {
  if (typeof window === "undefined") return;
  window.__errorReporter?.captureException?.(
    error,
    {
      source: "react_error_boundary",
      route: window.location.pathname,
      ...context,
    },
    {
      mechanism: "react_error_boundary",
      handled: false,
      severity: "error",
    },
  );
}
