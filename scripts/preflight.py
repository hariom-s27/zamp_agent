"""Demo-day check. Run it before an interview or a recording:

    python -m scripts.preflight            # everything except a live call to the Claude API
    python -m scripts.preflight --llm      # also makes one very small live call, to prove the key and the model work

It checks the environment, runs every bundled sample through the real pipeline on a scratch
database and compares each decision with the documented one, rehearses the eleven steps of
the guided demo in order, and opens every page of the app without a browser. Exit code 0
means ready. Nothing in the app's own database is touched and nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import json
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
            ("rapidfuzz", "rapidfuzz"), ("yaml", "PyYAML"), ("anthropic", "anthropic"), ("dotenv", "python-dotenv"),
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
    case_manifest = sample_lib.load_case_manifest()
    missing = [s["file"] for s in case_manifest if not (config.SAMPLES_DIR / s["file"]).is_file()]
    rep.line("FAIL" if missing or not case_manifest else "ok", "after-hold sample files",
             f"missing {missing}" if missing else f"{len(case_manifest)} after-hold files")
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


HTTP_CLIENT = None       # tests put an SDK http client with a fake transport here; None means the real API


def check_llm(rep: Report, live: bool) -> None:
    print("\nAI assist (Claude)")
    from invoice_desk import llm as llm_module
    cfg = config.llm_config()
    rep.line("ok", "mode", f"{cfg.label}. {cfg.explanation}")
    if cfg.requested:
        rep.line("WARN", "LLM_MODE", f"'{cfg.requested}' is not one of fixture, cached-live, off; treated as {cfg.mode}")
    rep.line("ok", "model", f"{cfg.model}" + (f", escalation {cfg.escalation_model}" if cfg.escalation_model else "") +
             f"; cap ${cfg.per_invoice_cap_usd:.2f} per invoice, ${cfg.daily_cap_usd:.2f} per day")
    rep.line("ok", "API key", "set (never shown or stored)" if cfg.api_key else "not set: recorded answers only")
    assist = llm_module.LlmAssist(config.llm_config(mode="fixture"))
    letter = next((s for s in sample_lib.load_manifest() if "with_llm" in s), None)
    notes = len(list((config.FIXTURES_DIR / "llm").glob("vendor_note__*.json")))
    if letter is not None:
        have = assist.has_recording(reader.sha256_of(sample_lib.sample_bytes(letter)))
        rep.line("ok" if have else "WARN", "recorded answer for the letter invoice (sample 10)",
                 "present: the demo shows it read by Claude and approved" if have else
                 "none yet, so sample 10 is held by the rules alone. With your key in .env run: "
                 "python -m scripts.record_llm_fixtures")
    rep.line("ok", "recorded vendor-note rewordings", str(notes))
    if not live:
        return
    if not cfg.api_key:
        rep.line("FAIL", "live call", "ANTHROPIC_API_KEY is not set")
        return
    try:
        import anthropic
        kwargs = {"api_key": cfg.api_key, "timeout": cfg.timeout_s, "max_retries": cfg.max_retries}
        if HTTP_CLIENT is not None:
            kwargs["http_client"] = HTTP_CLIENT
        message = anthropic.Anthropic(**kwargs).messages.create(
            model=cfg.model, max_tokens=64, messages=[{"role": "user", "content": "Reply with the single word: ready"}])
        text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text").strip()
        cost = llm_module.cost_usd(cfg, cfg.model, message.usage.input_tokens, message.usage.output_tokens)
        rep.line("ok", "live call", f"{cfg.model} answered {text[:30]!r}; {message.usage.input_tokens} tokens in, "
                 f"{message.usage.output_tokens} out, ${cost:.5f}; request {getattr(message, '_request_id', '-')}")
    except Exception as exc:
        status = getattr(exc, "status_code", "")
        rep.line("FAIL", "live call", f"{type(exc).__name__}{f' (HTTP {status})' if status else ''}: {str(exc)[:160]}")


def check_samples(rep: Report) -> None:
    print("\nEvery bundled sample through the real pipeline (scratch database)")
    from invoice_desk.pipeline import Pipeline
    from invoice_desk.store import Store
    folder = Path(tempfile.mkdtemp(prefix="invoice_desk_preflight_"))
    try:
        from invoice_desk.llm import LlmAssist
        store = Store(folder / "preflight.db")
        mode = config.llm_config().mode
        # preflight never spends money: with cached-live configured, the samples run on recorded answers only
        pipeline = Pipeline(store=store, llm=LlmAssist(config.llm_config(mode="fixture" if mode == "cached-live" else mode)),
                            today=sample_lib.business_date())
        slowest = 0.0
        for sample in sample_lib.load_manifest():
            result = pipeline.run(sample_lib.sample_bytes(sample), sample_lib.received_name(sample), "preflight")
            ok, want, reasons = sample_lib.check(sample, result)
            got = ", ".join(sorted(f.code for f in result.decision.reasons)) or "no blocking reason"
            slowest = max(slowest, result.duration_ms)
            hint = ""
            if not ok and sample.get("scanned") and any(p.get("ocr_source") == "live" for p in result.pages):
                # the OCR engine on this machine reads the scan differently: does the recorded read give the documented result?
                check = Pipeline(store=Store(folder / f"recorded_{sample['order']}.db"), ocr_mode="fixture", llm=pipeline.llm,
                                 today=sample_lib.business_date())
                again = check.run(sample_lib.sample_bytes(sample), sample_lib.received_name(sample), "preflight")
                if sample_lib.check(sample, again)[0]:
                    hint = (". The OCR engine installed here reads this scan differently from the one the sample was "
                            "built with. Add the line OCR_MODE=fixture to .env: the bundled scans then use their "
                            "recorded OCR output and give the documented result on any machine")
            rep.line("ok" if ok else "FAIL", f"{sample['order']:02d} {sample['title']}",
                     f"{result.verdict} ({got}), {result.duration_ms:.0f} ms" +
                     ("" if ok else f"; documented: {want} ({', '.join(sorted(reasons)) or 'no blocking reason'})") + hint)
        chain = store.verify_chain()
        rep.line("ok" if chain["ok"] else "FAIL", "audit trail", f"{chain['count']} receipts verified")
        rep.line("ok", "slowest sample", f"{slowest:.0f} ms")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def check_demo(rep: Report) -> None:
    """Rehearse the guided demo: the same eleven steps the Guided demo page runs, in order,
    on a scratch database, each compared with its expected decision."""
    print("\nGuided demo rehearsal (the eleven scenarios, scratch database)")
    from invoice_desk import demo_steps
    from invoice_desk.llm import LlmAssist
    from invoice_desk.pipeline import Pipeline
    from invoice_desk.store import Store
    folder = Path(tempfile.mkdtemp(prefix="invoice_desk_demo_"))
    try:
        mode = config.llm_config().mode
        pipeline = Pipeline(store=Store(folder / "demo.db"), today=sample_lib.business_date(),
                            llm=LlmAssist(config.llm_config(mode="fixture" if mode == "cached-live" else mode)))
        done: dict = {}
        manifest = sample_lib.load_manifest()
        for number, step in enumerate(demo_steps.STEPS, 1):
            sample = demo_steps.sample_of(step, manifest)
            result = pipeline.run(sample_lib.sample_bytes(sample), sample_lib.received_name(sample), "preflight")
            ok, want = demo_steps.outcome(step, result, done, pipeline.rules, manifest)
            done[step["key"]] = {"run_id": result.run_id}
            rep.line("ok" if ok else "FAIL", f"step {number:>2} {step['short']}",
                     f"{result.verdict}" + ("" if ok else f"; expected {want}"))
        chain = pipeline.store.verify_chain()
        rep.line("ok" if chain["ok"] else "FAIL", "audit trail after the demo", f"{chain['count']} entries verified")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def check_cases(rep: Report) -> None:
    """Rehearse steps 12 and 13 on a scratch database: the case for an invoice that came before
    the goods is released by rule once they are received, and a small supplier's case is urgent,
    escalated, and gets a message approved by a named person into the outbox."""
    print("\nAfter the decision rehearsal (steps 12 and 13, scratch database)")
    import email
    import email.policy
    from decimal import Decimal
    from invoice_desk import cases, demo_steps
    from invoice_desk.llm import LlmAssist
    from invoice_desk.pipeline import Pipeline
    from invoice_desk.store import Store
    folder = Path(tempfile.mkdtemp(prefix="invoice_desk_cases_"))
    try:
        today = sample_lib.business_date()
        pipeline = Pipeline(store=Store(folder / "cases.db"), today=today, llm=LlmAssist(config.llm_config(mode="off")))
        store, master, rules = pipeline.store, pipeline.master, pipeline.rules
        case_manifest = sample_lib.load_case_manifest()
        early, clock = demo_steps.AFTER_HOLD
        number = len(demo_steps.STEPS) + 1

        sample = demo_steps.sample_of_case(early, case_manifest)
        run = pipeline.run(sample_lib.sample_bytes(sample), sample_lib.received_name(sample), "preflight")
        cases.sync(store, master, rules, today)
        case = store.case_for_run(run.run_id)
        problems = []
        if not case or case["waiting_on"] != "Stores":
            problems.append(f"expected a case waiting on Stores, got {case and case['waiting_on']}")
        else:
            cases.record_goods_receipt(store, master, "PO-2026-1041", 2, Decimal("60"), today, "Preflight (Stores)",
                                       "DN-PREFLIGHT", for_run=run.run_id)
            cases.sync(store, master, rules, today)
            if store.run_row(run.run_id)["review_status"] != "RELEASED" or                     store.get_ledger(run.ledger_id)["status"] != "POSTED":
                problems.append("the goods receipt did not release the invoice")
        rep.line("FAIL" if problems else "ok", f"case {number} {early['short']}",
                 "; ".join(problems) or "waiting on Stores, then released by rule and posted")

        sample = demo_steps.sample_of_case(clock, case_manifest)
        run = pipeline.run(sample_lib.sample_bytes(sample), sample_lib.received_name(sample), "preflight")
        cases.sync(store, master, rules, today)
        case = store.case_for_run(run.run_id)
        problems = []
        if not case or case["priority"] != "urgent" or case["escalated_to"] != "Finance controller" or                 case["statutory_due"] != "2026-11-24":
            problems.append(f"expected urgent, escalated, pay-by 2026-11-24; got {case}")
        else:
            sent = cases.approve_message(store, master, rules, case["case_id"], "Preflight (AP)", today)
            mail = email.message_from_bytes(sent["path"].read_bytes(), policy=email.policy.default)
            if f"[{case['case_id']}]" not in str(mail["Subject"]) or mail["To"] != sent["to"]:
                problems.append("the outbox file is not the expected email")
        rep.line("FAIL" if problems else "ok", f"case {number + 1} {clock['short']}",
                 "; ".join(problems) or "urgent, escalated to Finance controller, pay-by 24 Nov 2026, message in the outbox")
        chain = store.verify_chain()
        rep.line("ok" if chain["ok"] else "FAIL", "audit trail after the after-hold steps",
                 f"{chain['count']} entries verified")
    except Exception as exc:
        rep.line("FAIL", "after-hold rehearsal", f"{type(exc).__name__}: {str(exc)[:160]}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def check_desk(rep: Report) -> None:
    """Rehearse the round 4 desk on a scratch database: today's mail is sorted into the expected kinds."""
    print("\nThe desk rehearsal (round 4, scratch database)")
    from invoice_desk import inbox
    from invoice_desk.llm import LlmAssist
    from invoice_desk.pipeline import Pipeline
    from invoice_desk.store import Store
    folder = Path(tempfile.mkdtemp(prefix="invoice_desk_desk_"))
    try:
        today = sample_lib.business_date()
        pipeline = Pipeline(store=Store(folder / "desk.db"), today=today, llm=LlmAssist(config.llm_config(mode="off")))
        store, master, rules = pipeline.store, pipeline.master, pipeline.rules
        if not rules.inbox.enabled:
            rep.line("WARN", "inbox", "switched off in rules.yaml (inbox.enabled)")
            return
        mail = sample_lib.load_mail_manifest()
        inbox.deliver(store, master, rules, pipeline, inbox.bundled_mail(), today)
        got = [i["kind"] for i in store.inbox()]
        want = [m["expected_kind"] for m in mail]
        problems = [] if got == want else [f"expected {want}, got {got}"]
        if not problems and any(not i["owner"] or not i["due_on"] for i in store.inbox()):
            problems.append("a message has no owner or due date")
        rep.line("FAIL" if problems else "ok", f"inbox: {len(want)} messages",
                 "; ".join(problems) or "each sorted as expected, with an owner and a due date")
        # the next morning's replies, read from recorded AI answers only (fixture mode never calls the API)
        pipeline.llm = LlmAssist(config.llm_config(mode="fixture"))
        out = inbox.deliver(store, master, rules, pipeline, inbox.bundled_mail("next_morning"), today)
        reports = [json.loads(store.get_message(o["message_id"])["read_json"] or "{}") for o in out]
        leaked = [o["file"] for o, r in zip(out[1:], reports[1:]) if r.get("offered")]
        if leaked:
            rep.line("FAIL", "replies", f"a reply that must offer nothing offered a value: {', '.join(leaked)}")
        elif reports and reports[0].get("outcome") == "offered":
            rep.line("ok", "replies", "invoice number offered from a recorded AI answer; the look-alike sender and "
                     "the reply with instructions offer nothing")
        elif not rules.replies.enabled:
            rep.line("WARN", "replies", "switched off in rules.yaml (replies.enabled)")
        else:
            rep.line("WARN", "replies", "no recorded AI answer for the bundled replies, so the manual path is shown "
                     "(record them with python -m scripts.record_reply_fixtures)")
        _rehearse_payments(rep, folder, today)
        chain = store.verify_chain()
        rep.line("ok" if chain["ok"] else "FAIL", "audit trail after the desk rehearsal",
                 f"{chain['count']} entries verified")
    except Exception as exc:
        rep.line("FAIL", "desk rehearsal", f"{type(exc).__name__}: {str(exc)[:160]}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _rehearse_payments(rep: Report, folder: Path, today) -> None:
    """The worked example of the payment run on its own scratch books: groups, order, two approvers, the export."""
    import hashlib
    from invoice_desk import cases, payments
    from invoice_desk.llm import LlmAssist
    from invoice_desk.pipeline import Pipeline
    from invoice_desk.store import Store
    pipeline = Pipeline(store=Store(folder / "payments.db"), today=today,
                        llm=LlmAssist(config.llm_config(mode="fixture")))
    store, master, rules = pipeline.store, pipeline.master, pipeline.rules
    if not rules.payments.enabled:
        rep.line("WARN", "payment run", "switched off in rules.yaml (payments.enabled)")
        return
    by_id = {s["id"]: s for s in sample_lib.load_manifest() + sample_lib.load_case_manifest()
             + sample_lib.load_desk_manifest()}
    ids = {}
    for sid in ("01_happy_path_furniture", "02_scanned_clean_safety", "03_monthly_service_instalment",
                "07_E4a_tax_inclusive_within_tolerance", "09_fraud_guard_bank_change", "p1_micro_supplier_due",
                "c2_small_supplier_late_invoice"):
        ids[sid] = pipeline.run(sample_lib.sample_bytes(by_id[sid]), sample_lib.received_name(by_id[sid]), "preflight")
    cases.sync(store, master, rules, today)
    proposal = payments.propose(store, master, rules, today)
    problems = []
    if not proposal["pay"] or proposal["pay"][0]["ledger_id"] != ids["p1_micro_supplier_due"].ledger_id:
        problems.append("the micro supplier's legal date is not first")
    if ids["01_happy_path_furniture"].ledger_id not in {ln["ledger_id"] for ln in proposal["blocked"]}:
        problems.append("the vendor with a bank change is not blocked")
    if not problems:
        done = payments.approve(store, master, rules, today, proposal["fingerprint"], ["Preflight (CFO)", "Preflight (AP)"],
                                True, today)
        entry = store.receipts()[-1]["body"]
        if entry.get("export_sha256") != hashlib.sha256(done["path"].read_bytes()).hexdigest():
            problems.append("the export file does not match its audit entry")
    rep.line("FAIL" if problems else "ok", "payment run", "; ".join(problems) or
             f"legal date first, bank-change vendor blocked, approved by two people, {len(proposal['pay'])} lines "
             f"exported; nothing paid")


def check_pages(rep: Report) -> None:
    """Open every page of the app without a browser, on a scratch database."""
    print("\nEvery page of the app opens (headless, scratch database)")
    import os
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:                       # an old Streamlit: say so and carry on
        rep.line("WARN", "page check skipped", f"streamlit.testing is not available ({type(exc).__name__})")
        return
    import logging
    # Outside a browser session Streamlit logs "missing ScriptRunContext" for every page. It means nothing here and
    # would bury the result lines, so its loggers are kept to errors for this check.
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("streamlit"):
            logging.getLogger(name).setLevel(logging.ERROR)
    logging.getLogger("streamlit").setLevel(logging.ERROR)
    folder = Path(tempfile.mkdtemp(prefix="invoice_desk_pages_"))
    kept = {name: os.environ.get(name) for name in ("INVOICE_DESK_DB", "LLM_USAGE_DB")}
    os.environ["INVOICE_DESK_DB"], os.environ["LLM_USAGE_DB"] = str(folder / "pages.db"), str(folder / "usage.db")
    try:
        for key, title in (("demo", "Guided demo"), ("inbox", "Inbox"), ("process", "Process invoice"), ("review", "Review queue"),
                           ("cases", "Cases"), ("payments", "Payments"), ("dashboard", "Dashboard"), ("value", "Business case"), ("reference", "Rules and data"), ("guide", "How it works")):
            app = AppTest.from_file(str(config.ROOT / "app.py"), default_timeout=120)
            app.session_state["goto"] = key
            app.run()
            problems = [str(e.value)[:120] for e in app.exception]
            rep.line("FAIL" if problems else "ok", title, "; ".join(problems))
    except Exception as exc:
        rep.line("FAIL", "page check", f"{type(exc).__name__}: {str(exc)[:160]}")
    finally:
        for name, value in kept.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
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
    if rep.failed == 0:
        check_demo(rep)
        check_cases(rep)
        check_desk(rep)
        check_pages(rep)
    print()
    if rep.failed:
        print(f"NOT READY: {rep.failed} check(s) failed. Fix the FAIL lines above and run this again.")
        return 1
    print("READY" + (f" ({rep.warned} warning(s): read the WARN lines above)" if rep.warned else "") +
          ". Start the app with the same Python: python -m streamlit run app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
