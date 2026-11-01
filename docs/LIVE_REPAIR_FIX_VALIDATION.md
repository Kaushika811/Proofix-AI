# Live repair fixes — validation on 26 October 2026

The five issues identified in the live audit have been addressed in the working tree. Existing user changes were preserved. No deployment or GitHub publication was performed.

## Changes

- Causality comparisons use consistent pytest verbosity and compare the assertion's primary comparison, relative file and line. Expanded diagnostic operands and list diff formatting no longer determine failure identity. Pinned test/source integrity, repeated execution, original failure, patched success and counterfactual failure checks remain required.
- A8 checks causality after scoped tests pass. A still-failing candidate retains the normal bounded retry decision instead of being stopped by the successful-patch acceptance gate.
- The LLM gateway retries transient ConnectError, ReadError, WriteError and RemoteProtocolError failures. Existing permanent HTTP error exclusions and retry limits remain intact.
- Failed run creation displays the API error, retains the repository input and permits retry. No fabricated run ID is used. Empty backend run IDs are rejected, and stale launch completions are ignored after navigation.

## Verification

- 53 focused backend tests passed, including real pytest replays of the expired-token and pagination causality failures, changed assertion/location rejection, changing diagnostic operands, bounded failed-patch retry, and transport retry.
- 79 additional backend recovery, verification, live-repair classification, guard and publication tests passed.
- 44 frontend tests passed, including two consecutive identical API failures followed by successful creation and an empty-run-ID rejection.
- TypeScript checking and the frontend production build passed. The build retains warnings about chunk size and an ineffective dynamic import.
- A real Chromium check against the local frontend intercepted two create-run requests with HTTP 503, confirmed the input and workspace URL were retained, then accepted a third request and verified navigation to that returned ID. No page errors occurred. API responses were controlled for this UI check; this is not a deployed-backend check.

## Live model validation

The actual agent graph used Mistral codestral-latest with stub mode off, fakeredis for isolated state, and GitHub publishing explicitly disabled.

**Discount demo with an explicitly pinned acceptance test:** completed in 23.16 seconds. The model generated one patch; target and regression tests passed; causality was supported; 15 mutants were killed and none survived. The run reached publication with dry_run status. It produced no remote PR.

**Pagination:** the formerly false-blocking causality check now passed. Mutation validation found 7 killed and 6 surviving mutants. The graph reinvestigated, then stopped when the model proposed the same patch again. This is an honest evidence limitation in the small pagination fixture, not a reason to bypass the mutation or duplicate-patch guards.

The prepared discount demo remains the better demonstration candidate because its fuller test suite supports mutation validation. These are seeded examples, not a general repair-success measurement. Assertion failures whose primary comparison changes, unsupported causality relationships and incomplete evidence still conservatively block acceptance.

## Remaining deployment work

The changes are local. Deploy them before rehearsing the hosted UI. Real Redis connectivity, deployed browser/WebSocket behavior, GitHub permissions, branch push, PR creation and GitHub Checks still require a publication rehearsal against a dedicated repository the presenter controls. The earlier backend health timeout was not diagnosed as an application outage by this work.
