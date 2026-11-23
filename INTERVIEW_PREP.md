# Interview prep

The toughest questions this build is likely to get, with answers grounded in
[DECISIONS.md](DECISIONS.md) (the D-numbers) and in what the code does. The answers are
written to be said out loud: short first, detail only if they ask.

Read section 0 before anything else. Sections C, D and E are new in round 2: the AI
layer, the audit and what it changed, and the results on invoices I did not generate.

## 0. Before you defend it, own it

The guide says: use AI tools, be deliberate about it, and be able to "speak to it
fluently". This project was built with an AI coding assistant doing most of the typing,
then audited by a second one, then rebuilt against that audit. That is allowed. What is
not survivable is being asked "why does it do that?" and not knowing. So:

**If they ask how you built it, say what is true.** Something like: "I used an AI
coding assistant heavily, as the guide suggests. I gave it the brief and my three
earlier projects, and it ran research, design, build and test loops. Then I had the
result audited independently on my own laptop, got a list of real bugs back, and ran a
second round against that list with a rubric. I can walk you through any decision and
any file." Then make that last sentence true. Do not claim you wrote every line by
hand, and do not claim anything the README lists as not verified.

**That story is your best answer to "be deliberate about AI".** You did not accept the
first build. You had it attacked, you kept the report in the repository
(`docs/AUDIT_REPORT.md`), and every finding has a test that failed first. Say that.

**Three hours that make it yours:**

1. Read `rules.yaml` top to bottom (15 minutes). It is the whole policy, including the
   trust levels for values a model read.
2. Read `invoice_desk/decide.py` (10 minutes). It is the decision.
3. Read `invoice_desk/pipeline.py`: only the stage list and `iter_run`.
4. Read `invoice_desk/po_match.py`, `invoice_desk/duplicates.py` and
   `invoice_desk/review.py`: the AP logic, and the two controls the audit forced.
5. Read the long comment at the top of `invoice_desk/llm.py`, then the function
   `_apply`. That is everything the model is allowed to do, in about a hundred lines.
6. Read `docs/AUDIT_REPORT.md` and `CHANGELOG.md` side by side.
7. Skim `tests/test_round2_audit.py`: each test is one audit finding, reproduced.
8. Do these with your own hands:
   - Change `unit_price.pct` in `rules.yaml` from 1.0 to 0.9, reload the app, run sample
     07, watch it hold. Change it back.
   - Run step 1 of the guided demo, then process the same sample again on the Process
     page. Explain the notice you get.
   - In the Review queue, approve the split-PO invoice. Read the refusal out loud.
   - Do the bank change with one name, then try the same name again.
   - Open the database (`data\runtime\invoice_desk.db`) with any SQLite viewer, edit one
     character in a row of the `receipts` table, reload the Dashboard, and watch the
     audit-trail line turn red. Then press Start a fresh demo.
   - Record Claude's answers (SETUP_WINDOWS.md step 6) and open the JSON file it wrote.
     Then open stage 3 of sample 10 and match each value to its quote.
   - Upload any PDF of your own and explain every reason it gives.
9. Read `DECISIONS.md` twice. Every answer below comes from it.

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
| Bank change | 2 different approvers |
| Samples | 10 invoices and 8 bad inputs |
| Guided demo | 11 scenarios |
| Automated tests | 561 (364 before the audit) |
| The tolerance pair | 0.95% and Rs 488 on the line: approved. 1.01% and Rs 515: held. Printed prices two rupees apart |
| Independent test set | 49 documents: 45 right, 4 held, **0 approved wrongly**. Before I improved the reader: 13 right |
| The part of it I never opened | 12 text PDFs: 12 right (6 before). 5 photographs: 3 right, 2 held |
| Older scorecard, my own generator | text 200 of 200. Scans: 86 of 96 straight through, 6 held although read correctly, 4 misread and held, 0 misread and approved |
| Claude | default model `claude-haiku-5-5`; caps of $0.05 per invoice and $1 per day |
| Trust in a value Claude read | 0.85 verified, 0.75 quote only, 0.60 from a scan, 0.50 if the model was unsure. The line for approval is 0.70 |
| Speed | well under a second for a text PDF; about five seconds for a scanned page (two OCR reads) |
| My rubric | 19 of 40 before the audit round, 38 after; the last two points are a Windows run and a real recording |

---

## A. Design and judgment

**1. "Why didn't you just give the invoice to an LLM?"** (D02, D03, D45)
"Because this is money, and because it has to run live. Labels, page geometry and
arithmetic read these invoices the same way every time, in a fraction of a second,
offline, for free. A model is more general, but it varies between runs, it needs a key
and a network, and it can be confidently wrong about a digit. So the rules read,
arithmetic checks the read, and a model is brought in only for what the rules cannot
read. Even then its answer has to prove itself against the page."

**2. "Why Streamlit and one app? Why not n8n, or a proper front end?"** (D01)
"It has to run on a laptop in three commands, host free, and not break live. One
process, one file database, no services. I scored four designs. A low-code workflow
could not be tested offline and depends on several accounts being up. A hand-built
front end is more code to defend for the same information on screen. The engine does
not import the UI, so a different front end is a contained change."

**3. "Why are the rules in a YAML file?"** (D04, D55)
"Because the person who owns the policy is not a programmer. Each finding has a code;
the file says what that code means: reject, hold or note, and who owns it. Every
threshold is in that file and none has a default in code, so a typo stops the app
instead of running on a number nobody chose. The file's hash goes into every audit
entry, so an auditor can tell which version of the rules produced a decision."

**4. "Why three outcomes and not a confidence score?"** (D05)
"A score hides the reason, and somebody then has to pick a cut-off that nobody can
defend. Three outcomes with named reasons tell the person what to do next. Strictest
wins, and when it rejects, the things that would have held it are still listed."

**5. "Is it deterministic? Same invoice, same answer?"** (D04, D50, D25)
"Stated carefully: the same findings and rules always give the same decision, because
the decision is a pure function. The same file, rules, master data, business date and
recorded AI answers give the same record on any machine. And the same file sent again
returns the first record instead of being processed twice. My first version claimed
this too loosely. The audit found four holes: a key in the environment switched live
calls on, a second run of the same file was rejected as a duplicate, date checks used
the real clock, and the model could change an outcome. All four are closed or stated."

**6. "What did you choose not to build?"** (D29)
"E-invoice IRN and QR validation, GSTR-2B matching, TDS, GL coding, an approval matrix,
a real mailbox, invoices across several POs, applying credit notes, foreign currency,
single sign-on, and actually paying. Each is a known next step. I cut them because the
guide says a process that handles a few scenarios well beats one that half-handles ten."

## B. The accounts payable logic

**7. "Walk me through matching an invoice to a PO."** (D06 to D10, D59)
"Find the PO: quoted on the invoice, or inferred. Pair each invoice line with a PO line
by description. Compare before tax, because PO prices are before tax; if the invoice
prices include GST I convert them and show the conversion. Check the unit price against
both limits. Check quantity: what earlier invoices billed plus this one must fit what
was ordered, and what was received if receipts are tracked. Two invoice lines for one
PO line are added up first. Then the PO as a whole: the running total must fit its
value."

**8. "Why both a percentage and a rupee limit?"** (D07, D61)
"A percentage alone lets a twenty-lakh line drift by twenty thousand rupees. A rupee
limit alone lets a two-hundred-rupee item double. My two tolerance samples show it:
the printed prices are two rupees apart. One is 0.95% and Rs 488 over on the line and
is approved with a note. The other is 1.01% and Rs 515 and is held."

**9. "How do you handle one PO split over several invoices?"** (D09, D42)
"With a ledger. Approved invoices post and use up PO balance, line by line. Held
invoices do not use it until someone approves them. So the third invoice is judged
against what the first two left, and the screen shows the running total invoice by
invoice. And when a person approves a held invoice, the PO is checked again at that
moment, because two holds can each fit the PO and overbill it together."

**10. "How do you detect duplicates?"** (D11, D44, D50)
"Three tiers. The same file, by fingerprint: the first answer is returned and nothing
is booked twice. The same invoice number for the same vendor, ignoring punctuation,
case and leading zeros: reject, if the first one was booked. The same vendor, lines and
total under a different number within fifteen days: reject, and the only thing that
clears it is goods receipts showing a second delivery."
*Follow-up, "why reject and not ask?":* "My first version asked whenever the PO still
had room. The audit pointed out that my duplicate sample was rejected only because its
PO happened to be full. Room on a PO is not evidence that goods came twice. A client
who prefers to hold changes one word in the rules file."
*Follow-up, "what if the vendor resends a corrected invoice?":* "If the first is still
on hold, a different file with the same number is treated as a possible corrected
copy. It is held, and it cannot be approved until the first one is decided."

**11. "The PO is not on the invoice. Why not auto-match when you are 98% sure?"** (D10)
"Because the cost of being wrong is not one invoice. Posting to the wrong PO corrupts
that PO's balance for every later invoice. So the best candidate is shown with its
score and the runner-up, and a person confirms. If the best two are close, it suggests
nothing."

**12. "Why hold a perfect invoice because the bank account changed?"** (D13, D43)
"Because that is exactly what payment-redirection fraud looks like: everything matches
except where the money goes. The hold tells the reviewer to call the number already on
file, not one from the invoice. Then it takes two different people: the first records
the call and may attach the vendor's letter, the second reads that record and
approves. Only then is the account remembered."
*If pressed, "how do you know they are different people?":* "In this demo I do not:
there is no login, so names are typed, and the check compares names. In a deployment
the name comes from the company's single sign-on and the same check compares accounts.
I would rather say that than pretend a text box is an identity."

**13. "Is this two-way or three-way matching?"** (D08)
"Three-way where the data exists. If the PO line carries a received quantity, billing
ahead of receipt is held, and a second delivery is what clears a repeat invoice. Where
the company does not record receipts, it is two-way."

**14. "What happens after a hold?"** (D18, D36, D40, D42)
"It goes to the review queue with an owner inside the company and one specific
question. The queue shows the purchase order as it stands now. If the vendor has to
act, a message is drafted and ready to copy. A named person approves or rejects, and
that goes into the same audit trail as the system's decisions. A document the system
never booked, such as a dollar invoice, cannot be approved there; it can only be closed
as processed manually."

## C. The AI layer

**15. "So where is the AI?"** (D45 to D49)
"In three places, each where it earns its place. OCR reads scans and photographs.
Claude reads documents my rules cannot, such as an invoice written as a letter, and can
reword the message to a vendor. And I used AI to build, audit and test the whole thing.
What is deliberately not AI is the decision: approve, hold or reject is a pure function
of the findings and a rules file."

**16. "What exactly does Claude do, step by step?"** (D45, D46)
"When the rules reader leaves a required field or the item table empty, the text of
the page goes to Claude with a strict output format. Every value must come back as text
copied from the page, with the sentence it was copied from. Claude is not asked to
convert a date, add up a total or judge anything. Then my code checks four things: the
sentence is on the page character for character; the value is a whole token inside
that sentence; an invoice or PO number stands beside words that say what it is; and the
invoice's own arithmetic agrees. Anything that fails is dropped. A value the rules
already read is never overwritten."

**17. "How do you stop it making things up?"** (D46, D31)
"It never gets the last word. A value with no quote on the page is dropped. A quote
that is on the page but does not state the value is dropped. I have tests for each,
including a real sentence with two digits swapped in the value, and a piece of a longer
invoice number offered as the number. And the model never sees the PO register, so it
cannot bend an invoice toward a match."
*If pressed:* "One limit I know about: a line item's description is free text. A
made-up description fails the PO match and holds the invoice, so it is caught one stage
later, and there is a test for exactly that."

**18. "Why not let the model decide, or at least give a confidence?"** (D49)
"The decision has to be the same tomorrow and explainable from a file a person can
read. A model's confidence is not evidence. In my build it can only lower trust: if
Claude says it is unsure, or the text itself came from OCR, the value is below the line
at which a person checks, and the invoice is held."

**19. "What other uses of AI did you consider and reject?"** (D49)
"A model-written summary of the decision: it would paraphrase a text that is already
plain and exact, with a chance of being wrong. Model-based duplicate detection: vendor,
amount, lines and dates are comparisons, and comparisons are exact in code. Chat with
your invoices: it demos well and controls nothing. I kept two uses: reading what rules
cannot, and rewording a vendor message, where code checks that no figure, date or
reference changed and a person sends it anyway."

**20. "How do you keep it reproducible and cheap?"** (D47, D48)
"An answer is stored under the file's fingerprint, the prompt version and the model.
The demo replays recorded answers: no key, no network, the same result every time. In
live mode it calls only when no stored answer exists, then keeps it. There is a
timeout, a retry limit, a cap per invoice checked before the call, and a cap per day. A
key on its own switches nothing on."

**21. "What if the API is down, slow, or returns rubbish?"** (D48, D63)
"The model's part is skipped, the reason is shown, and the rules decide alone, which
for a letter means a hold. That covers a refusal, a truncated answer, broken JSON, a
bad key, a rate limit and no network; there is a test for each. A run never fails
because the API did."

**22. "Have you actually called the model?"**
Say only what is true on the day.
- *If you recorded (SETUP_WINDOWS.md step 6):* "Yes. I recorded real answers with my
  own key on [date]; they are in `fixtures/llm` with the request id and token counts.
  The demo replays them, and I can make a live call now if you want to see one." Then
  DEMO_SCRIPT.md, "When to show a live Claude call".
- *If you did not:* "The build environment had no API access, so that layer is tested
  with the real SDK against a stand-in for the API, including every failure. No
  recording ships, because a hand-written one would be a fake. So sample 10 shows the
  rules holding it, which is the safe behaviour." Do not blur this.

**23. "Which model, and why?"**
"Claude Haiku by default: copying fields out of a page is a small, fast task and it is
the cheapest current model. A stronger one can be set as the main model or as a second
try when the first answer gives nothing usable. The model name is a setting, and a
stored answer belongs to the model that made it."

**24. "What leaves the building?"**
"In live mode, the text of that one document goes to the Claude API, up to a size
limit, and only when the rules could not read it. Nothing from the vendor master, the
PO register or the ledger is ever sent. In the default mode nothing is sent at all. For
a client with stricter rules, the mode is OFF and the letter is handled by a person."

## D. The audit, and what it changed

**25. "Tell me about a bug you found."** Pick one you can explain end to end.
- "The audit approved two held invoices from my review queue and overbilled a PO by
  four units, with no warning. That was the very risk my first edge case exists to
  catch; I had controlled the system's decision and not the person's. Now the PO is
  checked again at the moment of approval, an approval that would overbill is refused
  with the numbers, and there is no override." (D42)
- "If saving the audit receipt failed after the ledger write, the ledger said posted
  and the decision said hold. Now the ledger entry, the receipt and the record are one
  transaction." (D51)
- "My screen said a bank change needs a second person, and the code let one person do
  it. Now it takes two." (D43)
- "My 'tolerance edge' sample was at 0.11% and 1.56% against a 1% limit. It is now
  0.95% and 1.01%." (D61)
- From my own look at the finished screen: "The second approver of a bank change was
  shown the first approver's name already typed in. The headless UI test passed; only
  a real browser showed it."

**26. "What did the reviews get wrong, or what did you not take from them?"**
"Mostly they were right. Three places where I chose differently, all from the UI
walkthrough. It suggested a 'simulation mode' for re-running a sample; I made a re-sent
file return its first record instead, because a second code path that does not write
is not the process I am showing. It suggested a throughput figure per hour for the
dashboard; I show handling time, because I have not measured throughput. And for bank
changes it suggested a second reviewer above an amount; I require one always, because
the fraud does not depend on the amount."

**27. "Your first version passed 364 tests and still had these bugs. Why should I
trust 561?"**
"You should trust them for what they cover and no more. The 364 were green and the
audit found what they did not cover: what a person does after a hold, and what happens
when a write fails halfway. So I wrote a rubric with pass tests before round 2, scored
the first build at 19 of 40, and added a test for every finding before fixing it. The
tests are not the proof on their own. The audit report in the repository is the other
half."

**28. "How do I know a decision was not changed afterwards?"** (D19, D53)
"Every decision writes an entry whose fingerprint covers its content and the entry
before it, plus the fingerprints of the rules file and the master data. The dashboard
re-checks the whole trail. Tests edit an entry, forge one with a matching hash, and
delete one; each is detected."
*If pressed:* "Someone who can rewrite the whole database could rebuild the chain. Two
things close that: a signing key held outside the database, which is a setting here,
or noting the latest fingerprint somewhere this system cannot write. The dashboard
says so."

## E. Does it work on invoices you did not make?

**29. "Your test invoices come from your own generator."** (D56)
"That was the audit's fairest point, so I built a second set with other tools:
headless Chrome, wkhtmltopdf, LibreOffice and LaTeX, in the layouts Tally, Zoho and
Busy print, plus twelve photographs of printed pages with perspective, uneven light
and blur. Forty-nine documents. Thirty-four should be approved and fifteen should be
stopped, for eleven different reasons."

**30. "And?"**
"On the first run my reader got 13 of 49 right. It held the other 36. It approved none
wrongly. After I improved it, 45 of 49 are right, 4 are held, and still none is
approved wrongly. The four are photographs."

**31. "You tuned on your test set."**
"On part of it, and I kept the other part closed. Thirty-two documents I opened and
debugged. Seventeen, made with different tools and layouts, I never opened. On those,
text PDFs went from 6 of 12 to 12 of 12 without my looking at one, which is the
evidence that the fixes generalise. Photographs went to 3 of 5. One caveat I wrote
into the report: the first full run printed the outcome and reason codes of the closed
documents too, so I saw that much. I opened none and changed nothing to fix one."

**32. "13 of 49 is a bad first result."**
"Yes, and it is the most useful number in the project. It showed that my reader was
fitted to my own layouts. What it also showed is the property I care about most: on
36 documents it could not read properly, it stopped 36 times and approved zero. A
reader that fails by asking a person is one you can put in front of real invoices and
improve. The fixes were general rules, not patches: tax rows inside the item table,
totals chosen by arithmetic, straightening a photographed page."

**33. "What about the four that still fail?"**
"Two I examined: one read of the scan got a single character wrong, an I read as a 1 in
an invoice number, a date missing a digit. The two reads disagreed, so it went to a
person. That is the control working, and I did not add a rule to guess. The other two
are in the closed part; the report shows they were held because values could not be
read."

**34. "What would a real invoice from my company do?"**
"It would be held, and that is correct. Your vendor is not on this made-up vendor list
and your PO is not in the register, so it says exactly that. What you can judge is
stage 3: what it read from your document and where."

**35. "What failure worries you most?"**
"A misread that looks consistent. Arithmetic protects the amounts. The value with no
arithmetic behind it is the invoice number on a scan: if both reads got it wrong in the
same way, a wrong number would be recorded, which weakens duplicate control later. In
production I would check it against the e-invoice IRN, which takes OCR out of it."

## F. Reliability and scale

**36. "What if a stage crashes?"** (D32, D51)
"It cannot crash the run. An unexpected error becomes a finding that holds the invoice
for a person. Posting is all or nothing. If nothing can be written at all, the run
still ends as a hold that says it could not be recorded."

**37. "What if OCR or the model is down during the demo?"** (D02, D14, D47)
"Nothing on the critical path needs a network. OCR is local; if the engine is missing,
the bundled scans run from recorded OCR output and the screen says 'recorded'. The
model's answers replay from disk. And one command, preflight, rehearses all eleven demo
steps and opens every page on the machine I will use."

**38. "Hundreds of invoices a month. Would this hold? What about fifty thousand?"**
"Hundreds a month is nothing: a text invoice takes well under a second. At fifty
thousand the engine stays and the shell changes: a queue and workers instead of one
process, Postgres instead of SQLite, OCR on its own workers because it is the slow
step, and the two CSV loaders replaced by ERP queries."

**39. "How would you connect it to a real mailbox and ERP?"**
"Intake is one function that takes bytes and a file name, so a mailbox poller calls it
for each attachment. Vendors and POs come from two loader functions, which become ERP
queries. Posting writes to a local ledger; that becomes a posting call followed by the
same read-back check. The drafted vendor message becomes a reply a person approves."

**40. "This is on a public link. What about a malicious PDF, or someone running up
your API bill?"** (D41, D60, D48)
"Uploads are untrusted. Text from a document is escaped before it reaches the page.
Files over fifteen megabytes or twenty pages are held. On the public link every visitor
has private books, so nobody can reset mine mid-demo. A key in the host's secrets does
nothing unless a second switch is set, and then the daily cap applies."

**41. "What would you build next?"**
"Mailbox intake first, because that is where the work starts. Then e-invoice
verification, because it removes OCR risk on the fields that matter most. Then single
sign-on, so that 'two different people' is a fact and not a text box."

## G. Pushback you should expect

**"This is over-built for a case study."**
"The guide says the edge cases show how well you understand the problem, and that a
demo that breaks live is hard to recover from. Most of the volume is tests and checks
that make four edge cases behave and keep the demo boring. The part I would defend
hardest is the smallest: one pure function for the decision and one rules file."

**"It is a rules engine with an AI sticker."**
"For deciding, yes, on purpose. Nobody wants a payment decision that changes between
runs. For reading, the rules handle what has structure and Claude handles what does
not, with every value verified. I would rather show you a narrow use of AI that I can
prove is safe than a wide one I cannot."

**"Rules do not generalise."**
"I measured that instead of arguing it. On invoices from tools I did not use, they
went from 13 of 49 to 45 of 49 with general fixes, and the 12 text documents I never
looked at all came out right. Where rules stop, the model takes over. Where both stop,
a person does."

**"Your documents are still synthetic."**
"Yes. The second set is closer to real ones than my samples, and it is still made by
me. Real scans have stamps, handwriting and folds. The design choice that matters is
what happens when reading fails: it holds. I would run the same evaluation on a
client's real invoices in the first week; the script takes a folder and a truth file."

**"What are the limits?"**
Say them without being asked twice: Indian GST invoices in English and rupees; one PO
per invoice; photographs are the weak spot; typed reviewer names; no mailbox; CSV
master data; not load-tested; and, until you have done step 6, no real model answer
recorded.

## H. If you do not know

"I do not know. Here is where I would look." Then name the file. Guessing about your
own build is the one unrecoverable answer.
