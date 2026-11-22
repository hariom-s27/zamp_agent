"""Stage 3, Extract: fields and line items from the lines the reader produced.

No vendor templates. Three general tools do the work:
  1. a vocabulary of the labels invoices use ("Invoice No.", "Bill No", "Inv #", ...);
  2. page geometry: a value sits right after its label, in the next cell on the line, or
     directly under it; table numbers sit under their column heading;
  3. arithmetic: quantity x rate = amount, lines sum to the subtotal, subtotal + tax =
     total. Arithmetic decides which printed total is which and repairs a single misread
     number on a scan, and it is what the confidence figures rest on.

A field that cannot be read stays empty with a reason. Nothing is guessed (D16).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from . import textutil as T
from .models import FIELD_LABELS, FieldValue, Invoice, LineItem, SourceRef
from .reader import Cell, DocRead, Line, Word

ZERO = Decimal("0")


def _keys(*labels: str) -> set[str]:
    return {T.letters_digits(label.replace("#", " no ")) for label in labels}


# --------------------------------------------------------------------------- label vocabulary

LABELS: dict[str, list[set[str]]] = {
    # each field: label groups from most to least specific
    "invoice_number": [
        _keys("invoice no", "invoice number", "inv no", "inv number", "invoice #", "tax invoice no",
              "tax invoice number", "bill no", "bill number", "invoice id", "document no"),
        _keys("invoice"),
    ],
    "invoice_date": [
        _keys("invoice date", "date of invoice", "inv date", "bill date", "dated", "date of issue",
              "issue date", "invoice dt"),
        _keys("date"),
    ],
    "po_reference": [
        _keys("po no", "po number", "p o no", "p o number", "purchase order no", "purchase order number",
              "po ref", "po reference", "buyer's order no", "buyers order no", "buyer order no",
              "your order no", "your order ref", "your order reference", "customer po", "customer po no",
              "order ref", "order reference", "ref po no"),
        _keys("purchase order", "order no", "order number"),
    ],
}
SUBTOTAL = _keys("sub total", "taxable value", "taxable amount", "total taxable value", "total before tax",
                 "amount before tax", "basic amount", "assessable value", "taxable total")
GRAND = _keys("grand total", "invoice total", "total amount", "total payable", "net payable", "amount payable",
              "amount due", "total invoice value", "invoice value", "total amount payable", "balance due",
              "total inr", "total rs", "net total", "invoice total in figures", "total inclusive of gst",
              "total due", "total amount due", "net amount", "net amount payable",
              "total incl gst", "total inclusive of tax", "invoice value in figures", "quoted total")
GENERIC_TOTAL = _keys("total")
TAX_LABELS = {"cgst": "cgst", "sgst": "sgst", "utgst": "sgst", "igst": "igst"}
ROUND_OFF = _keys("round off", "rounding off", "rounded off", "roundoff")
WORDS_LABELS = _keys("amount in words", "invoice total in words", "total in words", "amount chargeable in words",
                     "invoice value in words", "in words")
BANK_NAME = _keys("bank name", "bank", "bankers")
BANK_ACCOUNT = _keys("a/c no", "a/c", "ac no", "account no", "account number", "a/c number", "acc no",
                     "alc no", "aic no", "a c no", "alc", "aic")
BANK_IFSC = _keys("ifsc", "ifsc code", "ifs code", "rtgs/neft ifsc", "ifsc no")
OTHER_LABELS = _keys(
    "due date", "po date", "p o date", "order date", "delivery date", "challan no", "challan date",
    "vehicle no", "e way bill no", "eway bill no", "place of supply", "state", "state code", "supplier state",
    "service period", "email", "phone", "pan", "terms", "branch", "bill to", "billed to", "ship to", "to",
    "customer", "customer gstin", "supply details", "payment details", "bank details",
    "bank details for payment", "gstin", "gstin/uin", "gst no", "details of receiver billed to", "ref",
    "quotation no", "quotation date", "valid until", "subject")
ALL_LABELS: set[str] = set().union(*[g for groups in LABELS.values() for g in groups], SUBTOTAL, GRAND,
                                   GENERIC_TOTAL, ROUND_OFF, WORDS_LABELS, BANK_NAME, BANK_ACCOUNT,
                                   BANK_IFSC, OTHER_LABELS, set(TAX_LABELS))
END_OF_TABLE = SUBTOTAL | GRAND | GENERIC_TOTAL | ROUND_OFF | WORDS_LABELS | BANK_NAME | BANK_ACCOUNT | \
    BANK_IFSC | _keys("terms", "bank details", "bank details for payment", "payment details",
                      "declaration", "e & o e", "tax breakup")

NON_INVOICE_TITLES = {
    "quotation": "quotation", "quote": "quotation", "estimate": "quotation",
    "proforma invoice": "proforma", "pro forma invoice": "proforma", "pro-forma invoice": "proforma",
    "purchase order": "purchase_order", "delivery challan": "delivery_challan", "challan": "delivery_challan",
    "credit note": "credit_note", "debit note": "debit_note", "statement of account": "statement",
    "payment receipt": "receipt", "receipt": "receipt", "sales order": "sales_order",
}
DOC_TYPE_NAMES = {
    "tax_invoice": "tax invoice", "quotation": "quotation", "proforma": "pro-forma invoice",
    "purchase_order": "purchase order", "delivery_challan": "delivery challan", "credit_note": "credit note",
    "debit_note": "debit note", "statement": "statement of account", "receipt": "payment receipt",
    "sales_order": "sales order", "not_invoice": "not an invoice",
}
UOM_WORDS = {"nos", "no", "pcs", "pc", "kg", "kgs", "ltr", "ltrs", "l", "mtr", "mtrs", "m", "roll", "rolls",
             "box", "boxes", "carton", "cartons", "set", "sets", "pair", "pairs", "lot", "each", "ea", "unit",
             "units", "pail", "pails", "bottle", "bottles", "pkt", "pkts", "dozen", "ream", "reams"}
LEGAL_FORMS = {"pvt", "private", "ltd", "limited", "llp", "inc", "corp", "corporation", "company"}
COMPANY_WORDS = LEGAL_FORMS | {"co", "industries", "enterprises", "traders", "supplies", "services", "solutions",
                               "systems", "associates", "agencies", "equipment", "infotech", "packaging", "furnishings"}
# a line that starts like this, or carries a contact detail, is never the supplier's name
NOT_A_NAME = re.compile(r"(?i)^\s*(e-?mail|phone|ph|tel|telephone|mobile|mob|fax|website|web|www|cin|pan|address|"
                        r"regd|registered office|contact|state|place of supply)\b\s*[.:\-]|@|\bwww\.|https?://")

# table headings: token sequence -> column role
HEADER_VOCAB: dict[tuple[str, ...], str] = {}
for _role, _phrases in {
    "sl": ["sl", "sl no", "s no", "sr", "sr no", "#", "sno"],
    "desc": ["description", "description of goods", "description of service", "description of services",
             "description of goods and services", "particulars", "item", "item description", "item name",
             "product", "product description", "goods", "details", "name of item"],
    "hsn": ["hsn", "sac", "hsn sac", "hsn code", "sac code"],
    "qty": ["qty", "quantity", "nos"],
    "uom": ["uom", "unit", "per", "units"],
    "rate": ["rate", "unit price", "price", "unit rate", "rate rs", "rate inr", "basic rate", "unit price rs",
             "rate incl gst", "price incl gst", "unit price incl gst", "rate incl tax", "mrp"],
    "amount": ["amount", "amount rs", "amount inr", "line total", "value", "net amount", "amount incl gst",
               "amount incl tax", "value rs"],
    "taxable": ["taxable value", "taxable amount", "taxable val", "assessable value"],
    "taxrate": ["gst %", "gst", "gst rate", "igst %", "cgst %", "sgst %", "tax %", "tax rate", "gst rate %"],
    "taxamt": ["igst amt", "igst amount", "cgst amt", "sgst amt", "gst amt", "gst amount", "tax amt", "tax amount"],
    "gross": ["total", "total amount", "total rs", "gross amount"],
}.items():
    for _phrase in _phrases:
        HEADER_VOCAB[tuple(_phrase.split())] = _role
INCLUSIVE_RE = re.compile(r"(?i)\b(?:inclusive of (?:all )?(?:gst|tax(?:es)?)|incl\.? (?:of )?(?:gst|tax)|"
                          r"(?:rates?|prices?) (?:are )?inclusive|tax(?:es)? included|gst included)\b")
PERCENT_RE = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/\-_.]{1,24}$")


# --------------------------------------------------------------------------- small helpers

@dataclass
class Piece:
    """A word, or half of a word that was split at a colon ('No:SIS/2311')."""
    text: str
    word: Word


def _pieces(cell: Cell) -> list[Piece]:
    out: list[Piece] = []
    for w in cell.words:
        if ":" in w.text.strip(":") and not re.match(r"^\d{1,2}:\d{2}", w.text):
            head, _, tail = w.text.partition(":")
            if head:
                out.append(Piece(head + ":", w))
            if tail:
                out.append(Piece(tail, w))
        else:
            out.append(Piece(w.text, w))
    return out


@dataclass
class LabelHit:
    key: str
    rest: list[Piece]

    @property
    def rest_text(self) -> str:
        return re.sub(r"^[\s:;#=.\-]+", "", " ".join(p.text for p in self.rest)).strip()


def match_label(cell: Cell, vocab: set[str], fuzzy: bool = False) -> Optional[LabelHit]:
    """Does the cell start with one of the labels? Longest label first. On scanned pages a
    one-character slip in a longer label is tolerated ('lnvoice No')."""
    pieces = _pieces(cell)
    top = min(6, len(pieces))
    prefixes = []
    for n in range(top, 0, -1):
        prefix = " ".join(p.text for p in pieces[:n])
        prefixes.append((n, T.letters_digits(prefix.replace("#", " no "))))
    for n, key in prefixes:
        if key in vocab:
            return LabelHit(key, pieces[n:])
    if fuzzy:
        for n, key in prefixes:
            if len(key) >= 6 and key.isalpha():
                slack = 1 if len(key) < 10 else 2 if len(key) < 13 else 3
                for candidate in vocab:
                    if len(candidate) >= 6 and abs(len(candidate) - len(key)) <= slack \
                            and T.edit_distance(candidate, key) <= slack:
                        return LabelHit(candidate, pieces[n:])
    return None


def is_label(cell: Cell, fuzzy: bool = False) -> bool:
    hit = match_label(cell, ALL_LABELS, fuzzy)
    return hit is not None and not hit.rest_text


def _bbox(words: list[Word]) -> list[float]:
    return [round(min(w.x0 for w in words), 1), round(min(w.top for w in words), 1),
            round(max(w.x1 for w in words), 1), round(max(w.bottom for w in words), 1)]


def _conf(words: list[Word], method: str, loose: bool = False, below: bool = False) -> float:
    if method == "ocr":
        values = [w.conf for w in words if w.conf is not None]
        base = min(0.95, max(0.4, (sum(values) / len(values)) / 100)) if values else 0.6
    else:
        base = 0.99
    if below:
        base -= 0.03
    if loose:
        base *= 0.7
    return round(base, 2)


def _amount_words(words: list[Word]) -> list[tuple[Decimal, Word]]:
    """Numbers among the words, skipping percentages ('9%', '18 %', '18 percent')."""
    out = []
    for i, w in enumerate(words):
        text = w.text.strip()
        nxt = words[i + 1].text.strip().lower() if i + 1 < len(words) else ""
        if "%" in text or nxt in {"%", "percent", "per"} or nxt.startswith("%"):
            continue
        if text.startswith("@") or text.startswith("("):
            text = text.lstrip("@(").rstrip(")")
            if "%" in w.text:
                continue
        value = T.parse_amount(text.rstrip(".,;)"))
        if value is not None:
            out.append((value, w))
    return out


class _Ctx:
    """Everything the extraction functions need about one document."""

    def __init__(self, doc: DocRead):
        self.doc = doc
        self.lines = doc.lines
        self.method = {p.number: p.method for p in doc.pages}
        self.height = {p.number: p.height for p in doc.pages}

    def fuzzy(self, line: Line) -> bool:
        return self.method.get(line.page) == "ocr"

    def src(self, line: Line, words: list[Word], snippet: Optional[str] = None) -> SourceRef:
        return SourceRef(page=line.page, snippet=T.unify(snippet if snippet is not None else line.text)[:200],
                         bbox=_bbox(words), method=self.method.get(line.page, "text"))

    def below(self, line: Line, cell: Cell) -> Optional[tuple[Line, Cell]]:
        """The cell directly under `cell` on one of the next lines of the same page."""
        idx = self.lines.index(line)
        for nxt in self.lines[idx + 1: idx + 4]:
            if nxt.page != line.page or nxt.top - cell.bottom > 3.4 * max(cell.bottom - cell.top, 8):
                break
            for c in nxt.cells:
                overlap = min(c.x1, cell.x1) - max(c.x0, cell.x0)
                if overlap > 0 or abs(c.x0 - cell.x0) <= 12:
                    return nxt, c
        return None


# --------------------------------------------------------------------------- header fields

@dataclass
class Candidate:
    value: object
    words: list[Word]
    line: Line
    priority: int      # label group, then where the value was found (lower is better)
    loose: bool = False
    below: bool = False
    raw: str = ""


def _parse_identifier(pieces: list[Piece]) -> Optional[tuple[str, list[Word], bool]]:
    tokens = [p for p in pieces if p.text.strip(":;#=.-")]
    if not tokens:
        return None
    first = tokens[0].text.strip(".,;:_|'\"`").lstrip("-=~").replace("$", "S").replace("§", "S")
    if not ID_RE.match(first) or not any(ch.isdigit() for ch in first) or T.parse_date(first):
        return None
    rest = [p.text.strip(".,;:").lower() for p in tokens[1:]]
    benign = (not rest) or rest[0] in {"dt", "dated", "date", "of", "/"} or T.parse_date(" ".join(rest[:3])) is not None
    return first, [tokens[0].word], not benign


def _parse_field(kind: str, pieces: list[Piece]) -> Optional[tuple[object, list[Word], bool, str]]:
    """(value, words, loose, raw text) or None when the text is not a value of this kind."""
    if not pieces:
        return None
    text = re.sub(r"^[\s:;#=.\-]+", "", " ".join(p.text for p in pieces)).strip()
    if not text:
        return None
    if kind == "id":
        got = _parse_identifier(pieces)
        return (got[0], got[1], got[2], got[1][0].text.strip(".,;:_|'\"`").lstrip("-=~")) if got else None
    if kind == "date":
        got = T.parse_date(text)
        if not got:
            return None
        loose = len(text) > len(got[1]) + 12
        return got[0].isoformat(), [p.word for p in pieces], loose, got[1]
    return None


def _find_labelled(ctx: _Ctx, name: str, kind: str) -> Optional[Candidate]:
    best: Optional[Candidate] = None
    for group_no, vocab in enumerate(LABELS[name]):
        for line in ctx.lines:
            fuzzy = ctx.fuzzy(line)
            for k, cell in enumerate(line.cells):
                hit = match_label(cell, vocab, fuzzy)
                if hit is None:
                    continue
                options: list[tuple[int, list[Piece], Line, bool]] = []
                if hit.rest_text:
                    options.append((0, hit.rest, line, False))
                elif k + 1 < len(line.cells) and not is_label(line.cells[k + 1], fuzzy):
                    options.append((1, _pieces(line.cells[k + 1]), line, False))
                under = ctx.below(line, cell)
                if under and not hit.rest_text and not is_label(under[1], fuzzy):
                    options.append((2, _pieces(under[1]), under[0], True))
                for place, pieces, src_line, below in options:
                    parsed = _parse_field(kind, pieces)
                    if parsed is None:
                        continue
                    cand = Candidate(parsed[0], parsed[1], src_line, group_no * 10 + place, parsed[2], below, parsed[3])
                    # a clean label and value beats the same label found in running text
                    # (the running head of a continuation page, for example)
                    if best is None or (cand.loose, cand.priority) < (best.loose, best.priority):
                        best = cand
                    break
        if best is not None:
            return best
    return best


def _field(name: str, value=None, confidence: float = 0.0, source: Optional[SourceRef] = None,
           missing: Optional[str] = None, loose: bool = False, notes: Optional[list[str]] = None) -> FieldValue:
    return FieldValue(name=name, label=FIELD_LABELS.get(name, name), value=value, confidence=confidence,
                      source=source, missing_reason=missing, loose=loose, notes=notes or [])


# --------------------------------------------------------------------------- document type

# words that turn "Purchase Order" into a label ("Purchase Order No.") instead of a title
LABEL_TAILS = {"no", "no.", "nos", "number", "num", "#", "date", "dt", "dated", "ref", "ref.", "reference", "id",
               "details", "value", "amount", "qty", "quantity", "and", "&", "/", "terms", "by", "to", "from"}
INVOICE_TITLE = re.compile(r"(tax |retail |gst |commercial |service )?invoice( cum .*| ?\(?original.*)?|bill of supply|cash memo")


def _title_kind(cell_text: str) -> Optional[str]:
    """'tax_invoice', another document kind, or None when the cell is not a document title.
    A title stands on its own: 'QUOTATION' is one, 'Quotation Ref:' and 'Purchase Order No.' are labels."""
    raw = cell_text.strip()
    if raw.endswith(":"):
        return None
    words = T.norm(raw).strip(" -.")
    if not words or len(words.split()) > 5 or any(ch.isdigit() for ch in words):
        return None
    for title, kind in NON_INVOICE_TITLES.items():
        if words == title:
            return kind
        if words.startswith(title + " "):
            rest = words[len(title):].split()
            if rest[0] in LABEL_TAILS or ("invoice" in rest and kind != "proforma"):
                return None
            return kind
    if INVOICE_TITLE.fullmatch(words):
        return "tax_invoice"
    return None


def detect_doc_type(ctx: _Ctx) -> tuple[str, str]:
    """('tax_invoice' | 'quotation' | ... | 'not_invoice', the text that decided it).
    The document's title decides: the first standalone title in the upper part of page 1."""
    first_page = [ln for ln in ctx.lines if ln.page == 1]
    limit = 0.4 * ctx.height.get(1, 842)
    for line in first_page:
        if line.top > limit:
            break
        for cell in line.cells:
            kind = _title_kind(cell.text)
            if kind:
                return kind, cell.text
    text = T.norm(ctx.doc.text)
    if re.search(r"\btax invoice\b|\binvoice (no|number|#|date)\b|\bbill no\b", text):
        return "tax_invoice", "mentions an invoice number or 'tax invoice'"
    signals = sum(bool(re.search(p, text)) for p in (r"\bgstin\b", r"\binvoice\b|\bbill\b", r"\btotal\b", r"\d,\d{3}\.\d{2}"))
    if signals >= 3:
        return "tax_invoice", "has GSTIN, totals and amounts"
    return "not_invoice", "no invoice title, number, GSTIN or amounts found"


# --------------------------------------------------------------------------- GSTINs

def _gstin_candidates(ctx: _Ctx, known: dict[str, str]) -> list[dict]:
    """GSTIN-shaped strings in reading order. `known` maps known GSTINs to a tag
    ('buyer' or a vendor id); a scanned GSTIN one or two characters off a known one
    is matched to it and marked as corrected."""
    out: list[dict] = []
    seen: set[str] = set()
    for line in ctx.lines:
        for w in line.words:
            token = re.sub(r"[^A-Za-z0-9|]", "", w.text).upper()
            if not (13 <= len(token) <= 17) or sum(c.isdigit() for c in token) < 4 or not token[:1].isdigit():
                continue
            found = T.find_gstins(token)
            gstin = found[0]["gstin"] if found else None
            valid = bool(found and found[0]["valid"])
            corrected = bool(found and found[0]["repaired"])
            tag = known.get(gstin) if gstin else None
            if not valid or tag is None:
                # compare with known GSTINs, tolerating OCR slips
                key = T.confusable_key(token)
                near = [(T.edit_distance(key, T.confusable_key(k)), k) for k in known]
                near = [n for n in near if n[0] <= 2]
                if near and (ctx.fuzzy(line) or min(near)[0] == 0):
                    distance, match = min(near)
                    if gstin != match:
                        corrected = True
                    gstin, valid, tag = match, True, known[match]
            if gstin is None or gstin in seen:
                continue
            seen.add(gstin)
            out.append({"gstin": gstin, "raw": w.text, "valid": valid, "corrected": corrected, "tag": tag,
                        "page": line.page, "snippet": T.unify(line.text)[:160], "bbox": _bbox([w]),
                        "conf": _conf([w], ctx.method.get(line.page, "text"))})
    return out


# --------------------------------------------------------------------------- line items

@dataclass
class Column:
    role: str
    x0: float
    x1: float
    inclusive: bool = False

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Table:
    header: Optional[Line] = None
    columns: list[Column] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    last_line: Optional[Line] = None
    total_row: Optional[dict] = None
    inclusive_header: bool = False

    def col(self, role: str) -> Optional[Column]:
        return next((c for c in self.columns if c.role == role), None)


def _header_columns(line: Line) -> tuple[list[Column], float]:
    """Column headings on a line: (columns, share of the line's words that are headings).
    A heading of several words must sit inside one cell, so 'GST' and 'Amount' in
    neighbouring columns are never read as 'GST Amount'."""
    cell_of = {id(w): k for k, cell in enumerate(line.cells) for w in cell.words}
    tokens: list[tuple[str, Word]] = []
    for w in line.words:
        for tok in re.findall(r"[a-z]+|%|#", w.text.lower()):
            tokens.append((tok, w))
    columns: list[Column] = []
    used = 0
    i = 0
    while i < len(tokens):
        for n in (4, 3, 2, 1):
            chunk = tokens[i:i + n]
            if len(chunk) < n or len({cell_of[id(w)] for _, w in chunk}) != 1:
                continue
            seq = tuple(t for t, _ in chunk)
            role = HEADER_VOCAB.get(seq)
            if role:
                span = [w for _, w in chunk]
                columns.append(Column(role, min(w.x0 for w in span), max(w.x1 for w in span),
                                      inclusive="incl" in seq))
                used += n
                i += n
                break
        else:
            i += 1
    return columns, used / max(len(tokens), 1)


# what sits between the pages of a long invoice and is neither an item nor the end of the table
PAGE_FURNITURE = re.compile(
    r"(?i)\bpage\s*\d+\s*(of|/)\s*\d+\b|^\s*page\s*\d+\s*$|\b(continued|contd|cont'd)\b|"
    r"^\s*\(?\s*(carried|brought)\s+(forward|over|down)\b|^\s*\(?\s*[cb]\s*/\s*[fd]\b|"
    r"^\s*(balance|total|sub\s?total|amount)\s+(c\s*/\s*f|b\s*/\s*f|carried|brought)\b|\bp\.?\s?t\.?\s?o\b")


def _is_table_header(line: Line) -> bool:
    columns, coverage = _header_columns(line)
    roles = {c.role for c in columns}
    return "desc" in roles and bool(roles & {"amount", "taxable", "gross"}) and coverage >= 0.6 \
        and (len(roles) >= 3 or len(line.cells) <= 3)


def _between_pages(ctx: _Ctx, header: Line) -> set[int]:
    """ids of lines that belong to page furniture: the running head of a continuation page
    (everything above a repeated column heading, and the heading itself), 'continued' and
    carried-forward strips, page numbers, and text in the bottom margin."""
    skip: set[int] = set()
    by_page: dict[int, list[Line]] = {}
    for line in ctx.lines:
        by_page.setdefault(line.page, []).append(line)
    for page, lines in by_page.items():
        height = ctx.height.get(page, 842) or 842
        if page > header.page:
            repeat = next((i for i, ln in enumerate(lines) if _is_table_header(ln)), None)
            if repeat is not None:
                skip.update(id(ln) for ln in lines[:repeat + 1])
        for ln in lines:
            if PAGE_FURNITURE.search(ln.text) or (ln.top > 0.94 * height and not any(T.looks_numeric(w.text) for w in ln.words)):
                skip.add(id(ln))
    skip.discard(id(header))
    return skip


def _find_table(ctx: _Ctx) -> Table:
    table = Table()
    for line in ctx.lines:
        if _is_table_header(line):
            table.header, table.columns = line, _header_columns(line)[0]
            break
    if table.header is None:
        return table
    # 'Unit' next to 'Price' was matched as 'unit price'; a lone 'Total' beside 'Taxable Value' is gross
    roles = [c.role for c in table.columns]
    if "amount" in roles and "taxable" in roles:
        for c in table.columns:
            if c.role == "amount":
                c.role = "gross"
    if "amount" not in roles and "taxable" not in roles and "gross" in roles:
        table.col("gross").role = "amount"
    table.inclusive_header = any(c.inclusive for c in table.columns)
    desc = table.col("desc")
    numeric_roles = {"sl", "hsn", "qty", "rate", "amount", "taxable", "taxrate", "taxamt", "gross"}
    start = ctx.lines.index(table.header) + 1
    table.last_line = table.header
    furniture = _between_pages(ctx, table.header)
    for line in ctx.lines[start:]:
        if id(line) in furniture:
            continue
        fuzzy = ctx.fuzzy(line)
        first_hit = match_label(line.cells[0], END_OF_TABLE | set(TAX_LABELS), fuzzy)
        starts_tax = T.letters_digits(line.cells[0].words[0].text)[:5].rstrip("0123456789") in TAX_LABELS
        if first_hit or starts_tax or any(match_label(c, END_OF_TABLE, fuzzy) for c in line.cells[1:]) \
                or T.norm(line.text).startswith("rupees"):
            if first_hit and first_hit.key in GENERIC_TOTAL:
                table.total_row = _assign_row(line, table, numeric_roles)
            break
        row = _assign_row(line, table, numeric_roles)
        has_money = any(r in row["values"] for r in ("amount", "taxable", "gross"))
        if has_money:
            table.rows.append(row)
            table.last_line = line
        elif table.rows and row["text_words"] and desc and min(w.x0 for w in row["text_words"]) >= desc.x0 - 20 \
                and line.page == table.last_line.page and line.top - table.last_line.top <= 30:
            # a wrapped description: text under the item, on the same page and close to it
            table.rows[-1]["more_text"].append(" ".join(w.text for w in row["text_words"]))
            table.last_line = line
        elif table.rows and row["text_words"] and line.page != table.last_line.page:
            continue        # stray text at the top of a continuation page
        elif not table.rows and row["text_words"]:
            continue
        else:
            if table.rows:
                break
    return table


def _role_ok(role: str, value: Decimal, word: Word, desc: Optional[Column]) -> bool:
    digits = re.sub(r"\D", "", word.text)
    if role == "sl":
        return value == value.to_integral_value() and value < 1000 and (desc is None or word.xc < desc.x0)
    if role == "hsn":
        return word.text.strip().isdigit() and 4 <= len(digits) <= 8
    if role == "taxrate":
        return value <= 40
    if role == "qty":
        return value > 0
    return value >= 0


def _assign_row(line: Line, table: Table, numeric_roles: set[str]) -> dict:
    """Give each number on the row to the column heading it lines up with (right edge,
    centre or left edge, whichever is closest), best fits first."""
    desc = table.col("desc")
    numeric: list[tuple[Decimal, Word]] = []
    for w in line.words:
        text = w.text.strip().rstrip("%")
        if re.search(r"[A-Za-z]", text.replace("O", "").replace("l", "")) and not re.fullmatch(r"[\dOoIl|,.]+", text):
            continue
        value = T.parse_amount(text)
        if value is not None:
            numeric.append((value, w))
    options = []
    for value, w in numeric:
        for col in table.columns:
            if col.role not in numeric_roles or not _role_ok(col.role, value, w, desc):
                continue
            cost = min(abs(w.x1 - col.x1), abs(w.xc - col.xc), abs(w.x0 - col.x0))
            if cost <= 28:
                options.append((cost, id(w), col.role, value, w))
    options.sort(key=lambda o: o[0])
    values: dict[str, Decimal] = {}
    value_words: dict[str, Word] = {}
    taken: set[int] = set()
    for cost, wid, role, value, w in options:
        if role in values or wid in taken:
            continue
        values[role], value_words[role] = value, w
        taken.add(wid)
    uom = None
    uom_col = table.col("uom")
    text_words: list[Word] = []
    first_num_x = min((w.x0 for r, w in value_words.items() if r != "sl"), default=1e9)
    for w in line.words:
        if id(w) in taken:
            continue
        low = w.text.strip(".,").lower()
        qty_word = value_words.get("qty")
        if low in UOM_WORDS and ((qty_word is not None and 0 <= w.x0 - qty_word.x1 < 30) or
                                 (uom_col is not None and abs(w.xc - uom_col.xc) < 20)):
            uom = w.text.strip(".,")
            continue
        if desc is not None and w.x1 < desc.x0 - 3 and len(w.text.strip(".")) <= 2:
            continue  # a serial number the scan turned into a letter
        if w.x0 < first_num_x:
            text_words.append(w)
    return {"line": line, "values": values, "value_words": value_words, "uom": uom,
            "text_words": text_words, "more_text": [], "all_numbers": numeric}


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= max(Decimal("0.51"), abs(b) * Decimal("0.0005"))


def _build_items(ctx: _Ctx, table: Table) -> list[LineItem]:
    items: list[LineItem] = []
    for n, row in enumerate(table.rows, 1):
        line: Line = row["line"]
        v = row["values"]
        method = ctx.method.get(line.page, "text")
        amount = v.get("taxable", v.get("amount", v.get("gross")))
        description = " ".join([" ".join(w.text for w in row["text_words"])] + row["more_text"]).strip()
        hsn_word = row["value_words"].get("hsn")
        item = LineItem(line_no=n, description=T.unify(description), hsn=hsn_word.text.strip() if hsn_word else None,
                        qty=v.get("qty"), uom=row["uom"], unit_price=v.get("rate"), amount=amount,
                        tax_rate=v.get("taxrate"), source=ctx.src(line, line.words),
                        confidence=_conf([w for w in row["value_words"].values()] or line.words, method))
        q, r, a = item.qty, item.unit_price, item.amount
        if q is not None and r is not None and a is not None and not _close(q * r, a):
            # try the other numbers on the row before concluding something was misread
            numbers = [val for val, _ in row["all_numbers"]]
            fixed = False
            for cand_q in numbers:
                for cand_r in numbers:
                    if cand_q > 0 and cand_r > 0 and _close(cand_q * cand_r, a) and (cand_q, cand_r) != (q, r) \
                            and cand_q == cand_q.to_integral_value() and cand_q != a and cand_r != a:
                        item.qty, item.unit_price = cand_q, cand_r
                        item.flags.append("columns_resolved_by_arithmetic")
                        fixed = True
                        break
                if fixed:
                    break
            if not fixed:
                item.flags.append("line_math_mismatch")
        if q is not None and r is not None and a is None:
            item.amount = T.q2(q * r)
            item.flags.append("amount_derived")
        if a is not None and (item.qty is None or item.unit_price is None):
            _fill_by_arithmetic(item, row)
        items.append(item)
    return items


def _fill_by_arithmetic(item: LineItem, row: dict) -> None:
    """A quantity or unit price that no column heading claimed can still be identified:
    it is the number on the row that makes quantity x price equal the amount. Used only
    when exactly one number (or one pair) fits."""
    claimed = {id(w) for role, w in row["value_words"].items() if role in ("sl", "hsn", "taxrate", "taxamt", "gross")}
    spare = [val for val, w in row["all_numbers"] if id(w) not in claimed and val > 0 and val != item.amount]
    a = item.amount
    if item.qty is None and item.unit_price is not None:
        fits = {n for n in spare if n != item.unit_price and _close(n * item.unit_price, a)}
        if len(fits) == 1:
            item.qty = fits.pop()
            item.flags.append("qty_found_by_arithmetic")
    elif item.unit_price is None and item.qty is not None:
        fits = {n for n in spare if n != item.qty and _close(item.qty * n, a)}
        if len(fits) == 1:
            item.unit_price = fits.pop()
            item.flags.append("price_found_by_arithmetic")
    elif item.qty is None and item.unit_price is None:
        pairs = {(n1, n2) for n1 in spare for n2 in spare
                 if n1 == n1.to_integral_value() and n1 < n2 * 1000 and _close(n1 * n2, a) and n2 != a}
        whole = {p for p in pairs if p[0] != p[1] or True}
        if len(whole) == 1:
            item.qty, item.unit_price = whole.pop()
            item.flags.append("qty_and_price_found_by_arithmetic")


# --------------------------------------------------------------------------- totals

def _label_amounts(ctx: _Ctx, line: Line, k: int, hit: LabelHit) -> list[tuple[Decimal, Word]]:
    """Amounts that belong to the label in cell k: the rest of that cell, then the cells to
    its right until another label starts."""
    words = [p.word for p in hit.rest]
    found = _amount_words(words)
    for cell in line.cells[k + 1:]:
        if match_label(cell, ALL_LABELS, ctx.fuzzy(line)):
            break
        found += _amount_words(cell.words)
    return found


def _find_totals(ctx: _Ctx, table: Table) -> dict:
    start = ctx.lines.index(table.last_line) + 1 if table.last_line else 0
    out: dict = {"subtotal": [], "grand": [], "generic": [], "cgst": None, "sgst": None, "igst": None,
                 "round_off": None, "words": None}
    if table.total_row:
        row = table.total_row
        line = row["line"]
        for role, target in (("taxable", "subtotal"), ("amount", "subtotal"), ("gross", "grand")):
            if role in row["values"] and (role != "amount" or table.col("gross") is None or True):
                word = row["value_words"][role]
                if role == "amount" and table.col("taxable") is None and table.col("gross") is None:
                    out["generic"].append((row["values"][role], word, line))
                else:
                    out[target].append((row["values"][role], word, line))
        if "taxamt" in row["values"]:
            out["table_tax"] = (row["values"]["taxamt"], row["value_words"]["taxamt"], line)
        if not row["values"] and row["all_numbers"]:
            out["generic"].append((row["all_numbers"][-1][0], row["all_numbers"][-1][1], line))
    for line in ctx.lines[start:]:
        fuzzy = ctx.fuzzy(line)
        for k, cell in enumerate(line.cells):
            first = T.letters_digits(cell.words[0].text)
            tax_kind = next((v for key, v in TAX_LABELS.items() if first.startswith(key)), None)
            if tax_kind and (table.total_row is None or line is not table.total_row["line"]):
                amounts = _amount_words([w for w in cell.words[1:]])
                for nxt in line.cells[k + 1:]:
                    if match_label(nxt, ALL_LABELS, fuzzy):
                        break
                    amounts += _amount_words(nxt.words)
                rate = PERCENT_RE.search(cell.text)
                small = [a for a in amounts if a[0] > 40 or "." in a[1].text or "," in a[1].text]
                amounts = small or amounts
                if amounts and out[tax_kind] is None:
                    out[tax_kind] = {"amount": amounts[-1][0], "word": amounts[-1][1], "line": line,
                                     "rate": Decimal(rate.group(1)) if rate else None}
                continue
            for target, vocab in (("words", WORDS_LABELS), ("subtotal", SUBTOTAL), ("grand", GRAND),
                                  ("round_off", ROUND_OFF), ("generic", GENERIC_TOTAL)):
                hit = match_label(cell, vocab, fuzzy)
                if hit is None:
                    continue
                if target == "words":
                    text = hit.rest_text or " ".join(c.text for c in line.cells[k + 1:])
                    if out["words"] is None and text:
                        out["words"] = (text, line, [p.word for p in hit.rest] or line.words)
                    break
                if table.total_row is not None and line is table.total_row["line"]:
                    break
                amounts = _label_amounts(ctx, line, k, hit)
                if amounts:
                    value, word = amounts[-1]
                    if target == "round_off":
                        out["round_off"] = (value, word, line)
                    else:
                        out[target].append((value, word, line))
                break
        if out["words"] is None and T.norm(line.text).startswith("rupees"):
            out["words"] = (line.text, line, line.words)
    return out


# --------------------------------------------------------------------------- bank, vendor name, currency

def _bank_details(ctx: _Ctx, fields: dict[str, FieldValue]) -> None:
    for line in ctx.lines:
        fuzzy = ctx.fuzzy(line)
        method = ctx.method.get(line.page, "text")
        for k, cell in enumerate(line.cells):
            for name, vocab in (("bank_account", BANK_ACCOUNT), ("bank_ifsc", BANK_IFSC), ("bank_name", BANK_NAME)):
                if name in fields:
                    continue
                hit = match_label(cell, vocab, fuzzy)
                if hit is None:
                    continue
                rest_words = [p.word for p in hit.rest]
                text = hit.rest_text
                if not text and k + 1 < len(line.cells) and not is_label(line.cells[k + 1], fuzzy):
                    rest_words, text = line.cells[k + 1].words, line.cells[k + 1].text
                if not text:
                    continue
                if name == "bank_account":
                    digits = T.digits_only(text.split(",")[0])
                    if 9 <= len(digits) <= 18:
                        fields[name] = _field(name, digits, _conf(rest_words, method), ctx.src(line, rest_words))
                elif name == "bank_ifsc":
                    code = _repair_ifsc(text)
                    if code:
                        fields[name] = _field(name, code, _conf(rest_words, method), ctx.src(line, rest_words))
                else:
                    if re.search(r"(?i)details|account|a/c", text):
                        continue
                    fields[name] = _field(name, text.split(",")[0].strip(), _conf(rest_words, method),
                                          ctx.src(line, rest_words))
                break
    text = ctx.doc.text
    if "bank_ifsc" not in fields:
        m = T.IFSC_RE.search(text.upper())
        if m:
            fields["bank_ifsc"] = _field("bank_ifsc", m.group(0), 0.6, SourceRef(snippet=m.group(0), method="text"),
                                         loose=True)
    if "bank_account" not in fields:
        m = re.search(r"(?i)\b(?:a.?c|acc(?:oun)?t)\b[^\d\n]{0,25}(\d[\d ]{8,20}\d)", text)
        if m:
            fields["bank_account"] = _field("bank_account", T.digits_only(m.group(1)), 0.6,
                                            SourceRef(snippet=m.group(0)[:80], method="text"), loose=True)


def _repair_ifsc(text: str) -> Optional[str]:
    for token in re.findall(r"[A-Za-z0-9]{11}", text.upper().replace(" ", "")[:40] + " " + text.upper()):
        if T.IFSC_RE.fullmatch(token):
            return token
        fixed = token[:4].translate(str.maketrans("01258", "OIZSB")) + "0" + token[5:]
        if token[4] in "0OQD" and T.IFSC_RE.fullmatch(fixed):
            return fixed
    return None


def _vendor_name(ctx: _Ctx, buyer_name: str) -> Optional[FieldValue]:
    first_page = [ln for ln in ctx.lines if ln.page == 1]
    limit = 0.32 * ctx.height.get(1, 842)
    stop = re.compile(r"(?i)^(bill(ed)? to|ship to|to,?|buyer|customer|details of receiver|consignee)\b")
    candidates: list[tuple[int, Line, Cell]] = []
    for line in first_page:
        if line.top > limit or stop.match(line.text.strip()):
            break
        for cell in line.cells:
            text = cell.text.strip()
            tokens = set(re.findall(r"[a-z]+", text.lower()))
            if not text or re.search(r"(?i)\binvoice\b|original for|gstin|^\(?state\b", text) or NOT_A_NAME.search(text):
                continue
            if T.name_similarity(text, buyer_name) >= 0.8:
                continue
            legal = bool(tokens & LEGAL_FORMS)
            score = (2 if tokens & COMPANY_WORDS else 0) + (1 if len(text.split()) >= 2 else 0) + (1 if legal else 0)
            # a digit usually means an address line; a name with a legal form (Pvt Ltd, LLP) may contain one
            looks_like_address = any(ch.isdigit() for ch in text) and (not legal or re.search(r"\b\d{6}\b", text))
            if score >= 2 and not looks_like_address:
                candidates.append((score, line, cell))
    if candidates:
        best = max(score for score, _, _ in candidates)
        _, line, cell = next(c for c in candidates if c[0] == best)       # the first of the strongest candidates
        name = re.sub(r"(?i)^m/s\.?\s*", "", cell.text.strip())
        return _field("vendor_name", T.unify(name), _conf(cell.words, ctx.method.get(1, "text")),
                      ctx.src(line, cell.words))
    for line in reversed(ctx.lines):
        m = re.match(r"(?i)^for\s+(.{4,80})$", line.cells[-1].text.strip())
        if m and T.name_similarity(m.group(1), buyer_name) < 0.8:
            return _field("vendor_name", T.unify(m.group(1)), 0.7, ctx.src(line, line.cells[-1].words), loose=True)
    return None


# --------------------------------------------------------------------------- reconciliation

def _pick(cands: list, prefer: Optional[Decimal] = None):
    if not cands:
        return None
    if prefer is not None:
        for c in cands:
            if _close(c[0], prefer):
                return c
    return cands[0]


def _money_field(ctx: _Ctx, name: str, cand, note: Optional[str] = None) -> FieldValue:
    value, word, line = cand
    method = ctx.method.get(line.page, "text")
    return _field(name, f"{T.q2(value)}", _conf([word], method), ctx.src(line, [word]),
                  notes=[note] if note else [])


def _derived(name: str, value: Decimal, how: str, confidence: float = 0.9) -> FieldValue:
    return _field(name, f"{T.q2(value)}", confidence, SourceRef(snippet=how, method="derived"), notes=[how])


def _digits_agree(raw: str, expected: Decimal, slack: int = 1) -> bool:
    """Same digits, give or take one slip: '12,182532' against 12182.32."""
    return T.edit_distance(T.digits_only(raw), T.digits_only(f"{T.q2(expected)}")) <= slack


def _restore_scan_slips(ctx: _Ctx, inv: Invoice, line_sum: Optional[Decimal], tax_sum: Decimal,
                        round_off: Decimal, tot: dict) -> None:
    """On a scanned page one printed figure may be garbled (a dot read as a digit). If the
    other figures fix what it must be, and its digits agree with that value to within one
    slip, the arithmetic value is used and the correction is recorded on the field."""
    f = inv.fields

    def scanned(name: str) -> bool:
        return name in f and f[name].source is not None and f[name].source.method == "ocr"

    subtotal, total = inv.money("subtotal"), inv.money("total")
    if scanned("subtotal") and line_sum and subtotal is not None and not _close(subtotal, line_sum) \
            and _digits_agree(f["subtotal"].source.snippet.split()[-1], line_sum):
        raw = f["subtotal"].value
        f["subtotal"].value = f"{T.q2(line_sum)}"
        f["subtotal"].notes.append(f"scan read {raw}; the line amounts add up to {T.q2(line_sum)} and the digits agree, so that value is used")
        f["subtotal"].confidence = 0.85
        subtotal = line_sum
    if scanned("total") and subtotal is not None and tax_sum and total is not None:
        expected = subtotal + tax_sum + round_off
        raw_word = next((c[1].text for c in tot["grand"] + tot["generic"] if T.q2(c[0]) == T.q2(total)), "")
        if not _close(total, expected) and raw_word and _digits_agree(raw_word, expected):
            f["total"].value = f"{T.q2(expected)}"
            f["total"].notes.append(f"scan read '{raw_word}'; taxable value plus tax is {T.q2(expected)} and the digits agree, so that value is used")
            f["total"].confidence = 0.85


def _reconcile(ctx: _Ctx, inv: Invoice, table: Table, tot: dict) -> None:
    """Decide which printed figures are the taxable value, the taxes and the total, using
    arithmetic, then convert tax-inclusive lines to pre-tax."""
    f = inv.fields
    items = inv.line_items
    line_sum = sum((it.amount for it in items if it.amount is not None), ZERO) if items else None
    taxes: dict[str, Decimal] = {}
    rates: list[Decimal] = []
    for kind in ("cgst", "sgst", "igst"):
        found = tot.get(kind)
        if found:
            taxes[kind] = found["amount"]
            f[kind] = _money_field(ctx, kind, (found["amount"], found["word"], found["line"]))
            if found["rate"] is not None:
                rates.append(found["rate"])
    if "table_tax" in tot and not taxes:
        value, word, line = tot["table_tax"]
        taxes["igst"] = value
        f["igst"] = _money_field(ctx, "igst", (value, word, line))
    tax_sum = sum(taxes.values(), ZERO)
    round_off = tot["round_off"][0] if tot.get("round_off") else ZERO

    grand = _pick(tot["grand"])
    generic = list(tot["generic"])
    if grand is None and generic:
        grand = max(generic, key=lambda c: c[0])
        generic = [g for g in generic if g is not grand]
    if grand is not None:
        f["total"] = _money_field(ctx, "total", grand)
    total = grand[0] if grand else None

    subtotal_cand = _pick(tot["subtotal"], line_sum if not inv.tax_inclusive else None)
    if subtotal_cand is None and generic and not inv.tax_inclusive:
        subtotal_cand = _pick([g for g in generic if line_sum is not None and _close(g[0], line_sum)])
    if inv.tax_inclusive:
        rate = (sum(rates, ZERO) if len(rates) > 1 or "igst" not in taxes else rates[0]) if rates else None
        if rate is None:
            m = re.search(r"(?i)gst\s*@?\s*(\d{1,2}(?:\.\d+)?)\s*%", ctx.doc.text)
            rate = Decimal(m.group(1)) if m else None
        if subtotal_cand is not None:
            f["subtotal"] = _money_field(ctx, "subtotal", subtotal_cand)
        elif total is not None and tax_sum:
            f["subtotal"] = _derived("subtotal", total - tax_sum, "total minus the tax amounts printed")
        elif total is not None and rate:
            f["subtotal"] = _derived("subtotal", total / (1 + rate / 100), f"total divided by 1.{int(rate):02d}", 0.85)
        if rate is None and inv.money("subtotal") and tax_sum:
            rate = T.q2(tax_sum / inv.money("subtotal") * 100)
        if rate:
            factor = 1 + rate / 100
            for it in items:
                it.tax_rate = it.tax_rate or rate
                if it.unit_price is not None:
                    it.unit_price_pre_tax = T.q2(it.unit_price / factor)
                if it.amount is not None:
                    it.amount_pre_tax = T.q2(it.amount / factor)
            inv.notes.append(f"Prices include GST at {rate:g}%; converted to pre-tax by dividing by {factor:g}.")
            f["tax_rate"] = _derived("tax_rate", rate, "rate stated on the invoice", 0.95)
        if not taxes and inv.money("subtotal") is not None and total is not None:
            f["tax_total"] = _derived("tax_total", total - inv.money("subtotal"), "total minus taxable value")
    else:
        if subtotal_cand is not None:
            f["subtotal"] = _money_field(ctx, "subtotal", subtotal_cand)
        elif line_sum is not None and total is not None and _close(line_sum + tax_sum + round_off, total):
            f["subtotal"] = _derived("subtotal", line_sum, "sum of the line amounts (no subtotal printed)")
        elif line_sum is not None and items and total is None:
            f["subtotal"] = _derived("subtotal", line_sum, "sum of the line amounts (no subtotal printed)", 0.8)
        for it in items:
            it.unit_price_pre_tax, it.amount_pre_tax = it.unit_price, it.amount
        taxable = inv.money("subtotal")
        if rates:
            rate = sum(rates, ZERO) if "igst" not in taxes else rates[0]
            f["tax_rate"] = _derived("tax_rate", rate, "rate printed beside the tax lines", 0.97)
        elif taxable and tax_sum:
            f["tax_rate"] = _derived("tax_rate", T.q2(tax_sum / taxable * 100), "tax divided by taxable value", 0.9)
        elif items and all(it.tax_rate is not None for it in items) and len({it.tax_rate for it in items}) == 1:
            f["tax_rate"] = _derived("tax_rate", items[0].tax_rate, "rate printed on the lines", 0.95)
    if taxes:
        f["tax_total"] = _derived("tax_total", tax_sum, "sum of the tax lines", 0.97)
    if not inv.tax_inclusive:
        _restore_scan_slips(ctx, inv, line_sum, tax_sum, round_off, tot)
    if round_off:
        inv.notes.append(f"Round-off of {round_off} printed on the invoice.")

    # a gross 'Total' column next to 'Taxable Value' on the lines: keep the pre-tax figure
    taxable = inv.money("subtotal")
    # repair a single misread number on a line when the other two and the subtotal agree
    amounts_trusted = taxable is not None and line_sum is not None and \
        _close(sum((it.amount_pre_tax or ZERO for it in items), ZERO), taxable)
    for it in items:
        if "line_math_mismatch" not in it.flags or it.amount is None or not amounts_trusted:
            continue
        price_fix = qty_fix = None
        if it.qty and it.qty > 0:
            implied = it.amount / it.qty
            if implied == T.q2(implied):
                price_fix = T.q2(implied)
        if it.unit_price and it.unit_price > 0:
            implied = it.amount / it.unit_price
            if implied > 0 and implied == implied.to_integral_value():
                qty_fix = implied
        if price_fix is not None and qty_fix is None:
            it.notes.append(f"Unit price was read as {it.unit_price}; amount / quantity gives {price_fix}, and "
                            f"the line amounts add up to the taxable value, so the price was corrected.")
            it.unit_price = price_fix
            factor = (1 + (it.tax_rate or ZERO) / 100) if inv.tax_inclusive else Decimal(1)
            it.unit_price_pre_tax = T.q2(it.unit_price / factor)
            it.flags.remove("line_math_mismatch")
            it.flags.append("price_corrected_by_arithmetic")
            it.confidence = min(it.confidence, 0.85)
        elif qty_fix is not None and price_fix is None:
            it.notes.append(f"Quantity was read as {it.qty}; amount / unit price gives {qty_fix:f}, so the "
                            f"quantity was corrected.")
            it.qty = qty_fix
            it.flags.remove("line_math_mismatch")
            it.flags.append("qty_corrected_by_arithmetic")
            it.confidence = min(it.confidence, 0.85)
        elif price_fix is not None and qty_fix is not None:
            it.notes.append(f"Quantity {it.qty} x price {it.unit_price} does not give {it.amount}. Either the "
                            f"quantity is {qty_fix:f} or the price is {price_fix}; the invoice alone cannot say which.")

    if amounts_trusted and any(p.method == "ocr" for p in ctx.doc.pages):
        for it in items:
            if it.amount is None or "line_math_mismatch" in it.flags:
                continue
            if it.qty is None and it.unit_price:
                implied = it.amount / it.unit_price
                if implied > 0 and implied == implied.to_integral_value():
                    it.qty = implied
                    it.flags.append("qty_derived_by_arithmetic")
                    it.notes.append(f"Quantity was not legible; amount / unit price gives {implied:f}.")
                    it.confidence = min(it.confidence, 0.8)
            elif it.unit_price is None and it.qty:
                implied = it.amount / it.qty
                if implied == T.q2(implied):
                    it.unit_price = it.unit_price_pre_tax = T.q2(implied)
                    it.flags.append("price_derived_by_arithmetic")
                    it.notes.append(f"Unit price was not legible; amount / quantity gives {T.q2(implied)}.")
                    it.confidence = min(it.confidence, 0.8)

    # confidence rests on cross-checks: when the sums close, the amounts were read right
    total = inv.money("total")
    tax_total = inv.money("tax_total") or ZERO
    if taxable is not None and total is not None and _close(taxable + tax_total + round_off, total):
        for name in ("subtotal", "total", "cgst", "sgst", "igst", "tax_total"):
            if name in f and f[name].present:
                f[name].confidence = max(f[name].confidence, 0.97)
                if "confirmed by arithmetic" not in f[name].notes:
                    f[name].notes.append("confirmed by arithmetic")
        if amounts_trusted:
            for it in items:
                if "line_math_mismatch" not in it.flags:
                    it.confidence = max(it.confidence, 0.95 if not any("corrected" in x for x in it.flags) else 0.85)


# --------------------------------------------------------------------------- entry point

def extract_invoice(doc: DocRead, buyer_gstin: str = "", buyer_name: str = "",
                    vendor_gstins: Optional[dict[str, str]] = None,
                    known_po_numbers: Optional[list[str]] = None) -> Invoice:
    """Extract one invoice. `vendor_gstins` (GSTIN -> vendor id) and `known_po_numbers`
    come from master data; they let a scanned GSTIN or PO number that is one character
    off be recognised, and an unlabelled PO number in running text be spotted."""
    ctx = _Ctx(doc)
    inv = Invoice()
    f = inv.fields
    if not ctx.lines:
        inv.doc_type, inv.doc_type_evidence = "not_invoice", "no readable text"
        return inv
    inv.doc_type, inv.doc_type_evidence = detect_doc_type(ctx)

    # GSTINs: ours and the vendor's
    known = {buyer_gstin: "buyer"} if buyer_gstin else {}
    known.update(vendor_gstins or {})
    inv.gstin_candidates = _gstin_candidates(ctx, known)
    for cand in inv.gstin_candidates:
        src = SourceRef(page=cand["page"], snippet=cand["snippet"], bbox=cand["bbox"],
                        method=ctx.method.get(cand["page"], "text"))
        notes = [f"read as {cand['raw']}; matched after correcting scan slips"] if cand["corrected"] else []
        if cand["tag"] == "buyer":
            f.setdefault("buyer_gstin", _field("buyer_gstin", cand["gstin"], cand["conf"], src, notes=notes))
        elif "vendor_gstin" not in f:
            f["vendor_gstin"] = _field("vendor_gstin", cand["gstin"], cand["conf"] if cand["valid"] else 0.5,
                                       src, notes=notes + ([] if cand["valid"] else ["check character does not match"]))

    name = _vendor_name(ctx, buyer_name)
    if name:
        f["vendor_name"] = name

    for field_name, kind in (("invoice_number", "id"), ("invoice_date", "date"), ("po_reference", "id")):
        cand = _find_labelled(ctx, field_name, kind)
        if cand is not None:
            method = ctx.method.get(cand.line.page, "text")
            f[field_name] = _field(field_name, cand.value, _conf(cand.words, method, cand.loose, cand.below),
                                   ctx.src(cand.line, cand.words), loose=cand.loose)
            if kind == "id" and cand.raw and cand.raw != cand.value:
                f[field_name].notes.append(f"read as {cand.raw}; '$' taken as 'S'")
                f[field_name].confidence = min(f[field_name].confidence, 0.6)

    # a PO number from the register that appears anywhere in the text counts as quoted
    if known_po_numbers:
        po_keys = {T.confusable_key(p): p for p in known_po_numbers}
        current = f.get("po_reference")
        if current is None or T.confusable_key(str(current.value)) not in po_keys:
            for line in ctx.lines:
                for w in line.words:
                    key = T.confusable_key(w.text.strip(".,;:()"))
                    if key in po_keys and current is None:
                        exact = w.text.strip(".,;:()").upper() == po_keys[key]
                        f["po_reference"] = _field(
                            "po_reference", po_keys[key], _conf([w], ctx.method.get(line.page, "text")) - 0.02,
                            ctx.src(line, [w]),
                            notes=["found in running text, not beside a PO label"] +
                                  ([] if exact else [f"read as {w.text}; matched to the PO register"]))
                        current = f["po_reference"]
        elif str(current.value).upper() != po_keys[T.confusable_key(str(current.value))]:
            current.notes.append(f"read as {current.value}; matched to the PO register")
            current.value = po_keys[T.confusable_key(str(current.value))]

    inv.tax_inclusive = bool(INCLUSIVE_RE.search(doc.text))
    table = _find_table(ctx)
    inv.tax_inclusive = inv.tax_inclusive or table.inclusive_header
    inv.line_items = _build_items(ctx, table)
    totals = _find_totals(ctx, table)
    _reconcile(ctx, inv, table, totals)

    if totals.get("words"):
        text, line, words = totals["words"]
        value = T.words_to_amount(text)
        f["amount_in_words"] = _field("amount_in_words", f"{value}" if value is not None else None,
                                      _conf(words, ctx.method.get(line.page, "text")) if value is not None else 0.0,
                                      ctx.src(line, words),
                                      missing=None if value is not None else "the words could not be read as a number")
    _bank_details(ctx, f)

    codes = re.findall(r"\b(USD|EUR|GBP|AED|SGD)\b", doc.text)
    symbols = re.findall(r"([$€£])\s?\d[\d,]*(?:\.\d+)?", doc.text)
    foreign = codes[0] if codes else None
    if foreign is None and len(symbols) >= (2 if doc.is_scanned else 1):   # on a scan a lone '$' is often a misread 'S'
        foreign = {"$": "USD", "€": "EUR", "£": "GBP"}[symbols[0]]
    f["currency"] = _field("currency", foreign or "INR", 0.9 if foreign else 0.95,
                           SourceRef(snippet=f"currency marker {foreign} on the document" if foreign
                                     else "no foreign currency marker; rupees assumed",
                                     method="text" if foreign else "derived"))

    reasons = {
        "invoice_number": "no invoice number found beside an invoice or bill number label",
        "invoice_date": "no invoice date found",
        "vendor_gstin": "no supplier GSTIN found",
        "buyer_gstin": "our GSTIN does not appear on the document",
        "po_reference": "no PO number quoted",
        "total": "no invoice total found beside a total label",
        "subtotal": "no taxable value could be established",
        "vendor_name": "no supplier name found at the top of the page",
        "bank_account": "no bank account printed",
        "bank_ifsc": "no IFSC printed",
    }
    if "invoice_number" not in f and any(match_label(cell, LABELS["invoice_number"][0], ctx.fuzzy(line))
                                         for line in ctx.lines for cell in line.cells):
        reasons["invoice_number"] = "the invoice number is left blank on the document"
    for field_name, reason in reasons.items():
        if field_name not in f:
            f[field_name] = _field(field_name, None, 0.0, None, missing=reason)
    for field_name, value in f.items():
        value.label = FIELD_LABELS.get(field_name, field_name)
    return inv
