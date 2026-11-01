// @vitest-environment jsdom
/**
 * A3.5's Runtime Observation Record panel. Every assertion here traces back
 * to a specific field on `GET /api/runs/{runId}/reproduction` — the point of
 * this suite is to catch the panel inventing a verdict, a confidence score,
 * a test count, or a duration the backend never sent.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getReproductionEvidence = vi.fn();
const getKnowledgeMetrics = vi.fn();
const getSemanticGraph = vi.fn();
const getDependencyRisk = vi.fn();
const getStaticFindings = vi.fn();

vi.mock("@/lib/runService", () => ({
  getReproductionEvidence: (...a: unknown[]) => getReproductionEvidence(...a),
  getKnowledgeMetrics: (...a: unknown[]) => getKnowledgeMetrics(...a),
  getSemanticGraph: (...a: unknown[]) => getSemanticGraph(...a),
  getDependencyRisk: (...a: unknown[]) => getDependencyRisk(...a),
  getStaticFindings: (...a: unknown[]) => getStaticFindings(...a),
}));

const { ReproductionEvidencePanel } = await import("./ReproductionEvidencePanel");

const CONFIRMED = {
  status: "CONFIRMED",
  uiStatus: "reproduced",
  confidence: 0.9,
  evidenceSource: "pytest_report",
  failingTest: "tests/test_auth.py::test_expired_token_rejected",
  exceptionType: "AssertionError",
  exceptionMessage: "assert True is False",
  errorSignature: "AssertionError: assert True is False",
  failingFile: "vulnapi/tests/test_auth.py",
  failingLine: 27,
  traceback: "E   AssertionError: assert True is False",
  infraDetail: null,
  command: "python -m pytest --tb=long --json-report -v",
  exitCode: 1,
  timedOut: false,
  stdout: "collected 12 items",
  stderr: "",
  durationSeconds: 0.03,
  startedAt: "2026-08-13T17:30:37",
  finishedAt: "2026-08-13T17:30:38",
  testsCollected: 12,
  testsPassed: 8,
  testsFailed: 4,
  reexecutionCommand:
    "python -m pytest tests/test_auth.py::test_expired_token_rejected -v --tb=long",
  reexecutionIsTargeted: true,
  reexecutionTimeoutSeconds: 120,
  baselineFailures: ["tests/test_config.py::test_secret_from_env"],
  stages: [
    {
      id: "suite_executed",
      label: "Test suite executed",
      status: "done",
      detail: "pytest exited with code 1.",
    },
    {
      id: "tests_collected",
      label: "Tests collected",
      status: "done",
      detail: "12 test(s) collected.",
    },
    { id: "tests_run", label: "Tests executed", status: "done", detail: "8 passed, 4 failed." },
    {
      id: "failure_observed",
      label: "Failure observed",
      status: "done",
      detail: "tests/test_auth.py::test_expired_token_rejected failed with AssertionError.",
    },
    {
      id: "evidence_captured",
      label: "Evidence captured",
      status: "done",
      detail: "Exception and traceback captured from the structured pytest report.",
    },
  ],
};

const OUTPUT_TEXT_CONFIRMED = {
  ...CONFIRMED,
  evidenceSource: "output_text",
  confidence: 0.7,
};

const UNCONFIRMED = {
  ...CONFIRMED,
  status: "UNCONFIRMED",
  uiStatus: "not_reproduced",
  confidence: 0.0,
  evidenceSource: null,
  failingTest: null,
  exceptionType: null,
  exceptionMessage: null,
  errorSignature: null,
  failingFile: null,
  failingLine: null,
  traceback: null,
  testsFailed: 0,
  testsPassed: 12,
  baselineFailures: [],
  stages: [
    {
      id: "suite_executed",
      label: "Test suite executed",
      status: "done",
      detail: "pytest exited with code 0.",
    },
    {
      id: "tests_collected",
      label: "Tests collected",
      status: "done",
      detail: "12 test(s) collected.",
    },
    { id: "tests_run", label: "Tests executed", status: "done", detail: "12 passed, 0 failed." },
    {
      id: "failure_observed",
      label: "Failure observed",
      status: "not_triggered",
      detail: "No failure — every collected test passed.",
    },
    {
      id: "evidence_captured",
      label: "Evidence captured",
      status: "skipped",
      detail: "Not reached — no failure was observed.",
    },
  ],
};

const TIMEOUT = {
  ...CONFIRMED,
  status: "INFRA_ERROR",
  uiStatus: "error",
  confidence: 0.0,
  evidenceSource: null,
  failingTest: null,
  exceptionType: null,
  exceptionMessage: null,
  errorSignature: null,
  failingFile: null,
  failingLine: null,
  traceback: null,
  infraDetail: "timeout",
  exitCode: -1,
  timedOut: true,
  durationSeconds: null,
  testsCollected: null,
  testsPassed: null,
  testsFailed: null,
  baselineFailures: [],
  stages: [
    {
      id: "suite_executed",
      label: "Test suite executed",
      status: "failed",
      detail: "The pytest subprocess exceeded its time limit and was terminated.",
    },
    {
      id: "tests_collected",
      label: "Tests collected",
      status: "skipped",
      detail: "Not reached — the suite never executed.",
    },
    {
      id: "tests_run",
      label: "Tests executed",
      status: "skipped",
      detail: "Not reached — no tests were collected.",
    },
    {
      id: "failure_observed",
      label: "Failure observed",
      status: "skipped",
      detail: "Not reached — the tests did not run to completion.",
    },
    {
      id: "evidence_captured",
      label: "Evidence captured",
      status: "skipped",
      detail: "Not reached — no failure was observed.",
    },
  ],
};

const NO_TESTS = {
  ...TIMEOUT,
  status: "NO_TESTS",
  uiStatus: "unavailable",
  infraDetail: "No tests collected by pytest",
  exitCode: 5,
  timedOut: false,
  testsCollected: 0,
  stages: [
    {
      id: "suite_executed",
      label: "Test suite executed",
      status: "done",
      detail: "pytest exited with code 5.",
    },
    {
      id: "tests_collected",
      label: "Tests collected",
      status: "failed",
      detail: "pytest collected zero tests.",
    },
    {
      id: "tests_run",
      label: "Tests executed",
      status: "skipped",
      detail: "Not reached — no tests were collected.",
    },
    {
      id: "failure_observed",
      label: "Failure observed",
      status: "skipped",
      detail: "Not reached — the tests did not run to completion.",
    },
    {
      id: "evidence_captured",
      label: "Evidence captured",
      status: "skipped",
      detail: "Not reached — no failure was observed.",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ReproductionEvidencePanel", () => {
  it("shows a loading message before the backend responds", () => {
    getReproductionEvidence.mockReturnValue(new Promise(() => {}));
    render(<ReproductionEvidencePanel runId="r1" />);
    expect(screen.getByText("Loading reproduction evidence…")).toBeTruthy();
  });

  it("renders Pending when A3.5 has not completed (404)", async () => {
    getReproductionEvidence.mockResolvedValue(null);
    render(<ReproductionEvidencePanel runId="r1" />);
    expect(
      await screen.findByText(/Pending — A3\.5 has not completed for this run yet/),
    ).toBeTruthy();
  });

  it("shows a distinct running message rather than the generic pending one", async () => {
    getReproductionEvidence.mockResolvedValue(null);
    render(<ReproductionEvidencePanel runId="r1" status="running" />);
    expect(await screen.findByText(/RUNNING — A3\.5 is executing the test suite now/)).toBeTruthy();
  });

  it("surfaces a real API failure with a retry", async () => {
    getReproductionEvidence.mockRejectedValue(new Error("API 500: Internal Server Error"));
    render(<ReproductionEvidencePanel runId="r1" />);
    expect(await screen.findByText("API 500: Internal Server Error")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("labels the panel as a Runtime Observation Record", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);
    expect(await screen.findByText("Runtime Observation Record")).toBeTruthy();
  });

  it("CONFIRMED leads with the error signature as the hero, badge secondary", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Failure Reproduction");
    expect(screen.getByText("AssertionError: assert True is False")).toBeTruthy();
    expect(screen.getByText("CONFIRMED")).toBeTruthy();
    expect(screen.getByText("vulnapi/tests/test_auth.py")).toBeTruthy();
    expect(screen.getByText("27")).toBeTruthy();
    expect(screen.getByText(CONFIRMED.failingTest)).toBeTruthy();
  });

  it("shows the evidence provenance chain and real confidence for a structured report", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Evidence provenance");
    expect(screen.getByText("JSON report")).toBeTruthy();
    expect(screen.getByText("failure classifier")).toBeTruthy();
    expect(screen.getByText("Structured evidence")).toBeTruthy();
    expect(screen.getByText("0.90")).toBeTruthy();
    // The chain's terminus box names the real exception type.
    expect(screen.getByText("AssertionError")).toBeTruthy();
    expect(screen.queryByText(/Structured report unavailable/)).toBeNull();
  });

  it("shows the degraded-evidence caveat for output-text fallback evidence, with its own real confidence", async () => {
    getReproductionEvidence.mockResolvedValue(OUTPUT_TEXT_CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Evidence provenance");
    expect(screen.getByText("stdout")).toBeTruthy();
    expect(screen.getByText("text / regex")).toBeTruthy();
    expect(screen.getByText("Fallback evidence")).toBeTruthy();
    expect(screen.getByText("0.70")).toBeTruthy();
    expect(screen.getByText(/Structured report unavailable/)).toBeTruthy();
  });

  it("UNCONFIRMED reads as a real negative result, not an error", async () => {
    getReproductionEvidence.mockResolvedValue(UNCONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Failure Reproduction");
    expect(screen.getByText("UNCONFIRMED")).toBeTruthy();
    expect(screen.getByText("No failure observed")).toBeTruthy();
    expect(screen.getByText(/12 executed/)).toBeTruthy();
    // No provenance chain is drawn — evidenceSource is null for this status.
    expect(screen.queryByText("Evidence provenance")).toBeNull();
  });

  it("NO_TESTS reads as a coverage gap, not a failed run", async () => {
    getReproductionEvidence.mockResolvedValue(NO_TESTS);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Failure Reproduction");
    expect(screen.getByText("NO TESTS")).toBeTruthy();
    expect(screen.getByText("No test surface")).toBeTruthy();
    expect(
      screen.getByText("No executable tests were available to observe the reported failure."),
    ).toBeTruthy();
  });

  it("INFRA_ERROR states the timeout explicitly — never inferred from the bare exit code", async () => {
    getReproductionEvidence.mockResolvedValue(TIMEOUT);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Failure Reproduction");
    expect(screen.getByText("INFRASTRUCTURE ERROR")).toBeTruthy();
    expect(screen.getByText("Observation blocked")).toBeTruthy();
    // TIMEOUT has no durationSeconds, so the hero falls back to the real
    // stage detail text rather than inventing a duration.
    expect(screen.getByText(/Timed out — The pytest subprocess exceeded/)).toBeTruthy();
    expect(screen.getAllByText("-1").length).toBeGreaterThan(0);
  });

  it("never fabricates a timeout duration the backend did not send", async () => {
    getReproductionEvidence.mockResolvedValue(TIMEOUT);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Failure Reproduction");
    // 120 only appears via `reexecutionTimeoutSeconds`, which belongs to the
    // *different* targeted command — never attached to the observed timeout.
    expect(screen.queryByText(/Timed out after 120/)).toBeNull();
  });

  it("shows a real measured timeout duration when the backend reports one", async () => {
    getReproductionEvidence.mockResolvedValue({ ...TIMEOUT, durationSeconds: 120.04 });
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Failure Reproduction");
    expect(screen.getByText("Timed out after 120.04s")).toBeTruthy();
  });

  it("runtime signal shows real pass/reproduced/baseline counts and nothing fabricated", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Runtime signal");
    expect(screen.getByText("12 collected")).toBeTruthy();
    expect(screen.getByText((_, el) => el?.textContent === "8 passed")).toBeTruthy();
    expect(screen.getByText((_, el) => el?.textContent === "1 baseline")).toBeTruthy();
    expect(screen.getByText((_, el) => el?.textContent === "1 reproduced")).toBeTruthy();
  });

  it("runtime signal is honestly omitted (not zero-filled) when testsCollected is null", async () => {
    getReproductionEvidence.mockResolvedValue(TIMEOUT);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Runtime signal");
    expect(screen.getByText(/never reached a collection count/)).toBeTruthy();
  });

  it("shows ALL PASSED and no signal-vs-noise split for a clean UNCONFIRMED run", async () => {
    getReproductionEvidence.mockResolvedValue(UNCONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Runtime signal");
    expect(screen.getByText("ALL PASSED")).toBeTruthy();
    expect(screen.queryByText("Signal")).toBeNull();
    expect(screen.queryByText("Noise floor")).toBeNull();
  });

  it("distinguishes signal (target failure) from noise floor (pre-existing failures)", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Signal");
    expect(screen.getByText(/Target failure observed/)).toBeTruthy();
    expect(screen.getByText("1 reproduced failure")).toBeTruthy();
    expect(screen.getByText("Noise floor")).toBeTruthy();
    expect(screen.getByText(/1 baseline failure/)).toBeTruthy();
    expect(screen.getByText("Already failing before reproduction")).toBeTruthy();
  });

  it("evidence strength synthesizes existing facts, never a new invented score, per status", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Evidence strength");
    expect(screen.getByText("OBSERVED")).toBeTruthy();
    expect(screen.getByText("Runtime failure reproduced")).toBeTruthy();
    expect(screen.getByText("1 target · 8 passed · 1 baseline")).toBeTruthy();
    expect(screen.getByText("Structured pytest evidence")).toBeTruthy();
  });

  it("evidence strength reads NOT OBSERVED for a clean negative result", async () => {
    getReproductionEvidence.mockResolvedValue(UNCONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Evidence strength");
    expect(screen.getByText("NOT OBSERVED")).toBeTruthy();
    expect(screen.getByText("0 reproduced · 12 passed")).toBeTruthy();
  });

  it("evidence strength reads INCONCLUSIVE for an infrastructure failure, distinct from a real negative", async () => {
    getReproductionEvidence.mockResolvedValue(TIMEOUT);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Evidence strength");
    expect(screen.getByText("INCONCLUSIVE")).toBeTruthy();
    expect(screen.getByText("Execution failed before reliable observation")).toBeTruthy();
  });

  it("labels a nonzero exit code as a test failure, never left for the reader to decode", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Execution measurement");
    expect(screen.getByText("test failure")).toBeTruthy();
  });

  it("labels a negative exit code as an execution error, and a clean exit as clean", async () => {
    getReproductionEvidence.mockResolvedValue({ ...CONFIRMED, exitCode: 0 });
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Execution measurement");
    expect(screen.getByText("clean exit")).toBeTruthy();
  });

  it("full traceback is available but collapsed by default, for CONFIRMED only", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    const user = userEvent.setup();
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Full execution traceback");
    expect(screen.queryByText(/^E\s+AssertionError/)).toBeNull();

    await user.click(screen.getByText("Full execution traceback"));
    expect(await screen.findByText(/^E\s+AssertionError/)).toBeTruthy();

    await user.click(screen.getByText("Full execution traceback"));
    await waitFor(() => {
      expect(screen.queryByText(/^E\s+AssertionError/)).toBeNull();
    });
  });

  it("does not offer a traceback toggle when there is no failure to show one for", async () => {
    getReproductionEvidence.mockResolvedValue(UNCONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Failure Reproduction");
    expect(screen.queryByText("Full execution traceback")).toBeNull();
  });

  it("shows baseline failures, excluding the target itself", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText(/Baseline failures — pre-existing, excluded from the target \(1\)/);
    expect(screen.getByText("tests/test_config.py::test_secret_from_env")).toBeTruthy();
    expect(
      screen.queryByText("tests/test_auth.py::test_expired_token_rejected", { selector: "li" }),
    ).toBeNull();
  });

  it("shows the command A3.5 actually executed, labelled distinctly", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Actually executed");
    expect(screen.getByText(`$ ${CONFIRMED.command}`)).toBeTruthy();
  });

  it("labels the re-execution command as prepared for A8, never as executed", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("A8 targeted re-execution");
    expect(screen.getByText("prepared — not executed by A3.5")).toBeTruthy();
    expect(screen.getByText(`$ ${CONFIRMED.reexecutionCommand}`)).toBeTruthy();
  });

  it("the A4 handoff names the exact file/line for a confirmed failure", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("A4 — Evidence Investigation");
    expect(
      screen.getByText(/Handed off: error signature, vulnapi\/tests\/test_auth\.py:27/),
    ).toBeTruthy();
  });

  it("the A4 handoff states limited confidence rather than a location for a non-reproduced result", async () => {
    getReproductionEvidence.mockResolvedValue(UNCONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("A4 — Evidence Investigation");
    expect(
      screen.getByText(
        "No runtime evidence to hand off — downstream agents proceed without confirmed reproduction.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/^Handed off/)).toBeNull();
  });

  it("never calls A0.5's, A1's, A2's, or A3's endpoints — A3.5's evidence is its own", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Failure Reproduction");
    expect(getKnowledgeMetrics).not.toHaveBeenCalled();
    expect(getSemanticGraph).not.toHaveBeenCalled();
    expect(getDependencyRisk).not.toHaveBeenCalled();
    expect(getStaticFindings).not.toHaveBeenCalled();
  });

  it("carries the explainability source line for the reproduction endpoint", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Failure Reproduction");
    expect(screen.getByText(/\/reproduction/)).toBeTruthy();
  });

  it("never claims a link to a specific A3 finding", async () => {
    getReproductionEvidence.mockResolvedValue(CONFIRMED);
    render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Failure Reproduction");
    expect(screen.getAllByText(/does not target a specific A3 finding/).length).toBeGreaterThan(0);
  });

  it("compresses the runtime signal into a fixed-size dense grid for a large suite instead of one cell per test", async () => {
    getReproductionEvidence.mockResolvedValue({
      ...CONFIRMED,
      testsCollected: 400,
      testsPassed: 395,
      baselineFailures: Array.from({ length: 4 }, (_, i) => `tests/mod_${i}.py::test_x`),
    });
    const { container } = render(<ReproductionEvidencePanel runId="r1" />);

    await screen.findByText("Runtime signal");
    // The real 400 is still stated in text...
    expect(screen.getByText("400 collected")).toBeTruthy();
    // ...but the cell grid itself is capped to a fixed budget — the DOM
    // node count for the signal must not scale with the suite size.
    const cells = container.querySelectorAll('[role="img"] > span');
    expect(cells.length).toBeLessThan(60);
    expect(cells.length).toBeGreaterThan(0);
  });
});
