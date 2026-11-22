"""Page 2, Dashboard: what has been processed, how it was decided, and the full record
of any run (stages, evidence, document, receipts)."""
from __future__ import annotations

import statistics
from collections import Counter

import altair as alt
import pandas as pd
import streamlit as st

from .. import textutil as T
from . import common as C

ORDER = ["APPROVE", "HOLD", "REJECT"]
COLOURS = ["#1a8a52", "#d99a00", "#c9343d"]


def _kpis(runs: list[dict]) -> None:
    total = len(runs)
    approved = [r for r in runs if r["verdict"] == "APPROVE"]
    held = [r for r in runs if r["review_status"] == "PENDING"]
    rejected = [r for r in runs if r["verdict"] == "REJECT"]
    released = approved + [r for r in runs if r["review_status"] == "APPROVED"]      # rupee invoices only
    value = lambda rows: sum((T.to_decimal(r["total"]) or 0 for r in rows), 0)   # noqa: E731
    times = [float(r["duration_ms"] or 0) for r in runs]
    plain = {"delta_color": "off", "delta_arrow": "off", "border": True}
    row = st.columns(3)
    row[0].metric("Documents processed", total, f"{sum(1 for r in runs if r['scanned'])} of them scanned", **plain)
    row[1].metric("Approved with no person involved", len(approved), f"{len(approved) / total:.0%} straight through", **plain)
    row[2].metric("Waiting for a person", len(held), C.money(value(held)) + " on hold" if held else "queue is empty", **plain)
    row = st.columns(3)
    row[0].metric("Rejected", len(rejected), "nothing was booked for these", **plain)
    row[1].metric("Released for payment", C.money(value(released)), f"{len(released)} invoice(s), including approved on review", **plain)
    row[2].metric("Median processing time", f"{statistics.median(times):.0f} ms", f"slowest {max(times):.0f} ms", **plain)


def _charts(runs: list[dict], rules) -> None:
    left, right = st.columns(2, gap="large")
    counts = Counter(r["verdict"] for r in runs)
    decisions = pd.DataFrame({"Decision": ORDER, "Documents": [counts.get(v, 0) for v in ORDER]})
    with left:
        st.markdown("**Decisions**")
        chart = alt.Chart(decisions).mark_bar(cornerRadius=3).encode(
            y=alt.Y("Decision:N", sort=ORDER, title=None),
            x=alt.X("Documents:Q", axis=alt.Axis(tickMinStep=1), title="Documents"),
            color=alt.Color("Decision:N", scale=alt.Scale(domain=ORDER, range=COLOURS), legend=None),
            tooltip=["Decision", "Documents"]).properties(height=alt.Step(40))
        st.altair_chart(chart, width="stretch")
    reasons: Counter = Counter()
    action: dict[str, str] = {}
    for r in runs:
        for code in [c for c in (r["reason_codes"] or "").split(",") if c]:
            title = rules.reason(code).title
            reasons[title] += 1
            action[title] = rules.reason(code).action
    with right:
        st.markdown("**Why documents were held or rejected**")
        if not reasons:
            st.caption("No holds or rejections so far.")
        else:
            frame = pd.DataFrame([{"Reason": k, "Documents": v, "Action": action[k]} for k, v in reasons.most_common(12)])
            chart = alt.Chart(frame).mark_bar(cornerRadius=3).encode(
                y=alt.Y("Reason:N", sort="-x", title=None, axis=alt.Axis(labelLimit=340, labelOverlap=False)),
                x=alt.X("Documents:Q", axis=alt.Axis(tickMinStep=1), title="Documents"),
                color=alt.Color("Action:N", scale=alt.Scale(domain=["HOLD", "REJECT"], range=[COLOURS[1], COLOURS[2]]),
                                legend=alt.Legend(orient="bottom", title=None)),
                tooltip=["Reason", "Documents", "Action"]).properties(height=alt.Step(24))
            st.altair_chart(chart, width="stretch")


def _chain(store) -> None:
    check = store.verify_chain()
    if check["ok"]:
        st.success(f"Audit trail intact: {check['count']} receipt(s), every hash and every link recomputed just now.")
    else:
        st.error("Audit trail broken: " + "; ".join(check["problems"][:5]))
    st.caption("Every machine decision and every human review writes a receipt whose hash covers its content and the "
               "previous receipt's hash. Editing or deleting any stored receipt makes this check fail.")


def page() -> None:
    store = C.get_store()
    rules = C.get_rules()
    st.title("Dashboard")
    st.caption("Run history, status and outputs. Everything here is read from the stored run records.")
    runs = store.list_runs()
    if not runs:
        st.markdown(C.neutral_banner_html("No runs yet", "Nothing has been processed in this database",
                                          "Process an invoice on the first page, or run every bundled sample now."),
                    unsafe_allow_html=True)
        if st.button(f"Process all {len(C.manifest())} samples now", type="primary", key="dash_batch"):
            st.session_state["goto"], st.session_state["auto_batch"] = "process", True
            st.rerun()
        return

    _kpis(runs)
    _charts(runs, rules)
    _chain(store)

    st.subheader("Run history")
    f1, f2, f3 = st.columns([0.3, 0.3, 0.4])
    verdicts = f1.multiselect("Decision", ORDER, default=[], placeholder="All decisions", key="dash_verdict")
    vendors = sorted({r["vendor_name"] for r in runs if r["vendor_name"]})
    vendor = f2.selectbox("Vendor", ["All vendors"] + vendors, key="dash_vendor")
    query = f3.text_input("Search file, invoice number or PO", key="dash_query").strip().lower()
    shown = [r for r in runs
             if (not verdicts or r["verdict"] in verdicts)
             and (vendor == "All vendors" or r["vendor_name"] == vendor)
             and (not query or query in " ".join(str(r.get(k) or "") for k in ("file_name", "invoice_no", "po_number", "run_id")).lower())]
    rows = C.run_table_rows(shown, rules)
    if not rows:
        st.info("No run matches these filters.")
        return
    frame = pd.DataFrame(rows)
    event = st.dataframe(frame, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key="dash_table", height=min(38 * len(rows) + 40, 420))
    st.download_button("Download this table (CSV)", data=frame.to_csv(index=False).encode("utf-8-sig"),
                       file_name="invoice_desk_runs.csv", mime="text/csv", key="dash_csv", on_click="ignore")
    picked = event.selection.rows if event and event.selection else []
    ids = [r["run_id"] for r in shown]
    default = ids.index(st.session_state["dash_run"]) if st.session_state.get("dash_run") in ids else 0
    if picked:
        default = picked[0]
    st.subheader("Run record")
    run_id = st.selectbox("Run to open (or select a row above)", ids, index=default, key=f"dash_pick_{default}_{len(ids)}",
                          format_func=lambda i: next(f"{r['run_id']}  {r['verdict']}  {r['file_name']}" for r in shown if r["run_id"] == i))
    st.session_state["dash_run"] = run_id
    result = store.get_run(run_id)
    if result is None or result.decision is None:
        st.info("This run has no stored record.")
        return
    C.render_run(result, store, key=f"dash_{run_id}")
