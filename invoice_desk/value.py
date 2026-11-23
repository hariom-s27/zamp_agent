"""The Business case (round 3, DECISIONS.md D71). No Streamlit here.

`measured` reads what the stored runs and cases show. `model` turns the assumptions in
rules.yaml (value_model) into minutes, hours and rupees, using the ROI formula Zamp's own
article gives: (value created minus cost) divided by cost.

Exception-chasing time is deliberately the same before and after: the case desk aims to cut
it, but that is not measured, so the page does not claim it.
"""
from __future__ import annotations

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from . import textutil as T
from .rules import ValueModel

DUPLICATES = ("DUPLICATE_FILE", "DUPLICATE_INVOICE_NUMBER", "DUPLICATE_CONTENT")
OVERBILLING_OR_BANK = ("PRICE_VARIANCE", "QTY_OVER_PO", "OVER_PO_BALANCE", "BANK_DETAILS_CHANGED")
HUNDRED, SIXTY = Decimal(100), Decimal(60)


def _q(value: Decimal, places: str) -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _codes(run: dict) -> set[str]:
    return {c for c in (run.get("reason_codes") or "").split(",") if c}


def _total(rows: list[dict]) -> Decimal:
    return sum((T.to_decimal(r.get("total")) or Decimal(0) for r in rows), Decimal(0))


def measured(runs: list[dict], cases: list[dict], spend_usd: float, invoices_with_ai: int) -> dict[str, Any]:
    """What this session's books show. A file sent twice is one invoice and never adds to a figure."""
    received = [r for r in runs if not r.get("resubmission_of")]
    touchless = [r for r in received if r["verdict"] == "APPROVE"]
    held = [r for r in received if r["verdict"] == "HOLD"]
    owner_of = {c["run_id"]: c["owner"] for c in cases}
    resolved = [c for c in cases if c["state"] == "RESOLVED"]
    return {
        "received": len(received),
        "sent_twice": len(runs) - len(received),
        "touchless": len(touchless),
        "touchless_share": (Decimal(len(touchless)) / Decimal(len(received))) if received else None,
        "held": len(held),
        "held_by_owner": dict(Counter(owner_of[r["run_id"]] for r in held if r["run_id"] in owner_of)),
        "resolved_by_rule": sum(1 for c in resolved if c["resolution"] == "released by rule"),
        "resolved_by_person": sum(1 for c in resolved if c["resolution"] != "released by rule"),
        "rejected_duplicates_inr": _total([r for r in received if r["verdict"] == "REJECT" and _codes(r) & set(DUPLICATES)]),
        "held_overbilling_or_bank_inr": _total([r for r in held if _codes(r) & set(OVERBILLING_OR_BANK)]),
        "ai_invoices": invoices_with_ai,
        "ai_spend_usd": spend_usd,
        "ai_cost_per_invoice_usd": (spend_usd / invoices_with_ai) if invoices_with_ai else None,
    }


def model(a: ValueModel) -> dict[str, Optional[Decimal]]:
    """Minutes, hours and rupees from the assumptions. Every input comes from `a`."""
    exceptions = a.exception_rate_pct_today / HUNDRED
    touchless = a.touchless_share_pct / HUNDRED
    chasing = exceptions * a.minutes_per_exception_manual            # unchanged by the desk: not measured
    before = a.minutes_per_invoice_manual + chasing
    after = (1 - touchless) * a.minutes_per_review_with_desk + chasing
    hours = a.invoices_per_month * (before - after) / SIXTY
    saving = hours * a.loaded_cost_per_hour_inr
    cost = a.desk_cost_per_month_inr
    return {
        "minutes_before": _q(before, "0.01"),
        "minutes_after": _q(after, "0.01"),
        "hours_saved": _q(hours, "0.1"),
        "saving_inr": _q(saving, "1"),
        "cost_before_per_invoice": _q(before / SIXTY * a.loaded_cost_per_hour_inr, "0.01"),
        "cost_after_per_invoice": _q(after / SIXTY * a.loaded_cost_per_hour_inr, "0.01"),
        "exposure_inr": _q(a.invoices_per_month * a.duplicate_or_wrong_payment_pct / HUNDRED * a.average_invoice_value_inr,
                           "1"),
        "net_saving_inr": _q(saving - cost, "1") if cost > 0 else None,
        "roi": _q((saving - cost) / cost, "0.01") if cost > 0 else None,
    }
