"""Two reads of a scanned invoice, and how they are reconciled.

A scan is read twice: once from the greyscale picture and once after the picture is
reduced to pure black and white. The engine is the same, but it sees different pixels
and makes different mistakes. That gives two things a single read cannot:

  * a choice: the read whose figures add up (line amounts, tax, total, amount in words)
    is used, so a garbled number in one read does not hold the invoice when the other
    read got it right;
  * corroboration for the values no arithmetic can check. An invoice number or a date
    that comes out the same in both reads is trusted; one that differs is marked as
    doubtful, which holds the invoice for a person to look at.

Nothing here guesses. A disagreement is never settled by picking one silently.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from . import textutil as T
from .models import Invoice
from .rules import Rules
from .validate import validate

LABEL = {"bw": "black-and-white read", "gray": "greyscale read"}
ADD_UP = {"ARITHMETIC_MISMATCH", "AMOUNT_WORDS_MISMATCH", "MISSING_TOTAL", "LINE_ITEMS_UNREADABLE",
          "MISSING_INVOICE_DATE", "GSTIN_INVALID", "BUYER_GSTIN_MISSING", "TAX_RATE_UNUSUAL", "TAX_TYPE_MISMATCH"}
UNCHECKABLE = ("invoice_number", "invoice_date", "po_reference")     # no arithmetic or checksum covers these
SHOWN = ("invoice_number", "invoice_date", "po_reference", "vendor_gstin", "subtotal", "total")
AGREED, DOUBT = 0.92, 0.50


def _same(name: str, a: Any, b: Any) -> bool:
    if name in ("invoice_number", "po_reference"):
        return T.invoice_no_key(str(a)) == T.invoice_no_key(str(b))
    if name in ("subtotal", "total"):
        return T.to_decimal(a) == T.to_decimal(b)
    return str(a) == str(b)


def problems(inv: Invoice, rules: Rules, today: date) -> int:
    """How many self-consistency checks a read fails. Fewer is better."""
    findings, _ = validate(inv, rules, today)
    return sum(1 for f in findings if f.code in ADD_UP) + sum(1 for item in inv.line_items if "line_math_mismatch" in item.flags)


def reconcile(reads: dict[str, Invoice], rules: Rules, today: date) -> tuple[str, Invoice, dict]:
    """reads maps 'gray' and 'bw' to the invoice extracted from each read.
    Returns (the variant used, its invoice with confidences adjusted, a report for the run record)."""
    if len(reads) == 1:
        variant, inv = next(iter(reads.items()))
        return variant, inv, {"used": variant, "reads": 1, "why": "only one read was available", "fields": []}
    score = {v: problems(inv, rules, today) for v, inv in reads.items()}
    # fewer failed checks wins; then more line items read; then the black-and-white read
    used = min(reads, key=lambda v: (score[v], -len(reads[v].line_items), 0 if v == "bw" else 1))
    other = next(v for v in reads if v != used)
    chosen, second = reads[used], reads[other]
    if score[used] < score[other]:
        why = (f"its figures {'add up' if score[used] == 0 else 'fail fewer checks'} "
               f"({score[used]} failed check(s) against {score[other]} in the {LABEL[other]})")
    else:
        why = f"both reads pass the same checks ({score[used]} failed); the {LABEL[used]} is the default"
    rows = []
    for name in SHOWN:
        a, b = chosen.fields.get(name), second.fields.get(name)
        va, vb = (a.value if a and a.present else None), (b.value if b and b.present else None)
        if va is None and vb is None:
            status = "not found in either read"
        elif va is None or vb is None:
            status = "found in one read only"
        elif _same(name, va, vb):
            status = "same in both reads"
            if name in UNCHECKABLE or name == "vendor_gstin":
                a.confidence = max(a.confidence, AGREED)
                a.notes.append("the same in both reads of the scan")
        else:
            status = "the reads differ"
            if name in UNCHECKABLE:
                a.confidence = min(a.confidence, DOUBT)
                a.notes.append(f"the two reads of the scan disagree: '{va}' and '{vb}'")
        rows.append({"field": name, "label": a.label if a else (b.label if b else name), "used": va, "other": vb,
                     "status": status})
    lines_used = [(str(i.qty), str(i.unit_price), str(i.amount)) for i in chosen.line_items]
    lines_other = [(str(i.qty), str(i.unit_price), str(i.amount)) for i in second.line_items]
    rows.append({"field": "line_items", "label": "Line items", "used": f"{len(lines_used)} line(s)",
                 "other": f"{len(lines_other)} line(s)",
                 "status": "same in both reads" if lines_used == lines_other else "the reads differ"})
    chosen.notes.append(f"The scan was read twice. The {LABEL[used]} is used: {why}.")
    return used, chosen, {"used": used, "other": other, "reads": 2, "why": why, "failed_checks": score, "fields": rows}


def doubtful_fields(report: Optional[dict]) -> list[str]:
    return [r["field"] for r in (report or {}).get("fields", []) if r["status"] == "the reads differ" and r["field"] in UNCHECKABLE]
