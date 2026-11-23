"""The Claude layer. There is no network here: the real Anthropic SDK is used, with a fake
transport standing in for the API (the seam the SDK documents for proxies and tests). So
what is tested is everything this project wrote around the model: the request that goes
out, when the model is asked, how its answer is verified, how answers are stored and
replayed, the cost caps, and what happens when the API is wrong, slow or absent."""
import json
from datetime import datetime, timezone

import anthropic
import httpx2
import pytest

from invoice_desk import config, llm as llm_module, reader, samples
from invoice_desk.extract import extract_invoice
from invoice_desk.llm import (PROMPT_VERSIONS, LlmAssist, SpendLedger, cost_usd, id_in_quote, template_sha256,
                              value_in_quote)
from invoice_desk.pipeline import Pipeline
from invoice_desk.store import Store
from scripts import generate_test_data as G

from .conftest import TODAY, sample_bytes

LETTER = "10_ai_assist_letter_invoice"
RULES_ONLY = {"LINE_ITEMS_UNREADABLE", "MISSING_INVOICE_NUMBER", "MISSING_TOTAL"}

# What a correct answer for the letter looks like. Every value is copied as written on the
# page ("six", "2,30,100.00", "14 November 2026"); code turns it into numbers and dates.
GOOD = {
    "document_type": "tax_invoice", "confidence": "high", "notes": "",
    "fields": [
        {"name": "invoice_number", "value": "DQI/26-27/1213", "quote": "Subject: Tax invoice DQI/26-27/1213 for network equipment"},
        {"name": "invoice_date", "value": "14 November 2026", "quote": "Date: 14 November 2026"},
        {"name": "po_reference", "value": "PO-2026-0178", "quote": "your purchase order PO-2026-0178 dated 02 November 2026"},
        {"name": "taxable_value", "value": "1,95,000.00", "quote": "value of this supply is Rs. 1,95,000.00"},
        {"name": "igst", "value": "35,100.00", "quote": "IGST at 18 percent comes to Rs. 35,100.00"},
        {"name": "total", "value": "2,30,100.00", "quote": "payable is Rs. 2,30,100.00"}],
    "line_items": [
        {"description": "Managed network switches (24 port gigabit)", "hsn": "8517", "quantity": "six",
         "unit_price": "18,500.00", "amount": "1,11,000.00",
         "quote": "six managed network switches (24 port gigabit) at Rs. 18,500.00 each, amounting to Rs. 1,11,000.00"},
        {"description": "Online UPS units of 3 kVA with batteries", "hsn": "8504", "quantity": "two",
         "unit_price": "42,000.00", "amount": "84,000.00",
         "quote": "two online UPS units of 3 kVA with batteries at Rs. 42,000.00 each, amounting to Rs. 84,000.00"}],
}


def changed(**edits) -> dict:
    """GOOD with one field replaced: changed(total={"value": ..., "quote": ...})."""
    answer = json.loads(json.dumps(GOOD))
    for name, new in edits.items():
        if name in ("line_items", "confidence", "document_type", "notes"):
            answer[name] = new
        else:
            answer["fields"] = [f for f in answer["fields"] if f["name"] != name] + ([{"name": name, **new}] if new else [])
    return answer


class FakeApi:
    """Stands in for api.anthropic.com. Counts calls and keeps what was sent."""

    def __init__(self, reply=None, status=200, raises=None, stop_reason="end_turn", usage=(1900, 420), script=None):
        self.reply, self.status, self.raises, self.stop_reason, self.usage = reply, status, raises, stop_reason, usage
        self.script = list(script or [])            # optional per-call overrides: dicts of the arguments above
        self.calls, self.bodies, self.headers = 0, [], []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        body = json.loads(request.content)
        self.bodies.append(body)
        self.headers.append({k.lower(): v for k, v in request.headers.items()})
        step = self.script.pop(0) if self.script else {}
        reply, status = step.get("reply", self.reply), step.get("status", self.status)
        raises = step.get("raises", self.raises)
        if raises is not None:
            raise raises("simulated", request=request)
        if status != 200:
            return httpx2.Response(status, headers={"retry-after-ms": "1"},
                                   json={"type": "error", "error": {"type": "api_error", "message": f"simulated {status}"}})
        if isinstance(reply, bytes):
            return httpx2.Response(200, content=reply, headers={"content-type": "text/html"})
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return httpx2.Response(200, headers={"request-id": "req_fake_0001"}, json={
            "id": "msg_fake_0001", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": text}], "stop_reason": step.get("stop_reason", self.stop_reason),
            "stop_sequence": None, "usage": {"input_tokens": self.usage[0], "output_tokens": self.usage[1]}})


def assistant(tmp_path, api, mode="cached-live", key="test-key-123", **settings) -> LlmAssist:
    settings.setdefault("max_retries", 0)
    cfg = config.LlmConfig(mode=mode, api_key=key, timeout_s=5.0, **settings)
    return LlmAssist(cfg, fixtures_dir=tmp_path / "fixtures", cache_dir=tmp_path / "cache",
                     ledger=SpendLedger(tmp_path / "spend.db"),
                     http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(api)))


def make(tmp_path, api, mode="cached-live", rules=None, master=None, db="t.db", **settings) -> Pipeline:
    from invoice_desk.masterdata import load_master
    from invoice_desk.rules import load_rules
    return Pipeline(store=Store(tmp_path / db), rules=rules or load_rules(), master=master or load_master(),
                    llm=assistant(tmp_path, api, mode, **settings), today=TODAY, ocr_mode="fixture")


def reasons(r) -> set[str]:
    return {f.code for f in r.decision.reasons}


def letter(pipeline):
    return pipeline.run(sample_bytes(LETTER), "letter.pdf", "test")


# ---------------------------------------------------------------- when the model is asked, and what it is sent

def test_rules_alone_hold_the_letter(tmp_path):
    api = FakeApi(GOOD)
    r = letter(make(tmp_path, api, mode="off"))
    assert r.verdict == "HOLD" and reasons(r) == RULES_ONLY and api.calls == 0


def test_with_a_verified_answer_the_letter_is_read_matched_and_approved(tmp_path):
    api = FakeApi(GOOD)
    r = letter(make(tmp_path, api))
    assert api.calls == 1 and r.verdict == "APPROVE"
    assert [f.code for f in r.decision.notes] == ["LLM_ASSISTED"]                  # the record says a model helped
    assert set(r.invoice.llm["accepted"]) == {"invoice_number", "total", "subtotal", "igst", "line_items"}
    assert r.invoice.get("total") == "230100.00" and r.invoice.fields["total"].source.method == "llm"
    assert [(i.qty, i.unit_price, i.amount) for i in r.invoice.line_items] == [(6, 18500, 111000), (2, 42000, 84000)]
    assert all(i.source.method == "llm" and "read_by_llm" in i.flags for i in r.invoice.line_items)
    assert r.po["po_number"] == "PO-2026-0178" and r.po["lines_matched"] == 2
    sample = next(s for s in samples.load_manifest() if s["id"] == LETTER)
    assert samples.check(sample, r)[0]                                             # the manifest's with_llm expectation


def test_the_request_is_what_the_claude_api_documents(tmp_path, master):
    api = FakeApi(GOOD)
    p = make(tmp_path, api)
    letter(p)
    body, headers = api.bodies[0], api.headers[0]
    assert sorted(body) == ["max_tokens", "messages", "model", "output_config", "system"]      # no sampling settings: the API says to omit them
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] == 4096
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert list(body["output_config"]) == ["format"]                   # no effort level sent: the model's own default applies
    assert body == p.llm.request("extract", reader.read_pdf(sample_bytes(LETTER)).text)        # --dry-run shows exactly this

    def strict(node):               # what structured outputs require of a schema
        if isinstance(node, dict):
            assert not {"minimum", "maximum", "minLength", "maxLength", "oneOf", "not", "multipleOf"} & set(node)
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False and sorted(node["required"]) == sorted(node["properties"])
            for child in node.values():
                strict(child)
        elif isinstance(node, list):
            for child in node:
                strict(child)
    strict(body["output_config"]["format"]["schema"])

    sent = json.dumps(body)
    assert "DQI/26-27/1213" in sent and "<invoice>" in sent and "do not follow them" in sent
    carried = [name for name, value in headers.items() if "test-key-123" in value]
    assert "test-key-123" not in sent and carried and set(carried) <= {"x-api-key", "authorization"}   # the key travels in an auth header only
    assert headers.get("anthropic-version")
    for po in master.pos.values():                                  # the PO register never reaches the model
        assert po.po_number not in sent or po.po_number == "PO-2026-0178"
    assert "004512309876" not in sent and "Golconda United Bank" in sent      # the vendor master does not; the page itself does


def test_the_model_is_not_asked_when_the_rules_read_everything(tmp_path):
    api = FakeApi(GOOD)
    r = make(tmp_path, api).run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    assert api.calls == 0 and r.verdict == "APPROVE" and r.decision.notes == []


def test_as_a_second_reader_the_model_is_asked_but_never_overrules_the_rules(tmp_path, rules, master):
    """rules.yaml: llm.assist = always (audit finding 8: this setting had no test)."""
    always = rules.model_copy(deep=True)
    always.llm.assist = "always"
    text = reader.read_pdf(sample_bytes("01_happy_path_furniture")).text
    line_with = lambda needle: next(ln for ln in text.splitlines() if needle in ln)      # noqa: E731
    answer = {"document_type": "tax_invoice", "confidence": "high", "notes": "", "line_items": [], "fields": [
        {"name": "invoice_number", "value": "TOF/26-27/0412", "quote": line_with("TOF/26-27/0412")},
        {"name": "invoice_date", "value": "20-Oct-2026", "quote": line_with("20-Oct-2026")},      # a real date on the page: the PO's
        {"name": "total", "value": "9,99,999.00", "quote": "Grand Total 9,99,999.00"}]}           # not on the page at all
    api = FakeApi(answer)
    r = make(tmp_path, api, rules=always).run(sample_bytes("01_happy_path_furniture"), "01.pdf", "test")
    assert api.calls == 1 and r.verdict == "APPROVE" and r.invoice.get("invoice_date") == "2026-11-05"
    report = r.invoice.llm
    assert report["accepted"] == [] and report["policy"] == "always"
    assert report["disagreements"] == [{"field": "invoice_date", "rules": "2026-11-05", "llm": "2026-10-20"}]
    assert [x["why"] for x in report["rejected"]] == ["its quote is not on the page"]
    assert "LLM_ASSISTED" not in {f.code for f in r.decision.notes}                 # nothing was taken from it


# ---------------------------------------------------------------- how an answer is verified

def test_a_value_whose_quote_is_not_on_the_page_is_rejected(tmp_path):
    bad = changed(total={"value": "3,20,100.00", "quote": "the total amount payable is Rs. 3,20,100.00"})      # invented
    r = letter(make(tmp_path, FakeApi(bad)))
    assert not r.invoice.has("total") and "MISSING_TOTAL" in reasons(r) and r.verdict == "HOLD"
    assert {"field": "total", "value": "3,20,100.00", "why": "its quote is not on the page"} in r.invoice.llm["rejected"]


def test_a_value_that_its_own_quote_does_not_state_is_rejected(tmp_path):
    bad = changed(total={"value": "2,03,100.00", "quote": "payable is Rs. 2,30,100.00"})       # real quote, two digits swapped
    r = letter(make(tmp_path, FakeApi(bad)))
    assert not r.invoice.has("total") and r.verdict == "HOLD"
    assert any(x["field"] == "total" and x["why"] == "the value is not stated in its quote" for x in r.invoice.llm["rejected"])


def test_a_piece_of_a_longer_number_is_not_the_number(tmp_path):
    """Audit finding 6: the identifier check accepted any substring ('12' against 'INV-0012')."""
    bad = changed(invoice_number={"value": "1213", "quote": "Subject: Tax invoice DQI/26-27/1213 for network equipment"})
    r = letter(make(tmp_path, FakeApi(bad)))
    assert not r.invoice.has("invoice_number") and "MISSING_INVOICE_NUMBER" in reasons(r)
    assert any(x["field"] == "invoice_number" and x["why"] == "the value is not stated in its quote" for x in r.invoice.llm["rejected"])


def test_a_number_is_taken_only_if_its_quote_says_what_it_is(tmp_path):
    bad = changed(invoice_number={"value": "PO-2026-0178", "quote": "PO-2026-0178 dated 02 November 2026"})    # a real number, the wrong one
    r = letter(make(tmp_path, FakeApi(bad)))
    assert not r.invoice.has("invoice_number") and r.verdict == "HOLD"
    assert any(x["why"] == "its quote does not say what the number is" for x in r.invoice.llm["rejected"])


def test_one_bad_line_item_means_no_line_items_are_taken(tmp_path):
    lines = json.loads(json.dumps(GOOD["line_items"]))
    lines[1]["quantity"] = "three"                                                    # the page says two
    r = letter(make(tmp_path, FakeApi(changed(line_items=lines))))
    assert r.invoice.line_items == [] and "LINE_ITEMS_UNREADABLE" in reasons(r)


def test_an_invented_line_item_is_caught_by_the_po_match_even_if_quoted(tmp_path):
    lines = json.loads(json.dumps(GOOD["line_items"]))
    lines[0]["description"] = "Extended warranty and installation charges"            # numbers real, item made up
    r = letter(make(tmp_path, FakeApi(changed(line_items=lines))))
    assert r.verdict == "HOLD" and "LINE_NOT_ON_PO" in reasons(r)


def test_a_real_sentence_used_for_the_wrong_field_is_caught_by_the_arithmetic(tmp_path):
    """The model can be misled by what a page says, as a person can. The checks that do not depend on it catch that."""
    bad = changed(total={"value": "1,95,000.00", "quote": "value of this supply is Rs. 1,95,000.00"})    # the taxable value, offered as the total
    r = letter(make(tmp_path, FakeApi(bad)))
    assert r.verdict == "HOLD" and "ARITHMETIC_MISMATCH" in reasons(r)


def test_the_model_never_overwrites_a_value_the_rules_read(tmp_path, master, rules):
    doc = reader.read_pdf(sample_bytes(LETTER))
    inv = extract_invoice(doc, G.BUYER["gstin"], G.BUYER["name"], master.gstin_index, list(master.pos))
    assert inv.get("invoice_date") == "2026-11-14"
    wrong = changed(invoice_date={"value": "02 November 2026", "quote": "dated 02 November 2026"})      # a real date on the page, the wrong one
    report = assistant(tmp_path, FakeApi(wrong)).assist(doc, inv, "gaps", rules)
    assert inv.get("invoice_date") == "2026-11-14"
    assert report["disagreements"] == [{"field": "invoice_date", "rules": "2026-11-14", "llm": "2026-11-02"}]


def test_what_a_model_read_value_is_worth_comes_from_rules_yaml(tmp_path, rules):
    r = letter(make(tmp_path, FakeApi(GOOD)))
    levels = rules.llm.confidence
    assert r.invoice.fields["total"].confidence == levels.verified                    # quote, value, and the arithmetic agrees
    assert r.invoice.fields["invoice_number"].confidence == levels.quote_only         # nothing else can confirm a number
    assert all(i.confidence == levels.verified for i in r.invoice.line_items)
    assert levels.quote_only >= rules.reading.min_confidence_critical > levels.from_scan > levels.model_unsure


def test_when_the_model_is_unsure_the_invoice_is_held(tmp_path, rules):
    """The model's own doubt can only lower trust. Its confidence is never a reason to approve."""
    r = letter(make(tmp_path, FakeApi(changed(confidence="low"))))
    assert r.verdict == "HOLD" and reasons(r) == {"LOW_CONFIDENCE_READ"}
    assert r.invoice.fields["total"].confidence == rules.llm.confidence.model_unsure
    assert r.invoice.get("total") == "230100.00"                                      # read and shown, but a person checks it


def test_values_read_from_a_scan_are_held_for_a_person(tmp_path, master, rules):
    doc = reader.read_pdf(sample_bytes(LETTER))
    for page in doc.pages:
        page.kind = "scan"                                                            # as if the same text had come from OCR
    inv = extract_invoice(doc, G.BUYER["gstin"], G.BUYER["name"], master.gstin_index, list(master.pos))
    assistant(tmp_path, FakeApi(GOOD)).assist(doc, inv, "gaps", rules)
    assert inv.fields["total"].confidence == rules.llm.confidence.from_scan < rules.reading.min_confidence_critical
    from invoice_desk.validate import validate
    assert "LOW_CONFIDENCE_READ" in {f.code for f in validate(inv, rules, TODAY)[0]}


@pytest.mark.parametrize("value,quote,ok", [
    ("DQI/26-27/1213", "Tax invoice DQI/26-27/1213 for network equipment", True),
    ("dqi/26-27/1213", "Tax invoice DQI/26-27/1213.", True),                  # case and a full stop after it do not matter
    ("DQI/26-27/1218", "Tax invoice DQI/26-27/1213 for network equipment", False),
    ("1213", "Tax invoice DQI/26-27/1213 for network equipment", False),      # a piece of a longer number
    ("12", "Invoice no. INV-0012", False),                                    # the audit's example
    ("0012", "Invoice no. INV-0012", False),
    ("INV-0012", "Invoice no. INV-0012", True),
    ("26-27", "Tax invoice DQI/26-27/1213", False),
    ("4471", "Bill No. 4471 dated 3 Nov", True),
    ("", "anything", False), ("---", "a --- b", False),
])
def test_an_identifier_must_be_the_whole_identifier_in_its_quote(value, quote, ok):
    assert id_in_quote(value, quote) is ok


@pytest.mark.parametrize("kind,value,quote,ok", [
    ("amount", "2,30,100.00", "payable is Rs. 2,30,100.00", True),
    ("amount", "230100", "payable is Rs. 2,30,100.00", True),
    ("amount", 230100, "payable is Rs. 2,30,100.00", True),
    ("amount", "2,30,010.00", "payable is Rs. 2,30,100.00", False),
    ("amount", "six", "six managed network switches", True),
    ("amount", "seven", "six managed network switches", False),
    ("amount", "6", "six managed network switches", True),
    ("id", "DQI/26-27/1213", "Tax invoice DQI/26-27/1213 for network equipment", True),
    ("date", "14 November 2026", "Date: 14 November 2026", True),
    ("date", "2026-11-14", "Date: 14 November 2026", True),
    ("date", "15 November 2026", "Date: 14 November 2026", False),
    ("date", "sometime soon", "Date: 14 November 2026", False),
    ("amount", None, "anything", False), ("amount", "5", "", False),
])
def test_value_in_quote(kind, value, quote, ok):
    assert value_in_quote(kind, value, quote) is ok


# ---------------------------------------------------------------- when the API is wrong, slow or absent

@pytest.mark.parametrize("api", [
    FakeApi(GOOD, stop_reason="max_tokens"), FakeApi(GOOD, stop_reason="refusal"),
    FakeApi("I am sorry, I cannot help with that."), FakeApi("{not json"), FakeApi("[1, 2, 3]"),
    FakeApi({"fields": []}), FakeApi(b"<html>bad gateway</html>"),
], ids=["cut off at max_tokens", "refusal", "prose", "broken json", "wrong json type", "schema not met", "html from a proxy"])
def test_an_unusable_answer_changes_nothing_and_is_not_stored(api, tmp_path):
    p = make(tmp_path, api)
    r = letter(p)
    assert api.calls == 1 and r.verdict == "HOLD" and reasons(r) == RULES_ONLY
    assert all(s.status != "error" for s in r.stages) and r.invoice.llm["error"]
    assert list((tmp_path / "cache").glob("*.json")) == []                             # nothing bad is ever replayed


@pytest.mark.parametrize("api,name", [
    (FakeApi(status=401), "AuthenticationError"), (FakeApi(status=429), "RateLimitError"),
    (FakeApi(status=500), "InternalServerError"), (FakeApi(status=529), "OverloadedError"),
    (FakeApi(raises=httpx2.ReadTimeout), "APITimeoutError"), (FakeApi(raises=httpx2.ConnectError), "APIConnectionError"),
], ids=["bad key", "rate limited", "server error", "overloaded", "timeout", "no network"])
def test_when_the_api_fails_the_run_carries_on_without_it(api, name, tmp_path):
    r = letter(make(tmp_path, api))
    assert api.calls == 1 and r.verdict == "HOLD" and reasons(r) == RULES_ONLY and "INTERNAL_ERROR" not in reasons(r)
    assert all(s.status != "error" for s in r.stages)
    block = next(b for b in r.stages[2].blocks if b["title"] == "AI assist (Claude)")
    assert name in next(row["Value"] for row in block["rows"] if row["Item"] == "Note")     # and the screen says why


def test_a_busy_api_is_retried_a_bounded_number_of_times(tmp_path):
    api = FakeApi(GOOD, script=[{"status": 529}, {"status": 529}])                    # busy twice, then fine
    r = letter(make(tmp_path, api, max_retries=2))
    assert api.calls == 3 and r.verdict == "APPROVE"
    stuck = FakeApi(status=529)
    r = letter(make(tmp_path / "b", stuck, max_retries=2))
    assert stuck.calls == 3 and r.verdict == "HOLD"                                   # three attempts, then the rules-only result


# ---------------------------------------------------------------- stored answers: same file, same answer

def test_an_answer_is_stored_and_reused_without_the_network(tmp_path):
    first_api = FakeApi(GOOD)
    first = letter(make(tmp_path, first_api, db="a.db"))
    stored = list((tmp_path / "cache").glob("*.json"))
    assert first_api.calls == 1 and len(stored) == 1
    assert stored[0].name == f"extract__{first.file_sha256[:16]}__{PROMPT_VERSIONS['extract']}__claude-haiku-5-5.json"
    saved = json.loads(stored[0].read_text(encoding="utf-8"))
    assert saved["key"] == first.file_sha256 and saved["model"] == "claude-haiku-5-5" and saved["response"] == GOOD
    assert saved["template_sha256"] == template_sha256("extract") and saved["usage"]["input_tokens"] == 1900
    assert "test-key-123" not in stored[0].read_text(encoding="utf-8")

    offline = FakeApi(raises=httpx2.ConnectError)                                     # any call now would fail
    second = letter(make(tmp_path, offline, db="b.db"))
    assert offline.calls == 0 and second.verdict == "APPROVE" and second.invoice.llm["source"] == "cache"
    # the same decision in the same words, except that the record says truthfully where the answer came from
    assert "a live call" in first.decision.explanation and "a stored answer" in second.decision.explanation
    assert second.decision.explanation.replace("a stored answer", "a live call") == first.decision.explanation
    assert [(i.description, i.qty, i.amount) for i in second.invoice.line_items] == \
        [(i.description, i.qty, i.amount) for i in first.invoice.line_items]
    assert first.invoice.llm["cost_usd"] > 0 and second.invoice.llm["cost_usd"] == 0   # paid for once


def test_a_stored_answer_belongs_to_one_file_one_prompt_version_and_one_model(tmp_path):
    """Audit finding 7: replay ignored the prompt and the model an answer was recorded with."""
    letter(make(tmp_path, FakeApi(GOOD), db="a.db"))
    other_model = FakeApi(GOOD)
    r = letter(make(tmp_path, other_model, db="b.db", model="claude-sonnet-5-5"))
    assert other_model.calls == 1 and other_model.bodies[0]["model"] == "claude-sonnet-5-5"       # no stored answer for this model
    assert len(list((tmp_path / "cache").glob("*.json"))) == 2 and r.invoice.llm["model"] == "claude-sonnet-5-5"

    stored = next((tmp_path / "cache").glob("*haiku*.json"))
    saved = json.loads(stored.read_text(encoding="utf-8"))
    saved["template_sha256"] = "0" * 64                                               # as if the prompt had been edited since
    stored.write_text(json.dumps(saved), encoding="utf-8")
    again = FakeApi(GOOD)
    letter(make(tmp_path, again, db="c.db"))
    assert again.calls == 1                                                           # the stale answer was not used


def test_a_stored_answer_is_not_overwritten_unless_asked(tmp_path, master, rules):
    a = assistant(tmp_path, FakeApi(GOOD))
    doc = reader.read_pdf(sample_bytes(LETTER))
    fresh = lambda: extract_invoice(doc, G.BUYER["gstin"], G.BUYER["name"], master.gstin_index, list(master.pos))   # noqa: E731
    a.assist(doc, fresh(), "gaps", rules)
    stored = next((tmp_path / "cache").glob("*.json"))
    before = stored.read_text(encoding="utf-8")
    other = changed(notes="second recording")
    b = assistant(tmp_path, FakeApi(other))
    b.assist(doc, fresh(), "gaps", rules)                                             # a hit: no call, no write
    assert stored.read_text(encoding="utf-8") == before
    b.assist(doc, fresh(), "gaps", rules, force=True)                                 # re-recording, asked for explicitly
    assert json.loads(stored.read_text(encoding="utf-8"))["response"]["notes"] == "second recording"


def test_fixture_mode_replays_recordings_and_never_touches_the_network(tmp_path):
    recorder = make(tmp_path, FakeApi(GOOD), db="rec.db")
    recorder.llm.save_to = "fixtures"                                                 # what scripts/record_llm_fixtures.py does
    letter(recorder)
    assert len(list((tmp_path / "fixtures").glob("extract__*.json"))) == 1 and not (tmp_path / "cache").exists()

    wire = FakeApi(raises=httpx2.ConnectError)
    replay = letter(make(tmp_path, wire, mode="fixture", db="demo.db"))
    assert wire.calls == 0 and replay.verdict == "APPROVE" and replay.invoice.llm["source"] == "fixture"
    assert "a recorded answer" in replay.decision.explanation

    # a recording made with one model is replayed, and labelled, when another model is configured
    other = letter(make(tmp_path, wire, mode="fixture", db="demo2.db", model="claude-sonnet-5-5"))
    assert wire.calls == 0 and other.verdict == "APPROVE" and other.invoice.llm["model"] == "claude-haiku-5-5"


# ---------------------------------------------------------------- a fresh answer on request (the live moment of a demo)

def test_a_fresh_answer_is_a_real_call_even_when_one_is_stored(tmp_path):
    """For showing a live call: with `fresh` set, the API is asked again although an answer is stored. The
    repository's recordings are not touched; only this machine's cache is refreshed."""
    recorder = make(tmp_path, FakeApi(GOOD), db="rec.db")
    recorder.llm.save_to = "fixtures"
    letter(recorder)
    recording = next((tmp_path / "fixtures").glob("extract__*.json"))
    before = recording.read_bytes()

    api = FakeApi(GOOD, usage=(2100, 450))
    live = make(tmp_path, api, db="live.db")
    live.llm.fresh = True
    r = letter(live)
    assert api.calls == 1 and r.verdict == "APPROVE"
    assert r.invoice.llm["source"] == "live" and r.invoice.llm["usage"]["input_tokens"] == 2100 and r.invoice.llm["cost_usd"] > 0
    assert "a live call" in r.decision.explanation
    assert recording.read_bytes() == before                                           # the recording stands
    assert len(list((tmp_path / "cache").glob("extract__*.json"))) == 1               # the fresh answer is kept here

    # without `fresh`, the same set-up replays the recording and calls nothing
    quiet = FakeApi(GOOD)
    again = letter(make(tmp_path, quiet, db="quiet.db"))
    assert quiet.calls == 0 and again.invoice.llm["source"] == "fixture"


def test_when_a_fresh_answer_cannot_be_had_the_stored_one_is_used_and_the_record_says_so(tmp_path):
    """A live demonstration must not depend on the network: if the fresh call fails, the stored answer decides,
    and the record says what happened."""
    recorder = make(tmp_path, FakeApi(GOOD), db="rec.db")
    recorder.llm.save_to = "fixtures"
    letter(recorder)
    down = FakeApi(raises=httpx2.ConnectError)
    live = make(tmp_path, down, db="live.db")
    live.llm.fresh = True
    r = letter(live)
    report = r.invoice.llm
    assert down.calls >= 1 and r.verdict == "APPROVE" and report["source"] == "fixture" and report["cost_usd"] == 0
    assert "could not be reached" in report["note"] and "stored answer was used" in report["note"] and "error" not in report

    # with nothing stored either, it ends as it always does when the API is down: the rules decide alone
    alone = make(tmp_path / "empty", FakeApi(raises=httpx2.ConnectError), db="alone.db")
    alone.llm.fresh = True
    r = letter(alone)
    assert r.verdict == "HOLD" and reasons(r) == RULES_ONLY and "could not be reached" in r.invoice.llm["error"]


def test_fresh_changes_nothing_where_live_calls_are_not_possible(tmp_path):
    recorder = make(tmp_path, FakeApi(GOOD), db="rec.db")
    recorder.llm.save_to = "fixtures"
    letter(recorder)
    for name, settings in (("fixture.db", {"mode": "fixture"}), ("nokey.db", {"mode": "cached-live", "key": ""})):
        wire = FakeApi(GOOD)
        p = make(tmp_path, wire, db=name, **settings)
        p.llm.fresh = True
        r = letter(p)
        assert wire.calls == 0 and r.verdict == "APPROVE" and r.invoice.llm["source"] == "fixture", name


def test_fixture_mode_with_a_key_but_no_recording_stays_offline(tmp_path):
    wire = FakeApi(GOOD)
    r = letter(make(tmp_path, wire, mode="fixture"))
    assert wire.calls == 0 and r.verdict == "HOLD" and reasons(r) == RULES_ONLY
    assert "record_llm_fixtures" in r.invoice.llm["note"]                             # and it says what to do about it


def test_a_new_recording_makes_an_unbooked_file_worth_reading_again(tmp_path, rules, master):
    """The pipeline replays an old answer for a file it has seen. A recording that did not exist then changes that."""
    wire = FakeApi(raises=httpx2.ConnectError)
    store = Store(tmp_path / "one.db")
    def pipeline(mode):
        return Pipeline(store=store, rules=rules, master=master, llm=assistant(tmp_path, wire, mode), today=TODAY, ocr_mode="fixture")
    doc = b"%PDF-1.4 not really a pdf"
    first = pipeline("fixture").run(doc, "x.pdf", "test")
    assert pipeline("fixture").run(doc, "x.pdf", "test").resubmission_of == first.run_id
    assert pipeline("off").run(doc, "x.pdf", "test").resubmission_of is None          # the set-up changed: read afresh


# ---------------------------------------------------------------- configuration: a key alone changes nothing

def test_a_key_alone_never_switches_live_calls_on(monkeypatch):
    """Audit claims table: LLM_MODE=auto went live as soon as a key was in .env."""
    for name in ("LLM_MODE", "ANTHROPIC_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert config.llm_config().mode == "fixture" and not config.llm_config().can_call          # no LLM_MODE: fixture
    for asked, got in (("auto", "fixture"), ("replay", "fixture"), ("fixture", "fixture"), ("cahced-live", "fixture"),
                       ("live", "cached-live"), ("cached-live", "cached-live"), ("CACHED_LIVE", "cached-live"),
                       ("off", "off"), ("", "fixture")):
        monkeypatch.setenv("LLM_MODE", asked)
        assert config.llm_config().mode == got, asked
    monkeypatch.setenv("LLM_MODE", "cached-live")
    cfg = config.llm_config()
    assert cfg.can_call and cfg.model == "claude-haiku-5-5" and cfg.label == "CACHED-LIVE (claude-haiku-5-5)"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert not config.llm_config().can_call and "without a key" in config.llm_config().label
    assert "sk-ant-test" not in repr(config.llm_config())


def test_an_answer_that_starts_with_a_thinking_block_is_read_from_its_text(tmp_path):
    """Current Claude models think by default, so a real answer can carry a thinking block before the text."""
    def api(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        return httpx2.Response(200, headers={"request-id": "req_fake_0002"}, json={
            "id": "msg_fake_0002", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "thinking", "thinking": "Six header values and two lines to copy.", "signature": "c2ln"},
                        {"type": "text", "text": json.dumps(GOOD)}],
            "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 1900, "output_tokens": 640}})
    r = letter(make(tmp_path, api))
    assert r.verdict == "APPROVE" and "line_items" in r.invoice.llm["accepted"]
    assert r.invoice.llm["usage"]["output_tokens"] == 640               # thinking is paid for, and counted


def test_an_effort_level_is_sent_only_when_one_is_set(tmp_path):
    api = FakeApi(GOOD)
    letter(make(tmp_path, api, effort="high"))
    assert api.bodies[0]["output_config"]["effort"] == "high"
    assert config.LlmConfig().effort == "" and config.llm_config().effort == ""        # nothing is set by default


def test_cached_live_without_a_key_behaves_like_fixture_mode_and_says_so(tmp_path):
    wire = FakeApi(GOOD)
    r = letter(make(tmp_path, wire, key=""))
    assert wire.calls == 0 and r.verdict == "HOLD" and "ANTHROPIC_API_KEY" in r.invoice.llm["error"]


def test_models_timeouts_and_caps_come_from_the_environment(monkeypatch):
    for name, value in (("LLM_MODE", "cached-live"), ("ANTHROPIC_API_KEY", "k"), ("LLM_MODEL", "claude-sonnet-5-5"),
                        ("LLM_ESCALATION_MODEL", "claude-opus-5-5"), ("LLM_TIMEOUT_S", "7.5"), ("LLM_MAX_RETRIES", "1"),
                        ("LLM_MAX_COST_PER_INVOICE_USD", "0.02"), ("LLM_DAILY_CAP_USD", "0.5"), ("LLM_EFFORT", "medium")):
        monkeypatch.setenv(name, value)
    cfg = config.llm_config()
    assert (cfg.model, cfg.escalation_model, cfg.timeout_s, cfg.max_retries) == ("claude-sonnet-5-5", "claude-opus-5-5", 7.5, 1)
    assert (cfg.per_invoice_cap_usd, cfg.daily_cap_usd, cfg.effort) == (0.02, 0.5, "medium")
    monkeypatch.setenv("LLM_TIMEOUT_S", "soon")
    assert config.llm_config().timeout_s == 20.0                                       # a bad number falls back, it does not crash
    assert config.llm_config(mode="off").mode == "off"                                # the mode chosen on screen wins


# ---------------------------------------------------------------- cost: bounded before the call, counted after it

def test_cost_is_worked_out_from_real_usage_and_the_price_table(tmp_path):
    api = FakeApi(GOOD, usage=(1900, 420))
    p = make(tmp_path, api)
    r = letter(p)
    assert r.invoice.llm["cost_usd"] == pytest.approx(1900 * 0.10 / 1e6 + 420 * 0.50 / 1e6)      # $0.0004 for the letter on Haiku
    assert p.llm.ledger.spent_today() == pytest.approx(0.0004) and p.llm.ledger.calls_today() == 1
    cfg = config.LlmConfig()
    assert cost_usd(cfg, "claude-sonnet-5-5", 1_000_000, 1_000_000) == 12.0
    assert cost_usd(cfg, "some-future-model", 1_000_000, 0) == 8.0                    # unknown: costed at the dearest listed price
    priced = config.LlmConfig(price_input_per_mtok=1.0, price_output_per_mtok=2.0)
    assert cost_usd(priced, "claude-haiku-5-5", 1_000_000, 1_000_000) == 3.0          # an override in .env wins


def test_a_call_that_could_exceed_the_cap_per_invoice_is_not_made(tmp_path):
    api = FakeApi(GOOD)
    r = letter(make(tmp_path, api, per_invoice_cap_usd=0.0005))
    assert api.calls == 0 and r.verdict == "HOLD" and reasons(r) == RULES_ONLY
    assert "cap of $0.00 per invoice" in r.invoice.llm["error"] or "per invoice" in r.invoice.llm["error"]


def test_the_daily_cap_stops_calls_until_the_next_day(tmp_path):
    day = {"now": datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)}
    ledger = SpendLedger(tmp_path / "spend.db", clock=lambda: day["now"])
    ledger.add("extract", "claude-haiku-5-5", 10, 10, 0.4990, "earlier calls today")
    api = FakeApi(GOOD)
    def run(db):
        p = make(tmp_path, api, db=db, daily_cap_usd=0.50)
        p.llm._ledger = ledger
        return letter(p)
    blocked = run("a.db")
    assert api.calls == 0 and blocked.verdict == "HOLD" and "daily cap is $0.50" in blocked.invoice.llm["error"]
    day["now"] = datetime(2026, 11, 23, 0, 5, tzinfo=timezone.utc)                    # a new day
    assert ledger.spent_today() == 0.0
    allowed = run("b.db")
    assert api.calls == 1 and allowed.verdict == "APPROVE" and ledger.calls_today() == 1


def test_a_paid_call_is_counted_even_when_its_answer_is_unusable(tmp_path):
    p = make(tmp_path, FakeApi(GOOD, stop_reason="max_tokens", usage=(1900, 4096)))
    letter(p)
    assert p.llm.ledger.calls_today() == 1 and p.llm.ledger.spent_today() > 0.002


def test_a_stronger_model_is_tried_once_when_the_first_answer_gives_nothing_usable(tmp_path):
    useless = changed(total={"value": "9.00", "quote": "not on the page at all"}, invoice_number=None, taxable_value=None,
                      igst=None, line_items=[])
    api = FakeApi(script=[{"reply": useless}, {"reply": GOOD}])
    r = letter(make(tmp_path, api, escalation_model="claude-sonnet-5-5"))
    assert [b["model"] for b in api.bodies] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert r.verdict == "APPROVE" and r.invoice.llm["model"] == "claude-sonnet-5-5"
    assert r.invoice.llm["escalated_from"]["model"] == "claude-haiku-5-5"
    assert r.invoice.llm["cost_usd"] == pytest.approx(0.0004 + 1900 * 2 / 1e6 + 420 * 10 / 1e6)     # both calls are in the invoice's cost
    no_need = FakeApi(GOOD)
    letter(make(tmp_path / "b", no_need, escalation_model="claude-sonnet-5-5"))
    assert no_need.calls == 1                                                         # a usable first answer is not second-guessed


# ---------------------------------------------------------------- use 2: rewording the note to the vendor

TEMPLATE = """Subject: Invoice SIS/2327 dated 17-Nov-2026: action needed before payment

Dear Sahyadri Inkwell Supplies accounts team,

We received invoice SIS/2327 dated 17-Nov-2026 for ₹61,024.00 against PO-2026-0229.
Before we can process it we need one point resolved:

  1. Line 1 is priced ₹32.20 above the agreed ₹3,200.00 on PO-2026-0229.

Please reply to this email with a corrected invoice or a clarification, quoting the reference above.

Regards,
Accounts Payable
Nilgiri Precision Components Pvt Ltd"""
KEEP = ["SIS/2327", "PO-2026-0229", "61,024.00"]


def note(body: str, subject="Invoice SIS/2327 dated 17-Nov-2026: one point to resolve before payment") -> dict:
    return {"subject": subject, "body": body}


def test_a_reworded_note_is_used_only_if_no_fact_in_it_changed(tmp_path):
    fine = note("Dear Sahyadri Inkwell Supplies accounts team,\n\nThank you for invoice SIS/2327 dated 17-Nov-2026 for "
                "₹61,024.00 against PO-2026-0229. Before we can process it, one point needs your attention:\n\n"
                "1. Line 1 is priced ₹32.20 above the agreed ₹3,200.00 on PO-2026-0229.\n\nPlease reply with a "
                "corrected invoice or a short clarification.\n\nRegards,\nAccounts Payable\nNilgiri Precision Components Pvt Ltd")
    api = FakeApi(fine)
    report = assistant(tmp_path, api).draft_vendor_note(TEMPLATE, KEEP)
    assert api.calls == 1 and report["accepted"] and report["text"].startswith("Subject: Invoice SIS/2327")
    assert "Thank you for invoice SIS/2327" in report["text"] and api.bodies[0]["output_config"]["format"]["schema"]["required"] == ["subject", "body"]


@pytest.mark.parametrize("body,why", [
    ("Dear team, invoice SIS/2327 dated 17-Nov-2026 for ₹61,024.00 against PO-2026-0229 is priced ₹32.20 above the agreed "
     "₹3,200.00. Please send a corrected invoice by 30-Nov-2026.", "30-nov-2026"),                       # an invented deadline
    ("Dear team, invoice SIS/2327 dated 17-Nov-2026 for ₹61,042.00 against PO-2026-0229 is priced ₹32.20 above the agreed "
     "₹3,200.00.", "61042.00"),                                                                          # two digits swapped in the total
    ("Dear team, your invoice dated 17-Nov-2026 for ₹61,024.00 against PO-2026-0229 is priced ₹32.20 above the agreed "
     "₹3,200.00.", "SIS/2327"),                                                                          # the invoice number dropped
], ids=["adds a date", "changes an amount", "drops the invoice number"])
def test_a_reworded_note_that_changes_a_fact_is_discarded(body, why, tmp_path):
    report = assistant(tmp_path, FakeApi(note(body, subject="About your invoice"))).draft_vendor_note(TEMPLATE, KEEP)
    assert not report["accepted"] and report["text"] == TEMPLATE and why.lower() in report["why"].lower()


def test_the_note_falls_back_to_the_original_when_the_model_cannot_be_used(tmp_path):
    for api, mode in ((FakeApi(status=529), "cached-live"), (FakeApi(note("x")), "off"), (FakeApi(note("x")), "fixture")):
        report = assistant(tmp_path, api, mode).draft_vendor_note(TEMPLATE, KEEP)
        assert not report["accepted"] and report["text"] == TEMPLATE
    assert assistant(tmp_path, FakeApi(note("x"))).draft_vendor_note("", KEEP)["text"] == ""


# ---------------------------------------------------------------- honesty about what ships

def test_each_prompt_version_names_one_exact_prompt():
    """Edit a prompt, its instructions or its schema and this fails until the version is bumped
    (and the fixtures are recorded again), so a stored answer can never be replayed under a prompt it was not made with."""
    pinned = {"extract": ("extract-v2", "db3555443b17c6c474dd79eb09a08bb7bf20772891e88ef3f3ae551a5f1135b2"),
              "vendor_note": ("vendor-note-v1", "2add7545374917fa62935e7100a1ee070f845903a6f7c9c8af797b32ae14ccad")}
    for purpose, (version, digest) in pinned.items():
        assert (PROMPT_VERSIONS[purpose], template_sha256(purpose)) == (version, digest), purpose


def test_no_recording_ships_that_did_not_come_from_the_real_api():
    """This project was built without access to the API. Whatever is in fixtures/llm must have been
    recorded by scripts/record_llm_fixtures.py against the real service, never written by hand."""
    for path in (config.FIXTURES_DIR / "llm").glob("*.json"):
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["model"] in llm_module.PRICES_USD_PER_MTOK and saved["recorded_at"] and saved["sdk"].startswith("anthropic ")
        assert saved["request_id"].startswith("req_") and "fake" not in saved["request_id"]
        assert not saved["request_id"].startswith("req_recorded_"), "made by the tests' stand-in service, not by the API"
        assert saved["usage"]["input_tokens"] > 0 and saved["template_sha256"] in {template_sha256(p) for p in llm_module.PURPOSES}


def test_the_spend_ledger_keeps_days_apart(tmp_path):
    now = {"t": datetime(2026, 11, 22, 23, 59, tzinfo=timezone.utc)}
    ledger = SpendLedger(tmp_path / "s.db", clock=lambda: now["t"])
    ledger.add("extract", "claude-haiku-5-5", 100, 50, 0.01, "a" * 64)
    ledger.add("vendor_note", "claude-haiku-5-5", 100, 50, 0.02, "b" * 64)
    assert ledger.spent_today() == pytest.approx(0.03) and ledger.calls_today() == 2
    now["t"] = datetime(2026, 11, 23, 0, 1, tzinfo=timezone.utc)
    assert ledger.spent_today() == 0.0 and SpendLedger(tmp_path / "s.db", clock=lambda: now["t"]).calls_today() == 0


# ---------------------------------------------------------------- where on the page a model-read value was found

def _printed_inside(words, box) -> str:
    x0, top, x1, bottom = box
    return " ".join(w.text for w in words if w.x0 >= x0 - 1 and w.x1 <= x1 + 1 and w.top >= top - 1 and w.bottom <= bottom + 1)


def test_a_value_read_by_the_model_is_outlined_where_code_found_it_on_the_page(tmp_path):
    """Every value on screen is outlined where it was read. A value Claude read is outlined too, and the place is
    worked out by code from the verified quote: the model is never asked where something is."""
    from invoice_desk import textutil as T
    r = letter(make(tmp_path, FakeApi(GOOD)))
    words = [w for line in reader.read_pdf(sample_bytes(LETTER)).pages[0].lines for w in line.words]
    for name, printed in (("invoice_number", "DQI/26-27/1213"), ("subtotal", "1,95,000.00"), ("igst", "35,100.00"),
                          ("total", "2,30,100.00")):
        source = r.invoice.fields[name].source
        assert source.method == "llm" and source.page == 1 and source.bbox is not None, name
        assert T.quote_form(_printed_inside(words, source.bbox)) == T.quote_form(printed), name
    for item, amount in zip(r.invoice.line_items, ("1,11,000.00", "84,000.00")):
        assert item.source.bbox is not None
        assert T.quote_form(_printed_inside(words, item.source.bbox)) == T.quote_form(amount)


def test_locate_gives_the_words_of_the_value_or_nothing():
    from invoice_desk.llm import locate
    from invoice_desk.reader import DocRead, Line, PageRead, Word

    def line(page, index, top, *texts):
        return Line(page, index, [Word(t, 40 + 60 * n, 95 + 60 * n, top, top + 10) for n, t in enumerate(texts)])

    first = PageRead(1, "text", 595, 842, [line(1, 0, 100, "Dear", "Sir,")])
    second = PageRead(2, "text", 595, 842, [line(2, 0, 100, "The", "amount", "payable", "is"),
                                            line(2, 1, 120, "Rs.", "2,30,100.00", "in", "all.")])
    doc = DocRead("0" * 64, 1, [first, second])
    # the quote wraps over two lines of page 2; the box is the value's own word on the second of them
    assert locate(doc, "amount payable is Rs. 2,30,100.00", "2,30,100.00") == (2, [100.0, 120.0, 155.0, 130.0])
    # with no value given, the box is the part of the quote on its first line
    assert locate(doc, "amount payable is Rs. 2,30,100.00") == (2, [100.0, 100.0, 275.0, 110.0])
    # a value that is not in the quote's words falls back to the quote; a quote that is not on any page has no place
    assert locate(doc, "payable is Rs. 2,30,100.00", "9,99,999.00") == (2, [160.0, 100.0, 275.0, 110.0])
    assert locate(doc, "the amount due is Rs. 5.00", "5.00") == (1, None)


def test_the_screen_outlines_model_read_values_in_their_own_colour(tmp_path):
    from invoice_desk.ui import common as C
    r = letter(make(tmp_path, FakeApi(GOOD)))
    kinds = [box[4] for box in C.source_boxes(r, 1)]
    assert kinds.count("ai") == 6                      # invoice number, taxable value, IGST, total and two line items
    assert "field" in kinds                            # the values the rules read keep their own colour
    assert not any(box[4] == "ai" for box in C.source_boxes(letter(make(tmp_path, FakeApi(GOOD), mode="off", db="o.db")), 1))


def test_what_the_model_read_is_named_in_plain_words(tmp_path):
    """A buyer reads 'invoice number' and 'line items', not the names the code uses for them."""
    r = letter(make(tmp_path, FakeApi(GOOD)))
    stage = next(s for s in r.stages if s.key == "extract")
    note = next(f for f in r.decision.notes if f.code == "LLM_ASSISTED")
    for text in (stage.summary, note.summary):
        assert "invoice number" in text and "line items" in text and "IGST" in text, text
        assert "invoice_number" not in text and "line_items" not in text and "igst" not in text, text
    fields = next(t for t in stage.blocks if t["title"].startswith("Fields"))
    read_by = {row["Field"]: row["Read by"] for row in fields["rows"]}
    assert read_by["Invoice number"] == "Claude, checked by code" and read_by["Invoice date"] == "text layer"
    assist = next(t for t in stage.blocks if t["title"].startswith("AI assist"))
    said = " ".join(str(row["Value"]) for row in assist["rows"])
    assert "invoice number" in said and "invoice_number" not in said and "line_items" not in said
