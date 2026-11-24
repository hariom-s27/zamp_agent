"""Regressions from the final round 5 screen review, using recorded or disabled AI only."""
from __future__ import annotations

import csv
import io
import re

import pandas as pd
import pytest

from invoice_desk import payments
from invoice_desk.masterdata import load_master
from invoice_desk.rules import load_rules
from invoice_desk.ui import common as C, dashboard
from invoice_desk.ui import cases as cases_page
from invoice_desk.ui import payments as payments_page

from .conftest import TODAY
from .test_round5_correctness import _page, _run
from .test_round5_screens import page_text
from .test_ui import app, go, press, process, shown  # noqa: F401


@pytest.mark.parametrize("sample", ["01", "04"])
def test_duplicate_result_has_its_own_status_and_opens_original_above_timeline(app, sample):
    process(app, sample)
    original = app.session_state["current_run"]
    app.button(key="proc_go").click().run()
    assert not app.exception
    text = shown(app)
    assert "### Duplicate file: not booked" in text
    assert "Waiting on Procurement" not in text
    assert "On hold" not in C.verdict_banner_html(app.db.get_run(app.session_state["current_run"]))
    press(app, f"Open the original record, {original}")
    text = shown(app)
    assert text.index("### Duplicate file: not booked") < text.index(f"**The original record, {original}**")
    assert text.index(f"**The original record, {original}**") < text.index("**How it was handled**")
    assert app.db.get_run(original).ledger_id is not None


def test_duplicate_history_never_labels_a_repeat_as_cleared(app):
    process(app, "01")
    app.button(key="proc_go").click().run()
    duplicate = app.session_state["current_run"]
    go(app, "dashboard")
    table = app.dataframe(key="dash_table").value.set_index("Reference")
    assert table.loc[duplicate, "Decision"] == "Duplicate, not booked"
    picker = next(s for s in app.selectbox if s.label.startswith("Record to open"))
    assert "Duplicate, not booked" in next(s for s in picker.options if s.startswith(duplicate))
    assert {m.label: m.value for m in app.metric}["On hold now"] == "0"


def test_held_reason_chart_includes_po_value_and_quantity_once_each(store):
    _run(store, "01_happy_path_furniture")
    _run(store, "07_E4a_tax_inclusive_within_tolerance")
    held = _run(store, "04_E1_split_po_overbilling")
    assert held.run_id == "AP-2026-0003"
    assert {f.code for f in held.decision.reasons} >= {"QTY_OVER_PO", "OVER_PO_BALANCE"}
    counts = dashboard.held_reason_counts(store.list_runs(), load_rules())
    assert counts["Quantity above the PO or receipt"] == 1
    assert counts["Billing would exceed the PO value"] == 1
    _run(store, "04_E1_split_po_overbilling")
    assert dashboard.held_reason_counts(store.list_runs(), load_rules()) == counts


def test_attention_counts_all_held_invoices_including_ones_without_open_cases(app):
    for sid in ("04_E1_split_po_overbilling", "06_E3_scan_no_number_implied_po", "08_E4b_tax_inclusive_over_tolerance",
                "09_fraud_guard_bank_change", "10_ai_assist_letter_invoice", "c1_invoice_before_goods",
                "c2_small_supplier_late_invoice"):
        _run(app.db, sid)
    go(app, "dashboard")
    # A held invoice still needs attention when its case has been closed separately.
    case = app.db.list_cases()[0]
    app.db.update_case(case["case_id"], state="RESOLVED")
    app.run()
    held = int(next(m.value for m in app.metric if m.label == "On hold now"))
    visible = len([b for b in app.button if b.label == "Open"])
    more = re.search(r"and (\d+) more", " ".join(c.value for c in app.caption))
    assert held > load_rules().display.attention_max
    assert visible + (int(more.group(1)) if more else 0) == held


def test_holding_chart_leaves_room_after_largest_stacked_count():
    frame = pd.DataFrame([{"Team": "Procurement", "State": s, "Invoices": n}
                          for s, n in (("On time", 2), ("Overdue", 1))])
    assert dashboard.team_chart(frame).to_dict()["encoding"]["x"]["scale"]["domain"] == [0, 4]


def test_gross_saving_keeps_monthly_context_out_of_the_metric_value(app):
    go(app, "value")
    metric = next(m for m in app.metric if m.label == "Gross saving")
    assert "a month" not in metric.value
    assert any(c.value.startswith("a month; enter the desk's cost") for c in app.caption)


def test_inbox_wraps_full_actions_and_formats_reply_subject_dates(app):
    _page(app, "inbox")
    app.button(key="ib_deliver").click().run()
    app.button(key="ib_deliver_next").click().run()
    assert not app.exception
    table = app.table[0].value
    assert len(table) == int(next(m.value for m in app.metric if m.label == "Received"))
    assert any("14-Nov-2026" in s for s in table["Subject"])
    assert not any(re.search(r"\d{1,2} (?:Nov|November) 2026", s) for s in table["Subject"])
    actions = {C.screen_words(r["action"]) for r in app.db.inbox()}
    assert set(table["What the desk did"]) <= actions
    styles = " ".join(str(x.proto) for x in app.get("html"))
    assert "white-space:normal" in styles and "overflow-wrap:anywhere" in styles


def test_payments_dates_display_consistently_and_csv_keeps_iso(app):
    for sid in ("07_E4a_tax_inclusive_within_tolerance", "p1_micro_supplier_due"):
        _run(app.db, sid)
    rules, master = load_rules(), load_master()
    proposal = payments.propose(app.db, master, rules, TODAY)
    lines = proposal["pay"] + proposal["not_due"]
    lines[0]["why"] = "Due on 2026-11-21. Pay by 21 Nov 2026."
    rows = payments_page._rows(lines, with_run=True)
    assert "Due on 21-Nov-2026. Pay by 21-Nov-2026." in rows[0]["Why"]
    assert all(re.fullmatch(r"\d{2}-[A-Z][a-z]{2}-\d{4}", r["Pay by"]) for r in rows)
    _page(app, "payments")
    assert app.date_input(key="pay_date").proto.format == "DD/MM/YYYY"
    done = payments.approve(app.db, master, rules, TODAY, proposal["fingerprint"], ["N. Rao"], True, TODAY)
    exported = list(csv.DictReader(io.StringIO(done["path"].read_text(encoding="utf-8-sig"))))
    assert exported and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["pay_by"]) for row in exported)


def test_process_formats_due_date_from_an_older_record(app):
    result = _run(app.db, "01_happy_path_furniture")
    result.decision.next_action = "Posted to the ledger for payment; due 2026-12-18."
    app.db.replace_run_record(result)
    app.session_state["current_run"] = result.run_id
    go(app, "process")
    assert "due 18-Dec-2026" in shown(app) and "due 2026-12-18" not in shown(app)
    assert app.db.get_run(result.run_id).decision.next_action.endswith("2026-12-18.")


def test_cases_and_dashboard_use_the_same_legal_pay_by_phrase(app):
    _run(app.db, "c2_small_supplier_late_invoice")
    for page in ("cases", "dashboard"):
        go(app, page)
        text = page_text(app).lower()
        assert "legal pay-by date" in text
        assert "legal limit" not in text and "legal date " not in text


def test_zero_receipts_and_extra_units_read_naturally(app):
    _run(app.db, "c1_invoice_before_goods")
    go(app, "cases")
    assert "nothing received yet" in shown(app)
    assert "only 0 received" not in shown(app) and "up to 0" not in shown(app)
    assert C.screen_words("were the extra 1 Nos ordered and received?") == "was 1 extra unit ordered and received?"
    assert C.screen_words("up to 0.5 units") == "up to 0.5 units"


def test_sahyadri_price_question_keeps_the_hundredth_and_limit(store):
    result = _run(store, "08_E4b_tax_inclusive_over_tolerance")
    reason = next(f for f in result.decision.reasons if f.code == "PRICE_VARIANCE")
    assert "1.01% above the PO (limit 1%)" in cases_page.family_question(reason)


def test_review_success_preserves_vendor_proper_case(app):
    process(app, "09")
    run_id = app.session_state["current_run"]
    go(app, "review")
    app.text_input(key=f"rev_name_{run_id}").set_value("N. Rao")
    app.text_input(key=f"rev_note_{run_id}").set_value("Called the number on file and confirmed the account")
    app.checkbox(key=f"rev_bank_{run_id}").check()
    press(app, "Record first approval")
    app.text_input(key=f"rev_name2_{run_id}").set_value("S. Menon")
    app.checkbox(key=f"rev_bank2_{run_id}").check()
    press(app, "Approve for payment")
    vendor = C.vendor_case(app.db.get_run(run_id).vendor["name"])
    assert any(f"verified account for {vendor}" in s.value for s in app.success)


def test_review_queue_orders_by_invoice_date_even_when_received_out_of_order(app):
    newer = _run(app.db, "08_E4b_tax_inclusive_over_tolerance")
    older = _run(app.db, "c2_small_supplier_late_invoice")
    newest = _run(app.db, "09_fraud_guard_bank_change")
    go(app, "review")
    expected = sorted((app.db.run_row(r.run_id) for r in (newer, older, newest)),
                      key=lambda r: (r["invoice_date"], r["created_at"], r["seq"]))
    forms = [f.key.removeprefix("rev_form_") for f in app.get("form") if f.key.startswith("rev_form_")]
    assert forms == [r["run_id"] for r in expected]
