"""The whole process, end to end, on real files: every bundled sample, repeated files,
stage failures, and inputs that must fail gracefully."""
import io
import json
import random
import types

import pytest

from invoice_desk import config, pipeline as pipeline_module, reader
from invoice_desk.llm import LlmAssist
from invoice_desk.models import RunResult
from invoice_desk.pipeline import STAGES, Pipeline
from invoice_desk.store import Store
from scripts import generate_test_data as G

from .conftest import LLM_OFF, READING_HOLDS, STRICT_OCR, TODAY, sample_bytes

MANIFEST = json.loads((config.SAMPLES_DIR / "manifest.json").read_text(encoding="utf-8"))


def reasons(result) -> set[str]:
    return {f.code for f in result.decision.reasons}


@pytest.mark.parametrize("sample", MANIFEST, ids=[s["id"] for s in MANIFEST])
def test_every_sample_gives_its_documented_decision(sample, pipeline):
    """Each sample on its own fresh database (prior history only)."""
    result = pipeline.run((config.SAMPLES_DIR / sample["file"]).read_bytes(), sample["file"], "test")
    assert result.verdict == sample["expected_verdict"]
    assert reasons(result) == set(sample["expected_reasons"])
    assert [s.key for s in result.stages] == [k for k, _ in STAGES]
    assert result.decision.explanation and result.decision.next_action and result.receipt["hash"]


def test_all_samples_in_demo_order_on_one_database(pipeline, store):
    verdicts = []
    for sample in MANIFEST:
        result = pipeline.run((config.SAMPLES_DIR / sample["file"]).read_bytes(), sample["file"], "test")
        verdicts.append((sample["id"], result.verdict, sample["expected_verdict"]))
    assert all(got == want for _, got, want in verdicts), verdicts
    assert len(store.list_runs()) == len(MANIFEST) and store.verify_chain() == {"ok": True, "count": len(MANIFEST), "problems": []}


def test_events_arrive_stage_by_stage(pipeline):
    events = [(kind, getattr(payload, "key", payload) if kind != "result" else "") for kind, payload in
              pipeline.iter_run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")]
    kinds = [k for k, _ in events]
    assert kinds[0] == "begin" and kinds[-1] == "result"
    middle = events[1:-1]
    assert [k for k, _ in middle] == ["start", "stage"] * 9
    assert [key for kind, key in middle if kind == "start"] == [k for k, _ in STAGES]     # 'start' precedes the work


def test_happy_path_posts_to_the_ledger_with_evidence_at_every_stage(pipeline, store):
    r = pipeline.run(sample_bytes("01_happy_path_furniture"), "01_happy_path_furniture.pdf", "test")
    assert r.verdict == "APPROVE" and all(s.status == "ok" for s in r.stages) and all(s.blocks for s in r.stages)
    entry = store.get_ledger(r.ledger_id)
    assert entry["status"] == "POSTED" and entry["invoice_no"] == "TOF/26-27/0412" and len(entry["lines"]) == 3
    assert store.po_usage("PO-2026-0147")["posted_amount"] == 381300
    assert r.receipt["verdict"] == "APPROVE" and r.receipt["rules"]["sha256"] == pipeline.rules.sha256
    assert store.load_file(r.file_sha256) == sample_bytes("01_happy_path_furniture")


def test_the_same_file_twice_is_rejected_the_second_time(pipeline, store):
    first = pipeline.run(sample_bytes("01_happy_path_furniture"), "a.pdf", "test")
    second = pipeline.run(sample_bytes("01_happy_path_furniture"), "renamed_copy.pdf", "test")
    assert first.verdict == "APPROVE" and second.verdict == "REJECT" and reasons(second) == {"DUPLICATE_FILE"}
    assert first.run_id in second.decision.explanation
    assert store.po_usage("PO-2026-0147")["posted_amount"] == 381300          # booked once
    assert store.get_ledger(second.ledger_id)["status"] == "REJECTED"


def test_a_held_invoice_sent_again_is_not_queued_twice(pipeline, store):
    first = pipeline.run(sample_bytes("09_fraud_guard_bank_change"), "a.pdf", "test")
    second = pipeline.run(sample_bytes("09_fraud_guard_bank_change"), "a.pdf", "test")
    assert first.verdict == "HOLD" and second.verdict == "REJECT" and "DUPLICATE_FILE" in reasons(second)
    assert [r["review_status"] for r in store.list_runs()] == ["", "PENDING"]


def test_split_po_story_in_sequence(pipeline, store):
    """E1: 16 of 20 units are posted history. A further 4 fit exactly; the bundled sample bills 5."""
    def invoice(number, qty):
        return G.render_invoice_pdf(G.spec(id="x", layout="grid", vendor="V004", invoice_no=number,
                                           invoice_date=G.date(2026, 11, 12), po_ref="PO-2026-0193", po_date=G.date(2026, 10, 12),
                                           lines=[G.line("Portable Multi-Gas Detector (4 Gas)", "9027", qty, "Nos", "25000.00")]))
    five = pipeline.run(invoice("ASS/2026/0933", 5), "five.pdf", "test")
    assert five.verdict == "HOLD" and reasons(five) == {"QTY_OVER_PO", "OVER_PO_BALANCE"}
    four = pipeline.run(invoice("ASS/2026/0934", 4), "four.pdf", "test")
    assert four.verdict == "APPROVE" and "fully billed" in four.decision.explanation
    one_more = pipeline.run(invoice("ASS/2026/0935", 1), "one.pdf", "test")
    assert one_more.verdict == "HOLD" and "21 of 20" in one_more.decision.explanation


def test_result_survives_a_json_round_trip(pipeline, store):
    r = pipeline.run(sample_bytes("06_E3_scan_no_number_implied_po"), "06.pdf", "test")
    again = RunResult.model_validate_json(r.model_dump_json())
    assert again.model_dump() == r.model_dump()
    stored = store.get_run(r.run_id)
    assert stored.verdict == "HOLD" and stored.decision.model_dump() == r.decision.model_dump()
    row = store.list_runs()[0]
    assert row["verdict"] == "HOLD" and row["scanned"] == 1 and row["review_status"] == "PENDING"
    assert set(row["reason_codes"].split(",")) == {"MISSING_INVOICE_NUMBER", "PO_INFERRED"}


def test_the_same_file_gives_the_same_decision_on_a_fresh_database(rules, master, tmp_path):
    outcomes = []
    for n in range(2):
        p = Pipeline(store=Store(tmp_path / f"{n}.db"), rules=rules, master=master, llm=LlmAssist(LLM_OFF), today=TODAY,
                     ocr_mode="fixture")
        r = p.run(sample_bytes("08_E4b_tax_inclusive_over_tolerance"), "08.pdf", "test")
        outcomes.append((r.verdict, r.decision.explanation, r.decision.vendor_draft,
                         [(f.code, f.expected, f.observed) for f in r.decision.reasons]))
    assert outcomes[0] == outcomes[1]


def test_file_names_are_reduced_to_a_plain_name(pipeline):
    r = pipeline.run(sample_bytes("01_happy_path_furniture"), "..\\..\\secret/../evil name.pdf", "test")
    assert r.file_name == "evil name.pdf"


# ---------------------------------------------------------------- a stage that fails

@pytest.mark.parametrize("target", ["match_po", "check_vendor", "validate", "check_duplicates"])
def test_an_unexpected_error_in_a_stage_holds_the_invoice(target, pipeline, monkeypatch):
    def boom(*args, **kwargs):
        raise ZeroDivisionError("simulated fault")
    monkeypatch.setattr(pipeline_module, target, boom)
    r = pipeline.run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    assert r.verdict == "HOLD" and "INTERNAL_ERROR" in reasons(r)
    assert [s.status for s in r.stages].count("error") == 1 and len(r.stages) == 9
    assert pipeline.store.get_ledger(r.ledger_id)["status"] == "PENDING"       # held, never posted for payment


def test_an_error_while_reading_skips_the_analysis_and_holds(pipeline, monkeypatch):
    monkeypatch.setattr(pipeline_module, "read_pdf", lambda *a, **k: (_ for _ in ()).throw(MemoryError("simulated")))
    r = pipeline.run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    assert r.verdict == "HOLD" and reasons(r) == {"INTERNAL_ERROR"}
    assert [s.status for s in r.stages[2:7]] == ["skipped"] * 5 and r.ledger_id is None


def test_an_error_in_decide_still_ends_in_a_hold(pipeline, monkeypatch):
    calls = {"n": 0}
    real = pipeline_module.decide
    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated")
        return real(*args, **kwargs)
    monkeypatch.setattr(pipeline_module, "decide", flaky)
    r = pipeline.run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    assert r.verdict == "HOLD" and "INTERNAL_ERROR" in reasons(r)


def test_a_ledger_write_that_fails_never_leaves_an_approval_standing(pipeline, store, monkeypatch):
    def broken(entry, lines):
        raise OSError("disk full (simulated)")
    monkeypatch.setattr(store, "add_ledger", broken)
    r = pipeline.run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    assert r.verdict == "HOLD" and reasons(r) == {"POSTING_FAILED"} and r.ledger_id is None
    assert store.po_usage("PO-2026-0147")["posted_amount"] == 0 and r.receipt["verdict"] == "HOLD"


def test_a_ledger_entry_that_reads_back_differently_is_not_treated_as_posted(pipeline, store, monkeypatch):
    real = store.get_ledger
    monkeypatch.setattr(store, "get_ledger", lambda ledger_id: dict(real(ledger_id), total=None) if real(ledger_id) else None)
    r = pipeline.run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    monkeypatch.undo()
    assert r.verdict == "HOLD" and reasons(r) == {"POSTING_FAILED"}
    assert store.get_ledger(r.ledger_id)["status"] == "PENDING" and store.po_usage("PO-2026-0147")["posted_amount"] == 0


# ---------------------------------------------------------------- inputs that must fail gracefully

def _pdf(pages: int, text: str = "Minutes of the canteen committee meeting") -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    for n in range(pages):
        c.drawString(72, 720, f"{text}, page {n + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def _encrypted_pdf() -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, encrypt="open-sesame")
    c.drawString(72, 720, "TAX INVOICE")
    c.save()
    return buf.getvalue()


PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489")


@pytest.mark.parametrize("name,content,verdict,code", [
    ("empty", b"", "REJECT", "UNREADABLE_FILE"),
    ("plain text", b"Dear AP team, please find the invoice attached.", "REJECT", "UNREADABLE_FILE"),
    ("png image renamed to pdf", PNG, "REJECT", "UNREADABLE_FILE"),
    ("word document", b"PK\x03\x04" + b"\x00" * 400, "REJECT", "UNREADABLE_FILE"),
    ("pdf header then garbage", b"%PDF-1.7\n" + bytes(range(256)) * 20, "REJECT", "UNREADABLE_FILE"),
    ("truncated pdf", sample_bytes("01_happy_path_furniture")[:700], "REJECT", "UNREADABLE_FILE"),
    ("password protected", _encrypted_pdf(), "REJECT", "UNREADABLE_FILE"),
    ("corrupt sample", sample_bytes("bad_inputs/corrupt_file.pdf"), "REJECT", "UNREADABLE_FILE"),
    ("25 pages", _pdf(25), "HOLD", "FILE_TOO_LARGE"),
    ("over 15 MB", b"%PDF-1.4\n" + b"0" * (15 * 1024 * 1024 + 1), "HOLD", "FILE_TOO_LARGE"),
    ("blank page", _pdf(1, ""), "REJECT", "NOT_AN_INVOICE"),
    ("20 pages of something else", _pdf(20), "REJECT", "NOT_AN_INVOICE"),
    ("quotation", sample_bytes("bad_inputs/quotation_not_invoice.pdf"), "REJECT", "NOT_A_TAX_INVOICE"),
])
def test_bad_inputs_fail_gracefully(name, content, verdict, code, pipeline, store):
    r = pipeline.run(content, f"{name}.pdf", "test")
    assert (r.verdict, reasons(r)) == (verdict, {code}), r.decision.explanation
    assert len(r.stages) == 9 and r.ledger_id is None and r.receipt["hash"]       # recorded, nothing booked
    assert store.get_run(r.run_id).verdict == verdict and store.verify_chain()["ok"]
    assert r.decision.explanation and not r.decision.explanation.startswith("Traceback")


def test_random_bytes_never_crash_the_pipeline(pipeline):
    rng = random.Random(2026)
    base = sample_bytes("01_happy_path_furniture")
    blobs = [bytes(rng.getrandbits(8) for _ in range(rng.choice([1, 17, 300, 5000]))) for _ in range(12)]
    for _ in range(12):                                   # real PDFs with random damage in the middle
        cut, size = rng.randrange(20, len(base) - 400), rng.choice([1, 8, 64, 300])
        blobs.append(base[:cut] + bytes(rng.getrandbits(8) for _ in range(size)) + base[cut + size:])
    for blob in blobs:
        r = pipeline.run(blob, "fuzz.pdf", "test")
        assert r.verdict in {"APPROVE", "HOLD", "REJECT"} and len(r.stages) == 9
        assert "INTERNAL_ERROR" not in reasons(r), r.decision.explanation


def test_a_scan_with_no_ocr_engine_and_no_recording_is_held_with_instructions(pipeline, monkeypatch):
    spec = G.spec(id="x", layout="grid", vendor="V004", invoice_no="ASS/2026/0990", invoice_date=G.date(2026, 11, 18),
                  po_ref="PO-2026-0201", po_date=G.date(2026, 11, 5),
                  lines=[G.line("Fire Extinguisher ABC Type 6 kg", "8424", 2, "Nos", "2150.00")])
    scan = G.to_scan(G.render_invoice_pdf(spec), angle=0.4, noise=4.0, seed=3)
    missing = types.SimpleNamespace(available=False, label="not installed", version="")
    monkeypatch.setattr(reader, "get_engine", lambda refresh=False: missing)
    monkeypatch.setattr(pipeline_module, "get_engine", lambda refresh=False: missing)
    r = pipeline.run(scan, "unknown_scan.pdf", "test")
    assert r.verdict == "HOLD" and reasons(r) == {"SCAN_NOT_READ"}
    assert "Tesseract" in r.decision.questions[0] and r.pages[0]["kind"] == "scan_unread"


def test_recorded_ocr_lets_the_bundled_scans_run_without_an_engine(pipeline, monkeypatch):
    missing = types.SimpleNamespace(available=False, label="not installed", version="")
    monkeypatch.setattr(reader, "get_engine", lambda refresh=False: missing)
    monkeypatch.setattr(pipeline_module, "get_engine", lambda refresh=False: missing)
    pipeline.ocr_mode = "auto"
    r = pipeline.run(sample_bytes("02_scanned_clean_safety"), "02.pdf", "test")
    assert r.verdict == "APPROVE" and r.pages[0]["ocr_source"] == "recorded"
    assert "recorded" in r.stages[1].summary                                     # and the screen says so


@pytest.mark.skipif(not reader.get_engine().available, reason="Tesseract is not installed")
def test_a_scan_never_seen_before_is_read_live(rules, master, store):
    spec = G.spec(id="x", layout="classic", vendor="V001", invoice_no="TOF/26-27/0466", invoice_date=G.date(2026, 11, 18),
                  po_ref="PO-2026-0152", po_date=G.date(2026, 10, 28),
                  lines=[G.line("Visitor Chair, Cantilever Frame", "9401", 6, "Nos", "2950.00")])
    scan = G.to_scan(G.render_invoice_pdf(spec), angle=1.1, noise=5.0, seed=5)
    p = Pipeline(store=store, rules=rules, master=master, llm=LlmAssist(LLM_OFF), today=TODAY, ocr_mode="live")
    r = p.run(scan, "fresh_scan.pdf", "test")
    assert r.pages[0]["ocr_source"] == "live" and abs(r.pages[0]["angle"]) > 0.5
    assert r.invoice.reads["reads"] == 2                                   # read twice and reconciled
    # on any OCR engine: approved only if read right; otherwise held for a reading reason, never rejected
    assert r.verdict in {"APPROVE", "HOLD"} and reasons(r) <= READING_HOLDS, r.decision.explanation
    if r.verdict == "APPROVE":
        assert r.invoice.get("total") == "20886.00" and r.invoice.get("po_reference") == "PO-2026-0152"
    if STRICT_OCR:
        assert r.verdict == "APPROVE"


@pytest.mark.skipif(not reader.get_engine().available, reason="Tesseract is not installed")
@pytest.mark.parametrize("sample", [s for s in MANIFEST if s["scanned"]], ids=[s["id"] for s in MANIFEST if s["scanned"]])
def test_bundled_scans_through_the_live_engine_are_right_or_held(sample, rules, master, store):
    p = Pipeline(store=store, rules=rules, master=master, llm=LlmAssist(LLM_OFF), today=TODAY, ocr_mode="live")
    r = p.run((config.SAMPLES_DIR / sample["file"]).read_bytes(), sample["file"], "test")
    documented = (r.verdict == sample["expected_verdict"] and reasons(r) == set(sample["expected_reasons"]))
    assert documented or (r.verdict == "HOLD" and reasons(r) <= READING_HOLDS), r.decision.explanation
    if STRICT_OCR:
        assert documented


def test_a_page_with_absurd_dimensions_is_rendered_at_a_safe_size(pipeline):
    """A single 200 x 200 inch page must not ask for a multi-gigabyte bitmap, at OCR or in the preview."""
    from PIL import Image
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    side = 14400
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(side, side), invariant=1)
    picture = Image.new("L", (400, 400), 255)
    c.drawImage(ImageReader(picture), 0, 0, width=side, height=side)       # one picture covering the page: looks like a scan
    c.save()
    r = pipeline.run(buf.getvalue(), "poster.pdf", "test")
    assert r.verdict in {"HOLD", "REJECT"} and "INTERNAL_ERROR" not in reasons(r)
    image = reader.render_page_image(buf.getvalue(), 1, scale=1.6)
    assert max(image.size) <= reader.MAX_RENDER_PX + 1


def test_a_two_page_invoice_is_matched_line_by_line(rules, master, store):
    """13 items over two pages against a PO with the same 13 lines: read, matched and approved."""
    from decimal import Decimal
    from invoice_desk.masterdata import PoLine, PurchaseOrder
    items = [G.line(f"{desc} lot {i}", hsn, (i * 7) % 23 + 1, uom, f"{100 + i * 37.25:.2f}")
             for i, (desc, hsn, uom) in enumerate(G.VARIANT_ITEMS * 2, 1)][:13]
    spec = G.spec(id="long", layout="classic", vendor="V001", invoice_no="TOF/26-27/0500", invoice_date=G.date(2026, 11, 18),
                  po_ref="PO-2026-0700", po_date=G.date(2026, 10, 20), lines=items)
    truth = G.truth_for(spec)
    register = master.model_copy(deep=True)
    register.pos["PO-2026-0700"] = PurchaseOrder(
        po_number="PO-2026-0700", po_date=G.date(2026, 10, 20), vendor_id="V001", status="OPEN", buyer="R. Iyer (Procurement)",
        lines=[PoLine(line_no=n, description=ln["description"], uom=ln["uom"], qty_ordered=Decimal(str(ln["qty"])),
                      unit_price=Decimal(str(ln["unit_price_pre_tax"])), line_amount=Decimal(str(ln["amount_pre_tax"])),
                      qty_received=Decimal(str(ln["qty"]))) for n, ln in enumerate(truth["lines"], 1)])
    p = Pipeline(store=store, rules=rules, master=register, llm=LlmAssist(LLM_OFF), today=TODAY, ocr_mode="fixture")
    r = p.run(G.render_multipage_invoice_pdf(spec, carry_forward=True), "two_pages.pdf", "test")
    assert r.page_count == 2 and len(r.invoice.line_items) == 13
    assert r.verdict == "APPROVE", r.decision.explanation
    assert r.po["lines_matched"] == 13 and Decimal(r.po["remaining_after"]) == 0
    assert [p["number"] for p in r.pages] == [1, 2]


# ---------------------------------------------------------------- what the words say

def test_a_blank_number_box_is_the_vendors_to_fix_but_an_unread_document_is_ours(pipeline):
    blank = pipeline.run(sample_bytes("06_E3_scan_no_number_implied_po"), "06.pdf", "test")
    number = next(f for f in blank.decision.reasons if f.code == "MISSING_INVOICE_NUMBER")
    assert "left blank" in number.summary and "invoice number is left blank" in blank.decision.vendor_draft
    letter = pipeline.run(sample_bytes("10_ai_assist_letter_invoice"), "10.pdf", "test")
    number = next(f for f in letter.decision.reasons if f.code == "MISSING_INVOICE_NUMBER")
    assert number.summary == "No invoice number could be read from this document." and number.vendor_issue is None
    assert letter.decision.vendor_draft is None            # nothing is the vendor's fault here, so nothing is drafted
    assert letter.stages[5].summary == "PO-2026-0178 (quoted on the invoice) found, but there are no line items to compare with it"


def test_the_vendor_note_fits_what_was_received(pipeline):
    quotation = pipeline.run(sample_bytes("bad_inputs/quotation_not_invoice.pdf"), "q.pdf", "test").decision.vendor_draft
    assert quotation.startswith("Subject: Document received in our invoice inbox: not booked")
    assert "Dear Tungabhadra Office Furnishings Pvt Ltd accounts team," in quotation and "your invoice" not in quotation
    assert "The document received is a quotation" in quotation and "\u20b9" not in quotation
    corrupt = pipeline.run(sample_bytes("bad_inputs/corrupt_file.pdf"), "c.pdf", "test").decision.vendor_draft
    assert "Dear Sir or Madam," in corrupt and "accounts team" not in corrupt


def test_a_service_instalment_is_described_as_matched_on_amount(pipeline):
    r = pipeline.run(sample_bytes("03_monthly_service_instalment"), "03.pdf", "test")
    assert "agree with the PO on item and amount" in r.decision.explanation and "remains open" in r.decision.explanation
    goods = pipeline.run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    assert "agree with the PO on item, quantity and price" in goods.decision.explanation


def test_every_hold_is_owned_by_someone_inside_the_company(rules):
    owners = {r.owner for r in rules.reasons.values() if r.action != "NOTE"}
    assert owners == {"AP analyst", "Procurement", "Vendor master team", "Tax team", "Finance controller", "Stores"}
