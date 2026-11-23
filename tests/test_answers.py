"""Round 4 checkpoint 6: payment-status answers, written by rules from the records, sent only
after a named person approves (invoice_desk/answers.py)."""
import json
from email import message_from_bytes, policy as email_policy
from email.message import EmailMessage

import pytest

from invoice_desk import answers, cases, config, inbox, payments, samples
from invoice_desk.llm import LlmAssist
from tests.conftest import TODAY
from tests.test_llm import FakeApi, assistant

AP = "ap@nilgiri-precision.example"
DRAFTS: list[str] = []          # every draft produced here, for the "never says paid, never a bank detail" check


def _eml(sender="billing@deccanquill.example", subject="Payment status", text="", mid="<q1@test.example>") -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = sender, AP, subject, mid
    m["Date"] = "Sat, 21 Nov 2026 09:00:00 +0530"
    m.set_content(text, cte="8bit")
    return m.as_bytes(policy=email_policy.SMTP)


def _ask(store, master, rules, pipeline, data, name="q.eml"):
    out = inbox.deliver(store, master, rules, pipeline, [(name, data)], TODAY)
    return store.get_message(out[0]["message_id"])


def _draft(store, master, rules, item):
    d = answers.draft(store, master, rules, item, TODAY)
    if d.get("text"):
        DRAFTS.append(d["text"])
    return d


def _run(pipeline, sid):
    by_id = {s["id"]: s for s in samples.load_manifest() + samples.load_case_manifest() + samples.load_desk_manifest()}
    return pipeline.run(samples.sample_bytes(by_id[sid]), samples.received_name(by_id[sid]), source="cli")


def m3() -> bytes:
    return (config.SAMPLES_DIR / "desk" / "mail" / "m3_payment_question.eml").read_bytes()


def test_unknown_sender_gets_no_draft(store, master, rules, pipeline):
    item = _ask(store, master, rules, pipeline, _eml(sender="someone@festive-offers.example",
                                                     text="What is the payment status of DQI/26-27/1187?\n"))
    d = _draft(store, master, rules, item)
    assert d["status"] == "refused" and d["text"] == ""
    assert item["action"].startswith("Sender is not on file for any vendor. No invoice details are shared.")
    assert [m for m in store.messages("") if m["direction"] == "out"] == []


def test_shared_domain_sender_gets_no_draft(store, master, rules, pipeline):
    item = _ask(store, master, rules, pipeline, _eml(sender="deccanquill.billing@gmail.com",
                                                     text="Payment status of DQI/26-27/1187 please.\n"))
    assert _draft(store, master, rules, item)["status"] == "refused"


def test_invoice_numbers_found_despite_spacing_and_case(store, master, rules, pipeline):
    item = _ask(store, master, rules, pipeline, _eml(text="What is the payment status of dqi 26-27 1187?\n"))
    d = _draft(store, master, rules, item)
    assert [f["invoice_no"] for f in d["facts"]] == ["DQI/26-27/1187"]
    assert "DQI/26-27/1187 dated 03-Nov-2026 for ₹6,39,088.00: Approved. Scheduled for payment on 18-Dec-2026 (not in this week's run)." in d["text"]


def test_not_found_asks_for_a_copy(store, master, rules, pipeline):
    d = _draft(store, master, rules, _ask(store, master, rules, pipeline, m3(), "m3.eml"))
    assert ("We have no record of receiving invoice DQI/26-27/1193. Please send a copy to "
            "ap@nilgiri-precision.example.") in d["text"]
    assert d["text"].startswith("Subject: RE: Payment status: DQI/26-27/1187 and DQI/26-27/1193\n\n"
                                "Dear Deccan Quill Infotech LLP accounts team,\n\n"
                                "Here is where your invoices stand in our records on 21-Nov-2026.")
    assert d["text"].rstrip().endswith("Accounts Payable, Nilgiri Precision Components Pvt Ltd\nap@nilgiri-precision.example")


def test_duplicate_is_explained_with_the_existing_vendor_sentence(store, master, rules, pipeline):
    dup = _run(pipeline, "05_E2_disguised_duplicate")
    issue = next(f.vendor_issue for f in dup.decision.reasons if f.vendor_issue)
    d = _draft(store, master, rules, _ask(store, master, rules, pipeline, m3(), "m3.eml"))
    assert f"DQI/26-27/1193 dated 05-Nov-2026 for ₹6,39,088.00: Not accepted. {answers._sentence(issue)}" in d["text"]
    assert all(line.strip()[-1] in ".:," for line in d["text"].splitlines() if line.strip() and not line.startswith(("Subject", "ap@", "Accounts Payable")))
    assert "DQI/26-27/1187" in issue


def test_status_sentence_for_each_state(store, master, rules, pipeline):
    run_ids = {sid: _run(pipeline, sid) for sid in ("01_happy_path_furniture", "07_E4a_tax_inclusive_within_tolerance",
                                                     "09_fraud_guard_bank_change", "08_E4b_tax_inclusive_over_tolerance",
                                                     "c1_invoice_before_goods")}
    cases.sync(store, master, rules, TODAY)
    p = payments.propose(store, master, rules, TODAY)
    payments.approve(store, master, rules, TODAY, p["fingerprint"], ["N. Rao"], True, TODAY)
    s = lambda vendor_id, number: answers.status_line(store, master, rules, vendor_id, number, TODAY)  # noqa: E731
    assert s("V005", "SIS/2311")["sentence"] == ("Approved and included in the payment run approved on 21-Nov-2026. "
                                                 "Payment is released by our Finance team through the bank.")
    assert s("V001", "TOF/26-27/0412")["sentence"] == (
        "Approved. Payment is waiting for a verification on your vendor record. Our team will contact you on the "
        "phone number we hold for you.")
    case09 = store.case_for_run(run_ids["09_fraud_guard_bank_change"].run_id)
    assert s("V001", "TOF/26-27/0431")["sentence"] == (f"Received on 21-Nov-2026 and being checked. "
                                                       f"Reference {case09['case_id']}.")        # a fraud check is never named
    run08 = run_ids["08_E4b_tax_inclusive_over_tolerance"]
    issue = next(f.vendor_issue for f in run08.decision.reasons if f.vendor_issue).strip().rstrip(".")
    case08 = store.case_for_run(run08.run_id)
    assert s("V005", "SIS/2327")["sentence"] == f"On hold. {answers._sentence(issue)} Reference {case08['case_id']}."
    case_c1 = store.case_for_run(run_ids["c1_invoice_before_goods"].run_id)
    assert s("V101", "KBD/26-27/1193")["sentence"] == f"Received on 21-Nov-2026 and being checked. Reference {case_c1['case_id']}."
    assert s("V002", "DQI/26-27/1187")["sentence"] == "Approved. Scheduled for payment on 18-Dec-2026 (not in this week's run)."
    assert s("V002", "NOPE/1")["sentence"] == ("We have no record of receiving invoice NOPE/1. Please send a copy to "
                                               "ap@nilgiri-precision.example.")


def test_no_number_lists_open_invoices_up_to_limit(store, master, rules, pipeline):
    for sid in ("10_ai_assist_letter_invoice", "05_E2_disguised_duplicate"):
        _run(pipeline, sid)
    item = _ask(store, master, rules, pipeline, _eml(text="Could you tell us the payment status of our invoices?\n",
                                                     mid="<m8@deccanquill.example>"), "m8.eml")
    d = _draft(store, master, rules, item)
    history = [e for e in store.ledger_entries(vendor_id="V002") if e["status"] in ("POSTED", "PENDING") and e["invoice_no"]]
    assert 0 < len(d["facts"]) <= rules.answers.max_listed and len(d["facts"]) == min(len(history), rules.answers.max_listed)
    dates = [f["invoice_date"] for f in d["facts"]]
    assert dates == sorted(dates, reverse=True)
    small = rules.model_copy(update={"answers": rules.answers.model_copy(update={"max_listed": 1})})
    assert len(answers.draft(store, master, small, item, TODAY)["facts"]) == 1


def test_answer_goes_to_address_on_file(store, master, rules, pipeline):
    item = _ask(store, master, rules, pipeline, _eml(sender="ravi@deccanquill.example",
                                                     text="Payment status of DQI/26-27/1187?\n"))
    assert item["sender_check"] == "same_domain"
    d = _draft(store, master, rules, item)
    sent = answers.approve(store, master, rules, item["message_id"], "Priya (AP)", d["sha256"], TODAY)
    mail = message_from_bytes(sent["path"].read_bytes(), policy=email_policy.default)
    assert mail["To"] == "billing@deccanquill.example" and "ravi@" not in sent["path"].read_text(encoding="utf-8")


def test_nothing_is_written_until_a_named_person_approves(store, master, rules, pipeline):
    item = _ask(store, master, rules, pipeline, m3(), "m3.eml")
    d = _draft(store, master, rules, item)
    outbox = cases.outbox_dir(store)
    assert list(outbox.glob("*.eml")) == []
    with pytest.raises(answers.AnswerError):
        answers.approve(store, master, rules, item["message_id"], "  ", d["sha256"], TODAY)
    with pytest.raises(answers.AnswerError, match="changed"):
        answers.approve(store, master, rules, item["message_id"], "Priya (AP)", "stale", TODAY)
    before = len(store.receipts())
    sent = answers.approve(store, master, rules, item["message_id"], "Priya (AP)", d["sha256"], TODAY)
    assert list(outbox.glob("*.eml")) == [sent["path"]] and len(store.receipts()) == before + 1
    data = sent["path"].read_bytes()
    assert b"\n" not in data.replace(b"\r\n", b"") and b"quoted-printable" not in data.lower()
    assert store.get_message(item["message_id"])["state"] == "answered"
    out = store.get_message(sent["message_id"])
    assert out["direction"] == "out" and out["in_reply_to"] == item["message_id"] and json.loads(out["facts_json"])
    with pytest.raises(answers.AnswerError):
        answers.approve(store, master, rules, item["message_id"], "Priya (AP)", d["sha256"], TODAY)


def test_overdue_invoice_flags_the_finance_controller(store, master, rules, pipeline):
    store.add_ledger({"ledger_id": "L-LATE", "run_id": "AP-2026-9997", "origin": "run", "vendor_id": "V102",
                      "invoice_no": "NHP/26-27/0101", "invoice_date": "2026-08-01", "total": "5000.00",
                      "status": "POSTED"}, [])
    item = _ask(store, master, rules, pipeline, _eml(sender="billing@nandihillspack.example",
                                                     text="Reminder: payment status of NHP/26-27/0101?\n"))
    d = _draft(store, master, rules, item)
    assert any(f.startswith("Finance controller: NHP/26-27/0101 is overdue in our records") and "legal pay-by date" in f
               for f in d["flags"])


def test_reword_refused_when_a_fact_changes(store, master, rules, pipeline, tmp_path):
    on = rules.model_copy(update={"answers": rules.answers.model_copy(update={"ai_reword": True})})
    item = _ask(store, master, rules, pipeline, _eml(text="Payment status of DQI/26-27/1187?\n"))
    plain = answers.draft(store, master, rules, item, TODAY)
    altered = {"subject": "RE: Payment status", "body": plain["body"].replace("18-Dec-2026", "28-Dec-2026")}
    d = answers.draft(store, master, on, item, TODAY, llm=assistant(tmp_path, FakeApi(altered)))
    assert d["text"] == plain["text"] and d["reworded"] is False


def test_answers_off(store, master, rules, pipeline):
    off = rules.model_copy(update={"answers": rules.answers.model_copy(update={"enabled": False})})
    out = inbox.deliver(store, master, off, pipeline, [("m3.eml", m3())], TODAY)
    item = store.get_message(out[0]["message_id"])
    assert item["kind"] == "status_query" and item["owner"] == "AP analyst"
    assert "Answers are switched off in the desk rules" in item["action"] and "answers.enabled" not in item["action"]
    assert answers.draft(store, master, off, item, TODAY)["status"] == "off"


def test_answer_never_contains_bank_details_or_the_word_paid(master):
    assert DRAFTS, "the other tests produce drafts"
    accounts = [v.bank_account for v in master.vendors.values() if v.bank_account]
    for text in DRAFTS:
        words = text.lower().replace(".", " ").replace(",", " ").split()
        assert "paid" not in words and "ifsc" not in words
        assert not any(a in text for a in accounts)
