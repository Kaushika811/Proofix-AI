import { AGENTS } from "@/components/proofix/data";

export const MOCK_CHAT_SUGGESTIONS = [
  "Summarize agent run",
  "Show the root cause",
  "Which files changed?",
  "How was the fix validated?",
  "Show blast radius",
  "Why Draft PR?",
];

/**
 * Default in-browser answerer used until the backend chat endpoint is wired.
 * Generates OpenAI Codex styled responses:
 *  1. Direct Technical Analysis / Finding
 *  2. Run Summary
 *  3. Graphical Flow / Metrics Visualization
 */
export function mockAnswerer(q: string): string {
  const s = q.toLowerCase();

  // 1. Agent Run / Summary Question
  if (
    s.includes("agent run") ||
    s.includes("summarize") ||
    s.includes("what did the agents find") ||
    s.includes("overview") ||
    s.includes("pipeline") ||
    s.includes("stage")
  ) {
    return `### Agent Pipeline Execution Summary

ProoFix executed **11 autonomous agents** across the repository repair pipeline. The pipeline reproduced the issue, located 1/1 cited source locations, generated candidate patches, but concluded in a **Draft PR** due to mutation validation thresholds and unreviewed diagnosis claims.

### Run Summary & Metrics
• **Repository**: \`vulnapi\` (Python 3.11)
• **Reproduction**: \`pytest\` reproduced runtime failure in \`test_auth.py\` (100% confidence)
• **Citations**: 1/1 source locations found (\`auth/token.py:142\`); diagnosis claim requires review
• **Patch Status**: Candidate patch generated touching 2 files (\`+14 / −6\`)
• **Validation**: Mutation score **0.81** (Kill threshold: 0.92 — failed)
• **Routing Decision**: **Draft PR** (Trust Score: 0.83 / 0.90 required for auto-merge)

### Execution Pipeline & State Flow Graph
\`\`\`
[A0.5 Repo Intel] ──► [A1 SIG Graph] ──► [A3.5 Repro Gate: PASSED]
                                                  │
[A6 Fix DAG] ◄── [A5.5 Blast Radius] ◄── [A4 Investigation: SOURCE LOCATED]
      │
      ▼
[A7 Patch Engine] ──► [A8 Mutation Validation: 81%] ──► [A10 Decision: DRAFT PR]
                           (Threshold: 92%)
\`\`\``;
  }

  // 2. Root Cause Question
  if (s.includes("root") || s.includes("cause") || s.includes("bug")) {
    return `### Root Cause Analysis

The defect originates in \`auth/token.py\` inside the \`validate_token()\` function at line 142. The expiration timestamp check \`expired_at < now\` was omitted during token validation, causing expired JWT tokens to be erroneously treated as valid.

### Run Summary
• **Target Function**: \`validate_token()\` in \`auth/token.py:142\`
• **Detection Mechanism**: Static Rule \`sec-jwt-04\` + Runtime pytest reproduction
• **Investigation Anchor**: \`1/1\` source locations found by AST; claim remains unverified
• **Impact Severity**: High (Authentication Bypass vulnerability)

### Vulnerability & Call Topology Graph
\`\`\`
[HTTP Request]
       │
       ▼
[api/session.py] ──► [validate_token() in auth/token.py:142]  <-- [VULNERABILITY]
                             │
                              ├── Missing: \`expired_at < now\` check
                              └── Status: Accepted invalid/expired sessions
\`\`\``;
  }

  // 3. Validation / Mutation Testing Question
  if (
    s.includes("validation") ||
    s.includes("fail") ||
    s.includes("mutation") ||
    s.includes("test")
  ) {
    const m = AGENTS.find((a) => a.id === "mutation");
    const killed = m?.metrics?.[3]?.value ?? "26";
    const total = m?.metrics?.[1]?.value ?? "32";

    return `### Mutation Validation Analysis

Mutation validation failed to meet the required kill threshold. The generated patch killed **${killed} of ${total}** synthetic mutants (score **0.81**), falling short of the **0.92** threshold required for automated approval.

### Run Summary
• **Total Mutants Generated**: 32
• **Mutants Killed**: 26
• **Mutants Survived**: 6 (edge-case time delta & clock skew mutations)
• **Kill Rate**: 81.2% (Requirement: ≥ 92.0%)
• **Action Taken**: Retry budget exhausted after 3 attempts; escalated to human review.

### Mutation Score & Gate Progress
\`\`\`
Mutant Kill Rate:  [████████░░] 81.2%  (Target: 92.0%)
Equivalence Score: [███████░░░] 74.0%  (Target: 85.0%)
Fidelity Score:    [███████░░░] 78.0%  (Target: 90.0%)

Status: [FAILED GATES] ──► Triggered Draft PR Route
\`\`\``;
  }

  // 4. Blast Radius / Impact Question
  if (s.includes("blast") || s.includes("radius") || s.includes("impact") || s.includes("affect")) {
    return `### Blast Radius & Impact Analysis

The proposed change is localized to 2 core modules with a secondary impact on 3 downstream callers and 1 shared utility.

### Run Summary
• **Directly Modified**: \`auth/token.py\`, \`api/session.py\`
• **Direct Callers (Hop 1)**: \`api/routes.py\`, \`services/auth_service.py\`
• **Indirect Callers (Hop 2)**: \`handlers/login.py\`, \`handlers/oauth.py\`, \`handlers/refresh.py\`
• **Shared Utilities**: \`utils/jwt.py\`
• **Convergence Score**: **0.94** (High containment — no systemic leakage)

### Blast Radius Dependency Map
\`\`\`
[auth/token.py] (Primary Edit)
      ├──► [api/session.py] (Direct Dependent)
      │          ├──► [handlers/login.py]
      │          └──► [handlers/refresh.py]
      └──► [utils/jwt.py]
                 └──► [handlers/oauth.py]
\`\`\``;
  }

  // 5. Files Changed / Patch Question
  if (s.includes("file") || s.includes("change") || s.includes("patch") || s.includes("diff")) {
    return `### Modified Files & Patch Provenance

Candidate patch generated by Agent A7 (Code Generation) modified **2 files** following the Repair DAG sequence.

### Run Summary
• **Files Changed**: 2 files (\`+14 lines, −6 lines\`)
• **Repair Sequence**: \`auth/token.py\` → \`api/session.py\` → \`tests/test_auth.py\`
• **Patch Integrity**: Syntactically valid Python 3.11 AST

### Patch File Changes
\`\`\`
1. auth/token.py:
   + import time
   + if token.expired_at < int(time.time()):
   +     raise TokenExpiredError("Session expired")
   - pass

2. api/session.py:
   + except TokenExpiredError:
   +     return JSONResponse({"error": "expired"}, status_code=401)
\`\`\``;
  }

  // 6. Draft PR / Decision Question
  if (s.includes("draft") || s.includes("merge") || s.includes("decision") || s.includes("why")) {
    return `### Mergeability Decision Analysis

The run terminated in a **Draft PR** verdict. Although syntax and baseline tests passed, the trust composite score (**0.83**) was below the **0.90** auto-merge threshold due to surviving mutation mutants.

### Run Summary
• **Final Verdict**: \`Draft PR\` (Human Review Required)
• **Composite Trust Score**: **0.83 / 1.00**
• **Passed Circuit Gates**: 7 / 10
• **Failed Circuit Gates**: Mutation Gate (A8), Semantic Fidelity Gate (A9)

### 10-Gate Merge Circuit Status
\`\`\`
[✓] Gate 1: AST Validation       [✓] Gate 6: Static Lint
[✓] Gate 2: Test Reproduction    [✗] Gate 7: Mutation Kill (81%)
[✓] Gate 3: Verified Citations   [✗] Gate 8: Fidelity Score (74%)
[✓] Gate 4: Context Containment  [✓] Gate 9: Security Re-scan
[✓] Gate 5: Patch Compilation    [✗] Gate 10: Auto-Merge Threshold
\`\`\``;
  }

  // Fallback response with suggestions
  return `### ProoFix Run Intelligence

I can provide detailed evidence, run summaries, and visual graphs for any phase of this repair run.

### Suggested Questions
• **"Summarize agent run"** — Complete 11-stage pipeline summary & state flow graph
• **"Show the root cause"** — Anchored defect analysis & vulnerability graph
• **"Which files changed?"** — Patch diffs & DAG sequence
• **"How was the fix validated?"** — Mutation testing scores & progress meter
• **"Show blast radius"** — Dependency topology tree & caller map
• **"Why Draft PR?"** — 10-gate merge circuit status breakdown`;
}
