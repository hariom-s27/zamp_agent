# Invoice Desk (PS-1) — UI Walkthrough & AP Usability Audit

**Date:** 22 November 2026  
**Auditor:** AP Operations & Solutions Review  
**Environment:** Windows 11, PowerShell, Python 3.11.16, Tesseract OCR 5.4.0  
**Test Suite Summary:** `364 passed in 121.29s (0:02:01)`  
**App URL:** `http://localhost:8501`  

---

## 1. Executive Summary & Setup Verification

The system was verified against steps 1–5 of [SETUP_WINDOWS.md](SETUP_WINDOWS.md) strictly within PowerShell:
1. **Python Environment:** Verified running Python 3.11.16 on Windows x64.
2. **Virtual Environment & Dependencies:** Installed clean via `.venv\Scripts\python -m pip install -r requirements.txt`.
3. **Preflight Verification:** Executed `.venv\Scripts\python -m scripts.preflight`. All environment packages, rules, master data (7 vendors, 12 purchase orders), and all 14 sample files verified with `READY`.
4. **Full Test Suite:** Executed full test run via `python -m pytest -q`.
   - **Verbatim Summary Line:**
     ```text
     364 passed in 121.29s (0:02:01)
     ```
   *(Note: 364 of 364 passed, confirming full test execution with OCR test coverage active).*
5. **Application Lifecycle:** Started via `.venv\Scripts\streamlit run app.py` running at `http://localhost:8501`.
6. **Execution Scope:** No Python logic in `invoice_desk/` or `rules.yaml` was modified. All walkthrough observations are documented below without applying prospective UI fixes.

---

## 2. Navigational Walkthrough Across All Five Pages

The application provides five distinct views accessible via the sidebar navigation:

| Page | URL Path | Business Function | Initial Visual State |
| :--- | :--- | :--- | :--- |
| **Process invoice** | `/` | Ingests PDF invoices (bundled or uploaded), triggers the 9-stage pipeline, displays live progress, and renders verdict banners. | ![Process Initial](docs/screenshots/walkthrough/00_page_process_initial.png) |
| **Dashboard** | `/dashboard` | Operational analytics showing total processed, Straight-Through Processing (STP) rates, holds, rejections, processing latencies, and reasons distribution. | ![Dashboard Initial](docs/screenshots/walkthrough/00_page_dashboard_initial.png) |
| **Review queue** | `/review` | Human-in-the-loop exception workbench for held invoices requiring dual-control verification (e.g. bank change confirmation, manual PO reconciliation). | ![Review Initial](docs/screenshots/walkthrough/00_page_review_initial.png) |
| **Rules and data** | `/rules` | Reference directory listing active validation rules, policy tolerance thresholds, vendor master records, and open purchase orders. | ![Rules](docs/screenshots/walkthrough/00_page_rules.png) |
| **How it works** | `/guide` | High-level system architecture documentation, stage descriptions, and control model explanations. | ![Guide](docs/screenshots/walkthrough/00_page_guide.png) |

---

## 3. Sample-by-Sample Processing Results (All 14 Bundled Cases)

Every sample in `samples/` was processed individually through the live UI pipeline. The table below records the execution results:

| # | Sample Title & File | Documented Outcome | Actual Verdict | Reason(s) Shown on UI | All 9 Stages Live? | Screenshot |
| :-: | :--- | :---: | :---: | :--- | :-: | :--- |
| **01** | **Happy path**<br>`01_happy_path_furniture.pdf` | `APPROVE` | **`APPROVE`** | Invoice `TOF/26-27/0412` dated 05-Nov-2026 matches `PO-2026-0147`: 3 of 3 lines agree on item, quantity, and price. PO is now fully billed (`₹3,81,300.00`). Due 05-Dec-2026. | **Yes** (9/9) | ![Sample 01](docs/screenshots/walkthrough/sample_01_01_happy_path_furniture.png) |
| **02** | **Scanned invoice, clean**<br>`02_scanned_clean_safety.pdf` | `APPROVE` | **`APPROVE`** | Invoice `ASS/2026/0941` dated 14-Nov-2026 matches `PO-2026-0201`: 2 of 2 lines agree. Read live via Tesseract OCR; arithmetic balanced. Due 14-Dec-2026. | **Yes** (9/9) | ![Sample 02](docs/screenshots/walkthrough/sample_02_02_scanned_clean_safety.png) |
| **03** | **Monthly service instalment**<br>`03_monthly_service_instalment.pdf` | `APPROVE` | **`APPROVE`** | Invoice `VSF/26-27/11` dated 15-Nov-2026 matches `PO-2026-0160`: 1 line matches. Partial billing: `₹4,80,000.00` billed of `₹7,20,000.00`; `₹2,40,000.00` open. Due 15-Dec-2026. | **Yes** (9/9) | ![Sample 03](docs/screenshots/walkthrough/sample_03_03_monthly_service_instalment.png) |
| **04** | **E1 Split PO, third invoice over-bills**<br>`04_E1_split_po_overbilling.pdf` | `HOLD` | **`HOLD`** | (1) Line 1: 21 of 20 units would be billed (16 previously + 5 now). (2) PO balance exceeded: `₹5,25,000.00` vs `₹5,00,000.00` PO limit (`₹25,000.00` over tolerance). Routed to Procurement. | **Yes** (9/9) | ![Sample 04](docs/screenshots/walkthrough/sample_04_04_E1_split_po_overbilling.png) |
| **05** | **E2 Disguised duplicate**<br>`05_E2_disguised_duplicate.pdf` | `REJECT` | **`REJECT`** | Duplicate content: Matches posted invoice `DQI/26-27/1187` dated 03-Nov-2026 (same vendor, same items, identical total `₹6,39,088.00`, 2 days apart under new invoice # `DQI/26-27/1193`). | **Yes** (9/9) | ![Sample 05](docs/screenshots/walkthrough/sample_05_05_E2_disguised_duplicate.png) |
| **06** | **E3 Scan, no invoice number, PO implied**<br>`06_E3_scan_no_number_implied_po.pdf` | `HOLD` | **`HOLD`** | (1) Invoice number left blank on document. (2) No PO quoted; inferred best-fit PO is `PO-2026-0212` (98% match). Human confirmation required before posting. | **Yes** (9/9) | ![Sample 06](docs/screenshots/walkthrough/sample_06_06_E3_scan_no_number_implied_po.png) |
| **07** | **E4 Tax-inclusive prices, inside tolerance**<br>`07_E4a_tax_inclusive_within_tolerance.pdf` | `APPROVE` | **`APPROVE`** | Prices include 18% GST; converted to pre-tax (`₹3,780 / 1.18 = ₹3,203.39`). Line 1 is `₹3.39` over PO (`0.11%`), which is within the 1% / ₹500 line tolerance. Approved. | **Yes** (9/9) | ![Sample 07](docs/screenshots/walkthrough/sample_07_07_E4a_tax_inclusive_within_tolerance.png) |
| **08** | **E4 Tax-inclusive prices, outside tolerance**<br>`08_E4b_tax_inclusive_over_tolerance.pdf` | `HOLD` | **`HOLD`** | (1) Line 1 price `₹3,250.00` vs PO `₹3,200.00` is `₹50.00` over (`1.56%`, exceeds 1% limit). (2) Over-bills PO by `₹1,500.00`. Routed to Procurement. | **Yes** (9/9) | ![Sample 08](docs/screenshots/walkthrough/sample_08_08_E4b_tax_inclusive_over_tolerance.png) |
| **09** | **Fraud guard: bank account changed**<br>`09_fraud_guard_bank_change.pdf` | `HOLD` | **`HOLD`** | Bank mismatch: Invoice specifies account `770031245566` (IFSC `CHUB0002290`), but master record has `004512309876` (IFSC `HMBL0000451`). Potential redirection fraud. | **Yes** (9/9) | ![Sample 09](docs/screenshots/walkthrough/sample_09_09_fraud_guard_bank_change.png) |
| **10** | **AI assist: invoice written as a letter**<br>`10_ai_assist_letter_invoice.pdf` | `HOLD` | **`HOLD`** | Unstructured letter: (1) Missing invoice number. (2) Missing total beside total label. (3) No recognized item table. Replay mode holding for AP review. | **Yes** (9/9) | ![Sample 10](docs/screenshots/walkthrough/sample_10_10_ai_assist_letter_invoice.png) |
| **11** | **Quotation sent to the invoice inbox**<br>`bad_inputs/quotation_not_invoice.pdf` | `REJECT` | **`REJECT`** | Document title is explicitly `QUOTATION`. Not a tax invoice; no payable liability. Not booked. | **Yes** (9/9) | ![Sample 11](docs/screenshots/walkthrough/sample_11_quotation_not_invoice.png) |
| **12** | **Unrelated PDF**<br>`bad_inputs/newsletter_not_invoice.pdf` | `REJECT` | **`REJECT`** | Staff bulletin; missing mandatory invoice headers, GSTIN, line items, and totals. Not booked. | **Yes** (9/9) | ![Sample 12](docs/screenshots/walkthrough/sample_12_newsletter_not_invoice.png) |
| **13** | **Corrupt file**<br>`bad_inputs/corrupt_file.pdf` | `REJECT` | **`REJECT`** | File payload starts with PDF magic bytes but contains corrupted binary streams. Rejected at Read stage. | **Yes** (9/9) | ![Sample 13](docs/screenshots/walkthrough/sample_13_corrupt_file.png) |
| **14** | **Empty file**<br>`bad_inputs/empty_file.pdf` | `REJECT` | **`REJECT`** | Zero-byte payload. Rejected at Intake/Read stage with unreadable file finding. | **Yes** (9/9) | ![Sample 14](docs/screenshots/walkthrough/sample_14_empty_file.png) |

---

## 4. Post-Run State: Dashboard and Review Queue

Following the sequential processing of all 14 sample files, the system state was captured across both operational overview screens.

### 4.1 Operational Dashboard Metrics
![Dashboard After All Runs](docs/screenshots/walkthrough/15_dashboard_after_all_runs.png)

- **Total Invoices Processed:** 14 (2 scanned via OCR)
- **Straight-Through Processing (STP) Rate:** 29% (4 approved automatically without human touch)
- **Exceptions on Hold:** 5 invoices (`₹6,30,686.00` total value held awaiting review)
- **Rejected Documents:** 5 files rejected (0 financial liability booked)
- **Released for Payment:** `₹10,12,036.00` (4 invoices posted to ledger)
- **Processing Speed:** Median latency `164 ms` (slowest scan `5,663 ms`)

### 4.2 Review Queue Pending Exceptions
![Review Queue After All Runs](docs/screenshots/walkthrough/16_review_queue_after_all_runs.png)

The review queue populated with the 5 held invoices:
1. `04_E1_split_po_overbilling.pdf`: Over-quantity and PO balance breach (Assigned to: **Procurement**).
2. `06_E3_scan_no_number_implied_po.pdf`: Missing invoice number & inferred PO match (Assigned to: **AP Analyst, Procurement**).
3. `08_E4b_tax_inclusive_over_tolerance.pdf`: Unit price variance exceeds 1% tolerance (Assigned to: **Procurement**).
4. `09_fraud_guard_bank_change.pdf`: Bank account deviation requiring vendor callback confirmation (Assigned to: **Vendor Master Team**).
5. `10_ai_assist_letter_invoice.pdf`: Unstructured narrative requiring manual keying or LLM transcription (Assigned to: **AP Analyst**).

---

## 5. AP Manager Usability Audit: UI Problems, Confusing Wording & Suggested Fixes

A non-technical Accounts Payable (AP) Manager or clerk interacts with software from a perspective of compliance, vendor relationships, accounting ledger accuracy, audit trails, and payment turnaround time (SLA). Technical developer terminology, internal engineering codes, and ambiguous state cues create friction and operational risk.

Below is a categorized audit of UI friction points discovered during the walkthrough, accompanied by concrete recommendations.

---

### Category A: Developer & Engineering Jargon vs. Financial Terminology

#### 1. Technical Cryptography & Pipeline Details in Primary Cards
- **Where it appears:** Process invoice banner, Dashboard run details, and Receipt verification box.
- **The Confusing Text:** `SHA-256: 4a2b9...`, `Hash chain`, `Receipt chain: 14 receipts verified`, `Rules hash: a90c...`, `Master data hash`.
- **Why an AP Manager trips:** Non-technical accounting personnel do not know what SHA-256 or hash chaining means. Seeing cryptographic hashes makes the screen feel like a software development debugger rather than an accounting tool. Users may worry that an "unverified hash" indicates an IT network error.
- **Suggested Fix:** Replace technical cryptography labels with accounting audit language:
  - Rename `SHA-256` to **"Document Digital Fingerprint"** or **"File Signature"**.
  - Rename `Receipt chain` to **"Immutable Audit Trail"** or **"Tamper-Evident Activity Log"**.
  - Move raw cryptographic hex strings into a collapsed "Technical & Compliance Metadata" expander.

#### 2. "Run ID" vs. Voucher / Tracking Number
- **Where it appears:** Primary verdict banners (`R-0001`, `R-0002`, etc.) and batch summary tables.
- **The Confusing Text:** `R-0001 | 01_happy_path_furniture.pdf | Approved in 264 ms`.
- **Why an AP Manager trips:** "Run ID" sounds like an internal code execution job or batch script identifier. AP staff look for an **AP Tracking Number**, **Voucher Number**, or **Internal Batch Reference**.
- **Suggested Fix:** Change `R-0001` to **`AP-2026-0001`** or **`Batch Voucher #0001`**.

#### 3. Execution Speed in Milliseconds vs. Operational Throughput
- **Where it appears:** KPI metric on the Dashboard (`Median processing time: 164 ms`, `slowest 5663 ms`) and in verdict headers (`Approved in 264 ms`).
- **The Confusing Text:** Displaying raw execution speeds in milliseconds (`164 ms`, `3461 ms`).
- **Why an AP Manager trips:** Milliseconds are a developer benchmark. In financial operations, processing times under 1 second are indistinguishable. AP managers care about **Throughput (Invoices Processed per Hour)**, **Invoice Cycle Time**, and **Pending SLA Days**.
- **Suggested Fix:** 
  - Change the dashboard KPI from "Median processing time (ms)" to **"Average Handling Speed"** displaying `Instant (< 1 sec)` or **"Processing Throughput: ~220 invoices/hr"**.
  - Keep the exact millisecond duration tucked into the stage breakdown timing column.

#### 4. "Approved with no person involved"
- **Where it appears:** Dashboard KPI card 2.
- **The Confusing Text:** `Approved with no person involved (29% straight through)`.
- **Why an AP Manager trips:** The phrase "with no person involved" can alarm internal auditors, finance controllers, and compliance directors who might misinterpret it as "uncontrolled" or "reckless automation".
- **Suggested Fix:** Use standard banking and enterprise ERP terminology: **"Straight-Through Processing (STP)"** with a sub-caption reading: *"100% rule-matched without manual intervention"*.

---

### Category B: Confusing Sample & Test Case Nomenclature

#### 5. Software Test Case Codes ("E1", "E2", "E4a", "Happy path")
- **Where it appears:** Process invoice sample dropdown (`01 Happy path`, `04 E1 Split PO, third invoice over-bills`, `05 E2 Disguised duplicate`, `07 E4a Tax-inclusive prices...`).
- **The Confusing Text:** "Happy path", "E1", "E2", "E3", "E4a", "E4b".
- **Why an AP Manager trips:** "Happy path" and "E1/E2" are software QA jargon for edge cases. An AP manager or prospective buyer testing the system wants to evaluate business scenarios, such as standard PO matching, split shipments, duplicate prevention, and vendor fraud protection.
- **Suggested Fix:** Rename the dropdown labels to business-oriented scenarios:
  - `01 Happy path` $\rightarrow$ **`01 Standard 3-Way Match (Clean Text PDF)`**
  - `04 E1 Split PO...` $\rightarrow$ **`04 Split Purchase Order (Quantity & Balance Exceeded)`**
  - `05 E2 Disguised duplicate` $\rightarrow$ **`05 Suspected Duplicate (Same Items, Altered Inv #)`**
  - `06 E3 Scan, no invoice...` $\rightarrow$ **`06 Scanned Invoice (Missing Inv #, Inferred PO)`**
  - `07 E4a Tax-inclusive...` $\rightarrow$ **`07 Tax-Inclusive Pricing (Within Tolerance)`**
  - `08 E4b Tax-inclusive...` $\rightarrow$ **`08 Price Variance (Exceeds Policy Tolerance)`**
  - `09 Fraud guard...` $\rightarrow$ **`09 Vendor Fraud Alert (Unauthorized Bank Account Change)`**

---

### Category C: Demo Controls & Accidental Data Loss Risks

#### 6. Prominent "Reset Demo Data" Button in Sidebar
- **Where it appears:** Sidebar popover (`Reset demo data` with confirmation `Yes, reset now`).
- **The Confusing Text:** "Clears every run, review and receipt and restores the seeded history... Use it before a demo".
- **Why an AP Manager trips:** In production financial software, having a prominent red/warning button in the sidebar that can wipe out all transaction records, approvals, and receipts is terrifying. A clerk or manager might worry that clicking it will delete actual accounting records.
- **Suggested Fix:** 
  - Relocate "Reset demo data" to an administrative settings drawer or footer menu labelled **"Demo Utilities & Sandbox Reset"**.
  - Add an explicit banner across the app header clarifying: **"Sandbox Environment: Demo Mode Active"**.

#### 7. Artificial Delay Slider ("Viewing pause per stage")
- **Where it appears:** Sidebar slider (`Viewing pause per stage (seconds)`, default 0.4s).
- **The Confusing Text:** "The pause only slows the display so each stage can be followed; the timings shown are the real processing times."
- **Why an AP Manager trips:** An AP user wonders: *"Why is there a dial to make my software slower? Why wouldn't I always keep it at 0?"* It feels like an artificial throttle or unfinished prototype feature.
- **Suggested Fix:** 
  - Hide this control from the primary sidebar navigation. 
  - Alternatively, convert it into an icon toggle on the Process page: **"Slow-motion Demonstration" [On / Off]**.

#### 8. Technical Environment Diagnostics in User Sidebar
- **Where it appears:** Sidebar mode badges.
- **The Confusing Text:** 
  `Tesseract OCR: ok (Tesseract 5.4.0.20240606)`  
  `LLM assist: REPLAY (recorded responses)`  
  `Rules: 2026.11-1`  
  `Synthetic data only. No real vendors...`
- **Why an AP Manager trips:** AP clerks do not need to know the specific binary version of Tesseract or whether LLM responses are replaying from disk fixtures. If an AP user sees `LLM assist: REPLAY`, they might believe the app is broken or failing to connect to the internet.
- **Suggested Fix:** 
  - Consolidate system health into a simple single status indicator: **"System Status: All Engines Operational"** with a green dot.
  - Clicking the indicator can open a diagnostic modal for IT administrators containing the OCR build, LLM configuration, and rules version.

---

### Category D: Review Queue & Exception Handling Usability

#### 9. "Mark as handled by hand"
- **Where it appears:** Review Queue action button for unbooked invoices (e.g. sample 10 letter invoice or bad inputs).
- **The Confusing Text:** Button text reads: `Mark as handled by hand`.
- **Why an AP Manager trips:** "Handled by hand" sounds casual and unstandardized. Accounting departments follow formal standard operating procedures (SOPs).
- **Suggested Fix:** Change button label to: **"Resolve Manually Offline"** or **"Record Manual ERP Entry"**.

#### 10. Missing Direct Action for "Reply to the sender with the note below"
- **Where it appears:** Banners and next-action notes for rejected invoices (`REJECT: not an invoice`, `REJECT: duplicate content`, `REJECT: not a tax invoice`).
- **The Confusing Text:** Next action reads: `Not booked. Reply to the sender with the note below.`
- **Why an AP Manager trips:** The user expects to see an interactive action—such as a **"Copy Vendor Email Template"** button, an email drafter, or an automated rejection dispatch. Instead, the user has to manually scroll down, expand accordion tabs, find the generated vendor text, and copy-paste it manually.
- **Suggested Fix:** 
  - Add a dedicated **"Vendor Communication"** card with a **"Copy Email to Clipboard"** one-click button containing the pre-filled polite rejection notice and invoice reference.

#### 11. Lack of Supporting Document Attachment in Bank Detail Verification
- **Where it appears:** Review Queue for Bank Account Change (`09_fraud_guard_bank_change.pdf`).
- **The Confusing Text:** Checkbox: `Bank account confirmed with vendor by phone`.
- **Why an AP Manager trips:** In AP internal controls (SOX / ISO audits), an AP clerk cannot simply check a box saying they made a phone call. Audit standards require attaching proof (e.g., vendor letterhead confirmation, voided cheque, or dual supervisor approval sign-off).
- **Suggested Fix:** 
  - In addition to the confirmation checkbox, provide a file upload input: **"Attach Proof of Bank Change (Mandate Letter / Cancelled Cheque)"** and an optional **"Second Reviewer Sign-off"** field for invoices over ₹1,00,000.

---

### Category E: Processing Flow & Duplicate Handling Confusion

#### 12. Retesting the Same Sample Triggers an Intimidating "Duplicate" Warning
- **Where it appears:** Process invoice page when a user clicks "Process this invoice" on a sample that was already run earlier.
- **The Confusing Text:** 
  `Already processed as R-0001. Processing the same file again is rejected as a duplicate submission (that is the control working). Use 'Reset demo data' in the sidebar to start over.`
- **Why an AP Manager trips:** When demonstrating or evaluating the software, users frequently press the button a second time to see the animation or show a colleague. Seeing a rejection with an instruction to "Reset demo data" makes users think the app got stuck.
- **Suggested Fix:**
  - Add a dedicated re-run button: **"Re-run in Sandbox Simulation Mode"** that executes the visualization without writing a duplicate record into the persistent database.
  - Clarify the notice: *"Notice: This document was already posted to the ledger in Voucher #R-0001. In live production, this prevents double payments. To re-simulate this invoice, click 'Re-test in Scratchpad'."*

#### 13. Mathematical Notation in Tax Tolerance Explanations
- **Where it appears:** Stage 6 (PO Match) finding card and verdict banner on samples 07 and 08 (`07_E4a` and `08_E4b`).
- **The Confusing Text:** `Prices on the invoice include GST at 18%. They were converted to pre-tax to compare with the PO; for example line 1: ₹3,780.00 / 1.18 = ₹3,203.39.`
- **Why an AP Manager trips:** Raw programming formula syntax (`₹3,780.00 / 1.18 = ₹3,203.39`) can be hard to skim quickly during a busy invoice approval batch.
- **Suggested Fix:** Format the calculation in a clean two-line comparison table:
  - **Billed Unit Price (Tax-Inclusive):** `₹3,780.00`
  - **Pre-Tax Computed Rate:** `₹3,203.39`
  - **PO Contracted Rate:** `₹3,200.00`
  - **Unit Variance:** `+₹3.39 (+0.11%)` — *Inside 1.00% Policy Tolerance*

---

## 6. Summary of Suggested UI Improvements

| Priority | Issue / Area | Current Text / Layout | Recommended Improvement |
| :---: | :--- | :--- | :--- |
| **High** | Sample Scenario Labels | `Happy path`, `E1`, `E2`, `E4a` | Replace with accounting terms: `3-Way Match`, `Split PO Overbilling`, `Duplicate Detection` |
| **High** | Cryptographic Hashes | `SHA-256`, `Hash chain`, `Receipts` | Relabel as `Audit Fingerprint` and `Immutable Audit Trail`; hide raw hashes in expanders |
| **High** | Vendor Rejection Action | *"Reply to the sender with the note below"* | Add an interactive **"Copy Vendor Email Template"** one-click button |
| **Medium** | Review Queue Resolution | `Mark as handled by hand` | Rename to **`Resolve Manually Offline`** |
| **Medium** | Throughput & Latency | `164 ms median latency` | Display **`Instant (< 1s)`** or **`Throughput: ~220 invoices/hr`** on executive dashboard |
| **Medium** | Straight-Through Metric | `Approved with no person involved` | Rename to **`Straight-Through Processing (STP)`** |
| **Low** | Demo Pause Control | Sidebar slider `Viewing pause per stage` | Convert to **"Slow-motion Animation"** toggle on the process view |
| **Low** | Reset Demo Data | Prominent sidebar button | Move to administrative utility section to prevent accidental trigger |

---

*End of UI Walkthrough & Audit Report. No code changes have been applied.*
