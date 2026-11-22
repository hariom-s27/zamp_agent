"""Stage 2, Read: turn a PDF into lines of positioned words, page by page.

Per page (the triage rule is adapted from my outcome project's pdf_reader.py):
  * enough characters in the text layer  -> read the words with pdfplumber
  * almost no characters and a page-sized picture -> it is a scan: render it, straighten
    it, and OCR it with Tesseract (or replay recorded OCR output when no engine exists)
Both paths end in the same structure (Word -> Line -> Cell in page points), so every
later stage is identical for text PDFs and scans.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import config

SCAN_MAX_CHARS = 50          # fewer text-layer characters than this ...
SCAN_MIN_IMAGE_SHARE = 0.5   # ... and a picture over half the page = a scan
MAX_PAGES = 20
MAX_BYTES = 15 * 1024 * 1024
OCR_DPI = 300
OCR_PSM = 6            # the page is re-laid out as one column before reading
_PUNCT_ONLY = re.compile(r"^[\W_]+$")


class ReaderError(Exception):
    """The file cannot be processed at all. `code` becomes the reason code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    conf: Optional[float] = None   # OCR confidence 0..100; None for text-layer words

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 0.1)

    @property
    def width(self) -> float:
        return max(self.x1 - self.x0, 0.1)


@dataclass
class Cell:
    """Words on one line with only ordinary spaces between them. A wide gap starts a new
    cell, which is how columns and label/value pairs show up on a page."""
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return self.words[0].x0

    @property
    def x1(self) -> float:
        return self.words[-1].x1

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)

    @property
    def bbox(self) -> list[float]:
        return [round(self.x0, 1), round(self.top, 1), round(self.x1, 1), round(self.bottom, 1)]

    @property
    def conf(self) -> Optional[float]:
        values = [w.conf for w in self.words if w.conf is not None]
        return sum(values) / len(values) if values else None


@dataclass
class Line:
    page: int
    index: int
    words: list[Word]
    cells: list[Cell] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return self.words[0].x0

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)

    @property
    def yc(self) -> float:
        return statistics.fmean(w.yc for w in self.words)

    @property
    def bbox(self) -> list[float]:
        return [round(self.x0, 1), round(self.top, 1), round(self.x1, 1), round(self.bottom, 1)]


@dataclass
class PageRead:
    number: int
    kind: str                 # text | scan | scan_unread | empty
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)
    chars: int = 0
    image_share: float = 0.0
    ocr: Optional[dict] = None   # engine, source (live|recorded), angle, mean_conf, seconds
    note: str = ""

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def method(self) -> str:
        return "ocr" if self.kind == "scan" else "text"


@dataclass
class DocRead:
    sha256: str
    size_bytes: int
    pages: list[PageRead]
    variant: str = "gray"        # how scanned pages were treated before OCR: 'gray' or 'bw'

    @property
    def lines(self) -> list[Line]:
        return [line for page in self.pages for line in page.lines]

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    @property
    def is_scanned(self) -> bool:
        return any(p.kind in ("scan", "scan_unread") for p in self.pages)

    def page(self, number: int) -> Optional[PageRead]:
        return next((p for p in self.pages if p.number == number), None)


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# --------------------------------------------------------------------------- words -> lines -> cells

def split_cells(words: list[Word]) -> list[Cell]:
    """Split one line into cells at gaps wider than ordinary word spacing. The limit is
    1.8 average character widths, so it adapts to the font (proportional or typewriter)."""
    chars = sum(len(w.text) for w in words)
    char_w = sum(w.width for w in words) / max(chars, 1)
    limit = max(5.5, 1.8 * char_w)
    cells, current = [], [words[0]]
    for w in words[1:]:
        if w.x0 - current[-1].x1 > limit:
            cells.append(Cell(current))
            current = [w]
        else:
            current.append(w)
    cells.append(Cell(current))
    return cells


def build_lines(words: list[Word], page: int, start_index: int = 0) -> list[Line]:
    """Group words into lines by vertical position, left to right, then into cells."""
    words = [w for w in words if w.text.strip()]
    if not words:
        return []
    words.sort(key=lambda w: (w.yc, w.x0))
    groups: list[list[Word]] = [[words[0]]]
    centre = words[0].yc
    for w in words[1:]:
        ref_h = statistics.median(x.height for x in groups[-1])
        if abs(w.yc - centre) <= max(2.5, 0.45 * min(w.height, ref_h)):
            groups[-1].append(w)
            centre = statistics.fmean(x.yc for x in groups[-1])
        else:
            groups.append([w])
            centre = w.yc
    lines = []
    for i, group in enumerate(groups):
        group.sort(key=lambda w: w.x0)
        lines.append(Line(page=page, index=start_index + i, words=group, cells=split_cells(group)))
    return lines


# --------------------------------------------------------------------------- OCR

def find_tesseract() -> Optional[str]:
    """TESSERACT_CMD, then PATH, then the usual Windows install folders."""
    override = config.env("TESSERACT_CMD")
    if override and Path(override).exists():
        return override
    found = shutil.which("tesseract")
    if found:
        return found
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


class OcrEngine:
    """Tesseract through pytesseract. `available` is False when the binary is missing."""

    def __init__(self) -> None:
        self.cmd = find_tesseract()
        self.version = ""
        self.available = False
        if not self.cmd:
            return
        # Tesseract starts several OpenMP threads by default. On a laptop or a small cloud
        # host they compete with each other and a page takes two to three times longer,
        # so one thread is the default here. Set OMP_THREAD_LIMIT yourself to change it.
        if not os.environ.get("OMP_THREAD_LIMIT"):
            os.environ["OMP_THREAD_LIMIT"] = "1"
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = self.cmd
            self.version = str(pytesseract.get_tesseract_version()).split()[0]
            self.available = True
        except Exception:
            self.available = False

    @property
    def label(self) -> str:
        return f"Tesseract {self.version}" if self.available else "not installed"

    def words_px(self, image) -> list[Word]:
        """OCR one image as a single column of text. Boxes are in image pixels."""
        import pytesseract
        data = pytesseract.image_to_data(image, lang="eng", config=f"--psm {OCR_PSM} --dpi {OCR_DPI}",
                                         output_type=pytesseract.Output.DICT)
        out: list[Word] = []
        for i, raw in enumerate(data["text"]):
            text = (raw or "").strip().strip("|").strip()
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            if not text or conf < 0 or (_PUNCT_ONLY.match(text) and text != "#"):
                continue  # layout boxes, table rules read as bars, dust
            left, top = float(data["left"][i]), float(data["top"][i])
            out.append(Word(text, left, left + data["width"][i], top, top + data["height"][i], conf))
        return out


def page_blocks(ink) -> list[tuple[int, int, int, int]]:
    """Split a page into blocks of text at wide blank bands and gutters (a recursive
    'XY cut'). `ink` is a boolean array, True where the page is dark. Returns boxes as
    (x0, y0, x1, y1). An invoice header with the vendor on the left and the invoice
    details on the right becomes two blocks, so their text lines cannot get mixed up."""
    import numpy as np
    band_gap, gutter, speck = 22, 28, 3      # pixels at about 100 dpi
    out: list[tuple[int, int, int, int]] = []

    def widest_gap(profile, minimum: int):
        best_len, best = 0, None
        start = None
        for i, v in enumerate(profile):
            if v <= speck:
                if start is None:
                    start = i
            else:
                if start is not None and start > 0 and i - start > best_len:
                    best_len, best = i - start, (start, i)
                start = None
        return (best_len, best) if best_len >= minimum else (0, None)

    def cut(x0: int, y0: int, x1: int, y1: int, depth: int) -> None:
        sub = ink[y0:y1, x0:x1]
        rows = np.where(sub.sum(axis=1) > speck)[0]
        cols = np.where(sub.sum(axis=0) > speck)[0]
        if len(rows) == 0 or len(cols) == 0:
            return
        x0, x1, y0, y1 = x0 + int(cols[0]), x0 + int(cols[-1]) + 1, y0 + int(rows[0]), y0 + int(rows[-1]) + 1
        sub = ink[y0:y1, x0:x1]
        h_len, h_gap = widest_gap(sub.sum(axis=1), band_gap)
        v_len, v_gap = widest_gap(sub.sum(axis=0), gutter)
        if depth >= 8 or (h_gap is None and v_gap is None):
            out.append((x0, y0, x1, y1))
        elif v_gap is not None and (h_gap is None or v_len >= h_len):
            cut(x0, y0, x0 + v_gap[0], y1, depth + 1)
            cut(x0 + v_gap[1], y0, x1, y1, depth + 1)
        else:
            cut(x0, y0, x1, y0 + h_gap[0], depth + 1)
            cut(x0, y0 + h_gap[1], x1, y1, depth + 1)

    cut(0, 0, ink.shape[1], ink.shape[0], 0)
    return out


def ocr_image(engine: "OcrEngine", image, px_per_point: float = OCR_DPI / 72.0,
              variant: str = "gray") -> tuple[list[Word], int]:
    """OCR a straightened page: cut it into blocks, lay the blocks out one under another
    on a fresh sheet, read that sheet in one pass, and map every word back to where it
    was on the page. Returns (words in page points, number of blocks)."""
    import numpy as np
    from PIL import Image
    gray = image.convert("L")
    small = gray.resize((max(gray.width // 3, 1), max(gray.height // 3, 1)), Image.BOX)
    boxes = [(x0 * 3, y0 * 3, x1 * 3, y1 * 3) for x0, y0, x1, y1 in page_blocks(np.asarray(small) < 185)]
    boxes = [b for b in boxes if b[2] - b[0] >= 20 and b[3] - b[1] >= 12]
    scale = 1.0 / px_per_point
    treat = binarise if variant == "bw" else (lambda picture: picture)
    if len(boxes) <= 1:
        words = engine.words_px(treat(gray))
        offsets = None
    else:
        boxes.sort(key=lambda b: (b[1], b[0]))
        pad, gap, margin = 40, 70, 8
        crops = []
        for x0, y0, x1, y1 in boxes:
            cx0, cy0 = max(x0 - margin, 0), max(y0 - margin, 0)
            crops.append((gray.crop((cx0, cy0, min(x1 + margin, gray.width), min(y1 + margin, gray.height))), cx0, cy0))
        sheet = Image.new("L", (max(c.width for c, _, _ in crops) + 2 * pad,
                                sum(c.height + gap for c, _, _ in crops) + gap), 255)
        offsets, y = [], gap
        for crop, cx0, cy0 in crops:
            sheet.paste(crop, (pad, y))
            offsets.append((y, y + crop.height, cx0 - pad, cy0 - y))
            y += crop.height + gap
        words = engine.words_px(treat(sheet))
    out: list[Word] = []
    for w in words:
        dx = dy = 0.0
        if offsets is not None:
            slot = next((o for o in offsets if o[0] - 20 <= w.yc <= o[1] + 20), None)
            if slot is None:
                continue
            dx, dy = slot[2], slot[3]
        out.append(Word(w.text, round((w.x0 + dx) * scale, 1), round((w.x1 + dx) * scale, 1),
                        round((w.top + dy) * scale, 1), round((w.bottom + dy) * scale, 1), w.conf))
    return out, max(len(boxes), 1)


_ENGINE: Optional[OcrEngine] = None


def get_engine(refresh: bool = False) -> OcrEngine:
    global _ENGINE
    if _ENGINE is None or refresh:
        _ENGINE = OcrEngine()
    return _ENGINE


def estimate_skew(image) -> float:
    """Angle in degrees that makes the text rows horizontal, found by trying small
    rotations of a shrunken black-and-white copy and keeping the one whose row totals
    are the most uneven (crisp rows of text give peaks and gaps)."""
    import numpy as np
    from PIL import Image

    small = image.convert("L")
    small.thumbnail((700, 1000))
    ink = Image.fromarray(((np.asarray(small) < 150) * 255).astype("uint8"))

    def score(angle: float) -> float:
        rows = np.asarray(ink.rotate(angle, resample=Image.BILINEAR, fillcolor=0), dtype="float32").sum(axis=1)
        return float(((rows[1:] - rows[:-1]) ** 2).sum())

    coarse = [a / 4 for a in range(-12, 13)]            # -3.0 .. 3.0 in 0.25 steps
    best = max(coarse, key=score)
    fine = [best + d / 20 for d in range(-5, 6)]        # +-0.25 in 0.05 steps
    return round(max(fine, key=score), 2)


OCR_VARIANTS = {"gray": "greyscale", "bw": "black and white"}


def binarise(image):
    """Reduce a greyscale image to pure black and white at Otsu's threshold (the level
    that best separates ink from paper). Sensor noise and paper texture disappear, which
    changes what the OCR engine sees; that is the point of the second read."""
    import numpy as np
    from PIL import Image
    pixels = np.asarray(image.convert("L"))
    hist = np.bincount(pixels.ravel(), minlength=256).astype(np.float64)
    weight_dark = np.cumsum(hist)
    weight_light = pixels.size - weight_dark
    sum_dark = np.cumsum(hist * np.arange(256))
    with np.errstate(divide="ignore", invalid="ignore"):
        between = weight_dark * weight_light * (sum_dark / weight_dark - (sum_dark[-1] - sum_dark) / weight_light) ** 2
    threshold = int(np.argmax(np.nan_to_num(between)))
    return Image.fromarray(np.where(pixels > threshold, 255, 0).astype(np.uint8))


def _fixture_path(sha256: str, page: int, variant: str = "gray") -> Path:
    suffix = "" if variant == "gray" else f"_{variant}"
    return config.FIXTURES_DIR / "ocr" / f"{sha256[:16]}_p{page}{suffix}.json"


def load_ocr_fixture(sha256: str, page: int, variant: str = "gray") -> Optional[dict]:
    path = _fixture_path(sha256, page, variant)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_ocr_fixture(sha256: str, page: int, payload: dict, variant: str = "gray") -> Path:
    path = _fixture_path(sha256, page, variant)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return path


MAX_RENDER_PX = 5200        # longest side of a rendered page (A3 at 300 dpi is 4961)


def safe_scale(pdf_page, scale: float) -> float:
    """The requested scale, reduced for pages with outsized dimensions so that one
    crafted page cannot ask for a multi-gigabyte bitmap."""
    width, height = pdf_page.get_size()
    return min(scale, MAX_RENDER_PX / max(width, height, 1.0))


def _render(pdf_page, scale: float, grayscale: bool = True):
    bitmap = pdf_page.render(scale=scale, grayscale=grayscale)
    try:
        return bitmap.to_pil().copy()
    finally:
        bitmap.close()


def _ocr_page(pdf_page, sha256: str, number: int, mode: str, record: bool,
              variant: str = "gray") -> tuple[list[Word], Optional[dict]]:
    """OCR one scanned page. Returns (words, info); info is None when nothing could read it."""
    engine = get_engine()
    fixture = load_ocr_fixture(sha256, number, variant)
    use_fixture = fixture is not None and (mode == "fixture" or (mode == "auto" and not engine.available))
    if use_fixture:
        words = [Word(t, x0, x1, top, bottom, conf) for t, x0, top, x1, bottom, conf in fixture["words"]]
        return words, {"engine": fixture.get("engine", "Tesseract"), "source": "recorded", "variant": variant,
                       "angle": fixture.get("angle", 0.0), "mean_conf": fixture.get("mean_conf"),
                       "seconds": 0.0, "recorded_on": fixture.get("recorded_on", "")}
    if not engine.available:
        return [], None
    from PIL import Image
    started = time.perf_counter()
    px_per_point = safe_scale(pdf_page, OCR_DPI / 72.0)
    image = _render(pdf_page, px_per_point)
    angle = estimate_skew(image)
    if abs(angle) >= 0.1:
        image = image.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
    words, blocks = ocr_image(engine, image, px_per_point, variant)
    confs = [w.conf for w in words if w.conf is not None]
    mean_conf = round(sum(confs) / len(confs), 1) if confs else 0.0
    info = {"engine": engine.label, "source": "live", "variant": variant, "angle": angle, "mean_conf": mean_conf,
            "blocks": blocks, "seconds": round(time.perf_counter() - started, 2)}
    if record:
        save_ocr_fixture(sha256, number, {
            "sha256": sha256, "page": number, "variant": variant, "engine": engine.label, "dpi": OCR_DPI,
            "angle": angle, "mean_conf": mean_conf, "recorded_on": config.now_utc().isoformat(),
            "words": [[w.text, w.x0, w.top, w.x1, w.bottom, w.conf] for w in words]}, variant)
    return words, info


# --------------------------------------------------------------------------- the reader

def _image_share(pdf_page, raw) -> float:
    """Share of the page area covered by pictures."""
    width, height = pdf_page.get_size()
    area = max(width * height, 1.0)
    covered = 0.0
    try:
        for obj in pdf_page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=4):
            getter = getattr(obj, "get_bounds", None) or getattr(obj, "get_pos")
            left, bottom, right, top = getter()
            covered += max(0.0, min(right, width) - max(left, 0.0)) * max(0.0, min(top, height) - max(bottom, 0.0))
    except Exception:
        return 0.0
    return min(1.0, covered / area)


def read_pdf(content: bytes, record_ocr: bool = False, ocr_mode: Optional[str] = None,
             variant: str = "gray") -> DocRead:
    """Read a PDF into pages of lines. Raises ReaderError for files that cannot be
    processed (not a PDF, damaged, password-protected, too large). `variant` chooses how a
    scanned page is treated before OCR ('gray' or 'bw'); it makes no difference to text pages."""
    if not content:
        raise ReaderError("UNREADABLE_FILE", "The file is empty (0 bytes).")
    if len(content) > MAX_BYTES:
        raise ReaderError("FILE_TOO_LARGE", f"The file is {len(content) / 1048576:.1f} MB; the limit for "
                                             f"automatic processing is {MAX_BYTES // 1048576} MB.")
    if b"%PDF-" not in content[:1024]:
        raise ReaderError("UNREADABLE_FILE", "This is not a PDF file (no PDF header found).")
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_raw
    try:
        pdf = pdfium.PdfDocument(content)
    except Exception as exc:
        text = str(exc).lower()
        if "password" in text:
            raise ReaderError("UNREADABLE_FILE", "The PDF is password-protected and cannot be opened.") from exc
        raise ReaderError("UNREADABLE_FILE", "The PDF is damaged and cannot be opened.") from exc
    mode = ocr_mode or config.ocr_mode()
    sha = sha256_of(content)
    pages: list[PageRead] = []
    plumber = None
    try:
        count = len(pdf)
        if count == 0:
            raise ReaderError("UNREADABLE_FILE", "The PDF has no pages.")
        if count > MAX_PAGES:
            raise ReaderError("FILE_TOO_LARGE", f"The PDF has {count} pages; the limit for automatic "
                                                 f"processing is {MAX_PAGES}.")
        import pdfplumber
        try:
            plumber = pdfplumber.open(io.BytesIO(content))
        except Exception:
            plumber = None
        index = 0
        for i in range(count):
            number = i + 1
            page = pdf[i]
            try:
                width, height = page.get_size()
                textpage = page.get_textpage()
                try:
                    plain = textpage.get_text_bounded() or ""
                finally:
                    textpage.close()
                chars = len("".join(plain.split()))
                share = _image_share(page, pdfium_raw)
                result = PageRead(number, "text", round(width, 1), round(height, 1), chars=chars,
                                  image_share=round(share, 2))
                if chars < SCAN_MAX_CHARS and share > SCAN_MIN_IMAGE_SHARE:
                    words, info = _ocr_page(page, sha, number, mode, record_ocr, variant)
                    if info is None:
                        result.kind = "scan_unread"
                        result.note = ("Scanned page: no OCR engine is installed and no recorded "
                                       "OCR output exists for this file.")
                    else:
                        result.kind = "scan"
                        result.ocr = info
                        result.lines = build_lines(words, number, index)
                elif chars < 8:
                    result.kind = "empty"
                    result.note = "Page has no text."
                else:
                    words = _text_words(plumber, i)
                    if not words:  # layout extraction failed: fall back to plain page text
                        words = _plain_words(plain, width, height)
                        result.note = "Layout could not be read; used plain page text."
                    result.lines = build_lines(words, number, index)
                index += len(result.lines)
                pages.append(result)
            finally:
                page.close()
    finally:
        if plumber is not None:
            plumber.close()
        pdf.close()
    return DocRead(sha256=sha, size_bytes=len(content), pages=pages, variant=variant)


def _text_words(plumber, page_index: int) -> list[Word]:
    if plumber is None:
        return []
    try:
        page = plumber.pages[page_index]
        raw = page.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False, use_text_flow=False)
        return [Word(w["text"], round(w["x0"], 1), round(w["x1"], 1), round(w["top"], 1), round(w["bottom"], 1))
                for w in raw if w.get("text", "").strip()]
    except Exception:
        return []  # one bad page must not stop the run (rule kept from the outcome reader)


def _plain_words(plain: str, width: float, height: float) -> list[Word]:
    """Last resort when word boxes are unavailable: lines of text with made-up positions."""
    words: list[Word] = []
    rows = [r for r in plain.splitlines() if r.strip()]
    for row_no, row in enumerate(rows):
        top = 40 + row_no * 14
        x = 40.0
        for token in row.split():
            w = 5.0 * len(token)
            words.append(Word(token, x, x + w, top, top + 10))
            x += w + 3
    return words


def render_page_image(content: bytes, page_number: int, scale: float = 1.6, angle: float = 0.0):
    """PIL image of one page for the UI. `angle` is the straightening angle found at read
    time, so boxes computed on the straightened scan land in the right place."""
    import pypdfium2 as pdfium
    from PIL import Image
    pdf = pdfium.PdfDocument(content)
    try:
        page = pdf[page_number - 1]
        try:
            image = _render(page, safe_scale(page, scale), grayscale=False).convert("RGB")
        finally:
            page.close()
    finally:
        pdf.close()
    if abs(angle) >= 0.1:
        image = image.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    return image
