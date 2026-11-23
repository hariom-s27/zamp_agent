# Audit report: Invoice Desk (PS-1)

Audited 22 Nov 2026 on Windows 11, PowerShell, Python 3.11.16, Tesseract 5.4.0 (the
samples were built with 5.3.4 on Linux). No code was changed. The audit added only the
`.venv\` folder and an empty `data\runtime\` folder, which preflight creates. Both are
in `.gitignore`.

## 1. Summary verdict

**It works on Windows and the headline claims hold.** I got the documented test counts
in both configurations, all 14 samples gave their documented decision with live OCR and
with recorded OCR, and preflight reports READY. Nothing in the engine is hard-coded to
sample IDs, file hashes or PO numbers. The tests mostly check real behaviour, including
values on both sides of each threshold.

**But there are two real logic bugs, and the weakest area for an interview is the AI
component.**

- **Overbilling through the review queue.** Approving held invoices never re-checks the
  PO balance. I approved two held invoices and overbilled a PO by 4 units (₹1,00,000)
  with no warning. That is the very risk E1 is built to catch.
- **Late failure in the Post stage.** If a step after the ledger write fails, the
  decision says HOLD but the ledger stays POSTED.
- **The AI assist has never run against a real model.** `fixtures/llm/` is empty, so
  sample 10 ("AI assist") only ever shows a HOLD. For an *AI* Solutions Analyst role,
  that is the first thing to fix.

The E4 edge case is also not really "at the tolerance edge". The numbers are far from
the limit on both sides (details in section 4).

## 2. Architecture in plain words

One Python package (`invoice_desk/`) does all the work. The Streamlit app (`app.py`,
`invoice_desk/ui/`), the command line (`invoice_desk/run.py`), preflight and the tests
all call the same `Pipeline` (`invoice_desk/pipeline.py`). It runs nine stages in
order. Each stage returns a summary, evidence tables and **findings** (a code plus
expected/observed values, a question and a note to the vendor). Each stage is wrapped
in try/except, so a crash becomes an `INTERNAL_ERROR` hold instead of a stack trace.

| # | Stage | Reads | Decides / produces | `rules.yaml` used? |
|---|---|---|---|---|
| 1 | Intake | file bytes | SHA-256, run ID | no |
| 2 | Read (`reader.py`) | PDF | Text-layer words with positions, or for a scan: deskew, block split, Tesseract run twice (greyscale and black-and-white). Recorded OCR if no engine. Corrupt or empty files are rejected; over 15 MB or 20 pages is held. | no (limits are in code) |
| 3 | Extract (`extract.py`, `second_read.py`, `llm.py`) | words | Header fields and line items, each with source and confidence. Document type (a quotation is rejected). The two scan reads are compared and the one whose figures add up is used. Optional LLM fills gaps, but only with quotes found on the page. | `company`, `llm.assist`; also `tolerances`/`gst`/`dates` through `validate()` during reconciliation |
| 4 | Validate (`validate.py`) | invoice | 14 self-checks: required fields, read confidence, arithmetic, amount in words, GST rate/type/GSTIN checksum, buyer GSTIN, dates, currency | `tolerances.rounding_inr`, `reading`, `gst`, `dates`, `company` |
| 5 | Vendor (`vendor.py`) | invoice, `data/vendors.csv`, reviewer-verified accounts | Vendor by GSTIN, else by name; blocked/unapproved; **bank account differs from master** | no (name thresholds are in code) |
| 6 | PO match (`po_match.py`) | invoice, `data/purchase_orders.csv`, ledger | Quoted PO or inferred PO (always held); line pairing; quantity against ordered minus already posted; price inside **both** % and ₹ limits; cumulative PO value; goods received | `tolerances.*`, `po_inference` |
| 7 | Duplicates (`duplicates.py`) | ledger | Same file, same invoice number (reject if booked, hold if the first is on hold), same content under a new number (reject only if the PO has no room left, else hold) | `duplicates.*` |
| 8 | Decide (`decide.py`) | findings | **Pure function.** Each code is looked up in `rules.yaml → reasons` to get its action and owner. Strictest wins: REJECT > HOLD > NOTE. A clean invoice over ₹10 lakh is held. An unknown code is held. All text comes from templates. | `reasons`, `auto_approve` |
| 9 | Post and verify | decision | Ledger write (POSTED/PENDING/REJECTED), read-back compare, hash-chained receipt (includes rules hash and master-data hash), file stored | rules hash |

The review queue (`review.py`) lets a named person approve or reject a hold. A bank
change needs a call-back checkbox. A missing invoice number needs the number to be
entered. The human decision is added to the same receipt chain.

## 3. Windows run: exact results

| Step | Result |
|---|---|
| `py -3.11 -m venv .venv` | **Failed on this laptop.** The `py` launcher's 3.11 entry is a broken uv registration ("Unable to create process using '.venv-python\…'"). This is the machine's fault, not the project's. I used uv's Python 3.11.16 by full path. Note the README's fallback (`python`) points to **3.14** here, which preflight only WARNs about. |
| `pip install -r requirements.txt` | OK, 54 packages, no build errors. About 10 minutes on this network. |
| `python -m pytest` (Tesseract present) | **364 passed, 0 skipped**, 154 s |
| `python -m pytest` (Tesseract hidden in-process) | **358 passed, 6 skipped** (the 6 live-OCR tests), 65 s |
| Live-OCR tests with `INVOICE_DESK_STRICT_OCR=1` | **91 passed**: both bundled scans and a never-seen scan read exactly right |
| `python -m scripts.preflight` | **READY**, 0 warnings, 14/14, receipt chain verified, slowest sample 5.7 s |
| `python -m invoice_desk.run --all` (live Tesseract) | **14 of 14**. Scans 4.9 to 5.5 s, everything else under 0.4 s. |
| Same, Tesseract hidden | **14 of 14**. The Read stage says "recorded OCR confidence 93% and 92%". |
| `streamlit run app.py` | Starts, `/_stcore/health` = ok, page = 200. All pages are also driven headlessly by `tests/test_ui.py` (passed). I did **not** click through the UI in a browser. |

Windows-specific issues: **none in the project.** The `₹` sign prints correctly
(`run.py` reconfigures stdout to UTF-8), file names with backslashes are cleaned,
SQLite showed no locking problems, and CRLF line endings are normalised before the
rules and master data are hashed. Two notes: hiding Tesseract with environment
variables doesn't work because Windows resets `ProgramFiles` per process (irrelevant to
users), and `pytest -q` on top of the `-q` in `pytest.ini` hides the summary line.

## 4. Claims table

| Claim | Verdict | Evidence |
|---|---|---|
| 364 tests pass (358 + 6 skipped without Tesseract) | **TRUE** | Section 3: both counts reproduced exactly on Windows. |
| All 14 samples give their documented decision | **TRUE** | CLI live OCR 14/14, CLI recorded OCR 14/14, preflight 14/14. Verdict **and** reason codes must match (`samples.py:check`). |
| The decision uses no LLM: same invoice → same answer | **PARTLY TRUE** | `decide()` is pure: no I/O, clock or model (`decide.py:39`). Three fresh-database runs of samples 02, 06 and 08 with live OCR were identical. **But:** (a) the LLM *can* change a decision indirectly by filling fields; sample 10 flips HOLD→APPROVE by design. (b) `LLM_MODE=auto` goes **live** as soon as a key is in `.env` (`config.py:427`), and live replies vary. (c) The same file on the same database is REJECTed the second time (`DUPLICATE_FILE`). (d) Date rules use the real clock unless `INVOICE_DESK_TODAY` is set. Defensible wording: "same findings + same rules → same decision; same file from the same state → same decision". |
| E1 split-PO overbilling → HOLD | **TRUE** | PO-2026-0193 is 20 units; history H001+H002 = 16 units; the sample bills 5 → `QTY_OVER_PO`, `OVER_PO_BALANCE`. `test_split_po_story_in_sequence` also checks that 4 units fit. |
| E2 disguised duplicate → REJECT | **TRUE, with a condition** | It matches H003 (same total ₹6,39,088, 2 days apart, same lines). It rejects only because PO-2026-0171 is already fully billed (`duplicates.py:139`). The same invoice against a PO with room would only HOLD (`POSSIBLE_DUPLICATE`). That is a sound design, but say it out loud. |
| E3 scan, no number, PO inferred → HOLD | **TRUE** | `MISSING_INVOICE_NUMBER` + `PO_INFERRED`, with live and recorded OCR. "Left blank" means a number label was found with no value next to it (`extract.py:1183`). |
| E4 tax-inclusive: ₹3.39 over APPROVE, ₹50 over HOLD | **PARTLY TRUE** | Outcomes are right. But "at the tolerance edge" is wrong. E4a is +₹3.39/unit = **0.11%**, ₹67.80 on the line, against limits of 1% / ₹500. E4b is +₹50/unit = **1.56%**, **₹1,500** on the line, over both limits. E4b also trips `OVER_PO_BALANCE`, so it isn't a clean price-tolerance demo. The real subtlety is the rounding (₹3,780 / 1.18 = ₹3,203.39). |
| Bad inputs never crash | **TRUE** | `test_bad_inputs_fail_gracefully` (empty, plain text, PNG, DOCX, header+garbage, truncated, **password-protected**, corrupt, **25 pages**, **>15 MB**, blank, quotation), `test_random_bytes_never_crash_the_pipeline` (24 blobs), absurd page size. All passed on Windows. Note: only *corrupt* and *empty* exist as files in `samples/`; the others are generated inside tests, so you can't upload them live without making them first. Oversized → **HOLD**, not reject. |
| Runs without an API key; scans fall back to recorded output and say so on screen | **TRUE** | No `.env` here; the LLM shows "REPLAY", which does nothing when no recording exists. With the engine hidden, the Read stage summary says "recorded OCR" (`pipeline.py:212`). The sidebar says "not installed (recorded output for bundled scans)" (`ui/common.py:443`). An uploaded unknown scan is held with `SCAN_NOT_READ` and an instruction. On-screen wording was checked in code and the CLI, not visually in a browser. |
| No files from other companies' briefs, no secrets | **TRUE (as far as a scan can show)** | No API-key, token or private-key patterns. No `.env`, `*.db`, `secrets.toml`, office files or zips. Every email address uses the reserved `.example` domain. The candidate guide itself is not included. The README links your own GitHub repos, which is your choice. |
| (implied) "every threshold is in rules.yaml" | **PARTLY TRUE** | The decision thresholds are. These are in code: vendor-name thresholds (`vendor.py:18-20`), line-pairing similarity 0.72 (`po_match.py:22`), PO-inference weights 65/25/10 (`po_match.py:78`), scan-agreement confidences (`second_read.py:31`), file limits (`reader.py:28-29`). |

## 5. Bugs, ranked by severity

| # | Severity | Bug | Evidence |
|---|---|---|---|
| 1 | **High** | **Approving from the review queue never re-checks PO quantity or balance.** Held invoices don't use up PO balance (D09, by design). `apply_review` then sets them POSTED without recomputing. Result: overbilling with no warning. | Reproduced: two held 4-unit invoices against PO-2026-0193 (4 left), both approved → **24 of 20 units, ₹6,00,000 against ₹5,00,000**. `review.py:400-441`. |
| 2 | Medium | **If the Post stage fails after the ledger write, the ledger stays POSTED while the decision says HOLD.** `append_receipt` and `save_file` run outside the try block. The outer handler recomputes the decision but never touches the ledger. This contradicts D32 ("an approval never survives a failed posting"). | Reproduced by making `append_receipt` raise: decision `HOLD [INTERNAL_ERROR]`, ledger `POSTED`, PO balance used. `pipeline.py:126-129, 478-480`. |
| 3 | Medium (control design) | **The bank-change approval is one person ticking a box.** The finding's own question says "do not pay until a second person has approved", but nothing enforces a second person. After one approval, the new account is trusted for that vendor's future invoices. Expect an interviewer to attack this. | `review.py:416, 433-438`, `vendor.py:299`. |
| 4 | Low | A failure while saving the run is silently ignored. The ledger and receipt exist, but the run is missing from the dashboard. | `pipeline.py:135-138` (`except Exception: pass`). |
| 5 | Low | "Left blank" is inferred from a label with no value. On a scan where OCR simply missed the number, the drafted vendor note still says the vendor left it blank, against the intent of D40. | `extract.py:1183`, `validate.py:36-41`. |
| 6 | Low | The LLM check for invoice/PO numbers uses substring matching: a value "12" passes against a quote containing "INV-0012". | `llm.py:109`. |
| 7 | Low | LLM replay ignores the prompt hash and model it recorded, and every live call overwrites the recording. | `llm.py:147-166`. |
| 8 | Low | `llm.assist: always` (in rules.yaml and the UI) has no test. | `grep always tests/test_llm.py` finds nothing. |

## 6. Interviewer's audit: what looks weak, fake or over-built

- **The AI part is the thinnest and least proven.** It has never been run against a
  real model, the fixtures folder is empty, and the "AI assist" sample just shows a
  HOLD. A sceptical interviewer will say "so it's a rules engine". The design (the model
  only fills gaps, quotes are checked against the page, it never decides) is good. You
  need **one real recorded run** to show it.
- **The "held-out" scorecard is not really out-of-distribution.** "140/140 held out"
  comes from the same parametric generator that made the training layouts. Call it
  "unseen random layouts from my generator", not generalisation. A real invoice from
  the interviewer will almost certainly HOLD, because the vendor isn't in the master, there's no PO
  and it isn't addressed to Nilgiri's GSTIN. Prepare to explain that this is the correct
  outcome.
- **Over-built and hard to defend line by line.** `extract.py` is 1,191 lines of label and
  geometry heuristics, and `reader.py` adds XY-cut segmentation and double OCR. Learn the
  *shape* (label vocabulary + positions + arithmetic cross-checks + "missing stays
  missing"), not the code. There are also twelve markdown documents, including
  ready-made INTERVIEW_PREP and DEMO_SCRIPT. Make sure every sentence you say is yours.
- **"Tamper-evident" receipts** have no secret (D19 says so). Anyone with write access to
  the database can recompute the whole chain. They catch edits to individual records,
  not a rewrite of the whole history. Phrase it that way.
- **E4 is not at the edge** (see the claims table). Easy to fix, or to word honestly.
- **Tests: mostly genuine.** There are boundary tables for every tolerance, monotonicity
  of `decide()` over 400 random cases, fault injection in every stage, tamper detection,
  and a full split-PO sequence. Weak spots:
  - The end-to-end sample tests run on **recorded** OCR (`conftest.py:11`).
  - The live-OCR tests pass on almost any HOLD, because the "reading" set includes
    `PRICE_VARIANCE`, `QTY_OVER_PO` and `BANK_DETAILS_CHANGED` (`conftest.py:30-35`),
    unless strict mode is on. They do pass in strict mode here.
  - Some tests assert exact template wording, which is brittle but harmless.
- **Nothing is hard-coded to make samples pass.** I searched the engine for sample IDs,
  file hashes, PO numbers and vendor IDs and found none. Outcomes come from
  `data/*.csv` plus the seeded history. Recorded OCR is keyed by file hash, but that is
  a labelled fallback, not a shortcut.

## 7. Live-demo risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Re-running a sample without resetting → `DUPLICATE_FILE` REJECT | High | Press "Reset demo data" first, or narrate it as the duplicate control working. |
| Interviewer asks you to approve two held invoices from the queue → silent overbilling (bug 1) | Medium | Fix bug 1 before the demo. |
| "Show me the AI" → sample 10 only HOLDs | High | Record a fixture with your key (SETUP_WINDOWS step 6) and replay it in the demo. |
| A key left in `.env` → live network call, varying output, latency | Medium | Set `LLM_MODE=replay` on demo day. |
| Interviewer uploads a real invoice | Medium | It will HOLD (vendor/PO/GSTIN unknown). Explain that this is correct, and show the evidence tables. |
| Scans take 5 to 9 s (8.7 s seen under load) | Certain | Talk over it, or show a text sample first. |
| Hosted app: one shared SQLite for every visitor; anyone can reset or process samples during your demo; the app sleeps; the apt step can fail | Medium | Use the laptop as primary, hosted as backup, and wake it 5 minutes before. |
| A different OCR build on the host reads scans differently | Low (5.4 here matched exactly) | `OCR_MODE=fixture` in Secrets. |
| This laptop: `py -3.11` is broken; `python` is 3.14 | Certain on this machine | Always use `.venv\Scripts\python`. If you rebuild the venv, use `uv python find 3.11` for the path. |
| Clock: samples dated Nov 2026, 365-day age limit | Not before ~Nov 2027 | Optionally set `INVOICE_DESK_TODAY=2026-11-21` anyway. |

## 8. Top 5 improvements (value vs effort)

| Rank | Improvement | Value | Effort | Who can do it |
|---|---|---|---|---|
| 1 | **Re-check PO quantity and balance at review approval** (fix bug 1). When approving, recompute against what is currently posted. Refuse, or require an explicit "approve over PO" note, if it overbills. Add a test that reproduces the 24-of-20 case. | Very high: closes a hole in the E1 story | Small to medium | **Careful work (logic)** |
| 2 | **Record one real LLM response for sample 10** with your own key (`scripts.record_llm_fixtures`), commit it, and demo in replay mode. Run `--llm` preflight once so you have seen it live. | Very high for the interview | Low (one call) | Safe for a faster agent (setup); needs your key |
| 3 | **Make Post failure-safe** (fix bug 2). Write the receipt and file before marking the entry POSTED, or set the ledger to PENDING in the outer handler when the post stage errors. Add a fault-injection test on `append_receipt`. | Medium: makes D32 actually true | Small | **Careful work (logic)** |
| 4 | **Make E4 genuinely straddle the limit.** For example, one line ₹499 over vs ₹501 over, or 0.99% vs 1.01%. Size E4b's PO so it doesn't also trip `OVER_PO_BALANCE`. This requires regenerating samples, truth files and the manifest, and updating the tests and docs. Alternative with zero code: reword the docs as "clearly inside / clearly outside, after tax-inclusive conversion". | Medium: the edge case becomes defensible | Medium (rewording: trivial) | Regenerating: **careful**. Rewording: safe for a faster agent |
| 5 | **Docs honesty pass.** Mark Windows as verified (Python 3.11.16, Tesseract 5.4, 364/358+6). Add the `py` launcher fallback (full path or `uv python find 3.11`) instead of bare `python`. Call the scorecard "unseen generated layouts". Change "every threshold" to "every decision threshold". State that E2 rejects because the PO is full. Mention bug 3 as a known limit if it isn't fixed. | Medium: removes easy gotchas | Small | Safe for a faster agent (docs) |

Also worth considering (careful, small): require a **second, different reviewer**
for `BANK_DETAILS_CHANGED` approvals (bug 3); and tighten the live-OCR test assertion to
"documented outcome or a reading-only hold" by removing business codes from
`READING_HOLDS`.
