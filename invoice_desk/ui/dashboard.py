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
    """Six figures an AP manager asks for. A file that was sent twice is one invoice, so
    re-submissions are counted apart and never add to a rupee figure."""
    received = [r for r in runs if not r.get("resubmission_of")]
    again = len(runs) - len(received)
    total = len(received)
    approved = [r for r in received if r["verdict"] == "APPROVE"]
    held = [r for r in received if r["review_status"] == "PENDING"]
    rejected = [r for r in received if r["verdict"] == "REJECT"]
    released = approved + [r for r in received if r["review_status"] == "APPROVED"]      # rupee invoices only
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
                  f"{len(released)} invoice(s), including approved on review", **plain)
    typical = C.took(statistics.median(text_times)) if text_times else "-"
    row[2].metric("Typical handling time", typical if text_times else (C.took(statistics.median(scan_times)) if scan_times else "-"),
                  (f"scanned pages: {C.took(statistics.median(scan_times))} (OCR)" if scan_times and text_times
                   else "from file received to decision recorded"), **plain)


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
        st.success(f"Tamper-evident audit trail: intact. All {check['count']} entries were re-checked just now.",
                   icon=":material/verified_user:")
    else:
        st.error("Tamper-evident audit trail: BROKEN. " + "; ".join(check["problems"][:5]), icon=":material/gpp_bad:")
    with st.expander("What this check proves, and what it does not"):
        from ..store import signing_enabled
        head = store.chain_head()
        st.markdown(
            "- Every decision by the system and every decision by a reviewer is written once as an entry. Each entry "
            "carries a fingerprint (SHA-256) of its own content and of the entry before it.\n"
            "- This check recomputes every fingerprint and every link. Editing or removing any past entry makes it fail.\n"
            "- What it does not prove on its own: someone with full access to the database could rewrite the whole "
            "trail from the start. Two things close that gap. Keep the latest fingerprint somewhere this system "
            "cannot write to (an email to audit, a note in the ERP); and set RECEIPT_SIGNING_KEY, which signs each "
            "entry with a key the database does not hold.")
        st.caption(f"Latest entry fingerprint: {head}  |  entries: {check['count']}  |  "
                   f"signing: {'on' if signing_enabled() else 'off (RECEIPT_SIGNING_KEY not set)'}")


def page() -> None:
    store = C.get_store()
    rules = C.get_rules()
    st.title("Dashboard")
    st.caption("What was received, how it was decided and where it stands. Everything here is read from the stored records.")
    runs = store.list_runs()
    if not runs:
        st.markdown(C.neutral_banner_html("Nothing received yet", "No document has been processed in these books",
                                          "Run the Guided demo, process one invoice, or process the whole sample inbox now."),
                    unsafe_allow_html=True)
        if st.button(f"Process all {len(C.manifest())} samples now", type="primary", key="dash_batch"):
            st.session_state["goto"], st.session_state["auto_batch"] = "process", True
            st.rerun()
        return

    _kpis(runs)
    _charts(runs, rules)
    _chain(store)

    st.subheader("History")
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
                          format_func=lambda i: next(f"{r['run_id']}  {r['verdict']}  {C.display_name(r['file_name'])}"
                                                     for r in shown if r["run_id"] == i))
    st.session_state["dash_run"] = run_id
    result = store.get_run(run_id)
    if result is None or result.decision is None:
        st.info("This reference has no stored record.")
        return
    C.render_run(result, store, key=f"dash_{run_id}")
