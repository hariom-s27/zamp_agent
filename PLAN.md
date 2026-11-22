# PLAN.md

Plan for PS-1 (invoice processing, from PDF to decision). Research is in RESEARCH.md,
the reasoning behind each choice is in DECISIONS.md, the work log is in PROGRESS.md.

## 1. Designs considered

| | Design | Summary |
|---|---|---|
| A | Low-code workflow | n8n or Make: mailbox trigger, LLM node for extraction, Google Sheet as PO register, code node for rules, chat message for the decision |
| B | LLM-first service | Fork of `outcome`: FastAPI, Postgres queue, workers, an LLM reads every invoice, a matching layer and a web UI on top |
| C | Glass-box single app | One Python app: deterministic reader and rules engine, OCR for scans, SQLite history, Streamlit UI, optional LLM assist with record and replay |
| D | Custom web app | Same engine as C behind FastAPI with a hand-built front end and a server-sent event stream |

Scores, 1 (poor) to 5 (strong), after the third review round:

| Design | Impressive | Realistic | Reliable live | Buildable | Explainable | Total |
|---|---|---|---|---|---|---|
| A | 3 | 3 | 2 | 2 | 4 | 14 |
| B | 4 | 4 | 2 | 3 | 3 | 16 |
| **C** | 4 | 4 | 5 | 5 | 5 | **23** |
| D | 5 | 4 | 4 | 3 | 4 | 20 |

### Review rounds (playing a sceptical interviewer)

**Round 1: "C is a regex demo. Where is the AI, and what about a vendor you have not seen?"**
Revision: the reader is generic (label vocabulary, page geometry and arithmetic, no
per-vendor templates) and is measured on layouts it was not tuned on. An LLM assist
fills gaps the rules cannot read, and nothing it returns is accepted unless the quoted
text is found on the page. Live responses are recorded so they can be replayed offline.

**Round 2: "What breaks live?"**
Found: OCR output differing between machines, re-running a sample and getting a
duplicate verdict, a sleeping host, Tesseract missing on the laptop, an API outage, the
demo clock drifting past date rules. Revisions: recorded OCR as a labelled fallback, a
preflight script that runs every sample and compares verdicts, a reset button and
"already processed" markers, no LLM call on the critical path, an optional frozen clock.
B dropped two points here: it cannot run without a paid API and Docker.

**Round 3: "What is shallow, and what is over-built?"**
Shallow: a hold that does not say who must do what. Added an owner, a specific
question, a drafted note to the vendor, post-and-read-back, and a review queue whose
decisions enter the audit chain. Over-built and cut: approval matrices by cost centre,
machine-learned duplicate scoring, a vendor portal, real mailbox intake, full goods
receipt handling (kept only as an optional received-quantity check).
A fourth round changed nothing material, so the design was frozen.

## 2. Chosen design (C)

```
PDF -> 1 Intake -> 2 Read -> 3 Extract -> 4 Validate -> 5 Vendor -> 6 PO match
     -> 7 Duplicates -> 8 Decide -> 9 Post and verify -> decision + receipt
```

- **Read**: text layer per page, else straighten the scan and OCR it (Tesseract), twice
  (added in improve round 1: see section 5).
- **Extract**: label vocabulary plus geometry; line items by table columns, confirmed
  by arithmetic; every field has a source box, snippet and confidence; missing stays
  missing. Optional LLM gap-fill, verified against the page.
- **Decide**: a pure function over findings and `rules.yaml`. APPROVE, HOLD or REJECT,
  with reason codes, expected and observed values, owner, question, plain-English text.
- **Post and verify**: ledger write, read-back, hash-chained receipt, run saved.
- **UI**: Process invoice (live stages), Dashboard, Review queue, Rules and data, How it works.
- **Stack**: Python 3.11, Streamlit, SQLite, pdfplumber, pypdfium2, pytesseract,
  rapidfuzz, pydantic, PyYAML, reportlab (test data), pytest.

## 3. Edge cases

Twelve candidates, scored 1 to 5 on how often it happens, what it costs if missed, how
much of the pipeline it exercises, and how clearly it demos in under a minute.

| # | Candidate | Often | Cost | Breadth | Demo | Total | Use |
|---|---|---|---|---|---|---|---|
| 1 | Split PO, third invoice over-bills | 5 | 4 | 5 | 5 | 19 | **E1** |
| 2 | Disguised duplicate (new number, new date) | 4 | 5 | 4 | 5 | 18 | **E2** |
| 3 | Scan, no invoice number, PO only implied | 4 | 3 | 5 | 5 | 17 | **E3** |
| 4 | Tax-inclusive prices at the tolerance edge | 4 | 3 | 4 | 4 | 15 | **E4** |
| 5 | Vendor bank account changed | 2 | 5 | 3 | 4 | 14 | built, shown as a fraud guard |
| 6 | Unit price above PO | 5 | 3 | 2 | 4 | 14 | covered inside E4 |
| 7 | Bundled lump-sum invoice against an itemised PO | 3 | 2 | 3 | 3 | 11 | rule and unit test |
| 8 | Vendor not on the approved list | 3 | 4 | 2 | 3 | 12 | rule and unit test |
| 9 | Arithmetic error on the invoice | 3 | 3 | 2 | 3 | 11 | rule and unit test |
| 10 | Wrong tax type or invalid GSTIN | 2 | 3 | 2 | 2 | 9 | rule and unit test |
| 11 | Billed before goods received | 4 | 3 | 2 | 2 | 11 | optional check, unit test |
| 12 | Credit note, quotation or pro-forma sent as an invoice | 2 | 2 | 2 | 3 | 9 | rule and unit test |

The four chosen map one-to-one onto difficulties the brief itself names, and each ends
differently: E1 hold with a running total, E2 reject with a link to the original, E3
hold with a question, E4 approve on one side of the tolerance and hold on the other.

## 4. Tasks and acceptance checks

### P1 Research and design
- [x] RESEARCH.md, PLAN.md, DECISIONS.md written. *Check: six acceptance questions in RESEARCH.md section 8 each map to a feature.*

### P2 Test data
- [x] Generator for vendors, POs, prior history, invoice PDFs in five layouts, scans, bad inputs, truth files. *Check: script reruns cleanly; PDFs open; scans have no text layer.*

### P3 Pipeline
- [x] Reader: triage, lines and cells, deskew, OCR, recorded OCR. *Check: text and scanned samples both yield lines.*
- [x] Extractor: header fields, line items, totals, tax, bank, document type. *Check: all truth fields match on every sample.*
- [x] Validate, vendor, PO match, duplicates. *Check: unit tests per rule, both sides of each threshold.*
- [x] Decide, receipt, store, post and verify. *Check: decide is pure; receipts chain verifies; tampering is detected.*
- [x] Pipeline runner and CLI. *Check: every sample returns its expected verdict and reason codes.*
- [x] LLM assist with verify, record, replay. *Check: stub-transport tests; unverifiable values are dropped.*

### P4 UI
- [x] Process invoice with live stages, evidence, verdict card, downloads.
- [x] Dashboard with KPIs, filters, run trace, receipt-chain check.
- [x] Review queue with recorded human decisions.
- [x] Rules and data page. *Check for all four: headless test loads each page, runs a sample, sees the verdict and the history; screenshots reviewed.*

### P5 Verify
- [x] Full test suite green.
- [x] Every sample end to end, verdict and explanation read by eye.
- [x] Bad inputs: corrupt, empty, non-invoice, oversized, password-protected. *Check: friendly message, no exception.*

### P6 Improve (two rounds minimum)
- [x] Round 1: grade as the interviewer, fix the weakest area, re-verify.
- [x] Round 2: same again.

### P7 Package and prove
- [x] README, SETUP_WINDOWS, DEMO_SCRIPT, INTERVIEW_PREP, final DECISIONS and PROGRESS.
- [x] ZIP built without caches, venvs, secrets or scratch files.
- [x] Fresh unzip, fresh environment, install, tests, one sample end to end.

## 5. What the improve rounds changed

The plan above is the plan as frozen before the build. Two improve rounds, each graded
against the guide as a sceptical interviewer, changed the following (details in
PROGRESS.md, reasons in DECISIONS.md D30 to D41).

**Round 1. Weakest area: reading scans and layouts not seen before.** A scorecard that
runs generated layouts through the whole pipeline found valid invoices rejected because
a label was taken for a title, a stray OCR character mistaken for a currency, and a
contact line read as the vendor name. All fixed. Scans are now read twice and
reconciled, which took the scanned layouts used during development from 16 of 24
approved untouched to 24 of 24, and gave 64 of 72 on scans first run afterwards with no
misread approved.

**Round 2. Weakest area: situations around the happy path that a real AP desk meets.**
Item tables that run over several pages; a corrected copy of an invoice that is still
on hold; a foreign-currency invoice; a posting failure after an approval; a vendor note
that blamed the vendor for a field our reader had missed. Each now has a defined
behaviour and tests.
