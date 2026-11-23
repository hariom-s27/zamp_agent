"""Payment-status answers (round 4): "where is my payment?", answered from the records.

Every sentence comes from one record (a ledger entry, a run, a case or an approved payment
run), and the record is listed beside the draft. The draft is written by rules; a person
approves it or it is not sent, and it always goes to the vendor's address on file. An answer
never says "paid" (the desk cannot know), never contains a bank detail, never mentions another
vendor and never names the reason for a fraud check.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from email import policy as email_policy
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from typing import Any, Optional

from . import cases as case_engine
from . import config, payments
from .inbox import invoice_refs
from .llm import LlmAssist
from .mailbox import strip_quoted
from .masterdata import MasterData
from .rules import Rules
from .store import Store
from .textutil import fmt_inr, invoice_no_key

SENDER_OK = ("on_file", "same_domain")
REFUSED = "Sender is not on file for any vendor. No invoice details are shared."
REFUSED_LONG = REFUSED + " If this needs an answer, call the vendor on the number on file."
OFF = "Answers are switched off in rules.yaml (answers.enabled); the AP analyst replies by hand."
SEND_NOTE = ("In this build, send means the email is written to the outbox folder and recorded. No mail server is "
             "connected.")
INTERNAL_ONLY = {"BANK_DETAILS_CHANGED"}       # a fraud check is never named to the vendor


class AnswerError(Exception):
    pass


def _d(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _when(value: Any) -> str:
    day = _d(value)
    return day.strftime("%d %b %Y") if day else ""


def _records(store: Store, vendor_id: str) -> dict[str, dict]:
    """invoice key -> what the books hold for this vendor: the ledger entry first, else the latest run."""
    found: dict[str, dict] = {}
    for row in store.list_runs():                                   # newest first
        if row.get("vendor_id") == vendor_id and row.get("invoice_no") and not row.get("resubmission_of"):
            found.setdefault(invoice_no_key(row["invoice_no"]), {"run": row})
    for entry in store.ledger_entries(vendor_id=vendor_id):
        key = entry["invoice_key"] or invoice_no_key(entry["invoice_no"])
        if key:
            found.setdefault(key, {})["entry"] = entry
    return found


def _sentence_for(store: Store, master: MasterData, rules: Rules, vendor_id: str, rec: dict, today: date,
                  proposal: Optional[dict]) -> dict[str, Any]:
    entry, run = rec.get("entry"), rec.get("run")
    run_row = run or (store.run_row(entry["run_id"]) if entry and entry.get("run_id") else None)
    result = store.get_run(run_row["run_id"]) if run_row else None
    number = (entry or {}).get("invoice_no") or (run_row or {}).get("invoice_no") or ""
    inv_date = (entry or {}).get("invoice_date") or (run_row or {}).get("invoice_date") or ""
    total = (entry or {}).get("total") if entry else (run_row or {}).get("total")
    fact = {"invoice_no": number, "invoice_date": _d(inv_date), "total": str(total or ""), "overdue": None}
    locked = store.locked_for_payment()
    status = (entry or {}).get("status") or ""
    if entry and entry["ledger_id"] in locked:
        pr = store.payment_run(locked[entry["ledger_id"]])
        fact.update(record=f"payment run {pr['run_id']}", says="on an approved payment run",
                    sentence=(f"Approved and included in the payment run approved on {_when(pr['run_date'])}. "
                              f"Payment is released by our Finance team through the bank."))
        return fact
    if status == "POSTED":
        blocked = {ln["ledger_id"] for ln in (proposal or {}).get("blocked", [])}
        if entry["ledger_id"] in blocked:
            fact.update(record=f"ledger {entry['ledger_id']}", says="posted; vendor record waiting for verification",
                        sentence=("Approved. Payment is waiting for a verification on your vendor record. Our team "
                                  "will contact you on the phone number we hold for you."))
            return fact
        line = payments._line(store, case_engine.master_with_receipts(master, store), rules, entry, {}, today, today)
        fact.update(record=f"ledger {entry['ledger_id']}", says="posted for payment",
                    sentence=f"Approved. Due for payment on {_when(line['pay_by'])}.")
        if line["pay_by"] < today:
            fact["overdue"] = {"pay_by": line["pay_by"], "legal": line["basis"] == "legal" or bool(
                case_engine.statutory_clock(master.vendors.get(vendor_id), [], _d(inv_date), total, rules, today))}
        return fact
    verdict = (run_row or {}).get("verdict") or ""
    review = (run_row or {}).get("review_status") or ""
    reasons = result.decision.reasons if result and result.decision else []
    issue = next((f.vendor_issue for f in reasons if f.vendor_issue and f.code not in INTERNAL_ONLY), None)
    if verdict == "HOLD" and review in ("", "PENDING"):
        case = store.case_for_run(run_row["run_id"])
        ref = case["case_id"] if case else run_row["run_id"]
        internal = any(f.code in INTERNAL_ONLY for f in reasons) or not issue
        received = _when(case["opened_on"]) if case else _when(today)
        fact.update(record=f"case {ref}", says="on hold" + (" (internal check)" if internal else " (vendor to act)"),
                    sentence=(f"Received on {received} and being checked. Reference {ref}." if internal else
                              f"On hold. {issue.strip().rstrip('.')}. Reference {ref}."))
        return fact
    if verdict == "REJECT" or status == "REJECTED" or review == "REJECTED":
        why = (issue or (reasons[0].title if reasons else "")).strip()
        fact.update(record=f"run {run_row['run_id']}" if run_row else f"ledger {entry['ledger_id']}",
                    says="not accepted", sentence=f"Not accepted. {why}".strip())
        return fact
    fact.update(record=f"run {run_row['run_id']}" if run_row else "-", says="received",
                sentence=f"Received on {_when(inv_date) or _when(today)} and being checked.")
    return fact


def status_line(store: Store, master: MasterData, rules: Rules, vendor_id: str, number: str, today: date) -> dict:
    """What the records say about one invoice number of one vendor."""
    rec = _records(store, vendor_id).get(invoice_no_key(number))
    if rec is None:
        return {"invoice_no": number, "invoice_date": None, "total": "", "record": "-", "says": "not on our records",
                "overdue": None,
                "sentence": (f"We have no record of receiving invoice {number}. Please send a copy to "
                             f"{rules.company.ap_email}.")}
    return _sentence_for(store, master, rules, vendor_id, rec, today, payments.propose(store, master, rules, today)
                         if rules.payments.enabled else None)


def _find(store: Store, rules: Rules, vendor_id: str, text: str) -> tuple[list[tuple[str, Optional[dict]]], bool]:
    records = _records(store, vendor_id)
    flat = invoice_no_key(text)
    hits: list[tuple[str, Optional[dict]]] = []
    for key, rec in sorted(records.items(), key=lambda kv: flat.find(kv[0])):
        if key and len(key) >= 4 and key in flat:
            hits.append((key, rec))
    known = {k for k, _ in hits}
    for ref in invoice_refs(text):
        if invoice_no_key(ref) not in known and not any(invoice_no_key(ref) in k for k in known):
            hits.append((ref, None))
    return hits, bool(hits)


def draft(store: Store, master: MasterData, rules: Rules, item: dict, today: date,
          llm: Optional[LlmAssist] = None) -> dict[str, Any]:
    """The rule-written answer to one payment question, with the records it is built on."""
    if not rules.answers.enabled:
        return {"status": "off", "text": "", "facts": [], "flags": [], "reason": OFF}
    if item["sender_check"] not in SENDER_OK or not item["vendor_id"]:
        return {"status": "refused", "text": "", "facts": [], "flags": [], "reason": REFUSED_LONG}
    vendor = master.vendors.get(item["vendor_id"])
    if vendor is None or not vendor.email:
        return {"status": "refused", "text": "", "facts": [], "flags": [], "reason": REFUSED_LONG}
    words = f"{item['subject']}\n{strip_quoted(item['body'] or '')[0]}"
    hits, quoted = _find(store, rules, vendor.vendor_id, words)
    proposal = payments.propose(store, master, rules, today) if rules.payments.enabled else None
    facts = []
    if quoted:
        for key, rec in hits:
            facts.append(_sentence_for(store, master, rules, vendor.vendor_id, rec, today, proposal) if rec else
                         status_line(store, master, rules, vendor.vendor_id, key, today))
    else:
        open_recs = []
        for rec in _records(store, vendor.vendor_id).values():
            entry, run = rec.get("entry"), rec.get("run")
            if (entry and entry["status"] in ("POSTED", "PENDING")) or (run and run["verdict"] == "HOLD"):
                open_recs.append(rec)
        open_recs.sort(key=lambda r: ((r.get("entry") or {}).get("invoice_date") or (r.get("run") or {}).get(
            "invoice_date") or ""), reverse=True)
        facts = [_sentence_for(store, master, rules, vendor.vendor_id, rec, today, proposal)
                 for rec in open_recs[:rules.answers.max_listed]]
    lines = []
    for f in facts:
        head = f["invoice_no"]
        if f["invoice_date"]:
            head += f" dated {f['invoice_date'].strftime('%d %b %Y')}"
        if f["total"]:
            head += f" for {fmt_inr(f['total'], symbol='Rs ')}"
        lines.append(f"{head}: {f['sentence']}")
    if not lines:
        lines.append("We have no open invoices from you in our records.")
    subject = item["subject"] if item["subject"].upper().startswith("RE:") else f"RE: {item['subject'] or 'Payment status'}"
    body = (f"Dear {vendor.legal_name} accounts team,\n\nHere is where your invoices stand in our records on "
            f"{case_engine.day_text(today)}.\n\n" + "\n\n".join(lines) +
            f"\n\nAccounts Payable, {rules.company.name}\n{rules.company.ap_email}\n")
    text = f"Subject: {subject}\n\n{body}"
    flags = []
    for f in facts:
        if f.get("overdue"):
            legal = " A micro or small supplier: the legal pay-by date has passed." if f["overdue"]["legal"] else ""
            flags.append(f"Finance controller: {f['invoice_no']} is overdue in our records (pay-by "
                         f"{_when(f['overdue']['pay_by'])}).{legal}")
    reworded = False
    if rules.answers.ai_reword and llm is not None:
        keep = [f["invoice_no"] for f in facts] + [_when(f.get("invoice_date")) for f in facts]
        report = llm.draft_vendor_note(text, [k for k in keep if k])
        if report.get("accepted"):
            text, reworded = report["text"], True
    body = text.split("\n\n", 1)[1] if "\n\n" in text else body
    return {"status": "drafted", "text": text, "subject": subject, "body": body, "facts": facts, "flags": flags,
            "recipient": vendor.email, "reworded": reworded, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def approve(store: Store, master: MasterData, rules: Rules, message_id: str, approver: str, shown_sha256: str,
            today: date, llm: Optional[LlmAssist] = None) -> dict[str, Any]:
    """A named person approves the answer as shown. It is written to the outbox, addressed to the address on file."""
    approver = (approver or "").strip()
    if not approver:
        raise AnswerError("Enter your name: nothing is sent to a vendor without a named person approving it.")
    item = store.get_message(message_id)
    if item is None or item["kind"] != "status_query":
        raise AnswerError(f"{message_id} is not a payment question.")
    if item["state"] in ("answered", "closed"):
        raise AnswerError(f"{message_id} is already {item['state']}.")
    d = draft(store, master, rules, item, today, llm)
    if d["status"] != "drafted":
        raise AnswerError(d["reason"])
    if d["sha256"] != shown_sha256:
        raise AnswerError("The records have changed since this answer was shown. Look at it again.")
    sender = rules.company.ap_email
    mail = EmailMessage()
    mail["From"], mail["To"], mail["Subject"] = sender, d["recipient"], d["subject"]
    now_time = config.now_utc().time()
    mail["Date"] = format_datetime(datetime.combine(today, now_time, tzinfo=timezone.utc))
    mail["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)
    if item["rfc_message_id"]:
        mail["In-Reply-To"] = item["rfc_message_id"]
    mail.set_content(d["body"], cte="8bit")           # UTF-8 as 8bit with CRLF, as the outbox writes every message
    data = mail.as_bytes(policy=email_policy.SMTP)
    digest = hashlib.sha256(data).hexdigest()
    out_id = f"{message_id}-ANS"
    path = case_engine.outbox_dir(store) / f"{out_id}.eml"
    with path.open("xb") as handle:
        handle.write(data)
    facts = [{k: (str(v) if k == "invoice_date" and v else v) for k, v in f.items() if k != "overdue"} for f in d["facts"]]
    try:
        with store.transaction():
            store.add_message({"message_id": out_id, "case_id": "", "direction": "out", "status": "written",
                               "approved_by": approver, "sender": sender, "recipient": d["recipient"],
                               "subject": d["subject"], "body": d["body"], "eml_sha256": digest})
            store.set_answer_facts(out_id, message_id, facts)
            store.update_message(message_id, state="answered")
            store.append_receipt("answer", message_id, {
                "actor": "human", "reviewer": approver, "decision": "payment-status answer approved and written to the outbox",
                "to": d["recipient"], "message_id": out_id, "eml_sha256": digest,
                "invoices": [f["invoice_no"] for f in d["facts"]]})
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"message_id": out_id, "path": path, "to": d["recipient"], "eml_sha256": digest}

