// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ImpactGraph } from "./ImpactGraph";
import { RepairDependencyGraph } from "./RepairDependencyGraph";
import { RepairDag } from "./RepairDag";
import type { BlastImpact } from "./blastTypes";
import type { RepairPlan } from "./repairPlanTypes";

function impact(count: number): BlastImpact {
  return {
    origin: null,
    origins: ["src/origin.py"],
    maxHop: 1,
    autoPatchScope: [],
    humanReviewRequired: [],
    patchAuthorityOverlap: [],
    riskMeasurementCaveat: "unmeasured",
    capabilities: null,
    scope: Array.from({ length: count }, (_, i) => ({
      path: i ? `src/module${i}/consumer.py` : "src/origin.py",
      hopCount: i ? 1 : 0,
      reachedVia: i ? "src/origin.py" : null,
      directions: ["forward"],
      edgeBasis: i ? "resolved_suffix" : null,
      propagationConfidence: null,
      priorityScore: null,
      origin: "src/origin.py",
      role: null,
      criticality: null,
      churnWeight: null,
      autoPatchable: false,
      humanReviewRequired: false,
      hasStaticFinding: false,
    })),
    edges: Array.from({ length: count - 1 }, (_, i) => ({
      from: "src/origin.py",
      to: `src/module${i + 1}/consumer.py`,
      direction: "forward",
      basis: "resolved_suffix",
      hopCount: 1,
    })),
  };
}
function plan(count: number): RepairPlan {
  return {
    steps: Array.from({ length: count }, (_, i) => ({
      issueId: `repair-${i}`,
      position: i + 1,
      ordered: true,
      files: [`src/${i}.py`],
      dependsOn: i ? [`repair-${i - 1}`] : [],
      incomingEdges: i
        ? [{ fromIssue: `repair-${i - 1}`, reason: "Requires preceding interface change" }]
        : [],
      conflictsWith: [],
      why: null,
      isHandoffTarget: i === 0,
    })),
    conflictBatches: [],
    orderingSource: "deterministic",
    orderingRationale: "",
    deterministicOrder: [],
    totalDependencyEdges: count - 1,
    executionAuthority: {
      consumedBy: "A7",
      field: "execution_order[0]",
      note: "Plan is a proposal.",
    },
    carriedForward: null,
  };
}

describe("Adaptive agent graphs", () => {
  it("shows a single-repair relationship map with details only after selection", async () => {
    render(<RepairDag plan={plan(1)} />);
    expect(screen.getByRole("group", { name: "Single repair relationships" })).toBeTruthy();
    expect(screen.queryByText("Inspect change & ordering")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Inspect repair change" }));
    expect(screen.getByText("Inspect change & ordering")).toBeTruthy();
    await userEvent.click(screen.getByText("Clear prerequisite highlight"));
    expect(screen.queryByText("Inspect change & ordering")).toBeNull();
  });
  it("opens only the selected candidate in a multi-step plan", async () => {
    render(<RepairDag plan={plan(3)} />);
    expect(screen.queryByText("Inspect change & ordering")).toBeNull();
    const graph = screen.getByLabelText("Repair dependency graph");
    await userEvent.click(within(graph).getByRole("button", { name: /Repair 2.py/ }));
    expect(screen.getAllByText("Inspect change & ordering")).toHaveLength(1);
  });

  it("renders recorded writes and bundle-scoped validation", async () => {
    const data = plan(1);
    data.steps[0].evidence = {
      execution: "applied",
      appliedFiles: ["src/0.py"],
      changes: [
        {
          file: "src/0.py",
          targetFunction: "validate",
          expectedOutcome: "Reject expired tokens",
          diff: "-old\n+new",
        },
      ],
      validation: "passed",
      validationScope: "patch_bundle",
      targetTestPassed: true,
      regressionTestsPassed: true,
      patchHash: "hash",
      note: "Bundle-level evidence",
    };
    render(<RepairDag plan={data} />);
    expect(screen.getByText("Files applied")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Inspect repair change" }));
    expect(screen.getByText("Validation: passed (whole patch bundle)")).toBeTruthy();
    expect(screen.getByText("Reject expired tokens")).toBeTruthy();
    await userEvent.click(screen.getByText("Inspect applied diff"));
    expect(screen.getByText(/-old/)).toBeTruthy();
  });

  it("shows individual files for a small impact scope and filters paths", async () => {
    const onSelect = vi.fn();
    render(<ImpactGraph impact={impact(3)} onSelect={onSelect} />);
    await userEvent.click(screen.getByRole("button", { name: "Inspect src/module1/consumer.py" }));
    expect(onSelect.mock.calls[0][0].path).toBe("src/module1/consumer.py");
    await userEvent.type(screen.getByLabelText("Find affected file"), "module2");
    expect(screen.queryByRole("button", { name: "Inspect src/module1/consumer.py" })).toBeNull();
    expect(screen.getByRole("button", { name: "Inspect src/origin.py" })).toBeTruthy();
  });
  it("bounds the graph for 1000 files while retaining searchable access to every file", async () => {
    render(<ImpactGraph impact={impact(1000)} onSelect={vi.fn()} />);
    expect(screen.getAllByRole("button", { name: /^Inspect / })).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: "Inspect Depth 1" }));
    expect(screen.getByText("Show 12 more files")).toBeTruthy();
    await userEvent.type(screen.getByLabelText("Find affected file"), "module999");
    expect(screen.getByRole("button", { name: "Inspect src/module999/consumer.py" })).toBeTruthy();
  });
  it("terminates tracing when parent metadata has a cycle", async () => {
    const data = impact(3);
    data.scope[1].reachedVia = data.scope[2].path;
    data.scope[2].reachedVia = data.scope[1].path;
    data.edges.push(
      {
        from: data.scope[2].path,
        to: data.scope[1].path,
        direction: "forward",
        basis: "resolved_suffix",
        hopCount: 1,
      },
      {
        from: data.scope[1].path,
        to: data.scope[2].path,
        direction: "forward",
        basis: "resolved_suffix",
        hopCount: 1,
      },
    );
    render(<ImpactGraph impact={data} selectedPath={data.scope[1].path} onSelect={vi.fn()} />);
    await userEvent.click(screen.getByText("Trace selected path"));
    expect(screen.getByText("3 / 3 files")).toBeTruthy();
  });
  it("compresses a 1000-step chain and keeps the final candidate accessible", async () => {
    const select = vi.fn();
    render(<RepairDependencyGraph plan={plan(1000)} onSelect={select} />);
    const graph = screen.getByLabelText("Repair dependency graph");
    expect(within(graph).getAllByRole("button").length).toBeLessThanOrEqual(12);
    await userEvent.click(within(graph).getAllByRole("button").at(-1)!);
    while (screen.queryByText("Show next 12 candidates in group"))
      await userEvent.click(screen.getByText("Show next 12 candidates in group"));
    await userEvent.click(screen.getByText("1000. repair-999"));
    expect(select).toHaveBeenCalledWith("repair-999");
  });
  it("detects cycles beyond the first 24 steps", () => {
    const data = plan(40);
    data.steps[30].dependsOn = ["repair-31"];
    render(<RepairDependencyGraph plan={data} onSelect={vi.fn()} />);
    expect(screen.getByRole("alert").textContent).toContain("cycle");
  });
  it("marks dangling prerequisites as incomplete", () => {
    const data = plan(2);
    data.steps[1].dependsOn.push("missing-step");
    render(<RepairDependencyGraph plan={data} onSelect={vi.fn()} />);
    expect(screen.getByText(/Graph is incomplete/)).toBeTruthy();
  });
  it("shows execution and validation as unavailable rather than inferring applied status", async () => {
    render(<RepairDag plan={plan(1)} />);
    await userEvent.click(screen.getByRole("button", { name: "Inspect repair change" }));
    expect(screen.getByText("Execution: unavailable")).toBeTruthy();
    expect(screen.getByText("Validation: unavailable")).toBeTruthy();
    expect(screen.queryByText("Applied")).toBeNull();
  });
});

it("filters patch eligibility while preserving source context", async () => {
  const data = impact(4);
  data.scope[2].autoPatchable = true;
  render(<ImpactGraph impact={data} onSelect={vi.fn()} />);
  await userEvent.selectOptions(screen.getByLabelText("Patch eligibility filter"), "auto");
  expect(screen.getByRole("button", { name: "Inspect src/origin.py" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Inspect src/module2/consumer.py" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Inspect src/module1/consumer.py" })).toBeNull();
});

it("does not invent a path when predecessor metadata lacks a recorded edge", () => {
  const data = impact(3);
  data.scope[1].reachedVia = data.scope[2].path;
  render(<ImpactGraph impact={data} selectedPath={data.scope[1].path} onSelect={vi.fn()} />);
  expect(
    within(screen.getByLabelText("Recorded dependency path")).queryByText(data.scope[2].path),
  ).toBeNull();
});
