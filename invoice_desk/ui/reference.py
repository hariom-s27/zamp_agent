"""Page 4, Rules and data: the decision table, the tolerances, a what-if sandbox, and the
master data and ledger the pipeline works against."""
from __future__ import annotations

import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

import streamlit as st

from .. import config
from .. import samples as sample_lib
from .. import textutil as T
from ..llm import LlmAssist
from ..pipeline import Pipeline
from ..store import Store
from . import common as C


def _decision_table(rules) -> None:
    st.write("Every finding a stage can raise has one row here. The decision stage looks the code up and the "
             "strictest action wins: REJECT, then HOLD, otherwise APPROVE. A code that is not in the table is held.")
    action = st.radio("Show", ["All", "REJECT", "HOLD", "NOTE"], horizontal=True, key="ref_action")
    rows = [{"Code": code, "Meaning": r.title, "Action": r.action, "Owner": r.owner or "-"}
            for code, r in rules.reasons.items() if action in ("All", r.action)]
    C.table(rows, height=min(36 * len(rows) + 40, 640))
    st.caption(f"{len(rules.reasons)} codes in rules.yaml version {rules.version}. To change policy, edit the file: "
               f"no code changes, and the file's hash is written into every receipt.")


def _policy(rules) -> None:
    t = rules.tolerances
    cols = st.columns(4)
    cols[0].metric("Unit price tolerance", f"{T.fmt_num(t.unit_price.pct)}% and {C.money(t.unit_price.abs_inr)}",
                   "per line, both must hold", delta_color="off", border=True)
    cols[1].metric("Quantity over PO", f"{T.fmt_num(t.quantity_over_pct)}%", "no over-billing", delta_color="off", border=True)
    cols[2].metric("PO value tolerance", f"{T.fmt_num(t.po_total.pct)}% and {C.money(t.po_total.abs_inr)}",
                   "running total, both must hold", delta_color="off", border=True)
    cols[3].metric("Auto-approval ceiling", C.money(rules.auto_approve.max_invoice_total_inr),
                   "above this a person signs", delta_color="off", border=True)
    cols = st.columns(4)
    cols[0].metric("Rounding allowed", C.money(t.rounding_inr), "in invoice arithmetic", delta_color="off", border=True)
    cols[1].metric("Duplicate window", f"{rules.duplicates.window_days} days", "same content, new number",
                   delta_color="off", border=True)
    cols[2].metric("PO inference", f"{rules.po_inference.min_confidence:.0%} and {rules.po_inference.min_margin:.0%} clear",
                   "and never auto-accepted", delta_color="off", border=True)
    cols[3].metric("GST slabs accepted", ", ".join(T.fmt_num(r) for r in rules.gst.rates_current) + "%",
                   "since 22-Sep-2025", delta_color="off", border=True)
    with st.expander("rules.yaml as loaded"):
        st.caption(f"File hash {rules.sha256}")
        st.code(config.RULES_PATH.read_text(encoding="utf-8"), language="yaml")


def _dry_run(sample: dict, rules) -> tuple[str, list[str]]:
    """Process one sample against a scratch copy of the seeded books. Nothing is kept."""
    folder = Path(tempfile.mkdtemp(prefix="invoice_desk_whatif_"))
    try:
        pipeline = Pipeline(store=Store(folder / "whatif.db"), rules=rules, master=C.get_master(), llm=LlmAssist())
        result = pipeline.run(sample_lib.sample_bytes(sample), Path(sample["file"]).name, "what-if")
        return result.verdict, [f"{f.title}: {f.summary}" for f in result.decision.reasons] or \
            [result.decision.explanation]
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _sandbox(rules) -> None:
    st.write("Policy is data, not code. Change a number below and the chosen sample is processed twice on a scratch "
             "copy of the seeded books: once with rules.yaml as it is and once with your numbers. Nothing here "
             "touches the real history or the rules file.")
    samples = [s for s in C.manifest() if s["group"] != "bad_input"]
    by_id = {s["id"]: s for s in samples}
    default = next((i for i, s in enumerate(samples) if s["id"].startswith("07")), 0)
    chosen = st.selectbox("Sample", list(by_id), index=default, key="wi_sample",
                          format_func=lambda i: f"{by_id[i]['order']:02d}  {by_id[i]['title']}")
    st.caption(by_id[chosen]["description"])
    t = rules.tolerances
    c1, c2, c3 = st.columns(3)
    price_pct = c1.slider("Unit price tolerance, %", 0.0, 5.0, float(t.unit_price.pct), 0.05, key="wi_price_pct")
    price_abs = c1.slider("Unit price tolerance, rupees per line", 0, 5000, int(t.unit_price.abs_inr), 50, key="wi_price_abs")
    po_pct = c2.slider("PO value tolerance, %", 0.0, 5.0, float(t.po_total.pct), 0.05, key="wi_po_pct")
    po_abs = c2.slider("PO value tolerance, rupees", 0, 5000, int(t.po_total.abs_inr), 50, key="wi_po_abs")
    ceiling = c3.number_input("Auto-approval ceiling, rupees", min_value=1000, max_value=100000000,
                              value=int(rules.auto_approve.max_invoice_total_inr), step=50000, key="wi_ceiling")
    window = c3.slider("Duplicate window, days", 0, 60, int(rules.duplicates.window_days), 1, key="wi_window")
    st.caption("Ideas: sample 07 with the price tolerance at 0.10%; sample 01 with the ceiling at 4,00,000; "
               "sample 08 with both price limits and both PO limits raised to 2% and 2,000.")
    if st.button("Run both ways", type="primary", key="wi_go"):
        trial = rules.model_copy(deep=True)
        trial.tolerances.unit_price.pct, trial.tolerances.unit_price.abs_inr = Decimal(str(price_pct)), Decimal(price_abs)
        trial.tolerances.po_total.pct, trial.tolerances.po_total.abs_inr = Decimal(str(po_pct)), Decimal(po_abs)
        trial.auto_approve.max_invoice_total_inr = Decimal(int(ceiling))
        trial.duplicates.window_days = int(window)
        with st.spinner("Processing the sample twice"):
            st.session_state["wi_result"] = {"sample": chosen, "base": _dry_run(by_id[chosen], rules),
                                             "trial": _dry_run(by_id[chosen], trial)}
    outcome = st.session_state.get("wi_result")
    if outcome and outcome["sample"] == chosen:
        left, right = st.columns(2, gap="large")
        for column, title, (verdict, reasons) in ((left, "With rules.yaml as it is", outcome["base"]),
                                                   (right, "With the numbers above", outcome["trial"])):
            label, colour, background, border = C.VERDICT[verdict]
            column.markdown(
                f"<div class='idk-banner' style='color:{colour};background:{background};border-color:{border}'>"
                f"<div class='tag'>{C.esc(title)}</div><div class='headline'>{C.esc(verdict)}</div>"
                f"<div class='why'>{'<br>'.join(C.esc(r) for r in reasons)}</div></div>", unsafe_allow_html=True)
        if outcome["base"][0] != outcome["trial"][0]:
            st.info("The decision changed, and the only thing that changed was a number in the rules.")
        else:
            st.caption("Same decision both ways.")


def _purchase_orders(store, master) -> None:
    rows = []
    for po in master.pos.values():
        usage = store.po_usage(po.po_number)
        vendor = master.vendors.get(po.vendor_id)
        rows.append({"PO": po.po_number, "Date": T.fmt_date(po.po_date), "Vendor": vendor.legal_name if vendor else po.vendor_id,
                     "Status": po.status, "PO value": C.money(po.total), "Billed (posted)": C.money(usage["posted_amount"]),
                     "Waiting for review": C.money(usage["pending_amount"]),
                     "Still open": C.money(po.total - usage["posted_amount"]), "Owner": po.buyer})
    C.table(rows)
    chosen = st.selectbox("Open a PO", list(master.pos), key="ref_po")
    po = master.pos[chosen]
    usage = store.po_usage(chosen)
    st.markdown("**Lines**")
    C.table({"Line": ln.line_no, "Item": ln.description, "Unit": ln.uom, "Ordered": T.fmt_num(ln.qty_ordered),
             "Received": T.fmt_num(ln.qty_received) if ln.qty_received is not None else "not tracked",
             "Billed": T.fmt_num(usage["posted"].get(ln.line_no, {}).get("qty", 0)) if ln.match_basis == "QTY"
             else C.money(usage["posted"].get(ln.line_no, {}).get("amount", 0)),
             "PO price": C.money(ln.unit_price), "Line value": C.money(ln.line_amount),
             "Matched on": "quantity and price" if ln.match_basis == "QTY" else "amount (service)"} for ln in po.lines)
    st.markdown("**Invoices against this PO**")
    C.table({"Invoice": e["invoice_no"] or "(no number)", "Date": T.fmt_date(e["invoice_date"]),
             "Taxable value": C.money(e["taxable"]), "Status": e["status"],
             "From": "prior history" if e["origin"] == "history" else f"run {e['run_id']}"} for e in usage["invoices"])


def _vendors(store, master) -> None:
    learned: dict[str, list[str]] = {}
    for o in store.overrides(kind="bank_account"):
        learned.setdefault(o["vendor_id"], []).append(f"{o['value']} (verified in {o['source_run']})")
    C.table({"ID": v.vendor_id, "Legal name": v.legal_name, "Also known as": ", ".join(v.aliases) or "-",
             "GSTIN": v.gstin, "Status": v.status, "Bank account": f"{v.bank_account} / {v.ifsc}" if v.bank_account else "-",
             "Accounts verified on review": "; ".join(learned.get(v.vendor_id, [])) or "-",
             "Terms": f"{v.payment_terms_days} days", "Phone on file": v.phone_on_file or "-"}
            for v in master.vendors.values())
    st.caption("The vendor master is the source of truth for identity (GSTIN) and for where money goes. An invoice "
               "never updates it: a changed bank account is held until a person confirms it by calling the number on file.")


def _ledger(store, master) -> None:
    entries = store.ledger_entries()
    C.table({"Ledger ID": e["ledger_id"], "Invoice": e["invoice_no"] or "(no number)", "Date": T.fmt_date(e["invoice_date"]),
             "Vendor": master.vendors[e["vendor_id"]].legal_name if e["vendor_id"] in master.vendors else (e["vendor_id"] or "-"),
             "PO": e["po_number"] or "-", "Taxable value": C.money(e["taxable"]), "Total": C.money(e["total"]),
             "Status": e["status"], "From": "prior history" if e["origin"] == "history" else f"run {e['run_id']}",
             "Note": e["note"] or ""} for e in reversed(entries))
    st.caption("POSTED entries count against PO balances and are payable. PENDING entries wait in the review queue "
               "and do not use PO balance. REJECTED entries are kept for the record and never paid.")


def page() -> None:
    store, rules, master = C.get_store(), C.get_rules(), C.get_master()
    st.title("Rules and data")
    st.caption("What the pipeline decides with, and what it checks invoices against.")
    tabs = st.tabs(["Decision table", "Tolerances and policy", "What-if sandbox", "Purchase orders", "Vendors", "Ledger"])
    with tabs[0]:
        _decision_table(rules)
    with tabs[1]:
        _policy(rules)
    with tabs[2]:
        _sandbox(rules)
    with tabs[3]:
        _purchase_orders(store, master)
    with tabs[4]:
        _vendors(store, master)
    with tabs[5]:
        _ledger(store, master)
