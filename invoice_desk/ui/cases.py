"""Page, Cases: what happens after a hold (round 3, DECISIONS.md D65 to D70).

Every hold becomes a case with one owner, one question and a clock. This page is the only
screen that runs the reconciler (`cases.sync`), so the first eleven demo steps and every
other page behave exactly as before. Stores can record a delivery here, which may release
an invoice that waited only for it, and a person approves the drafted message to a vendor
before it is written to the outbox.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import streamlit as st

from .. import cases as case_engine
from .. import config
from .. import mailbox
from .. import replies
from ..mailbox import strip_quoted
from .. import textutil as T
from . import common as C

WAITING_ON = {"vendor": "the vendor"}


def _who(party: str) -> str:
    return WAITING_ON.get(party, party)


def _rupees_a_day(amount: int) -> str:
    return C.money(amount).removesuffix(".00")


def _flash() -> None:
    flash = st.session_state.pop("flash_cases", None)
    if flash:
        kind, text = flash
        {"success": st.success, "info": st.info, "error": st.error}[kind](text)


def _figures(all_cases: list[dict]) -> None:
    plain = {"delta_color": "off", "delta_arrow": "off", "border": True}
    open_cases = [c for c in all_cases if c["state"] != "RESOLVED"]
    row = st.columns(4)
    row[0].metric("Open cases", len(open_cases), f"{len(all_cases) - len(open_cases)} resolved", **plain)
    row[1].metric("Urgent", sum(1 for c in open_cases if c["priority"] == "urgent"),
                  "a legal pay-by date is close", **plain)
    row[2].metric("Resolved without a person", sum(1 for c in all_cases if c["resolution"] == "released by rule"),
                  "released by rule once the fact arrived", **plain)
    row[3].metric("Waiting on the vendor", sum(1 for c in open_cases if c["waiting_on"] == "vendor"),
                  "a message has been sent", **plain)


def _clock_line(clock: dict) -> str:
    left = clock["days_left"]
    when = (f"{left} day{'s' if left != 1 else ''} left" if left > 0 else
            "due today" if left == 0 else f"{-left} day{'s' if left != -1 else ''} overdue")
    return (f"<div class='idk-text'><b>Legal pay-by date {C.esc(case_engine.day_text(clock['due']))}, {when}.</b> "
            f"MSMED Act, {C.esc(clock['basis'])}; about {C.esc(_rupees_a_day(clock['interest_per_day']))} a day in "
            f"interest after that date (approximate).</div>")


def _goods_receipt_form(case: dict, store, live) -> None:
    run_id = case["run_id"]
    lines = case_engine.billed_lines(store, live, case)
    if not lines:
        return
    missing = [ln for ln in lines if ln["received"] < ln["billed"]] or lines
    labels = {f"{ln['po_number']} line {ln['line_no']}: {ln['description']} ({T.fmt_num(ln['billed'])} billed, "
              f"{T.fmt_num(ln['received'])} received)": ln for ln in missing}
    first = missing[0]
    short = max(first["billed"] - first["received"], Decimal("0"))
    with st.form(key=f"gr_form_{run_id}"):
        st.markdown("**Record goods receipt (Stores)**")
        pick = st.selectbox("PO line", list(labels), key=f"gr_line_{run_id}")
        c1, c2, c3 = st.columns(3)
        if short == short.to_integral_value():
            qty = c1.number_input("Quantity received", min_value=0, value=int(short), step=1, key=f"gr_qty_{run_id}")
        else:
            qty = c1.number_input("Quantity received", min_value=0.0, value=float(short), step=1.0,
                                  key=f"gr_qty_{run_id}")
        reference = c2.text_input("Delivery note number", key=f"gr_ref_{run_id}")
        received_on = c3.date_input("Received on", value=C.business_date(), key=f"gr_day_{run_id}", format="DD/MM/YYYY")
        name = st.text_input("Your name (recorded with the receipt)", key=f"gr_name_{run_id}")
        submitted = st.form_submit_button("Record goods receipt", type="primary")
    if not submitted:
        return
    line = labels[pick]
    try:
        case_engine.record_goods_receipt(store, C.get_master(), line["po_number"], line["line_no"], Decimal(str(qty)),
                                         received_on if isinstance(received_on, date) else C.business_date(),
                                         name, reference, for_run=run_id)
    except case_engine.CaseError as exc:
        st.session_state["flash_cases"] = ("error", str(exc))
        st.rerun()
    master = case_engine.master_with_receipts(C.get_master(), store)
    case_engine.sync(store, master, C.get_rules(), C.business_date())
    now = store.get_case(case["case_id"])
    if now["state"] == "RESOLVED" and now["resolution"] == "released by rule":
        st.session_state["flash_cases"] = ("success", "Receipt recorded. Nothing else was holding this invoice, so it "
                                                      "has been released and posted for payment.")
    else:
        why = case_engine.try_resume(store, master, C.get_rules(), now, C.business_date())["why"]
        st.session_state["flash_cases"] = ("info", f"Receipt recorded. {why}")
    st.rerun()


def _message(case: dict, result, store) -> None:
    run_id = case["run_id"]
    sent = [m for m in store.messages(case["case_id"]) if m["direction"] == "out"]
    draft = result.decision.vendor_draft if result.decision else None
    if sent:
        last = sent[-1]
        st.markdown(f"<div class='idk-small'><b>Message to the vendor</b> approved by {C.esc(last['approved_by'])} and "
                    f"written to the outbox on {C.esc(config.to_ist_text(last['created_at']))}, to "
                    f"{C.esc(last['recipient'])}.</div>", unsafe_allow_html=True)
        path = case_engine.outbox_dir(store) / f"{last['message_id']}.eml"
        if path.is_file():
            st.download_button("Download the email (.eml)", data=path.read_bytes(), file_name=path.name,
                               mime="message/rfc822", key=f"msg_eml_{run_id}", icon=":material/download:",
                               on_click="ignore")
        return
    if not draft or case["state"] == "RESOLVED":
        return
    st.markdown("**Message to the vendor** (drafted by the rules; nothing leaves until a person approves it)")
    st.code(draft, language=None, wrap_lines=True)
    with st.form(key=f"msg_form_{run_id}", border=False):
        name = st.text_input("Your name (recorded as the approver)", key=f"msg_name_{run_id}")
        send = st.form_submit_button("Approve and send", type="primary")
    st.caption("In this build, send means the email is written to the outbox folder and recorded. No mail server is "
               "connected.")
    if send:
        try:
            out = case_engine.approve_message(store, C.get_master(), C.get_rules(), case["case_id"], name,
                                              C.business_date())
        except case_engine.CaseError as exc:
            st.session_state["flash_cases"] = ("error", str(exc))
        else:
            st.session_state["flash_cases"] = (
                "success", f"Message approved by {name.strip()} and written to the outbox, to {out['to']}. The case "
                           f"now waits on the vendor, reply due {case_engine.day_text(out['due_on'])}.")
        st.rerun()


def _card(case: dict, store, live, rules) -> None:
    run_id = case["run_id"]
    result = store.get_run(run_id)
    row = store.run_row(run_id) or {}
    if result is None:
        return
    with st.container(border=True):
        vendor = row.get("vendor_name") or "Unknown vendor"
        number = row.get("invoice_no")
        num_str = f"invoice {C.esc(number)}" if number else "invoice (number missing)"
        amount = C.money(row["total"]) if row.get("total") else ""
        st.markdown(f"<div class='idk-text'><b>{C.esc(vendor)}</b>, {num_str}"
                    f"{', ' + C.esc(amount) if amount else ''} <span class='idk-small'>({C.esc(run_id)})</span></div>",
                    unsafe_allow_html=True)
        state = (f"Waiting on {_who(case['waiting_on'])}" if case["state"] == "WAITING" else
                 "Answered, ready for a person to decide")
        due = case_engine._as_date(case["due_on"])
        urgent = "Urgent. " if case["priority"] == "urgent" else ""
        escalated = (f" Escalated to {case['escalated_to']} on {case_engine.day_text(case_engine._as_date(case['escalated_on']))}."
                     if case["escalated_to"] else "")
        st.markdown(f"<div class='idk-text'><b>{C.esc(urgent)}{C.esc(state)}.</b> Answer due "
                    f"{C.esc(case_engine.day_text(due)) if due else '-'}. Owner: {C.esc(case['owner'])}."
                    f"{C.esc(escalated)}</div>", unsafe_allow_html=True)
        clock = case_engine.clock_for_case(store, live, rules, case, C.business_date())
        if clock:
            st.markdown(_clock_line(clock), unsafe_allow_html=True)
        st.markdown(f"<div class='idk-text'><b>The question.</b> {C.esc(case['question'])}</div>",
                    unsafe_allow_html=True)
        st.markdown("**What has happened**")
        C.table({"When": config.to_ist_text(e["at"]), "What": e["summary"], "By": e["actor"]}
                for e in store.case_events(case["case_id"]))
        with st.expander("Why it was held"):
            C.render_findings(result.decision.reasons if result.decision else [])
        if "GOODS_NOT_RECEIVED" in case["reason_codes"].split(","):
            _goods_receipt_form(case, store, live)
        _replies(case, store)
        _message(case, result, store)
        if st.button("Decide this hold in the review queue", key=f"case_review_{run_id}", icon=":material/fact_check:"):
            st.session_state["goto"] = "review"
            st.rerun()


def _replies(case: dict, store) -> None:
    """Replies matched to this case (round 4): the text as plain text, and what was offered or why not."""
    master = C.get_master()
    rules = C.get_rules()
    for m in store.messages(case["case_id"]):
        if m["direction"] != "in":
            continue
        day = case_engine._as_date(m.get("received_at") or "")
        st.markdown(f"**Reply received from {C.esc(m['sender'] or 'an unknown sender')} on "
                    f"{C.esc(case_engine.day_text(day)) if day else '-'}.**", unsafe_allow_html=True)
        sender_chk = mailbox.check_sender(m.get("sender") or "", master, rules)
        if sender_chk.result == "lookalike":
            dom = mailbox._domain(m.get("sender") or "")
            ven = master.vendors.get(sender_chk.vendor_id)
            ven_dom = mailbox._domain(ven.email) if ven and ven.email else "the vendor domain"
            st.warning(f"Careful: {dom} looks like {ven_dom} and is not the same.")
        attachments = json.loads(m.get("attachments_json") or "[]")
        pdf_atts = [a for a in attachments if (a.get("name") or "").lower().endswith(".pdf") or a.get("mime") == "application/pdf"]
        for a in pdf_atts:
            st.info(f"A corrected invoice is attached. Replacing the original is done by a person in this build. ({a['name']})")
        st.text(strip_quoted(m["body"] or "")[0].strip() or "(no text)")
        report = json.loads(m.get("read_json") or "{}")
        offered = report.get("offered") or {}
        for name, o in offered.items():
            label = replies.VALUE_LABEL.get(name, name)
            st.success(f"The reply states {label} {o['value']}. Read by AI and checked against the reply text: "
                       f"\"{o['quote']}\". Confirm it in the review queue.")
        if report.get("instructions") or "This reply tells the reader to mark the invoice paid" in (report.get("reason") or ""):
            st.warning("This reply tells the reader to mark the invoice paid and change bank details. "
                       "The desk never acts on instructions in an email. "
                       "Bank details change only after a call-back to the number on file and approval by two people.")
        elif report.get("outcome") == "person":
            st.warning(f"{replies.PERSON} Reason: {report.get('reason') or 'not read'}")
        elif report.get("outcome") in ("confirms", "disputes", "asks_question") and report.get("summary"):
            C.text_block(f"AI summary of the reply: {report['summary']}")


def page() -> None:
    store = C.get_store()
    rules = C.get_rules()
    st.title("Cases")
    st.caption("Every hold becomes a case with one owner, one question and a clock. The desk follows it until it is "
               "resolved or a person has a clear decision to make.")
    if not rules.cases.enabled:
        st.info("The case desk is switched off in rules.yaml (cases.enabled: false). Holds wait in the review queue "
                "as before.")
        return
    live = case_engine.master_with_receipts(C.get_master(), store)
    case_engine.sync(store, live, rules, C.business_date())
    _flash()
    all_cases = store.list_cases()
    _figures(all_cases)

    owners = ["All"] + list(rules.cases.reply_due_days)
    chosen = st.selectbox("Show cases for", owners, key="cases_owner", format_func=lambda o: _who(o).capitalize()
                          if o == "vendor" else o)
    open_cases = [c for c in all_cases if c["state"] != "RESOLVED"
                  and (chosen == "All" or chosen in (c["owner"], c["waiting_on"]))]
    open_cases.sort(key=lambda c: (c["priority"] != "urgent", c["due_on"]))
    if not open_cases:
        st.markdown(C.neutral_banner_html("No open cases", "Nothing is waiting" + ("" if chosen == "All" else f" on {chosen}"),
                                          "When an invoice is put on hold, its case appears here with its owner, its "
                                          "question and its answer date."), unsafe_allow_html=True)
    for case in open_cases:
        _card(case, store, live, rules)

    resolved = [c for c in all_cases if c["state"] == "RESOLVED"]
    if resolved:
        st.subheader("Resolved cases")
        rows = []
        for c in reversed(resolved):
            run = store.run_row(c["run_id"]) or {}
            day = case_engine._as_date(c["resolved_on"])
            rows.append({"Reference": c["run_id"], "Vendor": run.get("vendor_name") or "-",
                         "Invoice no.": run.get("invoice_no") or "-", "Owner": c["owner"],
                         "Outcome": c["resolution"], "Resolved on": case_engine.day_text(day) if day else "-"})
        C.table(rows)
