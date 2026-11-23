"""What happens after a hold (round 3, DECISIONS.md D65 to D70).

Every held invoice becomes a case with one owner, one question, a reply date and a timeline.
Cases are derived from held runs by `sync`, a reconciler that is safe to call any number of
times, so the pipeline that decides is not touched. Opening, closing and escalating a case
are bookkeeping and write to case_events only; they never add a receipt to the audit chain.

Clocks follow the business date passed in as `today` (the pinned demo date on screen), not
the wall clock, so a rehearsal and the interview give the same answer.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from .masterdata import MasterData, PoLine, Vendor
from .rules import Rules
from .store import Store

STATES = ("WAITING", "ANSWERED", "RESOLVED")
MOVES = {("WAITING", "fact"): "ANSWERED", ("WAITING", "reply"): "ANSWERED",
         ("WAITING", "message_sent"): "WAITING", ("ANSWERED", "message_sent"): "WAITING",
         ("WAITING", "resolve"): "RESOLVED", ("ANSWERED", "resolve"): "RESOLVED"}   # anything else raises CaseError

DESK = "Invoice Desk"
RESOLUTION = {"APPROVED": "approved", "REJECTED": "rejected", "HANDLED": "handled", "RELEASED": "released by rule"}


class CaseError(Exception):
    pass


def move(state: str, event: str) -> str:
    """The state a case moves to on an event. A move that is not listed is refused."""
    target = MOVES.get((state, event))
    if target is None:
        raise CaseError(f"A case that is {state} cannot take the step '{event}'.")
    return target


def case_id_for(file_sha256: str) -> str:
    return "CASE-" + file_sha256[:8].upper()


def day_text(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _iso(d: Optional[date]) -> str:
    return d.isoformat() if d else ""


def _as_date(text: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(text)[:10]) if text else None
    except ValueError:
        return None


# ---------------------------------------------------------------- the statutory clock

def statutory_clock(vendor: Optional[Vendor], po_lines_billed: list[PoLine], invoice_date: Optional[date],
                    total: Optional[Decimal], rules: Rules, today: date) -> Optional[dict]:
    """The MSMED Act pay-by date for a micro or small supplier, or None when it does not apply.

    The clock starts when the goods are accepted. That is taken as the latest delivery on record
    for the PO lines billed, when every one of them has a delivery date; otherwise the invoice
    date, and the basis sentence says so."""
    m = rules.msme
    if vendor is None or vendor.msme_category not in m.applies_to or invoice_date is None:
        return None
    delivered = [ln.received_on for ln in po_lines_billed]
    if delivered and all(delivered):
        start = max(delivered)
        basis = f"counted from the delivery recorded on {day_text(start)}"
    else:
        start = invoice_date
        basis = ("counted from the invoice date because no delivery date is on record; "
                 "the legal clock starts when the goods are accepted")
    terms = vendor.payment_terms_days
    days = min(terms, m.max_days_with_agreement) if terms > 0 else m.days_without_agreement
    due = start + timedelta(days=days)
    per_day = (Decimal(total or 0) * m.interest_multiple * m.bank_rate_pct / Decimal(100) / Decimal(365))
    return {"due": due, "days_left": (due - today).days, "basis": basis, "days_allowed": days,
            "interest_per_day": int(per_day.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), "approximate": True}


def _clock_for_run(store: Store, master: MasterData, rules: Rules, result, today: date) -> Optional[dict]:
    vendor = master.vendors.get((result.vendor or {}).get("vendor_id") or "")
    entry = store.get_ledger(result.ledger_id) if result.ledger_id else None
    po = master.pos.get(entry["po_number"]) if entry else None
    lines = [po.line(ln["po_line_no"]) for ln in entry["lines"]] if po else []
    lines = [ln for ln in lines if ln is not None]
    invoice_date = _as_date(result.invoice.get("invoice_date") if result.invoice else None)
    total = entry["total"] if entry and entry.get("total") is not None else None
    return statutory_clock(vendor, lines, invoice_date, total, rules, today)


# ---------------------------------------------------------------- the reconciler

def _free_case_id(store: Store, file_sha256: str) -> str:
    """The case id from the file fingerprint. The same file read afresh (after a rules change) gets a suffix."""
    base = case_id_for(file_sha256)
    candidate, n = base, 1
    while store.get_case(candidate) is not None:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _escalate(store: Store, case: dict, rules: Rules, today: date, summary: str) -> None:
    target = rules.cases.escalate_to[case["waiting_on"]]
    store.update_case(case["case_id"], escalated_to=target, escalated_on=_iso(today))
    store.add_case_event(case["case_id"], "escalated", DESK, summary.format(target=target),
                         {"to": target, "waiting_on": case["waiting_on"]})


def _open(store: Store, master: MasterData, rules: Rules, run_id: str, today: date) -> tuple[int, int]:
    result = store.get_run(run_id)
    d = result.decision if result else None
    if d is None or not d.owners or not d.questions:
        raise CaseError(f"{run_id} is held with no owner or question; every HOLD reason needs an owner in rules.yaml.")
    owner, question = d.owners[0], d.questions[0]
    if owner not in rules.cases.reply_due_days:
        raise CaseError(f"{run_id} is held for '{owner}', who has no reply time in rules.yaml (cases.reply_due_days).")
    clock = _clock_for_run(store, master, rules, result, today)
    urgent = clock is not None and clock["days_left"] <= rules.msme.warn_days_before
    due = today + timedelta(days=rules.cases.reply_due_days[owner])
    with store.transaction():
        if store.case_for_run(run_id) is not None:          # opened by someone else a moment ago
            return 0, 0
        case = {"case_id": _free_case_id(store, result.file_sha256), "run_id": run_id, "opened_on": _iso(today),
                "state": "WAITING", "waiting_on": owner, "owner": owner, "question": question,
                "reason_codes": ",".join(f.code for f in d.reasons), "priority": "urgent" if urgent else "normal",
                "due_on": _iso(due), "statutory_due": _iso(clock["due"]) if clock else "",
                "statutory_basis": clock["basis"] if clock else ""}
        store.add_case(case)
        store.add_case_event(case["case_id"], "opened", DESK, f"Held for {owner}, answer due {day_text(due)}: {question}",
                             {"reasons": case["reason_codes"].split(","), "due_on": case["due_on"]})
        if urgent:
            _escalate(store, case, rules, today,
                      f"Urgent: the legal pay-by date is {day_text(clock['due'])}, {clock['days_left']} day(s) away. "
                      "{target} told at once.")
    return 1, int(urgent)


def _close_if_decided(store: Store, case: dict, today: date) -> int:
    row = store.run_row(case["run_id"])
    status = (row or {}).get("review_status") or ""
    if status in ("", "PENDING"):
        return 0
    reviews = store.reviews(case["run_id"])
    actor = reviews[-1]["reviewer"] if reviews else DESK
    resolution = RESOLUTION.get(status, status.lower())
    with store.transaction():
        store.update_case(case["case_id"], state=move(case["state"], "resolve"), resolved_on=_iso(today),
                          resolution=resolution)
        store.add_case_event(case["case_id"], "closed_by_review", actor, f"Decided in the review queue: {resolution}.",
                             {"review_status": status})
    return 1


def sync(store: Store, master: MasterData, rules: Rules, today: date) -> dict:
    """Bring the cases in line with the runs: open, close and escalate. Safe to call any number of times."""
    counts = {"opened": 0, "closed": 0, "released": 0, "escalated": 0}
    if not rules.cases.enabled:
        return counts
    for row in reversed(store.list_runs()):                  # oldest first
        if row["verdict"] != "HOLD" or row.get("resubmission_of") or store.case_for_run(row["run_id"]):
            continue
        opened, escalated = _open(store, master, rules, row["run_id"], today)
        counts["opened"] += opened
        counts["escalated"] += escalated
    for case in store.list_cases(open_only=True):
        counts["closed"] += _close_if_decided(store, case, today)
    for case in store.list_cases(open_only=True):
        due = _as_date(case["due_on"])
        if due and today > due and not case["escalated_to"]:
            _escalate(store, case, rules, today,
                      f"No answer from {case['waiting_on']} by {day_text(due)}. {{target}} told.")
            counts["escalated"] += 1
    return counts
