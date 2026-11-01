import { createFileRoute } from "@tanstack/react-router";
import { Workspace } from "@/components/proofix/Workspace";

/**
 * A single run's execution workspace. Naming the run in the path makes it
 * shareable, reload-safe, and navigable with the browser's back button.
 */
export const Route = createFileRoute("/runs/$runId")({
  head: () => ({
    meta: [{ title: "ProoFix — Run" }],
  }),
  component: RunWorkspace,
});

function RunWorkspace() {
  const { runId } = Route.useParams();
  return <Workspace runId={runId} />;
}
