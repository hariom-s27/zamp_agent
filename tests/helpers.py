"""Build Invoice objects directly, so rule tests do not depend on PDF reading."""
from decimal import Decimal

from invoice_desk.models import FIELD_LABELS, FieldValue, Invoice, LineItem, SourceRef
from invoice_desk.textutil import q2

BUYER_GSTIN = "29AAKCN4821M1ZX"
VENDOR_GSTIN = {"V001": "29AABCT7306K1Z2", "V002": "36AAHFD5127Q1Z0", "V003": "32AAVFM2290R1ZH",
                "V004": "27AALCA9084H1Z7", "V005": "29ABJFS6641D1ZY", "V006": "29AAGCV3518B1ZG"}


def D(x) -> Decimal:
    return Decimal(str(x))


def make_invoice(vendor="V001", number="TEST/001", inv_date="2026-11-10", po="PO-2026-0147",
                 lines=(("Ergonomic mesh office chair, high back", 40, "4500.00"),), gst_rate=18,
                 bank_account="004512309876", ifsc="HMBL0000451", vendor_name=None, **overrides) -> Invoice:
    """A self-consistent invoice. `lines` are (description, qty or None, unit price or amount).
    Any field can be overridden by name, e.g. total="100.00" or vendor_gstin=None."""
    gstin = VENDOR_GSTIN.get(vendor, vendor)
    items = []
    for n, (desc, qty, price) in enumerate(lines, 1):
        if qty is None:
            items.append(LineItem(line_no=n, description=desc, amount=q2(price), amount_pre_tax=q2(price), confidence=0.99))
        else:
            amount = q2(D(qty) * D(price))
            items.append(LineItem(line_no=n, description=desc, qty=D(qty), unit_price=D(price), amount=amount,
                                  unit_price_pre_tax=D(price), amount_pre_tax=amount, confidence=0.99))
    taxable = sum((it.amount for it in items), Decimal("0"))
    tax = q2(taxable * D(gst_rate) / 100)
    intra = gstin[:2] == BUYER_GSTIN[:2]
    values = {"vendor_gstin": gstin, "buyer_gstin": BUYER_GSTIN, "vendor_name": vendor_name, "invoice_number": number,
              "invoice_date": inv_date, "po_reference": po, "currency": "INR", "subtotal": f"{taxable}",
              "cgst": f"{q2(tax / 2)}" if intra else None, "sgst": f"{tax - q2(tax / 2)}" if intra else None,
              "igst": None if intra else f"{tax}", "tax_total": f"{tax}", "tax_rate": f"{D(gst_rate)}",
              "total": f"{taxable + tax}", "bank_account": bank_account, "bank_ifsc": ifsc}
    values.update(overrides)
    fields = {name: FieldValue(name=name, label=FIELD_LABELS.get(name, name), value=value,
                               confidence=0.99 if value not in (None, "") else 0.0,
                               source=SourceRef(snippet="test", method="text"),
                               missing_reason=None if value not in (None, "") else "not on the document")
              for name, value in values.items()}
    return Invoice(fields=fields, line_items=items)


def codes(findings) -> set[str]:
    return {f.code for f in findings}
