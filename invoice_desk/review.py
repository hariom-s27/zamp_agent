"""Human review of held invoices.

A hold ends when a named person approves or rejects it. A held document that the system
did not book at all (a foreign-currency invoice, a credit note, a file too large to read)
cannot be approved here: it is processed by hand and then closed as handled. Every
decision made here is written to the same receipt chain as the machine decisions, marked
with the actor, so the audit trail shows who did what.

Three controls sit on an approval, because a hold is only as strong as what happens when
someone clears it:

  * the purchase order is checked again at the moment of approval. Held invoices do not
    use PO balance, so two of them can each fit the PO on their own and overbill it
    together. An approval that would take the PO over its quantity or value is refused,
    the refusal is recorded, and the hold stays with the fresh reason on it;
  * a changed bank account needs two different people (rules.yaml, review). The first
    confirms a call-back to the number on file and records the call. Nothing is payable
    and the new account is not trusted until a second person has approved;
  * an invoice with no number can be approved only once the reviewer enters the number
    obtained from the vendor, because the ledger needs it for duplicate control.

All the writes of one review commit together or not at all.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Optional

from . import textutil as T
from .decide import decide
from .masterdata import MasterData, load_master
from .models import Finding, RunResult
from .po_match import breaks_limit
from .rules import Rules, current_rules
from .store import Store

ZERO = Decimal("0")
FIRST = "FIRST_APPROVAL"


class ReviewError(Exception):
    pass


def _person(name: str) -> str:
    """'N. Rao', 'n rao' and ' N.RAO ' are one person."""
    return re.sub(r"[^a-z0-9]", "", (name or "").casefold())


def review_state(store: Store, run_id: str) -> dict:
    """Where a held run stands: the first approval of a bank change, if one was given."""
    first = None
    for row in store.reviews(run_id):
        if row["decision"] == FIRST:
            first = row
    return {"first_approval": first}


def required_checks(reason_codes: set[str], second: bool = False) -> dict[str, str]:
    """What the reviewer must supply before an approval is accepted: {check: wording}.
    `second` is True when a first approval of a bank change is already on record."""
    needs: dict[str, str] = {}
    if "BANK_DETAILS_CHANGED" in reason_codes:
        if second:
            needs["bank_second_check"] = "I have read the call record above and I approve the change of bank account."
        else:
            needs["bank_verified"] = ("I called the vendor on the number in the vendor master, they stated the new "
                                      "account themselves, and it matches this invoice.")
    if "MISSING_INVOICE_NUMBER" in reason_codes:
        needs["invoice_number"] = "Invoice number obtained from the vendor"
    return needs


def po_position(store: Store, master: MasterData, rules: Rules, entry: Optional[dict]) -> dict:
    """Would posting this ledger entry overbill its purchase order, given what is posted
    right now? Uses the same tolerances as the PO match stage. Returns the PO number, a
    list of problems (code, text, numbers) and rows for the reviewer's screen."""
    out: dict[str, Any] = {"po_number": (entry or {}).get("po_number") or "", "problems": [], "rows": []}
    po = master.pos.get(out["po_number"]) if out["po_number"] else None
    if po is None or entry is None:
        return out
    tol = rules.tolerances
    usage = store.po_usage(po.po_number)
    mine = entry.get("ledger_id")
    posted_lines: dict[int, dict] = {}
    posted_amount = ZERO
    for other in usage["invoices"]:                       # everything posted, except this entry itself
        if other["status"] != "POSTED" or other["ledger_id"] == mine:
            continue
        posted_amount += other["taxable"] or ZERO
        for ln in other["lines"]:
            slot = posted_lines.setdefault(ln["po_line_no"], {"qty": ZERO, "amount": ZERO})
            slot["qty"] += ln["qty"] or ZERO
            slot["amount"] += ln["amount"] or ZERO
    billed: dict[int, Decimal] = {}                       # this invoice, per PO line: two lines may bill the same one
    for ln in entry.get("lines") or []:
        po_line = po.line(ln["po_line_no"]) if ln.get("po_line_no") is not None else None
        if po_line is None or po_line.match_basis != "QTY" or ln.get("qty") is None:
            continue
        billed[po_line.line_no] = billed.get(po_line.line_no, ZERO) + ln["qty"]
    for line_no, qty in billed.items():
        po_line = po.line(line_no)
        before = posted_lines.get(line_no, {"qty": ZERO})["qty"]
        after = before + qty
        limit = po_line.qty_ordered * (1 + tol.quantity_over_pct / 100)
        over = after > limit
        out["rows"].append({"PO line": f"{po_line.line_no} {po_line.description[:44]}", "Ordered": T.fmt_num(po_line.qty_ordered),
                            "Posted so far": T.fmt_num(before), "This invoice": T.fmt_num(qty),
                            "After approval": T.fmt_num(after), "Result": "over the PO" if over else "fits"})
        if over:
            out["problems"].append({
                "code": "QTY_OVER_PO", "po_line": po_line.line_no,
                "text": f"line {po_line.line_no} ({po_line.description}) would reach {T.fmt_num(after)} of "
                        f"{T.fmt_num(po_line.qty_ordered)} {po_line.uom} ({T.fmt_num(before)} already posted plus "
                        f"{T.fmt_num(qty)} on this invoice)",
                "expected": f"at most {T.fmt_num(po_line.qty_ordered - before)} {po_line.uom} still open",
                "observed": f"{T.fmt_num(qty)} {po_line.uom} on this invoice",
                "evidence": {"po_line": po_line.line_no, "ordered": str(po_line.qty_ordered),
                             "billed_before": str(before), "this_invoice": str(qty)}})
    this = entry.get("taxable") or sum((ln.get("amount") or ZERO for ln in entry.get("lines") or []), ZERO)
    after_amount = posted_amount + this
    over_amount = after_amount - po.total
    value_over = breaks_limit(over_amount, po.total, tol.po_total)
    out["rows"].append({"PO line": "PO value", "Ordered": T.fmt_inr(po.total), "Posted so far": T.fmt_inr(posted_amount),
                        "This invoice": T.fmt_inr(this), "After approval": T.fmt_inr(after_amount),
                        "Result": f"{T.fmt_inr(over_amount)} over the PO" if value_over else "fits"})
    if value_over:
        out["problems"].append({
            "code": "OVER_PO_BALANCE",
            "text": f"billing would reach {T.fmt_inr(after_amount)} against a PO value of {T.fmt_inr(po.total)} "
                    f"({T.fmt_inr(posted_amount)} already posted plus {T.fmt_inr(this)} on this invoice)",
            "expected": f"at most {T.fmt_inr(po.total - posted_amount)} still open", "observed": T.fmt_inr(this),
            "evidence": {"po_total": str(po.total), "billed_before": str(posted_amount), "this_invoice": str(this),
                         "over": str(over_amount)}})
    out["buyer"] = po.buyer
    return out


def _refuse_overbilling(store: Store, rules: Rules, result: RunResult, position: dict, reviewer: str, note: str) -> str:
    """Record a refused approval: the hold on file gets the reasons as they stand today, and
    the attempt joins the audit trail. Returns the message for the reviewer."""
    po, buyer = position["po_number"], position.get("buyer") or "the PO owner"
    fresh = [Finding(
        code=p["code"], stage="review",
        summary=f"Checked again when approval was attempted: on {po}, {p['text']}.",
        expected=p["expected"], observed=p["observed"], evidence=p["evidence"],
        question=f"PO owner {buyer}: amend {po} if the supply is valid, or have the vendor correct the invoice. "
                 f"The invoice cannot be approved past the PO.") for p in position["problems"]]
    codes = {f.code for f in fresh}
    old = result.decision
    kept = [f for f in old.reasons + old.also_noted + old.notes
            if f.code not in codes and f.code != "ABOVE_AUTO_APPROVE_LIMIT"]
    result.decision = decide(kept + fresh, rules, result.decision_ctx or _context(result, rules))
    with store.transaction():
        store.replace_run_record(result)
        store.append_receipt("review_refused", result.run_id, {
            "actor": "human", "reviewer": reviewer, "decision": "APPROVE", "outcome": "refused", "note": note,
            "reason": "approval would overbill the purchase order", "po_number": po,
            "checks": [{"code": p["code"], **p["evidence"]} for p in position["problems"]],
            "reviews_receipt": (result.receipt or {}).get("hash")})
    return (f"Approving this invoice would overbill {po}: " + "; ".join(p["text"] for p in position["problems"]) +
            ". It stays on hold with that reason on it. Have Procurement amend the PO and approve again, or reject "
            "the invoice.")


def _context(result: RunResult, rules: Rules) -> dict:
    """Display facts for decide(), rebuilt from a stored run that predates decision_ctx."""
    inv, vendor = result.invoice, result.vendor or {}
    return {"vendor_name": vendor.get("name") or (inv.get("vendor_name") if inv else None),
            "invoice_number": inv.get("invoice_number") if inv else None,
            "invoice_date": inv.get("invoice_date") if inv else None, "total": inv.get("total") if inv else None,
            "po": result.po, "payment_terms_days": vendor.get("payment_terms_days"),
            "company_name": rules.company.name, "is_invoice": True,
            "currency": (inv.get("currency") if inv else None) or "INR"}


def apply_review(store: Store, run_id: str, decision: str, reviewer: str, note: str = "",
                 checks: Optional[dict[str, Any]] = None, *, master: Optional[MasterData] = None,
                 rules: Optional[Rules] = None, evidence: Optional[tuple[str, bytes]] = None) -> dict:
    """Record a reviewer's APPROVE, REJECT or HANDLED for a held run and return the review
    receipt. For a changed bank account the first APPROVE is recorded as a first approval
    and the run stays on hold until a different person approves. `evidence` is an optional
    (file name, content) kept with the review, such as the vendor's mandate letter."""
    checks = dict(checks or {})
    decision = (decision or "").upper()
    reviewer, note = (reviewer or "").strip(), (note or "").strip()
    if decision not in ("APPROVE", "REJECT", "HANDLED"):
        raise ReviewError("The decision must be APPROVE, REJECT or HANDLED.")
    if not reviewer:
        raise ReviewError("Enter the reviewer's name: every human decision is recorded against a person.")
    row = store.run_row(run_id)
    if row is None:
        raise ReviewError(f"Run {run_id} does not exist.")
    if row.get("resubmission_of"):
        raise ReviewError(f"{run_id} is a re-submission of {row['resubmission_of']}. Decide {row['resubmission_of']} "
                          f"in the review queue; a re-submission carries no decision of its own.")
    if row["verdict"] != "HOLD" or row["review_status"] != "PENDING":
        raise ReviewError(f"Run {run_id} is not waiting for review.")
    rules = rules or current_rules()
    result = store.get_run(run_id)
    codes = {f.code for f in result.decision.reasons}
    ledger_id = result.ledger_id
    entry = store.get_ledger(ledger_id) if ledger_id else None
    booked = entry is not None
    learned: list[str] = []
    first = review_state(store, run_id)["first_approval"]
    two_people = "BANK_DETAILS_CHANGED" in codes and rules.review.bank_change_approvers >= 2
    kept: Optional[dict] = None

    if decision == "HANDLED" and booked:
        raise ReviewError("This invoice is in the ledger waiting for a decision: approve it or reject it.")
    if decision == "APPROVE":
        if not booked:
            raise ReviewError("This document was not booked by the system (see its reasons), so it cannot be approved "
                              "here. Process it by hand and close it as handled, or reject it.")
        for finding in result.decision.reasons:
            if finding.code != "RESUBMITTED_WHILE_ON_HOLD":
                continue
            earlier = store.get_ledger(str(finding.evidence.get("original_ledger_id") or ""))
            where = finding.evidence.get("original_run_id") or "the earlier invoice"
            if earlier and earlier["status"] == "PENDING":
                raise ReviewError(f"{where} carries the same invoice number and is still on hold. Decide that one "
                                  f"first: two invoices with one number cannot both stay open.")
            if earlier and earlier["status"] == "POSTED":
                raise ReviewError(f"{where} with the same invoice number has since been approved, so this one is a "
                                  f"duplicate. Reject it.")

        # ---- the bank change: who is approving, and have they done their part
        if "BANK_DETAILS_CHANGED" in codes:
            if two_people and first is not None:
                if _person(first["reviewer"]) == _person(reviewer):
                    raise ReviewError(f"The call-back was recorded by {first['reviewer']}. A changed bank account must "
                                      f"be approved by a different person.")
                if not checks.get("bank_second_check"):
                    raise ReviewError(f"Read the call record left by {first['reviewer']} and confirm it before approving "
                                      f"the change of bank account.")
            else:
                if not checks.get("bank_verified"):
                    raise ReviewError("This invoice changes the vendor's bank account. It can be approved only after a "
                                      "call-back to the number on file is confirmed.")
                if not note:
                    raise ReviewError("Record the call in the note: who you spoke to, on which number, and when.")

        # ---- the purchase order, as it stands now
        position = po_position(store, master or load_master(), rules, entry)
        if position["problems"]:
            raise ReviewError(_refuse_overbilling(store, rules, result, position, reviewer, note))

        if evidence is not None:
            name, content = evidence
            kept = {"name": str(name)[:120], "sha256": store.save_attachment(bytes(content)), "bytes": len(content)}

        # ---- first of two approvals: recorded, and the invoice stays on hold
        if two_people and first is None:
            with store.transaction():
                store.add_review(run_id, FIRST, reviewer, note, checks)
                return store.append_receipt("review", run_id, {
                    "actor": "human", "reviewer": reviewer, "decision": FIRST, "awaiting": "second approver",
                    "note": note, "checks": checks, "evidence": kept, "reasons_reviewed": sorted(codes),
                    "reviews_receipt": (result.receipt or {}).get("hash"),
                    "ledger": {"ledger_id": ledger_id, "status": "PENDING"}})

    number = ""
    if decision == "APPROVE" and "MISSING_INVOICE_NUMBER" in codes:
        number = str(checks.get("invoice_number") or "").strip()
        if not number:
            raise ReviewError("This invoice has no invoice number. Enter the number obtained from the vendor "
                              "before approving.")
        vendor_id = (result.vendor or {}).get("vendor_id")
        clash = [e for e in store.ledger_entries(vendor_id=vendor_id, statuses=("POSTED", "PENDING"))
                 if e["ledger_id"] != ledger_id and e["invoice_key"] == T.invoice_no_key(number)] if vendor_id else []
        if clash:
            raise ReviewError(f"Invoice number {number} is already on record for this vendor "
                              f"({clash[0]['invoice_no']}, {clash[0]['status'].lower()}). If the vendor confirms "
                              f"that number, this invoice is a duplicate and should be rejected.")

    outcome = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "HANDLED": "HANDLED"}[decision]
    approvers = [first["reviewer"], reviewer] if (decision == "APPROVE" and two_people and first) else None
    with store.transaction():
        if number:
            store.set_ledger_invoice_no(ledger_id, number)
            learned.append(f"invoice number recorded as {number}")
        if decision == "APPROVE" and "BANK_DETAILS_CHANGED" in codes and result.vendor and result.invoice:
            account = result.invoice.get("bank_account")
            if account:
                store.add_override(result.vendor["vendor_id"], "bank_account", T.digits_only(account),
                                   extra=str(result.invoice.get("bank_ifsc") or ""), source_run=run_id)
                learned.append(f"account {account} is now a verified account for {result.vendor['name']}")
        if booked:
            who = " and ".join(approvers) if approvers else reviewer
            store.set_ledger_status(ledger_id, "POSTED" if decision == "APPROVE" else "REJECTED",
                                    f"{'approved' if decision == 'APPROVE' else 'rejected'} on review by {who}")
        store.set_review_status(run_id, outcome)
        store.add_review(run_id, decision, reviewer, note, checks)
        body = {"actor": "human", "reviewer": reviewer, "decision": decision, "note": note, "checks": checks,
                "learned": learned, "evidence": kept, "reviews_receipt": (result.receipt or {}).get("hash"),
                "reasons_reviewed": sorted(codes),
                "ledger": {"ledger_id": ledger_id, "status": "POSTED" if decision == "APPROVE" else "REJECTED"} if booked else None}
        if approvers:
            body["approvers"] = approvers
        return store.append_receipt("review", run_id, body)
