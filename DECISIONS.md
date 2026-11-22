# DECISIONS.md

Every non-obvious choice: what was decided, why, and what was rejected. Numbers are
stable so other documents can point here (for example "D07").

## Assumptions

The brief leaves these open. Each is an assumption I made, written down so it can be
challenged in the interview.

- **A1. The buyer is a mid-size Indian manufacturer.** Invoices are GST tax invoices in
  English and in rupees. The same design works elsewhere; the tax rules would change.
- **A2. "Arrives by email" is represented by the upload box and the sample picker.** A
  mailbox connector is not built. Everything after the attachment is.
- **A3. The PO spreadsheet and the vendor list are CSV files in `data/`.** They stand in
  for the procurement system. Only two loader functions know they are files.
- **A4. One invoice bills one purchase order.**
- **A5. PO prices are before tax.** Invoices may print prices with or without GST.
- **A6. Goods receipts are a received quantity on the PO line, where the company keeps
  one.** Lines without it are matched two-way (invoice against PO).
- **A7. The thresholds in `rules.yaml` are example policy** (1% and Rs 500 on price,
  nothing over on quantity, Rs 10,00,000 auto-approval ceiling, a 15-day duplicate
  window). A real client sets its own; no code changes.
- **A8. A decision is approve for payment, hold for a named person, or reject.** Paying
  the vendor is outside the process.
- **A9. The company has history.** Ten invoices posted before this tool existed are
  loaded at start, so split POs and duplicates have something real to collide with.
- **A10. All data is synthetic.** Fictional buyer, vendors, GSTINs, banks and invoices.

## Shape of the solution

**D01. One Python app: Streamlit UI, SQLite history, no services.**
Why: it must run on a Windows laptop in a few commands, be hosted free at a public link,
and never break in a live demo. Fewer moving parts means fewer ways to fail.
Rejected: a low-code workflow (cannot be tested offline, depends on several SaaS
accounts being up); a fork of my `outcome` service (needs Docker, Postgres and a paid
model for every invoice); FastAPI with a hand-built front end (nicer pixels, but more
code to defend and a colder free-tier host).

**D02. A deterministic reader does the reading. No model call on the critical path.**
Why: the guide's hardest requirement is a live run that does not depend on luck. Labels,
page geometry and arithmetic read the invoices; the same input gives the same output.
Rejected: LLM-first extraction, which is more general but slower, costs a key, varies
between runs, and cannot be shown offline.

**D03. The LLM is a gap-filler, and nothing it says is trusted without proof.**
When the rules cannot read a required field or the line items, and a key is configured,
one call asks the model for the fields with a verbatim quote for each. A value is
accepted only if the quote is found on the page and the value is stated in the quote
(the check from my `outcome` repo). Accepted values never overwrite a rule-read value.
Responses are recorded so the same run can be replayed with no network.
Rejected: trusting model output; three provider SDKs (one OpenAI-compatible endpoint
behind three env vars covers OpenAI, Gemini, OpenRouter and local models).

**D04. The decision is a pure function of findings and `rules.yaml`.**
`decide()` has no I/O, no clock and no model. Stages produce findings (code, expected,
observed, evidence); the rules file maps each code to an action and an owner.
Why: explainable, testable on both sides of every threshold, and a process owner can
change a threshold without touching code. Pattern from my `controlplane` repo.
Rejected: letting a model decide; thresholds scattered through code.

**D05. Three verdicts, strictest wins.** REJECT beats HOLD beats APPROVE. When the
verdict is REJECT, hold-level findings are listed under "also noted".
Rejected: a single confidence score with cut-offs (hides the reason).

## Matching

**D06. Match on pre-tax values.** PO prices are pre-tax, so invoice lines are compared
before tax, and tax is validated separately (rate, type, arithmetic). Tax-inclusive
invoices are converted to pre-tax first and the conversion is shown.

**D07. Price tolerance needs both limits.** A line passes only if the unit price is
within the percentage limit and the extra rupees on the line are within the absolute
limit (defaults 1% and Rs 500). Paying less than the PO is a note, never a block.
Why: this is how SAP's price-variance tolerance behaves (RESEARCH.md section 3); a
percentage alone lets large lines drift by thousands, a rupee limit alone lets small
lines double.
Rejected: "either limit is enough".

**D08. Quantity may not exceed the PO; receipts are checked when the data exists.**
Billed-to-date plus this invoice must fit the ordered quantity. If the PO line carries
a received quantity, billed-to-date must also fit what was received (three-way match).
Rejected: a full goods-receipt module (out of scope for the brief's procurement system).

**D09. A ledger of posted invoices drives split-PO tracking.** Approved invoices post
and consume PO balance; held invoices are pending and do not consume it until a person
approves them. The running total is shown invoice by invoice.

**D10. An inferred PO is a suggestion, never a match.** With no PO number on the
invoice, the vendor's open POs are scored on line similarity, price and remaining
capacity. The best candidate is shown with its evidence and the invoice is held for
confirmation. Policy from my `ef-recon` repo: never force a match, escalate.
Rejected: auto-accepting above a score threshold. Posting against the wrong PO corrupts
that PO's balance for every later invoice.

**D11. Duplicates in three tiers.** Same file hash or same normalised invoice number
for the vendor: reject. Same vendor, amount and line items with a different number
inside the window: reject only if the PO has no room left for it, because then nothing
can be owed; otherwise hold and ask whether it is a second delivery. If receipts show
the goods arrived twice, it is not a duplicate.
Rejected: exact-match only (misses the expensive cases); rejecting on amount and date
alone (rejects legitimate repeat deliveries and monthly service bills).

## Reading and identity

**D12. GSTIN identifies the vendor; the name is a cross-check.** Names vary between
invoices; the GSTIN has a check character. Scanned GSTINs are repaired by position
(a letter where a digit must be, and the reverse) and then must pass the checksum.

**D13. A changed bank account is its own hold.** The invoice may be perfect; the
payment is still held until someone confirms the change by calling the number on file.
The review screen will not approve it without that confirmation, and a confirmed
account is remembered for later invoices.

**D14. Tesseract for scans, after straightening the page and cutting it into blocks.**
Local, free, works offline. The page is deskewed, split into blocks so that two header
columns on different baselines are not read as one line, and read in a single pass.
Recorded OCR output for the bundled scans is a labelled fallback when the engine is
not installed, so the demo and the hosted link still run.
Rejected: a cloud OCR API (network and cost on the critical path); heavier neural OCR
packages (large installs, system libraries on the host).

**D30. A scan is read twice, and the two reads are reconciled.** Once from the
greyscale picture and once after reducing it to black and white. The read whose
figures add up is used. An invoice number or date, which no arithmetic can confirm, is
trusted only when both reads agree; a disagreement holds the invoice.
Why: the end-to-end scorecard showed a single read garbling whole figures on noisy
typewriter-style scans, and showed that the two reads fail on different documents. On
72 held-out scanned layouts: 64 straight through, 3 false alarms, 5 misreads all held,
none approved (docs/extraction_scorecard.md has the current numbers).
Rejected: one read with a confidence cut-off (confidence was high on some wrong values);
three reads with a vote (slower, and two agreeing reads plus arithmetic was enough).
Cost: a scanned page takes about 3.5 seconds instead of 2.

**D15. Arithmetic is the cross-check on every read.** Quantity times rate must equal
the line amount, lines must sum to the subtotal, subtotal plus tax must equal the
total, and the amount in words must agree with the figures. Confidence comes from
these checks passing.

**D16. Missing stays missing.** A field that cannot be read is null with a reason, and
the invoice is held with a question. No default values, no nearest guess.

## Control and audit

**D17. One auto-approval ceiling.** A clean invoice above the ceiling (default
Rs 10,00,000) still goes to a person. It is the simplest honest version of a delegation
of authority. Rejected: a full approval matrix by department and cost centre.

**D18. Every hold names an owner and asks one specific question; vendor-facing issues
get a drafted note.** All text comes from templates filled with the evidence.
Rejected: model-written explanations (they must be exactly reproducible).

**D19. Hash-chained receipts.** Each decision is serialised canonically, hashed with
SHA-256, and includes the previous receipt's hash, the rules file hash and the master
data hash. Human review decisions join the same chain. Any later edit breaks it.
Rejected: an HMAC secret as in `controlplane` (a secret to manage adds a demo risk for
no gain here; the chain already makes tampering evident).

**D20. Post, then read back.** After the ledger write the record is read again and
compared field by field before the run is marked complete.

**D21. Intake is idempotent.** The same file a second time is a duplicate submission
that points at the original run. The UI marks samples already processed and offers a
reset to the seeded state.

## Data, demo and hosting

**D22. Prior history is seeded from CSV and labelled as history.** It gives the split
and duplicate cases something real to collide with, and a reset restores it exactly.

**D23. All test data is synthetic.** Fictional buyer, vendors, GSTINs and banks; every
PDF carries a footer saying so. One parametric generator draws six layouts (five
tabular, one written as a letter) plus multi-page tables; scans are made by
rasterising, tilting, blurring and adding noise.

**D24. A viewing pause between stages, off or on.** Stages finish in milliseconds, so
an optional short pause lets a viewer follow them. Reported timings are always real.

**D25. Real clock, with an override.** `INVOICE_DESK_TODAY` freezes "today" so date
rules cannot make a recorded demo go stale (idea from `controlplane`).

**D26. Streamlit Community Cloud first, Dockerfile as plan B.** The app also runs when
the host has no Tesseract, using recorded OCR for the bundled scans.

**D27. Money is Decimal, currency is INR.** Another currency is held for review.

**D28. Not-an-invoice is screened early.** Quotations, pro-formas, challans, credit
notes and unrelated PDFs get a clear message.

## Added during the build (each came out of a test or a review round)

**D31. The quote check for model output is exact.** A quote counts as "on the page"
only if every letter, digit and decimal point matches; spacing, punctuation and
thousands separators are ignored. A test with two digits swapped inside an otherwise
real quote got through the first, fuzzy version of this check. It does not now.

**D32. A failure late in the run can never leave an approval standing.** If posting
fails, or the entry read back from the ledger differs from what was written, or the
decision stage itself errors, the decision is recomputed with that failure in it and
the invoice ends on hold (`POSTING_FAILED`, `INTERNAL_ERROR`).

**D33. The document's title decides what it is.** "Purchase Order No." on an invoice
is a label, not a title. The scorecard caught 7 of 60 valid invoices being rejected as
"not a tax invoice" because of that label; the rule is now "a title stands alone".

**D34. A foreign-currency invoice is read, shown and stopped.** Its values appear in
its own currency, nothing is compared with rupee POs, nothing is booked, and its total
is kept out of rupee sums on the dashboard. A person closes it as handled by hand.

**D35. The same invoice number again is a duplicate only if the first one was booked.**
If the first is still on hold, a different file with the same number is most likely the
vendor's corrected copy. It is held, and it cannot be approved until the first is
decided. Rejecting a corrected invoice as a duplicate would be the expensive mistake.

**D36. A document the system did not book cannot be approved in the review queue.** It
can be marked as handled by hand, or rejected. Approval always means a ledger entry.

**D37. Item tables that run over pages.** Running heads, repeated column headings,
"continued" strips, page numbers and carried-forward amounts between pages are not
line items. A clean label-and-value pair beats the same label found in running text
(for example "Invoice No. X (continued), page 2 of 2").

**D38. Reading is measured end to end, on layouts the reader was not built on.** The
scorecard generates random layouts, adds a matching PO, and runs the whole pipeline,
so "correct" means approved untouched and "silent" means a misread that was approved.
Layouts seen during development and held-out layouts are reported separately.

**D39. Arithmetic may repair one value, never choose between two.** If quantity times
price does not equal the amount and exactly one of the three can be changed to make the
line and the invoice totals agree, it is corrected and noted. If more than one repair
would work, nothing is changed and the line stays flagged.

**D40. Every hold has an owner inside the company.** The vendor is never an owner; the
vendor gets the drafted note, and only when the problem is the vendor's. A document the
reader could not read does not produce a note telling the vendor a field is missing.

**D41. Anything from an uploaded file is untrusted.** Text is escaped before it reaches
HTML or markdown, files over 15 MB or 20 pages are held, and a page with absurd
dimensions is rendered at a capped size.

## Left out on purpose

**D29.** E-invoice IRN and QR validation, GSTR-2B matching, TDS, GL coding, approval
matrices, real mailbox intake, invoices spanning several POs, applying credit and debit
notes, foreign-currency processing, payment execution. Each is named in the README
with what it would take.
