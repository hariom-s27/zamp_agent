"""Generate every piece of test data: vendor master, PO register, prior invoice
history, sample invoice PDFs (five layouts, two of them made to look scanned), bad
inputs, truth files and the sample manifest.

    python -m scripts.generate_test_data            # rewrite data/ and samples/

Everything is fictional: buyer, vendors, GSTINs, bank names and account numbers are
invented, and every PDF says so in its footer. Output is seeded and reproducible on the
machine that runs it; the committed files are the reference.

The idea of a seeded generator that makes deliberately messy inputs comes from my
ef-recon project (src/generate_synthetic_bills.py). Nothing else is shared with it.
"""
from __future__ import annotations

import csv
import io
import json
import random
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.utils import ImageReader, simpleSplit  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from invoice_desk.textutil import amount_to_words, fmt_inr, make_gstin, q2, state_name  # noqa: E402

PAGE_W, PAGE_H = A4
DISCLAIMER = "Synthetic test document for a case study. Fictional parties. Not a real invoice."
SEED = 20261121

# --------------------------------------------------------------------------- master data

BUYER = {
    "name": "Nilgiri Precision Components Pvt Ltd",
    "address": ["Plot 42, Peenya Industrial Area, Phase II", "Bengaluru 560058, Karnataka"],
    "state_code": "29",
    "gstin": make_gstin("29", "AAKCN4821M"),
}

VENDORS = [
    {"vendor_id": "V001", "legal_name": "Tungabhadra Office Furnishings Pvt Ltd",
     "aliases": "Tungabhadra Furnishings|Tungabhadra Office Furnishing", "pan": "AABCT7306K",
     "state_code": "29", "address": ["No. 118, 4th Cross, Rajajinagar Industrial Town", "Bengaluru 560044, Karnataka"],
     "bank_name": "Hoysala Mercantile Bank", "bank_branch": "Rajajinagar", "bank_account": "004512309876",
     "ifsc": "HMBL0000451", "status": "APPROVED", "payment_terms_days": 30,
     "phone_on_file": "080-5550-0118", "email": "accounts@tungabhadra-furnishings.example", "layout": "classic"},
    {"vendor_id": "V002", "legal_name": "Deccan Quill Infotech LLP",
     "aliases": "Deccan Quill Infotech|DQ Infotech", "pan": "AAHFD5127Q",
     "state_code": "36", "address": ["Unit 7B, Cyber Pearl Annexe, Madhapur", "Hyderabad 500081, Telangana"],
     "bank_name": "Golconda United Bank", "bank_branch": "Madhapur", "bank_account": "918273645501",
     "ifsc": "GUBL0007712", "status": "APPROVED", "payment_terms_days": 45,
     "phone_on_file": "040-5550-0177", "email": "billing@deccanquill.example", "layout": "modern"},
    {"vendor_id": "V003", "legal_name": "Malabar Polywrap Packaging Co",
     "aliases": "Malabar Polywrap|Malabar Polywrap Packaging Company", "pan": "AAVFM2290R",
     "state_code": "32", "address": ["Door 14/221, Industrial Estate, Kalamassery", "Kochi 683104, Kerala"],
     "bank_name": "Periyar Co-operative Bank", "bank_branch": "Kalamassery", "bank_account": "30211456789",
     "ifsc": "PCBL0003021", "status": "APPROVED", "payment_terms_days": 30,
     "phone_on_file": "0484-555-0142", "email": "office@malabarpolywrap.example", "layout": "typewriter"},
    {"vendor_id": "V004", "legal_name": "Aravalli Shield Safety Equipment Pvt Ltd",
     "aliases": "Aravalli Shield|Aravalli Shield Safety", "pan": "AALCA9084H",
     "state_code": "27", "address": ["Gat No. 512, Chakan Industrial Area, Phase I", "Pune 410501, Maharashtra"],
     "bank_name": "Sinhagad Industrial Bank", "bank_branch": "Chakan", "bank_account": "620045871230",
     "ifsc": "SIBL0006200", "status": "APPROVED", "payment_terms_days": 30,
     "phone_on_file": "02135-555-019", "email": "ar@aravallishield.example", "layout": "grid"},
    {"vendor_id": "V005", "legal_name": "Sahyadri Inkwell Supplies",
     "aliases": "Sahyadri Inkwell|Sahyadri Ink Well Supplies", "pan": "ABJFS6641D",
     "state_code": "29", "address": ["Shop 9, Avenue Road Cross, Chickpet", "Bengaluru 560053, Karnataka"],
     "bank_name": "Cauvery Urban Co-op Bank", "bank_branch": "Chickpet", "bank_account": "117700452981",
     "ifsc": "CUCB0001177", "status": "APPROVED", "payment_terms_days": 15,
     "phone_on_file": "080-5550-0109", "email": "sales@sahyadriinkwell.example", "layout": "retail"},
    {"vendor_id": "V006", "legal_name": "Vindhya Spark Facility Services Pvt Ltd",
     "aliases": "Vindhya Spark|Vindhya Spark Facility Services", "pan": "AAGCV3518B",
     "state_code": "29", "address": ["2nd Floor, 27 Lalbagh Road", "Bengaluru 560027, Karnataka"],
     "bank_name": "Hoysala Mercantile Bank", "bank_branch": "Lalbagh", "bank_account": "004598001234",
     "ifsc": "HMBL0000459", "status": "APPROVED", "payment_terms_days": 30,
     "phone_on_file": "080-5550-0127", "email": "finance@vindhyaspark.example", "layout": "classic"},
    {"vendor_id": "V007", "legal_name": "Konkan Fasteners & Hardware",
     "aliases": "Konkan Fasteners", "pan": "AAOFK1182C",
     "state_code": "30", "address": ["Plot 7, Verna Industrial Estate", "Verna 403722, Goa"],
     "bank_name": "Mandovi Traders Bank", "bank_branch": "Verna", "bank_account": "550012340099",
     "ifsc": "MTBL0005500", "status": "BLOCKED", "payment_terms_days": 30,
     "phone_on_file": "0832-555-0107", "email": "accounts@konkanfasteners.example", "layout": "classic"},
]
for _v in VENDORS:
    _v["gstin"] = make_gstin(_v["state_code"], _v["pan"])
VENDOR = {v["vendor_id"]: v for v in VENDORS}

# po_number, po_date, vendor, status, buyer, lines: (item_code, description, hsn, uom, qty, unit_price, basis, received)
PURCHASE_ORDERS = [
    ("PO-2026-0099", "2026-06-10", "V001", "CLOSED", "R. Iyer (Procurement)", [
        ("FUR-FC-3DR", "Filing cabinet, 3 drawer", "9403", "Nos", 5, "7600.00", "QTY", 5)]),
    ("PO-2026-0147", "2026-10-20", "V001", "OPEN", "R. Iyer (Procurement)", [
        ("FUR-CH-ERG", "Ergonomic mesh office chair, high back", "9401", "Nos", 40, "4500.00", "QTY", 40),
        ("FUR-WS-150", "Height adjustable workstation 1500 mm", "9403", "Nos", 10, "14250.00", "QTY", 10),
        ("FUR-CB-4SH", "Steel storage cupboard, 4 shelf", "9403", "Nos", 6, "9800.00", "QTY", 6)]),
    ("PO-2026-0152", "2026-10-28", "V001", "OPEN", "R. Iyer (Procurement)", [
        ("FUR-CH-VIS", "Visitor chair, cantilever frame", "9401", "Nos", 12, "2950.00", "QTY", 12),
        ("FUR-TB-CNF", "Conference table 8 seater, laminate top", "9403", "Nos", 4, "21500.00", "QTY", 4)]),
    ("PO-2026-0160", "2026-04-01", "V006", "OPEN", "M. Fernandes (Admin)", [
        ("SVC-FM-AMC", "Facility management and housekeeping services, FY 2026-27", "998533", "Lot", 1, "720000.00", "AMOUNT", "")]),
    ("PO-2026-0171", "2026-10-22", "V002", "OPEN", "S. Menon (IT Procurement)", [
        ("IT-LT-14", "Business laptop 14 inch, 16 GB RAM, 512 GB SSD", "8471", "Nos", 8, "58500.00", "QTY", 8),
        ("IT-DK-USBC", "USB-C docking station with power delivery", "8473", "Nos", 8, "9200.00", "QTY", 8)]),
    ("PO-2026-0178", "2026-11-02", "V002", "OPEN", "S. Menon (IT Procurement)", [
        ("IT-SW-24", "Managed network switch 24 port gigabit", "8517", "Nos", 6, "18500.00", "QTY", 6),
        ("IT-UPS-3K", "Online UPS 3 kVA with batteries", "8504", "Nos", 2, "42000.00", "QTY", 2)]),
    ("PO-2026-0188", "2026-10-15", "V003", "OPEN", "A. Kulkarni (Stores)", [
        ("PKG-STRAP", "PP strapping roll 12 mm x 1000 m", "3920", "Roll", 60, "1890.00", "QTY", ""),
        ("PKG-STRW", "Stretch wrap film roll 23 micron", "3920", "Roll", 50, "640.00", "QTY", "")]),
    ("PO-2026-0193", "2026-10-12", "V004", "OPEN", "P. Deshmukh (EHS)", [
        ("SAF-GD-4G", "Portable multi-gas detector, 4 gas", "9027", "Nos", 20, "25000.00", "QTY", "")]),
    ("PO-2026-0201", "2026-11-05", "V004", "OPEN", "P. Deshmukh (EHS)", [
        ("SAF-FE-6K", "Fire extinguisher ABC type 6 kg", "8424", "Nos", 30, "2150.00", "QTY", 30),
        ("SAF-SG-LED", "Emergency exit signage, LED", "9405", "Nos", 20, "1480.00", "QTY", 20)]),
    ("PO-2026-0212", "2026-11-01", "V003", "OPEN", "A. Kulkarni (Stores)", [
        ("PKG-STRW", "Stretch wrap film roll 23 micron", "3920", "Roll", 120, "640.00", "QTY", ""),
        ("PKG-TAPE", "BOPP packing tape 48 mm, carton of 72", "3919", "Carton", 25, "2340.00", "QTY", ""),
        ("PKG-BUBL", "Bubble wrap roll 1 m x 100 m", "3921", "Roll", 40, "1150.00", "QTY", "")]),
    ("PO-2026-0224", "2026-11-06", "V005", "OPEN", "R. Iyer (Procurement)", [
        ("OFF-TN-26A", "Laser toner cartridge 26A compatible", "8443", "Nos", 20, "3200.00", "QTY", 20),
        ("OFF-SH-20L", "Paper shredder cross cut 20 litre", "8472", "Nos", 4, "7450.00", "QTY", 4)]),
    ("PO-2026-0229", "2026-11-09", "V005", "OPEN", "R. Iyer (Procurement)", [
        ("OFF-TN-26A", "Laser toner cartridge 26A compatible", "8443", "Nos", 30, "3200.00", "QTY", 30)]),
]

# Invoices that were already posted before this tool existed (the seeded ledger).
# (ledger_id, vendor, invoice_no, invoice_date, po, [(po_line, description, qty, unit_price, amount)], gst_rate)
HISTORY = [
    ("H001", "V004", "ASS/2026/0871", "2026-10-20", "PO-2026-0193",
     [(1, "Portable multi-gas detector, 4 gas", 8, "25000.00", "200000.00")], 18),
    ("H002", "V004", "ASS/2026/0902", "2026-10-30", "PO-2026-0193",
     [(1, "Portable multi-gas detector, 4 gas", 8, "25000.00", "200000.00")], 18),
    ("H003", "V002", "DQI/26-27/1187", "2026-11-03", "PO-2026-0171",
     [(1, "Business laptop 14 inch, 16 GB RAM, 512 GB SSD", 8, "58500.00", "468000.00"),
      (2, "USB-C docking station with power delivery", 8, "9200.00", "73600.00")], 18),
] + [
    (f"H{4 + i:03d}", "V006", f"VSF/26-27/{4 + i:02d}", f"2026-{4 + i:02d}-15", "PO-2026-0160",
     [(1, "Facility management and housekeeping services", "", "", "60000.00")], 18)
    for i in range(7)  # April to October
]


def D(x) -> Decimal:
    return Decimal(str(x))


def money(x, prefix: str = "") -> str:
    return prefix + fmt_inr(x, symbol="")


# --------------------------------------------------------------------------- invoice specs

def line(desc, hsn, qty, uom, rate):
    return {"description": desc, "hsn": hsn, "qty": qty, "uom": uom, "rate": D(rate)}


def spec(**kw) -> dict:
    base = {"scanned": False, "tax_inclusive": False, "gst_rate": 18, "po_ref": None, "po_date": None,
            "invoice_no": None, "labels": {}, "date_format": None, "vendor_name_printed": None,
            "bank_override": None, "kind": "invoice", "service_period": None, "scan": None}
    base.update(kw)
    return base


SAMPLES = [
    spec(id="01_happy_path_furniture", layout="classic", vendor="V001", invoice_no="TOF/26-27/0412",
         invoice_date=date(2026, 11, 5), po_ref="PO-2026-0147", po_date=date(2026, 10, 20),
         lines=[line("Ergonomic Mesh Office Chair (High Back)", "9401", 40, "Nos", "4500.00"),
                line("Height Adjustable Workstation 1500 mm", "9403", 10, "Nos", "14250.00"),
                line("Steel Storage Cupboard, 4 Shelf", "9403", 6, "Nos", "9800.00")]),
    spec(id="02_scanned_clean_safety", layout="grid", vendor="V004", invoice_no="ASS/2026/0941",
         invoice_date=date(2026, 11, 14), po_ref="PO-2026-0201", po_date=date(2026, 11, 5), scanned=True,
         scan={"angle": -0.7, "noise": 7.0, "seed": 11},
         lines=[line("Fire Extinguisher ABC Type 6 kg", "8424", 30, "Nos", "2150.00"),
                line("Emergency Exit Signage LED", "9405", 20, "Nos", "1480.00")]),
    spec(id="03_monthly_service_instalment", layout="modern", vendor="V006", invoice_no="VSF/26-27/11",
         invoice_date=date(2026, 11, 15), po_ref="PO-2026-0160", po_date=date(2026, 4, 1),
         service_period="November 2026",
         labels={"inv_no": "Invoice No.", "po": "PO Number", "desc": "Description of Service",
                 "hsn": "SAC", "amount": "Amount"},
         lines=[{"description": "Facility management and housekeeping services for November 2026 (instalment 8 of 12)",
                 "hsn": "998533", "qty": None, "uom": None, "rate": None, "amount": D("60000.00")}]),
    spec(id="04_E1_split_po_overbilling", layout="grid", vendor="V004", invoice_no="ASS/2026/0933",
         invoice_date=date(2026, 11, 12), po_ref="PO-2026-0193", po_date=date(2026, 10, 12),
         lines=[line("Portable Multi-Gas Detector, 4 Gas", "9027", 5, "Nos", "25000.00")]),
    spec(id="05_E2_disguised_duplicate", layout="modern", vendor="V002", invoice_no="DQI/26-27/1193",
         invoice_date=date(2026, 11, 5), po_ref="PO-2026-0171", po_date=date(2026, 10, 22),
         lines=[line("Business Laptop 14 inch, 16 GB RAM, 512 GB SSD", "8471", 8, "Nos", "58500.00"),
                line("USB-C Docking Station with Power Delivery", "8473", 8, "Nos", "9200.00")]),
    spec(id="06_E3_scan_no_number_implied_po", layout="typewriter", vendor="V003", invoice_no=None,
         invoice_date=date(2026, 11, 12), po_ref=None, scanned=True,
         scan={"angle": 0.9, "noise": 9.0, "seed": 23},
         lines=[line("Stretch wrap film roll 23 micron", "3920", 120, "Roll", "640.00"),
                line("BOPP packing tape 48 mm (carton of 72)", "3919", 25, "Carton", "2340.00"),
                line("Bubble wrap roll 1 m x 100 m", "3921", 40, "Roll", "1150.00")]),
    spec(id="07_E4a_tax_inclusive_within_tolerance", layout="retail", vendor="V005", invoice_no="SIS/2311",
         invoice_date=date(2026, 11, 13), po_ref="PO-2026-0224", tax_inclusive=True,
         lines=[line("Laser Toner Cartridge 26A (Compatible)", "8443", 20, "Nos", "3780.00"),
                line("Paper Shredder Cross Cut 20 Litre", "8472", 4, "Nos", "8791.00")]),
    spec(id="08_E4b_tax_inclusive_over_tolerance", layout="retail", vendor="V005", invoice_no="SIS/2327",
         invoice_date=date(2026, 11, 17), po_ref="PO-2026-0229", tax_inclusive=True,
         lines=[line("Laser Toner Cartridge 26A (Compatible)", "8443", 30, "Nos", "3835.00")]),
    spec(id="09_fraud_guard_bank_change", layout="classic", vendor="V001", invoice_no="TOF/26-27/0431",
         invoice_date=date(2026, 11, 14), po_ref="PO-2026-0152", po_date=date(2026, 10, 28),
         vendor_name_printed="Tungabhadra Office Furnishing Pvt. Ltd.",
         bank_override={"bank_name": "Chalukya Urban Bank", "bank_branch": "Yeshwanthpur",
                        "bank_account": "770031245566", "ifsc": "CHUB0002290"},
         lines=[line("Visitor Chair, Cantilever Frame", "9401", 12, "Nos", "2950.00"),
                line("Conference Table 8 Seater, Laminate Top", "9403", 4, "Nos", "21500.00")]),
    spec(id="10_ai_assist_letter_invoice", layout="letter", vendor="V002", invoice_no="DQI/26-27/1213",
         invoice_date=date(2026, 11, 14), po_ref="PO-2026-0178", po_date=date(2026, 11, 2),
         lines=[line("Managed network switch 24 port gigabit", "8517", 6, "Nos", "18500.00"),
                line("Online UPS 3 kVA with batteries", "8504", 2, "Nos", "42000.00")]),
]

MANIFEST_META = {
    "01_happy_path_furniture": ("Happy path", "core", "APPROVE", [],
        "Clean text PDF, PO quoted, three itemised lines, CGST and SGST. Everything matches."),
    "02_scanned_clean_safety": ("Scanned invoice, clean", "core", "APPROVE", [],
        "A tilted, noisy scan with no text layer. OCR reads it, arithmetic confirms the read, and it posts untouched."),
    "03_monthly_service_instalment": ("Monthly service instalment", "core", "APPROVE", [],
        "Instalment 8 of 12 against a Rs 7,20,000 service PO. A partial bill that fits the balance is normal."),
    "04_E1_split_po_overbilling": ("E1 Split PO, third invoice over-bills", "edge", "HOLD",
        ["QTY_OVER_PO", "OVER_PO_BALANCE"],
        "PO for 20 units and Rs 5,00,000. Two invoices already posted for 16 units. This one bills 5 more."),
    "05_E2_disguised_duplicate": ("E2 Disguised duplicate", "edge", "REJECT", ["DUPLICATE_CONTENT"],
        "Same vendor, lines and amount as an invoice posted two days earlier, under a new invoice number."),
    "06_E3_scan_no_number_implied_po": ("E3 Scan, no invoice number, PO implied", "edge", "HOLD",
        ["MISSING_INVOICE_NUMBER", "PO_INFERRED"],
        "Scanned bill with the number left blank and no PO quoted. The PO is inferred and a person is asked to confirm."),
    "07_E4a_tax_inclusive_within_tolerance": ("E4 Tax-inclusive prices, inside tolerance", "edge", "APPROVE", [],
        "Prices include GST; the PO is pre-tax. After conversion one price is Rs 3.39 over, inside both limits."),
    "08_E4b_tax_inclusive_over_tolerance": ("E4 Tax-inclusive prices, outside tolerance", "edge", "HOLD",
        ["PRICE_VARIANCE", "OVER_PO_BALANCE"],
        "Same vendor and item; after conversion the price is Rs 50 over (1.56%), outside the 1% limit."),
    "09_fraud_guard_bank_change": ("Fraud guard: bank account changed", "guard", "HOLD", ["BANK_DETAILS_CHANGED"],
        "A perfect match on the PO, but the bank account differs from the vendor master and the name is a variant."),
    "10_ai_assist_letter_invoice": ("AI assist: invoice written as a letter", "assist", "HOLD",
        ["LINE_ITEMS_UNREADABLE", "MISSING_INVOICE_NUMBER", "MISSING_TOTAL"],
        "Free-form prose, no table. Rules alone hold it. With LLM assist on, the lines are read and verified against the page."),
}
# What a sample should give when the LLM assist actually supplied values (live or replayed).
WITH_LLM = {"10_ai_assist_letter_invoice": {"expected_verdict": "APPROVE", "expected_reasons": []}}


def totals(inv: dict) -> dict:
    """Printed line amounts plus taxable, tax split and total for one invoice spec."""
    vendor = VENDOR[inv["vendor"]]
    rate = D(inv["gst_rate"])
    amounts = []
    for ln in inv["lines"]:
        amt = ln.get("amount")
        if amt is None:
            amt = q2(D(ln["qty"]) * ln["rate"])
        amounts.append(q2(amt))
    printed_sum = sum(amounts, D(0))
    if inv["tax_inclusive"]:
        total = q2(printed_sum)
        taxable = q2(total / (1 + rate / 100))
        tax = q2(total - taxable)
    else:
        taxable = q2(printed_sum)
        tax = q2(taxable * rate / 100)
        total = q2(taxable + tax)
    intra = vendor["state_code"] == BUYER["state_code"]
    if intra:
        cgst = q2(tax / 2)
        sgst = q2(tax - cgst)
        igst = D(0)
    else:
        cgst = sgst = D(0)
        igst = tax
    return {"amounts": amounts, "taxable": taxable, "tax": tax, "cgst": cgst, "sgst": sgst,
            "igst": igst, "total": total, "intra": intra, "rate": rate}


def fmt_d(d: date, pattern: str) -> str:
    return d.strftime(pattern)


# --------------------------------------------------------------------------- drawing helpers

class Pen:
    """Thin wrapper over a reportlab canvas with top-left style helpers."""

    def __init__(self, c: canvas.Canvas):
        self.c = c

    def text(self, x, y, s, font="Helvetica", size=9, grey=0.0):
        self.c.setFillGray(grey)
        self.c.setFont(font, size)
        self.c.drawString(x, y, str(s))
        self.c.setFillGray(0)

    def rtext(self, x, y, s, font="Helvetica", size=9, grey=0.0):
        self.c.setFillGray(grey)
        self.c.setFont(font, size)
        self.c.drawRightString(x, y, str(s))
        self.c.setFillGray(0)

    def ctext(self, x, y, s, font="Helvetica", size=9, grey=0.0):
        self.c.setFillGray(grey)
        self.c.setFont(font, size)
        self.c.drawCentredString(x, y, str(s))
        self.c.setFillGray(0)

    def hline(self, x0, x1, y, width=0.6, grey=0.0, dash=None):
        self.c.setStrokeGray(grey)
        self.c.setLineWidth(width)
        self.c.setDash(dash or [])
        self.c.line(x0, y, x1, y)
        self.c.setDash([])
        self.c.setStrokeGray(0)

    def vline(self, x, y0, y1, width=0.6):
        self.c.setLineWidth(width)
        self.c.line(x, y0, x, y1)

    def box(self, x, y, w, h, width=0.6, fill_grey=None):
        self.c.setLineWidth(width)
        if fill_grey is not None:
            self.c.setFillGray(fill_grey)
            self.c.rect(x, y, w, h, stroke=0, fill=1)
            self.c.setFillGray(0)
        else:
            self.c.rect(x, y, w, h, stroke=1, fill=0)

    def wrap(self, s, font, size, width):
        return simpleSplit(str(s), font, size, width)

    def footer(self):
        self.ctext(PAGE_W / 2, 22, DISCLAIMER, "Helvetica-Oblique", 6.5, grey=0.45)


def _labels(inv: dict, defaults: dict) -> dict:
    out = dict(defaults)
    out.update(inv.get("labels") or {})
    return out


def _bank(inv: dict) -> dict:
    vendor = VENDOR[inv["vendor"]]
    bank = {k: vendor[k] for k in ("bank_name", "bank_branch", "bank_account", "ifsc")}
    if inv.get("bank_override"):
        bank.update(inv["bank_override"])
    return bank


def _vendor_name(inv: dict) -> str:
    return inv.get("vendor_name_printed") or VENDOR[inv["vendor"]]["legal_name"]


# --------------------------------------------------------------------------- layouts

def draw_classic(p: Pen, inv: dict, t: dict) -> None:
    v = VENDOR[inv["vendor"]]
    L = _labels(inv, {"title": "TAX INVOICE", "inv_no": "Invoice No.", "inv_date": "Dated",
                      "po": "Buyer's Order No.", "po_date": "Order Date", "desc": "Description of Goods",
                      "hsn": "HSN", "qty": "Qty", "unit": "Unit", "rate": "Rate", "amount": "Amount",
                      "subtotal": "Sub Total", "total": "Grand Total"})
    df = inv.get("date_format") or "%d-%b-%Y"
    p.ctext(PAGE_W / 2, 806, L["title"], "Helvetica-Bold", 13)
    p.text(40, 780, _vendor_name(inv), "Helvetica-Bold", 13)
    y = 766
    for ln in v["address"]:
        p.text(40, y, ln, size=9)
        y -= 12
    p.text(40, y, f"GSTIN: {v['gstin']}", size=9)
    p.text(40, y - 12, f"State: {state_name(v['state_code'])} ({v['state_code']})", size=9)
    p.text(40, y - 24, f"Email: {v['email']}", size=9)
    meta = [(L["inv_no"], inv["invoice_no"] or ""), (L["inv_date"], fmt_d(inv["invoice_date"], df))]
    if inv.get("po_ref"):
        meta.append((L["po"], inv["po_ref"]))
        if inv.get("po_date"):
            meta.append((L["po_date"], fmt_d(inv["po_date"], df)))
    meta.append(("Place of Supply", f"{state_name(BUYER['state_code'])} ({BUYER['state_code']})"))
    my = 780
    for label, value in meta:
        p.text(350, my, label, size=9)
        p.text(455, my, value, "Helvetica-Bold", 9)
        my -= 14
    p.hline(40, 555, 692)
    p.text(40, 676, "Bill To:", "Helvetica-Bold", 9)
    p.text(40, 662, BUYER["name"], "Helvetica-Bold", 10)
    y = 649
    for ln in BUYER["address"]:
        p.text(40, y, ln, size=9)
        y -= 12
    p.text(40, y, f"GSTIN: {BUYER['gstin']}", size=9)
    top = 598
    p.hline(40, 555, top + 14)
    p.text(44, top, "Sl", "Helvetica-Bold", 9)
    p.text(70, top, L["desc"], "Helvetica-Bold", 9)
    p.text(300, top, L["hsn"], "Helvetica-Bold", 9)
    p.rtext(385, top, L["qty"], "Helvetica-Bold", 9)
    p.text(395, top, L["unit"], "Helvetica-Bold", 9)
    p.rtext(485, top, L["rate"], "Helvetica-Bold", 9)
    p.rtext(552, top, L["amount"], "Helvetica-Bold", 9)
    p.hline(40, 555, top - 6)
    y = top - 22
    for i, (ln, amt) in enumerate(zip(inv["lines"], t["amounts"]), 1):
        wrapped = p.wrap(ln["description"], "Helvetica", 9, 220)
        p.text(44, y, str(i), size=9)
        p.text(70, y, wrapped[0], size=9)
        p.text(300, y, ln["hsn"], size=9)
        if ln.get("qty") is not None:
            p.rtext(385, y, str(ln["qty"]), size=9)
            p.text(395, y, ln["uom"], size=9)
            p.rtext(485, y, money(ln["rate"]), size=9)
        p.rtext(552, y, money(amt), size=9)
        for extra in wrapped[1:]:
            y -= 11
            p.text(70, y, extra, size=9)
        y -= 18
    p.hline(40, 555, y + 8)
    y -= 8
    rows = [(L["subtotal"], money(t["taxable"]))]
    if t["intra"]:
        half = t["rate"] / 2
        rows += [(f"CGST @ {half:g}%", money(t["cgst"])), (f"SGST @ {half:g}%", money(t["sgst"]))]
    else:
        rows.append((f"IGST @ {t['rate']:g}%", money(t["igst"])))
    for label, value in rows:
        p.rtext(470, y, label, size=9)
        p.rtext(552, y, value, size=9)
        y -= 14
    p.hline(380, 555, y + 9)
    p.rtext(470, y - 3, L["total"], "Helvetica-Bold", 10)
    p.rtext(552, y - 3, money(t["total"], "Rs. "), "Helvetica-Bold", 10)
    y -= 30
    p.text(40, y, f"Amount in words: {amount_to_words(t['total'])}", size=9)
    y -= 30
    bank = _bank(inv)
    p.text(40, y, "Bank Details", "Helvetica-Bold", 9)
    for i, (label, value) in enumerate([("Bank Name", bank["bank_name"]), ("A/c No.", bank["bank_account"]),
                                         ("IFSC", bank["ifsc"]), ("Branch", bank["bank_branch"])], 1):
        p.text(40, y - 13 * i, f"{label}: {value}", size=9)
    p.rtext(552, y, f"For {_vendor_name(inv)}", "Helvetica-Bold", 9)
    p.rtext(552, y - 52, "Authorised Signatory", size=9)
    p.text(40, y - 78, f"Terms: Payment within {v['payment_terms_days']} days of invoice date.", size=8, grey=0.3)
    p.footer()


def draw_modern(p: Pen, inv: dict, t: dict) -> None:
    v = VENDOR[inv["vendor"]]
    L = _labels(inv, {"title": "TAX INVOICE", "inv_no": "Invoice #", "inv_date": "Invoice Date",
                      "po": "PO Reference", "desc": "Item", "hsn": "HSN/SAC", "qty": "Qty",
                      "rate": "Unit Price", "gst": "GST %", "amount": "Taxable Value",
                      "subtotal": "Subtotal", "total": "Total (INR)"})
    df = inv.get("date_format") or "%d %b %Y"
    p.text(40, 792, _vendor_name(inv), "Helvetica-Bold", 16)
    p.text(40, 777, ", ".join(v["address"]), size=8.5, grey=0.35)
    p.text(40, 765, f"GSTIN {v['gstin']}", size=8.5, grey=0.35)
    p.rtext(555, 790, L["title"], "Helvetica-Bold", 18)
    p.hline(40, 555, 752, width=1.2)
    due = inv["invoice_date"].toordinal() + v["payment_terms_days"]
    cols = [(40, L["inv_no"], inv["invoice_no"] or ""), (180, L["inv_date"], fmt_d(inv["invoice_date"], df))]
    if inv.get("po_ref"):
        cols.append((320, L["po"], inv["po_ref"]))
    cols.append((450, "Due Date", fmt_d(date.fromordinal(due), df)))
    for x, label, value in cols:
        p.text(x, 734, label, size=8, grey=0.4)
        p.text(x, 719, value, "Helvetica-Bold", 10.5)
    p.text(40, 690, "Billed To", size=8, grey=0.4)
    p.text(40, 676, BUYER["name"], "Helvetica-Bold", 10)
    y = 663
    for ln in BUYER["address"]:
        p.text(40, y, ln, size=9)
        y -= 12
    p.text(40, y, f"GSTIN: {BUYER['gstin']}", size=9)
    p.text(320, 690, "Supply Details", size=8, grey=0.4)
    p.text(320, 676, f"Supplier State: {state_name(v['state_code'])} ({v['state_code']})", size=9)
    p.text(320, 663, f"Place of Supply: {state_name(BUYER['state_code'])} ({BUYER['state_code']})", size=9)
    if inv.get("service_period"):
        p.text(320, 650, f"Service Period: {inv['service_period']}", size=9)
    top = 598
    p.box(40, top - 7, 515, 20, fill_grey=0.92)
    has_qty = any(ln.get("qty") is not None for ln in inv["lines"])
    p.text(46, top, L["desc"], "Helvetica-Bold", 8.5)
    p.text(290, top, L["hsn"], "Helvetica-Bold", 8.5)
    if has_qty:
        p.rtext(370, top, L["qty"], "Helvetica-Bold", 8.5)
        p.rtext(440, top, L["rate"], "Helvetica-Bold", 8.5)
    p.rtext(478, top, L["gst"], "Helvetica-Bold", 8.5)
    p.rtext(551, top, L["amount"], "Helvetica-Bold", 8.5)
    y = top - 24
    for ln, amt in zip(inv["lines"], t["amounts"]):
        wrapped = p.wrap(ln["description"], "Helvetica", 9, 240)
        p.text(46, y, wrapped[0], size=9)
        p.text(290, y, ln["hsn"], size=9)
        if ln.get("qty") is not None:
            p.rtext(370, y, str(ln["qty"]), size=9)
            p.rtext(440, y, money(ln["rate"]), size=9)
        p.rtext(478, y, f"{t['rate']:g}", size=9)
        p.rtext(551, y, money(amt), size=9)
        for extra in wrapped[1:]:
            y -= 11
            p.text(46, y, extra, size=9)
        p.hline(40, 555, y - 7, width=0.3, grey=0.7)
        y -= 20
    y -= 4
    rows = [(L["subtotal"], money(t["taxable"]))]
    if t["intra"]:
        half = t["rate"] / 2
        rows += [(f"CGST ({half:g}%)", money(t["cgst"])), (f"SGST ({half:g}%)", money(t["sgst"]))]
    else:
        rows.append((f"IGST ({t['rate']:g}%)", money(t["igst"])))
    for label, value in rows:
        p.rtext(455, y, label, size=9)
        p.rtext(551, y, value, size=9)
        y -= 15
    p.box(360, y - 9, 195, 22, fill_grey=0.92)
    p.rtext(455, y - 2, L["total"], "Helvetica-Bold", 10.5)
    p.rtext(551, y - 2, money(t["total"]), "Helvetica-Bold", 10.5)
    y -= 36
    p.text(40, y, f"Amount in words: {amount_to_words(t['total'])}", size=9)
    bank = _bank(inv)
    y -= 30
    p.text(40, y, "Payment Details", size=8, grey=0.4)
    p.text(40, y - 14, f"Bank: {bank['bank_name']}", size=9)
    p.text(215, y - 14, f"A/c No: {bank['bank_account']}", size=9)
    p.text(375, y - 14, f"IFSC: {bank['ifsc']}", size=9)
    p.text(40, y - 46, f"For {_vendor_name(inv)}", "Helvetica-Bold", 9)
    p.text(40, y - 60, "This is a computer generated invoice.", size=8, grey=0.4)
    p.footer()


def draw_typewriter(p: Pen, inv: dict, t: dict) -> None:
    v = VENDOR[inv["vendor"]]
    L = _labels(inv, {"title": "TAX INVOICE", "inv_no": "Bill No.", "inv_date": "Date",
                      "po": "Your Order No.", "desc": "Particulars", "qty": "Qty", "rate": "Rate",
                      "amount": "Amount", "subtotal": "Total", "total": "Net Payable"})
    df = inv.get("date_format") or "%d-%m-%Y"
    F, FB, S = "Courier", "Courier-Bold", 10
    mid = PAGE_W / 2
    p.ctext(mid, 792, _vendor_name(inv).upper(), FB, 13)
    p.ctext(mid, 777, ", ".join(v["address"]), F, 9)
    p.ctext(mid, 764, f"GSTIN : {v['gstin']}", F, 9.5)
    p.hline(50, 545, 754, dash=[3, 2])
    p.ctext(mid, 739, L["title"], FB, 11)
    p.text(55, 716, f"{L['inv_no']} : {inv['invoice_no'] or ''}", F, S)
    p.text(390, 716, f"{L['inv_date']} : {fmt_d(inv['invoice_date'], df)}", F, S)
    p.text(55, 694, "To,", F, S)
    p.text(55, 681, f"M/s {BUYER['name']}", FB, S)
    p.text(55, 668, ", ".join(BUYER["address"]), F, 9)
    p.text(55, 655, f"GSTIN : {BUYER['gstin']}", F, S)
    if inv.get("po_ref"):
        ref = f"{L['po']} : {inv['po_ref']}"
        if inv.get("po_date"):
            ref += f" dt. {fmt_d(inv['po_date'], df)}"
        p.text(55, 634, ref, F, S)
    else:
        p.text(55, 634, "Ref : Supply of packing material as per your order", F, S)
    top = 606
    p.hline(50, 545, top + 13, dash=[3, 2])
    p.text(55, top, "Sl", FB, S)
    p.text(85, top, L["desc"], FB, S)
    p.rtext(370, top, L["qty"], FB, S)
    p.rtext(450, top, L["rate"], FB, S)
    p.rtext(540, top, L["amount"], FB, S)
    p.hline(50, 545, top - 6, dash=[3, 2])
    y = top - 22
    for i, (ln, amt) in enumerate(zip(inv["lines"], t["amounts"]), 1):
        p.text(55, y, str(i), F, S)
        p.text(85, y, ln["description"], F, S)
        if ln.get("qty") is not None:
            p.rtext(370, y, str(ln["qty"]), F, S)
            p.rtext(450, y, money(ln["rate"]), F, S)
        p.rtext(540, y, money(amt), F, S)
        y -= 18
    p.hline(50, 545, y + 8, dash=[3, 2])
    y -= 8
    rows = [(L["subtotal"], money(t["taxable"]))]
    if t["intra"]:
        half = t["rate"] / 2
        rows += [(f"CGST {half:g}%", money(t["cgst"])), (f"SGST {half:g}%", money(t["sgst"]))]
    else:
        rows.append((f"IGST {t['rate']:g}%", money(t["igst"])))
    rows.append((L["total"], money(t["total"])))
    for label, value in rows:
        bold = label == L["total"]
        p.text(330, y, label, FB if bold else F, S)
        p.rtext(540, y, value, FB if bold else F, S)
        y -= 15
    y -= 10
    p.text(55, y, amount_to_words(t["total"]), F, 9.5)
    bank = _bank(inv)
    y -= 28
    p.text(55, y, f"Bank : {bank['bank_name']}, {bank['bank_branch']}", F, S)
    p.text(55, y - 14, f"A/c No : {bank['bank_account']}", F, S)
    p.text(300, y - 14, f"IFSC : {bank['ifsc']}", F, S)
    p.rtext(540, y - 50, f"For {_vendor_name(inv)}", FB, S)
    p.rtext(540, y - 92, "Authorised Signatory", F, S)
    p.footer()


def draw_grid(p: Pen, inv: dict, t: dict) -> None:
    v = VENDOR[inv["vendor"]]
    L = _labels(inv, {"title": "TAX INVOICE", "inv_no": "Invoice No.", "inv_date": "Invoice Date",
                      "po": "P.O. No.", "po_date": "P.O. Date", "desc": "Item Description", "hsn": "HSN/SAC",
                      "qty": "Qty", "unit": "UOM", "rate": "Rate (Rs.)", "amount": "Taxable Value",
                      "total": "Invoice Total (in figures)"})
    df = inv.get("date_format") or "%d/%m/%Y"
    p.box(36, 760, 523, 62)
    p.vline(380, 760, 822)
    p.text(42, 806, _vendor_name(inv), "Helvetica-Bold", 12)
    p.text(42, 793, v["address"][0], size=8.5)
    p.text(42, 782, v["address"][1], size=8.5)
    p.text(42, 768, f"GSTIN/UIN: {v['gstin']}", "Helvetica-Bold", 8.5)
    p.ctext(469, 800, L["title"], "Helvetica-Bold", 14)
    p.ctext(469, 784, "(Original for Recipient)", size=8)
    cells = [(L["inv_no"], inv["invoice_no"] or ""), (L["inv_date"], fmt_d(inv["invoice_date"], df)),
             (L["po"], inv.get("po_ref") or ""),
             (L["po_date"], fmt_d(inv["po_date"], df) if inv.get("po_date") else ""),
             ("Vehicle No.", "MH 14 GX 4471"), ("E-Way Bill No.", "2710 4455 8812")]
    cw, ch = 523 / 3, 30
    for i, (label, value) in enumerate(cells):
        cx = 36 + (i % 3) * cw
        cy = 730 - (i // 3) * ch
        p.box(cx, cy, cw, ch)
        p.text(cx + 6, cy + 19, label, size=7.5, grey=0.35)
        p.text(cx + 6, cy + 6, value, "Helvetica-Bold", 9.5)
    p.box(36, 640, 523, 60)
    p.text(42, 688, "Details of Receiver (Billed to)", size=7.5, grey=0.35)
    p.text(42, 675, BUYER["name"], "Helvetica-Bold", 9.5)
    p.text(42, 663, ", ".join(BUYER["address"]), size=8.5)
    p.text(42, 650, f"GSTIN/UIN: {BUYER['gstin']}", size=8.5)
    p.text(330, 650, f"State Code: {BUYER['state_code']}", size=8.5)
    top = 618
    p.box(36, top - 8, 523, 22, fill_grey=0.9)
    H, HS = "Helvetica-Bold", 7.5
    p.text(40, top, "S.No", H, HS)
    p.text(64, top, L["desc"], H, HS)
    p.text(196, top, L["hsn"], H, HS)
    p.rtext(262, top, L["qty"], H, HS)
    p.text(268, top, L["unit"], H, HS)
    p.rtext(340, top, L["rate"], H, HS)
    p.rtext(408, top, L["amount"], H, HS)
    p.rtext(440, top, "IGST%", H, HS)
    p.rtext(492, top, "IGST Amt", H, HS)
    p.rtext(556, top, "Total", H, HS)
    y = top - 24
    S = 8.5
    for i, (ln, amt) in enumerate(zip(inv["lines"], t["amounts"]), 1):
        line_tax = q2(amt * t["rate"] / 100)
        wrapped = p.wrap(ln["description"], "Helvetica", S, 128)
        p.text(42, y, str(i), size=S)
        p.text(64, y, wrapped[0], size=S)
        p.text(196, y, ln["hsn"], size=S)
        p.rtext(262, y, str(ln["qty"]), size=S)
        p.text(268, y, ln["uom"], size=S)
        p.rtext(340, y, money(ln["rate"]), size=S)
        p.rtext(408, y, money(amt), size=S)
        p.rtext(440, y, f"{t['rate']:g}", size=S)
        p.rtext(492, y, money(line_tax), size=S)
        p.rtext(556, y, money(amt + line_tax), size=S)
        for extra in wrapped[1:]:
            y -= 10
            p.text(64, y, extra, size=S)
        y -= 18
    p.hline(36, 559, y + 8)
    p.text(64, y - 6, "Total", "Helvetica-Bold", S)
    p.rtext(408, y - 6, money(t["taxable"]), "Helvetica-Bold", S)
    p.rtext(492, y - 6, money(t["igst"]), "Helvetica-Bold", S)
    p.rtext(556, y - 6, money(t["total"]), "Helvetica-Bold", S)
    p.hline(36, 559, y - 14)
    p.vline(36, y - 14, top - 8)
    p.vline(559, y - 14, top - 8)
    y -= 36
    p.text(42, y, f"{L['total']}: Rs. {money(t['total'])}", "Helvetica-Bold", 10)
    p.text(42, y - 15, f"Invoice Total (in words): {amount_to_words(t['total'])}", size=8.5)
    bank = _bank(inv)
    y -= 42
    p.box(36, y - 46, 300, 56)
    p.text(42, y, "Bank Details for Payment", size=7.5, grey=0.35)
    p.text(42, y - 13, f"Bank Name: {bank['bank_name']}", size=8.5)
    p.text(42, y - 25, f"Account No.: {bank['bank_account']}", size=8.5)
    p.text(42, y - 37, f"IFSC Code: {bank['ifsc']}", size=8.5)
    p.box(336, y - 46, 223, 56)
    p.ctext(447, y, f"For {_vendor_name(inv)}", "Helvetica-Bold", 8.5)
    p.ctext(447, y - 38, "Authorised Signatory", size=8.5)
    p.footer()


def draw_retail(p: Pen, inv: dict, t: dict) -> None:
    v = VENDOR[inv["vendor"]]
    L = _labels(inv, {"title": "TAX INVOICE", "inv_no": "Invoice No", "inv_date": "Date", "po": "PO No",
                      "desc": "Item", "hsn": "HSN", "qty": "Qty", "rate": "Rate (incl. GST)",
                      "amount": "Amount (incl. GST)", "total": "Total (inclusive of GST)"})
    df = inv.get("date_format") or "%d/%m/%Y"
    mid = PAGE_W / 2
    p.ctext(mid, 800, _vendor_name(inv), "Helvetica-Bold", 14)
    p.ctext(mid, 786, ", ".join(v["address"]), size=9)
    p.ctext(mid, 773, f"GSTIN: {v['gstin']}", size=9)
    p.ctext(mid, 752, L["title"], "Helvetica-Bold", 11)
    p.hline(60, 535, 744)
    p.text(60, 726, f"{L['inv_no']}: {inv['invoice_no'] or ''}", size=9.5)
    p.text(250, 726, f"{L['inv_date']}: {fmt_d(inv['invoice_date'], df)}", size=9.5)
    if inv.get("po_ref"):
        p.text(400, 726, f"{L['po']}: {inv['po_ref']}", size=9.5)
    p.text(60, 706, f"Customer: {BUYER['name']}", size=9.5)
    p.text(60, 693, f"Customer GSTIN: {BUYER['gstin']}", size=9.5)
    top = 664
    p.hline(60, 535, top + 13)
    B, S = "Helvetica-Bold", 9
    p.text(62, top, "#", B, S)
    p.text(82, top, L["desc"], B, S)
    p.text(290, top, L["hsn"], B, S)
    p.rtext(345, top, L["qty"], B, S)
    p.rtext(430, top, L["rate"], B, S)
    p.rtext(533, top, L["amount"], B, S)
    p.hline(60, 535, top - 6)
    y = top - 22
    for i, (ln, amt) in enumerate(zip(inv["lines"], t["amounts"]), 1):
        p.text(62, y, str(i), size=S)
        p.text(82, y, ln["description"], size=S)
        p.text(290, y, ln["hsn"], size=S)
        p.rtext(345, y, str(ln["qty"]), size=S)
        p.rtext(430, y, money(ln["rate"]), size=S)
        p.rtext(533, y, money(amt), size=S)
        y -= 18
    p.hline(60, 535, y + 8)
    p.rtext(446, y - 8, L["total"], B, 10)
    p.rtext(533, y - 8, money(t["total"], "Rs. "), B, 10)
    y -= 40
    p.box(60, y - 50, 250, 62)
    p.text(66, y, "Tax breakup (included in the total above)", size=8, grey=0.3)
    rows = [("Taxable Value", money(t["taxable"]))]
    if t["intra"]:
        half = t["rate"] / 2
        rows += [(f"CGST @ {half:g}%", money(t["cgst"])), (f"SGST @ {half:g}%", money(t["sgst"]))]
    else:
        rows.append((f"IGST @ {t['rate']:g}%", money(t["igst"])))
    for i, (label, value) in enumerate(rows, 1):
        p.text(66, y - 13 * i, label, size=9)
        p.rtext(300, y - 13 * i, value, size=9)
    p.text(330, y - 13, f"All rates are inclusive of GST @ {t['rate']:g}%.", size=9)
    y -= 72
    p.text(60, y, f"Amount in words: {amount_to_words(t['total'])}", size=9)
    bank = _bank(inv)
    p.text(60, y - 22, f"Bank: {bank['bank_name']}", size=9)
    p.text(240, y - 22, f"A/c: {bank['bank_account']}", size=9)
    p.text(390, y - 22, f"IFSC: {bank['ifsc']}", size=9)
    p.rtext(535, y - 60, f"For {_vendor_name(inv)}", "Helvetica-Bold", 9)
    p.rtext(535, y - 74, "Authorised Signatory", size=9)
    p.footer()


def _count_word(n: int) -> str:
    return ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"][n]


def draw_letter(p: Pen, inv: dict, t: dict) -> None:
    v = VENDOR[inv["vendor"]]
    F, FB, S = "Times-Roman", "Times-Bold", 11
    p.text(60, 790, _vendor_name(inv), FB, 16)
    p.text(60, 774, ", ".join(v["address"]), F, 10)
    p.text(60, 761, f"GSTIN {v['gstin']}", F, 10)
    p.hline(60, 535, 750)
    p.text(60, 724, f"Date: {inv['invoice_date'].strftime('%d %B %Y')}", F, S)
    p.text(60, 700, "To,", F, S)
    p.text(60, 686, "The Accounts Department", F, S)
    p.text(60, 672, BUYER["name"], FB, S)
    p.text(60, 658, ", ".join(BUYER["address"]), F, S)
    p.text(60, 644, f"GSTIN {BUYER['gstin']}", F, S)
    p.text(60, 614, f"Subject: Tax invoice {inv['invoice_no']} for network equipment", FB, S)
    p.text(60, 588, "Dear Sir or Madam,", F, S)
    a, b = inv["lines"][0], inv["lines"][1]
    body = (
        f"With reference to your purchase order {inv['po_ref']} dated {inv['po_date'].strftime('%d %B %Y')}, "
        f"we have delivered {_count_word(a['qty'])} managed network switches (24 port gigabit) at "
        f"Rs. {money(a['rate'])} each, amounting to Rs. {money(t['amounts'][0])}, and {_count_word(b['qty'])} "
        f"online UPS units of 3 kVA with batteries at Rs. {money(b['rate'])} each, amounting to "
        f"Rs. {money(t['amounts'][1])}. The taxable value of this supply is Rs. {money(t['taxable'])}. "
        f"IGST at {t['rate']:g} percent comes to Rs. {money(t['igst'])}, so the total amount payable is "
        f"Rs. {money(t['total'])} ({amount_to_words(t['total'])})."
    )
    y = 566
    for ln in p.wrap(body, F, S, 470):
        p.text(60, y, ln, F, S)
        y -= 16
    bank = _bank(inv)
    y -= 10
    closing = (f"Kindly remit the amount to our account {bank['bank_account']} with {bank['bank_name']}, "
               f"IFSC {bank['ifsc']}, within {v['payment_terms_days']} days. HSN codes: 8517 and 8504.")
    for ln in p.wrap(closing, F, S, 470):
        p.text(60, y, ln, F, S)
        y -= 16
    p.text(60, y - 24, "Yours faithfully,", F, S)
    p.text(60, y - 44, f"For {_vendor_name(inv)}", FB, S)
    p.text(60, y - 88, "Authorised Signatory", F, S)
    p.footer()


LAYOUTS = {"classic": draw_classic, "modern": draw_modern, "typewriter": draw_typewriter,
           "grid": draw_grid, "retail": draw_retail, "letter": draw_letter}


def render_invoice_pdf(inv: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(f"Invoice {inv.get('invoice_no') or 'unnumbered'} (synthetic test document)")
    c.setAuthor("Synthetic test data generator")
    LAYOUTS[inv["layout"]](Pen(c), inv, totals(inv))
    c.showPage()
    c.save()
    return buf.getvalue()


def render_multipage_invoice_pdf(inv: dict, first_page_lines: int = 12, later_page_lines: int = 30,
                                 repeat_header: bool = True, carry_forward: bool = False) -> bytes:
    """An invoice whose item table runs over several pages: letterhead and the start of the
    table on page 1, a 'continued' strip and (optionally) the column headings again on the
    following pages, totals, amount in words and bank details after the last line. With
    carry_forward, each page ends with a 'Carried forward' amount and the next begins with
    'Brought forward', as accounting packages print them."""
    v, t = VENDOR[inv["vendor"]], totals(inv)
    df = inv.get("date_format") or "%d-%b-%Y"
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(f"Invoice {inv.get('invoice_no') or 'unnumbered'} (synthetic test document)")
    p = Pen(c)
    lines = list(zip(inv["lines"], t["amounts"]))
    chunks = [lines[:first_page_lines]]
    rest = lines[first_page_lines:]
    while rest:
        chunks.append(rest[:later_page_lines])
        rest = rest[later_page_lines:]

    def table_header(top: float) -> float:
        p.hline(40, 555, top + 14)
        p.text(44, top, "Sl", "Helvetica-Bold", 9)
        p.text(70, top, "Description of Goods", "Helvetica-Bold", 9)
        p.text(300, top, "HSN", "Helvetica-Bold", 9)
        p.rtext(385, top, "Qty", "Helvetica-Bold", 9)
        p.text(395, top, "Unit", "Helvetica-Bold", 9)
        p.rtext(485, top, "Rate", "Helvetica-Bold", 9)
        p.rtext(552, top, "Amount", "Helvetica-Bold", 9)
        p.hline(40, 555, top - 6)
        return top - 22

    number = 0
    running = D(0)
    for page_no, chunk in enumerate(chunks, 1):
        if page_no == 1:
            p.ctext(PAGE_W / 2, 806, "TAX INVOICE", "Helvetica-Bold", 13)
            p.text(40, 780, _vendor_name(inv), "Helvetica-Bold", 13)
            y = 766
            for ln in v["address"]:
                p.text(40, y, ln, size=9)
                y -= 12
            p.text(40, y, f"GSTIN: {v['gstin']}", size=9)
            meta = [("Invoice No.", inv["invoice_no"] or ""), ("Dated", fmt_d(inv["invoice_date"], df))]
            if inv.get("po_ref"):
                meta.append(("Buyer's Order No.", inv["po_ref"]))
            my = 780
            for label, value in meta:
                p.text(350, my, label, size=9)
                p.text(455, my, value, "Helvetica-Bold", 9)
                my -= 14
            p.hline(40, 555, 700)
            p.text(40, 684, "Bill To:", "Helvetica-Bold", 9)
            p.text(40, 670, BUYER["name"], "Helvetica-Bold", 10)
            p.text(40, 657, f"GSTIN: {BUYER['gstin']}", size=9)
            y = table_header(626)
        else:
            p.text(40, 800, f"{_vendor_name(inv)}", "Helvetica-Bold", 10)
            p.rtext(552, 800, f"Invoice No. {inv['invoice_no']} (continued), page {page_no} of {len(chunks)}", size=9)
            y = table_header(772) if repeat_header else 772
            if carry_forward:
                p.text(70, y, "Brought forward", "Helvetica-Oblique", 9)
                p.rtext(552, y, money(running), "Helvetica-Oblique", 9)
                y -= 18
        for ln, amt in chunk:
            number += 1
            running += amt
            p.text(44, y, str(number), size=9)
            p.text(70, y, ln["description"], size=9)
            p.text(300, y, ln["hsn"], size=9)
            p.rtext(385, y, str(ln["qty"]), size=9)
            p.text(395, y, ln["uom"], size=9)
            p.rtext(485, y, money(ln["rate"]), size=9)
            p.rtext(552, y, money(amt), size=9)
            y -= 18
        p.hline(40, 555, y + 8)
        if page_no < len(chunks):
            if carry_forward:
                p.text(70, y - 6, "Carried forward", "Helvetica-Oblique", 9)
                p.rtext(552, y - 6, money(running), "Helvetica-Oblique", 9)
                y -= 16
            p.rtext(552, y - 6, "Continued on next page", size=8, grey=0.3)
        else:
            y -= 8
            rows = [("Sub Total", money(t["taxable"]))]
            if t["intra"]:
                half = t["rate"] / 2
                rows += [(f"CGST @ {half:g}%", money(t["cgst"])), (f"SGST @ {half:g}%", money(t["sgst"]))]
            else:
                rows.append((f"IGST @ {t['rate']:g}%", money(t["igst"])))
            for label, value in rows:
                p.rtext(470, y, label, size=9)
                p.rtext(552, y, value, size=9)
                y -= 14
            p.hline(380, 555, y + 9)
            p.rtext(470, y - 3, "Grand Total", "Helvetica-Bold", 10)
            p.rtext(552, y - 3, money(t["total"], "Rs. "), "Helvetica-Bold", 10)
            y -= 30
            p.text(40, y, f"Amount in words: {amount_to_words(t['total'])}", size=9)
            y -= 28
            bank = _bank(inv)
            p.text(40, y, "Bank Details", "Helvetica-Bold", 9)
            for i, (label, value) in enumerate([("Bank Name", bank["bank_name"]), ("A/c No.", bank["bank_account"]),
                                                 ("IFSC", bank["ifsc"])], 1):
                p.text(40, y - 13 * i, f"{label}: {value}", size=9)
            p.rtext(552, y, f"For {_vendor_name(inv)}", "Helvetica-Bold", 9)
        p.footer()
        c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------------- scans

def to_scan(pdf_bytes: bytes, angle: float = 0.8, noise: float = 8.0, seed: int = 1, dpi: int = 220) -> bytes:
    """Rasterise each page, tilt it, blur it, add sensor noise and a few specks, and wrap
    the picture in a new PDF that has no text layer: what a desk scanner produces."""
    import numpy as np
    import pypdfium2 as pdfium
    from PIL import Image, ImageFilter

    rng = np.random.default_rng(seed)
    src = pdfium.PdfDocument(pdf_bytes)
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=A4, invariant=1)
    c.setTitle("Scanned document (synthetic test document)")
    try:
        for i in range(len(src)):
            page = src[i]
            bitmap = page.render(scale=dpi / 72, grayscale=True)
            img = bitmap.to_pil().convert("L")
            bitmap.close()
            page.close()
            img = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
            img = img.filter(ImageFilter.GaussianBlur(0.6))
            arr = np.asarray(img).astype(np.float32)
            arr = arr * 0.93 + 10  # slightly grey paper, slightly faded ink
            arr += rng.normal(0, noise, arr.shape)
            h, w = arr.shape
            for _ in range(60):  # dust specks
                sy, sx = int(rng.integers(0, h - 3)), int(rng.integers(0, w - 3))
                arr[sy:sy + 2, sx:sx + 2] -= float(rng.integers(40, 120))
            ramp = np.linspace(-6, 6, w, dtype=np.float32)[None, :]  # uneven lamp
            arr = np.clip(arr + ramp, 0, 255).astype(np.uint8)
            jpeg = io.BytesIO()
            Image.fromarray(arr, mode="L").save(jpeg, format="JPEG", quality=72)
            jpeg.seek(0)
            c.drawImage(ImageReader(jpeg), 0, 0, width=PAGE_W, height=PAGE_H)
            c.showPage()
    finally:
        src.close()
    c.save()
    return out.getvalue()


# --------------------------------------------------------------------------- bad inputs

def render_quotation_pdf() -> bytes:
    inv = spec(id="quotation", layout="classic", vendor="V001", invoice_no="Q/26-27/0088",
               invoice_date=date(2026, 11, 10),
               labels={"title": "QUOTATION", "inv_no": "Quotation No.", "inv_date": "Quotation Date",
                       "total": "Quoted Total"},
               lines=[line("Executive Desk 1800 mm with Side Return", "9403", 3, "Nos", "32500.00")])
    return render_invoice_pdf(inv)


def render_newsletter_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    p = Pen(c)
    p.text(60, 780, "Nilgiri Precision Components", "Helvetica-Bold", 16)
    p.text(60, 760, "Safety Week Bulletin", "Helvetica-Bold", 12)
    body = ("This week every shift will walk the shop floor with the EHS team. Please keep aisles clear, "
            "wear hearing protection near the press line, and report any spill straight away. The "
            "canteen committee meets on Thursday. Blood donation camp details will follow on the notice "
            "board. Thank you for keeping each other safe.")
    y = 730
    for ln in p.wrap(body, "Helvetica", 11, 470):
        p.text(60, y, ln, size=11)
        y -= 17
    p.ctext(PAGE_W / 2, 22, "Synthetic test document for a case study. Not an invoice.", "Helvetica-Oblique", 6.5, 0.45)
    c.showPage()
    c.save()
    return buf.getvalue()


def corrupt_bytes() -> bytes:
    rnd = random.Random(SEED)
    return b"%PDF-1.7\n" + bytes(rnd.getrandbits(8) for _ in range(4096))


# --------------------------------------------------------------------------- truth and files

def truth_for(inv: dict) -> dict:
    t = totals(inv)
    v = VENDOR[inv["vendor"]]
    bank = _bank(inv)
    rate = t["rate"]
    lines = []
    for ln, amt in zip(inv["lines"], t["amounts"]):
        pre_rate = ln["rate"]
        pre_amt = amt
        if inv["tax_inclusive"] and ln.get("rate") is not None:
            pre_rate = q2(ln["rate"] / (1 + rate / 100))
            pre_amt = q2(amt / (1 + rate / 100))
        lines.append({"description": ln["description"], "hsn": ln["hsn"],
                      "qty": None if ln.get("qty") is None else str(ln["qty"]), "uom": ln.get("uom"),
                      "unit_price": None if ln.get("rate") is None else str(ln["rate"]),
                      "amount": str(amt),
                      "unit_price_pre_tax": None if pre_rate is None else str(pre_rate),
                      "amount_pre_tax": str(pre_amt)})
    return {"id": inv["id"], "layout": inv["layout"], "scanned": inv["scanned"], "vendor_id": inv["vendor"],
            "vendor_name_printed": _vendor_name(inv), "vendor_gstin": v["gstin"], "buyer_gstin": BUYER["gstin"],
            "invoice_no": inv["invoice_no"], "invoice_date": inv["invoice_date"].isoformat(),
            "po_ref": inv.get("po_ref"), "tax_inclusive": inv["tax_inclusive"], "gst_rate": str(rate),
            "taxable": str(t["taxable"]), "cgst": str(t["cgst"]), "sgst": str(t["sgst"]), "igst": str(t["igst"]),
            "tax_total": str(t["tax"]), "total": str(t["total"]), "bank_account": bank["bank_account"],
            "ifsc": bank["ifsc"], "lines": lines}


def build_sample(inv: dict) -> bytes:
    pdf = render_invoice_pdf(inv)
    if inv["scanned"]:
        pdf = to_scan(pdf, **(inv.get("scan") or {}))
    return pdf


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_master_data(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    write_csv(data_dir / "vendors.csv",
              ["vendor_id", "legal_name", "aliases", "gstin", "state_code", "status", "bank_name",
               "bank_account", "ifsc", "payment_terms_days", "phone_on_file", "email"],
              [[v["vendor_id"], v["legal_name"], v["aliases"], v["gstin"], v["state_code"], v["status"],
                v["bank_name"], v["bank_account"], v["ifsc"], v["payment_terms_days"], v["phone_on_file"],
                v["email"]] for v in VENDORS])
    po_rows = []
    for po_number, po_date, vendor, status, buyer, lines in PURCHASE_ORDERS:
        for n, (code, desc, hsn, uom, qty, price, basis, received) in enumerate(lines, 1):
            amount = q2(D(qty) * D(price))
            po_rows.append([po_number, po_date, vendor, status, "INR", buyer, n, code, desc, hsn, uom, qty,
                            price, f"{amount}", basis, received])
    write_csv(data_dir / "purchase_orders.csv",
              ["po_number", "po_date", "vendor_id", "status", "currency", "buyer", "line_no", "item_code",
               "description", "hsn_sac", "uom", "qty_ordered", "unit_price", "line_amount", "match_basis",
               "qty_received"], po_rows)
    head_rows, line_rows = [], []
    for ledger_id, vendor, inv_no, inv_date, po, lines, rate in HISTORY:
        taxable = sum((D(a) for *_x, a in lines), D(0))
        tax = q2(taxable * D(rate) / 100)
        head_rows.append([ledger_id, vendor, inv_no, inv_date, po, f"{q2(taxable)}", f"{tax}",
                          f"{q2(taxable + tax)}", "POSTED"])
        for po_line, desc, qty, price, amount in lines:
            line_rows.append([ledger_id, po_line, desc, qty, price, amount])
    write_csv(data_dir / "invoice_history.csv",
              ["ledger_id", "vendor_id", "invoice_no", "invoice_date", "po_number", "taxable_amount",
               "tax_amount", "total_amount", "status"], head_rows)
    write_csv(data_dir / "invoice_history_lines.csv",
              ["ledger_id", "po_line_no", "description", "qty", "unit_price", "amount"], line_rows)


def write_samples(samples_dir: Path) -> None:
    samples_dir.mkdir(parents=True, exist_ok=True)
    (samples_dir / "truth").mkdir(exist_ok=True)
    (samples_dir / "bad_inputs").mkdir(exist_ok=True)
    manifest = []
    for order, inv in enumerate(SAMPLES, 1):
        pdf = build_sample(inv)
        name = f"{inv['id']}.pdf"
        (samples_dir / name).write_bytes(pdf)
        (samples_dir / "truth" / f"{inv['id']}.json").write_text(
            json.dumps(truth_for(inv), indent=2), encoding="utf-8")
        title, group, verdict, reasons, description = MANIFEST_META[inv["id"]]
        manifest.append({"id": inv["id"], "file": name, "order": order, "title": title, "group": group,
                         "scanned": inv["scanned"], "layout": inv["layout"], "expected_verdict": verdict,
                         "expected_reasons": reasons, "description": description})
        if inv["id"] in WITH_LLM:
            manifest[-1]["with_llm"] = WITH_LLM[inv["id"]]
    bad = [
        ("quotation_not_invoice.pdf", render_quotation_pdf(), "Quotation sent to the invoice inbox",
         "REJECT", ["NOT_A_TAX_INVOICE"], "A quotation in an invoice-like layout. Nothing is payable."),
        ("newsletter_not_invoice.pdf", render_newsletter_pdf(), "Unrelated PDF", "REJECT", ["NOT_AN_INVOICE"],
         "A staff bulletin. No invoice fields at all."),
        ("corrupt_file.pdf", corrupt_bytes(), "Corrupt file", "REJECT", ["UNREADABLE_FILE"],
         "Starts like a PDF, then random bytes."),
        ("empty_file.pdf", b"", "Empty file", "REJECT", ["UNREADABLE_FILE"], "Zero bytes."),
    ]
    for order, (name, content, title, verdict, reasons, description) in enumerate(bad, len(SAMPLES) + 1):
        (samples_dir / "bad_inputs" / name).write_bytes(content)
        manifest.append({"id": Path(name).stem, "file": f"bad_inputs/{name}", "order": order, "title": title,
                         "group": "bad_input", "scanned": False, "layout": "-", "expected_verdict": verdict,
                         "expected_reasons": reasons, "description": description})
    (samples_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- unseen-layout variants

LABEL_POOLS = {
    "inv_no": ["Invoice No.", "Invoice Number", "Inv. No.", "Bill No.", "Invoice #", "Tax Invoice No."],
    "inv_date": ["Dated", "Invoice Date", "Date of Invoice", "Bill Date", "Date"],
    "po": ["Buyer's Order No.", "PO No.", "P.O. Number", "Purchase Order No.", "Your Order Ref.",
           "PO Reference", "Order No."],
    "subtotal": ["Sub Total", "Subtotal", "Taxable Value", "Total Taxable Value", "Amount before tax"],
    "total": ["Grand Total", "Total Amount", "Invoice Total", "Net Payable", "Total Payable", "Amount Due"],
    "desc": ["Description of Goods", "Particulars", "Item Description", "Description", "Item"],
    "qty": ["Qty", "Quantity", "Nos"],
    "rate": ["Rate", "Unit Price", "Price", "Rate (Rs.)"],
    "amount": ["Amount", "Taxable Value", "Line Total", "Amount (Rs.)", "Value"],
}
DATE_FORMATS = ["%d-%b-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %B %Y", "%d-%m-%Y"]
VARIANT_ITEMS = [
    ("Industrial ball bearing 6205 ZZ", "8482", "Nos"), ("Hydraulic hose assembly 1/2 inch x 2 m", "4009", "Nos"),
    ("Cutting oil, soluble, 20 litre pail", "2710", "Pail"), ("LED high bay light 150 W", "9405", "Nos"),
    ("Pneumatic cylinder 50 mm bore", "8412", "Nos"), ("Welding electrode E6013, 3.15 mm", "8311", "Box"),
    ("Thermal label roll 100 x 150 mm", "4821", "Roll"), ("Network cable Cat6, 305 m box", "8544", "Box"),
    ("Hex bolt M12 x 50, zinc plated", "7318", "Kg"), ("Inkjet coding fluid, black, 1 litre", "3215", "Bottle"),
]


def variant_spec(index: int) -> dict:
    """A layout the reader was not tuned on: random style, labels, date format, items and
    amounts. Seeded by index so the scorecard is repeatable."""
    rnd = random.Random(SEED * 1000 + index)
    vendor = rnd.choice(["V001", "V002", "V003", "V004", "V005", "V006"])
    intra = VENDOR[vendor]["state_code"] == BUYER["state_code"]
    styles = ["classic", "modern", "typewriter"] + ([] if intra else ["grid"])
    layout = rnd.choice(styles)
    labels = {k: rnd.choice(v) for k, v in LABEL_POOLS.items()}
    if layout == "grid":
        labels.pop("subtotal")
        labels["amount"] = "Taxable Value"
        labels["total"] = rnd.choice(["Invoice Total (in figures)", "Invoice Value"])
    if layout == "typewriter":
        labels["subtotal"] = rnd.choice(["Total", "Sub Total", "Taxable Value"])
    if labels.get("subtotal") == "Taxable Value" and labels["amount"] == "Taxable Value":
        labels["amount"] = "Amount"
    n_lines = rnd.randint(1, 4)
    items = rnd.sample(VARIANT_ITEMS, n_lines)
    lines = []
    for desc, hsn, uom in items:
        qty = rnd.choice([1, 2, 3, 5, 8, 10, 12, 24, 40, 75, 120])
        rate = D(rnd.choice([85, 240, 615, 1290, 1875, 3420, 7600, 12500, 48250])) + D(rnd.choice(["0", "0.50", "0.75"]))
        lines.append(line(desc, hsn, qty, uom, f"{rate:.2f}"))
    day = rnd.randint(1, 20)
    number = rnd.choice(["INV", "TI", "SB", "GST"]) + rnd.choice(["/", "-"]) + f"{rnd.randint(100, 9999)}"
    if rnd.random() < 0.5:
        number = f"{rnd.choice(['A', 'KA', 'HO'])}/26-27/{rnd.randint(1, 999):04d}"
    return spec(id=f"variant_{index:03d}", layout=layout, vendor=vendor, invoice_no=number,
                invoice_date=date(2026, 11, day), po_ref=f"PO-2026-{rnd.randint(300, 999):04d}",
                po_date=date(2026, 10, rnd.randint(1, 28)), labels=labels,
                date_format=rnd.choice(DATE_FORMATS), lines=lines)


def main() -> None:
    write_master_data(ROOT / "data")
    write_samples(ROOT / "samples")
    print(f"wrote {len(VENDORS)} vendors, {len(PURCHASE_ORDERS)} purchase orders, {len(HISTORY)} history "
          f"invoices and {len(SAMPLES)} sample invoices plus 4 bad inputs")
    print(f"buyer GSTIN {BUYER['gstin']}")


if __name__ == "__main__":
    main()
