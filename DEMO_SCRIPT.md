# Demo script

Two things are here: the narration for the 5-minute video, and the run order for the
live interview, including when to show a live Claude call. Both use the **Guided demo**
page and the same scenarios in the same order, so one rehearsal serves both.

The guide asks for the happy path running live, at least one edge case, narration of
what happens and why, five minutes at most, no slides and no editing. It also says the
reader is a non-technical buyer. So the words below are about money and risk, not code.

## Before you record or present (3 minutes)

1. `.venv\Scripts\python -m scripts.preflight` ends in `READY` with no warning. If it
   still warns that no answer is recorded for the letter invoice, do SETUP_WINDOWS.md
   step 6 first, or use the alternative words for step 9 below.
2. Start the app. Browser at 100% zoom, full screen.
3. On **Guided demo**, scroll to the bottom and open **Demo settings and admin**. AI
   assist mode: **FIXTURE**. "Process on the demo date": **on**. Close the panel.
4. Press **Start a fresh demo**.
5. **Pace of the live view**: **Normal** for the video, **Presentation** for the
   interview, where there is more time to talk over each stage.
6. Have a timer where you can see it. Do one full dry run first.

The two scanned steps (2 and 5) take about five seconds at the Read stage when the OCR
engine is installed. That pause is real work: two reads of the page. Say so.

---

## Part 1. The 5-minute video

About 600 words. Speak at a normal pace. While a step runs, let the stages be seen; you
do not have to fill every second.

| Time | Do | Say |
|---|---|---|
| **0:00** | Guided demo page, nothing run yet | "This is my build for problem statement one: invoice processing, from PDF to decision. An accounts payable team gets hundreds of invoices a month. For each one, somebody finds the purchase order, checks the numbers and decides. The costly mistakes are the quiet ones: paying twice, paying for more than was ordered, paying into the wrong account. My process makes that decision, shows its work, and stops for a person whenever it is not sure." |
| **0:30** | Press **Run step 1** | "A clean invoice first. Nine stages, each lighting up as it runs: register the file, read it, pull out the fields, check the invoice against itself, identify the vendor, match the purchase order, look for duplicates. Only then does it decide. Approved, and every box on the document is a value it read." |
| **0:52** | **Run step 2** | "The same for a scan. It reads the page twice, and trusts only what both reads and the arithmetic agree on." |
| **1:02** | **Run step 3** | "Edge case one. This purchase order is for twenty gas detectors. Earlier invoices billed sixteen, and this one bills five more. Nothing is wrong with the invoice itself. Only the running total shows it: twenty-one of twenty. So it is on hold, for a named owner, with one question." |
| **1:24** | **Run step 4** | "Edge case two. Same vendor, same items, same amount as an invoice booked two days ago, under a new number. A check on the number alone would pay this twice. Rejected, pointing at the original, and the reply to the vendor is already drafted." |
| **1:44** | **Run step 5** | "Edge case three. A scan with the invoice number left blank and no purchase order quoted. It does not invent a number. It suggests the best-fitting order, with its score, and a person confirms." |
| **2:02** | **Run step 6**, then **Run step 7** | "Edge case four. These prices include tax, and our order is before tax. It converts and compares. This one is just inside the tolerance: approved, with a note. This one is two rupees dearer per unit, just outside: held. Same rule, both sides of the line, and the limit is a number in a rules file." |
| **2:28** | **Run step 8** | "And a fraud check. Everything matches except the bank account. That is what payment fraud looks like, so it is held." |
| **2:38** | **Run step 9**. Scroll down to the document. | "Now the AI part. This invoice is written as a letter: no table, no labels. My rules will not guess. So Claude reads it and copies out each value with the sentence it came from. Then code checks every value against the page, character by character, and checks the arithmetic. The purple boxes are what Claude read. Claude reads. It does not decide. The decision is still the rules." |
| **3:10** | Click **Review queue**. On the first invoice type your name, press **Approve for payment** | "Holds wait here for a person. Watch what happens if I approve the split order anyway. Refused. The order is checked again at the moment of approval, so two approvals can never overbill it. And a changed bank account takes a call-back and two different people." |
| **3:32** | Click **Dashboard** | "The dashboard: what came in, how much went straight through, what is waiting and why. Every decision, the system's and the person's, goes into an audit trail, and this line re-checks the whole trail." |
| **3:48** | Click **Rules and data** | "None of the policy is in code. Every threshold and every action is a line in one file that a process owner can change." |
| **3:58** | Stay on the page | "How do I know it works? More than five hundred and fifty tests. An independent audit, whose findings I fixed. And forty-nine invoices I did not make with my own generator, in the layouts Tally and Zoho print, plus photographs. Forty-five reach the right answer, four go to a person, and none is approved wrongly. That last number is the one that matters." |
| **4:28** | Stop | "That is the process. Thank you." |

That leaves about thirty seconds of slack for the scans and for moving the mouse.

**Step 9 if you have not recorded Claude's answer.** The step ends in HOLD, which is the
correct outcome without AI. Say instead: "Now a hard one. This invoice is written as a
letter: no table, no labels. My rules will not guess, so they hold it and say exactly
what they could not read. This is where I use Claude: it reads the letter, code checks
every value against the page, and the rules still decide. On this machine that answer
is not recorded yet, so you are seeing the safe default."

**If you are running long:** drop the Rules and data step (3:48), then shorten the
close to its last two sentences. Do not drop an edge case or the AI step.

**If you are running short:** on the Review queue, also scroll to the bank-change
invoice, type a name and a call note, tick the box and press **Record first approval**.
Say: "First approval recorded. It stays on hold until a second person approves."

**If a step shows "NOT the expected decision":** the books were not fresh. In the video,
press Start a fresh demo and start over; it is five minutes.

---

## Part 2. The live interview

No slides. The process runs, they ask, you explain. Budget about ten minutes for the
runs and leave the rest for questions. Pace: **Presentation**.

**While saying hello:** Start a fresh demo. Check the sidebar says "AI assist: recorded
answers (no network)".

| # | Do | The one point to land |
|---|---|---|
| 1 | **Run step 1** (clean invoice). Scroll to the stages; open stage 3 (fields with sources) and stage 6 (line-by-line match). | Everything it read is traceable to a place on the page. Nothing is decided until stage 8. |
| 2 | **Run step 2** (scan). Open stage 3, "Two reads of the scan". | Two reads. A value no arithmetic can confirm is trusted only when both agree. |
| 3 | **Run step 3** (split PO). Open stage 6, the running total on the PO. | The invoice is fine on its own; only history shows the over-billing. That is why there is a ledger. |
| 4 | **Run step 4** (duplicate). Show the message to the vendor and its copy icon. | Rejected on the match itself. Room on the PO is not evidence that goods came twice; a second goods receipt would be. |
| 5 | **Run step 5** (scan, no number, no PO). | Missing stays missing. The blank box is checked on the picture before the vendor is asked. An inferred PO is a suggestion. |
| 6 | **Run steps 6 and 7** (tolerance). Open stage 6 on step 7: the conversion table. | Two rupees apart, opposite outcomes. Both a percentage and a rupee limit must hold. |
| 7 | **Run step 8** (bank change). | A perfect invoice can still be a fraud. |
| 8 | **Run step 9** (letter). Scroll to the document; open stage 3, the "AI assist (Claude)" table. | Claude copies; code verifies every value against the page; rules decide. The table shows what was accepted, what was rejected and what it cost. |
| 9 | **Run steps 10 and 11**. | The same file again returns the first record and books nothing twice. A quotation is refused with a reason. Bad input never crashes. |
| 10 | **Review queue**. Approve the split PO: refused, with the PO as it stands now. | The control holds when a person acts, not only when the system does. There is no override; amend the PO or reject. |
| 11 | Same page, bank change: your name, a call note, tick, **Record first approval**. Then type the same name again and try to approve. | Two different people. The first one's call record is what the second one approves. |
| 12 | **Dashboard**. Point at straight-through processing and the audit-trail line; open one record from the history. | History, status and outputs across runs. Any decision can be reconstructed later. |
| 13 | **Rules and data**, "What-if sandbox": sample 07, unit price tolerance to **0.90**, **Run both ways**. | The decision flips from approve to hold, and the only thing that changed was a number in the rules. |

### When to show a live Claude call

Row 8 above (the letter) replays a recorded answer. That is deliberate: a demo that
depends on a network call can break. Show a live call **after step 13**, or when they ask "is that
really the model?" or "where is the AI?". It resets the books, which is why it comes
last.

1. Say: "What you saw was a recorded answer, replayed. Here is the same thing live."
2. Guided demo, **Demo settings and admin**: AI assist mode **CACHED-LIVE**, then
   switch on **Ask Claude afresh on every run**. The sidebar now says "live Claude, a
   fresh answer on every run".
3. Press **Start a fresh demo**.
4. Go to **Process invoice**, choose **10 Invoice written as a letter**, press
   **Process this invoice**. Stage 3 takes a second or two longer.
5. Open stage 3. In the "AI assist (Claude)" table point at three lines: **Answer**
   ("live call to the Claude API"), **Accepted** and **Tokens and cost**.
6. Say: "Same values, same checks, same decision, and it cost a fraction of a cent. If
   that call had failed, the recorded answer would have been used and the record would
   say so. There is a cap per invoice and a cap per day."
7. Set the mode back to **FIXTURE** before you go on.

Do this only if you rehearsed it the same day. If you did not, say what is true: "The
live call works the same way; I keep the demo on recorded answers so that it cannot
break. I can show you the recording and the request that produced it." Then open a file
in `fixtures\llm\` and run
`.venv\Scripts\python -m scripts.record_llm_fixtures --dry-run --show-request`.

### Keep these ready in case they ask

- **"Upload something of mine."** Guided demo, **Try your own invoice**. A real invoice
  is held because its vendor is not on this made-up vendor list. Say that before you
  press the button: "It will hold it as an unknown vendor, which is what should happen."
  Then open stage 3 to show what it read from their document.
- **"Upload something that is not an invoice."** Process invoice, "From the sample
  inbox": samples 11 to 18 are a quotation, a newsletter, and corrupt, empty,
  password-protected, oversized, renamed and truncated files. Each ends in a decision
  with a reason.
- **"What if Claude gets it wrong?"** Open stage 3 on the letter. Every value has its
  quote. A value whose quote is not on the page, or that the quote does not state, is
  dropped, and there are tests for each case, including one where two digits are
  swapped.
- **"How did it do on invoices you did not make?"** `docs\robustness_report.md`: 45 of
  49 right, 4 held, none approved wrongly. Say what the four are: photographs.
- **"Run them all."** Process invoice, "Process the whole sample inbox in one go":
  eighteen runs, each compared with its documented outcome.
- **"Show me it is real."** A terminal: `.venv\Scripts\python -m invoice_desk.run --all`,
  or `-m pytest`.

## If something goes wrong live

| What you see | What to do, and what to say |
|---|---|
| A step shows "NOT the expected decision" | Read the reasons on screen out loud; they say exactly why. Then **Start a fresh demo** and run it again. Do not guess. |
| "These books already hold records from outside the guided demo" | Press **Start a fresh demo**. "I had processed something by hand; the demo wants a clean start." |
| A sample says "already received" | "That is the re-submission control: the same file is never booked twice." Then start fresh if you need the run itself. |
| The scan is slow | "It is reading the page twice. That is the only slow step, about five seconds." |
| The live Claude call fails | The record says the stored answer was used. "The network call failed, so it fell back to the recorded answer. That is the designed behaviour." Switch back to FIXTURE. |
| The hosted link is asleep or broken | Switch to the laptop. "The link is on a free tier that sleeps; here is the same app locally." |
| The app shows an error box | Reload the page, start fresh, rerun. If it repeats, open the terminal and run `.venv\Scripts\python -m invoice_desk.run samples\01_happy_path_furniture.pdf` to show the same pipeline without the UI. |
| You do not know the answer to a question | "I do not know. Here is how I would find out." Then point at the file where the answer would live. Never invent one. |

## Words to prefer

Say "holds it for a person" instead of "flags an exception". Say "the invoice bills more
than the PO has left" instead of "three-way match variance". Say "it does not guess"
instead of "null with a reason". Say "Claude reads, code checks, rules decide" instead
of "LLM extraction with grounding". They asked for an explanation a buyer can follow.
