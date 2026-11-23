"""Round 5 finishing, checkpoint 2: correctness the browser test found.

Payments say when one approval is enough and write a CSV Excel can read, the next email on a case
does not ask again for what the vendor already gave, "What the desk found" shows real values and
where they were read, the Business case and the Dashboard agree, the review refusal sits under the
button, every check that ran has a row, and the procedure replay counts each document once."""
from __future__ import annotations

import json

from invoice_desk import cases, changes, config, payments, samples
from invoice_desk.llm import LlmAssist
from invoice_desk.masterdata import load_master
from invoice_desk.pipeline import Pipeline
from invoice_desk.rules import load_rules
from invoice_desk.store import Store
from invoice_desk.ui import process as process_page

from .conftest import LLM_OFF, TODAY
from .test_ui import app, case_run, go, press, process, said, shown  # noqa: F401  (app is a fixture)


def _pipe(store):
    return Pipeline(store=store, rules=load_rules(), master=load_master(), llm=LlmAssist(LLM_OFF), today=TODAY,
                    ocr_mode="fixture")


def _run(store, sample_id):
    by_id = {s["id"]: s for s in samples.load_manifest() + samples.load_case_manifest() + samples.load_desk_manifest()}
    s = by_id[sample_id]
    return _pipe(store).run(samples.sample_bytes(s), samples.received_name(s), "test")


def _page(at, page):
    at.session_state["goto"] = page
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


# ---------------------------------------------------------------- 6 and 7. payments

def test_one_approval_is_enough_below_the_limit_read_from_the_rules(app):
    _run(app.db, "07_E4a_tax_inclusive_within_tolerance")
    _page(app, "payments")
    limit = load_rules().payments.second_approver_above_inr
    words = [i.value for i in app.info if i.value.startswith("One approval is enough")]
    assert words and "below the ₹2,00,000 limit for a second approver." in words[0] and int(limit) == 200000
    assert "Second approval: waiting" not in shown(app)


def test_above_the_limit_there_are_two_slots(app):
    for sid in ("02_scanned_clean_safety", "07_E4a_tax_inclusive_within_tolerance", "p1_micro_supplier_due"):
        _run(app.db, sid)
    _page(app, "payments")
    assert "First approval: waiting" in shown(app) and "Second approval: waiting" in shown(app)


def test_the_payment_file_opens_with_the_rupee_sign_and_the_banner_says_review_copy(tmp_path):
    store = Store(tmp_path / "b.db")
    rules, master = load_rules(), load_master()
    for sid in ("07_E4a_tax_inclusive_within_tolerance", "p1_micro_supplier_due"):
        _run(store, sid)
    cases.sync(store, master, rules, TODAY)
    p = payments.propose(store, master, rules, TODAY)
    done = payments.approve(store, master, rules, TODAY, p["fingerprint"], ["N. Rao"], True, TODAY)
    data = done["path"].read_bytes()
    assert data.startswith(b"\xef\xbb\xbf") and "₹".encode("utf-8") in data and b"Rs " not in data
    assert "review copy with masked accounts" in done["message"] and "release it in the bank" not in done["message"]


# ---------------------------------------------------------------- 9 and 11. cases

def _deliver(at):
    _page(at, "inbox")
    at.button(key="ib_deliver").click().run()
    assert not at.exception


def test_a_reply_that_gave_the_number_gets_a_thank_you_and_the_case_is_ready_to_process(app):
    _deliver(app)
    case = next(c for c in app.db.list_cases() if "MISSING_INVOICE_NUMBER" in c["reason_codes"])
    sentence = "The invoice number for our invoice dated 14 November 2026 is NHP/26-27/0301."
    app.db.update_case(case["case_id"], state="ANSWERED", waiting_on=case["owner"],
                       proposed_json=json.dumps({"invoice_number": {"value": "NHP/26-27/0301", "quote": sentence}}))
    draft, thanks = cases.next_draft(app.db.get_case(case["case_id"]), app.db.get_run(case["run_id"]), load_rules())
    assert thanks and "NHP/26-27/0301" in draft and "please" not in draft.lower().split("regards")[0]
    _page(app, "cases")
    page = shown(app)
    assert "Ready to process" in page and "The vendor gave the invoice number NHP/26-27/0301." in page
    assert "Thank-you note to the vendor" in page
    pills = app.button_group(key="cases_waiting").options
    counts = [int(str(o).rsplit("(", 1)[1].rstrip(")")) for o in pills]
    assert counts[0] == sum(counts[1:])                      # the pills add up to All
    out = cases.approve_message(app.db, load_master(), load_rules(), case["case_id"], "S. Rao", TODAY)
    now = app.db.get_case(case["case_id"])
    assert out["thanks"] and now["state"] == "ANSWERED" and now["waiting_on"] != "vendor"


def test_the_ai_reading_row_is_tagged_as_checked_by_the_desk():
    from invoice_desk.ui import cases as cases_page
    assert cases_page._actor("AI") == "AI (checked by the desk)"
    assert cases_page._actor("Invoice Desk") == "Rules" and cases_page._actor("S. Rao") == "Person"


def test_the_original_invoice_email_is_not_shown_as_a_corrected_invoice(app):
    _deliver(app)
    _page(app, "cases")
    assert not any("corrected invoice attached" in i.value for i in app.info)
    assert "Reply received from" not in shown(app)


# ---------------------------------------------------------------- 10 and 15. what was checked

def test_what_the_desk_found_shows_real_values_and_where_they_were_read(tmp_path):
    store = Store(tmp_path / "b.db")
    result = _run(store, "c1_invoice_before_goods")
    rows = process_page.evidence_rows(result)
    text = " ".join(str(v) for r in rows for v in r.values())
    assert "billed quantity up to" not in text and "Matched to the PO" not in text and " Nos" not in text
    goods = [r for r in rows if r["field"] == "Goods received"]
    assert goods and goods[0]["expected"] == "no goods receipt yet" and goods[0]["source"].startswith("Page ")


def test_every_check_that_ran_has_a_row_and_the_po_row_shows_the_quoted_po(tmp_path):
    store = Store(tmp_path / "b.db")
    result = _run(store, "01_happy_path_furniture")
    rows = {r["field"]: r for r in process_page.evidence_rows(result)}
    for field in ("Vendor", "Purchase order", "Prices", "Quantities", "Tax (GST)", "Duplicates", "Bank details"):
        assert field in rows, field
    assert rows["Purchase order"]["on_invoice"] == result.invoice.get("po_reference")
    assert all(r["ok"] for r in rows.values())


def test_a_price_hold_shows_the_line_it_was_read_from(tmp_path):
    store = Store(tmp_path / "b.db")
    result = _run(store, "08_E4b_tax_inclusive_over_tolerance")
    price = next(r for r in process_page.evidence_rows(result) if r["field"] == "Price")
    assert not price["ok"] and price["source"].startswith("Page 1, line 1") and "per unit" in price["expected"]


# ---------------------------------------------------------------- 13. one set of figures

def test_the_business_case_and_the_dashboard_show_the_same_figures(app):
    for sid in ("01_happy_path_furniture", "04_E1_split_po_overbilling", "08_E4b_tax_inclusive_over_tolerance",
                "09_fraud_guard_bank_change"):
        process(app, sid[:2])
    _page(app, "dashboard")
    dash = {m.label: m.value for m in app.metric}
    _page(app, "value")
    case = {m.label: m.value for m in app.metric}
    assert case["On hold now"] == dash["On hold now"]
    assert case["Cleared, no person"] == dash["Cleared, no person"]
    assert case["Invoices in these books"] == "4"
    teams = " · ".join(f"{t} {n}" for t, n in sorted(
        __import__("invoice_desk.ui.dashboard", fromlist=["x"]).book_figures(app.db, app.db.list_runs())["by_owner"].items()))
    assert f"**Who is holding them:** {teams}" in shown(app)


# ---------------------------------------------------------------- 14. the refusal under the button

def test_choose_a_different_person_appears_under_the_button_too(app):
    process(app, "09")
    go(app, "review")
    run = "AP-2026-0001"
    app.text_input(key=f"rev_name_{run}").set_value("N. Rao")
    app.text_input(key=f"rev_note_{run}").set_value("Spoke to Meera in accounts on 080-5550-0118 at 11:40")
    app.checkbox(key=f"rev_bank_{run}").check()
    press(app, "Record first approval")
    app.text_input(key=f"rev_name2_{run}").set_value("n. rao")
    app.checkbox(key=f"rev_bank2_{run}").check()
    press(app, "Approve for payment")
    errors = [e.value for e in app.error if "Choose a different person" in e.value]
    assert len(errors) == 2                                   # at the top of the page and under the button


# ---------------------------------------------------------------- 16. procedure replay

def test_the_replay_counts_each_document_once_and_reports_progress(tmp_path):
    store = Store(tmp_path / "b.db")
    for sid in ("01_happy_path_furniture", "08_E4b_tax_inclusive_over_tolerance", "08_E4b_tax_inclusive_over_tolerance"):
        _run(store, sid)
    assert len(store.list_runs()) == 3
    seen = []
    from decimal import Decimal
    result = changes.replay(store, load_master(), load_rules(), {"tolerances.unit_price.pct": Decimal("1.10")}, TODAY,
                            ocr_mode="fixture",
                            llm=LlmAssist(config.LlmConfig(mode="off")), progress=lambda *a: seen.append(a))
    assert result["count"] == 2
    assert seen == [("now", 1, 2), ("now", 2, 2), ("change", 1, 2), ("change", 2, 2)]
