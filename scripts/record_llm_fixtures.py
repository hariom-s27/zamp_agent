"""Record real Claude answers for every bundled sample that needs one, so the demo can
replay them with no key and no network (FIXTURE mode).

    python -m scripts.record_llm_fixtures --dry-run     # what would be sent and what it could cost; no network, no key
    python -m scripts.record_llm_fixtures               # record whatever is not recorded yet
    python -m scripts.record_llm_fixtures --force       # record again, replacing what is there
    python -m scripts.record_llm_fixtures --model claude-sonnet-5-5
    python -m scripts.record_llm_fixtures --only 10_ai_assist_letter_invoice

It needs ANTHROPIC_API_KEY in .env (or the environment). Two kinds of answer are recorded:

  * extract      the model reads a document the rules could not (sample 10, the letter)
  * vendor_note  the model rewords the note to the vendor that the rules drafted

Every sample is run through the real pipeline, in the documented order, on a scratch copy
of the seeded books, so what gets recorded is what the demo will ask for. Recordings go to
fixtures/llm/ and belong in the repository. Running the script twice costs nothing the
second time: an answer that is already recorded is not asked for again.

The script exits with 1 if a sample that should be approved with AI assist is not, so a
bad recording is noticed here and not in front of an audience.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

from invoice_desk import config
from invoice_desk import samples as sample_lib
from invoice_desk.decide import note_must_keep
from invoice_desk.llm import LlmAssist, SpendLedger, cost_usd, estimate_tokens
from invoice_desk.pipeline import Pipeline
from invoice_desk.store import Store


HTTP_CLIENT = None       # tests put an SDK http client with a fake transport here; None means the real API


def _plan(llm: LlmAssist, purpose: str, document: str) -> str:
    request = llm.request(purpose, document)
    tokens = estimate_tokens(json.dumps(request))
    worst = cost_usd(llm.cfg, llm.cfg.model, tokens, llm.cfg.max_output_tokens)
    return f"about {tokens} tokens in, at most {llm.cfg.max_output_tokens} out, at most ${worst:.4f}"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Record Claude answers for the bundled samples.")
    parser.add_argument("--dry-run", action="store_true", help="show what would be sent and its worst-case cost; no network")
    parser.add_argument("--force", action="store_true", help="record again, replacing existing recordings")
    parser.add_argument("--model", default="", help="model to record with (default: LLM_MODEL, else claude-haiku-5-5)")
    parser.add_argument("--only", default="", help="record for one sample id only")
    parser.add_argument("--show-request", action="store_true", help="with --dry-run, print the full request body for sample 10")
    args = parser.parse_args(argv)

    cfg = config.llm_config(mode="cached-live")
    if args.model:
        cfg = dataclasses.replace(cfg, model=args.model)
    if not cfg.api_key and not args.dry_run:
        print("No API key found. Put ANTHROPIC_API_KEY=... in .env (see .env.example), or try --dry-run first.")
        return 2
    print(f"Model {cfg.model}; timeout {cfg.timeout_s:g} s; {cfg.max_retries} retries; cap ${cfg.per_invoice_cap_usd:.2f} per "
          f"invoice and ${cfg.daily_cap_usd:.2f} per day" + ("; DRY RUN, nothing is sent" if args.dry_run else ""))

    failures, rows = 0, []
    with tempfile.TemporaryDirectory(prefix="invoice_desk_record_") as tmp:
        work = Path(tmp)
        # Only the repository's recordings count here. An answer cached on this machine by the app is
        # ignored, so that what gets committed is complete on its own.
        llm = LlmAssist(dataclasses.replace(cfg, mode="off" if args.dry_run else "cached-live"),
                        cache_dir=work / "unused_cache", ledger=SpendLedger(), save_to="fixtures", http_client=HTTP_CLIENT)
        llm.force = args.force
        planner = LlmAssist(cfg, cache_dir=work / "unused_cache")
        # the same business date as the app, because a drafted message can carry dates
        pipeline = Pipeline(store=Store(work / "record.db"), llm=llm, today=sample_lib.business_date())
        for sample in sample_lib.load_manifest():
            if args.only and sample["id"] != args.only:
                continue
            content = sample_lib.sample_bytes(sample)
            result = pipeline.run(content, sample_lib.received_name(sample), "record")
            report = (result.invoice.llm if result.invoice else None) or {}
            inv = result.invoice
            if args.dry_run:
                # would the model be asked? The same test the pipeline applies: a payable rupee invoice read with gaps
                ask = inv is not None and inv.doc_type == "tax_invoice" and (inv.get("currency") or "INR") == "INR" \
                    and bool(LlmAssist.gaps(inv)) and pipeline.rules.llm.assist != "off" and result.stages[2].status != "skipped"
            else:
                ask = bool(report.get("gaps")) and not result.resubmission_of        # the pipeline did ask (or tried to)
            if ask and args.dry_run:
                from invoice_desk import reader
                text = reader.read_pdf(content).text
                have = planner.has_recording(result.file_sha256)
                rows.append((sample["id"], "extract", "already recorded" if have and not args.force else "WOULD ASK",
                             _plan(planner, "extract", text[:12000]), result.verdict))
                if args.show_request and sample["id"].startswith("10"):
                    print(json.dumps(planner.request("extract", text[:12000]), indent=2, ensure_ascii=False))
            elif ask:
                usage = report.get("usage") or {}
                what = {"live": "RECORDED", "fixture": "already recorded"}.get(report.get("source"), "NOT RECORDED")
                detail = (f"{usage.get('input_tokens', 0)} in, {usage.get('output_tokens', 0)} out, ${report.get('cost_usd', 0):.4f}; "
                          f"accepted: {', '.join(report.get('accepted') or []) or 'nothing'}"
                          + (f"; rejected: {'; '.join(x['field'] + ' (' + x['why'] + ')' for x in report['rejected'])}"
                             if report.get("rejected") else "")) if report.get("called") else (report.get("error") or report.get("note") or "")
                ok, want, _ = sample_lib.check(sample, result)
                expects_ai = "with_llm" in sample
                if expects_ai and not (ok and sample_lib.llm_supplied_values(result)):
                    failures += 1
                    detail += f"  <-- expected {sample['with_llm']['expected_verdict']} with AI assist, got {result.verdict}"
                rows.append((sample["id"], "extract", what, detail, result.verdict))
            note = result.decision.vendor_draft if result.decision else None
            if note and not result.resubmission_of:
                if args.dry_run:
                    have = planner.find("vendor_note", __import__("hashlib").sha256(note.encode("utf-8")).hexdigest(), cfg.model)
                    rows.append((sample["id"], "vendor_note", "already recorded" if have and not args.force else "WOULD ASK",
                                 _plan(planner, "vendor_note", note), result.verdict))
                else:
                    draft = llm.draft_vendor_note(note, note_must_keep(result.decision_ctx, note))
                    usage = draft.get("usage") or {}
                    what = {"live": "RECORDED", "fixture": "already recorded"}.get(draft.get("source"), "NOT RECORDED")
                    detail = (f"{usage.get('input_tokens', 0)} in, {usage.get('output_tokens', 0)} out, ${draft.get('cost_usd', 0):.4f}; "
                              + ("rewording accepted" if draft.get("accepted") else f"rewording NOT used: {draft.get('why')}")) \
                        if draft.get("called") else (draft.get("error") or draft.get("note") or "")
                    rows.append((sample["id"], "vendor_note", what, detail, result.verdict))
        spent, calls = (0.0, 0) if args.dry_run else (llm.ledger.spent_today(), llm.ledger.calls_today())

    width = max((len(r[0]) for r in rows), default=10)
    print(f"\n{'sample':<{width}}  purpose      result            verdict  detail")
    for sample_id, purpose, what, detail, verdict in rows:
        print(f"{sample_id:<{width}}  {purpose:<11}  {what:<16}  {verdict:<7}  {detail}")
    recordings = sorted((config.FIXTURES_DIR / "llm").glob("*.json"))
    print(f"\n{len(recordings)} recording(s) in fixtures/llm" + ("" if args.dry_run else f"; spent today ${spent:.4f} over {calls} call(s)"))
    if args.dry_run:
        print("Nothing was sent. Run again without --dry-run to record.")
        return 0
    if failures:
        print(f"\n{failures} sample(s) did not reach the documented result with AI assist. Read the 'rejected' notes above: "
              "each says which value failed which check. Try --force, or --model claude-sonnet-5-5.")
        return 1
    print("\nDone. Next: python -m scripts.preflight, then commit fixtures/llm/*.json so the hosted demo can replay them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
