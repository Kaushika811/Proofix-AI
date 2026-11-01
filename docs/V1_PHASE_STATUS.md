# V1 Phase Status

Audit date: 2026-08-08. Legend: ✅ COMPLETE · 🟡 PARTIAL · 🔴 MISSING · ⛔ BLOCKED

---

## Status table

| Phase | Name | Status | V1 Frontend | Backend | Tests | Blockers |
|---|---|---|---|---|---|---|
| **0** | V2 decommission & V1-only cleanup | ✅ COMPLETE | V2 deleted + committed; dead fixtures gone; `prettier --check` clean | comment + test rename done | backend 1954✓ / frontend 20✓ | none |
| **1** | Run lifecycle & execution experience | 🟡 PARTIAL | terminal states ✅; error/retry ✅; reconnect ✅; **skeletons 🔴** (B-F08) | lifecycle events ✅; severity absence ✅ | mapping ✅, rendering ✅ (17 component tests) | none |
| **2** | Repository Intelligence surface | ✅ COMPLETE | A0.5 card + visualization ✅; header identity strip ✅ | A0.5 on the `v1` surface ✅ | 19 card tests + 3 identity tests | none |
| **3** | Investigation / Evidence / Blast Radius | ✅ COMPLETE | A3.5/A4/A5 cards + visualizations | agents + citation verification ✅ | ✅ | none |
| **4** | Context Engineering / Repair Planning | ✅ COMPLETE | A5.5 card + viz ✅; context panel (ranking, redactions) ✅; planner shows edge reasons ✅ | A5.5 on the `v1` surface ✅ | 15 card tests + 6 panel tests | none |
| **5** | Patch Generation | ✅ COMPLETE | diff view + patch panel ✅ | B-B03/06/07/08 ✅ | 19 A7 tests + 6 diff-parser + 5 panel | none |
| **6** | Validation & scoring correctness | ✅ COMPLETE | mutation card ✅; null axes render "Not measured" | B-B01 ✅ (`measurement.py`); B-B02 ✅ (line-free finding key); B-B16 ✅ (absent scanner ≠ 100) | 15 A9 tests + `measured_mean` arithmetic + projection | none |
| **7** | Final decision & report | ✅ COMPLETE | every draft reason on screen ✅ | B-B10 ✅ single writer; B-B11 ✅ shim deleted; B-B09 ✅ decided | 16 authority tests + 4 UI | none |
| **8** | Production hardening | 🟡 PARTIAL | reduced motion ✅, poll cost ✅ | B-B04 ✅, B-B05 ✅, B-B14 ✅, B-B15 ✅, B-B13 broadcaster ✅ | 23 new | **B-B12 sandbox + B-B13 checkpointer need a decision** |
| **9** | Final QA / certification | 🟡 PARTIAL | tsc clean, build ✅, 66 tests ✅; **browser pass not run** | 5 real-GitHub E2E runs, all 3 terminal outcomes ✅ | backend 2078✓/1 env-fail; frontend 66✓ | **B-B17 must be fixed before certifying** |

---

## What "complete" rests on

**Phase 9 🟡** (2026-08-09) — the E2E half ran and found four defects; the
certification half cannot be signed while one of them is open. Full record in
`docs/PRODUCTION_CERTIFICATION.md` §13.

Five real public GitHub repositories, nothing prepared, real LLM calls, real
subprocesses, `GITHUB_DRY_RUN=true`. All three terminal outcomes reached:
`unsupported` (node), `no_manifest`, `not_prepared` (`freezegun`), **completed**
(`toolz`, full twelve-agent chain, three retries, 35 s), and **failed** (clone
of a nonexistent repository).

Before any of it, a bare `"python"` at seven subprocess call sites was replaced
with `sys.executable`. That string resolved through `PATH` — not the interpreter
ProoFix runs under — so the probe could report `No module named 'pytest'` inside
a message claiming to describe *"the interpreter ProoFix would run its tests
with"*. Every blocked verdict depended on which interpreter the server happened
to inherit; a test now fails if any runner reintroduces a bare interpreter.

**The routing layer held. The evidence layer did not.**

* **B-B17 (S1, open)** — `scan_text` de-anchors the `_VALUE_RULES` email pattern
  and runs it unanchored, so `[^@\s]+` spans slashes and any absolute path
  containing `@` is matched *in full* and replaced. On Homebrew macOS the
  interpreter path is `python@3.14` by construction, so every traceback entering
  the stdlib loses its frames to `<REDACTED_EMAIL>` — with the guard reporting
  `masked` and a tidy ledger, believing it protected an email. Stack frames are
  the pipeline's highest-weighted ranking signal (`STACK_FRAME_EXACT = 1.00`),
  so this deletes the strongest evidence *before* ranking runs. Observed
  consequence: `target_function: None`, an unrelated target file, and four
  rejected patch attempts.
* **B-B18 (S2, open)** — the mirror of the Flask false-block this project fixed
  last round. `--collect-only` succeeding does not mean the project is
  installed, and `toolz`'s sole failing test is its own installation self-check.
  A0.7 said `ready`; A3.5 recorded `CONFIRMED` at 90 %.
* **B-B19 (S2, open)** — `unsupported` and `no_manifest` both render
  "Environment not prepared", contradicting the reason printed beneath them.
* **B-B20 (S4, open)** — `currentAgent` is stale on every terminal run.

**Measured, not asserted.** Per-agent cost attribution by `run_id` works live
(B-B05 confirmed outside unit tests): 9 calls, 25,290 tokens, $0.0094, 33 s.
Two assumptions in `CLAUDE.md` are now contradicted by measurement — A7 is *not*
the most expensive call (A4 cost 31 % more), and A4's reinvestigation loop spent
12,647 tokens across three calls to finish with **zero** verified citations,
exactly the discard-the-verification-result defect §3 predicts.

**Not verified:** the browser pass. No Playwright exists in this repository and
`vitest` runs on jsdom, so "console clean, no error boundary, responsive, a11y"
was not re-established this round — earlier rounds' results stand on their own
evidence. Sandboxing (B-B12) and the checkpointer (B-B13) remain open by
decision.

**Phase 3 ✅** — A3.5, A4, A5 all execute, publish typed payloads, have V1 cards
with bespoke visualizations, and are covered by backend tests. Citation
verification is deterministic and tested. Nothing outstanding that is
V1-specific.

**Phase 1 🟡** — the terminal-state half was already done and verified against
three real runs. The transport-resilience half has now landed: per-model
`{error, loading, loaded}` with a visible retry per panel (B-F01), backoff
WebSocket reconnect that never infers completion from a close (B-F03), the
`blocked` status tone (B-F05), `RunReport`'s required `report` prop (B-F02) and
`severity: "not measured"` (B-F09). What remains of B-F08 is cosmetic: there are
still no per-panel loading skeletons. The honesty defect it was filed for — a
failed fetch rendering as an empty one — is fixed; the polish is not.

**Phase 8 🟡** (2026-08-09) — six of eight items closed. The two left are
decisions, not refactors, and are described at the end.

* **B-B04** the privacy guard covered extracted code and stopped there, and
  A5.5 is the only point where secrets are masked before an LLM call, so its
  coverage *is* the privacy claim. `acceptance_criteria`, `contracts`,
  `validation_requirements` and `patch_constraints` all reach the prompt and
  none were scanned — they are built from exception messages and test names,
  exactly where a credential leaks. Separately, the scans that *did* run on free
  text discarded their findings, so a secret masked in a traceback was reported
  as `clean` with an empty ledger. Status is now recomputed from the full
  ledger, `failed` still winning.
* **B-B05** was already fixed and simply untested, which is how it kept reading
  as open. Pinned end to end, including an AST scan that fails if any call site
  omits `run_id`.
* **B-B13 (broadcaster half)** turned out to be a deletion. Redis pub/sub was
  already complete; the in-memory `WSBroadcaster` delivered every event a second
  time to same-process clients — hidden by frame dedupe, useless to any other
  replica. The socket loop was driven by that queue and its "drain before
  checking terminal" ordering is load-bearing, so the Redis listener now signals
  an `asyncio.Event` the loop waits on instead.
* **B-B14** clone cleanup runs in a `finally`, covering completed, blocked and
  failed alike — the leak was worst on the paths nobody thinks about. It refuses
  anything outside the system temp root or without the `proofix_` prefix.
* **B-B15** `/events` could only expose a run's tail. `after` is an exclusive
  cursor on `sequence`; its *presence* selects forward reading, so existing
  callers keep the "most recent page" behaviour unchanged.
* **B-F06** the CSS reduced-motion block was already comprehensive; what it
  cannot reach is `requestAnimationFrame` count-ups and
  `scrollTo({behavior:"smooth"})`, where the explicit option beats the
  stylesheet. Both honour the setting now.
* **B-F10** change detection stringified both sides every poll; the previous
  value cannot have changed since it was accepted, so half the work was pure
  waste.

**Still open, both needing a decision rather than a refactor:**

* **B-B12 — sandboxing.** pytest, bandit, semgrep, mutmut and ruff run against
  cloned code with the host interpreter. This blocks any hosted multi-tenant
  deployment. Closing it means choosing an isolation technology (container,
  gVisor, nsjail, bubblewrap, microVM), each with different deployment
  requirements, and rewriting `subprocess_runner` plus every caller to marshal
  work in and results out. That is a product and infrastructure decision.
* **B-B13 — checkpointer.** `MemorySaver` means no run resumes after a restart.
  Fixing it needs either `langgraph-checkpoint-redis` as a new dependency or a
  hand-written saver over the existing Redis client.

**Phase 7 ✅** (closed 2026-08-09) — `force_draft_pr` has one owner, and every
reason a run is a draft is on screen.

**B-B10.** The flag was written from three places — A3.5 on failed
reproduction, A4 on unverified citations, `trust_gating` on exhausted
validation. A flag written from three places has no single moment at which it is
true, and answering "why is this a draft?" meant reading three files and knowing
which had run. Both agent writes were *derivable* from state those agents
already published (`reproduction.status`, `root_cause.evidence_incomplete`), so
the flag is now computed from that evidence once, immediately before routing.
Agents record observations; the gate decides what they mean — the same split the
rest of the pipeline follows. It is assigned rather than or-ed, so a stale
`True` cannot survive a pass that finds no reason. A test greps the backend for
a second writer, because reintroducing one would otherwise be silent.

Making the reasons enumerable is what put them on screen. `draft_reasons`
returns `(code, detail)` pairs; A10's `review_note` carries only the *first*
reason routing hit, so a run blocked for three showed one and the rest were
unrecoverable from any client. The report now publishes all of them, from the
same computation that sets the flag, so the explanation and the routing cannot
disagree.

**B-B11.** `citation_validator.py` is deleted. Its `validate_all_citations*`
functions returned `verify_all_citations_with_metrics` unchanged; only
`coerce_llm_citations` did work, and it now lives beside the verification it
feeds. Its two verification tests moved to the verifier's own test file, where
they were always testing.

**B-B09 — decided: surface, do not enforce.** `reproduction_gate` still flows
unconditionally to `investigate`. Halting would save two LLM calls on an
unreproducible run but discard the diff, which `CLAUDE.md` documents as
deliberate value ("you still get a diff to look at"), and A0.7 already halts the
common case — a repository whose tests cannot run at all never reaches here. The
cost is now visible instead: the reproduction failure appears as a named draft
reason rather than being implied by a flag.

**Phase 5 ✅** (closed 2026-08-09) — the diff is the product, and it was
reachable only as two filenames. `/runs/{id}/patch` had served both sides of
every file all along.

Four backend defects closed, all the same family as Phase 6's: the pipeline
stating something it had not established.

* **B-B03** was worse than filed. `_expected_from_test_name` matched on
  `"exp" in root_cause_text` — which hits "unexpected", "export", "explicit",
  "experiment" — so a null-dereference repair was told its expected behaviour
  was "Reject tokens whose exp timestamp is earlier than time.time()", in the
  one prompt section that says what success looks like. Expected behaviour is
  now built from evidence only: the failing test, the exception it raises, and
  A4's citation-verified conclusion. Thin evidence produces a thin prompt
  rather than a guess. The same literals are gone from the retry brief, and
  `github_repo_name` no longer defaults to the fixture repository — an
  unconfigured target now refuses to publish instead of naming `vulnapi`.
* **B-B06** rollback. The subtlety is that a plan producing *no patch* is
  ordinary — most scope files need no change — so that must never trigger a
  restore. What must is an exception partway through: writes already on disk
  are real, `state.patch_bundle` is never set, and A8 then validates changes no
  bundle records. A7 now snapshots every file it writes and restores them on
  exception. The redundant `write_text(original)` immediately before the patch
  write is gone with it.
* **B-B07** was mostly already handled — `validate_patch_integrity` rejects a
  no-op, so a stub output never became a candidate. What remained: an LLM
  exception returned `apply_stub_plan`, the integrity gate rejected it as
  `no_op`, and that overwrote `retry_reason` — so a call that never completed
  was recorded as "the model returned an unchanged file". A failed call is now
  a failed attempt, with the error preserved.
* **B-B08** the lease was 60 s against one to three LLM calls. It is now 600 s
  and renewed per plan; losing it stops further writes rather than risking a
  concurrent writer in the same clone.

The diff renderer is a plain table, deliberately no `shiki` — that dependency
was removed once already and re-adding ~2 MB to colour keywords buys less than
add/remove colouring. Line numbers come from the hunk headers and advance per
side, so they are the file's own numbering; `+++`/`---` are classified before
`+`/`-` content, or every file header reads as two extra changed lines.

**Phase 4 ✅** (closed 2026-08-09) — A5.5 is the pipeline's **privacy
boundary**: the only point where secrets are masked before an LLM call. It ran
on every run and published to the `v2` surface alone, so the evidence that
nothing secret reached the model had no consumer at all.

Split by cost, deliberately:

* The **card** summarises from the agent's event payload — target, token
  reduction, guard status, counts — so the polled `/agents` response stays
  small. `token_reduction` is `null` rather than `0.0` when unmeasured, and
  `privacy_guard_status: "failed"` is never rounded to `clean`: it means the
  guard itself errored, so nothing may be assumed about what got through.
* The **context panel** reads `/runs/{id}/context` once and renders the full
  ranking with its per-signal breakdown, plus the redaction ledger. A ranking
  you cannot inspect is an oracle, and A5.5's whole design is that its
  selection is deterministic and reviewable.

**A 404 is an answer, not a failure.** `/context` 404s until A5.5 publishes.
`apiFetch` now throws a typed `ApiError` carrying the status, and
`getRunContext` maps 404 to `null` while every other status still rejects. The
panel renders three distinct states — error (retryable), pending (404), loaded
— because "Could not load. Retry" over a stage that had not run is the same
class of lie as a failed fetch rendering as an empty one (B-F01).

The package is fetched write-once: A5.5 runs a single time per run, so once
read the poll skips the request rather than growing by a round trip for an
answer that cannot change.

A6's planner card now publishes the *reason* recorded for each dependency edge
and names the conflicting fixes. Only counts were published before, and a
number cannot be disagreed with.

**`surface` is now inert.** Both values return the same cards. The parameter is
still accepted and validated because it is in the published schema, but
`_V2_ONLY` is deleted rather than left as an empty category, and a registry test
asserts the two surfaces stay identical so the split cannot quietly reopen.

**Phase 2 ✅** (closed 2026-08-09) — A0.5 moved from the `v2`-only surface onto
the product surface, so the layer whose entire purpose is reusing work across
runs is no longer invisible to the person paying for it.

The card leads with **how the index was obtained** — cache hit, incremental, or
full rebuild — because that is the only fact distinguishing A0.5 from A1
re-reading the same files. Its visualization is a timing breakdown of the
phases that actually ran (a zero-millisecond phase is omitted, not drawn as a
sliver) plus node/edge/callable/commit/remembered-repair counts.

A0.5 is projected unlike any other agent, and two things followed from that:

* It never mutates `RunStateModel` by design, so `_visualization_for` now takes
  the agent's events — its numbers exist only in its own emitted payload.
* Its failure is caught so the pipeline can continue, so it emits `started` and
  never a terminal event. `_agent_status` read that as "running" forever on a
  finished run; a terminal run now resolves an unfinished agent to `failed`
  when `state.errors` names it and `skipped` otherwise.

Disabled A0.5 (`repository_intelligence_enabled=False`) emits nothing and
renders as absence — `skipped`, no visualization, no zeroed index.

The header identity strip (`repositoryId` / `headSha` / `repositoryHash`)
renders values the backend had been publishing all along. Fields the run never
observed are omitted rather than dashed.

**`surface` does not collapse yet.** The two values now differ by A5.5 alone;
retiring the parameter is Phase 4's to finish.

**Phase 6 ✅** (closed 2026-08-09) — every number that gates a merge is now a
real measurement, in both directions.

B-B01's half was already done: `services/measurement.py` holds the tri-state
semantics, `AxisScores` is nullable, and `measured_mean` divides by the
measurements rather than by four.

What closed the phase:

* **B-B02** — A9's finding identity dropped the line number. The key was
  `file:line:message[:50]`, so a patch inserting a line above a pre-existing
  finding shifted it, changed its key, and had it rejected as newly introduced.
  Identity is now `(tool, normalized path, normalized message)` with the
  message no longer truncated — the 50-character prefix collided distinct
  bandit issues, and a collision there *accepts* a real vulnerability.
  Comparison is by multiplicity (`Counter`), not set difference, so a file that
  went from one hardcoded password to two still reports one new finding.
* **B-B16** (found during the phase) — the inverse of B-B01. A9 returned `[]`
  both for "scanned, nothing found" and "bandit could not be executed", and the
  score is derived from that count, so an absent scanner produced
  `security_score = 100.0` and cleared the 90.0 auto-merge gate. Scanners now
  report `(executed, findings)`; with none executed the score is `None`, which
  `meets_threshold` refuses. Two projection sites that read "A9 produced a
  result" as "A9 measured one" were corrected with it.

---

## Test suites

- **Backend** — 2071 pass, 4 skipped. One pre-existing environmental failure:
  `test_reproduction_stability_gate` asserts `get_head_sha(vulnapi)` is
  non-empty, but the `vulnapi/` fixture has no `.git`, so it returns `""`. The
  test's skip guard only checks that the directory exists, not that it is a git
  repo, so it fails rather than skipping. Not caused by any phase work.
- **Frontend** — 66 pass across 7 files. Component tests run on jsdom, opted
  into per file with a `// @vitest-environment jsdom` docblock; shared shims
  live in `src/test/setup.ts`. Test config is `vitest.config.ts`, separate from
  `vite.config.ts` because the TanStack Start plugin must not run under Vitest
  (it resolves a second React copy and every hook reads a null dispatcher).
