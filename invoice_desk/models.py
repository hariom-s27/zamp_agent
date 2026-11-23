"""Data shapes passed between stages. Everything serialises to plain JSON, so a run can
be stored, shown again later and hashed for its receipt."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field

from .textutil import to_decimal

FIELD_LABELS = {
    "vendor_name": "Vendor name", "vendor_gstin": "Vendor GSTIN", "buyer_gstin": "Our GSTIN on invoice",
    "invoice_number": "Invoice number", "invoice_date": "Invoice date", "po_reference": "PO reference",
    "currency": "Currency", "subtotal": "Taxable value", "cgst": "CGST", "sgst": "SGST", "igst": "IGST",
    "tax_total": "Total tax", "tax_rate": "GST rate %", "total": "Invoice total",
    "amount_in_words": "Amount in words", "bank_name": "Bank name", "bank_account": "Bank account",
    "bank_ifsc": "IFSC",
}
FIELD_ORDER = list(FIELD_LABELS)
MONEY_FIELDS = {"subtotal", "cgst", "sgst", "igst", "tax_total", "total", "amount_in_words"}


class SourceRef(BaseModel):
    """Where a value came from: page, the text it was read from, and its box on the page."""
    page: int = 1
    snippet: str = ""
    bbox: Optional[list[float]] = None      # [x0, top, x1, bottom] in page points
    method: str = "text"                    # text | ocr | derived | llm


class FieldValue(BaseModel):
    name: str
    label: str = ""
    value: Any = None                       # str, ISO date string or decimal string; None = missing
    confidence: float = 0.0
    source: Optional[SourceRef] = None
    missing_reason: Optional[str] = None
    notes: list[str] = Field(default_factory=list)
    loose: bool = False                     # read from running text, not a clean label/value pair

    @property
    def present(self) -> bool:
        return self.value not in (None, "")


class LineItem(BaseModel):
    line_no: int
    description: str = ""
    hsn: Optional[str] = None
    qty: Optional[Decimal] = None
    uom: Optional[str] = None
    unit_price: Optional[Decimal] = None        # as printed
    amount: Optional[Decimal] = None            # as printed
    tax_rate: Optional[Decimal] = None
    unit_price_pre_tax: Optional[Decimal] = None
    amount_pre_tax: Optional[Decimal] = None
    confidence: float = 0.0
    source: Optional[SourceRef] = None
    flags: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Invoice(BaseModel):
    doc_type: str = "tax_invoice"
    doc_type_evidence: str = ""
    fields: dict[str, FieldValue] = Field(default_factory=dict)
    line_items: list[LineItem] = Field(default_factory=list)
    tax_inclusive: bool = False
    notes: list[str] = Field(default_factory=list)
    gstin_candidates: list[dict] = Field(default_factory=list)
    llm: Optional[dict] = None              # what the LLM assist did, if it ran
    reads: Optional[dict] = None            # for a scan: how the two reads compared and which was used

    def get(self, name: str) -> Any:
        f = self.fields.get(name)
        return f.value if f else None

    def has(self, name: str) -> bool:
        f = self.fields.get(name)
        return bool(f and f.present)

    def money(self, name: str) -> Optional[Decimal]:
        return to_decimal(self.get(name))

    def day(self, name: str = "invoice_date") -> Optional[date]:
        value = self.get(name)
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    @property
    def taxable(self) -> Optional[Decimal]:
        return self.money("subtotal")

    @property
    def total(self) -> Optional[Decimal]:
        return self.money("total")


class Finding(BaseModel):
    """One thing a stage noticed. `decide()` fills in action, title and owner from rules.yaml."""
    code: str
    stage: str
    summary: str                             # one plain sentence with the numbers in it
    expected: Optional[str] = None
    observed: Optional[str] = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    question: Optional[str] = None           # what a person is asked, for holds
    vendor_issue: Optional[str] = None       # sentence for the note to the vendor, if they must act
    action: str = ""                         # REJECT | HOLD | NOTE (set by decide)
    title: str = ""
    owner: str = ""


class StageResult(BaseModel):
    key: str
    number: int
    title: str
    status: str = "ok"                       # ok | attention | blocked | error | skipped
    summary: str = ""
    duration_ms: float = 0.0
    blocks: list[dict[str, Any]] = Field(default_factory=list)   # evidence to display
    findings: list[Finding] = Field(default_factory=list)


class Decision(BaseModel):
    verdict: str                             # APPROVE | HOLD | REJECT
    headline: str
    explanation: str
    reasons: list[Finding] = Field(default_factory=list)       # what drives the verdict
    also_noted: list[Finding] = Field(default_factory=list)    # lesser issues under a REJECT
    notes: list[Finding] = Field(default_factory=list)         # information only
    owners: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    next_action: str = ""
    vendor_draft: Optional[str] = None


class RunResult(BaseModel):
    run_id: str
    created_at: str
    file_name: str
    file_sha256: str
    source: str = "upload"                   # upload | sample | batch | cli
    size_bytes: int = 0
    page_count: int = 0
    modes: dict[str, Any] = Field(default_factory=dict)
    stages: list[StageResult] = Field(default_factory=list)
    invoice: Optional[Invoice] = None
    vendor: Optional[dict[str, Any]] = None
    po: Optional[dict[str, Any]] = None
    decision: Optional[Decision] = None
    receipt: Optional[dict[str, Any]] = None
    ledger_id: Optional[str] = None
    duration_ms: float = 0.0
    pages: list[dict[str, Any]] = Field(default_factory=list)   # per page: kind, straightening angle
    resubmission_of: Optional[str] = None    # set when this exact file was processed before: the answer is that run's
    decision_ctx: Optional[dict[str, Any]] = None   # the display facts decide() was given, so a hold can be rebuilt later

    @property
    def verdict(self) -> str:
        return self.decision.verdict if self.decision else "-"
