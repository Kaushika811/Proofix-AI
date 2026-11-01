// @vitest-environment jsdom
/**
 * `RepairPlanPanel` orchestrates loading/error/pending states and the
 * secondary evidence sections around `RepairImpactMap` (covered in
 * `RepairPlanSpine.test.tsx`). The point of this suite is the panel's own
 * job: never hide the LLM-ordering warning behind a collapsible, never show
 * an empty DAG, and keep the visualization as the primary content — not a
 * repeated card.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getRepairPlan = vi.fn();

vi.mock("@/lib/runService", () => ({
  getRepairPlan: (...a: unknown[]) => getRepairPlan(...a),
}));

const { RepairPlanPanel } = await import("./RepairPlanPanel");
import type { RepairPlan } from "./repairPlanTypes";

const PLAN: RepairPlan = {
  steps: [
    {
      issueId: "cve-CVE-1",
      position: 1,
      ordered: true,
      files: ["vulnapi/auth.py"],
      dependsOn: [],
      incomingEdges: [],
      conflictsWith: [],
      why: {
        kind: "cve",
        package: "pyjwt",
        severity: "HIGH",
        installedVersion: "1.0.0",
        reachPath: ["vulnapi/auth.py"],
      },
      isHandoffTarget: true,
    },
    {
      issueId: "finding-0",
      position: 2,
      ordered: true,
      files: ["vulnapi/auth.py"],
      dependsOn: ["cve-CVE-1"],
      incomingEdges: [
        { fromIssue: "cve-CVE-1", reason: "cve_reachability:pyjwt->vulnapi/auth.py" },
      ],
      conflictsWith: [],
      why: {
        kind: "static_finding",
        message: "hardcoded secret",
        severity: 0.9,
        severityMeasured: true,
        tools: ["bandit"],
      },
      isHandoffTarget: false,
    },
  ],
  conflictBatches: [],
  orderingSource: "deterministic",
  orderingRationale: "",
  deterministicOrder: ["cve-CVE-1", "finding-0"],
  totalDependencyEdges: 1,
  executionAuthority: {
    consumedBy: "A7",
    field: "execution_order[0]",
    note: "A7 reads only the first step's identifier as a label for its patch bundle.",
  },
  carriedForward: {
    acceptanceCriteria: ["reject tokens whose exp is in the past"],
    patchConstraints: ["Must not reintroduce: hardcoded secret"],
    targetFunction: "validate_token",
  },
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

beforeEach(() => {
  getRepairPlan.mockReset();
});

describe("RepairPlanPanel", () => {
  it("renders the impact map as the primary content", async () => {
    getRepairPlan.mockResolvedValue(clone(PLAN));
    render(<RepairPlanPanel runId="run-1" />);
    expect(await screen.findByRole("group", { name: /repair impact map/i })).toBeTruthy();
  });

  it("never hides the LLM-ordering warning behind a collapsible", async () => {
    const llm = clone(PLAN);
    llm.orderingSource = "llm";
    getRepairPlan.mockResolvedValue(llm);
    render(<RepairPlanPanel runId="run-1" />);
    const banner = await screen.findByRole("note");
    expect(banner.textContent).toMatch(/model-proposed order/i);
    expect(banner.closest("details")).toBeNull();
  });

  it("shows no ordering banner when ordering is deterministic", async () => {
    getRepairPlan.mockResolvedValue(clone(PLAN));
    render(<RepairPlanPanel runId="run-1" />);
    await screen.findByRole("group", { name: /repair impact map/i });
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("shows an honest empty state instead of an empty DAG", async () => {
    const empty: RepairPlan = {
      ...clone(PLAN),
      steps: [],
      conflictBatches: [],
      totalDependencyEdges: 0,
    };
    getRepairPlan.mockResolvedValue(empty);
    render(<RepairPlanPanel runId="run-1" />);
    expect(await screen.findByText(/no repair plan/i)).toBeTruthy();
    expect(screen.getByText(/a6 produced no actionable repair steps/i)).toBeTruthy();
    expect(screen.queryByRole("group", { name: /repair impact map/i })).toBeNull();
  });

  it("does not render secondary sections when there are no steps", async () => {
    const empty: RepairPlan = {
      ...clone(PLAN),
      steps: [],
      conflictBatches: [],
      totalDependencyEdges: 0,
    };
    getRepairPlan.mockResolvedValue(empty);
    render(<RepairPlanPanel runId="run-1" />);
    await screen.findByText(/no repair plan/i);
    expect(screen.queryByText(/full plan ledger/i)).toBeNull();
  });

  it("keeps secondary evidence collapsed by default, below the visualization", async () => {
    getRepairPlan.mockResolvedValue(clone(PLAN));
    render(<RepairPlanPanel runId="run-1" />);
    await screen.findByRole("group", { name: /repair impact map/i });

    const ledgerSummary = screen.getByText(/full plan ledger/i);
    const details = ledgerSummary.closest("details")!;
    // Native <details> keeps its content in the DOM either way — closed is a
    // real CSS-hidden state, not an unmounted one — so the behavioral check
    // is the `open` attribute, not text presence.
    expect(details.open).toBe(false);

    await userEvent.click(ledgerSummary);
    expect(details.open).toBe(true);
    expect(within(details).getAllByText("cve-CVE-1").length).toBeGreaterThan(0);
  });

  it("shows the handoff step's real acceptance criteria under the collapsible section", async () => {
    getRepairPlan.mockResolvedValue(clone(PLAN));
    render(<RepairPlanPanel runId="run-1" />);
    await screen.findByRole("group", { name: /repair impact map/i });

    const summary = screen.getByText(/acceptance criteria/i, { selector: "summary" });
    await userEvent.click(summary);
    expect(screen.getByText("reject tokens whose exp is in the past")).toBeTruthy();
  });

  it("says acceptance criteria are not measured when A5.5 never ran", async () => {
    const noContext = clone(PLAN);
    noContext.carriedForward = null;
    getRepairPlan.mockResolvedValue(noContext);
    render(<RepairPlanPanel runId="run-1" />);
    await screen.findByRole("group", { name: /repair impact map/i });

    const summary = screen.getByText(/acceptance criteria/i, { selector: "summary" });
    await userEvent.click(summary);
    expect(within(summary.closest("details")!).getByText(/not measured/i)).toBeTruthy();
  });

  it("shows the model's rationale only when ordering came from the LLM", async () => {
    const llm = clone(PLAN);
    llm.orderingSource = "llm";
    llm.orderingRationale = "Fix the reachable CVE before the app-level finding.";
    getRepairPlan.mockResolvedValue(llm);
    render(<RepairPlanPanel runId="run-1" />);
    await screen.findByRole("group", { name: /repair impact map/i });

    const summary = screen.getByText(/model rationale/i, { selector: "summary" });
    await userEvent.click(summary);
    expect(screen.getByText(/fix the reachable cve/i)).toBeTruthy();
  });

  it("says model rationale is not applicable on the deterministic path", async () => {
    getRepairPlan.mockResolvedValue(clone(PLAN));
    render(<RepairPlanPanel runId="run-1" />);
    await screen.findByRole("group", { name: /repair impact map/i });

    const summary = screen.getByText(/model rationale/i, { selector: "summary" });
    await userEvent.click(summary);
    expect(screen.getByText(/not applicable/i)).toBeTruthy();
  });

  it("distinguishes A6 pending from A6 running", async () => {
    getRepairPlan.mockResolvedValue(null);
    const { unmount } = render(<RepairPlanPanel runId="run-1" />);
    expect(await screen.findByText(/a6 has not completed/i)).toBeTruthy();
    unmount();

    getRepairPlan.mockResolvedValue(null);
    render(<RepairPlanPanel runId="run-2" status="running" />);
    expect(await screen.findByText(/a6 is sequencing/i)).toBeTruthy();
  });

  it("reports a load failure and retries on demand", async () => {
    getRepairPlan.mockRejectedValueOnce(new Error("API 500: Internal Server Error"));
    getRepairPlan.mockResolvedValueOnce(clone(PLAN));
    render(<RepairPlanPanel runId="run-1" />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByRole("group", { name: /repair impact map/i })).toBeTruthy();
  });

  it("never shows a confidence figure anywhere on the panel", async () => {
    getRepairPlan.mockResolvedValue(clone(PLAN));
    const { container } = render(<RepairPlanPanel runId="run-1" />);
    await screen.findByRole("group", { name: /repair impact map/i });
    expect(container.textContent).not.toMatch(/confidence/i);
  });
});
