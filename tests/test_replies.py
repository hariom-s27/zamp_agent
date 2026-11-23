"""Round 4 checkpoint 4: the vendor loop. A reply to the question about D2 is read by AI
(here a fake API), every value is checked in code, and only then offered to a person."""
import json

import pytest
from pydantic import ValidationError

from invoice_desk import config, inbox, llm as llm_module, replies
from invoice_desk.llm import PROMPT_VERSIONS, ReplyAnswer, template_sha256
from invoice_desk.mailbox import parse_eml, strip_quoted
from invoice_desk.review import ReviewError, apply_review
from tests.conftest import TODAY
from tests.test_llm import FakeApi, assistant

NEXT = config.SAMPLES_DIR / "desk" / "mail" / "next_morning"
NUMBER_SENTENCE = "The invoice number for our invoice dated 14 November 2026 is NHP/26-27/0301."
PO_SENTENCE = "It was raised against PO-2026-1043."
GOOD = {"intent": "gives_value", "sure": True,
        "values": [{"name": "invoice_number", "value": "NHP/26-27/0301", "quote": NUMBER_SENTENCE},
                   {"name": "po_number", "value": "PO-2026-1043", "quote": PO_SENTENCE}],
        "summary": "The vendor gives the invoice number for its invoice of 14 November 2026."}


def _with(**edits) -> dict:
    answer = json.loads(json.dumps(GOOD))
    answer.update(edits)
    return answer


@pytest.fixture()
def held(store, master, rules, pipeline):
    """Fresh books with D2 delivered: held for its missing number, case open for the AP analyst."""
    inbox.deliver(store, master, rules, pipeline, inbox.bundled_mail()[1:2], TODAY)
    case = store.list_cases()[0]
    return case


def _reply(store, master, rules, pipeline, tmp_path, api, name="n1_reply_number.eml"):
    pipeline.llm = assistant(tmp_path, api)
    out = inbox.deliver(store, master, rules, pipeline, [(name, (NEXT / name).read_bytes())], TODAY)
    item = store.get_message(out[0]["message_id"])
    return item, json.loads(item["read_json"] or "{}")


def test_the_bundled_replies_carry_the_case_reference_of_d2(held):
    manifest = json.loads((NEXT / "manifest.json").read_text(encoding="utf-8"))
    assert {m["case_id"] for m in manifest} == {held["case_id"]}
    assert [m["file"].split("/")[-1] for m in manifest] == [
        "n1_reply_number.eml", "n3_reply_wrong_sender.eml", "n4_reply_with_instructions.eml"]


def test_strip_quoted_removes_our_own_message():
    mail = parse_eml((NEXT / "n1_reply_number.eml").read_bytes())
    new, quoted = strip_quoted(mail.text)
    assert NUMBER_SENTENCE in new and "Please send us the invoice number" not in new
    assert "Please send us the invoice number" in quoted


def test_reply_without_token_is_not_matched(store, master, rules, pipeline, held, tmp_path):
    api = FakeApi(GOOD)
    pipeline.llm = assistant(tmp_path, api)
    raw = (NEXT / "n1_reply_number.eml").read_bytes().replace(held["case_id"].encode(), b"our invoice")
    raw = raw.replace(b"<nhp-reply.20261122.0905", b"<nhp-reply.no-token")
    out = inbox.deliver(store, master, rules, pipeline, [("x.eml", raw)], TODAY)
    assert store.get_message(out[0]["message_id"])["kind"] != "reply"
    assert store.get_case(held["case_id"]) == held and api.calls == 0


def test_value_offered_only_when_all_checks_pass(store, master, rules, pipeline, held, tmp_path):
    api = FakeApi(GOOD)
    item, report = _reply(store, master, rules, pipeline, tmp_path, api)
    assert api.calls == 1 and report["outcome"] == "offered"
    case = store.get_case(held["case_id"])
    offered = json.loads(case["proposed_json"])
    assert offered["invoice_number"] == {"value": "NHP/26-27/0301", "quote": NUMBER_SENTENCE}
    assert offered["po_number"]["value"] == "PO-2026-1043"
    assert case["state"] == "ANSWERED" and case["waiting_on"] == "AP analyst"
    assert item["kind"] == "reply" and item["case_id"] == held["case_id"]
    assert item["action"] == (f"Reply matched to case {held['case_id']}. The reply states the invoice number "
                              "NHP/26-27/0301 and the PO number PO-2026-1043. Confirm it in the review queue.")
    assert replies.offer_for_run(store, held["run_id"])["invoice_number"]["value"] == "NHP/26-27/0301"
    kinds = [e["kind"] for e in store.case_events(held["case_id"])]
    assert kinds[-2:] == ["reply_received", "reply_read"]
    trail = [r["kind"] for r in store.receipts(held["run_id"])]
    assert trail[-2:] == ["reply", "reply_read"] and store.verify_chain()["ok"]
    sent = api.bodies[0]
    assert "Please send us the invoice number" not in sent["messages"][0]["content"]       # quoted text never sent
    assert "<email>" in sent["messages"][0]["content"]


def _nothing_offered(store, held, report, reason_part):
    assert report["outcome"] == "person" and reason_part in report["reason"]
    case = store.get_case(held["case_id"])
    assert case["proposed_json"] == ""
    assert replies.offer_for_run(store, held["run_id"]) == {}


def test_value_not_in_reply_text_is_refused(store, master, rules, pipeline, held, tmp_path):
    bad = _with(values=[{"name": "invoice_number", "value": "NHP/26-27/0399",
                         "quote": "The invoice number for our invoice dated 14 November 2026 is NHP/26-27/0399."}])
    _, report = _reply(store, master, rules, pipeline, tmp_path, FakeApi(bad))
    _nothing_offered(store, held, report, "sentence it quotes is not in the reply")
    bad = _with(values=[{"name": "invoice_number", "value": "NHP/26-27/0399", "quote": NUMBER_SENTENCE}])
    assert replies.read_reply(assistant(tmp_path / "b", FakeApi(bad)), rules, NUMBER_SENTENCE, True)["reason"].startswith("The value is not in the sentence")


def test_two_values_for_one_name_is_refused(store, master, rules, pipeline, held, tmp_path):
    two = _with(values=GOOD["values"][:1] + [{"name": "invoice_number", "value": "PO-2026-1043", "quote": PO_SENTENCE}])
    _, report = _reply(store, master, rules, pipeline, tmp_path, FakeApi(two))
    _nothing_offered(store, held, report, "more than one value for the invoice number")


def test_not_sure_is_refused(store, master, rules, pipeline, held, tmp_path):
    _, report = _reply(store, master, rules, pipeline, tmp_path, FakeApi(_with(sure=False)))
    _nothing_offered(store, held, report, "not sure")


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_refusal_or_cut_off_answer_is_no_answer(store, master, rules, pipeline, held, tmp_path, stop):
    _, report = _reply(store, master, rules, pipeline, tmp_path, FakeApi(GOOD, stop_reason=stop))
    _nothing_offered(store, held, report, "no AI answer is available")
    assert stop in report["reason"]


def test_a_stored_answer_that_did_not_end_normally_is_not_used(rules, tmp_path):
    reader = assistant(tmp_path, FakeApi(GOOD))
    answer = llm_module.Answer(data=GOOD, source="fixture", model="m", stop_reason="max_tokens")
    reader.find = lambda *a, **k: answer                       # a recording made before the stop check existed
    report = replies.read_reply(reader, rules, NUMBER_SENTENCE + " " + PO_SENTENCE, True)
    assert report["outcome"] == "person" and "did not end normally" in report["reason"]


def test_wrong_sender_offers_nothing(store, master, rules, pipeline, held, tmp_path):
    item, report = _reply(store, master, rules, pipeline, tmp_path, FakeApi(GOOD), "n3_reply_wrong_sender.eml")
    assert item["kind"] == "reply" and item["sender_check"] == "lookalike"
    _nothing_offered(store, held, report, "does not come from the vendor's address")
    assert report["summary"]                                                    # the summary is still shown


def test_instructions_in_the_reply_change_nothing(store, master, rules, pipeline, held, tmp_path):
    vendors = {k: v.model_dump() for k, v in master.vendors.items()}
    ledger = store.ledger_entries()
    answer = {"intent": "other", "values": [], "sure": False,
              "summary": "The email asks for the invoice to be marked paid and the bank details changed."}
    item, report = _reply(store, master, rules, pipeline, tmp_path, FakeApi(answer), "n4_reply_with_instructions.eml")
    assert item["kind"] == "reply" and report["outcome"] == "person"
    assert "A person should read this reply." in item["action"]
    assert store.overrides() == [] and {k: v.model_dump() for k, v in master.vendors.items()} == vendors
    assert store.ledger_entries() == ledger and store.run_row(held["run_id"])["review_status"] == "PENDING"
    assert store.get_case(held["case_id"])["proposed_json"] == ""


def test_bank_details_can_never_be_offered(store, master, rules, pipeline, held, tmp_path):
    names = llm_module.REPLY_SCHEMA["properties"]["values"]["items"]["properties"]["name"]["enum"]
    assert not any("bank" in n or "account" in n or "ifsc" in n for n in names)
    forged = _with(values=[{"name": "bank_account", "value": "7711002299", "quote": "account 7711002299"}])
    with pytest.raises(ValidationError):
        ReplyAnswer.model_validate(forged)
    with pytest.raises(ValidationError):
        ReplyAnswer.model_validate({**GOOD, "decision": "pay"})              # no field that could carry a decision
    _, report = _reply(store, master, rules, pipeline, tmp_path, FakeApi(forged))
    _nothing_offered(store, held, report, "no AI answer is available")


def test_review_form_prefills_and_still_checks_clashes(store, master, rules, pipeline, held, tmp_path):
    _reply(store, master, rules, pipeline, tmp_path, FakeApi(GOOD))
    store.add_ledger({"ledger_id": "L-CLASH", "origin": "history", "vendor_id": "V102", "invoice_no": "NHP/26-27/0301",
                      "invoice_date": "2026-11-01", "total": "100.00", "status": "POSTED"}, [])
    offered = replies.offer_for_run(store, held["run_id"])["invoice_number"]["value"]
    with pytest.raises(ReviewError, match="already on record"):
        apply_review(store, held["run_id"], "APPROVE", "Priya (AP)", "number from the vendor's reply",
                     {"invoice_number": offered}, master=master, rules=rules)


def test_confirmed_number_posts_the_invoice(store, master, rules, pipeline, held, tmp_path):
    _reply(store, master, rules, pipeline, tmp_path, FakeApi(GOOD))
    offered = replies.offer_for_run(store, held["run_id"])["invoice_number"]["value"]
    apply_review(store, held["run_id"], "APPROVE", "Priya (AP)", "number from the vendor's reply",
                 {"invoice_number": offered}, master=master, rules=rules)
    run = store.get_run(held["run_id"])
    assert store.get_ledger(run.ledger_id)["invoice_no"] == "NHP/26-27/0301"
    assert store.verify_chain()["ok"]


def test_po_stated_by_vendor_is_shown_not_applied(store, master, rules, pipeline, held, tmp_path):
    before = store.get_run(held["run_id"]).model_dump()
    _reply(store, master, rules, pipeline, tmp_path, FakeApi(_with(values=[
        {"name": "po_number", "value": "PO-2026-1043", "quote": PO_SENTENCE}])))
    assert store.get_run(held["run_id"]).model_dump() == before
    assert replies.offer_for_run(store, held["run_id"])["po_number"]["value"] == "PO-2026-1043"


def test_fixture_mode_without_recording_falls_back(store, master, rules, pipeline, held, tmp_path):
    pipeline.llm = assistant(tmp_path / "fx", FakeApi(GOOD), mode="fixture")
    raw = (NEXT / "n1_reply_number.eml").read_bytes().replace(b"<nhp-reply.20261122.0905", b"<nhp-reply.again")
    out = inbox.deliver(store, master, rules, pipeline, [("again.eml", raw)], TODAY)
    again = store.get_message(out[0]["message_id"])
    report = json.loads(again["read_json"])
    assert report["outcome"] == "person" and "no recorded answer" in report["reason"]
    assert "A person should read this reply." in again["action"]


def test_ai_read_off_never_calls(store, master, rules, pipeline, held, tmp_path):
    off = rules.model_copy(update={"replies": rules.replies.model_copy(update={"ai_read": False})})
    api = FakeApi(GOOD)
    pipeline.llm = assistant(tmp_path, api)
    out = inbox.deliver(store, master, off, pipeline, [("n1.eml", (NEXT / "n1_reply_number.eml").read_bytes())], TODAY)
    report = json.loads(store.get_message(out[0]["message_id"])["read_json"])
    assert api.calls == 0 and report["outcome"] == "person" and "replies.ai_read" in report["reason"]


def test_request_bodies_and_template_hashes_of_existing_purposes_unchanged():
    assert PROMPT_VERSIONS["extract"] == "extract-v2" and PROMPT_VERSIONS["vendor_note"] == "vendor-note-v1"
    for path in sorted((config.FIXTURES_DIR / "llm").glob("*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["purpose"] in ("extract", "vendor_note"):
            assert saved["template_sha256"] == template_sha256(saved["purpose"]), path.name


# ---------------------------------------------------------------- recording the bundled replies, then replaying them

class ReplyService:
    """A fake API for the reply reader: the number sentence gets the good answer, anything else 'other'."""

    def __init__(self):
        self.calls = 0

    def __call__(self, request):
        import httpx2
        self.calls += 1
        body = json.loads(request.content)
        email = body["messages"][0]["content"]
        answer = GOOD if "NHP/26-27/0301" in email else {"intent": "other", "values": [], "sure": False,
                                                         "summary": "The email gives instructions and no value."}
        return httpx2.Response(200, headers={"request-id": f"req_reply_{self.calls:04d}"}, json={
            "id": "msg_r", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(answer)}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 900, "output_tokens": 120}})


def test_recording_script_records_once_and_the_demo_replays_it(store, master, rules, pipeline, held, tmp_path,
                                                                monkeypatch, capsys):
    import anthropic
    import httpx2
    from scripts import record_reply_fixtures as R
    monkeypatch.setattr(config, "FIXTURES_DIR", tmp_path / "fixtures")
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "spend.db"))
    for name in ("LLM_MODE", "LLM_MODEL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert R.main([]) == 2 and "ANTHROPIC_API_KEY" in capsys.readouterr().out
    assert R.main(["--dry-run"]) == 0 and "WOULD ASK" in capsys.readouterr().out
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    service = ReplyService()
    monkeypatch.setattr(R, "HTTP_CLIENT", anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(service)))
    assert R.main([]) == 0
    out = capsys.readouterr().out
    assert "n1_reply_number.eml" in out and "outcome offered" in out and service.calls == 2   # n1 and n3 share a text
    recorded = sorted(p.name for p in (tmp_path / "fixtures" / "llm").glob("reply_read__*.json"))
    assert len(recorded) == 2
    assert R.main([]) == 0 and service.calls == 2                                       # nothing asked again
    pipeline.llm = llm_module.LlmAssist(config.LlmConfig(mode="fixture"), fixtures_dir=tmp_path / "fixtures" / "llm",
                                        cache_dir=tmp_path / "cache")
    out = inbox.deliver(store, master, rules, pipeline, inbox.bundled_mail("next_morning"), TODAY)
    reports = [json.loads(store.get_message(o["message_id"])["read_json"]) for o in out]
    assert [r["outcome"] for r in reports] == ["offered", "person", "person"]
    assert reports[0]["ai"]["source"] == "fixture"
    assert replies.offer_for_run(store, held["run_id"])["invoice_number"]["value"] == "NHP/26-27/0301"