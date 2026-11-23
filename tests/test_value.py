"""The Business case page (round 3, DECISIONS.md D71): measured figures from the store, and a
model whose every input is an assumption kept in rules.yaml and shown with its source."""
from decimal import Decimal

import pytest

from invoice_desk import value


def test_the_business_case_arithmetic_is_what_the_page_says(rules):
    m = value.model(rules.value_model)
    assert m["minutes_before"] == Decimal("17.00") and m["minutes_after"] == Decimal("6.56")
    assert m["hours_saved"] == Decimal("104.4") and m["saving_inr"] == Decimal("46980")
    assert m["cost_before_per_invoice"] == Decimal("127.50") and m["cost_after_per_invoice"] == Decimal("49.20")
    assert m["exposure_inr"] == Decimal("765000")                          # Rs 7.65 lakh a month
    assert m["roi"] is None and m["net_saving_inr"] is None               # no desk cost given: gross saving only


def test_with_no_desk_cost_there_is_a_gross_saving_and_no_roi(rules):
    free = rules.value_model.model_copy(update={"desk_cost_per_month_inr": Decimal("0")})
    assert value.model(free)["roi"] is None
    priced = rules.value_model.model_copy(update={"desk_cost_per_month_inr": Decimal("15660")})
    m = value.model(priced)
    assert m["net_saving_inr"] == Decimal("31320") and m["roi"] == Decimal("2.00")     # (46,980 - 15,660) / 15,660


def test_the_model_never_claims_a_saving_on_exception_chasing(rules):
    a = rules.value_model
    more = a.model_copy(update={"minutes_per_exception_manual": a.minutes_per_exception_manual + 100})
    assert value.model(more)["hours_saved"] == value.model(a)["hours_saved"]     # the same in before and after


def _run(run_id, verdict, total="1000.00", codes="", status="", again=""):
    return {"run_id": run_id, "verdict": verdict, "total": total, "reason_codes": codes, "review_status": status,
            "resubmission_of": again}


def test_measured_figures_count_a_resubmission_and_a_released_invoice_once():
    runs = [_run("AP-1", "APPROVE", "100.00"), _run("AP-2", "APPROVE", "100.00", again="AP-1"),
            _run("AP-3", "HOLD", "500.00", "GOODS_NOT_RECEIVED", "RELEASED"),
            _run("AP-4", "REJECT", "700.00", "DUPLICATE_CONTENT"),
            _run("AP-5", "HOLD", "900.00", "PRICE_VARIANCE", "PENDING"),
            _run("AP-6", "HOLD", "300.00", "BANK_DETAILS_CHANGED", "APPROVED")]
    cases = [{"run_id": "AP-3", "owner": "Stores", "state": "RESOLVED", "resolution": "released by rule"},
             {"run_id": "AP-5", "owner": "Procurement", "state": "WAITING", "resolution": ""},
             {"run_id": "AP-6", "owner": "Vendor master team", "state": "RESOLVED", "resolution": "approved"}]
    m = value.measured(runs, cases, spend_usd=0.006, invoices_with_ai=2)
    assert m["received"] == 5 and m["sent_twice"] == 1
    assert m["touchless"] == 1 and m["touchless_share"] == Decimal("0.2")
    assert m["held"] == 3 and m["held_by_owner"] == {"Stores": 1, "Procurement": 1, "Vendor master team": 1}
    assert m["resolved_by_rule"] == 1 and m["resolved_by_person"] == 1
    assert m["rejected_duplicates_inr"] == Decimal("700.00")
    assert m["held_overbilling_or_bank_inr"] == Decimal("1200.00")        # 900 over the PO, 300 bank change
    assert m["ai_cost_per_invoice_usd"] == pytest.approx(0.003)


def test_measured_figures_on_empty_books_are_zero_not_an_error():
    m = value.measured([], [], spend_usd=0.0, invoices_with_ai=0)
    assert m["received"] == 0 and m["touchless_share"] is None and m["ai_cost_per_invoice_usd"] is None
