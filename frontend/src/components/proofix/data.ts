import type { AgentVisualizationPayload } from "./visualizationTypes";

export type AgentStatus =
  | "completed"
  | "running"
  | "retry"
  | "failed"
  | "draft"
  /** Stage the pipeline routed around (e.g. security re-scan after a failed validation). */
  | "skipped"
  /**
   * The run halted before the pipeline could start — A0.7 found the repository's
   * tests unrunnable. Distinct from `failed` (the run tried and could not) and
   * from `draft` (a PR exists but wants review); blocked produced neither.
   */
  | "blocked";

export interface ExecutionLine {
  text: string;
  done?: boolean;
}

export interface MetricItem {
  label: string;
  value: string;
}

export interface EvidenceField {
  label: string;
  value: string;
  mono?: boolean;
}

export interface EvidencePayload {
  title: string;
  subtitle: string;
  fields: EvidenceField[];
  pills?: string[];
  bars?: { label: string; value: number; tone?: "ok" | "warn" | "bad" }[];
}

export interface AgentEntry {
  id: string;
  index: number;
  agent: string;
  purpose: string;
  /** Label for the evidence this agent hands to the next one. From AGENT_REGISTRY. */
  handoff: string;
  status: AgentStatus;
  duration: string;
  lines: string[];
  /** `null` (not just absent) when a card deliberately omits the generic
   * Supporting Metrics grid — today only `context`, whose own panel already
   * shows every one of these numbers more prominently. */
  metrics?: MetricItem[] | null;
  pills?: string[];
  modifiedFiles?: string[];
  evidence: EvidencePayload;
  /** Typed payload consumed by `AgentVisualization`. Optional so backends
   * may omit visualization data; the component falls back to nothing. */
  visualization?: AgentVisualizationPayload;
  details?: {
    input?: string;
    output?: string;
    files?: string[];
    logs?: string[];
  };
}

export const AGENTS: AgentEntry[] = [
  {
    id: "repo-intel",
    index: 1,
    agent: "Repository Intelligence",
    purpose: "Understand repository structure before any repair.",
    handoff: "Semantic Graph",
    status: "completed",
    duration: "8.2s",
    lines: [
      "Scanning repository…",
      "Found 6 source files.",
      "Building AST…",
      "Extracting dependency graph…",
      "Classifying semantic roles…",
      "Creating Semantic Intent Graph…",
      "Repository understanding complete.",
    ],
    metrics: [
      { label: "Files", value: "6" },
      { label: "AST Nodes", value: "1,284" },
      { label: "LLM Calls Saved", value: "37" },
      { label: "Duration", value: "8.2s" },
    ],
    evidence: {
      title: "Semantic Intent Graph",
      subtitle: "Static map of intent across the repository.",
      fields: [
        { label: "Language", value: "Python 3.11" },
        { label: "Entry points", value: "2", mono: true },
        { label: "Public APIs", value: "9", mono: true },
        { label: "Internal modules", value: "14", mono: true },
      ],
      pills: ["auth/", "api/routes", "db/models", "utils/tokens"],
    },
    visualization: {
      kind: "repo-intel",
      data: {
        files: [
          { name: "auth.py", ast: 184 },
          { name: "api.py", ast: 312 },
          { name: "middleware.py", ast: 96 },
          { name: "db.py", ast: 148 },
          { name: "tests/", ast: 544 },
        ],
        graphNodes: 14,
        metrics: {
          files: 5,
          imports: 48,
          dependencies: 11,
          semanticRoles: 7,
        },
      },
    },
  },
  {
    id: "deps",
    index: 2,
    agent: "Dependency Analyzer",
    purpose: "Determine whether vulnerabilities are actually reachable.",
    handoff: "Reachability Map",
    status: "completed",
    duration: "4.7s",
    lines: [
      "Loading dependency graph…",
      "Checking imported packages…",
      "Querying vulnerability database…",
      "Found 14 vulnerabilities.",
      "Running reachability analysis…",
      "No critical vulnerabilities are reachable.",
    ],
    metrics: [
      { label: "Packages", value: "42" },
      { label: "CVEs Found", value: "14" },
      { label: "Reachable", value: "0" },
      { label: "Duration", value: "4.7s" },
    ],
    evidence: {
      title: "Reachability Report",
      subtitle: "Vulnerability surface narrowed by call-graph analysis.",
      fields: [
        { label: "Direct deps", value: "18" },
        { label: "Transitive", value: "212" },
        { label: "Advisories", value: "14" },
        { label: "Exploitable", value: "0" },
      ],
      pills: ["requests@2.31", "cryptography@41.0", "fastapi@0.110"],
    },
    visualization: {
      kind: "deps",
      data: {
        path: [
          { name: "API", sub: "request entrypoint" },
          { name: "validate_token()", sub: "auth boundary" },
          { name: "JWT Decoder", sub: "decode + verify" },
          { name: "Database", sub: "session lookup" },
        ],
        unreachable: [{ name: "legacy_login" }, { name: "admin_panel" }, { name: "debug_routes" }],
        metrics: { reachable: 8, deadFindings: 6, attackPaths: 2 },
      },
    },
  },
  {
    id: "static",
    index: 3,
    agent: "Static Analysis",
    purpose: "Combine multiple analyzers into one prioritized report.",
    handoff: "Prioritized Findings",
    status: "completed",
    duration: "11.4s",
    lines: [
      "Running Bandit…",
      "Running Semgrep…",
      "Running Ruff…",
      "Removing duplicate findings…",
      "Prioritizing actionable issues…",
      "6 findings remain.",
    ],
    metrics: [
      { label: "Raw issues", value: "31" },
      { label: "Deduped", value: "19" },
      { label: "Actionable", value: "6" },
      { label: "Duration", value: "11.4s" },
    ],
    evidence: {
      title: "Prioritized Findings",
      subtitle: "Cross-analyzer consensus, ranked by exploitability.",
      fields: [
        { label: "High", value: "2" },
        { label: "Medium", value: "3" },
        { label: "Low", value: "1" },
        { label: "False-positives filtered", value: "13" },
      ],
      pills: ["auth/token.py", "api/session.py", "utils/jwt.py"],
    },
    visualization: {
      kind: "static",
      data: {
        scanners: ["Bandit", "Semgrep", "Custom Rules"],
        findings: [
          { sev: "HIGH", text: "JWT Validation — missing expiry check", at: 0.15 },
          { sev: "MEDIUM", text: "Unsafe subprocess call in worker", at: 0.3 },
          { sev: "LOW", text: "Weak hashing (md5) in legacy util", at: 0.42 },
          { sev: "HIGH", text: "Open redirect in /auth/callback", at: 0.55 },
          { sev: "MEDIUM", text: "Missing CSRF token on /session", at: 0.68 },
        ],
        metrics: { raw: 31, deduped: 19, prioritized: 6 },
      },
    },
  },
  {
    id: "reproduce",
    index: 4,
    agent: "Failure Reproduction",
    purpose: "Verify the reported bug actually exists.",
    handoff: "Runtime Evidence",
    status: "completed",
    duration: "6.1s",
    lines: [
      "Launching pytest…",
      "Executing failing test…",
      "Failure reproduced.",
      "Capturing runtime trace…",
      "Saving execution evidence…",
    ],
    metrics: [
      { label: "Tests run", value: "1" },
      { label: "Reproduced", value: "Yes" },
      { label: "Confidence", value: "97%" },
      { label: "Duration", value: "6.1s" },
    ],
    evidence: {
      title: "Runtime Evidence",
      subtitle: "Live failure captured from the test harness.",
      fields: [
        {
          label: "Failing test",
          value: "tests/test_auth.py::test_validate_token_expired",
          mono: true,
        },
        { label: "Exit code", value: "1", mono: true },
        { label: "Confidence", value: "0.97" },
        { label: "Report", value: "runs/11b8/repro/trace.json", mono: true },
      ],
      pills: ["AssertionError", "validate_token", "expired_at < now"],
    },
    visualization: {
      kind: "reproduce",
      data: {
        command: "$ pytest tests/test_auth.py -v",
        tests: [
          { name: "test_login_ok", result: "PASS" },
          { name: "test_refresh_ok", result: "PASS" },
          { name: "test_session_persist", result: "PASS" },
          { name: "test_validate_token_expired", result: "FAIL" },
        ],
        failure: {
          name: "test_validate_token_expired()",
          assertion: "AssertionError",
          expected: "False",
          actual: "True",
          stack: ["API", "validate_token()", "decode_token()"],
        },
        successMessage: "Runtime Failure Reproduced",
      },
    },
  },
  {
    id: "root",
    index: 5,
    agent: "Root Cause Analysis",
    purpose: "Determine why the bug occurred.",
    handoff: "Root Cause",
    status: "completed",
    duration: "5.3s",
    lines: [
      "Matching runtime trace with source code…",
      "Locating failure…",
      "Root cause isolated.",
    ],
    metrics: [
      { label: "Candidate sites", value: "4" },
      { label: "Confirmed", value: "1" },
      { label: "Evidence links", value: "3" },
      { label: "Duration", value: "5.3s" },
    ],
    evidence: {
      title: "Root Cause",
      subtitle: "Primary function and supporting runtime evidence.",
      fields: [
        { label: "Function", value: "validate_token()", mono: true },
        { label: "File", value: "auth/token.py:142", mono: true },
        { label: "Reason", value: "Missing expiry comparison branch" },
        { label: "Trace links", value: "3 frames" },
      ],
    },
    visualization: {
      kind: "root",
      data: {
        lines: [
          { code: "def validate_token(token):", probe: "payload" },
          { code: "    payload = decode(token)", probe: "expired_at" },
          { code: "    # expected: check payload.expired_at", probe: "missing condition" },
          { code: "    return payload  # ← BUG FOUND", probe: "BUG FOUND" },
        ],
        bugMessage: "BUG FOUND — missing expiry condition",
        evidence: [
          { n: 1, title: "Missing expiry validation", detail: "auth/token.py:142", conf: 98 },
          {
            n: 2,
            title: "Runtime assertion failed",
            detail: "test_validate_token_expired",
            conf: 94,
          },
          {
            n: 3,
            title: "Static warning R-204",
            detail: "Semgrep · auth.jwt.missing-exp",
            conf: 89,
          },
        ],
      },
    },
  },
  {
    id: "blast",
    index: 6,
    agent: "Blast Radius",
    purpose: "Understand repository-wide impact.",
    handoff: "Impact Set",
    status: "completed",
    duration: "3.9s",
    lines: [
      "Traversing dependency graph…",
      "Finding indirect callers…",
      "Checking shared utilities…",
      "5 affected modules identified.",
    ],
    pills: [
      "api/session.py",
      "api/routes/login.py",
      "workers/refresh.py",
      "utils/jwt.py",
      "tests/test_auth.py",
    ],
    metrics: [
      { label: "Direct callers", value: "2" },
      { label: "Indirect", value: "3" },
      { label: "Shared utils", value: "1" },
      { label: "Duration", value: "3.9s" },
    ],
    evidence: {
      title: "Affected Files",
      subtitle: "Repository-wide impact of the proposed repair.",
      fields: [
        { label: "Scope", value: "5 modules" },
        { label: "Dependency count", value: "11 edges" },
        { label: "Shared modules", value: "utils/jwt.py", mono: true },
      ],
      pills: [
        "api/session.py",
        "api/routes/login.py",
        "workers/refresh.py",
        "utils/jwt.py",
        "tests/test_auth.py",
      ],
    },
    visualization: {
      kind: "blast",
      data: {
        source: "validate_token()",
        modules: [
          { name: "middleware.py", hitAt: 0.2 },
          { name: "api.py", hitAt: 0.36 },
          { name: "jwt.py", hitAt: 0.52 },
          { name: "tests.py", hitAt: 0.7 },
          { name: "session.py", hitAt: 0.86 },
        ],
      },
    },
  },
  {
    id: "planner",
    index: 7,
    agent: "Repair Planner",
    purpose: "Create dependency-aware repair order.",
    handoff: "Repair DAG",
    status: "completed",
    duration: "2.4s",
    lines: [
      "Grouping findings…",
      "Resolving repair conflicts…",
      "Generating execution DAG…",
      "Repair sequence completed.",
    ],
    metrics: [
      { label: "Repair groups", value: "3" },
      { label: "Conflicts", value: "0" },
      { label: "DAG depth", value: "2" },
      { label: "Duration", value: "2.4s" },
    ],
    evidence: {
      title: "Repair DAG",
      subtitle: "Ordered repair plan respecting cross-file dependencies.",
      fields: [
        { label: "Stage 1", value: "auth/token.py", mono: true },
        { label: "Stage 2", value: "api/session.py", mono: true },
        { label: "Stage 3", value: "tests/test_auth.py", mono: true },
      ],
    },
    visualization: {
      kind: "planner",
      data: {
        nodes: [
          { id: "fix", label: "Fix validate_token()", x: 130, y: 26 },
          { id: "auth", label: "Update auth.py", x: 130, y: 78 },
          { id: "mw", label: "Update middleware", x: 130, y: 130 },
          { id: "tests", label: "Run Tests", x: 40, y: 188 },
          { id: "mut", label: "Mutation Validation", x: 130, y: 188 },
          { id: "sec", label: "Security Scan", x: 220, y: 188 },
        ],
        edges: [
          [0, 1],
          [1, 2],
          [2, 3],
          [2, 4],
          [2, 5],
        ],
      },
    },
  },
  {
    id: "patch",
    index: 8,
    agent: "Patch Generator",
    purpose: "Generate repository-safe patches.",
    handoff: "Patch Candidate",
    status: "completed",
    duration: "7.6s",
    lines: [
      "Creating AST-safe patch…",
      "Applying minimal modifications…",
      "Preparing validation candidate.",
    ],
    modifiedFiles: ["auth/token.py", "api/session.py"],
    metrics: [
      { label: "Files changed", value: "2" },
      { label: "Lines +", value: "14" },
      { label: "Lines −", value: "6" },
      { label: "Duration", value: "7.6s" },
    ],
    evidence: {
      title: "Patch Candidate",
      subtitle: "Minimal AST-safe modification ready for validation.",
      fields: [
        { label: "Strategy", value: "Guarded expiry check" },
        { label: "Files", value: "2" },
        { label: "Risk score", value: "Low" },
      ],
      pills: ["auth/token.py +9 −3", "api/session.py +5 −3"],
    },
    visualization: {
      kind: "patch",
      data: {
        files: [
          {
            file: "auth/token.py",
            method: "ast_validated_write",
            isTarget: true,
            targetFunction: "validate_token",
            generationSource: "llm",
            contextSource: "a5_5_context_package",
            runtimePrompt: true,
            retryNumber: 0,
            retryReason: null,
            semanticDiff: true,
          },
          {
            file: "api/session.py",
            method: "ast_validated_write",
            isTarget: false,
            targetFunction: null,
            generationSource: "llm",
            contextSource: "a7_local_extraction",
            runtimePrompt: true,
            retryNumber: 0,
            retryReason: null,
            semanticDiff: true,
          },
        ],
      },
    },
  },
  {
    id: "mutation",
    index: 9,
    agent: "Mutation Validation",
    purpose: "Verify repair correctness.",
    handoff: "Validation Report",
    status: "failed",
    duration: "18.2s",
    lines: [
      "Running pytest…",
      "Running mutation testing…",
      "Validation failed.",
      "Repair rejected.",
      "Retry initiated.",
    ],
    metrics: [
      { label: "Tests", value: "142" },
      { label: "Mutants", value: "38" },
      { label: "Killed", value: "31" },
      { label: "Survivors", value: "7" },
    ],
    evidence: {
      title: "Validation Report",
      subtitle: "Behavioural verification rejected the candidate patch.",
      fields: [
        { label: "Mutation score", value: "0.81" },
        { label: "Threshold", value: "0.92" },
        { label: "Survivors", value: "7 mutants", mono: true },
        { label: "Decision", value: "Reject" },
      ],
    },
    visualization: {
      kind: "mutation",
      data: {
        score: 0.57,
        survived: true,
        survivedMutants: 7,
        pytestPassed: true,
        correctness: 62,
        correctnessThreshold: 80,
        failureMessage: "Validation failed — mutants survived. Patch behaviour is too permissive.",
      },
    },
  },
  {
    id: "merge",
    index: 10,
    agent: "Mergeability Router",
    purpose: "Decide whether the repair deserves merging.",
    handoff: "PR Decision",
    status: "draft",
    duration: "1.7s",
    lines: [
      "Evaluating correctness…",
      "Evaluating security…",
      "Evaluating fidelity…",
      "Evaluating repository risk…",
      "Repair does not satisfy mergeability threshold.",
      "Generating Draft PR.",
    ],
    metrics: [
      { label: "Correctness", value: "0.78" },
      { label: "Security", value: "0.91" },
      { label: "Fidelity", value: "0.74" },
      { label: "Scope risk", value: "Low" },
    ],
    evidence: {
      title: "Mergeability Assessment",
      subtitle: "Trust composite below auto-merge threshold (0.90).",
      fields: [
        { label: "Decision", value: "Draft PR" },
        { label: "Review note", value: "Human review recommended for expiry logic." },
        { label: "Proof bundle", value: "sha256:7a31…b4e2", mono: true },
      ],
      bars: [
        { label: "Correctness", value: 78, tone: "warn" },
        { label: "Security", value: 91, tone: "ok" },
        { label: "Fidelity", value: 74, tone: "warn" },
        { label: "Scope risk (inv.)", value: 88, tone: "ok" },
      ],
    },
    visualization: {
      kind: "merge",
      data: {
        metrics: [
          { label: "Correctness", value: 78, ok: false, measured: true },
          { label: "Security", value: 91, ok: true, measured: true },
          { label: "Fidelity", value: 74, ok: false, measured: true },
          { label: "Scope Risk", value: 88, ok: true, measured: true, scopeLabel: "Low" },
        ],
        compositeScore: 83,
        decisionLabel: "Draft PR",
        reviewNote: "Human review recommended — fidelity below auto-merge threshold.",
      },
    },
  },
];

// `RETRY_ATTEMPTS` and `AGENT_SUMMARY_BULLETS` were removed here (bug B-F04).
// Both were exported and imported by nothing. `AGENT_SUMMARY_BULLETS` in
// particular was a table of hardcoded evidence claims — `"Runtime failure
// reproduced.", ok: true` — of exactly the kind that once drew green ticks on
// runs where A3.5 and A4 never executed. Repair attempts now come from
// `GET /api/runs/{id}/attempts` and evidence flags from the run report, both
// with the backend's own wording. Nothing in this file may assert a run fact.
