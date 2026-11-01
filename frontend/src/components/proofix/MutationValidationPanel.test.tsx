// @vitest-environment jsdom
/**
 * A8's Mutation Validation battle map. Every assertion traces to a field on
 * `GET /api/runs/{runId}/mutation` — the point of this suite is to catch the
 * panel inventing a line the backend never resolved, a specific test name
 * mutmut never attributed, a passed gate the run never reached, or a 0%
 * score for mutation testing that was never scored.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getMutationValidation = vi.fn();

vi.mock("@/lib/runService", () => ({
  getMutationValidation: (...a: unknown[]) => getMutationValidation(...a),
}));

const { MutationValidationPanel } = await import("./MutationValidationPanel");
import type { MutationValidationReport } from "./mutationValidationTypes";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

const DECODE_TOKEN_LINES = [
  { line: 42, text: "def decode_token(tok: str) -> Claims:" },
  { line: 43, text: '    payload = jwt.decode(tok, KEY, algorithms=["HS256"])' },
  { line: 44, text: '    if payload["exp"] < time.time():' },
  { line: 45, text: "        raise ExpiredToken()" },
  { line: 46, text: "    return Claims(**payload)" },
];

const CLEAN: MutationValidationReport = {
  stage: "mutation",
  pytestAvailable: true,
  pytestPassed: true,
  targetTestId: "auth::test_expired_token",
  targetTestPassed: true,
  regressionTestsPassed: true,
  newFailures: [],
  preExistingFailures: [],
  mutationStatus: "scored",
  unavailableReason: null,
  mutationScore: 1,
  mutantSurvived: false,
  killedMutants: 3,
  survivedMutants: 0,
  totalMutants: 3,
  inconclusiveMutants: 0,
  mutantsByStatus: { killed: 3 },
  correctnessScore: 94,
  correctnessThreshold: 80,
  patchRetryRequired: false,
  pytestReexecutionCommand: "python -m pytest tests/test_auth.py::test_expired -v",
  reexecutionCommand: "mutmut run",
  reexecutionTimeoutSeconds: 120,
  retryContext: null,
  patchFile: "backend/auth.py",
  functions: [
    {
      name: "decode_token",
      killed: 3,
      survived: 0,
      inconclusive: 0,
      total: 3,
      markers: [
        {
          mutantId: "x_decode_token__mutmut_1",
          status: "killed",
          line: 44,
          before: null,
          after: null,
        },
        {
          mutantId: "x_decode_token__mutmut_2",
          status: "killed",
          line: 44,
          before: null,
          after: null,
        },
        {
          mutantId: "x_decode_token__mutmut_3",
          status: "killed",
          line: 45,
          before: null,
          after: null,
        },
      ],
      unattributed: 0,
      codeAvailable: true,
      spanStart: 42,
      spanEnd: 46,
      lines: DECODE_TOKEN_LINES,
      patchedLines: [44],
    },
  ],
  survivors: [],
  unattributedCounts: { killed: 0, survived: 0, inconclusive: 0 },
};

const SURVIVOR: MutationValidationReport = {
  ...clone(CLEAN),
  mutantSurvived: true,
  killedMutants: 2,
  survivedMutants: 1,
  totalMutants: 3,
  correctnessScore: 40,
  mutationScore: 0.67,
  functions: [
    {
      name: "decode_token",
      killed: 2,
      survived: 1,
      inconclusive: 0,
      total: 3,
      markers: [
        {
          mutantId: "x_decode_token__mutmut_1",
          status: "killed",
          line: 44,
          before: null,
          after: null,
        },
        {
          mutantId: "x_decode_token__mutmut_2",
          status: "survived",
          line: 44,
          before: 'if payload["exp"] < time.time():',
          after: 'if payload["exp"] <= time.time():',
        },
        {
          mutantId: "x_decode_token__mutmut_3",
          status: "killed",
          line: 45,
          before: null,
          after: null,
        },
      ],
      unattributed: 2,
      codeAvailable: true,
      spanStart: 42,
      spanEnd: 46,
      lines: DECODE_TOKEN_LINES,
      patchedLines: [44],
    },
  ],
  survivors: [
    {
      mutantId: "x_decode_token__mutmut_2",
      function: "decode_token",
      file: "backend/auth.py",
      line: 44,
      before: 'if payload["exp"] < time.time():',
      after: 'if payload["exp"] <= time.time():',
    },
  ],
};

const INCONCLUSIVE: MutationValidationReport = {
  ...clone(CLEAN),
  killedMutants: 2,
  survivedMutants: 0,
  inconclusiveMutants: 1,
  totalMutants: 3,
  functions: [
    {
      name: "decode_token",
      killed: 2,
      survived: 0,
      inconclusive: 1,
      total: 3,
      markers: [
        {
          mutantId: "x_decode_token__mutmut_1",
          status: "killed",
          line: 44,
          before: null,
          after: null,
        },
        {
          mutantId: "x_decode_token__mutmut_2",
          status: "inconclusive",
          line: 44,
          before: null,
          after: null,
        },
        {
          mutantId: "x_decode_token__mutmut_3",
          status: "killed",
          line: 45,
          before: null,
          after: null,
        },
      ],
      unattributed: 0,
      codeAvailable: true,
      spanStart: 42,
      spanEnd: 46,
      lines: DECODE_TOKEN_LINES,
      patchedLines: [44],
    },
  ],
};

const NO_LINE_ATTRIBUTION: MutationValidationReport = {
  ...clone(CLEAN),
  functions: [
    {
      name: "decode_token",
      killed: 12,
      survived: 2,
      inconclusive: 0,
      total: 14,
      markers: [],
      unattributed: 14,
      codeAvailable: false,
      spanStart: null,
      spanEnd: null,
      lines: [],
      patchedLines: [],
    },
  ],
};

const TARGET_TEST_FAILED: MutationValidationReport = {
  ...clone(CLEAN),
  stage: "target_test",
  pytestPassed: false,
  targetTestPassed: false,
  regressionTestsPassed: null,
  mutationStatus: "not_run",
  mutationScore: null,
  mutantSurvived: null,
  killedMutants: null,
  survivedMutants: null,
  totalMutants: null,
  inconclusiveMutants: null,
  correctnessScore: 0,
  patchRetryRequired: true,
  functions: [],
};

const UNAVAILABLE: MutationValidationReport = {
  ...clone(CLEAN),
  mutationStatus: "unavailable",
  unavailableReason: "no mutants produced a conclusive result (3 inconclusive, 3 recorded)",
  mutationScore: null,
  mutantSurvived: null,
  killedMutants: 0,
  survivedMutants: 0,
  totalMutants: 0,
  inconclusiveMutants: 3,
  correctnessScore: 70,
  functions: [],
};

const REGRESSION: MutationValidationReport = {
  ...clone(CLEAN),
  stage: "regression",
  regressionTestsPassed: false,
  newFailures: ["tests/test_billing.py::test_refund"],
  preExistingFailures: ["tests/test_legacy.py::test_flaky"],
  mutationStatus: "not_run",
  mutationScore: null,
  mutantSurvived: null,
  killedMutants: null,
  survivedMutants: null,
  totalMutants: null,
  inconclusiveMutants: null,
  correctnessScore: 0,
  patchRetryRequired: true,
  functions: [],
};

beforeEach(() => {
  getMutationValidation.mockReset();
});

describe("MutationValidationPanel", () => {
  it("shows the subtitle and all three compact gates", async () => {
    getMutationValidation.mockResolvedValue(clone(CLEAN));
    render(<MutationValidationPanel runId="run-1" />);

    expect(
      await screen.findByText(/did the test suite notice when we deliberately broke the patch/i),
    ).toBeTruthy();
    expect(screen.getByText("① Target test")).toBeTruthy();
    expect(screen.getByText("② Regression")).toBeTruthy();
    expect(screen.getByText("③ Sabotage")).toBeTruthy();
  });

  it("renders the real patched code with a left-edge patched-line indicator", async () => {
    getMutationValidation.mockResolvedValue(clone(CLEAN));
    const { container } = render(<MutationValidationPanel runId="run-1" />);

    await screen.findByText("44");
    expect(container.textContent).toMatch(/payload = jwt\.decode/);
    expect(container.querySelector(".border-l-status-running\\/60")).toBeTruthy();
  });

  it("does not surface survivor evidence when no mutant survived", async () => {
    getMutationValidation.mockResolvedValue(clone(CLEAN));
    render(<MutationValidationPanel runId="run-1" />);

    await screen.findByText("44");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows survivor evidence above the code and expands the real diff on click, never before", async () => {
    getMutationValidation.mockResolvedValue(clone(SURVIVOR));
    render(<MutationValidationPanel runId="run-1" />);

    const banner = await screen.findByRole("alert");
    expect(within(banner).getByText(/1 mutant survived/i)).toBeTruthy();

    // Not expanded until clicked.
    expect(screen.queryByText("< → <=")).toBeNull();
    expect(
      screen.queryByText(/the suite passed even after this behaviour was changed/i),
    ).toBeNull();

    const survivorDots = await screen.findAllByTitle(/no test detected this mutation/i);
    await userEvent.click(survivorDots[0]);

    expect(await screen.findByText("< → <=")).toBeTruthy();
    expect(
      screen.getByText(/the suite passed even after this behaviour was changed/i),
    ).toBeTruthy();
    expect(screen.getByText(/no test in the suite detected this change/i)).toBeTruthy();
  });

  it("never names a specific test as having caught or missed a mutant", async () => {
    getMutationValidation.mockResolvedValue(clone(SURVIVOR));
    const { container } = render(<MutationValidationPanel runId="run-1" />);

    const dots = await screen.findAllByTitle(/no test detected this mutation/i);
    await userEvent.click(dots[0]);
    await screen.findByText("< → <=");
    expect(container.textContent).not.toMatch(/test_expired_token_rejected/i);
  });

  it("expands a killed mutant's compact card on click, without claiming which test caught it", async () => {
    getMutationValidation.mockResolvedValue(clone(CLEAN));
    render(<MutationValidationPanel runId="run-1" />);

    await screen.findByText("44");
    expect(screen.queryByText(/a test in the suite detected this mutation/i)).toBeNull();

    const killedDots = screen.getAllByTitle(/a test detected this mutation/i);
    await userEvent.click(killedDots[0]);

    expect(await screen.findByText(/a test in the suite detected this mutation/i)).toBeTruthy();
    expect(screen.getByText(/does not report which specific test/i)).toBeTruthy();
  });

  it("expands an inconclusive mutant with the exact required copy", async () => {
    getMutationValidation.mockResolvedValue(clone(INCONCLUSIVE));
    render(<MutationValidationPanel runId="run-1" />);

    await screen.findByText("44");
    const inconclusiveDots = screen.getAllByTitle(/excluded from the mutation score/i);
    await userEvent.click(inconclusiveDots[0]);

    expect(await screen.findByText("INCONCLUSIVE")).toBeTruthy();
    expect(screen.getByText(/no test result was available for this mutant/i)).toBeTruthy();
    expect(screen.getByText(/this mutant is excluded from the mutation score/i)).toBeTruthy();
  });

  it("clicking a population-strip dot expands the same mutant as its gutter dot", async () => {
    getMutationValidation.mockResolvedValue(clone(SURVIVOR));
    render(<MutationValidationPanel runId="run-1" />);

    await screen.findByText(/mutants spawned/i);
    const dots = screen.getAllByTitle(/no test detected this mutation/i);
    // One in the gutter, one in the population strip.
    expect(dots.length).toBe(2);

    await userEvent.click(dots[1]);
    expect(await screen.findByText("< → <=")).toBeTruthy();
  });

  it("degrades honestly to function-level density when line attribution is unavailable", async () => {
    getMutationValidation.mockResolvedValue(clone(NO_LINE_ATTRIBUTION));
    render(<MutationValidationPanel runId="run-1" />);

    expect(
      await screen.findByText(/line attribution unavailable — mutants mapped to function/i),
    ).toBeTruthy();
    // Appears both in the fallback battle-map block and the Test Armor nav.
    expect(screen.getAllByText("decode_token()").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("42")).toBeNull();
  });

  it("stops the gauntlet wire at the target test, never showing a downstream pass", async () => {
    getMutationValidation.mockResolvedValue(clone(TARGET_TEST_FAILED));
    render(<MutationValidationPanel runId="run-1" />);

    await screen.findByText("① Target test");
    expect(screen.getByText("FAIL")).toBeTruthy();
  });

  it("shows NOT MEASURED rather than 0% when mutation testing was unavailable, keeping file identity visible", async () => {
    getMutationValidation.mockResolvedValue(clone(UNAVAILABLE));
    render(<MutationValidationPanel runId="run-1" />);

    expect(await screen.findByText("MUTATION NOT MEASURED")).toBeTruthy();
    expect(
      screen.getByText(/no conclusion can be drawn about test-suite resistance/i),
    ).toBeTruthy();
    expect(screen.queryByText(/^0%$/)).toBeNull();
    // No fabricated mutant dots when unmeasured.
    expect(screen.queryByRole("button", { name: "" })).toBeNull();
  });

  it("expanding the regression gate reveals new failures separated from pre-existing ones", async () => {
    getMutationValidation.mockResolvedValue(clone(REGRESSION));
    render(<MutationValidationPanel runId="run-1" />);

    await screen.findByText("② Regression");
    expect(screen.queryByText("tests/test_billing.py::test_refund")).toBeNull();

    await userEvent.click(screen.getByText("② Regression"));

    const newFailure = await screen.findByText("tests/test_billing.py::test_refund");
    const preExisting = screen.getByText("tests/test_legacy.py::test_flaky");
    expect(newFailure.className).toMatch(/text-status-failed/);
    expect(preExisting.className).toMatch(/line-through/);
  });

  it("shows the plain-English verdict sentence, not merely a percentage", async () => {
    getMutationValidation.mockResolvedValue(clone(SURVIVOR));
    render(<MutationValidationPanel runId="run-1" />);

    expect(
      await screen.findByText(
        /tests detect most behavioural changes, but 1 mutation can pass unnoticed/i,
      ),
    ).toBeTruthy();
  });

  it("keeps the mutation score and correctness gate as demoted technical metadata, not the headline", async () => {
    getMutationValidation.mockResolvedValue(clone(SURVIVOR));
    render(<MutationValidationPanel runId="run-1" />);

    await screen.findByText(/test suite defense/i);
    expect(screen.queryByText(/67%/)).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /technical metadata/i }));
    expect(await screen.findByText("40 / 80")).toBeTruthy();
  });

  it("reports a load failure and retries on demand", async () => {
    getMutationValidation.mockRejectedValueOnce(new Error("API 500: Internal Server Error"));
    getMutationValidation.mockResolvedValueOnce(clone(CLEAN));
    render(<MutationValidationPanel runId="run-1" />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText(/could not load mutation validation/i)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByText("① Target test")).toBeTruthy();
  });

  it("distinguishes A8 pending from A8 running", async () => {
    getMutationValidation.mockResolvedValue(null);
    const { unmount } = render(<MutationValidationPanel runId="run-1" />);
    expect(await screen.findByText(/mutation validation pending/i)).toBeTruthy();
    unmount();

    getMutationValidation.mockResolvedValue(null);
    render(<MutationValidationPanel runId="run-2" status="running" />);
    expect(await screen.findByText(/a8 is validating/i)).toBeTruthy();
  });
});
