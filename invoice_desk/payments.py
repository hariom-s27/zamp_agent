"""The payment-run proposal (round 4): from approved invoices to a payment-ready list, for a
person with release authority to approve.

Every line is arithmetic on recorded facts: the due date from the vendor's terms, the legal
pay-by date of a micro or small supplier (the round 3 clock), an early-payment discount when
its yearly return clears the hurdle rate. Nothing here pays anything or changes a ledger
status: an approved run is stored, a proposal file is written beside the database with bank
accounts masked, and one audit entry records it. The desk has no access to a bank.
"""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Optional

from . import cases as case_engine
from . import config
from .decide import _due_date
from .masterdata import MasterData
from .review import _person
from .rules import Rules
from .store import Store, canonical
from .textutil import fmt_inr, q2

ZERO = Decimal("0")
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
GROUPS = {"pay": "Pay in this run", "not_due": "Not due yet", "blocked": "Blocked",
          "on_hold": "On hold, with a legal date close", "deferred": "Deferred for cash", "removed": "Taken out"}
OFF = "Payment proposals are switched off in the desk rules."
CAPTION = "The desk prepares the payment run. A person with release authority approves it. Nothing is paid from here."
DOES_NOT = ("It does not deduct TDS. It does not check GSTR-2B. It does not know whether the bank released a payment. "
            "Amounts are the approved invoice amounts, less any discount shown.")
TERMS_FILE = "vendor_terms_desk.csv"


class PaymentError(Exception):
    pass


def load_terms(data_dir: Optional[Path] = None) -> dict[str, tuple[Decimal, int]]:
    """Early-payment discounts agreed with vendors: vendor_id -> (discount %, discount days)."""
    path = Path(data_dir or config.DATA_DIR) / TERMS_FILE
    if not path.is_file():
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["vendor_id"].strip(): (Decimal(r["discount_pct"].strip()), int(r["discount_days"]))
                for r in csv.DictReader(f)}


def discount_return(pct: Decimal, discount_days: int, terms_days: int) -> Optional[Decimal]:
    """Yearly return of taking a discount: d/(1-d) x 365/(N-t), as a percentage with two decimals."""
    if terms_days <= discount_days or pct <= 0 or pct >= 100:
        return None
    d = Decimal(pct) / 100
    value = d / (1 - d) * Decimal(365) / Decimal(terms_days - discount_days) * 100
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _day(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _days_left(n: int) -> str:
    return f"{n} day{'s' if n != 1 else ''} left"


def _pct(value: Decimal) -> str:
    return f"{value.normalize():f}"


def next_run_dates(rules: Rules, start: date, count: int = 2) -> list[date]:
    wanted = {WEEKDAYS.index(d) for d in rules.payments.run_weekdays}
    found, day = [], start
    while len(found) < count:
        if day.weekday() in wanted:
            found.append(day)
        day += timedelta(days=1)
    return found


def _falls_into(rules: Rules, pay_by: date, run_date: date) -> date:
    day = run_date
    wanted = {WEEKDAYS.index(d) for d in rules.payments.run_weekdays}
    while not (day.weekday() in wanted and day + timedelta(days=rules.payments.horizon_days) >= pay_by):
        day += timedelta(days=1)
    return day


def _open_bank_changes(store: Store) -> dict[str, str]:
    """vendor_id -> the AP reference of a hold for a changed bank account that is still waiting."""
    found: dict[str, str] = {}
    for row in reversed(store.list_runs()):
        codes = set((row.get("reason_codes") or "").split(","))
        if row["verdict"] == "HOLD" and "BANK_DETAILS_CHANGED" in codes and (row.get("review_status") or "") in ("", "PENDING") \
                and row.get("vendor_id") and not row.get("resubmission_of"):    # a repeat file is not a new request
            found.setdefault(row["vendor_id"], row["run_id"])
    return found


def _legal_sentence(vendor, clock: dict) -> str:
    basis = clock["basis"][0].upper() + clock["basis"][1:]
    return (f"Legal pay-by date for a {vendor.msme_category} supplier: {case_engine.day_text(clock['due'])}, "
            f"{_days_left(clock['days_left'])}. {basis}. After that date interest runs at about "
            f"{fmt_inr(clock['interest_per_day'], decimals=0)} a day (approximate).")


def _line(store: Store, master: MasterData, rules: Rules, entry: dict, terms: dict, run_date: date,
          end: date) -> dict[str, Any]:
    p = rules.payments
    vendor = master.vendors.get(entry["vendor_id"] or "")
    total = entry["total"] or ZERO
    inv_date = _day(entry["invoice_date"])
    terms_days = vendor.payment_terms_days if vendor else 0
    due = _due_date(entry["invoice_date"], terms_days) or run_date
    po = master.pos.get(entry["po_number"] or "")
    po_lines = [po.line(ln["po_line_no"]) for ln in entry.get("lines", []) if po and ln.get("po_line_no") is not None]
    clock = case_engine.statutory_clock(vendor, [ln for ln in po_lines if ln is not None], inv_date, total, rules,
                                        run_date)
    pay_by = min(due, clock["due"]) if clock else due
    line = {"ledger_id": entry["ledger_id"], "run_id": entry.get("run_id") or "", "vendor_id": entry["vendor_id"],
            "vendor": vendor.legal_name if vendor else entry["vendor_id"], "invoice_no": entry["invoice_no"],
            "invoice_date": inv_date, "total": total, "discount": ZERO, "gst_part": ZERO, "amount": total,
            "pay_by": pay_by, "due": due, "basis": "due", "why": "", "flags": [], "group": "not_due"}
    offer = terms.get(entry["vendor_id"] or "")
    taken, left_why = False, ""
    if offer and inv_date:
        pct, days = offer
        deadline = inv_date + timedelta(days=days)
        rate = discount_return(pct, days, terms_days)
        if rate is not None and rate >= p.hurdle_rate_pct and deadline >= run_date:
            base = total if p.discount_base == "total" else (entry.get("taxable") or total)
            discount = q2(base * pct / 100)
            tax = entry.get("tax") or ZERO
            gst = q2(discount * tax / (total if p.discount_base == "total" else base)) if base else ZERO
            taken = True
            line.update(discount=discount, gst_part=gst, amount=total - discount, discount_deadline=deadline,
                        why=(f"Early-payment discount: {_pct(pct)}% if paid by {case_engine.day_text(deadline)}. Worth "
                             f"{rate.quantize(Decimal('0.1'))}% a year against a {_pct(p.hurdle_rate_pct)}% hurdle, so it is "
                             f"taken. Pay {fmt_inr(total - discount)} in place of {fmt_inr(total)}. The vendor's credit note "
                             f"for {fmt_inr(discount)} is expected, and input tax credit of {fmt_inr(gst)} is reversed "
                             f"when it arrives."))
        elif rate is not None and rate < p.hurdle_rate_pct:
            left_why = (f"Early-payment discount of {_pct(pct)}% is worth {rate.quantize(Decimal('0.1'))}% a year, "
                        f"below the {_pct(p.hurdle_rate_pct)}% hurdle. Left to its due date, {case_engine.day_text(due)}.")
    if pay_by < run_date:
        line.update(basis="overdue", group="pay", why=f"Overdue since {case_engine.day_text(pay_by)}.")
    elif clock and clock["due"] <= end:
        line.update(basis="legal", group="pay", pay_by=clock["due"], why=_legal_sentence(vendor, clock))
    elif taken and line["discount_deadline"] <= end:
        line.update(basis="discount", group="pay", pay_by=line["discount_deadline"])
    elif due <= end:
        line.update(basis="due", group="pay", why=f"Due on {case_engine.day_text(due)} ({terms_days}-day terms).")
    else:
        if taken:                                    # the discount deadline is beyond this run: no discount yet
            line.update(discount=ZERO, gst_part=ZERO, amount=total)
        if clock and clock["due"] == pay_by:
            line.update(basis="legal", why=_legal_sentence(vendor, clock))
        else:
            line["why"] = left_why or f"Due on {case_engine.day_text(due)} ({terms_days}-day terms)."
        line["falls_into"] = _falls_into(rules, pay_by, run_date)
    if line["group"] == "pay" and not taken and left_why and line["basis"] == "due":
        line["flags"].append(left_why)
    if inv_date and (run_date - inv_date).days >= p.itc_reversal_warn_days:
        line["flags"].append(f"Unpaid for {(run_date - inv_date).days} days. Input tax credit must be reversed if this "
                             f"is not paid within 180 days of the invoice date.")
    return line


def _on_hold(store: Store, master: MasterData, rules: Rules, run_date: date, end: date) -> list[dict]:
    lines = []
    for row in reversed(store.list_runs()):
        if row["verdict"] != "HOLD" or (row.get("review_status") or "") not in ("", "PENDING") or row.get("resubmission_of"):
            continue
        result = store.get_run(row["run_id"])
        if result is None or not result.ledger_id:
            continue
        clock = case_engine._clock_for_run(store, master, rules, result, run_date)
        if clock is None or clock["due"] > end:
            continue
        case = store.case_for_run(row["run_id"])
        d = result.decision
        owner = case["owner"] if case else (d.owners[0] if d and d.owners else "a person")
        where = f"case {case['case_id']}" if case else "no case opened yet"
        entry = store.get_ledger(result.ledger_id) or {}
        lines.append({"ledger_id": result.ledger_id, "run_id": row["run_id"], "vendor_id": row.get("vendor_id") or "",
                      "vendor": row.get("vendor_name") or "", "invoice_no": row.get("invoice_no") or "",
                      "total": entry.get("total") or ZERO, "amount": ZERO, "pay_by": clock["due"], "basis": "legal",
                      "case_id": case["case_id"] if case else "", "flags": [], "group": "on_hold",
                      "why": (f"Cannot be paid: held for {owner} ({where}). Legal pay-by date "
                              f"{case_engine.day_text(clock['due'])}, {_days_left(clock['days_left'])}.")})
    return lines


def propose(store: Store, master: MasterData, rules: Rules, run_date: date, cash: Optional[Decimal] = None,
            removed: Optional[dict[str, dict]] = None, terms: Optional[dict] = None) -> Optional[dict[str, Any]]:
    """The proposal for a run date, in four groups (plus deferred and taken out). None when the feature is off."""
    p = rules.payments
    if not p.enabled:
        return None
    removed = removed or {}
    terms = load_terms() if terms is None else terms
    live = case_engine.master_with_receipts(master, store)
    end = run_date + timedelta(days=p.horizon_days)
    locked = store.locked_for_payment()
    origins = ("run", "history") if p.include_history else ("run",)
    banks = _open_bank_changes(store) if p.block_on_open_bank_change else {}
    groups: dict[str, list[dict]] = {name: [] for name in GROUPS}
    for entry in store.ledger_entries():
        if entry["status"] != "POSTED" or entry["origin"] not in origins or entry["ledger_id"] in locked:
            continue
        line = _line(store, live, rules, entry, terms, run_date, end)
        vendor = live.vendors.get(entry["vendor_id"] or "")
        if vendor is None or vendor.status != "APPROVED":
            line.update(group="blocked", amount=ZERO,
                        why=f"Not proposed: {line['vendor']} is not an approved vendor. With: Procurement.")
        elif entry["vendor_id"] in banks:
            line.update(group="blocked", amount=ZERO,
                        why=(f"Not proposed: {line['vendor']} has a bank account change waiting for verification "
                             f"({banks[entry['vendor_id']]}). Nothing is paid to this vendor until that is settled. "
                             f"With: Vendor master team."))
        elif line["group"] == "pay" and entry["ledger_id"] in removed:
            r = removed[entry["ledger_id"]]
            line.update(group="removed", why=f"Taken out by {r['by']}: {r['reason']}")
        groups[line["group"]].append(line)
    groups["pay"].sort(key=lambda ln: (ln["basis"] != "legal", ln["pay_by"], -ln["discount"], ln["ledger_id"]))
    groups["not_due"].sort(key=lambda ln: (ln["pay_by"], ln["ledger_id"]))
    groups["on_hold"] = _on_hold(store, live, rules, run_date, end)
    warnings: list[str] = []
    cash_short = False
    if cash is not None:
        legal = sum((ln["amount"] for ln in groups["pay"] if ln["basis"] == "legal"), ZERO)
        running, kept = legal, []
        if legal > cash:
            cash_short = True
            warnings.append(f"The cash entered does not cover the legal-date lines: {fmt_inr(legal)} is needed for them "
                            f"alone. They stay in the run.")
        for ln in groups["pay"]:
            if ln["basis"] == "legal":
                kept.append(ln)
            elif running + ln["amount"] <= cash:
                running += ln["amount"]
                kept.append(ln)
            else:
                ln["group"] = "deferred"
                groups["deferred"].append(ln)
        groups["pay"] = kept
    seen: dict[tuple, list[dict]] = {}
    for ln in groups["pay"]:
        seen.setdefault((ln["vendor_id"], ln["total"], ln["invoice_date"]), []).append(ln)
    for twins in seen.values():
        if len(twins) > 1:
            for ln in twins:
                ln["flags"].append("Same vendor, amount and invoice date as another line in this run. Check before approving.")
    total = sum((ln["amount"] for ln in groups["pay"]), ZERO)
    shape = {"run_date": run_date.isoformat(), "cash": str(cash) if cash is not None else "",
             "removed": sorted(removed), "lines": sorted(
                 [g, ln["ledger_id"], ln.get("run_id", ""), str(ln["amount"]), str(ln["pay_by"])]
                 for g, items in groups.items() for ln in items)}
    return {"run_date": run_date, "end": end, "next_runs": next_run_dates(rules, run_date), **groups,
            "total": total, "count": len(groups["pay"]), "vendors": len({ln["vendor_id"] for ln in groups["pay"]}),
            "needs_second": total > p.second_approver_above_inr, "cash_short": cash_short, "warnings": warnings,
            "approved": locked, "fingerprint": hashlib.sha256(canonical(shape).encode("utf-8")).hexdigest()}


def remove_line(proposal: dict, removed: dict[str, dict], ledger_id: str, person: str, reason: str) -> None:
    """A person takes a line out of the run, with a name and a reason. A blocked line can never be added."""
    person, reason = (person or "").strip(), (reason or "").strip()
    if not person or not reason:
        raise PaymentError("Enter your name and a reason: taking a line out of the run is recorded.")
    if ledger_id not in {ln["ledger_id"] for ln in proposal["pay"] + proposal["deferred"]}:
        raise PaymentError("Only a line in this run can be taken out. A blocked or held line can never be added.")
    removed[ledger_id] = {"by": person, "reason": reason}


def _mask(account: str, digits: int) -> str:
    account = (account or "").strip()
    return "XXXX" + (account[-digits:] if digits and account else "")


def _export(proposal: dict, master: MasterData, rules: Rules, run_id: str) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["run_id", "vendor_id", "vendor", "invoice_no", "invoice_date", "invoice_total", "discount",
                "amount_to_pay", "bank_account", "ifsc", "pay_by", "reason"])
    for ln in proposal["pay"]:
        vendor = master.vendors.get(ln["vendor_id"])
        w.writerow([run_id, ln["vendor_id"], ln["vendor"], ln["invoice_no"], ln["invoice_date"] or "", f"{ln['total']:.2f}",
                    f"{ln['discount']:.2f}", f"{ln['amount']:.2f}",
                    _mask(vendor.bank_account if vendor else "", rules.payments.export_account_digits),
                    vendor.ifsc if vendor else "", ln["pay_by"].isoformat(), ln["why"]])
    return out.getvalue().encode("utf-8-sig")          # with a BOM, so Excel shows the rupee sign


def approve(store: Store, master: MasterData, rules: Rules, run_date: date, fingerprint: str, approvers: list[str],
            authority: bool, today: date, cash: Optional[Decimal] = None,
            removed: Optional[dict[str, dict]] = None) -> dict[str, Any]:
    """Approve the run as it was shown. Refused if the books changed since; two different people above the limit."""
    p = rules.payments
    if not p.enabled:
        raise PaymentError(OFF)
    names = [n.strip() for n in approvers if n and n.strip()]
    if not names:
        raise PaymentError("Enter your name: a payment run is approved by a named person.")
    if not authority:
        raise PaymentError("Tick \"I have release authority for this run\": only a person with release authority "
                           "can approve it.")
    folder = Path(store.path).parent / "payments"
    folder.mkdir(parents=True, exist_ok=True)
    path: Optional[Path] = None
    try:
        with store.transaction():
            proposal = propose(store, master, rules, run_date, cash, removed)
            if proposal["fingerprint"] != fingerprint:
                raise PaymentError("The books have changed since this proposal was shown. Look at it again.")
            if not proposal["pay"]:
                raise PaymentError("There is nothing to pay in this run.")
            if proposal["needs_second"]:
                if len(names) < 2:
                    raise PaymentError(f"This run is above {fmt_inr(p.second_approver_above_inr)}. A second, different "
                                       f"person must approve.")
                if _person(names[0]) == _person(names[1]):
                    raise PaymentError("The second approver must be a different person from the first.")
            names = names[:2] if proposal["needs_second"] else names[:1]
            prefix = f"PR-{run_date:%Y%m%d}-"
            n = sum(1 for r in store.payment_runs() if r["run_id"].startswith(prefix)) + 1
            run_id = f"{prefix}{n:02d}"
            data = _export(proposal, master, rules, run_id)
            digest = hashlib.sha256(data).hexdigest()
            path = folder / f"{run_id}.csv"
            with path.open("xb") as handle:                  # never overwrite a proposal already written
                handle.write(data)
            at = datetime.combine(today, config.now_utc().timetz()).isoformat()
            lines = [{"run_id": run_id, "ledger_id": ln["ledger_id"], "vendor_id": ln["vendor_id"], "group_name": "pay",
                      "amount": ln["amount"], "discount": ln["discount"], "pay_by": ln["pay_by"].isoformat(),
                      "basis": ln["basis"], "why": ln["why"]} for ln in proposal["pay"]]
            lines += [{"run_id": run_id, "ledger_id": ln["ledger_id"], "vendor_id": ln["vendor_id"],
                       "group_name": "removed", "amount": ln["amount"], "discount": ln["discount"],
                       "pay_by": ln["pay_by"].isoformat(), "basis": ln["basis"], "why": ln["why"],
                       "removed_by": (removed or {})[ln["ledger_id"]]["by"],
                       "removed_reason": (removed or {})[ln["ledger_id"]]["reason"]} for ln in proposal["removed"]]
            store.add_payment_run({"run_id": run_id, "run_date": run_date.isoformat(), "horizon_days": p.horizon_days,
                                   "cash_limit": str(cash) if cash is not None else "", "status": "APPROVED",
                                   "lines_fingerprint": fingerprint, "total": str(proposal["total"]),
                                   "line_count": len(proposal["pay"]), "approved_by": names[0],
                                   "second_approved_by": names[1] if len(names) > 1 else "", "approved_at": at,
                                   "export_sha256": digest, "rules_version": rules.version}, lines)
            store.append_receipt("payment_run", run_id, {
                "actor": "human", "reviewer": names[0], "approvers": names,
                "decision": "payment run approved; nothing has been paid", "total": str(proposal["total"]),
                "line_ids": [ln["ledger_id"] for ln in proposal["pay"]], "export_sha256": digest,
                "rules_version": rules.version, "rules_sha256": rules.sha256})
    except Exception:
        if path is not None:
            path.unlink(missing_ok=True)                     # no record, no file
        raise
    return {"run_id": run_id, "path": path, "export_sha256": digest, "total": proposal["total"],
            "message": (f"Run {run_id} approved by {' and '.join(names)}. Nothing has been paid. The file is a review "
                        f"copy with masked accounts. The real bank file is prepared from the bank portal after approval.")}


def cancel(store: Store, run_id: str, person: str, reason: str, today: date) -> None:
    """A named person cancels an approved run with a reason. Its lines are proposed again."""
    person, reason = (person or "").strip(), (reason or "").strip()
    if not person or not reason:
        raise PaymentError("Enter your name and a reason: cancelling a run is recorded.")
    run = store.payment_run(run_id)
    if run is None or run["status"] != "APPROVED":
        raise PaymentError(f"There is no approved run {run_id}.")
    at = datetime.combine(today, config.now_utc().timetz()).isoformat()
    with store.transaction():
        store.cancel_payment_run(run_id, person, at, reason)
        store.append_receipt("payment_run_cancelled", run_id, {
            "actor": "human", "reviewer": person, "decision": "payment run cancelled; its lines are free again",
            "note": reason})


def approved_label(store: Store) -> dict[str, str]:
    """ledger_id -> "approved for payment in run {id}", for every screen that lists invoices."""
    return {lid: f"approved for payment in run {rid}" for lid, rid in store.locked_for_payment().items()}

