"""Page 3, Review queue: invoices on hold wait here for a named person.

A hold is only as strong as what happens when someone clears it, so three controls sit on
an approval and each is visible on this page before the button is pressed:

  * the purchase order as it stands right now (other invoices may have been approved
    since this one was held); an approval that would overbill it is refused;
  * a changed bank account takes two different people: the first records the call-back,
    the second confirms it;
  * an invoice with no number needs the number from the vendor.

Every decision made here joins the same audit trail as the machine's decisions.
"""
from __future__ import annotations

import streamlit as st

from .. import config, replies
from .. import textutil as T
from ..review import ReviewError, apply_review, po_position, required_checks, review_state
from . import common as C
from . import ui_kit as K
from .process import amount_text, plain


def _po_now(result, store) -> None:
    """The purchase order as it stands at this moment, before anyone approves."""
    entry = store.get_ledger(result.ledger_id) if result.ledger_id else None
    if not entry or not entry.get("po_number"):
        return
    position = po_position(store, C.get_master(), C.get_rules(), entry)
    if not position["rows"]:
        return
    st.markdown(f"<div class='idk-small'><b>{C.esc(position['po_number'])} as it stands now</b> (checked again when "
                f"you approve; invoices approved since this one was held are counted)</div>", unsafe_allow_html=True)
    C.table(position["rows"])
    if position["problems"]:
        st.warning("Approving now would overbill the purchase order: " + "; ".join(p["text"] for p in position["problems"]) +
                   ". An approval will be refused. Have Procurement amend the PO, or reject the invoice.")


def _approving(result, row: dict, bank: bool, booked: bool) -> str:
    """Exactly what the signature does, in one sentence: nobody approves what they cannot see."""
    amount = amount_text(row.get("total")) or "the invoice total"
    vendor = plain(C.vendor_case(row.get("vendor_name")) or "this vendor")
    if not booked:
        return "Closing this document as processed by hand. Nothing is paid from this screen."
    if bank:
        account = (result.invoice.get("bank_account") if result.invoice else None) or ""
        ending = f" ending {plain(str(account)[-4:])}" if account else ""
        return (f"Change of bank account for {vendor}: from the account on file to the account{ending} on this "
                f"invoice, and payment of {amount} to it.")
    return f"Payment of {amount} to {vendor} for invoice {plain(row.get('invoice_no') or '(number missing)')}."


def _pending_card(row: dict, store) -> None:
    run_id = row["run_id"]
    result = store.get_run(run_id)
    if result is None or result.decision is None:
        return
    d = result.decision
    codes = {f.code for f in d.reasons}
    first = review_state(store, run_id)["first_approval"]
    bank = "BANK_DETAILS_CHANGED" in codes
    booked = bool(result.ledger_id)
    with st.container(border=True):
        top = st.container(horizontal=True, vertical_alignment="center")
        top.markdown(f"**{plain(C.vendor_case(row.get('vendor_name')) or 'Unknown vendor')}** · "
                     f"{'invoice ' + plain(row.get('invoice_no')) if row.get('invoice_no') else 'invoice (number missing)'}"
                     f"{' · ' + amount_text(row.get('total')) if row.get('total') else ''}")
        if bank:
            top.markdown(K.chip_md("due_soon", "Needs two people"))
        st.markdown(C.verdict_banner_html(result), unsafe_allow_html=True)
        st.markdown("**You are approving**")
        st.markdown(_approving(result, row, bank, booked))
        st.markdown("**What the desk found**")
        C.render_findings(d.reasons, with_summary=len(d.reasons) > 1, codes=True)
        if bank:
            st.markdown(f"First approval: {plain(first['reviewer']) + ' · ' + config.to_ist_text(first['decided_at']) if first else 'waiting'}  \n"
                        f"Second approval: waiting")
            st.caption("The second person must be different from the first.")
        _po_now(result, store)
        if st.toggle("Show the evidence: all nine stages and the document", key=f"rev_show_{run_id}"):
            left, right = st.columns([0.58, 0.42], gap="large")
            with left:
                C.render_stages(result)
                C.audit_trail(result, store)
            with right:
                C.render_preview(store.load_file(result.file_sha256), result.file_sha256, result, key=f"rev_pv_{run_id}")
        if first is not None:
            st.info(f"First approval on record: {first['reviewer']} on {config.to_ist_text(first['decided_at'])}. "
                    f"Call record: \"{first['note']}\". A second, different person must now approve before the new "
                    f"account is trusted and the invoice becomes payable.", icon=":material/group:")
        needs = required_checks(codes, second=first is not None)
        # The second approval is a form of its own, with its own fields. Reusing the first one's would leave the first
        # approver's name and call note typed in for the second person (a browser keeps a field's text by its key).
        stage = "2" if first is not None else ""
        with st.form(key=f"rev_form{stage}_{run_id}", border=False):
            c1, c2 = st.columns([0.35, 0.65])
            reviewer = c1.text_input("Your name (recorded with the decision)",
                                     value="" if first is not None else st.session_state.get("reviewer", ""),
                                     key=f"rev_name{stage}_{run_id}",
                                     placeholder="A different person from the first approver" if first is not None else "")
            note = c2.text_input("Note for the record", key=f"rev_note{stage}_{run_id}",
                                 placeholder=("Who you spoke to, on which number, and when" if "bank_verified" in needs
                                              else "What you checked, who confirmed it"))
            checks: dict = {}
            evidence = None
            if "bank_verified" in needs:
                checks["bank_verified"] = st.checkbox(needs["bank_verified"], key=f"rev_bank_{run_id}")
                evidence = st.file_uploader("Proof of the change, if you have it (the vendor's letter or a cancelled "
                                            "cheque). It is kept with this review.", key=f"rev_proof_{run_id}")
            if "bank_second_check" in needs:
                checks["bank_second_check"] = st.checkbox(needs["bank_second_check"], key=f"rev_bank2_{run_id}")
            offer = replies.offer_for_run(store, run_id)       # round 4: a value from the vendor's reply, checked
            if "invoice_number" in needs:
                stated = offer.get("invoice_number") or {}
                checks["invoice_number"] = st.text_input(needs["invoice_number"], value=stated.get("value", ""),
                                                         key=f"rev_number_{run_id}")
                if stated:
                    st.caption(f"From the vendor's reply, read by AI and checked against the reply text: "
                               f"\"{stated['quote']}\"")
            if offer.get("po_number"):
                po = offer["po_number"]["value"]
                used = (store.run_row(run_id) or {}).get("po_number") or ""
                which = "the PO the desk inferred" if "PO_INFERRED" in codes else "the PO on the invoice"
                same = T.quote_form(po) == T.quote_form(used)
                st.caption(f"The vendor's reply states {po}. " + (f"This agrees with {which}." if same else
                           f"This differs from {which}. Check before approving."))
            if not booked:
                st.caption("The system did not book this document, so there is nothing to approve for payment. Process "
                           "it in the accounting system by hand, then close it here so the queue shows it as done.")
            b1, b2, _ = st.columns([0.26, 0.2, 0.54])
            label = ("Record first approval" if ("bank_verified" in needs and booked) else
                     "Approve for payment" if booked else "Close as processed manually")
            approve = b1.form_submit_button(label, type="primary", width="stretch")
            reject = b2.form_submit_button("Reject", width="stretch")
        inline = st.session_state.get("review_inline")
        if inline and inline[0] == run_id:              # the reason, right under the button that was pressed
            st.session_state.pop("review_inline", None)
            st.error(inline[1])
        if approve and first is not None and reviewer.strip().casefold() == str(first["reviewer"]).strip().casefold():
            words = "Choose a different person. Two people must agree to a bank change."
            st.session_state["flash_review_error"] = f"{run_id}: {words}"
            st.session_state["review_inline"] = (run_id, words)
            st.rerun()
        if approve or reject:
            decision = "REJECT" if reject else ("APPROVE" if booked else "HANDLED")
            proof = (evidence.name, evidence.getvalue()) if (evidence is not None and decision == "APPROVE") else None
            try:
                receipt = apply_review(store, run_id, decision, reviewer, note, checks, master=C.get_master(),
                                       rules=C.get_rules(), evidence=proof)
            except ReviewError as exc:
                st.session_state["flash_review_error"] = f"{run_id}: {exc}"
                st.session_state["review_inline"] = (run_id, str(exc))
                st.rerun()
            else:
                st.session_state["reviewer"] = reviewer.strip()
                vendor_name = (result.vendor or {}).get("name")
                lessons = [C.screen_words(line) for line in receipt.get("learned", [])]
                if vendor_name:
                    lessons = [line.replace(vendor_name, C.vendor_case(vendor_name)) for line in lessons]
                learned = " " + "; ".join(line[:1].upper() + line[1:] for line in lessons) + "." if lessons else ""
                if receipt.get("decision") == "FIRST_APPROVAL":
                    text = (f"{run_id}: first approval recorded by {reviewer.strip()}. The invoice stays on hold until a "
                            f"second, different person approves.")
                else:
                    done = {"APPROVE": "approved for payment", "REJECT": "rejected",
                            "HANDLED": "closed as processed manually"}[decision]
                    who = " and ".join(receipt["approvers"]) if receipt.get("approvers") else reviewer.strip()
                    text = f"{run_id} {done} by {who}.{learned}"
                st.session_state["flash_review"] = text + " The decision is in the audit trail."
                st.rerun()


def page() -> None:
    store = C.get_store()
    st.title("Review queue")
    st.caption("Sign-offs only a named person can give. For who is working what, see Cases. Every decision here is "
               "recorded against a person, in the same audit trail as the system's decisions.")
    flash = st.session_state.pop("flash_review", None)
    if flash:
        st.success(flash)
    problem = st.session_state.pop("flash_review_error", None)
    if problem:
        st.error(problem)
    runs = store.list_runs()
    pending = sorted((r for r in runs if r["review_status"] == "PENDING"),
                     key=lambda r: (r.get("invoice_date") or r["created_at"][:10], r["created_at"], r["seq"]))
    if not pending:
        st.markdown("**Nothing needs a signature.** Nothing is waiting for a person: invoices put on hold appear here "
                    "with the question that has to be answered.")
    else:
        st.markdown(f"**{T.plural(len(pending), 'invoice')} waiting**, oldest first.")
        for row in pending:
            _pending_card(row, store)

    reviews = store.reviews()
    if reviews:
        st.subheader("Decided on review")
        by_run = {r["run_id"]: r for r in runs}
        words = {"APPROVE": "approved for payment", "REJECT": "rejected", "HANDLED": "closed, processed manually",
                 "FIRST_APPROVAL": "first of two approvals", "RELEASED": "released by rule (no person needed)"}
        rows = [{"Reference": r["run_id"], "Vendor": C.vendor_case(by_run.get(r["run_id"], {}).get("vendor_name", "-")),
                 "Invoice no.": by_run.get(r["run_id"], {}).get("invoice_no", "-") or "-",
                 "Total": C.money(by_run.get(r["run_id"], {}).get("total")) if by_run.get(r["run_id"], {}).get("total") else "-",
                 "Decision": words.get(r["decision"], r["decision"]), "Reviewer": r["reviewer"], "Note": r["note"] or "-",
                 "When": config.to_ist_text(r["decided_at"])} for r in reversed(reviews)]
        st.dataframe(rows, hide_index=True, width="stretch", key="rev_decided",
                     column_config={"Note": st.column_config.TextColumn(width="large"),
                                    "Decision": st.column_config.TextColumn(width="medium")})
