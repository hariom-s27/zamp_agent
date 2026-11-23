"""The bundled sample invoices and what each one is expected to give.

`samples/manifest.json` is written by scripts/generate_test_data.py. The command line,
the preflight check, the tests and the UI all read expectations from here, so there is
one statement of what "correct" means for every sample.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from . import config
from .models import RunResult


# The business date the bundled samples were written for. The app processes on this date
# unless INVOICE_DESK_TODAY says otherwise or the demo clock is switched off, so the dated
# checks (invoice older than a year, dated in the future, dated before its PO) give the same
# answer in a rehearsal, in the interview and a year from now.
DEMO_TODAY = date(2026, 11, 21)


def business_date() -> date:
    """The date the bundled samples are processed on, on screen, on the command line and in
    the scripts: INVOICE_DESK_TODAY when it is set, otherwise the demo date."""
    return config.today() if config.env("INVOICE_DESK_TODAY") else DEMO_TODAY


def load_manifest() -> list[dict]:
    path = config.SAMPLES_DIR / "manifest.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def sample_bytes(sample: dict) -> bytes:
    return (config.SAMPLES_DIR / sample["file"]).read_bytes()


def received_name(sample: dict) -> str:
    """The name a sample is processed under everywhere (screen, command line, scripts):
    the attachment name it would arrive with, not the tidy name it has in the samples folder."""
    return sample.get("received_as") or str(sample["file"]).replace("\\", "/").split("/")[-1]


def llm_supplied_values(result: RunResult) -> bool:
    report = result.invoice.llm if result.invoice else None
    return bool(report and report.get("accepted"))


def expected(sample: dict, result: Optional[RunResult] = None) -> tuple[str, set[str]]:
    """(verdict, reason codes) this sample should give. A sample can state a second
    expectation for runs in which the LLM assist supplied values."""
    if result is not None and "with_llm" in sample and llm_supplied_values(result):
        return sample["with_llm"]["expected_verdict"], set(sample["with_llm"]["expected_reasons"])
    return sample["expected_verdict"], set(sample["expected_reasons"])


def check(sample: dict, result: RunResult) -> tuple[bool, str, set[str]]:
    """(matches, expected verdict, expected reason codes) for one finished run."""
    verdict, reasons = expected(sample, result)
    actual = {f.code for f in result.decision.reasons} if result.decision else set()
    return (result.verdict == verdict and actual == reasons), verdict, reasons
