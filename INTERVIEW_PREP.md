# Interview prep

The toughest questions this build is likely to get, with answers grounded in
[DECISIONS.md](DECISIONS.md) (the D-numbers) and in what the code does. The answers are
written to be said out loud: short first, detail only if they ask.

Read the first section before anything else.

## 0. Before you defend it, own it

The guide says: use AI tools, be deliberate about it, and be able to "speak to it
fluently". This project was built with an AI coding assistant doing most of the typing.
That is allowed. What is not survivable is being asked "why does it do that?" and not
knowing. So:

**If they ask how you built it, say what is true.** Something like: "I used an AI coding
assistant heavily, as the guide suggests. I gave it the brief and my three earlier
projects, and it ran research, design, build and test loops. I can walk you through any
decision and any file." Then make that last sentence true. Do not claim you wrote every
line by hand, and do not claim you tested things that the README lists as not verified.

**Two hours that make it yours:**

1. Read `rules.yaml` top to bottom (10 minutes). It is the whole policy.
2. Read `invoice_desk/decide.py` (10 minutes). It is 150 lines and it is the decision.
3. Read `invoice_desk/pipeline.py`, only the `iter_run` method and the stage list.
4. Read `invoice_desk/po_match.py` and `invoice_desk/duplicates.py`: the AP logic.
5. Read `invoice_desk/second_read.py`: the two reads of a scan, 100 lines.
6. Skim `tests/test_decide.py` and `tests/test_po_match.py`: the tests say what the
   code promises.
7. Do these five things with your own hands:
   - Change `unit_price.pct` in `rules.yaml` from 1.0 to 0.1, reload the app, run
     sample 07, watch it hold. Change it back.
   - Run sample 01 twice. Explain the second result.
   - Open the database (`data\runtime\invoice_desk.db`) with any SQLite viewer, edit one
     character in a row of the `receipts` table, reload the Dashboard, and watch the
     audit-trail line turn red. Then press Reset demo data.
   - Run `.venv\Scripts\python -m invoice_desk.run samples\04_E1_split_po_overbilling.pdf`
     and read the output line by line.
   - Upload any PDF of your own and explain every reason it gives.
8. Read `DECISIONS.md` twice. Every answer below comes from it.

**Numbers to know cold**

| | |
|---|---|
| Stages | 9 |
| Outcomes | 3: approve, hold, reject. Strictest wins |
| Reason codes in `rules.yaml` | 55 (7 reject, 39 hold, 9 note) |
| Price tolerance | 1% **and** Rs 500 on the line, both must hold |
| Quantity over PO | none allowed, counting earlier invoices |
| PO value tolerance | 0.5% **and** Rs 500, on the running total |
| Auto-approval ceiling | Rs 10,00,000 |
| Duplicate window | 15 days |
| PO inference | at least 75% and 15 points clear of the runner-up, and still never auto-accepted |
| Samples | 10 invoices and 4 bad inputs |
| Automated tests | 364 |
| Unseen text layouts | 200 of 200 read and approved untouched |
| Unseen scanned layouts (held out) | 72: 64 untouched, 3 held although read correctly, 5 misread and held, **0 misread and approved** |
| Speed | about 50 ms for a text PDF; about 4 seconds for a scanned page (two OCR reads) |

---

## A. Design and judgment

**1. "Why didn't you just give the invoice to an LLM?"** (D02, D03)
"Because the guide's hardest requirement is that it runs live, and because this is
money. Labels, page geometry and arithmetic read these invoices the same way every
time, in fifty milliseconds, offline, for free. A model is more general, but it varies
between runs, it needs a key and a network, and it can be confidently wrong about a
digit. So the rules read, arithmetic checks the read, and a model is brought in only
for what the rules cannot read, and then its answer has to prove itself."

**2. "So where is the AI?"**
"In three places, each where it earns its place. OCR reads the scans. An LLM reads
documents the rules cannot, such as an invoice written as a letter, and every value it
returns must come with a quote that is on the page word for word and that states the
value; otherwise it is dropped. And I used AI to build and test the whole thing. What
is deliberately not AI is the decision: approve, hold or reject is a pure function of
the findings and a rules file, so it is identical every time and I can show you why."

**3. "How do you stop the model making things up?"** (D03, D31)
"It never gets the last word. It must return a quote for each value. I check that the
quote is on the page letter for letter and digit for digit, and that the value is
stated in that quote. A first version of that check was fuzzy, and one of my own tests
got a quote through with two digits swapped. The check is exact now. Accepted values
only fill gaps; they never overwrite something the rules read. And the model never sees
the PO register, so it cannot bend an invoice toward a match."
*If pressed:* "One honest gap: the model's description of a line item is free text. A
made-up description fails the PO match and holds the invoice, so it is caught one stage
later, and there is a test for exactly that."

**4. "You have not actually called a model, have you?"**
"Not in the build environment; it had no model access. That path is tested through a
stub that stands exactly where the HTTP call goes, and the recordings folder ships
empty because a hand-written recording would be a fake. I recorded a real response
with my own key on [date] and it replays here with no network." *(Only say the last
sentence if you did it. If you did not, stop after "a fake" and say "so sample 10 shows
the rules holding it, which is the safe behaviour.")*

**5. "Why Streamlit and one app? Why not n8n, or a proper front end?"** (D01)
"It has to run on a laptop in three commands, host free, and not break live. One
process, one file database, no services. I scored four designs; a low-code workflow
could not be tested offline and depends on several accounts being up, and a hand-built
front end is more code to defend for the same information on screen. The engine does
not import the UI, so a different front end is a contained change."

**6. "Why are the rules in a YAML file?"** (D04)
"Because the person who owns the policy is not a programmer. Each finding has a code;
the file says what that code means: reject, hold or note, and who owns it. Change a
line and the behaviour changes with no code edit. The file's hash goes into every
receipt, so an auditor can tell which version of the rules produced a decision. There
is a sandbox page that reruns a sample with different numbers to show it."

**7. "Why three outcomes and not a confidence score?"** (D05)
"A score hides the reason, and somebody then has to pick a cut-off that nobody can
defend. Three outcomes with named reasons tell the person what to do next. Strictest
wins, and when it rejects, the things that would have held it are still listed."

**8. "What did you choose not to build?"** (D29)
"E-invoice IRN and QR validation, GSTR-2B matching, TDS, GL coding, an approval matrix,
a real mailbox, invoices across several POs, applying credit notes, foreign currency,
and actually paying. Each is a known next step. I cut them because the guide says a
process that handles a few scenarios well beats one that half-handles ten."

## B. The accounts payable logic

**9. "Walk me through matching an invoice to a PO."** (D06 to D10)
"Find the PO: quoted on the invoice, or inferred. Pair each invoice line with a PO line
by description. Compare before tax, because PO prices are before tax; if the invoice
prices include GST I convert them and show the conversion. Check the unit price against
both limits. Check quantity: what earlier invoices billed plus this one must fit what
was ordered, and what was received if receipts are tracked. Then check the PO as a
whole: the running total must fit the PO value."

**10. "Why both a percentage and a rupee limit?"** (D07)
"A percentage alone lets a twenty-lakh line drift by twenty thousand rupees. A rupee
limit alone lets a two-hundred-rupee item double. Requiring both is how SAP's price
tolerance works. Paying less than the PO is only noted."

**11. "How do you handle one PO split over several invoices?"** (D09)
"With a ledger. Approved invoices post and use up PO balance, line by line. Held
invoices are pending and do not use it until someone approves them. So the third
invoice is judged against what the first two left, and the screen shows the running
total invoice by invoice. Without history that invoice looks perfect."

**12. "How do you detect duplicates?"** (D11, D35)
"Three tiers. The same file, by fingerprint: reject. The same invoice number for the
same vendor, ignoring punctuation, case and leading zeros: reject, if the first one was
booked. The same vendor, amount and line items under a different number within fifteen
days: that is the dangerous one. If the PO has no room left, nothing more can be owed,
so reject. If it has room, it might be a second delivery, so it asks, and if goods
receipts show both deliveries arrived it does not even ask."
*Follow-up, "what if the vendor resends a corrected invoice?":* "If the first is still
on hold, a different file with the same number is treated as a possible corrected copy.
It is held, and it cannot be approved until the first one is decided. Rejecting a
correction as a duplicate would be the expensive mistake."

**13. "The PO is not on the invoice. Why not auto-match when you are 98% sure?"** (D10)
"Because the cost of being wrong is not one invoice. Posting to the wrong PO corrupts
that PO's balance for every later invoice. So the best candidate is shown with its
score and the runner-up, and a person confirms. If the best two are close, it suggests
nothing."

**14. "Why hold a perfect invoice because the bank account changed?"** (D13)
"Because that is exactly what payment-redirection fraud looks like: everything matches
except where the money goes. The hold tells the reviewer to call the number already on
file, not one from the invoice. The review screen will not approve it until that is
confirmed, and a confirmed account is remembered so the next invoice goes through."

**15. "Is this two-way or three-way matching?"** (D08)
"Three-way where the data exists. If the PO line carries a received quantity, billing
ahead of receipt is held. Where the company does not record receipts, it is two-way.
I did not build a goods-receipt module; the brief's procurement system does not have
one."

**16. "What happens after a hold?"** (D18, D36, D40)
"It goes to the review queue with an owner inside the company and one specific
question. If the vendor has to act, a note to the vendor is drafted for a person to
send. A named person approves or rejects, and that decision is written into the same
audit trail as the machine's. A document the system never booked, such as a dollar
invoice, cannot be approved there; it can only be marked as handled by hand."

## C. Reliability and proof

**17. "How do you know the reading is right?"** (D15, D30, D38)
"Three ways. Arithmetic: quantity times price, the sum of the lines, tax, the total and
the amount in words all have to agree. For scans, two reads in two image treatments,
and anything arithmetic cannot check must agree in both. And a scorecard: I generate
invoices in layouts the reader was not built on, add a matching PO, and run the whole
pipeline. Two hundred text layouts, all approved untouched. Seventy-two held-out scans:
sixty-four untouched, three held although they were read correctly, five misread and
held, none misread and approved."

**18. "A vendor you have never seen sends a new layout. What happens?"**
"There are no per-vendor templates, so nothing needs configuring. If the labels and the
table are recognisable it reads it. If not, fields come back empty with a reason and
the invoice is held; it does not guess. That is the scorecard's job to measure."

**19. "What failure worries you most?"**
"A misread that looks consistent. Arithmetic protects the amounts. The value with no
arithmetic behind it is the invoice number on a scan: if both reads got it wrong in the
same way, a wrong number would be recorded, which weakens duplicate control later. On
my test scans both invoice-number misreads were caught because the reads disagreed, but
those scans are synthetic. In production I would verify the number against the
e-invoice IRN, which takes OCR out of it."

**20. "What if OCR or the model is down during the demo?"** (D02, D14)
"Nothing on the critical path needs a network. OCR is local. If the engine is missing,
the two bundled scans run from OCR output recorded at build time, and the screen says
'recorded'. The model is optional, and its responses replay from disk."

**21. "What if a stage crashes?"** (D32)
"It cannot crash the run. An unexpected error becomes a finding that holds the invoice
for a person. I also closed the nastier case: if posting fails after the decision was
'approve', the decision is recomputed with that failure in it, so an approval can never
stand without a verified ledger entry. There are tests that break the ledger on purpose."

**22. "How do I know a decision was not changed afterwards?"** (D19)
"Every decision writes a receipt whose hash covers its content and the previous
receipt's hash, plus the hash of the rules file and the master data. The dashboard
recomputes the whole chain. Tests edit a receipt, forge a receipt with a matching hash,
and delete one; each is detected."
*If pressed:* "Someone with database access could rebuild the entire chain. Stopping
that needs a key held elsewhere or an external anchor. I left it out as a demo risk."

**23. "How did you test it?"**
"364 automated tests. Every rule from both sides of its threshold: a price exactly one
percent over passes, a paisa more fails. The decision function is checked for purity
and that adding a problem never makes the outcome more lenient. Every sample end to
end. Hostile and broken inputs: corrupt, empty, password-protected, twenty-five pages,
random bytes, a page two hundred inches wide, HTML in a vendor name. The UI is driven
headlessly. And when a test failed, the code was fixed, not the test."

**24. "Tell me about a bug you found."**
Pick one you can explain from `PROGRESS.md`:
- "The scorecard showed seven valid invoices in sixty rejected as 'not a tax invoice'.
  The label 'Purchase Order No.' was being read as the document's title. Field-level
  tests never saw it, because every field was read correctly. Only running the whole
  pipeline end to end exposed it."
- "A reviewer could type an invoice number that was already on record. Now refused."
- "An approval could survive a failed ledger write. Now it cannot."

## D. Scale and production

**25. "Hundreds of invoices a month. Would this hold? What about fifty thousand?"**
"Hundreds a month is nothing: a text invoice takes fifty milliseconds. At fifty
thousand the engine stays and the shell changes: a queue and workers instead of one
process, Postgres instead of SQLite, OCR on its own workers because it is the slow
step, and the two CSV loaders replaced by ERP queries."

**26. "How would you connect it to a real mailbox and ERP?"** (A2, A3)
"Intake is one function that takes bytes and a file name, so a mailbox poller calls it
for each attachment. Vendors and POs come from two loader functions, which become ERP
queries. Posting writes to a local ledger; that becomes a posting call followed by the
same read-back check."

**27. "This is on a public link. What about a malicious PDF?"** (D41)
"Uploads are untrusted. Text from a document is escaped before it reaches the page;
there is a test with script tags in a vendor name. Files over fifteen megabytes or
twenty pages are held. A page with absurd dimensions is rendered at a capped size. The
hosted demo stores uploads on the host, so the page says not to upload real invoices."

**28. "What would you build next?"**
"Mailbox intake first, because that is where the work starts. Then e-invoice
verification, because it removes OCR risk on the fields that matter most. Then credit
notes and an approval matrix."

## E. Pushback you should expect

**"This is over-built for a case study."**
"The guide says the edge cases show how well you understand the problem, and that a
demo that breaks live is hard to recover from. Most of the volume here is tests and
checks that make the four edge cases behave and keep the demo boring."

**"It is rule-based. Rules do not generalise."**
"For reading, I measured that instead of arguing it: two hundred unseen text layouts,
all read. Where rules stop, such as a letter with no table, the model takes over with a
citation check. For deciding, I want rules. Nobody wants a payment decision that
changes between runs."

**"Your scans are synthetic."**
"Yes, and the scorecard says so. They are tilted, blurred and noisy, but real scans
have stamps, handwriting and folds. The design choice that matters is what happens
when reading fails: it holds. I would rerun the scorecard on a client's real scans in
the first week."

**"What are the limits?"**
Say them without being asked twice: Indian GST invoices in English and rupees; one PO
per invoice; no mailbox; CSV master data; the LLM path proven by stub and by one
recorded call; not load-tested.

## F. If you do not know

"I do not know. Here is where I would look." Then name the file. Guessing about your
own build is the one unrecoverable answer.
