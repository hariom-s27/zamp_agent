"""Cases and Review queue: question-first cards, filter pills, two approval slots, empty states."""
from __future__ import annotations

import pytest

from invoice_desk.models import Finding
from invoice_desk.ui import cases as cases_page

from .test_ui import app, case_run, go, press, process, said, shown  # noqa: F401  (app is a fixture)


def F(code: str, **kw) -> Finding:
    return Finding(code=code, stage="test", summary="s", question="the engine's question", **kw)


@pytest.mark.parametrize("finding, words", [
    (F("PRICE_VARIANCE", evidence={"pct": "3.20"}),
     "The price is 3.2% above the PO. Accept the new price, or ask for a corrected invoice?"),
    (F("GOODS_NOT_RECEIVED"), "The goods receipt is not posted. Have the goods arrived?"),
    (F("QTY_OVER_PO", observed="40 Nos billed", expected="at most 30 Nos still open"),
     "The invoice bills 40 units. The PO has at most 30 units still open. Amend the PO, or ask for a corrected invoice?"),
    (F("NO_PO_FOUND"), "No open PO matches this invoice. Raise one, or reject the invoice?"),
    (F("BANK_DETAILS_CHANGED"),
     "The bank account differs from the one on file. Has the vendor confirmed this on a number we already hold?"),
    (F("DUPLICATE_INVOICE_NUMBER"), "This looks like an invoice we already have. Reject it as a duplicate?"),
    (F("TAX_TYPE_MISMATCH", observed="IGST 18%", expected="CGST 9% + SGST 9%"),
     "GST on the invoice is IGST 18%. We work it out as CGST 9% + SGST 9%. Ask for a corrected invoice?"),
    (F("VENDOR_NOT_APPROVED"), "the engine's question"),
])
def test_the_question_is_written_per_reason_family_and_ends_in_a_choice(finding, words):
    assert cases_page.case_question([finding], fallback="the engine's question") == words


def test_cases_are_question_first_cards_with_pills_and_an_actor_tagged_timeline(app):
    case_run(app, "c1_invoice_before_goods")
    go(app, "cases")
    page = shown(app)
    assert "The goods receipt is not posted. Have the goods arrived?" in page
    assert any("one question, one owner, one due date" in c.value for c in app.caption)
    assert {m.label for m in app.metric} >= {"Open cases", "Overdue", "Oldest", "Money on hold"}
    assert "Actor" in app.dataframe[0].value.columns and "Rules" in set(app.dataframe[0].value["Actor"])
    assert app.button_group(key="cases_waiting").value is not None


def test_empty_states_on_cases_and_the_review_queue(app):
    go(app, "cases")
    assert any("No open cases. Every invoice so far has cleared or been closed." in i.value for i in app.info)
    assert "Run a held invoice" in [b.label for b in app.button]
    go(app, "review")
    assert "Nothing needs a signature." in shown(app)
    case_run(app, "c1_invoice_before_goods")
    go(app, "cases")
    app.button_group(key="cases_waiting").set_value("Procurement").run()
    assert any("Nothing is waiting on Procurement." in i.value for i in app.info)


def test_a_bank_change_shows_two_slots_and_the_same_person_cannot_approve_twice(app):
    process(app, "09")
    go(app, "review")
    run = "AP-2026-0001"
    assert "**You are approving**" in shown(app) and "**What the desk found**" in shown(app)
    assert "Second approval: waiting" in shown(app)
    app.text_input(key=f"rev_name_{run}").set_value("N. Rao")
    app.text_input(key=f"rev_note_{run}").set_value("Spoke to Meera in accounts on 080-5550-0118 at 11:40")
    app.checkbox(key=f"rev_bank_{run}").check()
    press(app, "Record first approval")
    assert "First approval: N. Rao" in shown(app)
    app.text_input(key=f"rev_name2_{run}").set_value("n. rao")
    app.checkbox(key=f"rev_bank2_{run}").check()
    press(app, "Approve for payment")
    assert any("Choose a different person. Two people must agree to a bank change." in e.value for e in app.error)
    assert app.db.run_row(run)["review_status"] == "PENDING"
