"""Round 5 finishing, checkpoint 4: every page, opened headless on a fresh session, says nothing a
finance buyer should not read: no model or brand name, no internal mode, no file or developer words,
no raw APPROVE or HOLD:, no em dash, and no real calendar date in place of the business date.

The same sweep runs again on books filled by the guided demo's first steps and the day's mail, where
only the date check is left out (a legal pay-by date can fall on any day)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from invoice_desk import config  # noqa: E402

from .test_ui import process, start  # noqa: E402,F401

PAGES = ["demo", "inbox", "process", "review", "cases", "payments", "procedure", "dashboard", "value", "reference",
         "guide"]
FORBIDDEN = [re.compile(p) for p in (r"(?i)claude", r"(?i)haiku", r"(?i)sonnet", r"FIXTURE", r"rules\.yaml",
                                     r"(?i)pipeline", r"(?i)this build", r"fixtures/", r"\bAPPROVE\b", r"HOLD:",
                                     "—")]
REAL_DATES = [re.compile(r"23-Nov-2026"), re.compile(r"24-Nov-2026")]


def page_text(at: AppTest) -> str:
    """Everything a viewer can read on the page and in the sidebar, apart from the page's style sheet."""
    parts: list[str] = []
    for kind in ("markdown", "caption", "info", "success", "warning", "error", "title", "subheader", "header",
                 "text", "code", "latex"):
        for el in getattr(at, kind, []):
            value = str(getattr(el, "value", "") or "")
            if not value.lstrip().startswith("<style>"):
                parts.append(value)
    for kind in ("button", "expander", "toggle", "checkbox", "text_input", "number_input", "selectbox",
                 "multiselect", "radio", "slider", "tabs", "date_input", "text_area", "button_group"):
        for el in getattr(at, kind, []):
            parts.append(str(getattr(el, "label", "") or ""))
            parts.append(str(getattr(el, "help", "") or ""))
            for option in getattr(el, "options", []) or []:
                parts.append(str(option))
    for el in getattr(at, "tab", []):
        parts.append(str(getattr(el, "label", "") or ""))
    for el in at.metric:
        parts += [str(el.label), str(el.value), str(getattr(el, "delta", "") or "")]
    for frame in list(at.dataframe) + list(at.table):
        value = frame.value
        parts += [str(c) for c in value.columns] + [str(v) for v in value.to_numpy().ravel()]
    for button in at.get("download_button"):
        parts.append(str(getattr(button, "label", "") or ""))
    return "\n".join(parts)


def sweep(at: AppTest, pages: list[str], dates: bool) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for page in pages:
        at.session_state["goto"] = page
        at.run()
        assert not at.exception, (page, [str(e.value) for e in at.exception])
        text = page_text(at)
        bad = [p.pattern for p in FORBIDDEN + (REAL_DATES if dates else []) if p.search(text)]
        if bad:
            found[page] = bad + [line for line in text.splitlines()
                                 if any(p.search(line) for p in FORBIDDEN + (REAL_DATES if dates else []))][:4]
    return found


def test_every_page_on_a_fresh_session_reads_clean(tmp_path, monkeypatch):
    at = start(tmp_path, monkeypatch)
    assert sweep(at, PAGES, dates=True) == {}


def test_every_page_with_filled_books_reads_clean(tmp_path, monkeypatch):
    at = start(tmp_path, monkeypatch, LLM_MODE="fixture")
    for prefix in ("01", "04", "05", "08", "09", "10"):
        process(at, prefix)
    at.session_state["goto"] = "inbox"
    at.run()
    at.button(key="ib_deliver").click().run()
    at.button(key="ib_deliver_next").click().run()
    assert not at.exception
    assert sweep(at, PAGES, dates=False) == {}
