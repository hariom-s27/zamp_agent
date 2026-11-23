"""Tests for Checkpoint 2: Vocabulary.
- The brand-name scan on every page
- The nine stage labels in order
- Outcome vocabulary labels: Cleared, On hold, Rejected, Cleared without a person
"""
from datetime import date
import pytest

from invoice_desk.ui import common as C

BANNED = ("claude", "gpt", "gemini", "anthropic", "openai", "llama", "mistral")

EXPECTED_STAGES = [
    "Received",
    "Read the page",
    "Picked out the details",
    "Checked the maths and tax",
    "Checked the vendor",
    "Matched to the PO and goods receipt",
    "Looked for duplicates",
    "Decided",
    "Filed",
]


def test_nine_stage_labels_in_order():
    labels = [C.stage_display_title(k) for k, _ in C.STAGES]
    assert labels == EXPECTED_STAGES


def test_verdict_display_vocabulary():
    assert C.verdict_display_label("APPROVE") == "Cleared"
    assert C.verdict_display_label("HOLD") == "On hold"
    assert C.verdict_display_label("REJECT") == "Rejected"


def test_brand_name_scan_on_every_page(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from tests.test_ui import start
    app = start(tmp_path, monkeypatch)
    
    pages = ["demo", "inbox", "process", "review", "cases", "payments", "procedure",
             "dashboard", "value", "reference", "guide"]
    
    for key in pages:
        app.session_state["goto"] = key
        app.run()
        assert not app.exception, (key, [str(e.value) for e in app.exception])
        
        # Collect all text on screen
        text_pieces = []
        for kind in ("title", "header", "subheader", "markdown", "caption", "button", "metric"):
            for el in getattr(app, kind, []):
                val = str(getattr(el, "label", "") or getattr(el, "value", "") or "")
                text_pieces.append(val)
        full_text = " ".join(text_pieces).lower()
        for banned in BANNED:
            assert banned not in full_text, f"Brand name '{banned}' found on page '{key}'"
