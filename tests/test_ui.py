"""The Streamlit app, driven headlessly with Streamlit's own test harness: every page
loads, a run completes through the live view, the dashboard shows history, a review is
recorded, and hostile or broken uploads are shown safely."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from invoice_desk import config  # noqa: E402
from invoice_desk.models import Finding  # noqa: E402
from invoice_desk.store import Store  # noqa: E402
from invoice_desk.ui import common as C  # noqa: E402
from scripts import generate_test_data as G  # noqa: E402

PAGES = {"process": "Process an invoice", "dashboard": "Dashboard", "review": "Review queue",
         "reference": "Rules and data", "guide": "How it works"}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("INVOICE_DESK_DB", str(tmp_path / "ui.db"))
    monkeypatch.setenv("LLM_MODE", "off")
    monkeypatch.setenv("OCR_MODE", "fixture")
    at = AppTest.from_file(str(config.ROOT / "app.py"), default_timeout=180)
    at.run()
    assert not at.exception
    at.slider(key="pause").set_value(0.0)          # no viewing pause in tests
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


def process(at, sample_prefix: str):
    options = at.selectbox(key="proc_sample").options
    choice = next(o for o in options if o.startswith(sample_prefix))
    at.selectbox(key="proc_sample").select(choice).run()
    at.button(key="proc_go").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_every_page_loads_on_an_empty_database(app):
    for page in PAGES:
        go(app, page)
    assert app.db.list_runs() == []


def test_a_run_completes_through_the_live_view_and_shows_every_stage(app):
    process(app, "01")
    assert app.session_state["current_run"] == "R-0001"
    page = shown(app)
    assert "APPROVE: pay ₹4,49,934.00 to Tungabhadra Office Furnishings Pvt Ltd" in page and "idk-rail" in page
    labels = [e.label for e in app.expander]
    for number, title in enumerate(["Intake", "Read", "Extract", "Validate", "Vendor", "PO match", "Duplicates",
                                    "Decide", "Post and verify"], 1):
        assert any(f"{number} {title}" in label for label in labels), title
    assert len(app.get("download_button")) >= 2
    assert [r["verdict"] for r in app.db.list_runs()] == ["APPROVE"]
    assert any("This run matches it" in c.value for c in app.caption)


def test_a_hold_shows_its_reasons_owners_and_questions(app):
    process(app, "04")
    page = shown(app)
    assert "HOLD: 2 points need a person" in page
    assert "QTY_OVER_PO" in page and "owner: Procurement" in page and "PO owner P. Deshmukh (EHS)" in page
    assert any(e.label.startswith("Draft note to the vendor") for e in app.expander)


def test_processing_the_same_sample_again_is_shown_as_a_duplicate(app):
    process(app, "01")
    process(app, "01")
    assert "REJECT: this exact file was already processed" in shown(app)
    assert any("repeat is treated as a duplicate submission" in c.value for c in app.caption)


def test_a_broken_upload_is_refused_with_a_reason_not_an_error(app):
    app.radio(key="proc_source").set_value("Upload a file").run()
    app.file_uploader(key="proc_upload").set_value(("notes.txt", b"just some text, not a pdf", "text/plain")).run()
    app.button(key="proc_go_upload").click().run()
    assert not app.exception
    assert "REJECT: file cannot be opened" in shown(app)
    assert app.db.list_runs()[0]["verdict"] == "REJECT"


def test_text_from_an_uploaded_document_is_never_rendered_as_html(app):
    hostile = G.render_invoice_pdf(G.spec(
        id="x", layout="classic", vendor="V001", invoice_no="TOF/26-27/0499", invoice_date=G.date(2026, 11, 18),
        po_ref="PO-2026-0147", po_date=G.date(2026, 10, 20), vendor_name_printed="Tungabhadra <img src=x onerror=alert(1)> Ltd",
        lines=[G.line("Steel Storage Cupboard <script>alert(2)</script>", "9403", 1, "Nos", "9800.00")]))
    app.radio(key="proc_source").set_value("Upload a file").run()
    app.file_uploader(key="proc_upload").set_value(("<u onclick=alert(3)>evil.pdf", hostile, "application/pdf")).run()
    app.button(key="proc_go_upload").click().run()
    assert not app.exception
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
    app.radio(key="proc_source").set_value("Upload a file").run()
    app.file_uploader(key="proc_upload").set_value(("two_pages.pdf", two_pages, "application/pdf")).run()
    app.button(key="proc_go_upload").click().run()
    assert not app.exception and app.db.get_run("R-0001").page_count == 2
    pager = next(s for s in app.selectbox if s.label == "Page")
    assert list(pager.options) == ["1", "2"]
    pager.select(2).run()
    assert not app.exception
    app.toggle(key="beside_proc").set_value(False).run()
    assert not app.exception and not [s for s in app.selectbox if s.label == "Page"]      # document hidden, stages full width


def test_run_all_samples_then_the_dashboard_shows_the_history(app):
    app.button(key="proc_batch").click().run()
    assert not app.exception
    rows = app.session_state["batch"]
    assert len(rows) == 14 and all(r["As documented"] == "yes" for r in rows)
    assert "14 of 14 samples gave the documented decision" in shown(app)

    go(app, "dashboard")
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Documents processed"] == "14" and metrics["Approved with no person involved"] == "4"
    assert metrics["Waiting for a person"] == "5" and metrics["Rejected"] == "5"
    assert any("Audit trail intact: 14 receipt(s)" in s.value for s in app.success)
    history = app.dataframe[0].value
    assert len(history) == 14 and list(history["Run"])[0] == "R-0014" and set(history["Decision"]) == {"APPROVE", "HOLD", "REJECT"}
    # open a specific run record from the history
    picker = next(s for s in app.selectbox if s.label.startswith("Run to open"))
    picker.select("R-0005").run()
    assert not app.exception and "REJECT: same invoice under a different number" in shown(app)


def test_dashboard_filters(app):
    app.button(key="proc_batch").click().run()
    go(app, "dashboard")
    app.multiselect(key="dash_verdict").set_value(["HOLD"]).run()
    assert set(app.dataframe[0].value["Decision"]) == {"HOLD"} and len(app.dataframe[0].value) == 5
    app.multiselect(key="dash_verdict").set_value([]).run()
    app.text_input(key="dash_query").set_value("PO-2026-0193").run()
    assert list(app.dataframe[0].value["Run"]) == ["R-0004"]
    app.text_input(key="dash_query").set_value("no such thing").run()
    assert any("No run matches" in i.value for i in app.info)


def test_review_needs_a_name_then_records_the_decision(app):
    process(app, "08")
    go(app, "review")
    assert "HOLD: 2 points need a person" in shown(app)
    approve = next(b for b in app.button if b.label == "Approve for payment")
    approve.click().run()
    assert any("reviewer's name" in e.value for e in app.error)
    assert app.db.run_row("R-0001")["review_status"] == "PENDING"
    app.text_input(key="rev_name_R-0001").set_value("R. Iyer")
    app.text_input(key="rev_note_R-0001").set_value("Price increase agreed on 12 Nov")
    next(b for b in app.button if b.label == "Approve for payment").click().run()
    assert not app.exception
    assert app.db.run_row("R-0001")["review_status"] == "APPROVED"
    assert app.db.get_ledger("L-0001")["status"] == "POSTED"
    assert any("approved by R. Iyer" in s.value for s in app.success)
    assert [r["kind"] for r in app.db.receipts("R-0001")] == ["decision", "review"] and app.db.verify_chain()["ok"]
    assert "Nothing is waiting for a person" in shown(app)


def test_bank_change_cannot_be_approved_on_screen_without_the_call_back(app):
    process(app, "09")
    go(app, "review")
    app.text_input(key="rev_name_R-0001").set_value("N. Rao")
    next(b for b in app.button if b.label == "Approve for payment").click().run()
    assert any("call-back" in e.value for e in app.error) and app.db.run_row("R-0001")["review_status"] == "PENDING"
    app.text_input(key="rev_name_R-0001").set_value("N. Rao")
    app.checkbox(key="rev_bank_R-0001").check()
    next(b for b in app.button if b.label == "Approve for payment").click().run()
    assert app.db.run_row("R-0001")["review_status"] == "APPROVED"
    assert app.db.overrides("V001", "bank_account")[0]["value"] == "770031245566"


def test_an_unbooked_document_can_only_be_closed_by_hand_on_screen(app):
    from .test_review import _dollar_invoice
    app.radio(key="proc_source").set_value("Upload a file").run()
    app.file_uploader(key="proc_upload").set_value(("acme.pdf", _dollar_invoice(), "application/pdf")).run()
    app.button(key="proc_go_upload").click().run()
    assert "HOLD: invoice is not in rupees" in shown(app) and "USD 4,000.00" in shown(app)
    go(app, "review")
    labels = [b.label for b in app.button]
    assert "Mark as handled by hand" in labels and "Approve for payment" not in labels
    app.text_input(key="rev_name_R-0001").set_value("N. Rao")
    next(b for b in app.button if b.label == "Mark as handled by hand").click().run()
    assert app.db.run_row("R-0001")["review_status"] == "HANDLED"
    go(app, "dashboard")
    assert list(app.dataframe[0].value["Total"]) == ["USD 4000.00"] and list(app.dataframe[0].value["Review"]) == ["handled by hand"]
    assert {m.label: m.value for m in app.metric}["Released for payment"] == "₹0.00"


def test_rejecting_on_review(app):
    process(app, "04")
    go(app, "review")
    app.text_input(key="rev_name_R-0001").set_value("P. Deshmukh")
    next(b for b in app.button if b.label == "Reject").click().run()
    assert app.db.run_row("R-0001")["review_status"] == "REJECTED" and app.db.get_ledger("L-0001")["status"] == "REJECTED"


def test_what_if_sandbox_shows_a_decision_flip_and_touches_nothing(app):
    go(app, "reference")
    assert app.selectbox(key="wi_sample").value.startswith("07")
    app.slider(key="wi_price_pct").set_value(0.1).run()
    app.button(key="wi_go").click().run()
    assert not app.exception
    outcome = app.session_state["wi_result"]
    assert outcome["base"][0] == "APPROVE" and outcome["trial"][0] == "HOLD"
    assert any("The decision changed" in i.value for i in app.info)
    assert app.db.list_runs() == [] and len(app.db.ledger_entries()) == 10      # the real books are untouched


def test_reset_restores_the_seeded_state(app):
    process(app, "01")
    assert len(app.db.list_runs()) == 1
    app.button(key="reset_go").click().run()
    assert not app.exception
    assert app.db.list_runs() == [] and app.db.receipts() == [] and len(app.db.ledger_entries()) == 10
    assert any("Demo data reset" in s.value for s in app.success)
    assert "current_run" not in app.session_state


def test_rules_page_lists_every_code_and_the_master_data(app, rules, master):
    go(app, "reference")
    decision_table = app.dataframe[0].value
    assert set(decision_table["Code"]) == set(rules.reasons)
    everything = " ".join(str(v) for frame in app.dataframe for v in frame.value.to_numpy().ravel())
    assert "PO-2026-0193" in everything and master.vendors["V004"].legal_name in everything and "ASS/2026/0871" in everything


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


def test_a_key_pasted_into_the_hosts_secrets_box_is_picked_up(tmp_path, monkeypatch):
    """On Streamlit Community Cloud the key goes into the Secrets box, not a .env file."""
    monkeypatch.setenv("INVOICE_DESK_DB", str(tmp_path / "ui.db"))
    for name in ("LLM_MODE", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    at = AppTest.from_file(str(config.ROOT / "app.py"), default_timeout=180)
    at.secrets["LLM_BASE_URL"] = "https://llm.invalid/v1"
    at.secrets["LLM_API_KEY"] = "secret-from-the-host"
    at.secrets["LLM_MODEL"] = "hosted-model"
    at.run()
    assert not at.exception
    sidebar = " ".join(m.value for m in at.sidebar.markdown)
    assert "LIVE (hosted-model)" in sidebar and "secret-from-the-host" not in sidebar
