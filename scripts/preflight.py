"""Demo-day check. Run it before an interview or a recording:

    python -m scripts.preflight            # everything except a live model call
    python -m scripts.preflight --llm      # also sends one sample to the configured model

It checks the environment, then runs every bundled sample through the real pipeline on a
scratch database and compares each decision with the documented one. Exit code 0 means
ready. Nothing in the app's own database is touched.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import platform
import shutil
import sys
import tempfile
from pathlib import Path

from invoice_desk import config, reader
from invoice_desk import samples as sample_lib

REQUIRED = [("streamlit", "streamlit"), ("pandas", "pandas"), ("pydantic", "pydantic"), ("pdfplumber", "pdfplumber"),
            ("pypdfium2", "pypdfium2"), ("pytesseract", "pytesseract"), ("PIL", "pillow"), ("numpy", "numpy"),
            ("rapidfuzz", "rapidfuzz"), ("yaml", "PyYAML"), ("requests", "requests"), ("dotenv", "python-dotenv"),
            ("reportlab", "reportlab"), ("altair", "altair")]


class Report:
    def __init__(self) -> None:
        self.failed = 0
        self.warned = 0

    def line(self, status: str, what: str, detail: str = "") -> None:
        self.failed += status == "FAIL"
        self.warned += status == "WARN"
        print(f"  [{status:<4}] {what}" + (f": {detail}" if detail else ""))


def pinned_versions() -> dict[str, str]:
    pins = {}
    path = config.ROOT / "requirements.txt"
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#")[0].strip()
            if "==" in line:
                name, version = line.split("==", 1)
                pins[name.strip().lower()] = version.strip()
    return pins


def check_environment(rep: Report) -> None:
    print("\nEnvironment")
    version = sys.version_info
    ok = (3, 11) <= (version.major, version.minor) <= (3, 13)
    rep.line("ok" if ok else "WARN", "Python", f"{platform.python_version()} on {platform.system()}" +
             ("" if ok else " (built and tested on 3.11; 3.11 to 3.13 are expected to work)"))
    pins = pinned_versions()
    for module, dist in REQUIRED:
        try:
            importlib.import_module(module)
            have = importlib.metadata.version(dist)
            want = pins.get(dist.lower())
            if want and have != want:
                rep.line("WARN", dist, f"installed {have}, requirements.txt pins {want}")
            else:
                rep.line("ok", dist, have)
        except Exception as exc:
            rep.line("FAIL", dist, f"not importable ({type(exc).__name__}). Run: pip install -r requirements.txt")
    engine = reader.get_engine(refresh=True)
    if engine.available:
        rep.line("ok", "Tesseract OCR", f"{engine.label} at {engine.cmd}")
    else:
        rep.line("WARN", "Tesseract OCR", "not found. The bundled scans run from recorded OCR output; a scan you "
                 "upload yourself will be held as 'scanned page could not be read'. See SETUP_WINDOWS.md, step 5.")


def check_data(rep: Report) -> None:
    print("\nRules and data")
    try:
        from invoice_desk.rules import load_rules
        rules = load_rules()
        rep.line("ok", "rules.yaml", f"version {rules.version}, {len(rules.reasons)} reason codes")
    except Exception as exc:
        rep.line("FAIL", "rules.yaml", str(exc))
    try:
        from invoice_desk.masterdata import load_master
        master = load_master()
        rep.line("ok", "master data", f"{len(master.vendors)} vendors, {len(master.pos)} purchase orders")
    except Exception as exc:
        rep.line("FAIL", "master data", f"{type(exc).__name__}: {exc}")
    manifest = sample_lib.load_manifest()
    missing = [s["file"] for s in manifest if not (config.SAMPLES_DIR / s["file"]).is_file()]
    rep.line("FAIL" if missing or not manifest else "ok", "sample files",
             f"missing {missing}" if missing else f"{len(manifest)} files")
    try:
        folder = config.runtime_dir()
        rep.line("ok", "runtime folder is writable", str(folder))
    except Exception as exc:
        rep.line("FAIL", "runtime folder", str(exc))
    import json
    dates = []
    for path in (config.SAMPLES_DIR / "truth").glob("*.json"):
        try:
            dates.append(json.loads(path.read_text(encoding="utf-8"))["invoice_date"])
        except (OSError, ValueError, KeyError):
            pass
    latest = max(dates, default="")
    if config.today().isoformat() < latest:
        rep.line("FAIL", "clock", f"today is {config.today()}, before the newest sample invoice ({latest}), so the "
                 f"samples would be held as dated in the future; set INVOICE_DESK_TODAY=2026-11-21 in .env")
    else:
        frozen = " (frozen by INVOICE_DESK_TODAY)" if config.env("INVOICE_DESK_TODAY") else ""
        rep.line("ok", "clock", f"today is {config.today()}{frozen}")


def check_llm(rep: Report, live: bool) -> None:
    print("\nLLM assist (optional)")
    cfg = config.llm_config()
    recordings = sorted((config.FIXTURES_DIR / "llm").glob("*.json"))
    rep.line("ok", "mode", cfg.label)
    rep.line("ok", "recorded responses", f"{len(recordings)} in fixtures/llm" +
             ("" if recordings else " (none yet: sample 10 is held by the rules alone until you record one, "
                                    "see SETUP_WINDOWS.md, step 6)"))
    if not live:
        return
    if cfg.mode != "live":
        rep.line("WARN", "live call", "skipped: LLM_BASE_URL, LLM_API_KEY and LLM_MODEL are not all set")
        return
    from invoice_desk.llm import http_transport
    try:
        reply = http_transport({"model": cfg.model, "temperature": 0, "max_tokens": 5,
                                "messages": [{"role": "user", "content": "Reply with the single word: ready"}]}, cfg)
        rep.line("ok", "live call", f"{cfg.model} answered: {str(reply).strip()[:40]!r}")
    except Exception as exc:
        rep.line("FAIL", "live call", f"{type(exc).__name__}: {str(exc)[:200]}")


def check_samples(rep: Report) -> None:
    print("\nEvery bundled sample through the real pipeline (scratch database)")
    from invoice_desk.pipeline import Pipeline
    from invoice_desk.store import Store
    folder = Path(tempfile.mkdtemp(prefix="invoice_desk_preflight_"))
    try:
        store = Store(folder / "preflight.db")
        pipeline = Pipeline(store=store)
        slowest = 0.0
        for sample in sample_lib.load_manifest():
            result = pipeline.run(sample_lib.sample_bytes(sample), Path(sample["file"]).name, "preflight")
            ok, want, reasons = sample_lib.check(sample, result)
            got = ", ".join(sorted(f.code for f in result.decision.reasons)) or "no blocking reason"
            slowest = max(slowest, result.duration_ms)
            hint = ""
            if not ok and sample.get("scanned") and any(p.get("ocr_source") == "live" for p in result.pages):
                # the OCR engine on this machine reads the scan differently: does the recorded read give the documented result?
                check = Pipeline(store=Store(folder / f"recorded_{sample['order']}.db"), ocr_mode="fixture")
                again = check.run(sample_lib.sample_bytes(sample), Path(sample["file"]).name, "preflight")
                if sample_lib.check(sample, again)[0]:
                    hint = (". The OCR engine installed here reads this scan differently from the one the sample was "
                            "built with. Add the line OCR_MODE=fixture to .env: the bundled scans then use their "
                            "recorded OCR output and give the documented result on any machine")
            rep.line("ok" if ok else "FAIL", f"{sample['order']:02d} {sample['title']}",
                     f"{result.verdict} ({got}), {result.duration_ms:.0f} ms" +
                     ("" if ok else f"; documented: {want} ({', '.join(sorted(reasons)) or 'no blocking reason'})") + hint)
        chain = store.verify_chain()
        rep.line("ok" if chain["ok"] else "FAIL", "receipt chain", f"{chain['count']} receipts verified")
        rep.line("ok", "slowest sample", f"{slowest:.0f} ms")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Check that the demo is ready to run.")
    parser.add_argument("--llm", action="store_true", help="also make one small live call to the configured model")
    args = parser.parse_args(argv)
    rep = Report()
    print("Invoice Desk preflight")
    check_environment(rep)
    check_data(rep)
    check_llm(rep, args.llm)
    if rep.failed == 0:
        check_samples(rep)
    print()
    if rep.failed:
        print(f"NOT READY: {rep.failed} check(s) failed. Fix the FAIL lines above and run this again.")
        return 1
    print("READY" + (f" ({rep.warned} warning(s): read the WARN lines above)" if rep.warned else "") +
          ". Start the app with: streamlit run app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
