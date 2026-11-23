"""Page, Payments (round 4): the payment-run proposal. The desk prepares it, a person with
release authority approves it, and nothing is paid from here (invoice_desk/payments.py)."""
from __future__ import annotations

from decimal import Decimal
from datetime import date

import streamlit as st

from .. import cases as case_engine
from .. import payments as pay
from .. import samples
from . import common as C


def load_payment_example(store, master, rules, pipeline, today: date) -> None:
    by_id = {s["id"]: s for s in samples.load_manifest() + samples.load_case_manifest() + samples.load_desk_manifest()}
    order = ["p1_micro_supplier_due", "02_scanned_clean_safety", "07_E4a_tax_inclusive_within_tolerance",
             "03_monthly_service_instalment", "10_ai_assist_letter_invoice", "01_happy_path_furniture",
             "09_fraud_guard_bank_change", "c2_small_supplier_late_invoice"]
    for sid in order:
        s = by_id[sid]
        pipeline.run(samples.sample_bytes(s), samples.received_name(s), source="cli")
    case_engine.sync(store, master, rules, today)


def _rows(lines: list[dict], with_run: bool = False) -> list[dict]:
    rows = []
    for ln in lines:
        why = " ".join([ln["why"]] + ln.get("flags", []))
        row = {"Vendor": ln["vendor"], "Invoice": ln["invoice_no"] or "-",
               "Amount to pay": C.money(ln["amount"]) if ln["amount"] else "-",
               "Pay by": case_engine.day_text(ln["pay_by"]), "Why": why}
        if with_run and ln.get("falls_into"):
            row["Run"] = case_engine.day_text(ln["falls_into"])
        rows.append(row)
    return rows


def _flash() -> None:
    flash = st.session_state.pop("flash_pay", None)
    if flash:
        kind, text = flash
        {"success": st.success, "info": st.info, "error": st.error}[kind](text)


def _approve_form(proposal: dict, rules, store, run_date, cash, removed) -> None:
    first = st.session_state.get("pay_first")
    if first and first.get("fingerprint") != proposal["fingerprint"]:
        st.session_state.pop("pay_first", None)
        first = None
    with st.form(key="pay_approve_form"):
        st.markdown("**Approve the run**")
        if proposal["needs_second"]:
            st.caption(f"This run is above {C.money(rules.payments.second_approver_above_inr)}. A second, different "
                       f"person must approve.")
        if first:
            st.info(f"First approval: {first['name']}. A second, different person approves now.")
        name = st.text_input("Your name", key="pay_name_2" if first else "pay_name")
        authority = st.checkbox("I have release authority for this run", key="pay_auth_2" if first else "pay_auth")
        go = st.form_submit_button("Approve the run", type="primary")
    if not go:
        return
    approvers = ([first["name"]] if first else []) + [name]
    try:
        if proposal["needs_second"] and not first:
            if not name.strip() or not authority:
                pay.approve(store, C.get_master(), rules, run_date, proposal["fingerprint"], [name], authority,
                            C.business_date(), cash, removed)          # raises with the reason
            st.session_state["pay_first"] = {"name": name.strip(), "fingerprint": proposal["fingerprint"]}
            st.rerun()
        done = pay.approve(store, C.get_master(), rules, run_date, proposal["fingerprint"], approvers, authority,
                           C.business_date(), cash, removed)
        st.session_state.pop("pay_first", None)
        st.session_state["pay_removed"] = {}
        st.session_state["pay_last"] = {"run_id": done["run_id"], "path": str(done["path"])}
        st.session_state["flash_pay"] = ("success", done["message"])
        st.rerun()
    except pay.PaymentError as exc:
        st.error(str(exc))


def page() -> None:
    store = C.get_store()
    rules = C.get_rules()
    st.title("Payments")
    st.caption(pay.CAPTION)
    if not rules.payments.enabled:
        st.info(pay.OFF)
        return
    _flash()
    left, right = st.columns(2)
    run_date = left.date_input("Run date", value=C.business_date(), key="pay_date")
    cash_text = right.text_input("Cash available for this run (optional, in rupees)", key="pay_cash")
    cash = None
    if cash_text.strip():
        try:
            cash = Decimal(cash_text.replace(",", "").strip())
        except Exception:
            st.error("The cash available must be a number.")
    removed = st.session_state.setdefault("pay_removed", {})
    proposal = pay.propose(store, C.get_master(), rules, run_date, cash, removed)
    runs = ", ".join(case_engine.day_text(d) for d in proposal["next_runs"])
    C.text_block(f"Proposal for {case_engine.day_text(run_date)}. It covers what must be paid by "
                 f"{case_engine.day_text(proposal['end'])}. Next run days: {runs}.")
    for warning in proposal["warnings"]:
        st.error(warning)
    if not proposal["pay"]:
        st.info("Nothing is payable right now: no approved invoices are due for payment on this date. "
                "Process invoices or load the payment-run example to see legal-date priority, early discounts, "
                "blocked vendors and held small-supplier cases.")
        if st.button("Load the payment-run example", key="pay_load_example", type="primary", icon=":material/play_circle:"):
            load_payment_example(store, C.get_master(), rules, C.get_pipeline(store=store), C.business_date())
            st.session_state["flash_pay"] = ("success", "Payment-run example loaded (P1, 02, 07, 03, 10, 01, 09 and C2).")
            st.rerun()
    st.subheader(pay.GROUPS["pay"])
    C.table(_rows(proposal["pay"]), key="pay_tbl_pay")
    C.text_block(f"Total to pay: {C.money(proposal['total'])} across {proposal['count']} invoices to "
                 f"{proposal['vendors']} vendors.")
    for key in ("deferred", "removed", "not_due", "blocked", "on_hold"):
        if proposal[key]:
            st.subheader(pay.GROUPS[key])
            C.table(_rows(proposal[key], with_run=key == "not_due"), key=f"pay_tbl_{key}")
    with st.expander("What this proposal does not do", expanded=False):
        C.text_block(pay.DOES_NOT)
    if proposal["pay"]:
        with st.form(key="pay_remove_form"):
            st.markdown("**Take a line out of this run**")
            labels = {f"{ln['vendor']}, {ln['invoice_no']}": ln["ledger_id"] for ln in proposal["pay"] + proposal["deferred"]}
            pick = st.selectbox("Line", list(labels), key="pay_rm_line")
            who = st.text_input("Your name", key="pay_rm_name")
            why = st.text_input("Reason", key="pay_rm_reason")
            if st.form_submit_button("Take it out"):
                try:
                    pay.remove_line(proposal, removed, labels[pick], who, why)
                    st.rerun()
                except pay.PaymentError as exc:
                    st.error(str(exc))
        _approve_form(proposal, rules, store, run_date, cash, removed)
    last = st.session_state.get("pay_last")
    if last:
        from pathlib import Path
        path = Path(last["path"])
        if path.is_file():
            st.download_button("Download the proposal (CSV)", data=path.read_bytes(), file_name=path.name,
                               mime="text/csv", key="pay_download")
    approved = [r for r in store.payment_runs()]
    if approved:
        st.subheader("Approved runs")
        C.table({"Run": r["run_id"], "Run date": r["run_date"], "Total": C.money(r["total"]),
                 "Invoices": r["line_count"], "Approved by": " and ".join(filter(None, [r["approved_by"],
                                                                                      r["second_approved_by"]])),
                 "Status": "approved, nothing paid" if r["status"] == "APPROVED" else
                 f"cancelled by {r['cancelled_by']}: {r['cancel_reason']}"} for r in approved)
        open_runs = [r["run_id"] for r in approved if r["status"] == "APPROVED"]
        if open_runs:
            with st.form(key="pay_cancel_form"):
                st.markdown("**Cancel an approved run**")
                rid = st.selectbox("Run", open_runs, key="pay_cancel_run")
                who = st.text_input("Your name", key="pay_cancel_name")
                why = st.text_input("Reason", key="pay_cancel_reason")
                if st.form_submit_button("Cancel the run"):
                    try:
                        pay.cancel(store, rid, who, why, C.business_date())
                        st.session_state["flash_pay"] = ("info", f"Run {rid} cancelled. Its lines are proposed again.")
                        st.rerun()
                    except pay.PaymentError as exc:
                        st.error(str(exc))
