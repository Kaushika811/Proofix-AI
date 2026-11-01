import { createFileRoute } from "@tanstack/react-router";
import { Workspace } from "@/components/proofix/Workspace";

/**
 * Workspace route. With no run in the URL this is the entry point for starting
 * one; past runs live at `/runs/$runId` and are opened from the sidebar.
 */
export const Route = createFileRoute("/workspace")({
  head: () => ({
    meta: [
      { title: "ProoFix — AI Execution Workspace" },
      {
        name: "description",
        content:
          "Watch an autonomous engineer analyze, reason, validate and decide whether a pull request deserves to be merged.",
      },
    ],
  }),
  component: Workspace,
});
