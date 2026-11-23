"""Stage 7, Duplicates: has this invoice, in any disguise, been seen before?

Three tiers (D11):
  1. the same file (SHA-256)                              -> certain
  2. the same vendor and the same normalised number       -> certain when the earlier one
     is booked; when the earlier one is still on hold, the newcomer is probably a
     corrected copy and is held for a person to choose between the two
  3. the same vendor, amount and lines under a new number -> rejected on that match
     itself. Whether the PO still has money on it does not matter: a PO with room does not
     show that the goods came twice. The one thing that clears it is evidence of a second
     delivery (goods receipts that cover both invoices). When the lines could not be read,
     the match is weaker, and a person is asked instead.
The exact-key idea is the fingerprint check from my ef-recon project; tiers 2 and 3
exist because a real duplicate rarely arrives with identical fields.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from . import textutil as T
from .masterdata import MasterData, Vendor
from .models import Finding, Invoice
from .rules import Rules

STAGE = "duplicates"
ZERO = Decimal("0")
NO_ROOM = {"QTY_OVER_PO", "OVER_PO_BALANCE"}


def _line_overlap(allocations: list[dict], entry_lines: list[dict], amount_tol: Decimal, min_similarity: float) -> float:
    """Share of lines that appear in both invoices with the same amount (0..1)."""
    if not allocations or not entry_lines:
        return 0.0
    used: set[int] = set()
    hits = 0
    for a in allocations:
        for j, e in enumerate(entry_lines):
            if j in used or a["amount"] is None or e["amount"] is None:
                continue
            same_amount = abs(a["amount"] - e["amount"]) <= amount_tol
            same_item = (a["po_line_no"] is not None and a["po_line_no"] == e["po_line_no"]) or \
                T.desc_similarity(a["description"] or "", e["description"] or "") >= min_similarity
            if same_amount and same_item:
                used.add(j)
                hits += 1
                break
    return hits / max(len(allocations), len(entry_lines))


def _where(entry: dict) -> str:
    return f"run {entry['run_id']}" if entry.get("run_id") else "prior history"


def check_duplicates(inv: Invoice, vendor: Optional[Vendor], file_sha256: str, store, rules: Rules,
                     allocations: list[dict], po_summary: Optional[dict], po_codes: set[str],
                     master: MasterData) -> tuple[list[Finding], list[dict]]:
    findings: list[Finding] = []
    rows: list[dict] = []
    cfg = rules.duplicates

    # ---- tier 1: the same file
    same_file = store.find_by_file(file_sha256)
    rows.append({"Check": "Same file received before (SHA-256)", "Result": "FOUND" if same_file else "clear",
                 "Detail": f"{same_file['ledger_id']}, {_where(same_file)}, {same_file['status'].lower()}" if same_file
                 else "no earlier invoice came from this exact file"})
    if same_file:
        findings.append(Finding(
            code="DUPLICATE_FILE", stage=STAGE,
            summary=f"This exact file was already processed ({_where(same_file)}, invoice "
                    f"{same_file['invoice_no'] or 'without number'}, currently {same_file['status'].lower()}). "
                    f"It is not booked a second time.",
            expected="a file not seen before", observed=f"same SHA-256 as {same_file['ledger_id']}",
            evidence={"original_ledger_id": same_file["ledger_id"], "original_run_id": same_file.get("run_id"),
                      "original_status": same_file["status"]},
            vendor_issue="this invoice was already received" +
                         (" and booked for payment" if same_file["status"] == "POSTED" else " and is under review")))

    entries = store.ledger_entries(vendor_id=vendor.vendor_id, statuses=("POSTED", "PENDING")) if vendor else []
    entries = [e for e in entries if not same_file or e["ledger_id"] != same_file["ledger_id"]]

    # ---- tier 2: the same invoice number
    number = inv.get("invoice_number")
    key = T.invoice_no_key(number)
    same_number = [e for e in entries if key and e["invoice_key"] == key]
    rows.append({"Check": "Same invoice number for this vendor",
                 "Result": ("FOUND" if any(e["status"] == "POSTED" for e in same_number) else "REVIEW") if same_number
                 else "clear" if key else "n/a",
                 "Detail": (f"{same_number[0]['invoice_no']} is on record ({_where(same_number[0])})" if same_number else
                            f"{len(entries)} earlier invoice(s) from this vendor compared" if key else "invoice has no number")})
    if same_number and not same_file:
        posted = [e for e in same_number if e["status"] == "POSTED"]
        orig = (posted or same_number)[0]
        if posted:
            findings.append(Finding(
                code="DUPLICATE_INVOICE_NUMBER", stage=STAGE,
                summary=f"Invoice number {number} is already on record for {vendor.legal_name}: {orig['invoice_no']} dated "
                        f"{T.fmt_date(orig['invoice_date'])} for {T.fmt_inr(orig['total'])} ({_where(orig)}, "
                        f"{orig['status'].lower()}). A vendor's invoice numbers are unique within a year.",
                expected="an invoice number not used before", observed=str(number),
                evidence={"original_ledger_id": orig["ledger_id"], "original_run_id": orig.get("run_id")},
                vendor_issue=f"invoice number {number} was already submitted on an invoice dated "
                             f"{T.fmt_date(orig['invoice_date'])}; a correction to a booked invoice needs a credit note"))
        else:
            # the earlier one was never approved: a different file with the same number is most likely
            # the vendor's corrected copy, and rejecting it outright would be the expensive mistake
            findings.append(Finding(
                code="RESUBMITTED_WHILE_ON_HOLD", stage=STAGE,
                summary=f"Invoice number {number} is already in the review queue ({_where(orig)}, on hold, "
                        f"{T.fmt_inr(orig['total'])}). This is a different file, so it may be the vendor's corrected copy.",
                expected="an invoice number not used before", observed=f"{number}, also on hold as {_where(orig)}",
                evidence={"original_ledger_id": orig["ledger_id"], "original_run_id": orig.get("run_id")},
                question=f"Compare this with {_where(orig)}. If this is the corrected invoice, reject the earlier one "
                         f"in the review queue and then approve this one. If it was sent by mistake, reject this one."))

    # ---- tier 3: the same content under another number
    total, inv_date = inv.total, inv.day()
    detail = "needs a total and a date to compare" if (total is None or inv_date is None) else "no earlier invoice has the same amount inside the window"
    result = "n/a" if (total is None or inv_date is None) else "clear"
    if total is not None and inv_date is not None and not same_file and not same_number:
        for e in entries:
            if e["total"] is None or abs(e["total"] - total) > cfg.amount_tolerance_inr or not e["invoice_date"]:
                continue
            days = abs((inv_date - date.fromisoformat(e["invoice_date"][:10])).days)
            if days > cfg.window_days:
                continue
            overlap = _line_overlap(allocations, e["lines"], cfg.amount_tolerance_inr, cfg.line_similarity_min)
            if allocations and overlap < cfg.line_similarity_min:
                continue
            same_po = bool(po_summary) and e["po_number"] == po_summary["po_number"]
            no_room = bool(po_codes & NO_ROOM)
            received_twice = _receipts_cover(allocations, po_summary, master, store)
            tracked = _receipts_tracked(allocations, po_summary, master)
            evidence = {"original_ledger_id": e["ledger_id"], "original_run_id": e.get("run_id"),
                        "original_invoice_no": e["invoice_no"], "days_apart": days, "line_overlap": round(overlap, 2),
                        "same_po": same_po, "po_has_room": not no_room, "receipts_tracked": tracked}
            base = (f"This looks like invoice {e['invoice_no'] or '(no number)'} dated {T.fmt_date(e['invoice_date'])} "
                    f"({_where(e)}, {e['status'].lower()}): same vendor, same total {T.fmt_inr(total)}, "
                    f"{'the same line items' if allocations else 'line items not readable'}, dated {days} day(s) apart, "
                    f"under a different invoice number ({number or 'none'}).")
            if received_twice:
                result, detail = "clear", (f"same amount as {e['invoice_no']} {days} day(s) earlier, but goods receipts "
                                           f"cover both deliveries")
            elif allocations:
                why = ("Stores have recorded no second delivery of these goods" if tracked else
                       "Nothing on record shows a second delivery")
                room = (f" {po_summary['po_number']} is also fully billed by the earlier invoice." if same_po and no_room
                        else " That the PO still has money on it is not evidence that the goods came twice."
                        if po_summary else "")
                findings.append(Finding(
                    code="DUPLICATE_CONTENT", stage=STAGE,
                    summary=base + f" {why}, so this is treated as a second copy of the same invoice." + room,
                    expected="an invoice for goods or services not billed before, or a recorded second delivery",
                    observed=f"repeat of {e['invoice_no']}", evidence=evidence,
                    vendor_issue=f"this appears to be a second copy of invoice {e['invoice_no']} dated "
                                 f"{T.fmt_date(e['invoice_date'])}, which we already have; if it is a separate supply, "
                                 f"reply with the delivery challan for it"))
                result, detail = "FOUND", f"matches {e['invoice_no']} line for line; no second delivery on record"
            else:
                findings.append(Finding(
                    code="POSSIBLE_DUPLICATE", stage=STAGE,
                    summary=base + " The lines could not be compared, so the match rests on vendor, amount and date alone.",
                    expected="line items that can be compared, or evidence of a second delivery",
                    observed=f"same vendor, amount and date window as {e['invoice_no']}", evidence=evidence,
                    question=f"Is this a second delivery, or a re-send of invoice {e['invoice_no']}? Check the delivery "
                             f"challan or goods receipt before approving."))
                result, detail = "REVIEW", f"same vendor and amount as {e['invoice_no']}; lines could not be compared"
            break
    rows.append({"Check": f"Same content under another number (within {cfg.window_days} days)", "Result": result, "Detail": detail})
    return findings, [{"type": "table", "title": "Duplicate checks", "rows": rows}]


def _receipts_tracked(allocations: list[dict], po_summary: Optional[dict], master: MasterData) -> bool:
    """True when stores record receipts for every PO line this invoice bills."""
    po = master.pos.get(po_summary["po_number"]) if po_summary else None
    if po is None or not allocations:
        return False
    lines = [po.line(a["po_line_no"]) if a["po_line_no"] is not None else None for a in allocations]
    return all(pl is not None and pl.qty_received is not None for pl in lines)


def _receipts_cover(allocations: list[dict], po_summary: Optional[dict], master: MasterData, store) -> bool:
    """True when every matched PO line tracks receipts and the quantity received covers
    everything billed including this invoice: the goods really did arrive twice."""
    if not po_summary or not allocations:
        return False
    po = master.pos.get(po_summary["po_number"])
    if po is None:
        return False
    usage = store.po_usage(po.po_number)
    for a in allocations:
        pl = po.line(a["po_line_no"]) if a["po_line_no"] is not None else None
        if pl is None or pl.qty_received is None or a["qty"] is None:
            return False
        before = usage["posted"].get(pl.line_no, {}).get("qty", ZERO)
        if before + a["qty"] > pl.qty_received:
            return False
    return True
