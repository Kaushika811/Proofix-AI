// @vitest-environment jsdom
/**
 * A10's Mergeability Decision panel. Every assertion traces to a field on
 * `GET /api/runs/{runId}/decision` — the point of this suite is to catch the
 * panel inventing a merge probability, collapsing an unmeasured axis into a
 * zero, drawing a not-reached gate as passed, or presenting A8/A9's scores
 * as A10's own findings.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getMergeabilityDecision = vi.fn();

vi.mock("@/lib/runService", () => ({
  getMergeabilityDecision: (...a: unknown[]) => getMergeabilityDecision(...a),
}));

const { MergeabilityDecisionPanel } = await import("./MergeabilityDecisionPanel");
import type { HardGate, MergeabilityDecision } from "./mergeabilityTypes";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function tenGates(overrides: Partial<Record<string, Partial<HardGate>>> = {}): HardGate[] {
  const base: HardGate[] = [
    {
      code: "validation_exhausted",
      label: "Validation not exhausted",
      checked: true,
      passed: true,
      detail: null,
    },
    {
      code: "patch_retry_required",
      label: "Target test did not require another patch attempt",
      checked: true,
      passed: true,
      detail: null,
    },
    {
      code: "target_test_failed",
      label: "Target reproduced test passes after patch",
      checked: true,
      passed: true,
      detail: null,
    },
    {
      code: "regression_failed",
      label: "No new test regressions",
      checked: true,
      passed: true,
      detail: null,
    },
    {
      code: "security_rejected",
      label: "Security re-scan accepted the patch",
      checked: true,
      passed: true,
      detail: null,
    },
    {
      code: "phantoms_detected",
      label: "PR description matches the diff",
      checked: true,
      passed: true,
      detail: null,
    },
    {
      code: "correctness_low",
      label: "Correctness ≥ 80",
      checked: true,
      passed: true,
      detail: null,
    },
    { code: "security_low", label: "Security ≥ 80", checked: true, passed: true, detail: null },
    {
      code: "axes_measured",
      label: "All four axes measured",
      checked: true,
      passed: true,
      detail: null,
    },
    {
      code: "reproduction_confirmed",
      label: "Bug reproduction confirmed",
      checked: true,
      passed: true,
      detail: null,
    },
  ];
  return base.map((g) => ({ ...g, ...(overrides[g.code] ?? {}) }));
}

const AUTO_MERGE: MergeabilityDecision = {
  prType: "auto_mergeable",
  decisionLabel: "Auto Merge",
  reviewNote: null,
  trust: 0.95,
  axes: [
    {
      name: "correctness",
      label: "Correctness",
      value: 92,
      measured: true,
      lowThreshold: 80,
      meetsLowThreshold: true,
    },
    {
      name: "security",
      label: "Security",
      value: 100,
      measured: true,
      lowThreshold: 80,
      meetsLowThreshold: true,
      autoMergeThreshold: 90,
      meetsAutoMergeThreshold: true,
    },
    {
      name: "fidelity",
      label: "Fidelity",
      value: 100,
      measured: true,
      lowThreshold: 80,
      meetsLowThreshold: true,
    },
    {
      name: "scope_risk",
      label: "Scope Safety",
      value: 90,
      measured: true,
      lowThreshold: 80,
      meetsLowThreshold: true,
    },
  ],
  hardGates: tenGates(),
  routingModifiers: {
    hardGatesClear: true,
    citationReviewNeeded: false,
    reproductionConfidence: "exact_test",
    securityMeetsAutoMergeThreshold: true,
  },
  phantomChangesDetected: false,
  prUrl: "https://github.com/acme/vulnapi/pull/42",
  descriptionWhy: "Token expiry not checked",
  descriptionWhat: "Adds an expiry comparison in validate_token.",
  proofBundle: {
    bundleHash: "deadbeef1234",
    reproductionConfidence: "exact_test",
    steps: [
      {
        name: "reproduction_before",
        command: "pytest tests/test_auth.py::test_expired",
        baseCommit: "abc123",
        patchCommit: "",
        expectedResult: "fails",
        timeoutSeconds: 60,
        isTargeted: true,
      },
    ],
  },
  repositoryEvidence: {
    filesAnalyzed: 1284,
    changedFiles: ["vulnapi/auth.py"],
    affectedModules: ["vulnapi"],
    blastScopeFiles: ["vulnapi/auth.py", "vulnapi/middleware.py"],
    dependencyEdgeCount: 12,
  },
};

const DRAFT_UNMEASURED: MergeabilityDecision = {
  prType: "draft",
  decisionLabel: "Draft PR",
  reviewNote: "Not measured: security. Manual verification required before merge.",
  trust: 0.94,
  axes: [
    {
      name: "correctness",
      label: "Correctness",
      value: 92,
      measured: true,
      lowThreshold: 80,
      meetsLowThreshold: true,
    },
    {
      name: "security",
      label: "Security",
      value: null,
      measured: false,
      lowThreshold: 80,
      meetsLowThreshold: null,
      autoMergeThreshold: 90,
      meetsAutoMergeThreshold: null,
    },
    {
      name: "fidelity",
      label: "Fidelity",
      value: 100,
      measured: true,
      lowThreshold: 80,
      meetsLowThreshold: true,
    },
    {
      name: "scope_risk",
      label: "Scope Safety",
      value: 90,
      measured: true,
      lowThreshold: 80,
      meetsLowThreshold: true,
    },
  ],
  hardGates: tenGates({
    axes_measured: {
      passed: false,
      detail:
        "Not measured: security. The pipeline did not produce these scores, so merge readiness could not be established. Manual verification required before merge.",
    },
    reproduction_confirmed: { checked: false, passed: null },
  }),
  routingModifiers: {
    hardGatesClear: false,
    citationReviewNeeded: null,
    reproductionConfidence: null,
    securityMeetsAutoMergeThreshold: null,
  },
  phantomChangesDetected: false,
  prUrl: null,
  descriptionWhy: "",
  descriptionWhat: "",
  proofBundle: null,
  repositoryEvidence: null,
};

beforeEach(() => {
  getMergeabilityDecision.mockReset();
});

describe("MergeabilityDecisionPanel", () => {
  it("shows the real auto-mergeable verdict, with the real trust score behind detail", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(AUTO_MERGE));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    const band = await screen.findByRole("status");
    expect(within(band).getByText(/auto-mergeable/i)).toBeTruthy();

    // Trust is real but ranks below the routing decision — it lives behind
    // the optional-detail disclosure, not on the main face of the panel.
    expect(screen.queryByText("0.95")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /pr & proof-bundle detail/i }));
    expect(await screen.findByText("0.95")).toBeTruthy();
  });

  it("never turns an unmeasured axis into a zero", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(DRAFT_UNMEASURED));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    expect(screen.getByText("not measured")).toBeTruthy();
    expect(screen.queryByText(/^0$/)).toBeNull();
  });

  it("shows the security asymmetry: clears the low bar but not the stricter auto-merge bar", async () => {
    const asymmetric = clone(AUTO_MERGE);
    asymmetric.prType = "diff_only";
    asymmetric.axes[1] = {
      ...asymmetric.axes[1],
      value: 85,
      meetsAutoMergeThreshold: false,
    };
    getMergeabilityDecision.mockResolvedValue(asymmetric);
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    // Several "Security" labels exist now (the circuit's gate captions, the
    // axis card) — anchor on the axis meter's own accessible name instead.
    const securityRow = screen
      .getByLabelText("Security 85, threshold 80")
      .closest("div")!.parentElement!;
    expect(within(securityRow).getByText(/≥ 90 for auto-merge ✕/)).toBeTruthy();
    expect(within(securityRow).getByText(/≥ 80 ✓/)).toBeTruthy();
  });

  it("renders a not-evaluated gate as its own third state, not as passed or failed", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(DRAFT_UNMEASURED));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    // The tenth gate was never reached; the circuit must say so in its own
    // words rather than folding it into pass or fail. The wide rail and the
    // narrow ladder both render every gate (CSS decides which is visible),
    // so this can match either or both.
    expect(
      screen.getAllByLabelText(/gate 10: bug reproduction confirmed — not evaluated/i).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText(/not evaluated/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/not measured: security/i).length).toBeGreaterThan(0);
  });

  it("states how many gates were never evaluated behind the blocker", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(DRAFT_UNMEASURED));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    expect(screen.getByText(/1 never evaluated/i)).toBeTruthy();
    // Concise, decision-oriented copy — states the fact, never claims a
    // later gate passes or fails.
    expect(
      screen.getByText(/never evaluated — not claimed to pass, not claimed to fail/i),
    ).toBeTruthy();
  });

  it("shows the two outlets this run did not take alongside the one it did, greyed rather than hidden", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(DRAFT_UNMEASURED));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    const outcomes = screen.getByRole("list", { name: /routing outcomes/i });
    expect(within(outcomes).getByText(/— taken/)).toBeTruthy();
    expect(within(outcomes).getByText("Auto-Mergeable")).toBeTruthy();
    expect(within(outcomes).getByText("Diff Only")).toBeTruthy();
    expect(within(outcomes).getByText(/draft pr/i)).toBeTruthy();
  });

  it("headlines the blocking gate by topic, never by echoing its passing-condition label", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(DRAFT_UNMEASURED));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    // The real gate label is "All four axes measured" — echoing it next to
    // "BLOCKED HERE" would say the opposite of what happened. The headline
    // must name the topic instead and never contain that literal sentence.
    // Anchored: the circuit's own hover tooltips also mention gate 9's topic
    // and state (on a separate line, followed by "Required: …"), so an
    // unanchored match would hit those too. The headline div's normalized
    // text is exactly this string and nothing more.
    expect(screen.getByText(/^gate 9 · axes measurement — blocked here$/i)).toBeTruthy();
    expect(screen.queryByText(/all four axes measured — blocked here/i)).toBeNull();

    // Dynamic, grounded in the real axes: 3 of 4 measured, security is the
    // one that isn't.
    expect(screen.getByText(/3 \/ 4 axes measured/i)).toBeTruthy();
    expect(screen.getByText(/security unavailable/i)).toBeTruthy();
    expect(screen.getByText(/1 known blocker · 1 later gate unverified/i)).toBeTruthy();
  });

  it("connects the blocker to the specific axis evidence responsible for it", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(DRAFT_UNMEASURED));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    expect(screen.getByText("GATE 9 ✕")).toBeTruthy();
    expect(screen.getByText(/Correctness —/)).toBeTruthy();
    expect(screen.getByText(/92 \(meets threshold\)/)).toBeTruthy();
    expect(screen.getByText(/Security — NOT MEASURED/)).toBeTruthy();
  });

  it("gives a next action that names the actual missing evidence, not a generic instruction", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(DRAFT_UNMEASURED));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    expect(screen.getByText(/next action/i)).toBeTruthy();
    expect(
      screen.getByText(/run the tools that produce the security score, then re-run a10/i),
    ).toBeTruthy();
  });

  it("renders both the wide rail and the narrow two-row grid so the panel adapts without a JS breakpoint", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(DRAFT_UNMEASURED));
    const { container } = render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    // The wide rail: an SVG with one accessible node per gate.
    expect(container.querySelector("svg[role='img']")).toBeTruthy();
    // The narrow fallback: two rows of five gates, `@sm:hidden` at render
    // time — CSS switches between them, so both exist for a container-query
    // engine to pick from regardless of jsdom's lack of layout. Gate order
    // is preserved: row one is gates 1-5, row two is gates 6-10.
    const grid = container.querySelector("div.\\@sm\\:hidden");
    expect(grid).toBeTruthy();
    const gridButtons = within(grid as HTMLElement).getAllByRole("button");
    expect(gridButtons.length).toBe(10);
    expect(gridButtons[0].getAttribute("aria-label")).toMatch(/^Gate 1:/);
    expect(gridButtons[5].getAttribute("aria-label")).toMatch(/^Gate 6:/);
  });

  it("shows repository evidence as aggregate chips, never a per-file list, until expanded", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(AUTO_MERGE));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    expect(screen.getByText(/1,284 files analyzed/)).toBeTruthy();
    expect(screen.getByText(/1 changed/)).toBeTruthy();
    expect(screen.getByText(/1 module affected/)).toBeTruthy();
    expect(screen.getByText(/12 dependency paths/)).toBeTruthy();

    // Nothing per-file until the reviewer opens it.
    expect(screen.queryByText("vulnapi/")).toBeNull();
    expect(screen.queryByText(/auth\.py/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /view affected files/i }));

    // Grouped by module, filename shown relative to its module.
    expect(await screen.findByText("vulnapi/")).toBeTruthy();
    expect(screen.getByText(/auth\.py/)).toBeTruthy();
  });

  it("filters affected files by search, and caps how many modules render at once", async () => {
    const manyModules = clone(AUTO_MERGE);
    const changed = Array.from({ length: 25 }, (_, i) => `pkg${i}/module.py`);
    manyModules.repositoryEvidence = {
      filesAnalyzed: 50000,
      changedFiles: changed,
      affectedModules: changed.map((f) => f.split("/")[0]),
      blastScopeFiles: [],
      dependencyEdgeCount: 40,
    };
    getMergeabilityDecision.mockResolvedValue(manyModules);
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    await userEvent.click(screen.getByRole("button", { name: /view affected files/i }));

    // 25 modules exist; only a bounded page renders up front (alphabetical
    // sort puts "pkg9" last), with a "show more" control naming how many are
    // left — never all 25 at once.
    expect(await screen.findByText("pkg0/")).toBeTruthy();
    expect(screen.queryByText("pkg9/")).toBeNull();
    expect(screen.getByRole("button", { name: /show \d+ more modules/i })).toBeTruthy();

    // Search filters the real file set client-side.
    const search = screen.getByPlaceholderText(/filter files or modules/i);
    await userEvent.type(search, "pkg9");
    expect(await screen.findByText("pkg9/")).toBeTruthy();
    expect(screen.queryByText("pkg0/")).toBeNull();
  });

  it("shows the proof bundle only behind its own repository-evidence disclosure", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(AUTO_MERGE));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    expect(screen.queryByText(/pytest tests\/test_auth\.py::test_expired/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /view proof bundle/i }));
    expect(await screen.findByText(/pytest tests\/test_auth\.py::test_expired/)).toBeTruthy();
  });

  it("says a count was not measured rather than fabricating a zero when an agent never ran", async () => {
    const noEvidence = clone(AUTO_MERGE);
    noEvidence.repositoryEvidence = null;
    getMergeabilityDecision.mockResolvedValue(noEvidence);
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    expect(screen.getByText(/no repository evidence yet/i)).toBeTruthy();
  });

  it("offers no next action when every gate cleared", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(AUTO_MERGE));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    expect(screen.queryByText(/next action/i)).toBeNull();
    expect(screen.getByText(/all 10 cleared/i)).toBeTruthy();
  });

  it("shows routing modifiers only once every hard gate clears", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(DRAFT_UNMEASURED));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    await userEvent.click(screen.getByRole("button", { name: /pr & proof-bundle detail/i }));

    await screen.findByText(/routing modifiers/i);
    expect(screen.getByText(/not applicable/i)).toBeTruthy();
    expect(screen.queryByText(/citation review needed/i)).toBeNull();
  });

  it("shows routing modifiers as real facts when hard gates are clear", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(AUTO_MERGE));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    await userEvent.click(screen.getByRole("button", { name: /pr & proof-bundle detail/i }));

    await screen.findByText(/citation review needed/i);
    expect(screen.getByText(/reproduction confidence/i)).toBeTruthy();
    expect(screen.getByText("exact_test")).toBeTruthy();
  });

  it("exposes the PR link only behind progressive disclosure", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(AUTO_MERGE));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    expect(screen.queryByText(/github\.com\/acme\/vulnapi/i)).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /pr & proof-bundle detail/i }));
    expect(await screen.findByText(/github\.com\/acme\/vulnapi/i)).toBeTruthy();
  });

  it("says a PR was not published rather than inventing a link", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(DRAFT_UNMEASURED));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    const toggle = await screen.findByRole("button", { name: /pr & proof-bundle detail/i });
    await userEvent.click(toggle);
    expect(await screen.findByText(/not published/i)).toBeTruthy();
  });

  it("never presents a merge probability, only the real trust mean", async () => {
    getMergeabilityDecision.mockResolvedValue(clone(AUTO_MERGE));
    const { container } = render(<MergeabilityDecisionPanel runId="run-1" />);

    await screen.findByRole("status");
    expect(container.textContent).not.toMatch(/probability/i);
    expect(container.textContent).not.toMatch(/confidence score/i);

    await userEvent.click(screen.getByRole("button", { name: /pr & proof-bundle detail/i }));
    expect(await screen.findByText(/not itself the routing gate/i)).toBeTruthy();
  });

  it("distinguishes A10 pending from A10 running", async () => {
    getMergeabilityDecision.mockResolvedValue(null);
    const { unmount } = render(<MergeabilityDecisionPanel runId="run-1" />);
    expect(await screen.findByText(/mergeability decision pending/i)).toBeTruthy();
    unmount();

    getMergeabilityDecision.mockResolvedValue(null);
    render(<MergeabilityDecisionPanel runId="run-2" status="running" />);
    expect(await screen.findByText(/a10 is scoring/i)).toBeTruthy();
  });

  it("reports a load failure and retries on demand", async () => {
    getMergeabilityDecision.mockRejectedValueOnce(new Error("API 500: Internal Server Error"));
    getMergeabilityDecision.mockResolvedValueOnce(clone(AUTO_MERGE));
    render(<MergeabilityDecisionPanel runId="run-1" />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));

    const band = await screen.findByRole("status");
    expect(within(band).getByText(/auto-mergeable/i)).toBeTruthy();
  });
});
