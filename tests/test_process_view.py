"""Process invoice: the decision card first, a step timeline and a table of what was checked.

The live run reserves every result area, resets it with one element, runs the stages, stores the
result and reruns; the finished view is drawn from the stored record and never runs the pipeline.
"""
from __future__ import annotations

import pytest

from invoice_desk.rules import DisplayRules, load_rules

from .test_ui import app, process, shown  # noqa: F401  (app is a fixture)

STEP_TITLES = ["Received", "Read the page", "Picked out the details", "Checked the maths and tax", "Checked the vendor",
               "Matched to the PO and goods receipt", "Looked for duplicates", "Decided", "Filed"]


def test_the_display_pause_is_a_rules_setting_with_no_default_and_zero_in_tests(app):
    assert load_rules().display.stage_pause_seconds == 0.6
    assert DisplayRules.model_fields["stage_pause_seconds"].is_required()
    assert app.session_state["pace"] == "Fast"          # the tests run with no pause


def test_nine_steps_in_order_then_no_running_step_is_left(app):
    process(app, "01")
    labels = [e.label for e in app.expander if e.label.startswith("**")]
    steps = [next((i for i, label in enumerate(labels) if label.startswith(f"**{n}. {title}**")), None)
             for n, title in enumerate(STEP_TITLES, 1)]
    assert None not in steps and steps == sorted(steps)
    assert len(app.status) == 0                          # the live boxes are gone once the run settles
    assert "How it was handled" in shown(app) and "What was checked" in shown(app)


def test_an_unrelated_click_does_not_run_the_pipeline_again(app):
    process(app, "01")
    runs, records = app.session_state["proc_runs"], len(app.db.list_runs())
    app.toggle(key="beside_proc").set_value(False).run()
    assert not app.exception
    assert app.session_state["proc_runs"] == runs and len(app.db.list_runs()) == records
    assert "### Everything matches" in shown(app)


@pytest.mark.parametrize("prefix, chip", [("01", ":green-badge[:material/check_circle: Cleared]"),
                                          ("04", ":orange-badge[:material/pause_circle: On hold]"),
                                          ("05", ":red-badge[:material/cancel: Rejected]")])
def test_each_decision_gets_its_chip_and_a_title_that_is_the_reason(app, prefix, chip):
    process(app, prefix)
    page = shown(app)
    decision = app.db.get_run(app.session_state["current_run"]).decision
    assert chip in page
    if decision.verdict == "APPROVE":
        assert "### Everything matches" in page and "Nobody had to touch it." in page
    else:
        title = decision.reasons[0].title
        more = f", and {len(decision.reasons) - 1} more" if len(decision.reasons) > 1 else ""
        assert f"### {title}{more}" in page
