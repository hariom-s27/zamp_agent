"""The reader and extractor against ground truth: the bundled samples (two of them
scans, read from recorded OCR) and layouts generated at random that it was never tuned on."""
import json
from decimal import Decimal

import pytest

from invoice_desk import config, reader
from invoice_desk import textutil as T
from invoice_desk.extract import extract_invoice
from scripts import generate_test_data as G

from .conftest import STRICT_OCR, sample_bytes

SAMPLES = [s["id"] for s in G.SAMPLES]
RULES_READABLE = [s for s in SAMPLES if s != "10_ai_assist_letter_invoice"]


def _extract(content, master, ocr_mode="fixture"):
    doc = reader.read_pdf(content, ocr_mode=ocr_mode)
    return doc, extract_invoice(doc, G.BUYER["gstin"], G.BUYER["name"], master.gstin_index, list(master.pos))


def _money_equal(got, want) -> bool:
    got, want = T.to_decimal(got), T.to_decimal(want)
    if got is None:
        return want is None or want == 0
    return want is not None and abs(got - want) <= Decimal("0.01")


def _compare(inv, truth) -> list[str]:
    bad = []
    for field, key in (("invoice_number", "invoice_no"), ("invoice_date", "invoice_date"), ("po_reference", "po_ref"),
                       ("vendor_gstin", "vendor_gstin"), ("buyer_gstin", "buyer_gstin"), ("bank_account", "bank_account"),
                       ("bank_ifsc", "ifsc")):
        if (inv.get(field) or None) != (truth[key] or None):
            bad.append(f"{field}: {inv.get(field)!r} != {truth[key]!r}")
    for field, key in (("subtotal", "taxable"), ("cgst", "cgst"), ("sgst", "sgst"), ("igst", "igst"), ("total", "total")):
        if not _money_equal(inv.get(field), truth[key]):
            bad.append(f"{field}: {inv.get(field)!r} != {truth[key]!r}")
    if inv.tax_inclusive != truth["tax_inclusive"]:
        bad.append("tax_inclusive flag")
    if T.name_similarity(inv.get("vendor_name") or "", truth["vendor_name_printed"]) < 0.97:
        bad.append(f"vendor_name: {inv.get('vendor_name')!r} != {truth['vendor_name_printed']!r}")
    if len(inv.line_items) != len(truth["lines"]):
        return bad + [f"{len(inv.line_items)} lines != {len(truth['lines'])}"]
    for item, want in zip(inv.line_items, truth["lines"]):
        for got, key in ((item.qty, "qty"), (item.unit_price, "unit_price"), (item.amount, "amount"),
                         (item.unit_price_pre_tax, "unit_price_pre_tax"), (item.amount_pre_tax, "amount_pre_tax")):
            if (got is None) != (want[key] is None) or (got is not None and not _money_equal(got, want[key])):
                bad.append(f"line {item.line_no} {key}: {got} != {want[key]}")
        if T.desc_similarity(item.description, want["description"]) < 0.8:
            bad.append(f"line {item.line_no} description: {item.description!r}")
    return bad


@pytest.mark.parametrize("sample_id", RULES_READABLE)
def test_every_field_of_every_sample_matches_the_truth(sample_id, master):
    truth = json.loads((config.SAMPLES_DIR / "truth" / f"{sample_id}.json").read_text(encoding="utf-8"))
    doc, inv = _extract(sample_bytes(sample_id), master)
    assert _compare(inv, truth) == []
    assert inv.doc_type == "tax_invoice"
    for name in ("invoice_date", "total", "vendor_gstin"):
        src = inv.fields[name].source
        assert src and src.snippet and src.bbox, f"{name} has no source pointer"   # every value traces to the page


def test_scanned_pages_have_no_text_layer_and_are_read_by_ocr(master):
    doc, _ = _extract(sample_bytes("02_scanned_clean_safety"), master)
    page = doc.pages[0]
    assert page.kind == "scan" and page.chars == 0 and page.ocr["source"] == "recorded" and len(page.lines) > 15


def test_missing_invoice_number_stays_missing(master):
    _, inv = _extract(sample_bytes("06_E3_scan_no_number_implied_po"), master)
    number = inv.fields["invoice_number"]
    assert not number.present and number.missing_reason       # blank 'Bill No.' is not filled from the next cell
    assert not inv.has("po_reference")


def test_a_scan_slip_in_a_price_is_repaired_by_arithmetic_and_recorded(master):
    _, inv = _extract(sample_bytes("06_E3_scan_no_number_implied_po"), master)
    line = inv.line_items[2]
    assert line.unit_price == Decimal("1150.00") and "price_corrected_by_arithmetic" in line.flags and line.notes
    assert "correcting scan slips" in " ".join(inv.fields["buyer_gstin"].notes)    # our GSTIN was read with a stray character


def test_tax_inclusive_prices_are_converted_to_pre_tax(master):
    _, inv = _extract(sample_bytes("07_E4a_tax_inclusive_within_tolerance"), master)
    assert inv.tax_inclusive and inv.line_items[0].unit_price == Decimal("3780.00")
    assert inv.line_items[0].unit_price_pre_tax == Decimal("3203.39") and inv.money("subtotal") == Decimal("93867.80")


def test_free_form_letter_is_not_guessed_at(master):
    _, inv = _extract(sample_bytes("10_ai_assist_letter_invoice"), master)
    assert inv.line_items == [] and not inv.has("total") and not inv.has("invoice_number")
    assert inv.get("po_reference") == "PO-2026-0178"          # a PO number from the register found in the prose
    assert inv.get("vendor_gstin") == "36AAHFD5127Q1Z0"


@pytest.mark.parametrize("name,expected", [("bad_inputs/quotation_not_invoice.pdf", "quotation"),
                                           ("bad_inputs/newsletter_not_invoice.pdf", "not_invoice"),
                                           ("01_happy_path_furniture.pdf", "tax_invoice")])
def test_document_type(name, expected, master):
    _, inv = _extract(sample_bytes(name), master)
    assert inv.doc_type == expected


@pytest.mark.parametrize("layout", ["classic", "modern", "typewriter", "grid", "retail"])
@pytest.mark.parametrize("name", ["3M Precision Tools Pvt Ltd", "A2Z Office Supplies LLP", "Seven Hills Traders",
                                  "Tungabhadra <img src=x onerror=alert(1)> Ltd"])
def test_vendor_name_is_the_name_line_not_an_address_or_contact_line(layout, name, master):
    pdf = G.render_invoice_pdf(G.spec(id="x", layout=layout, vendor="V001", invoice_no="T/1", invoice_date=G.date(2026, 11, 18),
                                      po_ref="PO-2026-0147", po_date=G.date(2026, 10, 20), vendor_name_printed=name,
                                      lines=[G.line("Steel Storage Cupboard, 4 Shelf", "9403", 1, "Nos", "9800.00")]))
    _, inv = _extract(pdf, master)
    assert (inv.get("vendor_name") or "").lower() == name.lower()


@pytest.mark.parametrize("index", range(16))
def test_unseen_text_layouts_are_read_completely(index, master):
    """Random style, labels, column names, date format and items; nothing here was used to tune the reader."""
    spec = G.variant_spec(index)
    _, inv = _extract(G.render_invoice_pdf(spec), master)
    assert _compare(inv, G.truth_for(spec)) == []


@pytest.mark.skipif(not reader.get_engine().available, reason="Tesseract is not installed")
@pytest.mark.parametrize("sample_id", ["02_scanned_clean_safety", "06_E3_scan_no_number_implied_po"])
def test_live_ocr_reads_the_scanned_samples(sample_id, master):
    truth = json.loads((config.SAMPLES_DIR / "truth" / f"{sample_id}.json").read_text(encoding="utf-8"))
    doc, inv = _extract(sample_bytes(sample_id), master, ocr_mode="live")
    assert doc.pages[0].ocr["source"] == "live" and abs(doc.pages[0].ocr["angle"]) >= 0.5   # the tilt was found
    assert len(doc.lines) >= 15 and inv.doc_type == "tax_invoice" and inv.get("vendor_gstin") == truth["vendor_gstin"]
    if STRICT_OCR:
        assert _compare(inv, truth) == []


def test_page_blocks_separates_side_by_side_columns():
    import numpy as np
    ink = np.zeros((300, 400), dtype=bool)
    ink[20:60, 20:150] = True       # left block
    ink[26:70, 230:380] = True      # right block on a different baseline
    ink[150:260, 20:380] = True     # a table below
    boxes = reader.page_blocks(ink)
    assert len(boxes) == 3
    left = [b for b in boxes if b[2] <= 150]
    right = [b for b in boxes if b[0] >= 230]
    assert len(left) == 1 and len(right) == 1      # the two header blocks were not merged into one


# ---------------------------------------------------------------- invoices that run over several pages

def _long_spec(n_items: int) -> dict:
    items = [G.line(f"{desc} lot {i}", hsn, (i * 7) % 23 + 1, uom, f"{100 + i * 37.25:.2f}")
             for i, (desc, hsn, uom) in enumerate(G.VARIANT_ITEMS * 3, 1)][:n_items]
    return G.spec(id="long", layout="classic", vendor="V001", invoice_no="TOF/26-27/0500", invoice_date=G.date(2026, 11, 18),
                  po_ref="PO-2026-0147", po_date=G.date(2026, 10, 20), lines=items)


@pytest.mark.parametrize("n_items,first,later", [(30, 12, 30), (30, 10, 8), (13, 12, 30), (3, 2, 1)])
@pytest.mark.parametrize("repeat_header", [True, False])
@pytest.mark.parametrize("carry_forward", [True, False])
def test_item_tables_that_continue_over_pages(n_items, first, later, repeat_header, carry_forward, master):
    """Running heads, repeated column headings, 'continued' strips, page numbers and
    carried-forward amounts between the pages are not line items."""
    spec = _long_spec(n_items)
    pdf = G.render_multipage_invoice_pdf(spec, first_page_lines=first, later_page_lines=later,
                                         repeat_header=repeat_header, carry_forward=carry_forward)
    doc, inv = _extract(pdf, master)
    assert len(doc.pages) >= 2
    assert _compare(inv, G.truth_for(spec)) == []
    assert all("continued" not in i.description.lower() and "forward" not in i.description.lower() for i in inv.line_items)
    assert {i.source.page for i in inv.line_items} == set(range(1, len(doc.pages) + 1)) or n_items == 3
