"""Stage 6, PO match: find the purchase order, compare line by line, check tolerances,
and check what earlier invoices have already used up.

Rules of the road:
  * comparison is on pre-tax values (D06);
  * a price passes only inside both the percentage and the rupee limit (D07);
  * billed-to-date plus this invoice must fit the PO, line by line and in total (D08, D09);
  * a PO that had to be inferred is only ever a suggestion for a person to confirm (D10).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from . import textutil as T
from .masterdata import MasterData, PoLine, PurchaseOrder, Vendor
from .models import Finding, Invoice, LineItem
from .rules import Limit, Matching, Rules, current_rules

STAGE = "po_match"
ZERO = Decimal("0")
money, num = T.fmt_inr, T.fmt_num


def breaks_limit(extra: Decimal, base: Decimal, limit: Limit) -> bool:
    """True when a positive overage exceeds the rupee limit or the percentage limit."""
    if extra <= 0:
        return False
    pct = extra / base * 100 if base > 0 else Decimal("1e9")
    return extra > limit.abs_inr or pct > limit.pct


def _similarity(item: LineItem, po_line: PoLine) -> float:
    score = T.desc_similarity(item.description, po_line.description)
    if item.hsn and po_line.hsn_sac and item.hsn == po_line.hsn_sac:
        score = min(1.0, score + 0.08)
    return score


def pair_lines(items: list[LineItem], po: PurchaseOrder, matching: Optional[Matching] = None
               ) -> list[tuple[LineItem, Optional[PoLine], float]]:
    """Pair each invoice line with its best unused PO line, strongest pairs first. An
    invoice may bill one item on two lines (two deliveries) where the PO has it once: a
    line left without a PO line of its own then shares one that is already paired, but
    only on a near-exact wording match. The similarity each kind of pair needs comes
    from rules.yaml (matching)."""
    matching = matching or current_rules().matching
    scored = sorted(((_similarity(it, pl), i, pl.line_no) for i, it in enumerate(items) for pl in po.lines),
                    reverse=True)
    threshold = matching.line_pairing_min_single if (len(items) == 1 and len(po.lines) == 1) else matching.line_pairing_min
    chosen: dict[int, tuple[PoLine, float]] = {}
    used: set[int] = set()
    for score, i, line_no in scored:
        if score < threshold or i in chosen or line_no in used:
            continue
        chosen[i] = (po.line(line_no), score)
        used.add(line_no)
    for score, i, line_no in scored:
        if i not in chosen and line_no in used and score >= matching.line_sharing_min:
            chosen[i] = (po.line(line_no), score)
    return [(it, *(chosen.get(i, (None, max((_similarity(it, pl) for pl in po.lines), default=0.0)))))
            for i, it in enumerate(items)]


def score_po(inv: Invoice, po: PurchaseOrder, usage: dict, rules: Rules) -> dict:
    """How well an invoice with no PO number fits one open PO: item wording, price
    agreement and whether the quantity still fits, weighted as rules.yaml says
    (po_inference.weights) and averaged over the lines."""
    w = rules.po_inference.weights
    pairs = pair_lines(inv.line_items, po, rules.matching)
    if not pairs:
        return {"po_number": po.po_number, "score": 0.0, "matched": 0, "lines": 0}
    total = 0.0
    matched = 0
    for item, po_line, sim in pairs:
        if po_line is None:
            total += w.wording * sim * 0.5
            continue
        matched += 1
        price = 0.5
        if item.unit_price_pre_tax is not None and po_line.unit_price > 0:
            diff = abs(item.unit_price_pre_tax - po_line.unit_price) / po_line.unit_price
            price = 1.0 if diff <= rules.tolerances.unit_price.pct / 100 else max(0.0, 1.0 - float(diff) * 4)
        fits = 0.5
        if item.qty is not None:
            before = usage["posted"].get(po_line.line_no, {}).get("qty", ZERO)
            fits = 1.0 if before + item.qty <= po_line.qty_ordered else 0.0
        total += w.wording * sim + w.price * price + w.quantity * fits
    score = total / len(pairs)
    inv_date = inv.day()
    if inv_date and inv_date < po.po_date:
        score *= 0.5
    return {"po_number": po.po_number, "score": round(score, 3), "matched": matched, "lines": len(pairs)}


def match_po(inv: Invoice, vendor: Optional[Vendor], master: MasterData, store, rules: Rules
             ) -> tuple[Optional[dict], list[Finding], list[dict], list[dict]]:
    """Returns (PO summary or None, findings, evidence blocks, allocations for the ledger)."""
    findings: list[Finding] = []
    blocks: list[dict] = []
    tol = rules.tolerances
    items = inv.line_items
    allocations = [{"invoice_line": it.line_no, "po_line_no": None, "description": it.description, "qty": it.qty,
                    "unit_price": it.unit_price_pre_tax, "amount": it.amount_pre_tax} for it in items]

    # ---------------------------------------------------------------- 1. which PO?
    po: Optional[PurchaseOrder] = None
    method, confidence = "", 1.0
    ref_field = inv.fields.get("po_reference")
    ref = inv.get("po_reference")
    candidates: list[dict] = []
    if ref:
        po = master.pos.get(str(ref))
        method = "quoted on the invoice"
        if ref_field and any("matched to the PO register" in n for n in ref_field.notes):
            method = "quoted on the invoice (reading corrected)"
            findings.append(Finding(code="PO_REFERENCE_CORRECTED", stage=STAGE,
                                    summary=f"PO number {ref}: {ref_field.notes[-1]}."))
        if po is None:
            findings.append(Finding(
                code="PO_NOT_FOUND", stage=STAGE,
                summary=f"The invoice quotes PO '{ref}', which is not in the PO register.",
                expected="a PO number from the register", observed=str(ref),
                question="Check the PO number with the vendor or the person who placed the order.",
                vendor_issue=f"the PO number quoted ({ref}) is not one of our purchase orders"))
    elif vendor is not None and items:
        open_pos = master.pos_for_vendor(vendor.vendor_id, open_only=True)
        candidates = sorted((score_po(inv, p, store.po_usage(p.po_number), rules) for p in open_pos),
                            key=lambda c: c["score"], reverse=True)
        best = candidates[0] if candidates else None
        runner_up = candidates[1]["score"] if len(candidates) > 1 else 0.0
        if best and best["score"] >= rules.po_inference.min_confidence and \
                best["score"] - runner_up >= rules.po_inference.min_margin:
            po = master.pos[best["po_number"]]
            method, confidence = "inferred from the lines", best["score"]
        else:
            shown = ", ".join(f"{c['po_number']} ({c['score']:.0%})" for c in candidates[:3]) or "none open"
            findings.append(Finding(
                code="NO_PO_FOUND", stage=STAGE,
                summary=f"The invoice quotes no PO and no open PO of {vendor.legal_name} fits its lines clearly "
                        f"enough (candidates: {shown}).",
                expected=f"one PO scoring at least {rules.po_inference.min_confidence:.0%} and "
                         f"{rules.po_inference.min_margin:.0%} ahead of the next",
                observed=shown, evidence={"candidates": candidates[:5]},
                question="Which PO, if any, covers this invoice? Without a PO it needs the budget owner's approval."))
    else:
        why = "the vendor is not identified" if vendor is None else "no line items were read"
        findings.append(Finding(
            code="NO_PO_FOUND", stage=STAGE,
            summary=f"The invoice quotes no PO, and one cannot be inferred because {why}.",
            expected="a PO number on the invoice", observed="none",
            question="Which PO, if any, covers this invoice?"))
    if candidates:
        blocks.append({"type": "table", "title": "Open POs of this vendor, scored against the invoice lines",
                       "rows": [{"PO": c["po_number"], "Score": f"{c['score']:.0%}",
                                 "Lines matched": f"{c['matched']} of {c['lines']}",
                                 "PO value": money(master.pos[c["po_number"]].total)} for c in candidates]})
    if po is None:
        return None, findings, blocks, allocations

    # ---------------------------------------------------------------- 2. the PO itself
    usage = store.po_usage(po.po_number)
    po_vendor = master.vendors.get(po.vendor_id)
    if vendor is not None and po.vendor_id != vendor.vendor_id:
        findings.append(Finding(
            code="PO_VENDOR_MISMATCH", stage=STAGE,
            summary=f"{po.po_number} was issued to {po_vendor.legal_name if po_vendor else po.vendor_id}, not to {vendor.legal_name}.",
            expected=vendor.legal_name, observed=po_vendor.legal_name if po_vendor else po.vendor_id,
            question="Is the PO number on the invoice wrong, or was the invoice sent by the wrong company?",
            vendor_issue=f"{po.po_number} was not issued to you"))
    if po.status != "OPEN":
        findings.append(Finding(
            code="PO_CLOSED", stage=STAGE, summary=f"{po.po_number} is {po.status}; no further invoices are expected on it.",
            expected="OPEN", observed=po.status,
            question=f"PO owner {po.buyer}: reopen the PO if this supply is valid, or have the vendor withdraw the invoice."))
    inv_date = inv.day()
    if inv_date and inv_date < po.po_date:
        findings.append(Finding(
            code="DATE_BEFORE_PO", stage=STAGE,
            summary=f"The invoice is dated {T.fmt_date(inv_date)}, before {po.po_number} was raised on {T.fmt_date(po.po_date)}.",
            expected=f"on or after {T.fmt_date(po.po_date)}", observed=T.fmt_date(inv_date),
            question="Was the order placed before the PO existed, or is the PO number wrong?"))
    if inv.tax_inclusive and items:
        rate = items[0].tax_rate
        example = next((it for it in items if it.unit_price is not None and it.unit_price_pre_tax is not None), None)
        text = f"Prices on the invoice include GST at {rate:g}%. " if rate else "Prices on the invoice include GST. "
        if example:
            text += (f"They were converted to pre-tax to compare with the PO; for example line {example.line_no}: "
                     f"{money(example.unit_price)} / {1 + (rate or ZERO) / 100:g} = {money(example.unit_price_pre_tax)}.")
        findings.append(Finding(code="TAX_INCLUSIVE_NORMALISED", stage=STAGE, summary=text))
        factor = 1 + (rate or ZERO) / 100
        blocks.append({"type": "table", "title": "Prices printed with GST, converted to pre-tax for the PO comparison", "rows": [
            {"Line": f"{it.line_no} {it.description[:40]}", "Price printed (with GST)": money(it.unit_price),
             "GST": f"{rate:g}%" if rate else "-", "Divided by": f"{factor:g}", "Pre-tax price": money(it.unit_price_pre_tax)}
            for it in items if it.unit_price is not None and it.unit_price_pre_tax is not None]})

    # ---------------------------------------------------------------- 3. line by line
    pairs = pair_lines(items, po, rules.matching)
    rows: list[dict] = []
    matched = 0
    taken: dict[int, dict] = {}       # what earlier lines of this invoice already bill on each PO line
    remaining_total = po.total - usage["posted_amount"]
    whole_invoice_lump = (len(items) == 1 and items[0].qty is None and pairs[0][1] is None)
    if whole_invoice_lump and inv.taxable is not None:
        # one lump-sum line against an itemised PO: match on the remaining PO value
        if not breaks_limit(abs(inv.taxable - remaining_total), max(remaining_total, Decimal(1)), tol.po_total):
            allocations.clear()
            for pl in po.lines:
                before = usage["posted"].get(pl.line_no, {"qty": ZERO, "amount": ZERO})
                left_qty, left_amt = pl.qty_ordered - before["qty"], pl.line_amount - before["amount"]
                if left_amt > 0:
                    allocations.append({"invoice_line": 1, "po_line_no": pl.line_no, "description": pl.description,
                                        "qty": left_qty if pl.match_basis == "QTY" else None,
                                        "unit_price": pl.unit_price if pl.match_basis == "QTY" else None,
                                        "amount": left_amt})
            matched = 1
            findings.append(Finding(
                code="MATCHED_AT_TOTAL_LEVEL", stage=STAGE,
                summary=f"The invoice is one lump sum of {money(inv.taxable)}, equal to everything still open on "
                        f"{po.po_number} ({money(remaining_total)}), so it was matched at total level."))
            rows.append({"Invoice line": f"1 {items[0].description[:48]}", "PO line": "all open lines",
                         "Billed": money(inv.taxable), "PO open": money(remaining_total), "Result": "matches the open balance"})
        else:
            findings.append(Finding(
                code="BUNDLED_PARTIAL", stage=STAGE,
                summary=f"The invoice is one lump sum of {money(inv.taxable)} with no item detail, and it does not "
                        f"equal what is open on {po.po_number} ({money(remaining_total)}), so it cannot be allocated to PO lines.",
                expected=money(remaining_total), observed=money(inv.taxable),
                question=f"PO owner {po.buyer}: which PO lines does this invoice cover? Ask the vendor for an itemised invoice.",
                vendor_issue="the invoice is not itemised, so it cannot be matched to the lines of the purchase order"))
            rows.append({"Invoice line": f"1 {items[0].description[:48]}", "PO line": "-",
                         "Billed": money(inv.taxable), "PO open": money(remaining_total), "Result": "cannot be allocated"})
    else:
        for (item, po_line, sim), alloc in zip(pairs, allocations):
            label = f"{item.line_no} {item.description[:44]}"
            if po_line is None:
                findings.append(Finding(
                    code="LINE_NOT_ON_PO", stage=STAGE,
                    summary=f"Line {item.line_no} '{item.description}' ({money(item.amount_pre_tax)}) does not match "
                            f"any line of {po.po_number} (closest wording match {sim:.0%}).",
                    expected="an item that is on the PO", observed=item.description,
                    question=f"PO owner {po.buyer}: was this item ordered? If so the PO needs an amendment; if not, "
                             f"the vendor should issue a corrected invoice.",
                    vendor_issue=f"line {item.line_no} ({item.description}) is not on {po.po_number}"))
                rows.append({"Invoice line": label, "PO line": "no match", "Billed": money(item.amount_pre_tax),
                             "PO open": "-", "Result": "not on the PO"})
                continue
            matched += 1
            alloc["po_line_no"] = po_line.line_no
            before = usage["posted"].get(po_line.line_no, {"qty": ZERO, "amount": ZERO})
            mine = taken.setdefault(po_line.line_no, {"qty": ZERO, "amount": ZERO})
            result: list[str] = []
            row = {"Invoice line": label, "PO line": f"{po_line.line_no} {po_line.description[:40]} ({sim:.0%})"}
            if po_line.match_basis == "AMOUNT" or item.qty is None:
                amount = item.amount_pre_tax or ZERO
                left = po_line.line_amount - before["amount"] - mine["amount"]
                mine["amount"] += amount
                row.update({"Billed": money(amount), "PO open": f"{money(left)} of {money(po_line.line_amount)}"})
                if po_line.match_basis == "QTY":
                    if breaks_limit(abs(amount - left), max(left, Decimal(1)), tol.po_total):
                        findings.append(Finding(
                            code="BUNDLED_PARTIAL", stage=STAGE,
                            summary=f"Line {item.line_no} gives an amount ({money(amount)}) but no quantity, and the amount "
                                    f"is not what is open on PO line {po_line.line_no} ({money(left)}).",
                            expected=money(left), observed=money(amount),
                            question=f"PO owner {po.buyer}: how many units does this line cover?",
                            vendor_issue=f"line {item.line_no} does not state a quantity"))
                        result.append("no quantity")
                    else:
                        alloc["qty"], alloc["unit_price"] = po_line.qty_ordered - before["qty"], po_line.unit_price
                row["Result"] = ", ".join(result) or "ok"
                rows.append(row)
                continue
            # ---- quantity (lines of this invoice that share the PO line are added up)
            earlier = mine["qty"]
            open_qty = po_line.qty_ordered - before["qty"] - earlier
            after_qty = before["qty"] + earlier + item.qty
            mine["qty"] += item.qty
            mine["amount"] += item.amount_pre_tax or ZERO
            limit_qty = po_line.qty_ordered * (1 + tol.quantity_over_pct / 100)
            row.update({"Billed": f"{num(item.qty)} {po_line.uom}",
                        "PO open": f"{num(open_qty)} of {num(po_line.qty_ordered)}"})
            if after_qty > limit_qty:
                over = after_qty - po_line.qty_ordered
                shared = f", {num(earlier)} on earlier lines of this invoice," if earlier else ""
                findings.append(Finding(
                    code="QTY_OVER_PO", stage=STAGE,
                    summary=f"Line {item.line_no} '{item.description}': {num(after_qty)} of {num(po_line.qty_ordered)} "
                            f"{po_line.uom} would be billed in total ({num(before['qty'])} on earlier invoices{shared} plus "
                            f"{num(item.qty)} now), which is {num(over)} more than the PO.",
                    expected=f"at most {num(max(open_qty, ZERO))} {po_line.uom} still open",
                    observed=f"{num(item.qty)} {po_line.uom} billed",
                    evidence={"po_line": po_line.line_no, "ordered": str(po_line.qty_ordered),
                              "billed_before": str(before["qty"]), "this_invoice": str(earlier + item.qty)},
                    question=f"PO owner {po.buyer}: were the extra {num(over)} {po_line.uom} ordered and received? "
                             f"Amend the PO, or ask the vendor to correct the invoice.",
                    vendor_issue=f"line {item.line_no} bills {num(item.qty)} {po_line.uom}, but only "
                                 f"{num(max(open_qty, ZERO))} remain on {po.po_number}"))
                result.append(f"{num(over)} over the PO quantity")
            elif po_line.qty_received is not None and after_qty > po_line.qty_received:
                findings.append(Finding(
                    code="GOODS_NOT_RECEIVED", stage=STAGE,
                    summary=f"Line {item.line_no} '{item.description}': {num(after_qty)} {po_line.uom} billed in total, "
                            f"but stores have recorded only {num(po_line.qty_received)} received.",
                    expected=f"billed quantity up to {num(po_line.qty_received)}", observed=num(after_qty),
                    question="Stores: has the rest been delivered? Record the receipt, or the invoice waits."))
                result.append("billed before receipt")
            # ---- price
            if item.unit_price_pre_tax is not None:
                diff = item.unit_price_pre_tax - po_line.unit_price
                pct = diff / po_line.unit_price * 100 if po_line.unit_price else ZERO
                extra = T.q2(diff * item.qty)
                row.update({"Invoice price": money(item.unit_price_pre_tax), "PO price": money(po_line.unit_price),
                            "Variance": "0" if diff == 0 else f"{money(diff)} ({pct:+.2f}%)"})
                if diff > 0:
                    out = pct > tol.unit_price.pct or extra > tol.unit_price.abs_inr
                    text = (f"Line {item.line_no} '{item.description}': invoice price {money(item.unit_price_pre_tax)} "
                            f"against PO price {money(po_line.unit_price)}, which is {money(diff)} higher ({pct:.2f}%), "
                            f"or {money(extra)} on this line. Tolerance is {num(tol.unit_price.pct)}% and "
                            f"{money(tol.unit_price.abs_inr, decimals=0)} per line.")
                    if out:
                        findings.append(Finding(
                            code="PRICE_VARIANCE", stage=STAGE, summary=text,
                            expected=f"up to {money(po_line.unit_price * (1 + tol.unit_price.pct / 100))} per {po_line.uom}",
                            observed=money(item.unit_price_pre_tax),
                            evidence={"po_line": po_line.line_no, "pct": f"{pct:.2f}", "line_extra": str(extra)},
                            question=f"PO owner {po.buyer}: accept the higher price and amend the PO, or ask the vendor "
                                     f"for a credit note of {money(extra)}?",
                            vendor_issue=f"line {item.line_no} is priced {money(diff)} above the agreed "
                                         f"{money(po_line.unit_price)} on {po.po_number}"))
                        result.append("price above tolerance")
                    else:
                        findings.append(Finding(code="PRICE_WITHIN_TOLERANCE", stage=STAGE, summary=text + " Inside both limits."))
                        result.append("price inside tolerance")
                elif diff < 0:
                    findings.append(Finding(
                        code="PRICE_BELOW_PO", stage=STAGE,
                        summary=f"Line {item.line_no}: billed at {money(item.unit_price_pre_tax)}, below the PO price "
                                f"{money(po_line.unit_price)}. The lower invoice amount is what gets paid."))
                    result.append("below PO price")
            row["Result"] = ", ".join(result) or "ok"
            rows.append(row)
    blocks.append({"type": "table", "title": f"Invoice lines against {po.po_number}", "rows": rows})

    # ---------------------------------------------------------------- 4. the PO as a whole
    this = inv.taxable or sum((a["amount"] or ZERO for a in allocations), ZERO)
    before_amt, po_total = usage["posted_amount"], po.total
    after = before_amt + this
    over = after - po_total
    history = [{"Invoice": e["invoice_no"] or "(no number)", "Date": T.fmt_date(e["invoice_date"]),
                "Status": e["status"].lower() + (" (history)" if e["origin"] == "history" else ""),
                "Taxable value": money(e["taxable"])} for e in usage["invoices"]]
    history.append({"Invoice": (inv.get("invoice_number") or "(no number)") + "  <- this invoice",
                    "Date": T.fmt_date(inv.get("invoice_date")), "Status": "being decided", "Taxable value": money(this)})
    history.append({"Invoice": "PO value", "Date": "", "Status": "", "Taxable value": money(po_total)})
    history.append({"Invoice": "Billed if this invoice is posted", "Date": "", "Status": "",
                    "Taxable value": f"{money(after)}" + (f"  ({money(over)} over)" if over > 0 else f"  ({money(-over)} left)")})
    blocks.append({"type": "table", "title": f"Running total on {po.po_number}", "rows": history})
    if breaks_limit(over, po_total, tol.po_total):
        n_prior = sum(1 for e in usage["invoices"] if e["status"] == "POSTED")
        findings.append(Finding(
            code="OVER_PO_BALANCE", stage=STAGE,
            summary=f"{po.po_number} is for {money(po_total)}. " +
                    (f"{money(before_amt)} is already billed on {n_prior} earlier invoice(s); this invoice adds "
                     f"{money(this)}, taking the total to {money(after)}, which is " if before_amt > 0 else
                     f"Nothing has been billed on it before; this invoice alone is {money(this)}, which is ") +
                    f"{money(over)} over the PO (tolerance {money(tol.po_total.abs_inr, decimals=0)} and {num(tol.po_total.pct)}%).",
            expected=f"at most {money(po_total - before_amt)} still open", observed=money(this),
            evidence={"po_total": str(po_total), "billed_before": str(before_amt), "this_invoice": str(this), "over": str(over)},
            question=f"PO owner {po.buyer}: approve an amendment of {money(over)} to {po.po_number}, or ask the vendor "
                     f"to correct the invoice.",
            vendor_issue=f"this invoice takes billing on {po.po_number} to {money(after)} against a PO value of {money(po_total)}"))

    if method.startswith("inferred"):
        clean = not any(f.code in {"LINE_NOT_ON_PO", "PRICE_VARIANCE", "QTY_OVER_PO", "OVER_PO_BALANCE",
                                   "BUNDLED_PARTIAL", "GOODS_NOT_RECEIVED"} for f in findings)
        runner = candidates[1] if len(candidates) > 1 else None
        findings.append(Finding(
            code="PO_INFERRED", stage=STAGE,
            summary=f"The invoice quotes no PO number. The best fit among {vendor.legal_name}'s open POs is "
                    f"{po.po_number} at {confidence:.0%}: {matched} of {len(items)} lines match its items" +
                    (f"; the next candidate, {runner['po_number']}, scores {runner['score']:.0%}" if runner else "") +
                    ". An inferred PO is never posted without a person confirming it.",
            expected="a PO number on the invoice", observed=f"none; inferred {po.po_number} ({confidence:.0%})",
            evidence={"candidates": candidates[:5]},
            question=f"PO owner {po.buyer}: does this invoice belong to {po.po_number}? " +
                     ("If yes, it matches on quantity, price and balance and can be approved." if clean else
                      "If yes, the other points listed here still need a decision.")))

    summary = {"po_number": po.po_number, "method": method, "confidence": round(confidence, 3), "buyer": po.buyer,
               "po_total": str(po_total), "billed_before": str(before_amt), "billed_after": str(after),
               "remaining_after": str(po_total - after), "pending_other": str(usage["pending_amount"]),
               "lines_matched": matched, "lines_total": len(items), "status": po.status,
               "matched_on": "item, quantity and price" if items and all(
                   it.qty is not None and (pl is None or pl.match_basis == "QTY") for it, pl, _ in pairs)
               else "item and amount"}
    blocks.insert(0, {"type": "kv", "title": "Purchase order", "rows": [
        {"Item": "PO", "Value": f"{po.po_number} dated {T.fmt_date(po.po_date)}, {po.status.lower()}"},
        {"Item": "How it was found", "Value": method + (f" ({confidence:.0%})" if method.startswith("inferred") else "")},
        {"Item": "PO owner", "Value": po.buyer},
        {"Item": "PO value / billed before / this invoice", "Value": f"{money(po_total)} / {money(before_amt)} / {money(this)}"},
    ]})
    return summary, findings, blocks, allocations
