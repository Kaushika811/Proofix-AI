// @vitest-environment jsdom
/**
 * A2's Dependency Risk Map panel. Every assertion here traces back to a
 * specific field on `GET /api/runs/{runId}/dependency-risk` — the point of
 * this suite is to catch the panel inventing an advisory count, a severity
 * band, or a reachability verdict the backend never sent.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getDependencyRisk = vi.fn();
const getKnowledgeMetrics = vi.fn();
const getSemanticGraph = vi.fn();

vi.mock("@/lib/runService", () => ({
  getDependencyRisk: (...a: unknown[]) => getDependencyRisk(...a),
  getKnowledgeMetrics: (...a: unknown[]) => getKnowledgeMetrics(...a),
  getSemanticGraph: (...a: unknown[]) => getSemanticGraph(...a),
}));

const { DependencyRiskPanel } = await import("./DependencyRiskPanel");

const REPORT = {
  manifest: "requirements.txt",
  ecosystem: "PyPI",
  totalDependencies: 3,
  advisoryCount: 2,
  reachableCount: 1,
  informationalCount: 1,
  unknownCount: 0,
  findings: [
    {
      package: "urllib3",
      cveId: "CVE-2023-45803",
      severity: "7.5",
      installedVersion: "1.26.5",
      affectedSymbol: null,
      reachable: true,
      reachPath: ["vulnapi/net.py"],
      classification: "Critical",
    },
    {
      package: "requests",
      cveId: "CVE-INFO-1",
      severity: "HIGH",
      installedVersion: "2.31.0",
      affectedSymbol: null,
      reachable: false,
      reachPath: null,
      classification: "Informational",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("DependencyRiskPanel", () => {
  it("shows the loading message before the backend responds", () => {
    getDependencyRisk.mockReturnValue(new Promise(() => {}));
    render(<DependencyRiskPanel runId="r1" />);
    expect(screen.getByText("Analyzing dependency graph…")).toBeTruthy();
  });

  it("renders Pending when A2 has not completed for this run", async () => {
    getDependencyRisk.mockResolvedValue(null);
    render(<DependencyRiskPanel runId="r1" />);
    expect(
      await screen.findByText(/A2 has not completed dependency analysis for this run yet/),
    ).toBeTruthy();
  });

  it("shows a distinct running message rather than the generic pending one", async () => {
    getDependencyRisk.mockResolvedValue(null);
    render(<DependencyRiskPanel runId="r1" status="running" />);
    expect(await screen.findByText(/RUNNING — A2 is querying advisories now/)).toBeTruthy();
  });

  it("surfaces a real failure with a retry", async () => {
    getDependencyRisk.mockRejectedValue(new Error("API 500: Internal Server Error"));
    render(<DependencyRiskPanel runId="r1" />);
    expect(await screen.findByText("API 500: Internal Server Error")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("reports 'unavailable' distinctly when A2 ran but found no manifest", async () => {
    getDependencyRisk.mockResolvedValue({
      manifest: null,
      ecosystem: null,
      totalDependencies: 0,
      advisoryCount: 0,
      reachableCount: 0,
      informationalCount: 0,
      unknownCount: 0,
      findings: [],
    });
    render(<DependencyRiskPanel runId="r1" />);
    expect(await screen.findByText(/Dependency analysis unavailable — no manifest/)).toBeTruthy();
    // Must never be conflated with "0 advisories found" — the wording proves it.
    expect(screen.queryByText(/0 advisories/)).toBeNull();
  });

  it("renders a real 'safe dependency set' rather than an invented empty graph", async () => {
    getDependencyRisk.mockResolvedValue({
      manifest: "requirements.txt",
      ecosystem: "PyPI",
      totalDependencies: 5,
      advisoryCount: 0,
      reachableCount: 0,
      informationalCount: 0,
      unknownCount: 0,
      findings: [],
    });
    render(<DependencyRiskPanel runId="r1" />);

    expect(await screen.findByText("SAFE DEPENDENCY SET")).toBeTruthy();
    expect(screen.getByText(/5 dependencies A2 analyzed/)).toBeTruthy();
    // No graph/table toggle is offered when there is nothing to visualize.
    expect(screen.queryByRole("button", { name: "Risk Map" })).toBeNull();
  });

  it("renders real counts and never invents them", async () => {
    getDependencyRisk.mockResolvedValue(REPORT);
    render(<DependencyRiskPanel runId="r1" />);

    await screen.findByText("Dependency Risk Map");
    const chip = screen.getByText("Dependencies analyzed").closest("div");
    expect(within(chip!.parentElement!).getByText("3")).toBeTruthy();
    expect(screen.getByText(/2 advisories detected/)).toBeTruthy();
    expect(screen.getByText(/1 reachable vulnerability/)).toBeTruthy();
  });

  it("renders the reachable finding with its real classification badge", async () => {
    getDependencyRisk.mockResolvedValue(REPORT);
    render(<DependencyRiskPanel runId="r1" />);

    await screen.findByText("Dependency Risk Map");
    expect(screen.getByText(/urllib3/)).toBeTruthy();
    expect(screen.getAllByText("REACHABLE").length).toBeGreaterThan(0);
    expect(screen.getAllByText("NOT REACHABLE").length).toBeGreaterThan(0);
  });

  it("selecting a finding opens the vulnerability detail with real fields and 'Not measured' for what A2 never computes", async () => {
    getDependencyRisk.mockResolvedValue(REPORT);
    const user = userEvent.setup();
    render(<DependencyRiskPanel runId="r1" />);

    await screen.findByText("Dependency Risk Map");
    await user.click(screen.getByRole("button", { name: /urllib3/ }));

    const detail = await screen.findByRole("complementary", { name: "Vulnerability detail" });
    expect(within(detail).getByText("1.26.5")).toBeTruthy();
    expect(within(detail).getByText("7.5")).toBeTruthy();
    expect(within(detail).getByText("Reachable")).toBeTruthy();
    expect(within(detail).getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(within(detail).getByText(/vulnapi\/net\.py/)).toBeTruthy();
  });

  it("shows the real code-impact list for a reachable finding", async () => {
    getDependencyRisk.mockResolvedValue(REPORT);
    const user = userEvent.setup();
    render(<DependencyRiskPanel runId="r1" />);

    await screen.findByText("Dependency Risk Map");
    await user.click(screen.getByRole("button", { name: /urllib3/ }));

    const detail = await screen.findByRole("complementary", { name: "Vulnerability detail" });
    expect(within(detail).getByText(/1 production file\(s\) import this package/)).toBeTruthy();
  });

  it("shows a real 'not reachable' explanation for the informational finding, never a fabricated path", async () => {
    getDependencyRisk.mockResolvedValue(REPORT);
    const user = userEvent.setup();
    render(<DependencyRiskPanel runId="r1" />);

    await screen.findByText("Dependency Risk Map");
    await user.click(screen.getByRole("button", { name: /requests/ }));

    const detail = await screen.findByRole("complementary", { name: "Vulnerability detail" });
    expect(
      within(detail).getByText(/No production file in the indexed source imports this package/),
    ).toBeTruthy();
  });

  it("severity renders exactly as OSV reported it — never rebucketed into HIGH/MEDIUM/LOW", async () => {
    getDependencyRisk.mockResolvedValue(REPORT);
    render(<DependencyRiskPanel runId="r1" />);

    const user = userEvent.setup();
    await screen.findByText("Dependency Risk Map");
    await user.click(screen.getByRole("button", { name: "Table" }));
    const table = await screen.findByRole("table");
    // Raw OSV values, unmodified: a numeric score and the literal word HIGH.
    expect(within(table).getByText("7.5")).toBeTruthy();
    expect(within(table).getByText("HIGH")).toBeTruthy();
  });

  it("filters by reachability classification", async () => {
    getDependencyRisk.mockResolvedValue(REPORT);
    const user = userEvent.setup();
    render(<DependencyRiskPanel runId="r1" />);

    await screen.findByText("Dependency Risk Map");
    await user.click(screen.getByRole("button", { name: "Table" }));
    const table = await screen.findByRole("table");
    within(table).getByText("urllib3");
    within(table).getByText("requests");

    await user.click(screen.getByRole("button", { name: "NOT REACHABLE" }));
    await waitFor(() => {
      expect(within(table).queryByText("requests")).toBeNull();
      expect(within(table).getByText("urllib3")).toBeTruthy();
    });
  });

  it("searches by package name and by advisory id", async () => {
    getDependencyRisk.mockResolvedValue(REPORT);
    const user = userEvent.setup();
    render(<DependencyRiskPanel runId="r1" />);

    await screen.findByText("Dependency Risk Map");
    await user.click(screen.getByRole("button", { name: "Table" }));
    const table = await screen.findByRole("table");

    await user.type(screen.getByPlaceholderText("Search package or advisory…"), "urllib3");
    await waitFor(() => {
      expect(within(table).queryByText("requests")).toBeNull();
      expect(within(table).getByText("urllib3")).toBeTruthy();
    });
  });

  it("the table view is a complete fallback with accessible headers", async () => {
    getDependencyRisk.mockResolvedValue(REPORT);
    const user = userEvent.setup();
    render(<DependencyRiskPanel runId="r1" />);

    await screen.findByText("Dependency Risk Map");
    await user.click(screen.getByRole("button", { name: "Table" }));

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Package")).toBeTruthy();
    expect(within(table).getByText("Reachability")).toBeTruthy();
    expect(within(table).getByText("urllib3")).toBeTruthy();
    expect(within(table).getByText("requests")).toBeTruthy();
  });

  it("never calls A0.5's or A1's endpoints — A2's dependency risk is its own", async () => {
    getDependencyRisk.mockResolvedValue(REPORT);
    render(<DependencyRiskPanel runId="r1" />);

    await screen.findByText("Dependency Risk Map");
    expect(getKnowledgeMetrics).not.toHaveBeenCalled();
    expect(getSemanticGraph).not.toHaveBeenCalled();
  });

  it("carries the explainability source line for the dependency-risk endpoint", async () => {
    getDependencyRisk.mockResolvedValue(REPORT);
    render(<DependencyRiskPanel runId="r1" />);

    await screen.findByText("Dependency Risk Map");
    expect(screen.getByText(/dependency-risk/)).toBeTruthy();
  });
});
