# PROGRESS.md

Work log. Newest entry last. If the run is interrupted, "continue" should pick up at
the first unticked box in PLAN.md section 4.

## 2026-11-21

- Read the candidate guide in full (7 pages). Chose nothing: PS-1 was already chosen.
- Cloned `ef-recon`, `controlplane` and `outcome` into a scratch folder outside this
  project and read the modules listed in RESEARCH.md section 7.
- Web research on Zamp, AP matching and tolerances, duplicates, bank-change fraud and
  GST invoice rules. Findings and links in RESEARCH.md.
- Design loop: four designs, twelve candidate edge cases, three review rounds.
  Outcome in PLAN.md, reasons in DECISIONS.md (D01 to D29).
- Environment: Python 3.11.18, Streamlit 1.67, Tesseract 5.3.4 available here.
- Build loop, engine: test data generator (7 vendors, 12 POs, 10 posted history invoices,
  10 sample invoices in six layouts, two of them scans, 4 bad inputs), reader, extractor,
  validate, vendor, PO match, duplicates, decide, store with receipt chain, pipeline, CLI,
  LLM assist, human review. `python -m invoice_desk.run --all` gives 14 of 14.
- Tests written module by module and run until green: 277 passing. Bugs the tests found
  and that were fixed in the code, not in the tests:
  - the quote check for LLM values was fuzzy and accepted a quote with two digits
    swapped; it is now exact on every letter and digit;
  - a failure while posting could leave an APPROVE verdict standing; a failed posting
    or a failed decision now always ends in HOLD (POSTING_FAILED / INTERNAL_ERROR);
  - a reviewer could type an invoice number that is already on record; now refused;
  - a vendor name one letter away from the master name passed without a note.
- Not possible in this environment: calling a live LLM. The LLM path is tested through
  a stub transport only, and `fixtures/llm/` ships empty. See SETUP_WINDOWS.md for how
  to record real responses with your own key.
- Next: the Streamlit UI (PLAN.md section 4, UI tasks), then verify, improve, package.
- UI built: `app.py` plus `invoice_desk/ui/` (Process invoice with live stages, Dashboard,
  Review queue, Rules and data with a what-if sandbox, How it works). Checked in two
  ways: Streamlit's headless harness (tests/test_ui.py) and a real Chrome session with
  screenshots of every page, which led to layout fixes.
- Improve round 1 (weakest area: reading scans and unseen layouts). An end-to-end
  scorecard (`scripts/evaluate_extraction.py`) runs generated layouts through the whole
  pipeline against a matching PO. It found three real bugs, all fixed in the code:
  - a label such as "Purchase Order No." was taken for the document's title, so valid
    invoices were rejected as "not a tax invoice" (7 of 60 text layouts);
  - a stray "$" from OCR made an invoice look foreign-currency;
  - a contact line ("Email: ...") could be read as the vendor name.
  Scans are now read twice (greyscale and black and white) and reconciled
  (`invoice_desk/second_read.py`): the read whose figures add up is used, and an invoice
  number or date is trusted only when both reads agree. Quick scorecard after the change:
  text 60 of 60 straight through; scans 24 of 24 on the layouts seen while building.
  Held-out layouts (first run after the change): text 140 of 140; scans 64 of 72
  straight through, 3 false alarms, 5 misread and held, 0 misread and approved.
- Tests at that point: 330 passing.

## 2026-11-22

- Improve round 2 (weakest area: what a real AP desk meets around the happy path).
  Built, each with tests:
  - item tables that continue over pages (running heads, repeated headings, "continued"
    strips, carried-forward amounts are not line items);
  - a corrected copy of an invoice that is still on hold is held as a possible
    correction instead of being rejected as a duplicate number, and cannot be approved
    until the first one is decided;
  - a foreign-currency invoice is read, shown in its own currency, and handed to a
    person; a document the system did not book can be marked as handled, never approved;
  - a late failure (posting, read-back, the decision stage) always ends in a hold;
  - a page with absurd dimensions is rendered at a capped size;
  - hold owners are always inside the company; the vendor note is drafted only when the
    problem is the vendor's, and no longer says "your invoice" about a quotation.
- Reading every sample's decision text by eye led to wording fixes (service invoices are
  "matched on item and amount", a PO with nothing billed before says so, the stage 6
  summary when there are no line items).
- OCR runs single-threaded by default (`OMP_THREAD_LIMIT=1`): on a two-core machine a
  page read went from 4.6 s to 1.8 s.
- Scripts added: `scripts/preflight.py` (demo-day check, also explains what to do if the
  local OCR engine reads a bundled scan differently), `scripts/evaluate_extraction.py`
  (scorecard), `scripts/record_llm_fixtures.py`.
- Final scorecard, 200 text and 96 scanned layouts: docs/extraction_scorecard.md.
  Text 200 of 200 approved untouched. Scans seen while building 24 of 24. Scans held
  out 72: 64 approved untouched, 3 held although read correctly, 5 misread and held,
  0 misread and approved.
- Verified without an OCR engine on the path: 358 tests pass, 6 skip, preflight READY,
  bundled scans run from recorded OCR.
- Tests that run the live OCR engine now assert what must hold on any engine build (the
  documented outcome, or a hold for a reading reason, never a wrong approval). Exact
  reads on live OCR are asserted only with `INVOICE_DESK_STRICT_OCR=1`, as in the build
  environment; exact reading of the bundled scans is pinned everywhere by the recorded
  OCR output.
- Documents written: README.md, SETUP_WINDOWS.md, DEMO_SCRIPT.md, INTERVIEW_PREP.md.
  DECISIONS.md gained an assumptions section and D30 to D41.
- Tests: 364 passing.

- Package and prove. The ZIP was unzipped into a fresh folder. In a fresh Python 3.11
  environment made with `python -m venv` and `pip install -r requirements.txt`: 364
  tests passed, `scripts.preflight` ended READY (14 of 14 samples as documented),
  sample 01 ran from the command line, and the app started from that folder was driven
  in Chrome (sample 01, all 14 samples in one go, every page). The install and the
  test suite were repeated on Python 3.12 and 3.13: 364 passed on each.
- No file in the project is a copy of a file in the three reference repositories
  (checked by hash; the only matches are empty files).

## What was not possible here, and what to do about it (round 1)

This is the table as it stood at the end of round 1, with its step numbers of that time. The current
one is at the end of the round 2 section below.

| Not done | Why | What you do |
|---|---|---|
| Run on Windows | The build machine is Linux (tested there on Python 3.11, 3.12 and 3.13) | SETUP_WINDOWS.md steps 1 to 4, once, before the day |
| Deploy to a public link | No account or outbound access for that | SETUP_WINDOWS.md step 7 |
| Call a live LLM | No model was reachable | SETUP_WINDOWS.md step 6: add your key, run `python -m scripts.record_llm_fixtures` |
| Build the Dockerfile | No container runtime | Only needed for plan B hosting; build it once if you use it |

## Round 2 (started 2026-11-22)

Inputs: AUDIT_REPORT.md and UI_WALKTHROUGH.md (both now in `docs/`), the candidate guide.
The rubric that drives this round is EVALUATION.md. If the run is interrupted, "continue"
picks up at the first unticked box below.

- [x] Phase 1. Re-read the guide, the audit and the walkthrough. Research added to
      RESEARCH.md (sections 10 to 14). EVALUATION.md written, current project scored 19 / 40.
- [x] Phase 2. Audit fixes, each with a failing test first (tests/test_round2_audit.py; list in
      CHANGELOG.md). Engine side done: PO re-check at approval, atomic posting, two approvers
      for a bank change, idempotent intake, E4 pair at the tolerance edge (samples 07 and 08
      regenerated, every other sample byte-identical), E2 rejected with room on the PO, all
      thresholds in rules.yaml, blank-versus-unread on scans, tighter live-OCR assertions, four
      more bad-input files. UI side of these fixes is in Phase 5.
- [x] Phase 3. Claude API layer (invoice_desk/llm.py, config.py): fixture, cached-live and off;
      answers keyed by file, prompt version and model; caps per invoice and per day; tests with
      the real SDK and a fake transport (tests/test_llm.py, tests/test_scripts.py);
      scripts/record_llm_fixtures.py and scripts/preflight.py rewritten. No recording ships:
      the API is not reachable from the build environment.
- [x] Phase 4. Independent robustness set: 49 documents made with Chrome, wkhtmltopdf, LibreOffice Calc
      and Writer, LaTeX and OpenCV (robustness/build_set.py, truth.json), evaluator
      (scripts/evaluate_robustness.py), baseline 13 of 49 right, final 45 of 49 right, misread-approved 0
      in both (docs/robustness_report.md, robustness/results.json, pinned by tests/test_robustness.py).
      Reader and matcher changes driven by the tune split only (robustness/CHANGES.md,
      tests/test_robustness_fixes.py, tests/test_straighten.py); new invoice_desk/straighten.py for
      photographs. OCR output of the 12 photographs recorded in fixtures/ocr. Old scorecard regenerated.
- [x] Phase 5. Guided demo page (eleven scenarios, one click each, live stage view, fresh start, "Try your own
      invoice"), business wording throughout, the audit trail behind an expander, the vendor message ready to
      copy, PO position and refused approvals in the review queue, two-approver screen with proof upload,
      pace control, demo and admin controls off the sidebar, a pinned demo date, private books per visitor
      (INVOICE_DESK_SANDBOX=session), Streamlit Community Cloud files, Dockerfile updated (not built: the base
      image could not be pulled here).
- [x] Phase 6. Verify and improve, two rounds. Round A: full suite, samples, independent set, headless UI for
      every page; weakest was C7, so preflight now rehearses the demo and opens every page. Round B, in a real
      browser with stand-in answers for the letter invoice: weakest was C8, so four faults on the AI screen were
      fixed test-first (two expectations shown at once, code names for fields, no outline for values Claude
      read, no way to show a live call once an answer is stored), plus the second approver's form. The stand-in
      answers were deleted afterwards. EVALUATION.md re-scored: 19 of 40 before, 38 after.
- [x] Phase 7. README, SETUP_WINDOWS, DEMO_SCRIPT, INTERVIEW_PREP rewritten; CHANGELOG.md written; DECISIONS
      extended to D64; screenshots retaken from the new screens; requirements.lock.txt regenerated; ZIP built,
      unzipped into a clean folder, installed and tested there.

Package and prove (22 Nov 2026). The ZIP was unzipped into a clean folder. In three fresh environments made
with `python -m venv` and `pip install -r requirements.txt` (Python 3.11.18, 3.12.3 and 3.13.18) the full
suite passed: 561 tests on each. From the clean copy: `scripts.preflight` ended READY with one expected warning
(no recorded Claude answer yet), `invoice_desk.run --all` gave 18 of 18, and `scripts.evaluate_robustness` gave
30 approved-correct, 15 held-correct, 4 misread-held, 0 misread-approved. The app started from the clean copy was
driven in Chrome: the guided demo ended 11 of 11 as expected, the approval that would overbill was refused,
every page opened, the audit trail check said intact, and the whole sample inbox ran 18 of 18. With the OCR
engine hidden the suite gives 553 passed and 8 skipped, and preflight is still READY. No file in the package is
a copy of a file in the three reference repositories (checked by hash).

### What round 2 could not do here, and what you do about it

| Not done | Why | What you do |
|---|---|---|
| A real Claude answer recorded | The API cannot be reached from the build environment | SETUP_WINDOWS.md step 6: `python -m scripts.record_llm_fixtures` with your key |
| Run this round on Windows | The build machine is Linux | SETUP_WINDOWS.md steps 1 to 3 |
| Deploy to a public link | No account or outbound access for that | SETUP_WINDOWS.md step 8 |
| Build the Dockerfile | The base image could not be pulled | `docker build -t invoice-desk .` once, only if you use plan B |

## If this run is ever resumed

Round 1: everything in PLAN.md section 4 is ticked. Round 2: every box above is ticked.
To check the state of the project at any time: `python -m pytest`, then
`python -m scripts.preflight`, then `python -m scripts.evaluate_robustness`. What is
left is on your side and is listed in the table above; EVALUATION.md says which two
rubric points those steps settle.


## Round 3 handoff (23 Nov 2026)

Branch `round3`, local commits only (nothing pushed; `main` and tag `round2-final` untouched). The specification
is section 6 of `Invoice_Desk_round3_research_and_plan.md`, kept outside the repository.

### Checkpoints done

| # | What | Commit |
|---|---|---|
| 0 | Baseline recorded: the four command outputs and SHA-256 lists of samples, fixtures, data, rules.yaml and the frozen code files, in `D:\sem_iitk\sem9\zamp\baseline_round2\` | (outside the repo) |
| W | Screens say "AI" instead of the model's brand; the exact model stays in Technical details, the audit record and the JSON downloads | `f762db1` |
| 1 | rules.yaml `2026.11-3` (company.ap_email, cases, msme, value_model, all required and validated); `data/*_cases.csv`; `scripts.generate_case_data`; `samples/cases/`; `invoice_desk.run --cases` | `ec89c9b` |
| 1 | Test guard: no test can see a real API key or reach the API | `ae3fdac` |
| 1 | Case data generator writes LF line endings | `9341eeb` |
| 2 | Tables cases, case_events, messages, goods_receipts; store methods; `cases.sync` with clocks and escalation | `e3798f1` |
| 3 | Goods receipts and release by rule (`record_goods_receipt`, `master_with_receipts`, `try_resume`) | `56d6477` |

### Where it stands

- `python -m pytest`: 597 passed, 0 failed, 0 skipped (561 at the round 2 baseline).
- `python -m scripts.preflight`: READY, 11 lines starting `[ok  ] step`, no warning.
- `python -m invoice_desk.run --all`: 18 of 18. `python -m invoice_desk.run --cases`: 2 of 2.
- `python -m scripts.evaluate_robustness`: 30 approved-correct, 15 held-correct, 4 misread-held, 0 misread-approved.
- Every baseline file hashes the same as at `round2-final` except rules.yaml, which checkpoint 1 changes on purpose.
  The frozen code files (section 6.0) are unchanged.

### Departures from the specification, and why

1. **Case manifest paths.** `samples/cases/manifest.json` lists files as `cases/<name>.pdf`, relative to
   `samples/`, the convention the bad inputs already use. `sample_bytes` reads them unchanged, so it was not edited.
2. **LF line endings in the case generator.** It writes its JSON with `newline="\n"`, because `.gitattributes`
   keeps JSON as LF; with Windows endings the committed files would not match the generator after a clean checkout.
3. **`-2` suffix on a case id.** A file read afresh after a rules change and held again would collide with its
   first case (`CASE-` plus 8 hex characters is the primary key). The second case gets `-2`, the third `-3`.
4. **Test key guard in `tests/conftest.py`.** At import it marks `.env` as already read, removes
   `ANTHROPIC_API_KEY` and sets `ANTHROPIC_BASE_URL=http://127.0.0.1:9`; `tests/test_isolation.py` pins it.
   Reason: `.env` was read lazily, so a recording test that ran first in a session got the real key back. This
   happened twice on 23 Nov, around 14:00 to 14:10 IST, when `tests/test_scripts.py` was run on its own; real
   recording calls were made and their answers went only to a pytest temp folder.
5. **No case-history line before the first goods receipt.** `try_resume` writes a "partial receipt" line only
   once a delivery has been recorded for that PO line. Otherwise every C1 case would show "0 of 60 received" the
   moment it opens. Each refusal is still written once per distinct reason.
6. **Values read as printed: 691 of 705** on this machine, not the 693 quoted in the plan. The four outcome classes
   are identical. Round 3 figures use 691; `docs/robustness_report.md` is to say the count differs by 2 between
   OCR builds (checkpoint 7).
7. **Checkpoint W, option B.** Engine text that names the model (written by the frozen `pipeline.py` and
   `decide.py`) is relabelled when the page draws it (`screen_words` in `ui/common.py`). Two lines keep the
   exact model on screen: "AI model" in the sidebar Technical details, and "AI answer (model and prompt version)"
   in the audit trail's Technical details.
8. **One extra rules check.** Besides the validator in section 6.1, every `cases.escalate_to` target must itself
   have a reply time, so an escalated case always has a clock.

### Next

Checkpoint 4: approve and send to the outbox (`approve_message`, `outbox_dir`), tests first. Then checkpoints 5
to 8. Checkpoints 9 to 11 (day 3) wait for an explicit go-ahead.

### Round 3 after the handoff (run without stopping from checkpoint 6, as asked on 23 Nov)

| # | What | Commit | Tests |
|---|---|---|---|
| 4 | Approve and send to the outbox: `cases.approve_message`, `cases.outbox_dir` | `08438d0` | 603 passed |
| 5 | Cases page, RELEASED labels, dashboard case row, steps 12 and 13 in the Guided demo, preflight `case 12` and `case 13`, Cases in the page lists | `9d6b791` | 613 passed |
| 6 | Business case page: `invoice_desk/value.py` (`measured`, `model`), `invoice_desk/ui/value.py`, page in navigation and preflight | `de2ed97` | 619 passed |
| 7 | `invoice_desk/stats.py` (Wilson interval); "How sure can we be" in the robustness report (`--write --ocr fixture`); `scripts/benchmark_throughput.py` and `docs/throughput.md` (200 text invoices in 15.2 s, median 69 ms, about 47,000 an hour) | `a5859b8` | 625 passed |
| 8 | Documents (section 7): README (live link placeholder, After a hold, Business case, Rolling it out, limits, `--cases`), DECISIONS D65 to D73, EVALUATION C9 and C10 (47 of 50 until the hosted link runs 13 steps), DEMO_SCRIPT round 3 order, INTERVIEW_PREP section I and the three numbers, SETUP_WINDOWS, CHANGELOG | "Checkpoint 8: documents" | 625 passed |

Standing results after each of 4, 5 and 6: preflight READY with 11 `[ok  ] step` lines (and from 5 on, `[ok  ] case 12`
and `[ok  ] case 13`); 18 of 18; `--cases` 2 of 2; robustness 30, 15, 4, 0 with 691 of 705 values; frozen code files
and every baseline data file unchanged (rules.yaml changed on purpose at checkpoint 1).

Departures from the specification, and why:

9. **One message per wait (checkpoint 4).** `approve_message` refuses a second approval while the case already waits
   on the vendor for the message it sent, so a double click cannot send it twice. The `-NN` file numbering stays for
   a later message after a reply.
10. **Escalation once per party waited on (checkpoint 4).** Section 6.3 step 5 says "no escalation yet at this
    owner"; checkpoint 2 had checked "never escalated". Now a case already escalated while waiting on Procurement
    escalates again (to the AP analyst) if the vendor misses its reply date.
11. **The outbox file uses the standard library's default email policy (LF line endings)** so it parses back to the
    drafted text exactly; mail clients open it either way.
12. **`master_with_receipts` starts again from the original master (checkpoint 5, a bug fix).** The Cases page passes
    `sync` a master that already carries the goods receipts (section 6.4), and `try_resume` added them a second time,
    so 30 of 60 received counted as 60 and released the invoice. Found by the UI test; an engine test now pins it.
13. **Cases page details the spec leaves open:** urgent cases first, then by answer date; the owner filter matches the
    owner or the party the case waits on; the card shows the AP reference, not the case token (the token is in the
    email subject).
14. **Business case page:** it also runs `cases.sync`, because its "resolved after a hold" figure needs current cases
    (it is a new page, so "no other existing page calls it" still holds). Benchmarks are a fixed table with their
    sources; the assumptions' sources are the comments in rules.yaml, repeated as labels in `ui/value.py`.
15. **The robustness report is still written from the recorded OCR (checkpoint 7).** `robustness/results.json` and the
    tests that compare against it use `fixtures/ocr/` (Tesseract 5.3.4, 693 of 705 values), so the numbers reproduce on
    any machine; only its measurement date changed. With `--write --ocr fixture` on a machine with Tesseract, the script
    also reads the 12 photographs live, and "How sure can we be" leads with that count (691 of 705, Tesseract 5.4.0
    here), names the recorded 693, and says the lower one is the one to quote.
16. **Throughput benchmark:** invoice PDFs are drawn before the clock starts; layouts whose PO number collides with an
    earlier one are skipped so every invoice has its own purchase order; all 200 go into one set of books.
17. **Documents (checkpoint 8):** the live link and video are placeholders until you deploy and record. The rollout
    story says "set the ceiling below every invoice (Rs 1,000 in the sandbox)" because the sandbox input starts at
    Rs 1,000, not 0. The remaining part of checkpoint 8 is yours: redeploy `live`, run steps 1 to 13 on the hosted
    link, rehearse, record, freeze.

## Round 4 (branch `round4`, from round 3 at `b381b2e`)

Specification: `D:\sem_iitk\sem9\zamp\Invoice_Desk_feature_discovery.md` (section 4 the features, section 5 the
build order, section 7 the names, section 8 the defaults, all taken). Opening page stays the Guided demo; Inbox is
added to the navigation. Baseline: `D:\sem_iitk\sem9\zamp\baseline_round3\` (the five command outputs at `b381b2e`,
SHA-256 of every tracked file under samples, fixtures, data and rules.yaml, and of the eleven frozen code files).

### Checkpoint 0: names confirmed (commit "Round 4 checkpoint 0")

**7A, names from the round 3 plan.** Present: tables `cases`, `case_events`, `messages` (with `direction`, `status`,
`eml_sha256`, `attachments_json`, `read_json`), `goods_receipts`; `cases.proposed_json`; `cases.sync`, `case_id_for`,
`statutory_clock`, `approve_message`, `outbox_dir`, `master_with_receipts`; rules `cases.reply_due_days`,
`cases.escalate_to`, `msme`, `company.ap_email`; `data/vendors_cases.csv` with `email`, V101 and V102; POs
PO-2026-1041 and 1042; review status RELEASED with the ledger POSTED after a release; `AFTER_HOLD`, `after_done`,
the page list in `app.py`. **Not built (round 3 day three):** `mailbox.py`, the `case_reply` purpose,
`cases.receive_reply`, sample C3, its reply, and PO-2026-1043. Section 8 default 1 applies: build them as round 4
specifies, with new ids from `generate_desk_data.py`. Round 3 pins: 2 case samples, 2 `AFTER_HOLD` steps, 8 pages,
2 preflight `case` lines.

**7B, round 2 facts.** All present and unmoved: `Invoice.doc_type`, `extract.detect_doc_type`,
`pipeline.NOT_PAYABLE`, `Pipeline.run(content, file_name, source)`, `store.save_file/load_file/save_attachment/
load_attachment`, `ledger.origin`, `ledger_entries(vendor_id, po_number, statuses)`, `decide._due_date`,
`Finding.vendor_issue/evidence`, `review.apply_review/_person/required_checks`, `llm.PURPOSES/PROMPT_VERSIONS/
template_sha256/id_in_quote/value_in_quote/MAX_CHARS/draft_vendor_note`, `ui/common.get_rules/get_pipeline/
get_store/business_date/reset_books`, `ui/reference._sandbox/_dry_run`, `Rules.version/sha256`,
`rules.current_rules`, `store.MIGRATIONS/TABLES`.

**7C, answers.**
1. Duplicate checks read ledger entries with status POSTED or PENDING only, so a REJECTED entry is ignored
   (`duplicates.py`, `ledger_entries(..., statuses=("POSTED", "PENDING"))`). The corrected-copy flow can rely on it.
2. `append_receipt` takes an empty run id (round 3's goods receipt already uses `for_run=""`). No wrapper needed.
3. Structured output goes through `output_config` in `llm.py`; the model name is part of a recording's file name, so
   the hosted app must use the model the replies were recorded with.
4. Primary screens already say "AI" (round 3 checkpoint W); the exact model is only in Technical details.
5. Only `tests/test_masterdata.py` pins the master fingerprint, for the files it builds itself. Keeping discount terms
   in a file only `payments.py` reads (as the spec says) leaves the master digest alone.
6. Replay timing: to be measured at checkpoint 7.
7. "Start a fresh demo" cleared the tables but not the outbox folder, so after a fresh demo the step 13 message
   (same file, same case number) could not be approved again: the leftover `CASE-...-01.eml` blocked it. **Fixed
   here:** `store.reset()` now also removes the `.eml` files in the outbox beside the database; test
   `test_a_fresh_demo_empties_the_outbox_so_the_same_message_can_be_approved_again` failed first. Payment exports and
   stored mail (round 4) must be cleared the same way.

### Round 3 fix: email encoding (commit "Round 3 fix: email encoding")

Found by hand: the approved vendor email opened in Outlook on Windows showed "=gainst", "again=t" and "quoti=g". The
file was written as bytes, but with LF line endings and a quoted-printable body, and Outlook misread the soft line
breaks (`=` at the end of a line) on bare LF. Now `approve_message` writes the body as plain UTF-8 with
`Content-Transfer-Encoding: 8bit` and CRLF line endings (`email.policy.SMTP`): no soft breaks, and the rupee sign is
one readable character. Departure 11 above (default policy, LF) is replaced by this. New test
`test_the_outbox_email_is_plain_utf8_with_crlf_and_the_body_survives_exactly` checks the raw bytes and the parsed body
(failed first: the raw file had `quoti=` at a line end, then `ng`). The Cases page download button is now labelled "Download the email (.eml)" on
the case card after approval. Two assertions in round 3's own new tests changed: the body comparison in
`test_an_approved_message_is_a_valid_email_...` now turns CRLF into LF before comparing (an email body is CRLF by
definition; still an exact comparison), and the UI test looks for the new button label. 627 passed; preflight READY
(11 step, 2 case lines); 18 of 18; cases 2 of 2; robustness 30, 15, 4, 0 (691 of 705); hashes match.

## Handoff (23 Nov 2026, context nearly full)

**Done.** Round 3 checkpoints 0 to 8: `f762db1` (W), `ec89c9b`, `ae3fdac`, `9341eeb` (1), `e3798f1` (2), `56d6477` (3),
`08438d0` (4), `9d6b791` (5), `de2ed97` (6), `a5859b8` (7), `b381b2e` (8). Round 4 checkpoint 0 on `round4` (the commit
after `b381b2e`). Tests: 627 passed after the email-encoding fix (561 at round 2). Standing results unchanged throughout:
preflight READY with 11 `[ok  ] step` lines plus `case 12` and `case 13`; 18 of 18; `--cases` 2 of 2; robustness 30,
15, 4, 0 with 691 of 705 values; frozen files and baseline data unchanged (rules.yaml changed on purpose in round 3).

**Exact next step.** Round 4 checkpoint 1 on branch `round4`: read section 4 of the feature-discovery file in full
(4.1 to 4.5, lines 295 to 1064), then build "new rule sections and models with every feature switched on,
`scripts/generate_desk_data.py`, `samples/desk/`, the new POs, the bundled mail", failing tests first, every existing
file byte-identical, each new sample giving exactly its expected reasons. Choose ids outside V001 to V007, V101,
V102, R01 to R49 and PO-2026-0099 to 1042 (PO-2026-1043 is free; the round 3 plan reserved it for C3). Then
checkpoints 2 to 7 and T as in the user's instructions of 23 Nov; not 8 to 12.

**Standing-command helper.** The scratchpad script used for every checkpoint is not in the repository; the commands
are the four standing ones plus `python -m invoice_desk.run --cases`, and `sha256sum -c` against
`D:\sem_iitk\sem9\zamp\baseline_round3\sha256_*.txt`.

## Round 4

**Checkpoint 1: desk data and the inbox rules** (23 Nov 2026). `rules.yaml` gains an `inbox:` section (every key commented,
required by `InboxRules`; a validator ties each inbox owner to `cases.reply_due_days` and `cases.escalate_to`).
`scripts/generate_desk_data.py` writes `data/purchase_orders_desk.csv` (PO-2026-1043, 1046, 1047 for V101 and V102),
`samples/desk/` (d1 APPROVE, d2 HOLD `MISSING_INVOICE_NUMBER`, own manifest) and the seven bundled emails with their
manifest, UTF-8 8bit with CRLF, fixed dates, Message-IDs and MIME boundaries. `masterdata` reads the desk PO file.
`samples/desk/.gitattributes` keeps the .eml bytes. Tests 638 (+11). Standing results unchanged.
Deviation: the rule sections of features 2 to 5 are added at the checkpoint that builds each feature (context budget),
not all at checkpoint 1; `rules.yaml` changes are additions only (checked against `b381b2e`).
**Checkpoint 2: `mailbox.py`** (23 Nov 2026). Parse one `.eml` (fingerprint, Message-ID, sender, claimed date, plain
text with HTML reduced to words, attachments with their SHA-256), strip quoted text ("On ... wrote:", Outlook divider,
header block, `>` lines), find `CASE-` tokens, phrase matching, and the four-way sender check with the exact screen
sentences; shared mail domains count only by exact address. `tests/test_mailbox.py` (13). Tests 651. Standing results
unchanged. No deviations.
**Checkpoint 3: inbox sorting, routing, the Inbox page** (23 Nov 2026). `invoice_desk/inbox.py` registers each `.eml`
once (file hash or Message-ID), keeps the raw file, checks the sender, applies the safety limits, sorts by the six rules
(first match wins, the rule stored in words), runs each PDF through `Pipeline.run` with source `email` (a file seen
before is not run again), opens the case through `cases.sync`, sets owner, state and due date, and writes one audit
entry per message, per close and per routing. The `messages` table gains the inbox columns through `MIGRATIONS`.
Page "Inbox" (navigation after the Guided demo, which stays the opening page), preflight "desk rehearsal" lines and the
Inbox page in the page check. Tests 671 (`test_inbox.py` 17, `test_ui_desk.py` 3). Standing results unchanged.
Deviations: an item sorted with no person needed is owned by "Invoice Desk" so every row has an owner; the received time
is the business date with the current IST clock time, so the demo's due dates are fixed; the inbox audit entries use the
message id in the run column.
**Checkpoint 4: the reply reader** (23 Nov 2026). New purpose `reply_read` in `llm.py` (strict schema with no bank field,
`ReplyAnswer` with extra fields forbidden, `Answer.stop_reason` kept; the two existing purposes and their template
hashes unchanged). `invoice_desk/replies.py` reads a token-matched reply with quoted text removed, runs the eight checks,
writes `messages.read_json`, `cases.proposed_json`, the case events `reply_received`, `reply_read`,
`reply_needs_a_person` and audit entries `reply` and `reply_read` under the case's run; the case moves to its internal
owner. Cases page shows each reply and what was offered or why not; the review form pre-fills the number and shows the
sentence and the PO "agrees/differs" line. Inbox page: "Deliver the next morning's mail". `rules.yaml` gains `replies:`.
`scripts/record_reply_fixtures.py` (dry run, record, force). Preflight: `[WARN] replies` without recordings, READY.
Tests 693 (`test_replies.py` 20, UI 2 more). Standing results unchanged apart from that WARN.
Deviations: `n2_corrected_copy.eml` and the keys `corrected_copy`, `reminder_after_days`, `max_reminders` are left to
checkpoints 8 and 10 (not in this round), so a PDF inside a reply is listed and handled by hand; n1 and n3 have the
same text after quotes are removed, so one recording serves both (n3 is refused by the sender check); the n4 wording
avoids the exact bank-change phrases so that it reaches the reply reader, as the spec intends; recordings not made
(no API calls allowed here), so the manual path is what the app shows until they are recorded.
**Checkpoint 5: the payment run** (23 Nov 2026). `invoice_desk/payments.py`: candidates are POSTED ledger entries of
origin `run` not on an approved run; due date from the vendor's terms (`decide._due_date`), legal date from the round 3
`statutory_clock`, discount return d/(1-d) x 365/(N-t) against the hurdle, blocks (vendor not approved, open bank
change), flags (twins, 180-day credit reversal); groups pay / not due / blocked / on hold, plus deferred for cash and
taken out; legal-date lines never deferred; removal with name and reason; approval by a named person with release
authority, two different people above the limit (`review._person`), refused if the fingerprint changed; on approval the
run and lines are stored, `payments/PR-YYYYMMDD-NN.csv` is written with accounts masked, one audit entry carries the
export hash; cancel with a reason frees the lines. Ledger status never changes. New tables `payment_runs`,
`payment_run_lines` (in `TABLES`, reset clears them and the export folder). Page "Payments", `rules.yaml` `payments:`,
P1 sample and `data/vendor_terms_desk.csv` from the generator, preflight `payment run` line and the page in the page
check. The worked example of 4.3 comes out as written (37.24%, 9.17%, Rs 1,08,817.24, Rs 2,220.76, Rs 338.76, legal date
26 Nov first, sample 01 blocked by sample 09, C2 on hold with 24 Nov). Tests 715 (`test_payments.py` 20, UI 2).
Deviations: the terms file is `data/vendor_terms_desk.csv` (the round's data naming rule) not `data/vendor_terms.csv`;
"approved for payment in run {id}" is shown on the Payments page only (other pages unchanged); the MSME-1 register rider
is not built.
**Checkpoint 6: payment-status answers** (23 Nov 2026). `invoice_desk/answers.py`: sender check first (on file or the
vendor's own domain; shared mail services only by exact address), invoices found by `invoice_no_key` within the
sender's own vendor (spacing and case ignored), unmatched references answered "We have no record ...", no number quoted
lists open invoices newest first up to `answers.max_listed`; one sentence per invoice from one record (approved payment
run, posted with pay-by date, vendor blocked, on hold with the existing `vendor_issue` or "being checked", not accepted
with the existing sentence); rule-written draft with the records table; overdue invoices flagged for the Finance
controller; optional AI rewording only through `draft_vendor_note` and its checks. A named person approves the draft
as shown (fingerprint), the `.eml` goes to the outbox addressed to the address on file (UTF-8 8bit, CRLF), the outgoing
row carries `in_reply_to` and `facts_json`, one audit entry, the question becomes "answered". Inbox row shows the draft,
the records and "Approve and send". `rules.yaml` gains `answers:`; preflight "payment question" line. The bundled m3
gives "Approved. Due for payment on 18 Dec 2026." and, on fresh books, "We have no record of receiving invoice
DQI/26-27/1193"; after sample 05 it gives "Not accepted." with the duplicate sentence. Tests 729 (`test_answers.py` 13,
UI 1). Standing results unchanged.
Deviations: a hold for a changed bank account is answered "Received on {date} and being checked" so the fraud check is
never named; invoices without a number cannot be quoted back and are not listed; `reply_to_address_on_file` accepts
only `true`; m8 and m9 are built inside the tests, not as files.
**Checkpoint 7: change request step 1** (23 Nov 2026). `invoice_desk/changes.py`: a proposal may touch only the six
numbers the what-if sandbox changes, within the bounds in `rules.yaml` `changes.allowed`, at most
`max_keys_per_request` together; `apply` returns the base object for no change and otherwise a copy with version
"+proposal" and its own hash; `replay` decides every past document twice, in arrival order, on scratch copies of the
seeded books with recorded AI answers only (fixture mode, no live call), stops at `replay_max_documents` and says so;
`save` stores a PROPOSED change request (new table `change_requests`) with the replay, refusing a number already
proposed; a YAML difference can be downloaded. `in_force` returns rules.yaml itself: nothing goes in force in this step.
Page "Procedure" (propose, replay, save, waiting proposals, history) and preflight "change request replay" line plus
the page in the page check. Replay of samples 01, 07 and 08 with 1.1% and Rs 550 gives one difference (08, HOLD to
APPROVE, `PRICE_VARIANCE` gone); with only 1.1% none. Tests 743 (`test_changes.py` 13, UI 1). Standing results unchanged.
Deviations: the `changes` section carries only the step 1 keys (no `second_approver_role`, no `suggest`), and samples
L1, L2 and PO-2026-1048 are left to step 2, which is not in this round.
**Checkpoint T: navy primary colour** (23 Nov 2026). `.streamlit/config.toml` `primaryColor` changed to `#1F3A5F`; every
other setting kept, no CSS changed (the running-stage colour in `ui/common.py` is untouched). `tests/test_theme.py`
checks the colour, the rest of the config, that the colour is not in the CSS, and that all eleven pages open with the
Guided demo as the opening page. Tests 746. Standing results unchanged (preflight READY with the one replies WARN).

**Round 4 status after checkpoint T.** Checkpoints 1 to 7 and T done; 8 to 12 not started, by instruction. Recording
still to do (needs a key, run by the user): `python -m scripts.record_reply_fixtures --dry-run`, then without
`--dry-run`, then `python -m scripts.preflight` (the replies line turns ok), then commit `fixtures/llm/reply_read__*.json`.

## Round 5: UX, demo data and presentation

**Checkpoint 0: confirmation and baseline** (23 Nov 2026). Confirmed: pages registered via `st.navigation` in `app.py`; all 11 page files located; Process invoice uses `iter_run` generator; engine takes file content/name; only `tax_invoice` counted as invoices; approvers include Meera Iyer, Priya (AP), R. Iyer, N. Rao, Stores, Procurement; company state code "29"; MSME acceptance from goods receipt/invoice date; reasons without structural family; display settings not yet in rules.yaml. Baseline invariant results recorded: 18 samples, 11 guided demo steps, preflight READY, robustness 30/15/4/0 (691/705 values).

**Round 4 fixes from browser check** (23 Nov 2026). Payments empty state with one-click "Load the payment-run example" (samples P1, 02, 07, 03, 10, 01, 09, C2); Procedure screen builder wording removed, second approver/rollback marked planned, empty state with button to load samples 01, 07, 08; Cases instruction detection via rules with safety message and routing to Vendor master team; Cases look-alike sender warning ("Careful: {domain} looks like {vendor_domain} and is not the same."); Case header displays "invoice (number missing)"; token-matched reply with PDF attachment displays notice and attachment name; Outbox Date headers use pinned business date. Tests 753 (`test_browser_fixes.py` 7). Standing results unchanged (preflight READY, 18 of 18, cases 2 of 2, robustness 30/15/4/0). Deviations: none.

**Checkpoint 1: Theme and UI kit** (23 Nov 2026). Extended `.streamlit/config.toml` with the palette colours and theme keys from section 7.1 without duplicating existing round 4 settings. Added reusable UI components in `invoice_desk/ui/ui_kit.py` and root `ui_kit.py`: `STATUS`, `ACTOR`, `chip_md`, `status_chip`, `actor_chip`, `inr`, `inr_short`, `due_label`, `Stage`, `stage_card`, `run_stages_live`, `evidence_panel`, `case_card`, `kpi_card`. Grouped the sidebar in `app.py` under four sections ('See it work', 'Work the desk', 'Results', 'Reference'). Tests 755 (`tests/test_ui_kit.py` 2 added). Standing results unchanged (preflight READY, 18 of 18, cases 2 of 2, robustness 30/15/4/0). Deviations: none.

**Checkpoint 2: Vocabulary** (23 Nov 2026). Display-only mappings for stage titles, helper captions, and outcome verdicts in plain English (Cleared, On hold, Rejected, and 9 plain stage descriptions) via `STAGE_DISPLAY_INFO`, `stage_display_title`, `stage_display_help`, `VERDICT_DISPLAY`, and `verdict_display_label` in `invoice_desk/ui/common.py`. Underlying database values, reason codes, JSON fields, and engine functions unchanged. Tests 758 (`tests/test_vocabulary.py` 3 added). Standing results unchanged (preflight READY, 18 of 18, cases 2 of 2, robustness 30/15/4/0, values 691/705). Deviations: none.
