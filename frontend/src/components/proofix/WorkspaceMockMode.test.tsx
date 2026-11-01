// @vitest-environment jsdom
/**
 * The two data sources, and the wall between them.
 *
 * Mock mode is a real product mode — it is how the workspace demos with no
 * backend running — so it must still render. Live mode must never reach the
 * fixtures: that is the defect class this codebase has been removing (a
 * hardcoded evidence table drawing green ticks on runs where the agents never
 * executed). The strongest version of that check is a live run where *every*
 * fetch fails: if a fixture value can leak, it leaks there.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));

const isLive = vi.hoisted(() => ({ value: false }));
const rest = vi.hoisted(() => ({ fail: false }));

vi.mock("@/lib/runService", async () => {
  const reject = async () => {
    throw new Error("API 500: Internal Server Error");
  };
  const empty = async () => {
    throw new Error("unreachable in mock mode");
  };
  return {
    get isLive() {
      return isLive.value;
    },
    getRunAgents: () => (rest.fail ? reject() : empty()),
    getWorkspaceHeader: () => (rest.fail ? reject() : empty()),
    getExecutiveSummary: () => (rest.fail ? reject() : empty()),
    getRunReport: () => (rest.fail ? reject() : empty()),
    getRepairAttempts: () => (rest.fail ? reject() : empty()),
    // Null is the honest mock-mode answer: there is no context fixture, and
    // inventing a ranking would put fabricated privacy evidence on screen.
    getRunContext: async () => null,
    getRunPatch: async () => null,
    // Same reasoning for A1's semantic graph — there is no SIG fixture, and
    // inventing role classifications would put fabricated evidence on screen.
    getSemanticGraph: async () => null,
    // Same reasoning for A2's dependency risk report — there is no CVE
    // fixture, and inventing advisories would put fabricated evidence on screen.
    getDependencyRisk: async () => null,
    // Same reasoning for A3's static findings — there is no scanner-output
    // fixture, and inventing findings would put fabricated evidence on screen.
    getStaticFindings: async () => null,
    // Same reasoning for A3.5's reproduction evidence — there is no pytest
    // execution fixture, and inventing a verdict would put fabricated
    // evidence on screen.
    getReproductionEvidence: async () => null,
    listRepositories: async () => [],
    eventSourceFor: () => () => () => undefined,
    createRun: vi.fn(),
    askChat: vi.fn(),
  };
});

const { Workspace } = await import("./Workspace");
const { MOCK_WORKSPACE_HEADER } = await import("@/mocks");

afterEach(() => {
  isLive.value = false;
  rest.fail = false;
});

describe("mock mode", () => {
  it("renders the workspace from the bundled fixtures with no backend", async () => {
    isLive.value = false;

    render(<Workspace />);

    // The fixture repository is on screen — mock mode's entire purpose.
    expect(
      (await screen.findAllByText(new RegExp(MOCK_WORKSPACE_HEADER.repository))).length,
    ).toBeGreaterThan(0);
  });
});

describe("live mode fixture isolation", () => {
  it("shows no fixture data when every backend call fails", async () => {
    isLive.value = true;
    rest.fail = true;

    render(<Workspace runId="run-live" />);

    // The failure is reported...
    await waitFor(() => expect(screen.getAllByText(/Could not load/).length).toBeGreaterThan(0));

    // ...and nothing from the fixture set stands in for the missing data.
    expect(screen.queryByText(new RegExp(MOCK_WORKSPACE_HEADER.repository))).toBeNull();
    expect(screen.queryByText(/Draft PR/)).toBeNull();
  });
});
