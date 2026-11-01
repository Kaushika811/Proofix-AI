// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { RepairScopeEditor } from "./RepairScopeEditor";
import type { RepairPlan } from "./repairPlanTypes";
afterEach(cleanup);
const fixture = (count: number): RepairPlan => ({
  steps: Array.from({ length: count }, (_, i) => ({
    issueId: `id${i}`,
    position: i + 1,
    ordered: true,
    files: [`src/file${i}.py`],
    dependsOn: i ? ["id0"] : [],
    incomingEdges: [],
    conflictsWith: [],
    why: null,
    isHandoffTarget: i === 0,
  })),
  conflictBatches: [],
  orderingSource: null,
  orderingRationale: "",
  deterministicOrder: [],
  totalDependencyEdges: 0,
  executionAuthority: { consumedBy: "A7", field: "plan", note: "" },
  carriedForward: null,
});
it("shows a single planned repair and missing evidence honestly", () => {
  render(<RepairScopeEditor plan={fixture(1)} />);
  expect(screen.getByText("Plan for file0.py")).toBeTruthy();
  expect(screen.getByText("No prerequisite recorded.")).toBeTruthy();
  fireEvent.click(screen.getByText("Explain this decision"));
  expect(screen.getByText("No supporting finding was linked to this step.")).toBeTruthy();
});
it("searches a large plan and navigates prerequisites", () => {
  render(<RepairScopeEditor plan={fixture(1000)} />);
  expect(screen.getAllByRole("button").length).toBeLessThan(50);
  fireEvent.change(screen.getByLabelText("Search planned files"), { target: { value: "file999" } });
  fireEvent.click(screen.getByRole("button", { name: /file999.py/ }));
  expect(screen.getByText("Plan for file999.py")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Step 1 · file0.py/ }));
  expect(screen.getByText("Plan for file0.py")).toBeTruthy();
});

it("maps many steps to one file without expanding long plan-wide text", () => {
  const data = fixture(17);
  data.steps.forEach((step) => {
    step.files = ["backend/auth.py"];
  });
  data.carriedForward = {
    targetFunction: null,
    acceptanceCriteria: ["Long acceptance text"],
    patchConstraints: ["Long constraint text"],
  };
  render(<RepairScopeEditor plan={data} />);
  expect(screen.getByLabelText("Planned step to file relationships")).toBeTruthy();
  expect(screen.queryByText("Long acceptance text")).toBeNull();
  expect(screen.queryByText("Long constraint text")).toBeNull();
  expect(screen.getByText("Show 8 more steps")).toBeTruthy();
  fireEvent.click(screen.getByText("Show 8 more steps"));
  expect(screen.getByText("Show 1 more steps")).toBeTruthy();
});
