"""Shared fixtures. Tests run with LLM off and recorded OCR, so they give the same
result on any machine; separate tests exercise live OCR when Tesseract is installed."""
import json
import os
from datetime import date
from pathlib import Path

import pytest

os.environ["LLM_MODE"] = "off"
os.environ.setdefault("OCR_MODE", "fixture")

from invoice_desk import config  # noqa: E402
from invoice_desk.llm import LlmAssist  # noqa: E402
from invoice_desk.masterdata import load_master  # noqa: E402
from invoice_desk.pipeline import Pipeline  # noqa: E402
from invoice_desk.rules import load_rules  # noqa: E402
from invoice_desk.store import Store  # noqa: E402

TODAY = date(2026, 11, 21)
LLM_OFF = config.LlmConfig(mode="off")
# What an OCR engine reads from a noisy scan differs between builds and platforms. Tests
# that run the live engine therefore assert what must hold on ANY engine: the documented
# outcome, or a hold for a reading reason, never a wrong approval. Exact-value assertions
# on live OCR are added only when this is set, which it was in the environment the
# project was built in (Tesseract 5.3.4). Exact reading of the bundled scans is pinned
# on every machine by the recorded OCR output in fixtures/ocr.
STRICT_OCR = os.environ.get("INVOICE_DESK_STRICT_OCR") == "1"
# Holds that mean "the page was not read with certainty". Business exceptions (a price over
# tolerance, a quantity over the PO, a changed bank account) are deliberately not here: a
# live-OCR run that ends in one of those when the documented outcome is different is a
# misread, and the test must fail on it.
READING_HOLDS = {"LOW_CONFIDENCE_READ", "ARITHMETIC_MISMATCH", "AMOUNT_WORDS_MISMATCH", "MISSING_TOTAL",
                 "MISSING_INVOICE_DATE", "MISSING_INVOICE_NUMBER", "LINE_ITEMS_UNREADABLE", "GSTIN_INVALID",
                 "BUYER_GSTIN_MISSING", "MISSING_VENDOR_ID", "VENDOR_NOT_APPROVED", "VENDOR_MATCHED_BY_NAME",
                 "PO_NOT_FOUND", "NO_PO_FOUND", "PO_INFERRED", "LINE_NOT_ON_PO", "TAX_RATE_UNUSUAL", "TAX_TYPE_MISMATCH",
                 "DATE_IN_FUTURE", "DATE_TOO_OLD", "SCAN_NOT_READ"}


def sample_bytes(name: str) -> bytes:
    path = config.SAMPLES_DIR / name
    if not path.suffix:
        path = path.with_suffix(".pdf")
    return path.read_bytes()


@pytest.fixture(scope="session")
def rules():
    return load_rules()


@pytest.fixture(scope="session")
def master():
    return load_master()


@pytest.fixture(scope="session")
def manifest():
    return json.loads((config.SAMPLES_DIR / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture()
def store(tmp_path: Path):
    return Store(tmp_path / "test.db")


@pytest.fixture()
def pipeline(store, rules, master):
    return Pipeline(store=store, rules=rules, master=master, llm=LlmAssist(LLM_OFF), today=TODAY, ocr_mode="fixture")
