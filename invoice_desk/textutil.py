"""Text, money, date and identifier helpers.

Pure functions with no I/O, so every rule built on them can be unit-tested directly.
Money is always Decimal (idea carried over from the ef-recon project: exact, audit-grade
arithmetic, no float drift at a tolerance boundary).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from rapidfuzz import fuzz

TWO = Decimal("0.01")
ZERO = Decimal("0")


# --------------------------------------------------------------------------- money

def to_decimal(value: Any) -> Optional[Decimal]:
    """Decimal from Decimal, int, str or float (floats go through str). None on junk."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def q2(value: Any) -> Decimal:
    """Round half up to two places."""
    d = to_decimal(value)
    if d is None:
        raise ValueError(f"not a number: {value!r}")
    return d.quantize(TWO, rounding=ROUND_HALF_UP)


def _group_indian(digits: str) -> str:
    """'449934' -> '4,49,934' (thousands, then lakhs and crores in pairs)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def fmt_inr(value: Any, symbol: str = "₹", decimals: int = 2) -> str:
    """Indian-format rupees: 449934 -> '₹4,49,934.00'. '-' for a missing value."""
    d = to_decimal(value)
    if d is None:
        return "-"
    sign = "-" if d < 0 else ""
    d = abs(d).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    whole, _, frac = f"{d:f}".partition(".")
    out = _group_indian(whole)
    if decimals:
        out += "." + (frac or "").ljust(decimals, "0")
    return f"{sign}{symbol}{out}"


def fmt_money(value: Any, currency: Optional[str] = "INR") -> str:
    """Rupees in Indian format; any other currency as 'USD 4,850.00' so that a foreign
    amount is never shown with a rupee sign."""
    if not currency or currency == "INR":
        return fmt_inr(value)
    d = to_decimal(value)
    return "-" if d is None else f"{currency} {d:,.2f}"


def fmt_num(value: Any) -> str:
    """Quantity-style number: no trailing zeros, '-' when missing."""
    d = to_decimal(value)
    if d is None:
        return "-"
    if d == d.to_integral_value():
        return f"{int(d)}"
    return f"{d.normalize():f}"


_OCR_DIGIT_FIX = str.maketrans("OoIl|", "00111")
_AMOUNT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def parse_amount(text: Any) -> Optional[Decimal]:
    """Parse one printed amount. Handles 'Rs. 1,80,000.00', '₹2,12,400/-', '180000',
    '(1,200.00)' and the common scan slips: O for 0, l for 1, a comma for the decimal
    point ('1,80,000,00'), a stray extra dot ('1.80,000.00'). Returns None when the text
    is not a single clean number: a guess here would be worse than a blank."""
    if text is None:
        return None
    s = unicodedata.normalize("NFKC", str(text)).strip()
    if not s:
        return None
    s = s.replace("₹", "").replace("`", "")
    s = re.sub(r"^[$€£]\s?(?=\d)", "", s)                  # a foreign amount is still read; the currency check holds it
    s = re.sub(r"(?i)\b(?:rs|inr|usd|eur|gbp)\b\.?", "", s)
    s = s.replace("/-", "").replace("/=", "")
    s = s.strip(" :=|*")
    negative = (s.startswith("(") and s.endswith(")")) or s.startswith("-")
    s = s.strip("()-+ ")
    if not s:
        return None
    if re.fullmatch(r"[\dOoIl|,.\s]+", s) and any(c.isdigit() for c in s):
        s = s.translate(_OCR_DIGIT_FIX)  # 'l,25,OOO.00' -> '1,25,000.00'; needs a real digit
    s = s.replace(" ", "")
    if not re.fullmatch(r"[\d,.]+", s) or not s[0].isdigit():
        return None
    if s.count(".") > 1:  # keep only the last dot as the decimal point
        head, _, tail = s.rpartition(".")
        s = head.replace(".", "") + "." + tail
    if "." not in s and re.search(r",\d{2}$", s) and re.search(r"\d,\d{2,3},\d{2}$|^\d{1,3},\d{2}$", s):
        head, _, tail = s.rpartition(",")  # '1,80,000,00' -> 180000.00
        if len(head.replace(",", "")) >= 3:
            s = head + "." + tail
    s = s.replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", s):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if negative else d


def looks_numeric(token: str) -> bool:
    """True if a single word parses as a number (after the same clean-up as parse_amount)."""
    return parse_amount(token) is not None


# --------------------------------------------------------------------------- dates

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_PATTERNS = [
    # 05-Nov-2026, 5 November 2026, 05/Nov/26, 5th Nov, 2026
    (re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[\s\-/.,]+([A-Za-z]{3,9})[\s\-/.,]+(\d{4}|\d{2})(?!\d)"), "dMy"),
    # November 5, 2026
    (re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)"), "Mdy"),
    # 2026-11-05
    (re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"), "ymd"),
    # 05-11-2026, 5/11/26, 05.11.2026 (day first, the Indian convention)
    (re.compile(r"(?<!\d)(\d{1,2})[\-/.](\d{1,2})[\-/.](\d{4}|\d{2})(?!\d)"), "dmy"),
]


def _ocr_digits(text: str) -> str:
    """Fix O/l/I that sit next to digits or date separators ('O5-11-2O26' -> '05-11-2026')."""
    out = list(text)
    for i, ch in enumerate(out):
        if ch in "OoIl|":
            prev = out[i - 1] if i else " "
            nxt = out[i + 1] if i + 1 < len(out) else " "
            if (prev.isdigit() or prev in "-/.") and (nxt.isdigit() or nxt in "-/. "):
                out[i] = "0" if ch in "Oo" else "1"
            elif nxt.isdigit() and (prev in " -/.:" or i == 0):
                out[i] = "0" if ch in "Oo" else "1"
    return "".join(out)


def _mk_date(y: int, m: int, d: int) -> Optional[date]:
    if y < 100:
        y += 2000
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(text: Any) -> Optional[tuple[date, str]]:
    """First date found in the text as (date, matched text). Day-first for numeric forms."""
    if not text:
        return None
    s = _ocr_digits(unicodedata.normalize("NFKC", str(text)))
    best: Optional[tuple[int, date, str]] = None
    for pattern, kind in _DATE_PATTERNS:
        for m in pattern.finditer(s):
            g = m.groups()
            found: Optional[date] = None
            if kind == "dMy":
                mon = _MONTHS.get(g[1][:4].lower()) or _MONTHS.get(g[1][:3].lower())
                found = _mk_date(int(g[2]), mon, int(g[0])) if mon else None
            elif kind == "Mdy":
                mon = _MONTHS.get(g[0][:4].lower()) or _MONTHS.get(g[0][:3].lower())
                found = _mk_date(int(g[2]), mon, int(g[1])) if mon else None
            elif kind == "ymd":
                found = _mk_date(int(g[0]), int(g[1]), int(g[2]))
            else:
                found = _mk_date(int(g[2]), int(g[1]), int(g[0]))
            if found and (best is None or m.start() < best[0]):
                best = (m.start(), found, m.group(0))
    return (best[1], best[2]) if best else None


def fmt_date(value: Any) -> str:
    """ISO string or date -> '05-Nov-2026'."""
    if not value:
        return "-"
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return value.strftime("%d-%b-%Y")


# --------------------------------------------------------------------------- text

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}


def unify(text: str) -> str:
    """NFKC, one dash and quote style, single spaces."""
    s = unicodedata.normalize("NFKC", text or "").translate(_DASHES).translate(_QUOTES)
    return " ".join(s.split())


def norm(text: str) -> str:
    """Comparison form: unified, casefolded."""
    return unify(text).casefold()


def letters_digits(text: str) -> str:
    """Lowercase letters and digits only: 'Invoice No.' -> 'invoiceno'."""
    return re.sub(r"[^a-z0-9]", "", norm(text))


def invoice_no_key(text: Optional[str]) -> str:
    """Key for comparing invoice numbers: 'TOF/26-27/0412' == 'tof 26 27 412'.
    Upper-case alphanumeric runs, leading zeros dropped from digit runs."""
    if not text:
        return ""
    runs = re.findall(r"[A-Za-z]+|\d+", unify(text).upper())
    return "".join(r.lstrip("0") or "0" if r.isdigit() else r for r in runs)


_CONFUSABLE = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1",
                             "Z": "2", "S": "5", "B": "8", "G": "6"})


def confusable_key(text: Optional[str]) -> str:
    """Key that treats OCR look-alikes as equal: 'PO-2026-O147' == 'PO-2026-0147'."""
    if not text:
        return ""
    return re.sub(r"[^A-Z0-9]", "", unify(text).upper()).translate(_CONFUSABLE)


def edit_distance(a: str, b: str) -> int:
    from rapidfuzz.distance import Levenshtein
    return Levenshtein.distance(a, b)


_COMPANY_STOP = {"pvt", "private", "ltd", "limited", "llp", "co", "company", "ms", "the", "p"}


def company_key(name: str) -> str:
    """'M/s Tungabhadra Office Furnishings Pvt. Ltd.' -> 'tungabhadra office furnishings'."""
    s = norm(name).replace("&", " and ").replace("m/s", " ")
    tokens = [t for t in re.findall(r"[a-z0-9]+", s) if t not in _COMPANY_STOP]
    return " ".join(tokens)


def name_similarity(a: str, b: str) -> float:
    """0..1 similarity of two company names, ignoring legal suffixes and word order."""
    ka, kb = company_key(a), company_key(b)
    if not ka or not kb:
        return 0.0
    return max(fuzz.token_sort_ratio(ka, kb), fuzz.ratio(ka, kb)) / 100.0


def desc_key(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", norm(text)))


def desc_similarity(a: str, b: str) -> float:
    """0..1 similarity of two item descriptions. Blends 'same words' with 'same order and
    length' so a one-word description cannot fully match a long one."""
    ka, kb = desc_key(a), desc_key(b)
    if not ka or not kb:
        return 0.0
    return (0.5 * fuzz.token_set_ratio(ka, kb) + 0.5 * fuzz.token_sort_ratio(ka, kb)) / 100.0


def quote_form(text: str) -> str:
    """The form in which a quote is compared with the page: letters, digits and decimal
    points only. Case, spacing, line breaks, punctuation and thousands separators are
    ignored; every letter and every digit counts."""
    s = norm(text)
    s = re.sub(r"(?<=\d),(?=\d)", "", s)                 # 2,30,100.00 -> 230100.00
    s = re.sub(r"(?<=\d)\.(?=\d)", "\x00", s)            # keep a decimal point between digits
    return re.sub(r"[^a-z0-9\x00]", "", s).replace("\x00", ".")


def contains_snippet(haystack: str, needle: str, min_chars: int = 4) -> bool:
    """Is `needle` really on the page? It has to be there letter for letter and digit for
    digit (see quote_form). There is deliberately no fuzzy matching: a quote that is
    'almost' on the page, one digit different, is exactly what this check exists to stop."""
    h, n = quote_form(haystack), quote_form(needle)
    return len(n) >= min_chars and n in h


# --------------------------------------------------------------------------- number words

_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
          "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
          "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_WORD_VALUES: dict[str, int] = {w: i for i, w in enumerate(_UNITS)}
_WORD_VALUES.update({w: i * 10 for i, w in enumerate(_TENS) if w})
_WORD_VALUES["fourty"] = 40
_SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100000, "lakhs": 100000, "lac": 100000,
           "lacs": 100000, "crore": 10000000, "crores": 10000000}
_IGNORABLE = {"rupees", "rupee", "rs", "inr", "only", "and", "indian", "amount", "in", "words",
              "total", "net", "payable", "chargeable", "invoice", "value", "grand", "of"}
_NUMBER_VOCAB = list(_WORD_VALUES) + list(_SCALES)


def _below_thousand(n: int) -> list[str]:
    out: list[str] = []
    if n >= 100:
        out += [_UNITS[n // 100], "hundred"]
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10])
        n %= 10
    if n or not out:
        if n:
            out.append(_UNITS[n])
    return out


def amount_to_words(value: Any) -> str:
    """449934.50 -> 'Rupees Four Lakh Forty Nine Thousand Nine Hundred Thirty Four and Fifty Paise Only'."""
    d = q2(value)
    rupees, paise = int(d), int((d - int(d)) * 100)
    words: list[str] = []
    for scale_value, scale_name in ((10000000, "crore"), (100000, "lakh"), (1000, "thousand")):
        chunk, rupees = divmod(rupees, scale_value)
        if chunk:
            words += _below_thousand(chunk) + [scale_name]
    if rupees or not words:
        words += _below_thousand(rupees) or ["zero"]
    text = "Rupees " + " ".join(w.capitalize() for w in words)
    if paise:
        text += " and " + " ".join(w.capitalize() for w in _below_thousand(paise)) + " Paise"
    return text + " Only"


def _closest_word(token: str, vocabulary: Iterable[str], unique: bool = True) -> Optional[str]:
    """The single vocabulary word within one typo of `token` (two for long words).
    Edit distance, not a similarity ratio: 'nlne' is one slip from 'nine', while 'blah'
    is two from 'lakh' and must not be read as a number."""
    limit = 1 if len(token) <= 5 else 2
    hits = [w for w in vocabulary if abs(len(w) - len(token)) <= limit and edit_distance(token, w) <= limit]
    if not hits or (unique and len(hits) > 1):
        return None
    return hits[0]


def _words_to_int(tokens: Iterable[str]) -> Optional[int]:
    total = current = 0
    seen = False
    for raw in tokens:
        tok = raw
        if tok not in _WORD_VALUES and tok not in _SCALES:
            if tok in _IGNORABLE:
                continue
            fixed = _closest_word(tok, _NUMBER_VOCAB)
            if fixed is None:
                if _closest_word(tok, _IGNORABLE, unique=False) is not None:
                    continue
                return None  # an unknown word: do not pretend to understand the sentence
            tok = fixed
        seen = True
        if tok in _WORD_VALUES:
            current += _WORD_VALUES[tok]
        elif tok == "hundred":
            current = (current or 1) * 100
        else:
            total += (current or 1) * _SCALES[tok]
            current = 0
    return total + current if seen else None


def words_to_amount(text: Any) -> Optional[Decimal]:
    """'Rupees Four Lakh ... Only' -> Decimal. None if any word cannot be understood."""
    if not text:
        return None
    s = re.sub(r"[^a-z\s]", " ", norm(str(text)))
    tokens = s.split()
    if not tokens:
        return None
    paise = 0
    if "paise" in tokens or "paisa" in tokens:
        idx = tokens.index("paise") if "paise" in tokens else tokens.index("paisa")
        head = tokens[:idx]
        split_at = max((i for i, t in enumerate(head) if t == "and"), default=None)
        if split_at is None:
            return None
        paise_value = _words_to_int(head[split_at + 1:])
        if paise_value is None or paise_value > 99:
            return None
        paise = paise_value
        tokens = head[:split_at]
    rupees = _words_to_int(tokens)
    if rupees is None:
        return None
    return Decimal(rupees) + Decimal(paise) / 100


# --------------------------------------------------------------------------- GSTIN and bank

_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"(?<![A-Z0-9])\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9](?![A-Z0-9])")
IFSC_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Z0-9])")

STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman & Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh",
}


def gstin_check_char(first14: str) -> str:
    """Check character of a GSTIN (base-36 weighted sum, weights 1,2,1,2...)."""
    total = 0
    for i, ch in enumerate(first14):
        product = _GST_CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _GST_CHARS[(36 - total % 36) % 36]


def make_gstin(state_code: str, pan: str, entity: str = "1") -> str:
    base = f"{state_code}{pan}{entity}Z"
    return base + gstin_check_char(base)


def gstin_is_valid(gstin: Optional[str]) -> bool:
    if not gstin or not GSTIN_RE.fullmatch(gstin):
        return False
    return gstin_check_char(gstin[:14]) == gstin[14]


_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1", "Z": "2", "S": "5",
             "B": "8", "G": "6", "T": "7"}
_TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G"}
_DIGIT_POS = {0, 1, 7, 8, 9, 10}
_LETTER_POS = {2, 3, 4, 5, 6, 11}


def repair_gstin(token: str) -> Optional[str]:
    """Positional repair of a scanned GSTIN: where the format demands a digit, an 'O'
    becomes '0'; where it demands a letter, a '5' becomes 'S'; position 14 must be 'Z'.
    Returns the repaired text if it then has the GSTIN shape, else None."""
    t = re.sub(r"[^A-Za-z0-9|]", "", token or "").upper()
    if len(t) != 15:
        return None
    out = []
    for i, ch in enumerate(t):
        if i in _DIGIT_POS and not ch.isdigit():
            ch = _TO_DIGIT.get(ch, ch)
        elif i in _LETTER_POS and not ch.isalpha():
            ch = _TO_LETTER.get(ch, ch)
        elif i == 13 and ch in "27":
            ch = "Z"
        out.append(ch)
    fixed = "".join(out)
    return fixed if GSTIN_RE.fullmatch(fixed) else None


def find_gstins(text: str) -> list[dict]:
    """All GSTIN-shaped strings in the text, in reading order, each with
    {'gstin', 'raw', 'valid', 'repaired'}. Tries positional repair on 15-character tokens."""
    found: list[dict] = []
    seen: set[str] = set()
    upper = unify(text).upper()
    for m in re.finditer(r"(?<![A-Z0-9])[A-Z0-9|]{15}(?![A-Z0-9])", upper):
        raw = m.group(0)
        candidate = raw if GSTIN_RE.fullmatch(raw) else repair_gstin(raw)
        if not candidate or candidate in seen:
            continue
        if candidate != raw and not gstin_is_valid(candidate):
            continue  # a repair is only believed when the checksum then passes
        seen.add(candidate)
        found.append({"gstin": candidate, "raw": raw, "valid": gstin_is_valid(candidate),
                      "repaired": candidate != raw})
    return found


def state_name(code: Optional[str]) -> str:
    if not code:
        return "-"
    return STATE_CODES.get(code, f"state {code}")


def digits_only(text: Optional[str]) -> str:
    return re.sub(r"\D", "", text or "")


def plural(n: int, word: str, many: str | None = None) -> str:
    """'1 invoice', '3 invoices', '1 reply', '2 replies': a count with the right noun, never 'invoice(s)'."""
    return f"{n} {word if n == 1 else (many or word + 's')}"
