"""What the independent test set (robustness/) taught the reader and the matcher.

Every test here started as a failure on a TUNE document of that set: an invoice made with
another tool, font and layout than the bundled samples. The holdout documents are not
used here; tests/test_robustness.py pins the measured result on the whole set.
"""
import logging
from decimal import Decimal

import pytest

from invoice_desk import config, extract, reader, review
from invoice_desk.po_match import match_po, pair_lines
from invoice_desk.reader import Cell, Word

from .helpers import codes, make_invoice
from .test_po_match import CHAIR, DESK, post

SET = config.ROOT / "robustness"


def document(doc_id: str) -> bytes:
    return (SET / "documents" / f"{doc_id}.pdf").read_bytes()


def read(doc_id: str, rules, master):
    """Read one document of the set the way the pipeline's extract stage does."""
    doc = reader.read_pdf(document(doc_id), ocr_mode="fixture")
    return extract.extract_invoice(doc, rules.company.gstin, rules.company.name, master.gstin_index, list(master.pos))


def cell(text: str) -> Cell:
    return Cell([Word(t, 10.0 * i, 10.0 * i + 8, 0.0, 8.0, None) for i, t in enumerate(text.split())])


# ---- the same item on two invoice lines (two deliveries), one line on the PO

def test_two_invoice_lines_can_bill_the_same_po_line(master, store, rules):
    inv = make_invoice(lines=((CHAIR, 25, "4500.00"), (CHAIR, 15, "4500.00"), (DESK, 10, "14250.00")))
    po, findings, _, allocations = match_po(inv, master.vendors["V001"], master, store, rules)
    assert codes(findings) == set()
    assert [a["po_line_no"] for a in allocations] == [1, 1, 2]
    assert po["lines_matched"] == 3


def test_lines_sharing_a_po_line_are_added_up_before_the_quantity_check(master, store, rules):
    """25 + 16 chairs on a PO line of 40: neither line is over on its own, together they are."""
    inv = make_invoice(lines=((CHAIR, 25, "4500.00"), (CHAIR, 16, "4500.00")))
    _, findings, _, _ = match_po(inv, master.vendors["V001"], master, store, rules)
    over = [f for f in findings if f.code == "QTY_OVER_PO"]
    assert len(over) == 1 and "41 of 40" in over[0].summary and "25 on earlier lines of this invoice" in over[0].summary
    assert over[0].evidence["billed_before"] == "0" and over[0].evidence["this_invoice"] == "41"


def test_a_po_line_is_shared_only_by_lines_that_fit_nothing_else(master):
    """Two different items never land on one PO line: sharing needs the same wording."""
    inv = make_invoice(lines=((CHAIR, 10, "4500.00"), ("Visitor chair, fabric seat, chrome frame", 10, "4500.00")))
    pairs = pair_lines(inv.line_items, master.pos["PO-2026-0147"])
    assert pairs[0][1].line_no == 1 and pairs[1][1] is None


def test_the_approval_time_check_adds_up_lines_that_share_a_po_line(master, store, rules):
    """The review queue's PO re-check sees 25 + 16 on one PO line as 41, not as two lines that each fit."""
    post(store, "L-0001", "V001", "PO-2026-0147", [(1, CHAIR, 25, "112500.00"), (1, CHAIR, 16, "72000.00")],
         status="PENDING")
    entry = store.ledger_entries(po_number="PO-2026-0147", statuses=("PENDING",))[0]
    position = review.po_position(store, master, rules, entry)
    problem = next(p for p in position["problems"] if p["code"] == "QTY_OVER_PO")
    assert "41 of 40" in problem["text"] and problem["evidence"]["this_invoice"] == "41"
    assert [r["This invoice"] for r in position["rows"] if r["PO line"].startswith("1 ")] == ["41"]


# ---- tax lines printed as ledger rows inside the item grid (Tally-style)

@pytest.mark.parametrize("text,kind", [
    ("Output CGST 9%", "cgst"), ("Output SGST 9%", "sgst"), ("Output IGST 18%", "igst"), ("CGST @ 9%", "cgst"),
    ("Add : IGST @ 18.00 %", "igst"), ("CGST9 (9%)", "cgst"), ("IGST18 (18%)", "igst"), ("UTGST @ 9%", "sgst"),
    ("Input shaft bearing", None), ("Add-on warranty pack", None), ("Output flange 40 mm", None), ("Central Tax", None),
    ("Taper Roller Bearing 32208", None),
])
def test_a_tax_line_is_recognised_whatever_is_printed_in_front_of_it(text, kind):
    assert extract.tax_kind(cell(text)) == kind


def test_tax_ledger_rows_inside_the_item_grid_are_tax_lines_not_items(rules, master):
    inv = read("tally_01", rules, master)
    assert [(str(i.qty), str(i.unit_price), str(i.amount)) for i in inv.line_items] == [
        ("40", "385.00", "15400.00"), ("6", "1460.00", "8760.00"), ("24", "212.50", "5100.00")]
    assert (inv.get("cgst"), inv.get("sgst"), inv.get("subtotal"), inv.get("total")) == ("2633.40", "2633.40", "29260.00", "34526.80")
    assert "confirmed by arithmetic" in inv.fields["total"].notes


# ---- the amount in words: under its label, and over wrapped lines

def test_the_amount_in_words_is_found_on_the_line_under_its_label(rules, master):
    """'Amount Chargeable (in words)      E. & O.E' with the sentence on the next line."""
    inv = read("tally_01", rules, master)
    assert Decimal(inv.get("amount_in_words")) == Decimal("34526.80")


def test_a_wrapped_amount_in_words_is_read_to_its_end(rules, master):
    """The sentence runs over two lines beside the totals column; stopping after the first
    line would read 1,48,300 and hold a correct invoice for 'words differ from figures'."""
    inv = read("zoho_01", rules, master)
    assert Decimal(inv.get("amount_in_words")) == Decimal("148373.20") == Decimal(inv.get("total"))


# ---- glyphs the layout library cannot decode

def test_glyphs_the_layout_library_cannot_decode_are_read_again_with_the_pdf_engine(rules, master):
    """Chrome embeds the web font of this invoice so that pdfminer returns a private-use code
    point for '-', ':' and '('. Round 1 read 'INV?000482' and 'PO?2026?0506'."""
    doc = reader.read_pdf(document("zoho_01"))
    assert not reader.UNDECODED.search(doc.text)
    assert "re-read" not in doc.pages[0].note and "read again with the PDF engine" in doc.pages[0].note
    inv = read("zoho_01", rules, master)
    assert inv.get("po_reference") == "PO-2026-0506" and inv.fields["po_reference"].notes == []
    assert inv.fields["po_reference"].confidence >= 0.95


class _Engine:
    """Stands in for the PDF engine's text of a rectangle."""
    def __init__(self, answer):
        self.answer = answer

    def get_text_bounded(self, **box):
        return self.answer


def test_a_second_reading_is_accepted_only_if_it_keeps_what_was_already_decoded():
    good, bad, lost = (Word("INV000482", 10, 60, 10, 20) for _ in range(3))
    assert reader.repair_undecoded([good], _Engine("INV-000482"), 842) == (1, 0) and good.text == "INV-000482"
    assert reader.repair_undecoded([bad], _Engine("INV-000482 Balance"), 842) == (0, 1)        # more text than the gap allows
    assert reader.repair_undecoded([lost], _Engine("INV-999482"), 842) == (0, 1)               # contradicts decoded characters
    assert bad.text == "INV000482" and bad.conf == 0.0
    assert extract._conf([bad], "text") == 0.4 and extract._conf([good], "text") == 0.99       # an unreadable glyph is never trusted


def test_library_warnings_about_fonts_are_not_printed(caplog):
    with caplog.at_level(logging.WARNING):
        reader.read_pdf(document("zoho_01"))
    assert not [r for r in caplog.records if r.name.startswith("pdfminer")]


# ---- an invoice number labelled with nothing but '#'

def page(rows) -> reader.DocRead:
    """A one-page text document from rows of (x, text) cells, 14 points apart."""
    words = []
    for n, row in enumerate(rows):
        top = 40.0 + 14 * n
        for x, text in row:
            for token in text.split():
                width = 5.0 * len(token)
                words.append(Word(token, float(x), x + width, top, top + 9))
                x += width + 2.5
    return reader.DocRead("0" * 64, 1, [reader.PageRead(1, "text", 595, 842, lines=reader.build_lines(words, 1), chars=500)])


def extracted(rows, rules, master):
    return extract.extract_invoice(page(rows), rules.company.gstin, rules.company.name, master.gstin_index, list(master.pos))


def test_a_number_under_the_title_with_only_a_hash_sign_is_the_invoice_number(rules, master):
    inv = read("zoho_01", rules, master)
    assert inv.get("invoice_number") == "INV-000482"
    head = [[(40, "Acme Tools Pvt Ltd"), (400, "TAX INVOICE")]]
    assert extracted(head + [[(400, "# AT/2026/118")]], rules, master).get("invoice_number") == "AT/2026/118"
    assert extracted(head + [[(400, "No. AT/2026/118")]], rules, master).get("invoice_number") == "AT/2026/118"
    for under in ("Original for Recipient", "# 3 pages", "AT/2026/118", "# AT/2026/118 and 119"):
        assert extracted(head + [[(400, under)]], rules, master).get("invoice_number") is None, under


# ---- a label that runs into its value

def test_a_label_that_runs_into_its_value_is_split(rules, master):
    """A narrow spreadsheet column clips the colon: the page says 'P.O. NoPO-2026-0512'."""
    assert [p.text for p in extract._pieces(cell("P.O. NoPO-2026-0512"))] == ["P.O.", "No", "PO-2026-0512"]
    assert [p.text for p in extract._pieces(cell("Bill No.HS/571 Date12.11.2026"))] == ["Bill", "No.", "HS/571", "Date", "12.11.2026"]
    for word in ("November", "Notes", "NOIDA", "Number", "Dated", "Reference", "No", "Dt"):
        assert [p.text for p in extract._pieces(cell(word))] == [word]
    assert read("excel_03", rules, master).get("po_reference") == "PO-2026-0512"


def test_an_identifier_after_a_colon_is_not_reported_as_corrected(rules, master):
    """'P.O. No:PO-2026-0510' is one word on the page. Round 1 compared the identifier with the
    whole word, called the difference a correction, and cut its confidence to 0.6."""
    field = read("excel_01", rules, master).fields["po_reference"]
    assert field.value == "PO-2026-0510" and field.notes == [] and field.confidence >= 0.95


# ---- which printed figure is the invoice total

def test_arithmetic_picks_the_invoice_total_when_the_page_has_two_totals(rules, master):
    """'Total 19,780.00', then the taxes, then 'G. Total 23,340.40'. Round 1 took the first."""
    inv = read("excel_01", rules, master)
    assert (inv.get("subtotal"), inv.get("total")) == ("19780.00", "23340.40")


INVOICE = [[(40, "TAX INVOICE")], [(40, "Acme Tools Pvt Ltd")], [(40, "GSTIN: 29AABCT7306K1Z2")],
           [(40, "Invoice No: T/101"), (300, "Invoice Date: 10-11-2026")],
           [(40, "Description"), (300, "Qty"), (380, "Rate"), (460, "Amount")],
           [(40, "Office chair"), (300, "2"), (380, "1,000.00"), (460, "2,000.00")],
           [(40, "IGST @ 18%"), (460, "360.00")]]


def test_a_total_under_an_unknown_label_is_accepted_only_when_the_arithmetic_points_at_it(rules, master):
    inv = extracted(INVOICE + [[(40, "Kindly remit"), (460, "2,360.00")]], rules, master)
    assert inv.get("total") == "2360.00" and "Kindly remit" in inv.fields["total"].notes[0]
    assert "lines plus tax" in inv.fields["total"].notes[0]
    assert extracted(INVOICE + [[(40, "Kindly remit"), (460, "2,400.00")]], rules, master).get("total") is None


def test_a_known_total_label_that_does_not_add_up_is_still_the_total(rules, master):
    """A vendor's arithmetic mistake must stay visible: the printed total is reported as printed."""
    inv = extracted(INVOICE + [[(40, "Grand Total"), (460, "2,400.00")]], rules, master)
    assert inv.get("total") == "2400.00"
    held = read("excel_04", rules, master)
    assert held.get("total") == "126864.70" and Decimal(held.get("subtotal")) + Decimal(held.get("tax_total")) != Decimal(held.get("total"))


def test_a_description_keeps_its_short_words_on_a_text_page(rules, master):
    """'A4' and '75' sit left of the column heading; round 1 dropped them as stray serial numbers."""
    assert read("excel_01", rules, master).line_items[0].description == "A4 Copier Paper 75 GSM (Ream of 500)"


# ---- photographs of paper (read here from the recorded OCR output of each photograph)

def scan(doc_id: str, variant: str = "gray") -> reader.DocRead:
    return reader.read_pdf(document(doc_id), ocr_mode="fixture", variant=variant)


def run_document(doc_id: str, tmp_path, rules, master):
    """One document of the set through the whole pipeline, on fresh books."""
    from invoice_desk.store import Store
    from scripts import evaluate_robustness as ER
    from .conftest import LLM_OFF, TODAY
    from invoice_desk.llm import LlmAssist
    from invoice_desk.pipeline import Pipeline
    record = ER.load_set([doc_id])[0]
    pipeline = Pipeline(store=Store(tmp_path / f"{doc_id}.db"), rules=rules, master=ER.register(master, record),
                        llm=LlmAssist(LLM_OFF), today=TODAY, ocr_mode="fixture")
    return pipeline.run(document(doc_id), f"{doc_id}.pdf", "batch")


def test_a_photograph_is_cut_out_of_its_page_and_its_sheet_is_found():
    page = scan("photo_tally_01").pages[0]
    assert page.kind == "scan" and page.ocr["photo"] and page.ocr["how"].startswith("straightened photograph")
    crop, quad = page.ocr["crop"], page.ocr["quad"]
    assert crop[1] > 0.005 and crop[3] < 0.995                  # blank bands of the PDF page above and below the picture
    assert len(quad) == 4 and 0.02 < quad[0][0] < 0.15 and quad[2][0] > 0.9      # the sheet, inside the desk around it
    assert page.width < 595 and page.height < 842               # the page is the sheet now, not the sheet plus the desk
    flatbed = reader.read_pdf((config.SAMPLES_DIR / "02_scanned_clean_safety.pdf").read_bytes(), ocr_mode="fixture").pages[0]
    assert not flatbed.ocr["photo"] and flatbed.ocr["crop"] is None and flatbed.ocr["quad"] is None
    assert (flatbed.width, flatbed.height) == (595.3, 841.9)    # a flatbed scan is read exactly as before


def test_a_page_photographed_on_its_side_is_turned_and_read(tmp_path, rules, master):
    """Round 1 answered REJECT, 'not an invoice', for a correct invoice lying on its side."""
    page = scan("photo_sideways").pages[0]
    assert page.ocr["turns"] in (1, 3) and page.height > page.width
    result = run_document("photo_sideways", tmp_path, rules, master)
    assert result.verdict == "APPROVE" and result.invoice.get("invoice_number") == "KBT/26-27/1195"
    assert result.pages[0]["prep"]["turns"] == page.ocr["turns"] and result.pages[0]["prep"]["quad"]


def test_rows_of_a_photographed_table_come_out_level(rules, master):
    """Before straightening, one printed row was scattered over three text lines and no item was read."""
    inv = read("photo_zoho_01", rules, master)
    assert [(str(i.qty), str(i.unit_price), str(i.amount)) for i in inv.line_items] == [
        ("600.00", "118.40", "71040.00"), ("250.00", "62.75", "15687.50")]


def test_a_total_written_by_hand_is_established_by_the_arithmetic_and_the_words(tmp_path, rules, master):
    inv = read("photo_hand_total", rules, master)
    total = inv.fields["total"]
    assert total.value == "26013.10" and total.source.method == "derived"
    assert "the lines plus tax and the amount in words both give 26013.10" in total.notes[0]
    assert run_document("photo_hand_total", tmp_path, rules, master).verdict == "APPROVE"


def test_the_arithmetic_and_the_words_must_agree_before_a_scanned_total_is_supplied(rules, master):
    """One confirmation is not two: with the words saying something else, no total is made up."""
    doc = scan("photo_hand_total")
    for line in doc.lines:
        for word in line.words:
            if word.text == "Thirteen":
                word.text = "Fourteen"                       # the amount in words now says 26,014.10
    inv = extract.extract_invoice(doc, rules.company.gstin, rules.company.name, master.gstin_index, list(master.pos))
    assert inv.fields["total"].source is None or inv.fields["total"].source.method != "derived"
    assert inv.get("total") != "26013.10"


def test_the_foot_of_a_tax_summary_is_not_the_invoice_total(rules, master):
    """'Total  22,045.00  1,984.05  1,984.05  3,968.10' under the HSN table repeats the tax; round 1 took 3,968.10 as the total."""
    for doc_id in ("photo_hand_total", "photo_tally_01", "tally_01"):
        inv = read(doc_id, rules, master)
        assert Decimal(inv.get("total")) == Decimal(inv.get("subtotal")) + Decimal(inv.get("tax_total")), doc_id


def test_scan_slips_in_a_table_row_and_a_total():
    ocr = lambda text: Cell([Word(t, 10.0 * i, 10.0 * i + 8, 0.0, 8.0, 90.0) for i, t in enumerate(text.split())])   # noqa: E731
    assert [str(v) for v, _ in extract._amount_words(ocr("Total %1,02,338.45").words)] == ["102338.45"]      # a rupee sign read as '%'
    assert [str(v) for v, _ in extract._amount_words(ocr("IGST 18% 15,610.95").words)] == ["15610.95"]       # a real percentage is still skipped
    assert extract._amount_words(ocr("%18").words) == [] and extract._amount_words(cell("%1,02,338.45").words) == []   # text pages: nothing is guessed


def test_a_quantity_that_runs_into_its_unit_is_read(rules, master):
    """The scan reads '25Nos' as one word."""
    doc = scan("photo_tally_01")
    inv = extract.extract_invoice(doc, rules.company.gstin, rules.company.name, master.gstin_index, list(master.pos))
    assert [(str(i.qty), i.uom) for i in inv.line_items] == [("25", "Nos"), ("40", "Nos")]


def test_a_vendor_name_split_where_the_scan_lost_the_ampersand_is_one_name(tmp_path, rules, master):
    """'Kaveri Bearings & Transmission Pvt Ltd' was read as two cells and the invoice was held for
    'GSTIN belongs to another name'."""
    inv = read("photo_tally_01", rules, master)
    assert inv.get("vendor_name") == "Kaveri Bearings Transmission Pvt Ltd"
    result = run_document("photo_tally_01", tmp_path, rules, master)
    assert result.verdict == "APPROVE" and "VENDOR_GSTIN_MISMATCH" not in {f.code for f in result.decision.reasons}


def test_two_reads_that_differ_on_the_number_or_the_date_hold_the_invoice(tmp_path, rules, master):
    """The check that kept both remaining tune failures from being approved with a wrong value."""
    number = run_document("photo_tally_02", tmp_path, rules, master)
    assert number.verdict == "HOLD" and "LOW_CONFIDENCE_READ" in {f.code for f in number.decision.reasons}
    assert any("disagree" in n or "one of the two reads" in n for n in number.invoice.fields["invoice_number"].notes)
    dated = run_document("photo_zoho_01", tmp_path, rules, master)
    assert dated.verdict == "HOLD" and "LOW_CONFIDENCE_READ" in {f.code for f in dated.decision.reasons}
    assert any("disagree" in n for n in dated.invoice.fields["invoice_date"].notes)


def test_a_number_found_in_only_one_read_is_not_trusted(rules):
    from invoice_desk.second_read import doubtful_fields, reconcile
    from .conftest import TODAY
    first, second = make_invoice(), make_invoice(invoice_number=None, invoice_date=None)
    used, inv, report = reconcile({"gray": first, "bw": second}, rules, TODAY, photo=True)
    assert used == "gray" and report["labels"] == {"gray": "first read", "bw": "second (enlarged) read"}
    assert inv.fields["invoice_number"].confidence == rules.reading.scan_doubt_confidence < rules.reading.min_confidence_critical
    assert inv.fields["invoice_date"].confidence == rules.reading.scan_doubt_confidence
    assert "found in only one of the two reads of the scan" in inv.fields["invoice_number"].notes
    assert set(doubtful_fields(report)) == {"invoice_number", "invoice_date"}
    both = reconcile({"gray": make_invoice(), "bw": make_invoice()}, rules, TODAY)
    assert both[1].fields["invoice_number"].confidence >= rules.reading.scan_agreed_confidence and doubtful_fields(both[2]) == []
    assert both[0] == "bw" and both[2]["labels"]["bw"] == "black-and-white read"     # a flatbed scan keeps its default


@pytest.mark.skipif(not reader.get_engine().available, reason="Tesseract is not installed")
def test_live_ocr_of_a_sideways_photograph_ends_in_the_right_answer_or_a_reading_hold(tmp_path, rules, master):
    """On any Tesseract build: the page is found, turned and read, and the outcome is the
    documented one or a hold for a reading reason, never a rejection and never a wrong approval."""
    from invoice_desk.store import Store
    from scripts import evaluate_robustness as ER
    from .conftest import LLM_OFF, READING_HOLDS, TODAY
    from invoice_desk.llm import LlmAssist
    from invoice_desk.pipeline import Pipeline
    record = ER.load_set(["photo_sideways"])[0]
    pipeline = Pipeline(store=Store(tmp_path / "live.db"), rules=rules, master=ER.register(master, record),
                        llm=LlmAssist(LLM_OFF), today=TODAY, ocr_mode="live")
    result = pipeline.run(document("photo_sideways"), "photo_sideways.pdf", "batch")
    assert result.pages[0]["ocr_source"] == "live" and result.pages[0]["prep"]["turns"] in (1, 3)
    codes_given = {f.code for f in result.decision.reasons}
    assert result.verdict == "APPROVE" or (result.verdict == "HOLD" and codes_given <= READING_HOLDS | {"VENDOR_GSTIN_MISMATCH"})
    if result.verdict == "APPROVE":
        assert ER.compare(result.invoice, record["truth"]) == []
