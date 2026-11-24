"""Page, Dashboard: five numbers, three bar charts and what needs attention, then the full history
of any run (stages, evidence, document, receipts) in its own tab. Everything is read from the
stored records; nothing here changes a decision."""
from __future__ import annotations

import statistics
from collections import Counter

import altair as alt
import pandas as pd
import streamlit as st

from .. import cases as case_engine
from .. import textutil as T
from . import common as C
from . import ui_kit as K
from .process import amount_text, plain

ORDER = ["APPROVE", "HOLD", "REJECT"]
COLOURS = ["#1a8a52", "#d99a00", "#c9343d"]
BAD_FILE = {"UNREADABLE_FILE", "FILE_TOO_LARGE", "SCAN_NOT_READ", "NOT_AN_INVOICE", "NOT_A_TAX_INVOICE",
            "UNSUPPORTED_DOCUMENT"}                      # not an invoice: left out of every rate
DUPLICATES = {"DUPLICATE_FILE", "DUPLICATE_INVOICE_NUMBER", "DUPLICATE_CONTENT", "POSSIBLE_DUPLICATE"}
OVERBILLING = {"PRICE_VARIANCE", "QTY_OVER_PO", "OVER_PO_BALANCE"}
BANK = "BANK_DETAILS_CHANGED"
# Display only: reason codes grouped in plain words for the chart. The codes themselves are unchanged.
FAMILY = [
    ("Price above the PO", {"PRICE_VARIANCE"}),
    ("Goods receipt not posted", {"GOODS_NOT_RECEIVED"}),
    ("Quantity above the PO or receipt", {"QTY_OVER_PO", "OVER_PO_BALANCE", "BUNDLED_PARTIAL"}),
    ("No open PO", {"PO_NOT_FOUND", "NO_PO_FOUND", "PO_CLOSED", "PO_VENDOR_MISMATCH", "LINE_NOT_ON_PO", "PO_INFERRED",
                    "DATE_BEFORE_PO"}),
    ("Tax or total wrong", {"ARITHMETIC_MISMATCH", "AMOUNT_WORDS_MISMATCH", "TAX_RATE_UNUSUAL", "TAX_TYPE_MISMATCH",
                            "GSTIN_INVALID", "BUYER_GSTIN_MISSING", "CURRENCY_NOT_SUPPORTED"}),
    ("Vendor details differ", {"VENDOR_NOT_APPROVED", "VENDOR_BLOCKED", "VENDOR_GSTIN_MISMATCH", "VENDOR_NAME_VARIANT",
                               "VENDOR_MATCHED_BY_NAME", "MISSING_VENDOR_ID"}),
    ("A value could not be read", {"MISSING_INVOICE_NUMBER", "MISSING_INVOICE_DATE", "MISSING_TOTAL",
                                   "LINE_ITEMS_UNREADABLE", "LOW_CONFIDENCE_READ", "SCAN_READS_DIFFER"}),
    ("Duplicate", DUPLICATES | {"RESUBMITTED_WHILE_ON_HOLD"}),
    ("Bank account changed", {BANK}),
]
OUTCOMES = ["Cleared without a person", "Held, then cleared", "Still on hold", "Rejected"]
OUTCOME_COLOURS = ["#1a8a52", "#9aa0a6", "#d99a00", "#c9343d"]


def held_reason_counts(runs: list[dict], rules) -> Counter:
    """Why invoices were held: each held invoice counted once per reason family. A file sent again and an
    earlier record of the same file read again are not counted, and nor is the duplicate-file stop itself."""
    latest: dict[str, dict] = {}
    for r in invoices(runs):                        # newest first: the first row per file is the current record
        latest.setdefault(r.get("file_sha256") or r["run_id"], r)
    counts: Counter = Counter()
    for r in latest.values():
        if r["verdict"] != "HOLD":
            continue
        families = {family(code) for code in codes_of(r) - {"DUPLICATE_FILE", "RESUBMITTED_WHILE_ON_HOLD"}
                    if rules.reason(code).action == "HOLD"}
        counts.update(families)
    return counts


def month_chart(frame, months: list[str]):
    """Outcomes per month. The legend sits under the chart, one label a line, so no label is cut off."""
    return alt.Chart(frame).mark_bar().encode(
        x=alt.X("Month:N", sort=months, title=None),
        y=alt.Y("Invoices:Q", stack="zero", axis=alt.Axis(tickMinStep=1, format="d")),
        color=alt.Color("Outcome:N", scale=alt.Scale(domain=OUTCOMES, range=OUTCOME_COLOURS),
                        legend=alt.Legend(orient="bottom", direction="vertical", title=None, labelLimit=0)),
        order=alt.Order("rank:Q"), tooltip=["Month", "Outcome", "Invoices"])


def family(code: str) -> str:
    return next((name for name, codes in FAMILY if code in codes), "Other")


def codes_of(row: dict) -> set[str]:
    return {c for c in (row.get("reason_codes") or "").split(",") if c}


def received(runs: list[dict]) -> list[dict]:
    """A file sent twice is one invoice."""
    return [r for r in runs if not r.get("resubmission_of")]


def invoices(runs: list[dict]) -> list[dict]:
    return [r for r in received(runs) if not codes_of(r) & BAD_FILE]


def outcome(row: dict) -> str:
    if row["verdict"] == "APPROVE":
        return OUTCOMES[0]
    if row["review_status"] == "PENDING":
        return OUTCOMES[2]
    if row["verdict"] == "HOLD" and row["review_status"] in ("APPROVED", "RELEASED", "HANDLED"):
        return OUTCOMES[1]
    return OUTCOMES[3]


def money_stopped(items: list[dict]) -> dict[str, tuple[float, int]]:
    """Money that would have been paid wrongly. Duplicates and unverified bank changes count in full while
    held or rejected; overbilling counts only its excess; anything later cleared counts as zero."""
    parts: dict[str, tuple[float, int]] = {}

    def add(name: str, amount: float) -> None:
        total, count = parts.get(name, (0, 0))
        parts[name] = (total + amount, count + 1)

    for it in items:
        if it["status"] == "cleared":
            continue
        if BANK in it["codes"]:
            add("Bank change stopped", it["total"])
        elif it["codes"] & DUPLICATES:
            add("Duplicates", it["total"])
        elif it["codes"] & OVERBILLING and it["excess"] > 0:
            add("Overbilling", it["excess"])
    return parts


def largest_part(parts: dict[str, tuple[float, int]]):
    """The part named on the card when it is more than half the total."""
    total = sum(a for a, _ in parts.values())
    if not total:
        return None
    name, (amount, _) = max(parts.items(), key=lambda kv: kv[1][0])
    return name if amount > total / 2 else None


def stopped_items(store, runs: list[dict]) -> list[dict]:
    out = []
    for r in received(runs):
        codes = codes_of(r)
        if not codes & (DUPLICATES | OVERBILLING | {BANK}):
            continue
        status = ("cleared" if outcome(r) in OUTCOMES[:2] else "held" if r["review_status"] == "PENDING" else "rejected")
        excess = 0.0
        if codes & OVERBILLING:
            result = store.get_run(r["run_id"])
            reasons = result.decision.reasons if result and result.decision else []
            excess = sum(float(f.evidence.get("line_extra") or 0) for f in reasons if f.code == "PRICE_VARIANCE")
        out.append({"codes": codes, "total": float(T.to_decimal(r["total"]) or 0), "status": status, "excess": excess})
    return out


def _kpis(runs: list[dict]) -> None:
    """Six figures an AP manager asks for. A file that was sent twice is one invoice, so
    re-submissions are counted apart and never add to a rupee figure."""
    received = [r for r in runs if not r.get("resubmission_of")]
    again = len(runs) - len(received)
    total = len(received)
    approved = [r for r in received if r["verdict"] == "APPROVE"]
    held = [r for r in received if r["review_status"] == "PENDING"]
    rejected = [r for r in received if r["verdict"] == "REJECT"]
    released = approved + [r for r in received if r["review_status"] in ("APPROVED", "RELEASED")]   # rupee invoices only
    value = lambda rows: sum((T.to_decimal(r["total"]) or 0 for r in rows), 0)   # noqa: E731
    text_times = [float(r["duration_ms"] or 0) for r in received if not r["scanned"]]
    scan_times = [float(r["duration_ms"] or 0) for r in received if r["scanned"]]
    plain = {"delta_color": "off", "delta_arrow": "off", "border": True}
    row = st.columns(3)
    row[0].metric("Documents received", total,
                  f"{sum(1 for r in received if r['scanned'])} scanned" + (f"; {again} sent twice (not counted)" if again else ""),
                  **plain)
    row[1].metric("Straight-through processing (STP)", f"{len(approved) / total:.0%}" if total else "-",
                  f"{len(approved)} of {total} matched every rule, no manual touch", **plain)
    row[2].metric("Waiting for review", len(held), C.money(value(held)) + " on hold" if held else "queue is empty", **plain)
    row = st.columns(3)
    row[0].metric("Rejected", len(rejected), "nothing was booked for these", **plain)
    row[1].metric("Released for payment", C.money(value(released)),
                  f"{T.plural(len(released), 'invoice')}, including approved on review or released by rule", **plain)
    typical = C.took(statistics.median(text_times)) if text_times else "-"
    row[2].metric("Typical handling time", typical if text_times else (C.took(statistics.median(scan_times)) if scan_times else "-"),
                  (f"scanned pages: {C.took(statistics.median(scan_times))} (OCR)" if scan_times and text_times
                   else "from file received to decision recorded"), **plain)


def _case_figures(store) -> None:
    """One more row once the case desk has opened a case: what happened after the holds."""
    all_cases = store.list_cases()
    if not all_cases:
        return
    plain = {"delta_color": "off", "delta_arrow": "off", "border": True}
    by_rule = [c for c in all_cases if c["resolution"] == "released by rule"]
    pressing = [c for c in all_cases if c["state"] != "RESOLVED" and (c["priority"] == "urgent" or c["escalated_to"])]
    row = st.columns(3)
    row[0].metric("Cases opened", len(all_cases), f"{sum(1 for c in all_cases if c['state'] != 'RESOLVED')} still open",
                  **plain)
    row[1].metric("Resolved without a person", len(by_rule), "released by rule once the goods were received", **plain)
    row[2].metric("Urgent or escalated", len(pressing), "open cases past their answer date or near a legal date",
                  **plain)



def _chain(store) -> None:
    check = store.verify_chain()
    if check["ok"]:
        st.success(f"Audit trail: intact. All {check['count']} entries were re-checked just now.",
                   icon=":material/verified_user:")
    else:
        st.error("Audit trail: BROKEN. " + "; ".join(check["problems"][:5]), icon=":material/gpp_bad:")
    with st.expander("What this check proves, and what it does not"):
        from ..store import signing_enabled
        head = store.chain_head()
        st.markdown(
            "- Every decision by the desk and every decision by a reviewer is written once as an entry. Each entry "
            "carries a seal worked out from its own content and from the entry before it.\n"
            "- This check works out every seal again. Editing or removing any past entry makes it fail.\n"
            "- What it does not prove on its own: someone with full access to the books could rewrite the whole "
            "trail from the start. Two things close that gap: keep the latest seal somewhere the desk cannot write "
            "to (an email to audit, a note in the ERP), and switch on signing, which signs each entry with a key the "
            "books do not hold.")
        st.caption(f"Latest seal: {head}  ·  entries: {check['count']}  ·  "
                   f"signing: {'on' if signing_enabled() else 'off'}")



def _open_cases(store) -> list[dict]:
    return [c for c in store.list_cases() if c["state"] != "RESOLVED"]


def _days_left(day_text) -> int | None:
    day = case_engine._as_date(day_text or "")
    return (day - C.business_date()).days if day else None


def _at_risk(case: dict, within: int) -> bool:
    left = _days_left(case.get("statutory_due"))
    return left is not None and left <= within


def team_of(case: dict) -> str:
    return "The vendor" if case["waiting_on"] == "vendor" else case["waiting_on"]


def book_figures(store, runs: list[dict]) -> dict:
    """The figures the Dashboard and the Business case both show, from one query, so they always agree:
    invoices in the books, cleared without a person, on hold now and who is holding them."""
    inv = invoices(runs)
    cleared = [r for r in inv if outcome(r) == OUTCOMES[0]]
    held = [r for r in received(runs) if r["review_status"] == "PENDING"]
    teams: dict[str, list[int]] = {}
    for case in _open_cases(store):
        late = (_days_left(case["due_on"]) or 0) < 0
        teams.setdefault(team_of(case), [0, 0])[1 if late else 0] += 1
    return {"invoices": inv, "cleared": cleared, "held": held,
            "held_amount": sum(float(T.to_decimal(r["total"]) or 0) for r in held),
            "teams": teams, "by_owner": {t: sum(v) for t, v in teams.items()}}


CARD_LABELS = ["Cleared, no person", "On hold now", "Days to clear a hold", "Money stopped",
               "Legal dates at risk"]


def _cards(store, runs: list[dict], rules) -> None:
    within = rules.display.attention_days
    figures = book_figures(store, runs)
    inv, cleared, held = figures["invoices"], figures["cleared"], figures["held"]
    open_cases = _open_cases(store)
    overdue = sum(1 for c in open_cases if (_days_left(c["due_on"]) or 0) < 0)
    closed = [(case_engine._as_date(c["resolved_on"]) - case_engine._as_date(c["opened_on"])).days
              for c in store.list_cases() if c["state"] == "RESOLVED" and c["resolved_on"] and c["opened_on"]]
    parts = money_stopped(stopped_items(store, runs))
    stopped = sum(a for a, _ in parts.values())
    biggest = largest_part(parts)
    risk = [c for c in open_cases if _at_risk(c, within)]
    past = sum(1 for c in risk if (_days_left(c["statutory_due"]) or 0) < 0)
    cols = st.columns(5)
    with cols[0]:
        K.kpi_card(CARD_LABELS[0], f"{round(100 * len(cleared) / len(inv))}%" if inv else "-",
                   f"of {T.plural(len(inv), 'invoice')} in these books",
                   help="Invoices that passed every check with nobody touching them, out of all invoices received. "
                        "Files that are not invoices are left out.")
    with cols[1]:
        K.kpi_card(CARD_LABELS[1], str(len(held)),
                   f"{K.inr_short(figures['held_amount'])} · {overdue} overdue",
                   help="Invoices waiting on a person today. Overdue means past the answer date the rule book gives "
                        "its owner.")
    with cols[2]:
        if closed:
            p90 = sorted(closed)[max(0, -(-9 * len(closed) // 10) - 1)]
            K.kpi_card(CARD_LABELS[2], str(round(statistics.median(closed))),
                       f"median · 9 in 10 within {p90} day{'s' if p90 != 1 else ''}",
                       help="From the day an invoice was put on hold to the day it was cleared or rejected.")
        else:
            K.kpi_card(CARD_LABELS[2], "-", "no hold has been closed yet",
                       help="From the day an invoice was put on hold to the day it was cleared or rejected.")
    with cols[3]:
        note = (f"{biggest}: {K.inr_short(parts[biggest][0])}" if biggest else
                T.plural(sum(n for _, n in parts.values()), "invoice"))
        K.kpi_card(CARD_LABELS[3], K.inr_short(stopped), note,
                   help="Money that would have been paid wrongly. Duplicates and unverified bank changes count in "
                        "full. Overbilling counts only the excess. Anything later cleared counts as zero.")
        if parts:
            with st.popover("The parts", icon=":material/list:"):
                st.markdown(" · ".join(f"{name} {K.inr_short(a)} ({n})" for name, (a, n) in parts.items()))
    with cols[4]:
        K.kpi_card(CARD_LABELS[4], str(len(risk)),
                   f"small suppliers within {within} days of the legal limit" + (f", {past} past it" if past else ""),
                   help="Micro and small suppliers must be paid within the agreed period, at most 45 days from "
                        "acceptance, or 15 days with no written terms.")


def _charts(store, runs: list[dict]) -> None:
    a, b, c = st.columns(3, gap="large")
    inv = invoices(runs)
    with a:
        st.markdown("**What happened to each month's invoices**")
        rows = Counter((str(r["created_at"])[:7], outcome(r)) for r in inv)
        months = sorted({m for m, _ in rows})
        frame = pd.DataFrame([{"Month": pd.Timestamp(m + "-01").strftime("%b %Y"), "Outcome": o, "Invoices": n,
                               "rank": OUTCOMES.index(o)} for (m, o), n in rows.items()])
        if frame.empty:
            st.caption("No invoices yet.")
        else:
            chart = month_chart(frame, [pd.Timestamp(m + "-01").strftime("%b %Y") for m in months])
            st.altair_chart(chart, width="stretch", key="dash_chart_months")
    with b:
        st.markdown("**Who is holding invoices today**")
        teams = book_figures(store, runs)["teams"]
        if not teams:
            st.caption("Nobody is holding an invoice today.")
        else:
            frame = pd.DataFrame([{"Team": t, "State": s, "Invoices": v[i]} for t, v in teams.items()
                                  for i, s in enumerate(["On time", "Overdue"]) if v[i]])
            chart = alt.Chart(frame).mark_bar().encode(
                y=alt.Y("Team:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)),
                x=alt.X("Invoices:Q", stack="zero", axis=alt.Axis(tickMinStep=1, format="d")),
                color=alt.Color("State:N", scale=alt.Scale(domain=["On time", "Overdue"], range=["#9aa0a6", "#c9343d"]),
                                legend=alt.Legend(orient="bottom", title=None)), tooltip=["Team", "State", "Invoices"])
            st.altair_chart(chart, width="stretch", key="dash_chart_teams")
    with c:
        st.markdown("**Why invoices were held**")
        counts = held_reason_counts(runs, C.get_rules())
        if not counts:
            st.caption("No holds so far.")
        else:
            top = counts.most_common(7)
            rest = sum(counts.values()) - sum(n for _, n in top)
            frame = pd.DataFrame([{"Reason": k, "Invoices": v} for k, v in top] +
                                 ([{"Reason": "Other", "Invoices": rest}] if rest else []))
            chart = alt.Chart(frame).mark_bar(color="#d99a00").encode(
                y=alt.Y("Reason:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220)),
                x=alt.X("Invoices:Q", axis=alt.Axis(tickMinStep=1)), tooltip=["Reason", "Invoices"])
            st.altair_chart(chart, width="stretch", key="dash_chart_reasons")


def _needs_attention(store, rules) -> None:
    within = rules.display.attention_days
    cases = _open_cases(store)
    if not cases:
        return

    def urgency(case: dict) -> tuple:
        legal = _days_left(case.get("statutory_due"))
        rank = 0 if legal is not None and legal < 0 else 1 if legal is not None and legal <= within else 2
        return rank, case["due_on"]

    st.markdown("**Needs attention**")
    for case in sorted(cases, key=urgency)[:rules.display.attention_max]:
        row = store.run_row(case["run_id"]) or {}
        line = st.container(horizontal=True, vertical_alignment="center", border=True)
        amount = amount_text(row.get("total"))
        who = "the vendor" if case["waiting_on"] == "vendor" else case["waiting_on"]
        number = f"invoice {plain(row['invoice_no'])}" if row.get("invoice_no") else "invoice (number missing)"
        line.markdown(f"**{plain(C.vendor_case(row.get('vendor_name')) or 'Unknown vendor')}** · {number}"
                      f"{' · ' + amount if amount else ''} · Waiting on {plain(who)}")
        due = case_engine._as_date(case["due_on"])
        if due:
            line.markdown(K.chip_md(*K.due_label(due, C.business_date())))
        legal = _days_left(case.get("statutory_due"))
        if legal is not None:
            line.markdown(f":gray-badge[:material/gavel: Small supplier · legal limit "
                          f"{case_engine.day_text(case_engine._as_date(case['statutory_due']))}]")
        if line.button("Open", key=f"att_{case['case_id']}"):
            st.session_state["goto"] = "cases"
            st.rerun()
    if st.button("See all cases", key="dash_all_cases", icon=":material/folder_open:"):
        st.session_state["goto"] = "cases"
        st.rerun()


def _history(store, runs: list[dict], rules) -> None:
    f1, f2, f3 = st.columns([0.3, 0.3, 0.4])
    verdicts = f1.multiselect("Decision", ORDER, default=[], placeholder="All decisions", key="dash_verdict",
                              format_func=C.verdict_display_label)
    vendors = sorted({r["vendor_name"] for r in runs if r["vendor_name"]})
    vendor = f2.selectbox("Vendor", ["All vendors"] + vendors, key="dash_vendor")
    query = f3.text_input("Search file, invoice number or PO", key="dash_query").strip().lower()
    shown = [r for r in runs
             if (not verdicts or r["verdict"] in verdicts)
             and (vendor == "All vendors" or r["vendor_name"] == vendor)
             and (not query or query in " ".join(str(r.get(k) or "") for k in ("file_name", "invoice_no", "po_number", "run_id")).lower())]
    rows = C.run_table_rows(shown, rules)
    if not rows:
        st.info("No record matches these filters.")
        return
    frame = pd.DataFrame(rows)
    event = st.dataframe(frame, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key="dash_table", height=min(38 * len(rows) + 40, 420))
    st.download_button("Download this table (CSV)", data=frame.to_csv(index=False).encode("utf-8-sig"),
                       file_name="invoice_desk_history.csv", mime="text/csv", key="dash_csv", on_click="ignore")
    picked = event.selection.rows if event and event.selection else []
    ids = [r["run_id"] for r in shown]
    default = ids.index(st.session_state["dash_run"]) if st.session_state.get("dash_run") in ids else 0
    if picked:
        default = picked[0]
    st.subheader("Invoice record")
    run_id = st.selectbox("Record to open (or select a row above)", ids, index=default, key=f"dash_pick_{default}_{len(ids)}",
                          format_func=lambda i: next(f"{r['run_id']}  {C.verdict_display_label(r['verdict'])}  "
                                                     f"{C.display_name(r['file_name'])}"
                                                     for r in shown if r["run_id"] == i))
    st.session_state["dash_run"] = run_id
    result = store.get_run(run_id)
    if result is None or result.decision is None:
        st.info("This reference has no stored record.")
        return
    C.render_run(result, store, key=f"dash_{run_id}")


def page() -> None:
    store = C.get_store()
    rules = C.get_rules()
    st.title("Dashboard")
    st.caption("What was received, how it was decided and where it stands. Everything here is read from the stored records.")
    if rules.cases.enabled:                  # the same case figures as the Business case and Cases pages
        case_engine.sync(store, case_engine.master_with_receipts(C.get_master(), store), rules, C.business_date())
    runs = store.list_runs()
    if not runs:
        st.info("Nothing received yet. No document has been processed in these books. Run the Guided demo, process "
                "one invoice, or process the whole sample inbox now.", icon=":material/inbox:")
        if st.button(f"Process all {len(C.manifest())} samples now", type="primary", key="dash_batch"):
            st.session_state["goto"], st.session_state["auto_batch"] = "process", True
            st.rerun()
        return

    overview, history = st.tabs(["Overview", "All invoices"])
    with overview:
        _cards(store, runs, rules)
        st.caption("For comparison, published surveys put the share of invoices that need a person at 18 to 20%. The "
                   "sample inbox is built to show exceptions, so its share is higher. It is not a result.")
        _charts(store, runs)
        _needs_attention(store, rules)
        _chain(store)
        with st.expander("More figures"):
            _kpis(runs)
            _case_figures(store)
    with history:
        _history(store, runs, rules)
