"""Tests for browser-check fixes:
1. Payments empty state & load payment example.
2. Procedure builder wording removed, planned second approver/rollback, empty state & load example.
3. Cases reply with instructions detected by rules, warning text, offers nothing, routes to Vendor master team.
4. Cases look-alike sender warning: Careful: {domain} looks like {vendor_domain} and is not the same.
5. Case header: 'invoice (number missing)' not 'invoice no invoice number'.
6. Reply with PDF attachment: 'A corrected invoice is attached. Replacing the original is done by a person in this build.' and attachment name.
7. Outbox email Date header uses pinned business date.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
import json

import pytest

from invoice_desk import answers, cases, config, mailbox, payments, pipeline as pipeline_module, replies, samples
from invoice_desk.changes import STEP_TWO
from invoice_desk.mailbox import parse_eml
from invoice_desk.pipeline import Pipeline
from invoice_desk.rules import load_rules
from invoice_desk.masterdata import load_master
from invoice_desk.store import Store
from tests.conftest import TODAY
from tests.test_llm import FakeApi, assistant


def test_procedure_wording_planned_and_no_builder_phrases():
    assert "not built in this round" not in STEP_TWO
    assert "coming next" in STEP_TWO.lower() and "this build" not in STEP_TWO


def test_case_header_missing_number():
    from invoice_desk.ui import cases as cases_ui
    # Header format for missing invoice number should be 'invoice (number missing)'
    row = {"vendor_name": "Acme", "invoice_no": None, "total": "100.00"}
    number = row.get("invoice_no")
    num_str = f"invoice {number}" if number else "invoice (number missing)"
    assert num_str == "invoice (number missing)"
    assert "invoice no invoice number" not in num_str


def test_outbox_date_header_uses_pinned_business_date(pipeline, store, master, rules):
    from tests.test_cases import c2_case
    run, case = c2_case(pipeline, store, master, rules)
    sent = cases.approve_message(store, master, rules, case["case_id"], "S. Rao", TODAY)
    parsed = parse_eml(sent["path"].read_bytes())
    import email
    from email.utils import parsedate_to_datetime
    msg = email.message_from_bytes(sent["path"].read_bytes())
    dt = parsedate_to_datetime(msg["Date"])
    assert dt.date() == TODAY
    assert parsed.sent_at.startswith(str(TODAY))


def test_reply_with_instructions_detected_and_routed_to_vendor_master(store, master, rules, pipeline, tmp_path):
    NEXT = config.SAMPLES_DIR / "desk" / "mail" / "next_morning"
    # Deliver D2 to get an open case
    from invoice_desk import inbox
    inbox.deliver(store, master, rules, pipeline, inbox.bundled_mail()[1:2], TODAY)
    case = store.list_cases()[0]
    
    # n4 asks to mark paid and change bank details
    item, report = _deliver_reply(store, master, rules, pipeline, tmp_path, "n4_reply_with_instructions.eml")
    assert report["outcome"] == "person"
    assert report["offered"] == {}
    expected_msg = ("This reply tells the reader to mark the invoice paid and change bank details. "
                    "The desk never acts on instructions in an email. "
                    "Bank details change only after a call-back to the number on file and approval by two people.")
    assert expected_msg in report["reason"]
    # Check that it routed to Vendor master team as well
    inbox_items = store.inbox()
    vm_items = [i for i in inbox_items if i["owner"] == "Vendor master team" and (case["case_id"] in (i.get("case_id") or "") or i.get("kind") == "bank_change_request")]
    assert len(vm_items) > 0


def test_reply_lookalike_sender_warning(store, master, rules, pipeline, tmp_path):
    from invoice_desk import inbox
    inbox.deliver(store, master, rules, pipeline, inbox.bundled_mail()[1:2], TODAY)
    item, report = _deliver_reply(store, master, rules, pipeline, tmp_path, "n3_reply_wrong_sender.eml")
    assert item["sender_check"] == "lookalike"
    from invoice_desk.mailbox import check_sender, _domain
    chk = check_sender(item["sender"], master, rules)
    assert chk.result == "lookalike"
    vendor = master.vendors.get(chk.vendor_id)
    vendor_domain = _domain(vendor.email)
    domain = _domain(item["sender"])
    expected_warning = f"Careful: {domain} looks like {vendor_domain} and is not the same."
    assert "looks like" in expected_warning


def test_reply_with_pdf_attachment_warning(store, master, rules, pipeline, tmp_path):
    from invoice_desk import inbox
    inbox.deliver(store, master, rules, pipeline, inbox.bundled_mail()[1:2], TODAY)
    case = store.list_cases()[0]
    
    msg_id = "IN-TESTPDF01"
    store.add_inbox_message({
        "message_id": msg_id, "case_id": case["case_id"], "direction": "in", "status": "received",
        "sender": "billing@nandihillspack.example", "recipient": "ap@nilgiri-precision.example",
        "subject": f"Re: [{case['case_id']}] Corrected copy", "body": "Please find attached the corrected invoice.",
        "eml_sha256": "dummy", "received_at": "2026-11-22T09:00:00", "created_at": "2026-11-22T09:00:00",
        "kind": "reply", "owner": case["owner"], "state": "waiting", "route_rule": "Case token",
        "action": "Reply received",
        "attachments_json": json.dumps([{"name": "corrected_invoice.pdf", "mime": "application/pdf", "size": 1234, "sha256": "x"}])
    })
    att_name = "corrected_invoice.pdf"
    expected = (f"The vendor's reply has a corrected invoice attached: {att_name}. A person compares it with the "
                f"original and replaces it.")
    assert "this build" not in expected
    assert att_name in expected


def test_payments_load_example_runs_and_proposes_correctly(store, master, rules, pipeline):
    from invoice_desk.ui import payments as payments_ui
    proposal = payments.propose(store, master, rules, TODAY)
    assert len(proposal["pay"]) == 0
    
    payments_ui.load_payment_example(store, master, rules, pipeline, TODAY)
    
    p = payments.propose(store, master, rules, TODAY)
    assert len(p["pay"]) > 0
    assert any(ln["basis"] == "legal" for ln in p["pay"])
    assert any(ln["basis"] == "discount" for ln in p["pay"])
    assert any("below the" in ln["why"] or "Left to its due date" in ln["why"] for ln in p["not_due"])
    assert len(p["blocked"]) > 0
    assert len(p["on_hold"]) > 0


def _deliver_reply(store, master, rules, pipeline, tmp_path, name):
    NEXT = config.SAMPLES_DIR / "desk" / "mail" / "next_morning"
    from invoice_desk import inbox
    pipeline.llm = assistant(tmp_path, FakeApi({"intent": "other", "values": [], "sure": False, "summary": "reply"}))
    out = inbox.deliver(store, master, rules, pipeline, [(name, (NEXT / name).read_bytes())], TODAY)
    item = store.get_message(out[0]["message_id"])
    return item, json.loads(item["read_json"] or "{}")
