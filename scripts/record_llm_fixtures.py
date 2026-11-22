"""Record real model responses for the samples that use LLM assist.

    python -m scripts.record_llm_fixtures

Needs LLM_BASE_URL, LLM_API_KEY and LLM_MODEL (in .env or the environment). For each
bundled sample the rules reader leaves gaps in, one live call is made, the response is
verified against the page exactly as in a normal run, and the raw response is saved
under fixtures/llm/. After that the sample replays with no network (LLM_MODE=replay),
which is what makes the AI-assist step safe to show in a live demo.

The recordings in this folder must come from a real model. Do not write them by hand.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from invoice_desk import config, reader
from invoice_desk import samples as sample_lib
from invoice_desk.extract import extract_invoice
from invoice_desk.llm import LlmAssist
from invoice_desk.masterdata import load_master
from invoice_desk.pipeline import Pipeline
from invoice_desk.rules import load_rules
from invoice_desk.store import Store


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    cfg = config.llm_config()
    if cfg.mode != "live":
        print("No model is configured. Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env (see .env.example), "
              "leave LLM_MODE unset or set it to live, and run this again.")
        return 1
    rules, master = load_rules(), load_master()
    live = LlmAssist(cfg)
    recorded = 0
    for sample in sample_lib.load_manifest():
        if sample["group"] == "bad_input":
            continue
        content = sample_lib.sample_bytes(sample)
        try:
            doc = reader.read_pdf(content)
        except reader.ReaderError:
            continue
        inv = extract_invoice(doc, rules.company.gstin, rules.company.name, master.gstin_index, list(master.pos))
        gaps = live.gaps(inv)
        if not gaps:
            continue
        print(f"\n{sample['id']}: the rules left gaps ({', '.join(gaps)}); calling {cfg.model}")
        report = live.assist(doc, inv, rules.llm.assist)
        if report.get("error"):
            print(f"  call failed: {report['error']}")
            continue
        recorded += bool(report.get("recorded_to"))
        print(f"  accepted (quote found on the page and states the value): {', '.join(report['accepted']) or 'nothing'}")
        for item in report["rejected"]:
            print(f"  rejected {item['field']}: {item['why']}")
        for item in report["disagreements"]:
            print(f"  differs from the rules reader on {item['field']}: rules {item['rules']}, model {item['llm']}")
        print(f"  saved as fixtures/llm/{report.get('recorded_to')}")

    if not recorded:
        print("\nNothing was recorded.")
        return 1
    print("\nReplaying the samples from the recordings (no network)")
    folder = Path(tempfile.mkdtemp(prefix="invoice_desk_llm_"))
    failures = 0
    try:
        replay = Pipeline(store=Store(folder / "replay.db"), rules=rules, master=master,
                          llm=LlmAssist(config.LlmConfig("replay", "", "", "", 5.0)))
        for sample in sample_lib.load_manifest():
            if "with_llm" not in sample:
                continue
            result = replay.run(sample_lib.sample_bytes(sample), Path(sample["file"]).name, "replay")
            ok, want, _ = sample_lib.check(sample, result)
            failures += not ok
            print(f"  {sample['id']}: {result.verdict} ({'as documented with LLM assist' if ok else f'documented: {want}'})")
            if not ok:
                print(f"    {result.decision.explanation}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if failures:
        print("\nThe model's answer did not get the sample through. Read the rejected lines above: the usual cause is "
              "a quote that is not word for word from the page. Try a stronger model, then run this again.")
        return 1
    print("\nDone. Commit the new file(s) in fixtures/llm so the hosted app can replay them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
