"""A scan is read twice. These tests pin down how the two reads are reconciled."""
import types
from datetime import date
from decimal import Decimal

import numpy as np
import pytest
from PIL import Image

from invoice_desk import pipeline as pipeline_module
from invoice_desk import reader
from invoice_desk.rules import load_rules
from invoice_desk.second_read import reconcile
from invoice_desk.validate import validate

from .conftest import sample_bytes
from .helpers import codes, make_invoice

TODAY = date(2026, 11, 21)
_READING = load_rules().reading
AGREED, DOUBT = _READING.scan_agreed_confidence, _READING.scan_doubt_confidence     # from rules.yaml, not from code


def weak(inv, *names, confidence=0.55):
    for name in names:
        inv.fields[name].confidence = confidence
    return inv


def test_values_that_agree_in_both_reads_are_trusted(rules):
    a, b = weak(make_invoice(), "invoice_number", "invoice_date"), weak(make_invoice(), "invoice_number", "invoice_date")
    assert "LOW_CONFIDENCE_READ" in codes(validate(a, rules, TODAY)[0])          # one read alone would be held
    used, inv, report = reconcile({"gray": a, "bw": b}, rules, TODAY)
    assert used == "bw" and report["reads"] == 2 and "default" in report["why"]
    assert inv.fields["invoice_number"].confidence == AGREED and inv.fields["invoice_date"].confidence == AGREED
    assert "the same in both reads" in inv.fields["invoice_number"].notes[-1]
    assert validate(inv, rules, TODAY)[0] == []
    assert {r["status"] for r in report["fields"]} == {"same in both reads"}


def test_an_invoice_number_that_differs_between_reads_holds_the_invoice(rules):
    a, b = make_invoice(number="TWV/8427"), make_invoice(number="TWV/8421")
    used, inv, report = reconcile({"gray": a, "bw": b}, rules, TODAY)
    number = inv.fields["invoice_number"]
    assert number.value == "TWV/8421" and number.confidence == DOUBT        # high OCR confidence does not save it
    assert "disagree: 'TWV/8421' and 'TWV/8427'" in number.notes[-1]
    findings = validate(inv, rules, TODAY)[0]
    assert codes(findings) == {"LOW_CONFIDENCE_READ"} and "disagree" in findings[0].summary


def test_a_date_that_differs_between_reads_holds_the_invoice(rules):
    used, inv, _ = reconcile({"gray": make_invoice(inv_date="2026-11-10"), "bw": make_invoice(inv_date="2026-11-16")}, rules, TODAY)
    assert codes(validate(inv, rules, TODAY)[0]) == {"LOW_CONFIDENCE_READ"}


def test_formatting_differences_in_a_number_are_not_a_disagreement(rules):
    used, inv, report = reconcile({"gray": make_invoice(number="TOF/26-27/0412"), "bw": make_invoice(number="TOF-26-27-412")}, rules, TODAY)
    number = inv.fields["invoice_number"]
    assert number.confidence >= AGREED and number.notes == ["the same in both reads of the scan"]


def test_the_read_whose_figures_add_up_is_used_whichever_it_is(rules):
    good = make_invoice()
    garbled = make_invoice(total="25317555559.00")            # one read turned the total into nonsense
    for reads in ({"gray": good, "bw": garbled}, {"gray": garbled, "bw": good}):
        used, inv, report = reconcile({k: v.model_copy(deep=True) for k, v in reads.items()}, rules, TODAY)
        assert inv.get("total") == good.get("total") and "add up" in report["why"]
        assert report["failed_checks"][used] == 0 and validate(inv, rules, TODAY)[0] == []
    assert used == "bw"                                         # here the default lost and the other read won


def test_a_garbled_total_in_the_other_read_does_not_cast_doubt_on_a_total_that_adds_up(rules):
    used, inv, report = reconcile({"gray": make_invoice(total="999.00"), "bw": make_invoice()}, rules, TODAY)
    row = next(r for r in report["fields"] if r["field"] == "total")
    assert row["status"] == "the reads differ" and inv.fields["total"].confidence == 0.99


def test_the_read_with_more_line_items_wins_a_tie(rules):
    full = make_invoice(lines=(("Ergonomic mesh office chair, high back", 40, "4500.00"), ("Steel storage cupboard, 4 shelf", 6, "9800.00")))
    short = make_invoice(lines=(("Ergonomic mesh office chair, high back", 40, "4500.00"),))
    used, inv, _ = reconcile({"gray": full, "bw": short}, rules, TODAY)
    assert used == "gray" and len(inv.line_items) == 2


def test_one_read_alone_is_passed_through_unchanged(rules):
    a = weak(make_invoice(), "invoice_number")
    used, inv, report = reconcile({"gray": a}, rules, TODAY)
    assert used == "gray" and report["reads"] == 1 and inv.fields["invoice_number"].confidence == 0.55


def test_binarise_gives_pure_black_and_white_and_removes_paper_noise():
    rng = np.random.default_rng(1)
    paper = np.clip(235 + rng.normal(0, 8, (120, 200)), 0, 255)
    paper[40:60, 30:170] = np.clip(40 + rng.normal(0, 8, (20, 140)), 0, 255)          # a bar of ink
    out = np.asarray(reader.binarise(Image.fromarray(paper.astype(np.uint8))))
    assert set(np.unique(out)) == {0, 255}
    assert out[40:60, 30:170].mean() < 5 and out[:30].mean() > 250


def test_bundled_scans_are_read_twice_and_say_so(pipeline):
    r = pipeline.run(sample_bytes("02_scanned_clean_safety"), "02.pdf", "test")
    assert r.verdict == "APPROVE" and r.invoice.reads["reads"] == 2 and "read twice" in r.stages[1].summary
    table = next(b for b in r.stages[2].blocks if b["title"].startswith("Two reads of the scan"))
    assert [row["Result"] for row in table["rows"]].count("same in both reads") >= 5
    assert "the same in both reads of the scan" in r.invoice.fields["invoice_number"].notes


def test_text_pdfs_are_read_once(pipeline):
    r = pipeline.run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    assert r.invoice.reads is None and "read twice" not in r.stages[1].summary


def test_with_only_one_recorded_read_and_no_engine_the_scan_still_runs(pipeline, monkeypatch):
    missing = types.SimpleNamespace(available=False, label="not installed", version="")
    monkeypatch.setattr(reader, "get_engine", lambda refresh=False: missing)
    monkeypatch.setattr(pipeline_module, "get_engine", lambda refresh=False: missing)
    real = reader.load_ocr_fixture
    monkeypatch.setattr(reader, "load_ocr_fixture", lambda sha, page, variant="gray": None if variant == "bw" else real(sha, page, variant))
    r = pipeline.run(sample_bytes("02_scanned_clean_safety"), "02.pdf", "test")
    assert r.verdict == "APPROVE" and r.invoice.reads is None                       # one read, no corroboration, still correct


def test_reads_that_disagree_on_the_invoice_number_end_in_a_hold_end_to_end(pipeline, monkeypatch):
    real = pipeline_module.extract_invoice

    def second_read_slips(doc, *args, **kwargs):
        inv = real(doc, *args, **kwargs)
        if doc.variant == "bw":
            inv.fields["invoice_number"].value = "ASS/2026/0947"                    # the real number ends in 0941
        return inv
    monkeypatch.setattr(pipeline_module, "extract_invoice", second_read_slips)
    r = pipeline.run(sample_bytes("02_scanned_clean_safety"), "02.pdf", "test")
    assert r.verdict == "HOLD" and [f.code for f in r.decision.reasons] == ["LOW_CONFIDENCE_READ"]
    assert "disagree" in r.decision.explanation and "ASS/2026/0941" in r.decision.explanation
    assert [f.code for f in r.decision.notes] == ["SCAN_READS_DIFFER"]


def test_a_read_that_garbles_the_figures_is_set_aside_and_the_invoice_still_goes_through(pipeline, monkeypatch):
    real = pipeline_module.extract_invoice

    def first_read_garbled(doc, *args, **kwargs):
        inv = real(doc, *args, **kwargs)
        if doc.variant == "gray":
            inv.fields["total"].value = "1110380.00"
            inv.line_items[0].amount = Decimal("645000.00")
        return inv
    monkeypatch.setattr(pipeline_module, "extract_invoice", first_read_garbled)
    r = pipeline.run(sample_bytes("02_scanned_clean_safety"), "02.pdf", "test")
    assert r.verdict == "APPROVE" and r.invoice.get("total") == "111038.00" and r.invoice.reads["used"] == "bw"
    note = next(f for f in r.decision.notes if f.code == "SCAN_READS_DIFFER")
    assert "black-and-white read is used because its figures add up" in note.summary


def test_the_second_read_gives_the_engine_a_black_and_white_picture():
    """What differs between the two reads is the picture handed to the OCR engine. A stand-in
    engine records what it is given, so this holds on any machine."""
    seen = {}

    class Recorder:
        def words_px(self, image):
            seen["levels"] = set(np.unique(np.asarray(image)).tolist())
            return []

    rng = np.random.default_rng(3)
    page = np.clip(230 + rng.normal(0, 10, (600, 500)), 0, 255)
    page[100:130, 60:440] = np.clip(50 + rng.normal(0, 10, (30, 380)), 0, 255)       # one line of "text"
    picture = Image.fromarray(page.astype(np.uint8))
    reader.ocr_image(Recorder(), picture, variant="gray")
    assert len(seen["levels"]) > 20                                # greys reach the engine untouched
    reader.ocr_image(Recorder(), picture, variant="bw")
    assert seen["levels"] <= {0, 255} and len(seen["levels"]) == 2    # pure black and white


@pytest.mark.skipif(not reader.get_engine().available, reason="Tesseract is not installed")
def test_both_live_reads_straighten_the_page_by_the_same_angle():
    content = sample_bytes("06_E3_scan_no_number_implied_po")
    gray = reader.read_pdf(content, ocr_mode="live", variant="gray")
    bw = reader.read_pdf(content, ocr_mode="live", variant="bw")
    assert gray.variant == "gray" and bw.variant == "bw" and gray.pages[0].ocr["angle"] == bw.pages[0].ocr["angle"]
    assert gray.pages[0].ocr["variant"] == "gray" and bw.pages[0].ocr["variant"] == "bw" and len(bw.lines) >= 15
