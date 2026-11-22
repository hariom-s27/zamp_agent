import pytest

from invoice_desk import config
from invoice_desk.rules import RulesError, load_rules


def _write(tmp_path, transform):
    text = transform(config.RULES_PATH.read_text(encoding="utf-8"))
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_rules_load_and_carry_a_hash(rules):
    assert rules.version and len(rules.sha256) == 64
    assert rules.reason("DUPLICATE_FILE").action == "REJECT" and rules.reason("PRICE_WITHIN_TOLERANCE").action == "NOTE"


def test_a_finding_with_no_rule_is_held_not_approved(rules):
    assert rules.reason("SOMETHING_NEW").action == "HOLD"


def test_changing_a_threshold_changes_the_hash(tmp_path, rules):
    edited = load_rules(_write(tmp_path, lambda t: t.replace("abs_inr: 500               # ... and the extra", "abs_inr: 750               # ... and the extra")))
    assert edited.tolerances.unit_price.abs_inr == 750 and edited.sha256 != rules.sha256


def test_a_misspelt_key_is_an_error(tmp_path):
    with pytest.raises(RulesError, match="tolerances"):
        load_rules(_write(tmp_path, lambda t: t.replace("rounding_inr:", "rounding_rupees:")))


def test_an_invalid_action_is_an_error(tmp_path):
    with pytest.raises(RulesError, match="DUPLICATE_FILE"):
        load_rules(_write(tmp_path, lambda t: t.replace("DUPLICATE_FILE:         {action: REJECT", "DUPLICATE_FILE:         {action: MAYBE")))


def test_a_negative_tolerance_is_an_error(tmp_path):
    with pytest.raises(RulesError):
        load_rules(_write(tmp_path, lambda t: t.replace("rounding_inr: 1.00", "rounding_inr: -1")))
