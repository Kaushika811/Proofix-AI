import { expect, it, vi } from "vitest";

const apiFetch = vi.fn().mockResolvedValue({ run_id: "run-1" });

vi.mock("./api", () => ({
  DATA_SOURCE: "api",
  ENDPOINTS: { runs: () => "/api/runs" },
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  isStatus: vi.fn(),
}));

import { createRun } from "./runService";

it("sends the selected issue and test to the run API", async () => {
  const id = await createRun("/repo", {
    description: "Reject shell scripts",
    targetTest: "tests/test_upload.py::test_reject_script",
    expectedBehavior: "Return 400",
    targetFile: "app/uploads.py",
  });

  expect(id).toBe("run-1");
  expect(apiFetch).toHaveBeenCalledWith("/api/runs", {
    method: "POST",
    json: {
      url: "/repo",
      repo_path: "/repo",
      issue_hint: "Reject shell scripts",
      target_test: "tests/test_upload.py::test_reject_script",
      expected_behavior: "Return 400",
      target_file: "app/uploads.py",
    },
  });
});

it("requests generation without an existing test", async () => {
  await createRun("/repo", {
    description: "Wrong total",
    expectedBehavior: "Multiply price by count",
  });
  expect(apiFetch).toHaveBeenLastCalledWith("/api/runs", {
    method: "POST",
    json: {
      url: "/repo",
      repo_path: "/repo",
      issue_hint: "Wrong total",
      target_test: null,
      expected_behavior: "Multiply price by count",
      target_file: null,
    },
  });
});
