"""Stage 8, Decide: turn the findings into APPROVE, HOLD or REJECT.

`decide()` is a pure function: no file, database, network, clock or model. Everything it
uses arrives as an argument, so the same findings and the same rules always give the
same decision, and every threshold can be tested from both sides. (This is the design of
decide() in my controlplane project, applied to invoices.)

Every sentence a person reads comes from a template filled with the evidence. No model
writes the explanation, so it is exactly reproducible (D18).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from . import textutil as T
from .models import Decision, Finding
from .rules import SEVERITY, Rules

STAGE = "decide"


def classify(findings: list[Finding], rules: Rules) -> list[Finding]:
    """Copy each finding with the action, title and owner that rules.yaml gives its code."""
    out = []
    for f in findings:
        reason = rules.reason(f.code)
        out.append(f.model_copy(update={"action": reason.action, "title": reason.title, "owner": reason.owner}))
    return out


def worst_action(findings: list[Finding], rules: Rules) -> str:
    """'REJECT', 'HOLD', 'NOTE' or '' for a list of findings (used for stage status too)."""
    level = max((SEVERITY[rules.reason(f.code).action] for f in findings), default=0)
    return {3: "REJECT", 2: "HOLD", 1: "NOTE", 0: ""}[level]


def decide(findings: list[Finding], rules: Rules, ctx: Optional[dict[str, Any]] = None) -> Decision:
    """ctx carries display facts only: vendor_name, invoice_number, invoice_date, total,
    po (summary dict), payment_terms_days, company_name, is_invoice."""
    ctx = ctx or {}
    classified = classify(findings, rules)
    total: Optional[Decimal] = T.to_decimal(ctx.get("total"))
    blocking = [f for f in classified if f.action in ("REJECT", "HOLD")]

    # policy: a clean invoice above the ceiling still needs a signature
    ceiling = rules.auto_approve.max_invoice_total_inr
    if not blocking and total is not None and total > ceiling:
        reason = rules.reason("ABOVE_AUTO_APPROVE_LIMIT")
        classified.append(Finding(
            code="ABOVE_AUTO_APPROVE_LIMIT", stage=STAGE, action=reason.action, title=reason.title, owner=reason.owner,
            summary=f"Everything matches, but the invoice total {T.fmt_inr(total)} is above the auto-approval limit of "
                    f"{T.fmt_inr(ceiling, decimals=0)}.",
            expected=f"up to {T.fmt_inr(ceiling, decimals=0)}", observed=T.fmt_inr(total),
            question="Finance controller: all checks passed. Please sign off this payment."))

    rejects = [f for f in classified if f.action == "REJECT"]
    holds = [f for f in classified if f.action == "HOLD"]
    notes = [f for f in classified if f.action == "NOTE"]
    if rejects:
        verdict, reasons, also = "REJECT", rejects, holds
    elif holds:
        verdict, reasons, also = "HOLD", holds, []
    else:
        verdict, reasons, also = "APPROVE", [], []

    vendor = ctx.get("vendor_name") or "the vendor"
    number = ctx.get("invoice_number")
    inv_date = ctx.get("invoice_date")
    po = ctx.get("po") or {}
    ref = f"invoice {number}" if number else "the invoice"
    if inv_date:
        ref += f" dated {T.fmt_date(inv_date)}"
    owners = list(dict.fromkeys(f.owner for f in reasons if f.owner))
    questions = [f.question for f in reasons if f.question]

    if verdict == "APPROVE":
        headline = f"APPROVE: pay {T.fmt_inr(total)} to {vendor}"
        parts = []
        if po:
            parts.append(f"{_cap(ref)} matches {po.get('po_number')}: {po.get('lines_matched')} of "
                         f"{po.get('lines_total')} line(s) agree with the PO on {po.get('matched_on') or 'item, quantity and price'}.")
            left = T.to_decimal(po.get("remaining_after"))
            if left is not None:
                tail = ", which is now fully billed." if abs(left) < 1 else f"; {T.fmt_inr(left)} remains open." \
                    if left > 0 else f", which is {T.fmt_inr(-left)} over the PO value and inside the tolerance."
                parts.append(f"After this invoice {T.fmt_inr(po.get('billed_after'))} of "
                             f"{T.fmt_inr(po.get('po_total'))} is billed on the PO" + tail)
        parts.append("The figures add up, the vendor is approved, the bank account matches the vendor master and "
                     "no duplicate was found.")
        parts += [f"Note: {n.summary}" for n in notes]
        explanation = " ".join(parts)
        due = _due_date(inv_date, ctx.get("payment_terms_days"))
        next_action = "Posted to the ledger for payment" + (f"; due {T.fmt_date(due)}." if due else ".")
    elif verdict == "HOLD":
        headline = f"HOLD: {reasons[0].title.lower()}" if len(reasons) == 1 else f"HOLD: {len(reasons)} points need a person"
        amount = f" for {T.fmt_money(total, ctx.get('currency'))}" if total is not None else ""
        lead = f"{_cap(ref)} from {vendor}{amount} is not paid yet. " if ctx.get("is_invoice", True) \
            else (f"{_cap(ref)} from {vendor}{amount} is waiting for a person. " if number or total is not None
                  else "This document is waiting for a person. ")
        explanation = lead + " ".join(f"({i}) {f.summary}" for i, f in enumerate(reasons, 1)) if len(reasons) > 1 \
            else lead + reasons[0].summary
        next_action = "Waiting in the review queue for: " + (", ".join(owners) or "AP analyst") + "."
    else:
        headline = f"REJECT: {reasons[0].title.lower()}"
        explanation = " ".join(f.summary for f in reasons) + " Nothing was posted and nothing will be paid on this document."
        next_action = "Not booked." + (" Reply to the sender with the message drafted below." if any(f.vendor_issue for f in reasons) else "")

    return Decision(verdict=verdict, headline=headline, explanation=explanation, reasons=reasons, also_noted=also,
                    notes=notes, owners=owners, questions=questions, next_action=next_action,
                    vendor_draft=_vendor_note(verdict, reasons, ctx))


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _due_date(inv_date: Any, terms: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(inv_date)[:10]) + timedelta(days=int(terms))
    except (TypeError, ValueError):
        return None


def _vendor_note(verdict: str, reasons: list[Finding], ctx: dict) -> Optional[str]:
    """A note to the vendor, drafted for a person to send, when the vendor has to act."""
    issues = [f.vendor_issue for f in reasons if f.vendor_issue]
    if not issues:
        return None
    greeting = f"Dear {ctx['vendor_name']} accounts team," if ctx.get("vendor_name") else "Dear Sir or Madam,"
    number, inv_date = ctx.get("invoice_number"), ctx.get("invoice_date")
    po = (ctx.get("po") or {}).get("po_number")
    if ctx.get("is_invoice", True):
        what = f"invoice {number}" if number else "your invoice"
        if inv_date:
            what += f" dated {T.fmt_date(inv_date)}"
        total = T.to_decimal(ctx.get("total"))
        intro = f"We received {what}" + (f" for {T.fmt_money(total, ctx.get('currency'))}" if total is not None else "") + \
            (f" against {po}" if po else "") + "."
        subject = f"{_cap(what)}: action needed before payment"
    else:   # not a payable invoice: a quotation, an unreadable file
        intro = "We received a document from you in our invoice inbox."
        subject = "Document received in our invoice inbox: not booked"
    if verdict == "REJECT":
        middle = "We have not booked it, for this reason:" if len(issues) == 1 else "We have not booked it, for these reasons:"
        ask = "If it was sent by mistake, no action is needed. Otherwise please reply with a corrected document."
    else:
        middle = "Before we can process it we need one point resolved:" if len(issues) == 1 else \
            "Before we can process it we need these points resolved:"
        ask = "Please reply to this email with a corrected invoice or a clarification, quoting the reference above."
    lines = [f"Subject: {subject}", "", greeting, "", intro, middle, ""]
    lines += [f"  {i}. {issue[0].upper() + issue[1:]}." for i, issue in enumerate(issues, 1)]
    lines += ["", ask, "", "Regards,", "Accounts Payable", ctx.get("company_name") or ""]
    return "\n".join(lines).strip()


def resubmission_note(ctx: Optional[dict], received_on: str, status: str) -> Optional[str]:
    """A short reply for a vendor who sent an invoice we already have. `status` is a phrase
    such as 'it is booked for payment'."""
    ctx = ctx or {}
    number, inv_date = ctx.get("invoice_number"), ctx.get("invoice_date")
    what = f"invoice {number}" if number else "your invoice"
    if inv_date:
        what += f" dated {T.fmt_date(inv_date)}"
    greeting = f"Dear {ctx['vendor_name']} accounts team," if ctx.get("vendor_name") else "Dear Sir or Madam,"
    lines = [f"Subject: {_cap(what)}: already received", "", greeting, "",
             f"We have received {what} again. We already had it (first received {received_on}), and {status}.",
             "The second copy has not been booked, so there is nothing you need to do. Please do not send it again.",
             "", "Regards,", "Accounts Payable", ctx.get("company_name") or ""]
    return "\n".join(lines).strip()


def note_must_keep(ctx: Optional[dict], note: Optional[str]) -> list[str]:
    """The references a reworded vendor note has to carry over unchanged: the invoice number,
    the PO number and the invoice total, when the original note mentions them."""
    ctx = ctx or {}
    wanted = [ctx.get("invoice_number"), (ctx.get("po") or {}).get("po_number")]
    total = T.to_decimal(ctx.get("total"))
    if total is not None:
        wanted.append(T.fmt_inr(total, symbol=""))
    return [str(w) for w in wanted if w and T.quote_form(str(w)) in T.quote_form(note or "")]
