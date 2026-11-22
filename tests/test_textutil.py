from datetime import date
from decimal import Decimal

import pytest

from invoice_desk import textutil as T


@pytest.mark.parametrize("text,expected", [
    ("Rs. 1,80,000.00", "180000.00"), ("₹2,12,400/-", "212400"), ("180000", "180000"), ("4,49,934", "449934"),
    ("1,80,000,00", "180000.00"), ("1.80,000.00", "180000.00"), ("l,25,OOO.00", "125000.00"), ("(1,200.00)", "-1200.00"),
])
def test_parse_amount_reads_printed_and_scanned_forms(text, expected):
    assert T.parse_amount(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["9%", "abc", "05-11-2026", "Oil", "", None, "PO-2026-0147"])
def test_parse_amount_refuses_what_is_not_a_number(text):
    assert T.parse_amount(text) is None


@pytest.mark.parametrize("text,expected", [
    ("05-Nov-2026", date(2026, 11, 5)), ("Dated: 5th November, 2026", date(2026, 11, 5)), ("12/11/2026", date(2026, 11, 12)),
    ("2026-11-05", date(2026, 11, 5)), ("O5-11-2O26", date(2026, 11, 5)), ("Nov 5, 2026", date(2026, 11, 5)),
    ("12.11.26", date(2026, 11, 12)),
])
def test_parse_date_is_day_first_and_tolerates_scan_slips(text, expected):
    assert T.parse_date(text)[0] == expected


def test_parse_date_ignores_identifiers():
    assert T.parse_date("PO-2026-0147") is None and T.parse_date("31/02/2026") is None


@pytest.mark.parametrize("amount", ["449934", "147500.50", "100000", "10000000", "5", "21", "110764", "70800", "999999.99"])
def test_amount_in_words_round_trips(amount):
    assert T.words_to_amount(T.amount_to_words(amount)) == Decimal(amount)


def test_amount_in_words_tolerates_one_typo_but_never_invents_a_number():
    assert T.words_to_amount("Rupees Four Lakh Forty Nlne Thousand Nine Hundrcd Thirty Four Only") == Decimal("449934")
    assert T.words_to_amount("Rupees blah only") is None        # 'blah' must not be read as 'lakh'
    assert T.words_to_amount("Payment within 30 days") is None


def test_indian_number_format():
    assert T.fmt_inr("449934") == "₹4,49,934.00" and T.fmt_inr(Decimal("1234567.5")) == "₹12,34,567.50"
    assert T.fmt_inr(999) == "₹999.00" and T.fmt_inr(None) == "-" and T.fmt_inr("-25000") == "-₹25,000.00"


def test_gstin_check_character_and_scan_repair():
    assert T.gstin_is_valid("27AAPFU0939F1ZV")                 # a widely published valid sample
    assert not T.gstin_is_valid("27AAPFU0939F1ZX")
    assert T.make_gstin("27", "AAPFU0939F") == "27AAPFU0939F1ZV"
    assert T.repair_gstin("27AAPFUO939F1ZV") == "27AAPFU0939F1ZV"   # letter O where a digit must be
    found = T.find_gstins("GSTIN: 27AAPFUO939F1ZV")
    assert found and found[0]["gstin"] == "27AAPFU0939F1ZV" and found[0]["repaired"] and found[0]["valid"]


def test_keys_for_comparing_identifiers():
    assert T.invoice_no_key("TOF/26-27/0412") == T.invoice_no_key("tof 26 27 412")
    assert T.invoice_no_key("TOF/26-27/0412") != T.invoice_no_key("TOF/26-27/0413")
    assert T.confusable_key("PO-2026-O147") == T.confusable_key("PO-2026-0147")


def test_name_and_description_similarity():
    assert T.name_similarity("Tungabhadra Office Furnishings Pvt Ltd", "M/s Tungabhadra Office Furnishing Pvt. Ltd.") > 0.9
    assert T.name_similarity("Tungabhadra Office Furnishings Pvt Ltd", "Deccan Quill Infotech LLP") < 0.5
    assert T.desc_similarity("Stretch wrap film roll 23 micron", "Stretch wrap film roll") > 0.85
    assert T.desc_similarity("roll", "Bubble wrap roll 1 m x 100 m") < 0.72   # one shared word is not a match


def test_contains_snippet_is_the_quote_check():
    page = ("With reference to your purchase order PO-2026-0178\ndated 02 November 2026, we have delivered six\n"
            "switches. The total amount\npayable is Rs. 2,30,100.00 (Rupees Two Lakh Thirty Thousand One Hundred Only).")
    assert T.contains_snippet(page, "purchase order PO-2026-0178 dated 02 November 2026")
    assert T.contains_snippet(page, "The total amount payable is Rs 230100.00")        # spacing and separators ignored
    assert T.contains_snippet(page, "  TOTAL AMOUNT PAYABLE IS RS. 2,30,100.00 ")
    assert not T.contains_snippet(page, "purchase order PO-2026-9999 dated 02 December 2026")
    assert not T.contains_snippet(page, "The total amount payable is Rs. 3,20,100.00")  # one digit pair swapped
    assert not T.contains_snippet(page, "The total amount payable is Rs. 2,30,100.50")
    assert not T.contains_snippet(page, "The total amount payable is Rs. 23,010,000")   # decimal point moved
    assert not T.contains_snippet(page, "The total amount due is Rs. 2,30,100.00")      # a word changed
    assert not T.contains_snippet(page, "") and not T.contains_snippet("", "payable") and not T.contains_snippet(page, "Rs.")


def test_quote_form():
    assert T.quote_form("Rs. 2,30,100.00 only") == "rs230100.00only"
    assert T.quote_form("PO\u20132026\u20130178") == "po20260178"
