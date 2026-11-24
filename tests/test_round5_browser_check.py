"""Round 5, fixes from the second browser check: the sample dropdown keeps its choice after a run,
the original record opens under a duplicate stop, equal values read as a match, held reasons are
counted once per invoice, the month legend has room, one due-date style, and a way back to the demo."""
from __future__ import annotations

from datetime import date

from invoice_desk import samples
from invoice_desk.rules import load_rules
from invoice_desk.store import Store
from invoice_desk.ui import dashboard
from invoice_desk.ui import process as process_page
from invoice_desk.ui.ui_kit import due_label

from .test_round5_correctness import _run
from .test_ui import app, go, press, process, shown  # noqa: F401  (app is a fixture)


def _sha(sample_id: str) -> str:
    from invoice_desk.reader import sha256_of
    s = next(x for x in samples.load_manifest() if x["id"] == sample_id)
    return sha256_of(samples.sample_bytes(s))


# ---------------------------------------------------------------- 1. the dropdown keeps its choice

def test_running_04_twice_runs_04_both_times_and_the_second_stops_as_a_duplicate(app):
    process(app, "04")
    first = app.db.get_run(app.session_state["current_run"])
    assert first.file_sha256 == _sha("04_E1_split_po_overbilling")
    assert app.selectbox(key="proc_sample").value == "04_E1_split_po_overbilling"   # still 04 after the run
    app.button(key="proc_go").click().run()
    assert not app.exception
    second = app.db.get_run(app.session_state["current_run"])
    assert second.file_sha256 == _sha("04_E1_split_po_overbilling") != _sha("01_happy_path_furniture")
    assert second.resubmission_of == first.run_id
    assert [r["file_sha256"] for r in app.db.list_runs()].count(_sha("01_happy_path_furniture")) == 0


def test_the_dropdown_label_never_changes_after_a_run(app):
    go(app, "process")
    before = list(app.selectbox(key="proc_sample").options)
    process(app, "04")
    assert list(app.selectbox(key="proc_sample").options) == before     # same labels, so the choice is kept


# ---------------------------------------------------------------- 2. the original record, right there

def test_open_the_original_record_shows_it_under_the_duplicate_stop(app):
    process(app, "01")
    first = app.session_state["current_run"]
    app.button(key="proc_go").click().run()
    press(app, f"Open the original record, {first}")
    assert [t.value for t in app.title] == ["Process an invoice"]
    assert f"**The original record, {first}**" in shown(app)
    assert "Cleared: pay" in shown(app)                                # the original's own decision banner


# ---------------------------------------------------------------- 3. equal values match

def test_the_po_row_matches_when_both_sides_are_the_same_po(tmp_path):
    store = Store(tmp_path / "b.db")
    for sid in ("01_happy_path_furniture", "04_E1_split_po_overbilling", "08_E4b_tax_inclusive_over_tolerance",
                "09_fraud_guard_bank_change"):
        result = _run(store, sid)
        rows = process_page.evidence_rows(result)
        po = next(r for r in rows if r["field"] == "Purchase order")
        assert po["on_invoice"] == po["expected"] and po["ok"], (sid, po)
        for row in rows:                                              # no row anywhere has equal sides and a mismatch
            assert row["ok"] or not process_page.same_value(row["on_invoice"], row["expected"]), (sid, row)


def test_a_po_over_its_balance_keeps_its_own_row_and_the_po_row_still_matches(tmp_path):
    rows = process_page.evidence_rows(_run(Store(tmp_path / "b.db"), "04_E1_split_po_overbilling"))
    assert any(r["field"] != "Purchase order" and not r["ok"] for r in rows)        # the problem is still shown
    assert next(r for r in rows if r["field"] == "Purchase order")["ok"]


# ---------------------------------------------------------------- 4. held reasons, once per invoice

def test_held_reasons_count_each_invoice_once_and_skip_repeats(tmp_path):
    store = Store(tmp_path / "b.db")
    for sid in ("05_E2_disguised_duplicate", "08_E4b_tax_inclusive_over_tolerance", "08_E4b_tax_inclusive_over_tolerance",
                "09_fraud_guard_bank_change"):
        _run(store, sid)
    runs = store.list_runs()
    counts = dashboard.held_reason_counts(runs, load_rules())
    held = [r for r in dashboard.invoices(runs) if r["verdict"] == "HOLD"]
    assert counts["Price above the PO"] == 1                     # 08 sent twice is one invoice
    assert counts["Bank account changed"] == 1
    assert "Duplicate" not in counts                             # the repeat file is not a reason anyone held
    assert max(counts.values()) <= len(held)


# ---------------------------------------------------------------- 5. the month legend

def test_the_month_legend_sits_below_with_room_for_every_label():
    import pandas as pd
    frame = pd.DataFrame([{"Month": "Nov 2026", "Outcome": o, "Invoices": 1, "rank": i}
                          for i, o in enumerate(dashboard.OUTCOMES)])
    legend = dashboard.month_chart(frame, ["Nov 2026"]).to_dict()["encoding"]["color"]["legend"]
    assert legend["orient"] == "bottom" and legend["direction"] == "vertical" and legend["labelLimit"] == 0
    assert "Held, then cleared" in dashboard.OUTCOMES


# ---------------------------------------------------------------- 6. one due-date style

def test_one_due_date_style():
    today = date(2026, 11, 21)
    assert due_label(date(2026, 11, 23), today)[1] == "Due 23-Nov-2026 (in 2 days)"
    assert due_label(date(2026, 11, 22), today)[1] == "Due 22-Nov-2026 (in 1 day)"
    assert due_label(today, today)[1] == "Due 21-Nov-2026 (today)"
    assert due_label(date(2026, 11, 20), today)[1] == "Overdue since 20-Nov-2026"


def test_cases_use_the_one_style(app):
    from .test_ui import case_run
    case_run(app, "c1_invoice_before_goods")
    go(app, "cases")
    text = shown(app)
    assert "Due 23-Nov-2026 (in 2 days)" in text and "Answer due" not in text and "Due in " not in text


# ---------------------------------------------------------------- back to the guided demo

def test_watch_one_invoice_offers_a_way_back_to_the_guided_demo(app):
    press(app, "Watch one invoice go from PDF to decision")
    assert "Back to the guided demo" in [b.label for b in app.button]
    press(app, "Back to the guided demo")
    assert [t.value for t in app.title] == ["Guided demo"]


def test_an_ordinary_run_has_no_back_button(app):
    process(app, "01")
    assert "Back to the guided demo" not in [b.label for b in app.button]
