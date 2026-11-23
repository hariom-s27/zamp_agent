"""The Streamlit app, driven headlessly with Streamlit's own test harness: every page
loads, the guided demo runs every scenario to its expected decision, a run completes
through the live view, the dashboard shows history, reviews are recorded with their
controls, and hostile or broken uploads are shown safely."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from invoice_desk import config  # noqa: E402
from invoice_desk.models import Finding  # noqa: E402
from invoice_desk.store import Store  # noqa: E402
from invoice_desk.ui import common as C  # noqa: E402
from invoice_desk.ui import demo  # noqa: E402
from scripts import generate_test_data as G  # noqa: E402

PAGES = {"demo": "Guided demo", "process": "Process an invoice", "review": "Review queue", "dashboard": "Dashboard",
         "reference": "Rules and data", "guide": "How it works"}
ENV = ("LLM_MODE", "ANTHROPIC_API_KEY", "LLM_ALLOW_LIVE", "INVOICE_DESK_SANDBOX", "INVOICE_DESK_TODAY", "INVOICE_DESK_HOME",
       "LLM_MODEL", "RECEIPT_SIGNING_KEY")


def start(tmp_path, monkeypatch, **env) -> AppTest:
    """The app on a private database, recorded OCR, the model off, and no viewing pause."""
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("INVOICE_DESK_DB", str(tmp_path / "ui.db"))
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "usage.db"))
    monkeypatch.setenv("LLM_MODE", "off")
    monkeypatch.setenv("OCR_MODE", "fixture")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    at = AppTest.from_file(str(config.ROOT / "app.py"), default_timeout=240)
    at.session_state["pace"] = "Fast"
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


@pytest.fixture()
def app(tmp_path, monkeypatch):
    at = start(tmp_path, monkeypatch)
    at.db = Store(tmp_path / "ui.db")
    return at


def go(at, page):
    at.session_state["goto"] = page
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert [t.value for t in at.title] == [PAGES[page]]
    return at


def shown(at) -> str:
    return " ".join(m.value for m in at.markdown)


def said(at) -> str:
    """Everything the page says in captions, notices and code boxes."""
    parts = [x.value for kind in (at.caption, at.info, at.success, at.warning, at.error) for x in kind]
    return " ".join(parts + [c.value for c in at.code])


def press(at, label: str):
    button = next(b for b in at.button if b.label == label)
    button.click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def process(at, sample_prefix: str):
    if [t.value for t in at.title] != [PAGES["process"]]:
        go(at, "process")
    options = at.selectbox(key="proc_sample").options
    choice = next(o for o in options if o.startswith(sample_prefix))
    at.selectbox(key="proc_sample").select(choice).run()
    at.button(key="proc_go").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def upload(at, name: str, content: bytes, kind: str = "application/pdf"):
    go(at, "process")
    at.radio(key="proc_source").set_value("Upload a file").run()
    at.file_uploader(key="proc_upload").set_value((name, content, kind)).run()
    at.button(key="proc_go_upload").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


# ---------------------------------------------------------------- every page, and what a user lands on

def test_every_page_loads_on_empty_books(app):
    assert [t.value for t in app.title] == ["Guided demo"]          # the landing page
    for page in PAGES:
        go(app, page)
    assert app.db.list_runs() == []


def test_the_sidebar_says_one_thing_and_keeps_the_build_details_behind_a_click(app):
    side = " ".join(m.value for m in app.sidebar.markdown)
    assert "Ready." in side and "AI assist: off (rules only)" in side
    assert not app.sidebar.slider and not app.sidebar.button           # no slow-down dial and no reset beside the work
    details = next(e for e in app.sidebar.expander if e.label == "Technical details")
    text = " ".join(m.value for m in details.markdown)
    assert "Business date" in text and "21-Nov-2026 (pinned for the demo)" in text and "OCR engine" in text
    assert "shared on this machine" in text


# ---------------------------------------------------------------- the guided demo

def test_the_guided_demo_runs_one_scenario_per_click(app):
    assert "Press &#x27;Run step 1&#x27; to begin" in shown(app)          # escaped like every other text on the page
    app.button(key="demo_next").click().run()
    assert not app.exception
    done = app.session_state["demo_done"]
    assert done["clean"] == {"run_id": "AP-2026-0001", "verdict": "APPROVE", "ok": True, "want": "APPROVE"}
    page = shown(app)
    assert "APPROVE: pay ₹4,49,934.00 to Tungabhadra Office Furnishings Pvt Ltd" in page and "idk-rail" in page
    assert "What to notice." in page and "AP-2026-0001" in page and "as expected" in page
    assert any("This is the expected decision" in s.value for s in app.success)
    assert next(b for b in app.button if b.key == "demo_next").label.startswith("Run step 2: A scanned invoice")
    assert [r["verdict"] for r in app.db.list_runs()] == ["APPROVE"]


def test_run_all_takes_the_demo_through_every_scenario_to_its_expected_decision(app):
    app.button(key="demo_all").click().run()
    for _ in range(len(demo.STEPS) + 2):          # one scenario per rerun, as on screen
        if len(app.session_state["demo_done"]) == len(demo.STEPS) and not app.session_state["demo_auto"]:
            break
        app.run()
    assert not app.exception
    done = app.session_state["demo_done"]
    assert list(done) == [s["key"] for s in demo.STEPS]
    assert all(step["ok"] for step in done.values()), {k: v for k, v in done.items() if not v["ok"]}
    assert [done[k]["verdict"] for k in ("clean", "scan", "split", "duplicate", "blank", "inside", "outside", "bank",
                                         "letter", "again", "junk")] == \
        ["APPROVE", "APPROVE", "HOLD", "REJECT", "HOLD", "APPROVE", "HOLD", "HOLD", "HOLD", "APPROVE", "REJECT"]
    assert next(b for b in app.button if b.key == "demo_next").label == "All steps done"
    # the second arrival of the first file is that record again, and nothing was booked twice
    again = app.db.get_run(done["again"]["run_id"])
    assert again.resubmission_of == done["clean"]["run_id"] and again.ledger_id is None
    assert len([e for e in app.db.ledger_entries() if e["origin"] != "history" and e["status"] == "POSTED"]) == 3
    assert app.db.verify_chain()["ok"]


def test_a_hold_in_the_demo_leads_to_the_review_queue(app):
    for _ in range(3):
        app.button(key="demo_next").click().run()
    assert app.session_state["demo_last"] == "split" and app.session_state["demo_done"]["split"]["verdict"] == "HOLD"
    app.button(key="demo_follow_split").click().run()
    assert not app.exception and [t.value for t in app.title] == ["Review queue"]
    assert "21 of 20" in shown(app)


def test_books_that_hold_other_records_must_be_reset_before_the_demo(app):
    process(app, "09")
    go(app, "demo")
    assert any("Start a fresh demo" in w.value for w in app.warning)
    assert next(b for b in app.button if b.key == "demo_next").disabled
    app.button(key="demo_fresh").click().run()
    assert not app.exception and app.db.list_runs() == [] and len(app.db.ledger_entries()) == 10
    assert any("Fresh demo" in s.value for s in app.success)
    assert not next(b for b in app.button if b.key == "demo_next").disabled


def test_your_own_file_goes_through_the_same_stages_in_the_demo(app):
    app.file_uploader(key="demo_upload").set_value(("my notes.txt", b"not a pdf at all", "text/plain")).run()
    app.button(key="demo_upload_go").click().run()
    assert not app.exception
    assert "REJECT: file cannot be opened" in shown(app) and app.db.list_runs()[0]["verdict"] == "REJECT"


def test_the_ai_mode_on_screen_never_offers_live_calls_without_a_key(app):
    picker = app.selectbox(key="ai_mode_pick")
    assert list(picker.options) == ["FIXTURE: replay recorded answers (no network, no key)", "OFF: rules only"]
    assert picker.value == "off"
    assert "no API key is configured" in said(app)
    picker.select("fixture").run()
    assert not app.exception and app.session_state["ai_mode"] == "fixture"
    go(app, "dashboard")                                   # the choice holds on a page that does not show the control
    assert "AI assist: recorded answers (no network)" in " ".join(m.value for m in app.sidebar.markdown)


def test_a_key_makes_live_calls_available_but_does_not_switch_them_on(tmp_path, monkeypatch):
    at = start(tmp_path, monkeypatch, ANTHROPIC_API_KEY="sk-test-not-a-real-key", LLM_MODE="fixture")
    picker = at.selectbox(key="ai_mode_pick")
    assert picker.value == "fixture" and any("CACHED-LIVE" in str(o) for o in picker.options)
    assert "sk-test-not-a-real-key" not in shown(at) + said(at) + " ".join(m.value for m in at.sidebar.markdown)
    assert "Cost caps: $0.05 per invoice, $1.00 per day" in said(at)


def test_the_expected_decision_of_the_letter_follows_the_ai_set_up(tmp_path, monkeypatch):
    """Sample 10 has two right answers: held by the rules alone, approved once Claude's reading is available and
    verified. The line under the sample picker must name the one this session will produce, so the screen never
    states one expectation above the run and another below it."""
    from scripts import record_llm_fixtures
    from .test_scripts import Service, client

    def picked(at) -> str:
        go(at, "process")
        options = at.selectbox(key="proc_sample").options
        at.selectbox(key="proc_sample").select(next(o for o in options if o.startswith("10"))).run()
        return said(at)

    fixtures = tmp_path / "fixtures"
    (fixtures / "llm").mkdir(parents=True)
    (fixtures / "ocr").mkdir()
    monkeypatch.setattr(config, "FIXTURES_DIR", fixtures)
    home = str(tmp_path / "home")

    text = picked(start(tmp_path, monkeypatch, LLM_MODE="fixture", INVOICE_DESK_HOME=home))
    assert "Expected decision: HOLD (" in text and "No Claude answer is recorded for this file yet" in text
    assert "With a recorded answer: APPROVE" in text

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")              # record once, through the stand-in service
    monkeypatch.setattr(record_llm_fixtures, "HTTP_CLIENT", client(Service()))
    assert record_llm_fixtures.main(["--only", "10_ai_assist_letter_invoice"]) == 0

    at = start(tmp_path, monkeypatch, LLM_MODE="fixture", INVOICE_DESK_HOME=home)
    text = picked(at)
    assert "Expected decision: APPROVE" in text and "stored and will be replayed" in text
    assert "By the rules alone: HOLD (" in text and "Expected decision: HOLD" not in text
    at.button(key="proc_go").click().run()
    assert not at.exception and "APPROVE: pay" in shown(at)
    assert "Expected decision: APPROVE (no exception). This is that decision." in said(at)
    assert "Purple boxes: values Claude read." in said(at)

    text = picked(start(tmp_path, monkeypatch, LLM_MODE="off", INVOICE_DESK_HOME=home))
    assert "Expected decision: HOLD (" in text and "because AI assist is off" in text and "With AI assist: APPROVE" in text


def test_a_live_call_is_made_only_when_asked_for_and_can_be_shown_on_purpose(tmp_path, monkeypatch):
    """The live moment of a demo: with a key, CACHED-LIVE calls Claude once and keeps the answer; after that the
    stored answer is used, unless the presenter asks for a fresh answer on every run."""
    from .test_scripts import Service, client
    fixtures = tmp_path / "fixtures"
    (fixtures / "llm").mkdir(parents=True)
    (fixtures / "ocr").mkdir()
    monkeypatch.setattr(config, "FIXTURES_DIR", fixtures)
    service = Service()
    monkeypatch.setattr(C, "HTTP_CLIENT", client(service))            # stands in for api.anthropic.com
    at = start(tmp_path, monkeypatch, ANTHROPIC_API_KEY="sk-ant-test", LLM_MODE="fixture",
               INVOICE_DESK_HOME=str(tmp_path / "home"))
    db = Store(tmp_path / "ui.db")

    def source_of_latest() -> str:
        return db.get_run(db.list_runs()[0]["run_id"]).invoice.llm.get("source", "none")

    assert not [t for t in at.toggle if t.key == "ai_fresh_pick"]     # nothing to switch while answers are only replayed
    process(at, "10")
    assert service.calls == 0 and "HOLD:" in shown(at)                # a key alone: no call, the rules decide

    go(at, "demo")
    at.selectbox(key="ai_mode_pick").select("cached-live").run()
    assert "sk-ant-test" not in shown(at) + said(at)
    press(at, "Start a fresh demo")
    process(at, "10")
    assert service.calls == 1 and "APPROVE: pay" in shown(at) and source_of_latest() == "live"

    go(at, "demo")
    press(at, "Start a fresh demo")
    process(at, "10")
    assert service.calls == 1 and source_of_latest() == "cache"       # the stored answer; nothing is paid for twice

    go(at, "demo")
    at.toggle(key="ai_fresh_pick").set_value(True).run()
    assert "fresh answer on every run" in " ".join(m.value for m in at.sidebar.markdown)
    press(at, "Start a fresh demo")
    process(at, "10")
    assert service.calls == 2 and "APPROVE: pay" in shown(at) and source_of_latest() == "live"
    assert not list((fixtures / "llm").glob("*.json"))                # the screen never writes into the recordings


def test_a_public_demo_gives_each_visitor_private_books_and_no_live_calls_unless_allowed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    first = start(tmp_path, monkeypatch, INVOICE_DESK_SANDBOX="session", INVOICE_DESK_HOME=str(home),
                  ANTHROPIC_API_KEY="sk-test-not-a-real-key", LLM_MODE="fixture")
    monkeypatch.delenv("INVOICE_DESK_DB")
    first = AppTest.from_file(str(config.ROOT / "app.py"), default_timeout=240)
    first.session_state["pace"] = "Fast"
    first.run()
    second = AppTest.from_file(str(config.ROOT / "app.py"), default_timeout=240)
    second.session_state["pace"] = "Fast"
    second.run()
    a, b = first.session_state["sandbox_id"], second.session_state["sandbox_id"]
    assert a != b and C.SANDBOX_ID.fullmatch(a) and first.query_params["s"] in (a, [a])
    first.button(key="demo_next").click().run()
    assert not first.exception
    assert len(Store(config.sandbox_db(a)).list_runs()) == 1 and Store(config.sandbox_db(b)).list_runs() == []
    # the same address comes back to the same books; a made-up id cannot point outside the sandbox folder
    back = AppTest.from_file(str(config.ROOT / "app.py"), default_timeout=240)
    back.query_params["s"] = a
    back.run()
    assert back.session_state["sandbox_id"] == a and any("Start a fresh demo" in w.value for w in back.warning)
    odd = AppTest.from_file(str(config.ROOT / "app.py"), default_timeout=240)
    odd.query_params["s"] = "../../etc"
    odd.run()
    assert not odd.exception and C.SANDBOX_ID.fullmatch(odd.session_state["sandbox_id"])
    # a key in the host's secrets is not an invitation to spend it
    assert not any("CACHED-LIVE" in str(o) for o in first.selectbox(key="ai_mode_pick").options)
    assert "LLM_ALLOW_LIVE is not set" in said(first)
    monkeypatch.setenv("LLM_ALLOW_LIVE", "1")
    allowed = AppTest.from_file(str(config.ROOT / "app.py"), default_timeout=240).run()
    assert any("CACHED-LIVE" in str(o) for o in allowed.selectbox(key="ai_mode_pick").options)
    assert "private to this browser session" in " ".join(m.value for e in allowed.sidebar.expander for m in e.markdown)


# ---------------------------------------------------------------- processing one invoice

def test_a_run_completes_through_the_live_view_and_shows_every_stage(app):
    process(app, "01")
    assert app.session_state["current_run"] == "AP-2026-0001"
    page = shown(app)
    assert "APPROVE: pay ₹4,49,934.00 to Tungabhadra Office Furnishings Pvt Ltd" in page and "idk-rail" in page
    assert "AP-2026-0001 &nbsp;|&nbsp; Tungabhadra_TOF-26-27-0412.pdf &nbsp;|&nbsp; Approved, processed in under a second" in page
    labels = [e.label for e in app.expander]
    for number, title in enumerate(["Intake", "Read", "Extract", "Validate", "Vendor", "PO match", "Duplicates",
                                    "Decide", "Post and verify"], 1):
        assert any(f"{number} {title}" in label for label in labels), title
    assert [b.label for b in app.get("download_button")][:2] == ["Full record (JSON)", "Audit receipt (JSON)"]
    assert [r["verdict"] for r in app.db.list_runs()] == ["APPROVE"]
    assert any("This is that decision" in c.value for c in app.caption)
    # each stage keeps its real timing; the headline, the captions and the notices speak in plain words
    assert all(label.rstrip().endswith("ms]") for label in labels if label.startswith(":"))
    assert " ms" not in page.split("idk-rail")[0] and " ms" not in said(app)


def test_a_hold_shows_its_reasons_owners_and_questions(app):
    process(app, "04")
    page = shown(app)
    assert "HOLD: 2 points need a person" in page
    assert "QTY_OVER_PO" in page and "owner: Procurement" in page and "PO owner P. Deshmukh (EHS)" in page
    assert any(e.label.startswith("Message to the vendor") for e in app.expander)


def test_the_message_to_the_vendor_is_ready_to_copy(app):
    process(app, "05")
    assert "REJECT: same invoice under a different number" in shown(app)
    card = next(e for e in app.expander if e.label.startswith("Message to the vendor"))
    draft = card.code[0].value
    assert draft.startswith("Subject:") and "DQI/26-27/1193" in draft
    assert any("To:" in m.value and "billing@deccanquill.example" in m.value for m in card.markdown)
    assert any("Copy it with the icon" in c.value for c in card.caption)
    assert "Vendor message (TXT)" in [b.label for b in app.get("download_button")]
    assert not [b for b in app.button if "Claude" in b.label]          # the model is off: no rewording is offered


def test_the_audit_trail_reads_as_events_and_keeps_the_hashes_one_click_further_in(app):
    process(app, "01")
    trail = next(e for e in app.expander if e.label.startswith("Tamper-evident audit trail"))
    assert trail.label == "Tamper-evident audit trail: 1 entry"
    rows = trail.dataframe[0].value
    assert list(rows.columns) == ["When", "Event", "By", "Outcome", "Note", "Entry fingerprint"]
    assert list(rows["Event"]) == ["Decision recorded by the system"] and list(rows["By"]) == ["Invoice Desk"]
    visible = shown(app) + said(app) + " ".join(e.label for e in app.expander)
    assert "SHA-256" not in visible and "hash chain" not in visible.lower()
    app.toggle(key="audit_tech_AP-2026-0001").set_value(True).run()
    technical = next(e for e in app.expander if e.label.startswith("Tamper-evident audit trail")).dataframe
    assert "SHA-256 of this entry" in technical[1].value.columns and len(technical) == 3
    assert app.db.get_run("AP-2026-0001").file_sha256 in set(technical[2].value["Value"])


def test_the_same_file_again_returns_the_first_record_and_says_so(app):
    process(app, "01")
    process(app, "01")
    assert app.session_state["current_run"] == "AP-2026-0002"
    assert "APPROVE: same file as AP-2026-0001, not booked again" in shown(app)
    assert any("already received as AP-2026-0001" in i.value for i in app.info)
    assert any("Already received as AP-2026-0001" in c.value for c in app.caption)
    assert len([e for e in app.db.ledger_entries() if e["origin"] != "history"]) == 1


def test_a_broken_upload_is_refused_with_a_reason_not_an_error(app):
    upload(app, "notes.txt", b"just some text, not a pdf", "text/plain")
    assert "REJECT: file cannot be opened" in shown(app)
    assert app.db.list_runs()[0]["verdict"] == "REJECT"


@pytest.mark.parametrize("sample_id", ["corrupt_file", "empty_file", "password_protected", "too_many_pages", "not_a_pdf",
                                       "truncated_download", "newsletter_not_invoice"])
def test_every_bad_input_in_the_sample_inbox_ends_in_a_decision_on_screen(app, sample_id, manifest):
    sample = next(s for s in manifest if s["id"] == sample_id)
    process(app, f"{sample['order']:02d}")
    row = app.db.list_runs()[0]
    assert row["verdict"] == sample["expected_verdict"] and row["reason_codes"].split(",") == sample["expected_reasons"]
    assert f"{sample['expected_verdict']}:" in shown(app)


def test_text_from_an_uploaded_document_is_never_rendered_as_html(app):
    hostile = G.render_invoice_pdf(G.spec(
        id="x", layout="classic", vendor="V001", invoice_no="TOF/26-27/0499", invoice_date=G.date(2026, 11, 18),
        po_ref="PO-2026-0147", po_date=G.date(2026, 10, 20), vendor_name_printed="Tungabhadra <img src=x onerror=alert(1)> Ltd",
        lines=[G.line("Steel Storage Cupboard <script>alert(2)</script>", "9403", 1, "Nos", "9800.00")]))
    upload(app, "<u onclick=alert(3)>evil.pdf", hostile)
    page = shown(app)
    assert "<img src=x" not in page and "<script>alert" not in page and "<u onclick" not in page
    assert "&lt;u onclick=alert(3)&gt;evil.pdf" in page              # the file name is shown as text
    assert "&lt;img src=x onerror=alert(1)&gt;" in page              # and so is the vendor name read from the PDF
    labels = " ".join(e.label for e in app.expander)
    assert "<img" not in labels and "<script" not in labels and "\\<" in labels


def test_a_multi_page_upload_can_be_paged_and_the_evidence_can_take_the_full_width(app):
    items = [G.line(f"{desc} lot {i}", hsn, i + 1, uom, f"{100 + i * 37.25:.2f}")
             for i, (desc, hsn, uom) in enumerate(G.VARIANT_ITEMS * 2, 1)][:14]
    two_pages = G.render_multipage_invoice_pdf(G.spec(
        id="long", layout="classic", vendor="V001", invoice_no="TOF/26-27/0500", invoice_date=G.date(2026, 11, 18),
        po_ref="PO-2026-0147", po_date=G.date(2026, 10, 20), lines=items))
    upload(app, "two_pages.pdf", two_pages)
    assert app.db.get_run("AP-2026-0001").page_count == 2
    pager = next(s for s in app.selectbox if s.label == "Page")
    assert list(pager.options) == ["1", "2"]
    pager.select(2).run()
    assert not app.exception
    app.toggle(key="beside_proc").set_value(False).run()
    assert not app.exception and not [s for s in app.selectbox if s.label == "Page"]      # document hidden, stages full width


def test_a_photographed_page_is_shown_the_way_it_was_read(app):
    """The preview of a photograph is the straightened sheet, so the boxes sit on the words they mark."""
    from scripts import evaluate_robustness as ER
    record = ER.load_set(["photo_sideways"])[0]
    upload(app, "phone photo.pdf", ER.content_of(record))
    result = app.db.get_run("AP-2026-0001")
    page = result.pages[0]
    assert page["prep"]["turns"] in (1, 3) and page["prep"]["quad"] and page["height"] > page["width"]
    assert any("Photographed page, shown as it was read" in c.value and "turned upright" in c.value for c in app.caption)
    import io
    import json
    from PIL import Image
    png = C._preview_png.__wrapped__(result.file_sha256, 1, page["angle"], C.source_boxes(result, 1), page["width"],
                                     ER.content_of(record), json.dumps(page["prep"], sort_keys=True))
    picture = Image.open(io.BytesIO(png))
    assert picture.height > picture.width                    # upright: the PDF page itself is landscape
    assert abs(picture.width / picture.height - page["width"] / page["height"]) < 0.02


def test_the_tax_inclusive_conversion_is_shown_as_a_table(app):
    process(app, "07")
    assert "APPROVE" in shown(app)
    tables = [frame.value for frame in app.dataframe if "Price printed (with GST)" in frame.value.columns]
    assert len(tables) == 1
    row = tables[0].iloc[0]
    assert (row["Price printed (with GST)"], row["GST"], row["Divided by"], row["Pre-tax price"]) == ("₹3,812.00", "18%", "1.18", "₹3,230.51")


# ---------------------------------------------------------------- the sample inbox and the dashboard

def test_the_whole_sample_inbox_then_the_dashboard_shows_the_history(app, manifest):
    go(app, "process")
    app.button(key="proc_batch").click().run()
    assert not app.exception
    rows = app.session_state["batch"]
    assert len(rows) == len(manifest) == 18 and all(r["As expected"] == "yes" for r in rows)
    assert "18 of 18 documents got the expected decision" in shown(app)
    assert rows[0]["Reference"] == "AP-2026-0001" and rows[0]["Time"] == "under a second"

    go(app, "dashboard")
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Documents received"] == "18" and metrics["Straight-through processing (STP)"] == "22%"
    assert metrics["Waiting for review"] == "6" and metrics["Rejected"] == "8"
    assert metrics["Typical handling time"] == "under a second"
    assert "Approved with no person involved" not in metrics and "Median processing time" not in metrics
    assert any("Tamper-evident audit trail: intact. All 18 entries were re-checked just now." in s.value for s in app.success)
    history = app.dataframe[0].value
    assert len(history) == 18 and list(history["Reference"])[0] == "AP-2026-0018"
    assert set(history["Decision"]) == {"APPROVE", "HOLD", "REJECT"} and "Run" not in history.columns
    picker = next(s for s in app.selectbox if s.label.startswith("Record to open"))
    picker.select("AP-2026-0005").run()
    assert not app.exception and "REJECT: same invoice under a different number" in shown(app)


def test_a_file_sent_twice_is_one_invoice_on_the_dashboard(app):
    process(app, "01")
    process(app, "01")
    go(app, "dashboard")
    metrics = {m.label: (m.value, m.delta) for m in app.metric}
    assert metrics["Documents received"][0] == "1" and "1 sent twice (not counted)" in metrics["Documents received"][1]
    assert metrics["Released for payment"][0] == "₹4,49,934.00"          # once, not twice
    assert list(app.dataframe[0].value["Status"]) == ["same file as AP-2026-0001", "-"]


def test_dashboard_filters(app):
    go(app, "process")
    app.button(key="proc_batch").click().run()
    go(app, "dashboard")
    app.multiselect(key="dash_verdict").set_value(["HOLD"]).run()
    assert set(app.dataframe[0].value["Decision"]) == {"HOLD"} and len(app.dataframe[0].value) == 6
    app.multiselect(key="dash_verdict").set_value([]).run()
    app.text_input(key="dash_query").set_value("PO-2026-0193").run()
    assert list(app.dataframe[0].value["Reference"]) == ["AP-2026-0004"]
    app.text_input(key="dash_query").set_value("no such thing").run()
    assert any("No record matches" in i.value for i in app.info)


# ---------------------------------------------------------------- the review queue and its controls

def test_review_needs_a_name_then_records_the_decision(app):
    process(app, "08")
    go(app, "review")
    assert "HOLD: price above the PO" in shown(app) or "HOLD" in shown(app)
    press(app, "Approve for payment")
    assert any("reviewer's name" in e.value for e in app.error)
    assert app.db.run_row("AP-2026-0001")["review_status"] == "PENDING"
    app.text_input(key="rev_name_AP-2026-0001").set_value("R. Iyer")
    app.text_input(key="rev_note_AP-2026-0001").set_value("Price increase agreed on 12 Nov")
    press(app, "Approve for payment")
    assert app.db.run_row("AP-2026-0001")["review_status"] == "APPROVED"
    assert app.db.get_ledger("L-0001")["status"] == "POSTED"
    assert any("approved for payment by R. Iyer" in s.value for s in app.success)
    assert [r["kind"] for r in app.db.receipts("AP-2026-0001")] == ["decision", "review"] and app.db.verify_chain()["ok"]
    assert "Nothing is waiting for a person" in shown(app)


def test_the_queue_shows_the_po_as_it_stands_and_refuses_an_approval_that_would_overbill_it(app):
    process(app, "04")
    go(app, "review")
    position = next(f.value for f in app.dataframe if "After approval" in f.value.columns)
    line = position.iloc[0]
    assert (line["Ordered"], line["Posted so far"], line["This invoice"], line["After approval"], line["Result"]) == \
        ("20", "16", "5", "21", "over the PO")
    assert any("An approval will be refused" in w.value for w in app.warning)
    app.text_input(key="rev_name_AP-2026-0001").set_value("P. Deshmukh")
    press(app, "Approve for payment")
    refusal = " ".join(e.value for e in app.error)
    assert "would overbill PO-2026-0193" in refusal and "would reach 21 of 20" in refusal
    assert app.db.run_row("AP-2026-0001")["review_status"] == "PENDING" and app.db.get_ledger("L-0001")["status"] == "PENDING"
    assert [r["kind"] for r in app.db.receipts("AP-2026-0001")] == ["decision", "review_refused"]
    assert "Checked again when approval was attempted" in shown(app)          # the hold now carries the fresh reason


def test_the_second_approver_starts_with_an_empty_form(app):
    """After the first approval the first approver's name and call note must not stay typed into the form. The second
    approver is a different person: left in place, the name would only earn a refusal, and the note would be
    recorded as if the second person had written it."""
    process(app, "09")
    go(app, "review")
    run = "AP-2026-0001"
    app.text_input(key=f"rev_name_{run}").set_value("N. Rao")
    app.text_input(key=f"rev_note_{run}").set_value("Spoke to Meera in accounts on 080-5550-0118 at 11:40")
    app.checkbox(key=f"rev_bank_{run}").check()
    press(app, "Record first approval")
    assert any("first approval recorded by N. Rao" in s.value for s in app.success)
    names = [t for t in app.text_input if t.label == "Your name (recorded with the decision)"]
    notes = [t for t in app.text_input if t.label == "Note for the record"]
    assert [t.value for t in names] == [""] and [t.value for t in notes] == [""]
    # fields of their own, not the first approver's fields emptied: a browser keeps a field's text by its key
    assert names[0].key == f"rev_name2_{run}" and notes[0].key == f"rev_note2_{run}"
    assert any("First approval on record: N. Rao" in i.value and "Spoke to Meera" in i.value for i in app.info)


def test_a_bank_change_takes_two_different_people_on_screen(app):
    process(app, "09")
    go(app, "review")
    run = "AP-2026-0001"
    app.text_input(key=f"rev_name_{run}").set_value("N. Rao")
    press(app, "Record first approval")
    assert any("call-back" in e.value for e in app.error) and app.db.run_row(run)["review_status"] == "PENDING"
    app.text_input(key=f"rev_name_{run}").set_value("N. Rao")
    app.text_input(key=f"rev_note_{run}").set_value("Spoke to Meera in accounts on 080-5550-0118 at 11:40")
    app.checkbox(key=f"rev_bank_{run}").check()
    app.file_uploader(key=f"rev_proof_{run}").set_value(("mandate letter.pdf", b"%PDF-1.4 signed letter", "application/pdf"))
    press(app, "Record first approval")
    assert any("first approval recorded by N. Rao" in s.value for s in app.success)
    assert app.db.run_row(run)["review_status"] == "PENDING" and app.db.overrides("V001", "bank_account") == []
    assert any("First approval on record: N. Rao" in i.value and "Spoke to Meera" in i.value for i in app.info)
    # the same person cannot give the second approval
    app.text_input(key=f"rev_name2_{run}").set_value("n rao")
    app.checkbox(key=f"rev_bank2_{run}").check()
    press(app, "Approve for payment")
    assert any("different person" in e.value for e in app.error) and app.db.run_row(run)["review_status"] == "PENDING"
    app.text_input(key=f"rev_name2_{run}").set_value("S. Menon")
    app.checkbox(key=f"rev_bank2_{run}").check()
    press(app, "Approve for payment")
    assert app.db.run_row(run)["review_status"] == "APPROVED" and app.db.get_ledger("L-0001")["status"] == "POSTED"
    assert app.db.overrides("V001", "bank_account")[0]["value"] == "770031245566"
    assert any("approved for payment by N. Rao and S. Menon" in s.value for s in app.success)
    kept = [r["body"].get("evidence") for r in app.db.receipts(run) if r["body"].get("evidence")]
    assert kept and kept[0]["name"] == "mandate letter.pdf" and app.db.load_attachment(kept[0]["sha256"]) == b"%PDF-1.4 signed letter"
    decided = next(f.value for f in app.dataframe if "Reviewer" in f.value.columns)
    assert list(decided["Decision"]) == ["approved for payment", "first of two approvals"]


def test_an_unbooked_document_can_only_be_closed_as_processed_manually(app):
    from .test_review import _dollar_invoice
    upload(app, "acme.pdf", _dollar_invoice())
    assert "HOLD: invoice is not in rupees" in shown(app) and "USD 4,000.00" in shown(app)
    go(app, "review")
    labels = [b.label for b in app.button]
    assert "Close as processed manually" in labels and "Approve for payment" not in labels
    assert "Mark as handled by hand" not in labels
    app.text_input(key="rev_name_AP-2026-0001").set_value("N. Rao")
    press(app, "Close as processed manually")
    assert app.db.run_row("AP-2026-0001")["review_status"] == "HANDLED"
    go(app, "dashboard")
    assert list(app.dataframe[0].value["Total"]) == ["USD 4000.00"]
    assert list(app.dataframe[0].value["Status"]) == ["closed, processed manually"]
    assert {m.label: m.value for m in app.metric}["Released for payment"] == "₹0.00"


def test_rejecting_on_review(app):
    process(app, "04")
    go(app, "review")
    app.text_input(key="rev_name_AP-2026-0001").set_value("P. Deshmukh")
    press(app, "Reject")
    assert app.db.run_row("AP-2026-0001")["review_status"] == "REJECTED" and app.db.get_ledger("L-0001")["status"] == "REJECTED"


# ---------------------------------------------------------------- rules, data and the reset

def test_what_if_sandbox_shows_a_decision_flip_and_touches_nothing(app):
    go(app, "reference")
    assert app.selectbox(key="wi_sample").value.startswith("07")
    app.slider(key="wi_price_pct").set_value(0.9).run()
    app.button(key="wi_go").click().run()
    assert not app.exception
    outcome = app.session_state["wi_result"]
    assert outcome["base"][0] == "APPROVE" and outcome["trial"][0] == "HOLD"          # 0.95% over, limit moved from 1.00% to 0.90%
    assert any("The decision changed" in i.value for i in app.info)
    assert app.db.list_runs() == [] and len(app.db.ledger_entries()) == 10      # the real books are untouched


def test_the_reset_lives_with_the_demo_controls_and_restores_the_starting_state(app):
    process(app, "01")
    assert len(app.db.list_runs()) == 1
    assert not [b for b in app.button if "reset" in b.label.lower()]          # nothing on the work page wipes the books
    go(app, "demo")
    app.button(key="demo_reset").click().run()
    assert not app.exception
    assert app.db.list_runs() == [] and app.db.receipts() == [] and len(app.db.ledger_entries()) == 10
    assert any("starting state" in s.value for s in app.success)
    assert "current_run" not in app.session_state


def test_rules_page_lists_every_code_and_the_master_data(app, rules, master):
    go(app, "reference")
    decision_table = app.dataframe[0].value
    assert set(decision_table["Code"]) == set(rules.reasons)
    everything = " ".join(str(v) for frame in app.dataframe for v in frame.value.to_numpy().ravel())
    assert "PO-2026-0193" in everything and master.vendors["V004"].legal_name in everything and "ASS/2026/0871" in everything


def test_the_demo_date_keeps_dated_checks_stable_and_can_be_switched_off(app):
    assert C.sample_lib.DEMO_TODAY.isoformat() == "2026-11-21"
    app.toggle(key="demo_clock_pick").set_value(False).run()
    assert not app.exception and app.session_state["demo_clock"] is False
    details = " ".join(m.value for e in app.sidebar.expander for m in e.markdown)
    assert "(today)" in details and "pinned" not in details


def test_a_date_set_by_the_host_wins_over_the_switch(tmp_path, monkeypatch):
    at = start(tmp_path, monkeypatch, INVOICE_DESK_TODAY="2026-12-01")
    assert at.toggle(key="demo_clock_pick").disabled
    assert "01-Dec-2026 (set by INVOICE_DESK_TODAY)" in said(at)
    assert "01-Dec-2026 (set by INVOICE_DESK_TODAY)" in " ".join(m.value for e in at.sidebar.expander for m in e.markdown)


# ---------------------------------------------------------------- the renderers, as plain functions

def test_stage_rows_escape_document_text_and_show_live_states():
    row = C.stage_row_html(3, "Extract", "running", "<script>x</script> & more")
    assert "<script>" not in row and "&lt;script&gt;" in row and "idk-spin" in row and "running" in row
    done = C.stage_row_html(5, "Vendor", "attention", "bank differs", 12.4,
                            [Finding(code="BANK_DETAILS_CHANGED", stage="vendor", summary="<b>x</b>", action="HOLD",
                                     title="Bank account differs", owner="Vendor master team", question="Call them")])
    assert "12 ms" in done and "f-hold" in done and "&lt;b&gt;x&lt;/b&gt;" in done and "owner: Vendor master team" in done


def test_rail_shows_each_stage_state():
    rail = C.rail_html({"intake": "ok", "read": "running", "decide": "attention", "post": "ok"})
    assert rail.count("idk-chip") == 9 and rail.count("st-pending") == 5
    assert "passed" in rail and "idk-spin" in rail and ">! hold<" in rail.replace("</span>", "<") and "recorded" in rail


def test_markdown_labels_are_escaped():
    assert C.md_safe("a*b_c $x$ [link](u) <i>") == "a\\*b\\_c \\$x\\$ \\[link\\]\\(u\\) \\<i\\>"


def test_processing_time_is_said_the_way_a_person_says_it():
    assert [C.took(ms) for ms in (0, 164, 999, 1000, 3461, 5663, 12400)] == \
        ["under a second", "under a second", "under a second", "1 seconds", "3.5 seconds", "5.7 seconds", "12 seconds"]


def test_a_key_pasted_into_the_hosts_secrets_box_is_picked_up(tmp_path, monkeypatch):
    """On Streamlit Community Cloud the key goes into the Secrets box, not a .env file."""
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("INVOICE_DESK_DB", str(tmp_path / "ui.db"))
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "usage.db"))
    at = AppTest.from_file(str(config.ROOT / "app.py"), default_timeout=240)
    at.secrets["ANTHROPIC_API_KEY"] = "secret-from-the-host"
    at.secrets["LLM_MODE"] = "cached-live"
    at.secrets["LLM_MODEL"] = "claude-sonnet-5-5"
    at.run()
    assert not at.exception
    details = " ".join(m.value for e in at.sidebar.expander for m in e.markdown)
    everything = details + shown(at) + said(at) + " ".join(m.value for m in at.sidebar.markdown)
    assert "CACHED-LIVE (claude-sonnet-5-5)" in details and "secret-from-the-host" not in everything
    assert "AI assist: live Claude, stored answers first" in everything
