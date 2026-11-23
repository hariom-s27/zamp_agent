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
import logging
import os
import re
import shutil
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import config, straighten

# pdfminer (under pdfplumber) logs a warning for every font detail it cannot parse; a PDF
# printed from a browser produces dozens per page. They say nothing a user can act on.
logging.getLogger("pdfminer").setLevel(logging.ERROR)

SCAN_MAX_CHARS = 50          # fewer text-layer characters than this ...
SCAN_MIN_IMAGE_SHARE = 0.5   # ... and a picture over half the page = a scan
OCR_DPI = 300
OCR_PSM = 6            # a flatbed scan is re-laid out as one column before reading
# A photographed page is read differently (measured on the tune photographs of the robustness set):
PHOTO_PSM = 3          # first read: the engine's own analysis of the page layout
PHOTO_SECOND_PSM = 11  # second read: sparse-text mode, every piece of text wherever it is ...
PHOTO_SECOND_SCALE = 1.4   # ... on a picture this much larger, so the two reads do not share their mistakes
HOW_READ = {(False, "gray"): "greyscale, page re-laid out as one column",
            (False, "bw"): "black and white, page re-laid out as one column",
            (True, "gray"): "straightened photograph, engine's own page layout",
            (True, "bw"): "straightened photograph, enlarged 1.4x, sparse-text mode"}
_PUNCT_ONLY = re.compile(r"^[\W_]+$")
# A glyph the layout library could not turn into a character: a private-use code point,
# the replacement character, or pdfminer's '(cid:12)' placeholder.
UNDECODED = re.compile(r"[\ue000-\uf8ff\ufffd]|\(cid:\d+\)")


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

    def words_px(self, image, psm: int = OCR_PSM, dpi: int = OCR_DPI) -> list[Word]:
        """OCR one image (by default as a single column of text). Boxes are in image pixels."""
        import pytesseract
        data = pytesseract.image_to_data(image, lang="eng", config=f"--psm {psm} --dpi {dpi}",
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
              variant: str = "gray", photo: bool = False) -> tuple[list[Word], int]:
    """OCR a straightened page. Returns (words in page points, number of blocks).

    A flatbed scan: cut the page into blocks, lay the blocks out one under another on a
    fresh sheet, read that sheet in one pass, and map every word back to where it was on
    the page. The second read ('bw') does the same on a pure black-and-white picture.

    A photograph (after straighten.py): the first read lets the engine work out the page
    layout itself; the second is made as different from the first as one engine allows
    (a larger picture, sparse-text mode), so the two do not share their mistakes. A digit
    one read drops is rarely dropped again at another size and with other line finding."""
    import numpy as np
    from PIL import Image
    gray = image.convert("L")
    if photo:
        zoom = PHOTO_SECOND_SCALE if variant == "bw" else 1.0
        picture = gray if zoom == 1.0 else gray.resize((int(gray.width * zoom), int(gray.height * zoom)), Image.LANCZOS)
        found = engine.words_px(picture, psm=PHOTO_SECOND_PSM if variant == "bw" else PHOTO_PSM, dpi=int(OCR_DPI * zoom))
        k = 1.0 / (px_per_point * zoom)
        return [Word(w.text, round(w.x0 * k, 1), round(w.x1 * k, 1), round(w.top * k, 1), round(w.bottom * k, 1), w.conf)
                for w in found], 1
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


READABLE_WORDS, READABLE_CONF = 12, 60.0     # an OCR read with at least this many words at this mean confidence is taken as the right way up
_PREP: dict[tuple[str, int], dict] = {}      # how each scanned page was straightened, so its second read uses the same geometry


def _attempt(engine: "OcrEngine", image, px_per_point: float, variant: str, turns: int, photo: bool = False) -> dict:
    """One OCR read of a page after `turns` quarter turns (counter-clockwise) and fine straightening."""
    from PIL import Image
    if turns:
        image = image.rotate(90 * turns, expand=True, fillcolor=255)
    angle = estimate_skew(image)
    if abs(angle) >= 0.1:
        image = image.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
    if photo:
        image = straighten.clean_for_ocr(image)
    words, blocks = ocr_image(engine, image, px_per_point, variant, photo)
    confs = [w.conf for w in words if w.conf is not None]
    return {"turns": turns, "angle": angle, "words": words, "blocks": blocks, "size": image.size,
            "mean_conf": round(sum(confs) / len(confs), 1) if confs else 0.0}


def _readable(read: dict) -> bool:
    return len(read["words"]) >= READABLE_WORDS and read["mean_conf"] >= READABLE_CONF


def _ocr_page(pdf_page, sha256: str, number: int, mode: str, record: bool,
              variant: str = "gray", picture: Optional[list[float]] = None) -> tuple[list[Word], Optional[dict]]:
    """OCR one scanned page. Returns (words, info); info is None when nothing could read it.
    `picture` is where the scanned image sits on the PDF page when it does not fill it.

    A photograph is straightened first (straighten.py): the sheet is cut out of its
    background and its perspective undone, uneven light is divided out, and a page lying
    on its side or upside down is turned until it reads. A flatbed scan needs none of
    that and goes straight to the fine straightening, as it always did."""
    engine = get_engine()
    fixture = load_ocr_fixture(sha256, number, variant)
    use_fixture = fixture is not None and (mode == "fixture" or (mode == "auto" and not engine.available))
    if use_fixture:
        words = [Word(t, x0, x1, top, bottom, conf) for t, x0, top, x1, bottom, conf in fixture["words"]]
        return words, {"engine": fixture.get("engine", "Tesseract"), "source": "recorded", "variant": variant,
                       "angle": fixture.get("angle", 0.0), "mean_conf": fixture.get("mean_conf"),
                       "crop": fixture.get("crop"), "quad": fixture.get("quad"), "turns": fixture.get("turns", 0),
                       "lit": fixture.get("lit", False), "photo": bool(fixture.get("photo")),
                       "how": HOW_READ[(bool(fixture.get("photo")), variant)],
                       "width": fixture.get("width"), "height": fixture.get("height"),
                       "seconds": 0.0, "recorded_on": fixture.get("recorded_on", "")}
    if not engine.available:
        return [], None
    started = time.perf_counter()
    px_per_point = safe_scale(pdf_page, OCR_DPI / 72.0)
    image = _render(pdf_page, px_per_point)
    if picture:                 # the photograph, without the blank margins of the PDF page around it
        image = image.crop((int(picture[0] * image.width), int(picture[1] * image.height),
                            int(picture[2] * image.width), int(picture[3] * image.height)))
    known = _PREP.get((sha256, number))
    quad = known["quad"] if known else straighten.find_sheet(image)
    if quad:
        image = straighten.warp(image, quad)
    lit = straighten.uneven_light(image)
    if lit:
        image = straighten.even_light(image)
    photo = quad is not None or lit          # a camera, not a flatbed: a sheet on a background, or a shadow across the paper
    if known:
        best = _attempt(engine, image, px_per_point, variant, known["turns"], photo)
    else:
        # the likely orientation first; the others only if that read is not text
        order = [1, 3, 0] if straighten.runs_vertically(image) else [0, 2]
        best = None
        for turns in order:
            read = _attempt(engine, image, px_per_point, variant, turns, photo)
            if best is None or (_readable(read), read["mean_conf"]) > (_readable(best), best["mean_conf"]):
                best = read
            if _readable(read):
                break
        if len(_PREP) > 256:
            _PREP.clear()
        _PREP[(sha256, number)] = {"quad": quad, "turns": best["turns"]}
    words, angle, mean_conf = best["words"], best["angle"], best["mean_conf"]
    straightened = quad is not None or best["turns"] != 0 or bool(picture)
    info = {"engine": engine.label, "source": "live", "variant": variant, "angle": angle, "mean_conf": mean_conf,
            "blocks": best["blocks"], "crop": picture, "quad": quad, "turns": best["turns"], "lit": lit,
            "photo": photo, "how": HOW_READ[(photo, variant)],
            "width": round(best["size"][0] / px_per_point, 1) if straightened else None,
            "height": round(best["size"][1] / px_per_point, 1) if straightened else None,
            "seconds": round(time.perf_counter() - started, 2)}
    if record:
        save_ocr_fixture(sha256, number, {
            "sha256": sha256, "page": number, "variant": variant, "engine": engine.label, "dpi": OCR_DPI,
            "angle": angle, "mean_conf": mean_conf, "crop": picture, "quad": quad, "turns": best["turns"], "lit": lit,
            "photo": photo, "width": info["width"], "height": info["height"], "recorded_on": config.now_utc().isoformat(),
            "words": [[w.text, w.x0, w.top, w.x1, w.bottom, w.conf] for w in words]}, variant)
    return words, info


# --------------------------------------------------------------------------- the reader

def _pictures(pdf_page, raw) -> list[tuple[float, float, float, float]]:
    """Where the pictures sit on the page: (left, bottom, right, top) in points, clipped to the page."""
    width, height = pdf_page.get_size()
    out = []
    try:
        for obj in pdf_page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=4):
            getter = getattr(obj, "get_bounds", None) or getattr(obj, "get_pos")
            left, bottom, right, top = getter()
            out.append((max(left, 0.0), max(bottom, 0.0), min(right, width), min(top, height)))
    except Exception:
        return []
    return [box for box in out if box[2] > box[0] and box[3] > box[1]]


def _image_share(pdf_page, raw) -> float:
    """Share of the page area covered by pictures."""
    width, height = pdf_page.get_size()
    covered = sum((right - left) * (top - bottom) for left, bottom, right, top in _pictures(pdf_page, raw))
    return min(1.0, covered / max(width * height, 1.0))


def _picture_box(pdf_page, raw) -> Optional[list[float]]:
    """A scan whose picture does not fill the PDF page (a photo placed on an A4 page keeps
    its own shape, with blank bands beside it): the picture's place as fractions of the
    page, [x0, y0, x1, y1] from the top-left. None when the picture fills the page, or
    when there is more than one."""
    width, height = pdf_page.get_size()
    boxes = _pictures(pdf_page, raw)
    if len(boxes) != 1 or width <= 0 or height <= 0:
        return None
    left, bottom, right, top = boxes[0]
    box = [left / width, 1 - top / height, right / width, 1 - bottom / height]
    margins = (box[0], box[1], 1 - box[2], 1 - box[3])
    return [round(v, 4) for v in box] if max(margins) > 0.005 else None


def read_pdf(content: bytes, record_ocr: bool = False, ocr_mode: Optional[str] = None,
             variant: str = "gray", max_bytes: Optional[int] = None, max_pages: Optional[int] = None) -> DocRead:
    """Read a PDF into pages of lines. Raises ReaderError for files that cannot be
    processed (not a PDF, damaged, password-protected, too large). `variant` chooses how a
    scanned page is treated before OCR ('gray' or 'bw'); it makes no difference to text pages.
    The size and page limits come from rules.yaml (intake) unless the caller passes them."""
    if max_bytes is None or max_pages is None:
        from .rules import current_rules
        intake = current_rules().intake
        max_bytes = intake.max_bytes if max_bytes is None else max_bytes
        max_pages = intake.max_pages if max_pages is None else max_pages
    if not content:
        raise ReaderError("UNREADABLE_FILE", "The file is empty (0 bytes).")
    if len(content) > max_bytes:
        raise ReaderError("FILE_TOO_LARGE", f"The file is {len(content) / 1048576:.1f} MB; the limit for "
                                             f"automatic processing is {max_bytes / 1048576:g} MB.")
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
        if count > max_pages:
            raise ReaderError("FILE_TOO_LARGE", f"The PDF has {count} pages; the limit for automatic "
                                                 f"processing is {max_pages}.")
        import pdfplumber
        try:
            plumber = pdfplumber.open(io.BytesIO(content))
        except Exception:
            plumber = None
        index = 0
        for i in range(count):
            number = i + 1
            page = pdf[i]
            textpage = None
            try:
                width, height = page.get_size()
                textpage = page.get_textpage()
                plain = textpage.get_text_bounded() or ""
                chars = len("".join(plain.split()))
                share = _image_share(page, pdfium_raw)
                result = PageRead(number, "text", round(width, 1), round(height, 1), chars=chars,
                                  image_share=round(share, 2))
                if chars < SCAN_MAX_CHARS and share > SCAN_MIN_IMAGE_SHARE:
                    words, info = _ocr_page(page, sha, number, mode, record_ocr, variant,
                                            picture=_picture_box(page, pdfium_raw))
                    if info is None:
                        result.kind = "scan_unread"
                        result.note = ("Scanned page: no OCR engine is installed and no recorded "
                                       "OCR output exists for this file.")
                    else:
                        result.kind = "scan"
                        result.ocr = info
                        if info.get("width") and info.get("height"):      # a photographed sheet, cut out and turned upright
                            result.width, result.height = info["width"], info["height"]
                        result.lines = build_lines(words, number, index)
                elif chars < 8:
                    result.kind = "empty"
                    result.note = "Page has no text."
                else:
                    words = _text_words(plumber, i)
                    if not words:  # layout extraction failed: fall back to plain page text
                        words = _plain_words(plain, width, height)
                        result.note = "Layout could not be read; used plain page text."
                    repaired, unreadable = repair_undecoded(words, textpage, height)
                    if repaired or unreadable:
                        result.note = (result.note + " " if result.note else "") + (
                            f"{repaired + unreadable} word(s) use a font whose characters the layout reader could not "
                            f"decode; {repaired} were read again with the PDF engine"
                            + (f", {unreadable} could not be read and are not trusted." if unreadable else "."))
                    result.lines = build_lines(words, number, index)
                index += len(result.lines)
                pages.append(result)
            finally:
                if textpage is not None:
                    textpage.close()
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


def _in_order(short: str, long: str) -> bool:
    """True when the characters of `short` appear in `long` in the same order."""
    rest = iter(long)
    return all(ch in rest for ch in short)


def repair_undecoded(words: list[Word], textpage, page_height: float) -> tuple[int, int]:
    """Some embedded fonts carry no usable character map for part of their glyphs (Chrome
    embeds a web font this way). pdfminer then returns a private-use code point for the
    hyphen, and 'INV-000482' arrives as 'INV?000482'. The PDF engine that renders the
    page decodes the same glyphs correctly, so each affected word is read again from the
    engine's text for the same rectangle. The second reading is accepted only if it keeps
    every character the first one did decode, in order, and adds no more than the glyphs
    that were missing. A word that cannot be repaired is marked (confidence 0), so nothing
    read from it is trusted without a person. Returns (repaired, still unreadable)."""
    repaired = unreadable = 0
    for word in words:
        gaps = UNDECODED.findall(word.text)
        if not gaps:
            continue
        known = UNDECODED.sub("", word.text)
        try:
            again = textpage.get_text_bounded(left=word.x0 - 0.3, bottom=page_height - word.bottom,
                                              right=word.x1 + 0.3, top=page_height - word.top) or ""
        except Exception:
            again = ""
        again = "".join(again.split())
        if again and not UNDECODED.search(again) and _in_order(known, again) and len(again) <= len(known) + len(gaps):
            word.text = again
            repaired += 1
        else:
            word.conf = 0.0
            unreadable += 1
    return repaired, unreadable


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


def render_page_image(content: bytes, page_number: int, scale: float = 1.6, angle: float = 0.0,
                      prep: Optional[dict] = None):
    """PIL image of one page for the UI. `angle` and `prep` (sheet outline and quarter
    turns of a photographed page) are what was found at read time, so boxes computed on
    the straightened scan land in the right place."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(content)
    try:
        page = pdf[page_number - 1]
        try:
            image = _render(page, safe_scale(page, scale), grayscale=False).convert("RGB")
        finally:
            page.close()
    finally:
        pdf.close()
    return straighten.apply_geometry(image, {**(prep or {}), "angle": angle}, fill=(255, 255, 255))


def ink_share(content: bytes, page_number: int, region: list[float], page_width: float, angle: float = 0.0,
              scale: float = 3.0, prep: Optional[dict] = None) -> Optional[float]:
    """How much of a region of a scanned page is ink, from 0 to 1. `region` is
    [x0, top, x1, bottom] in page points on the straightened page (the same coordinates
    the OCR word boxes use). Ruled lines do not count: the underline of a form field or
    the border of a box is not something written in it. Returns None when the region
    cannot be judged (off the page, too small, page not renderable).

    This is what separates "the vendor left the box empty" from "there is something
    there that OCR could not read"; only the first is the vendor's to fix."""
    import numpy as np
    from PIL import ImageFilter
    if page_width <= 0:
        return None
    try:
        image = render_page_image(content, page_number, scale=scale, angle=angle, prep=prep).convert("L")
    except Exception:
        return None
    k = image.width / page_width
    x0, top = max(0, int(region[0] * k)), max(0, int(region[1] * k))
    x1, bottom = min(image.width, int(region[2] * k)), min(image.height, int(region[3] * k))
    if x1 - x0 < 12 or bottom - top < 6:
        return None
    whole = np.asarray(image)
    hist = np.bincount(whole.ravel(), minlength=256).astype(np.float64)
    weight_dark = np.cumsum(hist)
    weight_light = whole.size - weight_dark
    sum_dark = np.cumsum(hist * np.arange(256))
    with np.errstate(divide="ignore", invalid="ignore"):
        between = weight_dark * weight_light * (sum_dark / weight_dark - (sum_dark[-1] - sum_dark) / weight_light) ** 2
    threshold = min(int(np.argmax(np.nan_to_num(between))), 170)       # the page's own split between ink and paper
    crop = np.asarray(image.crop((x0, top, x1, bottom)).filter(ImageFilter.MedianFilter(3)))
    dark = crop <= threshold
    dark[dark.mean(axis=1) > 0.6, :] = False        # a ruled line across the field
    dark[:, dark.mean(axis=0) > 0.8] = False        # a box border down its side
    return float(dark.mean())

