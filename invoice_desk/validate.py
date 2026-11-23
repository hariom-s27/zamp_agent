"""Stage 4, Validate: is the invoice complete and consistent with itself?

All checks here use only the invoice (and today's date and the GST settings). Each one
is a small deterministic test, listed in the stage's evidence whether it passed or not.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from . import textutil as T
from .models import Finding, Invoice
from .rules import Rules

ZERO = Decimal("0")
STAGE = "validate"


def _check(rows: list[dict], name: str, ok, detail: str) -> None:
    rows.append({"Check": name, "Result": "pass" if ok is True else "n/a" if ok is None else "FAIL",
                 "Detail": detail})


def validate(inv: Invoice, rules: Rules, today: date) -> tuple[list[Finding], list[dict]]:
    """Returns (findings, evidence blocks)."""
    findings: list[Finding] = []
    rows: list[dict] = []
    tol = rules.tolerances.rounding_inr
    company = rules.company

    # ---- required fields
    if not inv.has("invoice_number"):
        number = inv.fields.get("invoice_number")
        reason = (number.missing_reason if number else "") or ""
        blank_box = "left blank" in reason                     # proven: a text PDF, or a scan with no ink beside the label
        unread = "could not be read" in reason                 # a scan with something written there that OCR could not read
        unchecked = "nothing read beside it" in reason         # a scan where the picture could not be judged
        well_read = bool(inv.line_items) and inv.has("total")
        if blank_box:
            summary = ("The invoice number is left blank on the invoice. A tax invoice must carry one, and without it "
                       "the invoice cannot be tracked for duplicates.")
            question = ("Ask the vendor for a corrected invoice that carries its number. To approve this one, enter "
                        "the number the vendor gives you.")
            issue = "the invoice number is left blank (a tax invoice must carry one)"
        elif unread or unchecked:      # our reading problem before it is anybody else's: nothing is said to the vendor
            summary = ("Something is written beside the invoice number label on the scan, but it could not be read "
                       "(handwriting, a stamp or a faint print)." if unread else
                       "An invoice number label is on the scan, but no number could be read beside it.")
            question = ("Read the invoice number from the document image and enter it when approving. If the space "
                        "really is empty, ask the vendor for a corrected invoice.")
            issue = None
        elif well_read:
            summary = ("No invoice number was found on the invoice. A tax invoice must carry one, and without it the "
                       "invoice cannot be tracked for duplicates.")
            question = ("Check the document. If the number is there, enter it when approving; if it really is "
                        "missing, ask the vendor for a corrected invoice.")
            issue = "we could not find an invoice number on the invoice (a tax invoice must carry one)"
        else:   # the document as a whole was not read well: this is our problem before it is the vendor's
            summary = "No invoice number could be read from this document."
            question = ("Find the invoice number on the document and enter it when approving. If it really is "
                        "missing, ask the vendor for a corrected invoice.")
            issue = None
        findings.append(Finding(
            code="MISSING_INVOICE_NUMBER", stage=STAGE, summary=summary, expected="an invoice number",
            observed="left blank" if blank_box else "written but not readable" if unread else "none found",
            question=question, vendor_issue=issue))
    _check(rows, "Invoice number present", inv.has("invoice_number"), str(inv.get("invoice_number") or "not found"))
    if not inv.has("invoice_date"):
        findings.append(Finding(
            code="MISSING_INVOICE_DATE", stage=STAGE, summary="The invoice has no readable invoice date.",
            expected="an invoice date", observed="none found",
            question="Ask the vendor for a corrected invoice with its date.",
            vendor_issue="the invoice does not show an invoice date"))
    _check(rows, "Invoice date present", inv.has("invoice_date"), T.fmt_date(inv.get("invoice_date")))
    if not inv.has("total"):
        findings.append(Finding(
            code="MISSING_TOTAL", stage=STAGE, summary="No invoice total could be read beside a total label.",
            expected="an invoice total", observed="none found",
            question="Read the invoice by hand and enter the total, or ask the vendor for a clearer copy."))
    _check(rows, "Invoice total present", inv.has("total"), T.fmt_inr(inv.get("total")))
    if not inv.line_items:
        findings.append(Finding(
            code="LINE_ITEMS_UNREADABLE", stage=STAGE,
            summary="No line items could be read: the document has no item table the reader recognises.",
            expected="at least one line with an amount", observed="no item table found",
            question="Read the line items by hand. (With AI assist on, a document like this is read and checked automatically.)"))
    _check(rows, "Line items read", bool(inv.line_items), f"{len(inv.line_items)} line(s)")

    # ---- reading confidence on the fields nothing else cross-checks
    floor = rules.reading.min_confidence_critical
    weak = [f for name in ("invoice_number", "invoice_date", "total")
            if (f := inv.fields.get(name)) and f.present and f.confidence < floor]
    for f in weak:
        findings.append(Finding(
            code="LOW_CONFIDENCE_READ", stage=STAGE,
            summary=f"{f.label} was read as '{f.value}' with low confidence ({f.confidence:.2f})" +
                    (f": {f.notes[-1]}." if f.notes and "disagree" in f.notes[-1] else "."),
            expected=f"confidence of at least {floor:.2f}", observed=f"{f.confidence:.2f}",
            evidence={"field": f.name, "value": f.value, "notes": f.notes},
            question=f"Check {f.label.lower()} '{f.value}' against the document image and confirm or correct it."))
    _check(rows, "Key fields read with confidence", not weak,
           "invoice number, date and total at or above " + f"{floor:.2f}" if not weak else
           "; ".join(f"{f.label} {f.confidence:.2f}" for f in weak))

    # ---- arithmetic
    problems: list[str] = []
    items = inv.line_items
    for it in items:
        if "line_math_mismatch" in it.flags:
            problems.append(f"line {it.line_no}: {T.fmt_num(it.qty)} x {T.fmt_inr(it.unit_price)} is not "
                            f"{T.fmt_inr(it.amount)}" + (f" ({it.notes[-1]})" if it.notes else ""))
    taxable, total, tax_total = inv.taxable, inv.total, inv.money("tax_total")
    line_sum = sum((it.amount_pre_tax for it in items if it.amount_pre_tax is not None), ZERO) if items else None
    if items and taxable is not None and all(it.amount_pre_tax is not None for it in items):
        slack = tol * max(1, len(items)) if inv.tax_inclusive else tol
        ok = abs(line_sum - taxable) <= slack
        _check(rows, "Lines add up to the taxable value", ok,
               f"lines {T.fmt_inr(line_sum)} against taxable value {T.fmt_inr(taxable)}")
        if not ok:
            problems.append(f"the lines add up to {T.fmt_inr(line_sum)} but the taxable value is {T.fmt_inr(taxable)}")
    else:
        _check(rows, "Lines add up to the taxable value", None, "needs line amounts and a taxable value")
    if taxable is not None and total is not None:
        expected = taxable + (tax_total or ZERO)
        ok = abs(expected - total) <= tol
        _check(rows, "Taxable value + tax = total", ok,
               f"{T.fmt_inr(taxable)} + {T.fmt_inr(tax_total or ZERO)} against total {T.fmt_inr(total)}")
        if not ok:
            problems.append(f"taxable value {T.fmt_inr(taxable)} plus tax {T.fmt_inr(tax_total or ZERO)} is "
                            f"{T.fmt_inr(expected)}, but the total printed is {T.fmt_inr(total)}")
    else:
        _check(rows, "Taxable value + tax = total", None, "needs a taxable value and a total")
    cgst, sgst = inv.money("cgst"), inv.money("sgst")
    if cgst is not None and sgst is not None and abs(cgst - sgst) > tol:
        problems.append(f"CGST {T.fmt_inr(cgst)} and SGST {T.fmt_inr(sgst)} should be equal")
    if problems:
        findings.append(Finding(
            code="ARITHMETIC_MISMATCH", stage=STAGE,
            summary="The figures on the invoice do not add up: " + "; ".join(problems) + ".",
            expected="quantity x price = amount, lines = taxable value, taxable value + tax = total",
            observed="; ".join(problems), evidence={"problems": problems},
            question="Compare the figures with the document image. If the invoice itself is wrong, ask the "
                     "vendor for a corrected invoice.",
            vendor_issue="the amounts on the invoice do not add up (" + "; ".join(problems) + ")"))

    # ---- amount in words
    words = inv.money("amount_in_words")
    if words is not None and total is not None:
        ok = abs(words - total) <= tol
        _check(rows, "Amount in words agrees with figures", ok, f"words {T.fmt_inr(words)}, figures {T.fmt_inr(total)}")
        if not ok:
            findings.append(Finding(
                code="AMOUNT_WORDS_MISMATCH", stage=STAGE,
                summary=f"The amount in words is {T.fmt_inr(words)} but the total in figures is {T.fmt_inr(total)}.",
                expected=T.fmt_inr(total), observed=T.fmt_inr(words),
                question="Check which is right against the document and the PO; ask the vendor to correct the invoice.",
                vendor_issue=f"the amount in words ({T.fmt_inr(words)}) differs from the total in figures ({T.fmt_inr(total)})"))
    else:
        _check(rows, "Amount in words agrees with figures", None, "no readable amount in words")

    # ---- GST: rate, type, identifiers
    inv_date = inv.day()
    rate = inv.money("tax_rate")
    effective = (tax_total / taxable * 100) if (taxable and tax_total is not None and taxable > 0) else None
    if rate is None and effective is not None:
        rate = T.q2(effective)
    if rate is not None:
        allowed = list(rules.gst.rates_special)
        allowed += rules.gst.rates_before_2025_09_22 if (inv_date and inv_date < date(2025, 9, 22)) else rules.gst.rates_current
        slack = rules.gst.rate_tolerance_pct
        ok = any(abs(rate - a) <= slack for a in allowed)
        _check(rows, "GST rate is a standard rate", ok, f"{rate:g}% (allowed: {', '.join(f'{a:g}' for a in sorted(set(allowed)))})")
        if not ok:
            findings.append(Finding(
                code="TAX_RATE_UNUSUAL", stage=STAGE,
                summary=f"GST is charged at {rate:g}%, which is not one of the standard rates for the invoice date.",
                expected=", ".join(f"{a:g}%" for a in sorted(set(allowed))), observed=f"{rate:g}%",
                question="Confirm the correct GST rate for these items (HSN) before paying the tax amount."))
        if effective is not None and inv.money("tax_rate") is not None and abs(effective - rate) > Decimal("0.2"):
            findings.append(Finding(
                code="ARITHMETIC_MISMATCH", stage=STAGE,
                summary=f"The tax charged is {effective:.2f}% of the taxable value, not the {rate:g}% stated.",
                expected=f"{rate:g}%", observed=f"{effective:.2f}%",
                question="Check the tax amount against the stated rate; ask the vendor to correct the invoice.",
                vendor_issue=f"the tax amount does not correspond to the stated rate of {rate:g}%"))
    else:
        _check(rows, "GST rate is a standard rate", None, "no tax found on the invoice")

    vendor_gstin = inv.get("vendor_gstin")
    if vendor_gstin:
        ok = T.gstin_is_valid(vendor_gstin)
        _check(rows, "Vendor GSTIN check character", ok, vendor_gstin)
        if not ok:
            findings.append(Finding(
                code="GSTIN_INVALID", stage=STAGE,
                summary=f"The vendor GSTIN {vendor_gstin} fails its check character, so it was misprinted or misread.",
                expected="a GSTIN whose 15th character matches the first 14", observed=vendor_gstin,
                question="Read the GSTIN from the document and compare it with the vendor master."))
        igst = inv.money("igst") or ZERO
        split = (cgst or ZERO) + (sgst or ZERO)
        if igst or split:
            same_state = vendor_gstin[:2] == company.state_code
            ok = (same_state and split > 0 and igst == 0) or (not same_state and igst > 0 and split == 0)
            want = "CGST + SGST" if same_state else "IGST"
            got = "IGST" if igst and not split else "CGST + SGST" if split and not igst else "both"
            _check(rows, "Right kind of GST for the two states", ok,
                   f"supplier {T.state_name(vendor_gstin[:2])}, buyer {T.state_name(company.state_code)}: expected {want}, found {got}")
            if not ok:
                findings.append(Finding(
                    code="TAX_TYPE_MISMATCH", stage=STAGE,
                    summary=f"The supplier is in {T.state_name(vendor_gstin[:2])} and we are in "
                            f"{T.state_name(company.state_code)}, so the invoice should charge {want}; it charges {got}.",
                    expected=want, observed=got,
                    question="Ask the vendor to reissue the invoice with the correct tax; input credit is at risk otherwise.",
                    vendor_issue=f"the invoice charges {got} where {want} applies"))
    if not inv.has("buyer_gstin"):
        findings.append(Finding(
            code="BUYER_GSTIN_MISSING", stage=STAGE,
            summary=f"Our GSTIN ({company.gstin}) does not appear on the invoice, so input tax credit cannot be claimed on it.",
            expected=company.gstin, observed="not on the document",
            question="Ask the vendor to reissue the invoice addressed to our GSTIN.",
            vendor_issue=f"the invoice does not carry our GSTIN ({company.gstin})"))
    _check(rows, "Addressed to our GSTIN", inv.has("buyer_gstin"), str(inv.get("buyer_gstin") or "not found"))

    # ---- dates and currency
    if inv_date:
        age = (today - inv_date).days
        future_ok = age >= -rules.dates.allow_future_days
        age_ok = age <= rules.dates.max_age_days
        _check(rows, "Invoice date is plausible", future_ok and age_ok, f"{T.fmt_date(inv_date)}, {age} day(s) before today")
        if not future_ok:
            findings.append(Finding(
                code="DATE_IN_FUTURE", stage=STAGE,
                summary=f"The invoice is dated {T.fmt_date(inv_date)}, which is {-age} day(s) after today.",
                expected=f"on or before {T.fmt_date(today)}", observed=T.fmt_date(inv_date),
                question="Confirm the date with the vendor; a post-dated invoice should not be booked yet.",
                vendor_issue=f"the invoice is dated in the future ({T.fmt_date(inv_date)})"))
        elif not age_ok:
            findings.append(Finding(
                code="DATE_TOO_OLD", stage=STAGE,
                summary=f"The invoice is dated {T.fmt_date(inv_date)}, {age} days ago; the limit is {rules.dates.max_age_days} days.",
                expected=f"within {rules.dates.max_age_days} days", observed=f"{age} days old",
                question="Check why this invoice arrived so late and whether it was already paid another way."))
    currency = inv.get("currency") or "INR"
    _check(rows, "Currency is rupees", currency == "INR", currency)
    if currency != "INR":
        findings.append(Finding(
            code="CURRENCY_NOT_SUPPORTED", stage=STAGE, summary=f"The invoice is in {currency}; only rupee invoices are processed automatically.",
            expected="INR", observed=currency, question="Process this foreign-currency invoice by hand."))

    # ---- what the reader had to repair
    repairs = [f"line {it.line_no}: {it.notes[-1]}" for it in items if any("corrected" in x or "derived" in x for x in it.flags) and it.notes]
    repairs += [f"{f.label}: {n}" for f in inv.fields.values() for n in f.notes if "scan read" in n]
    if repairs:
        findings.append(Finding(
            code="READ_CORRECTED", stage=STAGE,
            summary="A figure the scan garbled was restored from the others: " + " ".join(repairs),
            evidence={"repairs": repairs}))
    blocks = [{"type": "table", "title": "Checks run on the invoice itself", "rows": rows}]
    return findings, blocks
