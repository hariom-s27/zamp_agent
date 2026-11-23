"""Round 4 checkpoint 1: the desk's own test data and the new rule sections.

The generator writes only new files, twice the same, and each new sample gives exactly
the reasons its manifest promises. Every file that existed before stays byte-identical.
"""
import json
from email import message_from_bytes, policy
from pathlib import Path

import pytest
import yaml

from invoice_desk import config, samples
from invoice_desk.masterdata import load_master
from invoice_desk.rules import RulesError, load_rules
from scripts import generate_desk_data as GD

ROOT = Path(__file__).resolve().parent.parent
DESK_FILES = ["data/purchase_orders_desk.csv", "data/vendor_terms_desk.csv", "samples/desk/manifest.json", "samples/desk/mail/manifest.json"]


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    out = tmp_path_factory.mktemp("desk")
    GD.write_desk_data(out / "data", out / "samples")
    return out


def _all_files(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_generator_output_matches_the_committed_files(written):
    for rel in _all_files(written):
        assert (ROOT / rel).read_bytes() == (written / rel).read_bytes(), rel


def test_generator_is_byte_identical_on_a_second_run(written, tmp_path):
    GD.write_desk_data(tmp_path / "data", tmp_path / "samples")
    assert _all_files(tmp_path) == _all_files(written)
    for rel in _all_files(written):
        assert (tmp_path / rel).read_bytes() == (written / rel).read_bytes(), rel


def test_generator_writes_only_new_files(written):
    files = _all_files(written)
    for rel in DESK_FILES:
        assert rel in files
    for rel in files:
        assert rel.startswith("samples/desk/") or (rel.startswith("data/") and rel.endswith("_desk.csv")), rel


def test_desk_ids_are_outside_every_range_in_use():
    master = load_master()
    for po in GD.DESK_POS:
        number = int(po[0].rsplit("-", 1)[1])
        assert number >= 1043 and po[0] != "PO-2026-1041" and po[0] != "PO-2026-1042"
    assert {p[0] for p in GD.DESK_POS} <= set(master.pos)
    assert all(po[2] in ("V101", "V102") for po in GD.DESK_POS)


def test_desk_samples_give_exactly_their_expected_reasons(pipeline):
    desk = samples.load_desk_manifest()
    assert [s["id"] for s in desk] == ["d1_clean_bearings", "d2_no_invoice_number", "p1_micro_supplier_due"]
    for sample in desk:
        result = pipeline.run(samples.sample_bytes(sample), samples.received_name(sample), source="cli")
        ok, verdict, reasons = samples.check(sample, result)
        assert ok, (sample["id"], result.verdict, [f.code for f in result.decision.reasons])
    assert desk[0]["expected_verdict"] == "APPROVE" and desk[0]["expected_reasons"] == []
    assert desk[1]["expected_verdict"] == "HOLD" and desk[1]["expected_reasons"] == ["MISSING_INVOICE_NUMBER"]
    assert desk[2]["expected_verdict"] == "APPROVE" and desk[2]["expected_reasons"] == []


def test_bundled_mail_is_utf8_8bit_with_crlf():
    mail_dir = config.SAMPLES_DIR / "desk" / "mail"
    files = sorted(mail_dir.glob("*.eml"))
    assert [f.name for f in files] == [
        "m1_invoice_clean.eml", "m2_invoice_no_number.eml", "m3_payment_question.eml",
        "m4_bank_change_lookalike.eml", "m5_statement.eml", "m6_other.eml", "m7_same_invoice_again.eml"]
    ids = set()
    for path in files:
        data = path.read_bytes()
        assert b"\n" not in data.replace(b"\r\n", b""), path.name          # CRLF only
        assert b"quoted-printable" not in data.lower(), path.name
        msg = message_from_bytes(data, policy=policy.default)
        body = msg.get_body(preferencelist=("plain",))
        assert body["Content-Transfer-Encoding"] == "8bit", path.name
        assert "utf-8" in body.get_content_type() + str(body.get_param("charset")), path.name
        assert msg["Message-ID"] and msg["Date"] and msg["From"].addresses[0].domain.endswith(".example")
        ids.add(msg["Message-ID"])
    assert len(ids) == 7


def test_mail_manifest_names_each_message_and_attachment():
    entries = json.loads((config.SAMPLES_DIR / "desk" / "mail" / "manifest.json").read_text(encoding="utf-8"))
    assert [e["file"] for e in entries] == [f"desk/mail/{n}" for n in (
        "m1_invoice_clean.eml", "m2_invoice_no_number.eml", "m3_payment_question.eml",
        "m4_bank_change_lookalike.eml", "m5_statement.eml", "m6_other.eml", "m7_same_invoice_again.eml")]
    assert [e["expected_kind"] for e in entries] == [
        "invoice", "invoice", "status_query", "bank_change_request", "statement", "other", "invoice"]
    m4 = message_from_bytes((config.SAMPLES_DIR / "desk/mail/m4_bank_change_lookalike.eml").read_bytes(),
                            policy=policy.default)
    assert m4["From"].addresses[0].domain == "tungabhadra-furnishing.example"
    m7 = message_from_bytes((config.SAMPLES_DIR / "desk/mail/m7_same_invoice_again.eml").read_bytes(),
                            policy=policy.default)
    m1 = message_from_bytes((config.SAMPLES_DIR / "desk/mail/m1_invoice_clean.eml").read_bytes(),
                            policy=policy.default)
    pdf = lambda m: next(p for p in m.iter_attachments()).get_content()     # noqa: E731
    assert pdf(m1) == pdf(m7) == (config.SAMPLES_DIR / "desk" / "d1_clean_bearings.pdf").read_bytes()
    assert m1["Message-ID"] != m7["Message-ID"]


# ---------------------------------------------------------------- rules: every feature switched on

def test_inbox_rules_are_loaded_and_switched_on(rules):
    assert rules.inbox.enabled is True
    assert rules.inbox.max_attachments > 0 and rules.inbox.answer_due_days >= 0
    assert set(rules.inbox.owners) == {"status_query", "bank_change_request", "statement", "credit_note",
                                       "not_an_invoice", "other"}
    assert rules.inbox.owners["bank_change_request"] == "Vendor master team"
    assert "gmail.com" in rules.inbox.shared_mail_domains
    assert GD.AP_EMAIL == rules.company.ap_email          # the bundled mail is addressed to this AP inbox


def _rules_without(tmp_path, mutate) -> Path:
    data = yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_inbox_section_is_required(tmp_path):
    with pytest.raises(RulesError, match="inbox"):
        load_rules(_rules_without(tmp_path, lambda d: d.pop("inbox")))


def test_every_inbox_key_is_required(tmp_path):
    with pytest.raises(RulesError, match="answer_due_days"):
        load_rules(_rules_without(tmp_path, lambda d: d["inbox"].pop("answer_due_days")))


def test_inbox_owner_must_have_a_clock(tmp_path):
    def mutate(d):
        d["inbox"]["owners"]["other"] = "Somebody new"
    with pytest.raises(RulesError, match="Somebody new"):
        load_rules(_rules_without(tmp_path, mutate))
