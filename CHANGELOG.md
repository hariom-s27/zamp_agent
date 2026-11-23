# Changelog

## Round 2 (22 Nov 2026)

Round 1 ended with 364 passing tests and 14 samples giving their documented decision.
Then two things were run against it on a Windows laptop: an independent audit
([docs/AUDIT_REPORT.md](docs/AUDIT_REPORT.md)) and a browser walkthrough of every sample
([docs/UI_WALKTHROUGH.md](docs/UI_WALKTHROUGH.md)). Round 2 answers both, adds a Claude
layer, measures the reader on documents its own generator did not make, and adds a
guided demo. The rubric in [EVALUATION.md](EVALUATION.md) scored round 1 at 19 of 40 and
set the order of work. The reasons behind each change are in
[DECISIONS.md](DECISIONS.md), D42 to D62.

Rules kept throughout: a failing test was written before each fix; no test was loosened
to pass; no sample outcome is hard-coded; the decision is still a pure function of
findings and `rules.yaml`.

State at the end of the round: **561 tests pass**, 18 of 18 bundled samples give their
documented decision, the independent set reads 45 of 49 documents to the right answer
with **none approved wrongly**, and `python -m scripts.preflight` ends in READY.

---

### 1. Bugs the audit found

Each row was reproduced by a test that failed on the round 1 code
(`tests/test_round2_audit.py` unless another file is named).

| Audit | What was wrong | What changed | Test that reproduces it |
|---|---|---|---|
| Bug 1, high | Approving from the review queue never looked at the PO again. Two held invoices were approved to 24 units on a PO of 20. | `review.po_position()` recomputes what is posted on the PO at the moment of approval, with the tolerances of the PO match stage. An approval that would overbill is refused, the hold on record is rewritten with the current numbers, and the attempt is written to the audit trail as `review_refused`. There is no override: amend the PO or reject. The queue shows the PO "as it stands now" before anyone presses a button. (D42) | `test_two_approvals_from_the_review_queue_can_never_overbill_a_po`, `test_a_refused_approval_shows_why_and_leaves_a_fresh_hold_on_the_record`, `test_when_the_po_is_amended_the_same_approval_goes_through` |
| Bug 2, medium | A failure after the ledger write left a POSTED entry under a HOLD decision. | Ledger entry, receipt and run record are written in one transaction (`Store.transaction()`). Any failure rolls all of them back, and a second attempt records a hold with the reason. (D51) | `test_a_failure_after_the_ledger_write_rolls_the_posting_back` (one case per write) |
| Bug 3, medium | A bank change was approved by one person ticking a box. | Two different people. The first confirms a call-back to the number in the vendor master, records the call and may attach the vendor's letter. The invoice stays on hold and the account is not trusted. The second reads the call record and approves. The same name in another spelling is refused. The count is `review.bank_change_approvers` in `rules.yaml`. (D43) | `test_a_bank_change_needs_two_different_people`, `test_the_first_approver_must_record_the_call_and_the_second_must_confirm_it`, `test_proof_of_the_bank_change_is_kept_with_the_review` |
| Bug 4, low | A failure while saving the run was swallowed: ledger and receipt existed, the run did not. | Covered by the same transaction. If nothing can be written at all, the run still ends as a hold that says it could not be recorded. (D51) | `test_when_nothing_can_be_written_the_run_still_ends_in_a_hold_and_nothing_is_posted`, `test_a_failed_review_changes_nothing` |
| Bug 5, low | "Left blank" was assumed from a label with no value, so a number OCR had missed was blamed on the vendor. | On a scan, the reader measures the ink in the box on the picture. Only a box seen to be empty asks the vendor for the number; anything else is "could not be read". (D54) | `test_ink_beside_a_label_is_measured_and_ruled_lines_do_not_count`, `test_the_vendor_is_blamed_for_a_blank_number_only_when_the_box_is_seen_to_be_empty`, `test_a_scan_with_something_unreadable_in_the_number_box_does_not_blame_the_vendor` |
| Bug 6, low | The check on model output accepted a piece of an identifier ("12" inside "INV-0012"). | An identifier must be a whole token in its quote and must stand beside words that say what it is. (D46) | `tests/test_llm.py -k "piece_of_a_longer_number or whole_identifier or says_what_it_is"` (13 cases) |
| Bug 7, low | Replay ignored the prompt and model it was recorded with, and every live call overwrote the recording. | An answer is stored under file fingerprint, purpose, prompt version and model. An answer made with another prompt is ignored. Nothing is overwritten unless `--force` is given. (D47) | `tests/test_llm.py -k "stored_answer or stored_and_reused or replays"` |
| Bug 8, low | `llm.assist: always` had no test. | Tested: asked as a second reader, the model still never overrules a value the rules read. | `tests/test_llm.py -k second_reader` |

### 2. Claims the audit marked "partly true"

| Claim | What the audit said | What changed |
|---|---|---|
| "Same invoice, same answer" | Four holes: a key in `.env` made the `auto` mode go live; the same file twice gave APPROVE then REJECT; date rules used the real clock; the model can change an outcome by filling fields. | A key alone changes nothing: live calls need `LLM_MODE=cached-live` (D49). A file already on record returns its first answer and books nothing twice (D50). The app runs on a pinned business date, 21 Nov 2026, unless told otherwise (D25, D60). The documents now say exactly what is deterministic: same findings and rules give the same decision; same file, rules, master data, date and recorded AI answers give the same record. Test: `test_three_fresh_databases_give_three_identical_records`. |
| E2 "disguised duplicate" is rejected | True only because its PO was already full. | Same vendor, lines and total under a new number is rejected on the match itself. Only goods receipts covering two deliveries clear it. Sample 05's PO was given room so the sample proves it. (D44) Test: `test_e2_is_rejected_on_the_match_itself_while_the_po_still_has_room`. |
| E4 "at the tolerance edge" | 0.11% and 1.56% against a 1% limit, and the second also tripped the PO balance. | The pair is now 0.95% and Rs 488.16 on the line (approved with a note) against 1.01% and Rs 515.20 (held for the price alone). The printed prices differ by two rupees. (D61) Tests: `test_e4_sits_on_both_sides_of_the_price_tolerance`, `test_between_the_two_e4_samples_one_rupee_shows_why_both_limits_exist`. |
| "Every threshold is in rules.yaml" | Name matching, line pairing, PO inference weights, scan agreement levels and file limits were in code. | All are in `rules.yaml` with a comment each. There are no defaults in code: a missing or misspelt key stops the app and names the key. (D55) Tests: `test_every_threshold_the_process_uses_is_in_rules_yaml`, `test_the_thresholds_moved_to_rules_yaml_are_really_used`. |
| "Tamper-evident" receipts | No secret: someone who can rewrite the database can rebuild the chain. | The wording now says what the chain proves. Setting `RECEIPT_SIGNING_KEY` adds an HMAC made with a key the database does not hold. The Dashboard shows the latest fingerprint to note down elsewhere. (D53) |
| Bad inputs "never crash" | True, but only two bad files were bundled, so the rest could not be tried live. | Four more are in `samples/bad_inputs/`: password-protected, 25 pages, a text file renamed to .pdf, a truncated download. |
| Live-OCR tests | They passed on almost any hold, because business reasons were in the accepted set. | Only reading reasons are accepted. Test: `test_the_live_ocr_tests_accept_only_reading_holds`. |
| Held-out scorecard | Same generator as the development layouts. | A second test set made with other tools: section 4. The old scorecard is now described as "unseen layouts from the samples' own generator". |

### 3. The AI layer (Claude)

Round 1 had an optional call to an OpenAI-compatible endpoint that had never run. It was
replaced. (D45 to D49; `invoice_desk/llm.py`, `invoice_desk/config.py`)

- **Provider and models.** The Anthropic Python SDK. `LLM_MODEL` defaults to
  `claude-haiku-5-5`; `LLM_ESCALATION_MODEL` (for example `claude-sonnet-5-5`) is tried
  once when the first answer gives nothing usable. No effort level is sent unless
  `LLM_EFFORT` is set, so each model runs at its documented default. The request and the
  schemas were checked against the API documentation on 22 Nov 2026 (RESEARCH.md
  section 14); an answer that carries a thinking block before its text is read correctly.
- **What the model does.** It reads a document the rules could not (sample 10, an
  invoice written as a letter), returning strict JSON in which every value is text
  copied from the page with the sentence it came from. On request it rewords the drafted
  message to a vendor. It never decides, never calculates, and never sees the PO
  register, the vendor master or the ledger.
- **What code checks.** The quote is on the page character for character; the value is a
  whole token inside the quote; an invoice or PO number stands beside words that name
  it; the invoice's own arithmetic agrees. A value the rules read is never overwritten.
  Trust levels are in `rules.yaml`: a value read by a model from a scan, or one the model
  marked as unsure, is below the approval line, so the invoice is held. A reworded vendor
  message is used only if it adds no figure, date or reference and keeps the invoice
  number, the PO and the total.
- **Three modes, shown in the sidebar.** FIXTURE replays recorded answers with no key
  and no network, and is the default. CACHED-LIVE calls the API only when no stored
  answer exists for that file, prompt version and model, then keeps it. OFF is rules
  only.
- **Bounds.** A timeout, a retry count, a cap per invoice checked against a worst-case
  estimate before the call, and a cap per day in a ledger shared by every visitor. Any
  failure (refusal, truncated answer, broken JSON, bad key, rate limit, server error, no
  network) skips the model's part and shows why. A run never fails because the API did.
- **Recording.** `python -m scripts.record_llm_fixtures` runs every sample through the
  real pipeline and records each answer the demo will ask for. `--dry-run` shows what
  would be sent and the most it could cost, with no network.
- **Rejected uses** (D49): the model deciding or advising the decision, a model-written
  summary of the decision, the model's confidence as a reason to approve, model-based
  duplicate detection, chat with your invoices.
- **Showing a live call on purpose** (D63). Once an answer is stored, CACHED-LIVE
  reuses it, so a rehearsed demo makes no call. "Ask Claude afresh on every run" in the
  demo settings sends the document again, inside the caps. If that call fails, the
  stored answer is used and the record says so. The screen never writes into
  `fixtures/llm/`.
- **What Claude read is outlined on the document** (D64), in its own colour: the
  value's own words inside the verified quote, located by code. Messages name fields the
  way a reader does ("invoice number", "line items").
- **Tests.** 86 in `tests/test_llm.py`, using the real SDK with a fake HTTP transport,
  including every failure mode. **No recorded answer ships**: the API cannot be reached
  from the build environment, and a hand-written recording would be a fake. A test fails
  if a file in `fixtures/llm/` does not have the shape of a real recording, or was made
  by the tests' own stand-in service.
- **Rehearsed.** To see the state the project will be in after recording, answers made
  through the stand-in service were placed in `fixtures/llm/` for one run: every test
  passed, preflight ended READY with sample 10 approved, and the screen was checked in
  a browser. They were then deleted, and the guard in the line above was added so that
  one left behind would fail the suite. What that rehearsal cannot show is how the real
  model answers.

### 4. Reading invoices the samples' generator did not make

- **A second test set** (`robustness/`): 49 documents made with headless Chrome,
  wkhtmltopdf, LibreOffice Calc and Writer and LaTeX in the layouts Indian accounting
  packages print (Tally, Zoho, Busy, spreadsheet and word-processor bills), plus 12
  photographs of printed pages. 34 should be approved and 15 should be stopped, for
  eleven different reasons. Two splits: 32 tune documents that were opened and debugged,
  17 holdout documents that were not. (D56)
- **The measurement** (`python -m scripts.evaluate_robustness`,
  [docs/robustness_report.md](docs/robustness_report.md)):

  | | Right answer | Held without reaching it | Approved wrongly |
  |---|---|---|---|
  | Before any change | 13 of 49 | 36 | 0 |
  | After | 45 of 49 | 4, all photographs | 0 |
  | Holdout text PDFs, before and after | 6 of 12, then 12 of 12 | | 0 |

- **What changed in the reader**, each from a failure on a tune document and each
  written as a general rule (full table in `robustness/CHANGES.md`): tax lines printed as
  rows of the item grid; the amount in words under its label and across a line break;
  glyphs the layout library cannot decode are read again from the PDF engine (D62); an
  invoice number introduced only by "#"; labels that run into their value; the invoice's
  arithmetic chooses between several printed totals (D58); two invoice lines may bill one
  PO line (D59, `matching.line_sharing_min`); photographs are cropped, straightened,
  evenly lit, turned upright and read twice in two different ways
  (`invoice_desk/straighten.py`, D57).
- **Stricter than before:** an invoice number or date found in only one of the two reads
  of a scan is no longer trusted.
- **What it cost.** On the old scorecard (296 layouts from the samples' generator) text
  PDFs are still 200 of 200. Scans went from 88 to 86 of 96 straight through, with no
  misread approved in either run. The two that moved are correct reads now held under
  the stricter rule above.
- **One disclosure.** The first full run printed the class and reason codes of the
  holdout documents next to the tune ones. No holdout document was opened or debugged,
  and no change was made to fix one. The two holdout photographs that fail were left
  exactly as they are.
- **Tests.** `tests/test_robustness.py` pins every number in the report;
  `tests/test_robustness_fixes.py` (42) and `tests/test_straighten.py` (19) cover each
  change. OCR output for the photographs is recorded in `fixtures/ocr/`, so the numbers
  are the same on every machine.

### 5. The screen: what the walkthrough asked for

| Walkthrough item | What was done |
|---|---|
| 1. Hashes on primary cards | An expander, "Tamper-evident audit trail", lists the events in words. Fingerprints are one click further in, under "Show technical details". |
| 2. "Run ID" | Records are numbered like vouchers: `AP-2026-0001`. (D52) |
| 3. Milliseconds | Banners say "processed in under a second" or "in 5 seconds". The Dashboard shows "Typical handling time". Exact timings stay on the stage cards. I did not use a made-up throughput per hour. |
| 4. "Approved with no person involved" | "Straight-through processing (STP)", with "matched every rule, no manual touch". |
| 5. Test-case labels | Samples have business titles and arrive under the file names a vendor would use (`Aravalli_ASS-2026-0933.pdf`). "Edge case 1" to "Edge case 4" remain as tags because the brief asks for edge cases by name. |
| 6. Reset button in the sidebar | Moved to "Demo settings and admin" on the Guided demo page. The sidebar no longer has controls. |
| 7. Delay slider | "Pace of the live view": Presentation, Normal, Fast, on the pages that have a live view. |
| 8. Diagnostics in the sidebar | One line ("Ready", rules version, AI mode) and a "Technical details" expander. |
| 9. "Mark as handled by hand" | "Close as processed manually". |
| 10. No direct action for the vendor reply | A "Message to the vendor" card with the address from the vendor master, a copy icon, a download, and an optional rewording by Claude that code checks. |
| 11. No proof for a bank change | The first approver can attach the vendor's letter or a cancelled cheque; it is stored with the review. A second approver is always required, not only above an amount. |
| 12. Same sample twice gave a red "duplicate" | The first record is returned with a notice that the file was received before. Nothing is booked twice. I did not add a "simulation mode": a second code path that does not write would be a different process from the one being shown. (D50) |
| 13. Formula in the tax-inclusive explanation | A table: price printed with GST, GST rate, divided by, pre-tax price. |

Also changed on screen: the review queue shows the PO as it stands now and a refused
approval with its reason; a re-submitted file counts once on the Dashboard; a
photographed page is shown the way it was read (cropped and straightened).

Found by looking at the finished screens in a real browser, each fixed with a test
first:

- The second approver of a bank change was shown the first approver's name and call
  note already typed in. A browser keeps a field's text by its key, so the second
  approval now has fields of its own. The headless test harness did not show this.
- With an answer for the letter invoice in place, the Process page stated "Expected
  decision: HOLD" above the run and "Expected decision: APPROVE" below it. The line now
  names the outcome this session will produce and why.
- Field names appeared as the code spells them (`invoice_number, line_items`).
- Values Claude read had no outline on the document, although every other value did.

### 6. Guided demo and hosting

- **Guided demo page** (the app's first page): eleven scenarios, one per click or "Run
  all remaining", through the real pipeline with the live stage view. Each decision is
  compared with the expected one after the fact. "Start a fresh demo" resets the books.
  "Try your own invoice" takes an upload through the same stages. (D60;
  `invoice_desk/demo_steps.py`, `invoice_desk/ui/demo.py`)
- **Preflight rehearses it.** `python -m scripts.preflight` now also runs the eleven
  steps and opens every page without a browser. `--llm` makes one small live call.
- **A pinned business date.** The app processes on 21 Nov 2026 unless
  `INVOICE_DESK_TODAY` is set or the switch on the Guided demo page is turned off, so
  date checks cannot change a rehearsed demo next year.
- **Private books per visitor.** With `INVOICE_DESK_SANDBOX=session` every browser
  session has its own database. On such a host live AI also needs `LLM_ALLOW_LIVE=1`.
- **Streamlit Community Cloud files.** Pinned `requirements.txt`, `packages.txt` for
  Tesseract, `.streamlit/secrets.toml.example`, `.streamlit/config.toml`.
- **Dockerfile.** Updated for the new settings. **It was not built**: the build
  machine could not pull the base image. Build it once before relying on it.
- **Scripts.** `scripts.generate_test_data` and `scripts.record_ocr_fixtures` now have
  a `--help` that does nothing else. Before, `--help` was ignored and the script ran.
- `pytest.ini` no longer passes `-q`, so `pytest -q` shows its summary line (audit,
  section 3).

### 7. Samples and data that changed

Every other sample file is byte for byte what it was in round 1 (checked by hash).

| File | Change | Why |
|---|---|---|
| `samples/07_E4a...pdf`, `08_E4b...pdf` and their truth files | Regenerated: 16 units at printed prices of Rs 3,812 and Rs 3,814 with GST inside, against a PO price of Rs 3,200 before tax. Their POs are unchanged; 16 units fit both. | The audit showed the old pair was not at the tolerance, and that the second one also tripped the PO balance (D61). |
| `data/purchase_orders.csv` | One PO changed: PO-2026-0171, behind sample 05, now orders 16 of each line instead of 8. Received stays 8. | E2 must be rejected while the PO still has room and no second delivery has arrived (D44). |
| `samples/bad_inputs/` | Four files added. | So that every bad input can be tried live. |
| `samples/manifest.json` | 18 entries; business titles; `received_as` file names; the expected reason of sample 08; a `with_llm` expectation for sample 10. | Walkthrough item 5; the changes above. |
| `rules.yaml` | Version `2026.11-2`. New keys for the thresholds moved out of code, `review.bank_change_approvers`, `llm.confidence`, `matching.line_sharing_min`. No reason code changed its action. | D43, D46, D55, D59. |

The documented outcome of every round 1 sample is unchanged, with one exception that the
audit asked for: sample 08 is now held for `PRICE_VARIANCE` alone, where it used to be
held for `PRICE_VARIANCE` and `OVER_PO_BALANCE`.

### 8. Tests

364 test cases in round 1, 561 now. By test function: 169 of round 1's 220 are
unchanged, 167 are new, 30 were edited and 21 were replaced or renamed. The edited and
replaced ones, by reason:

| Where | What changed in the test | Why |
|---|---|---|
| `test_pipeline.py` | `test_the_same_file_twice_is_rejected_the_second_time` became `test_the_same_file_twice_is_booked_once_and_answered_the_same_way`. | The old expectation was the behaviour the audit and the walkthrough reported as a fault (D50). |
| `test_duplicates.py` | `..._with_room_on_the_po_asks_a_person` became `..._is_rejected_even_when_the_po_has_room`; its sibling now also checks that a full PO is mentioned. | The rule changed on purpose (D44). |
| `test_review.py` | Bank-change tests now make two approvals, and assert that one approval is not enough. One amount follows the regenerated sample 08. | D43, D61. |
| `test_store.py` | Record ids are `AP-2026-0001`; `test_run_ids_are_sequential` became `test_ap_references_are_sequential_and_carry_the_business_year`. | D52. |
| `test_cli.py`, `test_extract.py` | 18 samples instead of 14; the figures of the regenerated sample 07. | Section 7. |
| `test_llm.py` | Rewritten for the Claude layer: 18 functions became 50. The 7 that kept their names assert the same things against the new client. | Section 3. |
| `test_ui.py` | Rewritten for the new screens: 20 functions became 42. Every round 1 behaviour that still exists is still asserted; the ones for the old duplicate banner, the one-person bank approval and the sidebar reset were replaced by tests of what replaced them. | Section 5. |

New files: `test_round2_audit.py` (31 cases), `test_robustness.py` (11),
`test_robustness_fixes.py` (42), `test_straighten.py` (19), `test_scripts.py` (10).

### 9. Documents

- New: `CHANGELOG.md`, `EVALUATION.md`, `docs/robustness_report.md`,
  `robustness/CHANGES.md`, `docs/AUDIT_REPORT.md` and `docs/UI_WALKTHROUGH.md` (the two
  inputs, kept as received).
- Rewritten: `README.md`, `DEMO_SCRIPT.md`, `INTERVIEW_PREP.md`, `SETUP_WINDOWS.md`.
- Extended: `DECISIONS.md` (ten round 1 decisions revised in place and marked; D42 to
  D62 added), `RESEARCH.md` (sections 10 to 15), `PROGRESS.md`.
- `docs/screenshots/` holds six pictures of the new screens. The pictures the
  walkthrough refers to (`docs/screenshots/walkthrough/`) were not in what I received,
  so those links in `docs/UI_WALKTHROUGH.md` do not resolve.

### 10. What could not be done or checked here

| Not done | Why | What to do |
|---|---|---|
| A real Claude answer recorded | The API cannot be reached from the build environment. | `SETUP_WINDOWS.md` step 6: one command with your key. Until then sample 10 is held by the rules alone, and the screen says so. |
| Windows run of this round | Built and tested on Linux, Python 3.11. Round 1 was verified on Windows by the audit. | `SETUP_WINDOWS.md` steps 2 and 3. |
| Deployment | No account or outbound access. | `SETUP_WINDOWS.md` step 8. |
| Dockerfile build | The base image could not be pulled. | `docker build -t invoice-desk .` once, if you use plan B. |
| Reviewer identity | The demo has no login, so reviewer names are typed. | In a deployment the name comes from single sign-on (D43). |
