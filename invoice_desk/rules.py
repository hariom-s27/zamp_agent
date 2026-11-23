"""Load and validate rules.yaml. A bad edit fails loudly with the offending key, instead
of silently changing how invoices are decided."""
from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import config

Action = Literal["REJECT", "HOLD", "NOTE"]
SEVERITY = {"REJECT": 3, "HOLD": 2, "NOTE": 1}


class RulesError(Exception):
    pass


class Strict(BaseModel):
    """A misspelt or unknown key in rules.yaml is an error, not something to ignore."""
    model_config = ConfigDict(extra="forbid")


class Company(Strict):
    name: str
    gstin: str
    state_code: str


class Intake(Strict):
    max_file_mb: float = Field(gt=0)
    max_pages: int = Field(gt=0)

    @property
    def max_bytes(self) -> int:
        return int(self.max_file_mb * 1024 * 1024)


class Matching(Strict):
    vendor_name_match: float = Field(ge=0, le=1)
    vendor_name_same: float = Field(ge=0, le=1)
    vendor_name_doubt: float = Field(ge=0, le=1)
    line_pairing_min: float = Field(ge=0, le=1)
    line_pairing_min_single: float = Field(ge=0, le=1)
    line_sharing_min: float = Field(ge=0, le=1)


class Limit(Strict):
    pct: Decimal = Field(ge=0)
    abs_inr: Decimal = Field(ge=0)


class Tolerances(Strict):
    rounding_inr: Decimal = Field(ge=0)
    unit_price: Limit
    quantity_over_pct: Decimal = Field(ge=0)
    po_total: Limit


class AutoApprove(Strict):
    max_invoice_total_inr: Decimal = Field(gt=0)


class InferenceWeights(Strict):
    wording: float = Field(ge=0, le=1)
    price: float = Field(ge=0, le=1)
    quantity: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _sum_to_one(self):
        if abs(self.wording + self.price + self.quantity - 1.0) > 1e-6:
            raise ValueError("wording, price and quantity must add up to 1")
        return self


class PoInference(Strict):
    min_confidence: float = Field(ge=0, le=1)
    min_margin: float = Field(ge=0, le=1)
    weights: InferenceWeights


class Duplicates(Strict):
    window_days: int = Field(ge=0)
    amount_tolerance_inr: Decimal = Field(ge=0)
    line_similarity_min: float = Field(ge=0, le=1)


class Dates(Strict):
    max_age_days: int = Field(gt=0)
    allow_future_days: int = Field(ge=0)


class Reading(Strict):
    min_confidence_critical: float = Field(ge=0, le=1)
    scan_agreed_confidence: float = Field(ge=0, le=1)
    scan_doubt_confidence: float = Field(ge=0, le=1)
    blank_ink_max_pct: float = Field(ge=0, le=100)


class Gst(Strict):
    rates_current: list[Decimal]
    rates_before_2025_09_22: list[Decimal]
    rates_special: list[Decimal] = []
    rate_tolerance_pct: Decimal = Field(ge=0)


class LlmConfidence(Strict):
    verified: float = Field(ge=0, le=1)
    quote_only: float = Field(ge=0, le=1)
    from_scan: float = Field(ge=0, le=1)
    model_unsure: float = Field(ge=0, le=1)


class LlmRules(Strict):
    assist: Literal["gaps", "always", "off"]
    confidence: LlmConfidence


class ReviewRules(Strict):
    bank_change_approvers: int = Field(ge=1, le=2)


class Reason(Strict):
    action: Action
    owner: str = ""
    title: str


class Rules(Strict):
    version: str
    company: Company
    intake: Intake
    tolerances: Tolerances
    auto_approve: AutoApprove
    matching: Matching
    po_inference: PoInference
    duplicates: Duplicates
    dates: Dates
    reading: Reading
    gst: Gst
    llm: LlmRules
    review: ReviewRules
    reasons: dict[str, Reason]
    sha256: str = ""

    def reason(self, code: str) -> Reason:
        """The rule for a finding. A code with no rule is held for a person: an unknown
        situation must never be approved by default."""
        found = self.reasons.get(code)
        if found is not None:
            return found
        return Reason(action="HOLD", owner="AP analyst", title=code.replace("_", " ").capitalize())


def load_rules(path: Optional[Path] = None) -> Rules:
    path = Path(path or config.RULES_PATH)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RulesError(f"rules file not found: {path}") from exc
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except yaml.YAMLError as exc:
        raise RulesError(f"rules.yaml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RulesError("rules.yaml must be a mapping of settings")
    try:
        rules = Rules(**data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise RulesError(f"rules.yaml: problem at '{where}': {first['msg']}") from exc
    rules.sha256 = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    return rules


_CACHE: dict[str, tuple[float, Rules]] = {}


def current_rules() -> Rules:
    """rules.yaml as it is on disk now (re-read only when the file changes). Used by
    functions that are called without a Rules object, so that no threshold needs a
    default in the code."""
    path = Path(config.RULES_PATH)
    stamp = path.stat().st_mtime
    hit = _CACHE.get(str(path))
    if hit is None or hit[0] != stamp:
        hit = (stamp, load_rules(path))
        _CACHE[str(path)] = hit
    return hit[1]
