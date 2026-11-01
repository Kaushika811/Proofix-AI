<div align="center">

# ProoFix

### Evidence before merge.

ProoFix investigates repository bugs, proposes a focused patch, and builds the evidence a reviewer needs before that patch can move toward a pull request.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/UI-React-61DAFB?logo=react&logoColor=111827)](https://react.dev/)
[![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-E65100)](https://langchain-ai.github.io/langgraph/)
[![Redis](https://img.shields.io/badge/State-Redis-DC382D?logo=redis&logoColor=white)](https://redis.io/)

[Quick start](#quick-start) · [How it works](#how-it-works) · [API](#run-a-repair) · [Architecture](#architecture) · [Contributing](#contributing)

</div>

---

## The problem

A code model can produce a convincing diff in seconds. It cannot, by itself, prove that the reported bug was real, that the patch fixes the same behavior, or that the change did not introduce a regression.

ProoFix turns those questions into a workflow. Every repair carries reproduction evidence, root-cause citations, affected-file context, test results, mutation results, and a differential security scan before the system recommends a PR route.

## Problem evidence

Public research documents developer frustration with nearly correct AI output and the work required to verify generated changes. We collected the sources, their limitations, and the questions we still need to test in our [developer research notes](docs/USER_RESEARCH.md).

The notes include clearly labeled fictional scenarios from different company settings. They are not user interviews. ProoFix-specific review-time savings have not yet been measured.

## What you get

| Input | Output |
| --- | --- |
| A local repository or repository URL | A tracked repair run with live events |
| An optional issue hint | Reproduction and root-cause evidence |
| A target bug | An ordered repair plan and generated patch |
| A patch candidate | Validation results, trust axes and a proof bundle |
| A completed run | `auto_mergeable`, `diff_only`, or `draft` routing |

## Features

### Repository intelligence

- AST-backed semantic intent mapping for functions, modules and responsibilities.
- Import, caller/callee and dependency graphs for tracing how a bug travels through the codebase.
- Repository history, ownership and cached indexing to keep later stages focused on the relevant code.

### Evidence-led repair

- Reproduction checks before repair generation, with explicit status when the target environment cannot run the evidence.
- Root-cause investigation that links scanner findings, traces, tests and source citations.
- Blast-radius analysis that identifies affected files before the patch is written.
- Context engineering that gives the repair model the smallest useful evidence package instead of the whole repository.

### Controlled patching

- Fix DAG planning so dependent changes are applied in a deliberate order.
- Patch generation constrained by the plan, target files and repair context.
- Retry loops that return failed validation evidence to the patch stage with a concrete failure brief.

### Validation and review

- Target-test and regression-test validation after every candidate patch.
- Mutation testing with explicit handling for killed, survived, unavailable and stale results.
- Differential security rescanning against the pre-patch baseline.
- Mutation Certification Index (MCI) and four trust axes: Correctness, Security, Fidelity and Scope Safety.
- Proof bundles, gate traces and live run events that let a reviewer inspect how the decision was reached.

## How it works

```mermaid
flowchart LR
    R[Repository + issue hint] --> I[Understand]
    I --> X[Reproduce]
    X --> E[Investigate]
    E --> S[Scope]
    S --> P[Plan and patch]
    P --> V[Validate]
    V --> D{A10 decision}
    D -->|evidence complete| M[Auto-mergeable]
    D -->|review needed| Q[Diff-only / Draft]
    V -->|failed validation| P
```

The repair loop is deliberately evidence-first:

1. **Understand** — index the repository, parse AST structure and build dependency relationships.
2. **Reproduce** — run the target behavior and require confirmed evidence before repair work continues.
3. **Investigate** — correlate traces, scanner findings, source citations and repository history into a root cause.
4. **Scope** — calculate affected files and build focused context for the repair model.
5. **Plan and patch** — create an ordered fix DAG, then generate and apply a constrained patch.
6. **Validate** — run target and regression tests, mutation validation, and a baseline-vs-post-patch security rescan.
7. **Route** — verify description/diff fidelity, calculate trust axes, and choose the safest PR outcome.

## What makes ProoFix different

| Typical repair workflow | ProoFix |
| --- | --- |
| Starts with an LLM-generated edit | Starts by checking whether the reported behavior can be reproduced |
| Sends broad repository context to one model | Builds semantic, dependency and blast-radius context in separate stages |
| Treats a passing test as enough evidence | Combines target tests, regression tests and mutation behavior |
| Runs security tooling as an unrelated final check | Compares baseline and post-patch findings to identify what the patch introduced |
| Returns a diff and a confidence label | Returns a diff, citations, validation evidence, gate trace and routing decision |
| Hides unavailable tools behind defaults | Preserves `null` for missing measurements; absent evidence cannot pass a gate |
| Assumes automation should always merge | Keeps `diff_only` and `draft` as explicit, explainable review paths |

The key design choice is that **the patch is not the product**. The product is a patch plus enough inspectable evidence for another engineer to decide whether it should move forward. Agents have separate responsibilities, failed validation returns to patch generation, and humans retain control whenever the evidence is incomplete or contradictory.

## Trust model

A10 exposes four axes on a 0–100 scale:

| Axis | Evidence behind it |
| --- | --- |
| **Correctness** | Target/regression tests and mutation score |
| **Security** | Newly introduced findings after baseline reconciliation |
| **Fidelity** | Agreement between the repair description, citations and actual diff |
| **Scope Safety** | Blast graph and files requiring human review |

**Trust** is the mean of measured axes. A missing measurement is `null` and is excluded from the arithmetic; a measured zero remains a real result. The UI returns the composite on a 0–1 scale (`0.97` = 97%). These are evidence summaries, not formal proofs of program correctness. Scope Safety is intentionally a heuristic and should be read with the blast graph and hard-gate trace.

## Architecture

```mermaid
flowchart TB
    UI[React / Vite workspace] --> API[FastAPI]
    API --> WS[REST + WebSocket events]
    API --> G[LangGraph orchestrator]
    G --> A[Agent pipeline A0–A10]
    G --> T[Trust gates and routing]
    A --> R[(Target repository workspace)]
    A --> C[(Redis run state and event history)]
    A --> L[LLM gateway]
    T --> B[Proof bundle / GitHub PR decision]
```

### Agent map

| Stage | Code | Job |
| --- | --- | --- |
| A0/A0.5 | `repository_intelligence.py` | Repository indexing, caching and knowledge graph |
| A0.7 | `a0_7_environment.py` | Target environment and test-runner precheck |
| A1 | `a1_semantic_mapper.py` | AST-backed semantic intent mapping |
| A2 | `a2_dependency_analyzer.py` | Import and caller/callee relationships |
| A3 | `a3_static_analysis.py` | Baseline security and quality scan |
| A3.5 | `a3_5_reproduction.py` | Reproduction execution and stability evidence |
| A4 | `a4_evidence_investigator.py` | Evidence correlation and root-cause analysis |
| A5 / A5.5 | `a5_blast_graph.py`, `a5_5_context_engineering.py` | Blast radius and repair context |
| A6 | `a6_fix_dag_planner.py` | Ordered modification plan |
| A7 | `a7_code_generation.py` | Patch generation and application |
| A8 | `a8_mutation_validator.py` | Tests and mutation validation |
| A9 | `a9_security_rescan.py` | Differential post-patch security scan |
| A10 | `a10_mci_scorer.py`, `a10_routing.py` | Fidelity, trust gates and PR routing |

## Quick start

### Requirements

- Python 3.11+
- Node.js and npm
- Redis 7+ (local or Docker)
- An LLM provider, or stub mode for local smoke tests

### 1. Install the backend

```bash
git clone https://github.com/CHEZHIYAN0277/Proofix.git
cd Proofix
python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
cp .env.example .env
```

For semantic NLP features, install the optional spaCy model:

```bash
python -m spacy download en_core_web_sm
```

### 2. Start Redis and the API

```bash
docker compose up -d redis
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

For a credential-free smoke test, set `STUB_MODE=true` in `.env`. For real patch generation, configure the provider selected by `LLM_PROVIDER` and its server-side key or local endpoint.

### 3. Start the workspace

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

## Run a repair

Create a run with a local path or repository URL:

```bash
curl -X POST http://127.0.0.1:8000/runs \
  -H 'Content-Type: application/json' \
  -d '{"repo_path":"/absolute/path/to/target-repository","issue_hint":"Uploading a shell script is accepted as an image","target_test":"tests/test_upload.py::test_reject_shell_script","expected_behavior":"Reject .sh uploads","target_file":"app/uploads.py"}'
```

For a reported issue, provide `issue_hint` and either an exact failing pytest
`target_test` or `expected_behavior`. The suspected Python `target_file` is optional.
With an existing test, ProoFix selects it from the full-suite baseline; another
test's failure cannot confirm the reported issue.

Without an existing test, ProoFix makes one bounded model request to generate a
regression test from the issue, expected behavior, and indexed source. The test
must execute and fail a behavioral assertion before repair is allowed. Import,
fixture, and API errors, already-passing tests, and unavailable generation leave
the issue unverified. This initial generator supports one undecorated pytest
test with assertions; tests requiring mocks, filesystem writes, or network
access are outside its supported scope. Its syntax checks are not a sandbox.

The generated test is frozen by content hash and rerun after the patch. A
skipped, missing, or changed test cannot count as a passing acceptance check.
The report includes its source, rationale, hash, and before/after results for
human review. A candidate PR includes the test, and the proof command recreates
the same test on both commits. The assertion is AI-generated and must still be
reviewed against the intended behavior; successful execution alone does not
establish that the specification is correct.

If reproduction or source targeting cannot be established, ProoFix produces no
targeted patch or PR. Leave all issue fields empty for repository-wide analysis.

The [generator benchmark](benchmarks/generator/README.md) records local
calibration and a limited live Mistral assessment on three seeded defects.
The latest live sample accepted three tests; each failed by assertion on the
original, passed the reviewed correct fix, and failed by assertion on the
incorrect and unrelated patches. Semantic validity remains unreviewed.
Earlier samples and separate vacuous-test and ambiguity results are preserved
in the benchmark report.

Then inspect the run:

```bash
curl http://127.0.0.1:8000/runs/<run_id>
curl http://127.0.0.1:8000/runs/<run_id>/events
```

Live updates are available at `ws://127.0.0.1:8000/ws/runs/<run_id>`. The API also exposes UI projections under `/api` and proof bundles under `/runs/<run_id>/proof/<issue_id>`.

## Repository map

```text
backend/       FastAPI app, agents, orchestrator, models, services and security
frontend/      React/Vite workspace
docs/           API contracts, workflow notes and proof examples
 tests/        Unit and integration tests
pyproject.toml Python package and test configuration
.env.example   Environment reference
```

## Verify locally

```bash
# Backend
pytest -q

# Frontend
cd frontend
npm test
npm run build
npm run lint
```

The validation pipeline uses Pytest, Mutmut, Bandit, Ruff and Semgrep when available in the target environment. Scanner availability is surfaced in the result; it is never silently converted into a passing security measurement.

### GitHub publication and mutation execution

For live PRs, set `GITHUB_DRY_RUN=false` and configure `GITHUB_TOKEN`, then
restart the backend. Each repair run publishes to **the submitted checkout's
GitHub `origin`**, targeting that repository's default branch. The token needs
repository contents and pull-request write access, plus permission to publish
the verification workflow. Local repositories need a GitHub origin; publishing
through a separate fork is not currently supported. HTTPS pushes use the token;
SSH origins require working SSH credentials.

A10 prepares the tested commits and report locally; it does not push or create
GitHub PRs. Click **Create draft PR** to publish the prepared repair. The button
shows **Creating draft PR…**, then **Open draft GitHub PR** after success.
Publication always creates a draft, independently of the trust score or repair
routing decision. Failed requests can be retried; the same branch and commits
are reused, and an existing open PR is returned instead of duplicated.

The prepared checkout is retained for the run-state lifetime (seven days by
default), cleaned after successful publication, and expired copies are swept
once per minute while the backend is running. The checkout must remain available
on the backend filesystem: use a shared persistent temporary directory for
multiple workers/hosts. If it expires or the host discards it, start a new run.
Older runs without a prepared checkout cannot be published through this button.

A8 uses mutmut 3.6+ in a fresh temporary copy, scopes mutation to all patched
Python files, and preserves the validated checkout for publication. The copy
gets its own mutation configuration and cache, including when the repository
already configures mutmut in TOML. Known baseline failures are deselected while
the acceptance test remains included. Timeouts stop the tool's process group;
unavailable or inconclusive execution does not become a passing score.

The real mutation and local-Git integration checks can be rerun with:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/integration/test_mutation_target_scope.py tests/unit/test_proof_publication.py tests/unit/test_subprocess_lifecycle.py
```

These checks execute mutation, commits, and pushes to a local bare remote.
GitHub API calls are mocked, so they do not demonstrate live GitHub publication.

## Safety notes

- Keep provider credentials, GitHub tokens and speech keys in the backend environment.
- Keep `GITHUB_DRY_RUN=true` while developing.
- Repository commands use guarded subprocess helpers, path checks and filtered environments. For hosted execution of untrusted repositories, add OS-level sandboxing.
- A patch cannot be considered merge-ready without confirmed reproduction, passing target and regression tests, measured correctness and security, and no phantom changes.
- Draft and diff-only outcomes are expected review paths, not errors.

## Documentation

- [`docs/WORKFLOW.md`](docs/WORKFLOW.md) — end-to-end workflow
- [`docs/TRUST_GATING.md`](docs/TRUST_GATING.md) — routing and hard gates
- [`docs/V1_API_DATA_CONTRACT.md`](docs/V1_API_DATA_CONTRACT.md) — API and state contract
- [`docs/PRODUCTION_CERTIFICATION.md`](docs/PRODUCTION_CERTIFICATION.md) — deployment checks
- [`docs/KNOWLEDGE_GRAPH.md`](docs/KNOWLEDGE_GRAPH.md) — repository graph model

## Contributing

1. Create a focused branch.
2. Keep changes scoped to one behavior or agent contract.
3. Add or update meaningful tests for behavior changes.
4. Run the backend and frontend checks before opening a pull request.
5. Include evidence for changes that affect routing, validation or security.

## License

This project is intended to be released under the MIT License.

Proofix Testing
