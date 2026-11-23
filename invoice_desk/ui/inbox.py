"""Page, Inbox (round 4): everything that reaches the AP address, sorted, owned and timed.

The page only displays and collects a name. Sorting, routing and the audit entries are done by
invoice_desk/inbox.py, which the tests and the preflight rehearsal use too. Messages are shown
as plain text only.
"""
from __future__ import annotations

import streamlit as st

from .. import cases as case_engine
from .. import config
from .. import inbox as desk_inbox
from ..mailbox import strip_quoted
from . import common as C

CAPTION = ("Everything that reaches the AP address lands here. The desk sorts each message, does what the procedure "
           "allows, and shows who has the next step.")


def _flash() -> None:
    flash = st.session_state.pop("flash_inbox", None)
    if flash:
        kind, text = flash
        {"success": st.success, "info": st.info, "error": st.error}[kind](text)


def _deliver(files: list[tuple[str, bytes]], store, rules) -> None:
    pipeline = C.get_pipeline(store=store)
    results = []
    with st.status(f"Reading {len(files)} message(s)...", expanded=True) as box:
        for name, data in files:
            st.write(f"{name}: reading")
            results += desk_inbox.deliver(store, C.get_master(), rules, pipeline, [(name, data)], C.business_date())
        box.update(label="Done", state="complete")
    new = sum(r["status"] == "received" for r in results)
    seen = len(results) - new
    st.session_state["flash_inbox"] = ("success", f"{new} message(s) received and sorted"
                                       + (f"; {seen} had already been received and changed nothing." if seen else "."))
    st.rerun()


def _figures(store) -> None:
    f = desk_inbox.figures(store, C.business_date())
    plain = {"delta_color": "off", "delta_arrow": "off", "border": True}
    row = st.columns(4)
    row[0].metric("Received", f["received"], **plain)
    row[1].metric("Sorted with no person needed", f["sorted"], **plain)
    row[2].metric("Waiting for a person", f["waiting"], **plain)
    row[3].metric("Oldest waiting (days)", f["oldest_days"], **plain)


def _due_text(row: dict) -> str:
    if row["state"] in ("sorted", "closed"):
        return "-"
    day = desk_inbox._day(row["due_on"])
    return (case_engine.day_text(day) if day else "-") + (" (overdue)" if row["overdue"] else "")


def _who(row: dict) -> str:
    return "-" if row["state"] in ("sorted", "closed") else row["owner"]


def _detail(row: dict, store, rules) -> None:
    mid = row["message_id"]
    st.markdown(f"**{C.esc(row['subject'] or '(no subject)')}**", unsafe_allow_html=True)
    st.caption(f"From {row['sender'] or 'unknown'} | received {config.to_ist_text(row['received_at'])} | {mid}")
    new_text, quoted = strip_quoted(row["body"] or "")
    st.text(new_text.strip() or "(no text)")
    if quoted:
        with st.expander("Quoted earlier messages"):
            st.text(quoted)
    if row["attachments"]:
        C.table({"Attachment": a["name"], "Type": a["mime"], "Size (KB)": f"{a['size'] / 1024:.0f}"}
                for a in row["attachments"])
    sender_line = row["sender_detail"]
    if row["sender_check"] == "lookalike":
        st.warning(sender_line)
    else:
        C.text_block(sender_line)
    C.text_block(f"Sorted by the rule: {row['route_rule']}.")
    C.text_block(f"What the desk did: {row['action']}")
    for flag in row["flags"]:
        st.warning(flag)
    links = st.columns(3)
    if row["run_ids"] and links[0].button("Open the run", key=f"ib_run_{mid}", icon=":material/receipt_long:"):
        st.session_state["goto"] = "dashboard"
        st.rerun()
    if row["case_id"] and links[1].button("Open the case", key=f"ib_case_{mid}", icon=":material/assignment_late:"):
        st.session_state["goto"] = "cases"
        st.rerun()
    if row["state"] == "closed":
        st.caption(f"Closed by {row['closed_by']}: {row['closed_note'] or 'no note'}.")
        return
    if row["kind"] == "other":
        with st.form(key=f"ib_route_{mid}"):
            st.markdown("**Route this message**")
            kinds = [k for k in rules.inbox.owners if k != "other"]
            kind = st.selectbox("It is", kinds, format_func=lambda k: desk_inbox.KIND_LABEL[k], key=f"ib_kind_{mid}")
            who = st.text_input("Your name", key=f"ib_route_name_{mid}")
            if st.form_submit_button("Route"):
                try:
                    desk_inbox.route_item(store, rules, mid, kind, who, C.business_date())
                    st.session_state["flash_inbox"] = ("success", f"Routed as {desk_inbox.KIND_LABEL[kind]}.")
                    st.rerun()
                except desk_inbox.InboxError as exc:
                    st.error(str(exc))
    if row["state"] == "waiting":
        with st.form(key=f"ib_close_{mid}"):
            st.markdown("**Close this item**")
            who = st.text_input("Your name", key=f"ib_close_name_{mid}")
            note = st.text_input("One-line note", key=f"ib_close_note_{mid}")
            if st.form_submit_button("Close"):
                try:
                    desk_inbox.close_item(store, mid, who, note, C.business_date())
                    st.session_state["flash_inbox"] = ("success", f"{mid} closed by {who.strip()}.")
                    st.rerun()
                except desk_inbox.InboxError as exc:
                    st.error(str(exc))


def page() -> None:
    store = C.get_store()
    rules = C.get_rules()
    st.title("Inbox")
    st.caption(CAPTION)
    if not rules.inbox.enabled:
        st.info(desk_inbox.SENTENCE["off"])
        return
    _flash()
    bundled = desk_inbox.bundled_mail()
    left, right = st.columns([1, 2])
    if left.button(f"Deliver today's mail ({len(bundled)} messages)", key="ib_deliver", type="primary",
                   icon=":material/mail:", disabled=not bundled):
        _deliver(bundled, store, rules)
    uploads = right.file_uploader("Add .eml files", type=["eml"], accept_multiple_files=True, key="ib_upload")
    if uploads and right.button("Read the added files", key="ib_read_uploads"):
        _deliver([(u.name, u.getvalue()) for u in uploads], store, rules)
    st.caption("No mailbox is connected in this build. Messages are files.")
    _figures(store)

    rows = desk_inbox.listing(store, C.business_date())
    if not rows:
        st.markdown(C.neutral_banner_html("Inbox empty", "No messages yet",
                                          "Deliver today's mail to see the desk sort it."), unsafe_allow_html=True)
        return
    C.table({"When": config.to_ist_text(r["received_at"]), "From": r["sender"], "Subject": r["subject"],
             "What it is": desk_inbox.KIND_LABEL.get(r["kind"], r["kind"]), "What the desk did": r["action"],
             "Next step with": _who(r), "Due": _due_text(r)} for r in rows)
    labels = {f"{desk_inbox.KIND_LABEL.get(r['kind'], r['kind'])}: {r['subject'] or '(no subject)'} ({r['message_id']})": r
              for r in rows}
    chosen = st.selectbox("Open a message", list(labels), key="ib_open")
    if chosen:
        with st.container(border=True):
            _detail(labels[chosen], store, rules)
