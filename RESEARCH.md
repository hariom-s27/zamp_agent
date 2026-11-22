# RESEARCH.md

Research done before design (21 Nov 2026). Every external claim has a link. Where a
source is a vendor's own marketing or a secondary report of someone else's survey, that
is said. Items marked *(background)* are general knowledge that was not re-checked online.

## 1. What the guide asks for

From the candidate guide (PS-1, "Invoice processing, from PDF to decision"):

- A process that takes an invoice PDF and returns a clear, reasoned decision, with
  everything in between visible. It must really execute; a mock-up does not count.
- The UI is graded: a live run view (each stage as it executes) and a dashboard
  (history, status and outputs across runs).
- 2 to 4 non-trivial edge cases of the candidate's own design.
- A 5-minute video, then a live demo in the interview, with no slides.
- Any tools or models, except Zamp or Pace products. Self-made test data.
- The messiness the brief names: different vendor formats, clean text PDFs and scans,
  itemised or bundled lines, tax embedded or separate, PO reference explicit or implied,
  missing fields, split POs, amounts close but not exact, approved vendors, tolerances,
  duplicate rules.

## 2. What Zamp builds and values

Sources: the Zamp home page (https://www.zamp.ai/) and a Zamp blog post on invoice
processing that search returned from a Zamp web deployment
(https://zamp-website.vercel.app/blogs/ai-agents-automate-invoice-processing). Both are
Zamp's own description of itself.

- Zamp sells "digital employees" that run a process end to end, inside the customer's
  guardrails, with people brought in only where judgment is needed. It stresses complete
  audit trails and learning from feedback given in plain English.
- Finance and procurement are core: AP approvals, invoice processing, accounting close,
  purchase requisitions. The home page quotes Instacart (AP approval time from two weeks
  to two days) and Mindbody (most invoice processing handled end to end).
- The blog post describes the invoice lifecycle in ten steps: receive and register
  (including a duplicate check), extract while keeping every field traceable to its
  source with field-level confidence, validate the supplier against master data with
  bank-detail changes on a separate approval path, recompute totals and tax with
  deterministic checks, find the PO and receipt, apply approved tolerances and turn
  mismatches into categorised exceptions, investigate and route exceptions to a named
  owner with a specific question, route approval by a matrix, post and then read the
  record back, archive the evidence including human corrections.
- Other points from the same post: duplicates are checked on sender, file, supplier,
  invoice number, amount, date and prior submissions; overriding a duplicate flag needs
  stronger controls; idempotency is a control; for non-PO invoices the agent may propose
  but should not infer facts without evidence; process owners change thresholds without
  developers; the audit trail holds the original invoice, extracted values, source
  records, rules applied, match details, exception actions, approvals and human
  corrections.

What this means for the build:

| Zamp's stated practice | What this project does |
|---|---|
| Fields traceable to source, with confidence | Every field carries page, snippet, box on the page, method and confidence |
| Deterministic checks for arithmetic | Decision path is code and rules; the LLM can only read |
| Bank changes on a separate approval path | Bank change is its own hold, with a call-back control at review |
| Tolerances, categorised exceptions | `rules.yaml` tolerances; each exception has a code, expected and observed values |
| Hand-off to a named owner with a specific question | Each hold names the owner role (and PO buyer) and states the question |
| Human in the loop on exceptions only | Clean invoices post untouched; holds go to a review queue |
| Post, then read back | The ledger write is read back and compared before the run is closed |
| Audit trail with actor and time | Hash-chained receipts for machine decisions and human reviews |
| Thresholds editable by process owners | All thresholds and the decision table live in `rules.yaml` |
| Learns from feedback | A bank account confirmed on review is remembered for that vendor's later invoices |

## 3. How AP teams process invoices

- **Matching.** Two-way match compares invoice to PO; three-way adds the goods receipt.
  *(background)* Zamp's post treats receipt or service-entry records as part of matching.
- **Tolerances.** SAP's invoice verification uses tolerance keys with both an absolute
  and a percentage limit, upper and lower; exceeding an upper limit blocks the invoice,
  while for price variance the lower limit only warns. The price check compares each
  item against invoiced quantity times order price.
  Sources: https://help.sap.com/doc/ea71b6531de6b64ce10000000a174cb4/1610%20002/en-US/8770b6531de6b64ce10000000a174cb4.html ,
  https://blogs.sap.com/2015/12/04/invoice-tolerance-keys-an-insight-part-2/ ,
  https://userapps.support.sap.com/sap/support/knowledge/en/3052672 and
  https://userapps.support.sap.com/sap/support/knowledge/en/3744703 .
  This project copies that shape: a line passes only if it is inside both the percentage
  and the rupee limit, and under-billing is a note, never a block.
- **Benchmarks.** Ardent Partners' 2026 metrics report, as summarised by its sponsor
  Medius, puts the average cost per invoice at $9.90 against $2.67 for the best group,
  and the average exception rate near one in five
  (https://www.medius.com/resources/guides-reports/ardent-partners-accounts-payable-metrics-that-matter/).
  Secondary write-ups of the 2025 edition give 3.1 days cycle time for the best group
  and about 9% exceptions against 22%
  (https://www.wexinc.com/resources/blog/what-does-it-cost-to-process-an-invoice-ap-benchmarks-every-finance-team-should-know/).
  The dashboard therefore reports the straight-through rate, holds, rejections and time per invoice.
- **Controls around AI.** Vendor write-ups (Tipalti, Stampli) agree on one point: the
  audit trail must separate what the machine did from what a person changed, and the
  human check has to be a real control rather than a click-through
  (https://www.stampli.com/resources/human-in-loop-ai/ ,
  https://tipalti.com/blog/5-principles-for-building-trusted-agentic-ai-into-ap/).

## 4. Duplicate invoices

- SAP's standard duplicate check keys on company code, vendor, currency, invoice date
  and the reference number; amount is not always in the key
  (https://userapps.support.sap.com/sap/support/knowledge/en/3774533 ,
  https://blogs.sap.com/2013/01/28/duplicate-invoice-check-part-2/).
- Exact-field checks miss the expensive cases: a re-keyed or re-formatted invoice
  number, a second intake channel, or a duplicated vendor record. Fuzzy checks normalise
  fields first (https://www.corpay.com/resources/blog/duplicate-payment-detection ,
  a vendor blog).
- SAP's fraud tooling has a separate method for same vendor, same date, same reference
  and similar amounts
  (https://help.sap.com/saphelp_fra110/helpdata/en/c5/107d5307e86655e10000000a423f68/content.htm).

Design consequence: three tiers. Same file or same normalised number is certain. Same
content with a different number is certain only when the PO has no room left for it;
otherwise it is a question for a person.

## 5. Fraud: changed bank details

- Invoice redirection is the AP form of business email compromise: a real-looking
  invoice or thread carries new bank details. The control is to verify every change by
  calling a number already on file, never one from the message, with a second person
  approving and the call written down
  (https://www.bitdefender.com/en-au/blog/hotforsecurity/vendor-bank-detail-verification-template ,
  https://www.coupa.com/blog/3-controls-stop-payment-fraud/).
- Scale, from secondary sources: Coupa's blog cites 24,768 reported incidents in 2025
  at an average above $122,000; Corpay's blog cites the AFP 2026 survey for 74% of
  organisations affected in 2025
  (https://www.corpay.com/resources/blog/business-email-compromise-ap).

## 6. Indian GST invoice specifics

- **Mandatory particulars.** Rule 46 of the CGST Rules lists what a tax invoice must
  carry, including supplier name, address and GSTIN, a consecutive serial number of at
  most sixteen characters (letters, digits, hyphen, slash) unique for the financial
  year, date, recipient details, HSN, description, quantity, value, tax rate and amount
  (https://taxinformation.cbic.gov.in/content/html/tax_repository/gst/rules/cgst_rules/active/chapter6/rule46_v1.00.html).
  So an invoice with no number is not a valid tax invoice, and the number is the natural
  duplicate key.
- **GSTIN.** Fifteen characters: two-digit state code, ten-character PAN, an entity
  character, the letter Z, and a check character
  (https://tallysolutions.com/glossary/gstin/). The check character lets a misread
  GSTIN be detected without any lookup.
- **Which tax.** Supplier state different from place of supply means IGST; same state
  means CGST plus SGST (https://taxguru.in/goods-and-service-tax/india-gst-registration-number-format-read.html).
- **Rates.** Since 22 September 2025 the main slabs are 5% and 18%, with 40% for
  demerit goods and nil for essentials; 12% and 28% were removed
  (https://cleartax.in/s/56th-gst-council-meet-news ,
  https://tallysolutions.com/gst/new-gst-rate-structure/). The rate check is date-aware.
- **E-invoicing.** Mandatory above Rs 5 crore aggregate turnover, with an IRN and QR
  code (https://tallysolutions.com/business-guides/e-invoicing-compliance-rules-in-2026/).
- **Input tax credit** needs a valid tax invoice in hand
  (https://taxguru.in/?p=1084807, a commentary on section 16(2)(a)).

Not built, and said so in the README: IRN or QR validation, GSTR-2B matching, TDS,
MSME payment deadlines.

## 7. What the three earlier repos offered

Read from https://github.com/hariom-s27 (cloned to a scratch folder, nothing copied
wholesale, and no file from another company's brief, contract or samples).

| Repo | Idea taken | Not taken |
|---|---|---|
| `outcome` | Page triage (text layer or scan) from `app/readers/pdf_reader.py`; "is the quoted text really on the page" from `app/extract/verify.py`; null instead of a guess; one OpenAI-compatible endpoint behind two env vars | Postgres queue, workers, Docker, the table-recipe engine |
| `controlplane` | `decide()` as a pure function; reasons with expected and observed; rules as data; a receipt for every decision (hash-chained here, not signed); a frozen demo clock; a pending-review queue | The claim and resolver engine, Zen graphs, benchmarks |
| `ef-recon` | Pipeline shape (ingest, extract, normalise, match, dedup, compute); "never force a match, escalate"; fingerprint dedup; a seeded generator of messy test data; Decimal for money; a measured scorecard | Emission-factor logic, Ollama, plotly |

## 8. What an AP manager and a Zamp interviewer would care about most

1. Does it ever pay something it should not? (duplicates, over-billing, bank fraud)
2. When it stops, does it say exactly why and who must do what?
3. Can every number be traced to the page it came from?
4. Will it run in front of me, including on a scan, and can I see each step?
5. Can I change a threshold without a developer?
6. Is the AI used where it helps, and fenced where it could hurt?

These six became the acceptance questions in PLAN.md.

## 9. Limits of this research

- Zamp's practices come from its own marketing pages, not from seeing the product.
- Fraud and benchmark figures are quoted through secondary pages; the primary reports
  (AFP, Ardent, FBI) were not opened.
- GST rate examples were not checked item by item against the rate schedule; all sample
  invoices use 18%, the standard rate, to stay on safe ground.
