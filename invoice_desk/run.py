"""Command line: run the real pipeline on one invoice, or on every bundled sample.

    python -m invoice_desk.run samples/01_happy_path_furniture.pdf
    python -m invoice_desk.run --all              # every sample, in order, expected against actual
    python -m invoice_desk.run --cases            # the after-hold samples, in order, on their own scratch books
    python -m invoice_desk.run FILE --json        # the full run record as JSON
    python -m invoice_desk.run FILE --save        # use the app's own database (shows in the dashboard)

Without --save each invocation works on a fresh temporary database seeded with the prior
history, so results do not depend on what was run before.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from . import samples
from .models import RunResult
from .pipeline import Pipeline
from .store import Store

MARK = {"ok": "ok  ", "attention": "HOLD", "blocked": "STOP", "skipped": "skip", "error": "ERR "}


def print_run(result: RunResult) -> None:
    print(f"\n{result.run_id}  {result.file_name}")
    for s in result.stages:
        print(f"  {s.number} {s.title:<16} {MARK.get(s.status, s.status):<5} {s.duration_ms:>8.1f} ms  {s.summary}")
    d = result.decision
    print(f"\n  {d.headline}")
    print(f"  {d.explanation}")
    for f in d.reasons:
        print(f"    - [{f.code}] {f.title} (owner: {f.owner or '-'})")
    for q in d.questions:
        print(f"    ? {q}")
    for f in d.notes:
        print(f"    note [{f.code}] {f.summary}")
    print(f"  Next: {d.next_action}")


def run_cases(db_path: Path, as_json: bool = False) -> int:
    """The second manifest, in order, on its own scratch books. Returns the number of mismatches."""
    store = Store(db_path)
    sample_run = Pipeline(store=store, today=samples.business_date())
    rows, failures = [], 0
    for sample in samples.load_case_manifest():
        result = sample_run.run(samples.sample_bytes(sample), samples.received_name(sample), source="cli")
        codes = {f.code for f in result.decision.reasons}
        ok, want, _ = samples.check(sample, result)
        failures += not ok
        rows.append((sample["id"], want, result.verdict, ",".join(sorted(codes)) or "-",
                     f"{result.duration_ms:.0f} ms", "ok" if ok else "MISMATCH"))
        if as_json:
            print(result.model_dump_json(indent=2))
    width = max([len(r[0]) for r in rows] + [len("sample")])
    print(f"\n{'sample':<{width}}  expected  actual   check     time     reasons")
    for sid, exp, act, codes, ms, ok in rows:
        print(f"{sid:<{width}}  {exp:<8}  {act:<7}  {ok:<8}  {ms:>7}  {codes}")
    print(f"\n{len(rows) - failures} of {len(rows)} after-hold samples gave the expected decision")
    return failures + (0 if rows else 1)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # a Windows console may not be UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Process invoice PDFs from the command line.")
    parser.add_argument("files", nargs="*", help="PDF files to process")
    parser.add_argument("--all", action="store_true", help="run every bundled sample in order")
    parser.add_argument("--cases", action="store_true", help="run the after-hold samples in order on scratch books")
    parser.add_argument("--json", action="store_true", help="print the full run record as JSON")
    parser.add_argument("--save", action="store_true", help="write to the app database instead of a temporary one")
    args = parser.parse_args(argv)
    if not args.files and not args.all and not args.cases:
        parser.print_help()
        return 2
    with tempfile.TemporaryDirectory(prefix="invoice_desk_") as tmp:
        store = Store() if args.save else Store(Path(tmp) / "cli.db")
        pipeline = Pipeline(store=store)
        failures = 0
        if args.all:
            rows = []
            # the samples are processed on the date they were written for, as in the app
            sample_run = Pipeline(store=store, today=samples.business_date())
            for sample in samples.load_manifest():
                result = sample_run.run(samples.sample_bytes(sample), samples.received_name(sample), source="cli")
                codes = {f.code for f in result.decision.reasons}
                ok, want, _ = samples.check(sample, result)      # verdict and reason codes must both match
                failures += not ok
                rows.append((sample["id"], want, result.verdict, ",".join(sorted(codes)) or "-",
                             f"{result.duration_ms:.0f} ms", "ok" if ok else "MISMATCH"))
                if args.json:
                    print(result.model_dump_json(indent=2))
            width = max(len(r[0]) for r in rows)
            print(f"\n{'sample':<{width}}  expected  actual   check     time     reasons")
            for sid, exp, act, codes, ms, ok in rows:
                print(f"{sid:<{width}}  {exp:<8}  {act:<7}  {ok:<8}  {ms:>7}  {codes}")
            print(f"\n{len(rows) - failures} of {len(rows)} samples gave the expected decision "
                  f"(LLM: {pipeline.llm.cfg.label}; OCR: {pipeline.modes()['ocr']})")
        if args.cases:
            failures += run_cases(Path(tmp) / "cases.db", args.json)
        for name in args.files:
            path = Path(name)
            if not path.is_file():
                print(f"file not found: {name}", file=sys.stderr)
                failures += 1
                continue
            result = pipeline.run(path.read_bytes(), path.name, source="cli")
            print(result.model_dump_json(indent=2)) if args.json else print_run(result)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
