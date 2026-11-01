// @vitest-environment jsdom
/**
 * `collapseActivity`/`collapseMetrics` gate the Activity feed and Supporting
 * metrics behind a closed disclosure instead of showing them open. The bug
 * this pins: `useState(!collapseActivity)` only reads the prop on first
 * render, so it is easy to accidentally regress into an initially-open
 * state (e.g. by moving the toggle to an effect, or flipping the negation)
 * without any visible error — the sections still work, they just start
 * open. Repository Intelligence (A1, `entry.id === "repo-intel"`) is one of
 * the callers that opts into this; this test renders it exactly as
 * `Workspace` does and asserts both sections start collapsed.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentCard } from "./AgentCard";
import { AGENTS } from "./data";
import type { LiveAgent } from "./useExecutionRun";

const REPO_INTEL_ENTRY: LiveAgent = {
  ...AGENTS.find((a) => a.id === "repo-intel")!,
  visibleLines: 7,
  liveStatus: "completed",
};

describe("AgentCard", () => {
  it("Activity starts collapsed when the caller opts in via collapseActivity", () => {
    render(
      <AgentCard
        entry={REPO_INTEL_ENTRY}
        agentIndex={0}
        active={false}
        expanded
        onSelect={() => {}}
        collapseActivity
        collapseMetrics
      />,
    );

    const toggle = screen.getByRole("button", { name: /Activity/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // The lines themselves are not rendered while collapsed.
    expect(screen.queryByText(REPO_INTEL_ENTRY.lines[0])).toBeNull();
  });

  it("Supporting metrics starts collapsed when the caller opts in via collapseMetrics", () => {
    render(
      <AgentCard
        entry={REPO_INTEL_ENTRY}
        agentIndex={0}
        active={false}
        expanded
        onSelect={() => {}}
        collapseActivity
        collapseMetrics
      />,
    );

    const toggle = screen.getByRole("button", { name: /Supporting metrics/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // "AST Nodes" only ever appears as a metrics label, unlike "Files",
    // which also appears in the live-view visualization above it.
    expect(screen.queryByText(REPO_INTEL_ENTRY.metrics![1].label)).toBeNull();
  });

  it("clicking the collapsed Activity toggle expands it", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    render(
      <AgentCard
        entry={REPO_INTEL_ENTRY}
        agentIndex={0}
        active={false}
        expanded
        onSelect={() => {}}
        collapseActivity
        collapseMetrics
      />,
    );

    const toggle = screen.getByRole("button", { name: /Activity/ });
    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(REPO_INTEL_ENTRY.lines[0])).toBeTruthy();
  });

  it("without collapseActivity/collapseMetrics, both sections render open with no toggle", () => {
    render(
      <AgentCard
        entry={REPO_INTEL_ENTRY}
        agentIndex={0}
        active={false}
        expanded
        onSelect={() => {}}
      />,
    );

    expect(screen.queryByRole("button", { name: /Activity/ })).toBeNull();
    expect(screen.getByText(REPO_INTEL_ENTRY.lines[0])).toBeTruthy();
    // "Files" also appears in the live-view visualization above it — the
    // point here is that the metrics label renders at all without a toggle.
    expect(screen.getAllByText(REPO_INTEL_ENTRY.metrics![0].label).length).toBeGreaterThan(0);
  });
});
