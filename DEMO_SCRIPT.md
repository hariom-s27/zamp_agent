# Demo script

Two things are here: the narration for the 5-minute video, and the exact run order for
the live interview. Both use the same samples in the same order, so one rehearsal
serves both.

The guide asks for the happy path running live, at least one edge case, narration of
what happens and why, five minutes at most, no slides and no editing. It also says the
reader is a non-technical buyer. So the words below are about money and risk, not code.

## Before you record or present (2 minutes)

1. Start the app and open it in the browser at 100% zoom, full screen.
2. Sidebar: press **Reset demo data**, then "Yes, reset now".
3. Sidebar: set **Viewing pause per stage** to **0.3** for the video (0.5 for the live
   interview, where there is more time to talk over each stage).
4. Stay on **Process invoice**, with "Sample invoice" selected.
5. Have a timer visible to you. Do one full dry run first.

Scanned samples (02 and 06) take about four seconds at the Read stage when the OCR
engine is installed. That pause is real work; say so.

---

## Part 1. The 5-minute video

About 620 words of narration. Speak at a normal pace and do not fill the silences while
a run is in progress: let the stages be seen.

| Time | On screen | Say |
|---|---|---|
| **0:00** | The Process invoice page, nothing run yet | "This is my build for problem statement one: invoice processing, from PDF to decision. An accounts payable team gets hundreds of invoices a month. For each one somebody finds the purchase order, checks the numbers and decides. The expensive mistakes are quiet ones: paying twice, paying more than was ordered, paying into the wrong account. My process makes that decision, shows everything it did on the way, and stops for a person whenever it is not sure." |
| **0:30** | Select **01 Happy path**. Press **Process this invoice**. Stages run. | "The happy path first. Nine stages, and each one lights up as it runs. It registers the file, reads the page, pulls out the fields, checks the invoice against itself, identifies the vendor, matches it to the purchase order, and looks for duplicates. Only then does it decide." |
| **0:50** | Decision banner. Point at the boxes on the document, then open stage 6. | "Approve: pay four lakh forty-nine thousand. Every box on the document is a value it read, so nothing is taken on trust. Here is the match: three lines, quantity and price, line by line against the PO. The invoice is posted and gets a receipt." |
| **1:15** | Select **04 E1 Split PO**. Process. | "Edge case one. A vendor splits one purchase order across several invoices. This PO is for twenty gas detectors. Two earlier invoices already billed sixteen. This one bills five more." |
| **1:30** | HOLD banner. Open stage 6, show "Running total on PO-2026-0193". | "Nothing is wrong with this invoice on its own. The problem only shows in the running total: twenty-one of twenty, twenty-five thousand rupees over. So it is on hold, with a named owner and one question: were the extra units ordered? And here is a note to the vendor, drafted for a person to send." |
| **2:00** | Select **05 E2 Disguised duplicate**. Process. | "Edge case two. Same vendor, same items, same amount as an invoice booked two days ago, but with a new invoice number. A check on the invoice number alone would pay this twice." |
| **2:15** | REJECT banner. | "Rejected, and it points at the original. It can be this firm because the purchase order is already fully billed, so nothing more can be owed. If the PO still had room it would ask a person whether this is a second delivery." |
| **2:35** | Select **06 E3 Scan**. Process. Wait for Read. | "Edge case three is a scan: a tilted photo of a page, no text in the file. It straightens the page and reads it twice, in two different ways, and uses the read whose figures add up." |
| **2:55** | HOLD banner. Point at the blank "Bill No." on the document, then the reasons. | "Two things are missing here. The invoice number box is blank, and it does not guess one. No purchase order is quoted either, so it finds the best fit among this vendor's open orders, shows the score and the runner-up, and asks the PO owner to confirm. An inferred PO is never posted without a person." |
| **3:25** | Select **08 E4 Tax-inclusive, outside tolerance**. Process. | "Edge case four. This vendor prints prices with tax inside; our PO is before tax. It converts them and compares. Fifty rupees over on each unit: one and a half percent, and the limit is one percent." |
| **3:45** | HOLD banner. | "On hold, with the exact arithmetic. The sister sample is three rupees over, and that one is approved with a note. Same rule, both sides of the line." |
| **3:55** | Click **Review queue**. On the scanned invoice, type your name and press Approve. | "Holds wait here for a person. I try to approve the invoice that had no number, and it refuses: the number has to be obtained from the vendor first, because without it the same invoice could be paid twice later." |
| **4:15** | Click **Dashboard**. | "The dashboard: what was processed, how much went straight through, what is waiting and why. Every decision, the machine's and the person's, writes a receipt into a chain, and this line recomputes the whole chain. Any run can be opened again with all its evidence." |
| **4:35** | Click **Rules and data**, the Decision table tab. | "And none of the policy is in code. Every threshold and every action is a line in one rules file that a process owner can change. The decision itself uses no AI model, so the same invoice always gets the same answer, and more than three hundred and fifty automated tests hold it there." |
| **4:55** | Stop. | "That is the process. Thank you." |

**If you are running long:** drop the Rules and data step (4:35) and close on the
dashboard. If you are still long, drop the Review queue step. Do not drop an edge case.

**If you are running short:** after E4, also run **09 Fraud guard** and say: "A perfect
match on the PO, but the bank account is not the one on file. That is how payment fraud
looks, so it is held until someone calls the vendor on the number we already had."

**If a run shows "already processed":** you forgot to reset. Press Reset demo data and
start that run again. In the video, start over; it is five minutes.

---

## Part 2. The live interview: exact run order

No slides. The process runs, they ask, you explain. Budget about ten minutes for the
runs and leave the rest for questions.

**Setup while saying hello:** Reset demo data. Viewing pause 0.5.

| # | Do | The one point to land |
|---|---|---|
| 1 | **01 Happy path**. Open stage 3 (fields with sources) and stage 6 (line-by-line match). | Everything it read is traceable to a place on the page. Nothing is decided until stage 8. |
| 2 | **04 E1 Split PO**. Open stage 6, the "Running total" table. | The invoice is fine on its own; only history shows the over-billing. That is why there is a ledger. |
| 3 | **05 E2 Disguised duplicate**. | Three tiers of duplicate control. Certain only when nothing more can be owed; otherwise it asks. |
| 4 | **06 E3 Scan**. Open stage 2 and stage 3 ("Two reads of the scan"). | Missing stays missing. An inferred PO is a suggestion with evidence, never a match. |
| 5 | **07 E4a**, then **08 E4b**. | The same rule on both sides of the threshold: approve with a note, hold with the arithmetic. |
| 6 | **09 Fraud guard**. Then **Review queue**: try to approve it without ticking the call-back. | A perfect invoice can still be a fraud. The control is in the review step, and a confirmed account is remembered. |
| 7 | **Review queue**: approve **08** with your name and a note. | A hold ends with a named person, and that decision joins the audit trail. |
| 8 | **Dashboard**. Point at the KPIs and the audit-trail line; open one run from the history. | History, status and outputs across runs. Any decision can be reconstructed later. |
| 9 | **Rules and data**, "What-if sandbox": sample 07, set unit price tolerance to **0.10**, "Run both ways". | The decision flips from approve to hold and the only thing that changed was a number in the rules. |

Keep these ready in case they ask:

- **"Upload something that is not an invoice."** Process invoice, "Upload a file",
  choose `samples\bad_inputs\quotation_not_invoice.pdf` (rejected as a quotation) or
  `corrupt_file.pdf` (rejected as unreadable). It refuses with a reason and never
  crashes. Their own file is welcome too: an unknown vendor is held as "not on the
  approved list", a dollar invoice is held as "not in rupees".
- **"Where is the AI?"** Run **10 AI assist** (only if you recorded a response; see
  SETUP_WINDOWS.md step 6). Without the recording it is held by the rules alone, which
  is also a fair thing to show: say that the rules know what they could not read.
- **"Run them all."** "Run every sample in one go" on the Process page: fourteen runs,
  each compared with its documented outcome.
- **"Show me it is real."** Open a terminal: `.venv\Scripts\python -m invoice_desk.run
  --all`, or `-m pytest`.

## If something goes wrong live

| What you see | What to do, and what to say |
|---|---|
| A sample is rejected as "already processed" | Reset demo data, run it again. "That is the duplicate control doing its job; I had run this file already." |
| A decision differs from what you rehearsed | Read the reasons on screen out loud. They say exactly why. Then reset and rerun. Do not guess. |
| The scan is slow | "It is reading the page twice at 300 dpi. That is the only slow step, about four seconds." |
| The hosted link is asleep or broken | Switch to the laptop. "The link is on a free tier that sleeps; here is the same app locally." |
| The app shows an error box | Reload the page, reset, rerun. If it repeats, open the terminal and run `python -m invoice_desk.run samples\01_happy_path_furniture.pdf` to show the same pipeline without the UI. |
| You do not know the answer to a question | "I do not know. Here is how I would find out." Then point at the file where the answer would live. Never invent one. |

## Words to prefer

Say "holds it for a person" instead of "flags an exception". Say "the invoice bills more
than the PO has left" instead of "three-way match variance". Say "it does not guess"
instead of "null with a reason". They asked for an explanation a buyer can follow.
