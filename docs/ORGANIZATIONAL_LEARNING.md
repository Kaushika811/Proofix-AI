# Organizational Learning System (Phase 6)

The platform learns; the model does not. Every value in this layer is derived by
counting, matching or aggregating observations that already happened. No
embeddings, no vector store, no fine-tuning, no extra LLM call. Additive
throughout: no LangGraph change, no `RunState` change, no agent responsibility
changed, no existing API touched.

---

## 1. Architecture

```
Repository ─► Repository Intelligence ─► Knowledge Graph ─► Context Engineering
                                                                    │
                                                          Enterprise Security
                                                                    │
                    ┌────────── Learning System ──────────┐
                    │                                      │
    BEFORE a repair │  knowledge_index ──► prompt context  │  AFTER a repair
                    │  style · framework · templates       │  repair_memory v2
                    │  organization · reviewer guardrails  │  outcome · review
                    │                                      │  pattern mining
                    └──────────────────┬───────────────────┘
                                       ▼
                                  LLM Gateway ─► Repair
```

Positioned after security by design. Everything learning adds to a prompt is
*still* sanitized before egress — learned directives are conventions rather than
content, so nothing is normally redacted, but the ordering means a learning bug
could never become a disclosure.

---

## 2. Learning pipeline

| When | What happens |
|---|---|
| A0.5 runs | `observe_repository` refreshes style + framework profiles from the index's **existing** parse |
| Before A7 | `context_for` returns directives; A7 appends them as a labelled block |
| After A10 | `learn_from_run` extracts metadata, re-mines templates, updates profiles |
| Human acts | `record_review` / `record_outcome` (via API) close the loop |

Every entry point catches its own exceptions. A learning fault means the
platform learns nothing from that run — never that the run fails.

---

## 3. Repair Memory v2

Structured metadata for every completed repair: identity, issue signature, bug
and root-cause category, target files and functions, context shape, patch
*shape*, validation/mutation/security results, reviewer decision, outcome, merge
and rollback status, framework, language.

**The privacy guarantee is structural.** `RepairKnowledge` has no field that can
hold source, a diff or a prompt — `test_no_learning_model_can_hold_source`
asserts this across every model in the module. `summarize_patch` converts a
bundle into `"2 file(s), +9/-3 line(s), 3 function(s) in scope"` and discards the
content before a record is built. A second guard refuses a multi-line or
oversized `patch_summary`, which is the only remaining route content could take.

The Phase 3 `services/repair_memory` is untouched and still answers "have we
fixed this exact function before?" during a run. This module answers the
different, durable question.

---

## 4. Organization Memory

Aggregates across repositories, **by repository not by file** — counting files
would let one large monorepo package outvote every other service.

Learned: naming, testing conventions, preferred libraries, architecture style,
error handling, dependency injection, logging, authentication, validation,
folder conventions.

A convention needs ≥2 repositories agreeing at ≥60% before it is asserted; below
that it stays `unknown`, because two repositories agreeing is as often
coincidence as convention. Standard-library modules are excluded from library
preference — every repository imports `os`, so counting it would top a list
meant to express *choice*.

---

## 5. Pattern mining

**Exact-key grouping, deliberately.** Repairs group by
`(bug_category, root_cause_category)` — both already deterministic categories
from a fixed vocabulary. No similarity metric, no clustering, no threshold to
tune; running it twice gives byte-identical output.

Fuzzy grouping was the alternative and is worse: a near-miss cluster would merge
"expiry comparison in auth" with "boundary condition in pagination" and then
advise the wrong approach *with aggregate evidence behind it*.

A template carries approach, guardrails, validation hints and its honest track
record — never a patch body. Guardrails are partly **learned**: what reviewers
repeatedly objected to in a family becomes a constraint on future repairs in it.

Templates with poor success rates are **kept, not deleted**: "this has failed 4
of 5 times" is more useful to a ranking decision than silence.

---

## 6. Framework learning

Evidence-weighted across three kinds: imports (1.0), manifest dependencies (0.6),
file/directory markers (0.3). Twelve frameworks across five languages.

**Markers corroborate; they never establish.** `models.py` and `routes/` are
ordinary directory names — treating them as evidence reported Django in every
repository with a models module, which is exactly what a test caught. A framework
now requires at least one import or declared dependency.

The conventions each framework implies are a fixed table, not mined. FastAPI's
routing convention is a property of FastAPI; mining it from three repositories
would give a worse answer than writing it down. What *is* learned is which
framework this repository uses, and how confidently.

---

## 7. Outcome learning

Append-only history, not a mutable status. A repair that was accepted, merged,
then rolled back has three recorded transitions, and the rollback does not erase
the acceptance — merged-then-reverted is a specific failure mode, distinguishable
from rejected-outright.

`suggested → accepted → merged → production_success | reverted | rolled_back`

Success rates are damped by sample size everywhere. `rollback_rate` is the metric
that matters most for trust: a high merge rate with a high rollback rate is worse
than a low merge rate. Strict transition validation exists but is off by default —
an external webhook may legitimately arrive out of order, and refusing it would
lose the information entirely.

---

## 8. Knowledge Graph extensions

Seven node types — `framework`, `style`, `review`, `outcome`, `organization`,
`pattern`, `template` — and seven edge types:

```
Repository ──USES_FRAMEWORK──▶ Framework      Repair ──RESULTED_IN─────▶ Outcome
Repository ──FOLLOWS_STYLE───▶ Style          Repair ──INSTANCE_OF─────▶ Pattern
Repository ──BELONGS_TO──────▶ Organization   Repair ──APPLIES_TEMPLATE▶ Template
Repair     ──REVIEWED_BY─────▶ Review
```

Attached by an opt-in adapter, exactly like the seven Phase 4 adapters. The graph
must remain buildable with no learning state at all — which is what keeps every
Phase 4 query working unchanged when Phase 6 is off.

---

## 9. Files

**New (14):** `models/learning.py`, `backend/learning/` (`repair_memory`,
`organization_memory`, `pattern_mining`, `framework_learning`, `outcome_learning`,
`style_learning`, `review_learning`, `learning_engine`, `knowledge_index`,
`metrics`, `graph_adapter`), `services/learning_pipeline.py`,
`api/routes/learning.py`.

**Modified (6):** `config.py`, `models/knowledge_graph.py` (node/edge types),
`agents/a7_code_generation.py` (appends a directive block), `agents/repository_intelligence.py`
(observes profiles), `orchestrator/nodes.py` (post-route extraction), `main.py`.

**Unchanged:** LangGraph, `RunState`, A5.5 ranking, every existing endpoint.

---

## 10. Performance

Measured on this repository (127 files, 500 accumulated records):

| Operation | Time |
|---|---|
| `learn_from_run` | **0.10 ms avg, 0.23 ms max** (target <100 ms) |
| `observe_repository` (once per run) | 42 ms — reuses A0.5's parse, adds no AST work |
| Mine templates + patterns (500 records) | 0.80 ms |
| Build knowledge index | 0.73 ms |
| Full analytics dashboard | 9.0 ms |

Three orders of magnitude inside the budget, because every operation is counting
over in-memory data.

---

## 11. Learning guarantees

1. **Deterministic** — identical inputs give identical templates, profiles and
   scores. Asserted per subsystem.
2. **Explainable** — every learned property carries its observation count and
   distribution; every score names its components; every directive names its
   source.
3. **Advisory only** — learning contributes prompt *context*, never a ranking
   weight, gate or threshold. A5.5's ranking logic is unchanged.
4. **Runtime evidence wins** — the directive block says so in its own text, and
   is appended after the evidence, not before.
5. **Sample-damped** — a convention seen twice is reported at low confidence, not
   asserted. One merged repair is not a 100% success rate.
6. **Failure-isolated** — every entry point catches its own exceptions.
7. **Honest about absence** — unmeasured score components are *excluded* from the
   mean, not counted as zero. A repair awaiting review is not one reviewed badly.
8. **No LLM, no embeddings** — asserted by
   `test_no_learning_module_imports_an_llm`, which greps every module.

---

## 12. Privacy guarantees

Never stored: raw prompts, secrets, PII, source files, diffs, whole repositories.

Enforced three ways: **structurally** (no model field can hold content),
**at the boundary** (`summarize_patch` discards content before a record exists),
and **by assertion** (`PrivacyViolation` on a multi-line or oversized summary).
Reviewer reasons are collapsed to one line and length-bounded, because a review
comment can contain a pasted diff.

Enterprise Security policies still apply: learning sits before the gateway, so
its contribution passes through sanitization like everything else.

---

## 13. Tests

314 new, across 4 files:

| File | Tests |
|---|---|
| `test_learning_engine.py` | 132 |
| `test_learning_style_framework.py` | 80 |
| `test_learning_repair_memory.py` | 69 |
| `test_learning_pipeline.py` | 33 |

Full suite: **1681 passed**, 1 pre-existing environmental failure
(`test_reproduction_stability_gate` needs a `vulnapi` git fixture).

---

## 14. Configuration

```python
learning_enabled: bool = True             # off ⇒ exact Phase 5 behaviour
learning_organization_id: str = "default"
learning_max_directives: int = 18
learning_influence_prompts: bool = True   # accumulate without influencing prompts
```
