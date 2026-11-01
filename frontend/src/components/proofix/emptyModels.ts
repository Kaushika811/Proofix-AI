/**
 * Neutral, empty view models for live mode.
 *
 * `useRunData` needs something to hold before the backend responds. Using the
 * bundled `MOCK_*` fixtures for that means a live workspace briefly renders
 * another repository's run — vulnapi's files, its trust scores, its verdict —
 * and any field the backend omits keeps showing fixture data indefinitely,
 * which is indistinguishable from real output. These blanks render as empty
 * rather than as someone else's data, so anything on screen in live mode came
 * from the backend.
 */
import type { AgentEntry } from "./data";
import type {
  ExecutiveSummaryModel,
  RepairAttemptsModel,
  RunReportModel,
  WorkspaceHeaderModel,
} from "@/mocks";

export const EMPTY_AGENTS: AgentEntry[] = [];

export const EMPTY_WORKSPACE_HEADER: WorkspaceHeaderModel = {
  repository: "",
  branch: "",
  shortRunId: "",
  retries: 0,
  executionTime: "—",
  decisionLabel: "Running",
};

export const EMPTY_EXECUTIVE_SUMMARY: ExecutiveSummaryModel = {
  repository: "",
  bug: "",
  severity: "not measured",
  rootCause: "",
  confidence: "—",
  filesAffected: 0,
  attempts: 0,
  mutationScore: "—",
  runtime: "—",
  trustScore: "—",
  decision: "draft",
  decisionReason: "",
};

export const EMPTY_RUN_REPORT: RunReportModel = {
  runId: "",
  shortRunId: "",
  repository: "",
  branch: "",
  decision: "draft",
  decisionLabel: "Running",
  prUrl: null,
  // Never 0: an empty run measured nothing, and 0 would assert it scored worst.
  trustScore: null,
  trustThreshold: 0,
  rootCause: { summary: "", statement: "", location: "", claim: "", verified: false },
  rejection: {
    attempts: 0,
    survivors: 0,
    score: null,
    correctnessScore: null,
    correctnessThreshold: 0,
    reason: "",
  },
  decisionReason: "",
  trust: [],
  files: [],
  evidence: [],
  proofBundle: "",
  agentCount: 0,
  totalDurationSeconds: 0,
};

export const EMPTY_REPAIR_ATTEMPTS: RepairAttemptsModel = {
  attempts: [],
  failureMessage: "",
  nextStepLabel: "",
};
