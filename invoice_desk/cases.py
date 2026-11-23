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

from . import textutil as T
from .masterdata import MasterData, PoLine, Vendor
from .review import po_position
from .rules import Rules
from .store import Store

ZERO = Decimal("0")

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


# ---------------------------------------------------------------- goods receipts and release by rule

def master_with_receipts(master: MasterData, store: Store) -> MasterData:
    """The master with every goods receipt recorded in this store added to its PO lines. With no
    receipt recorded it is the very same object, so callers that never record one see no change."""
    recorded = store.goods_receipts()
    if not recorded:
        return master
    live = master.model_copy(deep=True)
    for receipt in recorded:
        po = live.pos.get(receipt["po_number"])
        line = po.line(int(receipt["line_no"])) if po else None
        if line is None:
            continue
        line.qty_received = (line.qty_received or ZERO) + (receipt["qty"] or ZERO)
        day = _as_date(receipt["received_on"])
        if day and (line.received_on is None or day > line.received_on):
            line.received_on = day
    return live


def record_goods_receipt(store: Store, master: MasterData, po_number: str, line_no: int, qty: Decimal,
                         received_on: date | str, recorded_by: str, reference: str, for_run: str = "") -> dict:
    """Stores record a delivery against a PO line. It joins the audit chain, and the case of `for_run`."""
    po = master.pos.get(po_number)
    line = po.line(int(line_no)) if po else None
    if line is None:
        raise CaseError(f"{po_number} has no line {line_no}.")
    qty = Decimal(str(qty))
    if qty <= 0:
        raise CaseError("Enter the quantity received: it must be more than zero.")
    recorded_by, reference = (recorded_by or "").strip(), (reference or "").strip()
    if not recorded_by:
        raise CaseError("Enter your name: every goods receipt is recorded against a person.")
    day = received_on if isinstance(received_on, date) else _as_date(received_on)
    if day is None:
        raise CaseError("Enter the date the goods were received.")
    amount = f"{T.fmt_num(qty)} {line.uom}".strip()
    note = f"{amount} of line {line.line_no} ({line.description}) on {po_number}" + \
           (f", delivery note {reference}" if reference else "")
    case = store.case_for_run(for_run) if for_run else None
    with store.transaction():
        receipt_id = store.add_goods_receipt(po_number, line.line_no, qty, _iso(day), recorded_by, reference)
        if case is not None:
            store.add_case_event(case["case_id"], "goods_receipt", recorded_by, f"Goods receipt recorded: {note}.",
                                 {"po_number": po_number, "line_no": line.line_no, "qty": str(qty), "reference": reference})
        store.append_receipt("goods_receipt", for_run, {
            "actor": "human", "reviewer": recorded_by, "decision": "goods receipt recorded", "note": note,
            "po_number": po_number, "line_no": line.line_no, "qty": str(qty), "received_on": _iso(day),
            "reference": reference})
    return {"id": receipt_id, "po_number": po_number, "line_no": line.line_no, "qty": qty, "received_on": day}


def _say_once(store: Store, case: dict, kind: str, summary: str) -> None:
    """A refusal is written to the timeline once per distinct reason, not on every sync."""
    if not any(e["kind"] == kind and e["summary"] == summary for e in store.case_events(case["case_id"])):
        store.add_case_event(case["case_id"], kind, DESK, summary)


def try_resume(store: Store, master: MasterData, rules: Rules, case: dict, today: date) -> dict:
    """Release a held invoice by rule once the fact it waited for is on record (DECISIONS.md D66).

    Every condition must hold: the run is still a pending hold with a pending ledger entry; every
    reason on it is in rules.yaml cases.auto_resume; the goods received now cover what is billed;
    and the purchase order re-check a person's approval gets passes. Recorded as RELEASED, never
    as APPROVED, in one transaction."""
    run_id = case["run_id"]
    row = store.run_row(run_id)
    result = store.get_run(run_id)
    entry = store.get_ledger(result.ledger_id) if result and result.ledger_id else None
    if not row or row["verdict"] != "HOLD" or row["review_status"] != "PENDING" or not entry \
            or entry["status"] != "PENDING":
        return {"released": False, "why": "the invoice is no longer waiting on hold"}
    codes = [f.code for f in result.decision.reasons]
    allowed = set(rules.cases.auto_resume)
    if not codes or not set(codes) <= allowed:
        others = sorted(set(codes) - allowed)
        why = ("release by rule is switched off in rules.yaml (cases.auto_resume)" if not allowed else
               f"other reasons are on the hold and need a person: {', '.join(others)}")
        return {"released": False, "why": why}

    live = master_with_receipts(master, store)
    po = live.pos.get(entry["po_number"])
    recorded_lines = {(r["po_number"], int(r["line_no"])) for r in store.goods_receipts(entry["po_number"])}
    for ln in entry["lines"]:
        po_line = po.line(ln["po_line_no"]) if po and ln.get("po_line_no") is not None else None
        if po_line is None or po_line.match_basis != "QTY" or po_line.qty_received is None or ln.get("qty") is None:
            continue
        posted_by_others = sum((other_ln["qty"] or ZERO
                                for other in store.ledger_entries(po_number=po.po_number, statuses=("POSTED",))
                                if other["ledger_id"] != entry["ledger_id"]
                                for other_ln in other["lines"] if other_ln["po_line_no"] == po_line.line_no), ZERO)
        available = po_line.qty_received - posted_by_others
        if available < ln["qty"]:
            have = max(available, ZERO)
            why = (f"{T.fmt_num(have)} of {T.fmt_num(ln['qty'])} received. The invoice still waits for the remaining "
                   f"{T.fmt_num(ln['qty'] - have)}.")
            if (po.po_number, po_line.line_no) in recorded_lines:    # nothing to say until a delivery is recorded
                _say_once(store, case, "partial_receipt", why)
            return {"released": False, "why": why}

    position = po_position(store, live, rules, entry)
    if position["problems"]:
        why = (f"The goods are received, but releasing the invoice now would overbill {position['po_number']}: "
               + "; ".join(p["text"] for p in position["problems"]) + ". A person must decide it in the review queue.")
        _say_once(store, case, "resume_refused", why)
        return {"released": False, "why": why}

    note = "The goods receipt on record now covers every billed line, and the purchase order re-check passed."
    checks = {"goods_received": True, "po_recheck": "passed", "reasons_cleared": codes}
    with store.transaction():
        store.set_ledger_status(entry["ledger_id"], "POSTED", "released by rule: goods received")
        store.set_review_status(run_id, "RELEASED")
        store.add_review(run_id, "RELEASED", "Invoice Desk (rule)", note, checks)
        store.append_receipt("release", run_id, {
            "actor": "rule", "decision": "RELEASED", "note": note, "reasons_cleared": codes,
            "ledger": {"ledger_id": entry["ledger_id"], "status": "POSTED"},
            "reviews_receipt": (result.receipt or {}).get("hash")})
        store.update_case(case["case_id"], state=move(case["state"], "resolve"), resolved_on=_iso(today),
                          resolution=RESOLUTION["RELEASED"])
        store.add_case_event(case["case_id"], "released_by_rule", DESK,
                             "Released by rule and posted for payment: nothing else was holding this invoice.",
                             {"ledger_id": entry["ledger_id"]})
    return {"released": True, "why": note}


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
    allowed = set(rules.cases.auto_resume)
    for case in store.list_cases(open_only=True):
        codes = set(filter(None, case["reason_codes"].split(",")))
        if allowed and codes and codes <= allowed:
            counts["released"] += int(try_resume(store, master, rules, case, today)["released"])
    for case in store.list_cases(open_only=True):
        due = _as_date(case["due_on"])
        if due and today > due and not case["escalated_to"]:
            _escalate(store, case, rules, today,
                      f"No answer from {case['waiting_on']} by {day_text(due)}. {{target}} told.")
            counts["escalated"] += 1
    return counts
