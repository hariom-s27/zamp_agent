"""Round 4 checkpoint 7: change request step 1. A proposed change is replayed against every
past document on scratch books and saved; nothing about how invoices are decided changes."""
import sqlite3
from decimal import Decimal

import pytest

from invoice_desk import changes, samples
from invoice_desk.store import TABLES
from invoice_desk.llm import LlmAssist
from invoice_desk.pipeline import Pipeline
from invoice_desk.store import Store
from tests.conftest import LLM_OFF, TODAY
from tests.test_llm import FakeApi, assistant

PAIR = {"tolerances.unit_price.pct": Decimal("1.1"), "tolerances.unit_price.abs_inr": Decimal("550")}


def _run(pipeline, sid):
    by_id = {s["id"]: s for s in samples.load_manifest()}
    return pipeline.run(samples.sample_bytes(by_id[sid]), samples.received_name(by_id[sid]), source="cli")


@pytest.fixture()
def three(store, pipeline):
    for sid in ("01_happy_path_furniture", "07_E4a_tax_inclusive_within_tolerance",
                "08_E4b_tax_inclusive_over_tolerance"):
        _run(pipeline, sid)
    return store


def _counts(store):
    with sqlite3.connect(store.path) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def test_no_change_returns_the_base_rules_object(rules):
    assert changes.apply(rules, {}) is rules


def test_applied_change_has_its_own_version_and_hash_and_leaves_the_base_alone(rules):
    trial = changes.apply(rules, PAIR)
    assert trial is not rules and trial.tolerances.unit_price.pct == Decimal("1.1")
    assert rules.tolerances.unit_price.pct == Decimal("1.0") and trial.version == f"{rules.version}+proposal"
    assert trial.sha256 != rules.sha256


def test_key_outside_the_allowed_list_is_refused(rules):
    with pytest.raises(changes.ChangeError, match="cannot be changed"):
        changes.validate(rules, {"matching.vendor_name_match": Decimal("0.5")})


def test_value_outside_bounds_is_refused(rules):
    with pytest.raises(changes.ChangeError, match="The unit price tolerance can be set between 0.0% and 3.0%."):
        changes.validate(rules, {"tolerances.unit_price.pct": Decimal("4")})


def test_too_many_keys_and_no_change_are_refused(rules):
    four = {**PAIR, "tolerances.po_total.pct": Decimal("1"), "duplicates.window_days": Decimal("20")}
    with pytest.raises(changes.ChangeError, match="at most 3"):
        changes.validate(rules, four)
    with pytest.raises(changes.ChangeError, match="already"):
        changes.validate(rules, {"tolerances.unit_price.pct": Decimal("1.0")})


def test_replay_finds_the_one_document_that_flips(three, master, rules):
    result = changes.replay(three, master, rules, PAIR, TODAY, ocr_mode="fixture")
    assert result["count"] == 3 and len(result["differ"]) == 1
    diff = result["differ"][0]
    assert diff["file_name"].endswith(".pdf") and (diff["now"], diff["with_change"]) == ("HOLD", "APPROVE")
    assert diff["gone"] == ["PRICE_VARIANCE"] and diff["new"] == []
    assert result["summary"] == "3 documents replayed. 1 would have been decided differently."
    assert result["direction"] == ("Would no longer need a person: 1. Would newly need a person: 0. "
                                   "Would newly be refused: 0.")
    one = changes.replay(three, master, rules, {"tolerances.unit_price.pct": Decimal("1.1")}, TODAY, ocr_mode="fixture")
    assert one["differ"] == []                                   # with only one of the pair it stays held


def test_replay_leaves_the_real_books_untouched(three, master, rules):
    before, chain = _counts(three), three.chain_head()
    changes.replay(three, master, rules, PAIR, TODAY, ocr_mode="fixture")
    assert _counts(three) == before and three.chain_head() == chain


def test_replay_never_calls_the_api(three, master, rules, tmp_path):
    api = FakeApi({"subject": "x", "body": "y"})
    changes.replay(three, master, rules, PAIR, TODAY, ocr_mode="fixture", llm=assistant(tmp_path, api))
    assert api.calls == 0


def test_replay_is_repeatable(three, master, rules):
    a = changes.replay(three, master, rules, PAIR, TODAY, ocr_mode="fixture")
    b = changes.replay(three, master, rules, PAIR, TODAY, ocr_mode="fixture")
    assert a == b


def test_replay_stops_at_the_limit_and_says_so(three, master, rules):
    small = rules.model_copy(update={"changes": rules.changes.model_copy(update={"replay_max_documents": 2})})
    result = changes.replay(three, master, small, PAIR, TODAY, ocr_mode="fixture")
    assert result["count"] == 2 and result["limit_hit"]


def test_saved_proposal_changes_no_decision(three, master, rules, tmp_path):
    result = changes.replay(three, master, rules, PAIR, TODAY, ocr_mode="fixture")
    with pytest.raises(changes.ChangeError):
        changes.save(three, rules, PAIR, "  ", "reviewers keep approving these", result)
    with pytest.raises(changes.ChangeError):
        changes.save(three, rules, PAIR, "N. Rao", "", result)
    cid = changes.save(three, rules, PAIR, "N. Rao", "reviewers keep approving these", result)
    assert cid == "CR-0001"
    saved = three.change_request(cid)
    assert saved["status"] == "PROPOSED" and saved["proposed_by"] == "N. Rao" and saved["base_rules_sha256"] == rules.sha256
    assert rules.tolerances.unit_price.pct == Decimal("1.0") and changes.in_force(three, rules) is rules
    fresh = Pipeline(store=Store(tmp_path / "fresh.db"), rules=rules, master=master, llm=LlmAssist(LLM_OFF), today=TODAY,
                     ocr_mode="fixture")
    assert _run(fresh, "08_E4b_tax_inclusive_over_tolerance").verdict == "HOLD"   # still decided by rules.yaml
    with pytest.raises(changes.ChangeError, match="already proposed"):
        changes.save(three, rules, PAIR, "S. Iyer", "again", result)


def test_proposal_downloads_as_a_yaml_difference(rules):
    text = changes.yaml_difference(rules, PAIR, "N. Rao", "reviewers keep approving these")
    assert "tolerances:" in text and "pct: 1.1" in text and "abs_inr: 550" in text and "was 1.0" in text


def test_changes_off(three, master, rules):
    off = rules.model_copy(update={"changes": rules.changes.model_copy(update={"enabled": False})})
    with pytest.raises(changes.ChangeError, match="switched off"):
        changes.replay(three, master, off, PAIR, TODAY, ocr_mode="fixture")
