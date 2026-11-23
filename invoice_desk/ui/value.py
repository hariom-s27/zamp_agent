"""Page, Business case: what this session's invoices show, and a calculator whose every input
is an assumption from rules.yaml shown with its source (round 3, DECISIONS.md D71)."""
from __future__ import annotations

from decimal import Decimal

import streamlit as st

from .. import cases as case_engine
from .. import value as value_model
from ..rules import ValueModel
from . import common as C

# What each assumption is, and where its starting value comes from (the comments in rules.yaml).
ASSUMPTIONS = {
    "invoices_per_month": ("Invoices a month", "assumption: the brief says hundreds"),
    "minutes_per_invoice_manual": ("Minutes to handle one invoice by hand", "assumption"),
    "minutes_per_exception_manual": ("Minutes to chase one exception to an answer", "assumption"),
    "minutes_per_review_with_desk": ("Minutes for a person to decide a hold the desk has prepared", "assumption"),
    "loaded_cost_per_hour_inr": ("Fully loaded cost of an AP analyst, rupees an hour", "assumption"),
    "exception_rate_pct_today": ("Exception rate today, %", "Ardent Partners 2026 average is 19.9%"),
    "touchless_share_pct": ("Share the desk passes untouched, %", "measured: 30 of 49 documents in the robustness report"),
    "duplicate_or_wrong_payment_pct": ("Duplicate or wrong payments, % of payments", "APQC median"),
    "average_invoice_value_inr": ("Average invoice value, rupees", "assumption"),
    "desk_cost_per_month_inr": ("Cost of running the desk a month, rupees (0 = not known)",
                                "assumption: licence, hosting and oversight"),
}

CONTEXT = [
    {"Measure": "Cost to process one invoice", "Figure": "$9.90 average, $2.67 best in class",
     "Source": "Ardent Partners, AP Metrics that Matter 2026"},
    {"Measure": "Exception rate", "Figure": "19.9% average, 11.8% best in class",
     "Source": "Ardent Partners, AP Metrics that Matter 2026"},
    {"Measure": "Staff time spent dealing with suppliers", "Figure": "21.9%", "Source": "Ardent Partners, 2025 report"},
    {"Measure": "Duplicate or erroneous payments, by count", "Figure": "0.8% top, 1.5% median, 2.0% bottom",
     "Source": "APQC open standards benchmark"},
    {"Measure": "Organisations hit by business email compromise in 2025", "Figure": "74%",
     "Source": "AFP Payments Fraud survey 2026"},
]


def _spend(store, runs: list[dict]) -> tuple[float, int]:
    """AI cost recorded on the invoices that used it (a replayed answer carries its recorded cost)."""
    spend, used = 0.0, 0
    for row in runs:
        if row.get("resubmission_of"):
            continue
        result = store.get_run(row["run_id"])
        report = (result.invoice.llm if result and result.invoice else None) or {}
        if report.get("called"):
            used += 1
            spend += float(report.get("cost_usd") or 0.0)
    return spend, used


def _measured(store) -> None:
    st.subheader("What this session shows")
    st.caption("These are synthetic invoices. The figures show what the controls catch, not a forecast.")
    runs = store.list_runs()
    if not runs:
        st.info("No invoice has been processed in these books yet. Run the Guided demo or the sample inbox first.")
        return
    spend, used = _spend(store, runs)
    m = value_model.measured(runs, store.list_cases(), spend, used)
    plain = {"delta_color": "off", "delta_arrow": "off", "border": True}
    row = st.columns(3)
    share = f"{m['touchless_share']:.0%}" if m["touchless_share"] is not None else "-"
    row[0].metric("Passed untouched", share, f"{m['touchless']} of {m['received']} invoices received", **plain)
    row[1].metric("Rejected as duplicates", C.money(m["rejected_duplicates_inr"]), "stopped before posting", **plain)
    row[2].metric("Held for overbilling or a bank change", C.money(m["held_overbilling_or_bank_inr"]),
                  "money stopped until a person decides", **plain)
    row = st.columns(3)
    owners = ", ".join(f"{k} {v}" for k, v in sorted(m["held_by_owner"].items())) or "no case opened yet"
    row[0].metric("Held for a person", m["held"], owners, **plain)
    row[1].metric("Resolved after a hold", m["resolved_by_rule"] + m["resolved_by_person"],
                  f"{m['resolved_by_rule']} by rule, {m['resolved_by_person']} by a person", **plain)
    cost = m["ai_cost_per_invoice_usd"]
    row[2].metric("AI cost per invoice that used it", f"${cost:.4f}" if cost is not None else "-",
                  f"{m['ai_invoices']} invoice(s) used AI assist", **plain)


def _calculator(rules) -> None:
    st.subheader("Your numbers")
    st.caption("Every input starts from rules.yaml and can be changed here. The results follow at once. Nothing you "
               "type here is saved.")
    start = rules.value_model
    values = {}
    for name, (label, source) in ASSUMPTIONS.items():
        left, right = st.columns([0.45, 0.55], vertical_alignment="bottom")
        current = getattr(start, name)
        values[name] = left.number_input(label, min_value=0.0, value=float(current), key=f"vm_{name}",
                                         step=1.0 if current == current.to_integral_value() else 0.1)
        right.caption(f"Source: {source}")
    a = ValueModel(**{k: Decimal(str(v)) for k, v in values.items()})
    m = value_model.model(a)
    st.markdown("**Result**")
    rows = [{"Figure": "Minutes per invoice before", "Value": f"{m['minutes_before']:.1f} minutes"},
            {"Figure": "Minutes per invoice with the desk", "Value": f"{m['minutes_after']} minutes"},
            {"Figure": "Hours saved a month", "Value": f"{m['hours_saved']} hours"},
            {"Figure": "Gross saving a month", "Value": C.money(m["saving_inr"]).removesuffix(".00")},
            {"Figure": "Cost per invoice before", "Value": C.money(m["cost_before_per_invoice"])},
            {"Figure": "Cost per invoice with the desk", "Value": C.money(m["cost_after_per_invoice"])},
            {"Figure": "Duplicate or wrong payment exposure a month",
             "Value": C.money(m["exposure_inr"]).removesuffix(".00")}]
    if m["roi"] is not None:
        rows += [{"Figure": "Net saving a month", "Value": C.money(m["net_saving_inr"]).removesuffix(".00")},
                 {"Figure": "Return on the desk's cost", "Value": f"{m['roi']} times its cost"}]
    C.table(rows)
    st.markdown(
        "<div class='idk-text'><b>How it is worked out.</b> Before: minutes by hand, plus the exception rate times the "
        "minutes to chase one exception. With the desk: the share that is not untouched times the minutes to decide a "
        "prepared hold, plus the same chasing time. Hours saved are invoices a month times the difference, over 60. The "
        "saving is those hours times the cost of an hour. Exposure is invoices a month times the duplicate rate times "
        "the average invoice value. "
        + ("Return is the saving minus the desk's cost, divided by its cost."
           if m["roi"] is not None else "With no cost for the desk entered, only the gross saving is shown.")
        + "</div>", unsafe_allow_html=True)


def page() -> None:
    store = C.get_store()
    rules = C.get_rules()
    st.title("Business case")
    st.caption("What the desk did with the invoices in these books, and what that could be worth with your own numbers.")
    if rules.cases.enabled:                      # resolved-after-a-hold figures need the cases to be current
        case_engine.sync(store, case_engine.master_with_receipts(C.get_master(), store), rules, C.business_date())
    _measured(store)
    _calculator(rules)
    st.subheader("For context")
    C.table(CONTEXT)
    st.caption("Survey figures from other markets, in dollars. Compare against your own baseline.")
    st.subheader("What this page does not claim")
    st.markdown("- No saving on the time spent chasing exceptions: the case desk aims to cut it, but that is not "
                "measured, so it is the same before and after.\n"
                "- No saving from payment runs, early-payment discounts or avoided late interest.\n"
                "- Nothing about real invoices: the measured figures come from synthetic samples, and 30 of 49 outside "
                "documents passing untouched is a small sample.")
