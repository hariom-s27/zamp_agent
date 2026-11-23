"""Round 4 checkpoint T: the primary colour is navy, set in .streamlit/config.toml only (no CSS),
every other setting is kept, and every page still opens."""
import tomllib

import pytest

from invoice_desk import config
from invoice_desk.ui import common as C

NAVY = "#1F3A5F"


def test_primary_colour_is_navy_and_the_rest_of_the_config_is_kept():
    data = tomllib.loads((config.ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert data["theme"]["primaryColor"] == NAVY
    assert data["theme"]["base"] == "light" and data["theme"]["backgroundColor"] == "#ffffff"
    assert data["theme"]["secondaryBackgroundColor"] == "#f4f6f8" and data["theme"]["textColor"] == "#1f2933"
    assert data["server"] == {"headless": True, "maxUploadSize": 25, "runOnSave": False}
    assert data["browser"] == {"gatherUsageStats": False}
    assert data["client"] == {"toolbarMode": "minimal", "showErrorDetails": "type"}


def test_the_colour_is_not_set_in_css():
    assert NAVY.lower() not in C.CSS.lower()


PAGES = {"demo": "Guided demo", "inbox": "Inbox", "process": "Process an invoice", "review": "Review queue",
         "cases": "Cases", "payments": "Payments", "procedure": "Procedure", "dashboard": "Dashboard",
         "value": "Business case", "reference": "Rules and data", "guide": "How it works"}


def test_every_page_opens(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from tests.test_ui import start
    app = start(tmp_path, monkeypatch)
    assert [t.value for t in app.title] == ["Guided demo"]           # the opening page stays the guided demo
    for key, title in PAGES.items():
        app.session_state["goto"] = key
        app.run()
        assert not app.exception, (key, [str(e.value) for e in app.exception])
        assert [t.value for t in app.title] == [title]
