/**
 * A6 — Repair Planner.
 *
 * Visualization-first: `RepairImpactMap` (`RepairPlanSpine.tsx`) is the entire
 * first viewport. Everything else on this panel is secondary evidence, moved
 * below into collapsible sections so it never competes with the picture for
 * attention. No order, dependency, conflict or "why" is computed client-side
 * — every fact traces to `GET /api/runs/{runId}/repair-plan`
 * (`services/ui_projection.py::build_repair_plan`).
 *
 * The one fact everything here defers to: A7 reads exactly
 * `execution_order[0]` as a label and derives its real patch targets from
 * A5/A4. `RepairImpactMap` draws that as a hard execution boundary; this
 * panel never repeats the same finding in three different cards to say it
 * again.
 */
import { useEffect, useState } from "react";
import type { RepairPlan, RepairStep } from "./repairPlanTypes";
import { RepairScopeEditor } from "./RepairScopeEditor";
import { getRepairPlan } from "@/lib/runService";
import type { AgentStatus } from "./data";

// ------------------------------------------------------------------ chrome

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load the repair plan</span>
      <span className="font-mono text-ink-soft">{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className="ml-auto rounded border border-border px-2 py-0.5 font-medium text-ink transition-colors hover:bg-surface-muted"
      >
        Retry
      </button>
    </div>
  );
}

// ------------------------------------------------------------- ordering banner

/**
 * Always on screen, never collapsible — the one warning `RepairImpactMap`'s
 * structure (dashed spine, hollow nodes) reinforces visually but must not
 * depend on the reader noticing.
 */
function OrderingBanner({ plan }: { plan: RepairPlan }) {
  if (plan.orderingSource !== "llm") return null;
  return (
    <div
      role="note"
      className="rounded-md border border-status-retry/40 bg-status-retry-bg/20 px-3 py-1.5 text-[10px] text-ink"
    >
      <span className="font-semibold uppercase tracking-wider text-status-retry">
        Model-proposed order
      </span>{" "}
      — dependency consistency not validated for steps below the execution boundary.
    </div>
  );
}

// ------------------------------------------------------------------ secondary

function Collapsible({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <details className="group rounded-md border border-border">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-ink-soft marker:content-none">
        <span className="text-ink-soft transition-transform group-open:rotate-90">▸</span>
        {title}
        {count !== undefined && <span className="font-mono text-ink-soft/70">({count})</span>}
      </summary>
      <div className="border-t border-border px-3 py-2.5">{children}</div>
    </details>
  );
}

function AcceptanceCriteria({ plan }: { plan: RepairPlan }) {
  if (plan.carriedForward === null) {
    return (
      <p className="text-[11px] text-ink-soft">
        Not measured — A5.5 has not produced a context package for this run.
      </p>
    );
  }
  const { acceptanceCriteria } = plan.carriedForward;
  if (acceptanceCriteria.length === 0) {
    return <p className="text-[11px] text-ink-soft">A5.5 produced no acceptance criteria.</p>;
  }
  return (
    <ul className="list-disc space-y-0.5 pl-4">
      {acceptanceCriteria.map((c) => (
        <li key={c} className="text-[11px] text-ink">
          {c}
        </li>
      ))}
    </ul>
  );
}

function PatchConstraints({ plan }: { plan: RepairPlan }) {
  if (plan.carriedForward === null) {
    return (
      <p className="text-[11px] text-ink-soft">
        Not measured — A5.5 has not produced a context package for this run.
      </p>
    );
  }
  const { patchConstraints } = plan.carriedForward;
  if (patchConstraints.length === 0) {
    return <p className="text-[11px] text-ink-soft">A5.5 produced no patch constraints.</p>;
  }
  return (
    <ul className="list-disc space-y-0.5 pl-4">
      {patchConstraints.map((c) => (
        <li key={c} className="text-[11px] text-ink">
          {c}
        </li>
      ))}
    </ul>
  );
}

function ModelRationale({ plan }: { plan: RepairPlan }) {
  if (plan.orderingSource !== "llm") {
    return (
      <p className="text-[11px] text-ink-soft">
        Not applicable — this plan&apos;s order came from the deterministic topological sort, not a
        model call.
      </p>
    );
  }
  if (plan.orderingRationale.trim().length === 0) {
    return <p className="text-[11px] text-ink-soft">The model returned no rationale.</p>;
  }
  return <p className="text-[11px] text-ink">{plan.orderingRationale}</p>;
}

function FullPlanLedger({ steps }: { steps: RepairStep[] }) {
  return (
    <div className="max-h-[320px] overflow-auto rounded border border-border">
      <table className="w-full border-collapse text-left text-[11px]">
        <thead className="sticky top-0 bg-surface">
          <tr className="border-b border-border text-[9px] uppercase tracking-wider text-ink-soft">
            <th className="px-2 py-1.5 font-medium">#</th>
            <th className="px-2 py-1.5 font-medium">Issue</th>
            <th className="px-2 py-1.5 font-medium">Ordered</th>
            <th className="px-2 py-1.5 font-medium">Files</th>
            <th className="px-2 py-1.5 font-medium">Depends on</th>
            <th className="px-2 py-1.5 font-medium">Conflicts</th>
          </tr>
        </thead>
        <tbody>
          {steps.map((s) => (
            <tr key={s.issueId} className="border-b border-border/60">
              <td className="px-2 py-1 font-mono text-ink-soft">{s.position}</td>
              <td className="px-2 py-1 font-mono text-ink">{s.issueId}</td>
              <td className="px-2 py-1 font-mono text-ink-soft">{s.ordered ? "yes" : "no"}</td>
              <td className="px-2 py-1 font-mono text-ink-soft">{s.files.join(", ") || "—"}</td>
              <td className="px-2 py-1 font-mono text-ink-soft">{s.dependsOn.join(", ") || "—"}</td>
              <td className="px-2 py-1 text-status-retry">{s.conflictsWith.join(", ") || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------- panel

export function RepairPlanPanel({ runId, status }: { runId: string; status?: AgentStatus }) {
  const [plan, setPlan] = useState<RepairPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async () => {
      try {
        const next = await getRepairPlan(runId);
        if (!cancelled) {
          setPlan(next);
          setError(null);
        }
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (!cancelled) {
          setLoading(false);
          timer = setTimeout(refresh, 5000);
        }
      }
    };
    void refresh();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [runId, attempt, status]);

  if (loading) {
    return (
      <section className="rounded-md border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Repair Planner
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">Loading repair plan…</p>
      </section>
    );
  }

  if (error) {
    return <ErrorBanner message={error} onRetry={() => setAttempt((n) => n + 1)} />;
  }

  if (!plan) {
    return (
      <section className="rounded-md border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Repair Planner
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          {status === "running"
            ? "RUNNING — A6 is sequencing the repair plan now; this panel renders once it publishes."
            : "Pending — A6 has not completed for this run yet."}
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-3 rounded-md border border-border bg-surface p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
            Repair Planner
          </h3>
          <p className="mt-0.5 text-[11px] text-ink-soft">
            Create a dependency-aware repair order.
          </p>
        </div>
        <div className="font-mono text-[11px] text-ink-soft">
          {plan.steps.length} step{plan.steps.length === 1 ? "" : "s"} · {plan.totalDependencyEdges}{" "}
          dependency edge{plan.totalDependencyEdges === 1 ? "" : "s"}
        </div>
      </div>

      <OrderingBanner plan={plan} />

      {plan.steps.length === 0 ? (
        <div className="rounded-md border border-dashed border-border py-8 text-center">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-ink-soft">
            No repair plan
          </div>
          <p className="mt-1 text-[11px] text-ink-soft">A6 produced no actionable repair steps.</p>
        </div>
      ) : (
        <RepairScopeEditor key={runId} plan={plan} />
      )}

      {plan.steps.length > 0 && (
        <div className="space-y-1.5">
          <Collapsible
            title="Acceptance criteria"
            count={plan.carriedForward?.acceptanceCriteria.length}
          >
            <AcceptanceCriteria plan={plan} />
          </Collapsible>
          <Collapsible
            title="Patch constraints"
            count={plan.carriedForward?.patchConstraints.length}
          >
            <PatchConstraints plan={plan} />
          </Collapsible>
          <Collapsible title="Model rationale">
            <ModelRationale plan={plan} />
          </Collapsible>
          <Collapsible title="Full plan ledger" count={plan.steps.length}>
            <FullPlanLedger steps={plan.steps} />
          </Collapsible>
        </div>
      )}

      <p className="text-[9px] text-ink-soft/70">
        Source: <code className="font-mono">GET /api/runs/{"{run_id}"}/repair-plan</code>
      </p>
    </section>
  );
}
