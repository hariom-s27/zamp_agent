"""The landing band on Guided demo, and the measured figures on How it works."""
from __future__ import annotations

from invoice_desk import demo_steps
from invoice_desk.ui import demo, guide

from .test_ui import app, go, press, shown, said  # noqa: F401  (app is a fixture)


def test_the_landing_band_says_what_this_is_and_who_does_what(app):
    page = shown(app)
    assert demo.PROMISE in page
    for chip in ("AI reads the page", "Rules make the decision", "A named person approves what leaves"):
        assert chip in page
    assert "Watch one invoice go from PDF to decision" in [b.label for b in app.button]
    assert "Deliver today's mail" in [b.label for b in app.button]
    assert "Tested on 49 difficult documents: none was misread and approved. 4 were misread and held" in said(app)
    assert len(demo_steps.STEPS) == 11


def test_the_three_start_here_rows_are_existing_steps():
    keys = [s["key"] for s in demo_steps.STEPS]
    assert [k for k, _, _ in demo.START_HERE] == ["clean", "outside", "bank"]
    assert all(k in keys for k, _, _ in demo.START_HERE)


def test_watch_one_invoice_runs_the_clean_sample_on_process_invoice(app):
    press(app, "Watch one invoice go from PDF to decision")
    assert [t.value for t in app.title] == ["Process an invoice"]
    assert "### Everything matches" in shown(app) and app.session_state["proc_runs"] == 1


def test_how_it_works_shows_the_four_measured_numbers_and_none_without_the_file(tmp_path):
    assert guide.robustness_numbers() == {"documents": 49, "approved-correct": 30, "held-correct": 15,
                                          "misread-held": 4, "misread-approved": 0}
    assert guide.robustness_numbers(tmp_path / "missing.json") is None


def test_how_it_works_page_shows_the_figures(app):
    go(app, "guide")
    metrics = {m.label: m.value for m in app.metric}
    assert (metrics["Cleared correctly"], metrics["Held correctly"], metrics["Misread, held for a person"],
            metrics["Misread and cleared"]) == ("30", "15", "4", "0")
