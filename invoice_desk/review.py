"""Human review of held invoices.

A hold ends when a named person approves or rejects it. A held document that the system
did not book at all (a foreign-currency invoice, a credit note, a file too large to read)
cannot be approved here: it is processed by hand and then marked as handled. The decision is written to the
same hash chain as the machine decisions, marked with the actor, so the audit trail
shows who did what. Two approvals carry an extra control:

  * a changed bank account can be approved only after the reviewer confirms a call-back
    to the number on file; the confirmed account is then remembered for that vendor;
  * an invoice with no number can be approved only once the reviewer enters the number
    obtained from the vendor, because the ledger needs it for duplicate control.
"""
from __future__ import annotations

from typing import Any, Optional

from . import textutil as T
from .store import Store


class ReviewError(Exception):
    pass


def required_checks(reason_codes: set[str]) -> dict[str, str]:
    """What the reviewer must supply before an approval is accepted: {check: explanation}."""
    needs: dict[str, str] = {}
    if "BANK_DETAILS_CHANGED" in reason_codes:
        needs["bank_verified"] = "I called the vendor on the number in the vendor master and confirmed the new account."
    if "MISSING_INVOICE_NUMBER" in reason_codes:
        needs["invoice_number"] = "Invoice number obtained from the vendor"
    return needs


def apply_review(store: Store, run_id: str, decision: str, reviewer: str, note: str = "",
                 checks: Optional[dict[str, Any]] = None) -> dict:
    """Record a reviewer's APPROVE or REJECT for a held run. Returns the review receipt."""
    checks = dict(checks or {})
    decision = (decision or "").upper()
    if decision not in ("APPROVE", "REJECT", "HANDLED"):
        raise ReviewError("The decision must be APPROVE, REJECT or HANDLED.")
    if not (reviewer or "").strip():
        raise ReviewError("Enter the reviewer's name: every human decision is recorded against a person.")
    row = store.run_row(run_id)
    if row is None:
        raise ReviewError(f"Run {run_id} does not exist.")
    if row["verdict"] != "HOLD" or row["review_status"] != "PENDING":
        raise ReviewError(f"Run {run_id} is not waiting for review.")
    result = store.get_run(run_id)
    codes = {f.code for f in result.decision.reasons}
    ledger_id = result.ledger_id
    learned: list[str] = []
    booked = bool(ledger_id) and store.get_ledger(ledger_id) is not None
    if decision == "HANDLED" and booked:
        raise ReviewError("This invoice is in the ledger waiting for a decision: approve it or reject it.")
    if decision == "APPROVE":
        if not booked:
            raise ReviewError("This document was not booked by the system (see its reasons), so it cannot be approved "
                              "here. Process it by hand and mark it as handled, or reject it.")
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
        needs = required_checks(codes)
        if "bank_verified" in needs and not checks.get("bank_verified"):
            raise ReviewError("This invoice changes the vendor's bank account. It can be approved only after a "
                              "call-back to the number on file is confirmed.")
        if "invoice_number" in needs:
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
            store.set_ledger_invoice_no(ledger_id, number)
            learned.append(f"invoice number recorded as {number}")
        if "bank_verified" in needs and result.vendor and result.invoice:
            account = result.invoice.get("bank_account")
            if account:
                store.add_override(result.vendor["vendor_id"], "bank_account", T.digits_only(account),
                                   extra=str(result.invoice.get("bank_ifsc") or ""), source_run=run_id)
                learned.append(f"account {account} is now a verified account for {result.vendor['name']}")
    outcome = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "HANDLED": "HANDLED"}[decision]
    if booked:
        store.set_ledger_status(ledger_id, "POSTED" if decision == "APPROVE" else "REJECTED",
                                f"{'approved' if decision == 'APPROVE' else 'rejected'} on review by {reviewer.strip()}")
    store.set_review_status(run_id, outcome)
    store.add_review(run_id, decision, reviewer.strip(), note.strip(), checks)
    return store.append_receipt("review", run_id, {
        "actor": "human", "reviewer": reviewer.strip(), "decision": decision, "note": note.strip(),
        "checks": checks, "learned": learned, "reviews_receipt": (result.receipt or {}).get("hash"),
        "reasons_reviewed": sorted(codes),
        "ledger": {"ledger_id": ledger_id, "status": "POSTED" if decision == "APPROVE" else "REJECTED"} if booked else None})
