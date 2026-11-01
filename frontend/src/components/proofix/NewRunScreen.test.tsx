// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

vi.mock("@/lib/runService", () => ({
  isLive: true,
  parseGithubUrl: () => null,
  validateRepository: vi.fn(),
}));

import { NewRunScreen } from "./NewRunScreen";

it("launches a repository-only run without issue fields", async () => {
  const onAnalyze = vi.fn();
  render(<NewRunScreen onAnalyze={onAnalyze} />);

  const launch = screen.getByRole("button", { name: /Launch Autonomous Analysis/i });
  expect(launch.hasAttribute("disabled")).toBe(true);
  expect(screen.queryByLabelText(/Reported issue/i)).toBeNull();
  expect(screen.queryByLabelText(/Failing pytest test/i)).toBeNull();
  expect(screen.queryByLabelText(/Expected behavior/i)).toBeNull();
  expect(screen.queryByLabelText(/Suspected source file/i)).toBeNull();

  fireEvent.change(screen.getByPlaceholderText(/local path/i), {
    target: { value: " /repo " },
  });
  expect(launch.hasAttribute("disabled")).toBe(false);

  fireEvent.click(launch);
  await waitFor(() => expect(onAnalyze).toHaveBeenCalledWith("/repo"));
});
