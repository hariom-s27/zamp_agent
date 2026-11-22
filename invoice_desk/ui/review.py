"""Page 3, Review queue: invoices on hold wait here for a named person.

Approving or rejecting writes a review receipt into the same hash chain as the machine
decisions. Two approvals carry an extra control (see invoice_desk/review.py).
"""
from __future__ import annotations

import streamlit as st

from .. import config
from ..review import ReviewError, apply_review, required_checks
from . import common as C


def _pending_card(row: dict, store) -> None:
    run_id = row["run_id"]
    result = store.get_run(run_id)
    if result is None or result.decision is None:
        return
    d = result.decision
    codes = {f.code for f in d.reasons}
    with st.container(border=True):
        st.markdown(C.verdict_banner_html(result), unsafe_allow_html=True)
        C.render_findings(d.reasons, with_summary=False)
        if st.toggle("Show the evidence: all nine stages and the document", key=f"rev_show_{run_id}"):
            left, right = st.columns([0.58, 0.42], gap="large")
            with left:
                C.render_stages(result)
            with right:
                C.render_preview(store.load_file(result.file_sha256), result.file_sha256, result, key=f"rev_pv_{run_id}")
        needs = required_checks(codes)
        with st.form(key=f"rev_form_{run_id}", border=False):
            c1, c2 = st.columns([0.35, 0.65])
            reviewer = c1.text_input("Your name (recorded with the decision)", value=st.session_state.get("reviewer", ""),
                                     key=f"rev_name_{run_id}")
            note = c2.text_input("Note for the record", key=f"rev_note_{run_id}",
                                 placeholder="What you checked, who confirmed it")
            checks: dict = {}
            if "bank_verified" in needs:
                checks["bank_verified"] = st.checkbox(needs["bank_verified"], key=f"rev_bank_{run_id}")
            if "invoice_number" in needs:
                checks["invoice_number"] = st.text_input(needs["invoice_number"], key=f"rev_number_{run_id}")
            booked = bool(result.ledger_id)
            if not booked:
                st.caption("The system did not book this document, so there is nothing to approve. Process it by "
                           "hand, then close it here.")
            b1, b2, _ = st.columns([0.24, 0.2, 0.56])
            approve = b1.form_submit_button("Approve for payment" if booked else "Mark as handled by hand",
                                            type="primary", width="stretch")
            reject = b2.form_submit_button("Reject", width="stretch")
        if approve or reject:
            decision = "REJECT" if reject else ("APPROVE" if booked else "HANDLED")
            try:
                receipt = apply_review(store, run_id, decision, reviewer, note, checks)
            except ReviewError as exc:
                st.error(str(exc))
            else:
                st.session_state["reviewer"] = reviewer.strip()
                learned = (" " + "; ".join(receipt["learned"]).capitalize() + ".") if receipt.get("learned") else ""
                done = {"APPROVE": "approved", "REJECT": "rejected", "HANDLED": "marked as handled by hand"}[decision]
                st.session_state["flash_review"] = (f"{run_id} {done} by {reviewer.strip()}. Review receipt "
                                                    f"{receipt['hash'][:12]} added to the audit trail.{learned}")
                st.rerun()


def page() -> None:
    store = C.get_store()
    st.title("Review queue")
    st.caption("A hold is a question for a named owner. The decision made here is recorded against a person, in the "
               "same audit trail as the machine's decisions.")
    flash = st.session_state.pop("flash_review", None)
    if flash:
        st.success(flash)
    runs = store.list_runs()
    pending = [r for r in reversed(runs) if r["review_status"] == "PENDING"]       # oldest first
    if not pending:
        st.markdown(C.neutral_banner_html("Queue empty", "Nothing is waiting for a person",
                                          "Invoices that are put on hold appear here with the question that has to be "
                                          "answered."), unsafe_allow_html=True)
    else:
        st.markdown(f"**{len(pending)} invoice(s) waiting**, oldest first.")
        for row in pending:
            _pending_card(row, store)

    reviews = store.reviews()
    if reviews:
        st.subheader("Decided on review")
        by_run = {r["run_id"]: r for r in runs}
        C.table({"Run": r["run_id"], "Vendor": by_run.get(r["run_id"], {}).get("vendor_name", "-"),
                 "Invoice no.": by_run.get(r["run_id"], {}).get("invoice_no", "-") or "-",
                 "Total": C.money(by_run.get(r["run_id"], {}).get("total")) if by_run.get(r["run_id"], {}).get("total") else "-",
                 "Decision": r["decision"], "Reviewer": r["reviewer"], "Note": r["note"] or "-",
                 "When": config.to_ist_text(r["decided_at"])} for r in reversed(reviews))
