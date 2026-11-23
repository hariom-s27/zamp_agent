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
When the rules cannot read a required field or the line items, the model is asked for
the fields with a verbatim quote for each. A value is accepted only if the quote is
found on the page and the value is stated in the quote (the check from my `outcome`
repo). Accepted values never overwrite a rule-read value.
*Revised in round 2:* the provider is now Claude through the Anthropic SDK, with three
explicit modes, stored answers and cost caps. D45 to D49 replace the round 1 details
(an OpenAI-compatible endpoint, and a mode called `auto` that went live as soon as a
key was present, which the audit rightly flagged).

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

**D11. Duplicates in three tiers.** Same file again: the first answer is returned and
nothing is booked twice (D50). Same normalised invoice number for the vendor: reject.
Same vendor, amount and line items with a different number inside the window: see D44.
Rejected: exact-match only (misses the expensive cases); rejecting on amount and date
alone (rejects legitimate repeat deliveries and monthly service bills).
*Revised in round 2:* tier 3 used to reject only when the PO had no room left, and hold
otherwise. The audit showed that sample E2 was rejected because its PO happened to be
full, not because of the match. D44 is the rule now.

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
*Revised in round 2:* an invoice number or date found in only one of the two reads is
no longer trusted either (D57). On the same 72 layouts that gives 62 straight through,
6 false alarms, 4 misreads held, none approved: two correct reads are now held, which
is the price of the stricter rule.

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
*Revised in round 2:* round 1 rejected a signing key as a demo risk for no gain. The
audit's point stands: someone who can rewrite the whole database can rebuild the chain.
Signing is now available and optional (D53), and the documents say what the chain
proves and what it does not.

**D20. Post, then read back.** After the ledger write the record is read again and
compared field by field before the run is marked complete.

**D21. Intake is idempotent.** *Replaced in round 2 by D50.* Round 1 treated the same
file a second time as a duplicate and rejected it, so the same file got APPROVE and
then REJECT. That is not what idempotent means.

## Data, demo and hosting

**D22. Prior history is seeded from CSV and labelled as history.** It gives the split
and duplicate cases something real to collide with, and a reset restores it exactly.

**D23. All test data is synthetic.** Fictional buyer, vendors, GSTINs and banks; every
PDF carries a footer saying so. One parametric generator draws six layouts (five
tabular, one written as a letter) plus multi-page tables; scans are made by
rasterising, tilting, blurring and adding noise.

**D24. A pace for the live view.** Stages finish in milliseconds, so the live view
holds each one on screen for a moment. Three paces (presentation, normal, fast) sit on
the pages that run invoices. Reported timings are always real.
*Revised in round 2:* it was a slider in the sidebar, which read as a dial to make the
software slower (walkthrough item 7).

**D25. The app processes on the demo date unless told otherwise.** The samples are
dated November 2026. The app, the command line for samples and the scripts use
21-Nov-2026 as the business date, so the dated checks give the same answer in a
rehearsal, in the interview and a year later. `INVOICE_DESK_TODAY` sets another date,
and a switch on the Guided demo page turns the pin off for real use.
*Revised in round 2:* round 1 used the real clock by default and offered the pin as an
option, which made a working demo depend on remembering to set it.

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
*Extended in round 2 by D51:* the audit found the one path this missed, a failure after
the ledger write, which left a POSTED entry under a HOLD.

**D33. The document's title decides what it is.** "Purchase Order No." on an invoice
is a label, not a title. The scorecard caught 7 of 60 valid invoices being rejected as
"not a tax invoice" because of that label; the rule is now "a title stands alone".

**D34. A foreign-currency invoice is read, shown and stopped.** Its values appear in
its own currency, nothing is compared with rupee POs, nothing is booked, and its total
is kept out of rupee sums on the dashboard. A person closes it as processed manually.

**D35. The same invoice number again is a duplicate only if the first one was booked.**
If the first is still on hold, a different file with the same number is most likely the
vendor's corrected copy. It is held, and it cannot be approved until the first is
decided. Rejecting a corrected invoice as a duplicate would be the expensive mistake.

**D36. A document the system did not book cannot be approved in the review queue.** It
can be closed as processed manually, or rejected. Approval always means a ledger entry.

**D37. Item tables that run over pages.** Running heads, repeated column headings,
"continued" strips, page numbers and carried-forward amounts between pages are not
line items. A clean label-and-value pair beats the same label found in running text
(for example "Invoice No. X (continued), page 2 of 2").

**D38. Reading is measured end to end, on layouts the reader was not built on.** The
scorecard generates random layouts, adds a matching PO, and runs the whole pipeline,
so "correct" means approved untouched and "silent" means a misread that was approved.
Layouts seen during development and held-out layouts are reported separately.
*Qualified in round 2:* those "held-out" layouts come from the same generator as the
samples, so they share its fonts and its idea of what an invoice looks like. The audit
called this the biggest credibility gap. D56 is the answer.

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

## Round 2: what the audit, the walkthrough and an independent test set changed

An independent audit (docs/AUDIT_REPORT.md) and a browser walkthrough
(docs/UI_WALKTHROUGH.md) were run on round 1. Every decision below answers something
they found, or something the independent test set showed. CHANGELOG.md lists the code
and test changes; this is the reasoning.

### Controls that hold when a person acts

**D42. The purchase order is checked again at the moment of approval.** A held invoice
does not use PO balance. Two held invoices can each fit the PO on their own and overbill
it together, and the audit approved 24 units against a PO of 20 that way. An approval
from the review queue now recomputes what is posted on the PO, with the same tolerances
as the PO match stage. If it would overbill, the approval is refused, the hold on file
is rewritten with today's numbers, and the attempt is written to the audit trail.
There is no override. The way through is a PO amendment or a rejection.
Rejected: reserving PO balance for held invoices (a hold that never clears would block a
good invoice behind it); an "approve anyway" tick box (it turns the control into a habit).

**D43. A changed bank account takes two different people.** The screen always said so
and the code did not enforce it. Now the first person confirms a call-back to the number
in the vendor master, records the call, and may attach the vendor's letter. The invoice
stays on hold and the new account is not trusted. A second person, with a different
name, reads the call record and approves. Only then is the invoice payable and the
account remembered. The number of approvers is `review.bank_change_approvers`.
What this is not: identity. Reviewer names are typed, because the demo has no login. In
a deployment the name comes from the company's single sign-on, and "different person"
becomes a comparison of accounts, which is the same check on a better input.
Sources for the control: RESEARCH.md section 11.

**D44. The same invoice under a new number is rejected on the match itself.** Same
vendor, same lines, same total, inside the window, different number: rejected, whether
or not the PO has room. The only thing that clears it is evidence of a second delivery:
goods receipts that cover both. If the lines could not be compared, it is a question for
a person (`POSSIBLE_DUPLICATE`), not a rejection.
Why: "the PO still has money on it" is not evidence that goods came twice. Three-way
matching is what stops the second payment (RESEARCH.md section 12).
A client that prefers to hold changes one word in `rules.yaml` (`DUPLICATE_CONTENT`).

**D50. The same file again gets the same answer.** A file whose fingerprint is already
on record is not read or decided a second time. The earlier record's answer is returned
as it stands now (approved, on hold, rejected), a re-submission entry joins the audit
trail, and nothing is booked or queued twice. The file is read afresh only when nothing
is booked for it and either the first run ended on something transient (an internal
error, a posting failure, a scan with no OCR engine) or the rules, the master data, the
business date or the AI set-up have changed since.
Rejected: rejecting the second copy as a duplicate (round 1: the same input then gives
APPROVE and REJECT, and a presenter who clicks twice gets a red banner); always
re-reading (a re-read under a newer rule could contradict a payment already made).

**D51. Posting is all or nothing.** The ledger entry, the audit receipt and the run
record are written in one database transaction. If any of them fails, all are rolled
back and a second attempt records a hold with the reason. If nothing can be written at
all, the run still ends as a hold and says that it could not be recorded.
Before: a failure while saving the receipt left a POSTED ledger entry under a HOLD.

**D53. Receipts can be signed, and the documents say what the chain proves.** With
`RECEIPT_SIGNING_KEY` set, every entry also carries an HMAC made with a key the database
does not hold. Without it, the chain shows any edit to a past entry, but someone able to
rewrite the whole database could rebuild it. The Dashboard says so, and shows the latest
fingerprint, which pins the whole history when it is noted somewhere this system cannot
write. Rejected: making the key mandatory (a missing key would break a demo).

### Determinism and rules

**D55. Every threshold is in `rules.yaml`, and none has a default in code.** The audit
found name-matching cut-offs, line-pairing cut-offs, PO inference weights, scan
agreement levels and file limits in Python. They are in the rules file now, with a
comment each, and a missing or misspelt key stops the app with a message naming it.
Rejected: defaults in code "for safety" (a typo in the file would then run on a number
nobody chose).

**D52. Records are numbered like vouchers: AP-2026-0001.** "R-0001" read as the id of a
batch job (walkthrough item 2).

**D54. A blank box and an unreadable box are different findings.** On a scan, an empty
value beside "Invoice No." is either left blank by the vendor or something OCR could not
read. The reader measures the ink in that spot on the picture. Only a proven blank
produces a message asking the vendor for the number.

**D61. The tolerance samples now sit at the tolerance.** Round 1's pair was 0.11% and
1.56% against a 1% limit, and the second also tripped the PO balance. The pair is now
0.95% and Rs 488 on the line (approved, with a note) against 1.01% and Rs 515 (held, for
the price alone). Sample E2's PO was given room, so the rejection is for the match and
not for a full PO. Samples 07, 08 and one PO line changed; every other sample file is
byte for byte what it was. The audit proved the old ones wrong; CHANGELOG.md documents it.

### The AI layer (Claude)

**D45. The model copies; code parses.** The request asks for a strict JSON structure
(the API's structured output) in which every value is text copied from the document,
with the sentence it was copied from. The model is not asked to convert a date, add up
a total or judge anything. Parsing dates and amounts is done by the same code that
parses what the rules reader finds. Why: a model that copies can be checked character
by character; a model that calculates cannot.

**D46. Every value is verified against the page, exactly.** Three checks by code: the
quote is on the page (letters and digits must match one for one), the value is inside
the quote as a whole token ("INV-10" does not pass because "INV-100" is on the page),
and an invoice or PO number must sit beside words that say what it is. Then the
invoice's own arithmetic must agree. Trust levels are numbers in `rules.yaml`: verified
0.85, quote only 0.75, text that itself came from OCR 0.60, model said it was unsure
0.50. The line for approval is 0.70, so a value read by a model from a scan always goes
to a person, and the model's own confidence can lower trust and never raise it.
A value the rules reader already has is never overwritten.

**D47. One file, one prompt version, one model: one answer, kept.** An answer is stored
under the file's fingerprint, the prompt version and the model name. The repository's
recordings (`fixtures/llm/`) are replayed in FIXTURE mode with no network; this
machine's cache is filled in CACHED-LIVE mode on the first call and reused after. A
stored answer is never overwritten in passing, and a recording whose prompt text no
longer matches is ignored. So the same file gives the same answer with or without a key.
No recording is written by hand: `scripts/record_llm_fixtures.py` makes them from real
responses, and a test fails if a file in `fixtures/llm/` did not come from the API.

**D48. Cost and failure are bounded.** No sampling settings and no effort level are
sent by default, so the model runs as its documentation describes it; `LLM_EFFORT` is
there for someone who has measured that another level is better. A timeout and a retry count per call; a cap per
invoice, checked against a worst-case estimate before the call is made; a cap per day,
kept in a small ledger that every visitor's sandbox shares. A refusal, a truncated
answer, broken JSON, a bad key, a rate limit, a server error or no network all end the
same way: the model's part is skipped, the reason is shown, and the rules decide alone.
A run never fails because the API did.

**D49. Three modes, and the safe one is the default.** FIXTURE (recorded answers, no
network, no key), CACHED-LIVE (call on a cache miss, then keep the answer), OFF. A key
on its own changes nothing: live calls need `LLM_MODE=cached-live` or the choice on
screen, and on a public demo they also need `LLM_ALLOW_LIVE=1`. The mode in force is in
the sidebar on every page.
Where the model is used, and where it is not:
- **Used: reading what rules cannot.** An invoice written as a letter has no table and
  no labels. This is the one place a model does something rules do badly.
- **Used: rewording the message to a vendor.** The rules draft it from the findings. On
  request the model rewrites it in a friendlier tone. Code then checks that it added no
  number, date or reference and kept the invoice number, the PO and the total. If not,
  the draft stands. Low stakes, real use, and a person sends it either way.
- **Rejected: the model decides, or advises the decision.** The decision must be the
  same tomorrow and explainable from a rules file.
- **Rejected: a model-written summary of the decision.** It would be a paraphrase of a
  text that is already plain and exact, with a chance of being wrong.
- **Rejected: the model's confidence as a reason to approve.** It may only lower trust.
- **Rejected: model-based duplicate detection.** Vendor, amount, lines and dates are
  comparisons, and comparisons are exact in code.
- **Rejected: chat with your invoices.** It demos well and controls nothing.

**D63. A live call can be shown on purpose, and it cannot break the demo.** Once an
answer is stored, CACHED-LIVE reuses it, so a rehearsed demo makes no call at all. To
show one, "Ask Claude afresh on every run" (Guided demo, Demo settings) sends the
document again on each run, inside the same caps. If that call fails for any reason, the
stored answer is used and the record says a fresh answer was asked for and why it was
not had. The repository's recordings are never written from the screen; a fresh answer
goes to this machine's cache only.
Rejected: deleting the stored answer to force a call (the fallback would be gone exactly
when the network fails in front of an audience); a separate "live demo" code path (it
would not be the process being shown).

**D64. What Claude read is outlined on the page, and code finds the place.** Every
value on screen has a box where it was read. A value from the model gets one too, in its
own colour: the value's own words inside the quote that was verified. The position comes
from the words the reader extracted, never from the model, so a box cannot be drawn
around something that is not there. Field names in these messages are the ones a reader
uses ("invoice number", "line items"), not the code's.

### Reading invoices the samples' generator did not make

**D56. A second test set, made with other tools.** 49 documents built with headless
Chrome, wkhtmltopdf, LibreOffice Calc and Writer, and LaTeX, in the layouts Indian
accounting packages print, plus 12 photographs of printed pages (robustness/). Each has
its ground truth and the outcome that is right for it; 15 should be stopped, for eleven
different reasons. Two splits: what failed on the tune split was used to improve the
reader; the holdout split uses other tools and families and was never opened. Four
classes are counted, and the one that matters is "misread, approved".
Before any change: 13 of 49 right, 36 held without need or for the wrong reason, none
approved wrongly. After: 45 of 49 right, 4 held (all photographs), none approved
wrongly. Holdout text PDFs went from 6 of 12 to 12 of 12 without being looked at.
The report (docs/robustness_report.md) is generated, and a test pins every number in it.
What it is not: real invoices. The limits are in the report.

**D57. Photographs are straightened with numpy and Pillow, and flatbed scans are left
alone.** For a photographed page: crop to the picture, find the sheet on its background,
undo the perspective, divide out uneven light, turn a page that lies on its side, whiten
ruled lines, invert white-on-black headings. Then two reads that differ in layout
analysis and in size, so they share fewer mistakes. A flatbed scan gets none of this and
reads exactly as in round 1, which the older scorecard confirms.
Stricter than before: a number or date found in only one of the two reads is not trusted.
Rejected: OpenCV in the app (a large dependency on the free host for what two hundred
lines of numpy do); a cloud document API (network, cost and a third party on the
critical path); a vision model reading the page (it would make the model the reader).

**D58. Arithmetic picks the total, and a scan's total needs two confirmations.** When a
page prints several totals, the one that equals the lines plus the taxes is the invoice
total, whatever its label. A total under a label the reader does not know is accepted
only on that arithmetic. A known label that does not add up is reported as printed, so a
vendor's mistake stays visible. On a scan whose total figure cannot be used as read
(written by hand, or misread), the total is supplied only when the lines plus the taxes
and the amount in words agree to the paisa.

**D59. Two invoice lines may bill one PO line.** One item delivered twice is billed on
two lines against one PO line. The second line may share the PO line only on a
near-exact wording match (`matching.line_sharing_min`), and the quantities are added up
before the PO check, in the pipeline and again at approval.

**D62. Text the layout library cannot decode is read again, or not trusted.** Chrome
embeds some web-font glyphs with no usable character map, and the hyphen of
"INV-000482" arrived as an unprintable code point. The PDF engine that renders the page
decodes it correctly, so such a word is read again from the engine. The second reading
is accepted only if it keeps every character the first one did decode. A word that still
cannot be decoded is marked and never trusted.

### Demo and hosting

**D60. A guided demo, a pinned date, and private books per visitor.** The Guided demo
page runs eleven scenarios, one per click, through the real pipeline with the live view,
and compares each decision with the expected one after the fact. Nothing in it is
staged. `scripts/preflight.py` rehearses the same eleven steps and opens every page
without a browser, so "is it ready" is one command on the machine that will be used.
On a public link (`INVOICE_DESK_SANDBOX=session`) every browser session has its own
database, so a visitor cannot reset or fill the books during someone else's demo, and
live AI needs an explicit switch.
The reset moved from the sidebar to the demo controls (walkthrough item 6).

## Round 3: after the hold, value and evidence

**D65. A hold opens a case.** Cases are derived by a reconciler (`cases.sync`) from held
runs; the pipeline does not write them, so the most tested code is untouched.
Rejected: opening the case inside the posting transaction.

**D66. Release by rule, narrowly.** The desk may release a held invoice by itself only
when every reason on the hold is in `cases.auto_resume` and the fact is now on record,
after the same purchase order re-check a person's approval gets. It is recorded as
RELEASED, never APPROVED. Rejected: re-running the whole pipeline on the stored file;
a person-free release for any reason.

**D67. Nothing leaves without a named person.** A drafted vendor message is approved by
name and then written as an email to an outbox and recorded. Rejected: sending by SMTP;
letting the approver rewrite the text (approve it or do not send it).

**D68. Each owner has a reply time and an escalation target in the rules.** Escalation is
by the clock, once per party the case waits on, to a named role. Rejected: one queue
ordered by arrival.

**D69. The statutory clock outranks the reply time.** For micro and small suppliers the
MSMED Act pay-by date makes a case urgent near the date. With no delivery date on record
it is counted from the invoice date and says so. Rejected: hiding the clock when the date
is unknown; flagging the existing vendors.

**D70. Case bookkeeping stays out of the audit chain.** Opening, closing and escalating
write to the case history only. A goods receipt, a release and an approved message go
into the chain. Rejected: every case event in the chain (it would change the receipt
counts of the first eleven steps).

**D71. The business case separates what is measured from what is assumed.** Measured
figures come from the store; everything else is an assumption in `rules.yaml`, shown
with its source and changeable on screen. Chasing time is left unchanged in the "after"
figure. Rejected: one headline saving; benchmark dollars converted to rupees.

**D72. Percentages come with sample size and a Wilson 95% interval.** The headline is
wrongly approved, not accuracy. Rejected: reporting 0 of 49 as "zero errors".

**D73. New scenarios live in a second manifest and a second demo chapter.** The 18
samples and the 11 steps are frozen. Rejected: appending to the existing lists.

## Left out on purpose

**D29.** E-invoice IRN and QR validation, GSTR-2B matching, TDS, GL coding, approval
matrices, real mailbox intake, invoices spanning several POs, applying credit and debit
notes, foreign-currency processing, payment execution. Each is named in the README
with what it would take.
