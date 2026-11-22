"""Scorecard for the reader on layouts it was not tuned on.

    python -m scripts.evaluate_extraction                  # quick: 60 text layouts, 24 scanned (needs Tesseract)
    python -m scripts.evaluate_extraction --scans 0        # text only
    python -m scripts.evaluate_extraction --full --write   # 200 text, 96 scanned; writes docs/extraction_scorecard.md

Each test invoice is generated with a random layout style, label wording, column names,
date format, items and amounts (scripts/generate_test_data.variant_spec, seeded by index
so the run is repeatable). Scanned versions are rasterised, tilted, blurred and given
noise. A purchase order that the invoice matches exactly is added to the register, and
the invoice then goes through the whole pipeline. Every extracted value is compared with
the generator's ground truth.

Since each invoice is a correct invoice for its PO, the right outcome is always APPROVE.
The number that matters most is not "how many were read perfectly" but "how many wrong
values got through". So each document lands in one of four classes:

  straight through   read correctly and approved
  false alarm        read correctly, but held for a person anyway
  caught             something was misread, and the invoice was held
  silent             something was misread, and the invoice was approved
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

from invoice_desk import config, reader
from invoice_desk import textutil as T
from invoice_desk.llm import LlmAssist
from invoice_desk.masterdata import PoLine, PurchaseOrder, load_master
from invoice_desk.pipeline import Pipeline
from invoice_desk.rules import load_rules
from invoice_desk.store import Store
from scripts import generate_test_data as G

HEADER = (("invoice_number", "invoice_no"), ("invoice_date", "invoice_date"), ("po_reference", "po_ref"),
          ("vendor_gstin", "vendor_gstin"), ("buyer_gstin", "buyer_gstin"), ("bank_account", "bank_account"),
          ("bank_ifsc", "ifsc"))
MONEY = (("subtotal", "taxable"), ("cgst", "cgst"), ("sgst", "sgst"), ("igst", "igst"), ("total", "total"))
NOT_DECISION_RELEVANT = {"bank_ifsc"}       # read and shown, but no rule depends on it
SCAN_ANGLES = [-1.1, 0.6, 1.3, -0.4]        # degrees of tilt given to the scanned versions, by turn
# Layouts with a lower index than this were looked at while the reader was being built
# (they drove fixes). Everything from here up was first run after the reader was finished.
SEEN = {"text": 60, "scan": 24}
LLM_OFF = config.LlmConfig("off", "", "", "", 5.0)


def _same_money(got, want) -> bool:
    got, want = T.to_decimal(got), T.to_decimal(want)
    if got is None:
        return want is None or want == 0
    return want is not None and abs(got - want) <= Decimal("0.01")


def compare(inv, truth) -> list[tuple[str, str, str]]:
    """[(field, got, want)] for every value that differs from the truth."""
    wrong = []
    for field, key in HEADER:
        got, want = inv.get(field) or None, truth[key] or None
        if got != want:
            wrong.append((field, str(got), str(want)))
    for field, key in MONEY:
        if not _same_money(inv.get(field), truth[key]):
            wrong.append((field, str(inv.get(field)), str(truth[key])))
    if len(inv.line_items) != len(truth["lines"]):
        wrong.append(("line count", str(len(inv.line_items)), str(len(truth["lines"]))))
    else:
        for item, want in zip(inv.line_items, truth["lines"]):
            for got, key in ((item.qty, "qty"), (item.unit_price, "unit_price"), (item.amount, "amount")):
                if (got is None) != (want[key] is None) or (got is not None and not _same_money(got, want[key])):
                    wrong.append((f"line {key}", str(got), str(want[key])))
    return wrong


def matching_po(spec: dict, truth: dict) -> PurchaseOrder:
    """The purchase order this invoice bills exactly: same items, quantities and pre-tax prices."""
    lines = [PoLine(line_no=n, description=ln["description"], hsn_sac=ln.get("hsn") or "", uom=ln.get("uom") or "",
                    qty_ordered=Decimal(str(ln["qty"])), unit_price=Decimal(str(ln["unit_price_pre_tax"])),
                    line_amount=Decimal(str(ln["amount_pre_tax"])), qty_received=Decimal(str(ln["qty"])))
             for n, ln in enumerate(truth["lines"], 1)]
    return PurchaseOrder(po_number=truth["po_ref"], po_date=spec["po_date"], vendor_id=spec["vendor"], status="OPEN",
                         buyer="Scorecard", lines=lines)


def evaluate(index: int, scanned: bool, master, rules, folder: Path) -> dict:
    spec = G.variant_spec(index)
    truth = G.truth_for(spec)
    pdf = G.render_invoice_pdf(spec)
    if scanned:
        pdf = G.to_scan(pdf, angle=SCAN_ANGLES[index % 4], noise=6 + index % 5, seed=index)
    register = master.model_copy(deep=True)
    register.pos[truth["po_ref"]] = matching_po(spec, truth)
    store = Store(folder / f"{'scan' if scanned else 'text'}_{index}.db")       # fresh books for every document
    pipeline = Pipeline(store=store, rules=rules, master=register, llm=LlmAssist(LLM_OFF), today=G.date(2026, 11, 21),
                        ocr_mode="live")
    result = pipeline.run(pdf, f"{spec['id']}.pdf", "scorecard")
    inv = result.invoice
    wrong = compare(inv, truth) if inv is not None else [("document", "not read", "an invoice")]
    relevant = [w for w in wrong if w[0] not in NOT_DECISION_RELEVANT]
    approved = result.verdict == "APPROVE"
    outcome = ("straight through" if approved else "false alarm") if not relevant else ("silent" if approved else "caught")
    seconds = sum(s.duration_ms for s in result.stages[:3]) / 1000          # intake, read, extract
    seen = index < SEEN["scan" if scanned else "text"]
    return {"id": spec["id"], "layout": spec["layout"], "scanned": scanned, "wrong": wrong, "relevant": relevant,
            "group": f"{'Scanned' if scanned else 'Text'} PDFs, {'seen while building' if seen else 'held out'}",
            "verdict": result.verdict, "reasons": sorted({f.code for f in result.decision.reasons}),
            "outcome": outcome, "seconds": seconds,
            "fields": len(HEADER) + len(MONEY) + 3 * len(truth["lines"])}


def summarise(rows: list[dict]) -> dict:
    outcomes = Counter(r["outcome"] for r in rows)
    values = sum(r["fields"] for r in rows)
    wrong_values = sum(len(r["wrong"]) for r in rows)
    return {"documents": len(rows), "through": outcomes["straight through"], "false_alarms": outcomes["false alarm"],
            "caught": outcomes["caught"], "silent": outcomes["silent"],
            "values": values, "values_correct": values - wrong_values,
            "by_field": Counter(w[0] for r in rows for w in r["wrong"]),
            "median_seconds": sorted(r["seconds"] for r in rows)[len(rows) // 2] if rows else 0.0}


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["group"], []).append(r)
    return out


def report(text_rows: list[dict], scan_rows: list[dict], engine: str) -> str:
    lines = ["# Extraction scorecard", "",
             "Generated by `python -m scripts.evaluate_extraction --full --write`. Nothing in this file is typed by hand.",
             "", f"- Reader: rules only (no LLM). OCR engine: {engine}. Scans are read twice (greyscale and black and white).",
             "- Test documents: layouts generated at random (style, labels, column names, date format, items, amounts), "
             "not the bundled samples. Scans are rasterised at 220 dpi, tilted, blurred and given noise.",
             "- Each invoice is a correct invoice for a purchase order that is added to the register, and it goes "
             "through the whole pipeline on fresh books. The right outcome is therefore always APPROVE. The "
             "auto-approval ceiling is lifted for this run, because the scorecard measures reading, not approval policy.",
             f"- **Seen while building**: the first {SEEN['text']} text layouts and the first {SEEN['scan']} scanned "
             "layouts were looked at during development and drove fixes. **Held out**: the rest were first run after "
             "the reader was finished. The held-out rows are the fair measure.",
             "- **Straight through**: read correctly and approved. **False alarm**: read correctly but held. "
             "**Caught**: something was misread and the invoice was held for a person. **Silent**: something was "
             "misread and the invoice was approved. Silent is the outcome that costs money.", ""]
    lines += ["| Set | Documents | Straight through | False alarm | Misread, caught | Misread, silent | Values read correctly | Median read time |",
              "|---|---|---|---|---|---|---|---|"]
    for name, rows in list(groups(text_rows).items()) + list(groups(scan_rows).items()):
        s = summarise(rows)
        lines.append(f"| {name} | {s['documents']} | {s['through']} | {s['false_alarms']} | {s['caught']} | {s['silent']} | "
                     f"{s['values_correct']} of {s['values']} ({s['values_correct'] / s['values']:.1%}) | "
                     f"{s['median_seconds'] * 1000:.0f} ms |")
    for name, rows in (("Text PDFs", text_rows), ("Scanned PDFs", scan_rows)):
        misread = [r for r in rows if r["wrong"]]
        if not misread:
            continue
        lines += ["", f"## {name}: every misread value", "",
                  "| Document | Layout | Field | Read as | Truth | Pipeline decision | Reasons |", "|---|---|---|---|---|---|---|"]
        for r in misread:
            for field, got, want in r["wrong"]:
                note = " (field not used by any rule)" if field in NOT_DECISION_RELEVANT else ""
                lines.append(f"| {r['id']} | {r['layout']} | {field}{note} | `{got}` | `{want}` | {r['verdict']} | "
                             f"{', '.join(r['reasons']) or '-'} |")
    all_rows = text_rows + scan_rows
    alarms = [r for r in all_rows if r["outcome"] == "false alarm"]
    lines += ["", "## False alarms", "",
              f"{len(alarms)} of {len(all_rows)} documents were read correctly and still held."]
    if alarms:
        lines += ["", "| Document | Layout | Scanned | Reasons |", "|---|---|---|---|"]
        lines += [f"| {r['id']} | {r['layout']} | {'yes' if r['scanned'] else 'no'} | {', '.join(r['reasons'])} |" for r in alarms]
    silent = [r for r in all_rows if r["outcome"] == "silent"]
    lines += ["", "## Silent misreads", ""]
    if silent:
        fields = Counter(w[0] for r in silent for w in r["relevant"])
        lines.append(f"{len(silent)} document(s). Fields involved: " + ", ".join(f"{k} ({v})" for k, v in fields.most_common()) + ".")
    else:
        lines.append("None in this run. That is a result for these documents, not a guarantee. These are synthetic "
                     "scans; real scans vary more. A value that no arithmetic, master-data or PO check can confirm "
                     "(an invoice number, a date) is trusted only when both reads of the scan agree on it, and a "
                     "disagreement holds the invoice.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Score the reader on unseen layouts.")
    parser.add_argument("--text", type=int, default=60, help="number of text layouts (default 60)")
    parser.add_argument("--scans", type=int, default=24, help="number of scanned layouts (default 24)")
    parser.add_argument("--first", type=int, default=0, help="index of the first generated layout (default 0)")
    parser.add_argument("--full", action="store_true", help="the full set: 200 text and 96 scanned layouts (several minutes)")
    parser.add_argument("--write", action="store_true", help="write docs/extraction_scorecard.md")
    args = parser.parse_args(argv)
    if args.full:
        args.first, args.text, args.scans = 0, 200, 96
    master, rules = load_master(), load_rules().model_copy(deep=True)
    rules.auto_approve.max_invoice_total_inr = Decimal("1e12")       # reading is measured here, not approval policy
    engine = reader.get_engine()
    folder = Path(tempfile.mkdtemp(prefix="invoice_desk_scorecard_"))
    try:
        text_rows = [evaluate(i, False, master, rules, folder) for i in range(args.first, args.first + args.text)]
        scan_rows: list[dict] = []
        if args.scans and not engine.available:
            print("Tesseract is not installed: scanned layouts skipped.")
        elif args.scans:
            for n, i in enumerate(range(args.first, args.first + args.scans), 1):
                scan_rows.append(evaluate(i, True, master, rules, folder))
                print(f"\rscans {n}/{args.scans}", end="", flush=True)
            print()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    for name, rows in list(groups(text_rows).items()) + list(groups(scan_rows).items()):
        s = summarise(rows)
        print(f"{name}: {s['documents']} documents, {s['through']} straight through, {s['false_alarms']} false "
              f"alarm, {s['caught']} misread and caught, {s['silent']} misread and silent; values "
              f"{s['values_correct']}/{s['values']}; misses by field {dict(s['by_field'])}")
    if args.write:
        out = config.ROOT / "docs" / "extraction_scorecard.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report(text_rows, scan_rows, engine.label), encoding="utf-8")
        print(f"wrote {out.relative_to(config.ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
