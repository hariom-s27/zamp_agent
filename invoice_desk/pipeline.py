"""The process: nine stages from a PDF to a posted, receipted decision.

    1 Intake  2 Read  3 Extract  4 Validate  5 Vendor  6 PO match  7 Duplicates
    8 Decide  9 Post and verify

`iter_run` yields an event before and after every stage, which is what lets the UI show
each stage as it executes. A stage can never crash a run: an unexpected error becomes a
finding that holds the invoice for a person (fail safe, never fail open).

Two properties are worth knowing before reading the code:

  * Intake is idempotent. A file that was processed before is recognised by its
    fingerprint and answered from that run's record: the same decision, nothing booked a
    second time, and a receipt saying the file came in again. It is read afresh only if
    nothing was booked for it and something the answer depends on has changed (the rules,
    the master data, the business date, the reading set-up).
  * Posting is atomic. The ledger entry, the receipt and the run record are written in
    one transaction. If any of them fails, none of them is kept, and the invoice is
    recorded as a hold instead, so the ledger and the decision cannot disagree.
"""
from __future__ import annotations

import hashlib
import time
import traceback
from datetime import date
from typing import Any, Iterator, Optional

from . import __version__, config
from . import textutil as T
from .decide import classify, decide, resubmission_note, worst_action
from .duplicates import check_duplicates
from .extract import BLANK_PROVEN, BLANK_UNCHECKED, BLANK_UNREAD, DOC_TYPE_NAMES, extract_invoice
from .llm import LlmAssist
from .masterdata import MasterData, load_master
from .models import FIELD_LABELS, FIELD_ORDER, MONEY_FIELDS, Decision, Finding, RunResult, StageResult
from .po_match import match_po
from .reader import ReaderError, get_engine, ink_share, read_pdf, sha256_of
from .rules import Rules, load_rules
from .second_read import reconcile
from .store import Store, canonical
from .validate import validate
from .vendor import check_vendor

STAGES = [("intake", "Intake"), ("read", "Read"), ("extract", "Extract"), ("validate", "Validate"),
          ("vendor", "Vendor"), ("po_match", "PO match"), ("duplicates", "Duplicates"), ("decide", "Decide"),
          ("post", "Post and verify")]
STAGE_HELP = {
    "intake": "Register the file and fingerprint it.",
    "read": "Get the words off the page: text layer, or straighten and OCR a scan.",
    "extract": "Find the fields and line items, each with its source on the page.",
    "validate": "Check the invoice against itself: required fields, arithmetic, GST, dates.",
    "vendor": "Identify the vendor in the master and compare bank details.",
    "po_match": "Find the PO, compare lines and prices with tolerances, check the PO balance.",
    "duplicates": "Look for the same invoice already on record, in any disguise.",
    "decide": "Apply rules.yaml to everything found: approve, hold or reject.",
    "post": "Write the ledger, read it back, and chain a receipt.",
}
ANALYSIS = ("extract", "validate", "vendor", "po_match", "duplicates")
# a run that ended on one of these did not give a final answer: the same file is read again
TRANSIENT = {"INTERNAL_ERROR", "POSTING_FAILED", "SCAN_NOT_READ"}


# How a value was read, in the words the evidence tables use.
READ_BY = {"text": "text layer", "ocr": "OCR", "derived": "worked out", "llm": "Claude, checked by code"}


def plain_names(names) -> str:
    """Field names the way a reader says them: 'invoice number, IGST, line items', not 'invoice_number, igst'."""
    said = []
    for name in names:
        label = FIELD_LABELS.get(name, str(name).replace("_", " "))
        said.append(" ".join(word if word.isupper() else word.lower() for word in label.split()))
    return ", ".join(said)


class PostingError(Exception):
    """The ledger entry could not be written, or did not read back as written."""


def currency_ok(inv) -> bool:
    return (inv.get("currency") or "INR") == "INR"
NOT_PAYABLE = {"quotation", "proforma", "purchase_order", "delivery_challan", "statement", "receipt", "sales_order"}


class Pipeline:
    def __init__(self, store: Optional[Store] = None, rules: Optional[Rules] = None,
                 master: Optional[MasterData] = None, llm: Optional[LlmAssist] = None,
                 today: Optional[date] = None, ocr_mode: Optional[str] = None, replay_resubmissions: bool = True):
        self.store = store or Store()
        self.rules = rules or load_rules()
        self.master = master or load_master()
        self.llm = llm or LlmAssist()
        self._today = today
        self.ocr_mode = ocr_mode
        self.replay_resubmissions = replay_resubmissions

    @property
    def today(self) -> date:
        return self._today or config.today()

    def modes(self, file_sha256: str = "") -> dict[str, Any]:
        engine = get_engine()
        return {"reader": "deterministic rules", "llm": self.llm.cfg.label, "llm_mode": self.llm.cfg.mode,
                "llm_model": self.llm.cfg.model if self.llm.cfg.mode != "off" else "",
                "llm_policy": self.rules.llm.assist, "ocr": engine.label, "ocr_mode": self.ocr_mode or config.ocr_mode(),
                "rules_version": self.rules.version, "today": self.today.isoformat(),
                "clock_frozen": bool(config.env("INVOICE_DESK_TODAY")) or self._today is not None,
                "engine_version": __version__, "context": self.context_key(file_sha256)}

    def context_key(self, file_sha256: str = "") -> str:
        """A fingerprint of everything outside the file that an answer depends on: the rules,
        the master data, the engine version, the business date, how pages get read, and the
        model set-up including whether a stored answer exists for this file. Two runs of one
        file with the same context key must give the same answer."""
        parts = {"rules": self.rules.sha256, "master": self.master.sha256, "engine": __version__,
                 "today": self.today.isoformat(), "ocr_engine": bool(get_engine().available),
                 "ocr_mode": self.ocr_mode or config.ocr_mode(), "llm": self.llm.context(file_sha256),
                 "llm_policy": self.rules.llm.assist}
        return hashlib.sha256(canonical(parts).encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------ running

    def run(self, content: bytes, file_name: str, source: str = "upload") -> RunResult:
        result: Optional[RunResult] = None
        for kind, payload in self.iter_run(content, file_name, source):
            if kind == "result":
                result = payload
        assert result is not None
        return result

    def iter_run(self, content: bytes, file_name: str, source: str = "upload") -> Iterator[tuple[str, Any]]:
        content = bytes(content or b"")
        sha256 = sha256_of(content)
        result = RunResult(run_id=self.store.allocate_run_id(self.today.year), created_at=config.now_utc().isoformat(),
                           file_name=(file_name or "upload.pdf").replace("\\", "/").split("/")[-1][:120],
                           file_sha256=sha256, source=source, size_bytes=len(content), modes=self.modes(sha256))
        state: dict[str, Any] = {"content": content, "result": result, "findings": [], "stop": None, "replay": None,
                                 "doc": None, "inv": None, "vendor": None, "po": None, "allocations": []}
        yield "begin", result
        for number, (key, title) in enumerate(STAGES, 1):
            yield "start", key
            stage = StageResult(key=key, number=number, title=title)
            t0 = time.perf_counter()
            if key == "post":
                self._run_post(state, stage, t0)          # fills and stores the stage itself; never raises
                yield "stage", stage
                continue
            if state["replay"] is not None and key in ("read",) + ANALYSIS:
                stage.status = "skipped"
                stage.summary = f"Not run again: this exact file was already processed as {state['replay'].run_id}."
            elif key in ANALYSIS and state["stop"]:
                stage.status, stage.summary = "skipped", f"Not run: {state['stop']}."
            else:
                try:
                    summary, blocks, findings = getattr(self, f"_stage_{key}")(state)
                    stage.summary, stage.blocks = summary, blocks
                    stage.findings = classify(findings, self.rules)
                    worst = worst_action(findings, self.rules)
                    stage.status = {"REJECT": "blocked", "HOLD": "attention"}.get(worst, "ok")
                    if key == "decide" and result.decision:
                        stage.status = {"REJECT": "blocked", "HOLD": "attention"}.get(result.decision.verdict, "ok")
                    state["findings"] += findings
                except Exception as exc:  # never crash a run; hold it for a person instead
                    finding = Finding(
                        code="INTERNAL_ERROR", stage=key,
                        summary=f"The {title} stage failed unexpectedly ({type(exc).__name__}). The invoice is held "
                                f"so that a person can process it by hand.",
                        evidence={"error": f"{type(exc).__name__}: {str(exc)[:300]}",
                                  "trace": traceback.format_exc(limit=4)[-900:]},
                        question="Process this invoice by hand and report the error to the process owner.")
                    stage.status, stage.summary = "error", f"Unexpected error: {type(exc).__name__}"
                    stage.findings = classify([finding], self.rules)
                    state["findings"].append(finding)
                    state["replay"] = None        # an answer could not be replayed: fall back to holding this file
                    if key in ("read", "extract") and not state["stop"]:
                        state["stop"] = f"the {title} stage failed"
                    if key == "decide":
                        # the decision is recomputed with the error in it: an approval never survives a failed decide
                        result.decision = self._decide_again(state)
            stage.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            result.stages.append(stage)
            yield "stage", stage
        yield "result", result

    # ------------------------------------------------------------------ stages

    def _stage_intake(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        r: RunResult = state["result"]
        rows = [{"Item": "Reference", "Value": r.run_id}, {"Item": "File", "Value": r.file_name},
                {"Item": "Size", "Value": f"{r.size_bytes / 1024:.1f} KB"},
                {"Item": "Document fingerprint (SHA-256)", "Value": r.file_sha256},
                {"Item": "Received", "Value": config.to_ist_text(r.created_at)},
                {"Item": "Source", "Value": r.source}]
        prior = self._prior_run(r.file_sha256) if self.replay_resubmissions else None
        if prior is None:
            rows.append({"Item": "Seen before", "Value": "no: this exact file has not been processed before"})
            return (f"{r.file_name}, {r.size_bytes / 1024:.1f} KB, fingerprint {r.file_sha256[:12]}",
                    [{"type": "kv", "title": "File registered", "rows": rows}], [])
        state["replay"], r.resubmission_of = prior, prior.run_id
        rows.append({"Item": "Seen before", "Value": f"yes: processed as {prior.run_id} on "
                                                     f"{config.to_ist_text(prior.created_at)} ({prior.verdict})"})
        return (f"{r.file_name}: the same file as {prior.run_id} (fingerprint {r.file_sha256[:12]}), so it is answered "
                f"from that record and not booked again",
                [{"type": "kv", "title": "File registered", "rows": rows}], [])

    def _prior_run(self, sha256: str) -> Optional[RunResult]:
        """The earlier run whose answer stands for this file, or None if the file is new or
        has to be read afresh."""
        prior = self.store.original_run_for_file(sha256)
        if prior is None or prior.decision is None:
            return None
        if self.store.find_by_file(sha256) is not None:
            return prior                      # it is in the ledger (posted or on hold): never booked twice
        if {f.code for f in prior.decision.reasons} & TRANSIENT:
            return None                       # the earlier attempt broke down: this is the retry
        if prior.modes.get("context") != self.context_key(sha256):
            return None                       # nothing was booked and something has changed since: read it again
        return prior

    def _stage_read(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        r: RunResult = state["result"]
        try:
            doc = read_pdf(state["content"], ocr_mode=self.ocr_mode, max_bytes=self.rules.intake.max_bytes,
                           max_pages=self.rules.intake.max_pages)
        except ReaderError as exc:
            state["stop"] = "the file could not be read"
            return exc.message, [], [Finding(
                code=exc.code, stage="read", summary=exc.message, expected="a readable PDF", observed=exc.message,
                question="Ask the sender for the document again, or process it by hand.",
                vendor_issue="the attached file could not be opened; please send the invoice again as a PDF"
                if exc.code == "UNREADABLE_FILE" else None)]
        state["doc"], state["doc_alt"] = doc, None
        if any(p.kind == "scan" for p in doc.pages):
            # a scan is read a second time from a black-and-white version of the same picture (second_read.py)
            try:
                alt = read_pdf(state["content"], ocr_mode=self.ocr_mode, variant="bw",
                               max_bytes=self.rules.intake.max_bytes, max_pages=self.rules.intake.max_pages)
                if alt.lines and all(p.kind != "scan_unread" for p in alt.pages):
                    state["doc_alt"] = alt
            except Exception:       # no second read: the first one stands on its own
                state["doc_alt"] = None
        alt = state["doc_alt"]
        r.page_count = len(doc.pages)
        findings: list[Finding] = []
        rows = []
        for p in doc.pages:
            ocr = p.ocr or {}
            how = {"text": "text layer", "scan": f"OCR ({'recorded output' if ocr.get('source') == 'recorded' else ocr.get('engine', '')})",
                   "scan_unread": "scan, not read", "empty": "blank"}[p.kind]
            second = (alt.page(p.number).ocr or {}) if alt is not None and alt.page(p.number) else {}
            rows.append({"Page": p.number, "Kind": "scanned image" if p.kind.startswith("scan") else p.kind,
                         "Read by": how, "Text-layer characters": p.chars,
                         "Straightened": f"{ocr['angle']:+.2f} deg" if ocr else "-",
                         "OCR confidence": f"{ocr['mean_conf']:.0f}%" if ocr.get("mean_conf") is not None else "-",
                         "Second read": f"{second['mean_conf']:.0f}%" if second.get("mean_conf") is not None
                         else ("-" if p.kind != "scan" else "not available"),
                         "Lines": len(p.lines), "Note": p.note})
            r.pages.append({"number": p.number, "kind": p.kind, "angle": float(ocr.get("angle", 0.0)),
                            "method": p.method, "ocr_source": ocr.get("source", ""),
                            "width": p.width, "height": p.height,
                            "prep": {"crop": ocr.get("crop"), "quad": ocr.get("quad"), "turns": int(ocr.get("turns") or 0)}})
        unread = [p for p in doc.pages if p.kind == "scan_unread"]
        if unread:
            findings.append(Finding(
                code="SCAN_NOT_READ", stage="read",
                summary=f"Page(s) {', '.join(str(p.number) for p in unread)} are scanned images, and no OCR engine is "
                        f"installed on this machine to read them.",
                expected="an OCR engine (Tesseract) or a text PDF", observed="scan with no engine available",
                question="Install Tesseract (see SETUP_WINDOWS.md) and process the file again, or key the invoice by hand."))
        if not doc.lines:
            state["stop"] = "no text could be read from the document"
            if not unread:
                findings.append(Finding(code="NOT_AN_INVOICE", stage="read",
                                        summary="The PDF contains no readable text.",
                                        expected="an invoice", observed="blank document"))
        scans = [p for p in doc.pages if p.kind == "scan"]
        parts = [f"{len(doc.pages)} page(s)"]
        if scans:
            o = scans[0].ocr or {}
            o2 = (alt.page(scans[0].number).ocr or {}) if alt is not None and alt.page(scans[0].number) else {}
            kind = "recorded OCR" if o.get("source") == "recorded" else "OCR"
            parts.append(f"scanned image, straightened by {abs(o.get('angle', 0)):.1f} deg, " +
                         (f"read twice ({kind} confidence {o.get('mean_conf', 0):.0f}% and {o2.get('mean_conf', 0):.0f}%)"
                          if o2 else f"{kind} confidence {o.get('mean_conf', 0):.0f}%"))
        elif not unread:
            parts.append("text layer")
        parts.append(f"{len(doc.lines)} lines")
        return ", ".join(parts), [{"type": "table", "title": "Pages", "rows": rows}], findings

    def _stage_extract(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        doc = state["doc"]
        company = self.rules.company
        inv = extract_invoice(doc, company.gstin, company.name, self.master.gstin_index, list(self.master.pos))
        findings: list[Finding] = []
        blocks: list[dict] = []
        reads = None
        if state.get("doc_alt") is not None:
            other = extract_invoice(state["doc_alt"], company.gstin, company.name, self.master.gstin_index, list(self.master.pos))
            photo = any((p.ocr or {}).get("photo") for p in doc.pages)
            used, inv, reads = reconcile({"gray": inv, "bw": other}, self.rules, self.today, photo=photo)
            inv.reads = reads
            if used == "bw":
                doc = state["doc"] = state["doc_alt"]
            differing = [row["label"].lower() for row in reads["fields"] if row["status"] == "the reads differ"]
            if differing:
                findings.append(Finding(
                    code="SCAN_READS_DIFFER", stage="extract",
                    summary=f"The scan was read twice and the reads differ on {', '.join(differing)}. The "
                            f"{reads['labels'][used]} is used because {reads['why']}.",
                    evidence={"used": used, "failed_checks": reads["failed_checks"], "fields": reads["fields"]}))
        self._blank_or_unread(state, inv, doc)
        state["inv"] = state["result"].invoice = inv
        kind = DOC_TYPE_NAMES.get(inv.doc_type, inv.doc_type)
        if inv.doc_type == "not_invoice":
            state["stop"] = "the document is not an invoice"
            findings.append(Finding(
                code="NOT_AN_INVOICE", stage="extract",
                summary=f"This document does not look like an invoice ({inv.doc_type_evidence}).",
                expected="a tax invoice", observed=inv.doc_type_evidence))
        elif inv.doc_type in NOT_PAYABLE:
            state["stop"] = f"the document is a {kind}, not a tax invoice"
            findings.append(Finding(
                code="NOT_A_TAX_INVOICE", stage="extract",
                summary=f"This is a {kind} (its title reads '{inv.doc_type_evidence}'), not a tax invoice. "
                        f"Nothing is payable against it.",
                expected="a tax invoice", observed=kind,
                vendor_issue=f"the document received is a {kind}; please send the tax invoice when the supply is made"))
        elif inv.doc_type in ("credit_note", "debit_note"):
            state["stop"] = f"{kind}s are handled by hand"
            findings.append(Finding(
                code="UNSUPPORTED_DOCUMENT", stage="extract",
                summary=f"This is a {kind}. Credit and debit notes are applied to the original invoice by a person.",
                expected="a tax invoice", observed=kind, question=f"Apply this {kind} to the invoice it refers to."))
        if state["stop"]:
            return f"Document type: {kind}", [{"type": "text", "title": "Document type", "text": f"{kind}: {inv.doc_type_evidence}"}], findings

        report = self.llm.assist(doc, inv, self.rules.llm.assist if currency_ok(inv) else "off", self.rules)
        if report.get("accepted"):
            how = {"fixture": "a recorded answer", "cache": "a stored answer", "live": "a live call"}.get(report.get("source"), "")
            findings.append(Finding(
                code="LLM_ASSISTED", stage="extract",
                summary=f"The rules reader could not read: {plain_names(report['gaps'])}. Claude ({report.get('model')}, "
                        f"{how}) supplied: {plain_names(report['accepted'])}. Each value was accepted only because "
                        f"its quote is on the page and states the value.",
                evidence={k: report.get(k) for k in ("source", "model", "prompt_version", "accepted", "rejected",
                                                     "disagreements", "self_confidence")}))
        currency = inv.get("currency") or "INR"
        if currency != "INR":
            # a foreign-currency invoice is read and shown, then handed to a person: nothing
            # in rupees (PO values, tolerances, the ledger) can be compared with it
            state["stop"] = f"the invoice is in {currency}"
            findings.append(Finding(
                code="CURRENCY_NOT_SUPPORTED", stage="extract",
                summary=f"The invoice is in {currency}. Only rupee invoices are processed automatically; purchase "
                        f"orders, tolerances and the ledger are all in rupees.",
                expected="INR", observed=currency,
                question="Process this foreign-currency invoice by hand (exchange rate, PO match and booking)."))

        def shown(amount):
            return T.fmt_money(amount, currency)
        rows = []
        for name in FIELD_ORDER:
            f = inv.fields.get(name)
            if f is None:
                continue
            value = "-" if not f.present else shown(f.value) if name in MONEY_FIELDS else \
                T.fmt_date(f.value) if name == "invoice_date" else str(f.value)
            src = f.source
            rows.append({"Field": f.label, "Value": value if f.present else f"not found ({f.missing_reason})" if f.missing_reason else "-",
                         "Confidence": f"{f.confidence:.2f}" if f.present else "-",
                         "Read by": READ_BY.get(src.method, src.method) if src and f.present else "-",
                         "Source on the page": (f"p{src.page}: {src.snippet[:90]}" if src and f.present else "-"),
                         "Notes": "; ".join(f.notes)})
        blocks.append({"type": "table", "title": "Fields, each with where it was read", "rows": rows})
        if inv.line_items:
            blocks.append({"type": "table", "title": "Line items", "rows": [
                {"#": it.line_no, "Description": it.description, "HSN": it.hsn or "-", "Qty": T.fmt_num(it.qty),
                 "Unit": it.uom or "-", "Price (printed)": shown(it.unit_price), "Amount (printed)": shown(it.amount),
                 "Pre-tax price": shown(it.unit_price_pre_tax), "Pre-tax amount": shown(it.amount_pre_tax),
                 "Confidence": f"{it.confidence:.2f}",
                 "Read by": READ_BY.get(it.source.method, it.source.method) if it.source else "-",
                 "Notes": "; ".join(it.notes + [x.replace("_", " ") for x in it.flags])} for it in inv.line_items]})
        if reads and reads.get("reads") == 2:
            names = reads["labels"]
            blocks.append({"type": "table", "title": f"Two reads of the scan (used: {names[reads['used']]})", "rows": [
                {"Field": row["label"], names[reads["used"]].capitalize() + " (used)": row["used"] if row["used"] is not None else "-",
                 names[reads["other"]].capitalize(): row["other"] if row["other"] is not None else "-",
                 "Result": row["status"]} for row in reads["fields"]]})
        if inv.notes:
            blocks.append({"type": "text", "title": "Reader notes", "text": " ".join(inv.notes)})
        if report.get("called") or report.get("error") or (report.get("gaps") and report.get("mode") != "off"
                                                             and self.rules.llm.assist != "off"):
            usage = report.get("usage") or {}
            where = {"fixture": "recorded answer replayed, no network", "cache": "stored answer reused, no network",
                     "live": "live call to the Claude API"}.get(report.get("source"), "not asked")
            blocks.append({"type": "kv", "title": "AI assist (Claude)", "rows": [
                {"Item": "Mode", "Value": self.llm.cfg.label},
                {"Item": "Why it was needed", "Value": ("the rules left gaps: " + plain_names(report["gaps"])) if report["gaps"]
                 else "second reader (rules.yaml: llm.assist = always)"},
                {"Item": "Answer", "Value": where + (f" ({report.get('model')}, prompt {report.get('prompt_version')})"
                                                     if report.get("called") else "")},
                {"Item": "Accepted (quote on the page, value in the quote)", "Value": plain_names(report["accepted"]) or "nothing"},
                {"Item": "Rejected", "Value": "; ".join(f"{plain_names([x['field']])}: {x['why']}"
                                                        for x in report["rejected"]) or "nothing"},
                {"Item": "Differences from the rules reader (rules kept)", "Value": "; ".join(
                    f"{plain_names([x['field']])}: rules {x['rules']}, model {x['llm']}"
                    for x in report["disagreements"]) or "none"},
                {"Item": "Model's own confidence", "Value": report.get("self_confidence") or "-"},
                {"Item": "Tokens and cost", "Value": (f"{usage.get('input_tokens', 0)} in, {usage.get('output_tokens', 0)} out; "
                                                      + (f"${report.get('cost_usd', 0):.4f} for this run" if report.get("source") == "live"
                                                         else "nothing spent on this run")) if report.get("called") else "none"},
                {"Item": "Note", "Value": report.get("error") or report.get("note") or "none"}]})
        present = sum(1 for n in FIELD_ORDER if inv.has(n))
        missing = [inv.fields[n].label.lower() for n in ("invoice_number", "invoice_date", "po_reference", "total") if not inv.has(n)]
        summary = f"{present} fields and {len(inv.line_items)} line item(s) read"
        if missing:
            summary += "; not found: " + ", ".join(missing)
        if report.get("accepted"):
            summary += f"; AI assist filled: {plain_names(report['accepted'])}"
        return summary, blocks, findings

    def _blank_or_unread(self, state: dict, inv, doc) -> None:
        """On a scan, a number label with no value beside it is either an empty box or something
        the OCR engine could not read. Look at the picture to tell which, because only an empty
        box is the vendor's to fix (audit finding 5)."""
        number = inv.fields.get("invoice_number")
        if number is None or number.present or number.missing_reason != BLANK_UNCHECKED or number.source is None:
            return
        page = doc.page(number.source.page)
        if page is None or not number.source.bbox:
            return
        ocr = page.ocr or {}
        share = ink_share(state["content"], page.number, number.source.bbox, page.width, float(ocr.get("angle", 0.0)),
                          prep={"crop": ocr.get("crop"), "quad": ocr.get("quad"), "turns": int(ocr.get("turns") or 0)})
        if share is None:
            return
        limit = self.rules.reading.blank_ink_max_pct / 100
        number.missing_reason = BLANK_PROVEN if share <= limit else BLANK_UNREAD
        number.notes.append(f"{share:.1%} of the space beside the label is ink on the scan "
                            f"(left blank means at most {limit:.1%})")

    def _stage_validate(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        findings, blocks = validate(state["inv"], self.rules, self.today)
        rows = blocks[0]["rows"]
        passed, failed = sum(r["Result"] == "pass" for r in rows), sum(r["Result"] == "FAIL" for r in rows)
        return f"{passed} of {passed + failed} checks passed" + (f", {failed} failed" if failed else ""), blocks, findings

    def _stage_vendor(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        verified: dict[str, list[str]] = {}
        for o in self.store.overrides(kind="bank_account"):
            verified.setdefault(o["vendor_id"], []).append(o["value"])
        vendor, findings, blocks, summary = check_vendor(state["inv"], self.master, verified, self.rules)
        state["vendor"], state["result"].vendor = vendor, summary
        if vendor is None:
            return "Vendor not identified in the master", blocks, findings
        bank = "bank account differs from the master" if any(f.code == "BANK_DETAILS_CHANGED" for f in findings) else \
            "bank account matches" if state["inv"].has("bank_account") else "no bank account on the invoice"
        return f"{vendor.vendor_id} {vendor.legal_name}, matched by {summary['method']}; {bank}", blocks, findings

    def _stage_po_match(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        po, findings, blocks, allocations = match_po(state["inv"], state["vendor"], self.master, self.store, self.rules)
        state["po"], state["allocations"], state["result"].po = po, allocations, po
        state["po_codes"] = {f.code for f in findings}
        if po is None:
            return "No purchase order matched", blocks, findings
        issues = [f for f in findings if self.rules.reason(f.code).action != "NOTE"]
        if not po["lines_total"]:
            return f"{po['po_number']} ({po['method']}) found, but there are no line items to compare with it", blocks, findings
        text = f"{po['po_number']} ({po['method']}): {po['lines_matched']} of {po['lines_total']} lines matched"
        text += f"; {len(issues)} issue(s)" if issues else "; prices, quantities and balance inside tolerance"
        return text, blocks, findings

    def _stage_duplicates(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        findings, blocks = check_duplicates(state["inv"], state["vendor"], state["result"].file_sha256, self.store,
                                            self.rules, state["allocations"], state["po"], state.get("po_codes", set()),
                                            self.master)
        return (findings[0].summary.split(":")[0] if findings else "No duplicate on record"), blocks, findings

    def _stage_decide(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        if state["replay"] is not None:
            return self._replay_decision(state)
        inv, vendor, r = state["inv"], state["vendor"], state["result"]
        is_invoice = inv is not None and inv.doc_type == "tax_invoice" and not state["stop"]
        ctx = {"vendor_name": vendor.legal_name if vendor else (inv.get("vendor_name") if inv else None),
               "invoice_number": inv.get("invoice_number") if inv else None,
               "invoice_date": inv.get("invoice_date") if inv else None, "total": inv.get("total") if inv else None,
               "po": state["po"], "payment_terms_days": vendor.payment_terms_days if vendor else None,
               "company_name": self.rules.company.name, "is_invoice": is_invoice,
               "currency": (inv.get("currency") if inv else None) or "INR"}
        state["ctx"], state["is_invoice"] = ctx, is_invoice
        r.decision_ctx = ctx
        decision = decide(state["findings"], self.rules, ctx)
        r.decision = decision
        rows = [{"Finding": f.code, "Meaning": f.title, "Action": f.action, "Owner": f.owner or "-", "Stage": f.stage}
                for f in decision.reasons + decision.also_noted + decision.notes]
        blocks = [{"type": "table", "title": "Findings and what rules.yaml says about each", "rows": rows}] if rows else \
            [{"type": "text", "title": "Findings", "text": "No finding blocks this invoice."}]
        blocks.append({"type": "text", "title": "Rule applied",
                       "text": "The strictest action among the findings wins: REJECT, then HOLD, otherwise APPROVE. "
                               f"Rules version {self.rules.version}."})
        return decision.headline, blocks, []

    def _status_now(self, prior: RunResult) -> tuple[str, str]:
        """(sentence for our screen, phrase for the vendor) on where the earlier run stands today."""
        entry = self.store.get_ledger(prior.ledger_id) if prior.ledger_id else None
        row = self.store.run_row(prior.run_id) or {}
        review = row.get("review_status") or ""
        if entry and entry["status"] == "POSTED":
            return (("That invoice was approved on review and is posted for payment." if review == "APPROVED"
                     else "That invoice is posted for payment."), "it is booked for payment")
        if entry and entry["status"] == "PENDING":
            return "That invoice is waiting in the review queue.", "it is with our team for review"
        if entry and entry["status"] == "REJECTED":
            return (("That invoice was rejected on review." if review == "REJECTED" else "That invoice was rejected."),
                    "it was not accepted, as explained in our earlier note")
        if review == "HANDLED":
            return "That document was processed by hand and closed.", "it has been dealt with"
        if prior.verdict == "HOLD":
            return "That document is waiting for a person.", "it is with our team for review"
        return "Nothing was booked for that document.", "it could not be accepted, as explained in our earlier note"

    def _replay_decision(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        """Stage 8 for a file that was processed before: the same decision, taken from the record."""
        prior: RunResult = state["replay"]
        r: RunResult = state["result"]
        ours, theirs = self._status_now(prior)
        d = prior.decision.model_copy(deep=True)
        when = config.to_ist_text(prior.created_at)
        d.headline = f"{d.verdict}: same file as {prior.run_id}, not booked again"
        d.explanation = (f"This exact file was already processed as {prior.run_id} on {when}. The decision then was "
                         f"{d.verdict} and it stands; the file is not booked again. {ours} What was decided then: "
                         f"{prior.decision.explanation}")
        d.next_action = f"Nothing new was booked. Open {prior.run_id} for the full record and its current status."
        d.vendor_draft = resubmission_note(prior.decision_ctx, when, theirs) if (prior.decision_ctx or {}).get("is_invoice") else None
        r.decision, r.decision_ctx = d, prior.decision_ctx
        r.invoice, r.vendor, r.po, r.pages, r.page_count = prior.invoice, prior.vendor, prior.po, prior.pages, prior.page_count
        state["is_invoice"] = False                 # nothing is booked for a re-submission
        rows = [{"Finding": f.code, "Meaning": f.title, "Action": f.action, "Owner": f.owner or "-", "Stage": f.stage}
                for f in d.reasons + d.also_noted + d.notes]
        blocks = [{"type": "kv", "title": "Re-submission", "rows": [
            {"Item": "Same file as", "Value": f"{prior.run_id}, received {when}"},
            {"Item": "Decision then", "Value": prior.decision.headline},
            {"Item": "Where it stands now", "Value": ours}]}]
        if rows:
            blocks.append({"type": "table", "title": f"Findings of {prior.run_id}, repeated here", "rows": rows})
        blocks.append({"type": "text", "title": "Rule applied",
                       "text": "The same file under the same rules gets the same answer. It is taken from the record "
                               "of the first run instead of being worked out again, and nothing is booked twice."})
        return d.headline, blocks, []

    def _decide_again(self, state: dict, extra: Optional[list[Finding]] = None) -> Decision:
        """Recompute the decision after something went wrong late in the run."""
        findings = state["findings"] + list(extra or [])
        try:
            return decide(findings, self.rules, state.get("ctx"))
        except Exception:  # decide itself is broken: hold by hand-built decision
            reasons = classify([f for f in findings if f.code in ("INTERNAL_ERROR", "POSTING_FAILED")][-1:], self.rules)
            return Decision(verdict="HOLD", headline="HOLD: processing error, handled by hand",
                            explanation=reasons[0].summary if reasons else "The decision stage failed.",
                            reasons=reasons, owners=[f.owner for f in reasons if f.owner],
                            questions=[f.question for f in reasons if f.question],
                            next_action="Waiting in the review queue for: AP analyst.")

    # ------------------------------------------------------------------ stage 9: post and verify

    def _run_post(self, state: dict, stage: StageResult, t0: float) -> None:
        """Write the ledger entry, the receipt and the run record as one unit. If that fails
        for any reason, nothing of it is kept and the invoice is recorded as a hold instead.
        If even that cannot be recorded, the run still ends as a hold and says so."""
        r: RunResult = state["result"]
        if r.decision is None:                      # the decide stage produced nothing at all
            r.decision = self._decide_again(state)
        try:
            self._post_once(state, stage, t0, [], strict=True)
            return
        except Exception as exc:
            problem = str(exc) if isinstance(exc, PostingError) else \
                f"the posting could not be completed ({type(exc).__name__}: {str(exc)[:120]})"
        wanted = r.decision.verdict
        finding = Finding(
            code="POSTING_FAILED", stage="post",
            summary=f"The decision was {wanted}, but {problem}. Nothing is treated as posted for payment; the "
                    f"invoice waits for a person.",
            expected="a ledger entry, a receipt and a run record written together", observed=problem,
            question="Check the ledger, then process this file again or post it by hand.")
        r.decision = self._decide_again(state, [finding])
        try:
            self._post_once(state, stage, t0, [finding], strict=False)
            return
        except Exception as exc:
            finding = finding.model_copy(update={
                "summary": finding.summary + f" The hold itself could not be recorded either "
                                             f"({type(exc).__name__}), so this run will not appear in the history: "
                                             f"note the reference and report it."})
        r.decision = self._decide_again(state, [finding])
        r.receipt, r.ledger_id = None, None
        stage.status, stage.findings = "error", classify([finding], self.rules)
        stage.summary = "Nothing could be recorded; the invoice is held and must be processed again"
        stage.blocks = [{"type": "kv", "title": "Posting and audit record", "rows": [
            {"Item": "Ledger entry", "Value": "none"}, {"Item": "Audit receipt", "Value": "could not be written"}]}]
        stage.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        r.stages.append(stage)
        r.duration_ms = round(sum(x.duration_ms for x in r.stages), 1)
        try:
            self.store.save_run(r)                  # last attempt, on its own; the decision text already says what happened
        except Exception:
            pass

    def _post_once(self, state: dict, stage: StageResult, t0: float, findings: list[Finding], strict: bool) -> None:
        r: RunResult = state["result"]
        store, decision = self.store, r.decision
        replay: Optional[RunResult] = state["replay"]
        r.receipt, r.ledger_id = None, None
        if state["content"]:
            store.save_file(r.file_sha256, state["content"])      # a file cannot be rolled back, so it goes first; saving it twice is harmless
        try:
            with store.transaction():
                ledger_info, rows = (None, [{"Item": "Ledger entry", "Value": f"none: already recorded under {replay.run_id}"}]) \
                    if replay is not None else self._book(state, decision, strict)
                kind = "resubmission" if replay is not None else "decision"
                receipt = store.append_receipt(kind, r.run_id, self._receipt_body(state, decision, ledger_info))
                r.receipt, r.ledger_id = receipt, (ledger_info or {}).get("ledger_id")
                rows += [{"Item": "Audit receipt", "Value": receipt["hash"]},
                         {"Item": "Linked to the receipt before it", "Value": receipt["prev_hash"]},
                         {"Item": "Rules file fingerprint", "Value": self.rules.sha256},
                         {"Item": "Master data fingerprint", "Value": self.master.sha256}]
                if findings:
                    stage.summary = f"Posting failed, invoice held; receipt {receipt['hash'][:12]} chained"
                elif replay is not None:
                    stage.summary = f"Nothing booked: re-submission of {replay.run_id} recorded; receipt {receipt['hash'][:12]} chained"
                else:
                    stage.summary = (f"Ledger {ledger_info['ledger_id']} {ledger_info['status'].lower()} and read back; "
                                     if ledger_info else "Nothing posted; ") + f"receipt {receipt['hash'][:12]} chained"
                stage.blocks = [{"type": "kv", "title": "Posting and audit record", "rows": rows}]
                stage.findings = classify(findings, self.rules)
                stage.status = {"REJECT": "blocked", "HOLD": "attention"}.get(worst_action(findings, self.rules), "ok")
                stage.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                r.stages.append(stage)
                r.duration_ms = round(sum(x.duration_ms for x in r.stages), 1)   # a viewer's pause between stages is not counted
                store.save_run(r)
        except BaseException:
            if r.stages and r.stages[-1] is stage:      # the database rolled back; so does the record in memory
                r.stages.pop()
            r.receipt, r.ledger_id = None, None
            raise

    def _book(self, state: dict, decision: Decision, strict: bool) -> tuple[Optional[dict], list[dict]]:
        """Write the ledger entry for a payable invoice and read it back. With `strict`, any
        problem raises PostingError (and the caller's transaction rolls back). Without it
        (recording a hold after a failed posting) the entry is kept if it could be written at all."""
        r: RunResult = state["result"]
        inv, vendor, store = state["inv"], state["vendor"], self.store
        if not (state.get("is_invoice") and inv is not None):
            return None, [{"Item": "Ledger entry", "Value": "none: this document is not a payable invoice"}]
        status = {"APPROVE": "POSTED", "HOLD": "PENDING", "REJECT": "REJECTED"}[decision.verdict]
        ledger_id = "L-" + r.run_id.split("-")[-1]
        entry = {"ledger_id": ledger_id, "run_id": r.run_id, "origin": "run",
                 "vendor_id": vendor.vendor_id if vendor else "", "invoice_no": inv.get("invoice_number"),
                 "invoice_date": inv.get("invoice_date"), "po_number": (state["po"] or {}).get("po_number"),
                 "taxable": inv.get("subtotal"), "tax": inv.get("tax_total"), "total": inv.get("total"),
                 "status": status, "file_sha256": r.file_sha256, "created_at": r.created_at,
                 "note": decision.headline}
        try:
            store.add_ledger(entry, state["allocations"])
        except Exception as exc:
            if strict:
                raise PostingError(f"the ledger could not be written ({type(exc).__name__})") from exc
            return None, [{"Item": "Ledger entry", "Value": "NOT written: the ledger could not be written"}]
        back = store.get_ledger(ledger_id)          # read the record back and compare (post, then verify)
        same = bool(back) and back["status"] == status \
            and (back["invoice_no"] or None) == (entry["invoice_no"] or None) \
            and T.to_decimal(back["total"]) == T.to_decimal(entry["total"]) \
            and len(back["lines"]) == len(state["allocations"])
        if strict and not same:
            raise PostingError("the entry read back from the ledger does not match what was written")
        if not back:
            return None, [{"Item": "Ledger entry", "Value": "NOT written: it could not be read back"}]
        meaning = {"POSTED": "posted: counts against the PO and will be paid",
                   "PENDING": "pending: waits in the review queue, does not use PO balance yet",
                   "REJECTED": "rejected: kept for the record, never paid"}[status]
        rows = [{"Item": "Ledger entry", "Value": f"{ledger_id}, {meaning}"}]
        rows.append({"Item": "Read back and compared", "Value": "status, invoice number, total and line count all match"
                     if same else "written, but it did not read back as written: held for a person"})
        return {"ledger_id": ledger_id, "status": status}, rows

    def _receipt_body(self, state: dict, decision: Decision, ledger_info: Optional[dict]) -> dict:
        r: RunResult = state["result"]
        inv, vendor, replay = state["inv"], state["vendor"], state["replay"]
        body = {
            "file": {"name": r.file_name, "sha256": r.file_sha256, "bytes": r.size_bytes},
            "rules": {"version": self.rules.version, "sha256": self.rules.sha256},
            "master_data_sha256": self.master.sha256, "engine_version": __version__,
            "modes": {k: r.modes.get(k) for k in ("llm", "ocr", "ocr_mode", "today", "context")},
            "invoice": None if inv is None else {
                "vendor_id": vendor.vendor_id if vendor else None,
                "vendor_name": vendor.legal_name if vendor else inv.get("vendor_name"),
                "invoice_number": inv.get("invoice_number"), "invoice_date": inv.get("invoice_date"),
                "po_number": (state["po"] or {}).get("po_number"), "taxable": inv.get("subtotal"),
                "tax": inv.get("tax_total"), "total": inv.get("total")},
            "checks": [{"code": f.code, "action": f.action, "stage": f.stage, "expected": f.expected,
                        "observed": f.observed} for f in decision.reasons + decision.also_noted + decision.notes],
            "verdict": decision.verdict, "owners": decision.owners, "ledger": ledger_info, "actor": "process",
        }
        if replay is not None:
            body.update({"resubmission_of": replay.run_id, "original_receipt": (replay.receipt or {}).get("hash"),
                         "invoice": None, "booked": False})
        return body
