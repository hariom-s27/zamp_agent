"""Generate the after-hold test data (round 3): two vendors registered as micro or small
enterprises, their purchase orders with delivery dates, two invoices and their manifest.

    python -m scripts.generate_case_data            # write data/*_cases.csv and samples/cases/

It reuses the layouts and arithmetic of scripts/generate_test_data.py and adds its two
vendors to that module only while it writes, so nothing about the first 18 samples, the
base vendor master or the PO register changes. Dates are fixed: a re-run is byte-identical.

Ids are chosen outside every range already in use: V001 to V007, R01 to R49,
PO-2026-0099 to 0229, the 0300 to 0999 range of variant_spec, 0501 onward in robustness/.
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from invoice_desk.textutil import make_gstin, q2  # noqa: E402
from scripts import generate_test_data as G  # noqa: E402

CASE_VENDORS = [
    {"vendor_id": "V101", "legal_name": "Kaveri Bearings & Drives Pvt Ltd",
     "aliases": "Kaveri Bearings|Kaveri Bearings and Drives", "pan": "AAECK6152L",
     "state_code": "29", "address": ["No. 63, 2nd Stage, Peenya Industrial Area", "Bengaluru 560058, Karnataka"],
     "bank_name": "Hoysala Mercantile Bank", "bank_branch": "Peenya", "bank_account": "004533118870",
     "ifsc": "HMBL0000453", "status": "APPROVED", "payment_terms_days": 30,
     "phone_on_file": "080-5550-0163", "email": "accounts@kaveribearings.example", "layout": "classic",
     "msme_category": "small", "udyam_number": "UDYAM-KR-03-0041187"},
    {"vendor_id": "V102", "legal_name": "Nandi Hills Print & Pack",
     "aliases": "Nandi Hills Print and Pack|Nandi Print & Pack", "pan": "ABNFN7734G",
     "state_code": "29", "address": ["Plot 18, KIADB Industrial Area, Doddaballapur", "Bengaluru Rural 561203, Karnataka"],
     "bank_name": "Cauvery Urban Co-op Bank", "bank_branch": "Doddaballapur", "bank_account": "117709934410",
     "ifsc": "CUCB0001179", "status": "APPROVED", "payment_terms_days": 45,
     "phone_on_file": "080-5550-0182", "email": "billing@nandihillspack.example", "layout": "modern",
     "msme_category": "micro", "udyam_number": "UDYAM-KR-29-0107734"},
]
for _v in CASE_VENDORS:
    _v["gstin"] = make_gstin(_v["state_code"], _v["pan"])

# po_number, po_date, vendor, status, buyer,
# lines: (item_code, description, hsn, uom, qty, unit_price, basis, received, received_on)
CASE_POS = [
    ("PO-2026-1041", "2026-11-03", "V101", "OPEN", "R. Iyer (Procurement)", [
        ("BRG-6205-2RS", "Ball bearing 6205-2RS", "8482", "Nos", 400, "185.00", "QTY", 400, "2026-11-18"),
        ("BLT-HTD-8M1200", "Timing belt HTD 8M-1200", "4010", "Nos", 60, "1240.00", "QTY", 0, "")]),
    ("PO-2026-1042", "2026-09-25", "V102", "OPEN", "A. Kulkarni (Stores)", [
        ("PKG-CTN-5PLY", "Printed 5-ply carton", "4819", "Nos", 2500, "38.50", "QTY", 2000, "2026-10-10")]),
]

CASE_SAMPLES = [
    G.spec(id="c1_invoice_before_goods", layout="classic", vendor="V101", invoice_no="KBD/26-27/1193",
           invoice_date=date(2026, 11, 19), po_ref="PO-2026-1041", po_date=date(2026, 11, 3),
           lines=[G.line("Ball Bearing 6205-2RS", "8482", 400, "Nos", "185.00"),
                  G.line("Timing Belt HTD 8M-1200", "4010", 60, "Nos", "1240.00")]),
    G.spec(id="c2_small_supplier_late_invoice", layout="modern", vendor="V102", invoice_no="NHP/26-27/0287",
           invoice_date=date(2026, 10, 12), po_ref="PO-2026-1042", po_date=date(2026, 9, 25),
           lines=[G.line("Printed 5-Ply Carton", "4819", 2000, "Nos", "40.00")]),
]

# id: (title, verdict, reason codes, description, the file name the attachment "arrived" under)
CASE_META = {
    "c1_invoice_before_goods": (
        "The invoice arrives before the goods are booked in", "HOLD", ["GOODS_NOT_RECEIVED"],
        "Both lines of the PO billed in full. The bearings were received on 18 Nov; the timing belts are not "
        "booked in yet, so the invoice waits for Stores and for nothing else.",
        "Kaveri_KBD-26-27-1193.pdf"),
    "c2_small_supplier_late_invoice": (
        "A small supplier's invoice, three days from the legal pay-by date", "HOLD", ["PRICE_VARIANCE"],
        "2,000 cartons delivered on 10 Oct and billed at Rs 40.00 against Rs 38.50 on the PO. The supplier is a "
        "registered micro enterprise, so the statutory pay-by date is 45 days from delivery.",
        "NandiHills_NHP-26-27-0287.pdf"),
}


@contextmanager
def _case_vendors():
    """The two vendors, known to the generator's layouts only while the case files are written."""
    added = [v["vendor_id"] for v in CASE_VENDORS if v["vendor_id"] not in G.VENDOR]
    G.VENDOR.update({v["vendor_id"]: v for v in CASE_VENDORS})
    try:
        yield
    finally:
        for vendor_id in added:
            G.VENDOR.pop(vendor_id, None)


def write_case_master(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    G.write_csv(data_dir / "vendors_cases.csv",
                ["vendor_id", "legal_name", "aliases", "gstin", "state_code", "status", "bank_name",
                 "bank_account", "ifsc", "payment_terms_days", "phone_on_file", "email", "msme_category",
                 "udyam_number"],
                [[v["vendor_id"], v["legal_name"], v["aliases"], v["gstin"], v["state_code"], v["status"],
                  v["bank_name"], v["bank_account"], v["ifsc"], v["payment_terms_days"], v["phone_on_file"],
                  v["email"], v["msme_category"], v["udyam_number"]] for v in CASE_VENDORS])
    rows = []
    for po_number, po_date, vendor, status, buyer, lines in CASE_POS:
        for n, (code, desc, hsn, uom, qty, price, basis, received, received_on) in enumerate(lines, 1):
            rows.append([po_number, po_date, vendor, status, "INR", buyer, n, code, desc, hsn, uom, qty, price,
                         f"{q2(G.D(qty) * G.D(price))}", basis, received, received_on])
    G.write_csv(data_dir / "purchase_orders_cases.csv",
                ["po_number", "po_date", "vendor_id", "status", "currency", "buyer", "line_no", "item_code",
                 "description", "hsn_sac", "uom", "qty_ordered", "unit_price", "line_amount", "match_basis",
                 "qty_received", "received_on"], rows)


def write_case_samples(samples_dir: Path) -> None:
    folder = samples_dir / "cases"
    (folder / "truth").mkdir(parents=True, exist_ok=True)
    manifest = []
    for order, inv in enumerate(CASE_SAMPLES, 1):
        (folder / f"{inv['id']}.pdf").write_bytes(G.build_sample(inv))
        (folder / "truth" / f"{inv['id']}.json").write_text(json.dumps(G.truth_for(inv), indent=2), encoding="utf-8",
                                                           newline="\n")   # LF on every machine, as git keeps it
        title, verdict, reasons, description, received_as = CASE_META[inv["id"]]
        manifest.append({"id": inv["id"], "file": f"cases/{inv['id']}.pdf", "order": order, "title": title,
                         "group": "after_hold", "tag": "After the decision", "received_as": received_as,
                         "scanned": inv["scanned"], "layout": inv["layout"], "expected_verdict": verdict,
                         "expected_reasons": reasons, "description": description})
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")


def write_case_data(data_dir: Path, samples_dir: Path) -> None:
    with _case_vendors():
        write_case_master(data_dir)
        write_case_samples(samples_dir)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Write data/vendors_cases.csv, data/purchase_orders_cases.csv and samples/cases/. "
                    "The output is the same on every run.")
    parser.parse_args(argv)
    write_case_data(ROOT / "data", ROOT / "samples")
    print(f"wrote {len(CASE_VENDORS)} vendors, {len(CASE_POS)} purchase orders and {len(CASE_SAMPLES)} "
          f"after-hold invoices")


if __name__ == "__main__":
    main(sys.argv[1:])
