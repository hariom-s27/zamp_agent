"""The process: nine stages from a PDF to a posted, receipted decision.

    1 Intake  2 Read  3 Extract  4 Validate  5 Vendor  6 PO match  7 Duplicates
    8 Decide  9 Post and verify

`iter_run` yields an event before and after every stage, which is what lets the UI show
each stage as it executes. A stage can never crash a run: an unexpected error becomes a
finding that holds the invoice for a person (fail safe, never fail open).
"""
from __future__ import annotations

import time
import traceback
from datetime import date
from typing import Any, Iterator, Optional

from . import __version__, config
from . import textutil as T
from .decide import classify, decide, worst_action
from .duplicates import check_duplicates
from .extract import DOC_TYPE_NAMES, extract_invoice
from .llm import LlmAssist
from .masterdata import MasterData, load_master
from .models import FIELD_ORDER, MONEY_FIELDS, Decision, Finding, RunResult, StageResult
from .po_match import match_po
from .reader import ReaderError, get_engine, read_pdf, sha256_of
from .rules import Rules, load_rules
from .second_read import LABEL as READ_LABEL
from .second_read import reconcile
from .store import Store
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


def currency_ok(inv) -> bool:
    return (inv.get("currency") or "INR") == "INR"
NOT_PAYABLE = {"quotation", "proforma", "purchase_order", "delivery_challan", "statement", "receipt", "sales_order"}


class Pipeline:
    def __init__(self, store: Optional[Store] = None, rules: Optional[Rules] = None,
                 master: Optional[MasterData] = None, llm: Optional[LlmAssist] = None,
                 today: Optional[date] = None, ocr_mode: Optional[str] = None):
        self.store = store or Store()
        self.rules = rules or load_rules()
        self.master = master or load_master()
        self.llm = llm or LlmAssist()
        self._today = today
        self.ocr_mode = ocr_mode

    @property
    def today(self) -> date:
        return self._today or config.today()

    def modes(self) -> dict[str, Any]:
        engine = get_engine()
        return {"reader": "deterministic rules", "llm": self.llm.cfg.label, "llm_mode": self.llm.cfg.mode,
                "llm_policy": self.rules.llm.assist, "ocr": engine.label, "ocr_mode": self.ocr_mode or config.ocr_mode(),
                "rules_version": self.rules.version, "today": self.today.isoformat(),
                "clock_frozen": bool(config.env("INVOICE_DESK_TODAY")) or self._today is not None,
                "engine_version": __version__}

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
        result = RunResult(run_id=self.store.allocate_run_id(), created_at=config.now_utc().isoformat(),
                           file_name=(file_name or "upload.pdf").replace("\\", "/").split("/")[-1][:120],
                           file_sha256=sha256_of(content), source=source, size_bytes=len(content), modes=self.modes())
        state: dict[str, Any] = {"content": content, "result": result, "findings": [], "stop": None,
                                 "doc": None, "inv": None, "vendor": None, "po": None, "allocations": []}
        yield "begin", result
        for number, (key, title) in enumerate(STAGES, 1):
            yield "start", key
            stage = StageResult(key=key, number=number, title=title)
            t0 = time.perf_counter()
            if key in ANALYSIS and state["stop"]:
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
                    if key in ("read", "extract") and not state["stop"]:
                        state["stop"] = f"the {title} stage failed"
                    if key in ("decide", "post"):
                        # the decision is recomputed with the error in it: an approval never
                        # survives a failed decide or a failed posting
                        result.decision = self._decide_again(state)
            stage.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            result.stages.append(stage)
            if key == "post":
                # processing time is the sum of the stage times; a viewer's pause between stages is not counted
                result.duration_ms = round(sum(s.duration_ms for s in result.stages), 1)
                try:
                    self.store.save_run(result)
                except Exception:
                    pass
            yield "stage", stage
        yield "result", result

    # ------------------------------------------------------------------ stages

    def _stage_intake(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        r: RunResult = state["result"]
        rows = [{"Item": "Run", "Value": r.run_id}, {"Item": "File", "Value": r.file_name},
                {"Item": "Size", "Value": f"{r.size_bytes / 1024:.1f} KB"},
                {"Item": "Fingerprint (SHA-256)", "Value": r.file_sha256},
                {"Item": "Received", "Value": config.to_ist_text(r.created_at)},
                {"Item": "Source", "Value": r.source}]
        return (f"{r.file_name}, {r.size_bytes / 1024:.1f} KB, fingerprint {r.file_sha256[:12]}",
                [{"type": "kv", "title": "File registered", "rows": rows}], [])

    def _stage_read(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        r: RunResult = state["result"]
        try:
            doc = read_pdf(state["content"], ocr_mode=self.ocr_mode)
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
                alt = read_pdf(state["content"], ocr_mode=self.ocr_mode, variant="bw")
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
                         "Second read (black and white)": f"{second['mean_conf']:.0f}%" if second.get("mean_conf") is not None
                         else ("-" if p.kind != "scan" else "not available"),
                         "Lines": len(p.lines), "Note": p.note})
            r.pages.append({"number": p.number, "kind": p.kind, "angle": float(ocr.get("angle", 0.0)),
                            "method": p.method, "ocr_source": ocr.get("source", ""),
                            "width": p.width, "height": p.height})
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
            used, inv, reads = reconcile({"gray": inv, "bw": other}, self.rules, self.today)
            inv.reads = reads
            if used == "bw":
                doc = state["doc"] = state["doc_alt"]
            differing = [row["label"].lower() for row in reads["fields"] if row["status"] == "the reads differ"]
            if differing:
                findings.append(Finding(
                    code="SCAN_READS_DIFFER", stage="extract",
                    summary=f"The scan was read twice and the reads differ on {', '.join(differing)}. The "
                            f"{READ_LABEL[used]} is used because {reads['why']}.",
                    evidence={"used": used, "failed_checks": reads["failed_checks"], "fields": reads["fields"]}))
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

        report = self.llm.assist(doc, inv, self.rules.llm.assist if currency_ok(inv) else "off")
        if report.get("accepted"):
            findings.append(Finding(
                code="LLM_ASSISTED", stage="extract",
                summary=f"The rules reader could not read {', '.join(report['gaps'])}. LLM assist "
                        f"({report.get('source')}, {report.get('model')}) supplied {', '.join(report['accepted'])}; each "
                        f"value was accepted only because its quote is on the page and states the value.",
                evidence={k: report.get(k) for k in ("source", "model", "accepted", "rejected", "disagreements")}))
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
                         "Read by": src.method if src and f.present else "-",
                         "Source on the page": (f"p{src.page}: {src.snippet[:90]}" if src and f.present else "-"),
                         "Notes": "; ".join(f.notes)})
        blocks.append({"type": "table", "title": "Fields, each with where it was read", "rows": rows})
        if inv.line_items:
            blocks.append({"type": "table", "title": "Line items", "rows": [
                {"#": it.line_no, "Description": it.description, "HSN": it.hsn or "-", "Qty": T.fmt_num(it.qty),
                 "Unit": it.uom or "-", "Price (printed)": shown(it.unit_price), "Amount (printed)": shown(it.amount),
                 "Pre-tax price": shown(it.unit_price_pre_tax), "Pre-tax amount": shown(it.amount_pre_tax),
                 "Confidence": f"{it.confidence:.2f}", "Read by": it.source.method if it.source else "-",
                 "Notes": "; ".join(it.notes + [x.replace("_", " ") for x in it.flags])} for it in inv.line_items]})
        if reads and reads.get("reads") == 2:
            blocks.append({"type": "table", "title": f"Two reads of the scan (used: {READ_LABEL[reads['used']]})", "rows": [
                {"Field": row["label"], READ_LABEL[reads["used"]].capitalize() + " (used)": row["used"] if row["used"] is not None else "-",
                 READ_LABEL[reads["other"]].capitalize(): row["other"] if row["other"] is not None else "-",
                 "Result": row["status"]} for row in reads["fields"]]})
        if inv.notes:
            blocks.append({"type": "text", "title": "Reader notes", "text": " ".join(inv.notes)})
        if report.get("called") or report.get("error"):
            blocks.append({"type": "kv", "title": "LLM assist", "rows": [
                {"Item": "Why it was called", "Value": "rules left gaps: " + ", ".join(report["gaps"])},
                {"Item": "Source", "Value": f"{report.get('source', '-')} ({report.get('model', '-')})"},
                {"Item": "Accepted (quote verified on the page)", "Value": ", ".join(report["accepted"]) or "nothing"},
                {"Item": "Rejected", "Value": "; ".join(f"{x['field']}: {x['why']}" for x in report["rejected"]) or "nothing"},
                {"Item": "Disagreements with the rules reader", "Value": "; ".join(
                    f"{x['field']}: rules {x['rules']}, LLM {x['llm']}" for x in report["disagreements"]) or "none"},
                {"Item": "Error", "Value": report.get("error") or "none"}]})
        present = sum(1 for n in FIELD_ORDER if inv.has(n))
        missing = [inv.fields[n].label.lower() for n in ("invoice_number", "invoice_date", "po_reference", "total") if not inv.has(n)]
        summary = f"{present} fields and {len(inv.line_items)} line item(s) read"
        if missing:
            summary += "; not found: " + ", ".join(missing)
        if report.get("accepted"):
            summary += f"; LLM assist filled {', '.join(report['accepted'])}"
        return summary, blocks, findings

    def _stage_validate(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        findings, blocks = validate(state["inv"], self.rules, self.today)
        rows = blocks[0]["rows"]
        passed, failed = sum(r["Result"] == "pass" for r in rows), sum(r["Result"] == "FAIL" for r in rows)
        return f"{passed} of {passed + failed} checks passed" + (f", {failed} failed" if failed else ""), blocks, findings

    def _stage_vendor(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        verified: dict[str, list[str]] = {}
        for o in self.store.overrides(kind="bank_account"):
            verified.setdefault(o["vendor_id"], []).append(o["value"])
        vendor, findings, blocks, summary = check_vendor(state["inv"], self.master, verified)
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
        inv, vendor, r = state["inv"], state["vendor"], state["result"]
        is_invoice = inv is not None and inv.doc_type == "tax_invoice" and not state["stop"]
        ctx = {"vendor_name": vendor.legal_name if vendor else (inv.get("vendor_name") if inv else None),
               "invoice_number": inv.get("invoice_number") if inv else None,
               "invoice_date": inv.get("invoice_date") if inv else None, "total": inv.get("total") if inv else None,
               "po": state["po"], "payment_terms_days": vendor.payment_terms_days if vendor else None,
               "company_name": self.rules.company.name, "is_invoice": is_invoice,
               "currency": (inv.get("currency") if inv else None) or "INR"}
        state["ctx"], state["is_invoice"] = ctx, is_invoice
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

    def _decide_again(self, state: dict, extra: Optional[list[Finding]] = None) -> Decision:
        """Recompute the decision after something went wrong late in the run."""
        findings = state["findings"] + list(extra or [])
        try:
            return decide(findings, self.rules, state.get("ctx"))
        except Exception:  # decide itself is broken: hold by hand-built decision
            reasons = classify([f for f in findings if f.code == "INTERNAL_ERROR"][-1:], self.rules)
            return Decision(verdict="HOLD", headline="HOLD: processing error, handled by hand",
                            explanation=reasons[0].summary if reasons else "The decision stage failed.",
                            reasons=reasons, owners=[f.owner for f in reasons if f.owner],
                            questions=[f.question for f in reasons if f.question],
                            next_action="Waiting in the review queue for: AP analyst.")

    def _stage_post(self, state: dict) -> tuple[str, list[dict], list[Finding]]:
        r: RunResult = state["result"]
        inv, vendor = state["inv"], state["vendor"]
        store = self.store
        rows: list[dict] = []
        findings: list[Finding] = []
        ledger_info = None
        if state.get("is_invoice") and inv is not None:
            status = {"APPROVE": "POSTED", "HOLD": "PENDING", "REJECT": "REJECTED"}[r.decision.verdict]
            ledger_id = "L-" + r.run_id.split("-")[-1]
            entry = {"ledger_id": ledger_id, "run_id": r.run_id, "origin": "run",
                     "vendor_id": vendor.vendor_id if vendor else "", "invoice_no": inv.get("invoice_number"),
                     "invoice_date": inv.get("invoice_date"), "po_number": (state["po"] or {}).get("po_number"),
                     "taxable": inv.get("subtotal"), "tax": inv.get("tax_total"), "total": inv.get("total"),
                     "status": status, "file_sha256": r.file_sha256, "created_at": r.created_at,
                     "note": r.decision.headline}
            problem = ""
            try:
                store.add_ledger(entry, state["allocations"])
                back = store.get_ledger(ledger_id)  # read the record back and compare (post, then verify)
                same = bool(back) and back["status"] == status \
                    and (back["invoice_no"] or None) == (entry["invoice_no"] or None) \
                    and T.to_decimal(back["total"]) == T.to_decimal(entry["total"]) \
                    and len(back["lines"]) == len(state["allocations"])
                if not same:
                    problem = "the entry read back from the ledger does not match what was written"
            except Exception as exc:
                problem = f"the ledger could not be written ({type(exc).__name__})"
            if problem:
                # fail safe: whatever was decided, nothing counts as posted until a person has looked
                finding = Finding(
                    code="POSTING_FAILED", stage="post",
                    summary=f"The decision was {r.decision.verdict}, but {problem}. Nothing is treated as posted "
                            f"for payment; the invoice waits for a person.",
                    expected="a ledger entry that reads back exactly as written", observed=problem,
                    question="Check the ledger, then process this file again or post it by hand.")
                findings.append(finding)
                r.decision = self._decide_again(state, findings)
                status = {"HOLD": "PENDING", "REJECT": "REJECTED"}[r.decision.verdict]
                try:
                    if store.get_ledger(ledger_id):
                        store.set_ledger_status(ledger_id, status, "posting could not be verified")
                        r.ledger_id = ledger_id
                        ledger_info = {"ledger_id": ledger_id, "status": status}
                except Exception:
                    ledger_info = None
                rows.append({"Item": "Ledger entry", "Value": f"NOT posted: {problem}"})
            else:
                r.ledger_id = ledger_id
                ledger_info = {"ledger_id": ledger_id, "status": status}
                meaning = {"POSTED": "posted: counts against the PO and will be paid",
                           "PENDING": "pending: waits in the review queue, does not use PO balance yet",
                           "REJECTED": "rejected: kept for the record, never paid"}[status]
                rows += [{"Item": "Ledger entry", "Value": f"{ledger_id}, {meaning}"},
                         {"Item": "Read back and compared", "Value": "status, invoice number, total and line count all match"}]
        else:
            rows.append({"Item": "Ledger entry", "Value": "none: this document is not a payable invoice"})
        decision = r.decision
        body = {
            "file": {"name": r.file_name, "sha256": r.file_sha256, "bytes": r.size_bytes},
            "rules": {"version": self.rules.version, "sha256": self.rules.sha256},
            "master_data_sha256": self.master.sha256, "engine_version": __version__,
            "modes": {k: r.modes.get(k) for k in ("llm", "ocr", "ocr_mode", "today")},
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
        receipt = store.append_receipt("decision", r.run_id, body)
        r.receipt = receipt
        store.save_file(r.file_sha256, state["content"])
        rows += [{"Item": "Receipt hash", "Value": receipt["hash"]},
                 {"Item": "Links to previous receipt", "Value": receipt["prev_hash"]},
                 {"Item": "Rules file hash", "Value": self.rules.sha256},
                 {"Item": "Master data hash", "Value": self.master.sha256}]
        summary = (f"Ledger {ledger_info['ledger_id']} {ledger_info['status'].lower()} and read back; " if ledger_info
                   else "Nothing posted; ") + f"receipt {receipt['hash'][:12]} chained"
        if findings:
            summary = "Posting failed, invoice held; " + f"receipt {receipt['hash'][:12]} chained"
        return summary, [{"type": "kv", "title": "Posting and audit record", "rows": rows}], findings
