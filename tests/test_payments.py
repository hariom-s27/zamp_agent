"""Round 4 checkpoint 5: the payment-run proposal (invoice_desk/payments.py), on the worked
example of section 4.3: business date 21 Nov 2026, the samples run in the documented order."""
import csv
import hashlib
import io
import shutil
from datetime import date, timedelta
from decimal import Decimal

import pytest

from invoice_desk import cases, config, payments, samples
from invoice_desk.llm import LlmAssist
from invoice_desk.masterdata import load_master
from invoice_desk.pipeline import Pipeline
from invoice_desk.rules import load_rules
from invoice_desk.store import Store
from tests.conftest import TODAY

ORDER = ["01_happy_path_furniture", "02_scanned_clean_safety", "03_monthly_service_instalment",
         "07_E4a_tax_inclusive_within_tolerance", "09_fraud_guard_bank_change", "10_ai_assist_letter_invoice"]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("books")
    rules, master = load_rules(), load_master()
    store = Store(folder / "books.db")
    llm = LlmAssist(config.LlmConfig(mode="fixture"), cache_dir=folder / "cache")      # recorded answers, no network
    pipeline = Pipeline(store=store, rules=rules, master=master, llm=llm, today=TODAY, ocr_mode="fixture")
    by_id = {s["id"]: s for s in samples.load_manifest() + samples.load_case_manifest() + samples.load_desk_manifest()}
    runs = {}
    for sid in ORDER + ["p1_micro_supplier_due", "c2_small_supplier_late_invoice"]:
        result = pipeline.run(samples.sample_bytes(by_id[sid]), samples.received_name(by_id[sid]), source="cli")
        runs[sid] = result
    cases.sync(store, master, rules, TODAY)
    assert [runs[s].verdict for s in ("01_happy_path_furniture", "02_scanned_clean_safety", "10_ai_assist_letter_invoice",
                                      "p1_micro_supplier_due", "09_fraud_guard_bank_change")] == \
        ["APPROVE", "APPROVE", "APPROVE", "APPROVE", "HOLD"]
    return folder, {k: v.ledger_id for k, v in runs.items()}, {k: v.run_id for k, v in runs.items()}


@pytest.fixture()
def books(built, tmp_path):
    folder, ledger, run_ids = built
    shutil.copytree(folder, tmp_path / "copy")
    return Store(tmp_path / "copy" / "books.db"), ledger, run_ids


@pytest.fixture()
def rules():
    return load_rules()


@pytest.fixture()
def master():
    return load_master()


def _propose(store, master, rules, **kw):
    return payments.propose(store, master, rules, TODAY, **kw)


def _ids(lines):
    return [ln["ledger_id"] for ln in lines]


def test_candidates_are_posted_run_entries_not_history(books, master, rules):
    store, ledger, _ = books
    p = _propose(store, master, rules)
    every = _ids(p["pay"] + p["not_due"] + p["blocked"])
    assert all(store.get_ledger(i)["origin"] == "run" for i in every)
    history = [e["ledger_id"] for e in store.ledger_entries() if e["origin"] == "history" and e["status"] == "POSTED"]
    assert history and not set(history) & set(every)
    on = rules.model_copy(update={"payments": rules.payments.model_copy(update={"include_history": True})})
    assert set(history) <= set(_ids(_propose(store, master, on)["pay"] + _propose(store, master, on)["not_due"]
                                    + _propose(store, master, on)["blocked"]))


def test_legal_date_beats_due_date_in_order(books, master, rules):
    store, ledger, _ = books
    pay = _ids(_propose(store, master, rules)["pay"])
    assert pay[0] == ledger["p1_micro_supplier_due"]
    assert pay.index(ledger["p1_micro_supplier_due"]) < pay.index(ledger["07_E4a_tax_inclusive_within_tolerance"])
    p1 = _propose(store, master, rules)["pay"][0]
    assert p1["basis"] == "legal" and p1["pay_by"] == date(2026, 11, 26)
    assert p1["why"].startswith("Legal pay-by date for a micro supplier: 26-Nov-2026, 5 days left. Counted from the "
                                "delivery recorded on 12-Oct-2026. After that date interest runs at about ₹")
    assert p1["why"].endswith(" a day (approximate).")


def test_horizon_splits_pay_now_and_not_due(books, master, rules):
    store, ledger, _ = books
    p = _propose(store, master, rules)
    assert set(_ids(p["pay"])) == {ledger[s] for s in ("p1_micro_supplier_due", "02_scanned_clean_safety",
                                                       "07_E4a_tax_inclusive_within_tolerance")}
    not_due = {ln["ledger_id"]: ln for ln in p["not_due"]}
    assert ledger["10_ai_assist_letter_invoice"] in not_due and ledger["03_monthly_service_instalment"] in not_due
    assert not_due[ledger["10_ai_assist_letter_invoice"]]["why"] == "Due on 29-Dec-2026 (45-day terms)."
    seven = next(ln for ln in p["pay"] if ln["ledger_id"] == ledger["07_E4a_tax_inclusive_within_tolerance"])
    assert seven["why"] == "Due on 28-Nov-2026 (15-day terms)." and seven["amount"] == Decimal("60992.00")


def test_discount_return_formula():
    assert payments.discount_return(Decimal("2"), 10, 30) == Decimal("37.24")
    assert payments.discount_return(Decimal("0.5"), 10, 30) == Decimal("9.17")


def test_discount_taken_only_at_or_above_hurdle(books, master, rules):
    store, ledger, _ = books
    p = _propose(store, master, rules)
    two = next(ln for ln in p["pay"] if ln["ledger_id"] == ledger["02_scanned_clean_safety"])
    three = next(ln for ln in p["not_due"] if ln["ledger_id"] == ledger["03_monthly_service_instalment"])
    assert two["basis"] == "discount" and two["pay_by"] == date(2026, 11, 24)
    assert "Worth 37.2% a year against a 12% hurdle, so it is taken." in two["why"]
    assert three["discount"] == 0 and three["why"] == ("Early-payment discount of 0.5% is worth 9.2% a year, below the "
                                                       "12% hurdle. Left to its due date, 15-Dec-2026.")
    high = rules.model_copy(update={"payments": rules.payments.model_copy(update={"hurdle_rate_pct": Decimal("40")})})
    assert next(ln for ln in _propose(store, master, high)["not_due"]
                if ln["ledger_id"] == ledger["02_scanned_clean_safety"])["discount"] == 0


def test_discount_amounts_and_credit_note_note(books, master, rules):
    store, ledger, _ = books
    two = next(ln for ln in _propose(store, master, rules)["pay"] if ln["ledger_id"] == ledger["02_scanned_clean_safety"])
    assert two["total"] == Decimal("111038.00") and two["discount"] == Decimal("2220.76")
    assert two["amount"] == Decimal("108817.24") and two["gst_part"] == Decimal("338.76")
    assert "The vendor's credit note for" in two["why"] and "input tax credit of" in two["why"]


def test_vendor_with_open_bank_change_is_blocked(books, master, rules):
    store, ledger, run_ids = books
    blocked = {ln["ledger_id"]: ln for ln in _propose(store, master, rules)["blocked"]}
    one = blocked[ledger["01_happy_path_furniture"]]
    assert run_ids["09_fraud_guard_bank_change"] in one["why"]
    assert one["why"].startswith("Not proposed: Tungabhadra Office Furnishings Pvt Ltd has a bank account change "
                                 "waiting for verification")
    assert one["why"].endswith("With: Vendor master team.")


def test_held_small_supplier_shown_but_never_payable(books, master, rules):
    store, ledger, run_ids = books
    p = _propose(store, master, rules)
    held = {ln["run_id"]: ln for ln in p["on_hold"]}
    c2 = held[run_ids["c2_small_supplier_late_invoice"]]
    case = store.case_for_run(run_ids["c2_small_supplier_late_invoice"])
    assert c2["why"] == f"Cannot be paid: held for Procurement (case {case['case_id']}). Legal pay-by date 24-Nov-2026, 3 days left."
    two = payments.approve(store, master, rules, TODAY, p["fingerprint"], ["N. Rao", "S. Iyer"], True, TODAY)
    text = two["path"].read_text(encoding="utf-8-sig")
    assert "NHP/26-27/0287" not in text


def test_cash_limit_never_defers_a_legal_date_line(books, master, rules):
    store, ledger, _ = books
    p = _propose(store, master, rules, cash=Decimal("1000"))
    assert ledger["p1_micro_supplier_due"] in _ids(p["pay"])
    assert set(_ids(p["deferred"])) == {ledger["02_scanned_clean_safety"], ledger["07_E4a_tax_inclusive_within_tolerance"]}
    assert p["cash_short"] and any("does not cover the legal" in w for w in p["warnings"])
    roomy = _propose(store, master, rules, cash=Decimal("250000"))
    assert not roomy["cash_short"] and roomy["deferred"] == []


def test_removing_a_line_needs_name_and_reason(books, master, rules):
    store, ledger, _ = books
    p = _propose(store, master, rules)
    removed = {}
    with pytest.raises(payments.PaymentError):
        payments.remove_line(p, removed, ledger["07_E4a_tax_inclusive_within_tolerance"], "", "dispute")
    with pytest.raises(payments.PaymentError):
        payments.remove_line(p, removed, ledger["07_E4a_tax_inclusive_within_tolerance"], "N. Rao", " ")
    with pytest.raises(payments.PaymentError):                     # nobody can add or touch a blocked line
        payments.remove_line(p, removed, ledger["01_happy_path_furniture"], "N. Rao", "reason")
    payments.remove_line(p, removed, ledger["07_E4a_tax_inclusive_within_tolerance"], "N. Rao", "vendor asked to wait")
    again = _propose(store, master, rules, removed=removed)
    assert ledger["07_E4a_tax_inclusive_within_tolerance"] not in _ids(again["pay"])
    assert _ids(again["removed"]) == [ledger["07_E4a_tax_inclusive_within_tolerance"]]


def test_second_person_required_above_limit_and_must_differ(books, master, rules):
    store, _, _ = books
    p = _propose(store, master, rules)
    assert p["total"] > rules.payments.second_approver_above_inr and p["needs_second"]
    with pytest.raises(payments.PaymentError, match="second, different person"):
        payments.approve(store, master, rules, TODAY, p["fingerprint"], ["N. Rao"], True, TODAY)
    with pytest.raises(payments.PaymentError, match="different"):
        payments.approve(store, master, rules, TODAY, p["fingerprint"], ["N. Rao", "n rao"], True, TODAY)
    with pytest.raises(payments.PaymentError, match="release authority"):
        payments.approve(store, master, rules, TODAY, p["fingerprint"], ["N. Rao", "S. Iyer"], False, TODAY)
    assert store.payment_runs() == []


def test_stale_proposal_is_refused(books, master, rules):
    store, _, _ = books
    p = _propose(store, master, rules)
    store.add_ledger({"ledger_id": "L-NEW", "run_id": "AP-2026-9999", "origin": "run", "vendor_id": "V005",
                      "invoice_no": "SIS/2400", "invoice_date": "2026-11-20", "total": "1000.00", "status": "POSTED"}, [])
    with pytest.raises(payments.PaymentError, match="The books have changed since this proposal was shown. Look at it again."):
        payments.approve(store, master, rules, TODAY, p["fingerprint"], ["N. Rao", "S. Iyer"], True, TODAY)


def test_approval_writes_run_lines_export_and_one_audit_entry(books, master, rules):
    store, ledger, _ = books
    p = _propose(store, master, rules)
    before = len(store.receipts())
    done = payments.approve(store, master, rules, TODAY, p["fingerprint"], ["N. Rao", "S. Iyer"], True, TODAY)
    assert done["run_id"] == "PR-20261121-01"
    run = store.payment_run(done["run_id"])
    assert run["status"] == "APPROVED" and run["approved_by"] == "N. Rao" and run["second_approved_by"] == "S. Iyer"
    assert Decimal(run["total"]) == p["total"] and run["line_count"] == 3
    lines = store.payment_run_lines(done["run_id"])
    assert {ln["ledger_id"] for ln in lines if ln["group_name"] == "pay"} == set(_ids(p["pay"]))
    data = done["path"].read_bytes()
    entries = store.receipts()[before:]
    assert len(entries) == 1 and entries[0]["kind"] == "payment_run"
    assert entries[0]["body"]["export_sha256"] == hashlib.sha256(data).hexdigest() == run["export_sha256"]
    assert entries[0]["body"]["approvers"] == ["N. Rao", "S. Iyer"] and store.verify_chain()["ok"]
    assert done["message"].startswith(f"Run {done['run_id']} approved by N. Rao and S. Iyer. Nothing has been paid.")


def test_export_masks_bank_account(books, master, rules):
    store, ledger, _ = books
    p = _propose(store, master, rules)
    done = payments.approve(store, master, rules, TODAY, p["fingerprint"], ["N. Rao", "S. Iyer"], True, TODAY)
    rows = list(csv.DictReader(io.StringIO(done["path"].read_text(encoding="utf-8-sig"))))
    assert len(rows) == 3
    for row in rows:
        account = master.vendors[row["vendor_id"]].bank_account
        assert account not in done["path"].read_text(encoding="utf-8-sig")
        assert row["bank_account"] == "XXXX" + account[-4:] and row["ifsc"]


def test_lines_on_an_approved_run_are_not_proposed_again(books, master, rules):
    store, _, _ = books
    p = _propose(store, master, rules)
    done = payments.approve(store, master, rules, TODAY, p["fingerprint"], ["N. Rao", "S. Iyer"], True, TODAY)
    again = _propose(store, master, rules)
    assert not set(_ids(p["pay"])) & set(_ids(again["pay"] + again["not_due"]))
    assert all(v == done["run_id"] for v in again["approved"].values()) and len(again["approved"]) == 3


def test_ledger_status_is_never_changed_by_a_run(books, master, rules):
    store, _, _ = books
    p = _propose(store, master, rules)
    payments.approve(store, master, rules, TODAY, p["fingerprint"], ["N. Rao", "S. Iyer"], True, TODAY)
    assert all(store.get_ledger(i)["status"] == "POSTED" for i in _ids(p["pay"]))


def test_cancel_frees_lines_and_is_recorded(books, master, rules):
    store, _, _ = books
    p = _propose(store, master, rules)
    done = payments.approve(store, master, rules, TODAY, p["fingerprint"], ["N. Rao", "S. Iyer"], True, TODAY)
    with pytest.raises(payments.PaymentError):
        payments.cancel(store, done["run_id"], "N. Rao", "", TODAY)
    before = len(store.receipts())
    payments.cancel(store, done["run_id"], "N. Rao", "bank holiday, run again on Tuesday", TODAY)
    assert store.payment_run(done["run_id"])["status"] == "CANCELLED"
    assert set(_ids(p["pay"])) <= set(_ids(_propose(store, master, rules)["pay"]))
    assert len(store.receipts()) == before + 1 and store.receipts()[-1]["kind"] == "payment_run_cancelled"


def test_180_day_warning(books, master, rules):
    store, _, _ = books
    old = (TODAY - timedelta(days=160)).isoformat()
    store.add_ledger({"ledger_id": "L-OLD", "run_id": "AP-2026-9998", "origin": "run", "vendor_id": "V005",
                      "invoice_no": "SIS/1999", "invoice_date": old, "total": "5000.00", "status": "POSTED"}, [])
    line = next(ln for ln in _propose(store, master, rules)["pay"] if ln["ledger_id"] == "L-OLD")
    assert line["basis"] == "overdue"
    assert ("Unpaid for 160 days. Input tax credit must be reversed if this is not paid within 180 days of the "
            "invoice date.") in line["flags"]


def test_twin_lines_are_flagged(books, master, rules):
    store, _, _ = books
    for n in (1, 2):
        store.add_ledger({"ledger_id": f"L-TWIN{n}", "run_id": f"AP-2026-990{n}", "origin": "run", "vendor_id": "V005",
                          "invoice_no": f"SIS/30{n}", "invoice_date": "2026-11-12", "total": "700.00",
                          "status": "POSTED"}, [])
    twins = [ln for ln in _propose(store, master, rules)["pay"] if ln["ledger_id"].startswith("L-TWIN")]
    assert len(twins) == 2 and all("Same vendor, amount and invoice date as another line in this run. "
                                   "Check before approving." in ln["flags"] for ln in twins)


def test_payments_off(books, master, rules):
    store, _, _ = books
    off = rules.model_copy(update={"payments": rules.payments.model_copy(update={"enabled": False})})
    assert payments.propose(store, master, off, TODAY) is None
    with pytest.raises(payments.PaymentError, match="switched off"):
        payments.approve(store, master, off, TODAY, "x", ["N. Rao", "S. Iyer"], True, TODAY)
    assert store.payment_runs() == []
