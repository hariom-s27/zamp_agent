"""The vendor loop (round 4): a reply to a case's question is read by AI, every value is
checked in code, and a value that passes all eight checks is offered to a person.

Nothing here approves, posts or changes an invoice. An offered invoice number pre-fills the
review form, where a named person confirms it and `review.apply_review` still refuses a
number already on record for that vendor. A PO number is shown beside the one on the run and
never changes it. Dates and totals are evidence only. The reader has no field for a bank
account, so a bank change cannot enter through a reply.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from typing import Any, Optional

from pydantic import ValidationError

from . import cases as case_engine
from . import textutil as T
from .llm import MAX_CHARS, LlmAssist, ReplyAnswer, id_in_quote, value_in_quote
from .mailbox import strip_quoted
from .rules import Rules
from .store import Store

DESK = "Invoice Desk"
SENDER_OK = ("on_file", "same_domain")
KIND_OF_VALUE = {"invoice_number": "id", "po_number": "id", "invoice_date": "date", "total": "amount"}
VALUE_LABEL = {"invoice_number": "the invoice number", "po_number": "the PO number", "invoice_date": "the invoice date",
               "total": "the total"}
PERSON = "A person should read this reply."


def _value_checks(values: list[dict], text: str, answer: ReplyAnswer, intent: str, sender_ok: bool,
                  rules: Rules) -> list[dict]:
    """Checks 3 to 8 for every value. Checks 1 and 2 (schema, normal end) are done before this is called."""
    distinct: dict[str, set[str]] = defaultdict(set)
    for v in values:
        distinct[v["name"]].add(T.quote_form(v["value"]))
    checked = []
    for v in values:
        why = ""
        if not (answer.sure and intent == "gives_value"):
            why = "the AI was not sure, or the reply does not give a value"                       # 3
        elif not T.contains_snippet(text, v["quote"], min_chars=4):
            why = "the sentence it quotes is not in the reply"                                    # 4
        elif not value_in_quote(KIND_OF_VALUE[v["name"]], v["value"], v["quote"]):
            why = "the value is not in the sentence it quotes"                                    # 5
        elif len(distinct[v["name"]]) != 1:
            why = f"the reply gives more than one value for {VALUE_LABEL[v['name']]}"             # 6
        elif not sender_ok:
            why = "the reply does not come from the vendor's address or domain on file"           # 7
        elif v["name"] not in rules.replies.offer:
            why = f"{VALUE_LABEL[v['name']]} is evidence only and is never offered"               # 8
        checked.append({**v, "ok": not why, "why": why})
    return checked


def read_reply(llm: LlmAssist, rules: Rules, text: str, sender_ok: bool) -> dict[str, Any]:
    """What the reply says and what may be offered. Never raises; the reason for every refusal is in the report."""
    report: dict[str, Any] = {"outcome": "person", "intent": "", "summary": "", "values": [], "offered": {},
                              "reason": "", "sender_ok": sender_ok}
    if not rules.replies.ai_read:
        report["reason"] = "AI reading of replies is switched off in rules.yaml (replies.ai_read)."
        return report
    if not text.strip():
        report["reason"] = "the reply has no text of its own."
        return report
    asked: dict[str, Any] = {}
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    answer = llm.ask("reply_read", key, text[:MAX_CHARS], asked, force=llm.force)   # force: re-recording only
    report["ai"] = {k: asked.get(k) for k in ("source", "model", "recorded_at", "prompt_version", "note", "error")
                    if asked.get(k)}
    if answer is None:
        why = asked.get("error") or asked.get("note") or "no answer"
        if why.startswith("no recorded answer"):
            why = "no recorded answer; record the bundled replies once with python -m scripts.record_reply_fixtures"
        report["reason"] = f"no AI answer is available for this reply ({why})."
        return report
    if answer.stop_reason != "end_turn":                                                         # 2
        report["reason"] = f"the AI answer did not end normally ({answer.stop_reason or 'no stop reason'}), so it is not used."
        return report
    try:
        parsed = ReplyAnswer.model_validate(answer.data)                                         # 1
    except ValidationError:
        report["reason"] = "the AI answer did not match the expected form, so it is not used."
        return report
    intent = parsed.intent.strip().lower()
    summary = parsed.summary.strip()
    report["intent"] = intent
    report["summary"] = summary if len(summary.split()) <= 30 else ""
    values = [v.model_dump() for v in parsed.values]
    checked = _value_checks(values, text, parsed, intent, sender_ok, rules)
    report["values"] = checked
    offered = {v["name"]: {"value": v["value"].strip(), "quote": v["quote"].strip()} for v in checked if v["ok"]}
    if intent == "out_of_office":
        report["outcome"] = "out_of_office"
    elif intent == "gives_value" and offered:
        report.update(outcome="offered", offered=offered)
    elif intent == "gives_value":
        first = next((v["why"] for v in checked if v["why"]), "the reply states no value")
        report["reason"] = first[0].upper() + first[1:] + "."
    elif intent in ("confirms", "disputes", "asks_question"):
        report["outcome"] = intent
    elif intent == "corrected_document":
        report["reason"] = "A corrected document is handled by hand in this build."
    else:
        report["reason"] = "The AI could not tell what the reply says."
    if report["outcome"] == "person" and not report["reason"]:
        report["reason"] = "The reply could not be read with certainty."
    return report


def action_text(case_id: str, report: dict) -> str:
    """The line on the inbox row and the case card."""
    head = f"Reply matched to case {case_id}."
    if report["outcome"] == "offered":
        parts = [f"{VALUE_LABEL[name]} {o['value']}" for name, o in report["offered"].items()]
        return f"{head} The reply states {' and '.join(parts)}. Confirm it in the review queue."
    if report["outcome"] == "out_of_office":
        return f"{head} An automatic out-of-office reply. The case keeps waiting on the vendor."
    if report["outcome"] in ("confirms", "disputes", "asks_question"):
        what = {"confirms": "The vendor confirms", "disputes": "The vendor disputes",
                "asks_question": "The vendor asks a question"}[report["outcome"]]
        return f"{head} {what}: {report['summary'] or 'see the reply'}"
    return f"{head} {PERSON} Reason: {report['reason']}"


def handle_reply(store: Store, rules: Rules, llm: LlmAssist, message_id: str, today: date) -> Optional[dict]:
    """Attach a received reply to its case, read it, and record what is offered. Returns the reader's report."""
    item = store.get_message(message_id)
    case = store.get_case(item["case_id"]) if item and item["case_id"] else None
    if case is None:
        return None
    result = store.get_run(case["run_id"])
    case_vendor = ((result.vendor or {}).get("vendor_id") or "") if result else ""
    sender_ok = item["sender_check"] in SENDER_OK and bool(case_vendor) and item["vendor_id"] == case_vendor
    if not rules.replies.require_sender_match:
        sender_ok = True
    day = case_engine.day_text(today)
    with store.transaction():
        store.add_case_event(case["case_id"], "reply_received", DESK,
                             f"Reply received from {item['sender'] or 'an unknown sender'} on {day}.",
                             {"message_id": message_id})
        store.append_receipt("reply", case["run_id"], {
            "actor": "system", "decision": "vendor reply received and matched to the case", "message_id": message_id,
            "sender": item["sender"], "eml_sha256": item["eml_sha256"], "case_id": case["case_id"]})
    if not rules.replies.enabled:
        return None
    text, _ = strip_quoted(item["body"] or "")
    report = read_reply(llm, rules, text, sender_ok)
    action = action_text(case["case_id"], report)
    owner = case["owner"]
    with store.transaction():
        store.update_message(message_id, read_json=json.dumps(report, default=str), action=action)
        if report["outcome"] == "out_of_office":
            store.add_case_event(case["case_id"], "reply_read", "AI", "Automatic out-of-office reply. Still waiting "
                                 "on the vendor.", {"message_id": message_id})
            return report
        fields: dict[str, Any] = {"waiting_on": owner}
        if case["state"] == "WAITING":
            fields["state"] = case_engine.move(case["state"], "reply")
        if report["outcome"] == "offered":
            fields["proposed_json"] = json.dumps({"from_message": message_id, **report["offered"]})
            store.add_case_event(case["case_id"], "reply_read", "AI",
                                 action.removeprefix(f"Reply matched to case {case['case_id']}. "),
                                 {"offered": report["offered"]})
            store.append_receipt("reply_read", case["run_id"], {
                "actor": "AI", "decision": "reply read; values offered to a person, nothing applied",
                "message_id": message_id, "offered": report["offered"], "source": report.get("ai", {}).get("source", "")})
        else:
            kind = "reply_needs_a_person" if report["outcome"] == "person" else "reply_read"
            store.add_case_event(case["case_id"], kind, "AI" if kind == "reply_read" else DESK,
                                 action.removeprefix(f"Reply matched to case {case['case_id']}. "),
                                 {"reason": report["reason"]})
        store.update_case(case["case_id"], **fields)
        store.update_message(message_id, state="waiting", owner=owner)
    return report


def offer_for_run(store: Store, run_id: str) -> dict:
    """What a vendor's reply offers for this run's review form: {name: {value, quote}}, or {}."""
    case = store.case_for_run(run_id)
    if not case or not case.get("proposed_json"):
        return {}
    try:
        data = json.loads(case["proposed_json"])
    except ValueError:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("value") and v.get("quote")}
