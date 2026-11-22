from datetime import date
from decimal import Decimal

from invoice_desk.validate import validate

from .helpers import codes, make_invoice

TODAY = date(2026, 11, 21)


def test_a_consistent_invoice_passes_every_check(rules):
    findings, blocks = validate(make_invoice(), rules, TODAY)
    assert findings == []
    assert all(row["Result"] in ("pass", "n/a") for row in blocks[0]["rows"])


def test_missing_required_fields_are_each_reported(rules):
    inv = make_invoice(invoice_number=None, invoice_date=None, total=None)
    inv.line_items = []
    assert {"MISSING_INVOICE_NUMBER", "MISSING_INVOICE_DATE", "MISSING_TOTAL", "LINE_ITEMS_UNREADABLE"} <= codes(validate(inv, rules, TODAY)[0])


def test_total_off_by_one_rupee_passes_and_by_two_fails(rules):
    base = Decimal(make_invoice().get("total"))
    assert "ARITHMETIC_MISMATCH" not in codes(validate(make_invoice(total=f"{base + 1}"), rules, TODAY)[0])
    assert "ARITHMETIC_MISMATCH" in codes(validate(make_invoice(total=f"{base + 2}"), rules, TODAY)[0])


def test_lines_that_do_not_add_up_are_reported(rules):
    inv = make_invoice(subtotal="170000.00")
    found = [f for f in validate(inv, rules, TODAY)[0] if f.code == "ARITHMETIC_MISMATCH"]
    assert found and "lines add up" in found[0].summary and found[0].vendor_issue


def test_a_line_flagged_by_the_reader_is_reported(rules):
    inv = make_invoice()
    inv.line_items[0].flags.append("line_math_mismatch")
    assert "ARITHMETIC_MISMATCH" in codes(validate(inv, rules, TODAY)[0])


def test_amount_in_words_must_agree_with_figures(rules):
    total = make_invoice().get("total")
    assert "AMOUNT_WORDS_MISMATCH" not in codes(validate(make_invoice(amount_in_words=total), rules, TODAY)[0])
    assert "AMOUNT_WORDS_MISMATCH" in codes(validate(make_invoice(amount_in_words="212000"), rules, TODAY)[0])


def test_gst_rate_must_be_a_current_slab_and_the_check_is_date_aware(rules):
    assert "TAX_RATE_UNUSUAL" in codes(validate(make_invoice(gst_rate=12), rules, TODAY)[0])
    assert "TAX_RATE_UNUSUAL" not in codes(validate(make_invoice(gst_rate=12, inv_date="2025-09-01"), rules, date(2025, 9, 30))[0])
    assert "TAX_RATE_UNUSUAL" not in codes(validate(make_invoice(gst_rate=5), rules, TODAY)[0])


def test_wrong_kind_of_gst_for_the_two_states(rules):
    intra_with_igst = make_invoice(vendor="V001", cgst=None, sgst=None, igst="32400.00")
    inter_with_split = make_invoice(vendor="V002", cgst="16200.00", sgst="16200.00", igst=None)
    assert "TAX_TYPE_MISMATCH" in codes(validate(intra_with_igst, rules, TODAY)[0])
    assert "TAX_TYPE_MISMATCH" in codes(validate(inter_with_split, rules, TODAY)[0])
    assert "TAX_TYPE_MISMATCH" not in codes(validate(make_invoice(vendor="V002"), rules, TODAY)[0])


def test_gstin_check_character_and_buyer_gstin(rules):
    assert "GSTIN_INVALID" in codes(validate(make_invoice(vendor_gstin="29AABCT7306K1Z9"), rules, TODAY)[0])
    assert "BUYER_GSTIN_MISSING" in codes(validate(make_invoice(buyer_gstin=None), rules, TODAY)[0])


def test_dates_in_the_future_or_too_old(rules):
    assert "DATE_IN_FUTURE" in codes(validate(make_invoice(inv_date="2026-11-22"), rules, TODAY)[0])
    assert "DATE_IN_FUTURE" not in codes(validate(make_invoice(inv_date="2026-11-21"), rules, TODAY)[0])
    assert "DATE_TOO_OLD" in codes(validate(make_invoice(inv_date="2025-11-01"), rules, TODAY)[0])


def test_foreign_currency_is_held(rules):
    assert "CURRENCY_NOT_SUPPORTED" in codes(validate(make_invoice(currency="USD"), rules, TODAY)[0])


def test_low_confidence_on_a_field_nothing_cross_checks(rules):
    inv = make_invoice()
    inv.fields["invoice_number"].confidence = 0.55
    found = [f for f in validate(inv, rules, TODAY)[0] if f.code == "LOW_CONFIDENCE_READ"]
    assert found and "TEST/001" in found[0].summary
