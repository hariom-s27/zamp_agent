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
from ..pipeline import Pipeline
from ..store import Store
from . import common as C


ACTION_WORDS = {"REJECT": "\u2715 Rejected", "HOLD": "\u23f8 On hold", "NOTE": "Noted, no effect"}


def _decision_table(rules) -> None:
    st.write("Every reason the desk can give has one row here. The strictest wins: a rejection, then a hold, "
             "otherwise the invoice is cleared. A reason the rule book does not list is held for a person.")
    action = st.pills("Show", ["All", "REJECT", "HOLD", "NOTE"], default="All", key="ref_action",
                      format_func=lambda a: {"All": "All", "REJECT": "Rejected", "HOLD": "On hold",
                                             "NOTE": "Noted"}[a]) or "All"
    rows = [{"Reason": r.title, "What the desk does": ACTION_WORDS.get(r.action, r.action),
             "Waiting on": r.owner or "-"} for code, r in rules.reasons.items() if action in ("All", r.action)]
    C.table(rows, height=min(36 * len(rows) + 40, 640))
    with st.expander("Reason codes (for auditors)"):
        C.table([{"Reason code": code, "Reason": r.title, "Action": ACTION_WORDS.get(r.action, r.action)}
                 for code, r in rules.reasons.items()], height=min(36 * len(rules.reasons) + 40, 640))
    st.caption(f"{len(rules.reasons)} reasons in rule book version {rules.version}. Changing a rule changes no "
               f"program code, and the rule book's fingerprint is written into every audit entry.")


def _limits(rules) -> None:
    """The rules as sentences first; the file behind them on request."""
    t = rules.tolerances
    lines = [
        ("Price", f"may differ from the PO by up to {T.fmt_num(t.unit_price.pct)}% and "
                  f"{T.fmt_inr(t.unit_price.abs_inr, decimals=0)} a line. Both limits must hold."),
        ("Quantity", f"may not exceed what was ordered by more than {T.fmt_num(t.quantity_over_pct)}%, nor what "
                     f"Stores received."),
        ("PO value", f"the running total may exceed the PO by up to {T.fmt_num(t.po_total.pct)}% and "
                     f"{T.fmt_inr(t.po_total.abs_inr, decimals=0)}."),
        ("Clearing alone", f"an invoice above {T.fmt_inr(rules.auto_approve.max_invoice_total_inr, decimals=0)} "
                           f"always goes to a person."),
        ("Bank change", "needs a call-back to the number on file and two different people."),
        ("Duplicates", f"the same lines and total within {rules.duplicates.window_days} days is a duplicate unless "
                       f"a second delivery is on record."),
        ("Rounding", f"up to {T.fmt_inr(t.rounding_inr)} is allowed in the invoice arithmetic."),
    ]
    st.markdown("\n".join(f"- **{name}** {text}" for name, text in lines))
    st.download_button("Download the rule book (YAML)", data=config.RULES_PATH.read_bytes(), file_name="rules.yaml",
                       mime="text/yaml", key="ref_yaml", icon=":material/download:", on_click="ignore")
    st.caption(f"Rule book fingerprint {rules.sha256[:16]}. Every decision records which version it used.")


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


def _dry_run(sample: dict, rules) -> tuple[str, list[str]]:
    """Process one sample against a scratch copy of the seeded books. Nothing is kept."""
    folder = Path(tempfile.mkdtemp(prefix="invoice_desk_whatif_"))
    try:
        pipeline = Pipeline(store=Store(folder / "whatif.db"), rules=rules, master=C.get_master(), llm=C.get_llm(),
                            today=C.business_date())
        result = pipeline.run(sample_lib.sample_bytes(sample), sample_lib.received_name(sample), "what-if")
        return result.verdict, [f"{f.title}: {C.screen_words(f.summary)}" for f in result.decision.reasons] or \
            [C.screen_words(result.decision.explanation)]
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _replay_all(rules, trial) -> list[tuple[str, str, str]]:
    """Every sample in order on two fresh copies of the starting books: (title, now, with the change)."""
    samples = [s for s in C.manifest() if s["group"] != "bad_input"]
    out: dict[str, list[str]] = {}
    with st.status(f"Replaying {len(samples)} samples", expanded=False) as box:
        for name, used in (("now", rules), ("change", trial)):
            folder = Path(tempfile.mkdtemp(prefix="invoice_desk_whatif_"))
            try:
                pipeline = Pipeline(store=Store(folder / "whatif.db"), rules=used, master=C.get_master(),
                                    llm=C.get_llm(), today=C.business_date())
                for i, sample in enumerate(samples, 1):
                    box.update(label=f"Replaying {len(samples)} samples: invoice {i} of {len(samples)}")
                    result = pipeline.run(sample_lib.sample_bytes(sample), sample_lib.received_name(sample), "what-if")
                    out.setdefault(sample["title"], []).append(result.verdict)
            finally:
                shutil.rmtree(folder, ignore_errors=True)
        box.update(label="Done", state="complete")
    return [(title, v[0], v[1]) for title, v in out.items()]


def _sandbox(rules) -> None:
    st.write("Change a number and see what would have happened. The chosen sample is processed twice on a copy of "
             "the starting books: once with the rule book as it is and once with your numbers. Nothing is saved "
             "from here.")
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
    st.caption("Ideas: the invoice just inside tolerance (0.95% above the PO price) with the price limit at 0.90%; "
               "the one two rupees dearer (1.01%, ₹515 on the line) with the limit at 1.10% and ₹550; the clean "
               "invoice with the ceiling at ₹4,00,000.")
    trial = rules.model_copy(deep=True)
    trial.tolerances.unit_price.pct, trial.tolerances.unit_price.abs_inr = Decimal(str(price_pct)), Decimal(price_abs)
    trial.tolerances.po_total.pct, trial.tolerances.po_total.abs_inr = Decimal(str(po_pct)), Decimal(po_abs)
    trial.auto_approve.max_invoice_total_inr = Decimal(int(ceiling))
    trial.duplicates.window_days = int(window)
    buttons = st.container(horizontal=True)
    every = buttons.button("Replay all samples", key="wi_all", icon=":material/replay:")
    if buttons.button("Run this sample both ways", type="primary", key="wi_go"):
        with st.spinner("Processing the sample twice"):
            st.session_state["wi_result"] = {"sample": chosen, "base": _dry_run(by_id[chosen], rules),
                                             "trial": _dry_run(by_id[chosen], trial)}
    if every:
        changed = [(t, a, b) for t, a, b in _replay_all(rules, trial) if a != b]
        total = len([s for s in C.manifest() if s["group"] != "bad_input"])
        if not changed:
            st.info("Nothing would change with these limits.")
        else:
            st.info(f"{len(changed)} of {total} samples would change: " + "; ".join(
                f"\u201c{t}\u201d goes from {C.verdict_display_label(a)} to {C.verdict_display_label(b)}"
                for t, a, b in changed) + ".")
        st.caption("Nothing is saved from here. This shows what would have happened.")
    outcome = st.session_state.get("wi_result")
    if outcome and outcome["sample"] == chosen:
        left, right = st.columns(2, gap="large")
        for column, title, (verdict, reasons) in ((left, "With the rule book as it is", outcome["base"]),
                                                   (right, "With the numbers above", outcome["trial"])):
            label, colour, background, border = C.VERDICT[verdict]
            column.markdown(
                f"<div class='idk-banner' style='color:{colour};background:{background};border-color:{border}'>"
                f"<div class='tag'>{C.esc(title)}</div><div class='headline'>{C.esc(label)}</div>"
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
             "Taxable value": C.money(e["taxable"]), "Status": LEDGER_WORDS.get(e["status"], e["status"].lower()),
             "From": "prior history" if e["origin"] == "history" else e["run_id"]} for e in usage["invoices"])


LEDGER_WORDS = {"POSTED": "posted for payment", "PENDING": "waiting for review", "REJECTED": "rejected"}


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
             "Status": LEDGER_WORDS.get(e["status"], e["status"].lower()),
             "From": "prior history" if e["origin"] == "history" else e["run_id"],
             "Note": C.screen_words(e["note"] or "")} for e in reversed(entries))
    st.caption("Posted entries count against PO balances and are payable. Entries waiting for review do not use PO "
               "balance. Rejected entries are kept for the record and never paid.")


def page() -> None:
    store, rules, master = C.get_store(), C.get_rules(), C.get_master()
    head = st.container(horizontal=True, vertical_alignment="center")
    head.title("Rules and data")
    head.badge(f"Rule book {rules.version}", icon=":material/rule:", color="gray")
    st.caption("Every number the desk uses is here. Nothing is hidden in code.")
    tabs = st.tabs(["Limits", f"Reasons ({len(rules.reasons)})", "Vendors and POs", "Try a change", "Ledger"])
    with tabs[0]:
        _limits(rules)
        with st.expander("All limits as figures"):
            _policy(rules)
    with tabs[1]:
        _decision_table(rules)
    with tabs[2]:
        st.markdown("**Purchase orders**")
        _purchase_orders(store, master)
        st.markdown("**Vendors**")
        _vendors(store, master)
    with tabs[3]:
        _sandbox(rules)
    with tabs[4]:
        _ledger(store, master)
