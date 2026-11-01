/**
 * Single export surface for every mock data fixture.
 *
 * Production swap path: replace these exports with calls to `src/lib/api.ts`
 * (see `src/lib/runService.ts`). UI components import only from this barrel
 * or from typed services, never from individual fixture files.
 */
export { AGENTS } from "@/components/proofix/data";
export type {
  AgentEntry,
  AgentStatus,
  EvidencePayload,
  EvidenceField,
  MetricItem,
} from "@/components/proofix/data";

export { MOCK_REPOSITORIES, RUN_NAME_POOL } from "./repositories";
export {
  MOCK_WORKSPACE_HEADER,
  MOCK_EXECUTIVE_SUMMARY,
  MOCK_REPAIR_ATTEMPTS,
} from "./runReport";
export type {
  RunReportModel,
  WorkspaceHeaderModel,
  ExecutiveSummaryModel,
  RepairAttemptsModel,
  RetryAttempt,
  RepoMetadata,
  ContextPackageModel,
  PatchBundleModel,
  PatchCandidateModel,
  ContextRedaction,
  RankedContextFile,
} from "./runReport";
export { MOCK_CHAT_SUGGESTIONS, mockAnswerer } from "./chat";
export { HANDOFF_LABELS } from "./handoff";
