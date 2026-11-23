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

