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
from . import ui_kit as K
from .process import amount_text, evidence_rows, plain, tidy

WAITING_ON = {"vendor": "the vendor"}
ALL = "All"

# Display only: the question a person can answer in one click, per reason family. The engine's own
# question stays on the record and is shown in the case.
PO_FAMILY = {"PO_NOT_FOUND", "NO_PO_FOUND", "PO_CLOSED", "PO_VENDOR_MISMATCH", "LINE_NOT_ON_PO"}
DUPLICATE_FAMILY = {"DUPLICATE_FILE", "DUPLICATE_INVOICE_NUMBER", "DUPLICATE_CONTENT", "POSSIBLE_DUPLICATE"}
TAX_FAMILY = {"TAX_RATE_UNUSUAL", "TAX_TYPE_MISMATCH"}


def _strip_unit(text: str, word: str) -> str:
    return (text or "").removesuffix(word).strip()


def family_question(f) -> str | None:
    code = f.code
    if code == "PRICE_VARIANCE" and f.evidence.get("pct"):
        pct = str(Decimal(str(f.evidence['pct'])).normalize())
        limit = str(C.get_rules().tolerances.unit_price.pct.normalize())
        return f"The price is {pct}% above the PO (limit {limit}%). Accept the new price, or ask for a corrected invoice?"
    if code == "GOODS_NOT_RECEIVED":
        return "The goods receipt is not posted. Have the goods arrived?"
    if code == "QTY_OVER_PO" and f.observed and f.expected:
        return (f"The invoice bills {tidy(_strip_unit(f.observed, 'billed'))}. The PO has {tidy(f.expected)}. Amend the "
                f"PO, or ask for a corrected invoice?")
    if code in PO_FAMILY:
        return "No open PO matches this invoice. Raise one, or reject the invoice?"
    if code == "BANK_DETAILS_CHANGED":
        return "The bank account differs from the one on file. Has the vendor confirmed this on a number we already hold?"
    if code in DUPLICATE_FAMILY:
        return "This looks like an invoice we already have. Reject it as a duplicate?"
    if code in TAX_FAMILY and f.observed and f.expected:
        return f"GST on the invoice is {f.observed}. We work it out as {f.expected}. Ask for a corrected invoice?"
    return None


def case_question(findings, fallback: str) -> str:
    """The first reason that has a plain question wins; otherwise the engine's question."""
    for f in findings:
        words = family_question(f)
        if words:
            return words
    return fallback


def _who(party: str) -> str:
    return WAITING_ON.get(party, party)


def _due(case: dict):
    return case_engine._as_date(case["due_on"])


def _overdue(case: dict) -> bool:
    due = _due(case)
    return bool(due and due < C.business_date())


def _rupees_a_day(amount: int) -> str:
    return C.money(amount).removesuffix(".00")


def _flash() -> None:
    flash = st.session_state.pop("flash_cases", None)
    if flash:
        kind, text = flash
        {"success": st.success, "info": st.info, "error": st.error}[kind](text)


def _figures(all_cases: list[dict], store) -> None:
    flat = {"delta_color": "off", "delta_arrow": "off", "border": True}
    open_cases = [c for c in all_cases if c["state"] != "RESOLVED"]
    today = C.business_date()
    opened = [d for d in (case_engine._as_date(c["opened_on"]) for c in open_cases) if d]
    oldest = max(((today - d).days for d in opened), default=0)
    on_hold = sum(float(T.to_decimal((store.run_row(c["run_id"]) or {}).get("total")) or 0) for c in open_cases)
    row = st.columns(4)
    row[0].metric("Open cases", len(open_cases), f"{len(all_cases) - len(open_cases)} resolved", **flat)
    row[1].metric("Overdue", sum(1 for c in open_cases if _overdue(c)), "past the answer date", **flat)
    row[2].metric("Oldest", f"{oldest} day{'s' if oldest != 1 else ''}", "since the hold", **flat)
    row[3].metric("Money on hold", K.inr_short(on_hold), "invoice totals in open cases", **flat)
    with st.expander("More figures"):
        more = st.columns(3)
        more[0].metric("Urgent", sum(1 for c in open_cases if c["priority"] == "urgent"),
                       "a legal pay-by date is close", **flat)
        more[1].metric("Resolved without a person", sum(1 for c in all_cases if c["resolution"] == "released by rule"),
                       "released by rule once the fact arrived", **flat)
        more[2].metric("Waiting on the vendor", sum(1 for c in open_cases if c["waiting_on"] == "vendor"),
                       "a message has been sent", **flat)


def _clock_line(clock: dict) -> str:
    left = clock["days_left"]
    when = (f"{left} day{'s' if left != 1 else ''} left" if left > 0 else
            "due today" if left == 0 else f"{-left} day{'s' if left != -1 else ''} overdue")
    return (f"**Legal pay-by date {plain(case_engine.day_text(clock['due']))}, {when}.** "
            f"MSMED Act, {plain(clock['basis'])}; about {plain(_rupees_a_day(clock['interest_per_day']))} a day in "
            f"interest after that date (approximate).")


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
    vendor = C.get_master().vendors.get((result.vendor or {}).get("vendor_id") or "")
    draft, thanks = case_engine.next_draft(case, result, C.get_rules(), vendor.legal_name if vendor else "")
    thank_sent = [m for m in sent if "Thank you" in (m["subject"] or "")]
    if (sent and not thanks) or thank_sent:
        sent = thank_sent or sent
        last = sent[-1]
        st.markdown(f"**Message to the vendor** approved by {plain(last['approved_by'])} and written to the outbox on "
                    f"{plain(config.to_ist_text(last['created_at']))}, to {plain(last['recipient'])}.")
        path = case_engine.outbox_dir(store) / f"{last['message_id']}.eml"
        if path.is_file():
            st.download_button("Download the email (.eml)", data=path.read_bytes(), file_name=path.name,
                               mime="message/rfc822", key=f"msg_eml_{run_id}", icon=":material/download:",
                               on_click="ignore")
        return
    if not draft or case["state"] == "RESOLVED":
        return
    if thanks:
        st.markdown("**Thank-you note to the vendor** (the reply already gave what was asked, so nothing is asked "
                    "again; nothing leaves until a person approves it)")
    else:
        st.markdown("**Message to the vendor** (drafted by the rules; nothing leaves until a person approves it)")
    st.code(draft, language=None, wrap_lines=True)
    with st.form(key=f"msg_form_{run_id}", border=False):
        name = st.text_input("Your name (recorded as the approver)", key=f"msg_name_{run_id}")
        send = st.form_submit_button("Approve and send", type="primary")
    st.caption("Send means the email is written to the outbox and recorded. No mail server is connected.")
    if send:
        try:
            out = case_engine.approve_message(store, C.get_master(), C.get_rules(), case["case_id"], name,
                                              C.business_date())
        except case_engine.CaseError as exc:
            st.session_state["flash_cases"] = ("error", str(exc))
        else:
            st.session_state["flash_cases"] = (
                "success", f"Thank-you note approved by {name.strip()} and written to the outbox, to {out['to']}. The "
                           f"case is ready to process." if out.get("thanks") else
                           f"Message approved by {name.strip()} and written to the outbox, to {out['to']}. The case "
                           f"now waits on the vendor, reply due {case_engine.day_text(out['due_on'])}.")
        st.rerun()


def _card(case: dict, store, live, rules) -> None:
    run_id = case["run_id"]
    result = store.get_run(run_id)
    row = store.run_row(run_id) or {}
    if result is None:
        return
    reasons = result.decision.reasons if result.decision else []
    clock = case_engine.clock_for_case(store, live, rules, case, C.business_date())
    due = _due(case)
    with st.container(border=True, key=f"case_{case['case_id']}"):
        vendor = C.vendor_case(row.get("vendor_name")) or "Unknown vendor"
        number = row.get("invoice_no")
        num_str = f"invoice {plain(number)}" if number else "invoice (number missing)"
        amount = amount_text(row.get("total"))
        top = st.container(horizontal=True, vertical_alignment="center")
        top.markdown(f"**{plain(vendor)}** · {num_str}{' · ' + amount if amount else ''} :gray[({plain(run_id)})]")
        if due:
            top.markdown(K.chip_md(*K.due_label(due, C.business_date())))
        offered = case_engine.offered_values(case)
        if offered and case["state"] == "ANSWERED":
            stated = " and ".join(f"{case_engine.VALUE_WORDS.get(k, k)} {v['value']}" for k, v in offered.items())
            st.markdown(f"#### {plain(f'The vendor gave the {stated}. Confirm it and process the invoice.')}")
        else:
            st.markdown(f"#### {plain(case_question(reasons, case['question']))}")
        chips = [K.chip_md("resolved", "Ready to process") if ready(case) else
                 f":gray-badge[:material/assignment_ind: Waiting on {plain(_who(case['waiting_on']))}]"]
        if case["priority"] == "urgent":
            chips.append(K.chip_md("overdue", "Urgent"))
        if clock:
            chips.append(f":gray-badge[:material/gavel: Small supplier · legal pay-by date {case_engine.day_text(clock['due'])}]")
        if case["escalated_to"]:
            chips.append(f":gray-badge[:material/trending_up: Escalated to {plain(case['escalated_to'])} on "
                         f"{case_engine.day_text(case_engine._as_date(case['escalated_on']))}]")
        st.markdown(" ".join(chips))
        with st.expander("Open case", icon=":material/folder_open:"):
            state = state_words(case)
            urgent = "Urgent. " if case["priority"] == "urgent" else ""
            escalated = (f" Escalated to {case['escalated_to']} on "
                         f"{case_engine.day_text(case_engine._as_date(case['escalated_on']))}." if case["escalated_to"] else "")
            when = K.due_label(due, C.business_date())[1] if due else "No due date"
            st.markdown(f"**{plain(urgent)}{plain(state)}.** {plain(when)}. Owner: {plain(case['owner'])}.{plain(escalated)}")
            if clock:
                st.markdown(_clock_line(clock))
            st.markdown(f"**The question for the owner.** {plain(case['question'])}")
            st.markdown("**What the desk found**")
            rows = [r for r in evidence_rows(result) if not r["ok"]]
            if rows:
                K.evidence_panel(rows)
            st.markdown("\n".join(f"- {plain(f.title or f.code)}: {plain(C.screen_words(f.summary))}" for f in reasons))
            st.markdown("**Timeline**")
            C.table({"When": config.to_ist_text(e["at"]), "Actor": _actor(e["actor"]), "What": C.screen_words(e["summary"]),
                     "By": "AI (checked by the desk)" if _actor(e["actor"]).startswith("AI") else e["actor"]}
                    for e in store.case_events(case["case_id"]))
            if "GOODS_NOT_RECEIVED" in case["reason_codes"].split(","):
                _goods_receipt_form(case, store, live)
            _replies(case, store)
            _message(case, result, store)
            if st.button("Decide this hold in the review queue", key=f"case_review_{run_id}",
                         icon=":material/fact_check:"):
                st.session_state["goto"] = "review"
                st.rerun()


def _actor(name: str) -> str:
    """Who did it, as a kind: the rules, the AI (whose reading the desk checks), or a named person."""
    if (name or "") == "Invoice Desk":
        return "Rules"
    return "AI (checked by the desk)" if "AI" in (name or "").split() else "Person"


def ready(case: dict) -> bool:
    """The vendor's reply gave what was asked and nothing else is awaited: a person can process the invoice."""
    return case["state"] == "ANSWERED" and bool(case_engine.offered_values(case))


def state_words(case: dict) -> str:
    if case["state"] == "WAITING":
        return f"Waiting on {_who(case['waiting_on'])}"
    if ready(case):
        return "Ready to process"
    return "Answered, ready for a person to decide"


def _replies(case: dict, store) -> None:
    """Replies matched to this case (round 4): the text as plain text, and what was offered or why not."""
    master = C.get_master()
    rules = C.get_rules()
    for m in store.messages(case["case_id"]):
        if m["direction"] != "in" or m.get("kind") != "reply" or m["message_id"].endswith("-VM"):
            continue                              # the original invoice email is not a reply
        st.markdown(f"**Reply received from {plain(m['sender'] or 'an unknown sender')} on "
                    f"{plain(config.to_ist_text(m.get('received_at') or ''))}.**")
        st.caption(f"Subject: {C.screen_dates(m.get('subject') or '(no subject)')}")
        sender_chk = mailbox.check_sender(m.get("sender") or "", master, rules)
        if sender_chk.result == "lookalike":
            dom = mailbox._domain(m.get("sender") or "")
            ven = master.vendors.get(sender_chk.vendor_id)
            ven_dom = mailbox._domain(ven.email) if ven and ven.email else "the vendor domain"
            st.warning(f"Careful: {dom} looks like {ven_dom} and is not the same.")
        attachments = json.loads(m.get("attachments_json") or "[]")
        pdf_atts = [a for a in attachments if (a.get("name") or "").lower().endswith(".pdf") or a.get("mime") == "application/pdf"]
        for a in pdf_atts:
            st.info(f"The vendor's reply has a corrected invoice attached: {a['name']}. A person compares it with the "
                    f"original and replaces it.", icon=":material/attach_file:")
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
    st.caption("Every hold becomes a case: one question, one owner, one due date. The desk follows it until it is "
               "resolved or a person has a clear decision to make.")
    if not rules.cases.enabled:
        st.info("The case desk is switched off in the desk rules. Holds wait in the review queue as before.")
        return
    live = case_engine.master_with_receipts(C.get_master(), store)
    case_engine.sync(store, live, rules, C.business_date())
    _flash()
    all_cases = store.list_cases()
    _figures(all_cases, store)

    every_open = [c for c in all_cases if c["state"] != "RESOLVED"]

    def waiting(party: str) -> list[dict]:
        return [c for c in every_open if party == ALL or c["waiting_on"] == party]

    present = list(dict.fromkeys(c["waiting_on"] for c in every_open))      # every case is in exactly one pill
    parties = [ALL] + list(rules.cases.reply_due_days) + [p for p in present if p not in rules.cases.reply_due_days]
    chosen = st.pills("Waiting on", parties, default=ALL, key="cases_waiting",
                      format_func=lambda p: f"{_who(p).capitalize() if p == 'vendor' else p} ({len(waiting(p))})")
    chosen = chosen or ALL
    open_cases = waiting(chosen)
    open_cases.sort(key=lambda c: (not _overdue(c), c["priority"] != "urgent", c["due_on"]))   # overdue, then urgent, then nearest
    if not every_open:
        st.info("No open cases. Every invoice so far has cleared or been closed.", icon=":material/task_alt:")
        if st.button("Run a held invoice", key="cases_run_held", icon=":material/play_circle:"):
            st.session_state["goto"] = "process"
            st.rerun()
    elif not open_cases:
        st.info(f"Nothing is waiting on {_who(chosen)}.", icon=":material/filter_alt_off:")
    for case in open_cases:
        _card(case, store, live, rules)

    resolved = [c for c in all_cases if c["state"] == "RESOLVED"]
    if resolved:
        st.subheader("Resolved cases")
        rows = []
        for c in reversed(resolved):
            run = store.run_row(c["run_id"]) or {}
            day = case_engine._as_date(c["resolved_on"])
            rows.append({"Reference": c["run_id"], "Vendor": C.vendor_case(run.get("vendor_name")) or "-",
                         "Invoice no.": run.get("invoice_no") or "-", "Owner": c["owner"],
                         "Outcome": c["resolution"], "Resolved on": case_engine.day_text(day) if day else "-"})
        C.table(rows)
