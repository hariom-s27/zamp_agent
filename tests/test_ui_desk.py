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
    assert "No mailbox is connected in this build. Messages are files." in said(app)
    app.button(key="ib_deliver").click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert "7 message(s) received and sorted." in said(app)
    assert len(app.db.inbox()) == 7 and len(app.db.list_runs()) == 2
    table = app.dataframe[0].value
    assert len(table) == 7 and set(table.columns) == {"When", "From", "Subject", "What it is", "What the desk did",
                                                      "Next step with", "Due"}
    labels = app.selectbox(key="ib_open").options
    bank = next(o for o in labels if o.startswith("Bank change request"))
    app.selectbox(key="ib_open").select(bank).run()
    assert "looks like tungabhadra-furnishings.example and is not the same" in said(app)
    assert "The desk has changed nothing." in shown(app)
    app.button(key="ib_deliver").click().run()                     # the same mail again changes nothing
    assert "0 message(s) received and sorted; 7 had already been received and changed nothing." in said(app)
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