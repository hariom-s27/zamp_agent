"""Round 4 checkpoint 3: the AP inbox sorts, routes and times every message (invoice_desk/inbox.py)."""
import json
import sqlite3
from datetime import date
from email.message import EmailMessage
from email import policy as email_policy

import pytest

from invoice_desk import config, inbox
from invoice_desk.store import TABLES
from scripts import generate_test_data as G
from tests.conftest import TODAY

AP = "ap@nilgiri-precision.example"


def _eml(sender="billing@deccanquill.example", subject="Hello", text="Hi.\n", attachments=(), mid="<x1@test.example>"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = sender, AP, subject, mid
    m["Date"] = "Sat, 21 Nov 2026 09:00:00 +0530"
    m.set_content(text, cte="8bit")
    for name, data, mime in attachments:
        main, sub = mime.split("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return m.as_bytes(policy=email_policy.SMTP)


def _deliver(store, master, rules, pipeline, *files):
    return inbox.deliver(store, master, rules, pipeline, list(files), TODAY)


def _counts(store) -> dict:
    with sqlite3.connect(store.path) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def _pdf(title: str, number: str) -> bytes:
    inv = G.spec(id="t", layout="classic", vendor="V001", invoice_no=number, invoice_date=date(2026, 11, 10),
                 labels={"title": title}, lines=[G.line("Executive Desk 1800 mm", "9403", 3, "Nos", "32500.00")])
    return G.render_invoice_pdf(inv)


def test_same_file_twice_is_received_once(store, master, rules, pipeline):
    data = _eml(subject="Payment status please", text="What is the payment status of our invoices?\n")
    first = _deliver(store, master, rules, pipeline, ("a.eml", data))
    before, receipts = _counts(store), len(store.receipts())
    second = _deliver(store, master, rules, pipeline, ("a.eml", data))
    assert first[0]["status"] == "received" and second[0]["status"] == "already received"
    assert _counts(store) == before and len(store.receipts()) == receipts


def test_same_message_id_different_bytes_is_received_once(store, master, rules, pipeline):
    _deliver(store, master, rules, pipeline, ("a.eml", _eml(text="One.\n")))
    before = _counts(store)
    again = _deliver(store, master, rules, pipeline, ("b.eml", _eml(text="One, sent again.\n")))
    assert again[0]["status"] == "already received" and _counts(store) == before


def test_bank_change_wording_goes_to_vendor_master_and_changes_nothing(store, master, rules, pipeline):
    vendors_before = {k: v.model_dump() for k, v in master.vendors.items()}
    out = _deliver(store, master, rules, pipeline, ("m.eml", _eml(
        sender="accounts@tungabhadra-furnishings.example", subject="Bank update",
        text="We have a new bank account. Please update our bank details.\n")))
    item = store.get_message(out[0]["message_id"])
    assert item["kind"] == "bank_change_request" and item["owner"] == "Vendor master team"
    assert item["action"].startswith("Asks to change a bank account. The desk has changed nothing.")
    assert store.overrides() == [] and {k: v.model_dump() for k, v in master.vendors.items()} == vendors_before
    assert store.list_runs() == []


def test_invoice_email_with_bank_wording_is_an_invoice_with_a_flag(store, master, rules, pipeline):
    pdf = (config.SAMPLES_DIR / "desk" / "d1_clean_bearings.pdf").read_bytes()
    out = _deliver(store, master, rules, pipeline, ("m.eml", _eml(
        sender="accounts@kaveribearings.example", subject="Invoice",
        text="Invoice attached. Note our new bank account details below.\n",
        attachments=[("inv.pdf", pdf, "application/pdf")])))
    items = store.inbox()
    assert len(items) == 2
    main, extra = items
    assert main["kind"] == "invoice" and len(main["flags"]) == 1 and "bank" in main["flags"][0].lower()
    assert extra["kind"] == "bank_change_request" and extra["owner"] == "Vendor master team"
    assert extra["eml_sha256"] == main["eml_sha256"] and out[0]["message_id"] == main["message_id"]


def test_pdf_attachment_runs_once_with_source_email(store, master, rules, pipeline):
    pdf = (config.SAMPLES_DIR / "desk" / "d1_clean_bearings.pdf").read_bytes()
    out = _deliver(store, master, rules, pipeline, ("m.eml", _eml(
        sender="accounts@kaveribearings.example", attachments=[("inv.pdf", pdf, "application/pdf")])))
    runs = store.list_runs()
    assert len(runs) == 1 and runs[0]["source"] == "email"
    assert store.get_message(out[0]["message_id"])["run_ids"] == [runs[0]["run_id"]]


@pytest.mark.parametrize("title,kind", [("STATEMENT OF ACCOUNT", "statement"), ("CREDIT NOTE", "credit_note"),
                                        ("QUOTATION", "not_an_invoice")])
def test_kind_follows_the_engine_document_type(store, master, rules, pipeline, title, kind):
    out = _deliver(store, master, rules, pipeline, ("m.eml", _eml(
        sender="accounts@tungabhadra-furnishings.example",
        attachments=[("doc.pdf", _pdf(title, "Q/26-27/0091"), "application/pdf")])))
    item = store.get_message(out[0]["message_id"])
    assert item["kind"] == kind and len(item["run_ids"]) == 1
    assert item["owner"] == rules.inbox.owners[kind]


def test_too_many_attachments_goes_to_a_person_unopened(store, master, rules, pipeline):
    pdf = (config.SAMPLES_DIR / "desk" / "d1_clean_bearings.pdf").read_bytes()
    many = [(f"inv{i}.pdf", pdf, "application/pdf") for i in range(rules.inbox.max_attachments + 1)]
    out = _deliver(store, master, rules, pipeline, ("m.eml", _eml(sender="accounts@kaveribearings.example",
                                                                  attachments=many)))
    item = store.get_message(out[0]["message_id"])
    assert store.list_runs() == [] and item["state"] == "waiting" and item["owner"] == "AP analyst"
    assert item["action"] == "Too many or too large attachments. Nothing was opened."


def test_every_message_has_owner_state_and_due_date(store, master, rules, pipeline):
    inbox.deliver(store, master, rules, pipeline, inbox.bundled_mail(), TODAY)
    items = store.inbox()
    assert len(items) == 7
    for item in items:
        assert item["owner"] and item["state"] in inbox.STATES and item["due_on"] == "2026-11-23"
        assert item["route_rule"] and item["action"] and item["received_at"]


def test_closing_needs_a_name_and_writes_one_audit_entry(store, master, rules, pipeline):
    out = _deliver(store, master, rules, pipeline, ("a.eml", _eml(text="Season's greetings.\n")))
    mid = out[0]["message_id"]
    with pytest.raises(inbox.InboxError):
        inbox.close_item(store, mid, "  ", "read it", TODAY)
    before = len(store.receipts())
    inbox.close_item(store, mid, "Priya (AP)", "Greeting, nothing to do", TODAY)
    item = store.get_message(mid)
    assert item["state"] == "closed" and item["closed_by"] == "Priya (AP)" and item["closed_note"]
    assert len(store.receipts()) == before + 1 and store.verify_chain()["ok"]
    with pytest.raises(inbox.InboxError):
        inbox.close_item(store, mid, "Priya (AP)", "again", TODAY)


def test_route_an_unsorted_item_needs_a_name(store, master, rules, pipeline):
    out = _deliver(store, master, rules, pipeline, ("a.eml", _eml(text="Season's greetings.\n")))
    mid = out[0]["message_id"]
    with pytest.raises(inbox.InboxError):
        inbox.route_item(store, rules, mid, "status_query", "", TODAY)
    inbox.route_item(store, rules, mid, "status_query", "Priya (AP)", TODAY)
    item = store.get_message(mid)
    assert item["kind"] == "status_query" and "Priya (AP)" in item["route_rule"]


def test_each_received_message_writes_one_audit_entry(store, master, rules, pipeline):
    _deliver(store, master, rules, pipeline, ("a.eml", _eml(text="Season's greetings.\n")))
    entries = [r for r in store.receipts() if r["kind"] == "inbox"]
    assert len(entries) == 1
    body = entries[0]["body"]
    assert body["eml_sha256"] and body["sender"] == "billing@deccanquill.example" and body["message_kind"] == "other"
    assert body["route_rule"]


def test_inbox_off_reads_nothing(store, master, rules, pipeline):
    off = rules.model_copy(update={"inbox": rules.inbox.model_copy(update={"enabled": False})})
    before = _counts(store)
    assert inbox.deliver(store, master, off, pipeline, inbox.bundled_mail(), TODAY) == []
    assert _counts(store) == before


def test_bundled_mailbox_outcomes(store, master, rules, pipeline):
    out = inbox.deliver(store, master, rules, pipeline, inbox.bundled_mail(), TODAY)
    assert [o["status"] for o in out] == ["received"] * 7
    got = [(i["kind"], i["owner"], i["state"]) for i in store.inbox()]
    assert got == [("invoice", inbox.DESK, "sorted"), ("invoice", "AP analyst", "waiting"),
                   ("status_query", "AP analyst", "waiting"), ("bank_change_request", "Vendor master team", "waiting"),
                   ("statement", "AP analyst", "waiting"), ("other", "AP analyst", "waiting"),
                   ("invoice", inbox.DESK, "sorted")]
    expected = [m["expected_kind"] for m in json.loads(
        (config.SAMPLES_DIR / "desk" / "mail" / "manifest.json").read_text(encoding="utf-8"))]
    assert [g[0] for g in got] == expected
    items = store.inbox()
    assert items[0]["action"].startswith("Invoice read and approved: ") and "No person needed." in items[0]["action"]
    case = store.case_for_run(items[1]["run_ids"][0])
    assert items[1]["action"] == f"Invoice read and held: Invoice number missing. Case {case['case_id']} opened for AP analyst."
    assert items[1]["case_id"] == case["case_id"]
    assert items[3]["sender_check"] == "lookalike" and "tungabhadra-furnishing.example" in items[3]["sender_detail"]
    assert items[6]["action"].startswith("This file was already received on ")
    assert items[6]["run_ids"] == [] and len(store.list_runs()) == 2
    assert items[2]["action"] == ("Payment question about 2 invoice(s). An answer is drafted from the records for "
                                  "you to approve.")
    assert store.verify_chain()["ok"]


def test_a_reply_with_an_open_case_token_goes_to_its_case(store, master, rules, pipeline):
    inbox.deliver(store, master, rules, pipeline, inbox.bundled_mail()[1:2], TODAY)
    case = store.list_cases()[0]
    out = _deliver(store, master, rules, pipeline, ("r.eml", _eml(
        sender="billing@nandihillspack.example", subject=f"Re: [{case['case_id']}] Invoice number",
        text="The number is NHP/26-27/0301.\n", mid="<r1@nandihillspack.example>")))
    item = store.get_message(out[0]["message_id"])
    assert item["kind"] == "reply" and item["case_id"] == case["case_id"]
    assert item["action"].startswith(f"Reply matched to case {case['case_id']}. ")
    assert "A person should read this reply. Reason: no AI answer is available" in item["action"]   # the model is off here


def test_overdue_items_are_listed_first(store, master, rules, pipeline):
    inbox.deliver(store, master, rules, pipeline, inbox.bundled_mail(), TODAY)
    rows = inbox.listing(store, date(2026, 11, 25))
    waiting = [r for r in rows if r["state"] == "waiting"]
    assert rows[:len(waiting)] == waiting and all(r["overdue"] for r in waiting)
    figures = inbox.figures(store, date(2026, 11, 25))
    assert figures == {"received": 7, "sorted": 2, "waiting": 5, "oldest_days": 4}
