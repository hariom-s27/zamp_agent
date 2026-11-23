"""The AP inbox (round 4): every message that reaches the AP address is registered, sorted
by rules, given an owner and a clock, and recorded in the audit chain.

Nothing leaves from here: there is no automatic acknowledgement and no message is sent. A
PDF attachment goes through the nine stages unchanged (`Pipeline.run`, source "email"), and
the message's kind follows what the engine found on the document. A bank-change request
changes nothing: bank details change only after a call-back and approval by two people.
The only human acts are routing an unsorted item and closing an item, both with a name.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from . import cases as case_engine
from . import config, replies, samples
from .mailbox import ParsedMail, SenderCheck, case_tokens, check_sender, find_phrases, parse_eml, strip_quoted
from .masterdata import MasterData
from .pipeline import Pipeline
from .rules import Rules
from .store import Store
from .textutil import fmt_inr

DESK = "Invoice Desk"
STATES = ("sorted", "waiting", "answered", "closed")
STATE_LABEL = {"sorted": "sorted, no person needed", "waiting": "waiting for a person", "answered": "answered",
               "closed": "closed"}
KIND_LABEL = {"invoice": "Invoice", "reply": "Reply to a case", "status_query": "Payment question",
              "bank_change_request": "Bank change request", "statement": "Statement of account",
              "credit_note": "Credit or debit note", "not_an_invoice": "Not an invoice", "other": "Other"}
DOC_KIND = {"tax_invoice": "invoice", "credit_note": "credit_note", "debit_note": "credit_note",
            "statement": "statement"}
DOC_NAME = {"quotation": "quotation", "proforma": "pro-forma invoice", "purchase_order": "purchase order",
            "delivery_challan": "delivery challan", "statement": "statement of account", "receipt": "receipt",
            "sales_order": "sales order", "not_invoice": "document that is not an invoice",
            "credit_note": "credit note", "debit_note": "debit note", "tax_invoice": "tax invoice"}
# invoice numbers quoted in a message: letters, then groups of letters or digits joined by / or -, with a digit
_INVOICE_REF = re.compile(r"(?<![\w/-])([A-Za-z]{2,8}(?:[/-][A-Za-z0-9]{1,10}){1,4})(?![\w/-])")

SENTENCE = {
    "approved": "Invoice read and approved: {amount} to {vendor}, posted for payment. No person needed.",
    "held": "Invoice read and held: {reason}. Case {case_id} opened for {owner}.",
    "held_no_case": "Invoice read and held: {reason}. Waiting for {owner}.",
    "refused": "Invoice refused: {reason}. A note to the sender is drafted for you.",
    "seen": "This file was already received on {date} ({run_id}). Nothing was booked again.",
    "reply": "Reply matched to case {case_id}.",
    "status_query": "Payment question about {n} invoice(s). An answer is drafted from the records for you to approve.",
    "bank_change_request": ("Asks to change a bank account. The desk has changed nothing. Bank details change only "
                            "after a call-back to the number on file and approval by two people."),
    "statement": "Statement of account received. This build does not reconcile statements. Sent to the AP analyst.",
    "credit_note": "Credit note received. Credit and debit notes are applied to the original invoice by a person.",
    "not_an_invoice": "The attachment is a {kind}, not a tax invoice. Nothing was booked.",
    "other": "The rules could not tell what this is. A person should read it.",
    "unsafe": "Too many or too large attachments. Nothing was opened.",
    "off": "The inbox is switched off in rules.yaml (inbox.enabled).",
}


class InboxError(Exception):
    pass


def invoice_refs(text: str) -> list[str]:
    """Invoice-number-like references in a message, each once, leaving out PO numbers and case references."""
    found: list[str] = []
    for match in _INVOICE_REF.finditer(text or ""):
        ref = match.group(1)
        if not any(ch.isdigit() for ch in ref) or ref.upper().startswith(("PO-", "CASE-")):
            continue
        if ref.upper() not in {f.upper() for f in found}:
            found.append(ref)
    return found


DELIVERIES = {"today": "today's mail", "next_morning": "the next morning's mail"}


def bundled_mail(delivery: str = "today") -> list[tuple[str, bytes]]:
    """A demo delivery: today's mail (samples/desk/mail) or the next morning's replies, in delivery order."""
    return [(entry["file"].split("/")[-1], (config.SAMPLES_DIR / entry["file"]).read_bytes())
            for entry in samples.load_mail_manifest(delivery)]


def _day(text: str) -> Optional[date]:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def _received_at(today: date) -> str:
    """The business date with the clock time now, in IST: the demo's mail arrives on the demo's day."""
    now = config.now_utc().astimezone(config.IST)
    return datetime.combine(today, now.timetz()).isoformat()


def _vendor_name(master: MasterData, vendor_id: str, fallback: str = "") -> str:
    vendor = master.vendors.get(vendor_id or "")
    return vendor.legal_name if vendor else (fallback or "the vendor")


def _seen_on(store: Store, run_id: str) -> str:
    for item in store.inbox():
        if run_id in item["run_ids"]:
            day = _day(item["received_at"])
            if day:
                return case_engine.day_text(day)
    row = store.run_row(run_id) or {}
    day = _day(row.get("created_at") or "")
    return case_engine.day_text(day) if day else "an earlier date"


def _sync_cases(store: Store, master: MasterData, rules: Rules, today: date) -> None:
    if rules.cases.enabled:
        case_engine.sync(store, case_engine.master_with_receipts(master, store), rules, today)


def _run_pdfs(store: Store, master: MasterData, rules: Rules, pipeline: Pipeline, mail: ParsedMail,
              today: date) -> dict[str, Any]:
    """Each PDF through the nine stages, once. Returns the kind, owner, state, action, rule, runs and case."""
    pdfs = [a for a in mail.attachments if a.is_pdf]
    outcomes = []
    for att in pdfs:
        earlier = store.original_run_for_file(att.sha256)
        if earlier is not None:
            outcomes.append({"kind": "invoice", "state": "sorted", "owner": DESK, "run_id": "",
                             "action": SENTENCE["seen"].format(date=_seen_on(store, earlier.run_id),
                                                               run_id=earlier.run_id),
                             "doc": "invoice seen before"})
            continue
        result = pipeline.run(att.content, att.name, source="email")
        outcomes.append({"result": result, "run_id": result.run_id})
    if any("result" in o for o in outcomes):
        _sync_cases(store, master, rules, today)
    for o in outcomes:
        result = o.pop("result", None)
        if result is None:
            continue
        inv, d = result.invoice, result.decision
        doc_type = inv.doc_type if inv is not None else "not_invoice"
        if inv is None and result.verdict != "REJECT":
            doc_type = "tax_invoice"
        kind = DOC_KIND.get(doc_type, "not_an_invoice")
        reason = d.reasons[0].title if d and d.reasons else ""
        o.update(kind=kind, doc=DOC_NAME.get(doc_type, doc_type.replace("_", " ")), case_id="")
        if kind == "invoice":
            vendor_id = (result.vendor or {}).get("vendor_id") or ""
            if result.verdict == "APPROVE":
                o.update(state="sorted", owner=DESK, action=SENTENCE["approved"].format(
                    amount=fmt_inr(inv.total),
                    vendor=_vendor_name(master, vendor_id, (result.vendor or {}).get("vendor_name", ""))))
            elif result.verdict == "HOLD":
                case = store.case_for_run(result.run_id)
                owner = case["owner"] if case else (d.owners[0] if d and d.owners else rules.inbox.owners["other"])
                o.update(state="waiting", owner=owner, case_id=case["case_id"] if case else "",
                         action=(SENTENCE["held"].format(reason=reason, case_id=case["case_id"], owner=owner) if case
                                 else SENTENCE["held_no_case"].format(reason=reason, owner=owner)))
            else:
                owner = d.owners[0] if d and d.owners else rules.inbox.owners["other"]
                o.update(state="waiting", owner=owner, action=SENTENCE["refused"].format(reason=reason))
        elif kind == "not_an_invoice":
            o.update(state="waiting", owner=rules.inbox.owners[kind],
                     action=SENTENCE["not_an_invoice"].format(kind=o["doc"]))
        else:
            o.update(state="waiting", owner=rules.inbox.owners[kind], action=SENTENCE[kind])
    lead = next((o for o in outcomes if o["kind"] == "invoice"), outcomes[0])
    docs = ", ".join(dict.fromkeys(o["doc"] for o in outcomes))
    return {**lead, "run_ids": [o["run_id"] for o in outcomes if o.get("run_id")],
            "action": " ".join(dict.fromkeys(o["action"] for o in outcomes)),
            "rule": f"PDF attachment read by the nine stages: the engine found {docs}"}


def _sort(store: Store, master: MasterData, rules: Rules, pipeline: Pipeline, mail: ParsedMail,
          today: date, sender: Optional[SenderCheck] = None) -> dict[str, Any]:
    """The sorting rules of DECISIONS.md D71, first match wins. Returns what to record for the message."""
    ib = rules.inbox
    new_text, _ = strip_quoted(mail.text)
    words = f"{mail.subject}\n{new_text}"
    too_many = len(mail.attachments) > ib.max_attachments
    too_large = any(a.size > rules.intake.max_bytes for a in mail.attachments)
    if too_many or too_large:
        return {"kind": "other", "state": "waiting", "owner": ib.owners["other"], "action": SENTENCE["unsafe"],
                "rule": (f"Safety limit: more than {ib.max_attachments} attachments" if too_many else
                         f"Safety limit: an attachment larger than {rules.intake.max_file_mb:g} MB")}
    bank = find_phrases(words, ib.bank_change_phrases)
    has_pdf = any(a.is_pdf for a in mail.attachments)
    if bank and not has_pdf:
        return {"kind": "bank_change_request", "state": "waiting", "owner": ib.owners["bank_change_request"],
                "action": SENTENCE["bank_change_request"],
                "rule": f"Bank-change wording (\"{bank[0]}\") and no invoice PDF"}
    open_cases = {c["case_id"]: c for c in store.list_cases(open_only=True)}
    token = next((t for t in case_tokens(words) if t in open_cases), None)
    if token:
        case = open_cases[token]
        return {"kind": "reply", "state": "waiting", "owner": case["owner"], "case_id": token,
                "action": SENTENCE["reply"].format(case_id=token),
                "rule": f"Case reference {token} matches an open case"}
    if has_pdf:
        found = _run_pdfs(store, master, rules, pipeline, mail, today)
        if bank:
            found["flags"] = [f"Bank-change wording in the email (\"{bank[0]}\"). Sent to the Vendor master team too."]
        return found
    statement = find_phrases(words, ib.statement_phrases)
    if statement and any(a.is_table for a in mail.attachments):
        return {"kind": "statement", "state": "waiting", "owner": ib.owners["statement"],
                "action": SENTENCE["statement"],
                "rule": f"CSV or XLSX attachment with statement wording (\"{statement[0]}\")"}
    status = find_phrases(words, ib.status_phrases)
    if status:
        if not rules.answers.enabled:
            action = (f"Payment question about {len(invoice_refs(words))} invoice(s). Answers are switched off in "
                      f"rules.yaml (answers.enabled); the AP analyst replies by hand.")
        elif sender is None or sender.result not in ("on_file", "same_domain"):
            action = ("Sender is not on file for any vendor. No invoice details are shared. If this needs an answer, "
                      "call the vendor on the number on file.")
        else:
            action = SENTENCE["status_query"].format(n=len(invoice_refs(words)))
        return {"kind": "status_query", "state": "waiting", "owner": ib.owners["status_query"], "action": action,
                "rule": f"Payment-question wording (\"{status[0]}\")"}
    return {"kind": "other", "state": "waiting", "owner": ib.owners["other"], "action": SENTENCE["other"],
            "rule": "No rule matched"}


def receive(store: Store, master: MasterData, rules: Rules, pipeline: Pipeline, name: str, data: bytes,
            today: date) -> dict[str, Any]:
    """Register, sort and route one received file. A file or Message-ID seen before changes nothing."""
    mail = parse_eml(data)
    earlier = store.find_received(mail.sha256, mail.message_id)
    if earlier is not None:
        return {"status": "already received", "message_id": earlier["message_id"], "file": name,
                "kind": earlier["kind"]}
    store.save_attachment(data)
    sender = check_sender(mail.sender, master, rules)
    found = _sort(store, master, rules, pipeline, mail, today, sender)
    message_id = "IN-" + mail.sha256[:10].upper()
    received_at = _received_at(today)
    due = today + timedelta(days=rules.inbox.answer_due_days)
    flags = found.get("flags", [])
    base = {"sender": mail.sender, "recipient": mail.to, "subject": mail.subject, "body": mail.text,
            "eml_sha256": mail.sha256, "rfc_message_id": mail.message_id, "received_at": received_at,
            "created_at": mail.sent_at or received_at, "vendor_id": sender.vendor_id, "sender_check": sender.result,
            "sender_detail": sender.detail, "due_on": due.isoformat(),
            "attachments_json": json.dumps([{"name": a.name, "mime": a.mime, "sha256": a.sha256, "size": a.size}
                                            for a in mail.attachments])}
    with store.transaction():
        store.add_inbox_message({**base, "message_id": message_id, "kind": found["kind"], "owner": found["owner"],
                                 "state": found["state"], "route_rule": found["rule"], "action": found["action"],
                                 "case_id": found.get("case_id") or "", "flags_json": json.dumps(flags),
                                 "run_ids_json": json.dumps(found.get("run_ids", []))})
        store.append_receipt("inbox", message_id, {
            "actor": "system", "decision": f"message received and sorted as {found['kind']}", "file": name,
            "eml_sha256": mail.sha256, "sender": mail.sender, "received_at": received_at, "message_kind": found["kind"],
            "route_rule": found["rule"], "owner": found["owner"]})
        if flags:                               # a second item, so the bank wording is not lost inside an invoice
            extra_id = message_id + "-VM"
            store.add_inbox_message({**base, "message_id": extra_id, "kind": "bank_change_request",
                                     "owner": rules.inbox.owners["bank_change_request"], "state": "waiting",
                                     "route_rule": "Bank-change wording inside an invoice email",
                                     "action": SENTENCE["bank_change_request"], "flags_json": json.dumps(flags)})
            store.append_receipt("inbox", extra_id, {
                "actor": "system", "decision": "bank-change wording sent to the Vendor master team", "file": name,
                "eml_sha256": mail.sha256, "sender": mail.sender, "received_at": received_at,
                "message_kind": "bank_change_request", "route_rule": "Bank-change wording inside an invoice email",
                "owner": rules.inbox.owners["bank_change_request"]})
    action = found["action"]
    if found["kind"] == "reply":                # the vendor loop: read, check, offer to a person (invoice_desk/replies.py)
        report = replies.handle_reply(store, rules, pipeline.llm, message_id, today)
        if report is not None:
            action = replies.action_text(found["case_id"], report)
    return {"status": "received", "message_id": message_id, "file": name, "kind": found["kind"],
            "owner": found["owner"], "action": action}


def deliver(store: Store, master: MasterData, rules: Rules, pipeline: Pipeline, files: list[tuple[str, bytes]],
            today: date) -> list[dict[str, Any]]:
    """Receive each file in order. With the inbox switched off nothing is read and nothing changes."""
    if not rules.inbox.enabled:
        return []
    return [receive(store, master, rules, pipeline, name, data, today) for name, data in files]


def _named(person: str, what: str) -> str:
    person = (person or "").strip()
    if not person:
        raise InboxError(f"Enter your name: {what} is recorded with the name of the person who did it.")
    return person


def close_item(store: Store, message_id: str, person: str, note: str, today: date) -> None:
    """A person closes a waiting item with their name and a one-line note. One audit entry."""
    person = _named(person, "closing an item")
    item = store.get_message(message_id)
    if item is None or item["direction"] != "in":
        raise InboxError(f"There is no inbox item {message_id}.")
    if item["state"] == "closed":
        raise InboxError(f"{message_id} is already closed by {item['closed_by']}.")
    note = (note or "").strip().splitlines()[0] if (note or "").strip() else ""
    at = datetime.combine(today, config.now_utc().timetz()).isoformat()
    with store.transaction():
        store.update_message(message_id, state="closed", closed_by=person, closed_at=at, closed_note=note)
        store.append_receipt("inbox_closed", message_id, {
            "actor": "human", "reviewer": person, "decision": "inbox item closed", "note": note,
            "message_kind": item["kind"]})


def route_item(store: Store, rules: Rules, message_id: str, kind: str, person: str, today: date) -> None:
    """A person routes an item the rules left unsorted. It changes the kind and owner only, never sends anything."""
    person = _named(person, "routing an item")
    if kind not in rules.inbox.owners:
        raise InboxError(f"'{kind}' is not a kind a person can route to.")
    item = store.get_message(message_id)
    if item is None or item["direction"] != "in":
        raise InboxError(f"There is no inbox item {message_id}.")
    rule = f"Routed by {person} on {case_engine.day_text(today)}"
    with store.transaction():
        store.update_message(message_id, kind=kind, owner=rules.inbox.owners[kind], route_rule=rule,
                             action=f"{KIND_LABEL[kind]}, routed by a person. Next step with "
                                    f"{rules.inbox.owners[kind]}.")
        store.append_receipt("inbox_routed", message_id, {
            "actor": "human", "reviewer": person, "decision": f"inbox item routed as {kind}", "message_kind": kind})


def listing(store: Store, today: date) -> list[dict[str, Any]]:
    """Inbox rows for the page: overdue first, then waiting, then the rest, newest first within each."""
    rows = []
    for item in store.inbox():
        due = _day(item["due_on"])
        overdue = item["state"] == "waiting" and due is not None and today > due
        rows.append({**item, "overdue": overdue})
    rows.sort(key=lambda r: r["received_at"], reverse=True)
    rows.sort(key=lambda r: (not r["overdue"], r["state"] != "waiting"))
    return rows


def figures(store: Store, today: date) -> dict[str, int]:
    items = store.inbox()
    waiting = [i for i in items if i["state"] == "waiting"]
    ages = [(today - d).days for d in (_day(i["received_at"]) for i in waiting) if d]
    return {"received": len({i["eml_sha256"] for i in items}), "sorted": sum(i["state"] == "sorted" for i in items),
            "waiting": len(waiting), "oldest_days": max(ages, default=0)}
