// @vitest-environment jsdom
/**
 * A5's Blast Radius Impact Ledger. Every assertion traces to a specific field
 * on `GET /api/runs/{runId}/blast` — the point of this suite is to catch the
 * panel inventing a direction, a "risk" claim stronger than the backend
 * supports, or collapsing "could be affected" and "reachable" into the same
 * label.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getBlastImpact = vi.fn();

vi.mock("@/lib/runService", () => ({
  getBlastImpact: (...a: unknown[]) => getBlastImpact(...a),
}));

const { BlastRadiusPanel } = await import("./BlastRadiusPanel");
import type { BlastImpact } from "./blastTypes";

const IMPACT: BlastImpact = {
  origin: {
    originalPath: "/clone/vulnapi/auth.py",
    normalizedPath: "vulnapi/auth.py",
    resolvedPath: "vulnapi/auth.py",
    source: "stack_trace",
    confidence: 0.9,
    runtimeConfirmed: true,
    pinned: true,
  },
  origins: ["vulnapi/auth.py"],
  scope: [
    {
      path: "vulnapi/auth.py",
      hopCount: 0,
      directions: ["forward"],
      reachedVia: null,
      edgeBasis: null,
      propagationConfidence: 1.0,
      priorityScore: 0.0,
      origin: "vulnapi/auth.py",
      role: "auth-boundary",
      criticality: 0.9,
      churnWeight: 0.0,
      autoPatchable: true,
      humanReviewRequired: true,
      hasStaticFinding: false,
    },
    {
      path: "vulnapi/middleware.py",
      hopCount: 1,
      directions: ["backward"],
      reachedVia: "vulnapi/auth.py",
      edgeBasis: "resolved_suffix",
      propagationConfidence: 0.42,
      priorityScore: 0.18,
      origin: "vulnapi/auth.py",
      role: "internal-util",
      criticality: 0.5,
      churnWeight: 0.0,
      autoPatchable: false,
      humanReviewRequired: true,
      hasStaticFinding: true,
    },
    {
      path: "vulnapi/routes.py",
      hopCount: 1,
      directions: ["forward"],
      reachedVia: "vulnapi/auth.py",
      edgeBasis: "name_contains",
      propagationConfidence: 0.35,
      priorityScore: 0.1,
      origin: "vulnapi/auth.py",
      role: "public-api",
      criticality: 0.6,
      churnWeight: 0.0,
      autoPatchable: false,
      humanReviewRequired: true,
      hasStaticFinding: false,
    },
    {
      path: "vulnapi/tests/test_routes.py",
      hopCount: 2,
      directions: ["forward"],
      reachedVia: "vulnapi/routes.py",
      edgeBasis: "resolved_suffix",
      propagationConfidence: 0.12,
      priorityScore: 0.0,
      origin: "vulnapi/auth.py",
      role: "test-only",
      criticality: 0.2,
      churnWeight: 0.0,
      autoPatchable: false,
      humanReviewRequired: true,
      hasStaticFinding: false,
    },
  ],
  edges: [
    {
      from: "vulnapi/auth.py",
      to: "vulnapi/middleware.py",
      direction: "backward",
      basis: "resolved_suffix",
      hopCount: 1,
    },
    {
      from: "vulnapi/auth.py",
      to: "vulnapi/routes.py",
      direction: "forward",
      basis: "name_contains",
      hopCount: 1,
    },
    {
      from: "vulnapi/routes.py",
      to: "vulnapi/tests/test_routes.py",
      direction: "forward",
      basis: "resolved_suffix",
      hopCount: 2,
    },
  ],
  maxHop: 2,
  autoPatchScope: ["vulnapi/auth.py"],
  humanReviewRequired: [
    "vulnapi/auth.py",
    "vulnapi/middleware.py",
    "vulnapi/routes.py",
    "vulnapi/tests/test_routes.py",
  ],
  patchAuthorityOverlap: ["vulnapi/auth.py"],
  riskMeasurementCaveat:
    "Priority combines file criticality, churn and role. Churn is 0 both when a file has no recent bug-fix commits and when the repository has no git history to measure from — this run cannot tell those apart, so a 0 here is a floor, not a measured absence of risk.",
  capabilities: [
    {
      name: "Authentication",
      slug: "authentication",
      confidence: 0.8,
      filesInScope: ["vulnapi/auth.py", "vulnapi/middleware.py"],
      totalFilesInCapability: 3,
      why: "filename and import evidence",
    },
    {
      name: "Unclassified",
      slug: "unclassified",
      confidence: null,
      filesInScope: ["vulnapi/tests/test_routes.py"],
      totalFilesInCapability: null,
      why: "No capability signal matched these files.",
    },
  ],
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

beforeEach(() => {
  getBlastImpact.mockReset();
});

describe("BlastRadiusPanel", () => {
  it("renders the change origin with its resolution provenance", async () => {
    getBlastImpact.mockResolvedValue(clone(IMPACT));
    render(<BlastRadiusPanel runId="run-1" />);

    const origin = await screen.findByRole("group", { name: /change origin/i });
    expect(within(origin).getByText("vulnapi/auth.py")).toBeTruthy();
    expect(within(origin).getByText(/resolved from the reproduced stack trace/)).toBeTruthy();
    expect(within(origin).getByText(/90%/)).toBeTruthy();
    expect(within(origin).getByText(/pinned into auto-patch scope/i)).toBeTruthy();
  });

  it("separates direct impact into upstream and downstream", async () => {
    getBlastImpact.mockResolvedValue(clone(IMPACT));
    render(<BlastRadiusPanel runId="run-1" />);

    const direct = await screen.findByRole("group", { name: /direct impact/i });
    expect(within(direct).getByText(/upstream/i)).toBeTruthy();
    expect(within(direct).getByText(/downstream/i)).toBeTruthy();
    expect(within(direct).getByText("vulnapi/middleware.py")).toBeTruthy();
    expect(within(direct).getByText("vulnapi/routes.py")).toBeTruthy();
    // hop-2 files must not appear in the direct section.
    expect(within(direct).queryByText("vulnapi/tests/test_routes.py")).toBeNull();
  });

  it("draws every recorded traversal edge, including origin to hop-1 edges", async () => {
    getBlastImpact.mockResolvedValue(clone(IMPACT));
    render(<BlastRadiusPanel runId="run-1" />);

    await screen.findByRole("group", { name: /change-centered impact graph/i });
    expect(screen.getByTestId("impact-traversal-edges").querySelectorAll("path")).toHaveLength(3);
  });

  it("keeps transitive impact separate and collapsed by default", async () => {
    getBlastImpact.mockResolvedValue(clone(IMPACT));
    render(<BlastRadiusPanel runId="run-1" />);

    const transitive = await screen.findByRole("group", { name: /transitive impact/i });
    expect(within(transitive).queryByText("vulnapi/tests/test_routes.py")).toBeNull();

    const toggle = within(transitive).getByRole("button");
    await userEvent.click(toggle);
    expect(within(transitive).getByText("vulnapi/tests/test_routes.py")).toBeTruthy();
  });

  it("flags a name-matched edge as a possible false edge, never as certain", async () => {
    getBlastImpact.mockResolvedValue(clone(IMPACT));
    render(<BlastRadiusPanel runId="run-1" />);

    const direct = await screen.findByRole("group", { name: /direct impact/i });
    expect(within(direct).getByText(/could be a false edge/i)).toBeTruthy();
  });

  it("never claims a file is definitely or guaranteed affected", async () => {
    getBlastImpact.mockResolvedValue(clone(IMPACT));
    const { container } = render(<BlastRadiusPanel runId="run-1" />);

    await screen.findByRole("group", { name: /change origin/i });
    // "could (potentially) be affected" and "reachable" are the sanctioned
    // hedged phrasing; only an unhedged certainty claim is banned.
    expect(container.textContent).not.toMatch(/definitely affected/i);
    expect(container.textContent).not.toMatch(/guaranteed(ly)? affected/i);
    expect(container.textContent).not.toMatch(/will be affected/i);
  });

  it("shows priority, never 'risk', with the backend's own caveat", async () => {
    getBlastImpact.mockResolvedValue(clone(IMPACT));
    render(<BlastRadiusPanel runId="run-1" />);

    await screen.findByText(/scope ledger/i);
    expect(screen.getByText(/priority combines file criticality/i)).toBeTruthy();
    expect(screen.queryByText(/^risk$/i)).toBeNull();
  });

  it("shows the capability rollup with a distinct Unclassified bucket", async () => {
    getBlastImpact.mockResolvedValue(clone(IMPACT));
    render(<BlastRadiusPanel runId="run-1" />);

    const areas = await screen.findByRole("group", { name: /potentially affected areas/i });
    expect(within(areas).getByText("Authentication")).toBeTruthy();
    expect(within(areas).getByText(/2 of 3 files/)).toBeTruthy();
    expect(within(areas).getByText("Unclassified")).toBeTruthy();
  });

  it("says capabilities are not measured when A0.5 did not run", async () => {
    const noCapabilities = clone(IMPACT);
    noCapabilities.capabilities = null;
    getBlastImpact.mockResolvedValue(noCapabilities);
    render(<BlastRadiusPanel runId="run-1" />);

    expect(await screen.findByText(/potentially affected areas — not measured/i)).toBeTruthy();
  });

  it("reports patch-authority overlap as a real fact, not an error", async () => {
    getBlastImpact.mockResolvedValue(clone(IMPACT));
    render(<BlastRadiusPanel runId="run-1" />);

    const authority = await screen.findByRole("group", { name: /patch authority/i });
    expect(within(authority).getByText(/in both lists at once/i)).toBeTruthy();
    expect(within(authority).getByText(/pinned into auto-patch scope/i)).toBeTruthy();
  });

  it("opens the inspector with real per-file detail on selection", async () => {
    getBlastImpact.mockResolvedValue(clone(IMPACT));
    render(<BlastRadiusPanel runId="run-1" />);

    const direct = await screen.findByRole("group", { name: /direct impact/i });
    await userEvent.click(within(direct).getByText("vulnapi/middleware.py"));

    const inspector = screen.getByRole("group", { name: /details for vulnapi\/middleware\.py/i });
    expect(within(inspector).getByText("vulnapi/auth.py")).toBeTruthy(); // reached via
    expect(within(inspector).getByText(/precise match/i)).toBeTruthy();
  });

  it("shows a readable propagation path for the selected focus file", async () => {
    getBlastImpact.mockResolvedValue(clone(IMPACT));
    render(<BlastRadiusPanel runId="run-1" />);

    const direct = await screen.findByRole("group", { name: /direct impact/i });
    await userEvent.click(within(direct).getByText("vulnapi/routes.py"));

    const readout = screen.getByRole("group", { name: /current propagation path/i });
    expect(within(readout).getByText(/reached in the downstream lane/i)).toBeTruthy();
    expect(within(readout).getByText("auth.py")).toBeTruthy();
    expect(within(readout).getByText("routes.py")).toBeTruthy();
  });

  it("filters the ledger to A3-flagged files only", async () => {
    getBlastImpact.mockResolvedValue(clone(IMPACT));
    render(<BlastRadiusPanel runId="run-1" />);

    await screen.findByText(/scope ledger \(4\)/i);
    await userEvent.click(screen.getByLabelText(/a3-flagged only/i));

    expect(await screen.findByText(/scope ledger \(1\)/i)).toBeTruthy();
  });

  it("renders an empty scope as a real answer, not pending", async () => {
    const empty: BlastImpact = { ...clone(IMPACT), scope: [], edges: [], maxHop: 0, origin: null };
    getBlastImpact.mockResolvedValue(empty);
    render(<BlastRadiusPanel runId="run-1" />);

    expect(await screen.findByText(/no origin — a5 ran but resolved no file/i)).toBeTruthy();
    expect(screen.queryByRole("group", { name: /direct impact/i })).toBeNull();
  });

  it("distinguishes A5 pending from A5 running", async () => {
    getBlastImpact.mockResolvedValue(null);
    const { unmount } = render(<BlastRadiusPanel runId="run-1" />);
    expect(await screen.findByText(/a5 has not completed/i)).toBeTruthy();
    unmount();

    getBlastImpact.mockResolvedValue(null);
    render(<BlastRadiusPanel runId="run-2" status="running" />);
    expect(await screen.findByText(/a5 is traversing/i)).toBeTruthy();
  });

  it("reports a load failure and retries on demand", async () => {
    getBlastImpact.mockRejectedValueOnce(new Error("API 500: Internal Server Error"));
    getBlastImpact.mockResolvedValueOnce(clone(IMPACT));
    render(<BlastRadiusPanel runId="run-1" />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByRole("group", { name: /change origin/i })).toBeTruthy();
  });
});
