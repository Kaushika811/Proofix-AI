// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { RepairSummary } from "./RepairSummary";
import { MOCK_EXECUTIVE_SUMMARY } from "@/mocks";
import { EMPTY_RUN_REPORT, EMPTY_WORKSPACE_HEADER } from "./emptyModels";

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("distinguishes failed tests, unconfirmed diagnosis and skipped checks", () => {
  const attempts = vi.fn();
  render(
    <RepairSummary
      done
      summary={{
        ...MOCK_EXECUTIVE_SUMMARY,
        decision: "blocked",
        decisionReason: "Recovery budget exhausted",
        trustScore: "not measured",
        recordedAttempts: 4,
        executedValidations: 3,
        latestFailure: "assert upload.rejected",
        verification: [
          { label: "Diagnosis", status: "incomplete", detail: "Unconfirmed" },
          { label: "Target tests", status: "failed", detail: "Failed" },
          {
            label: "Mutation testing",
            status: "not_run",
            detail: "Not run — prerequisite tests failed",
          },
        ],
      }}
      header={EMPTY_WORKSPACE_HEADER}
      onAttempts={attempts}
    />,
  );
  expect(screen.getByText("Repair stopped")).toBeTruthy();
  expect(screen.getByText("Unconfirmed").getAttribute("data-evidence-state")).toBe("incomplete");
  expect(screen.getByText(/Not run — prerequisite/).getAttribute("data-evidence-state")).toBe(
    "not_run",
  );
  expect(screen.getByTitle("4 recorded attempts")).toBeTruthy();
  expect(screen.getByTitle("3 executed validations")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Open PR" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Compare repair attempts" }));
  expect(attempts).toHaveBeenCalledOnce();
});

it("only links a published PR, keeping dry-run routing separate", () => {
  const props = {
    done: true,
    summary: MOCK_EXECUTIVE_SUMMARY,
    header: EMPTY_WORKSPACE_HEADER,
    onAttempts: vi.fn(),
  };
  const { rerender } = render(
    <RepairSummary
      {...props}
      report={{
        ...EMPTY_RUN_REPORT,
        prUrl: "https://github.com/example/repo/pull/1",
        publication: { status: "dry_run" },
      }}
    />,
  );
  expect(screen.getByText("No PR created — dry run")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Open PR" })).toBeNull();
  rerender(
    <RepairSummary
      {...props}
      report={{
        ...EMPTY_RUN_REPORT,
        prUrl: "https://github.com/example/repo/pull/1",
        publication: { status: "published" },
      }}
    />,
  );
  expect(screen.getByRole("link", { name: "Open PR" }).getAttribute("href")).toContain("/pull/1");
});

it("expands overflowing text in place and keeps failure evidence collapsed", () => {
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(60);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(40);
  const rootCause = "A long diagnosis that needs more than two visible lines.";
  render(
    <RepairSummary
      done
      summary={{
        ...MOCK_EXECUTIVE_SUMMARY,
        rootCause,
        latestFailure: "failure details",
        decision: "blocked",
      }}
      header={EMPTY_WORKSPACE_HEADER}
      onAttempts={vi.fn()}
    />,
  );
  const expand = screen.getByRole("button", { name: "Show more: Root cause" });
  expect(expand.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(expand);
  expect(
    screen.getByRole("button", { name: "Show less: Root cause" }).getAttribute("aria-expanded"),
  ).toBe("true");
  expect(screen.getAllByText(rootCause)).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Show less: Root cause" }));
  expect(
    screen.getByRole("button", { name: "Show more: Root cause" }).getAttribute("aria-expanded"),
  ).toBe("false");
  expect(screen.getByText("Latest failing test evidence").closest("details")?.open).toBe(false);
});
