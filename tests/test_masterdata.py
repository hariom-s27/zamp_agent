"""Master data for round 3: two optional files beside the base ones add the after-hold vendors
and their purchase orders, with an MSME category and delivery dates. Without them, the master
is exactly what it was."""
import hashlib
import shutil
from datetime import date
from decimal import Decimal

import pytest

from invoice_desk import config
from invoice_desk.masterdata import load_master

BASE = ("vendors.csv", "purchase_orders.csv")
EXTRA = ("vendors_cases.csv", "purchase_orders_cases.csv")


DATA = config.DATA_DIR                      # the bundled data, captured before any test points config elsewhere


def _copy(tmp_path, names):
    for name in names:
        shutil.copy(DATA / name, tmp_path / name)
    return tmp_path


def _digest(folder, names) -> str:
    digest = hashlib.sha256()
    for name in names:
        digest.update((folder / name).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def test_the_case_files_add_two_flagged_vendors_and_their_pos_with_delivery_dates():
    master = load_master()
    assert master.vendors["V101"].legal_name == "Kaveri Bearings & Drives Pvt Ltd"
    assert master.vendors["V101"].msme_category == "small" and master.vendors["V101"].udyam_number
    assert master.vendors["V102"].msme_category == "micro" and master.vendors["V102"].payment_terms_days == 45
    assert master.vendors["V001"].msme_category == "" and master.vendors["V001"].udyam_number == ""
    po = master.pos["PO-2026-1041"]
    assert po.vendor_id == "V101" and po.po_date == date(2026, 11, 3)
    assert po.line(1).qty_received == Decimal("400") and po.line(1).received_on == date(2026, 11, 18)
    assert po.line(2).qty_received == Decimal("0") and po.line(2).received_on is None
    po = master.pos["PO-2026-1042"]
    assert po.line(1).qty_received == Decimal("2000") and po.line(1).received_on == date(2026, 10, 10)
    assert master.pos["PO-2026-0147"].line(1).received_on is None


def test_without_the_case_files_the_master_is_exactly_as_before(tmp_path):
    master = load_master(_copy(tmp_path, BASE))
    assert sorted(master.vendors) == [f"V00{n}" for n in range(1, 8)] and "PO-2026-1041" not in master.pos
    assert master.sha256 == _digest(tmp_path, BASE)


def test_the_case_files_are_part_of_the_master_digest(tmp_path):
    master = load_master(_copy(tmp_path, BASE + EXTRA))
    assert master.sha256 == _digest(tmp_path, BASE + EXTRA) and master.sha256 != _digest(tmp_path, BASE)


def test_an_id_in_a_case_file_that_already_exists_is_refused_by_name(tmp_path):
    _copy(tmp_path, BASE + EXTRA)
    vendors = (tmp_path / "vendors_cases.csv").read_text(encoding="utf-8").replace("V102,", "V001,")
    (tmp_path / "vendors_cases.csv").write_text(vendors, encoding="utf-8")
    with pytest.raises(ValueError, match="V001"):
        load_master(tmp_path)
    _copy(tmp_path, EXTRA)
    pos = (tmp_path / "purchase_orders_cases.csv").read_text(encoding="utf-8").replace("PO-2026-1042,", "PO-2026-0147,")
    (tmp_path / "purchase_orders_cases.csv").write_text(pos, encoding="utf-8")
    with pytest.raises(ValueError, match="PO-2026-0147"):
        load_master(tmp_path)


def test_the_screens_reload_the_master_when_a_case_file_appears(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from invoice_desk.ui import common as C
    monkeypatch.setattr(config, "DATA_DIR", _copy(tmp_path, BASE))
    assert "V101" not in C.get_master().vendors
    _copy(tmp_path, EXTRA)
    assert "V101" in C.get_master().vendors
