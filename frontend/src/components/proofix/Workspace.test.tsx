// @vitest-environment jsdom
/**
 * Workspace rendering against the three terminal states, and against a backend
 * that is failing.
 *
 * The bugs these pin:
 *
 * - **B-F01** — every REST rejection was swallowed. A failed `/report` and a run
 *   that produced no report rendered identically: the empty model, silently. The
 *   user could not tell missing data from broken data, and once the run settled
 *   polling stopped, so there was no way back either. A failure must now name
 *   itself and offer a retry.
 * - **B-F02** — `RunReport` defaulted its `report` prop to `MOCK_RUN_REPORT`, so
 *   omitting it rendered another repository's trust scores as this run's.
 * - **B-F05** — `blocked` was not an `AgentStatus`, so a blocked run wore the
 *   draft badge: "a PR is waiting for review", about a run that produced no PR.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { AgentEntry } from "./data";
import type { PatchFileProvenance } from "./visualizationTypes";

/**
 * Matches an element whose full (recursive) text equals `text`. `DiffView`
 * colours tokens in separate `<span>`s, so RTL's default text query — which
 * reads only a node's direct text-node children, not descendant text — never
 * sees a highlighted line as one string; this reads `element.textContent`
 * directly instead of the (empty) `content` argument RTL passes in.
 */
function diffLine(text: string) {
  return (_content: string, element: Element | null) => element?.textContent?.trim() === text;
}

const navigate = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}));

// The journal's animation choreography measures real layout; jsdom has none.
// The stream itself is covered by `liveEventStream.test.ts`.
vi.mock("@/components/proofix/AnalyzingSequence", () => ({
  AnalyzingSequence: ({ onComplete }: { onComplete: () => void }) => (
    <button onClick={onComplete}>Finish launch animation</button>
  ),
}));

const getRunAgents = vi.fn();
const getWorkspaceHeader = vi.fn();
const getExecutiveSummary = vi.fn();
const getRunReport = vi.fn();
const getRepairAttempts = vi.fn();
const getRunContext = vi.fn();
const getRunPatch = vi.fn();
const listRepositories = vi.fn();
const createRun = vi.fn();

vi.mock("@/lib/runService", () => ({
  isLive: true,
  getRunAgents: (...a: unknown[]) => getRunAgents(...a),
  getWorkspaceHeader: (...a: unknown[]) => getWorkspaceHeader(...a),
  getExecutiveSummary: (...a: unknown[]) => getExecutiveSummary(...a),
  getRunReport: (...a: unknown[]) => getRunReport(...a),
  getRepairAttempts: (...a: unknown[]) => getRepairAttempts(...a),
  getRunContext: (...a: unknown[]) => getRunContext(...a),
  getRunPatch: (...a: unknown[]) => getRunPatch(...a),
  listRepositories: (...a: unknown[]) => listRepositories(...a),
  // No event source: the journal is driven by the agent list alone here.
  eventSourceFor: () => () => () => undefined,
  createRun: (...a: unknown[]) => createRun(...a),
  parseGithubUrl: () => null,
  validateRepository: vi.fn(),
  askChat: vi.fn(),
  // The "merge" agent id below mounts `MergeabilityDecisionPanel`, which
  // calls this directly — unmocked it isn't `undefined`, it doesn't exist on
  // this factory's object at all, so the panel's plain `.then()` call throws.
  // `null` is the panel's real "A10 has not completed" state.
  getMergeabilityDecision: vi.fn().mockResolvedValue(null),
}));

const { Workspace } = await import("./Workspace");
const { EMPTY_EXECUTIVE_SUMMARY, EMPTY_RUN_REPORT, EMPTY_REPAIR_ATTEMPTS } =
  await import("./emptyModels");

function agent(id: string, name: string): AgentEntry {
  return {
    id,
    index: 1,
    agent: name,
    purpose: "",
    handoff: "",
    status: "completed",
    duration: "1s",
    lines: ["line"],
    evidence: { title: "", subtitle: "", fields: [] },
  };
}

const AGENTS = [agent("environment", "Environment Precheck"), agent("merge", "Mergeability")];

function header(status: string, lifecycle: unknown[] = []) {
  return {
    repository: "vulnapi",
    branch: "main",
    status,
    lifecycle,
    shortRunId: "abc1234",
    retries: 0,
    executionTime: "12s",
    decisionLabel: status === "blocked" ? "Environment not prepared" : "Completed",
    environment: null,
  };
}

/** Every model resolves; the caller varies one of them. */
function backendReturns(over: Record<string, unknown> = {}) {
  getRunAgents.mockResolvedValue(over.agents ?? AGENTS);
  getWorkspaceHeader.mockResolvedValue(over.header ?? header("completed"));
  getExecutiveSummary.mockResolvedValue(over.summary ?? EMPTY_EXECUTIVE_SUMMARY);
  getRunReport.mockResolvedValue(over.report ?? EMPTY_RUN_REPORT);
  getRepairAttempts.mockResolvedValue(over.attempts ?? EMPTY_REPAIR_ATTEMPTS);
  // `null` is the 404 case: A5.5 has not published a package. `runService`
  // absorbs that status so absence never arrives as a rejection.
  getRunContext.mockResolvedValue(over.context ?? null);
  getRunPatch.mockResolvedValue(over.patch ?? null);
  listRepositories.mockResolvedValue([]);
}

beforeEach(() => {
  vi.clearAllMocks();
  createRun.mockReset();
});

describe("Workspace run creation", () => {
  it("shows the API failure, retains the target, and retries with a real run ID", async () => {
    backendReturns();
    createRun.mockRejectedValueOnce(new Error("API 503: Service Unavailable"))
      .mockRejectedValueOnce(new Error("API 503: Service Unavailable"))
      .mockResolvedValueOnce("accepted-run-id");
    render(<Workspace />);
    fireEvent.change(await screen.findByPlaceholderText(/local path/i), {
      target: { value: "/demo-repo" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Launch Autonomous Analysis/i }));
    expect((await screen.findByRole("alert")).textContent).toContain("API 503");
    expect(navigate).not.toHaveBeenCalled();
    expect((screen.getByPlaceholderText(/local path/i) as HTMLInputElement).value).toBe("/demo-repo");

    fireEvent.click(screen.getByRole("button", { name: /Launch Autonomous Analysis/i }));
    await waitFor(() => expect(createRun).toHaveBeenCalledTimes(2));
    await screen.findByRole("button", { name: /Launch Autonomous Analysis/i });
    expect(navigate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Launch Autonomous Analysis/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Finish launch animation" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith({
      to: "/runs/$runId", params: { runId: "accepted-run-id" },
    }));
    expect(createRun).toHaveBeenCalledTimes(3);
  });

  it("rejects an empty backend run ID instead of inventing one", async () => {
    backendReturns();
    createRun.mockResolvedValueOnce("");
    render(<Workspace />);
    fireEvent.change(await screen.findByPlaceholderText(/local path/i), {
      target: { value: "/demo-repo" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Launch Autonomous Analysis/i }));
    expect((await screen.findByRole("alert")).textContent).toContain("did not return a run ID");
    expect(navigate).not.toHaveBeenCalled();
  });
});

describe("Workspace home navigation", () => {
  it.each([false, true])("opens the landing page with collapsed sidebar = %s", async (collapsed) => {
    backendReturns();
    render(<Workspace runId="run-home-navigation" />);
    if (collapsed) {
      fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    }

    fireEvent.click(screen.getByRole("button", { name: "Home" }));

    expect(navigate).toHaveBeenCalledWith({ to: "/" });
    await waitFor(() => expect(listRepositories).toHaveBeenCalled());
    localStorage.removeItem("proofix-sidebar-collapsed");
  });
});

describe("Workspace terminal states", () => {
  it.each(["blocked", "failed"] as const)(
    "scrolls a %s run to its repair summary and then reveals its run report",
    async (status) => {
      const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
      backendReturns({
        header: header(status, [{ type: `run.${status}`, reason: "Repair could not continue" }]),
        summary: { ...EMPTY_EXECUTIVE_SUMMARY, decision: status },
        report: { ...EMPTY_RUN_REPORT, decision: status },
      });
      render(<Workspace runId={`run-summary-${status}`} />);
      await waitFor(() => {
        expect(
          scroll.mock.contexts.some((element) =>
            (element as HTMLElement).querySelector('[aria-label="Repair summary"]'),
          ),
        ).toBe(true);
      });
      expect(await screen.findByText("Run Report", {}, { timeout: 2_000 })).toBeTruthy();
      scroll.mockRestore();
    },
  );

  it("renders a blocked run as Blocked, not as a draft PR", async () => {
    backendReturns({
      header: header("blocked", [{ type: "run.blocked", reason: "pytest is not importable" }]),
      // A real blocked run's summary says so. The empty model's `"draft"`
      // default is a placeholder, not something the backend ever sends.
      summary: { ...EMPTY_EXECUTIVE_SUMMARY, decision: "blocked" },
    });

    render(<Workspace runId="run-blocked" />);

    expect(await screen.findByText(/Status · Blocked/)).toBeTruthy();
    // The draft badge's wording must not appear anywhere: a blocked run has no PR.
    expect(screen.queryByText(/Draft PR/)).toBeNull();
  });

  it("renders the backend's specific block reason, not a generic fallback (B-B19)", async () => {
    // "Unsupported repository" and "No dependency manifest" used to collapse
    // onto the same "Environment not prepared" label everywhere the decision
    // chip is rendered, contradicting the reason text printed beneath it.
    backendReturns({
      header: {
        ...header("blocked", [{ type: "run.blocked", reason: "This is a node project." }]),
        decisionLabel: "Unsupported repository",
      },
      summary: { ...EMPTY_EXECUTIVE_SUMMARY, decision: "blocked" },
    });

    render(<Workspace runId="run-blocked-unsupported" />);

    expect(await screen.findByText("Unsupported repository")).toBeTruthy();
    expect(screen.queryByText("Environment not prepared")).toBeNull();
  });

  it("renders a failed run as Failed", async () => {
    backendReturns({ header: header("failed", [{ type: "run.failed", reason: "boom" }]) });

    render(<Workspace runId="run-failed" />);

    expect(await screen.findByText(/Status · Failed/)).toBeTruthy();
  });

  it("renders a completed run as Completed", async () => {
    backendReturns({ header: header("completed", [{ type: "run.completed" }]) });

    render(<Workspace runId="run-done" />);

    expect(await screen.findByText(/Status · Completed/)).toBeTruthy();
  });

  it("shows the backend's real last agent, not the run state, in the Current Agent tile (B-B20)", async () => {
    // "Completed" is a run/state word, not an agent identity — showing it in
    // the "Current Agent" tile was the exact contradiction this pass targets.
    backendReturns({
      header: {
        ...header("completed", [{ type: "run.completed" }]),
        currentAgent: "A10",
      },
    });

    render(<Workspace runId="run-current-agent" />);

    await screen.findByText(/Status · Completed/);
    expect(await screen.findByText("A10")).toBeTruthy();
  });

  it("shows A0.7 as the Current Agent for a blocked run", async () => {
    backendReturns({
      header: {
        ...header("blocked", [{ type: "run.blocked", reason: "pytest is not importable" }]),
        currentAgent: "A0.7",
      },
      summary: { ...EMPTY_EXECUTIVE_SUMMARY, decision: "blocked" },
    });

    render(<Workspace runId="run-blocked-agent" />);

    await screen.findByText(/Status · Blocked/);
    expect(await screen.findByText("A0.7")).toBeTruthy();
  });
});

describe("Workspace REST failures", () => {
  it("surfaces a failed model instead of rendering it as empty, with a retry", async () => {
    backendReturns();
    getExecutiveSummary.mockRejectedValue(new Error("API 500: Internal Server Error"));

    render(<Workspace runId="run-err" />);

    expect(await screen.findByText(/Could not load executive summary/)).toBeTruthy();
    expect(screen.getByText(/API 500/)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Retry" }).length).toBeGreaterThan(0);
  });

  it("re-fetches when the retry control is used", async () => {
    backendReturns();
    getExecutiveSummary.mockRejectedValueOnce(new Error("API 503: Service Unavailable"));

    render(<Workspace runId="run-retry" />);

    const button = await screen.findByRole("button", { name: "Retry" });
    const before = getExecutiveSummary.mock.calls.length;
    button.click();

    await waitFor(() => expect(getExecutiveSummary.mock.calls.length).toBeGreaterThan(before));
    await waitFor(() => expect(screen.queryByText(/Could not load/)).toBeNull());
  });

  it("one failing model does not blank the others", async () => {
    backendReturns({ header: header("completed") });
    getRunReport.mockRejectedValue(new Error("API 500: Internal Server Error"));

    render(<Workspace runId="run-partial" />);

    // The header came back fine and still renders.
    expect(await screen.findByText(/Status · Completed/)).toBeTruthy();
    expect(screen.getAllByText(/vulnapi/).length).toBeGreaterThan(0);
  });
});

describe("Repository identity", () => {
  it("renders the identity the backend has always published", async () => {
    backendReturns({
      header: {
        ...header("completed"),
        repositoryId: "repo-abc123def456",
        headSha: "9c2d1f4a8b7e6d5c",
        repositoryHash: "idx-77aa88bb99cc",
      },
    });

    render(<Workspace runId="run-identity" />);

    expect(await screen.findByText("Repository ID")).toBeTruthy();
    // Abbreviated for the strip; the full value is the element's title.
    expect(screen.getByText("repo-abc123d")).toBeTruthy();
    expect(screen.getByText("9c2d1f4")).toBeTruthy();
    expect(screen.getByTitle("9c2d1f4a8b7e6d5c")).toBeTruthy();
  });

  it("omits a commit the run never observed rather than dashing it", async () => {
    backendReturns({
      header: { ...header("completed"), repositoryId: "repo-abc123", headSha: null },
    });

    render(<Workspace runId="run-no-head" />);

    expect(await screen.findByText("Repository ID")).toBeTruthy();
    expect(screen.queryByText("HEAD")).toBeNull();
  });

  it("renders no identity strip when the backend knows nothing", async () => {
    backendReturns({ header: header("completed") });

    render(<Workspace runId="run-bare" />);

    await screen.findByText(/Status · Completed/);
    expect(screen.queryByText("Repository ID")).toBeNull();
    expect(screen.queryByText("Index hash")).toBeNull();
  });
});

describe("Patch panel", () => {
  const PATCH_AGENTS = [agent("patch", "Patch Generator"), agent("merge", "Mergeability")];

  const BUNDLE = {
    issue_id: "fix-0",
    style_exemplar_commit: "abc1234",
    patches: [
      {
        file: "pkg/auth.py",
        original: "def validate(token):\n    return True\n",
        patched: "def validate(token):\n    return False\n",
        method: "ast_validated_write",
      },
    ],
    contracts: [{ assertion: "expired tokens rejected", location: "pkg/auth.py" }],
    diff_text:
      "--- a/pkg/auth.py\n+++ b/pkg/auth.py\n@@ -1,2 +1,2 @@\n def validate(token):\n-    return True\n+    return False\n",
  };

  it("renders the diff, not just the filenames", async () => {
    backendReturns({ agents: PATCH_AGENTS, patch: BUNDLE });

    render(<Workspace runId="run-patch" />);

    // Testing Library trims text on both sides, so the indentation the diff
    // preserves is not part of the query.
    expect(await screen.findByText(diffLine("return False"))).toBeTruthy();
    expect(screen.getByText(diffLine("return True"))).toBeTruthy();
    // Anchored: the summary spans hold exactly "+1" and "−1", while their
    // parent holds "+1 −1 across 1 file" and must not also match.
    expect(screen.getByText(/^\+1$/)).toBeTruthy();
    expect(screen.getByText(/^−1$/)).toBeTruthy();
  });

  it("shows AST validation only when the real method earns it, from the bundle itself", async () => {
    backendReturns({ agents: PATCH_AGENTS, patch: BUNDLE });

    render(<Workspace runId="run-patch-method" />);

    // No per-file provenance event exists for this fixture, so the badge
    // must come from `PatchCandidate.method` directly, not from
    // `a7_patch_metrics` — proving the two sources are independent.
    expect(await screen.findByText("AST validated ✓")).toBeTruthy();
  });

  it("distinguishes 'produced no bundle' from 'produced no change'", async () => {
    // 404 → null: generation never completed.
    backendReturns({ agents: PATCH_AGENTS, patch: null });
    const pending = render(<Workspace runId="run-patch-pending" />);
    expect(await screen.findByText(/Pending — patch generation/)).toBeTruthy();
    pending.unmount();

    // A bundle with no patches: generation completed and changed nothing.
    backendReturns({ agents: PATCH_AGENTS, patch: { ...BUNDLE, patches: [], diff_text: "" } });
    render(<Workspace runId="run-patch-empty" />);
    expect(await screen.findByText(/completed and produced no change/)).toBeTruthy();
    expect(screen.queryByText(/Pending — patch generation/)).toBeNull();
  });

  it("surfaces a real failure with a retry", async () => {
    backendReturns({ agents: PATCH_AGENTS });
    getRunPatch.mockRejectedValue(new Error("API 500: Internal Server Error"));

    render(<Workspace runId="run-patch-err" />);

    expect(await screen.findByText(/Could not load the patch/)).toBeTruthy();
    expect(screen.queryByText(/Pending — patch generation/)).toBeNull();
  });

  it("is not shown before the pipeline reaches patch generation", async () => {
    backendReturns({ agents: AGENTS, patch: BUNDLE });

    render(<Workspace runId="run-patch-early" />);

    await screen.findByText(/Status · Completed/);
    expect(screen.queryByText("Patch")).toBeNull();
  });

  const MULTI_BUNDLE = {
    issue_id: "fix-0",
    style_exemplar_commit: "abc1234",
    patches: [
      {
        file: "pkg/auth.py",
        original: "def validate(token):\n    return True\n",
        patched: "def validate(token):\n    return False\n",
        method: "ast_validated_write",
      },
      {
        file: "pkg/session.py",
        original: "def refresh():\n    pass\n",
        patched: "def refresh():\n    return None\n",
        method: "ast_validated_write",
      },
    ],
    contracts: [{ assertion: "expired tokens rejected", location: "pkg/auth.py" }],
    diff_text:
      "--- a/pkg/auth.py\n+++ b/pkg/auth.py\n@@ -1,2 +1,2 @@\n def validate(token):\n-    return True\n+    return False\n" +
      "--- a/pkg/session.py\n+++ b/pkg/session.py\n@@ -1,2 +1,2 @@\n def refresh():\n-    pass\n+    return None\n",
  };

  const PROVENANCE_DEFAULTS: PatchFileProvenance = {
    file: "",
    method: null,
    isTarget: false,
    targetFunction: null,
    generationSource: null,
    contextSource: null,
    runtimePrompt: null,
    retryNumber: null,
    retryReason: null,
    semanticDiff: null,
  };

  /** A "patch" agent entry carrying real `a7_patch_metrics` provenance. */
  function patchAgentWithProvenance(files: Partial<PatchFileProvenance>[]): AgentEntry {
    return {
      ...agent("patch", "Patch Generator"),
      visualization: {
        kind: "patch",
        data: { files: files.map((f) => ({ ...PROVENANCE_DEFAULTS, ...f })) },
      },
    };
  }

  it("shows a file selector for a multi-file patch, not one merged diff", async () => {
    backendReturns({
      agents: [patchAgentWithProvenance([]), agent("merge", "Mergeability")],
      patch: MULTI_BUNDLE,
    });

    render(<Workspace runId="run-patch-multi" />);

    // The active file's name appears twice (selector button + file header);
    // the inactive file's name appears once (selector button only) — either
    // way, both real filenames are on screen as separate selectable entries.
    expect((await screen.findAllByText("pkg/auth.py")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("pkg/session.py").length).toBeGreaterThan(0);
    // Only the active file's diff content is on screen — the inactive
    // file's changed line is not, because the two diffs are not merged into
    // one view.
    expect(screen.queryByText(diffLine("return None"))).toBeNull();
  });

  it("switching the file selector changes the displayed diff", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    backendReturns({
      agents: [patchAgentWithProvenance([]), agent("merge", "Mergeability")],
      patch: MULTI_BUNDLE,
    });

    render(<Workspace runId="run-patch-switch" />);
    await screen.findByText(diffLine("return False"));
    expect(screen.queryByText(diffLine("return None"))).toBeNull();

    await user.click(screen.getByText("pkg/session.py"));

    expect(await screen.findByText(diffLine("return None"))).toBeTruthy();
    expect(screen.queryByText(diffLine("return False"))).toBeNull();
  });

  it("marks the resolved target file using real backend provenance, not a guess", async () => {
    backendReturns({
      agents: [
        patchAgentWithProvenance([
          { file: "pkg/auth.py", isTarget: true },
          { file: "pkg/session.py", isTarget: false },
        ]),
        agent("merge", "Mergeability"),
      ],
      patch: MULTI_BUNDLE,
    });

    render(<Workspace runId="run-patch-target" />);

    expect((await screen.findAllByText("target")).length).toBeGreaterThan(0);
  });

  it("shows the target function only when A7 actually resolved one", async () => {
    backendReturns({
      agents: [
        patchAgentWithProvenance([{ file: "pkg/auth.py", targetFunction: "validate" }]),
        agent("merge", "Mergeability"),
      ],
      patch: BUNDLE,
    });

    render(<Workspace runId="run-patch-func" />);

    expect(await screen.findByText(/validate\(\)/)).toBeTruthy();
  });

  it("does not invent a target function when the backend has none", async () => {
    backendReturns({
      agents: [patchAgentWithProvenance([{ file: "pkg/auth.py" }]), agent("merge", "Mergeability")],
      patch: BUNDLE,
    });

    render(<Workspace runId="run-patch-nofunc" />);

    await screen.findByText(/Update\(/);
    expect(screen.queryByText(/::/)).toBeNull();
  });

  it("reports LLM generation honestly, not by assumption", async () => {
    backendReturns({
      agents: [
        patchAgentWithProvenance([{ file: "pkg/auth.py", generationSource: "llm" }]),
        agent("merge", "Mergeability"),
      ],
      patch: BUNDLE,
    });

    render(<Workspace runId="run-patch-llm" />);

    expect(await screen.findByText("LLM")).toBeTruthy();
  });

  it("reports stub mode honestly rather than always claiming LLM generation", async () => {
    backendReturns({
      agents: [
        patchAgentWithProvenance([{ file: "pkg/auth.py", generationSource: "stub" }]),
        agent("merge", "Mergeability"),
      ],
      patch: BUNDLE,
    });

    render(<Workspace runId="run-patch-stub" />);

    expect(await screen.findByText(/Stub mode — no LLM configured/)).toBeTruthy();
    expect(screen.queryByText("LLM")).toBeNull();
  });

  it("labels contracts as repair contracts, not as AI thoughts or reasoning", async () => {
    backendReturns({ agents: PATCH_AGENTS, patch: BUNDLE });

    render(<Workspace runId="run-patch-contracts" />);

    expect(await screen.findByText("Repair contracts")).toBeTruthy();
    expect(screen.queryByText(/thought/i)).toBeNull();
    expect(screen.queryByText(/reasoning/i)).toBeNull();
  });

  it("shows the empty patch state without any sample or fabricated code", async () => {
    backendReturns({ agents: PATCH_AGENTS, patch: null });

    render(<Workspace runId="run-patch-mock-guard" />);

    await screen.findByText(/Pending — patch generation/);
    expect(screen.queryByText("def validate")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("renders an A7 → A8 handoff without claiming A8 has already validated", async () => {
    backendReturns({ agents: PATCH_AGENTS, patch: BUNDLE });

    render(<Workspace runId="run-patch-handoff" />);

    expect(await screen.findByText("A7 generated patch")).toBeTruthy();
    expect(screen.getByText("A8 validation")).toBeTruthy();
    expect(screen.queryByText(/A8 passed/i)).toBeNull();
    expect(screen.queryByText(/validated successfully/i)).toBeNull();
  });

  it("shows a rejected attempt alongside a successful one, not just the survivor", async () => {
    backendReturns({
      agents: [
        patchAgentWithProvenance([
          { file: "pkg/auth.py", written: true },
          {
            file: "pkg/broken.py",
            written: false,
            retryNumber: 2,
            retryReason: "no semantic diff — output identical to original",
          },
        ]),
        agent("merge", "Mergeability"),
      ],
      patch: BUNDLE,
    });

    render(<Workspace runId="run-patch-rejected" />);

    expect(await screen.findByText("pkg/broken.py")).toBeTruthy();
    expect(
      screen.getByText(
        /Rejected — no semantic diff — output identical to original after 2 retries/,
      ),
    ).toBeTruthy();
    // The written file's real diff is still on screen — the reject pile is
    // additive, not a replacement for the successful patch.
    expect(screen.getByText(diffLine("return False"))).toBeTruthy();
  });

  it("marks a fully-rejected run as rejected, not as 'no change'", async () => {
    backendReturns({
      agents: [
        patchAgentWithProvenance([
          { file: "pkg/auth.py", written: false, retryReason: "invalid Python" },
        ]),
        agent("merge", "Mergeability"),
      ],
      patch: { ...BUNDLE, patches: [], diff_text: "" },
    });

    render(<Workspace runId="run-patch-all-rejected" />);

    expect(await screen.findByText(/Rejected — invalid Python/)).toBeTruthy();
    expect(screen.queryByText(/completed and produced no change/)).toBeNull();
  });

  it("collapses a long diff behind an expand affordance, then reveals it on click", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    const hunkLines = Array.from({ length: 90 }, (_, i) => ` line ${i}`).join("\n");
    const longDiff =
      `--- a/pkg/big.py\n+++ b/pkg/big.py\n@@ -1,90 +1,90 @@\n${hunkLines}\n` +
      "-old tail line\n+new tail line marker\n";
    backendReturns({
      agents: PATCH_AGENTS,
      patch: {
        ...BUNDLE,
        patches: [{ ...BUNDLE.patches[0], file: "pkg/big.py" }],
        diff_text: longDiff,
      },
    });

    render(<Workspace runId="run-patch-long-diff" />);

    // Head and tail context stay visible behind the fold, matching a
    // terminal transcript's head/tail collapse — only the middle is hidden.
    const expandButton = await screen.findByText(/click to expand/);
    expect(screen.getByText(diffLine("new tail line marker"))).toBeTruthy();
    expect(screen.queryByText(diffLine("line 60"))).toBeNull();

    await user.click(expandButton);

    expect(await screen.findByText(diffLine("line 60"))).toBeTruthy();
    expect(screen.queryByText(/click to expand/)).toBeNull();
  });
});

describe("Environment Preflight Board", () => {
  function headerWithEnv(
    status: string,
    environment: Record<string, unknown> | null,
    extra: Record<string, unknown> = {},
  ) {
    return { ...header(status), environment, ...extra };
  }

  it("renders READY with a real test count", async () => {
    backendReturns({
      header: headerWithEnv("completed", {
        status: "ready",
        language: "python",
        test_runner: "pytest",
        test_runner_available: true,
        tests_collected: 42,
        manifests: [{ path: "pyproject.toml", kind: "pyproject.toml", language: "python" }],
        blocking: false,
      }),
    });

    render(<Workspace runId="env-ready" />);

    expect(await screen.findByLabelText("Environment preflight: READY")).toBeTruthy();
    expect(screen.getByText("42 collected")).toBeTruthy();
    expect(screen.getByText("Reproduction can proceed.")).toBeTruthy();
  });

  it("renders UNSUPPORTED, distinctly from a generic block label", async () => {
    backendReturns({
      header: headerWithEnv("blocked", {
        status: "unsupported",
        language: "rust",
        reason: "This is a rust project. ProoFix analyses and repairs Python only.",
        blocking: true,
      }),
    });

    render(<Workspace runId="env-unsupported" />);

    expect(await screen.findByLabelText("Environment preflight: UNSUPPORTED")).toBeTruthy();
    expect(screen.getAllByText(/ProoFix analyses and repairs Python only/).length).toBeGreaterThan(
      0,
    );
    expect(
      screen.getByText("Repository is outside the supported repair environment."),
    ).toBeTruthy();
  });

  it("renders NO MANIFEST", async () => {
    backendReturns({
      header: headerWithEnv("blocked", {
        status: "no_manifest",
        reason: "No dependency manifest was found.",
        blocking: true,
      }),
    });

    render(<Workspace runId="env-no-manifest" />);

    expect(await screen.findByLabelText("Environment preflight: NO MANIFEST")).toBeTruthy();
    expect(screen.getByText("Unavailable")).toBeTruthy();
  });

  it("renders NOT PREPARED with the suggested command and a copy button", async () => {
    backendReturns({
      header: headerWithEnv("blocked", {
        status: "not_prepared",
        language: "python",
        test_runner: "pytest",
        test_runner_available: false,
        reason: "This repository's dependencies are not installed.",
        suggested_command: "pip install -r requirements.txt",
        missing_imports: ["pytest"],
        blocking: true,
      }),
    });

    render(<Workspace runId="env-not-prepared" />);

    expect(await screen.findByLabelText("Environment preflight: NOT PREPARED")).toBeTruthy();
    expect(screen.getByText("pip install -r requirements.txt")).toBeTruthy();
    expect(screen.getByRole("button", { name: /copy suggested setup command/i })).toBeTruthy();
  });

  it("does not show a copy button when the backend gives no suggested command", async () => {
    backendReturns({
      header: headerWithEnv("blocked", {
        status: "not_prepared",
        test_runner: "pytest",
        test_runner_available: false,
        reason: "pytest could not collect the test suite.",
        suggested_command: null,
        blocking: true,
      }),
    });

    render(<Workspace runId="env-not-prepared-no-cmd" />);

    await screen.findByLabelText("Environment preflight: NOT PREPARED");
    expect(screen.queryByRole("button", { name: /copy suggested setup command/i })).toBeNull();
  });

  it("renders NO TESTS as ready, never as a failure", async () => {
    backendReturns({
      header: headerWithEnv("completed", {
        status: "ready",
        language: "python",
        test_runner: "pytest",
        test_runner_available: true,
        tests_collected: 0,
        blocking: false,
      }),
    });

    render(<Workspace runId="env-no-tests" />);

    expect(await screen.findByLabelText("Environment preflight: NO TESTS")).toBeTruthy();
    expect(screen.getByText("None detected")).toBeTruthy();
    expect(screen.getByText("Reproduction cannot establish a failing test.")).toBeTruthy();
    // Never rendered as a failed environment.
    expect(screen.queryByLabelText("Environment preflight: NOT PREPARED")).toBeNull();
  });

  it("renders ERROR when the probe itself crashed, not when it simply hasn't run", async () => {
    backendReturns({
      header: headerWithEnv("running", null, { environmentProbeError: true }),
    });

    render(<Workspace runId="env-probe-error" />);

    expect(await screen.findByLabelText("Environment preflight: ERROR")).toBeTruthy();
  });

  it("renders nothing when there is no environment report and no probe error", async () => {
    backendReturns({ header: headerWithEnv("running", null) });

    render(<Workspace runId="env-absent" />);

    await screen.findByText(/Status · Running/);
    expect(screen.queryByLabelText(/Environment preflight/)).toBeNull();
  });

  it("never invents a suggested command or a language the backend did not report", async () => {
    backendReturns({
      header: headerWithEnv("blocked", {
        status: "not_prepared",
        language: null,
        test_runner: null,
        reason: "pytest could not collect the test suite: SyntaxError",
        suggested_command: null,
        blocking: true,
      }),
    });

    render(<Workspace runId="env-unmeasured" />);

    await screen.findByLabelText("Environment preflight: NOT PREPARED");
    // "Not measured" for every fact the backend did not supply — never a guess.
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
  });
});
