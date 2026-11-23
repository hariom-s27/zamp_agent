# EVALUATION.md

A scored rubric for this submission, written before round 2 of the build and used to
decide what to work on. Each criterion comes from the candidate guide. Each has five
pass tests that a reviewer can check with a command, a test name or a screen. The score
is the number of pass tests met (minimum 1), so a score cannot be argued up by wording.

Scores are given twice: **before** (the project as audited on 22 Nov 2026, 364 tests)
and **after** (this ZIP, 561 tests). The "after" column was filled in at the end of the
round from the verification run, not from intent: every command in the "How to check"
column was run on the final code, and the pass tests themselves were not reworded
afterwards. Where a "How to check" entry named a test selector that turned out not to
exist, it now names the real one.

**Result: 19 of 40 before, 38 of 40 after.** The two points not scored are both steps on
the candidate's own machine (a Windows run of this round, and a real recorded Claude
answer). They are listed at the end with the command for each.

## Where the criteria come from

| Guide text | Criterion |
|---|---|
| "whether your process actually works", "handles real inputs end-to-end" | C1 Works on real inputs |
| "the judgment behind your design choices" | C2 Judgment and controls |
| "Design and build 2 to 4 edge cases ... Don't pick trivial ones" | C3 Edge cases |
| "a live run view (showing each stage as it executes)" | C4 Live run view |
| "a dashboard (showing history, status, and outputs across runs)" | C5 Dashboard |
| "how you explain it to a non-technical buyer" | C6 Explanation |
| "A demo ... that breaks live, even on a minor thing, is hard to recover from" | C7 Demo that cannot break |
| "Use AI tools, and be deliberate about it" | C8 Deliberate AI |

## Rubric and scores

### C1. The process works on real inputs, end to end

| # | Pass test | How to check | Before | After |
|---|---|---|---|---|
| 1.1 | Every bundled sample gives its documented verdict and reason codes on a fresh database | `python -m invoice_desk.run --all` | pass | pass: 18 of 18 |
| 1.2 | Both bundled scans are read correctly by a live OCR engine, not only from recorded output | `INVOICE_DESK_STRICT_OCR=1 pytest tests/test_pipeline.py -k live` | pass | pass |
| 1.3 | Bad inputs (corrupt, empty, encrypted, oversized, random bytes) never crash and never book anything | `pytest -k "bad_inputs or random_bytes"` | pass | pass: 16 cases, and 8 bad files are bundled |
| 1.4 | On a test set made with different tools, fonts and layouts than the samples, no misread is approved, and the numbers are published | `python -m scripts.evaluate_robustness`, `docs/robustness_report.md` | fail (no such set; the scorecard used my own generator) | pass: 49 documents, 0 misread-approved, with recorded and with live OCR |
| 1.5 | On that set, at least 70% of clean text-PDF invoices are approved untouched by rules alone | same report | fail (not measured) | pass: 24 of 24 (8 of 8 on the holdout split; 5 of 24 before the reader was improved) |

Before: **3 / 5**. After: **5 / 5**.

### C2. Judgment behind the design, and controls that hold

| # | Pass test | How to check | Before | After |
|---|---|---|---|---|
| 2.1 | Every non-obvious choice is written down with what was rejected | `DECISIONS.md` | pass | pass: D01 to D64 |
| 2.2 | Every threshold the process uses is in `rules.yaml`, none in code | `pytest tests/test_round2_audit.py -k threshold` | fail (name matching, line pairing, PO scoring weights, scan agreement and file limits were in code) | pass |
| 2.3 | The decision is a pure function: same findings and rules, same decision | `pytest tests/test_decide.py` | pass | pass |
| 2.4 | Controls hold when a person acts: approvals from the review queue can never overbill a PO, and a bank change needs two different people | `pytest -k "overbill or two_different_people"` | fail (24 of 20 units approved; one person could approve a bank change) | pass. Limit: "different people" compares typed names, because the demo has no login (D43) |
| 2.5 | A failure at any write in the posting stage leaves the ledger and the decision in agreement | `pytest -k "rolls_the_posting_back or nothing_can_be_written"` | fail (receipt failure left POSTED under a HOLD) | pass |

Before: **2 / 5**. After: **5 / 5**.

### C3. Edge cases that are deliberate and not trivial

| # | Pass test | How to check | Before | After |
|---|---|---|---|---|
| 3.1 | Two to four edge cases, each with a sample file, a test and a written reason for choosing it | `samples/manifest.json`, `README.md` | pass | pass |
| 3.2 | Each edge case trips the rule it claims and no other | manifest `expected_reasons` | fail (E4b also tripped the PO balance; E2 leaned on a full PO) | pass: 08 is held for the price alone; 05 is rejected with room on its PO |
| 3.3 | The tolerance case sits at the boundary, with a file on each side | samples 07 and 08, `pytest -k "e4_sits or one_rupee"` | fail (0.11% and 1.56% against a 1% limit) | pass: 0.95% and Rs 488 against 1.01% and Rs 515 |
| 3.4 | Every hold names an owner inside the company and asks one question; the vendor gets a drafted note when the vendor must act | any HOLD sample | pass | pass |
| 3.5 | What happens after the hold is controlled too (re-check at approval, second approver, number required) | `pytest tests/test_review.py tests/test_round2_audit.py` | fail (see 2.4) | pass |

Before: **2 / 5**. After: **5 / 5**.

### C4. Live run view

| # | Pass test | How to check | Before | After |
|---|---|---|---|---|
| 4.1 | The nine stages update one by one while the run executes | `pytest tests/test_pipeline.py -k events`, Process page | pass | pass |
| 4.2 | Each stage shows its evidence, and the page image shows where each value was read | any run | pass | pass, now also for values Claude read (D64) |
| 4.3 | Primary cards use business wording: no test-case labels, hashes or milliseconds | Process page, `pytest tests/test_ui.py -k "audit_trail_reads or said_the_way or sidebar_says or live_view"` | fail (walkthrough items 1 to 5) | pass |
| 4.4 | A guided demo runs each scenario with one click, at a pace a viewer can follow, from a clean state | Guided demo page, `pytest tests/test_ui.py -k demo` | fail (did not exist) | pass: eleven scenarios, also rehearsed by `scripts.preflight` |
| 4.5 | Sending the same file again is recognised and explained, not shown as a failure | `pytest -k "same_file_again or sent_again or sent_twice"` | fail (second run was a REJECT) | pass |

Before: **2 / 5**. After: **5 / 5**.

### C5. Dashboard: history, status and outputs

| # | Pass test | How to check | Before | After |
|---|---|---|---|---|
| 5.1 | Run history with verdict, reasons and review status, filters, and any run opens in full | Dashboard | pass | pass |
| 5.2 | Headline figures a finance lead uses, in their words (straight-through rate, value on hold, value released) | Dashboard, `pytest tests/test_ui.py -k dashboard` | fail ("no person involved", milliseconds) | pass |
| 5.3 | The audit trail can be verified from the dashboard | Dashboard | pass | pass, and the page now says what the check does and does not prove |
| 5.4 | History exports as CSV; every run record and receipt downloads | Dashboard | pass | pass |
| 5.5 | The review queue shows what is waiting, and decisions are recorded against a named person | Review queue | pass | pass |

Before: **4 / 5**. After: **5 / 5**.

### C6. Explained so a non-technical buyer can act on it

| # | Pass test | How to check | Before | After |
|---|---|---|---|---|
| 6.1 | Every decision has a plain reason with the numbers in it and a next action | any run | pass | pass |
| 6.2 | A 5-minute script with timings, and a run order for the live interview | `DEMO_SCRIPT.md` | pass | pass: rewritten around the guided demo, with the moment for a live Claude call |
| 6.3 | Every claim in the documents matches what was measured | audit claims table, re-checked in `CHANGELOG.md` | fail (4 partly-true claims) | pass: each partly-true claim was made true or reworded (CHANGELOG section 2); the README separates verified from not verified |
| 6.4 | Limits are stated where a buyer will see them, including what a real invoice from outside the demo will do | How it works page, README | pass | pass |
| 6.5 | Prepared answers for the hard questions: the AI layer, the audit findings, the robustness numbers | `INTERVIEW_PREP.md` | fail (not covered) | pass: sections C, D and E |

Before: **3 / 5**. After: **5 / 5**.

### C7. A demo that cannot break

| # | Pass test | How to check | Before | After |
|---|---|---|---|---|
| 7.1 | Preflight reports READY on a clean Windows machine by following the written steps | `SETUP_WINDOWS.md`, `python -m scripts.preflight` | pass (audit) | **not scored**: this round's code was run on Linux only (READY there, in a clean folder, on Python 3.11, 3.12 and 3.13). Counted as not met until it is run on Windows |
| 7.2 | The default demo needs no network and no key | `pytest`, offline run | pass | pass |
| 7.3 | An earlier run cannot spoil the demo: clean state per demo, and re-running is safe | `pytest tests/test_ui.py -k "demo or same_file_again"` | fail | pass |
| 7.4 | The hosted app has what it needs: pinned requirements, system packages, a secrets template, and a sandbox per visitor | repo files, `pytest -k public_demo` | fail (one shared database; no secrets template) | pass on the files and the sandbox. The deployment itself was not done |
| 7.5 | A key left in `.env` cannot change the demo | `pytest tests/test_llm.py -k "key_alone"` | fail (`auto` went live) | pass |

Before: **2 / 5**. After: **4 / 5**.

### C8. AI used deliberately

| # | Pass test | How to check | Before | After |
|---|---|---|---|---|
| 8.1 | The model has a defined job, and no decision rests on its judgment | `DECISIONS.md`, `decide.py` | pass | pass: D45 to D49, with the uses that were rejected |
| 8.2 | Every value from the model is checked against the page, exactly; anything unverified is not used | `pytest tests/test_llm.py -k "quote or identifier or piece_of or value_in_quote"` | fail (identifier check accepted substrings) | pass: 30 cases |
| 8.3 | The same file, prompt version and model always replay the same answer, and recording every needed sample is one command | `pytest tests/test_llm.py -k "stored_answer or stored_and_reused or replays"`, `pytest tests/test_scripts.py -k "records_everything or after_recording"` | fail (replay ignored prompt and model; live calls overwrote recordings) | **not scored**: the mechanism passes its tests, and the state after recording was rehearsed end to end against a stand-in for the API (all tests green, preflight READY, sample 10 approved). But no answer from the real API exists yet, so I count it as not met until one does |
| 8.4 | Cost and failure are bounded: timeout, retries, a cap per invoice, a cap per day, and a run never crashes when the API fails | `pytest tests/test_llm.py -k "cap or api_fails or unusable or retried or fresh"` | fail (no caps) | pass: 21 cases |
| 8.5 | The three modes (fixture, cached-live, off) are shown on screen, and the default is the safe one | sidebar, `pytest tests/test_ui.py -k "ai_mode or live_call"` | fail | pass |

Before: **1 / 5**. After: **4 / 5**.

## Totals

| Criterion | Before | After |
|---|---|---|
| C1 Works on real inputs | 3 | 5 |
| C2 Judgment and controls | 2 | 5 |
| C3 Edge cases | 2 | 5 |
| C4 Live run view | 2 | 5 |
| C5 Dashboard | 4 | 5 |
| C6 Explanation | 3 | 5 |
| C7 Demo that cannot break | 2 | 4 |
| C8 Deliberate AI | 1 | 4 |
| **Total** | **19 / 40** | **38 / 40** |

The "before" score looks low for a project whose 364 tests passed. That is the point of
writing the pass tests strictly: the tests that existed were green, and the audit found
what they did not cover. The weakest areas, in order, were C8 (AI), then C2, C3, C4 and
C7 together. That order set the order of work in round 2.

## How the round went, against the rubric

Two verification rounds were run, as the plan required.

- **Round A**, after the engine, AI layer, independent test set and new screens were
  built: full suite, all samples, the independent set, headless UI tests for every page.
  Weakest criterion at that point: C7. The guided demo existed, but "is it ready" still
  meant clicking through it. Improvement: `scripts.preflight` now rehearses the eleven
  demo steps and opens every page, and the photograph straightening was hardened
  against degenerate pictures.
- **Round B**, while the documents were written, in a real browser: weakest criterion
  C8, because the one screen an AI interviewer will look at hardest, the letter read by
  Claude, had been built without ever being looked at with an answer in place. A
  rehearsal with answers made through a stand-in for the API showed four faults, all
  fixed with a test first: the page stated two different expected decisions at once;
  field names appeared as `invoice_number`; values Claude read had no outline on the
  document; and there was no way to show a live call once an answer was stored. The
  same pass found that the second approver of a bank change was shown the first
  approver's name and note already typed in. The stand-in answers were deleted
  afterwards, and a test now fails if one is ever left in `fixtures/llm/`.

## The two points that are yours to score

| Pass test | What to run | Then |
|---|---|---|
| 7.1 Windows | `SETUP_WINDOWS.md` steps 1 to 3: `python -m pytest`, `python -m scripts.preflight` | READY with 561 tests passing makes it a pass |
| 8.3 A real recording | `SETUP_WINDOWS.md` step 6: `python -m scripts.record_llm_fixtures` with your key | Sample 10 shown as APPROVE by preflight makes it a pass |

With both done the score is 40 of 40 on these pass tests. That is a statement about
this rubric, which I wrote. It is not a prediction of how the submission will be judged.

## What the rubric cannot score at all

- The hosted deployment being up on the day (7.4 is scored on the files and the
  sandbox).
- The video and the spoken explanation.
- Real invoices from real vendors. The independent set is closer to them than the
  samples are, and it is still synthetic.
