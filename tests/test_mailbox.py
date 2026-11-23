"""Round 4 checkpoint 2: reading one email (invoice_desk/mailbox.py)."""
import hashlib
from email.message import EmailMessage
from email import policy as email_policy

import pytest

from invoice_desk import config
from invoice_desk import mailbox
from invoice_desk.masterdata import MasterData, Vendor

MAIL = config.SAMPLES_DIR / "desk" / "mail"


def _eml(sender="accounts@kaveribearings.example", subject="Hello", text="Hi there.\n", html=None,
         attachments=(), message_id="<t1@kaveribearings.example>") -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = sender, "ap@nilgiri-precision.example", subject, message_id
    m["Date"] = "Sat, 21 Nov 2026 08:05:00 +0530"
    if text is not None:
        m.set_content(text, cte="8bit")
    if html is not None:
        if text is None:
            m.set_content(html, subtype="html", cte="8bit")
        else:
            m.add_alternative(html, subtype="html", cte="8bit")
    for name, data, mime in attachments:
        main, sub = mime.split("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return m.as_bytes(policy=email_policy.SMTP)


def test_parse_eml_reads_headers_text_and_attachments():
    data = (MAIL / "m1_invoice_clean.eml").read_bytes()
    mail = mailbox.parse_eml(data)
    pdf = (config.SAMPLES_DIR / "desk" / "d1_clean_bearings.pdf").read_bytes()
    assert mail.sha256 == hashlib.sha256(data).hexdigest()
    assert mail.sender == "accounts@kaveribearings.example"
    assert mail.subject == "Invoice KBD/26-27/1208 against PO-2026-1046"
    assert mail.message_id == "<kbd-1208.20261121.0805@kaveribearings.example>"
    assert mail.sent_at.startswith("2026-11-21T08:05:00")
    assert "Please find attached our invoice KBD/26-27/1208" in mail.text
    assert len(mail.attachments) == 1
    att = mail.attachments[0]
    assert att.name == "Kaveri_KBD-26-27-1208.pdf" and att.mime == "application/pdf" and att.is_pdf
    assert att.sha256 == hashlib.sha256(pdf).hexdigest() and att.content == pdf and att.size == len(pdf)


def test_html_only_email_is_read_as_text_without_tags():
    mail = mailbox.parse_eml(_eml(text=None, html="<html><body><p>Please pay <b>soon</b>.</p>"
                                                  "<script>alert(1)</script><br>Thanks &amp; regards</body></html>"))
    assert "<" not in mail.text and "alert" not in mail.text
    assert "Please pay soon." in mail.text and "Thanks & regards" in mail.text


def test_plain_text_is_preferred_over_html():
    mail = mailbox.parse_eml(_eml(text="Plain words.\n", html="<p>Html words</p>"))
    assert mail.text.strip() == "Plain words."


def test_a_broken_file_is_still_registered_with_what_could_be_read():
    mail = mailbox.parse_eml(b"\x00\x01 not an email at all")
    assert mail.sha256 and mail.attachments == [] and mail.sender == ""


def test_strip_quoted_text_keeps_the_new_part_only():
    text = ("The invoice number is NHP/26-27/0301.\n\nRegards,\nBilling\n\n"
            "On Sat, 21 Nov 2026 at 10:00, ap@nilgiri-precision.example wrote:\n"
            "> Please tell us the invoice number.\n> CASE-1A2B3C4D\n")
    new, quoted = mailbox.strip_quoted(text)
    assert "NHP/26-27/0301" in new and "Please tell us" not in new and "CASE-1A2B3C4D" not in new
    assert "Please tell us" in quoted


def test_strip_quoted_handles_outlook_original_message_and_angle_lines():
    new, quoted = mailbox.strip_quoted("Yes, PO-2026-1043.\n\n-----Original Message-----\nFrom: AP\nSubject: x\n")
    assert new.strip() == "Yes, PO-2026-1043." and "From: AP" in quoted
    new, _ = mailbox.strip_quoted("Answer here.\n> old line\n>> older\n")
    assert new.strip() == "Answer here."
    new, _ = mailbox.strip_quoted("From: ap@nilgiri-precision.example\nSent: Monday\nSubject: q\n\nold text")
    assert new.strip() == ""


def test_case_token_found_in_subject_or_text_in_any_case():
    assert mailbox.case_tokens("Re: [CASE-1A2B3C4D] Invoice number needed") == ["CASE-1A2B3C4D"]
    assert mailbox.case_tokens("about case-1a2b3c4d and CASE-0000FFFF") == ["CASE-1A2B3C4D", "CASE-0000FFFF"]
    assert mailbox.case_tokens("CASE-1A2B3C4 too short, CASE-1A2B3C4DE too long, CASE-XYZ12345 not hex") == []
    assert mailbox.case_tokens("CASE-1A2B3C4D CASE-1A2B3C4D") == ["CASE-1A2B3C4D"]


def _master(*vendors) -> MasterData:
    return MasterData(vendors={v.vendor_id: v for v in vendors}, pos={})


V_A = Vendor(vendor_id="V001", legal_name="Tungabhadra Office Furnishings Pvt Ltd", gstin="x", state_code="29",
             status="APPROVED", email="accounts@tungabhadra-furnishings.example")
V_G = Vendor(vendor_id="V009", legal_name="Small Shop", gstin="y", state_code="29", status="APPROVED",
             email="smallshop.billing@gmail.com")


def test_sender_on_file_same_domain_lookalike_unknown(rules):
    master = _master(V_A)
    on_file = mailbox.check_sender("Accounts@Tungabhadra-Furnishings.example", master, rules)
    assert (on_file.result, on_file.vendor_id) == ("on_file", "V001")
    assert on_file.detail == "Sender matches the address on file for Tungabhadra Office Furnishings Pvt Ltd."
    same = mailbox.check_sender("ravi@tungabhadra-furnishings.example", master, rules)
    assert (same.result, same.vendor_id) == ("same_domain", "V001")
    assert same.detail == ("Sender's domain matches Tungabhadra Office Furnishings Pvt Ltd, "
                           "but this address is new to us.")
    look = mailbox.check_sender("accounts@tungabhadra-furnishing.example", master, rules)
    assert (look.result, look.vendor_id) == ("lookalike", "V001")
    assert look.detail == ("Careful: the sender's domain tungabhadra-furnishing.example looks like "
                           "tungabhadra-furnishings.example and is not the same.")
    unknown = mailbox.check_sender("greetings@festive-offers.example", master, rules)
    assert (unknown.result, unknown.vendor_id) == ("unknown", "")
    assert unknown.detail == "Sender is not a known vendor address."


def test_lookalike_limit_comes_from_the_rules(rules):
    far = rules.model_copy(update={"inbox": rules.inbox.model_copy(update={"lookalike_max_edits": 1})})
    master = _master(V_A)
    assert mailbox.check_sender("a@tungabhadra-furnishin.example", master, rules).result == "lookalike"
    assert mailbox.check_sender("a@tungabhadra-furnishin.example", master, far).result == "unknown"


def test_shared_domain_needs_exact_address(rules):
    master = _master(V_G)
    assert mailbox.check_sender("smallshop.billing@gmail.com", master, rules).result == "on_file"
    other = mailbox.check_sender("someone.else@gmail.com", master, rules)
    assert (other.result, other.vendor_id) == ("unknown", "")
    assert mailbox.check_sender("x@gmai1.com", master, rules).result == "unknown"     # no look-alike on shared mail


def test_bundled_senders(rules, master):
    results = {}
    for path in sorted(MAIL.glob("*.eml")):
        mail = mailbox.parse_eml(path.read_bytes())
        results[path.name] = mailbox.check_sender(mail.sender, master, rules).result
    assert results == {"m1_invoice_clean.eml": "on_file", "m2_invoice_no_number.eml": "on_file",
                       "m3_payment_question.eml": "on_file", "m4_bank_change_lookalike.eml": "lookalike",
                       "m5_statement.eml": "on_file", "m6_other.eml": "unknown",
                       "m7_same_invoice_again.eml": "on_file"}


@pytest.mark.parametrize("phrase,text,found", [
    ("new bank account", "Our NEW  bank\naccount is below", True),
    ("change of bank", "no change of plans", False),
])
def test_phrase_match_ignores_case_and_spacing(phrase, text, found):
    assert bool(mailbox.find_phrases(text, [phrase])) is found
