from invoice_desk.vendor import check_vendor

from .helpers import codes, make_invoice


def test_vendor_is_identified_by_gstin_and_bank_matches(master):
    vendor, findings, _, summary = check_vendor(make_invoice(vendor_name="Tungabhadra Office Furnishings Pvt Ltd"), master)
    assert vendor.vendor_id == "V001" and summary["method"] == "GSTIN" and findings == []


def test_a_name_variant_is_a_note_not_a_block(master, rules):
    _, findings, _, _ = check_vendor(make_invoice(vendor_name="Tungabhadra Office Furnishing Pvt. Ltd."), master)
    assert codes(findings) == {"VENDOR_NAME_VARIANT"} and rules.reason("VENDOR_NAME_VARIANT").action == "NOTE"


def test_a_name_that_contradicts_the_gstin_is_held(master):
    _, findings, _, _ = check_vendor(make_invoice(vendor_name="Deccan Quill Infotech LLP"), master)
    assert "VENDOR_GSTIN_MISMATCH" in codes(findings)


def test_changed_bank_account_is_held_with_a_call_back_question(master):
    vendor, findings, _, _ = check_vendor(make_invoice(bank_account="770031245566", ifsc="CHUB0002290"), master)
    found = [f for f in findings if f.code == "BANK_DETAILS_CHANGED"]
    assert found and vendor.phone_on_file in found[0].question and "not one from this invoice" in found[0].question


def test_an_account_verified_on_review_is_accepted_later(master):
    inv = make_invoice(bank_account="770031245566", ifsc="CHUB0002290")
    _, findings, _, _ = check_vendor(inv, master, {"V001": ["770031245566"]})
    assert "BANK_DETAILS_CHANGED" not in codes(findings)


def test_no_bank_details_on_the_invoice_is_not_a_change(master):
    _, findings, _, _ = check_vendor(make_invoice(bank_account=None, ifsc=None), master)
    assert findings == []


def test_unknown_vendor_is_not_approved(master):
    vendor, findings, _, _ = check_vendor(make_invoice(vendor_gstin="33AAACX1234L1Z5", vendor_name="Coromandel Unknown Traders"), master)
    assert vendor is None and codes(findings) == {"VENDOR_NOT_APPROVED"}


def test_known_name_with_a_different_gstin_is_held(master):
    vendor, findings, _, _ = check_vendor(make_invoice(vendor_gstin="33AAACX1234L1Z5", vendor_name="Deccan Quill Infotech LLP"), master)
    assert vendor.vendor_id == "V002" and "VENDOR_GSTIN_MISMATCH" in codes(findings)


def test_vendor_matched_by_name_only_is_held(master):
    vendor, findings, _, _ = check_vendor(make_invoice(vendor_gstin=None, vendor_name="Deccan Quill Infotech LLP", bank_account=None), master)
    assert vendor.vendor_id == "V002" and codes(findings) == {"VENDOR_MATCHED_BY_NAME"}


def test_nothing_to_identify_the_vendor(master):
    vendor, findings, _, _ = check_vendor(make_invoice(vendor_gstin=None, vendor_name=None), master)
    assert vendor is None and codes(findings) == {"MISSING_VENDOR_ID"}


def test_blocked_vendor_is_rejected(master, rules):
    vendor, findings, _, _ = check_vendor(make_invoice(vendor="29XXXXX", vendor_gstin=master.vendors["V007"].gstin,
                                                       bank_account=None), master)
    assert vendor.vendor_id == "V007" and "VENDOR_BLOCKED" in codes(findings)
    assert rules.reason("VENDOR_BLOCKED").action == "REJECT"


def test_a_misread_ifsc_on_the_right_account_is_noted_not_raised(master):
    _, findings, blocks, _ = check_vendor(make_invoice(ifsc="HMBL0O00045"), master)
    assert findings == []
    bank = next(b for b in blocks if b["title"].startswith("Bank details"))
    assert "the master's IFSC is the one used" in bank["rows"][-1]["IFSC"]
