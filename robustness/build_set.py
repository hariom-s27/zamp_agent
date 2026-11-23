"""Build the robustness set: invoices made with tools, fonts and layouts that the sample
generator (scripts/generate_test_data.py, ReportLab) does not use.

    python robustness/build_set.py          # rewrites robustness/documents/ and robustness/truth.json

You do NOT need to run this to use the project, and normally should not. The PDFs it made
are in robustness/documents/, and `python -m scripts.evaluate_robustness` reads them. The
script is here so anyone can see exactly how the set was made. The tools stamp the time
into each PDF, so a rebuild gives files with the same content but different bytes; the
recorded OCR output in fixtures/ocr/ is filed by file hash and would have to be recorded
again (`python -m scripts.evaluate_robustness --record-ocr`, needs Tesseract).

Running it needs tools that the app itself does not: Google Chrome (headless),
wkhtmltopdf, LibreOffice, pdflatex, pdftoppm, and the Python packages openpyxl,
python-docx, opencv-python-headless, Pillow, numpy and img2pdf.

Why a second generator at all: the round 1 scorecard measured layouts drawn by the same
code that drew the samples the reader was built on. An independent audit called that out.
Here nothing is shared with that generator except the fictional buyer.

How the layouts were chosen (RESEARCH.md section 13): the patterns Indian accounting
packages print. A Tally-style grid with the tax ledgers as rows under the items, a
separate HSN summary and "Buyer's Order No."; a Zoho-style invoice with per-line tax
columns and "Balance Due"; a Busy-style bill with three-decimal quantities; an invoice
kept in Excel with the rate before the quantity; one typed in Word; one set in LaTeX;
a tax-inclusive retail bill; a long invoice over several pages. Then photographs of
paper: perspective, tilt, uneven light, JPEG artefacts, a page turned on its side, a
total written by hand, and a fax.

Two splits. Problems found on the TUNE split were used to improve the reader. The HOLDOUT
split uses other tools and layout families, and its failures were not looked at until the
final measurement (docs/robustness_report.md).

Everything is fictional. Every page says so.
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from html import escape as E
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from invoice_desk.textutil import amount_to_words, fmt_inr, gstin_is_valid, make_gstin, state_name  # noqa: E402

OUT = HERE / "documents"
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or "google-chrome"
NOTE = "Synthetic test document for a case study. Fictional parties. Not a real invoice."
BUYER = {"name": "Nilgiri Precision Components Pvt Ltd",
         "address": ["Plot 42, Peenya Industrial Area, Phase II", "Bengaluru 560058, Karnataka"],
         "state_code": "29", "gstin": make_gstin("29", "AAKCN4821M")}

# Vendors that exist only in this set. The evaluator adds each one to the vendor master for its own documents.
VENDORS = {
    "R01": ("Kaveri Bearings & Transmission Pvt Ltd", "AAFCK4410J", "29", ["No. 14, SJP Road, Kalasipalyam", "Bengaluru 560002, Karnataka"],
            "Tunga Valley Bank", "501200047731", "TVBL0005012", "080-5550-0161", "accounts@kaveribearings.example"),
    "R02": ("Shivalik Switchgear Industries", "ABMFS2291P", "07", ["B-41, Okhla Industrial Area, Phase I", "New Delhi 110020"],
            "Yamuna Mercantile Bank", "730011902284", "YMBL0007300", "011-5550-0143", "billing@shivalikswitchgear.example"),
    "R03": ("Godavari Polymers LLP", "AARFG7715C", "36", ["Plot 88, IDA Jeedimetla, Phase III", "Hyderabad 500055, Telangana"],
            "Musi Co-operative Bank", "118800236640", "MCBL0001188", "040-5550-0112", "ar@godavaripolymers.example"),
    "R04": ("Narmada Fasteners Pvt Ltd", "AAGCN6032H", "24", ["Shed 211, GIDC Makarpura", "Vadodara 390010, Gujarat"],
            "Vishwamitri Urban Bank", "240700915562", "VUBL0002407", "0265-555-0188", "sales@narmadafasteners.example"),
    "R05": ("Sabarmati Controls & Automation", "ACDFS9140L", "24", ["407, Sakar Complex, Ashram Road", "Ahmedabad 380009, Gujarat"],
            "Vishwamitri Urban Bank", "240911003478", "VUBL0002409", "079-5550-0126", "accounts@sabarmaticontrols.example"),
    "R06": ("Teesta Print & Pack", "AAYFT3358D", "19", ["27/B, Topsia Road South", "Kolkata 700046, West Bengal"],
            "Hooghly Peoples Bank", "190045118203", "HPBL0001900", "033-5550-0171", "office@teestaprintpack.example"),
    "R07": ("Vaigai Cables Pvt Ltd", "AAHCV5527N", "33", ["SF No. 118, SIDCO Industrial Estate, Kappalur", "Madurai 625008, Tamil Nadu"],
            "Thamirabarani Bank", "330120076418", "TBBL0003301", "0452-555-0134", "finance@vaigaicables.example"),
    "R08": ("Sharavathi Lubricants & Chemicals", "ABEFS8804Q", "29", ["Survey 62, Bommasandra Industrial Area", "Bengaluru 560099, Karnataka"],
            "Tunga Valley Bank", "501200059902", "TVBL0005012", "080-5550-0194", "billing@sharavathilubes.example"),
    "R09": ("Indrayani Process Consultants LLP", "AAOFI2206B", "27", ["Office 9, Bhosari MIDC, Telco Road", "Pune 411026, Maharashtra"],
            "Mula Mutha Bank", "270300418876", "MMBL0002703", "020-5550-0158", "accounts@indrayaniconsult.example"),
    "R10": ("Hemavathi Stationers", "ACQFH1173R", "29", ["No. 6, Avenue Road, Chickpet", "Bengaluru 560053, Karnataka"],
            "Tunga Valley Bank", "501200061145", "TVBL0005012", "080-5550-0107", "hemavathi@stationers.example"),
}
ITEMS = [  # description, HSN, unit, rate
    ("Deep Groove Ball Bearing 6205 2RS", "84821011", "Nos", "385.00"), ("Taper Roller Bearing 32208", "84822011", "Nos", "1460.00"),
    ("V-Belt B-52 Wrapped", "40103110", "Nos", "212.50"), ("Timing Belt HTD 1120-8M-30", "40103999", "Nos", "1875.00"),
    ("MCB 32A Triple Pole C Curve", "85362030", "Nos", "742.00"), ("Contactor 3 Pole 40A 415V Coil", "85364900", "Nos", "2315.00"),
    ("PVC Insulated Copper Cable 4 sq mm x 90 m", "85444999", "Coil", "6840.00"), ("HDPE Granules Blow Grade", "39012000", "Kg", "118.40"),
    ("Hex Bolt M16 x 75 Grade 8.8", "73181500", "Nos", "34.60"), ("Spring Washer M16", "73182100", "Nos", "3.25"),
    ("Hydraulic Oil ISO VG 68, 210 L Barrel", "27101980", "Barrel", "38750.00"), ("Lithium Grease EP2, 18 kg Pail", "27101990", "Pail", "4980.00"),
    ("Proximity Sensor Inductive M18 PNP", "85365090", "Nos", "1295.00"), ("Corrugated Box 5 Ply 450x300x300 mm", "48191010", "Nos", "62.75"),
    ("Thermal Transfer Ribbon 110 mm x 300 m", "96121010", "Roll", "418.00"), ("Solenoid Valve 5/2 Way 1/4 inch", "84812000", "Nos", "1680.00"),
    ("A4 Copier Paper 75 GSM (Ream of 500)", "48025690", "Ream", "268.00"), ("Whiteboard Marker (Box of 10)", "96082000", "Box", "185.00"),
]
SERVICES = [("Process audit of CNC cell, on-site days", "998311", "Days", "18500.00"),
            ("Preparation of control plan and PFMEA documentation", "998311", "Lot", "42000.00"),
            ("Operator training workshop, half day", "999293", "Session", "9500.00")]
HINDI = {"blessing": "\u0965 \u0936\u094d\u0930\u0940 \u0917\u0923\u0947\u0936\u093e\u092f \u0928\u092e\u0903 \u0965",     # an invocation printed on top of many traders' bills
         "number": "\u092c\u0940\u091c\u0915 \u0938\u0902.", "bill": "\u092c\u093f\u0932 \u0928\u0902.", "date": "\u0926\u093f\u0928\u093e\u0902\u0915"}
D = Decimal


def q2(x) -> Decimal:
    return D(str(x)).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def inr(x, western: bool = False) -> str:
    return f"{q2(x):,.2f}" if western else fmt_inr(x, symbol="")


def make(doc_id, family, tool, font, split, vendor, number, day, po, items, *, kind="text", quirks=(), gst=18, round_off=False,
         expect="APPROVE", reason=None, po_override=None, date_style="%d-%b-%Y", currency="INR", title="Tax Invoice",
         tax_inclusive=False, lump=None, register_vendor=True, quote_po=True, scan=None, po_day=None,
         total_typo=None, printed_account=None, gstin_typo=False, bilingual=False) -> dict:
    """One document: its data, how it is rendered, and what the right outcome is.

    Three vendor mistakes can be planted. `total_typo` is added to the printed total only, so
    the figures no longer add up. `printed_account` prints a bank account that is not the one
    in the vendor master. `gstin_typo` changes one digit of the printed GSTIN, so its check
    character fails. The vendor master always holds the correct details."""
    name, pan, state, address, bank, account, ifsc, phone, email = VENDORS[vendor]
    ordered = []
    for index, qty in items:
        desc, hsn, unit, rate = (SERVICES if family == "latex" else ITEMS)[index]
        ordered.append({"description": desc, "hsn": hsn, "uom": unit, "qty": D(str(qty)), "rate": D(rate),
                        "amount": q2(D(str(qty)) * D(rate))})
    lines = ordered
    if lump:
        lines = [{"description": lump[0], "hsn": lump[1], "uom": None, "qty": None, "rate": None, "amount": q2(lump[2])}]
    printed = sum((ln["amount"] for ln in lines), D(0))
    rate = D(gst)
    if tax_inclusive:            # printed prices already include GST
        total = printed
        taxable = q2(total / (1 + rate / 100))
        tax = total - taxable
    else:
        taxable = printed
        tax = q2(taxable * rate / 100)
        total = taxable + tax
    intra = state == BUYER["state_code"]
    cgst = q2(tax / 2) if intra else D(0)
    sgst = tax - cgst if intra else D(0)
    igst = D(0) if intra else tax
    rounding = D(0)
    if round_off:
        rounded = total.quantize(D("1"), rounding=ROUND_HALF_UP)
        rounding, total = rounded - total, q2(rounded)
    if total_typo:
        total = total + D(total_typo)
    gstin = make_gstin(state, pan)
    printed_gstin = gstin
    if gstin_typo:                                   # one digit of the PAN part is typed wrong
        printed_gstin = gstin[:7] + str((int(gstin[7]) + 1) % 10) + gstin[8:]
        assert not gstin_is_valid(printed_gstin)
    inv_date = date(2026, 11, day)
    po_date = date(2026, 10, po_day or (10 + day % 15))
    return {"id": doc_id, "family": family, "tool": tool, "font": font, "split": split, "kind": kind, "quirks": list(quirks),
            "vendor_id": vendor, "vendor": {"name": name, "pan": pan, "state_code": state, "address": address,
                                            "gstin": printed_gstin, "bank_name": bank,
                                            "bank_account": printed_account or account,
                                            "ifsc": ifsc, "phone": phone, "email": email},
            "master_gstin": gstin, "master_account": account, "ordered": ordered,
            "register_vendor": register_vendor, "number": number, "date": inv_date, "date_style": date_style,
            "po": po if quote_po else None, "po_number": po, "po_date": po_date, "po_override": po_override, "lines": lines,
            "gst": rate, "intra": intra, "taxable": taxable, "tax": tax, "cgst": cgst, "sgst": sgst, "igst": igst,
            "rounding": rounding, "total": total, "tax_inclusive": tax_inclusive, "currency": currency, "title": title,
            "expect": expect, "reason": reason, "scan": scan, "bilingual": bilingual}


def fmt(d: date, style: str) -> str:
    if style == "ordinal":
        n = d.day
        suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix} {d.strftime('%B %Y')}"
    return d.strftime(style)


def words(x, lead="INR") -> str:
    text = amount_to_words(x)                       # 'Rupees ... Only'
    return text.replace("Rupees", lead, 1) if lead != "Rupees" else text


def qty_text(q: Decimal, places: int = 0) -> str:
    return f"{q:.{places}f}" if places else (f"{q.normalize():f}" if q != q.to_integral() else f"{int(q)}")


# --------------------------------------------------------------------------- HTML families

BASE_CSS = "@page{size:A4;margin:12mm} body{margin:0;color:#111} table{border-collapse:collapse;width:100%} td,th{vertical-align:top}"


def page(body: str, font: str, size: str = "9.5pt", css: str = "") -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{BASE_CSS} body{{font-family:'{font}';font-size:{size}}} {css}"
            f"</style></head><body>{body}<div style='text-align:center;font-size:6.5pt;color:#777;margin-top:6px'>{NOTE}</div></body></html>")


def tally_html(v: dict, hand_total: bool = False, long_table: bool = False) -> str:
    """The grid most Tally users print: stacked label over value in the header boxes, tax
    ledgers as rows under the items, a quantity total, and a separate HSN summary."""
    s, t = v["vendor"], v["title"]
    number_label = {"Credit Note": "Credit Note No."}.get(t, "Invoice No.")
    dated, blessing = "Dated", ""
    if v["bilingual"]:           # a trader's bill from the Hindi belt: an invocation on top, labels in two languages
        number_label, dated = f"{number_label} / {HINDI['number']}", f"Dated / {HINDI['date']}"
        blessing = f"<div class='c' style='font-size:8pt'>{HINDI['blessing']}</div>"
    box = lambda label, value="": f"<td class='q'>{label}<br><b>{E(str(value)) or '&nbsp;'}</b></td>"   # noqa: E731
    head = f"""{blessing}<div class='t'>{t}</div><table class='f'>
<tr><td class='h' rowspan='3'><b>{E(s['name'])}</b><br>{'<br>'.join(E(a) for a in s['address'])}<br>GSTIN/UIN: {s['gstin']}<br>
State Name : {state_name(s['state_code'])}, Code : {s['state_code']}<br>E-Mail : {s['email']}</td>
{box(number_label, v['number'])}{box(dated, fmt(v['date'], v['date_style']))}</tr>
<tr>{box('Delivery Note')}{box('Mode/Terms of Payment', '30 Days')}</tr>
<tr>{box('Reference No. &amp; Date.')}{box('Other References')}</tr>
<tr><td class='h' rowspan='3'>Buyer (Bill to)<br><b>{E(BUYER['name'])}</b><br>{'<br>'.join(BUYER['address'])}<br>
GSTIN/UIN : {BUYER['gstin']}<br>State Name : Karnataka, Code : 29</td>
{box("Buyer's Order No.", v['po'] or '')}{box('Dated', fmt(v['po_date'], v['date_style']) if v['po'] else '')}</tr>
<tr>{box('Dispatch Doc No.')}{box('Delivery Note Date')}</tr>
<tr>{box('Dispatched through', 'Road')}{box('Destination', 'Bengaluru')}</tr></table>"""
    rows = ""
    for n, ln in enumerate(v["lines"], 1):
        rows += (f"<tr><td class='c'>{n}</td><td><b>{E(ln['description'])}</b></td><td>{ln['hsn']}</td>"
                 f"<td class='r'><b>{qty_text(ln['qty'])} {ln['uom']}</b></td><td class='r'>{inr(ln['rate'])}</td><td>{ln['uom']}</td>"
                 f"<td class='r'><b>{inr(ln['amount'])}</b></td></tr>")
    ledger = lambda name, amount: (f"<tr><td></td><td class='r'><b><i>{name}</i></b></td><td></td><td></td><td></td><td></td>"   # noqa: E731
                                   f"<td class='r'><b>{amount}</b></td></tr>")
    half = v["gst"] / 2
    if v["intra"]:
        rows += ledger(f"Output CGST {half:g}%", inr(v["cgst"])) + ledger(f"Output SGST {half:g}%", inr(v["sgst"]))
    else:
        rows += ledger(f"Output IGST {v['gst']:g}%", inr(v["igst"]))
    if v["rounding"]:
        sign = "(-)" if v["rounding"] < 0 else ""
        rows += ledger("Round Off", f"{sign}{inr(abs(v['rounding']))}")
    units = {ln["uom"] for ln in v["lines"]}
    total_qty = f"{qty_text(sum(ln['qty'] for ln in v['lines']))} {units.pop()}" if len(units) == 1 else ""
    shown_total = (f"<span class='pen'>{inr(v['total'])}</span>" if hand_total else f"&#8377; {inr(v['total'])}")
    rows += (f"<tr class='tot'><td></td><td class='r'>Total</td><td></td><td class='r'><b>{total_qty}</b></td><td></td><td></td>"
             f"<td class='r'><b>{shown_total}</b></td></tr>")
    items = (f"<table class='i'><thead><tr><th>Sl<br>No.</th><th>Description of Goods</th><th>HSN/SAC</th><th>Quantity</th>"
             f"<th>Rate</th><th>per</th><th>Amount</th></tr></thead><tbody>{rows}</tbody></table>")
    by_hsn: dict[str, Decimal] = {}
    for ln in v["lines"]:
        by_hsn[ln["hsn"]] = by_hsn.get(ln["hsn"], D(0)) + ln["amount"]
    hsn_rows = ""
    for code, value in by_hsn.items():
        tax = q2(value * v["gst"] / 100)
        if v["intra"]:
            hsn_rows += (f"<tr><td>{code}</td><td class='r'>{inr(value)}</td><td class='r'>{half:g}%</td><td class='r'>{inr(q2(tax / 2))}</td>"
                         f"<td class='r'>{half:g}%</td><td class='r'>{inr(tax - q2(tax / 2))}</td><td class='r'>{inr(tax)}</td></tr>")
        else:
            hsn_rows += (f"<tr><td>{code}</td><td class='r'>{inr(value)}</td><td class='r'>{v['gst']:g}%</td><td class='r'>{inr(tax)}</td>"
                         f"<td class='r'>{inr(tax)}</td></tr>")
    if v["intra"]:
        hsn_head = ("<tr><th rowspan='2'>HSN/SAC</th><th rowspan='2'>Taxable<br>Value</th><th colspan='2'>Central Tax</th>"
                    "<th colspan='2'>State Tax</th><th rowspan='2'>Total<br>Tax Amount</th></tr>"
                    "<tr><th>Rate</th><th>Amount</th><th>Rate</th><th>Amount</th></tr>")
        hsn_total = (f"<tr><td class='r'><b>Total</b></td><td class='r'><b>{inr(v['taxable'])}</b></td><td></td><td class='r'><b>{inr(v['cgst'])}</b></td>"
                     f"<td></td><td class='r'><b>{inr(v['sgst'])}</b></td><td class='r'><b>{inr(v['tax'])}</b></td></tr>")
    else:
        hsn_head = ("<tr><th rowspan='2'>HSN/SAC</th><th rowspan='2'>Taxable<br>Value</th><th colspan='2'>Integrated Tax</th>"
                    "<th rowspan='2'>Total<br>Tax Amount</th></tr><tr><th>Rate</th><th>Amount</th></tr>")
        hsn_total = (f"<tr><td class='r'><b>Total</b></td><td class='r'><b>{inr(v['taxable'])}</b></td><td></td>"
                     f"<td class='r'><b>{inr(v['igst'])}</b></td><td class='r'><b>{inr(v['tax'])}</b></td></tr>")
    tail = f"""<table class='f'><tr><td>Amount Chargeable (in words) <span style='float:right'><i>E. &amp; O.E</i></span><br>
<b>{words(v['total'])}</b></td></tr></table>
<table class='i s'>{hsn_head}{hsn_rows}{hsn_total}</table>
<table class='f'><tr><td>Tax Amount (in words) : <b>{words(v['tax'])}</b></td></tr></table>
<table class='f'><tr><td class='h'>Company's PAN : <b>{s['pan']}</b><br><u>Declaration</u><br>We declare that this invoice shows the actual
price of the goods described and that all particulars are true and correct.</td>
<td class='h'>Company's Bank Details<br>A/c Holder's Name : <b>{E(s['name'])}</b><br>Bank Name : <b>{s['bank_name']}</b><br>
A/c No. : <b>{s['bank_account']}</b><br>Branch &amp; IFS Code : <b>{s['ifsc']}</b></td></tr>
<tr><td class='h'>Customer's Seal and Signature</td><td class='h r'><b>for {E(s['name'])}</b><br><br><br>Authorised Signatory</td></tr></table>
<div class='c' style='margin-top:4px'>This is a Computer Generated Invoice</div>"""
    css = (".t{text-align:center;font-weight:bold;font-size:12pt;margin-bottom:4px} .f td{border:1px solid #000;padding:3px 5px}"
           ".h{width:50%} .q{width:25%} .i th,.i td{border-left:1px solid #000;border-right:1px solid #000;padding:2px 5px}"
           ".i th{border:1px solid #000;font-weight:normal} .i{border:1px solid #000} .tot td{border-top:1px solid #000;border-bottom:1px solid #000}"
           ".s td,.s th{border:1px solid #000} .r{text-align:right} .c{text-align:center}"
           ".pen{font-family:'TeX Gyre Chorus';font-size:19pt;color:#1a2f8f;display:inline-block;transform:rotate(-4deg)}"
           + (" .i td{padding:1px 5px}" if long_table else ""))
    return page(head + items + tail, v["font"], css=css)


def zoho_html(v: dict) -> str:
    """The clean invoice many small companies send from a cloud accounting tool: tax shown
    per line in its own columns, 'Sub Total', 'Balance Due', a dark table header."""
    s = v["vendor"]
    title = {"Proforma Invoice": "PROFORMA INVOICE"}.get(v["title"], "TAX INVOICE")
    rows = ""
    half = v["gst"] / 2
    for n, ln in enumerate(v["lines"], 1):
        tax = q2(ln["amount"] * v["gst"] / 100)
        taxes = (f"<td class='r'>{half:g}%<br><span class='m'>{inr(q2(tax / 2))}</span></td><td class='r'>{half:g}%<br><span class='m'>{inr(tax - q2(tax / 2))}</span></td>"
                 if v["intra"] else f"<td class='r'>{v['gst']:g}%<br><span class='m'>{inr(tax)}</span></td>")
        rows += (f"<tr><td>{n}</td><td>{E(ln['description'])}<br><span class='m'>As per your purchase order</span></td><td>{ln['hsn']}</td>"
                 f"<td class='r'>{ln['qty']:.2f}<br><span class='m'>{ln['uom']}</span></td><td class='r'>{inr(ln['rate'])}</td>{taxes}"
                 f"<td class='r'>{inr(ln['amount'])}</td></tr>")
    tax_head = "<th class='r'>CGST</th><th class='r'>SGST</th>" if v["intra"] else "<th class='r'>IGST</th>"
    tax_rows = (f"<tr><td>CGST{half:g} ({half:g}%)</td><td class='r'>{inr(v['cgst'])}</td></tr><tr><td>SGST{half:g} ({half:g}%)</td><td class='r'>{inr(v['sgst'])}</td></tr>"
                if v["intra"] else f"<tr><td>IGST{v['gst']:g} ({v['gst']:g}%)</td><td class='r'>{inr(v['igst'])}</td></tr>")
    po_row = f"<tr><td>P.O.#</td><td>: {v['po']}</td></tr>" if v["po"] else ""
    body = f"""<table><tr><td style='width:55%'><div class='n'>{E(s['name'])}</div>{'<br>'.join(E(a) for a in s['address'])}<br>GSTIN {s['gstin']}<br>{s['email']}</td>
<td class='r'><div class='big'>{title}</div><div># {v['number']}</div><div class='m' style='margin-top:10px'>Balance Due</div><div class='bal'>&#8377;{inr(v['total'])}</div></td></tr></table>
<table style='margin-top:14px'><tr><td style='width:55%'><table class='k'>
<tr><td>Invoice Date</td><td>: {fmt(v['date'], v['date_style'])}</td></tr><tr><td>Terms</td><td>: Net 30</td></tr>
<tr><td>Due Date</td><td>: {fmt(v['date'] + timedelta(days=30), v['date_style'])}</td></tr>{po_row}</table></td>
<td><table class='k'><tr><td>Place Of Supply</td><td>: Karnataka (29)</td></tr></table></td></tr></table>
<div style='margin-top:12px' class='m'>Bill To</div><b>{E(BUYER['name'])}</b><br>{'<br>'.join(BUYER['address'])}<br>GSTIN {BUYER['gstin']}
<table class='it' style='margin-top:12px'><thead><tr><th>#</th><th>Item &amp; Description</th><th>HSN/SAC</th><th class='r'>Qty</th><th class='r'>Rate</th>{tax_head}<th class='r'>Amount</th></tr></thead>
<tbody>{rows}</tbody></table>
<table style='margin-top:8px'><tr><td style='width:55%'><div class='m'>Total In Words</div><b><i>{words(v['total'], 'Indian Rupee')}</i></b>
<div class='m' style='margin-top:14px'>Notes</div>Thanks for your business.<div class='m' style='margin-top:8px'>Bank Details</div>
{s['bank_name']}, A/c {s['bank_account']}, IFSC {s['ifsc']}</td>
<td><table class='sum'><tr><td>Sub Total</td><td class='r'>{inr(v['taxable'])}</td></tr>{tax_rows}
<tr class='b'><td>Total</td><td class='r'>&#8377;{inr(v['total'])}</td></tr><tr class='b bg'><td>Balance Due</td><td class='r'>&#8377;{inr(v['total'])}</td></tr></table>
<div class='r' style='margin-top:36px'>Authorized Signature ____________________</div></td></tr></table>"""
    css = (".n{font-size:13pt;font-weight:bold} .big{font-size:22pt;letter-spacing:1px} .bal{font-size:13pt;font-weight:bold} .m{color:#666;font-size:8pt}"
           ".k td{padding:2px 6px 2px 0} .it th{background:#3c3d3a;color:#fff;padding:6px 8px;text-align:left;font-weight:normal}"
           ".it td{padding:7px 8px;border-bottom:1px solid #bbb} .r{text-align:right !important} .sum td{padding:5px 8px} .b td{font-weight:bold} .bg td{background:#f0f0f0}")
    return page(body, v["font"], "9pt", css)


def busy_html(v: dict) -> str:
    """The dense bill printed by desktop accounting packages: centred header, label : value
    pairs in two columns, quantities to three decimals, taxes added below the item total."""
    s = v["vendor"]
    rows = ""
    for n, ln in enumerate(v["lines"], 1):
        rows += (f"<tr><td class='c'>{n}.</td><td>{E(ln['description'])}</td><td class='c'>{ln['hsn']}</td><td class='r'>{ln['qty']:.3f}</td>"
                 f"<td class='c'>{ln['uom']}</td><td class='r'>{inr(ln['rate'])}</td><td class='r'>{inr(ln['amount'])}</td></tr>")
    half = v["gst"] / 2
    adds = (f"<tr><td colspan='6' class='r'>Add : CGST @ {half:.2f} %</td><td class='r'>{inr(v['cgst'])}</td></tr>"
            f"<tr><td colspan='6' class='r'>Add : SGST @ {half:.2f} %</td><td class='r'>{inr(v['sgst'])}</td></tr>" if v["intra"] else
            f"<tr><td colspan='6' class='r'>Add : IGST @ {v['gst']:.2f} %</td><td class='r'>{inr(v['igst'])}</td></tr>")
    if v["rounding"]:
        adds += (f"<tr><td colspan='6' class='r'>{'Less' if v['rounding'] < 0 else 'Add'} : Rounded Off ({'-' if v['rounding'] < 0 else '+'})</td>"
                 f"<td class='r'>{inr(abs(v['rounding']))}</td></tr>")
    order = f"{v['po']} &nbsp; Dt. {fmt(v['po_date'], v['date_style'])}" if v["po"] else ""
    number_label, dated, blessing = "Invoice No.", "Dated", ""
    if v["bilingual"]:
        number_label, dated = f"{HINDI['bill']} / Invoice No.", f"{HINDI['date']} / Dated"
        blessing = f"<div class='c' style='font-size:8pt'>{HINDI['blessing']}</div>"
    body = f"""{blessing}<table><tr><td style='width:33%'>GSTIN : {s['gstin']}</td><td class='c'><b><u>TAX INVOICE</u></b></td><td class='r' style='width:33%'>Original for Recipient</td></tr></table>
<div class='c' style='font-size:15pt;font-weight:bold;margin-top:4px'>{E(s['name'].upper())}</div>
<div class='c'>{E(', '.join(s['address']))}<br>Phone : {s['phone']} &nbsp; E-mail : {s['email']}</div>
<table class='g' style='margin-top:6px'><tr><td style='width:50%'><table class='k'>
<tr><td>{number_label}</td><td>: <b>{v['number']}</b></td></tr><tr><td>{dated}</td><td>: <b>{fmt(v['date'], v['date_style'])}</b></td></tr>
<tr><td>Place of Supply</td><td>: Karnataka (29)</td></tr></table></td>
<td><table class='k'><tr><td>Transport</td><td>: By Road</td></tr><tr><td>Vehicle No.</td><td>: KA 01 AB 4471</td></tr>
<tr><td>Order No.</td><td>: {order}</td></tr></table></td></tr>
<tr><td><i>Billed to :</i><br><b>{E(BUYER['name'])}</b><br>{'<br>'.join(BUYER['address'])}<br>GSTIN / UIN : {BUYER['gstin']}</td>
<td><i>Shipped to :</i><br><b>{E(BUYER['name'])}</b><br>{'<br>'.join(BUYER['address'])}</td></tr></table>
<table class='g it'><tr><th>S.N.</th><th>Description of Goods</th><th>HSN/SAC<br>Code</th><th>Qty.</th><th>Unit</th><th>Price</th><th>Amount(&#8377;)</th></tr>
{rows}<tr class='tt'><td colspan='3' class='r'>Total</td><td class='r'>{sum(ln['qty'] for ln in v['lines']):.3f}</td><td></td><td></td><td class='r'>{inr(v['taxable'])}</td></tr>
{adds}<tr class='tt'><td colspan='6' class='r'><b>Grand Total</b></td><td class='r'><b>{inr(v['total'])}</b></td></tr></table>
<table class='g'><tr><td><b>{words(v['total'], 'Rupees')}</b></td></tr>
<tr><td>Bank Details : {s['bank_name']} &nbsp; A/c No. {s['bank_account']} &nbsp; IFSC {s['ifsc']}</td></tr>
<tr><td><table><tr><td style='width:60%'><u>Terms &amp; Conditions</u><br>E.&amp; O.E.<br>1. Goods once sold will not be taken back.<br>2. Interest @ 18% p.a. will be charged if payment is delayed.<br>3. Subject to local jurisdiction only.</td>
<td class='r'>Receiver's Signature :<br><br>For <b>{E(s['name'].upper())}</b><br><br><br>Authorised Signatory</td></tr></table></td></tr></table>"""
    css = (".c{text-align:center} .r{text-align:right} .g{border:1px solid #000} .g>tbody>tr>td{border:1px solid #000;padding:3px 5px}"
           ".k td{padding:1px 4px;border:none} .it th{border:1px solid #000;padding:3px} .it td{border-left:1px solid #000;border-right:1px solid #000;padding:2px 5px}"
           ".tt td{border-top:1px solid #000 !important;border-bottom:1px solid #000 !important}")
    return page(body, v["font"], "8.8pt", css)


def retail_html(v: dict) -> str:
    """A counter bill on A5: prices include GST, the tax is broken out at the foot."""
    s = v["vendor"]
    rows = "".join(f"<tr><td>{n}</td><td>{E(ln['description'])}<br><span class='m'>HSN {ln['hsn']}</span></td><td class='r'>{qty_text(ln['qty'])} {ln['uom']}</td>"
                   f"<td class='r'>{inr(ln['rate'])}</td><td class='r'>{inr(ln['amount'])}</td></tr>" for n, ln in enumerate(v["lines"], 1))
    half = v["gst"] / 2
    body = f"""<div class='c'><div style='font-size:14pt;font-weight:600'>{E(s['name'])}</div>{E(', '.join(s['address']))}<br>GSTIN: {s['gstin']} &nbsp; Ph: {s['phone']}</div>
<div class='c' style='margin:6px 0;font-weight:600;border-top:1px dashed #000;border-bottom:1px dashed #000;padding:3px'>GST INVOICE (CREDIT)</div>
<table><tr><td>Bill No: <b>{v['number']}</b></td><td class='r'>Date: <b>{fmt(v['date'], v['date_style'])}</b></td></tr>
<tr><td colspan='2'>Customer: <b>{E(BUYER['name'])}</b>, {BUYER['address'][1]}</td></tr><tr><td>Customer GSTIN: {BUYER['gstin']}</td><td class='r'>PO Ref: {v['po'] or '-'}</td></tr></table>
<table class='it' style='margin-top:6px'><tr><th>#</th><th>Item</th><th class='r'>Qty</th><th class='r'>MRP</th><th class='r'>Amount</th></tr>{rows}</table>
<div class='m'>All prices are inclusive of GST @ {v['gst']:g}%.</div>
<table style='margin-top:6px'><tr><td style='width:45%'></td><td><table>
<tr><td>Taxable Value</td><td class='r'>{inr(v['taxable'])}</td></tr><tr><td>CGST @ {half:g}%</td><td class='r'>{inr(v['cgst'])}</td></tr>
<tr><td>SGST @ {half:g}%</td><td class='r'>{inr(v['sgst'])}</td></tr>
<tr style='font-weight:600;border-top:1px solid #000'><td>Net Amount Payable</td><td class='r'>&#8377; {inr(v['total'])}</td></tr></table></td></tr></table>
<div style='margin-top:4px'>{words(v['total'], 'Rupees')}</div>
<div class='m' style='margin-top:8px'>Bank: {s['bank_name']}, A/c {s['bank_account']}, IFSC {s['ifsc']}</div>
<div class='r' style='margin-top:22px'>For {E(s['name'])}<br><br>Signature</div>"""
    css = ("@page{size:A5;margin:9mm} .c{text-align:center} .r{text-align:right} .m{font-size:7pt;color:#444}"
           ".it th{border-bottom:1px solid #000;border-top:1px solid #000;text-align:left;padding:3px} .it td{padding:3px;border-bottom:1px dotted #999}")
    return page(body, v["font"], "8.5pt", css)


def usd_html(v: dict) -> str:
    """An invoice from an overseas supplier, in US dollars. No GST, no GSTIN."""
    rows = "".join(f"<tr><td>{E(ln['description'])}</td><td class='r'>{qty_text(ln['qty'])}</td><td class='r'>${ln['rate']:,.2f}</td><td class='r'>${ln['amount']:,.2f}</td></tr>"
                   for ln in v["lines"])
    body = f"""<table><tr><td><div style='font-size:20pt;font-weight:bold'>INVOICE</div><br><b>Lakeshore Motion Systems, Inc.</b><br>2100 Harbor Drive, Suite 300<br>Milwaukee, WI 53204, USA</td>
<td class='r'>Invoice #: <b>{v['number']}</b><br>Date: <b>{v['date'].strftime('%B %d, %Y')}</b><br>Customer PO: <b>{v['po'] or '-'}</b><br>Terms: Net 45</td></tr></table>
<p><b>Bill To:</b><br>{E(BUYER['name'])}<br>{'<br>'.join(BUYER['address'])}, India</p>
<table class='it'><tr><th>Description</th><th class='r'>Qty</th><th class='r'>Unit Price (USD)</th><th class='r'>Amount (USD)</th></tr>{rows}
<tr><td colspan='3' class='r'>Subtotal</td><td class='r'>${v['taxable']:,.2f}</td></tr><tr><td colspan='3' class='r'><b>Total Due (USD)</b></td><td class='r'><b>${v['total']:,.2f}</b></td></tr></table>
<p>Please remit by wire transfer in US dollars. Bank charges are for the account of the remitter.</p>"""
    css = ".r{text-align:right} .it th{border-bottom:2px solid #000;text-align:left;padding:5px} .it td{padding:5px;border-bottom:1px solid #ccc}"
    return page(body, v["font"], "10pt", css)


def lump_html(v: dict) -> str:
    """One line, no quantities: 'supply and installation as per your order'."""
    s, ln = v["vendor"], v["lines"][0]
    tax_rows = (f"<tr><td>CGST @ {v['gst'] / 2:g}%</td><td class='r'>{inr(v['cgst'])}</td></tr><tr><td>SGST @ {v['gst'] / 2:g}%</td><td class='r'>{inr(v['sgst'])}</td></tr>"
                if v["intra"] else f"<tr><td>IGST @ {v['gst']:g}%</td><td class='r'>{inr(v['igst'])}</td></tr>")
    body = f"""<div style='font-size:14pt;font-weight:bold'>{E(s['name'])}</div>{E(', '.join(s['address']))}<br>GSTIN: {s['gstin']}
<h3 style='text-align:center;letter-spacing:2px'>TAX INVOICE</h3>
<table><tr><td>Invoice No. <b>{v['number']}</b></td><td class='r'>Invoice Date <b>{fmt(v['date'], v['date_style'])}</b></td></tr>
<tr><td>Purchase Order No. <b>{v['po']}</b></td><td class='r'>PO Date <b>{fmt(v['po_date'], v['date_style'])}</b></td></tr></table>
<p>To<br><b>{E(BUYER['name'])}</b><br>{'<br>'.join(BUYER['address'])}<br>GSTIN: {BUYER['gstin']}</p>
<table class='it'><tr><th>Particulars</th><th>SAC/HSN</th><th class='r'>Amount (Rs.)</th></tr>
<tr><td>{E(ln['description'])}</td><td>{ln['hsn']}</td><td class='r'>{inr(ln['amount'])}</td></tr></table>
<table style='width:45%;margin-left:55%;margin-top:6px'><tr><td>Taxable Value</td><td class='r'>{inr(v['taxable'])}</td></tr>{tax_rows}
<tr style='font-weight:bold'><td>Invoice Total</td><td class='r'>{inr(v['total'])}</td></tr></table>
<p>{words(v['total'], 'Rupees')}</p><p>Bank: {s['bank_name']}, Account No. {s['bank_account']}, IFSC {s['ifsc']}</p>
<p class='r'>For {E(s['name'])}<br><br>Authorised Signatory</p>"""
    css = ".r{text-align:right} .it th,.it td{border:1px solid #000;padding:6px;text-align:left}"
    return page(body, v["font"], "10pt", css)


def chrome_pdf(html: str, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "doc.html"
        src.write_text(html, encoding="utf-8")
        subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={out}", str(src)], check=True, capture_output=True, timeout=120)


def wkhtml_pdf(html: str, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "doc.html"
        src.write_text(html, encoding="utf-8")
        subprocess.run(["wkhtmltopdf", "--quiet", "--page-size", "A4", "--margin-top", "10mm", "--margin-bottom", "10mm",
                        "--margin-left", "10mm", "--margin-right", "10mm", str(src), str(out)], check=True, capture_output=True, timeout=120)


def office_pdf(src: Path, out: Path) -> None:
    subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(src.parent), str(src)],
                   check=True, capture_output=True, timeout=180)
    shutil.move(str(src.with_suffix(".pdf")), str(out))


# --------------------------------------------------------------------------- a bill kept in Excel

def excel_pdf(v: dict, out: Path) -> None:
    """Rate before quantity, western digit grouping (Excel's default number format), merged cells."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    s = v["vendor"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice"
    for col, width in zip("ABCDEFG", (6, 44, 12, 12, 9, 8, 15)):
        ws.column_dimensions[col].width = width
    font = v["font"]
    thin = Side(style="thin")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)

    def put(cell, value, bold=False, size=10, align=None, border=False, fmt_=None):
        c = ws[cell]
        c.value = value
        c.font = Font(name=font, bold=bold, size=size)
        if align:
            c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=True)
        if border:
            c.border = box
        if fmt_:
            c.number_format = fmt_
        return c
    ws.merge_cells("A1:G1"); put("A1", "TAX INVOICE", True, 14, "center")
    ws.merge_cells("A2:G2"); put("A2", s["name"], True, 12, "center")
    ws.merge_cells("A3:G3"); put("A3", ", ".join(s["address"]), align="center")
    ws.merge_cells("A4:G4"); put("A4", f"GSTIN: {s['gstin']}    Phone: {s['phone']}", align="center")
    put("A6", "Bill No:", True); ws.merge_cells("B6:C6"); put("B6", v["number"])
    put("E6", "Bill Date:", True); ws.merge_cells("F6:G6"); put("F6", fmt(v["date"], v["date_style"]))
    put("A7", "P.O. No:", True); ws.merge_cells("B7:C7"); put("B7", v["po"] or "")
    put("E7", "P.O. Date:", True); ws.merge_cells("F7:G7"); put("F7", fmt(v["po_date"], v["date_style"]) if v["po"] else "")
    put("A9", "To,"); ws.merge_cells("A10:D10"); put("A10", BUYER["name"], True)
    ws.merge_cells("A11:D11"); put("A11", ", ".join(BUYER["address"]))
    ws.merge_cells("A12:D12"); put("A12", f"GSTIN: {BUYER['gstin']}")
    row = 14
    for col, head in zip("ABCDEFG", ("S.No", "Particulars", "HSN Code", "Rate", "Qty", "Unit", "Amount")):
        put(f"{col}{row}", head, True, align="center", border=True)
    for n, ln in enumerate(v["lines"], 1):
        row += 1
        put(f"A{row}", n, align="center", border=True); put(f"B{row}", ln["description"], border=True)
        put(f"C{row}", ln["hsn"], align="center", border=True); put(f"D{row}", float(ln["rate"]), border=True, fmt_="#,##0.00")
        put(f"E{row}", float(ln["qty"]), align="center", border=True, fmt_="0.##"); put(f"F{row}", ln["uom"], align="center", border=True)
        put(f"G{row}", float(ln["amount"]), border=True, fmt_="#,##0.00")
    half = v["gst"] / 2
    sums = [("Total", v["taxable"])] + ([(f"CGST @ {half:g}%", v["cgst"]), (f"SGST @ {half:g}%", v["sgst"])] if v["intra"]
                                        else [(f"IGST @ {v['gst']:g}%", v["igst"])])
    if v["rounding"]:
        sums.append(("Round Off", v["rounding"]))
    sums.append(("G. Total", v["total"]))
    for label, value in sums:
        row += 1
        ws.merge_cells(f"A{row}:F{row}")
        put(f"A{row}", label, label in ("Total", "G. Total"), align="right", border=True)
        for col in "BCDEF":
            ws[f"{col}{row}"].border = box
        put(f"G{row}", float(value), label in ("Total", "G. Total"), border=True, fmt_="#,##0.00")
    row += 2
    ws.merge_cells(f"A{row}:G{row}"); put(f"A{row}", "Rupees in words: " + words(v["total"], "Rupees").replace("Rupees ", "", 1))
    row += 2
    ws.merge_cells(f"A{row}:D{row}"); put(f"A{row}", f"Bank: {s['bank_name']}, A/c No: {s['bank_account']}, IFSC: {s['ifsc']}")
    ws.merge_cells(f"E{row}:G{row}"); put(f"E{row}", f"For {s['name']}", True, align="right")
    ws.merge_cells(f"E{row + 3}:G{row + 3}"); put(f"E{row + 3}", "Auth. Signatory", align="right")
    ws.merge_cells(f"A{row + 5}:G{row + 5}"); put(f"A{row + 5}", NOTE, size=7, align="center")
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "invoice.xlsx"
        wb.save(src)
        office_pdf(src, out)


# --------------------------------------------------------------------------- an invoice typed in Word

def word_pdf(v: dict, out: Path) -> None:
    """Letterhead paragraphs, header facts inside sentences, ordinal dates, 'Rs.' amounts."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.shared import Inches, Pt
    s = v["vendor"]
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name, style.font.size = v["font"], Pt(10.5)

    def para(text, bold=False, size=None, align=None, underline=False):
        p = doc.add_paragraph()
        run = p.add_run(text)
        run.bold, run.underline = bold, underline
        if size:
            run.font.size = Pt(size)
        if align == "center":
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if align == "right":
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p.paragraph_format.space_after = Pt(2)
        return p
    para(s["name"], True, 16, "center")
    para(", ".join(s["address"]) + f"  |  Tel: {s['phone']}", align="center")
    para(f"GSTIN: {s['gstin']}    PAN: {s['pan']}", align="center")
    para("TAX INVOICE", True, 12, "center", underline=True)
    head = para(f"Invoice No: {v['number']}\tDate: {fmt(v['date'], v['date_style'])}")
    head.paragraph_format.tab_stops.add_tab_stop(Inches(6.0), WD_TAB_ALIGNMENT.RIGHT)
    if v["po"]:
        para(f"Your Order Ref: {v['po']} dated {fmt(v['po_date'], v['date_style'])}")
    para(f"To: M/s {BUYER['name']}, {', '.join(BUYER['address'])}. GSTIN {BUYER['gstin']}")
    table = doc.add_table(rows=1, cols=6)
    table.style = "Table Grid"
    for cell, head in zip(table.rows[0].cells, ("Sr.", "Description", "HSN", "Qty", "Unit Rate (Rs.)", "Total (Rs.)")):
        cell.text = head
        cell.paragraphs[0].runs[0].bold = True
    for n, ln in enumerate(v["lines"], 1):
        cells = table.add_row().cells
        for cell, text in zip(cells, (str(n), ln["description"], ln["hsn"], f"{qty_text(ln['qty'])} {ln['uom']}", inr(ln["rate"]), inr(ln["amount"]))):
            cell.text = text
        for cell in cells[4:]:
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
    table.autofit = False
    widths = (0.45, 2.55, 0.85, 0.8, 1.1, 1.15)      # whoever typed it widened the description column
    for column, width in zip(table.columns, widths):
        column.width = Inches(width)
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.width = Inches(width)
    half = v["gst"] / 2
    para("")
    para(f"Sub-Total: Rs. {inr(v['taxable'])}", align="right")
    if v["intra"]:
        para(f"Add: CGST @ {half:g}%: Rs. {inr(v['cgst'])}", align="right")
        para(f"Add: SGST @ {half:g}%: Rs. {inr(v['sgst'])}", align="right")
    else:
        para(f"Add: IGST @ {v['gst']:g}%: Rs. {inr(v['igst'])}", align="right")
    para(f"Invoice Total: Rs. {inr(v['total'])}", True, align="right")
    para(f"({words(v['total'], 'Rupees')})")
    para(f"Payment by NEFT to A/c {s['bank_account']}, {s['bank_name']}, IFSC {s['ifsc']}, within 30 days.")
    para(f"For {s['name']}", True, align="right")
    para("\nAuthorised Signatory", align="right")
    para(NOTE, size=7, align="center")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "invoice.docx"
        doc.save(src)
        office_pdf(src, out)


# --------------------------------------------------------------------------- an invoice set in LaTeX

def latex_pdf(v: dict, out: Path) -> None:
    """A consultant's invoice: serif type with ligatures, a ruled table, 'Total due'."""
    s = v["vendor"]
    tex = lambda text: str(text).replace("&", r"\&").replace("%", r"\%").replace("#", r"\#").replace("_", r"\_")   # noqa: E731
    rows = "\n".join(rf"{tex(ln['description'])} & {ln['hsn']} & {qty_text(ln['qty'])} {tex(ln['uom'])} & {inr(ln['rate'])} & {inr(ln['amount'])} \\"
                     for ln in v["lines"])
    tax = (rf"CGST ({v['gst'] / 2:g}\%) & {inr(v['cgst'])} \\ SGST ({v['gst'] / 2:g}\%) & {inr(v['sgst'])} \\" if v["intra"]
           else rf"IGST ({v['gst']:g}\%) & {inr(v['igst'])} \\")
    po = rf"Purchase order: & {tex(v['po'])} \\" if v["po"] else ""
    source = rf"""\documentclass[11pt,a4paper]{{article}}
\usepackage[margin=22mm]{{geometry}}\usepackage{{booktabs,tabularx,array}}\pagestyle{{empty}}\setlength{{\parindent}}{{0pt}}
\begin{{document}}
{{\Huge\bfseries INVOICE}}\hfill\begin{{tabular}}[t]{{r@{{\ }}l}}Invoice number: & \textbf{{{tex(v['number'])}}} \\ Date of issue: & {fmt(v['date'], v['date_style'])} \\ {po}\end{{tabular}}

\vspace{{8mm}}
\begin{{tabularx}}{{\textwidth}}{{@{{}}X X@{{}}}}
\textbf{{From}}\newline {tex(s['name'])}\newline {tex(s['address'][0])}\newline {tex(s['address'][1])}\newline GSTIN: {s['gstin']}
& \textbf{{Bill to}}\newline {tex(BUYER['name'])}\newline {tex(BUYER['address'][0])}\newline {tex(BUYER['address'][1])}\newline GSTIN: {BUYER['gstin']}
\end{{tabularx}}

\vspace{{8mm}}
\begin{{tabularx}}{{\textwidth}}{{@{{}}X c r r r@{{}}}}
\toprule Description & SAC & Quantity & Rate & Amount \\ \midrule
{rows}
\bottomrule
\end{{tabularx}}

\vspace{{4mm}}\hfill\begin{{tabular}}{{l r}}
Subtotal & {inr(v['taxable'])} \\ {tax}
\midrule \textbf{{Total due (INR)}} & \textbf{{{inr(v['total'])}}} \\ \bottomrule
\end{{tabular}}

\vspace{{6mm}}
Amount in words: {words(v['total'], 'Rupees')}.

\vspace{{3mm}}
Payment within 30 days to {tex(s['bank_name'])}, account {s['bank_account']}, IFSC {s['ifsc']}.

\vfill {{\footnotesize\itshape {NOTE}}}
\end{{document}}"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "invoice.tex").write_text(source, encoding="utf-8")
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "invoice.tex"], cwd=tmp, check=True,
                       capture_output=True, timeout=180)
        shutil.move(str(Path(tmp) / "invoice.pdf"), str(out))


# --------------------------------------------------------------------------- photographs of paper

def photograph(pdf: Path, out: Path, seed: int, tilt: float = 2.0, warp: float = 0.012, light: float = 0.18, blur: float = 0.8,
               noise: float = 5.0, quality: int = 55, dpi: int = 170, quarter_turn: bool = False, fax: bool = False,
               desk: bool = True) -> None:
    """Turn a text PDF into what a phone camera (or a fax) makes of the printed page: no
    text layer, perspective, tilt, uneven light, soft focus, sensor noise, JPEG artefacts."""
    import cv2
    import img2pdf
    import numpy as np
    rng = np.random.default_rng(seed)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", "1", str(pdf), str(Path(tmp) / "p")], check=True, capture_output=True)
        image = cv2.imread(str(next(Path(tmp).glob("p*.png"))), cv2.IMREAD_COLOR)
        h, w = image.shape[:2]
        if fax:      # a fax: low resolution, pure black and white, a little skew, specks
            small = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (int(w * 0.62), int(h * 0.62)), interpolation=cv2.INTER_AREA)
            m = cv2.getRotationMatrix2D((small.shape[1] / 2, small.shape[0] / 2), tilt, 1.0)
            small = cv2.warpAffine(small, m, (small.shape[1], small.shape[0]), borderValue=255)
            _, bw = cv2.threshold(small, 165, 255, cv2.THRESH_BINARY)
            specks = rng.random(bw.shape) < 0.0015
            bw[specks] = 0
            result = cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)
            quality = 70
        else:
            pad = int(0.05 * w) if desk else 0
            canvas = np.full((h + 2 * pad, w + 2 * pad, 3), (58, 66, 74), np.uint8) if desk else image.copy()   # a dark desk around the sheet
            if desk:
                canvas[pad:pad + h, pad:pad + w] = image
            H, W = canvas.shape[:2]
            jitter = lambda: rng.uniform(-warp, warp, 2) * (W, H)       # noqa: E731
            src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
            dst = np.float32([src[0] + abs(jitter()), src[1] + (-abs(jitter()[0]), abs(jitter()[1])),
                              src[2] - abs(jitter()), src[3] + (abs(jitter()[0]), -abs(jitter()[1]))])
            warped = cv2.warpPerspective(canvas, cv2.getPerspectiveTransform(src, dst), (W, H), borderValue=(58, 66, 74))
            m = cv2.getRotationMatrix2D((W / 2, H / 2), tilt, 1.0)
            warped = cv2.warpAffine(warped, m, (W, H), borderValue=(58, 66, 74))
            yy, xx = np.mgrid[0:H, 0:W]
            shade = 1.0 - light * ((xx / W) * rng.uniform(0.4, 1.0) + (yy / H) * rng.uniform(0.2, 0.8))      # light falls off across the page
            warped = np.clip(warped.astype(np.float32) * shade[..., None] * np.array([0.97, 0.99, 1.0]), 0, 255)
            warped = cv2.GaussianBlur(warped, (0, 0), blur)
            warped = np.clip(warped + rng.normal(0, noise, warped.shape), 0, 255).astype(np.uint8)
            result = cv2.rotate(warped, cv2.ROTATE_90_CLOCKWISE) if quarter_turn else warped
        ok, jpeg = cv2.imencode(".jpg", result, [cv2.IMWRITE_JPEG_QUALITY, quality])
        hh, ww = result.shape[:2]
        layout = img2pdf.get_layout_fun((img2pdf.mm_to_pt(210 if ww < hh else 297), img2pdf.mm_to_pt(297 if ww < hh else 210)))
        out.write_bytes(img2pdf.convert(jpeg.tobytes(), layout_fun=layout))


# --------------------------------------------------------------------------- the set

def documents() -> list[dict]:
    T, H = "tune", "holdout"
    docs = [
        # ---- Tally-style grid, Chrome, Liberation Sans (tune)
        make("tally_01", "tally", "chrome", "Liberation Sans", T, "R01", "KBT/26-27/1184", 12, "PO-2026-0501", [(0, 40), (1, 6), (2, 24)],
             quirks=["label stacked over value", "tax ledgers as rows in the item grid", "HSN summary table", "two 'Dated' boxes", "quantity printed with its unit", "2-digit year", "rupee sign from a fallback font"],
             date_style="%d-%b-%y"),
        make("tally_02", "tally", "chrome", "Liberation Sans", T, "R02", "SSI/2026/0457", 9, "PO-2026-0502", [(4, 36), (5, 8)],
             quirks=["IGST", "tax ledgers as rows in the item grid", "HSN summary table"], date_style="%d-%b-%y"),
        make("tally_03", "tally", "chrome", "DejaVu Sans", T, "R08", "SLC/1097/26-27", 14, "PO-2026-0503", [(10, 2), (11, 5)],
             quirks=["round off line", "HSN summary table", "8-digit HSN"], round_off=True, gst=18, date_style="%d-%b-%y"),
        make("tally_04", "tally", "chrome", "Liberation Sans", T, "R04", "NF/26-27/3321", 6, "PO-2026-0504", [(8, 1500), (9, 3000), (3, 4), (15, 6)],
             quirks=["four lines", "small unit prices", "HSN summary with several rows"], date_style="%d-%b-%y"),
        make("tally_05", "tally", "chrome", "Liberation Sans", T, "R01", "KBT/26-27/1201", 17, "PO-2026-0505", [(0, 60), (1, 10)],
             quirks=["price 3% above the PO"], expect="HOLD", reason="PRICE_VARIANCE", date_style="%d-%b-%y",
             po_override={"price_factor": "0.97"}),
        make("tally_credit", "tally", "chrome", "Liberation Sans", T, "R01", "KBT/CN/26-27/019", 18, "PO-2026-0501", [(0, 4)],
             quirks=["credit note"], title="Credit Note", expect="HOLD", reason="UNSUPPORTED_DOCUMENT", date_style="%d-%b-%y"),
        make("tally_06", "tally", "chrome", "Liberation Sans", T, "R04", "NF/26-27/3329", 9, "PO-2026-0517", [(8, 800), (8, 400), (9, 1200)],
             quirks=["the same item billed on two lines (two deliveries)", "PO has it on one line"], date_style="%d-%b-%y"),
        # ---- Zoho-style, Chrome, Inter (tune)
        make("zoho_01", "zoho", "chrome", "Inter", T, "R03", "INV-000482", 11, "PO-2026-0506", [(7, 850), (13, 400)],
             quirks=["per-line tax columns", "white text on a dark header", "'Balance Due' repeats the total", "label 'Invoice#' is only '#'", "dd/mm/yyyy"],
             date_style="%d/%m/%Y"),
        make("zoho_02", "zoho", "chrome", "Inter", T, "R08", "INV-001204", 13, "PO-2026-0507", [(11, 12), (10, 1)],
             quirks=["CGST and SGST columns per line", "quantity and unit on two lines"], date_style="%d/%m/%Y"),
        make("zoho_03", "zoho", "chrome", "Poppins", T, "R05", "SCA-INV-0931", 10, "PO-2026-0508", [(12, 25), (15, 10), (5, 4)],
             quirks=["per-line tax columns", "three lines"], date_style="%d %b %Y"),
        make("zoho_04", "zoho", "chrome", "Inter", T, "R03", "INV-000497", 16, "PO-2026-0509", [(7, 1200)],
             quirks=["bills more than the PO has"], expect="HOLD", reason="QTY_OVER_PO", date_style="%d/%m/%Y",
             po_override={"qty_factor": "0.75"}),
        make("zoho_proforma", "zoho", "chrome", "Inter", T, "R05", "PI-0214", 8, "PO-2026-0508", [(12, 25)],
             quirks=["proforma invoice"], title="Proforma Invoice", expect="REJECT", reason="NOT_A_TAX_INVOICE", date_style="%d/%m/%Y"),
        make("zoho_05", "zoho", "chrome", "Inter", T, "R03", "INV-000503", 17, "PO-2026-0518", [(7, 500)],
             quirks=["bank account is not the one in the vendor master"], expect="HOLD", reason="BANK_DETAILS_CHANGED",
             printed_account="118800771209", date_style="%d/%m/%Y"),
        make("zoho_long", "zoho", "chrome", "Inter", T, "R08", "INV-001219", 15, "PO-2026-0519",
             [((i * 5) % 16, 2 + (i * 3) % 17) for i in range(16)],
             quirks=["16 lines over two pages", "table header repeated on the second page", "totals on the last page"], date_style="%d/%m/%Y"),
        make("zoho_06", "zoho", "chrome", "Inter", T, "R10", "INV-000077", 9, "PO-2026-0540", [(13, 600), (16, 40)],
             quirks=["GST at 5%: 'CGST2.5 (2.5%)'", "per-line tax columns"], gst=5, date_style="%d/%m/%Y"),
        make("tally_hindi", "tally", "chrome", "Liberation Sans", T, "R02", "SSI/2026/0466", 13, "PO-2026-0545", [(5, 6), (4, 24)],
             quirks=["Hindi invocation line on top", "labels in English and Hindi", "Devanagari from a fallback font"], bilingual=True,
             date_style="%d-%b-%y"),
        # ---- a bill kept in Excel, LibreOffice Calc, Carlito (tune)
        make("excel_01", "excel", "libreoffice calc", "Carlito", T, "R10", "HS/564", 12, "PO-2026-0510", [(16, 60), (17, 20)],
             quirks=["rate column before quantity", "western digit grouping", "merged cells", "'Bill No' and 'G. Total'", "dd.mm.yyyy"],
             date_style="%d.%m.%Y"),
        make("excel_02", "excel", "libreoffice calc", "Carlito", T, "R06", "TPP/2026-27/208", 7, "PO-2026-0511", [(13, 2400)],
             quirks=["rate column before quantity", "western digit grouping", "IGST at 5%", "round off"], round_off=True, gst=5,
             date_style="%d.%m.%Y"),
        make("excel_03", "excel", "libreoffice calc", "Liberation Sans", T, "R10", "HS/571", 15, "PO-2026-0512", [(16, 25)],
             quirks=["vendor not in the master"], expect="HOLD", reason="VENDOR_NOT_APPROVED", register_vendor=False, date_style="%d.%m.%Y"),
        make("excel_04", "excel", "libreoffice calc", "Carlito", T, "R06", "TPP/2026-27/211", 10, "PO-2026-0520", [(13, 1500), (14, 30)],
             quirks=["grand total typed by hand and wrong by 1,000"], expect="HOLD", reason="ARITHMETIC_MISMATCH", total_typo="1000",
             date_style="%d.%m.%Y"),
        # ---- retail bill with tax-inclusive prices, Chrome, Poppins, A5 (tune)
        make("retail_01", "retail", "chrome", "Poppins", T, "R10", "CR-4471", 13, "PO-2026-0513", [(16, 40), (17, 12)],
             quirks=["prices include GST", "A5 page", "'Net Amount Payable'", "HSN under the description"], tax_inclusive=True,
             date_style="%d-%m-%Y"),
        make("retail_02", "retail", "chrome", "Poppins", T, "R10", "CR-4490", 16, "PO-2026-0529", [(17, 30), (16, 15), (14, 6)],
             quirks=["prices include GST", "A5 page", "three lines"], tax_inclusive=True, date_style="%d-%m-%Y"),
        # ---- lump sum against an itemised PO (tune)
        make("lump_01", "lump", "chrome", "Liberation Serif", T, "R05", "SCA/26-27/0458", 14, "PO-2026-0514", [(12, 30), (15, 12)],
             quirks=["one bundled line, no quantities", "equals what is open on the PO"],
             lump=("Supply and commissioning of sensors and valves as per your purchase order", "9987", "59010.00")),
        make("lump_02", "lump", "chrome", "Liberation Serif", T, "R05", "SCA/26-27/0461", 16, "PO-2026-0515", [(12, 30), (15, 12)],
             quirks=["one bundled line", "covers only part of the PO"], expect="HOLD", reason="BUNDLED_PARTIAL",
             lump=("Part supply against your purchase order", "9987", "30000.00")),
        # ---- an overseas invoice in dollars (tune)
        make("usd_01", "usd", "chrome", "Liberation Sans", T, "R02", "LMS-88213", 10, "PO-2026-0516", [(3, 6), (12, 10)],
             quirks=["US dollars", "no GSTIN", "month-first date"], currency="USD", expect="HOLD", reason="CURRENCY_NOT_SUPPORTED",
             register_vendor=False),
        # ---- Busy-style bill, wkhtmltopdf, DejaVu Sans (holdout)
        make("busy_01", "busy", "wkhtmltopdf", "DejaVu Sans", H, "R07", "VC/2026-27/1021", 12, "PO-2026-0521", [(6, 8), (4, 20)],
             quirks=["quantities to three decimals", "'Add : IGST @ 18.00 %'", "order number and its date in one cell", "tax summary under the items"],
             date_style="%d-%m-%Y"),
        make("busy_02", "busy", "wkhtmltopdf", "DejaVu Sans", H, "R10", "HS/26-27/0588", 10, "PO-2026-0522", [(13, 900), (16, 12)],
             quirks=["CGST and SGST at 2.50 % added below the total", "rounded off"], round_off=True, gst=5, date_style="%d-%m-%Y"),
        make("busy_hindi", "busy", "wkhtmltopdf", "DejaVu Sans", H, "R02", "SSI/2026/0471", 14, "PO-2026-0539", [(4, 18), (5, 3)],
             quirks=["Hindi invocation line on top", "labels in Hindi and English"], bilingual=True, date_style="%d-%m-%Y"),
        make("busy_03", "busy", "wkhtmltopdf", "Liberation Mono", H, "R04", "NF/26-27/3340", 15, "PO-2026-0523", [(8, 2000), (9, 2000)],
             quirks=["monospaced type", "no PO quoted"], expect="HOLD", reason="PO_INFERRED", quote_po=False, date_style="%d-%m-%Y"),
        make("busy_04", "busy", "wkhtmltopdf", "DejaVu Sans", H, "R07", "VC/2026-27/1034", 16, "PO-2026-0530", [(6, 4)],
             quirks=["one digit of the vendor GSTIN is typed wrong"], expect="HOLD", reason="GSTIN_INVALID", gstin_typo=True,
             date_style="%d-%m-%Y"),
        # ---- typed in Word, LibreOffice Writer (holdout)
        make("word_01", "word", "libreoffice writer", "Caladea", H, "R06", "TPP/2026-27/214", 11, "PO-2026-0524", [(13, 1800), (14, 40)],
             quirks=["header facts inside sentences", "ordinal dates ('11th November 2026')", "'Rs.' before every amount", "quantity and unit in one cell", "US Letter page"],
             date_style="ordinal"),
        make("word_02", "word", "libreoffice writer", "Liberation Serif", H, "R08", "SLC/1111/26-27", 9, "PO-2026-0525", [(11, 6), (10, 1)],
             quirks=["CGST and SGST as right-aligned sentences", "ordinal dates", "US Letter page"], date_style="ordinal"),
        make("word_03", "word", "libreoffice writer", "Caladea", H, "R06", "TPP/2026-27/222", 17, "PO-2026-0537", [(13, 600), (14, 10)],
             quirks=["invoice total typed wrong by 900"], expect="HOLD", reason="ARITHMETIC_MISMATCH", total_typo="900", date_style="ordinal"),
        # ---- set in LaTeX (holdout)
        make("latex_01", "latex", "pdflatex", "Computer Modern", H, "R09", "IPC-2026-041", 13, "PO-2026-0526", [(0, 4), (1, 1)],
             quirks=["ligatures in the text layer", "ruled table without vertical lines", "'Total due (INR)'", "services with SAC codes"],
             date_style="%d %B %Y"),
        make("latex_02", "latex", "pdflatex", "Computer Modern", H, "R09", "IPC-2026-044", 17, "PO-2026-0527", [(0, 2), (2, 3)],
             quirks=["ligatures", "three-word invoice label ('Invoice number:')"], date_style="%d %B %Y"),
        make("latex_03", "latex", "pdflatex", "Computer Modern", H, "R09", "IPC-2026-049", 18, "PO-2026-0538", [(2, 2)],
             quirks=["bank account is not the one in the vendor master"], expect="HOLD", reason="BANK_DETAILS_CHANGED",
             printed_account="270300990012", date_style="%d %B %Y"),
        # ---- a long invoice over several pages (holdout)
        make("long_01", "tally", "chrome", "Liberation Sans", H, "R04", "NF/26-27/3352", 16, "PO-2026-0528",
             [((i * 3) % 16, 1 + (i * 7) % 9) for i in range(34) if (i * 3) % 16 not in (6, 10, 11)],
             quirks=["28 lines over two pages", "table header repeated on each page", "the same item on several lines"], date_style="%d-%b-%y"),
    ]
    # ---- photographs of printed pages. Each has its own invoice, so no two documents share a number.
    shots = [
        ("photo_tally_01", "tally", T, "R01", "KBT/26-27/1190", 13, "PO-2026-0531", [(0, 25), (2, 40)], dict(tilt=1.8, seed=3), "%d-%b-%y", ["phone photo", "mild perspective"]),
        ("photo_tally_02", "tally", T, "R02", "SSI/2026/0463", 11, "PO-2026-0532", [(4, 12), (5, 5)], dict(tilt=-3.2, warp=0.02, light=0.28, seed=5), "%d-%b-%y", ["phone photo", "stronger tilt and shadow"]),
        ("photo_zoho_01", "zoho", T, "R03", "INV-000489", 12, "PO-2026-0533", [(7, 600), (13, 250)], dict(tilt=1.2, seed=7), "%d/%m/%Y", ["phone photo", "dark table header"]),
        ("photo_excel_01", "excel", T, "R10", "HS/569", 14, "PO-2026-0534", [(16, 30), (17, 15)], dict(tilt=-1.5, seed=9), "%d.%m.%Y", ["phone photo of a spreadsheet print"]),
        ("photo_sideways", "tally", T, "R01", "KBT/26-27/1195", 15, "PO-2026-0535", [(1, 8)], dict(tilt=0.8, seed=11, quarter_turn=True), "%d-%b-%y", ["page photographed on its side"]),
        ("photo_hand_total", "tally", T, "R08", "SLC/1101/26-27", 12, "PO-2026-0536", [(11, 4), (2, 10)], dict(tilt=1.0, seed=13), "%d-%b-%y", ["total written by hand"]),
        ("photo_busy_01", "busy", H, "R07", "VC/2026-27/1029", 14, "PO-2026-0541", [(6, 5), (4, 30)], dict(tilt=2.4, seed=15), "%d-%m-%Y", ["phone photo"]),
        ("photo_busy_fax", "busy", H, "R08", "SLC/1108/26-27", 13, "PO-2026-0542", [(10, 1), (2, 20)], dict(tilt=0.7, seed=17, fax=True), "%d-%m-%Y", ["fax: low resolution, black and white"]),
        ("photo_word_01", "word", H, "R06", "TPP/2026-27/219", 15, "PO-2026-0543", [(13, 900), (14, 24)], dict(tilt=-2.0, seed=19), "ordinal", ["phone photo of a typed letterhead"]),
        ("photo_latex_01", "latex", H, "R09", "IPC-2026-047", 18, "PO-2026-0544", [(0, 3), (1, 1)], dict(tilt=1.6, seed=21), "%d %B %Y", ["phone photo", "serif type"]),
    ]
    problems = {     # photographed invoices that are read correctly should still be stopped for these
        "photo_tally_over": dict(expect="HOLD", reason="QTY_OVER_PO", po_override={"qty_factor": "0.75"}),
        "photo_busy_price": dict(expect="HOLD", reason="PRICE_VARIANCE", po_override={"price_factor": "0.97"}),
    }
    shots += [
        ("photo_tally_over", "tally", T, "R04", "NF/26-27/3335", 12, "PO-2026-0546", [(8, 1200), (15, 8)], dict(tilt=1.4, seed=23), "%d-%b-%y", ["phone photo", "bills more than the PO has"]),
        ("photo_busy_price", "busy", H, "R07", "VC/2026-27/1040", 17, "PO-2026-0547", [(6, 10), (4, 16)], dict(tilt=-1.7, seed=25), "%d-%m-%Y", ["phone photo", "price 3% above the PO"]),
    ]
    for doc_id, family, split, vendor, number, day, po, items, shot, style, quirks in shots:
        tool = {"tally": "chrome", "zoho": "chrome", "excel": "libreoffice calc", "busy": "wkhtmltopdf", "word": "libreoffice writer", "latex": "pdflatex"}[family]
        font = {"tally": "Liberation Sans", "zoho": "Inter", "excel": "Carlito", "busy": "DejaVu Sans", "word": "Caladea", "latex": "Computer Modern"}[family]
        docs.append(make(doc_id, family, tool + ", then photographed", font, split, vendor, number, day, po, items, kind="scan", quirks=quirks,
                         date_style=style, scan=shot, **problems.get(doc_id, {})))
    ids, numbers = [d["id"] for d in docs], [d["number"] for d in docs]
    assert len(set(ids)) == len(ids) and len(set(numbers)) == len(numbers), "ids and invoice numbers must be unique"
    for d in docs:      # a correct invoice above the sign-off ceiling would be held for policy, which is not what this set measures
        assert d["expect"] != "APPROVE" or d["total"] <= D("1000000"), (d["id"], d["total"])
    return docs


def render(v: dict, out: Path) -> None:
    family = v["family"]
    if family == "tally":
        chrome_pdf(tally_html(v, hand_total=v["id"] == "photo_hand_total", long_table=len(v["lines"]) > 20), out)
    elif family == "zoho":
        chrome_pdf(zoho_html(v), out)
    elif family == "retail":
        chrome_pdf(retail_html(v), out)
    elif family == "lump":
        chrome_pdf(lump_html(v), out)
    elif family == "usd":
        chrome_pdf(usd_html(v), out)
    elif family == "busy":
        wkhtml_pdf(busy_html(v), out)
    elif family == "excel":
        excel_pdf(v, out)
    elif family == "word":
        word_pdf(v, out)
    elif family == "latex":
        latex_pdf(v, out)
    else:
        raise ValueError(family)


def truth(v: dict) -> dict:
    """What a perfect reader would report, the PO the document should be matched against, and the right outcome."""
    rate = v["gst"]
    lines = [{"description": ln["description"], "hsn": ln["hsn"], "uom": ln["uom"],            # exactly as printed
              "qty": None if ln["qty"] is None else str(ln["qty"]),
              "unit_price": None if ln["rate"] is None else str(ln["rate"]), "amount": str(ln["amount"])}
             for ln in v["lines"]]
    # The purchase order behind the document, as Procurement would have raised it: one line per
    # item (an invoice may bill the same item on two lines), at pre-tax prices, fully received.
    override = v["po_override"] or {}
    by_item: dict[tuple, Decimal] = {}
    for ln in v["ordered"]:
        key = (ln["description"], ln["hsn"], ln["uom"], ln["rate"])
        by_item[key] = by_item.get(key, D(0)) + ln["qty"]
    po_lines = []
    for (desc, hsn, uom, price), qty in by_item.items():
        if v["tax_inclusive"]:
            price = q2(price / (1 + rate / 100))
        qty = (qty * D(override.get("qty_factor", "1"))).quantize(D("1"))
        po_lines.append({"description": desc, "hsn": hsn, "uom": uom, "qty": str(qty),
                         "unit_price": str(q2(price * D(override.get("price_factor", "1"))))})
    po = {"po_number": v["po_number"], "po_date": v["po_date"].isoformat(), "lines": po_lines} if v["currency"] == "INR" else None
    s = v["vendor"]
    return {"id": v["id"], "file": f"documents/{v['id']}.pdf", "family": v["family"], "tool": v["tool"], "font": v["font"],
            "split": v["split"], "kind": v["kind"], "quirks": v["quirks"], "tax_inclusive": v["tax_inclusive"],
            "vendor": {"vendor_id": v["vendor_id"], "legal_name": s["name"], "gstin": v["master_gstin"], "state_code": s["state_code"],
                       "bank_name": s["bank_name"], "bank_account": v["master_account"], "ifsc": s["ifsc"], "phone": s["phone"],
                       "email": s["email"]} if v["register_vendor"] else None,
            "po": po,
            "truth": {"invoice_number": v["number"], "invoice_date": v["date"].isoformat(), "po_reference": v["po"],
                      "vendor_gstin": s["gstin"] if v["currency"] == "INR" else None, "bank_account": s["bank_account"],
                      "taxable": str(v["taxable"]), "total": str(v["total"]), "currency": v["currency"], "lines": lines},
            "expect": {"verdict": v["expect"], "reason": v["reason"]}}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.pdf"):
        old.unlink()
    records = []
    random.seed(20261122)
    with tempfile.TemporaryDirectory() as tmp:
        for v in documents():
            target = OUT / f"{v['id']}.pdf"
            if v["kind"] == "scan":
                printed = Path(tmp) / f"{v['id']}_print.pdf"
                render(v, printed)
                photograph(printed, target, **v["scan"])
            else:
                render(v, target)
            record = truth(v)
            record["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()       # the recorded OCR output is filed under this
            records.append(record)
            print(f"  {v['id']:<18} {v['split']:<8} {v['tool']:<36} {target.stat().st_size / 1024:7.1f} KB")
    (HERE / "truth.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"{len(records)} documents, {sum(r['kind'] == 'scan' for r in records)} of them photographs; truth.json written")


if __name__ == "__main__":
    main()
