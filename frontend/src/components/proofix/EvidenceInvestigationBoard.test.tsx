// @vitest-environment jsdom
/**
 * A4's Evidence Investigation Board, rendered as a convergence evidence map.
 * Every assertion here traces to a specific field on
 * `GET /api/runs/{runId}/investigation` — the point of this suite is to catch
 * the map inventing a severity, a confidence, a citation or a location the
 * backend never sent, and to pin the distinctions the layout exists to
 * preserve:
 *
 *   ran-and-found-nothing ≠ could-not-run ≠ evidence against the finding.
 *
 * It also pins the honesty rules unique to the map: beams are drawn only for
 * real confidence terms, the earned-confidence figure under VERIFIED ONLY is
 * a plain sum of surviving terms (never a subtraction that could go negative
 * of a capped published figure), contradicting evidence is never hidden, and
 * no source code is ever rendered — only the cited location.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getInvestigation = vi.fn();

vi.mock("@/lib/runService", () => ({
  getInvestigation: (...a: unknown[]) => getInvestigation(...a),
}));

import type { InvestigationReport } from "./investigationTypes";
import { classifyTerms } from "./evidenceBeams";

const { EvidenceInvestigationBoard } = await import("./EvidenceInvestigationBoard");

const REPORT: InvestigationReport = {
  status: "partial",
  subjectKind: "runtime_failure",
  findingId: "tests/test_auth.py::test_expired",
  title: "AssertionError: expired token accepted",
  file: "vulnapi/auth.py",
  line: 42,
  severity: null,
  severityMeasured: false,
  reproductionStatus: "reproduced",
  rootCause: "validate_token never compares exp against the clock",
  summary: "Expired tokens are accepted",
  rootCauseSource: "deterministic",
  confidence: 0.6,
  confidenceBreakdown: [
    { component: "runtime evidence", points: 0.35, basis: "1 runtime reference(s) at 0.35 each" },
    { component: "verified citations", points: 0.25, basis: "1 citation(s) anchored to source" },
  ],
  evidence: [
    {
      id: "scanner:bandit",
      category: "scanner",
      source: "bandit",
      description: "Reported 1 finding(s) in the file under investigation.",
      status: "present",
      stance: "supporting",
      strength: 0.9,
      strengthBasis: "bandit's own severity for its finding in the file under investigation",
      detail: { scannerStatus: "ok", findings: 1, findingsAtSubject: 1, lines: [40] },
    },
    {
      id: "scanner:semgrep",
      category: "scanner",
      source: "semgrep",
      description: "Ran successfully and reported no findings.",
      status: "absent",
      stance: "neutral",
      strength: null,
      strengthBasis: null,
      detail: { scannerStatus: "ok_no_findings", findings: 0 },
    },
    {
      id: "scanner:ruff",
      category: "scanner",
      source: "ruff",
      description: "The scanner is not installed or could not be executed.",
      status: "unavailable",
      stance: "neutral",
      strength: null,
      strengthBasis: null,
      detail: { scannerStatus: "unavailable" },
    },
    {
      id: "reproduction",
      category: "reproduction",
      source: "pytest",
      description: "tests/test_auth.py::test_expired failed with AssertionError.",
      status: "present",
      stance: "supporting",
      strength: 0.9,
      strengthBasis: "A3.5's confidence in its own evidence source (pytest_report)",
      detail: { command: "pytest -q", exitCode: 1, testsCollected: 12 },
    },
    {
      id: "citation:0",
      category: "source",
      source: "vulnapi/auth.py:42",
      description: "no exp check",
      status: "present",
      stance: "supporting",
      strength: null,
      strengthBasis: null,
      detail: { file: "vulnapi/auth.py", line: 42, verified: true, sourceAvailable: false },
    },
    {
      id: "citation:1",
      category: "source",
      source: "vulnapi/ghost.py:9",
      description: "phantom claim",
      status: "absent",
      stance: "contradicting",
      strength: null,
      strengthBasis: null,
      detail: { file: "vulnapi/ghost.py", line: 9, verified: false, sourceAvailable: false },
    },
    {
      id: "dependency",
      category: "dependency",
      source: "OSV",
      description: "A2 has not run, so dependency reachability is unknown.",
      status: "unavailable",
      stance: "neutral",
      strength: null,
      strengthBasis: null,
      detail: {},
    },
  ],
  completeness: {
    measuredCategories: 3,
    totalCategories: 4,
    ratio: 0.75,
    categoryStatus: {
      scanner: "present",
      reproduction: "present",
      source: "present",
      dependency: "unavailable",
    },
  },
  unavailableSources: [
    { source: "ruff", reason: "The scanner is not installed or could not be executed." },
    { source: "A2 dependency analysis", reason: "A2 did not complete" },
  ],
  errors: [],
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

beforeEach(() => {
  getInvestigation.mockReset();
});

describe("EvidenceInvestigationBoard", () => {
  it("separates complete evidence coverage from an incomplete diagnosis", async () => {
    getInvestigation.mockResolvedValue({ ...clone(REPORT), status: "complete", diagnosisStatus: "incomplete" });
    render(<EvidenceInvestigationBoard runId="run-1" />);

    const claim = await screen.findByRole("group", { name: /root cause claim/i });
    expect(within(claim).getByText("Diagnosis evidence: incomplete")).toBeTruthy();
    expect(screen.getByText("Evidence coverage")).toBeTruthy();
    expect(screen.getByText("COMPLETE")).toBeTruthy();
  });

  it("renders the root cause claim, reproduction verdict and location", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    const claim = await screen.findByRole("group", { name: /root cause claim/i });
    expect(within(claim).getByText(/validate_token never compares exp/i)).toBeTruthy();
    expect(within(claim).getByText("REPRODUCED")).toBeTruthy();
    // Path splits so the directory can truncate and the basename+line cannot.
    expect(within(claim).getByText("auth.py:42")).toBeTruthy();
    expect(within(claim).getByTitle("vulnapi/auth.py")).toBeTruthy();
    expect(screen.getByText("PARTIAL")).toBeTruthy();
  });

  it("draws one beam per published confidence term, at its real weight", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    const sources = await screen.findByRole("list", { name: /evidence sources/i });
    expect(within(sources).getByText("runtime evidence")).toBeTruthy();
    expect(within(sources).getByText("verified citations")).toBeTruthy();
    expect(within(sources).getByText("+0.35")).toBeTruthy();
    expect(within(sources).getAllByText("VERIFIED").length).toBe(2);
  });

  it("shows the earned confidence the backend computed, not one of its own", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await screen.findByText("0.60");
    const bar = screen.getByRole("progressbar", { name: /earned confidence/i });
    expect(bar.getAttribute("aria-valuenow")).toBe("60");
  });

  it("converges on the finding's location, in the node", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await screen.findByText("Evidence converges");
    // The node repeats the cited location; both instances render the split path.
    expect(screen.getAllByText("auth.py:42").length).toBeGreaterThan(1);
  });

  it("never displays fabricated source code, and states why", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await screen.findByText("Evidence converges");
    expect(screen.queryByRole("button", { name: /view source/i })).toBeNull();
    expect(screen.queryByText(/def |import |class /)).toBeNull();
    const snapshot = screen.getByRole("group", { name: /source availability/i });
    expect(within(snapshot).getByText(/unavailable after run/i)).toBeTruthy();
    expect(within(snapshot).getByText(/2 locations preserved/i)).toBeTruthy();
  });

  it("builds the citation ladder from the full trail A4 followed, marking each stop", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    const ladder = await screen.findByRole("group", { name: /citation ladder/i });
    expect(within(ladder).getByText("auth.py:42")).toBeTruthy();
    expect(within(ladder).getByText(/no exp check/)).toBeTruthy();
    // The unverified stop is in the ladder too, marked distinctly — the
    // ladder is the trail, not a curated "supporting only" list.
    const ghost = within(ladder).getByText("ghost.py:9");
    expect(ghost).toBeTruthy();
    expect(within(ladder).getByText(/phantom claim/)).toBeTruthy();
    expect(within(ladder).getAllByText("✓").length).toBe(1);
    expect(within(ladder).getAllByText("✕").length).toBe(1);
  });

  it("shows the number of independent evidence sources in the node, unconditionally", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await screen.findByText("Evidence converges");
    // REPORT has two confidence terms (runtime, verified citations); no
    // diversity bonus is published, but the source count still shows.
    expect(screen.getByText("2 independent evidence sources")).toBeTruthy();
  });

  it("never hides contradicting evidence — it gets its own branch", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    const branch = await screen.findByRole("group", { name: /contradicting evidence/i });
    expect(within(branch).getByText("ghost.py:9")).toBeTruthy();
    expect(within(branch).getByText(/outweighs it/i)).toBeTruthy();
  });

  it("says none contradicts when every citation anchored", async () => {
    const clean = clone(REPORT);
    clean.evidence = clean.evidence.filter((e) => e.id !== "citation:1");
    getInvestigation.mockResolvedValue(clean);
    render(<EvidenceInvestigationBoard runId="run-1" />);

    const branch = await screen.findByRole("group", { name: /contradicting evidence/i });
    expect(within(branch).getByText(/every citation anchored/i)).toBeTruthy();
  });

  it("renders 'Not measured' for a severity no tool assigned", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await screen.findByText(/validate_token never compares exp/i);
    expect(screen.queryByText(/^HIGH|^MEDIUM|^LOW/)).toBeNull();
  });

  it("shows a measured severity as a band", async () => {
    const measured = clone(REPORT);
    measured.severity = 0.9;
    measured.severityMeasured = true;
    getInvestigation.mockResolvedValue(measured);
    render(<EvidenceInvestigationBoard runId="run-1" />);

    expect(await screen.findByText("HIGH (0.90)")).toBeTruthy();
  });

  it("says 'Not measured' rather than a fabricated score when nothing was scored", async () => {
    const empty = clone(REPORT);
    empty.confidence = null;
    empty.confidenceBreakdown = [];
    getInvestigation.mockResolvedValue(empty);
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await screen.findByText(/no confidence terms/i);
    expect(screen.queryByRole("progressbar", { name: /earned confidence/i })).toBeNull();
  });

  it("renders a no-finding investigation as exactly that", async () => {
    const none = clone(REPORT);
    none.status = "no_finding";
    none.subjectKind = null;
    none.findingId = null;
    none.title = null;
    none.file = null;
    none.line = null;
    none.rootCause = null;
    none.reproductionStatus = null;
    getInvestigation.mockResolvedValue(none);
    render(<EvidenceInvestigationBoard runId="run-1" />);

    expect(await screen.findByText("NO FINDING")).toBeTruthy();
    expect(screen.getByText(/there was nothing for it to take as its subject/i)).toBeTruthy();
  });

  it("surfaces a degraded investigation instead of hiding it", async () => {
    const degraded = clone(REPORT);
    degraded.status = "error";
    degraded.errors = [
      "LLM investigation unavailable (TimeoutError); deterministic analysis used.",
    ];
    getInvestigation.mockResolvedValue(degraded);
    render(<EvidenceInvestigationBoard runId="run-1" />);

    expect(await screen.findByText("DEGRADED")).toBeTruthy();
    expect(screen.getByText(/LLM investigation unavailable/)).toBeTruthy();
  });

  it("distinguishes A4 pending from A4 running", async () => {
    getInvestigation.mockResolvedValue(null);
    const { unmount } = render(<EvidenceInvestigationBoard runId="run-1" />);
    expect(await screen.findByText(/A4 has not completed/i)).toBeTruthy();
    unmount();

    getInvestigation.mockResolvedValue(null);
    render(<EvidenceInvestigationBoard runId="run-2" status="running" />);
    expect(await screen.findByText(/A4 is correlating evidence now/i)).toBeTruthy();
  });

  it("reports a load failure and retries on demand", async () => {
    getInvestigation.mockRejectedValueOnce(new Error("API 500: Internal Server Error"));
    getInvestigation.mockResolvedValueOnce(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() =>
      expect(screen.getByText(/validate_token never compares exp/i)).toBeTruthy(),
    );
  });

  it("puts the full category breakdown behind a collapsed disclosure", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    const toggle = await screen.findByRole("button", { name: /full evidence log/i });
    expect(screen.queryByRole("group", { name: /scanners evidence/i })).toBeNull();
    await userEvent.click(toggle);

    const scanners = screen.getByRole("group", { name: /scanners evidence/i });
    expect(within(scanners).getByText("3")).toBeTruthy();
    expect(screen.getByText(/3\/4 evidence categories answered/)).toBeTruthy();
    expect(screen.getByText("A2 dependency analysis")).toBeTruthy();
  });

  it("colors an unavailable source gray, never amber — it never asserted anything unproven", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await userEvent.click(await screen.findByRole("button", { name: /full evidence log/i }));
    const scanners = screen.getByRole("group", { name: /scanners evidence/i });
    await userEvent.click(within(scanners).getByRole("button", { name: /scanners/i }));

    const ruffLabel = within(scanners).getByText("not measured");
    expect(ruffLabel.getAttribute("style")).toContain("--color-ink-soft");
    expect(ruffLabel.getAttribute("style")).not.toContain("--color-status-retry");
  });

  it("expands a logged evidence item to its real backend metadata", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await userEvent.click(await screen.findByRole("button", { name: /full evidence log/i }));
    const reproduction = screen.getByRole("group", { name: /reproduction evidence/i });
    await userEvent.click(within(reproduction).getByRole("button", { name: /reproduction/i }));
    const row = within(reproduction).getByRole("button", { name: /pytest/i });
    expect(screen.queryByText("pytest -q")).toBeNull();
    await userEvent.click(row);

    expect(screen.getByText("pytest -q")).toBeTruthy();
    expect(screen.getByText("exitCode")).toBeTruthy();
  });
});

describe("EvidenceInvestigationBoard — verified only", () => {
  /** A run argued from a mix of anchored and merely-claimed evidence. */
  const MIXED: InvestigationReport = {
    ...clone(REPORT),
    confidence: 0.85,
    confidenceBreakdown: [
      { component: "runtime evidence", points: 0.35, basis: "1 runtime reference(s) at 0.35 each" },
      { component: "verified citations", points: 0.25, basis: "1 citation(s) anchored to source" },
      { component: "cve evidence", points: 0.1, basis: "1 cve reference(s) at 0.1 each" },
      { component: "stack trace evidence", points: 0.1, basis: "1 stack trace reference(s)" },
      {
        component: "source diversity",
        points: 0.05,
        basis: "4 independent evidence sources agree",
      },
    ],
  };

  it("makes unverified beams disappear and drops the earned confidence", async () => {
    getInvestigation.mockResolvedValue(clone(MIXED));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await screen.findByText("0.85");
    await userEvent.click(screen.getByRole("button", { name: /verified only/i }));

    // runtime 0.35 + verified citations 0.25 = 0.60.
    expect(await screen.findByText("0.60")).toBeTruthy();
    // The left column draws the four source beams (diversity lives in the
    // node instead); cve and stack trace are the two that withdraw.
    const sources = screen.getByRole("list", { name: /evidence sources/i });
    expect(within(sources).getAllByText("withdrawn").length).toBe(2);
    // The published figure stays on screen, struck through, right beside the
    // earned number — the gap is a glance, not a sentence to read.
    expect(screen.getByText("0.85")).toBeTruthy();
  });

  it("withdraws the diversity bonus from the node when it isn't verified-only", async () => {
    getInvestigation.mockResolvedValue(clone(MIXED));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await screen.findByText(/diversity bonus \+0\.05/i);
    await userEvent.click(screen.getByRole("button", { name: /verified only/i }));

    expect(await screen.findByText(/diversity bonus withdrawn/i)).toBeTruthy();
  });

  it("pays the diversity bonus only for genuinely verified sources", () => {
    const beams = classifyTerms(MIXED.confidenceBreakdown);
    const diversity = beams.find((b) => b.component === "source diversity");
    expect(diversity?.provenance).toBe("asserted");
    expect(diversity?.withdrawnBecause).toMatch(/1 verified source remain/);
  });

  it("keeps an unrecognised confidence term visible in both views", async () => {
    const grown = clone(MIXED);
    grown.confidenceBreakdown = [
      { component: "runtime evidence", points: 0.35, basis: "1 runtime reference(s)" },
      { component: "telemetry corroboration", points: 0.2, basis: "a term added after this build" },
    ];
    grown.confidence = 0.55;
    getInvestigation.mockResolvedValue(grown);
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await screen.findByText("0.55");
    const sources = screen.getByRole("list", { name: /evidence sources/i });
    expect(within(sources).getByText("UNCLASSIFIED")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /verified only/i }));

    // Dropping it would understate the run on the strength of a stale frontend.
    expect(within(sources).getByText("telemetry corroboration")).toBeTruthy();
  });

  it("says every term was verified when there is nothing to withdraw", async () => {
    getInvestigation.mockResolvedValue(clone(REPORT));
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await screen.findByText("0.60");
    await userEvent.click(screen.getByRole("button", { name: /verified only/i }));
    // No "withdrawn" label anywhere in the source list — both terms survive.
    const sources = screen.getByRole("list", { name: /evidence sources/i });
    expect(within(sources).queryByText("withdrawn")).toBeNull();
  });
});

describe("EvidenceInvestigationBoard — repository scale", () => {
  /** A large repository: A3 ranks up to eight findings, each drawing citations. */
  function largeReport(): InvestigationReport {
    const big = clone(REPORT);
    big.evidence = [
      ...big.evidence,
      ...Array.from({ length: 10 }, (_, i) => ({
        id: `scanner:extra-${i}`,
        category: "scanner" as const,
        source: `bandit-rule-B${100 + i}`,
        description: `Reported a finding in module ${i}.`,
        status: "present" as const,
        stance: "supporting" as const,
        strength: 0.6,
        strengthBasis: "bandit's own severity",
        detail: { scannerStatus: "ok", findings: 1 },
      })),
      ...Array.from({ length: 6 }, (_, i) => ({
        id: `citation:extra-${i}`,
        category: "source" as const,
        source: `services/very/deeply/nested/module_${i}.py:${10 + i}`,
        description: `claim ${i}`,
        status: "present" as const,
        stance: "supporting" as const,
        strength: null,
        strengthBasis: null,
        detail: {
          file: `services/very/deeply/nested/module_${i}.py`,
          line: 10 + i,
          verified: true,
          sourceAvailable: false,
        },
      })),
    ];
    return big;
  }

  it("draws the same beams however much evidence the repository produced", async () => {
    getInvestigation.mockResolvedValue(largeReport());
    render(<EvidenceInvestigationBoard runId="run-1" />);

    const sources = await screen.findByRole("list", { name: /evidence sources/i });
    expect(within(sources).getAllByRole("listitem").length).toBe(2);
    expect(within(sources).queryByText("bandit-rule-B100")).toBeNull();
  });

  it("caps the citation ladder and offers the rest on demand", async () => {
    getInvestigation.mockResolvedValue(largeReport());
    render(<EvidenceInvestigationBoard runId="run-1" />);

    const ladder = await screen.findByRole("group", { name: /citation ladder/i });
    expect(within(ladder).queryByText("module_5.py:15")).toBeNull();

    // 2 citations on the base report (1 verified, 1 unverified) + 6 added here.
    await userEvent.click(
      within(ladder).getByRole("button", { name: /show all 8 cited locations/i }),
    );
    expect(within(ladder).getByText("module_5.py:15")).toBeTruthy();
  });

  it("keeps a long category capped inside the evidence log", async () => {
    getInvestigation.mockResolvedValue(largeReport());
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await userEvent.click(await screen.findByRole("button", { name: /full evidence log/i }));
    const scanners = screen.getByRole("group", { name: /scanners evidence/i });
    await userEvent.click(within(scanners).getByRole("button", { name: /scanners/i }));
    expect(within(scanners).queryByText("bandit-rule-B109")).toBeNull();

    await userEvent.click(within(scanners).getByRole("button", { name: /show all 13 scanners/i }));
    expect(within(scanners).getByText("bandit-rule-B109")).toBeTruthy();
  });

  it("keeps a small run's beam field readable with a single term", async () => {
    const small = clone(REPORT);
    small.confidence = 0.15;
    small.confidenceBreakdown = [
      { component: "stack trace evidence", points: 0.15, basis: "1 stack trace reference(s)" },
    ];
    getInvestigation.mockResolvedValue(small);
    render(<EvidenceInvestigationBoard runId="run-1" />);

    const sources = await screen.findByRole("list", { name: /evidence sources/i });
    expect(within(sources).getByText("stack trace evidence")).toBeTruthy();
    expect(within(sources).getByText("UNVERIFIED")).toBeTruthy();
  });

  it("never truncates a directory without min-w-0, so long paths stay inside the row", async () => {
    const deep = clone(REPORT);
    deep.file = "services/very/deeply/nested/package/module/submodule/auth_handler.py";
    getInvestigation.mockResolvedValue(deep);
    render(<EvidenceInvestigationBoard runId="run-1" />);

    await screen.findAllByText("auth_handler.py:42");
    // The claim card and the node both render the same location; either
    // instance's directory span must carry both classes for it to be safe.
    const dirs = screen.getAllByText("services/very/deeply/nested/package/module/submodule/");
    expect(dirs.length).toBeGreaterThan(0);
    for (const dir of dirs) {
      expect(dir.className).toContain("min-w-0");
      expect(dir.className).toContain("truncate");
    }
  });
});
