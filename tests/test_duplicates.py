"""Duplicate control: the same file, the same number, and the same content in disguise."""
from invoice_desk.duplicates import check_duplicates
from invoice_desk.po_match import match_po

from .helpers import codes, make_invoice
from .test_po_match import CHAIR, SERVICE, WRAP, post

LAPTOP = "Business laptop 14 inch, 16 GB RAM, 512 GB SSD"
DOCK = "USB-C docking station with power delivery"


def check(inv, vendor_id, master, store, rules, sha="f" * 64):
    vendor = master.vendors[vendor_id]
    po, po_findings, _, allocations = match_po(inv, vendor, master, store, rules)
    findings, blocks = check_duplicates(inv, vendor, sha, store, rules, allocations, po, {f.code for f in po_findings}, master)
    return findings, blocks[0]["rows"]


def test_first_time_invoice_is_clear(master, store, rules):
    findings, rows = check(make_invoice(), "V001", master, store, rules)
    assert findings == [] and [r["Result"] for r in rows] == ["clear", "clear", "clear"]


def test_the_same_file_is_rejected_whatever_else_is_on_it(master, store, rules):
    post(store, "T1", "V001", "PO-2026-0147", [(1, CHAIR, 10, "45000.00")], number="TOF/1", sha="a" * 64)
    findings, _ = check(make_invoice(number="TOF/1", lines=((CHAIR, 10, "4500.00"),)), "V001", master, store, rules, sha="a" * 64)
    assert codes(findings) == {"DUPLICATE_FILE"}            # reported once, not again as a duplicate number
    assert rules.reason("DUPLICATE_FILE").action == "REJECT"


def test_the_same_number_written_differently_is_caught(master, store, rules):
    # DQI/26-27/1187 is in the posted history; separators, case and leading zeros do not disguise it
    for disguise in ("DQI/26-27/1187", "dqi-26-27-1187", "DQI 26/27/01187", "DQI/26-27/1187 "):
        inv = make_invoice(vendor="V002", number=disguise, po="PO-2026-0178",
                           lines=(("Managed network switch 24 port gigabit", 6, "18500.00"),))
        findings, _ = check(inv, "V002", master, store, rules)
        assert codes(findings) == {"DUPLICATE_INVOICE_NUMBER"}, disguise


def test_the_same_number_from_another_vendor_is_not_a_duplicate(master, store, rules):
    inv = make_invoice(vendor="V001", number="DQI/26-27/1187")
    assert check(inv, "V001", master, store, rules)[0] == []


def test_same_content_new_number_with_no_room_on_the_po_is_rejected(master, store, rules):
    inv = make_invoice(vendor="V002", number="DQI/26-27/1193", inv_date="2026-11-05", po="PO-2026-0171",
                       lines=((LAPTOP, 8, "58500.00"), (DOCK, 8, "9200.00")))
    findings, rows = check(inv, "V002", master, store, rules)
    assert codes(findings) == {"DUPLICATE_CONTENT"}
    f = findings[0]
    assert "DQI/26-27/1187" in f.summary and "2 day(s) apart" in f.summary and "fully billed" in f.summary
    assert f.evidence["line_overlap"] == 1.0 and f.evidence["po_has_room"] is False and f.vendor_issue


def test_same_content_new_number_with_room_on_the_po_asks_a_person(master, store, rules):
    post(store, "T1", "V003", "PO-2026-0212", [(1, WRAP, 20, "12800.00")], number="MPP/100", inv_date="2026-11-05")
    inv = make_invoice(vendor="V003", number="MPP/107", inv_date="2026-11-10", po="PO-2026-0212", lines=((WRAP, 20, "640.00"),))
    findings, rows = check(inv, "V003", master, store, rules)
    assert codes(findings) == {"POSSIBLE_DUPLICATE"} and rules.reason("POSSIBLE_DUPLICATE").action == "HOLD"
    assert "second delivery" in findings[0].question and rows[2]["Result"] == "REVIEW"


def test_a_second_delivery_covered_by_goods_receipts_is_cleared_without_asking(master, store, rules):
    post(store, "T1", "V001", "PO-2026-0147", [(1, CHAIR, 10, "45000.00")], number="TOF/100", inv_date="2026-11-05")
    inv = make_invoice(number="TOF/107", inv_date="2026-11-10", lines=((CHAIR, 10, "4500.00"),))
    findings, rows = check(inv, "V001", master, store, rules)       # 40 chairs received, 20 billed in total
    assert findings == [] and "goods receipts cover both deliveries" in rows[2]["Detail"]


def test_a_monthly_bill_of_the_same_amount_is_not_a_duplicate(master, store, rules):
    inv = make_invoice(vendor="V006", number="VSF/26-27/11", inv_date="2026-11-15", po="PO-2026-0160",
                       lines=((SERVICE, None, "60000.00"),), bank_account="004598001234", ifsc="HMBL0000459")
    assert check(inv, "V006", master, store, rules)[0] == []        # last month's bill is 31 days back


def test_window_boundary(master, store, rules):
    assert rules.duplicates.window_days == 15
    post(store, "T1", "V003", "PO-2026-0212", [(1, WRAP, 20, "12800.00")], number="MPP/100", inv_date="2026-10-20")
    inside = make_invoice(vendor="V003", number="MPP/107", inv_date="2026-11-04", po="PO-2026-0212", lines=((WRAP, 20, "640.00"),))
    outside = make_invoice(vendor="V003", number="MPP/107", inv_date="2026-11-05", po="PO-2026-0212", lines=((WRAP, 20, "640.00"),))
    assert codes(check(inside, "V003", master, store, rules)[0]) == {"POSSIBLE_DUPLICATE"}     # 15 days
    assert check(outside, "V003", master, store, rules)[0] == []                                # 16 days


def test_same_amount_but_different_items_is_not_a_duplicate(master, store, rules):
    post(store, "T1", "V003", "PO-2026-0212", [(1, WRAP, 20, "12800.00")], number="MPP/100", inv_date="2026-11-05")
    inv = make_invoice(vendor="V003", number="MPP/107", inv_date="2026-11-08", po="PO-2026-0212",
                       lines=(("Bubble wrap roll 1 m x 100 m", 8, "1600.00"),))            # also Rs 12,800 before tax
    findings, _ = check(inv, "V003", master, store, rules)
    assert "POSSIBLE_DUPLICATE" not in codes(findings) and "DUPLICATE_CONTENT" not in codes(findings)


def test_invoices_waiting_for_review_count_and_rejected_ones_do_not(master, store, rules):
    post(store, "T1", "V001", "PO-2026-0147", [(1, CHAIR, 10, "45000.00")], number="TOF/555", status="PENDING")
    post(store, "T2", "V001", "PO-2026-0147", [(1, CHAIR, 10, "45000.00")], number="TOF/556", status="REJECTED")
    held = make_invoice(number="TOF/555", lines=((CHAIR, 5, "4500.00"),))
    freed = make_invoice(number="TOF/556", lines=((CHAIR, 5, "4500.00"),))
    assert codes(check(held, "V001", master, store, rules)[0]) == {"RESUBMITTED_WHILE_ON_HOLD"}
    assert check(freed, "V001", master, store, rules)[0] == []


def test_same_number_as_a_booked_invoice_is_rejected_but_as_a_held_one_is_a_possible_correction(master, store, rules):
    post(store, "T1", "V001", "PO-2026-0147", [(1, CHAIR, 10, "45000.00")], number="TOF/600", status="POSTED")
    post(store, "T2", "V001", "PO-2026-0147", [(1, CHAIR, 10, "45000.00")], number="TOF/601", status="PENDING")
    booked = check(make_invoice(number="TOF/600", lines=((CHAIR, 5, "4500.00"),)), "V001", master, store, rules)[0]
    on_hold = check(make_invoice(number="TOF/601", lines=((CHAIR, 5, "4500.00"),)), "V001", master, store, rules)[0]
    assert codes(booked) == {"DUPLICATE_INVOICE_NUMBER"} and rules.reason("DUPLICATE_INVOICE_NUMBER").action == "REJECT"
    assert "credit note" in booked[0].vendor_issue
    assert codes(on_hold) == {"RESUBMITTED_WHILE_ON_HOLD"} and rules.reason("RESUBMITTED_WHILE_ON_HOLD").action == "HOLD"
    assert "corrected" in on_hold[0].question and on_hold[0].evidence["original_ledger_id"] == "T2"


def test_an_invoice_without_a_number_is_still_compared_on_content(master, store, rules):
    post(store, "T1", "V003", "PO-2026-0212", [(1, WRAP, 20, "12800.00")], number="MPP/100", inv_date="2026-11-05")
    inv = make_invoice(vendor="V003", number=None, inv_date="2026-11-06", po="PO-2026-0212", lines=((WRAP, 20, "640.00"),))
    findings, rows = check(inv, "V003", master, store, rules)
    assert rows[1]["Result"] == "n/a" and codes(findings) == {"POSSIBLE_DUPLICATE"}
