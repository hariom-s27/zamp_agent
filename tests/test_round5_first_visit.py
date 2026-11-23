"""Round 5 finishing, checkpoint 1: the things that break a first visit.

The guided demo is not locked by "Watch one invoice", a first run on Process invoice shows its
stages and a real repeat stops at step 1 with a link to the original, one business clock stamps
everything a person sees, the sidebar names no model, and the payment-run example never sends a
file into the books twice."""
from __future__ import annotations

from datetime import datetime as real_datetime

from invoice_desk import config, payments, samples
from invoice_desk.llm import LlmAssist
from invoice_desk.masterdata import load_master
from invoice_desk.pipeline import Pipeline
from invoice_desk.review import apply_review
from invoice_desk.rules import load_rules
from invoice_desk.ui import payments as pay_page

from .conftest import TODAY
from .test_ui import app, go, press, process, shown, said  # noqa: F401  (app is a fixture)


def _labels(at) -> str:
    boxes = list(at.expander) + list(getattr(at, "status", []))
    return " ".join([e.label for e in boxes] + [b.label for b in at.button])


# ---------------------------------------------------------------- 1. guided demo

def test_watch_one_invoice_counts_as_step_one_and_leaves_the_demo_open(app):
    press(app, "Watch one invoice go from PDF to decision")
    assert app.session_state["proc_runs"] == 1
    go(app, "demo")
    assert "clean" in app.session_state["demo_done"]
    assert not app.button(key="demo_next").disabled and not app.button(key="demo_all").disabled
    assert "already hold records from outside the guided demo" not in said(app)
    app.button(key="demo_next").click().run()
    assert not app.exception
    assert set(app.session_state["demo_done"]) == {"clean", "scan"}


def test_a_start_here_run_out_of_order_is_cleared_when_step_one_starts(app):
    app.button(key="start_bank").click().run()               # step 8's sample, run first from the landing band
    go(app, "demo")
    assert not app.button(key="demo_next").disabled and not app.button(key="demo_all").disabled
    app.button(key="demo_next").click().run()
    assert not app.exception
    assert set(app.session_state["demo_done"]) == {"clean"}
    assert len(app.db.list_runs()) == 1                       # the out-of-order run was cleared, step 1 ran fresh


# ---------------------------------------------------------------- 2. process invoice

def test_a_first_run_shows_the_stages_and_a_repeat_stops_at_intake_with_a_link(app):
    process(app, "01")
    first = app.session_state["current_run"]
    record = app.db.get_run(first)
    assert record.resubmission_of in (None, "")
    assert "Already received" not in said(app)
    assert "Processed in this session as" in said(app)
    labels = _labels(app)
    for n in range(1, 10):
        assert f"**{n}. " in labels
    app.button(key="proc_go").click().run()
    assert not app.exception
    again = app.db.get_run(app.session_state["current_run"])
    assert again.resubmission_of == first
    labels = _labels(app)
    assert "**1. Received** · Duplicate file: the same file as " + first in labels
    assert f"Open the original record, {first}" in labels
    assert "Not run: duplicate file" in labels


def test_changing_the_sample_clears_the_previous_answer(app):
    process(app, "01")
    assert "### Everything matches" in shown(app)
    options = app.selectbox(key="proc_sample").options
    app.selectbox(key="proc_sample").select(next(o for o in options if o.startswith("03"))).run()
    assert "### Everything matches" not in shown(app)
    assert "current_run" not in app.session_state


# ---------------------------------------------------------------- 3. one business clock

class _Elsewhere(real_datetime):
    """A real clock that reads 15-Mar-2031: nothing a person sees may carry that date."""

    @classmethod
    def now(cls, tz=None):
        return real_datetime(2031, 3, 15, 10, 7, 0, tzinfo=tz)


def test_the_business_clock_keeps_the_demo_day_and_the_real_time_of_day(monkeypatch):
    monkeypatch.delenv("INVOICE_DESK_TODAY", raising=False)
    monkeypatch.setattr(config, "datetime", _Elsewhere)
    moment = config.now_utc().astimezone(config.IST)
    assert (moment.date(), moment.hour, moment.minute) == (TODAY, 10, 7)
    assert config.to_ist_text(config.now_utc().isoformat()) == "21-Nov-2026 10:07"


def test_no_screen_shows_the_real_date(app, monkeypatch):
    monkeypatch.setattr(config, "datetime", _Elsewhere)
    process(app, "04")                                         # a hold: a case with a timeline
    app.session_state["goto"] = "inbox"
    app.run()
    press(app, next(b.label for b in app.button if b.label.startswith("Deliver today's mail")))
    press(app, next(b.label for b in app.button if b.label.startswith("Deliver the next morning's mail")))
    items = app.db.inbox()
    assert items and all(i["received_at"][:10] in ("2026-11-21", "2026-11-22") for i in items)
    assert any(i["received_at"].startswith("2026-11-22T0") for i in items)      # the next morning's mail
    pages = []
    for page in ("process", "cases", "dashboard", "inbox", "review", "payments"):
        app.session_state["goto"] = page
        app.run()
        assert not app.exception
        pages.append(shown(app) + said(app) + _labels(app) + " ".join(str(t.value) for t in app.dataframe))
    text = " ".join(pages)
    for real in ("2031", "15-Mar", "15 Mar", "Mar-2031"):
        assert real not in text
    assert "21-Nov-2026" in text


# ---------------------------------------------------------------- 4. sidebar

def test_the_sidebar_names_no_model_and_no_internal_mode(app):
    details = " ".join(m.value for e in app.sidebar.expander for m in e.markdown)
    side = " ".join(m.value for m in app.sidebar.markdown)
    assert "<b>AI reading</b> off" in details and "AI reading: off" in side
    for word in ("FIXTURE", "claude", "haiku", "sonnet", "AI model"):
        assert word not in details and word not in side


# ---------------------------------------------------------------- 5. payment-run example

CALL = "Called the number on file; the vendor confirmed the new account"


def test_the_payment_example_never_resubmits_and_an_approved_bank_change_does_not_block(tmp_path):
    from invoice_desk.store import Store
    rules, master = load_rules(), load_master()
    store = Store(tmp_path / "books.db")
    llm = LlmAssist(config.LlmConfig(mode="fixture"), cache_dir=tmp_path / "cache")
    pipeline = Pipeline(store=store, rules=rules, master=master, llm=llm, today=TODAY, ocr_mode="fixture")
    assert pay_page.load_payment_example(store, master, rules, pipeline, TODAY) == 8
    count = len(store.list_runs())
    bank = next(r for r in store.list_runs() if "BANK_DETAILS_CHANGED" in (r.get("reason_codes") or ""))
    vendor = bank["vendor_id"]
    proposal = payments.propose(store, master, rules, TODAY, None, {})
    assert any(ln["vendor_id"] == vendor for ln in proposal["blocked"])      # waiting for two people
    apply_review(store, bank["run_id"], "APPROVE", "N. Rao", CALL, {"bank_verified": True})
    apply_review(store, bank["run_id"], "APPROVE", "S. Menon", "Call record read", {"bank_second_check": True})
    assert pay_page.load_payment_example(store, master, rules, pipeline, TODAY) == 0
    assert len(store.list_runs()) == count
    assert not any(r.get("resubmission_of") for r in store.list_runs())
    proposal = payments.propose(store, master, rules, TODAY, None, {})
    assert not any(ln["vendor_id"] == vendor for ln in proposal["blocked"])


def test_a_repeat_of_a_bank_change_file_is_not_a_new_bank_request(tmp_path):
    from invoice_desk.store import Store
    rules, master = load_rules(), load_master()
    store = Store(tmp_path / "books.db")
    pipeline = Pipeline(store=store, rules=rules, master=master, llm=LlmAssist(config.LlmConfig(mode="off")),
                        today=TODAY, ocr_mode="fixture")
    sample = next(s for s in samples.load_manifest() if s["id"] == "09_fraud_guard_bank_change")
    first = pipeline.run(samples.sample_bytes(sample), samples.received_name(sample), "cli")
    apply_review(store, first.run_id, "APPROVE", "N. Rao", CALL, {"bank_verified": True})
    apply_review(store, first.run_id, "APPROVE", "S. Menon", "Call record read", {"bank_second_check": True})
    again = pipeline.run(samples.sample_bytes(sample), samples.received_name(sample), "cli")
    assert again.resubmission_of == first.run_id
    assert payments._open_bank_changes(store) == {}
