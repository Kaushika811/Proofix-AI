// @vitest-environment jsdom
/**
 * A9's Security Re-scan panel, as a reconciliation ledger.
 *
 * Every assertion traces to a field on `GET /api/runs/{runId}/security`. The
 * point of this suite is to catch the panel telling a story the backend did
 * not: a severity band, a "safe" verdict for a scan that never measured
 * anything, a carried count for a run whose pairing was never recorded, or a
 * verdict bar that disagrees with the ledger it sits under.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getSecurityRescan = vi.fn();

vi.mock("@/lib/runService", () => ({
  getSecurityRescan: (...a: unknown[]) => getSecurityRescan(...a),
}));

const { SecurityRescanPanel } = await import("./SecurityRescanPanel");
import type {
  ReconciliationKey,
  ReconciliationLane,
  SecurityRescanReport,
} from "./securityRescanTypes";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

const HARDCODED = "Possible hardcoded password: 'hunter2'";

function report(overrides: Partial<SecurityRescanReport>): SecurityRescanReport {
  return {
    verdict: "clean",
    rejected: false,
    securityScore: 100,
    newFindings: [],
    reconciliation: [],
    reconciliationAvailable: true,
    scannersRun: ["bandit", "semgrep"],
    reexecutionCommand: "bandit -f json -q -r vulnapi",
    reexecutionTimeoutSeconds: 60,
    retryContext: null,
    ...overrides,
  };
}

function lane(tool: string, compared: boolean, keys: ReconciliationKey[] = []): ReconciliationLane {
  return { tool, compared, keys };
}

/** One key, one baseline occurrence, one post occurrence, paired. */
function shiftedKey(id: string, file: string, from: number, to: number): ReconciliationKey {
  return {
    id,
    tool: "bandit",
    file,
    message: HARDCODED,
    baseline: [{ id: `b-${id}-0`, line: from }],
    post: [{ id: `p-${id}-0`, line: to }],
    rails: [{ baselineId: `b-${id}-0`, postId: `p-${id}-0`, lineDelta: to - from }],
    introducedIds: [],
    resolvedIds: [],
  };
}

/** Baseline had one; the post scan has two. One rail, one dangling chip. */
const MULTIPLICITY_KEY: ReconciliationKey = {
  id: "bandit-0",
  tool: "bandit",
  file: "vulnapi/auth.py",
  message: HARDCODED,
  baseline: [{ id: "b-bandit-0-0", line: 12 }],
  post: [
    { id: "p-bandit-0-0", line: 12 },
    { id: "p-bandit-0-1", line: 40 },
  ],
  rails: [{ baselineId: "b-bandit-0-0", postId: "p-bandit-0-0", lineDelta: 0 }],
  introducedIds: ["p-bandit-0-1"],
  resolvedIds: [],
};

const SHIFTED = report({
  reconciliation: [
    lane("bandit", true, [shiftedKey("bandit-0", "vulnapi/auth.py", 12, 14)]),
    lane("semgrep", false),
  ],
  scannersRun: ["bandit"],
});

const REJECTED = report({
  verdict: "new_findings",
  rejected: true,
  securityScore: 75,
  newFindings: [
    {
      id: "new-0",
      file: "vulnapi/auth.py",
      line: 40,
      message: HARDCODED,
      tools: ["bandit"],
    },
  ],
  reconciliation: [lane("bandit", true, [MULTIPLICITY_KEY]), lane("semgrep", true)],
  retryContext: {
    assertionMessage: `New security finding: ${HARDCODED}`,
    securityConstraint: `must not introduce ${HARDCODED} near vulnapi/auth.py:40`,
  },
});

const NOT_MEASURED = report({
  verdict: "not_measured",
  securityScore: null,
  reconciliation: [lane("bandit", false), lane("semgrep", false)],
  scannersRun: [],
});

const LEGACY = report({
  verdict: "new_findings",
  rejected: true,
  securityScore: 75,
  newFindings: [
    { id: "new-0", file: "vulnapi/auth.py", line: 40, message: HARDCODED, tools: ["bandit"] },
  ],
  reconciliation: [lane("bandit", true), lane("semgrep", true)],
  reconciliationAvailable: false,
});

/** 70 carried pairs in one file, plus one introduced finding in another. */
function largeReport(): SecurityRescanReport {
  const bulk = Array.from({ length: 70 }, (_, i) =>
    shiftedKey(`bulk-${i}`, "pkg/bulk.py", 10 + i * 3, 12 + i * 3),
  );
  const introduced: ReconciliationKey = {
    id: "rare-0",
    tool: "bandit",
    file: "pkg/rare.py",
    message: "Use of assert detected.",
    baseline: [],
    post: [{ id: "p-rare-0-0", line: 9 }],
    rails: [],
    introducedIds: ["p-rare-0-0"],
    resolvedIds: [],
  };
  return report({
    verdict: "new_findings",
    rejected: true,
    securityScore: 75,
    reconciliation: [lane("bandit", true, [...bulk, introduced]), lane("semgrep", true)],
  });
}

beforeEach(() => {
  getSecurityRescan.mockReset();
});

describe("SecurityRescanPanel — the ledger", () => {
  it("draws a carried pair with the line delta the patch moved it by", async () => {
    getSecurityRescan.mockResolvedValue(clone(SHIFTED));
    render(<SecurityRescanPanel runId="run-1" />);

    await screen.findAllByText("vulnapi/auth.py");
    // The shift is shown being absorbed, not asserted to have been.
    expect(screen.getByText("+2")).toBeTruthy();
    expect(screen.getByLabelText(/baseline bandit finding.*line 12.*carried/i)).toBeTruthy();
    expect(
      screen.getByLabelText(/post-patch bandit finding.*line 14.*moved \+2 lines/i),
    ).toBeTruthy();
    expect(screen.queryByLabelText(/introduced/i)).toBeNull();
  });

  it("renders multiplicity as one rail and one dangling chip", async () => {
    getSecurityRescan.mockResolvedValue(clone(REJECTED));
    const { container } = render(<SecurityRescanPanel runId="run-1" />);

    await screen.findAllByText("vulnapi/auth.py");
    // Two post-patch occurrences of one baseline occurrence: exactly one pairs.
    expect(container.querySelectorAll('[data-chip-kind="introduced"]').length).toBe(1);
    expect(screen.getByText("±0")).toBeTruthy();
    expect(
      screen.getByLabelText(/introduced bandit finding.*line 40.*no baseline counterpart/i),
    ).toBeTruthy();
  });

  it("renders a scanner that did not run as NOT COMPARED, never as an empty pass", async () => {
    getSecurityRescan.mockResolvedValue(clone(NOT_MEASURED));
    const { container } = render(<SecurityRescanPanel runId="run-1" />);

    await screen.findAllByText(/not compared — scanner did not run/i);
    expect(container.querySelectorAll('[data-compared="false"]').length).toBe(2);
    expect(container.querySelectorAll('[data-compared="true"]').length).toBe(0);
  });

  it("never renders a ruff lane — A9 excludes ruff as style, not security", async () => {
    getSecurityRescan.mockResolvedValue(clone(REJECTED));
    const { container } = render(<SecurityRescanPanel runId="run-1" />);

    await screen.findAllByText("vulnapi/auth.py");
    expect(container.textContent).not.toMatch(/ruff/i);
    expect(container.querySelectorAll("[data-lane]").length).toBe(2);
  });

  it("encodes only matched-vs-dangling and lane — never a severity grade", async () => {
    getSecurityRescan.mockResolvedValue(clone(REJECTED));
    const { container } = render(<SecurityRescanPanel runId="run-1" />);

    await screen.findAllByText("vulnapi/auth.py");
    const kinds = new Set(
      [...container.querySelectorAll("[data-chip-kind]")].map((el) =>
        el.getAttribute("data-chip-kind"),
      ),
    );
    expect([...kinds].sort()).toEqual(["carried", "introduced"]);
    expect(container.textContent).not.toMatch(/severity/i);
    expect(container.textContent).not.toMatch(/\b(critical|high|medium|low)\s*\d/i);
    expect(container.textContent).not.toMatch(/cwe-/i);
  });
});

describe("SecurityRescanPanel — the introduced finding", () => {
  it("reveals why it has no rail, and the constraint carried into the retry", async () => {
    getSecurityRescan.mockResolvedValue(clone(REJECTED));
    render(<SecurityRescanPanel runId="run-1" />);

    const chip = await screen.findByLabelText(/introduced bandit finding/i);
    expect(screen.queryByRole("note")).toBeNull();

    await userEvent.click(chip);

    const note = screen.getByRole("note");
    expect(within(note).getByText(/no matching baseline occurrence exists/i)).toBeTruthy();
    expect(within(note).getByText(/constraint carried into the next repair attempt/i)).toBeTruthy();
    expect(within(note).getByText(new RegExp("must not introduce", "i"))).toBeTruthy();
  });

  it("is reachable and explains itself by keyboard, not hover alone", async () => {
    getSecurityRescan.mockResolvedValue(clone(REJECTED));
    render(<SecurityRescanPanel runId="run-1" />);

    const chip = await screen.findByLabelText(/introduced bandit finding/i);

    // A real focus target, not a hover-only affordance.
    expect(chip.tagName).toBe("BUTTON");
    fireEvent.focus(chip);
    expect(screen.getByRole("note")).toBeTruthy();

    fireEvent.blur(chip);
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("ends the expanded detail with the revision instruction and restates tool/file/line/message", async () => {
    getSecurityRescan.mockResolvedValue(clone(REJECTED));
    render(<SecurityRescanPanel runId="run-1" />);

    const chip = await screen.findByLabelText(/introduced bandit finding/i);
    await userEvent.click(chip);

    const note = screen.getByRole("note");
    expect(within(note).getByText("bandit")).toBeTruthy();
    expect(within(note).getByText("40")).toBeTruthy();
    expect(within(note).getByText("vulnapi/auth.py")).toBeTruthy();
    expect(within(note).getByText(HARDCODED)).toBeTruthy();
    expect(within(note).getByText(/patch requires revision/i)).toBeTruthy();
  });
});

describe("SecurityRescanPanel — the matched-pair explanation", () => {
  it("names the key and the line shift on hover, and says so on the card itself", async () => {
    getSecurityRescan.mockResolvedValue(clone(SHIFTED));
    render(<SecurityRescanPanel runId="run-1" />);

    const baselineCard = await screen.findByLabelText(/baseline bandit finding.*line 12.*carried/i);
    // The card restates the finding on its own — no shared header to trace back to.
    expect(within(baselineCard).getByText(HARDCODED)).toBeTruthy();
    expect(within(baselineCard).getByText("vulnapi/auth.py")).toBeTruthy();

    expect(screen.queryByText(/same finding/i)).toBeNull();
    await userEvent.hover(baselineCard);
    expect(screen.getByText(/same finding/i)).toBeTruthy();
    expect(screen.getByText(/line moved:/i)).toBeTruthy();
    expect(screen.getByText(/12 → 14/)).toBeTruthy();

    await userEvent.unhover(baselineCard);
    expect(screen.queryByText(/same finding/i)).toBeNull();
  });
});

describe("SecurityRescanPanel — the verdict bar", () => {
  it("leads with a headline derived from the same counts, not a separate decision", async () => {
    getSecurityRescan.mockResolvedValue(clone(SHIFTED));
    render(<SecurityRescanPanel runId="run-1" />);

    const bar = await screen.findByRole("status");
    expect(within(bar).getByText("Security verdict")).toBeTruthy();
    expect(within(bar).getByText("No new findings")).toBeTruthy();
  });

  it("counts carried and introduced off the ledger, and scanners for not-compared", async () => {
    getSecurityRescan.mockResolvedValue(clone(SHIFTED));
    render(<SecurityRescanPanel runId="run-1" />);

    const bar = await screen.findByRole("status");
    expect(within(bar).getByText("1 CARRIED")).toBeTruthy();
    expect(within(bar).getByText("0 INTRODUCED")).toBeTruthy();
    // Scanners, not findings — and labelled so.
    expect(within(bar).getByText(/1 SCANNER NOT\s+COMPARED/)).toBeTruthy();
    expect(
      within(bar).getByText(
        /no new findings — all comparable scanner findings were already present/i,
      ),
    ).toBeTruthy();
  });

  it("says the patch is rejected when a finding has no baseline counterpart", async () => {
    getSecurityRescan.mockResolvedValue(clone(REJECTED));
    render(<SecurityRescanPanel runId="run-1" />);

    const bar = await screen.findByRole("status");
    expect(within(bar).getByText("1 CARRIED")).toBeTruthy();
    expect(within(bar).getByText("1 INTRODUCED")).toBeTruthy();
    expect(
      within(bar).getByText(/patch introduces 1 finding not present in baseline/i),
    ).toBeTruthy();
    expect(within(bar).getAllByText(/patch rejected/i).length).toBeGreaterThan(0);
  });

  it("never reports a clean result when nothing was compared", async () => {
    getSecurityRescan.mockResolvedValue(clone(NOT_MEASURED));
    render(<SecurityRescanPanel runId="run-1" />);

    const bar = await screen.findByRole("status");
    expect(within(bar).getByText(/2 SCANNERS NOT\s+COMPARED/)).toBeTruthy();
    expect(within(bar).getByText(/nothing was compared — no scanner ran/i)).toBeTruthy();
    expect(within(bar).queryByText(/no new findings/i)).toBeNull();
  });

  it("keeps its counts when a filter hides part of the ledger", async () => {
    // Two identities: one purely carried, one that gained an occurrence.
    const mixed = clone(REJECTED);
    mixed.reconciliation[0].keys.push(shiftedKey("bandit-1", "vulnapi/db.py", 5, 8));
    getSecurityRescan.mockResolvedValue(mixed);
    render(<SecurityRescanPanel runId="run-1" />);

    await screen.findAllByText("vulnapi/db.py");
    await userEvent.selectOptions(screen.getByLabelText(/filter findings/i), "introduced");

    // The purely-carried identity is off screen; the count it contributed is
    // not. The remaining identity keeps its own pair — that pair is the
    // evidence for why its sibling counts as introduced.
    expect(screen.queryByText("vulnapi/db.py")).toBeNull();
    expect(screen.getByLabelText(/introduced bandit finding/i)).toBeTruthy();
    const bar = screen.getByRole("status");
    expect(within(bar).getByText("2 CARRIED")).toBeTruthy();
    expect(within(bar).getByText("1 INTRODUCED")).toBeTruthy();
  });
});

describe("SecurityRescanPanel — scale", () => {
  it("collapses the carried bulk but never the introduced finding", async () => {
    getSecurityRescan.mockResolvedValue(clone(largeReport()));
    const { container } = render(<SecurityRescanPanel runId="run-1" />);

    // The rare signal renders individually and expanded, first.
    expect(await screen.findByLabelText(/introduced bandit finding.*line 9/i)).toBeTruthy();
    // 70 carried pairs are not 140 chips in the DOM.
    expect(container.querySelectorAll('[data-chip-kind="carried"]').length).toBe(0);
    expect(screen.getByText(/70 carried$/)).toBeTruthy();

    const bar = screen.getByRole("status");
    expect(within(bar).getByText("70 CARRIED")).toBeTruthy();
    expect(within(bar).getByText("1 INTRODUCED")).toBeTruthy();
  });

  it("caps how much of a large section renders at once, on request", async () => {
    getSecurityRescan.mockResolvedValue(clone(largeReport()));
    const { container } = render(<SecurityRescanPanel runId="run-1" />);

    await screen.findByLabelText(/introduced bandit finding.*line 9/i);
    await userEvent.click(screen.getByText(/70 carried$/));
    await userEvent.click(screen.getByText(/70 carried, unchanged/i));

    // 40 keys × 2 chips, not 140.
    expect(container.querySelectorAll('[data-chip-kind="carried"]').length).toBe(80);
    expect(screen.getByText(/show 30 more/i)).toBeTruthy();
  });
});

describe("SecurityRescanPanel — honesty about what was recorded", () => {
  it("shows the security score as a secondary field, dashed when unmeasured", async () => {
    getSecurityRescan.mockResolvedValue(clone(NOT_MEASURED));
    render(<SecurityRescanPanel runId="run-1" />);

    expect(await screen.findByText(/— not measured/i)).toBeTruthy();
    expect(screen.getByText(/secondary to the map above/i)).toBeTruthy();
  });

  it("does not claim nothing carried for a run whose pairing was never stored", async () => {
    getSecurityRescan.mockResolvedValue(clone(LEGACY));
    render(<SecurityRescanPanel runId="run-1" />);

    expect(await screen.findByText(/reconciliation unavailable/i)).toBeTruthy();
    expect(
      screen.getByText(/baseline ↔ post-patch pairing was not recorded for this run/i),
    ).toBeTruthy();
    const bar = screen.getByRole("status");
    // An introduced finding is a hard fact A9 measured directly — it still
    // rejects the patch even though the carried side was never recorded.
    expect(within(bar).getAllByText(/patch rejected/i).length).toBeGreaterThan(0);
    expect(within(bar).getByText(/1 introduced finding/i)).toBeTruthy();
    expect(within(bar).getByText(/— CARRIED \(not recorded\)/)).toBeTruthy();
  });

  it("marks the comparison incomplete rather than clean when nothing was carried and pairing is unavailable", async () => {
    const noIntroduced = clone(LEGACY);
    noIntroduced.newFindings = [];
    noIntroduced.rejected = false;
    noIntroduced.verdict = "clean";
    getSecurityRescan.mockResolvedValue(noIntroduced);
    render(<SecurityRescanPanel runId="run-1" />);

    const bar = await screen.findByRole("status");
    expect(within(bar).getByText(/reconciliation incomplete/i)).toBeTruthy();
    expect(within(bar).queryByText(/no new findings/i)).toBeNull();
    expect(within(bar).getByText("Baseline pairing:")).toBeTruthy();
    expect(within(bar).getByText("not recorded")).toBeTruthy();
  });

  it("reports a load failure and retries on demand", async () => {
    getSecurityRescan.mockRejectedValueOnce(new Error("API 500: Internal Server Error"));
    getSecurityRescan.mockResolvedValueOnce(clone(SHIFTED));
    render(<SecurityRescanPanel runId="run-1" />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText(/could not load security re-scan/i)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByRole("status")).toBeTruthy();
    expect(screen.getByText("+2")).toBeTruthy();
  });

  it("distinguishes A9 pending from A9 running", async () => {
    getSecurityRescan.mockResolvedValue(null);
    const { unmount } = render(<SecurityRescanPanel runId="run-1" />);
    expect(await screen.findByText(/security rescan pending/i)).toBeTruthy();
    unmount();

    getSecurityRescan.mockResolvedValue(null);
    render(<SecurityRescanPanel runId="run-2" status="running" />);
    expect(await screen.findByText(/a9 is re-scanning/i)).toBeTruthy();
  });

  it("exposes re-execution and A10 handoff detail only behind progressive disclosure", async () => {
    getSecurityRescan.mockResolvedValue(clone(REJECTED));
    render(<SecurityRescanPanel runId="run-1" />);

    await screen.findAllByText("vulnapi/auth.py");
    expect(screen.queryByText("bandit -f json -q -r vulnapi")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /re-execution & retry detail/i }));
    expect(await screen.findByText("bandit -f json -q -r vulnapi")).toBeTruthy();
    expect(screen.getByText(/blocks auto-merge/i)).toBeTruthy();
    // A9 supplies inputs; the decision belongs to the merge card.
    expect(screen.queryByText(/draft pr/i)).toBeNull();
    expect(screen.queryByText(/auto.?mergeable/i)).toBeNull();
  });
});
