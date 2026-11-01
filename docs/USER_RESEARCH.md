# ProoFix: developer problems and research notes

Prepared: 4 October 2026.

**Research status:** desk research using public sources. No original ProoFix interviews, company endorsements, or measured review-time savings are reported here. The first-person scenarios below are fictional examples written to explore different working environments. They are not testimonials or quotations from the linked sources.

## The problem we want to solve

A developer gets a change from an AI coding agent. Before it goes into the company's main branch, someone still has to understand the diff, check affected code, run the right tests, and decide whether the change does what was requested.

ProoFix's intended user is that reviewer. Our hypothesis is that a focused report with rerunnable checks can reduce their verification work without causing them to miss important defects.

The current repository is primarily a repository-analysis and repair workflow. Reviewing an existing agent-generated PR is the intended product direction; this document does not claim that the whole workflow is already implemented.

## What people have published

### 1. Developers report problems with nearly correct output

Stack Overflow's 2025 survey reports that 66% of respondents to its AI-frustrations question encountered output that was close to correct but still needed work; 45% selected time-consuming debugging of generated code. That question had 31,476 responses and allowed multiple selections. These are reported experiences, not measured defect rates. [Source: 2025 survey, AI tools' frustrations](https://survey.stackoverflow.co/2025/ai/#ai-tools-frustrations).

**Our interpretation:** checking plausible-looking changes is a reasonable problem to investigate. These percentages do not tell us how much time ProoFix could save.

### 2. A maintainer describes review work being passed to other people

In December 2025, Simon Willison described the problem of contributors submitting large, untested AI-assisted changes and expecting reviewers to establish whether they work. His argument is that the author must provide working, tested code. This is a practitioner's account and advice, not a representative survey. [Source: Your job is to deliver code you have proven to work](https://simonwillison.net/2025/Dec/18/code-proven-to-work/).

**Our interpretation:** evidence should accompany the change before another engineer spends time reviewing it. A report must help the author take responsibility, not provide an excuse to skip understanding the code.

### 3. Perceived speed and measured speed can differ

METR's July 2025 study involved 16 experienced open-source developers completing 246 tasks in repositories they knew. With the early-2025 tools studied, AI access increased completion time by 19%, although participants believed it helped them work faster. This measured whole-task time in a particular setting, not PR-review time or today's tools. [Source: METR's early-2025 study](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/).

In February 2026, METR said its follow-up experiment had serious selection and timing problems. It considered greater speedups plausible but could not reliably estimate their size from those data. Do not use the older result to claim AI generally slows developers down today. [Source: METR's experiment-design update](https://metr.org/blog/2026-02-24-uplift-update/).

**Our interpretation:** ask developers how the work feels, but also observe time and outcome quality. A faster-looking workflow may simply move work to the reviewer.

### 4. AI can help while review still matters

Anthropic surveyed 132 engineers and researchers and conducted 53 interviews for its internal study. Employees described productivity benefits and broader capabilities, alongside concerns about maintaining the knowledge needed to assess generated work. This is research published by an AI vendor about its own workforce; the setting and incentives limit generalization. [Source: How AI is transforming work at Anthropic](https://www.anthropic.com/research/how-ai-is-transforming-work-at-anthropic).

**Our interpretation:** ProoFix should help teams retain the benefits of AI-assisted development. It should not assume every generated patch is bad or replace engineers' judgment with another unsupported AI opinion.

## Different company settings: illustrative developer perspectives

**All five voices below are invented scenarios, not interview participants.** No real company is represented. The sources support the broad research questions; they do not establish that the specific incidents below occurred.

### A. Backend reviewer at a small SaaS company

*Fictional first-person scenario:*

I asked the agent to fix a date filter. It changed the query, added a helper, and rewrote part of the response handling. The screenshot looks fine. Now I need to work out why those other changes were needed. I can read the diff, but following every caller takes time. I'd rather get a small change with the relevant test result attached.

**Problem to investigate:** reconstructing intent and checking unrelated edits.

**Possible ProoFix response:** show requested behavior, changed functions, affected callers, and edits outside the stated scope. Let the reviewer inspect the underlying evidence.

**Question for a real interview:** show us a recent PR where you had to ask the author to explain or remove extra changes.

**Public context:** the survey and Willison's account above motivate this scenario. The date-filter incident is fictional.

### B. Backend engineer at a payments company

*Fictional first-person scenario:*

The agent added a retry after a timeout. The tests pass. What I need to know is what happens if the first request succeeded but the response never reached us. Does the retry create the operation again? A green test run doesn't answer that if nobody tested it.

**Problem to investigate:** business behavior that the existing test suite does not cover.

**Possible ProoFix response:** identify the retry change, list the acceptance cases actually checked, and mark missing cases as unverified. Use independent acceptance checks where available. Leave the business decision with the responsible engineer.

**Question for a real interview:** which rules do you routinely check by hand because your current tests do not capture them?

**Public context:** nearly correct output is reported in the survey. This payments example is a product hypothesis, not a sourced production incident or a claim about financial compliance.

### C. Maintainer at an established enterprise software company

*Fictional first-person scenario:*

The function looks cleaner after the change. But an older service still calls it with a value the new code rejects. The agent only saw the current usage. Before approving, I need to find the older callers and work out whether this is an intentional breaking change.

**Problem to investigate:** repository history and compatibility assumptions missing from the patch's explanation.

**Possible ProoFix response:** surface known callers, affected tests, and relevant history. State where static analysis cannot resolve a dependency, including consumers outside the repository.

**Question for a real interview:** describe a change that passed local tests but broke another consumer. How did you discover the dependency?

**Public context:** METR studied developers working in familiar, mature repositories. It did not report this fictional compatibility incident or prove that dependency graphs solve it.

### D. Platform engineer supporting several product teams

*Fictional first-person scenario:*

The report says the tests passed. On my machine they fail before collection because a package is missing. I need to know which commit was tested, which environment was used, and what command actually ran. Otherwise I'm starting the verification again.

**Problem to investigate:** results that another engineer cannot reproduce.

**Possible ProoFix response:** attach exact commits, commands, dependency requirements, exit status, and relevant logs. Separate setup errors from code failures. Never turn an unavailable check into a pass.

**Question for a real interview:** when did you last have to recreate someone else's test environment to review their change?

**Public context:** Willison's emphasis on the author's responsibility to demonstrate working code motivates this scenario; the missing-package example is fictional.

### E. Technical lead at a client-services company

*Fictional first-person scenario:*

The generated code is in a part of the stack the author hasn't used before. I don't mind that they used an agent. I need them to explain the failure cases and show what they checked. Another long generated summary won't help if I still have to inspect everything myself.

**Problem to investigate:** the reviewer becomes responsible for both verifying the change and filling gaps in the author's understanding.

**Possible ProoFix response:** produce a short review checklist tied to actual checks and unresolved questions. Avoid a generic approval recommendation. Keep the diff easy to reach.

**Question for a real interview:** what information do you ask a contributor for before you will review an unfamiliar change?

**Public context:** Anthropic's internal study discusses expanded capabilities and concerns about retaining expertise. This client-services setting is an extrapolation, not a reported Anthropic incident.

## What we should build and test first

These are proposed requirements, not validated findings or a completed-feature list.

| Research question | Product experiment | Evidence to collect |
| --- | --- | --- |
| Does finding affected code take meaningful review time? | Report changed functions and known callers for one Python PR | Time spent locating dependencies; missed relevant dependencies |
| Does a reviewer trust a result they can rerun? | Export commands, commits, environment notes, and outcomes | Whether another developer reproduces the result unaided |
| Does the report make missing verification clear? | Separate passed, failed, unavailable, and not-run checks | Whether users correctly identify unresolved risks |
| Does the report reduce work overall? | Compare normal review with report-assisted review | Active review time, report reading time, defect detection, false alarms |
| Can it create false confidence? | Include a patch with passing tests but a known unmet requirement | Whether the reviewer detects the problem or approves incorrectly |

For the first pilot, target Python maintainers reviewing small bug-fix PRs. The other settings help us explore different concerns; they are not evidence that we can serve all those industries now.

## Primary research still needed

Recruit 3–5 developers who have recently reviewed AI-assisted changes. Ask for permission to take notes. Do not collect private company code, names, or direct quotes for publication without permission. Describe convenience sampling honestly; friends or teammates may be useful participants but are not a representative industry sample.

Before showing ProoFix, ask:

1. Tell me about the last AI-assisted change you reviewed.
2. What did you check before approving or requesting changes?
3. Which step took the most effort? Can you show a non-confidential example?
4. What was missing from the PR description or test evidence?
5. Which checks would you still do yourself even with a verification report?

Record both supporting and contrary answers. Someone who already has an effective CI and review workflow may not need ProoFix.

Use this template for each actual session; do not fill it with the fictional scenarios:

```text
Participant ID:
Date:
Role and broad company setting:
Relevant AI-code-review experience:
Consent to notes / publication of anonymized findings:
Recent review example:
Observed steps and friction:
Participant-reported time (estimate):
Observed active time (if measured):
Current tools and workaround:
What would not help:
Permitted quote, if any:
Product decision this informs:
```

No completed primary-research sessions are included in this document yet.

## Small review pilot

Prepare comparable, non-confidential PRs with independently documented expected behavior. Include a valid patch and a defective patch. Keep the reference evaluation separate from ProoFix and hidden from participants until after their decisions.

Compare normal review with ProoFix-assisted review on different cases. Vary case assignment and order so familiarity with a patch does not masquerade as a time saving. Record repository familiarity, assistance, setup failures, and incomplete sessions.

Capture active review time separately from tool waiting time. Record defects found, defects missed, incorrect concerns, and the final decision. A shorter review with more missed defects is not a success. With a small sample, publish per-case observations rather than claiming an industry-wide percentage improvement.

| Participant | Case and condition | Active minutes | Tool wait | Defects found / known | Incorrect concerns | Final decision | Assistance |
| --- | --- | --- | --- | --- | --- | --- | --- |
| No results collected | — | — | — | — | — | — | — |

## Submission wording supported today

We reviewed public developer-survey results, a maintainer's published account, and studies of AI-assisted development. They support investigating the work needed to check generated changes. ProoFix is being developed to give reviewers focused, rerunnable evidence. We have not yet established review-time savings through a ProoFix user study.

Do not present the fictional voices as interviews, count them as users, attribute them to real companies, or claim that the linked organizations have evaluated ProoFix. Add actual interview and pilot findings when they exist.
