"""PO matching: which PO, line by line, both tolerance limits, and the running balance."""
from decimal import Decimal

import pytest

from invoice_desk.po_match import breaks_limit, match_po, pair_lines, score_po
from invoice_desk.rules import Limit

from .helpers import codes, make_invoice

CHAIR = "Ergonomic mesh office chair, high back"
DESK = "Height adjustable workstation 1500 mm"
CUPBOARD = "Steel storage cupboard, 4 shelf"
WRAP = "Stretch wrap film roll 23 micron"
SERVICE = "Facility management and housekeeping services"


def run(inv, vendor_id, master, store, rules):
    vendor = master.vendors.get(vendor_id) if vendor_id else None
    return match_po(inv, vendor, master, store, rules)


def post(store, ledger_id, vendor, po, lines, number="PRIOR/1", inv_date="2026-11-01", status="POSTED", sha=""):
    taxable = sum((Decimal(str(a)) for _, _, _, a in lines), Decimal("0"))
    store.add_ledger({"ledger_id": ledger_id, "vendor_id": vendor, "invoice_no": number, "invoice_date": inv_date,
                      "po_number": po, "taxable": taxable, "tax": taxable * Decimal("0.18"),
                      "total": taxable * Decimal("1.18"), "status": status, "file_sha256": sha},
                     [{"po_line_no": n, "description": d, "qty": None if q is None else Decimal(str(q)),
                       "unit_price": None, "amount": Decimal(str(a))} for n, d, q, a in lines])


def test_full_invoice_matches_every_line(master, store, rules):
    inv = make_invoice(lines=((CHAIR, 40, "4500.00"), (DESK, 10, "14250.00"), (CUPBOARD, 6, "9800.00")))
    po, findings, blocks, allocations = run(inv, "V001", master, store, rules)
    assert findings == []
    assert po["po_number"] == "PO-2026-0147" and po["method"] == "quoted on the invoice"
    assert po["lines_matched"] == 3 and Decimal(po["remaining_after"]) == 0
    assert [a["po_line_no"] for a in allocations] == [1, 2, 3]


def test_lines_are_paired_by_wording_not_by_position(master):
    inv = make_invoice(lines=(("Storage cupboard steel 4 shelf", 6, "9800.00"), ("Mesh office chair ergonomic high back", 40, "4500.00")))
    pairs = pair_lines(inv.line_items, master.pos["PO-2026-0147"])
    assert [p[1].line_no for p in pairs] == [3, 1]


# ---- price: a line passes only inside BOTH the percentage and the rupee limit

@pytest.mark.parametrize("qty,price,expected", [
    (10, "4545.00", "PRICE_WITHIN_TOLERANCE"),   # exactly +1.00%, Rs 450 on the line
    (10, "4545.01", "PRICE_VARIANCE"),           # +1.0002%: over the percentage limit
    (22, "4522.50", "PRICE_WITHIN_TOLERANCE"),   # +0.5%, Rs 495 on the line
    (23, "4522.50", "PRICE_VARIANCE"),           # +0.5% but Rs 517.50 on the line: over the rupee limit
    (40, "4512.50", "PRICE_WITHIN_TOLERANCE"),   # Rs 500.00 exactly: at the rupee limit
    (40, "4512.51", "PRICE_VARIANCE"),           # Rs 500.40
    (40, "4400.00", "PRICE_BELOW_PO"),
])
def test_price_tolerance_from_both_sides(qty, price, expected, master, store, rules):
    _, findings, _, _ = run(make_invoice(lines=((CHAIR, qty, price),)), "V001", master, store, rules)
    assert codes(findings) == {expected}
    assert rules.reason("PRICE_VARIANCE").action == "HOLD" and rules.reason("PRICE_WITHIN_TOLERANCE").action == "NOTE"


def test_price_variance_says_how_much_and_who_decides(master, store, rules):
    _, findings, _, _ = run(make_invoice(lines=((CHAIR, 40, "4800.00"),)), "V001", master, store, rules)
    f = next(f for f in findings if f.code == "PRICE_VARIANCE")
    assert "₹300.00 higher (6.67%)" in f.summary and "₹12,000.00 on this line" in f.summary
    assert "R. Iyer" in f.question and "credit note of ₹12,000.00" in f.question and f.vendor_issue


def test_breaks_limit_needs_both_limits_to_hold():
    limit = Limit(pct=Decimal("1.0"), abs_inr=Decimal("500"))
    assert not breaks_limit(Decimal("500"), Decimal("50000"), limit)      # 1.0% and Rs 500: on both limits
    assert breaks_limit(Decimal("500.01"), Decimal("1000000"), limit)     # tiny percentage, over the rupee limit
    assert breaks_limit(Decimal("11"), Decimal("1000"), limit)            # Rs 11 but 1.1%
    assert not breaks_limit(Decimal("-9000"), Decimal("1000"), limit)     # under-billing never breaks a limit


# ---- quantity and receipts

def test_quantity_over_the_po_is_held(master, store, rules):
    _, findings, _, _ = run(make_invoice(lines=((CHAIR, 41, "4500.00"),)), "V001", master, store, rules)
    assert codes(findings) == {"QTY_OVER_PO"}       # one unit over the line; the PO value itself is not exceeded
    assert "1 more than the PO" in findings[0].summary

def test_quantity_counts_what_earlier_invoices_already_billed(master, store, rules):
    post(store, "T1", "V001", "PO-2026-0147", [(1, CHAIR, 30, "135000.00")])
    _, ok, _, _ = run(make_invoice(lines=((CHAIR, 10, "4500.00"),)), "V001", master, store, rules)
    _, over, _, _ = run(make_invoice(lines=((CHAIR, 11, "4500.00"),)), "V001", master, store, rules)
    assert codes(ok) == set()
    f = next(f for f in over if f.code == "QTY_OVER_PO")
    assert "41 of 40" in f.summary and "30 on earlier invoices plus 11 now" in f.summary


def test_invoices_waiting_for_review_do_not_use_up_the_po(master, store, rules):
    post(store, "T1", "V001", "PO-2026-0147", [(1, CHAIR, 30, "135000.00")], status="PENDING")
    po, findings, _, _ = run(make_invoice(lines=((CHAIR, 40, "4500.00"),)), "V001", master, store, rules)
    assert codes(findings) == set() and Decimal(po["pending_other"]) == Decimal("135000.00")


def test_rejected_invoices_do_not_use_up_the_po(master, store, rules):
    post(store, "T1", "V001", "PO-2026-0147", [(1, CHAIR, 40, "180000.00")], status="REJECTED")
    _, findings, _, _ = run(make_invoice(lines=((CHAIR, 40, "4500.00"),)), "V001", master, store, rules)
    assert codes(findings) == set()


def test_billing_ahead_of_goods_receipt_is_held(master, store, rules):
    partial = master.model_copy(deep=True)
    partial.pos["PO-2026-0147"].lines[0].qty_received = Decimal("30")
    _, findings, _, _ = match_po(make_invoice(lines=((CHAIR, 35, "4500.00"),)), partial.vendors["V001"], partial, store, rules)
    assert codes(findings) == {"GOODS_NOT_RECEIVED"}
    _, findings, _, _ = match_po(make_invoice(lines=((CHAIR, 30, "4500.00"),)), partial.vendors["V001"], partial, store, rules)
    assert codes(findings) == set()


# ---- the PO as a whole

@pytest.mark.parametrize("amount,held", [("300000.00", False), ("300400.00", False), ("300500.00", False),
                                         ("300500.01", True), ("300600.00", True)])
def test_service_po_balance_from_both_sides(amount, held, master, store, rules):
    """Rs 7,20,000 service PO with seven instalments of Rs 60,000 already posted: Rs 3,00,000 is open."""
    inv = make_invoice(vendor="V006", po="PO-2026-0160", lines=((SERVICE, None, amount),),
                       bank_account="004598001234", ifsc="HMBL0000459")
    po, findings, _, _ = run(inv, "V006", master, store, rules)
    assert ("OVER_PO_BALANCE" in codes(findings)) is held
    assert Decimal(po["billed_before"]) == Decimal("420000.00")


def test_over_balance_explains_the_running_total(master, store, rules):
    inv = make_invoice(vendor="V004", po="PO-2026-0193", lines=(("Portable multi-gas detector, 4 gas", 5, "25000.00"),))
    _, findings, blocks, _ = run(inv, "V004", master, store, rules)
    assert codes(findings) == {"QTY_OVER_PO", "OVER_PO_BALANCE"}
    f = next(f for f in findings if f.code == "OVER_PO_BALANCE")
    for piece in ("₹5,00,000.00", "₹4,00,000.00 is already billed on 2 earlier invoice(s)", "₹1,25,000.00", "₹25,000.00 over"):
        assert piece in f.summary
    running = next(b for b in blocks if b["title"].startswith("Running total"))
    assert [r["Invoice"] for r in running["rows"]][:2] == ["ASS/2026/0871", "ASS/2026/0902"]


# ---- finding the PO

def test_po_not_in_the_register(master, store, rules):
    po, findings, _, _ = run(make_invoice(po="PO-2026-9999"), "V001", master, store, rules)
    assert po is None and codes(findings) == {"PO_NOT_FOUND"}


def test_closed_po(master, store, rules):
    inv = make_invoice(po="PO-2026-0099", lines=(("Filing cabinet, 3 drawer", 5, "7600.00"),))
    _, findings, _, _ = run(inv, "V001", master, store, rules)
    assert "PO_CLOSED" in codes(findings)


def test_po_issued_to_another_vendor(master, store, rules):
    inv = make_invoice(vendor="V002", po="PO-2026-0147", lines=((CHAIR, 1, "4500.00"),))
    _, findings, _, _ = run(inv, "V002", master, store, rules)
    assert "PO_VENDOR_MISMATCH" in codes(findings)


def test_invoice_dated_before_the_po(master, store, rules):
    _, findings, _, _ = run(make_invoice(inv_date="2026-10-01"), "V001", master, store, rules)
    assert codes(findings) == {"DATE_BEFORE_PO"}


def test_line_that_is_not_on_the_po(master, store, rules):
    inv = make_invoice(lines=((CHAIR, 40, "4500.00"), ("Annual maintenance contract for furniture", 1, "5000.00")))
    _, findings, _, allocations = run(inv, "V001", master, store, rules)
    assert "LINE_NOT_ON_PO" in codes(findings) and allocations[1]["po_line_no"] is None


def test_po_is_inferred_from_the_lines_and_never_auto_accepted(master, store, rules):
    inv = make_invoice(vendor="V003", po=None, lines=((WRAP, 120, "640.00"), ("BOPP packing tape 48 mm, carton of 72", 25, "2340.00"),
                                                      ("Bubble wrap roll 1 m x 100 m", 40, "1150.00")))
    po, findings, blocks, _ = run(inv, "V003", master, store, rules)
    assert po["po_number"] == "PO-2026-0212" and po["method"] == "inferred from the lines"
    assert codes(findings) == {"PO_INFERRED"} and rules.reason("PO_INFERRED").action == "HOLD"
    f = findings[0]
    assert "PO-2026-0188" in f.summary and "can be approved" in f.question      # runner-up shown; clean match says so
    assert any(b["title"].startswith("Open POs of this vendor") for b in blocks)


def test_inference_refuses_when_two_pos_fit_equally(master, store, rules):
    inv = make_invoice(vendor="V003", po=None, lines=((WRAP, 10, "640.00"),))    # stretch wrap is on both open POs
    po, findings, _, _ = run(inv, "V003", master, store, rules)
    assert po is None and codes(findings) == {"NO_PO_FOUND"}
    assert "PO-2026-0188" in findings[0].summary and "PO-2026-0212" in findings[0].summary


def test_no_po_and_no_vendor(master, store, rules):
    po, findings, _, _ = run(make_invoice(po=None), None, master, store, rules)
    assert po is None and codes(findings) == {"NO_PO_FOUND"}


def test_score_po_prefers_the_po_whose_items_and_prices_fit(master, store, rules):
    inv = make_invoice(vendor="V001", po=None, lines=(("Visitor chair, cantilever frame", 12, "2950.00"),
                                                      ("Conference table 8 seater, laminate top", 4, "21500.00")))
    right = score_po(inv, master.pos["PO-2026-0152"], store.po_usage("PO-2026-0152"), rules)
    wrong = score_po(inv, master.pos["PO-2026-0147"], store.po_usage("PO-2026-0147"), rules)
    assert right["score"] > 0.95 and wrong["score"] < 0.5


# ---- lump-sum invoices

def test_lump_sum_equal_to_the_open_po_value_matches_at_total_level(master, store, rules):
    inv = make_invoice(po="PO-2026-0152", lines=(("Supply of office furniture as per your order", None, "121400.00"),))
    _, findings, _, allocations = run(inv, "V001", master, store, rules)
    assert codes(findings) == {"MATCHED_AT_TOTAL_LEVEL"}
    assert [(a["po_line_no"], a["qty"], a["amount"]) for a in allocations] == \
        [(1, Decimal("12"), Decimal("35400.00")), (2, Decimal("4"), Decimal("86000.00"))]


def test_lump_sum_that_is_not_the_open_value_cannot_be_allocated(master, store, rules):
    inv = make_invoice(po="PO-2026-0152", lines=(("Supply of office furniture as per your order", None, "100000.00"),))
    _, findings, _, _ = run(inv, "V001", master, store, rules)
    assert codes(findings) == {"BUNDLED_PARTIAL"} and "itemised" in findings[0].question


def test_tax_inclusive_prices_are_compared_pre_tax(master, store, rules):
    inv = make_invoice(vendor="V005", po="PO-2026-0224", lines=(("Laser toner cartridge 26A compatible", 20, "3200.00"),),
                       bank_account="117700452981", ifsc="CUCB0001177")
    inv.tax_inclusive = True
    inv.line_items[0].tax_rate = Decimal("18")
    inv.line_items[0].unit_price = Decimal("3776.00")       # printed price includes 18% GST
    _, findings, _, _ = run(inv, "V005", master, store, rules)
    assert codes(findings) == {"TAX_INCLUSIVE_NORMALISED"}
    assert "₹3,776.00 / 1.18 = ₹3,200.00" in findings[0].summary
