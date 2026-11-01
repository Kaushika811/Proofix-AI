// @vitest-environment jsdom
/**
 * A3's Priority Lens panel. Every assertion here traces back to a specific
 * field on `GET /api/runs/{runId}/static-findings` — the point of this
 * suite is to catch the panel inventing a severity band, a scanner status,
 * a cluster count, or a reasoning claim the backend never supported.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getStaticFindings = vi.fn();
const getKnowledgeMetrics = vi.fn();
const getSemanticGraph = vi.fn();
const getDependencyRisk = vi.fn();

vi.mock("@/lib/runService", () => ({
  getStaticFindings: (...a: unknown[]) => getStaticFindings(...a),
  getKnowledgeMetrics: (...a: unknown[]) => getKnowledgeMetrics(...a),
  getSemanticGraph: (...a: unknown[]) => getSemanticGraph(...a),
  getDependencyRisk: (...a: unknown[]) => getDependencyRisk(...a),
}));

const { StaticFindingsPanel } = await import("./StaticFindingsPanel");

const REPORT = {
  scannerStatus: {
    bandit: "unavailable",
    semgrep: "unavailable",
    ruff: "ok",
  },
  rawCount: 2,
  prioritizedCount: 2,
  findings: [
    {
      id: "finding-0",
      rank: 1,
      file: "vulnapi/auth.py",
      line: 12,
      message: "pickle usage",
      tools: ["bandit", "ruff"],
      severity: 0.9,
      severityMeasured: true,
      consensus: true,
      blastRadiusScore: 0.62,
      criticality: 0.81,
      churnWeight: 0.37,
    },
    {
      id: "finding-1",
      rank: 2,
      file: "vulnapi/routes.py",
      line: 3,
      message: "`time` imported but unused",
      tools: ["ruff"],
      severity: 0.4,
      severityMeasured: false,
      consensus: false,
      blastRadiusScore: 0.184,
      criticality: 0.55,
      churnWeight: 0.1,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("StaticFindingsPanel", () => {
  it("shows the loading message before the backend responds", () => {
    getStaticFindings.mockReturnValue(new Promise(() => {}));
    render(<StaticFindingsPanel runId="r1" />);
    expect(screen.getByText("Static analysis loading…")).toBeTruthy();
  });

  it("renders Pending when A3 has not completed for this run (404)", async () => {
    getStaticFindings.mockResolvedValue(null);
    render(<StaticFindingsPanel runId="r1" />);
    expect(await screen.findByText(/Pending — A3 has not completed yet/)).toBeTruthy();
  });

  it("shows a distinct running message rather than the generic pending one", async () => {
    getStaticFindings.mockResolvedValue(null);
    render(<StaticFindingsPanel runId="r1" status="running" />);
    expect(await screen.findByText(/RUNNING — A3 is scanning now/)).toBeTruthy();
  });

  it("surfaces a real API failure with a retry — never disguised as a clean repository", async () => {
    getStaticFindings.mockRejectedValue(new Error("API 500: Internal Server Error"));
    render(<StaticFindingsPanel runId="r1" />);
    expect(await screen.findByText("API 500: Internal Server Error")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
    expect(screen.queryByText(/No findings require prioritization/)).toBeNull();
  });

  it("shows a calm empty state when scanners ran and genuinely found nothing", async () => {
    getStaticFindings.mockResolvedValue({
      scannerStatus: {
        bandit: "ok_no_findings",
        semgrep: "ok_no_findings",
        ruff: "ok_no_findings",
      },
      rawCount: 0,
      prioritizedCount: 0,
      findings: [],
    });
    render(<StaticFindingsPanel runId="r1" />);

    expect(await screen.findByText("No findings require prioritization.")).toBeTruthy();
    expect(await screen.findByText("No findings reported by the available scanners.")).toBeTruthy();
    expect(screen.queryByText(/Pending/)).toBeNull();
    // Never a safety claim.
    expect(screen.queryByText(/no security issues/i)).toBeNull();
    expect(screen.queryByText(/clean/i)).toBeNull();
  });

  it("shows a diagnostic warning — not a clean result — when no scanner ran at all", async () => {
    getStaticFindings.mockResolvedValue({
      scannerStatus: {
        bandit: "unavailable",
        semgrep: "unavailable",
        ruff: "unavailable",
      },
      rawCount: 0,
      prioritizedCount: 0,
      findings: [],
    });
    render(<StaticFindingsPanel runId="r1" />);

    expect(await screen.findByText("No findings require prioritization.")).toBeTruthy();
    expect(
      await screen.findByText(
        "Priority ranking could not be established because no scanner produced findings.",
      ),
    ).toBeTruthy();
    // Distinct from the "scanners ran, found nothing" wording.
    expect(screen.queryByText("No findings reported by the available scanners.")).toBeNull();
    expect(screen.getAllByText("unavailable").length).toBe(3);
  });

  it("renders every scanner's real status, including unavailable ones — never hidden", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    expect(screen.getAllByText("unavailable").length).toBe(2);
    expect(screen.getByText("ready")).toBeTruthy(); // ruff, ok
  });

  it("never derives scanner status from which tools appear on findings", async () => {
    // bandit contributes to a finding's tools, yet its own scanner status is
    // "unavailable" — the strip must show the backend's real status, not
    // infer "ran" from the tool name appearing on a finding.
    getStaticFindings.mockResolvedValue(REPORT);
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    const scannerStrip = screen.getByText("Scanners").parentElement as HTMLElement;
    expect(within(scannerStrip).getByText("bandit")).toBeTruthy();
    expect(within(scannerStrip).getAllByText("unavailable").length).toBe(2);
  });

  it("renders the raw and prioritized counts from the backend, and the dynamic summary sentence", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    expect(screen.getAllByText("2", { selector: "div" }).length).toBe(2); // raw + prioritized
    expect(
      screen.getByText(/2 findings distilled into 2 priorities — ranked by blast radius\./),
    ).toBeTruthy();
  });

  it("never fabricates a cluster count the backend does not emit", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    expect(screen.queryByText(/clustered/i)).toBeNull();
  });

  it("renders findings in the backend's own ranked order, never re-sorted", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    const rows = screen.getAllByText(/vulnapi\/(auth|routes)\.py:/);
    expect(rows[0].textContent).toContain("auth.py");
    expect(rows[1].textContent).toContain("routes.py");
  });

  it("shows severity as a real measured number for finding #1", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    expect(screen.getAllByText("0.90").length).toBeGreaterThan(0);
  });

  it("shows 'Not measured' rather than a fabricated severity — and never a HIGH/MEDIUM/LOW band", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(screen.queryByText("LOW")).toBeNull();
    expect(screen.queryByText("MEDIUM")).toBeNull();
    expect(screen.queryByText("HIGH")).toBeNull();
  });

  it("agreement is one dot per real tool, never a percentage or a fabricated scanner", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    // #1 has tools ["bandit", "ruff"] — 2 scanners.
    expect(screen.getByText("2 scanners")).toBeTruthy();
    // #2 has tools ["ruff"] — 1 scanner (singular).
    expect(screen.getByText("1 scanner")).toBeTruthy();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("clicking the #1 finding expands it in place and dims the other rows", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    const user = userEvent.setup();
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    const row1 = screen.getByRole("button", { name: /auth\.py/ });
    await user.click(row1);

    expect(row1.getAttribute("aria-expanded")).toBe("true");
    expect(await screen.findByText("Why this ranked #1")).toBeTruthy();
    // FILE / LINE / FINDING fields from the expansion.
    expect(screen.getByText("vulnapi/auth.py")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();

    // The other row is still present but dimmed via opacity, not removed.
    const row2 = screen.getByRole("button", { name: /routes\.py/ });
    expect(row2).toBeTruthy();
  });

  it("clicking an expanded finding again collapses it", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    const user = userEvent.setup();
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    const row1 = screen.getByRole("button", { name: /auth\.py/ });
    await user.click(row1);
    await screen.findByText("Why this ranked #1");
    await user.click(row1);

    expect(screen.queryByText("Why this ranked #1")).toBeNull();
  });

  it("only the #1 finding's expansion offers the A4 handoff", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    const user = userEvent.setup();
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    await user.click(screen.getByRole("button", { name: /routes\.py/ }));
    await screen.findByText("Why this ranked #2");
    expect(screen.queryByText(/Investigate with A4/)).toBeNull();

    await user.click(screen.getByRole("button", { name: /routes\.py/ }));
    await user.click(screen.getByRole("button", { name: /auth\.py/ }));
    await screen.findByText("Why this ranked #1");
    expect(screen.getByText(/Investigate with A4/)).toBeTruthy();
  });

  it("the panel-level handoff line always points at the real #1 finding", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    const handoff = screen.getByText(/Evidence Investigation examines/).closest("div")!;
    expect(within(handoff).getByText("vulnapi/auth.py:12")).toBeTruthy();
  });

  it("the score decomposition never recomputes blast_radius_score — it renders the backend's own value", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    const user = userEvent.setup();
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    await user.click(screen.getByRole("button", { name: /auth\.py/ }));

    expect(await screen.findByText("0.6200")).toBeTruthy();
  });

  it("the generated reasoning only cites factors that actually cross the high threshold", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    const user = userEvent.setup();
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    await user.click(screen.getByRole("button", { name: /auth\.py/ }));
    await screen.findByText("Why this ranked #1");

    // #1: severity 0.90 (measured, high), criticality 0.81 (high), churn
    // 0.37 (not high) — churn must not be cited.
    expect(screen.getByText("High measured severity")).toBeTruthy();
    expect(screen.getByText("High structural criticality")).toBeTruthy();
    expect(screen.queryByText("Active code churn")).toBeNull();
  });

  it("cites the honest 'not independently measured' reason instead of a fabricated severity claim", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    const user = userEvent.setup();
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    await user.click(screen.getByRole("button", { name: /routes\.py/ }));

    expect(
      await screen.findByText(
        "Severity not independently measured — ranked on agreement, criticality and churn",
      ),
    ).toBeTruthy();
    expect(screen.queryByText("High measured severity")).toBeNull();
  });

  it("never calls A0.5's, A1's, or A2's endpoints — A3's findings are its own", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    expect(getKnowledgeMetrics).not.toHaveBeenCalled();
    expect(getSemanticGraph).not.toHaveBeenCalled();
    expect(getDependencyRisk).not.toHaveBeenCalled();
  });

  it("carries the explainability source line for the static-findings endpoint", async () => {
    getStaticFindings.mockResolvedValue(REPORT);
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    expect(screen.getByText(/static-findings/)).toBeTruthy();
  });

  it("never renders more than the backend's own prioritized findings, however large rawCount is", async () => {
    const many = {
      scannerStatus: { bandit: "ok", semgrep: "ok", ruff: "ok" },
      rawCount: 15000,
      prioritizedCount: 8,
      findings: Array.from({ length: 8 }, (_, i) => ({
        id: `finding-${i}`,
        rank: i + 1,
        file: `pkg/mod_${i}.py`,
        line: i + 1,
        message: "issue",
        tools: ["bandit"],
        severity: 0.5,
        severityMeasured: true,
        consensus: false,
        blastRadiusScore: 0.5 - i * 0.01,
        criticality: 0.5,
        churnWeight: 0.5,
      })),
    };
    getStaticFindings.mockResolvedValue(many);
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    expect(screen.getByText("15000", { selector: "div" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /pkg\/mod_\d+\.py/ }).length).toBe(8);
  });

  it("only ranks #1 and #2 show the full equation by default — the rest stay compact until clicked", async () => {
    const many = {
      scannerStatus: { bandit: "ok", semgrep: "ok", ruff: "ok" },
      rawCount: 8,
      prioritizedCount: 8,
      findings: Array.from({ length: 8 }, (_, i) => ({
        id: `finding-${i}`,
        rank: i + 1,
        file: `pkg/mod_${i}.py`,
        line: i + 1,
        message: "issue",
        tools: ["bandit"],
        severity: 0.5,
        severityMeasured: true,
        consensus: false,
        blastRadiusScore: 0.5 - i * 0.01,
        criticality: 0.5,
        churnWeight: 0.5,
      })),
    };
    getStaticFindings.mockResolvedValue(many);
    const user = userEvent.setup();
    render(<StaticFindingsPanel runId="r1" />);

    await screen.findByText("Static Analysis");
    // The "Severity" factor label only renders inside an expanded equation —
    // by default that is exactly ranks #1 and #2.
    expect(screen.getAllByText("Severity").length).toBe(2);

    // Clicking a compact row (#3) expands its own equation too.
    await user.click(screen.getByRole("button", { name: /pkg\/mod_2\.py/ }));
    expect(screen.getAllByText("Severity").length).toBe(3);
  });
});
