// @vitest-environment jsdom
/**
 * `RepairImpactMap` — every assertion traces to a field on `RepairPlan`. The
 * point of this suite is to catch the map drawing an execution guarantee
 * A7 doesn't have, putting an unordered node on the spine, fabricating a
 * severity, or misaligning the model-vs-graph order comparison.
 */
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RepairImpactMap } from "./RepairPlanSpine";
import type { RepairPlan } from "./repairPlanTypes";

const THREE_STEP: RepairPlan = {
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
      conflictsWith: ["finding-1"],
      why: {
        kind: "static_finding",
        message: "hardcoded secret",
        severity: 0.9,
        severityMeasured: true,
        tools: ["bandit"],
      },
      isHandoffTarget: false,
    },
    {
      issueId: "finding-1",
      position: 3,
      ordered: true,
      files: ["vulnapi/auth.py"],
      dependsOn: [],
      incomingEdges: [],
      conflictsWith: ["finding-0"],
      why: {
        kind: "static_finding",
        message: "weak comparison",
        severity: 0.4,
        severityMeasured: false,
        tools: ["ruff"],
      },
      isHandoffTarget: false,
    },
  ],
  conflictBatches: [["finding-0", "finding-1"]],
  orderingSource: "deterministic",
  orderingRationale: "",
  deterministicOrder: ["cve-CVE-1", "finding-0", "finding-1"],
  totalDependencyEdges: 1,
  executionAuthority: {
    consumedBy: "A7",
    field: "execution_order[0]",
    note: "A7 reads only the first step's identifier as a label. It derives its actual patch targets from A5's blast scope and A4's root cause.",
  },
  carriedForward: {
    acceptanceCriteria: ["reject tokens whose exp is in the past"],
    patchConstraints: ["Must not reintroduce: hardcoded secret"],
    targetFunction: "validate_token",
  },
};

const ONE_STEP: RepairPlan = {
  ...THREE_STEP,
  steps: [THREE_STEP.steps[0]],
  conflictBatches: [],
  totalDependencyEdges: 0,
  deterministicOrder: ["cve-CVE-1"],
};

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

describe("RepairImpactMap — single step (case 1)", () => {
  it("renders the focused single-repair view, not the spine", () => {
    render(<RepairImpactMap plan={clone(ONE_STEP)} />);
    expect(screen.getByRole("group", { name: /repair impact map/i })).toBeTruthy();
    expect(screen.getByText("cve-CVE-1")).toBeTruthy();
    expect(screen.getByText(/a7 input — consumed now/i)).toBeTruthy();
    expect(screen.getByText(/no other steps currently proposed/i)).toBeTruthy();
  });

  it("shows proposed-only, not consumed, when the single step isn't the handoff target", () => {
    const noHandoff = clone(ONE_STEP);
    noHandoff.steps[0].isHandoffTarget = false;
    render(<RepairImpactMap plan={noHandoff} />);
    expect(screen.getByText(/proposed only — not handed to a7/i)).toBeTruthy();
  });

  it("shows the plan-truth panel beside the focused view", () => {
    render(<RepairImpactMap plan={clone(ONE_STEP)} />);
    const truth = screen.getByRole("group", { name: /plan truth/i });
    expect(within(truth).getByText("A7 consumes").nextElementSibling?.textContent).toBe("1 step");
  });
});

describe("RepairImpactMap — multi-step spine (case 2)", () => {
  it("draws the execution boundary after the handoff step only", () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    const boundary = screen.getByRole("separator", { name: /a7 execution boundary/i });
    const handoff = screen.getByText("cve-CVE-1");
    const finding0 = screen.getByText("finding-0");
    expect(
      handoff.compareDocumentPosition(boundary) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      boundary.compareDocumentPosition(finding0) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("keeps an unordered step off the spine entirely", () => {
    const withUnordered = clone(THREE_STEP);
    withUnordered.steps[2].ordered = false;
    render(<RepairImpactMap plan={withUnordered} />);
    expect(screen.getByText(/unordered repair candidates/i)).toBeTruthy();
    expect(screen.getByText(/not assigned a reliable execution position/i)).toBeTruthy();
    // still present exactly once (in the unordered panel), never numbered onto the spine
    expect(screen.getAllByText("finding-1")).toHaveLength(1);
  });

  it("labels a file touched by more than one step as shared, not just a conflict badge", () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    const callout = screen.getByText(/3 repair.*touch it/i);
    expect(callout.textContent).toMatch(/cannot be treated as independent/i);
  });

  it("shows a measured severity as filled with its value, and an unmeasured one as unmeasured", () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    expect(screen.getByTitle("severity 0.90")).toBeTruthy();
    expect(screen.getByTitle("severity not measured")).toBeTruthy();
    expect(screen.queryByText(/0\.40/)).toBeNull();
  });

  it("marks a CVE-originated step as reaching in from a package advisory", () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    expect(screen.getByText(/cve \/ package advisory/i)).toBeTruthy();
  });

  it("shows the no-dependency-edges note without implying zero risk, only when true", () => {
    const noEdges = clone(THREE_STEP);
    noEdges.steps = noEdges.steps.map((s) => ({ ...s, incomingEdges: [], dependsOn: [] }));
    noEdges.totalDependencyEdges = 0;
    render(<RepairImpactMap plan={noEdges} />);
    const note = screen.getByText(/does not mean they carry no risk/i);
    expect(note.textContent).toMatch(/no dependency edges/i);
  });

  it("does not show the no-dependency-edges note when edges exist", () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    expect(screen.queryByText(/no dependency edges/i)).toBeNull();
  });

  it("expands a step's detail inline on click, including honest ordering and evidence scoping", async () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    await userEvent.click(screen.getAllByText("finding-0")[0]);
    expect(screen.getByText(/depends on/i)).toBeTruthy();
    expect(screen.getAllByText("cve-CVE-1").length).toBeGreaterThan(0);
    expect(screen.getByText(/not tied to this proposed step/i)).toBeTruthy();
  });

  it("shows the handoff step's real acceptance criteria and patch constraints on expand", async () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    await userEvent.click(screen.getByText("cve-CVE-1"));
    expect(screen.getByText("reject tokens whose exp is in the past")).toBeTruthy();
    expect(screen.getByText("Must not reintroduce: hardcoded secret")).toBeTruthy();
  });

  it("collapses the detail again on a second click", async () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    await userEvent.click(screen.getAllByText("finding-0")[0]);
    expect(screen.getByText(/depends on/i)).toBeTruthy();
    await userEvent.click(screen.getAllByText("finding-0")[0]);
    expect(screen.queryByText(/depends on/i)).toBeNull();
  });

  it("never shows a confidence figure anywhere on the map", () => {
    const { container } = render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    expect(container.textContent).not.toMatch(/confidence/i);
  });

  it("shows accurate plan-truth counts", () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    const truth = screen.getByRole("group", { name: /plan truth/i });
    expect(within(truth).getByText("1 step")).toBeTruthy(); // A7 consumes
    expect(within(truth).getByText("3 steps")).toBeTruthy(); // A6 proposes
    const dependencies = within(truth).getByText("Dependencies").nextElementSibling;
    expect(dependencies?.textContent).toBe("1");
    const conflicts = within(truth).getByText("Conflict batches").nextElementSibling;
    expect(conflicts?.textContent).toBe("1");
    const unordered = within(truth).getByText("Unordered").nextElementSibling;
    expect(unordered?.textContent).toBe("0");
  });

  it("shows LLM-proposed authority styling only when ordering came from the model", () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    const truth = screen.getByRole("group", { name: /plan truth/i });
    expect(within(truth).getByText("Graph-derived ✓")).toBeTruthy();
  });
});

describe("RepairImpactMap — model vs. graph order comparison", () => {
  it("is absent when ordering is deterministic", () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    expect(screen.queryByRole("group", { name: /order comparison/i })).toBeNull();
  });

  it("shows 'matches' when the model order agrees with the graph order", () => {
    const llm = clone(THREE_STEP);
    llm.orderingSource = "llm";
    render(<RepairImpactMap plan={llm} />);
    const cmp = screen.getByRole("group", { name: /order comparison/i });
    expect(within(cmp).getByText(/model order matches dependency order/i)).toBeTruthy();
  });

  it("counts and reports displaced positions when the model reorders steps", () => {
    const llm = clone(THREE_STEP);
    llm.orderingSource = "llm";
    llm.steps = [llm.steps[0], llm.steps[2], llm.steps[1]].map((s, i) => ({
      ...s,
      position: i + 1,
    }));
    render(<RepairImpactMap plan={llm} />);
    const cmp = screen.getByRole("group", { name: /order comparison/i });
    expect(within(cmp).getByText(/2 positions differ from dependency order/i)).toBeTruthy();
  });

  it("shows an honest 'validation unavailable' message instead of fabricating a graph order", () => {
    const legacy = clone(THREE_STEP);
    legacy.orderingSource = "llm";
    legacy.deterministicOrder = [];
    render(<RepairImpactMap plan={legacy} />);
    expect(screen.getByText(/dependency validation unavailable/i)).toBeTruthy();
    expect(screen.queryByRole("group", { name: /order comparison/i })).toBeNull();
  });

  it("reindexes graph order within the model-order overlap, ignoring an omitted node", () => {
    const llm = clone(THREE_STEP);
    llm.orderingSource = "llm";
    llm.steps[2].ordered = false; // finding-1 unnamed by the model
    llm.deterministicOrder = ["cve-CVE-1", "finding-1", "finding-0"];
    render(<RepairImpactMap plan={llm} />);
    const cmp = screen.getByRole("group", { name: /order comparison/i });
    expect(within(cmp).getByText(/model order matches dependency order/i)).toBeTruthy();
  });
});

describe("RepairImpactMap — file interaction", () => {
  it("shows how many repairs touch a hovered file", async () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    const track = screen.getByTitle("vulnapi/auth.py");
    await userEvent.hover(track);
    expect(await screen.findByText(/3 repairs touch this file/i)).toBeTruthy();
  });
});

describe("RepairImpactMap — step hover", () => {
  it("keeps a step's own dependency source at full opacity, not dimmed, when hovering the dependent step", async () => {
    render(<RepairImpactMap plan={clone(THREE_STEP)} />);
    // finding-0 depends on cve-CVE-1 (incomingEdges: [{fromIssue: "cve-CVE-1"}]).
    // Hovering finding-0 must highlight cve-CVE-1, its dependency source, not
    // dim it — dimming only checked each row's own incoming edges, which is
    // empty for the upstream step and dimmed it incorrectly.
    const findingRow = screen.getAllByText("finding-0")[0].closest("button")!;
    await userEvent.hover(findingRow);
    const cveRow = screen.getAllByText("cve-CVE-1")[0].closest("[style]") as HTMLElement;
    expect(cveRow.style.opacity).toBe("1");
  });
});
