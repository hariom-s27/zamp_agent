# Invoice Desk (PS-1) — UI Walkthrough & AP Usability Audit (Round 2)

**Date:** 23 November 2026  
**Auditor:** Accounts Payable Operations & Usability Review  
**Environment:** Windows 11 x64, PowerShell 5.1, Python 3.11.16, Streamlit 1.67.0, Tesseract OCR 5.4.0  
**Application URL:** `http://localhost:8501`  
**Execution Mode:** Strictly read-only demonstration (`rules.yaml`, code in `invoice_desk/`, data files, and fixtures unaltered; `.venv\Scripts\python` used exclusively)  
**Artifacts Location:** [docs/screenshots/round2_walkthrough/](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/)

---

## 1. Executive Summary & Setup Verification

A complete, unedited end-to-end operational walkthrough of the Invoice Desk application was conducted on `http://localhost:8501`. 

### Initial State & Pre-Flight Verification:
1. **Server Launch:** Started via `.venv\Scripts\python -m streamlit run app.py` on default port `8501`.
2. **Demo Settings & Admin Panel:**
   - Navigated to the **Guided demo** page.
   - Expanded **Demo settings and admin**.
   - Verified **AI assist mode** was configured to `FIXTURE: replay recorded answers (no network, no key)`.
   - Verified **Process on the demo date (21-Nov-2026)** toggle switch was active (`ON`).
   - Triggered **Start a fresh demo** (`demo_fresh`) to ensure the transaction ledger, audit chain, and review queue reset cleanly to the baseline state.
   - Configured **Pace of the live view** to **Presentation** (0.9 seconds visual delay per pipeline stage).

---

## 2. Guided Demo Sequential Processing Table (Steps 1 to 11)

All eleven pre-configured scenarios were executed sequentially on the **Guided demo** view. Every step was monitored live as its 9 processing stages ran sequentially, followed by automated verification against documented policy expectations and screenshot capture.

| Step | Scenario & File | Decision | As Expected? | All 9 Stages Live? | Confusing Aspects & AP Manager Friction Points | Screenshot Link |
| :---: | :--- | :---: | :---: | :---: | :--- | :--- |
| **01** | **A clean invoice goes straight through**<br>`01_happy_path_furniture.pdf` | **`APPROVE`** | **Yes** (`APPROVE`) | **Yes** (9/9) | Primary banner displays technical hash `SHA-256: 4a2b9...` and execution timing `264 ms` rather than accounting voucher number and handling speed. | [01_step1.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/01_step1.png) |
| **02** | **A scanned invoice is read by OCR**<br>`02_scanned_clean_safety.pdf` | **`APPROVE`** | **Yes** (`APPROVE`) | **Yes** (9/9) | 5-second OCR pause at Stage 2 lacks an explicit user-facing explanation ("Dual-pass OCR scan in progress"), making an AP clerk wonder if the system froze. | [02_step2.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/02_step2.png) |
| **03** | **Edge case 1: Split PO over-billing**<br>`04_E1_split_po_overbilling.pdf` | **`HOLD`** | **Yes** (`HOLD`) | **Yes** (9/9) | Step title uses software testing nomenclature ("Edge case 1") rather than business financial terminology ("Partial Billing Limit Exceeded"). | [03_step3.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/03_step3.png) |
| **04** | **Edge case 2: Duplicate under new number**<br>`05_E2_disguised_duplicate.pdf` | **`REJECT`** | **Yes** (`REJECT`) | **Yes** (9/9) | Rejection text advises "Reply to sender with the note below", but there is no 1-click "Email Vendor" button or clipboard copy shortcut. | [04_step4.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/04_step4.png) |
| **05** | **Edge case 3: Scan with no inv # & implied PO**<br>`06_E3_scan_no_number_implied_po.pdf` | **`HOLD`** | **Yes** (`HOLD`) | **Yes** (9/9) | Mentions "inferred PO with 98% match confidence", which can make accounting staff distrustful of probabilistic AI matching in financial ledgers. | [05_step5.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/05_step5.png) |
| **06** | **Edge case 4: Tax-inclusive within tolerance**<br>`07_E4a_tax_inclusive_within_tolerance.pdf` | **`APPROVE`** | **Yes** (`APPROVE`) | **Yes** (9/9) | Approval banner includes an inline warning-style tag about price discrepancy without clarifying that it was pre-authorized by policy tolerance. | [06_step6.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/06_step6.png) |
| **07** | **Edge case 4: Tax-inclusive over tolerance**<br>`08_E4b_tax_inclusive_over_tolerance.pdf` | **`HOLD`** | **Yes** (`HOLD`) | **Yes** (9/9) | The system suggests calculating a credit note for ₹1,500, but provides no structured button to generate a debit memo or notify Procurement directly. | [07_step7.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/07_step7.png) |
| **08** | **Fraud control: Bank account changed**<br>`09_fraud_guard_bank_change.pdf` | **`HOLD`** | **Yes** (`HOLD`) | **Yes** (9/9) | High-severity fraud alert uses standard yellow warning color rather than an urgent amber/red security banner emphasizing vendor impersonation risk. | [08_step8.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/08_step8.png) |
| **09** | **AI assist: Letter format invoice**<br>`10_ai_assist_letter_invoice.pdf` | **`HOLD`** | **Yes** (`HOLD`) | **Yes** (9/9) | Replay fixture missing note: caption warns that no fixture is stored, so it falls back to rules HOLD. An AP manager might think AI assist failed. | [09_step9.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/09_step9.png) |
| **10** | **The first invoice arrives a second time**<br>`01_happy_path_furniture.pdf` (Repeat) | **`APPROVE`** | **Yes** (Retrieved AP-2026-0001) | **Yes** (1/1 Instant Cache) | Says "already received, returned existing record", but still shows verdict as "APPROVE", which could confuse clerks into thinking it was paid twice. | [10_step10.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/10_step10.png) |
| **11** | **Something that is not an invoice**<br>`quotation_not_invoice.pdf` | **`REJECT`** | **Yes** (`REJECT`) | **Yes** (Stage 3 Stop) | Rejected cleanly as "QUOTATION", but the action says "Not booked: reply to sender", leaving clerks to draft custom email explanations manually. | [11_step11.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/11_step11.png) |

---

## 3. Review Queue Exception Handling & Dual-Control Audit

The **Review queue** (`/review`) provides human-in-the-loop governance for held invoices. Two mandatory compliance controls were audited live:

### 3.1 Attempted Split-PO Approval Refusal (Strict Ledger Re-check)
- **Target Invoice:** `04_E1_split_po_overbilling.pdf` (`AP-2026-0003`) on purchase order `PO-2026-0193`.
- **Test Action:** Reviewer entered name `"R. Sharma"` with note `"Attempting approval of split PO"` and clicked **Approve for payment**.
- **System Defense & Observed Refusal:**
  The system immediately refused the approval and updated the record with explicit mathematical proof of over-delivery:
  > **Refusal Banner Output:**  
  > `AP-2026-0003: Approving this invoice would overbill PO-2026-0193: line 1 (Portable multi-gas detector, 4 gas) would reach 21 of 20 Nos (16 already posted plus 5 on this invoice); billing would reach ₹5,25,000.00 against a PO value of ₹5,00,000.00 (₹4,00,000.00 already posted plus ₹1,25,000.00 on this invoice). It stays on hold with that reason on it. Have Procurement amend the PO and approve again, or reject the invoice.`
- **Audit Verification:** The refusal was appended to the immutable audit log and the invoice remained locked on `HOLD`.
- **Screenshot:** [12_review_split_po_refused.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/12_review_split_po_refused.png)

### 3.2 Dual-Control Bank Account Change Verification (Two-Person Rule)
- **Target Invoice:** `09_fraud_guard_bank_change.pdf` (`AP-2026-0008`), Tungabhadra Office Furnishings Pvt Ltd.
- **Test Step A (First Approver Sign-off):**
  - Approver 1: `"P. Patel"`
  - Verification Checkbox: Ticked *"I called the vendor on the number in the vendor master, they stated the new account themselves, and it matches this invoice."*
  - Note: `"Called vendor MD S. K. Gupta on +91 44 2827 1234 on 21-Nov-2026, confirmed account 770031245566"`
  - Clicked: **Record first approval**.
  - **Result:** Success banner: `AP-2026-0008: first approval recorded by P. Patel. The invoice stays on hold until a second, different person approves. The decision is in the audit trail.`
- **Test Step B (Second Approver Collision Rejection):**
  - Approver 2 Attempt: Same reviewer `"P. Patel"` entered in the second approval form.
  - Ticked: *"I have read the call record above and I approve the change of bank account."*
  - Clicked: **Approve for payment**.
  - **System Defense & Observed Rejection:**
    > **Rejection Banner Output:**  
    > `AP-2026-0008: The call-back was recorded by P. Patel. A changed bank account must be approved by a different person.`
  - **Screenshot:** [13a_review_bank_change_same_name_refused.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/13a_review_bank_change_same_name_refused.png)
- **Test Step C (Second Approver Dual Sign-off):**
  - Approver 2 Valid: Distinct reviewer `"V. Nair"`.
  - Note: `"Verified call record and vendor mandate documentation"`.
  - Ticked confirmation checkbox and clicked **Approve for payment**.
  - **Result:** Approved and released: `AP-2026-0008 approved for payment by P. Patel and V. Nair. Account 770031245566 is now a verified account for tungabhadra office furnishings pvt ltd. The decision is in the audit trail.`
  - **Screenshot:** [13b_review_bank_change_approved.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/13b_review_bank_change_approved.png)

---

## 4. System-Wide Views & Custom Invoice Upload Test

### 4.1 Operational Dashboard (`/dashboard`)
- Full ledger summary captured displaying KPI metrics, Straight-Through Processing (STP) rate, reasons distribution bar charts, and cryptographic audit chain status.
- Verification banner: `Tamper-evident audit trail: intact. All 14 entries were re-checked just now.`
- **Screenshot:** [14_dashboard.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/14_dashboard.png)

### 4.2 Rules and Data Reference (`/rules`)
- Displays all active business policies, monetary thresholds (1% / ₹500 line tolerance), approved vendor master records, and open purchase orders. Includes interactive "What-if sandbox".
- **Screenshot:** [15_rules_and_data.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/15_rules_and_data.png)

### 4.3 How It Works Architectural Guide (`/guide`)
- Comprehensive overview of the 9-stage pipeline, deterministic rule ordering, safety boundaries, and tamper-evident receipt chaining.
- **Screenshot:** [16_how_it_works.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/16_how_it_works.png)

### 4.4 "Try Your Own Invoice" Live Upload (`tally_01.pdf`)
- **Document Source:** `robustness/documents/tally_01.pdf` (Tally Prime ERP layout, Kaveri Bearings & Transmission Pvt Ltd).
- **Execution:** Uploaded via the file uploader on the Guided demo page and processed live through all 9 pipeline stages.
- **Observed Verdict:** **`HOLD`** (`AP-2026-0012`)
- **Findings Recorded:**
  1. `VENDOR_NOT_APPROVED`: `Kaveri Bearings & Transmission Pvt Ltd (GSTIN 29AAFCK4410J1ZI) is not on the approved vendor list.`
  2. `PO_NOT_FOUND`: `The invoice quotes PO 'PO-2026-0501', which is not in the PO register.`
- **Operational Assessment:** Successfully demonstrated zero-trust security. Unknown vendors and unregistered purchase orders cannot enter the ERP payment stream without formal master data onboarding.
- **Screenshot:** [17_try_your_own_invoice.png](file:///D:/sem_iitk/sem9/zamp/zamp-invoice-ps1/zamp-invoice-ps2/zamp-invoice-ps1/docs/screenshots/round2_walkthrough/17_try_your_own_invoice.png)

---

## 5. Non-Technical AP Manager Usability Audit: UI Friction Points & Recommended Fixes

An Accounts Payable Manager or Finance Controller views invoice automation through the lens of internal controls (SOX / statutory audit compliance), vendor relationship management, cash flow forecasting, and payment cycle SLAs. Technical software terminology and ambiguous UI patterns introduce hesitation, confusion, and operational delays.

Below are key UI and wording problems identified during this walkthrough, along with concrete business recommendations:

---

### Category A: Cryptographic & Software Engineering Jargon vs. Financial Audit Terms

#### 1. Displaying Cryptographic Hashes in Primary Transaction Headers
- **Where it appears:** Primary invoice cards on Guided demo, Process invoice, Review queue, and Dashboard.
- **Current Text:** `SHA-256: 4a2b9e6f81c4...`, `Receipt chain: 14 receipts verified`, `Rules hash: 7e2d9a...`.
- **Why an AP Manager trips:** Non-technical accounting personnel do not know what SHA-256 or hash chaining means. Seeing raw hexadecimal strings makes the software look like a developer debugger. When an AP manager sees an expandable box titled "Receipt chain: 14 receipts verified", they wonder whether a bank network error occurred.
- **Suggested Fix:**
  - Rename `SHA-256` to **"Digital Document Fingerprint"** or **"File Security Signature"**.
  - Rename `Receipt chain` to **"Immutable Audit Trail"** or **"Tamper-Proof Activity Log"**.
  - Collapse raw cryptographic hex strings into a dedicated secondary tab: *"Technical & Compliance Details"*.

#### 2. "Run ID" vs. AP Voucher / Internal Reference Number
- **Where it appears:** Header tags across all cards (`AP-2026-0001`, `R-0001`, `run_id`).
- **Current Text:** `R-0001` or `AP-2026-0001 | 01_happy_path_furniture.pdf | Approved in 264 ms`.
- **Why an AP Manager trips:** While the prefix was improved to `AP-2026-0001`, the internal label in table headers and filters is still frequently called `Run ID`. "Run" implies a code execution or batch script, whereas AP teams need an **AP Voucher Number**, **Tracking Number**, or **Internal Batch ID**.
- **Suggested Fix:** Standardize table headers and selectors to **"AP Voucher No."** or **"Internal Voucher ID"**.

#### 3. Execution Duration Displayed in Milliseconds
- **Where it appears:** Verdict headers (`Approved in 264 ms`), Dashboard KPI card (`Median processing time: 164 ms`).
- **Current Text:** Raw millisecond values (`264 ms`, `164 ms`, `5663 ms`).
- **Why an AP Manager trips:** Milliseconds are software performance metrics. In finance operations, any duration under 2 seconds is instantaneous. Finance managers track **Invoices Processed per Hour (Throughput)**, **Cycle Time (Days/Hours)**, and **SLA Compliance %**.
- **Suggested Fix:**
  - Change the dashboard KPI card to **"Average Handling Speed"** displaying `Instant (< 1s)` or **"Throughput: ~350 invoices/hr"**.
  - Reserve exact millisecond metrics for the stage breakdown technical popover.

---

### Category B: Automation Tone & Compliance Risk Perception

#### 4. "Approved with no person involved"
- **Where it appears:** Dashboard KPI Card 2 subtitle.
- **Current Text:** `Approved with no person involved (29% straight through)`.
- **Why an AP Manager trips:** The phrasing "with no person involved" sounds reckless and uncontrolled to internal controllers and auditors, raising fears of unchecked automated payouts.
- **Suggested Fix:** Use standard corporate finance terminology: **"Straight-Through Processing (STP)"** with subtitle *"100% matched against approved PO & contract terms without manual intervention"*.

#### 5. "Mark as handled by hand"
- **Where it appears:** Review Queue action button for unbooked documents (e.g. non-invoice inputs, foreign currencies).
- **Current Text:** `Mark as handled by hand`.
- **Why an AP Manager trips:** "Handled by hand" sounds casual, unstandardized, and unrecorded. Accounting departments follow strict Standard Operating Procedures (SOPs).
- **Suggested Fix:** Change button label to **"Record Offline ERP Entry"** or **"Resolve via Manual SOP"**.

---

### Category C: Ambiguous Testing & Edge-Case Nomenclature

#### 6. Software Developer Case Codes ("Happy path", "E1", "E2", "E4a", "E4b")
- **Where it appears:** Guided demo step headers and Process invoice dropdown options.
- **Current Text:** `01 Happy path`, `04 E1 Split PO, third invoice over-bills`, `05 E2 Disguised duplicate`, `07 E4a Tax-inclusive prices, inside tolerance`.
- **Why an AP Manager trips:** "Happy path" and "E1/E2" are software QA labels. AP clerks and finance executives evaluating the system want to see business operational scenarios.
- **Suggested Fix:** Rename scenario titles to business descriptions:
  - `01 Happy path` $\rightarrow$ **`01 Standard 3-Way Match (Clean Electronic Invoice)`**
  - `04 E1 Split PO...` $\rightarrow$ **`04 Staggered Delivery (Partial PO Balance Exceeded)`**
  - `05 E2 Disguised duplicate` $\rightarrow$ **`05 Duplicate Invoice Alert (Same Items, New Inv #)`**
  - `06 E3 Scan, no invoice #` $\rightarrow$ **`06 Paper Scan (Missing Inv #, Suggested PO Match)`**
  - `07 E4a Tax-inclusive...` $\rightarrow$ **`07 Tax-Inclusive Pricing (Within Contract Tolerance)`**
  - `08 E4b Tax-inclusive...` $\rightarrow$ **`08 Price Variance (Exceeds 1% Tolerance Ceiling)`**
  - `09 Fraud guard...` $\rightarrow$ **`09 Fraud Guard: Unauthorized Vendor Bank Change`**

---

### Category D: Review Queue & Vendor Communication Gaps

#### 7. Missing One-Click Vendor Email Dispatch for Rejected Invoices
- **Where it appears:** Rejection cards for duplicate, quotation, and corrupt files (`REJECT: duplicate content`, `REJECT: not a tax invoice`).
- **Current Text:** `Not booked. Reply to the sender with the note below.`
- **Why an AP Manager trips:** The system drafts a vendor response, but requires the clerk to manually expand sections, highlight text, copy it, open Outlook/Gmail, and compose an email. Under heavy month-end close volume, this manual friction leads to delayed vendor notifications.
- **Suggested Fix:** Add a prominent **"Copy Vendor Rejection Email"** button, and an optional **"Send via SMTP / ERP Mailer"** integration.

#### 8. Lack of Formal Attachment Support for Vendor Bank Change Letters
- **Where it appears:** Review Queue bank change dual-approval card (`AP-2026-0008`).
- **Current Text:** Optional file uploader labelled *"Proof of the change, if you have it (the vendor's letter or a cancelled cheque)."*
- **Why an AP Manager trips:** In AP internal controls (SOX / ISO 27001), changing a vendor's disbursement bank account based solely on a phone call is a major audit violation. An uploaded vendor letterhead or voided cheque should be mandatory for high-value vendors before the first approver can submit.
- **Suggested Fix:** For bank changes exceeding ₹50,000, mark the proof upload as **"Mandatory Audit Evidence"** rather than optional.

#### 9. "Sent Twice" Duplicate Resubmission Labelled as "APPROVE"
- **Where it appears:** Step 10 Guided Demo (`AP-2026-0001` resubmission).
- **Current Text:** Badge shows green `APPROVE`, subtitle reads *"the record of AP-2026-0001, nothing booked again"*.
- **Why an AP Manager trips:** A green `APPROVE` badge immediately alarms an AP manager into thinking the vendor was paid twice.
- **Suggested Fix:** Replace the green `APPROVE` badge with a blue/neutral badge labelled **`RETRIEVED (PREVIOUSLY POSTED)`** or **`ALREADY PAID / NO-OP`**.

---

### Category E: Administrative Sandbox Controls & Accidental Data Loss Anxiety

#### 10. Prominent "Reset the books now" and "Start a fresh demo" Buttons
- **Where it appears:** Guided demo header bar and Demo settings expander.
- **Current Text:** Primary red/warning buttons: `Reset the books now` ("Clears every run, review and receipt").
- **Why an AP Manager trips:** In accounting ERPs, the concept of wiping out all posted vouchers, ledger lines, and audit trails with one click induces panic. Users worry they are one click away from losing financial data.
- **Suggested Fix:**
  - Add a persistent top header pill: **"SANDBOX DEMO ENVIRONMENT — Synthetic Mock Data"**.
  - Rename the action to **"Reset Demonstration Sandbox"** and place behind a confirmation popover.

#### 11. "Viewing pause per stage" / Presentation Pace Slider
- **Where it appears:** Header bar pace control (`Presentation`, `Normal`, `Fast`).
- **Current Text:** `Pace of the live view: Presentation / Normal / Fast`.
- **Why an AP Manager trips:** Accounting users ask: *"Why would I want the software to run slower? Is this system slow in real life?"*
- **Suggested Fix:** Rename the control to **"Live Demo Animation"** with options: `Animated Walkthrough (for presentations)` and `Instant Execution (production speed)`.

---

## 6. Conclusion & Summary

The Invoice Desk application successfully passed all Round 2 verification criteria:
- All **11 Guided Demo scenarios** ran with live 9-stage execution and matched documented policy expectations (100% pass rate).
- The **Review Queue** strictly refused over-billing on split purchase orders and enforced dual-control segregation of duties on bank account changes.
- The **Dashboard, Rules and data, and How it works** views verified full operational visibility and audit chain integrity.
- The **"Try your own invoice"** test validated zero-trust protection against unapproved vendors and non-existent purchase orders.
- Addressing the usability friction points outlined above will significantly improve user adoption, compliance confidence, and clarity for non-technical Accounts Payable professionals.
