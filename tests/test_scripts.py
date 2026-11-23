"""The two scripts a person runs before a demo, driven with a fake Claude API so that they
are known to work before a real key is ever spent on them."""
import json

import anthropic
import httpx2
import pytest

from invoice_desk import config
from invoice_desk import samples as sample_lib
from scripts import preflight, record_llm_fixtures

from .test_llm import GOOD


class Service:
    """A fake API that answers by purpose: the extraction answer for the letter, and for a
    vendor note the same note back with its opening line reworded (no fact changed)."""

    def __init__(self, extract=GOOD):
        self.extract, self.calls, self.purposes = extract, 0, []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        body = json.loads(request.content)
        schema = body.get("output_config", {}).get("format", {}).get("schema", {})
        if "fields" in schema.get("properties", {}):
            self.purposes.append("extract")
            text = json.dumps(self.extract)
        elif schema:
            self.purposes.append("vendor_note")
            email = body["messages"][0]["content"].rsplit("<email>", 1)[1].rsplit("</email>", 1)[0].strip()
            subject, _, rest = email.partition("\n")
            text = json.dumps({"subject": subject.removeprefix("Subject:").strip(),
                               "body": rest.strip().replace("We received", "Thank you for sending")})
        else:
            self.purposes.append("ping")
            text = "ready"
        return httpx2.Response(200, headers={"request-id": f"req_recorded_{self.calls:04d}"}, json={
            "id": "msg_x", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1500, "output_tokens": 300}})


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """A scratch fixtures folder, runtime folder and spend ledger, so the repository is not touched."""
    monkeypatch.setattr(config, "FIXTURES_DIR", tmp_path / "fixtures")
    (tmp_path / "fixtures" / "ocr").mkdir(parents=True)
    for recorded in (config.ROOT / "fixtures" / "ocr").glob("*.json"):       # the bundled scans still need their recorded OCR
        (tmp_path / "fixtures" / "ocr" / recorded.name).write_bytes(recorded.read_bytes())
    monkeypatch.setenv("INVOICE_DESK_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "spend.db"))
    monkeypatch.setenv("OCR_MODE", "fixture")
    monkeypatch.setenv("INVOICE_DESK_TODAY", "2026-11-21")
    for name in ("LLM_MODE", "LLM_MODEL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def client(service):
    return anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(service))


def test_recording_needs_a_key_and_the_dry_run_does_not(workspace, capsys, monkeypatch):
    assert record_llm_fixtures.main([]) == 2 and "ANTHROPIC_API_KEY" in capsys.readouterr().out
    assert record_llm_fixtures.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "10_ai_assist_letter_invoice          extract      WOULD ASK" in out and "Nothing was sent" in out
    assert "at most $0.00" in out and list((workspace / "fixtures").glob("llm/*.json")) == []


def test_one_run_records_everything_the_demo_replays_and_a_second_run_costs_nothing(workspace, capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    service = Service()
    monkeypatch.setattr(record_llm_fixtures, "HTTP_CLIENT", client(service))
    assert record_llm_fixtures.main([]) == 0
    out = capsys.readouterr().out
    recorded = sorted(p.name for p in (workspace / "fixtures" / "llm").glob("*.json"))
    assert sum(n.startswith("extract__") for n in recorded) == 1 and sum(n.startswith("vendor_note__") for n in recorded) >= 5
    assert service.calls == len(recorded) and "10_ai_assist_letter_invoice          extract      RECORDED" in out
    assert "APPROVE" in out and "rewording accepted" in out and "sk-ant-test" not in out
    one = json.loads((workspace / "fixtures" / "llm" / recorded[0]).read_text(encoding="utf-8"))
    assert one["request_id"].startswith("req_recorded_") and "sk-ant-test" not in json.dumps(one)

    before = service.calls
    assert record_llm_fixtures.main([]) == 0                         # everything is recorded: nothing is asked again
    assert service.calls == before and "already recorded" in capsys.readouterr().out
    assert record_llm_fixtures.main(["--force", "--only", "10_ai_assist_letter_invoice"]) == 0
    assert service.calls == before + 1                               # asked again only when told to


def test_a_recording_that_does_not_give_the_documented_result_fails_loudly(workspace, capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    bad = json.loads(json.dumps(GOOD))
    bad["fields"] = [f for f in bad["fields"] if f["name"] != "total"]            # the model missed the total
    monkeypatch.setattr(record_llm_fixtures, "HTTP_CLIENT", client(Service(bad)))
    assert record_llm_fixtures.main(["--only", "10_ai_assist_letter_invoice"]) == 1
    out = capsys.readouterr().out
    assert "expected APPROVE with AI assist, got HOLD" in out and "did not reach the documented result" in out


def test_preflight_is_ready_without_a_key_and_says_what_is_missing(workspace, capsys):
    assert preflight.main([]) == 0
    out = capsys.readouterr().out
    assert "READY" in out and "FIXTURE (recorded answers, no network)" in out
    assert "none yet, so sample 10 is held by the rules alone" in out and "python -m scripts.record_llm_fixtures" in out
    assert "[FAIL" not in out and "18 files" in out
    # the guided demo is rehearsed in its own order, and every page of the app is opened
    rehearsal = out.split("Guided demo rehearsal")[1].split("Every page of the app opens")[0]
    assert rehearsal.count("[ok  ] step") == 11 and "step 10 Sent twice: APPROVE" in rehearsal
    assert "audit trail after the demo: 11 entries verified" in rehearsal
    pages = out.split("Every page of the app opens")[1]
    for title in ("Guided demo", "Process invoice", "Review queue", "Dashboard", "Rules and data", "How it works"):
        assert f"[ok  ] {title}" in pages


def test_preflight_says_not_ready_when_the_samples_would_not_give_their_decisions(workspace, capsys, monkeypatch):
    """What a presenter must hear before the call, not during it. Here the business date is
    forced two years on, so the dated checks hold every sample invoice as too old."""
    monkeypatch.setenv("INVOICE_DESK_TODAY", "2028-12-01")
    assert preflight.main([]) == 1
    out = capsys.readouterr().out
    assert "NOT READY" in out and "[FAIL] 01 Standard three-way match" in out and "DATE_TOO_OLD" in out
    assert "Guided demo rehearsal" not in out                     # nothing further is run once something has failed


def test_without_a_forced_date_the_samples_are_processed_on_the_date_they_were_written_for(monkeypatch):
    """So the demo gives the same answers next year: the real clock is not consulted."""
    from datetime import date
    monkeypatch.delenv("INVOICE_DESK_TODAY", raising=False)
    monkeypatch.setattr(config, "today", lambda: date(2031, 1, 1))
    assert sample_lib.business_date() == sample_lib.DEMO_TODAY == date(2026, 11, 21)


def test_the_demo_steps_cover_every_scenario_the_brief_asks_for(manifest):
    from invoice_desk import demo_steps
    ids = [s["sample"] for s in demo_steps.STEPS]
    assert len(demo_steps.STEPS) == 11 and len({s["key"] for s in demo_steps.STEPS}) == 11
    assert all(any(m["id"] == i for m in manifest) for i in ids)
    tags = [demo_steps.sample_of(s, manifest)["tag"] for s in demo_steps.STEPS]
    assert tags[0] == "Standard" and {"Edge case 1", "Edge case 2", "Edge case 3", "Edge case 4, inside",
                                      "Edge case 4, outside", "Fraud control", "AI assist", "Bad input", "Scan"} <= set(tags)
    assert [s["key"] for s in demo_steps.STEPS if s.get("repeat")] == ["again"] and ids[-2] == ids[0]
    assert all(len(s["short"]) <= 18 and s["watch"].endswith(".") for s in demo_steps.STEPS)


def test_preflight_after_recording_shows_the_letter_approved_and_never_spends(workspace, capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    recorder = Service()
    monkeypatch.setattr(record_llm_fixtures, "HTTP_CLIENT", client(recorder))
    assert record_llm_fixtures.main([]) == 0
    capsys.readouterr()
    monkeypatch.setenv("LLM_MODE", "cached-live")                    # even with live calls allowed, preflight makes none
    watcher = Service()
    monkeypatch.setattr(preflight, "HTTP_CLIENT", client(watcher))
    assert preflight.main([]) == 0
    out = capsys.readouterr().out
    assert watcher.calls == 0 and "present: the demo shows it read by Claude and approved" in out
    assert "Invoice written as a letter, read with AI assist: APPROVE" in out

    assert preflight.main(["--llm"]) == 0                            # asked for: one very small call
    out = capsys.readouterr().out
    assert watcher.calls == 1 and watcher.purposes == ["ping"] and "answered 'ready'" in out


def test_preflight_reports_a_bad_key_instead_of_crashing(workspace, capsys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-wrong")

    def unauthorised(request):
        return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    monkeypatch.setattr(preflight, "HTTP_CLIENT", client(unauthorised))
    assert preflight.main(["--llm"]) == 1
    out = capsys.readouterr().out
    assert "AuthenticationError (HTTP 401)" in out and "NOT READY" in out and "sk-ant-wrong" not in out


def test_a_recorded_rewording_is_found_whatever_name_the_file_arrived_under(tmp_path, manifest):
    """The recorder, the command line and the screen process a sample under its attachment
    name. A rewording is stored under the text of the drafted message, and that text does
    not depend on the file name, so a recording made by the script is the one the screen finds."""
    from invoice_desk.llm import LlmAssist
    from invoice_desk.pipeline import Pipeline
    from invoice_desk.store import Store
    from .conftest import LLM_OFF, TODAY
    sample = next(s for s in manifest if s["id"].startswith("05"))
    assert sample_lib.received_name(sample) == "DeccanQuill_DQI-26-27-1193.pdf"
    drafts = []
    for n, name in enumerate((sample_lib.received_name(sample), "05_E2_disguised_duplicate.pdf", "scan0001.pdf")):
        pipeline = Pipeline(store=Store(tmp_path / f"{n}.db"), llm=LlmAssist(LLM_OFF), today=TODAY, ocr_mode="fixture")
        drafts.append(pipeline.run(sample_lib.sample_bytes(sample), name, "test").decision.vendor_draft)
    assert drafts[0] and drafts[0] == drafts[1] == drafts[2]
    assert all(sample_lib.received_name(s).endswith(".pdf") for s in manifest)
