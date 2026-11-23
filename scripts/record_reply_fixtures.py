"""Record real AI answers for the bundled vendor replies (round 4), so the vendor loop can be
shown with no key and no network (FIXTURE mode).

    python -m scripts.record_reply_fixtures --dry-run     # what would be sent and what it could cost; no network
    python -m scripts.record_reply_fixtures               # record whatever is not recorded yet
    python -m scripts.record_reply_fixtures --force       # record again, replacing what is there
    python -m scripts.record_reply_fixtures --model claude-sonnet-5-5

It needs ANTHROPIC_API_KEY in .env (or the environment). Each reply in
samples/desk/mail/next_morning/ is read the way the inbox reads it: quoted text removed,
then one "reply_read" request. The answer goes to fixtures/llm/ and belongs in the
repository. The eight checks are then run on it, and the script exits with 1 if the reply
that states the invoice number (n1) is not offered, or if a reply that must offer nothing
(n3, n4) offers something.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from invoice_desk import config
from invoice_desk import samples as sample_lib
from invoice_desk.llm import MAX_CHARS, LlmAssist, SpendLedger, cost_usd, estimate_tokens
from invoice_desk.mailbox import check_sender, parse_eml, strip_quoted
from invoice_desk.masterdata import load_master
from invoice_desk.replies import read_reply
from invoice_desk.rules import load_rules

HTTP_CLIENT = None       # tests put an SDK http client with a fake transport here; None means the real API
CASE_VENDOR = "V102"     # D2's vendor: the replies answer the question about D2
MUST_OFFER = {"n1_reply_number.eml"}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Record AI answers for the bundled vendor replies.")
    parser.add_argument("--dry-run", action="store_true", help="show what would be sent and its worst-case cost; no network")
    parser.add_argument("--force", action="store_true", help="record again, replacing existing recordings")
    parser.add_argument("--model", default="", help="model to record with (default: LLM_MODEL)")
    args = parser.parse_args(argv)

    cfg = config.llm_config(mode="cached-live")
    if args.model:
        cfg = dataclasses.replace(cfg, model=args.model)
    if not cfg.api_key and not args.dry_run:
        print("No API key found. Put ANTHROPIC_API_KEY=... in .env (see .env.example), or try --dry-run first.")
        return 2
    print(f"Model {cfg.model}" + ("; DRY RUN, nothing is sent" if args.dry_run else ""))
    rules, master = load_rules(), load_master()
    failures, rows = 0, []
    with tempfile.TemporaryDirectory(prefix="invoice_desk_record_replies_") as tmp:
        llm = LlmAssist(dataclasses.replace(cfg, mode="cached-live"), cache_dir=Path(tmp) / "unused_cache",
                        ledger=SpendLedger(), save_to="fixtures", http_client=HTTP_CLIENT)
        llm.force = args.force
        for entry in sample_lib.load_mail_manifest("next_morning"):
            name = entry["file"].split("/")[-1]
            mail = parse_eml((config.SAMPLES_DIR / entry["file"]).read_bytes())
            text = strip_quoted(mail.text)[0]
            sender = check_sender(mail.sender, master, rules)
            sender_ok = sender.result in ("on_file", "same_domain") and sender.vendor_id == CASE_VENDOR
            key = hashlib.sha256(text.encode("utf-8")).hexdigest()
            have = llm.find("reply_read", key, cfg.model) is not None
            if args.dry_run:
                request = llm.request("reply_read", text[:MAX_CHARS])
                tokens = estimate_tokens(json.dumps(request))
                worst = cost_usd(cfg, cfg.model, tokens, cfg.max_output_tokens)
                rows.append((name, "already recorded" if have and not args.force else "WOULD ASK",
                             f"about {tokens} tokens in, at most ${worst:.4f}"))
                continue

            report = read_reply(llm, rules, text, sender_ok)
            offered = sorted(report["offered"])
            if name in MUST_OFFER and report["outcome"] != "offered":
                failures += 1
            if name not in MUST_OFFER and offered:
                failures += 1
            source = report.get("ai", {}).get("source", "")
            what = {"live": "RECORDED", "fixture": "already recorded"}.get(source, "NOT RECORDED")
            rows.append((name, what, f"outcome {report['outcome']}; offered {', '.join(offered) or 'nothing'}"
                         + (f"; reason: {report['reason']}" if report["reason"] else "")))
    width = max((len(r[0]) for r in rows), default=10)
    for name, what, detail in rows:
        print(f"{name:<{width}}  {what:<16}  {detail}")
    if args.dry_run:
        print("Nothing was sent. Run again without --dry-run to record.")
        return 0
    if failures:
        print(f"\n{failures} reply(ies) did not give the expected outcome. Try --force, or --model claude-sonnet-5-5.")
        return 1
    print("\nDone. Next: python -m scripts.preflight, then commit fixtures/llm/reply_read__*.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
