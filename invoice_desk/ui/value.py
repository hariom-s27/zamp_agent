"""Page, Business case: one sentence, three numbers, the assumptions with where each comes from, two
bars, and what is not counted (UX section 3.6). The figures from these books come from the same query
as the Dashboard (dashboard.book_figures), so the two pages always agree."""
from __future__ import annotations

from decimal import Decimal

import altair as alt
import pandas as pd
import streamlit as st

from .. import cases as case_engine
from .. import textutil as T
from .. import value as value_model
from ..rules import ValueModel
from . import common as C
from . import ui_kit as K

MEASURED, BENCHMARK, ESTIMATE = "measured in this app", "published benchmark", "your estimate"
# What each assumption is, and where its starting value comes from.
ASSUMPTIONS = {
    "invoices_per_month": ("Invoices a month", ESTIMATE),
    "minutes_per_invoice_manual": ("Minutes to handle one invoice by hand", ESTIMATE),
    "minutes_per_exception_manual": ("Minutes to chase one exception to an answer", ESTIMATE),
    "minutes_per_review_with_desk": ("Minutes for a person to decide a prepared hold", ESTIMATE),
    "loaded_cost_per_hour_inr": ("Cost of an AP analyst, rupees an hour", ESTIMATE),
    "exception_rate_pct_today": ("Invoices that need a person today, %", BENCHMARK),
    "touchless_share_pct": ("Share the desk clears with nobody touching it, %", MEASURED),
    "duplicate_or_wrong_payment_pct": ("Duplicate or wrong payments, % of payments", BENCHMARK),
    "average_invoice_value_inr": ("Average invoice value, rupees", ESTIMATE),
    "desk_cost_per_month_inr": ("Cost of running the desk a month, rupees (0 if not known)", ESTIMATE),
}
SOURCE_NOTE = {
    "exception_rate_pct_today": "Ardent Partners 2026: 19.9% on average",
    "touchless_share_pct": "30 of 49 difficult documents in the reading test",
    "duplicate_or_wrong_payment_pct": "APQC median",
}
NOT_COUNTED = ("Duplicate payments avoided · late-payment interest avoided · early-payment discounts taken · "
               "time saved chasing exceptions (the desk aims to cut it, but it is not measured, so it is the same "
               "before and after)")


def _spend_inr(store, runs: list[dict], rate: Decimal) -> tuple[Decimal, int]:
    """AI cost recorded on the invoices that used it, in rupees (a replayed answer carries its recorded cost)."""
    spend, used = Decimal(0), 0
    for row in runs:
        if row.get("resubmission_of"):
            continue
        result = store.get_run(row["run_id"])
        report = (result.invoice.llm if result and result.invoice else None) or {}
        if report.get("called"):
            used += 1
            spend += Decimal(str(report.get("cost_usd") or 0)) * rate
    return spend, used


def _books(store, rules) -> None:
    """What these books show, from the same query as the Dashboard."""
    from .dashboard import book_figures
    st.subheader("What these books show")
    runs = store.list_runs()
    if not runs:
        st.info("No invoice has been processed in these books yet. Run the Guided demo or the sample inbox first.",
                icon=":material/inbox:")
        return
    f = book_figures(store, runs)
    n, cleared, held = len(f["invoices"]), len(f["cleared"]), len(f["held"])
    flat = {"delta_color": "off", "delta_arrow": "off", "border": True}
    row = st.columns(3)
    row[0].metric("Invoices in these books", n, "the same count as the Dashboard", **flat)
    row[1].metric("Cleared, no person", f"{round(100 * cleared / n)}%" if n else "-",
                  f"{cleared} of {T.plural(n, 'invoice')}", **flat)
    row[2].metric("On hold now", held, K.inr_short(f["held_amount"]) + " waiting on a person" if held else "nothing waiting",
                  **flat)
    owners = " · ".join(f"{team} {count}" for team, count in sorted(f["by_owner"].items()))
    if owners:
        st.markdown(f"**Who is holding them:** {owners}")
    spend, used = _spend_inr(store, runs, rules.display.usd_to_inr)
    if used and spend > 0:
        st.caption(f"AI reading cost: {T.fmt_inr(spend / used)} an invoice, on the {T.plural(used, 'invoice')} that "
                   f"needed it.")
    st.caption("These are synthetic invoices. They show what the controls catch, not a forecast.")


def _assumptions(rules) -> ValueModel:
    start = rules.value_model
    if st.button("Reset to defaults", key="vm_reset", icon=":material/restart_alt:"):
        for name in ASSUMPTIONS:
            st.session_state.pop(f"vm_{name}", None)
        st.rerun()
    values = {}
    for name, (label, source) in ASSUMPTIONS.items():
        current = getattr(start, name)
        values[name] = st.number_input(label, min_value=0.0, value=float(current), key=f"vm_{name}",
                                       step=1.0 if current == current.to_integral_value() else 0.1)
        st.caption(f":gray[{source}]" + (f" · {SOURCE_NOTE[name]}" if name in SOURCE_NOTE else ""))
    return ValueModel(**{k: Decimal(str(v)) for k, v in values.items()})


def _bars(before: Decimal, after: Decimal) -> None:
    frame = pd.DataFrame([{"When": "Today", "Rupees": float(before)}, {"When": "With the desk", "Rupees": float(after)}])
    chart = alt.Chart(frame).mark_bar(size=26).encode(
        y=alt.Y("When:N", sort=["Today", "With the desk"], title=None, axis=alt.Axis(labelLimit=200)),
        x=alt.X("Rupees:Q", title="Cost per invoice (₹)"),
        color=alt.Color("When:N", scale=alt.Scale(domain=["Today", "With the desk"], range=["#9aa0a6", "#1F3A5F"]),
                        legend=None),
        tooltip=["When", alt.Tooltip("Rupees:Q", format=",.2f")]).properties(height=120)
    st.altair_chart(chart, width="stretch", key="vm_bars")


def page() -> None:
    store = C.get_store()
    rules = C.get_rules()
    st.title("Business case")
    if rules.cases.enabled:                      # the case figures must be current, as on the Dashboard
        case_engine.sync(store, case_engine.master_with_receipts(C.get_master(), store), rules, C.business_date())
    left, right = st.columns([0.36, 0.64], gap="large")
    with left:
        st.markdown("**Your assumptions**")
        a = _assumptions(rules)
    m = value_model.model(a)
    hours = m["hours_saved"]
    yearly = int(a.invoices_per_month * 12)
    with right:
        if hours <= 0:
            st.markdown("### With these numbers the desk does not pay for itself.")
            st.markdown("Try a higher invoice count, or fewer minutes for a prepared hold.")
        else:
            pays = (" and costs less than it saves from the first month" if m["net_saving_inr"] is not None and
                    m["net_saving_inr"] > 0 else "")
            st.markdown(f"### At {yearly:,} invoices a year, the desk gives back about {hours:,.0f} hours a month{pays}.")
        st.caption("Change any assumption on the left. Nothing you type is saved.")
        cols = st.columns(3)
        with cols[0]:
            K.kpi_card("Hours given back", f"{hours:,.0f} a month", "from the minutes and the share cleared",
                       help="Invoices a month times the minutes saved on each, over 60.")
        with cols[1]:
            K.kpi_card("Cost per invoice", f"{K.inr(float(m['cost_before_per_invoice']))} → "
                                           f"{K.inr(float(m['cost_after_per_invoice']))}", "today → with the desk",
                       help="Minutes per invoice times the cost of an hour, over 60.")
        with cols[2]:
            if m["net_saving_inr"] is not None:
                K.kpi_card("Net saving", K.inr_short(float(m["net_saving_inr"])) + " a month",
                           f"after the desk's cost of {K.inr_short(float(a.desk_cost_per_month_inr))}",
                           help="The saving minus the cost of running the desk.")
            else:
                K.kpi_card("Gross saving", K.inr_short(float(m["saving_inr"])), "a month; enter the desk's cost for "
                           "the net figure", help="Hours given back times the cost of an hour.")
        _bars(m["cost_before_per_invoice"], m["cost_after_per_invoice"])
        st.caption("Published US averages for comparison: 9.40 to 9.90 dollars an invoice, 2.67 to 2.78 for the best "
                   "groups. US figures from Ardent Partners. Indian costs per invoice are lower. Use your own.")
        with st.container(border=True):
            st.markdown("**What this does not count**")
            st.markdown(NOT_COUNTED)
        with st.expander("How it is worked out"):
            st.markdown(
                "Before: minutes by hand, plus the share of invoices that need a person times the minutes to chase "
                "one. With the desk: the share not cleared untouched times the minutes to decide a prepared hold, "
                "plus the same chasing time. Hours given back are invoices a month times the difference, over 60. "
                f"Exposure to duplicate or wrong payments: {K.inr_short(float(m['exposure_inr']))} a month at these "
                f"numbers.")
    st.divider()
    _books(store, rules)
