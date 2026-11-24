"""The round 4 pages, driven headlessly: Inbox, and later Payments and Procedure."""
import pytest

pytest.importorskip("streamlit")

from invoice_desk.store import Store  # noqa: E402
from tests.test_ui import said, shown, start  # noqa: E402


@pytest.fixture()
def app(tmp_path, monkeypatch):
    at = start(tmp_path, monkeypatch)
    at.db = Store(tmp_path / "ui.db")
    return at


def go(at, page, title):
    at.session_state["goto"] = page
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert [t.value for t in at.title] == [title]
    return at


def test_the_guided_demo_is_still_the_opening_page(app):
    assert [t.value for t in app.title] == ["Guided demo"]


def test_inbox_delivers_todays_mail_and_shows_who_has_the_next_step(app):
    go(app, "inbox", "Inbox")
    assert "No mailbox is connected yet. Messages arrive as files." in said(app)
    app.button(key="ib_deliver").click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert "7 messages received and sorted." in said(app)
    assert len(app.db.inbox()) == 7 and len(app.db.list_runs()) == 2
    table = app.table[0].value
    assert len(table) == 7 and set(table.columns) == {"When", "From", "Subject", "What it is", "What the desk did",
                                                      "Next step with", "Due"}
    labels = app.selectbox(key="ib_open").options
    bank = next(o for o in labels if o.startswith("Bank change request"))
    app.selectbox(key="ib_open").select(bank).run()
    assert "looks like tungabhadra-furnishings.example and is not the same" in said(app)
    assert "The desk has changed nothing." in shown(app)
    app.button(key="ib_deliver").click().run()                     # the same mail again changes nothing
    assert "0 messages received and sorted. 7 messages had already been received and changed nothing." in said(app)
    assert len(app.db.inbox()) == 7


def test_inbox_close_form_needs_a_name(app):
    go(app, "inbox", "Inbox")
    app.button(key="ib_deliver").click().run()
    other = next(o for o in app.selectbox(key="ib_open").options if o.startswith("Other"))
    app.selectbox(key="ib_open").select(other).run()
    mid = other.rsplit("(", 1)[1].rstrip(")")
    app.button(key=f"FormSubmitter:ib_close_{mid}-Close").click().run()
    assert any("Enter your name" in e.value for e in app.error)
    app.text_input(key=f"ib_close_name_{mid}").input("Priya (AP)")
    app.text_input(key=f"ib_close_note_{mid}").input("Greeting, nothing to do")
    app.button(key=f"FormSubmitter:ib_close_{mid}-Close").click().run()
    assert app.db.get_message(mid)["state"] == "closed"


def test_next_morning_replies_show_on_the_case_and_the_manual_path_without_a_recording(app):
    go(app, "inbox", "Inbox")
    app.button(key="ib_deliver").click().run()
    app.button(key="ib_deliver_next").click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    replies_in = [i for i in app.db.inbox() if i["kind"] == "reply"]
    assert len(replies_in) == 3
    assert len(app.table[0].value) == 10                          # exactly 3 rows added, one per reply
    assert next(m.value for m in app.metric if m.label == "Received") == "10"
    go(app, "cases", "Cases")
    text = " ".join(m.value for m in app.markdown)
    assert "Reply received from billing@nandihillspack.example" in text
    assert any("A person should read this reply. Reason: no AI answer is available" in w.value for w in app.warning)


def test_review_form_is_prefilled_from_an_offered_value(app):
    import json
    go(app, "inbox", "Inbox")
    app.button(key="ib_deliver").click().run()
    case = app.db.list_cases()[0]
    sentence = "The invoice number for our invoice dated 14 November 2026 is NHP/26-27/0301."
    app.db.update_case(case["case_id"], proposed_json=json.dumps({
        "invoice_number": {"value": "NHP/26-27/0301", "quote": sentence},
        "po_number": {"value": "PO-2026-1043", "quote": "It was raised against PO-2026-1043."}}))
    go(app, "review", "Review queue")
    assert app.text_input(key=f"rev_number_{case['run_id']}").value == "NHP/26-27/0301"
    words = said(app)
    assert f"From the vendor's reply, read by AI and checked against the reply text: \"{sentence}\"" in words
    assert "The vendor's reply states PO-2026-1043. This agrees with the PO on the invoice." in words

def test_payments_page_opens_and_says_nothing_is_paid(app):
    go(app, "payments", "Payments")
    assert "Nothing is paid from here." in said(app)
    assert "Proposal for 21-Nov-2026. It covers what must be paid by 28-Nov-2026." in shown(app)


def test_payments_two_people_approve_and_nothing_is_paid(app, tmp_path):
    from invoice_desk import samples
    from invoice_desk.pipeline import Pipeline
    from invoice_desk.llm import LlmAssist
    from invoice_desk import config
    from tests.conftest import TODAY
    pipe = Pipeline(store=app.db, llm=LlmAssist(config.LlmConfig(mode="off")), today=TODAY, ocr_mode="fixture")
    by_id = {s["id"]: s for s in samples.load_manifest() + samples.load_desk_manifest()}
    for sid in ("02_scanned_clean_safety", "07_E4a_tax_inclusive_within_tolerance", "p1_micro_supplier_due"):
        pipe.run(samples.sample_bytes(by_id[sid]), samples.received_name(by_id[sid]), source="cli")
    go(app, "payments", "Payments")
    assert "This run is above" in said(app)
    app.text_input(key="pay_name").input("N. Rao")
    app.checkbox(key="pay_auth").check()
    app.button(key="FormSubmitter:pay_approve_form-Approve the run").click().run()
    assert "First approval: N. Rao." in said(app)
    app.text_input(key="pay_name_2").input("n rao")
    app.checkbox(key="pay_auth_2").check()
    app.button(key="FormSubmitter:pay_approve_form-Approve the run").click().run()
    assert any("different person" in e.value for e in app.error)
    app.text_input(key="pay_name_2").input("S. Iyer")
    app.button(key="FormSubmitter:pay_approve_form-Approve the run").click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert "approved by N. Rao and S. Iyer. Nothing has been paid." in said(app)
    assert app.db.payment_runs()[0]["status"] == "APPROVED"
    assert all(e["status"] == "POSTED" for e in app.db.ledger_entries() if e["origin"] == "run")

def test_payment_question_answer_is_approved_from_the_inbox(app):
    go(app, "inbox", "Inbox")
    app.button(key="ib_deliver").click().run()
    q = next(o for o in app.selectbox(key="ib_open").options if o.startswith("Payment question"))
    app.selectbox(key="ib_open").select(q).run()
    mid = q.rsplit("(", 1)[1].rstrip(")")
    assert any("We have no record of receiving invoice DQI/26-27/1193." in t.value for t in app.text)
    assert "No mail server is connected." in said(app)
    app.button(key=f"FormSubmitter:ib_ans_{mid}-Approve and send").click().run()
    assert any("Enter your name" in e.value for e in app.error)
    app.text_input(key=f"ib_ans_name_{mid}").input("Priya (AP)")
    app.button(key=f"FormSubmitter:ib_ans_{mid}-Approve and send").click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.db.get_message(mid)["state"] == "answered"
    assert app.db.get_message(f"{mid}-ANS")["recipient"] == "billing@deccanquill.example"

def test_procedure_replays_and_saves_without_changing_anything(app):
    from invoice_desk import samples, config
    from invoice_desk.llm import LlmAssist
    from invoice_desk.pipeline import Pipeline
    from tests.conftest import TODAY
    pipe = Pipeline(store=app.db, llm=LlmAssist(config.LlmConfig(mode="off")), today=TODAY, ocr_mode="fixture")
    by_id = {s["id"]: s for s in samples.load_manifest()}
    for sid in ("01_happy_path_furniture", "07_E4a_tax_inclusive_within_tolerance", "08_E4b_tax_inclusive_over_tolerance"):
        pipe.run(samples.sample_bytes(by_id[sid]), samples.received_name(by_id[sid]), source="cli")
    go(app, "procedure", "Procedure")
    assert "Saving a request changes nothing." in said(app)
    app.selectbox(key="cr_key_0").select("tolerances.unit_price.pct").run()
    app.text_input(key="cr_val_0").input("1.1").run()
    app.selectbox(key="cr_key_1").select("tolerances.unit_price.abs_inr").run()
    app.text_input(key="cr_val_1").input("550").run()
    app.text_input(key="cr_why").input("reviewers keep approving these").run()
    app.button(key="cr_replay").click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert "3 documents replayed. 1 would have been decided differently." in shown(app)
    app.text_input(key="cr_name").input("N. Rao")
    app.button(key="FormSubmitter:cr_save_form-Save as a change request").click().run()
    assert app.db.change_requests()[0]["status"] == "PROPOSED"
    assert app.db.list_runs()[0]["verdict"] == "HOLD"                 # sample 08 is still held
