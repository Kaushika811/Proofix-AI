// @vitest-environment jsdom
/**
 * A5.5's Context Reduction Funnel. Every assertion traces to a specific field
 * on `ContextPackage` (`GET /api/runs/{runId}/context`) — the point of this
 * suite is to catch the panel inventing a count, a percentage, a signal, or
 * collapsing `privacy_guard_status: "failed"` into an empty/clean redaction
 * list.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ContextPackageModel } from "@/mocks/runReport";

const getRunContext = vi.fn();

vi.mock("@/lib/runService", () => ({
  getRunContext: (...a: unknown[]) => getRunContext(...a),
}));

const { ContextEngineeringPanel } = await import("./ContextEngineeringPanel");

const PACKAGE: ContextPackageModel = {
  target_file: "vulnapi/auth.py",
  target_function: "validate_token",
  acceptance_criteria: ["Expired tokens must be rejected"],
  patch_constraints: ["Do not reformat unrelated code"],
  contracts: [],
  validation_requirements: [],
  dependency_summary: ["vulnapi/auth.py (score 2.40, confidence 0.90): resolved patch target"],
  ranked_files: [
    {
      file: "vulnapi/auth.py",
      score: 2.4,
      reason: "resolved patch target",
      confidence: 0.9,
      signals: { target_resolution: 1.0, runtime_stack: 0.9, static_finding: 0 },
      is_target: true,
      evidence: ["resolved patch target (confidence 0.90)", "frame depth 0"],
    },
    {
      file: "vulnapi/middleware.py",
      score: 0.6,
      reason: "import distance",
      confidence: 0.4,
      signals: { import_distance: 0.2, git_churn: 0.1 },
      is_target: false,
      evidence: ["1 import hop(s) from an origin"],
    },
    {
      file: "vulnapi/config.py",
      score: 0.1,
      reason: "shared utility",
      confidence: 0.1,
      signals: { shared_utility_penalty: -0.4 },
      is_target: false,
      evidence: ["shared utility with 9 dependents and no runtime evidence"],
    },
  ],
  relevant_imports: ["import jwt", "from .config import ALGORITHMS"],
  relevant_classes: [],
  relevant_functions: [
    {
      name: "validate_token",
      qualname: "validate_token",
      file: "vulnapi/auth.py",
      kind: "target_function",
      lineno: 40,
      end_lineno: 58,
      source: "def validate_token(token):\n    ...",
      signature_only: false,
      reason: "the resolved target",
    },
  ],
  related_utilities: [
    {
      name: "decode_header",
      qualname: "decode_header",
      file: "vulnapi/middleware.py",
      kind: "caller_function",
      lineno: 10,
      end_lineno: 14,
      source: "def decode_header():\n    ...",
      signature_only: true,
      reason: "calls the target",
    },
  ],
  constants: [],
  redactions: [
    {
      file: "vulnapi/auth.py",
      line: 12,
      kind: "REDACTED_SECRET",
      detector: "structural",
      identifier: "SECRET_KEY",
    },
  ],
  privacy_guard_status: "masked",
  prefer_focused: true,
  metrics: {
    files_ranked: 14,
    files_extracted: 3,
    context_files: 2,
    context_functions: 6,
    context_lines: 142,
    original_tokens: 8000,
    reduced_tokens: 2000,
    estimated_saved_tokens: 6000,
    token_reduction: 0.75,
    privacy_redactions: 1,
    degraded: false,
    cache_hit: false,
    ranking_time_ms: 4,
    extraction_time_ms: 6,
    privacy_time_ms: 1,
    build_time_ms: 12,
    adoption_reason: "smaller than A7's own baseline extraction",
  },
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

beforeEach(() => {
  getRunContext.mockReset();
});

describe("ContextEngineeringPanel", () => {
  // 1. Real backend data renders.
  it("renders the real funnel counts from the backend package", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    expect(await screen.findByText("14")).toBeTruthy(); // candidates
    expect(screen.getByText("3")).toBeTruthy(); // extracted
    expect(screen.getByText("6 functions")).toBeTruthy(); // context functions
    expect(screen.getAllByText(/142 lines/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/validate_token/).length).toBeGreaterThan(0);
  });

  // 2. 404 renders Pending rather than zero values.
  it("renders Pending, not zeroes, when A5.5 has not published a package", async () => {
    getRunContext.mockResolvedValue(null);
    render(<ContextEngineeringPanel runId="run-1" />);

    expect(await screen.findByText(/PENDING — Context Engineering/)).toBeTruthy();
    expect(screen.queryByText("0")).toBeNull();
  });

  it("distinguishes A5.5 pending from A5.5 running", async () => {
    getRunContext.mockResolvedValue(null);
    render(<ContextEngineeringPanel runId="run-2" status="running" />);

    expect(await screen.findByText(/A5\.5 is engineering the repair context now/)).toBeTruthy();
  });

  // 3. Empty redactions + clean status.
  it("reports a clean guard with no redactions as genuinely clean", async () => {
    const clean = clone(PACKAGE);
    clean.privacy_guard_status = "clean";
    clean.redactions = [];
    clean.metrics.privacy_redactions = 0;
    getRunContext.mockResolvedValue(clean);
    render(<ContextEngineeringPanel runId="run-1" />);

    const privacyButton = await screen.findByText(/^clean$/i);
    expect(privacyButton).toBeTruthy();
    await userEvent.click(privacyButton.closest("button")!);
    expect(
      screen.getByText("No secrets detected in the context handed to the patch generator."),
    ).toBeTruthy();
  });

  // 4. Failed privacy status.
  it("never reports a failed guard as clean, even with an empty redaction list", async () => {
    const failed = clone(PACKAGE);
    failed.privacy_guard_status = "failed";
    failed.redactions = [];
    getRunContext.mockResolvedValue(failed);
    render(<ContextEngineeringPanel runId="run-1" />);

    const privacyButton = await screen.findByText(/^failed$/i);
    expect(privacyButton).toBeTruthy();
    expect(screen.getByText(/No safety claim can be made/)).toBeTruthy();
    await userEvent.click(privacyButton.closest("button")!);
    expect(
      screen.getByText(/no claim can be made about what this context contained/i),
    ).toBeTruthy();
    expect(screen.queryByText(/No secrets detected/)).toBeNull();
  });

  it("shows the real redaction ledger under a masked guard, never the secret value", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    const privacyButton = await screen.findByText(/^masked$/i);
    await userEvent.click(privacyButton.closest("button")!);
    expect(screen.getByText("vulnapi/auth.py:12")).toBeTruthy();
    expect(screen.getByText("SECRET_KEY")).toBeTruthy();
    expect(screen.getByText(/masked by structural/)).toBeTruthy();
  });

  // 5. prefer_focused = true.
  it("shows ADOPTED when prefer_focused is true", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    expect(await screen.findByText("✓ ADOPTED")).toBeTruthy();
    expect(screen.queryByText(/A7 keeps its own extraction/)).toBeNull();
  });

  // 6. prefer_focused = false.
  it("shows NOT ADOPTED with the real backend reason when prefer_focused is false", async () => {
    const notAdopted = clone(PACKAGE);
    notAdopted.prefer_focused = false;
    notAdopted.metrics.adoption_reason =
      "no smaller than A7's own baseline extraction, and found nothing to mask";
    getRunContext.mockResolvedValue(notAdopted);
    render(<ContextEngineeringPanel runId="run-1" />);

    expect(await screen.findByText("✕ NOT ADOPTED")).toBeTruthy();
    expect(screen.getByText(/Reason: no smaller than A7's own baseline/)).toBeTruthy();
  });

  it("never invents an adoption reason when the backend sent none", async () => {
    const notAdopted = clone(PACKAGE);
    notAdopted.prefer_focused = false;
    notAdopted.metrics.adoption_reason = "";
    getRunContext.mockResolvedValue(notAdopted);
    render(<ContextEngineeringPanel runId="run-1" />);

    expect(await screen.findByText("✕ NOT ADOPTED")).toBeTruthy();
    expect(screen.queryByText(/A7 keeps its own extraction —/)).toBeNull();
  });

  // 7. Ranked files render from backend.
  it("renders every ranked file in the relevance map, sorted as the backend sent them", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await userEvent.click((await screen.findByText("3")).closest("button")!); // EXTRACTED stat expands it
    const map = await screen.findByText(/Context Map \(3 candidates scored\)/);
    const container = map.parentElement!;
    expect(within(container).getByText("vulnapi/auth.py")).toBeTruthy();
    expect(within(container).getByText("vulnapi/middleware.py")).toBeTruthy();
    expect(within(container).getByText("vulnapi/config.py")).toBeTruthy();
    expect(within(container).getByText("target")).toBeTruthy();
  });

  // 8. Excluded files are derived from ranked_files - file_order (via related_utilities/relevant_*).
  it("marks a ranked file with no extraction as excluded, never in context", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await userEvent.click((await screen.findByText("3")).closest("button")!);
    const map = (await screen.findByText(/Context Map/)).parentElement!;

    const configRow = within(map).getByText("vulnapi/config.py").closest("div")!;
    expect(within(configRow).getByText(/excluded/)).toBeTruthy();

    const authRow = within(map).getByText("vulnapi/auth.py").closest("div")!;
    expect(within(authRow).getByText(/in context/)).toBeTruthy();
  });

  // 9. Signal icons only appear for real nonzero signals.
  it("shows a signal glyph only for nonzero signals, never for a zero-valued one", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await userEvent.click((await screen.findByText("3")).closest("button")!);
    const map = (await screen.findByText(/Context Map/)).parentElement!;
    const authButton = within(map).getByText("vulnapi/auth.py").closest("button")!;
    await userEvent.click(authButton);

    // target_resolution and runtime_stack are nonzero; static_finding is 0 and must not appear.
    expect(within(map).getByText("Resolved patch target")).toBeTruthy();
    expect(within(map).getByText("Runtime stack frame")).toBeTruthy();
    expect(within(map).queryByText("Static finding")).toBeNull();
  });

  it("shows the real evidence strings and file confidence on expansion", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await userEvent.click((await screen.findByText("3")).closest("button")!);
    const map = (await screen.findByText(/Context Map/)).parentElement!;
    const authButton = within(map).getByText("vulnapi/auth.py").closest("button")!;
    await userEvent.click(authButton);

    expect(within(map).getByText("resolved patch target (confidence 0.90)")).toBeTruthy();
    expect(within(map).getByText("0.90")).toBeTruthy();
  });

  // 10. Extracted source only appears for files actually in context.
  it("shows extracted source only for a file actually in context", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await userEvent.click((await screen.findByText("3")).closest("button")!);
    const map = (await screen.findByText(/Context Map/)).parentElement!;

    const authButton = within(map).getByText("vulnapi/auth.py").closest("button")!;
    await userEvent.click(authButton);
    expect(within(map).getByText(/def validate_token\(token\):/)).toBeTruthy();

    const configRow = within(map).getByText("vulnapi/config.py").closest("div")!;
    const configButton = within(configRow).getByRole("button");
    await userEvent.click(configButton);
    expect(within(configRow).queryByText("Extracted source")).toBeNull();
  });

  // 12. No hardcoded repository/file names — the panel must render whatever the backend sent.
  it("renders a differently-named repository's file without any hardcoded name leaking through", async () => {
    const other = clone(PACKAGE);
    other.target_file = "src/otherrepo/handlers.py";
    other.target_function = "handle_request";
    other.ranked_files = [
      {
        file: "src/otherrepo/handlers.py",
        score: 1.0,
        reason: "resolved patch target",
        confidence: 0.5,
        signals: { target_resolution: 1.0 },
        is_target: true,
        evidence: [],
      },
    ];
    other.relevant_functions = [];
    other.related_utilities = [];
    other.relevant_imports = [];
    other.constants = [];
    getRunContext.mockResolvedValue(other);
    render(<ContextEngineeringPanel runId="run-1" />);

    expect(await screen.findByText("src/otherrepo/handlers.py")).toBeTruthy();
    expect(screen.queryByText("vulnapi/auth.py")).toBeNull();
    expect(screen.queryByText("auth.py")).toBeNull();
  });

  // 13. No fabricated percentages/confidence in the funnel.
  it("widths the Context band by a real ratio against candidates, never a fabricated one", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    const { container } = render(<ContextEngineeringPanel runId="run-1" />);

    await screen.findByText("6 functions");
    // 14 candidates, 2 context files: 6 + 94*(2/14) ≈ 19.4%.
    const contextRow = screen.getByText("6 functions").closest(".funnel-row")!;
    const fill = contextRow.querySelector(".funnel-fill") as HTMLElement;
    const width = parseFloat(fill.style.width);
    expect(width).toBeGreaterThan(10);
    expect(width).toBeLessThan(30);
    expect(container.textContent).not.toMatch(/\d+%\s*safe/i);
  });

  it("labels token figures as estimated, never as an exact measurement", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await userEvent.click(await screen.findByText(/Token & performance details/i));
    expect(screen.getByText(/token counts are estimated/i)).toBeTruthy();
  });

  // 14. Expand/collapse interactions.
  it("keeps the default screen collapsed until the user clicks", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await screen.findByText("14");
    expect(screen.queryByText(/Context Map/)).toBeNull();
    expect(screen.queryByText("SECRET_KEY")).toBeNull();
    expect(screen.queryByText("Expired tokens must be rejected")).toBeNull();
  });

  it("expands and collapses the relevance map on repeated clicks", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    const toggle = (await screen.findByText("3")).closest("button")!;
    await userEvent.click(toggle);
    expect(screen.getByText(/Context Map/)).toBeTruthy();
    await userEvent.click(toggle);
    expect(screen.queryByText(/Context Map/)).toBeNull();
  });

  it("expands constraints carried forward on click", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await screen.findByText("14");
    expect(screen.queryByText("Expired tokens must be rejected")).toBeNull();
    await userEvent.click(screen.getByText(/Constraints carried forward/i));
    expect(screen.getByText("Expired tokens must be rejected")).toBeTruthy();
    expect(screen.getByText("Do not reformat unrelated code")).toBeTruthy();
  });

  it("reports a load failure and retries on demand", async () => {
    getRunContext.mockRejectedValueOnce(new Error("API 500: Internal Server Error"));
    getRunContext.mockResolvedValueOnce(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect((await screen.findAllByText("vulnapi/auth.py")).length).toBeGreaterThan(0);
  });

  // -- refinement pass: connected funnel + real relevance-map scores --

  it("renders three connected funnel rows, plus arrows to privacy and adoption", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    const { container } = render(<ContextEngineeringPanel runId="run-1" />);

    await screen.findByText("14");
    expect(container.querySelectorAll(".funnel-row").length).toBe(3);
    // Two flow connectors: funnel → privacy, privacy → adoption.
    expect(container.querySelectorAll(".flow-connector").length).toBe(2);
  });

  it("shows the real, un-normalized score next to each relevance-map bar", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await userEvent.click((await screen.findByText("3")).closest("button")!);
    const map = (await screen.findByText(/Context Map/)).parentElement!;

    expect(within(map).getByText("2.40")).toBeTruthy();
    expect(within(map).getByText("0.60")).toBeTruthy();
    expect(within(map).getByText("0.10")).toBeTruthy();
  });

  it("always states what adoption means, not only when it fails", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    expect(
      await screen.findByText(/A7 uses this package instead of its own extraction/),
    ).toBeTruthy();
  });

  it("labels performance details as token reduction, distinct from narrowing", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await userEvent.click(await screen.findByText(/Token & performance details/i));
    expect(screen.getByText("Token reduction (estimated)")).toBeTruthy();
  });

  // -- second refinement pass: real file/function chips, no candidates bar --

  it("shows the real selected files as full repository-relative path chips, without a click", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    // `auth.py` is the target file (relevant_imports/functions non-empty);
    // `middleware.py` contributes a symbol via related_utilities; both are
    // real in-context files. `config.py` never contributed an extraction and
    // must not appear as a selected-file chip. Full paths, never a basename
    // that could name any file in the tree — a tooltip carries the same
    // string for the (here, already-short) truncated case.
    const authMatches = await screen.findAllByText("vulnapi/auth.py");
    const authChip = authMatches.find((el) => el.title === "vulnapi/auth.py")!;
    expect(authChip).toBeTruthy();
    expect(screen.getAllByText("vulnapi/middleware.py").length).toBeGreaterThan(0);
    expect(screen.queryByText("vulnapi/config.py")).toBeNull();
    expect(screen.queryByText("auth.py")).toBeNull();
  });

  it("shows the real selected function names as chips, with () only for callables", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    expect(await screen.findByText("validate_token()")).toBeTruthy();
    expect(screen.getByText("decode_header()")).toBeTruthy();
  });

  it("caps file chips with a real overflow count, never an unbounded list", async () => {
    const many = clone(PACKAGE);
    many.related_utilities = Array.from({ length: 8 }, (_, i) => ({
      name: `helper_${i}`,
      qualname: `helper_${i}`,
      file: `vulnapi/extra_${i}.py`,
      kind: "caller_function",
      lineno: 1,
      end_lineno: 2,
      source: "",
      signature_only: true,
      reason: "calls the target",
    }));
    getRunContext.mockResolvedValue(many);
    render(<ContextEngineeringPanel runId="run-1" />);

    expect((await screen.findAllByText(/\+\d+ more/)).length).toBeGreaterThan(0);
  });

  it("fills the Candidates row completely — it is the funnel's own base", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    const candidatesRow = (await screen.findByText("14")).closest(".funnel-row")!;
    const fill = candidatesRow.querySelector(".funnel-fill") as HTMLElement;
    expect(fill.style.width).toBe("100%");
  });

  it("uses differentiated icons for each funnel stage, not the same glyph three times", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    const { container } = render(<ContextEngineeringPanel runId="run-1" />);

    await screen.findByText("14");
    expect(container.querySelector(".lucide-files")).toBeTruthy();
    // lucide-react ships "Filter" as a deprecated alias of its "funnel" icon.
    expect(container.querySelector(".lucide-funnel")).toBeTruthy();
    expect(container.querySelector(".lucide-braces")).toBeTruthy();
  });

  it("shows no filename/function chips when nothing was extracted", async () => {
    const empty = clone(PACKAGE);
    empty.relevant_imports = [];
    empty.relevant_classes = [];
    empty.relevant_functions = [];
    empty.related_utilities = [];
    empty.constants = [];
    getRunContext.mockResolvedValue(empty);
    render(<ContextEngineeringPanel runId="run-1" />);

    await screen.findByText("14");
    expect(screen.queryByText("auth.py")).toBeNull();
    // The header's own `target_function` field is a separate, always-real
    // fact and still renders; only the callable-chip form must be absent.
    expect(screen.queryByText("validate_token()")).toBeNull();
  });

  // -- selected-context hierarchy: real counts, typed symbols, full paths --

  it("shows a real 'N selected files' heading above the file chips", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    // auth.py (target) + middleware.py (related_utilities) = 2 real files.
    expect(await screen.findByText("2 selected files")).toBeTruthy();
  });

  it("types each selected symbol with its real category glyph, not an untyped list", async () => {
    const withClass = clone(PACKAGE);
    withClass.relevant_classes = [
      {
        name: "TokenValidator",
        qualname: "TokenValidator",
        file: "vulnapi/auth.py",
        kind: "class_definition",
        lineno: 5,
        end_lineno: 8,
        source: "class TokenValidator:\n    ...",
        signature_only: false,
        reason: "supporting type",
      },
    ];
    getRunContext.mockResolvedValue(withClass);
    render(<ContextEngineeringPanel runId="run-1" />);

    // validate_token (relevant_functions) + decode_header (related_utilities,
    // kind "caller_function") are both real functions; TokenValidator is the
    // one class.
    expect(await screen.findByText(/2 Functions/)).toBeTruthy();
    expect(screen.getByText(/1 Class/)).toBeTruthy();
    expect(screen.queryByText(/\d+ Other/)).toBeNull();

    const functionChip = screen.getByTitle("Function: validate_token");
    expect(within(functionChip).getByText("validate_token()")).toBeTruthy();
    const classChip = screen.getByTitle("Class: TokenValidator");
    expect(within(classChip).getByText("TokenValidator")).toBeTruthy();
    // A class is never given the callable `()` suffix.
    expect(within(classChip).queryByText("TokenValidator()")).toBeNull();
  });

  it("buckets a config-shaped kind as Other, never guessed into Function or Class", async () => {
    const withConfig = clone(PACKAGE);
    withConfig.related_utilities = [
      {
        name: "SETTINGS",
        qualname: "SETTINGS",
        file: "vulnapi/middleware.py",
        kind: "config_object",
        lineno: 1,
        end_lineno: 1,
        source: "SETTINGS = {}",
        signature_only: false,
        reason: "referenced by the target",
      },
    ];
    getRunContext.mockResolvedValue(withConfig);
    render(<ContextEngineeringPanel runId="run-1" />);

    expect(await screen.findByText(/1 Function/)).toBeTruthy(); // validate_token
    expect(screen.getByText(/1 Other/)).toBeTruthy(); // SETTINGS
    expect(screen.queryByText(/\d+ Class/)).toBeNull();
  });

  // -- Context Map: real "why selected" badges and lines-selected ----------

  it("shows only the reason badges backed by a real nonzero signal", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await userEvent.click((await screen.findByText("3")).closest("button")!);
    const map = (await screen.findByText(/Context Map/)).parentElement!;
    const authButton = within(map).getByText("vulnapi/auth.py").closest("button")!;
    await userEvent.click(authButton);

    // auth.py has target_resolution and runtime_stack signals — Target and
    // Runtime evidence badges — but no dependency-group signal.
    expect(within(map).getByText("Target")).toBeTruthy();
    expect(within(map).getByText("Runtime evidence")).toBeTruthy();
    expect(within(map).queryByText("Dependency")).toBeNull();
  });

  it("shows the dependency badge for a file whose only signal is graph distance", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await userEvent.click((await screen.findByText("3")).closest("button")!);
    const map = (await screen.findByText(/Context Map/)).parentElement!;
    const middlewareButton = within(map).getByText("vulnapi/middleware.py").closest("button")!;
    await userEvent.click(middlewareButton);

    // middleware.py's only nonzero signal is import_distance (dependency group).
    expect(within(map).getByText("Dependency")).toBeTruthy();
    expect(within(map).queryByText("Target")).toBeNull();
    expect(within(map).queryByText("Runtime evidence")).toBeNull();
  });

  it("shows real lines-selected for an in-context file, counted from full-body symbols only", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await userEvent.click((await screen.findByText("3")).closest("button")!);
    const map = (await screen.findByText(/Context Map/)).parentElement!;
    const authButton = within(map).getByText("vulnapi/auth.py").closest("button")!;
    await userEvent.click(authButton);

    // validate_token spans lineno 40 - end_lineno 58 inclusive = 19 lines.
    // decode_header is signature_only and contributes no body lines, but it
    // belongs to middleware.py, not auth.py, so it would not count here anyway.
    expect(within(map).getByText("19")).toBeTruthy();
    expect(within(map).getByText(/lines selected/)).toBeTruthy();
  });

  it("does not show lines-selected for an excluded file", async () => {
    getRunContext.mockResolvedValue(clone(PACKAGE));
    render(<ContextEngineeringPanel runId="run-1" />);

    await userEvent.click((await screen.findByText("3")).closest("button")!);
    const map = (await screen.findByText(/Context Map/)).parentElement!;
    const configButton = within(map).getByText("vulnapi/config.py").closest("button")!;
    await userEvent.click(configButton);

    expect(within(map).queryByText(/lines selected/)).toBeNull();
  });
});
