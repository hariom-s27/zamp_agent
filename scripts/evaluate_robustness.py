"""Measure the whole pipeline on the independent test set in robustness/.

    python -m scripts.evaluate_robustness                 # run every document, print the result
    python -m scripts.evaluate_robustness --write         # also write docs/robustness_report.md and robustness/results.json
    python -m scripts.evaluate_robustness --only tally_01 # one document, with every value that differs
    python -m scripts.evaluate_robustness --ocr live      # read the photographs with the installed Tesseract
    python -m scripts.evaluate_robustness --record-ocr    # record OCR output for the photographs (needs Tesseract)
    python -m scripts.evaluate_robustness --llm cached-live   # measure what the Claude assist adds (uses your key and the caps)

The set was made by robustness/build_set.py with other tools, fonts and layouts than the
bundled samples (Chrome, wkhtmltopdf, LibreOffice Calc and Writer, LaTeX, OpenCV for the
photographs). Each document comes with what a perfect reader would report, the purchase
order it should be matched against, and the outcome that is right for it.

Every document is run on fresh books, with the rules exactly as shipped and the model
switched off, and lands in one of four classes:

  approved-correct   a correct invoice, approved, with every value read as printed
  held-correct       a document that should be stopped, stopped for the documented reason
                     (and, where a full read is expected, with every value read as printed)
  misread-held       the right answer was not reached, but the document went to a person
  misread-approved   a wrong value got through, or a document that should have been
                     stopped was approved. This is the class that costs money. It must be 0.

Two splits. TUNE: problems found here were used to improve the reader. HOLDOUT: other
tools and layout families, not looked at until the final measurement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from invoice_desk import config, reader
from invoice_desk import textutil as T
from invoice_desk.llm import LlmAssist
from invoice_desk.masterdata import MasterData, PoLine, PurchaseOrder, Vendor, load_master
from invoice_desk.pipeline import Pipeline
from invoice_desk.rules import load_rules
from invoice_desk.store import Store

SET_DIR = config.ROOT / "robustness"
REPORT = config.ROOT / "docs" / "robustness_report.md"
RESULTS = SET_DIR / "results.json"
BASELINE = SET_DIR / "baseline.json"
CHANGES = SET_DIR / "CHANGES.md"
TODAY = date(2026, 11, 21)                  # the business date every document is processed on
CLASSES = ("approved-correct", "held-correct", "misread-held", "misread-approved")
HEADER = (("invoice_number", "invoice_number"), ("invoice_date", "invoice_date"), ("po_reference", "po_reference"),
          ("vendor_gstin", "vendor_gstin"), ("bank_account", "bank_account"))
MONEY = (("subtotal", "taxable"), ("total", "total"))
# Documents that are stopped for what they are (a credit note, a pro-forma, a dollar invoice)
# are not read in full, so only the reason is checked for them.
STOPPED_ON_SIGHT = {"UNSUPPORTED_DOCUMENT", "NOT_A_TAX_INVOICE", "NOT_AN_INVOICE", "CURRENCY_NOT_SUPPORTED"}


def load_set(only: Optional[list[str]] = None) -> list[dict]:
    records = json.loads((SET_DIR / "truth.json").read_text(encoding="utf-8"))
    if only:
        unknown = sorted(set(only) - {r["id"] for r in records})
        if unknown:
            raise SystemExit(f"Not in the set: {', '.join(unknown)}")
        records = [r for r in records if r["id"] in only]
    return records


def content_of(record: dict) -> bytes:
    return (SET_DIR / record["file"]).read_bytes()


def register(master: MasterData, record: dict) -> MasterData:
    """The app's master data plus this document's vendor and purchase order. A document whose
    vendor is deliberately not on the approved list gets neither."""
    books = master.model_copy(deep=True)
    vendor, po = record.get("vendor"), record.get("po")
    if not vendor:
        return books
    books.vendors[vendor["vendor_id"]] = Vendor(
        vendor_id=vendor["vendor_id"], legal_name=vendor["legal_name"], gstin=vendor["gstin"],
        state_code=vendor["state_code"], status="APPROVED", bank_name=vendor["bank_name"],
        bank_account=vendor["bank_account"], ifsc=vendor["ifsc"], phone_on_file=vendor["phone"], email=vendor["email"])
    if po:
        lines = []
        for n, ln in enumerate(po["lines"], 1):
            qty, price = Decimal(ln["qty"]), Decimal(ln["unit_price"])
            lines.append(PoLine(line_no=n, description=ln["description"], hsn_sac=ln.get("hsn") or "", uom=ln.get("uom") or "",
                                qty_ordered=qty, unit_price=price, line_amount=T.q2(qty * price), qty_received=qty))
        books.pos[po["po_number"]] = PurchaseOrder(po_number=po["po_number"], po_date=date.fromisoformat(po["po_date"]),
                                                   vendor_id=vendor["vendor_id"], status="OPEN", buyer="Robustness set",
                                                   lines=lines)
    return books


def _same_money(got, want) -> bool:
    got, want = T.to_decimal(got), T.to_decimal(want)
    if got is None or want is None:
        return got is None and want is None
    return abs(got - want) <= Decimal("0.01")


def compare(inv, truth: dict) -> list[list[str]]:
    """[[field, read as, printed]] for every value that differs from what is on the page."""
    wrong: list[list[str]] = []
    for field, key in HEADER:
        got, want = inv.get(field) or None, truth.get(key) or None
        if got != want:
            wrong.append([field, str(got), str(want)])
    for field, key in MONEY:
        if not _same_money(inv.get(field), truth[key]):
            wrong.append([field, str(inv.get(field)), str(truth[key])])
    if len(inv.line_items) != len(truth["lines"]):
        wrong.append(["line count", str(len(inv.line_items)), str(len(truth["lines"]))])
        return wrong
    for n, (item, want) in enumerate(zip(inv.line_items, truth["lines"]), 1):
        keys = ("qty", "unit_price", "amount") if want["qty"] is not None else ("amount",)     # a lump-sum line prints only an amount
        for key in keys:
            if not _same_money(getattr(item, key), want[key]):
                wrong.append([f"line {n} {key}", str(getattr(item, key)), str(want[key])])
    return wrong


def values_expected(record: dict) -> int:
    lines = record["truth"]["lines"]
    return len(HEADER) + len(MONEY) + sum(3 if ln["qty"] is not None else 1 for ln in lines)


def full_read_expected(record: dict) -> bool:
    return record["expect"]["reason"] not in STOPPED_ON_SIGHT


def classify(record: dict, verdict: str, reasons: list[str], wrong: Optional[list]) -> tuple[str, str]:
    """(class, one line saying why). `wrong` is None when no full read is expected."""
    want, reason = record["expect"]["verdict"], record["expect"]["reason"]
    misread = bool(wrong)
    if want == "APPROVE":
        if verdict == "APPROVE":
            return ("misread-approved", "approved with a wrong value") if misread else ("approved-correct", "")
        if misread:
            return "misread-held", "a value was misread or not read; held for a person"
        return "misread-held", "read correctly, but held anyway (a false alarm)"
    if verdict == "APPROVE":
        return "misread-approved", f"should have been stopped ({reason}) and was approved"
    if verdict == want and reason in reasons and not misread:
        return "held-correct", ""
    if reason in reasons and verdict == want:
        return "misread-held", "stopped for the right reason, but a value was also misread"
    return "misread-held", f"stopped, but not for the documented reason ({reason})"


def evaluate(record: dict, master: MasterData, rules, folder: Path, llm: LlmAssist, ocr_mode: str) -> dict:
    content = content_of(record)
    store = Store(folder / f"{record['id']}.db")              # fresh books for every document
    pipeline = Pipeline(store=store, rules=rules, master=register(master, record), llm=llm, today=TODAY, ocr_mode=ocr_mode)
    result = pipeline.run(content, f"{record['id']}.pdf", "batch")
    inv = result.invoice
    decision = result.decision
    reasons = sorted({f.code for f in decision.reasons}) if decision else []
    notes = sorted({f.code for f in decision.notes}) if decision else []
    wrong = None
    if full_read_expected(record):
        wrong = compare(inv, record["truth"]) if inv is not None else [["document", "not read", "an invoice"]]
    outcome, why = classify(record, result.verdict, reasons, wrong)
    unread = any(p.get("kind") == "scan_unread" for p in result.pages)
    return {"id": record["id"], "split": record["split"], "kind": record["kind"], "family": record["family"],
            "tool": record["tool"], "font": record["font"], "quirks": record["quirks"],
            "expected": f"{record['expect']['verdict']}" + (f" ({record['expect']['reason']})" if record["expect"]["reason"] else ""),
            "verdict": result.verdict, "reasons": reasons, "notes": notes, "class": outcome, "why": why,
            "wrong": wrong or [], "values": values_expected(record) if wrong is not None else 0,
            "ocr_missing": unread, "llm_used": bool(inv is not None and inv.llm and inv.llm.get("called")),
            "seconds": round(sum(s.duration_ms for s in result.stages[:3]) / 1000, 2)}


def tally(rows: list[dict]) -> dict:
    counts = Counter(r["class"] for r in rows)
    held = [r for r in rows if r["class"] == "misread-held"]
    values = sum(r["values"] for r in rows)
    return {"documents": len(rows), **{c: counts[c] for c in CLASSES},
            "false_alarms": sum("false alarm" in r["why"] for r in held),
            "values": values, "values_correct": values - sum(len(r["wrong"]) for r in rows if r["values"])}


def groups(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    out = []
    for split in ("tune", "holdout"):
        for kind, name in (("text", "text PDFs"), ("scan", "photographs")):
            part = [r for r in rows if r["split"] == split and r["kind"] == kind]
            if part:
                out.append((f"{split.capitalize()}, {name}", part))
    return out


def summary(rows: list[dict]) -> dict:
    """The numbers the report and the regression test are built on."""
    out = {name: tally(part) for name, part in groups(rows)}
    for split in ("tune", "holdout"):
        part = [r for r in rows if r["split"] == split]
        if part:
            out[f"{split.capitalize()}, all"] = tally(part)
    out["All documents"] = tally(rows)
    return out


def _table(title_a: str, totals: dict) -> list[str]:
    lines = [f"| {title_a} | Documents | Approved, correct | Held, correct | Misread, held | of which false alarms | "
             "Misread, approved | Values read as printed |", "|---|---|---|---|---|---|---|---|"]
    for name, t in totals.items():
        share = f"{t['values_correct']} of {t['values']} ({t['values_correct'] / t['values']:.1%})" if t["values"] else "-"
        lines.append(f"| {name} | {t['documents']} | {t['approved-correct']} | {t['held-correct']} | {t['misread-held']} | "
                     f"{t['false_alarms']} | **{t['misread-approved']}** | {share} |")
    return lines


def report(rows: list[dict], baseline: Optional[dict], facts: dict) -> str:
    totals = summary(rows)
    families = Counter(r["family"] for r in rows)
    names = {"chrome": "Chrome (headless)", "libreoffice calc": "LibreOffice Calc", "libreoffice writer": "LibreOffice Writer",
             "pdflatex": "LaTeX", "wkhtmltopdf": "wkhtmltopdf"}
    tools = sorted({names.get(r["tool"].split(",")[0], r["tool"].split(",")[0]) for r in rows})
    lines = [
        "# Robustness report: invoices the samples' generator did not make", "",
        "Generated by `python -m scripts.evaluate_robustness --write`. The numbers and tables are written by the "
        "script. The one hand-written part is marked.", "",
        "## What was measured", "",
        f"- **{len(rows)} documents** in `robustness/documents/`, made by `robustness/build_set.py` with "
        f"{len(tools)} tools the sample generator does not use ({', '.join(tools)}), then OpenCV for "
        f"{sum(r['kind'] == 'scan' for r in rows)} photographs of printed pages (perspective, tilt, uneven light, "
        "soft focus, noise, JPEG; one page on its side, one total written by hand, one fax).",
        "- Layout families: " + ", ".join(f"{name} ({n})" for name, n in sorted(families.items())) + ". The quirks of "
        "each document are listed in the table at the end.",
        "- Every document carries its ground truth: the values printed on it, the purchase order it belongs to, and "
        "the outcome that is right for it. " + f"{sum(r['expected'].startswith('APPROVE') for r in rows)} are correct "
        f"invoices that should be approved; {sum(not r['expected'].startswith('APPROVE') for r in rows)} should be "
        "stopped (price over tolerance, quantity over the PO, a total that does not add up, a changed bank account, "
        "a mistyped GSTIN, an unknown vendor, no PO quoted, a partial lump-sum bill, a credit note, a pro-forma, a "
        "dollar invoice).",
        f"- Each one runs through the whole pipeline on fresh books, business date {TODAY.isoformat()}, rules as "
        f"shipped (version {facts['rules_version']}), {facts['llm']}. {facts['ocr']}",
        "- **Tune** documents were opened and debugged, and what failed there was used to improve the reader. "
        "**Holdout** documents use other tools and layout families (wkhtmltopdf, LibreOffice Writer, LaTeX, a long "
        "invoice). They were never opened or debugged and nothing was changed to fix one (the hand-written section "
        "below states the one caveat). The holdout rows are the fair measure.", "",
        "## The four classes", "",
        "- **Approved, correct**: a correct invoice, approved, with every value read as printed.",
        "- **Held, correct**: a document that should be stopped, stopped for the documented reason, with every value "
        "read as printed where a full read is expected.",
        "- **Misread, held**: the right answer was not reached, but the document went to a person. This costs a few "
        "minutes of someone's time. *False alarms* are the ones that were read correctly and held anyway.",
        "- **Misread, approved**: a wrong value got through, or a document that should have been stopped was "
        "approved. This is the class that costs money, and the design goal is that it stays at 0.", "",
        "## Result", ""]
    lines += _table("Final", totals)
    if baseline:
        lines += ["", f"Before any change to the reader (measured {baseline.get('measured_on', 'earlier')}, the reader "
                      "exactly as it stood when the set was first run):", ""]
        lines += _table("Baseline", baseline["summary"])
    silent = [r for r in rows if r["class"] == "misread-approved"]
    lines += ["", "### Misread, approved", ""]
    if silent:
        lines += [f"- `{r['id']}` ({r['split']}): {r['why']}. " + "; ".join(f"{w[0]} read as `{w[1]}`, printed `{w[2]}`" for w in r["wrong"])
                  for r in silent]
    else:
        lines.append("None. That is a result for these documents, not a guarantee for every invoice in the world. It "
                     "holds here because a value is only trusted when something independent confirms it (the "
                     "arithmetic of the invoice, the amount in words, the purchase order, the vendor master, or two "
                     "reads of a scan agreeing), and anything unconfirmed sends the invoice to a person.")
    if CHANGES.is_file():
        lines += ["", "## What was changed between the two measurements (written by hand)", "",
                  CHANGES.read_text(encoding="utf-8").strip()]
    held = [r for r in rows if r["class"] == "misread-held"]
    lines += ["", "## Every document that did not reach the right answer", ""]
    if held or silent:
        lines += ["| Document | Split | Should be | Was | Reasons given | What went wrong |", "|---|---|---|---|---|---|"]
        for r in held + silent:
            detail = r["why"]
            if r["wrong"]:
                detail += ": " + "; ".join(f"{w[0]} read as `{w[1]}`, printed `{w[2]}`" for w in r["wrong"][:6])
                if len(r["wrong"]) > 6:
                    detail += f"; and {len(r['wrong']) - 6} more"
            lines.append(f"| `{r['id']}` | {r['split']} | {r['expected']} | {r['verdict']} | {', '.join(r['reasons']) or '-'} | {detail} |")
    else:
        lines.append("None.")
    lines += ["", "## Every document", "",
              "| Document | Split | Made with | Font | Quirks | Should be | Was | Class |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| `{r['id']}` | {r['split']} | {r['tool']} | {r['font']} | {'; '.join(r['quirks'])} | {r['expected']} | "
                     f"{r['verdict']}{' (' + ', '.join(r['reasons']) + ')' if r['reasons'] else ''} | {r['class']} |")
    lines += ["", "## How to read this, and its limits", "",
              "- These are still made-up invoices. They were built to look like what Indian accounting packages print "
              "and to differ from the samples in tool, font and structure, but nobody outside this project chose them. "
              "A pilot on a customer's own last 200 invoices is the measurement that would count, and this script is "
              "the harness for it: put the PDFs in a folder with a truth file of the same shape.",
              "- The photographs are synthetic: a rendered page pushed through perspective, tilt, shadow, blur, noise "
              "and JPEG compression. Real phone photos also have folds, stamps, staples and handwriting over print.",
              "- The set is small. One document is about 2 percentage points on a split, so read the counts, not "
              "percentages.",
              "- The class that matters is misread-approved. Held documents cost reviewer minutes; an approved wrong "
              "value costs money."]
    return "\n".join(lines) + "\n"


def record_ocr(records: list[dict]) -> int:
    """Record what Tesseract reads on each photograph, both reads, into fixtures/ocr/."""
    engine = reader.get_engine(refresh=True)
    if not engine.available:
        print("Tesseract is not installed, so nothing can be recorded.")
        return 1
    count = 0
    for record in records:
        if record["kind"] != "scan":
            continue
        content = content_of(record)
        for variant in reader.OCR_VARIANTS:
            doc = reader.read_pdf(content, record_ocr=True, ocr_mode="live", variant=variant)
            count += sum(p.kind == "scan" for p in doc.pages)
        print(f"recorded {record['id']}")
    print(f"{count} page read(s) recorded into {config.FIXTURES_DIR / 'ocr'} ({engine.label})")
    return 0


def check_files(records: list[dict]) -> list[str]:
    """Documents whose bytes differ from the ones the truth file (and the OCR recordings) belong to."""
    return [r["id"] for r in records if hashlib.sha256(content_of(r)).hexdigest() != r.get("sha256")]


def run(records: list[dict], ocr_mode: str = "auto", llm_mode: str = "off", progress=None) -> list[dict]:
    master, rules = load_master(), load_rules()
    llm = LlmAssist(config.LlmConfig(mode="off") if llm_mode == "off" else config.llm_config(llm_mode))
    folder = Path(tempfile.mkdtemp(prefix="invoice_desk_robustness_"))
    rows = []
    try:
        for n, record in enumerate(records, 1):
            rows.append(evaluate(record, master, rules, folder, llm, ocr_mode))
            if progress:
                progress(n, len(records), rows[-1])
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return rows


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Measure the pipeline on the independent test set.")
    parser.add_argument("--only", nargs="+", metavar="ID", help="run only these documents and show every difference")
    parser.add_argument("--split", choices=("tune", "holdout"), help="run one split only")
    parser.add_argument("--ocr", choices=("auto", "live", "fixture"), default="auto",
                        help="how the photographs are read: auto = Tesseract if installed, else the recordings (default); "
                             "live = Tesseract; fixture = the recordings, so the numbers are the same on every machine")
    parser.add_argument("--llm", choices=("off", "fixture", "cached-live"), default="off",
                        help="off = rules only (default, and what the report shows); the other two let the Claude assist fill gaps")
    parser.add_argument("--write", action="store_true", help="write docs/robustness_report.md and robustness/results.json")
    parser.add_argument("--save-baseline", action="store_true", help="store this run as the 'before' measurement")
    parser.add_argument("--record-ocr", action="store_true", help="record OCR output for the photographs and stop")
    args = parser.parse_args(argv)
    records = load_set(args.only)
    if args.split:
        records = [r for r in records if r["split"] == args.split]
    changed = check_files(records)
    if changed:
        print(f"These files are not the ones the truth file describes: {', '.join(changed)}")
        return 1
    if args.record_ocr:
        return record_ocr(records)
    if (args.write or args.save_baseline) and (args.only or args.split or args.llm != "off"):
        print("--write and --save-baseline need the whole set, with the model off.")
        return 1
    engine = reader.get_engine()
    ocr_mode = args.ocr
    if ocr_mode == "live" and not engine.available:
        print("Tesseract is not installed. Use --ocr fixture to replay the recorded OCR output.")
        return 1

    def progress(n: int, total: int, row: dict) -> None:
        print(f"\r{n}/{total} {row['id']:<20}", end="", flush=True)
    rows = run(records, ocr_mode, args.llm, progress)
    print("\r" + " " * 40 + "\r", end="")
    for r in rows:
        if r["class"] in ("misread-held", "misread-approved") or args.only:
            print(f"{r['id']:<18} {r['split']:<8} want {r['expected']:<32} got {r['verdict']:<8} {r['class']}: {r['why']}")
            print(f"{'':<18} reasons: {', '.join(r['reasons']) or '-'}   notes: {', '.join(r['notes']) or '-'}")
            for field, got, want in r["wrong"]:
                print(f"{'':<18} {field}: read as {got!r}, printed {want!r}")
    print()
    for name, t in summary(rows).items():
        print(f"{name:<22} {t['documents']:>3} documents: {t['approved-correct']} approved-correct, {t['held-correct']} held-correct, "
              f"{t['misread-held']} misread-held ({t['false_alarms']} false alarms), {t['misread-approved']} misread-approved; "
              f"values {t['values_correct']}/{t['values']}")
    if any(r["ocr_missing"] for r in rows):
        print("Some photographs had no OCR engine and no recording. Install Tesseract or run with the recordings in place.")
    recordings = [reader.load_ocr_fixture(r["sha256"], 1) for r in records if r["kind"] == "scan"]
    recorded_with = sorted({f.get("engine", "") for f in recordings if f})
    used_live = engine.available and ocr_mode != "fixture"
    facts = {"rules_version": load_rules().version, "llm": "the model switched off (rules only)",
             "ocr": (f"Photographs read live by {engine.label}, twice each." if used_live else
                     f"Photographs read from the OCR output recorded in `fixtures/ocr/` with "
                     f"{', '.join(recorded_with) or 'Tesseract'} (two reads each), so the numbers are the same on every "
                     f"machine; `--ocr live` reads them with the installed engine instead, and another Tesseract build "
                     f"may read a photograph differently.")
             + (" The same OCR output is recorded in `fixtures/ocr/`, so `--ocr fixture` reproduces these numbers on a "
                "machine with no OCR engine." if used_live and all(recordings) else "")}
    payload = {"measured_on": config.now_utc().date().isoformat(), "ocr": facts["ocr"],
               "summary": summary(rows),
               "documents": {r["id"]: {"class": r["class"], "verdict": r["verdict"], "reasons": r["reasons"],
                                       "wrong": r["wrong"]} for r in rows}}
    if args.save_baseline:
        BASELINE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {BASELINE.relative_to(config.ROOT)}")
    if args.write:
        baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.is_file() else None
        RESULTS.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(report(rows, baseline, facts), encoding="utf-8")
        print(f"wrote {RESULTS.relative_to(config.ROOT)} and {REPORT.relative_to(config.ROOT)}")
    return 1 if any(r["class"] == "misread-approved" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
