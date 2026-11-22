"""LLM assist. No network here: the model is replaced by a stub transport, which is
exactly the seam the real HTTP call goes through. What is tested is everything around
the model: when it is called, how its answer is verified, and what happens when it is
wrong, slow or absent."""
import json

import pytest

from invoice_desk import config, reader
from invoice_desk.extract import extract_invoice
from invoice_desk.llm import LlmAssist, parse_json_reply, value_in_quote
from invoice_desk.pipeline import Pipeline
from invoice_desk.store import Store
from scripts import generate_test_data as G

from .conftest import TODAY, sample_bytes

LIVE = config.LlmConfig("live", "https://llm.invalid/v1", "test-key", "stub-model", 5.0)
REPLAY = config.LlmConfig("replay", "", "", "", 5.0)
LETTER = "10_ai_assist_letter_invoice"

GOOD = {
    "invoice_number": {"value": "DQI/26-27/1213", "quote": "Subject: Tax invoice DQI/26-27/1213 for network equipment"},
    "invoice_date": {"value": "2026-11-14", "quote": "Date: 14 November 2026"},
    "po_reference": {"value": "PO-2026-0178", "quote": "your purchase order PO-2026-0178 dated 02 November 2026"},
    "taxable_value": {"value": 195000.00, "quote": "value of this supply is Rs. 1,95,000.00"},
    "cgst": {"value": None, "quote": None}, "sgst": {"value": None, "quote": None},
    "igst": {"value": 35100.00, "quote": "IGST at 18 percent comes to Rs. 35,100.00"},
    "total": {"value": 230100.00, "quote": "payable is Rs. 2,30,100.00"},
    "line_items": [
        {"description": "Managed network switches (24 port gigabit)", "hsn": "8517", "qty": 6, "unit_price": 18500.00,
         "amount": 111000.00,
         "quote": "six managed network switches (24 port gigabit) at Rs. 18,500.00 each, amounting to Rs. 1,11,000.00"},
        {"description": "Online UPS units of 3 kVA with batteries", "hsn": "8504", "qty": 2, "unit_price": 42000.00,
         "amount": 84000.00,
         "quote": "two online UPS units of 3 kVA with batteries at Rs. 42,000.00 each, amounting to Rs. 84,000.00"}],
}


class Stub:
    """Stands in for the HTTP call and counts how often it is used."""
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls, self.payloads = reply, error, 0, []

    def __call__(self, payload, cfg):
        self.calls += 1
        self.payloads.append(payload)
        if self.error:
            raise self.error
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def make(tmp_path, stub, cfg=LIVE, rules=None, master=None):
    from invoice_desk.masterdata import load_master
    from invoice_desk.rules import load_rules
    llm = LlmAssist(cfg, transport=stub, fixtures_dir=tmp_path / "llm")
    return Pipeline(store=Store(tmp_path / "t.db"), rules=rules or load_rules(), master=master or load_master(),
                    llm=llm, today=TODAY, ocr_mode="fixture")


def reasons(r):
    return {f.code for f in r.decision.reasons}


def test_rules_alone_hold_the_letter(tmp_path):
    stub = Stub(GOOD)
    r = make(tmp_path, stub, config.LlmConfig("off", "", "", "", 5.0)).run(sample_bytes(LETTER), "letter.pdf", "test")
    assert r.verdict == "HOLD" and reasons(r) == {"LINE_ITEMS_UNREADABLE", "MISSING_INVOICE_NUMBER", "MISSING_TOTAL"}
    assert stub.calls == 0


def test_with_a_verified_model_answer_the_letter_is_matched_and_approved(tmp_path):
    stub = Stub(GOOD)
    r = make(tmp_path, stub).run(sample_bytes(LETTER), "letter.pdf", "test")
    assert stub.calls == 1 and r.verdict == "APPROVE"
    assert [f.code for f in r.decision.notes] == ["LLM_ASSISTED"]                 # and the record says a model helped
    assert set(r.invoice.llm["accepted"]) == {"invoice_number", "total", "subtotal", "igst", "line_items"}
    assert r.invoice.get("total") == "230100.00" and r.invoice.fields["total"].source.method == "llm"
    assert [(i.qty, i.unit_price, i.amount) for i in r.invoice.line_items] == [(6, 18500, 111000), (2, 42000, 84000)]
    assert all(i.source.method == "llm" and "read_by_llm" in i.flags for i in r.invoice.line_items)
    assert r.po["po_number"] == "PO-2026-0178" and r.po["lines_matched"] == 2
    from invoice_desk import samples
    sample = next(s for s in samples.load_manifest() if s["id"] == LETTER)
    assert samples.check(sample, r)[0]                                            # the manifest's with_llm expectation


def test_the_model_is_sent_the_page_text_and_nothing_else(tmp_path, master):
    stub = Stub(GOOD)
    make(tmp_path, stub).run(sample_bytes(LETTER), "letter.pdf", "test")
    sent = json.dumps(stub.payloads[0])
    assert "DQI/26-27/1213" in sent and stub.payloads[0]["temperature"] == 0
    for po in master.pos.values():                       # the PO register never reaches the model
        for line in po.lines:
            assert str(line.unit_price) not in sent or po.po_number == "PO-2026-0178"
    assert "PO-2026-0171" not in sent and "004512309876" not in sent and "test-key" not in sent


def test_the_model_is_not_called_when_the_rules_read_everything(tmp_path):
    stub = Stub(GOOD)
    r = make(tmp_path, stub).run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    assert stub.calls == 0 and r.verdict == "APPROVE" and r.decision.notes == []


def test_a_value_whose_quote_is_not_on_the_page_is_rejected(tmp_path):
    bad = json.loads(json.dumps(GOOD))
    bad["total"] = {"value": 320100.00, "quote": "the total amount payable is Rs. 3,20,100.00"}      # invented
    r = make(tmp_path, Stub(bad)).run(sample_bytes(LETTER), "letter.pdf", "test")
    assert not r.invoice.has("total") and "MISSING_TOTAL" in reasons(r) and r.verdict == "HOLD"
    assert {"field": "total", "value": 320100.0, "why": "its quote is not on the page"} in r.invoice.llm["rejected"]


def test_a_value_that_its_own_quote_does_not_state_is_rejected(tmp_path):
    bad = json.loads(json.dumps(GOOD))
    bad["total"] = {"value": 203100.00, "quote": "payable is Rs. 2,30,100.00"}       # real quote, transposed digits
    r = make(tmp_path, Stub(bad)).run(sample_bytes(LETTER), "letter.pdf", "test")
    assert not r.invoice.has("total") and r.verdict == "HOLD"
    assert any(x["field"] == "total" and x["why"] == "the value is not stated in its quote" for x in r.invoice.llm["rejected"])


def test_one_bad_line_item_means_no_line_items_are_taken(tmp_path):
    bad = json.loads(json.dumps(GOOD))
    bad["line_items"][1]["qty"] = 3                                                   # quote says two
    r = make(tmp_path, Stub(bad)).run(sample_bytes(LETTER), "letter.pdf", "test")
    assert r.invoice.line_items == [] and "LINE_ITEMS_UNREADABLE" in reasons(r)


def test_an_invented_line_item_is_caught_by_the_po_match_even_if_quoted(tmp_path):
    bad = json.loads(json.dumps(GOOD))
    bad["line_items"][0]["description"] = "Extended warranty and installation charges"   # numbers real, item made up
    r = make(tmp_path, Stub(bad)).run(sample_bytes(LETTER), "letter.pdf", "test")
    assert r.verdict == "HOLD" and "LINE_NOT_ON_PO" in reasons(r)


def test_the_model_never_overwrites_a_value_the_rules_read(tmp_path, master):
    doc = reader.read_pdf(sample_bytes(LETTER))
    inv = extract_invoice(doc, G.BUYER["gstin"], G.BUYER["name"], master.gstin_index, list(master.pos))
    assert inv.get("invoice_date") == "2026-11-14"
    wrong = json.loads(json.dumps(GOOD))
    wrong["invoice_date"] = {"value": "2026-11-02", "quote": "dated 02 November 2026"}   # a real date on the page, the wrong one
    report = LlmAssist(LIVE, transport=Stub(wrong), fixtures_dir=tmp_path).assist(doc, inv)
    assert inv.get("invoice_date") == "2026-11-14"
    assert report["disagreements"] == [{"field": "invoice_date", "rules": "2026-11-14", "llm": "2026-11-02"}]


@pytest.mark.parametrize("reply", ["I'm sorry, I cannot help with that.", "{not json", "[1, 2, 3]", ""])
def test_an_unusable_reply_changes_nothing(reply, tmp_path):
    r = make(tmp_path, Stub(reply)).run(sample_bytes(LETTER), "letter.pdf", "test")
    assert r.verdict == "HOLD" and reasons(r) == {"LINE_ITEMS_UNREADABLE", "MISSING_INVOICE_NUMBER", "MISSING_TOTAL"}


@pytest.mark.parametrize("error", [TimeoutError("timed out"), ConnectionError("no route"), RuntimeError("429 quota"), KeyError("choices")])
def test_when_the_model_is_down_the_run_carries_on_without_it(error, tmp_path):
    stub = Stub(error=error)
    r = make(tmp_path, stub).run(sample_bytes(LETTER), "letter.pdf", "test")
    assert stub.calls == 1 and r.verdict == "HOLD" and "INTERNAL_ERROR" not in reasons(r)
    assert all(s.status != "error" for s in r.stages)
    llm_block = next(b for b in r.stages[2].blocks if b["title"] == "LLM assist")
    assert type(error).__name__ in next(row["Value"] for row in llm_block["rows"] if row["Item"] == "Error")


def test_a_live_answer_is_recorded_and_can_be_replayed_with_no_network(tmp_path):
    live = make(tmp_path / "a", Stub(GOOD))
    first = live.run(sample_bytes(LETTER), "letter.pdf", "test")
    recording = live.llm.recording_path(first.file_sha256)
    saved = json.loads(recording.read_text(encoding="utf-8"))
    assert saved["model"] == "stub-model" and saved["sha256"] == first.file_sha256 and "test-key" not in recording.read_text(encoding="utf-8")

    offline = Stub(error=AssertionError("replay must not touch the network"))
    replay = Pipeline(store=Store(tmp_path / "b.db"), llm=LlmAssist(REPLAY, transport=offline, fixtures_dir=tmp_path / "a" / "llm"),
                      today=TODAY, ocr_mode="fixture")
    second = replay.run(sample_bytes(LETTER), "letter.pdf", "test")
    assert offline.calls == 0 and second.verdict == "APPROVE" and second.invoice.llm["source"] == "replay"
    # same decision and same words, except that the record states truthfully where the answer came from
    assert "(live, stub-model)" in first.decision.explanation and "(replay, stub-model)" in second.decision.explanation
    assert second.decision.explanation.replace("(replay, ", "(live, ") == first.decision.explanation
    assert [(i.description, i.qty, i.amount) for i in second.invoice.line_items] == \
        [(i.description, i.qty, i.amount) for i in first.invoice.line_items]


def test_a_failed_live_call_falls_back_to_the_recording(tmp_path):
    make(tmp_path, Stub(GOOD)).run(sample_bytes(LETTER), "letter.pdf", "test")          # records
    flaky = Pipeline(store=Store(tmp_path / "c.db"), llm=LlmAssist(LIVE, transport=Stub(error=TimeoutError("slow")),
                                                                    fixtures_dir=tmp_path / "llm"), today=TODAY, ocr_mode="fixture")
    r = flaky.run(sample_bytes(LETTER), "letter.pdf", "test")
    assert r.verdict == "APPROVE" and r.invoice.llm["source"] == "replay" and "recorded response was used" in r.invoice.llm["note"]


def test_replay_without_a_recording_does_nothing(tmp_path):
    stub = Stub(GOOD)
    r = make(tmp_path, stub, REPLAY).run(sample_bytes(LETTER), "letter.pdf", "test")
    assert stub.calls == 0 and r.verdict == "HOLD"
    assert reasons(r) == {"LINE_ITEMS_UNREADABLE", "MISSING_INVOICE_NUMBER", "MISSING_TOTAL"}


def test_no_recordings_are_bundled_that_did_not_come_from_a_real_model():
    """This project was built without access to a live model, so the shipped folder must be empty of recordings."""
    shipped = [p.name for p in (config.FIXTURES_DIR / "llm").glob("*.json")]
    for name in shipped:
        saved = json.loads((config.FIXTURES_DIR / "llm" / name).read_text(encoding="utf-8"))
        assert saved.get("model") and saved.get("model") != "stub-model" and saved.get("recorded_on")


def test_llm_mode_resolution(monkeypatch):
    for name in ("LLM_MODE", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_MODE", "auto")
    assert config.llm_config().mode == "replay"                          # nothing configured: recorded responses only
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    assert config.llm_config().mode == "live" and config.llm_config().label == "LIVE (m)"
    monkeypatch.setenv("LLM_MODE", "off")
    assert config.llm_config().mode == "off"
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.delenv("LLM_API_KEY")
    assert config.llm_config().mode == "off"                             # asked for live without a key: off, not a crash


def test_parse_json_reply_tolerates_fences_and_prose():
    assert parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_reply('Here you go:\n{"a": {"b": 2}}\nHope that helps') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_json_reply("no object here")


@pytest.mark.parametrize("kind,value,quote,ok", [
    ("amount", 230100, "payable is Rs. 2,30,100.00", True),
    ("amount", "230100.00", "payable is Rs. 2,30,100.00", True),
    ("amount", 230100, "payable is Rs. 2,30,010.00", False),
    ("amount", 6, "six managed network switches", True),
    ("amount", 7, "six managed network switches", False),
    ("id", "DQI/26-27/1213", "Tax invoice DQI/26-27/1213 for network equipment", True),
    ("id", "DQI/26-27/1218", "Tax invoice DQI/26-27/1213 for network equipment", False),
    ("date", "2026-11-14", "Date: 14 November 2026", True),
    ("date", "2026-11-15", "Date: 14 November 2026", False),
    ("amount", None, "anything", False),
    ("amount", 5, "", False),
])
def test_value_in_quote(kind, value, quote, ok):
    assert value_in_quote(kind, value, quote) is ok
