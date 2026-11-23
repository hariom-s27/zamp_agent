# Video script (5 minutes, no slides)

Spoken script for the recorded walkthrough. Every line names the exact click. Words on
screen are quoted as they appear. Business date on screen: 21-Nov-2026.

**Before recording (2 minutes).** Run `.venv\Scripts\python -m scripts.preflight` and
check it ends in `READY`. Start the app (`streamlit run app.py`), browser at 100% zoom.
On **Guided demo**, open **Demo settings and admin** at the bottom: AI assist mode
"Recorded answers: replay stored readings, no live calls", "Process on the demo date"
on. Press **Start a fresh demo**. Pace: **Reading speed**.

| Time | Click | Say |
|---|---|---|
| **0:00** | Sidebar: **Guided demo** (the opening page). Point at the three chips. | "An accounts payable team gets hundreds of supplier invoices a month. Each one has to be matched to the purchase order and the goods receipt before anyone pays it. The costly mistakes are quiet: paying twice, paying for more than was ordered, paying into a changed bank account. Invoice Desk reads each PDF, checks it, and decides in seconds. AI reads the page, rules make the decision, and a named person approves anything that leaves." |
| **0:25** | Press **Watch one invoice go from PDF to decision**. | "A clean invoice from a known vendor. Watch the steps on the right fill in one by one: received, read, details picked out, maths and tax, vendor, PO and goods receipt, duplicates, decided, filed." |
| **0:50** | Wait for the decision card (top right). | "Cleared: everything matches. Nobody had to touch it. On the left, every blue and green box is a place a value was read from the page." |
| **1:00** | Scroll to **What was checked**. | "And here is the proof, check by check: the vendor, the PO number quoted on the invoice, prices, quantities, tax, duplicates and the bank account, each with the page and line it came from." |
| **1:15** | Top of Process invoice: **Document** dropdown, choose "04 Edge case 1: a split purchase order, one invoice too many", press **Process this invoice**. | "Edge case one: overbilling. This PO is for 20 gas detectors. Two earlier invoices billed 16. This one bills 5 more. Nothing is wrong on the invoice itself; only the running total shows it, 21 of 20." |
| **1:40** | Decision card shows "On hold". Press **Open the case**. | "On hold, with one question for one owner: amend the PO, or ask for a corrected invoice? Every hold becomes a case with a due date, so it cannot sit in someone's inbox." |
| **2:00** | Sidebar: **Process invoice**. Choose "09 Fraud control: the bank account has changed", press **Process this invoice**. | "Edge case two: a bank-change fraud guard. Everything matches except the account to pay. That is what payment fraud looks like, so it stops whatever else is true." |
| **2:20** | Sidebar: **Review queue**. On the bank-change card type "N. Rao" as name, a call note, tick the call-back box, press **Record first approval**. Then type "n rao" in the second slot, tick the box, press **Approve for payment**. | "To release it, one person records a call-back to the number already on file. And if the same person tries to approve again, it is refused, right under the button: 'Choose a different person. Two people must agree to a bank change.'" |
| **2:50** | Sidebar: **Process invoice**. Choose "10 AI assist: an invoice written as a letter", press **Process this invoice**. Point at step 3 and the purple boxes. | "Now the one place AI is used. This invoice is written as a letter: no table, no labels. The rules will not guess. The AI copies each missing value with the sentence it came from, and the desk checks every one against the page and the invoice's own arithmetic. Step 3 is tagged 'AI read'. The purple boxes are what the AI read. The AI reads; it never decides." |
| **3:25** | Sidebar: **Dashboard**. | "The Dashboard is the history of every run: the share cleared without a person, what is on hold now and who is holding it, the money stopped before payment, and the audit trail, re-checked on every visit." |
| **3:50** | Sidebar: **Inbox**. Press **Deliver today's mail (7 messages)**. | "In real life invoices arrive by email. The desk sorts every message, does what the procedure allows, and names who has the next step: invoices processed, a payment question answered from the records, and a look-alike sender asking for a bank change flagged and sent to the Vendor master team." |
| **4:15** | Sidebar: **Cases**, then **Payments**; on Payments press **Load the payment-run example**. | "Holds are followed on Cases until they are resolved. And on Payments the desk proposes this week's run: legal small-supplier dates first, the vendor with an unverified bank change blocked, and one or two named approvals depending on the amount. Nothing is paid from here." |
| **4:45** | Stop on Payments. | "PDF in, decision out, every step shown, and a person wherever money or a bank account is at stake. Thank you." |

**If running long:** skip the Inbox line (3:50) and go from Dashboard straight to Cases.

**If the AI reading is not recorded on this machine** (preflight warns about the letter
invoice), step 10 ends "On hold". Say instead: "Without a recorded AI reading the rules
hold it and say exactly what they could not read. That is the safe default."
