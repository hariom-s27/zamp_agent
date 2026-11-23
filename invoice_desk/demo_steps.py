"""The guided demo as data: eleven scenarios in the order they are shown.

Kept apart from the screen (no Streamlit here) so that the preflight check can rehearse
exactly the sequence the Guided demo page runs, on the machine the demo will be given on.
"""
from __future__ import annotations

from typing import Optional

from . import samples as sample_lib
from .models import RunResult
from .rules import Rules

STEPS = [
    {"key": "clean", "short": "Clean invoice", "sample": "01_happy_path_furniture", "title": "A clean invoice goes straight through",
     "watch": "All nine stages pass. Three lines are matched to the purchase order on item, quantity and price, the "
              "invoice is posted for payment, and the PO balance goes down by what was billed."},
    {"key": "scan", "short": "Scanned", "sample": "02_scanned_clean_safety", "title": "A scanned invoice is read by OCR",
     "watch": "Stage 2 straightens the scan and reads it twice. Stage 4 checks the arithmetic of what was read before "
              "any figure is trusted: quantity times price, the sum of the lines, tax and total must all agree."},
    {"key": "split", "short": "Split PO", "sample": "04_E1_split_po_overbilling", "title": "Edge case 1: a split purchase order, one invoice too many",
     "watch": "Two earlier invoices already billed 16 of the 20 units ordered. Stage 6 adds the 5 on this one to the "
              "running total, finds 21 of 20, and holds it with the question for the PO owner. Approving it in the "
              "review queue is refused for the same reason: the PO is checked again at that moment.", "follow": "review"},
    {"key": "duplicate", "short": "Duplicate", "sample": "05_E2_disguised_duplicate", "title": "Edge case 2: the same invoice under a new number",
     "watch": "Stage 7 finds the same vendor, the same lines and the same total a few days apart, with no second "
              "delivery on record. Rejected, and the message to the vendor is drafted and ready to copy."},
    {"key": "blank", "short": "No number, no PO", "sample": "06_E3_scan_no_number_implied_po", "title": "Edge case 3: a scan with no invoice number and no PO",
     "watch": "The number box is looked at on the picture itself and found empty, so the vendor is asked for the "
              "number. The PO is worked out from the lines and offered to a person to confirm: an inferred PO is "
              "never posted on a guess."},
    {"key": "inside", "short": "Inside tolerance", "sample": "07_E4a_tax_inclusive_within_tolerance", "title": "Edge case 4: prices include GST, just inside tolerance",
     "watch": "The invoice prints prices with tax in them. Stage 6 converts them to pre-tax and compares with the PO: "
              "0.95% and Rs 488 above, inside both limits (1% and Rs 500 a line), so it is approved with a note."},
    {"key": "outside", "short": "Outside tolerance", "sample": "08_E4b_tax_inclusive_over_tolerance", "title": "Edge case 4: two rupees more, just outside tolerance",
     "watch": "The same invoice two rupees a unit dearer: 1.01% and Rs 515 above the PO. Held for the price alone, "
              "with the credit note amount worked out for the PO owner. The limits are numbers in rules.yaml."},
    {"key": "bank", "short": "Bank change", "sample": "09_fraud_guard_bank_change", "title": "Fraud control: the bank account has changed",
     "watch": "Everything on this invoice matches except the account to pay. It is held whatever else is true. In the "
              "review queue it takes a recorded call-back to the number on file and then a second, different person "
              "before anything is payable.", "follow": "review"},
    {"key": "letter", "short": "Letter, AI assist", "sample": "10_ai_assist_letter_invoice", "title": "AI assist: an invoice written as a letter",
     "watch": "No table, no labels. The rules reader will not guess. With AI assist, the AI copies the missing values "
              "out of the text and code checks every one against the page and against the invoice's own arithmetic. "
              "A value that fails a check is not used, and the model takes no part in the decision."},
    {"key": "again", "short": "Sent twice", "sample": "01_happy_path_furniture", "title": "The first invoice arrives a second time", "repeat": True,
     "watch": "Stage 1 recognises the file. Nothing is read or booked twice; the earlier record is returned, with a "
              "note for the sender. The same file always gets the same answer."},
    {"key": "junk", "short": "Not an invoice", "sample": "quotation_not_invoice", "title": "Something that is not an invoice",
     "watch": "A quotation sent to the invoice inbox is refused with a reason at stage 3 and nothing is booked. Corrupt, "
              "empty and password-protected files end the same way: a clear reason, never an error screen."},
]




def sample_of(step: dict, manifest: Optional[list[dict]] = None) -> dict:
    return next(s for s in (manifest or sample_lib.load_manifest()) if s["id"] == step["sample"])


def outcome(step: dict, result: RunResult, done: dict, rules: Rules, manifest: Optional[list[dict]] = None) -> tuple[bool, str]:
    """(as expected, the expectation in words) for one finished step. `done` maps the keys
    of the steps already run to what they recorded (at least 'run_id')."""
    if step.get("repeat"):
        first = (done.get("clean") or {}).get("run_id")
        return (bool(first and result.resubmission_of == first),
                f"the record of {first or 'the first run'}, nothing booked again")
    ok, want, reasons = sample_lib.check(sample_of(step, manifest), result)
    titles = ", ".join(sorted(rules.reason(code).title for code in reasons))
    return ok, want + (f" ({titles})" if titles else "")
