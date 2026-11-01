import { ArrowDown, RefreshCcw, XCircle, ArrowRight } from "lucide-react";
import { type RepairAttemptsModel } from "@/mocks";

/**
 * Repair Attempts. Fully prop-driven — the model comes from
 * `runService.getRepairAttempts(runId)`, which is real backend data in live
 * mode and the bundled fixture in mock mode.
 */
export function RetrySequence({ model }: { model: RepairAttemptsModel }) {
  const { attempts, failureMessage, nextStepLabel } = model;
  const rejected = attempts.filter((a) => a.result !== "Validation Passed").length;
  return (
    <section>
      <header className="mb-4">
        <div className="text-[11px] font-medium uppercase tracking-[0.14em] text-ink-soft">
          Repair Attempts
        </div>
        <h2 className="mt-1.5 text-2xl font-bold tracking-tight text-ink">
          The agent tried, validated, and tried again
        </h2>
        <p className="mt-1 text-sm text-ink-soft">
          {rejected === attempts.length
            ? `${attempts.length} repair attempts were generated and rejected before routing to a human reviewer.`
            : `${attempts.length} repair attempts were generated; ${rejected} were rejected before one passed validation.`}
        </p>
      </header>

      <div className="rounded-2xl border border-border bg-surface p-5">
        <div className="flex flex-col gap-3">
          {attempts.map((a, i) => {
            // Colour each row by its own outcome — a passing attempt rendered
            // in retry-amber reads as another failure.
            const passed = a.result === "Validation Passed";
            const tone = passed
              ? "bg-status-completed-bg text-status-completed"
              : "bg-status-retry-bg text-status-retry";
            return (
              <div key={a.n} className="flex flex-col gap-3">
                <div
                  style={{ animationDelay: `${i * 180}ms` }}
                  className="animate-card-in rounded-xl border border-border bg-surface-muted/50 px-4 py-3"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-start gap-3 min-w-0">
                      <span
                        className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${tone}`}
                      >
                        <RefreshCcw className="h-3.5 w-3.5" />
                      </span>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 text-sm">
                          <span className="font-semibold text-ink">Attempt {a.n}</span>
                          <span className="text-ink-soft">·</span>
                          <span className="text-ink-soft">{a.action}</span>
                        </div>
                        <div className="mt-1 text-sm text-ink-soft">{a.detail}</div>
                      </div>
                    </div>
                    <span
                      className={`shrink-0 inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-medium ${tone}`}
                    >
                      {a.result} · {a.scoreLabel ?? `mutation ${a.mutation.toFixed(2)}`}
                    </span>
                  </div>
                </div>
                {i < attempts.length - 1 && (
                  <ArrowDown className="mx-auto h-4 w-4 text-ink-soft/60" />
                )}
              </div>
            );
          })}

          <div className="mt-1 rounded-xl border border-border bg-surface-muted/50 px-4 py-3">
            <div className="flex items-center gap-3 text-sm">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-status-failed">
                <XCircle className="h-4 w-4" />
              </span>
              <span className="font-semibold text-ink">Maximum retry reached</span>
              <span className="text-ink-soft">·</span>
              <span className="text-ink-soft">{failureMessage}</span>
            </div>
            <div className="mt-2 flex items-center gap-3 text-sm">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-status-draft-bg text-status-draft">
                <ArrowRight className="h-3.5 w-3.5" />
              </span>
              <span className="font-semibold text-ink">{nextStepLabel}</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
