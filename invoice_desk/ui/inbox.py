"""Page, Inbox (round 4): everything that reaches the AP address, sorted, owned and timed.

The page only displays and collects a name. Sorting, routing and the audit entries are done by
invoice_desk/inbox.py, which the tests and the preflight rehearsal use too. Messages are shown
as plain text only.
"""
from __future__ import annotations

import streamlit as st

from .. import cases as case_engine
from .. import answers, config
from .. import inbox as desk_inbox
from ..mailbox import strip_quoted
from .. import textutil as T
from . import common as C
from . import ui_kit as K

CAPTION = ("Everything that reaches the AP address lands here. The desk sorts each message, does what the procedure "
           "allows, and shows who has the next step.")


def _flash() -> None:
    flash = st.session_state.pop("flash_inbox", None)
    if flash:
        kind, text = flash
        {"success": st.success, "info": st.info, "error": st.error}[kind](text)


def _deliver(files: list[tuple[str, bytes]], store, rules, morning: bool = False) -> None:
    pipeline = C.get_pipeline(store=store)
    results = []
    with st.status(f"Reading {T.plural(len(files), 'message')}", expanded=True) as box:
        for i, (name, data) in enumerate(files, 1):
            st.write(f"Message {i} of {len(files)}: {name}")
            results += desk_inbox.deliver(store, C.get_master(), rules, pipeline, [(name, data)], C.business_date(),
                                          morning=morning)
        box.update(label="Done", state="complete")
    new = sum(r["status"] == "received" for r in results)
    seen = len(results) - new
    st.session_state["flash_inbox"] = ("success", f"{T.plural(new, 'message')} received and sorted"
                                       + (f". {T.plural(seen, 'message')} had already been received and changed "
                                          f"nothing." if seen else "."))
    st.rerun()


def size_text(size: int) -> str:
    """An attachment's real size: 227 bytes, 3.1 KB, 1.2 MB."""
    size = int(size or 0)
    if size < 1024:
        return f"{size} byte{'s' if size != 1 else ''}"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def grouped(rows: list[dict]) -> list[dict]:
    """One row per email. A second item made from the same email (bank-change wording sent to the Vendor
    master team as well) is carried inside the first as `also`, so no email is listed twice."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in rows:
        key = r.get("eml_sha256") or r.get("rfc_message_id") or r["message_id"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    out = []
    for key in order:
        group = groups[key]
        lead = min(group, key=lambda g: (g["message_id"].endswith("-VM"), g["message_id"]))
        out.append({**lead, "also": [g for g in group if g is not lead]})
    return out


def short_action(text: str, limit: int = 90) -> str:
    """The first sentence of what the desk did, for the table. The full text is in the message."""
    text = (text or "").strip()
    first = text.split(". ")[0].rstrip(".")
    return (first if len(first) <= limit else first[:limit - 3].rsplit(" ", 1)[0] + "...") + "."


def _figures(store) -> None:
    f = desk_inbox.figures(store, C.business_date())
    row = st.columns(4)
    with row[0]:
        K.kpi_card("Received", str(f["received"]), "emails, each listed once",
                   help="Every email that reached the AP address. A second item made from the same email is shown "
                        "inside it, not as a new row.")
    with row[1]:
        K.kpi_card("Sorted, no person", str(f["sorted"]), "handled by the rules",
                   help="Messages the desk sorted and finished with nobody touching them.")
    with row[2]:
        K.kpi_card("Waiting for a person", str(f["waiting"]), "each with a named owner",
                   help="Items that need a person, including a bank-change request sent to the Vendor master team.")
    with row[3]:
        K.kpi_card("Oldest waiting", T.plural(f["oldest_days"], "day"), "since it arrived",
                   help="The age of the oldest item still waiting for a person.")


def _due_text(row: dict) -> str:
    if row["state"] in ("sorted", "closed") and not any(a["state"] == "waiting" for a in row.get("also", [])):
        return "-"
    day = desk_inbox._day(row["due_on"])
    return K.due_label(day, C.business_date())[1] if day else "-"


def _who(row: dict) -> str:
    owners = [g["owner"] for g in [row] + row.get("also", []) if g["state"] not in ("sorted", "closed") and g["owner"]]
    return " and ".join(dict.fromkeys(owners)) or "-"


def _detail(row: dict, store, rules) -> None:
    mid = row["message_id"]
    state = {"sorted": ("resolved", "Sorted, no person needed"), "waiting": ("due_soon", "Waiting for a person"),
             "answered": ("resolved", "Answered"), "closed": ("resolved", "Closed")}.get(row["state"])
    if state:
        st.markdown(K.chip_md(*state) + " " + K.chip_md("on_track", desk_inbox.KIND_LABEL.get(row["kind"], row["kind"])))
    st.markdown(f"**{C.esc(C.screen_dates(row['subject'] or '(no subject)'))}**", unsafe_allow_html=True)
    st.caption(f"From {row['sender'] or 'unknown'} · received {config.to_ist_text(row['received_at'])} · {mid}")
    new_text, quoted = strip_quoted(row["body"] or "")
    st.text(new_text.strip() or "(no text)")
    if quoted:
        with st.expander("Quoted earlier messages"):
            st.text(quoted)
    if row["attachments"]:
        C.table({"Attachment": a["name"], "Type": a["mime"], "Size": size_text(a["size"])}
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
    for extra in row.get("also", []):
        st.warning(f"Also sent to the {extra['owner']}: {extra['action']}")
    links = st.columns(3)
    if row["run_ids"] and links[0].button("Open the run", key=f"ib_run_{mid}", icon=":material/receipt_long:"):
        st.session_state["goto"] = "dashboard"
        st.rerun()
    if row["case_id"] and links[1].button("Open the case", key=f"ib_case_{mid}", icon=":material/assignment_late:"):
        st.session_state["goto"] = "cases"
        st.rerun()
    if row["kind"] == "status_query" and row["state"] not in ("closed",):
        _answer(row, store, rules)
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
    for extra in row.get("also", []):
        if extra["state"] == "waiting":
            _close_form(extra, store, f"Close the {extra['owner']} item")
    if row["state"] == "waiting":
        _close_form(row, store, "Close this item")


def _close_form(row: dict, store, title: str) -> None:
    mid = row["message_id"]
    with st.form(key=f"ib_close_{mid}"):
        st.markdown(f"**{title}**")
        who = st.text_input("Your name", key=f"ib_close_name_{mid}")
        note = st.text_input("One-line note", key=f"ib_close_note_{mid}")
        if st.form_submit_button("Close"):
            try:
                desk_inbox.close_item(store, mid, who, note, C.business_date())
                st.session_state["flash_inbox"] = ("success", f"{mid} closed by {who.strip()}.")
                st.rerun()
            except desk_inbox.InboxError as exc:
                st.error(str(exc))


def _answer(row: dict, store, rules) -> None:
    """The rule-written answer to a payment question, the records it is built on, and the named approval."""
    mid = row["message_id"]
    if row["state"] == "answered":
        sent = store.get_message(f"{mid}-ANS")
        if sent:
            st.success(f"Answer approved by {sent['approved_by']} and written to the outbox, addressed to "
                       f"{sent['recipient']}.")
            path = case_engine.outbox_dir(store) / f"{mid}-ANS.eml"
            if path.is_file():
                st.download_button("Download the answer (.eml)", data=path.read_bytes(), file_name=path.name,
                                   mime="message/rfc822", key=f"ib_ans_dl_{mid}")
        return
    d = answers.draft(store, C.get_master(), rules, row, C.business_date(),
                      C.get_llm() if rules.answers.ai_reword else None)
    if d["status"] != "drafted":
        st.info(d["reason"])
        return
    st.markdown("**Drafted answer**")
    st.text(d["text"])
    C.table({"Invoice": f["invoice_no"], "What our records say": f["says"], "Record": f["record"]} for f in d["facts"])
    for flag in d["flags"]:
        st.warning(flag)
    with st.form(key=f"ib_ans_{mid}"):
        who = st.text_input("Your name", key=f"ib_ans_name_{mid}")
        go = st.form_submit_button("Approve and send", type="primary")
        st.caption(answers.SEND_NOTE)
    if go:
        try:
            answers.approve(store, C.get_master(), rules, mid, who, d["sha256"], C.business_date(),
                            C.get_llm() if rules.answers.ai_reword else None)
            st.session_state["flash_inbox"] = ("success", f"Answer approved by {who.strip()} and written to the outbox.")
            st.rerun()
        except answers.AnswerError as exc:
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
    morning = desk_inbox.bundled_mail("next_morning")
    left, right = st.columns([1, 2])
    if left.button(f"Deliver today's mail ({T.plural(len(bundled), 'message')})", key="ib_deliver", type="primary",
                   icon=":material/mail:", disabled=not bundled):
        _deliver(bundled, store, rules)
    if left.button(f"Deliver the next morning's mail ({T.plural(len(morning), 'reply', 'replies')})",
                   key="ib_deliver_next", icon=":material/forward_to_inbox:", disabled=not morning):
        _deliver(morning, store, rules, morning=True)
    uploads = right.file_uploader("Add .eml files", type=["eml"], accept_multiple_files=True, key="ib_upload")
    if uploads and right.button("Read the added files", key="ib_read_uploads"):
        _deliver([(u.name, u.getvalue()) for u in uploads], store, rules)
    st.caption("No mailbox is connected yet. Messages arrive as files.")
    _figures(store)

    rows = grouped(desk_inbox.listing(store, C.business_date()))
    if not rows:
        st.info("No messages yet. Deliver today's mail to see the desk sort it.", icon=":material/inbox:")
        return
    with st.container(key="ib_table"):
        st.html("<style>.st-key-ib_table table {table-layout:fixed;width:100%} "
                ".st-key-ib_table th,.st-key-ib_table td {white-space:normal;overflow-wrap:anywhere}</style>")
        st.table([{"When": config.to_ist_text(r["received_at"]), "From": r["sender"],
                   "Subject": C.screen_dates(r["subject"]),
                   "What it is": desk_inbox.KIND_LABEL.get(r["kind"], r["kind"]),
                   "What the desk did": C.screen_words(r["action"]), "Next step with": _who(r), "Due": _due_text(r)}
                  for r in rows], hide_index=True, width="stretch")
    labels = {f"{desk_inbox.KIND_LABEL.get(r['kind'], r['kind'])}: {C.screen_dates(r['subject'] or '(no subject)')} ({r['message_id']})": r
              for r in rows}
    chosen = st.selectbox("Open a message", list(labels), key="ib_open")
    if chosen:
        with st.container(border=True):
            _detail(labels[chosen], store, rules)
