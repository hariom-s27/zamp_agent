"""Master data: the approved-vendor list and the purchase order register (CSV files in
data/). In a real deployment these two loaders would be replaced by ERP queries; nothing
else in the pipeline would change."""
from __future__ import annotations

import csv
import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from . import config
from .textutil import to_decimal


class Vendor(BaseModel):
    vendor_id: str
    legal_name: str
    aliases: list[str] = []
    gstin: str
    state_code: str
    status: str
    bank_name: str = ""
    bank_account: str = ""
    ifsc: str = ""
    payment_terms_days: int = 30
    phone_on_file: str = ""
    email: str = ""

    @property
    def names(self) -> list[str]:
        return [self.legal_name] + self.aliases


class PoLine(BaseModel):
    line_no: int
    item_code: str = ""
    description: str
    hsn_sac: str = ""
    uom: str = ""
    qty_ordered: Decimal
    unit_price: Decimal
    line_amount: Decimal
    match_basis: str = "QTY"                 # QTY (goods) | AMOUNT (services billed in instalments)
    qty_received: Optional[Decimal] = None   # None = receipts are not tracked for this line


class PurchaseOrder(BaseModel):
    po_number: str
    po_date: date
    vendor_id: str
    status: str
    currency: str = "INR"
    buyer: str = ""
    lines: list[PoLine] = []

    @property
    def total(self) -> Decimal:
        return sum((ln.line_amount for ln in self.lines), Decimal("0"))

    def line(self, number: int) -> Optional[PoLine]:
        return next((ln for ln in self.lines if ln.line_no == number), None)


class MasterData(BaseModel):
    vendors: dict[str, Vendor]
    pos: dict[str, PurchaseOrder]
    sha256: str = ""

    def vendor_by_gstin(self, gstin: Optional[str]) -> Optional[Vendor]:
        return next((v for v in self.vendors.values() if gstin and v.gstin == gstin), None)

    def pos_for_vendor(self, vendor_id: str, open_only: bool = False) -> list[PurchaseOrder]:
        found = [p for p in self.pos.values() if p.vendor_id == vendor_id]
        return [p for p in found if p.status == "OPEN"] if open_only else found

    @property
    def gstin_index(self) -> dict[str, str]:
        return {v.gstin: v.vendor_id for v in self.vendors.values()}


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def load_master(data_dir: Optional[Path] = None) -> MasterData:
    data_dir = Path(data_dir or config.DATA_DIR)
    vendor_file, po_file = data_dir / "vendors.csv", data_dir / "purchase_orders.csv"
    vendors: dict[str, Vendor] = {}
    for row in _rows(vendor_file):
        vendors[row["vendor_id"]] = Vendor(
            vendor_id=row["vendor_id"], legal_name=row["legal_name"],
            aliases=[a.strip() for a in row.get("aliases", "").split("|") if a.strip()],
            gstin=row["gstin"], state_code=row["state_code"], status=row["status"].upper(),
            bank_name=row.get("bank_name", ""), bank_account=row.get("bank_account", ""),
            ifsc=row.get("ifsc", ""), payment_terms_days=int(row.get("payment_terms_days") or 30),
            phone_on_file=row.get("phone_on_file", ""), email=row.get("email", ""))
    pos: dict[str, PurchaseOrder] = {}
    for row in _rows(po_file):
        po = pos.get(row["po_number"])
        if po is None:
            po = pos[row["po_number"]] = PurchaseOrder(
                po_number=row["po_number"], po_date=date.fromisoformat(row["po_date"]),
                vendor_id=row["vendor_id"], status=row["status"].upper(), currency=row.get("currency") or "INR",
                buyer=row.get("buyer", ""))
        qty, price = to_decimal(row["qty_ordered"]), to_decimal(row["unit_price"])
        po.lines.append(PoLine(
            line_no=int(row["line_no"]), item_code=row.get("item_code", ""), description=row["description"],
            hsn_sac=row.get("hsn_sac", ""), uom=row.get("uom", ""), qty_ordered=qty, unit_price=price,
            line_amount=to_decimal(row.get("line_amount")) or qty * price,
            match_basis=(row.get("match_basis") or "QTY").upper(), qty_received=to_decimal(row.get("qty_received"))))
    digest = hashlib.sha256()
    for path in (vendor_file, po_file):
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return MasterData(vendors=vendors, pos=pos, sha256=digest.hexdigest())
