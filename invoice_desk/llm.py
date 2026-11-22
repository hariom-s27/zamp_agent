"""Optional LLM assist for Stage 3 (D03).

The rules reader comes first. When it leaves a required field empty or finds no item
table (a free-form invoice written as a letter, say), and a model is configured, one
call asks the model for the fields together with a verbatim quote for each. Then:

  * a value is accepted only if its quote is found on the page AND the value is stated
    in that quote (the citation check from my outcome project, reduced to its core);
  * an accepted value fills a gap; it never overwrites a value the rules read cleanly;
  * the raw response is saved under fixtures/llm/, so the same document can be replayed
    later with no network (LLM_MODE=replay).

The model never sees the PO register and never takes part in the decision.
Any OpenAI-compatible chat endpoint works: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

from . import config
from . import textutil as T
from .models import FieldValue, FIELD_LABELS, Invoice, LineItem, SourceRef
from .reader import DocRead

REQUIRED = ("invoice_number", "invoice_date", "total")
HEADER_FIELDS = {"invoice_number": "id", "invoice_date": "date", "po_reference": "id", "taxable_value": "amount",
                 "cgst": "amount", "sgst": "amount", "igst": "amount", "total": "amount"}
TARGET = {"taxable_value": "subtotal"}
MAX_CHARS = 12000

SYSTEM_PROMPT = "You extract data from invoices. You answer with one JSON object and nothing else."
USER_PROMPT = """Read the invoice text below and return this JSON object:

{
  "invoice_number": {"value": "...", "quote": "..."},
  "invoice_date":   {"value": "YYYY-MM-DD", "quote": "..."},
  "po_reference":   {"value": "...", "quote": "..."},
  "taxable_value":  {"value": 0.00, "quote": "..."},
  "cgst":           {"value": 0.00, "quote": "..."},
  "sgst":           {"value": 0.00, "quote": "..."},
  "igst":           {"value": 0.00, "quote": "..."},
  "total":          {"value": 0.00, "quote": "..."},
  "line_items": [
    {"description": "...", "hsn": "...", "qty": 0, "unit_price": 0.00, "amount": 0.00, "quote": "..."}
  ]
}

Rules:
- "quote" must be copied character for character from the invoice text, and it must contain the value.
  For a line item, the quote is the sentence or row that states its quantity, unit price and amount.
- Use null for anything the invoice does not state. Do not calculate, infer or guess a value.
- Amounts are plain numbers without currency symbols or thousands separators.
- taxable_value is the amount before tax; total is the amount payable including tax.

Invoice text:
<<<
{document}
>>>"""

Transport = Callable[[dict, config.LlmConfig], str]


def http_transport(payload: dict, cfg: config.LlmConfig) -> str:
    """POST to an OpenAI-compatible /chat/completions endpoint and return the reply text."""
    import requests
    response = requests.post(cfg.base_url.rstrip("/") + "/chat/completions",
                             headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
                             json=payload, timeout=cfg.timeout_s)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def parse_json_reply(text: str) -> dict:
    """The JSON object in a model reply, tolerating code fences and stray prose around it."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    data = json.loads(cleaned[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    return data


def _numbers_in(text: str) -> list[Decimal]:
    found = []
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        value = T.parse_amount(token)
        if value is not None:
            found.append(value)
    for word in re.findall(r"[A-Za-z]+", text or ""):
        value = T.words_to_amount(word)
        if value is not None and value <= 100:
            found.append(value)
    return found


def value_in_quote(kind: str, value: Any, quote: str) -> bool:
    """Is `value` actually stated in `quote`?"""
    if value in (None, "") or not quote:
        return False
    if kind == "id":
        return T.invoice_no_key(str(value)) != "" and T.invoice_no_key(str(value)) in T.invoice_no_key(quote)
    if kind == "date":
        got = T.parse_date(quote)
        return bool(got) and got[0].isoformat() == str(value)[:10]
    number = T.to_decimal(value)
    return number is not None and any(abs(number - n) <= Decimal("0.005") for n in _numbers_in(quote))


class LlmAssist:
    def __init__(self, cfg: Optional[config.LlmConfig] = None, transport: Optional[Transport] = None,
                 fixtures_dir: Optional[Path] = None):
        self.cfg = cfg or config.llm_config()
        self.transport = transport or http_transport
        self.fixtures_dir = Path(fixtures_dir or (config.FIXTURES_DIR / "llm"))

    # ------------------------------------------------------------ when to call

    @staticmethod
    def gaps(inv: Invoice) -> list[str]:
        missing = [name for name in REQUIRED if not inv.has(name) or inv.fields[name].loose]
        if not inv.line_items:
            missing.append("line_items")
        if not inv.has("subtotal"):
            missing.append("subtotal")
        return missing

    def recording_path(self, sha256: str) -> Path:
        return self.fixtures_dir / f"{sha256[:16]}.json"

    def has_recording(self, sha256: str) -> bool:
        return self.recording_path(sha256).is_file()

    # ------------------------------------------------------------ the call

    def _reply(self, doc: DocRead, report: dict) -> Optional[str]:
        text = doc.text[:MAX_CHARS]
        prompt = USER_PROMPT.replace("{document}", text)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        path = self.recording_path(doc.sha256)
        recorded = None
        if path.is_file():
            try:
                recorded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                recorded = None
        if self.cfg.mode == "live":
            payload = {"model": self.cfg.model, "temperature": 0,
                       "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]}
            started = time.perf_counter()
            try:
                content = self.transport(payload, self.cfg)
                report.update({"source": "live", "model": self.cfg.model, "seconds": round(time.perf_counter() - started, 2)})
                try:
                    self.fixtures_dir.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({"sha256": doc.sha256, "model": self.cfg.model, "prompt_sha256": prompt_hash,
                                                "recorded_on": config.now_utc().isoformat(), "content": content}, indent=2),
                                    encoding="utf-8")
                    report["recorded_to"] = path.name
                except OSError:
                    pass
                return content
            except Exception as exc:  # network, key, quota, timeout: the pipeline carries on without the model
                report["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                if recorded is None:
                    return None
                report["note"] = "live call failed; the recorded response was used instead"
        if recorded is not None:
            report.update({"source": "replay", "model": recorded.get("model", "?"), "recorded_on": recorded.get("recorded_on", "")})
            return recorded.get("content")
        report.setdefault("note", "no recorded response for this document")
        return None

    def assist(self, doc: DocRead, inv: Invoice, policy: str = "gaps") -> dict:
        """Fill what the rules reader could not. Returns a report for the run record and
        mutates `inv`. Never raises."""
        gaps = self.gaps(inv)
        report: dict[str, Any] = {"mode": self.cfg.mode, "policy": policy, "gaps": gaps, "called": False,
                                  "accepted": [], "rejected": [], "disagreements": []}
        if policy == "off" or self.cfg.mode == "off":
            report["note"] = "LLM assist is off"
            return report
        if policy == "gaps" and not gaps:
            report["note"] = "nothing for the model to do: the rules read every required field"
            return report
        content = self._reply(doc, report)
        if content is None:
            return report
        report["called"] = True
        try:
            data = parse_json_reply(content)
        except ValueError as exc:
            report["error"] = f"reply could not be parsed: {exc}"
            return report
        page_text = doc.text
        scanned = doc.is_scanned

        for name, kind in HEADER_FIELDS.items():
            item = data.get(name)
            if not isinstance(item, dict) or item.get("value") in (None, ""):
                continue
            value, quote = item.get("value"), str(item.get("quote") or "")
            target = TARGET.get(name, name)
            if not T.contains_snippet(page_text, quote):
                report["rejected"].append({"field": target, "value": value, "why": "its quote is not on the page"})
                continue
            if not value_in_quote(kind, value, quote):
                report["rejected"].append({"field": target, "value": value, "why": "the value is not stated in its quote"})
                continue
            clean = f"{T.q2(value)}" if kind == "amount" else str(value)[:10] if kind == "date" else str(value).strip()
            current = inv.fields.get(target)
            if current is not None and current.present and not current.loose:
                same = (T.to_decimal(current.value) == T.to_decimal(clean)) if kind == "amount" else \
                    (T.invoice_no_key(str(current.value)) == T.invoice_no_key(clean)) if kind == "id" else str(current.value) == clean
                if not same:
                    report["disagreements"].append({"field": target, "rules": current.value, "llm": clean})
                continue
            inv.fields[target] = FieldValue(
                name=target, label=FIELD_LABELS.get(target, target), value=clean, confidence=0.7 if scanned else 0.8,
                source=SourceRef(page=1, snippet=T.unify(quote)[:200], method="llm"),
                notes=["read by the LLM; quote found on the page and value found in the quote"])
            report["accepted"].append(target)

        if not inv.line_items and isinstance(data.get("line_items"), list):
            lines, problems = [], []
            for n, raw in enumerate(data["line_items"], 1):
                if not isinstance(raw, dict):
                    continue
                quote = str(raw.get("quote") or "")
                qty, price, amount = T.to_decimal(raw.get("qty")), T.to_decimal(raw.get("unit_price")), T.to_decimal(raw.get("amount"))
                if not T.contains_snippet(page_text, quote):
                    problems.append(f"line {n}: its quote is not on the page")
                    continue
                if amount is None or not value_in_quote("amount", amount, quote):
                    problems.append(f"line {n}: the amount is not stated in its quote")
                    continue
                if price is not None and not value_in_quote("amount", price, quote):
                    problems.append(f"line {n}: the unit price is not stated in its quote")
                    continue
                if qty is not None and not value_in_quote("amount", qty, quote):
                    problems.append(f"line {n}: the quantity is not stated in its quote")
                    continue
                if qty is not None and price is not None and abs(qty * price - amount) > Decimal("0.51"):
                    problems.append(f"line {n}: quantity x price does not equal the amount")
                    continue
                lines.append(LineItem(
                    line_no=len(lines) + 1, description=T.unify(str(raw.get("description") or ""))[:200],
                    hsn=str(raw.get("hsn")) if raw.get("hsn") else None, qty=qty, unit_price=price, amount=T.q2(amount),
                    unit_price_pre_tax=price, amount_pre_tax=T.q2(amount), confidence=0.7 if scanned else 0.8,
                    source=SourceRef(page=1, snippet=T.unify(quote)[:200], method="llm"), flags=["read_by_llm"]))
            if problems:
                report["rejected"].append({"field": "line_items", "value": f"{len(problems)} line(s)", "why": "; ".join(problems)})
            if lines and not problems:
                inv.line_items = lines
                report["accepted"].append("line_items")
        _finish(inv)
        inv.llm = report
        return report


def _finish(inv: Invoice) -> None:
    """Recompute the derived figures after the model filled gaps."""
    f = inv.fields
    taxes = [inv.money(k) for k in ("cgst", "sgst", "igst") if inv.money(k) is not None]
    if taxes:
        f["tax_total"] = FieldValue(name="tax_total", label=FIELD_LABELS["tax_total"], value=f"{T.q2(sum(taxes))}",
                                    confidence=0.8, source=SourceRef(snippet="sum of the tax lines", method="derived"))
    taxable = inv.money("subtotal")
    if taxable and taxes and not inv.has("tax_rate"):
        f["tax_rate"] = FieldValue(name="tax_rate", label=FIELD_LABELS["tax_rate"], value=f"{T.q2(sum(taxes) / taxable * 100)}",
                                   confidence=0.8, source=SourceRef(snippet="tax divided by taxable value", method="derived"))
