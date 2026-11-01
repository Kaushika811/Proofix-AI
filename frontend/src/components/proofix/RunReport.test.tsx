// @vitest-environment jsdom
/**
 * The run report renders only what was measured.
 *
 * - **B-F02** — `report` used to default to `MOCK_RUN_REPORT`. Omitting the prop
 *   rendered a different repository's trust score, file list and PR decision as
 *   though they belonged to this run. The prop is now required, which makes the
 *   defect a compile error rather than a test; what is tested here is the
 *   behaviour that replaced it — an unmeasured report says so.
 * - **B-F05** — a blocked run's decision badge used to borrow the draft tone.
 */
import { describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { apiFetch } from "@/lib/api";
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  apiFetch: vi.fn(),
}));
import { RunReport } from "./RunReport";
import { EMPTY_RUN_REPORT } from "./emptyModels";

it("shows generated test evidence without presenting an absent after check as passed", () => {
  render(
    <RunReport
      done
      report={{
        ...EMPTY_RUN_REPORT,
        generatedTest: {
          status: "generated",
          path: "tests/test_proofix.py",
          content: "assert total(10, 2) == 20",
          sha256: "abc123",
          rationale: "Two units should cost twice the price",
          before_status: "CONFIRMED",
          after_passed: null,
        },
      }}
    />,
  );
  expect(
    screen.getByText(/Before repair: Assertion failed.*After repair: Unverified/),
  ).toBeTruthy();
  expect(screen.getByText(/Review whether its assertion matches/)).toBeTruthy();
  expect(screen.getByText("assert total(10, 2) == 20")).toBeTruthy();
  expect(screen.getByText("SHA-256: abc123")).toBeTruthy();
});

it("shows recovery category, decision, failure, and changed patch evidence", () => {
  render(
    <RunReport
      done
      report={{
        ...EMPTY_RUN_REPORT,
        decision: "blocked",
        decisionLabel: "Recovery stopped",
        recoveryAttempts: [
          {
            number: 2,
            target: "auth.py",
            diagnosis: "expiry guard missing",
            patchHash: "abcdef1234567890",
            failingTest: "tests/test_auth.py::test_expired",
            failure: "assert True is False",
            validationStatus: "executed",
            category: "implementation_failure",
            decision: "recovery_stop",
            reason: "Recovery budget exhausted",
            changedFromPrevious: { target: false, diagnosis: false, patch: true },
          },
        ],
      }}
    />,
  );
  expect(screen.getByText("Recovery Attempts")).toBeTruthy();
  expect(screen.getByText("implementation failure")).toBeTruthy();
  expect(screen.getByText("Decision: Stop for review")).toBeTruthy();
  expect(screen.getByText("assert True is False")).toBeTruthy();
  expect(screen.getByText("Changed: patch")).toBeTruthy();
  expect(screen.getByText("Execution stopped · review needed")).toBeTruthy();
});

it("describes a failed terminal report as stopped", () => {
  render(<RunReport done report={{ ...EMPTY_RUN_REPORT, decision: "failed" }} />);

  expect(screen.getByText("Execution stopped · review needed")).toBeTruthy();
  expect(screen.queryByText("Execution complete · awaiting review")).toBeNull();
});

it("labels a duplicate proposal as not executed", () => {
  render(
    <RunReport
      done
      report={{
        ...EMPTY_RUN_REPORT,
        recoveryAttempts: [
          {
            number: 2,
            target: "auth.py",
            diagnosis: "expiry guard missing",
            patchHash: "abcdef1234567890",
            failingTest: null,
            failure: null,
            validationStatus: "not_executed",
            category: null,
            decision: "recovery_stop",
            reason: "Generated patch exactly repeats a failed attempt; validation was skipped",
            changedFromPrevious: { target: false, diagnosis: false, patch: false },
          },
        ],
      }}
    />,
  );
  expect(screen.getByText("not executed")).toBeTruthy();
  expect(screen.getByText("Changed: nothing")).toBeTruthy();
});

describe("RunReport trust score", () => {
  it("reports an unmeasured trust score as unmeasured, never as a number", () => {
    render(<RunReport done report={{ ...EMPTY_RUN_REPORT, trustScore: null }} />);

    expect(screen.getByText("Not measured")).toBeTruthy();
    // The threshold comparison is a verdict; a run with no measurement has not
    // reached one, so neither phrasing may appear.
    expect(screen.queryByText(/Confidence meets threshold/)).toBeNull();
    expect(screen.queryByText(/Confidence below threshold/)).toBeNull();
    expect(
      screen.getByText(/No axis was measured, so confidence could not be established/),
    ).toBeTruthy();
  });

  it("renders a real score against its threshold", () => {
    render(
      <RunReport done report={{ ...EMPTY_RUN_REPORT, trustScore: 0.91, trustThreshold: 0.9 }} />,
    );

    expect(screen.queryByText("Not measured")).toBeNull();
    expect(screen.getByText(/Confidence meets threshold/)).toBeTruthy();
  });

  it("renders a measured zero as a score, not as an absence", () => {
    render(<RunReport done report={{ ...EMPTY_RUN_REPORT, trustScore: 0, trustThreshold: 0.9 }} />);

    expect(screen.queryByText("Not measured")).toBeNull();
    expect(screen.getByText(/Confidence below threshold/)).toBeTruthy();
  });
});

describe("RunReport decision badge", () => {
  it("gives a blocked run its own badge rather than the draft one", () => {
    render(
      <RunReport
        done
        report={{
          ...EMPTY_RUN_REPORT,
          decision: "blocked",
          decisionLabel: "Environment not prepared",
        }}
      />,
    );

    const badge = screen.getByText("Environment not prepared");
    expect(badge.className).toContain("status-blocked");
    expect(badge.className).not.toContain("status-draft");
  });
});

describe("RunReport GitHub action", () => {
  it("allows opening a published draft PR", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(
      <RunReport
        done
        report={{
          ...EMPTY_RUN_REPORT,
          decision: "draft",
          prUrl: "https://github.com/acme/example/pull/2",
        }}
      />,
    );
    const button = screen.getByRole("button", { name: "Open draft GitHub PR" });
    expect(button).toHaveProperty("disabled", false);
    button.click();
    expect(open).toHaveBeenCalledWith(
      "https://github.com/acme/example/pull/2",
      "_blank",
      "noopener,noreferrer",
    );
    open.mockRestore();
  });

  it("explains that a dry run did not create a PR", () => {
    render(
      <RunReport
        done
        report={{
          ...EMPTY_RUN_REPORT,
          decision: "draft",
          publication: { status: "dry_run", reason: "Live publication is disabled" },
        }}
      />,
    );
    expect(screen.getByRole("button", { name: "Dry run — no GitHub PR" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByText("Live publication is disabled")).toBeTruthy();
  });
  it("labels a failed publication as unpublished while retaining the proof hash", () => {
    render(
      <RunReport
        done
        report={{
          ...EMPTY_RUN_REPORT,
          proofBundle: "sha256:abc123",
          publication: {
            status: "failed",
            reason: "GitHub push failed (GitCommandError)",
          },
        }}
      />,
    );
    expect(screen.getByText("Unpublished")).toBeTruthy();
    expect(screen.getByText("sha256:abc123")).toBeTruthy();
    expect(screen.getByText("GitHub push failed (GitCommandError)")).toBeTruthy();
  });

  it("opens the published PR URL in a new tab", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(
      <RunReport
        done
        report={{
          ...EMPTY_RUN_REPORT,
          decision: "merge",
          prUrl: "https://github.com/acme/example/pull/1",
        }}
      />,
    );

    screen.getByRole("button", { name: "Open GitHub PR" }).click();
    expect(open).toHaveBeenCalledWith(
      "https://github.com/acme/example/pull/1",
      "_blank",
      "noopener,noreferrer",
    );
    open.mockRestore();
  });

  it("disables the action when no PR was published", () => {
    render(<RunReport done report={{ ...EMPTY_RUN_REPORT, decision: "merge" }} />);
    expect(screen.getByRole("button", { name: "GitHub PR unavailable" })).toHaveProperty(
      "disabled",
      true,
    );
  });
});

describe("Draft reasons", () => {
  it("lists every reason, not just the one A10 noted", () => {
    // `decisionReason` is A10's `review_note`, which carries the first reason
    // routing hit. A run blocked for three reasons showed one and the rest were
    // unrecoverable from the UI.
    render(
      <RunReport
        done
        report={{
          ...EMPTY_RUN_REPORT,
          decisionReason: "Validation retries exhausted. Manual verification required.",
          draftReasons: [
            {
              code: "validation_exhausted",
              detail: "Validation retries exhausted without a patch that passed.",
            },
            {
              code: "citations_unverified",
              detail: "Citation verification incomplete after the maximum reinvestigations.",
            },
            {
              code: "reproduction_no_tests",
              detail: "A3.5 Reproduction Gate: no tests available to confirm the vulnerability.",
            },
          ],
        }}
      />,
    );

    expect(screen.getByText(/Why this is a draft \(3\)/)).toBeTruthy();
    expect(screen.getByText(/no tests available to confirm/)).toBeTruthy();
    expect(screen.getByText(/Citation verification incomplete/)).toBeTruthy();
  });

  it("renders the backend's sentences verbatim", () => {
    const detail = "A3.5 Reproduction Gate: bug could not be reproduced in test environment.";
    render(
      <RunReport
        done
        report={{
          ...EMPTY_RUN_REPORT,
          draftReasons: [{ code: "reproduction_unconfirmed", detail }],
        }}
      />,
    );

    expect(screen.getByText(detail)).toBeTruthy();
    // Singular heading — the count is only shown when there is more than one.
    expect(screen.getByText("Why this is a draft")).toBeTruthy();
  });

  it("shows nothing for a run with no draft reasons", () => {
    render(<RunReport done report={{ ...EMPTY_RUN_REPORT, draftReasons: [] }} />);

    expect(screen.queryByText(/Why this is a draft/)).toBeNull();
  });

  it("shows nothing when the backend predates the field", () => {
    render(<RunReport done report={EMPTY_RUN_REPORT} />);

    expect(screen.queryByText(/Why this is a draft/)).toBeNull();
  });
});

describe("RunReport export and download functionality", () => {
  it("getRunReportFilename formats and sanitizes repository and runId", async () => {
    const { getRunReportFilename } = await import("./RunReport");

    expect(
      getRunReportFilename({
        ...EMPTY_RUN_REPORT,
        repository: "demo-vln",
        shortRunId: "4e55-4457",
      }),
    ).toBe("demo-vln-run-report-4e55-4457.json");

    expect(
      getRunReportFilename({
        ...EMPTY_RUN_REPORT,
        repository: "org/repo:name",
        shortRunId: "run#123",
      }),
    ).toBe("org-repo-name-run-report-run-123.json");

    expect(
      getRunReportFilename({
        ...EMPTY_RUN_REPORT,
        repository: "",
        shortRunId: "",
        runId: "",
      }),
    ).toBe("run-report.json");
  });

  it("serializeRunReport exports the full report object preserving nested fields", async () => {
    const { serializeRunReport } = await import("./RunReport");
    const testReport = {
      ...EMPTY_RUN_REPORT,
      repository: "DemoRepo",
      decision: "draft" as const,
      rootCause: {
        summary: "Full analysis summary",
        statement: "Defect statement",
        location: "backend/main.py:10",
        claim: "Claim text",
        verified: true,
      },
      files: ["backend/main.py", "frontend/src/App.tsx"],
    };

    const json = serializeRunReport(testReport);
    const parsed = JSON.parse(json);

    expect(parsed.repository).toBe("DemoRepo");
    expect(parsed.rootCause.summary).toBe("Full analysis summary");
    expect(parsed.rootCause.verified).toBe(true);
    expect(parsed.files).toEqual(["backend/main.py", "frontend/src/App.tsx"]);
  });

  it("copies complete JSON to clipboard and updates button to Copied", async () => {
    const { fireEvent, act } = await import("@testing-library/react");
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    });

    const testReport = {
      ...EMPTY_RUN_REPORT,
      repository: "test-repo",
      shortRunId: "abc-123",
    };

    render(<RunReport done report={testReport} />);

    const copyBtn = screen.getByRole("button", { name: /Copy JSON/i });
    expect(copyBtn).toBeTruthy();

    await act(async () => {
      fireEvent.click(copyBtn);
    });

    expect(writeTextMock).toHaveBeenCalledTimes(1);
    const copiedText = writeTextMock.mock.calls[0][0];
    const parsed = JSON.parse(copiedText);
    expect(parsed.repository).toBe("test-repo");
    expect(parsed.shortRunId).toBe("abc-123");

    // UI should show "Copied"
    expect(screen.getByText("Copied")).toBeTruthy();
  });

  it("triggers browser download with sanitized filename and cleans up object URL", async () => {
    const { fireEvent, act } = await import("@testing-library/react");
    const createObjectURLMock = vi.fn().mockReturnValue("blob:http://localhost/test-blob");
    const revokeObjectURLMock = vi.fn();
    global.URL.createObjectURL = createObjectURLMock;
    global.URL.revokeObjectURL = revokeObjectURLMock;

    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    const testReport = {
      ...EMPTY_RUN_REPORT,
      repository: "my-app",
      shortRunId: "xyz-789",
    };

    render(<RunReport done report={testReport} />);

    const downloadBtn = screen.getByRole("button", { name: /Download/i });
    expect(downloadBtn).toBeTruthy();

    await act(async () => {
      fireEvent.click(downloadBtn);
    });

    expect(createObjectURLMock).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURLMock).toHaveBeenCalledWith("blob:http://localhost/test-blob");

    clickSpy.mockRestore();
  });
});

describe("User-triggered draft publication", () => {
  const ready = {
    ...EMPTY_RUN_REPORT,
    runId: "click-run",
    decision: "draft" as const,
    publication: { status: "ready" as const, prepared_head_sha: "prepared", draft: true },
  };
  it("creates only on click, prevents duplicate clicks, then opens GitHub", async () => {
    vi.mocked(apiFetch).mockReset();
    const published = {
      ...ready,
      prUrl: "https://github.com/team/repo/pull/1",
      publication: { ...ready.publication, status: "published" as const },
    };
    let finish!: (value: typeof published) => void;
    vi.mocked(apiFetch).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<RunReport done report={ready} />);
    expect(apiFetch).not.toHaveBeenCalled();
    act(() => {
      screen.getByRole("button", { name: "Create draft PR" }).click();
    });
    const busy = screen.getByRole("button", { name: "Creating draft PR…" });
    expect(busy).toHaveProperty("disabled", true);
    busy.click();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/api/runs/click-run/draft-pr", { method: "POST" });
    await act(async () => {
      finish(published);
    });
    expect(open).not.toHaveBeenCalled();
    screen.getByRole("button", { name: "Open draft GitHub PR" }).click();
    expect(open).toHaveBeenCalledWith(published.prUrl, "_blank", "noopener,noreferrer");
    open.mockRestore();
  });

  it("shows a publication error and permits retry", async () => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockResolvedValueOnce({
      ...ready,
      publication: { ...ready.publication, status: "failed", reason: "Repository access denied" },
    });
    render(<RunReport done report={ready} />);
    await act(async () => {
      screen.getByRole("button", { name: "Create draft PR" }).click();
    });
    expect(screen.getByText("Repository access denied")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry draft PR" })).toHaveProperty(
      "disabled",
      false,
    );
  });
});
