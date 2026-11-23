"""The independent test set (robustness/) as a regression test.

`python -m scripts.evaluate_robustness --write` measured every document and wrote the
result to robustness/results.json and docs/robustness_report.md. This file runs the same
measurement again (photographs from the recorded OCR output, so no OCR engine is needed)
and fails if anything moved:

  * no document may be approved with a wrong value, and none that should be stopped may
    be approved (the one number that must stay at 0);
  * every document must land in the class the report says it did, with the same decision;
  * the report on disk must be the report these results produce.

So the report cannot drift from the code: a change to the reader that moves a document
shows up here, and the report has to be regenerated and read before the test passes.
"""
import hashlib
import json

import pytest

from invoice_desk import reader
from scripts import evaluate_robustness as ER


@pytest.fixture(scope="module")
def records():
    return ER.load_set()


@pytest.fixture(scope="module")
def rows(records):
    return ER.run(records, ocr_mode="fixture", llm_mode="off")


@pytest.fixture(scope="module")
def published():
    return json.loads(ER.RESULTS.read_text(encoding="utf-8"))


def test_the_set_is_the_one_the_truth_file_describes(records):
    assert len(records) == 49 and len({r["id"] for r in records}) == 49
    assert ER.check_files(records) == []                       # byte for byte: the OCR recordings are filed by file hash
    assert {r["split"] for r in records} == {"tune", "holdout"}
    tools = {r["tool"].split(",")[0] for r in records}
    assert tools == {"chrome", "wkhtmltopdf", "libreoffice calc", "libreoffice writer", "pdflatex"}
    assert sum(r["kind"] == "scan" for r in records) == 12


def test_every_photograph_has_both_reads_recorded(records):
    for record in records:
        if record["kind"] == "scan":
            for variant in reader.OCR_VARIANTS:
                assert reader.load_ocr_fixture(record["sha256"], 1, variant), f"{record['id']} {variant}"


def test_nothing_from_the_sample_generator_is_in_the_set(records):
    """Different tools and different vendors: no document of the set was made by ReportLab,
    which draws the bundled samples, and no vendor of the set is in the app's master data."""
    from invoice_desk.masterdata import load_master
    master = load_master()
    for record in records:
        head = ER.content_of(record)[:4096] + ER.content_of(record)[-4096:]
        assert b"ReportLab" not in head, record["id"]
        if record["vendor"]:
            assert record["vendor"]["gstin"] not in master.gstin_index
    assert not {r["po"]["po_number"] for r in records if r["po"]} & set(master.pos)


def test_no_document_is_approved_wrongly(rows):
    wrong = [(r["id"], r["why"], r["wrong"]) for r in rows if r["class"] == "misread-approved"]
    assert wrong == []


def test_every_document_lands_where_the_report_says(rows, published):
    moved = {r["id"]: (r["class"], r["verdict"], r["reasons"]) for r in rows
             if (r["class"], r["verdict"], r["reasons"]) != (published["documents"][r["id"]]["class"],
                                                           published["documents"][r["id"]]["verdict"],
                                                           published["documents"][r["id"]]["reasons"])}
    assert moved == {}, "run `python -m scripts.evaluate_robustness --write`, read the report, then commit both"
    assert ER.summary(rows) == published["summary"]


def test_every_value_that_differs_from_the_page_is_in_the_report(rows, published):
    assert {r["id"]: r["wrong"] for r in rows if r["wrong"]} == {k: v["wrong"] for k, v in published["documents"].items() if v["wrong"]}


def test_the_holdout_split_is_held_to_the_same_standard(rows):
    """The fair measure: documents nobody debugged. Text PDFs all reach the right answer;
    what fails among the photographs is held, never approved."""
    holdout = [r for r in rows if r["split"] == "holdout"]
    text = [r for r in holdout if r["kind"] == "text"]
    assert len(holdout) == 17 and len(text) == 12
    assert all(r["class"] in ("approved-correct", "held-correct") for r in text)
    assert all(r["class"] != "misread-approved" for r in holdout)
    assert sum(r["class"] in ("approved-correct", "held-correct") for r in holdout) >= 15


def test_the_report_on_disk_was_generated_from_these_results(rows, published):
    text = ER.REPORT.read_text(encoding="utf-8")
    totals = published["summary"]["All documents"]
    line = (f"| All documents | {totals['documents']} | {totals['approved-correct']} | {totals['held-correct']} | "
            f"{totals['misread-held']} | {totals['false_alarms']} | **{totals['misread-approved']}** |")
    assert line in text
    for row in rows:
        assert f"| `{row['id']}` | {row['split']} |" in text
    baseline = json.loads(ER.BASELINE.read_text(encoding="utf-8"))["summary"]["All documents"]
    assert f"| All documents | 49 | {baseline['approved-correct']} | {baseline['held-correct']} | {baseline['misread-held']} |" in text
    assert baseline["approved-correct"] + baseline["held-correct"] < totals["approved-correct"] + totals["held-correct"]


def test_the_change_notes_do_not_claim_more_than_the_numbers(published):
    """The hand-written part names the tune documents that still fail; they must be the ones that do."""
    notes = ER.CHANGES.read_text(encoding="utf-8")
    failing_tune = sorted(k for k, v in published["documents"].items()
                          if v["class"] == "misread-held" and k.startswith(("photo_tally", "photo_zoho", "photo_excel",
                                                                           "photo_sideways", "photo_hand", "tally", "zoho",
                                                                           "excel", "retail", "lump", "usd")))
    for doc_id in failing_tune:
        assert f"`{doc_id}`" in notes, doc_id


def test_the_set_runs_from_the_command_line_and_reports_zero(capsys):
    assert ER.main(["--ocr", "fixture", "--only", "tally_01", "busy_01", "photo_sideways"]) == 0
    out = capsys.readouterr().out
    assert "0 misread-approved" in out and "approved-correct" in out
    with pytest.raises(SystemExit, match="Not in the set: no_such_document"):
        ER.main(["--only", "no_such_document"])
    assert ER.main(["--write", "--only", "tally_01"]) == 1          # a report needs the whole set


def test_a_changed_document_is_refused(tmp_path, monkeypatch, records):
    """The truth file and the OCR recordings belong to exact files; a rebuilt or edited PDF is named, not measured."""
    monkeypatch.setattr(ER, "content_of", lambda record: b"%PDF-1.4 something else")
    assert ER.check_files(records[:2]) == [records[0]["id"], records[1]["id"]]
    assert hashlib.sha256(b"x").hexdigest() != records[0]["sha256"]
