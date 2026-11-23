# Final audit (round 5, 24 Nov 2026)

Each item of the guide, the exact page and click that shows it, and what is still weak.
Business date on every screen: 21-Nov-2026 with the real time of day. All data is
synthetic.

| # | The guide asks for | Where it is shown (page, then click) | Still weak |
|---|---|---|---|
| 1 | **A live link** | Not in this repository: the app is deployed from the branch the owner pushes (Streamlit Community Cloud, `INVOICE_DESK_SANDBOX=session` so each visitor gets private books). Locally: `streamlit run app.py`, then <http://localhost:8501>. | The URL is not written down here because this branch has not been pushed. After pushing, open the link in a private window and repeat checkpoint 1 below once. |
| 2 | **Real input** | **Process invoice** → "Upload a file" → choose any PDF → **Process this file**. Or **Inbox** → "Add .eml files". | Real invoices from vendors outside the made-up vendor list are held as "vendor not on the approved list", by design. The reader is built for Indian GST invoices in English. |
| 3 | **Real output** | Any finished run: decision card (Cleared / On hold / Rejected), **Full record** tab → **Full record (JSON)**, **Audit receipt (JSON)**, **Vendor message (TXT)**. **Payments** → approve → **Download the proposal (CSV)** (opens in Excel with the ₹ sign). **Cases** → **Approve and send** writes an `.eml` to the outbox. | No mail server or bank is connected: "send" writes a file, a payment run writes a review copy with masked accounts. |
| 4 | **Stage-by-stage UI** | **Guided demo** → **Watch one invoice go from PDF to decision**: the nine steps fill in one by one on Process invoice at reading speed, then settle into the timeline. A repeat of the same file stops at step 1 ("Duplicate file") with **Open the original record**. | At "Fast" pace the steps are too quick to follow; the presenter must keep "Reading speed". |
| 5 | **Dashboard of past runs** | **Dashboard** → Overview (five numbers, three charts, Needs attention, audit-trail check) → **All invoices** (every run, filters, CSV, the full record of any run). **Business case** shows the same invoice, on-hold and per-owner counts from the same query. | The history is the session's own books (no AP month of seeded volume; that dataset was not built). Monthly charts therefore show one month. |
| 6 | **2 to 4 edge cases** | **Guided demo** steps 3 to 8, or **Process invoice** dropdown: 04 split PO overbilling (On hold, 21 of 20), 05 disguised duplicate (Rejected), 06 scan with no number and an inferred PO (On hold), 07 and 08 tax-inclusive prices either side of tolerance, 09 bank-change fraud guard (On hold, two people in **Review queue**). | The two scanned samples take a few seconds at step 2 with live OCR. |
| 7 | **Deliberate AI use** | **Process invoice** → "10 AI assist: an invoice written as a letter" → **Process this invoice**: step 3 tagged "AI read N", purple boxes on the page, each value checked against a quote and the arithmetic; the decision stays with the rules. Sidebar **Technical details**: "AI reading: recorded answers (no live calls)". Vendor replies in **Cases** are read by AI and offered to a person, never applied. | Live AI calls need a key and are capped; the public demo uses recorded readings. The robustness test is 49 documents, so "misread and cleared" could be up to 7%. |
| 8 | **Clear for a non-technical buyer** | One set of status words (Cleared / On hold / Rejected) everywhere; plain step names; reason codes only under "Reason code (for auditors)"; ₹ everywhere; one date format; no model names or developer words on any screen (checked by `tests/test_round5_screens.py` on a fresh session and on filled books). | Some engine sentences are still long (they are reworded at display time, not rewritten, because the engine modules are frozen). |

## Checkpoint 1: a first visit, step by step

1. Fresh session → **Guided demo** → **Watch one invoice go from PDF to decision** → the
   steps fill in → back to **Guided demo**: step 1 is shown as done, **Run step 2: Scanned**
   and **Run all remaining** are enabled.
2. **Process invoice** → any sample → **Process this invoice**: the timeline fills in.
   Caption underneath: "Processed in this session as AP-2026-000N". Press again: step 1
   reads "Duplicate file: the same file as AP-2026-000N", later steps "Not run: duplicate
   file", and **Open the original record** links to it.
3. Every time a person sees (timelines, approvals, Dashboard, Inbox, payment runs) is on
   21-Nov-2026; the next morning's mail is on 22-Nov-2026 between 09:00 and 10:30.
4. Sidebar **Technical details**: "AI reading: recorded answers (no live calls)". No model
   name.
5. **Payments** → **Load the payment-run example** a second time adds nothing; a vendor
   whose bank change two people approved is not blocked.

## Commands (all green on 24 Nov 2026)

See PROGRESS.md, "Round 5 finishing", for the counts measured on this machine.
