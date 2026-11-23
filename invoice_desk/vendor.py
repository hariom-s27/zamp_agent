"""Stage 5, Vendor: who is billing us, are they approved, and is the money going where
the vendor master says it should?

Identity rests on the GSTIN (it has a check character and does not vary between
invoices); the printed name is a cross-check. A bank account that differs from the
master is held even when everything else matches, because that is exactly how invoice
redirection fraud looks (D13).
"""
from __future__ import annotations

from typing import Optional

from . import textutil as T
from .masterdata import MasterData, Vendor
from .models import Finding, Invoice
from .rules import Rules, current_rules

STAGE = "vendor"


def _best_name(printed: str, vendor: Vendor) -> tuple[float, str]:
    scored = [(T.name_similarity(printed, name), name) for name in vendor.names]
    return max(scored) if scored else (0.0, "")


def check_vendor(inv: Invoice, master: MasterData, verified_accounts: Optional[dict[str, list[str]]] = None,
                 rules: Optional[Rules] = None) -> tuple[Optional[Vendor], list[Finding], list[dict], dict]:
    """Returns (vendor or None, findings, evidence blocks, summary for the run record).
    `verified_accounts` maps vendor id to bank accounts confirmed earlier by reviewers.
    How close a printed name must be to the master comes from rules.yaml (matching)."""
    close = (rules or current_rules()).matching
    findings: list[Finding] = []
    gstin = inv.get("vendor_gstin")
    printed = inv.get("vendor_name") or ""
    vendor = master.vendor_by_gstin(gstin)
    method, score, matched_name = "", 0.0, ""

    if vendor is not None:
        method = "GSTIN"
        if printed:
            score, matched_name = _best_name(printed, vendor)
            legal = T.name_similarity(printed, vendor.legal_name)
            if score < close.vendor_name_doubt:
                findings.append(Finding(
                    code="VENDOR_GSTIN_MISMATCH", stage=STAGE,
                    summary=f"GSTIN {gstin} belongs to {vendor.legal_name}, but the invoice is in the name of '{printed}'.",
                    expected=vendor.legal_name, observed=printed,
                    question="Confirm who issued this invoice before it is processed further."))
            elif legal < close.vendor_name_same:
                findings.append(Finding(
                    code="VENDOR_NAME_VARIANT", stage=STAGE,
                    summary=f"The invoice prints the vendor as '{printed}'. The GSTIN identifies "
                            f"{vendor.legal_name}; the printed name is a {score:.0%} match with '{matched_name}' "
                            f"in the vendor master.",
                    expected=vendor.legal_name, observed=printed))
    else:
        best: Optional[tuple[float, str, Vendor]] = None
        if printed:
            for candidate in master.vendors.values():
                s, name = _best_name(printed, candidate)
                if best is None or s > best[0]:
                    best = (s, name, candidate)
        if best and best[0] >= close.vendor_name_match:
            score, matched_name, vendor = best
            method = "name"
            if gstin:
                findings.append(Finding(
                    code="VENDOR_GSTIN_MISMATCH", stage=STAGE,
                    summary=f"The name matches {vendor.legal_name}, but the invoice shows GSTIN {gstin} and the "
                            f"vendor master has {vendor.gstin}.",
                    expected=vendor.gstin, observed=gstin,
                    question="Check the GSTIN on the document. If it really differs, the vendor master team must "
                             "confirm the registration before payment."))
            else:
                findings.append(Finding(
                    code="VENDOR_MATCHED_BY_NAME", stage=STAGE,
                    summary=f"No supplier GSTIN was found; the vendor was matched to {vendor.legal_name} by name only ({score:.0%}).",
                    expected="a supplier GSTIN on the invoice", observed="none found",
                    question="Confirm the vendor and ask for an invoice that shows their GSTIN.",
                    vendor_issue="the invoice does not show your GSTIN"))
        elif not gstin and not printed:
            findings.append(Finding(
                code="MISSING_VENDOR_ID", stage=STAGE,
                summary="Neither a supplier GSTIN nor a supplier name could be read from the document.",
                expected="a supplier name or GSTIN", observed="none found",
                question="Identify the vendor from the document by hand."))
        else:
            who = printed or "the supplier"
            findings.append(Finding(
                code="VENDOR_NOT_APPROVED", stage=STAGE,
                summary=f"{who}" + (f" (GSTIN {gstin})" if gstin else "") + " is not on the approved vendor list.",
                expected="a vendor on the approved list", observed=f"{who} {gstin or ''}".strip(),
                evidence={"closest": best[2].legal_name if best else None, "closest_score": round(best[0], 2) if best else None},
                question="Should this vendor be onboarded, or is the invoice misdirected? Nothing can be paid until "
                         "the vendor is approved."))

    rows = [{"Item": "Supplier GSTIN on invoice", "Value": gstin or "not found"},
            {"Item": "Supplier name on invoice", "Value": printed or "not found"}]
    summary: dict = {"vendor_id": None, "name": printed or None, "method": method or "none"}
    blocks: list[dict] = []
    if vendor is not None:
        summary = {"vendor_id": vendor.vendor_id, "name": vendor.legal_name, "method": method, "gstin": vendor.gstin,
                   "status": vendor.status, "payment_terms_days": vendor.payment_terms_days,
                   "phone_on_file": vendor.phone_on_file, "email": vendor.email}
        rows += [{"Item": "Matched vendor", "Value": f"{vendor.vendor_id} {vendor.legal_name}"},
                 {"Item": "Matched by", "Value": "GSTIN (exact)" if method == "GSTIN" else f"name ({score:.0%})"},
                 {"Item": "Name on invoice against master", "Value": f"{score:.0%} match with '{matched_name}'" if printed else "no name read"},
                 {"Item": "Vendor status", "Value": vendor.status}]
        if vendor.status != "APPROVED":
            findings.append(Finding(
                code="VENDOR_BLOCKED", stage=STAGE,
                summary=f"{vendor.legal_name} is marked {vendor.status} in the vendor master. No invoice from a blocked vendor is paid.",
                expected="APPROVED", observed=vendor.status,
                question="Procurement must lift the block before any invoice from this vendor is accepted."))
        # ---- bank details
        account, ifsc = inv.get("bank_account"), inv.get("bank_ifsc")
        accepted = [vendor.bank_account] + (verified_accounts or {}).get(vendor.vendor_id, [])
        accepted = [T.digits_only(a) for a in accepted if a]
        bank_rows = [{"": "Vendor master", "Bank": vendor.bank_name, "Account": vendor.bank_account, "IFSC": vendor.ifsc},
                     {"": "This invoice", "Bank": inv.get("bank_name") or "-", "Account": account or "not printed",
                      "IFSC": ifsc or "-"}]
        if account and T.digits_only(account) not in accepted:
            findings.append(Finding(
                code="BANK_DETAILS_CHANGED", stage=STAGE,
                summary=f"The invoice asks for payment to account {account}" + (f" (IFSC {ifsc})" if ifsc else "") +
                        f", but the vendor master has {vendor.bank_account} (IFSC {vendor.ifsc}). A changed account on an "
                        f"otherwise normal invoice is the usual pattern of payment-redirection fraud.",
                expected=f"{vendor.bank_account} / {vendor.ifsc}", observed=f"{account} / {ifsc or '-'}",
                evidence={"master_account": vendor.bank_account, "invoice_account": account,
                          "phone_on_file": vendor.phone_on_file},
                question=f"Call {vendor.legal_name} on {vendor.phone_on_file or 'the number on file'} (the number in the "
                         f"vendor master, not one from this invoice or email) and ask them to state their bank account. "
                         f"Record the call. A second, different person must then approve the change before anything is paid."))
        elif account and T.digits_only(account) != T.digits_only(vendor.bank_account):
            bank_rows.append({"": "Note", "Bank": "", "Account": "matches an account verified earlier on review (call-back and second approval)", "IFSC": ""})
        elif account and ifsc and vendor.ifsc and ifsc != vendor.ifsc:
            # the account is the one on file; an IFSC that reads differently is a reading slip, and
            # payment always uses the vendor master, never what is printed on an invoice
            bank_rows.append({"": "Note", "Bank": "", "Account": "account matches the master",
                              "IFSC": "read differently from the master; the master's IFSC is the one used"})
        blocks.append({"type": "table", "title": "Bank details: vendor master against this invoice", "rows": bank_rows})
    blocks.insert(0, {"type": "table", "title": "Vendor identification", "rows": rows})
    return vendor, findings, blocks, summary
