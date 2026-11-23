"""Dashboard: five numbers, three bar charts, Needs attention, and the history in its own tab."""
from __future__ import annotations

from invoice_desk.ui import dashboard as D

from .test_ui import app, case_run, go, process, shown  # noqa: F401  (app is a fixture)

CARDS = ["Cleared without a person", "On hold now", "Days to clear a held invoice", "Money stopped before payment",
         "Small-supplier deadlines at risk"]


def item(codes, total, status, excess=0.0) -> dict:
    return {"codes": set(codes), "total": total, "status": status, "excess": excess}


def test_money_stopped_counts_duplicates_and_bank_changes_in_full_and_overbilling_only_by_its_excess():
    parts = D.money_stopped([
        item(["DUPLICATE_INVOICE_NUMBER"], 100000, "rejected"),
        item(["BANK_DETAILS_CHANGED"], 1840000, "held"),
        item(["PRICE_VARIANCE"], 248560, "held", excess=2640),
        item(["PRICE_VARIANCE"], 50000, "cleared", excess=900),          # later cleared in full: zero
        item(["DUPLICATE_CONTENT"], 70000, "cleared"),                   # cleared: zero
        item(["GOODS_NOT_RECEIVED"], 40320, "held"),                     # not money that would be paid wrongly
    ])
    assert parts == {"Bank change stopped": (1840000, 1), "Duplicates": (100000, 1), "Overbilling": (2640, 1)}
    assert D.largest_part(parts) == "Bank change stopped"                 # over half the total, so it is named
    assert D.largest_part({"Duplicates": (10, 1), "Overbilling": (10, 1)}) is None


def test_the_cards_reconcile_with_the_history(app):
    process(app, "01")
    process(app, "04")
    process(app, "05")
    go(app, "dashboard")
    metrics = {m.label: m.value for m in app.metric}
    assert all(label in metrics for label in CARDS)
    history = app.dataframe[0].value
    held = sum(1 for r in app.db.list_runs() if r["review_status"] == "PENDING")
    assert metrics["On hold now"] == str(held) and held == list(history["Decision"]).count("HOLD")
    assert metrics["Cleared without a person"] == f"{round(100 * list(history['Decision']).count('APPROVE') / 3)}%"
    assert "What happened to each month's invoices" in shown(app) and "Why invoices were held" in shown(app)
    assert "Who is holding invoices today" in shown(app)


def test_needs_attention_lists_open_cases_most_pressing_first(app):
    case_run(app, "c1_invoice_before_goods")
    case_run(app, "c2_small_supplier_late_invoice")
    go(app, "cases")                         # the Cases page is the one that opens cases for holds
    go(app, "dashboard")
    page = shown(app)
    assert "**Needs attention**" in page and "See all cases" in [b.label for b in app.button]
    assert page.index("NHP/26-27/0287") < page.index("KBD/26-27/1193")      # the legal clock first
    assert {m.label: m.value for m in app.metric}["Small-supplier deadlines at risk"] == "1"


def test_a_new_session_shows_an_empty_state_and_no_numbers(app):
    go(app, "dashboard")
    assert any("Nothing received yet" in i.value for i in app.info) and not app.metric
