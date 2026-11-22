"""decide() is a pure function of the findings and the rules. These tests pin that down."""
import itertools
import random
import re

import pytest

from invoice_desk import config
from invoice_desk.decide import classify, decide, worst_action
from invoice_desk.models import Finding
from invoice_desk.rules import SEVERITY

RANK = {"APPROVE": 0, "HOLD": 2, "REJECT": 3}
CTX = {"vendor_name": "Tungabhadra Office Furnishings Pvt Ltd", "invoice_number": "TOF/26-27/0412",
       "invoice_date": "2026-11-05", "total": "449934.00", "payment_terms_days": 30,
       "company_name": "Nilgiri Precision Components Pvt Ltd", "is_invoice": True,
       "po": {"po_number": "PO-2026-0147", "lines_matched": 3, "lines_total": 3, "billed_after": "381300.00",
              "po_total": "381300.00", "remaining_after": "0.00"}}


def F(code, **kw):
    return Finding(code=code, stage="test", summary=kw.pop("summary", f"{code} happened."), **kw)


def test_no_findings_means_approve_with_a_full_explanation(rules):
    d = decide([], rules, CTX)
    assert d.verdict == "APPROVE" and d.reasons == [] and d.vendor_draft is None
    assert d.headline == "APPROVE: pay ₹4,49,934.00 to Tungabhadra Office Furnishings Pvt Ltd"
    assert "Invoice TOF/26-27/0412 dated 05-Nov-2026 matches PO-2026-0147" in d.explanation   # case of the number kept
    assert "fully billed" in d.explanation and "due 05-Dec-2026" in d.next_action


def test_the_strictest_action_wins(rules):
    d = decide([F("PRICE_WITHIN_TOLERANCE"), F("BANK_DETAILS_CHANGED"), F("DUPLICATE_FILE")], rules, CTX)
    assert d.verdict == "REJECT"
    assert [f.code for f in d.reasons] == ["DUPLICATE_FILE"]
    assert [f.code for f in d.also_noted] == ["BANK_DETAILS_CHANGED"] and [f.code for f in d.notes] == ["PRICE_WITHIN_TOLERANCE"]


def test_hold_names_owners_and_questions(rules):
    d = decide([F("PRICE_VARIANCE", question="Accept the price?"), F("BANK_DETAILS_CHANGED", question="Call the vendor.")], rules, CTX)
    assert d.verdict == "HOLD" and d.headline == "HOLD: 2 points need a person"
    assert d.owners == [rules.reason("PRICE_VARIANCE").owner, rules.reason("BANK_DETAILS_CHANGED").owner]
    assert d.questions == ["Accept the price?", "Call the vendor."]
    assert "(1) PRICE_VARIANCE happened. (2) BANK_DETAILS_CHANGED happened." in d.explanation


def test_notes_never_change_the_verdict(rules):
    notes = [F(code) for code, r in rules.reasons.items() if r.action == "NOTE"]
    assert len(notes) >= 5
    assert decide(notes, rules, CTX).verdict == "APPROVE"
    assert decide(notes + [F("PRICE_VARIANCE")], rules, CTX).verdict == "HOLD"
    assert len(decide(notes, rules, CTX).notes) == len(notes)        # and every note is carried into the record


def test_an_unknown_code_holds_for_the_ap_analyst(rules):
    d = decide([F("SOMETHING_NEW")], rules, CTX)
    assert d.verdict == "HOLD" and d.owners == ["AP analyst"]


def test_same_input_same_output(rules):
    findings = [F("QTY_OVER_PO", question="q", vendor_issue="line 1 bills too many"), F("PRICE_BELOW_PO")]
    first = decide(findings, rules, CTX).model_dump()
    for _ in range(5):
        assert decide(findings, rules, CTX).model_dump() == first


def test_decide_does_not_change_its_inputs(rules):
    findings = [F("QTY_OVER_PO")]
    before = [f.model_dump() for f in findings]
    decide(findings, rules, CTX)
    assert [f.model_dump() for f in findings] == before and findings[0].action == ""


def test_adding_a_finding_never_makes_the_verdict_more_lenient(rules):
    """Monotonic: more problems can only keep or tighten the outcome. 400 random cases."""
    rng = random.Random(7)
    all_codes = sorted(rules.reasons)
    for _ in range(400):
        base = [F(c) for c in rng.sample(all_codes, rng.randint(0, 5))]
        extra = F(rng.choice(all_codes))
        assert RANK[decide(base + [extra], rules, CTX).verdict] >= RANK[decide(base, rules, CTX).verdict]


def test_order_of_findings_does_not_change_the_verdict(rules):
    findings = [F("PRICE_VARIANCE"), F("DUPLICATE_CONTENT"), F("READ_CORRECTED"), F("PO_INFERRED")]
    verdicts = {decide(list(p), rules, CTX).verdict for p in itertools.permutations(findings)}
    assert verdicts == {"REJECT"}


@pytest.mark.parametrize("total,verdict", [("999999.99", "APPROVE"), ("1000000.00", "APPROVE"), ("1000000.01", "HOLD")])
def test_auto_approval_ceiling_from_both_sides(total, verdict, rules):
    d = decide([], rules, {**CTX, "total": total})
    assert d.verdict == verdict
    if verdict == "HOLD":
        assert [f.code for f in d.reasons] == ["ABOVE_AUTO_APPROVE_LIMIT"] and d.owners == ["Finance controller"]


def test_the_ceiling_is_not_piled_onto_an_invoice_that_is_already_blocked(rules):
    d = decide([F("PRICE_VARIANCE")], rules, {**CTX, "total": "5000000.00"})
    assert [f.code for f in d.reasons] == ["PRICE_VARIANCE"]


def test_policy_lives_in_the_rules_file_not_in_code(rules):
    """Change one action in the rules and the same findings give a different verdict."""
    strict = rules.model_copy(deep=True)
    strict.reasons["BANK_DETAILS_CHANGED"].action = "REJECT"
    lenient = rules.model_copy(deep=True)
    lenient.reasons["PRICE_VARIANCE"].action = "NOTE"
    assert decide([F("BANK_DETAILS_CHANGED")], rules, CTX).verdict == "HOLD"
    assert decide([F("BANK_DETAILS_CHANGED")], strict, CTX).verdict == "REJECT"
    assert decide([F("PRICE_VARIANCE")], lenient, CTX).verdict == "APPROVE"
    higher = rules.model_copy(deep=True)
    higher.auto_approve.max_invoice_total_inr = 5_000_000
    assert decide([], higher, {**CTX, "total": "2500000.00"}).verdict == "APPROVE"


def test_vendor_note_is_drafted_only_when_the_vendor_has_to_act(rules):
    internal = decide([F("PO_INFERRED", question="Which PO?")], rules, CTX)
    external = decide([F("PRICE_VARIANCE", vendor_issue="line 1 is priced ₹300.00 above the agreed ₹4,500.00")], rules, CTX)
    assert internal.vendor_draft is None
    draft = external.vendor_draft
    assert draft.startswith("Subject: Invoice TOF/26-27/0412 dated 05-Nov-2026: action needed before payment")
    assert "1. Line 1 is priced ₹300.00 above the agreed ₹4,500.00." in draft and draft.endswith("Nilgiri Precision Components Pvt Ltd")


def test_reject_says_nothing_is_paid(rules):
    d = decide([F("DUPLICATE_FILE", vendor_issue="this invoice was already received")], rules, CTX)
    assert d.verdict == "REJECT" and "Nothing was posted and nothing will be paid" in d.explanation
    assert "We have not booked it" in d.vendor_draft


def test_worst_action_and_classify(rules):
    assert worst_action([], rules) == "" and worst_action([F("READ_CORRECTED")], rules) == "NOTE"
    assert worst_action([F("READ_CORRECTED"), F("PO_CLOSED")], rules) == "HOLD"
    c = classify([F("VENDOR_BLOCKED")], rules)[0]
    assert (c.action, c.owner) == ("REJECT", rules.reason("VENDOR_BLOCKED").owner) and c.title


def test_every_code_the_program_can_raise_is_in_the_rules_file_and_none_is_dead(rules):
    emitted: set[str] = set()
    for path in (config.ROOT / "invoice_desk").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        emitted |= set(re.findall(r'code="([A-Z_]+)"', text)) | set(re.findall(r'ReaderError\(\s*"([A-Z_]+)"', text))
    assert emitted - set(rules.reasons) == set()      # a typo would otherwise fall back to a silent default
    assert set(rules.reasons) - emitted == set()      # and no rule is left that nothing can trigger
    assert all(r.action in SEVERITY and r.title and (r.owner or r.action == "NOTE") for r in rules.reasons.values())


def test_decide_touches_nothing_outside_its_arguments():
    source = (config.ROOT / "invoice_desk" / "decide.py").read_text(encoding="utf-8")
    for banned in ("import os", "open(", "sqlite", "requests", "datetime.now", "date.today", "random", "config."):
        assert banned not in source, banned
