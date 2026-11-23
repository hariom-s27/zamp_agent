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


# ---------------------------------------------------------------- round 3: cases, msme, value_model

def _yaml_edit(tmp_path, change):
    """rules.yaml as data, changed by `change`, written to a scratch file."""
    import yaml
    data = yaml.safe_load(config.RULES_PATH.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _drop(*keys):
    def change(data):
        node = data
        for key in keys[:-1]:
            node = node[key]
        del node[keys[-1]]
    return change


def test_the_round_3_keys_load_from_rules_yaml(rules):
    assert rules.version == "2026.11-3" and rules.company.ap_email == "ap@nilgiri-precision.example"
    assert rules.cases.enabled is True and rules.cases.auto_resume == ["GOODS_NOT_RECEIVED"]
    assert rules.cases.reply_due_days["Stores"] == 2 and rules.cases.escalate_to["Stores"] == "Procurement"
    assert rules.msme.applies_to == ["micro", "small"] and rules.msme.max_days_with_agreement == 45
    assert rules.msme.days_without_agreement == 15 and rules.msme.warn_days_before == 7
    assert str(rules.msme.bank_rate_pct) == "5.75" and rules.msme.interest_multiple == 3
    assert rules.value_model.invoices_per_month == 600 and rules.value_model.desk_cost_per_month_inr == 0


def test_every_owner_in_the_reasons_has_a_reply_time_and_an_escalation(tmp_path, rules):
    owners = {r.owner for r in rules.reasons.values() if r.owner}
    assert owners <= set(rules.cases.reply_due_days) and owners <= set(rules.cases.escalate_to)
    assert "vendor" in rules.cases.reply_due_days and "vendor" in rules.cases.escalate_to
    for keys in (("cases", "reply_due_days", "Stores"), ("cases", "escalate_to", "Stores"),
                 ("cases", "reply_due_days", "vendor"), ("cases", "escalate_to", "vendor")):
        with pytest.raises(RulesError, match=f"{keys[1]}.*{keys[2]}"):
            load_rules(_yaml_edit(tmp_path, _drop(*keys)))
    for code in ("NO_SUCH_CODE", "VENDOR_BLOCKED", "LLM_ASSISTED"):        # unknown, a REJECT, a NOTE
        with pytest.raises(RulesError, match=f"auto_resume.*{code}"):
            load_rules(_yaml_edit(tmp_path, lambda d, c=code: d["cases"].update(auto_resume=[c])))
    with pytest.raises(RulesError, match="escalate_to.*Nobody"):            # a case escalates only to a role with a clock
        load_rules(_yaml_edit(tmp_path, lambda d: d["cases"]["escalate_to"].update(Stores="Nobody")))
    assert load_rules(_yaml_edit(tmp_path, lambda d: d["cases"].update(auto_resume=[]))).cases.auto_resume == []


def test_a_missing_cases_msme_or_value_key_stops_the_app_by_name(tmp_path):
    for keys in (("cases",), ("msme",), ("value_model",), ("company", "ap_email"), ("cases", "enabled"),
                 ("cases", "auto_resume"), ("msme", "applies_to"), ("msme", "bank_rate_pct"),
                 ("msme", "interest_multiple"), ("value_model", "touchless_share_pct"),
                 ("value_model", "desk_cost_per_month_inr")):
        with pytest.raises(RulesError, match=".".join(keys)):
            load_rules(_yaml_edit(tmp_path, _drop(*keys)))
    with pytest.raises(RulesError, match="value_model"):                   # a misspelt assumption is not ignored
        load_rules(_yaml_edit(tmp_path, lambda d: d["value_model"].update(invoices_a_month=600)))
