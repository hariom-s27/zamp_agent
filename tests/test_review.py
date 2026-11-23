"""A hold ends with a named person's decision, recorded in the same chain."""
from decimal import Decimal

import pytest

from invoice_desk.review import ReviewError, apply_review, required_checks

from .conftest import sample_bytes


def held(pipeline, sample):
    result = pipeline.run(sample_bytes(sample), f"{sample}.pdf", "test")
    assert result.verdict == "HOLD"
    return result


def test_approving_a_hold_posts_it_and_writes_a_review_receipt(pipeline, store):
    run = held(pipeline, "08_E4b_tax_inclusive_over_tolerance")
    assert store.get_ledger(run.ledger_id)["status"] == "PENDING"
    receipt = apply_review(store, run.run_id, "approve", "R. Iyer", "Price increase agreed by email on 12 Nov")
    assert store.get_ledger(run.ledger_id)["status"] == "POSTED" and store.run_row(run.run_id)["review_status"] == "APPROVED"
    assert receipt["actor"] == "human" and receipt["reviewer"] == "R. Iyer" and receipt["reviews_receipt"] == run.receipt["hash"]
    assert receipt["reasons_reviewed"] == ["PRICE_VARIANCE"]
    assert [r["kind"] for r in store.receipts(run.run_id)] == ["decision", "review"] and store.verify_chain()["ok"]


def test_rejecting_a_hold_frees_the_po_and_the_invoice_number(pipeline, store):
    run = held(pipeline, "08_E4b_tax_inclusive_over_tolerance")
    apply_review(store, run.run_id, "REJECT", "R. Iyer", "Vendor to re-issue at the PO price")
    assert store.get_ledger(run.ledger_id)["status"] == "REJECTED"
    assert store.po_usage("PO-2026-0229")["pending_amount"] == 0


def test_a_review_needs_a_named_reviewer_and_a_valid_decision(pipeline, store):
    run = held(pipeline, "08_E4b_tax_inclusive_over_tolerance")
    with pytest.raises(ReviewError, match="reviewer's name"):
        apply_review(store, run.run_id, "APPROVE", "  ")
    with pytest.raises(ReviewError, match="APPROVE, REJECT or HANDLED"):
        apply_review(store, run.run_id, "MAYBE", "R. Iyer")
    with pytest.raises(ReviewError, match="does not exist"):
        apply_review(store, "R-9999", "APPROVE", "R. Iyer")


def test_a_run_cannot_be_reviewed_twice_or_when_it_was_not_held(pipeline, store):
    run = held(pipeline, "08_E4b_tax_inclusive_over_tolerance")
    apply_review(store, run.run_id, "APPROVE", "R. Iyer")
    with pytest.raises(ReviewError, match="not waiting for review"):
        apply_review(store, run.run_id, "REJECT", "R. Iyer")
    approved = pipeline.run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    with pytest.raises(ReviewError, match="not waiting for review"):
        apply_review(store, approved.run_id, "REJECT", "R. Iyer")


CALL = "Called 080-5550-0118, spoke to Ms Hegde (accounts head), 21 Nov 15:10; she read out the new account"


def approve_bank_change(store, run_id, first="N. Rao", second="S. Menon"):
    """The two approvals a changed bank account needs (rules.yaml, review.bank_change_approvers)."""
    apply_review(store, run_id, "APPROVE", first, CALL, {"bank_verified": True})
    return apply_review(store, run_id, "APPROVE", second, "Call record read", {"bank_second_check": True})


def test_bank_change_needs_a_confirmed_call_back_and_is_then_remembered(pipeline, store):
    run = held(pipeline, "09_fraud_guard_bank_change")
    assert "bank_verified" in required_checks({f.code for f in run.decision.reasons})
    assert "bank_second_check" in required_checks({f.code for f in run.decision.reasons}, second=True)
    with pytest.raises(ReviewError, match="call-back"):
        apply_review(store, run.run_id, "APPROVE", "N. Rao")
    assert store.get_ledger(run.ledger_id)["status"] == "PENDING"            # the refused approval changed nothing
    receipt = approve_bank_change(store, run.run_id)
    assert store.overrides("V001", "bank_account")[0]["value"] == "770031245566"
    assert any("verified account" in item for item in receipt["learned"])


def test_after_the_call_back_the_same_account_no_longer_holds_invoices(pipeline, store):
    """What two reviewers confirm is remembered: the next invoice to that account goes straight through."""
    from datetime import date
    from scripts import generate_test_data as G
    bank = {"bank_name": "Chalukya Urban Bank", "bank_branch": "Yeshwanthpur", "bank_account": "770031245566", "ifsc": "CHUB0002290"}
    def next_invoice(number):
        return G.render_invoice_pdf(G.spec(
            id="followup", layout="classic", vendor="V001", invoice_no=number, invoice_date=date(2026, 11, 18),
            po_ref="PO-2026-0147", po_date=date(2026, 10, 20), bank_override=bank,
            lines=[G.line("Steel Storage Cupboard, 4 Shelf", "9403", 2, "Nos", "9800.00")]))
    before = pipeline.run(next_invoice("TOF/26-27/0450"), "before.pdf", "test")
    assert [f.code for f in before.decision.reasons] == ["BANK_DETAILS_CHANGED"]
    apply_review(store, before.run_id, "APPROVE", "N. Rao", CALL, {"bank_verified": True})
    meanwhile = pipeline.run(next_invoice("TOF/26-27/0451"), "meanwhile.pdf", "test")
    assert meanwhile.verdict == "HOLD"                                     # one approval is not enough to trust the account
    apply_review(store, before.run_id, "APPROVE", "S. Menon", "Call record read", {"bank_second_check": True})
    after = pipeline.run(next_invoice("TOF/26-27/0452"), "after.pdf", "test")
    assert after.verdict == "APPROVE"


def test_a_rejected_bank_change_is_not_remembered(pipeline, store):
    run = held(pipeline, "09_fraud_guard_bank_change")
    apply_review(store, run.run_id, "REJECT", "N. Rao", "Vendor says they never changed their account")
    assert store.overrides("V001", "bank_account") == []


def test_an_invoice_without_a_number_needs_one_before_approval(pipeline, store):
    run = held(pipeline, "06_E3_scan_no_number_implied_po")
    with pytest.raises(ReviewError, match="no invoice number"):
        apply_review(store, run.run_id, "APPROVE", "A. Kulkarni")
    apply_review(store, run.run_id, "APPROVE", "A. Kulkarni", "Number given by vendor on phone", {"invoice_number": "MPP/26-27/0388"})
    entry = store.get_ledger(run.ledger_id)
    assert entry["status"] == "POSTED" and entry["invoice_no"] == "MPP/26-27/0388"
    assert store.po_usage("PO-2026-0212")["posted_amount"] > 0               # the inferred PO is now used up


def test_a_number_already_on_record_is_refused_on_review(pipeline, store):
    from .test_po_match import post
    post(store, "T1", "V003", "PO-2026-0188", [(1, "PP strapping roll", 1, "1890.00")], number="MPP/26-27/0388")
    run = held(pipeline, "06_E3_scan_no_number_implied_po")
    with pytest.raises(ReviewError, match="already on record"):
        apply_review(store, run.run_id, "APPROVE", "A. Kulkarni", "", {"invoice_number": "mpp-26-27-388"})
    assert store.get_ledger(run.ledger_id)["status"] == "PENDING"


def _dollar_invoice() -> bytes:
    import io
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(72, 730, "INVOICE")
    c.setFont("Helvetica", 10)
    for x, y, text in ((72, 705, "Acme Cloud Hosting, Inc."), (400, 730, "Invoice #: INV-20418"), (400, 716, "Date: November 12, 2026"),
                       (72, 660, "Bill To:"), (72, 647, "Nilgiri Precision Components Pvt Ltd"),
                       (72, 600, "Description"), (330, 600, "Qty"), (400, 600, "Unit Price"), (490, 600, "Amount"),
                       (72, 582, "Cloud compute, reserved instances"), (330, 582, "3"), (400, 582, "$1,200.00"), (490, 582, "$3,600.00"),
                       (72, 564, "Premium support plan"), (330, 564, "1"), (400, 564, "$400.00"), (490, 564, "$400.00"),
                       (400, 530, "Subtotal"), (490, 530, "$4,000.00"), (400, 514, "Total Due"), (490, 514, "$4,000.00")):
        c.drawString(x, y, text)
    c.save()
    return buf.getvalue()


def test_a_foreign_currency_invoice_is_read_then_handed_to_a_person(pipeline, store):
    r = pipeline.run(_dollar_invoice(), "acme.pdf", "test")
    assert r.verdict == "HOLD" and [f.code for f in r.decision.reasons] == ["CURRENCY_NOT_SUPPORTED"]
    assert r.invoice.get("total") == "4000.00" and len(r.invoice.line_items) == 2          # it was read
    assert "USD 4,000.00" in r.decision.explanation and "\u20b9" not in r.decision.explanation   # never shown in rupees
    assert [s.status for s in r.stages[3:7]] == ["skipped"] * 4 and r.ledger_id is None  # nothing compared in rupees, nothing booked
    assert store.list_runs()[0]["total"] == "USD 4000.00"


def test_a_document_the_system_did_not_book_is_closed_by_hand_not_approved(pipeline, store):
    r = pipeline.run(_dollar_invoice(), "acme.pdf", "test")
    with pytest.raises(ReviewError, match="cannot be approved"):
        apply_review(store, r.run_id, "APPROVE", "N. Rao")
    receipt = apply_review(store, r.run_id, "HANDLED", "N. Rao", "Paid by wire through treasury, ref TT-2291")
    assert store.run_row(r.run_id)["review_status"] == "HANDLED" and receipt["ledger"] is None and receipt["decision"] == "HANDLED"
    assert store.verify_chain()["ok"]


def test_a_booked_invoice_cannot_be_waved_through_as_handled(pipeline, store):
    run = held(pipeline, "08_E4b_tax_inclusive_over_tolerance")
    with pytest.raises(ReviewError, match="approve it or reject it"):
        apply_review(store, run.run_id, "HANDLED", "R. Iyer")


def test_a_corrected_copy_of_a_held_invoice_replaces_it_only_after_the_first_is_decided(pipeline, store):
    """Sample 08 is held for a price above tolerance. The vendor re-issues the same invoice
    number at the PO price. Rejecting the correction as a duplicate would be wrong; so
    would leaving both open."""
    from datetime import date
    from scripts import generate_test_data as G
    first = held(pipeline, "08_E4b_tax_inclusive_over_tolerance")
    corrected = G.render_invoice_pdf(G.spec(
        id="corrected", layout="retail", vendor="V005", invoice_no="SIS/2327", invoice_date=date(2026, 11, 19),
        po_ref="PO-2026-0229", tax_inclusive=True, lines=[G.line("Laser Toner Cartridge 26A (Compatible)", "8443", 30, "Nos", "3776.00")]))
    second = pipeline.run(corrected, "SIS_2327_corrected.pdf", "test")
    assert second.verdict == "HOLD" and [f.code for f in second.decision.reasons] == ["RESUBMITTED_WHILE_ON_HOLD"]
    assert first.run_id in second.decision.explanation
    with pytest.raises(ReviewError, match="still on hold"):
        apply_review(store, second.run_id, "APPROVE", "R. Iyer")
    apply_review(store, first.run_id, "REJECT", "R. Iyer", "Superseded by the corrected invoice")
    apply_review(store, second.run_id, "APPROVE", "R. Iyer", "Corrected copy at the PO price")
    assert store.get_ledger(first.ledger_id)["status"] == "REJECTED" and store.get_ledger(second.ledger_id)["status"] == "POSTED"
    assert store.po_usage("PO-2026-0229")["posted_amount"] == 96000


def test_if_the_first_was_approved_meanwhile_the_copy_can_only_be_rejected(pipeline, store):
    from datetime import date
    from scripts import generate_test_data as G
    first = held(pipeline, "08_E4b_tax_inclusive_over_tolerance")
    copy = G.render_invoice_pdf(G.spec(
        id="copy", layout="retail", vendor="V005", invoice_no="SIS/2327", invoice_date=date(2026, 11, 19),
        po_ref="PO-2026-0229", tax_inclusive=True, lines=[G.line("Laser Toner Cartridge 26A (Compatible)", "8443", 30, "Nos", "3776.00")]))
    second = pipeline.run(copy, "copy.pdf", "test")
    apply_review(store, first.run_id, "APPROVE", "R. Iyer", "Higher price accepted")
    with pytest.raises(ReviewError, match="has since been approved"):
        apply_review(store, second.run_id, "APPROVE", "R. Iyer")
    apply_review(store, second.run_id, "REJECT", "R. Iyer")
    assert store.po_usage("PO-2026-0229")["posted_amount"] == Decimal("51715.25")   # sample 08: 16 x 3,814 with tax, 51,715.25 before tax
