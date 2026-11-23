"""Straighten a photographed page before OCR.

A flatbed scan fills the picture and is at most a degree or two off. A phone photo is
different: the sheet lies on a desk, it is seen at an angle (so its edges are not
parallel), the light falls off across it, and now and then the page is on its side.
OCR reads rows of text; none of this is rows of text until it is undone.

The steps, each skipped when it is not needed (a flatbed scan gets none of them and
reads exactly as it did before this module existed):

  find_sheet        the four corners of a light sheet on a darker surface
  warp              map that quadrilateral onto an upright rectangle (undoes perspective and tilt)
  even_light        divide out the brightness of the paper, so a shadow is not read as ink
  runs_vertically   do the text rows run up the page? then the page is on its side
  clean_for_ocr     whiten ruled lines, and turn white-on-black headings into black on white

numpy and Pillow only: no new dependency for the app or the hosted demo.
"""
from __future__ import annotations

from typing import Optional

Quad = list[list[float]]        # corners as fractions of width and height: top-left, top-right, bottom-right, bottom-left


def _otsu(pixels) -> int:
    """The grey level that best separates the dark and the light pixels."""
    import numpy as np
    hist = np.bincount(pixels.ravel(), minlength=256).astype(np.float64)
    weight_dark = np.cumsum(hist)
    weight_light = pixels.size - weight_dark
    sum_dark = np.cumsum(hist * np.arange(256))
    with np.errstate(divide="ignore", invalid="ignore"):
        between = weight_dark * weight_light * (sum_dark / weight_dark - (sum_dark[-1] - sum_dark) / weight_light) ** 2
    return int(np.argmax(np.nan_to_num(between)))


def find_sheet(image) -> Optional[Quad]:
    """Corners of the sheet of paper in a photograph, or None when the paper reaches the
    edges of the picture (a flatbed scan, a fax, a tightly cropped photo) or no single
    clear sheet can be made out. Nothing is guessed: a doubtful outline returns None and
    the page is read as it is."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    small = image.convert("L")
    small.thumbnail((360, 360))
    pixels = np.asarray(small)
    height, width = pixels.shape
    light = pixels > _otsu(pixels)
    edge = max(2, int(0.03 * min(height, width)))
    ring = np.zeros_like(light)
    ring[:edge], ring[-edge:], ring[:, :edge], ring[:, -edge:] = True, True, True, True
    if light[ring].mean() > 0.6:
        return None                                   # paper all the way to the border
    # drop specks of glare on the desk, close the holes that print leaves in the paper, then
    # take the outermost points along the two diagonals
    mask = Image.fromarray((light * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
    mask = mask.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MinFilter(9))
    sheet = np.asarray(mask) > 0
    ys, xs = np.nonzero(sheet)
    if len(xs) < 0.25 * height * width:
        return None
    total, difference = xs + ys, xs - ys
    corners = [(int(xs[i]), int(ys[i])) for i in (total.argmin(), difference.argmax(), total.argmax(), difference.argmin())]
    outline = Image.new("L", (width, height), 0)
    ImageDraw.Draw(outline).polygon(corners, fill=255)
    inside = np.asarray(outline) > 0
    area = int(inside.sum())
    if area < 0.25 * height * width:
        return None
    filled = (sheet & inside).sum() / area            # print and shadow leave holes; a sheet is still mostly light
    spilled = (sheet & ~inside).sum() / max(int(sheet.sum()), 1)
    if filled < 0.8 or spilled > 0.05:
        return None                                   # not one four-cornered sheet
    return [[round(x / width, 4), round(y / height, 4)] for x, y in corners]


def warp(image, quad: Quad, fill=255):
    """The sheet inside `quad`, as an upright rectangle."""
    import numpy as np
    from PIL import Image
    width, height = image.size
    centre_x = sum(x for x, _ in quad) / 4 * width
    centre_y = sum(y for _, y in quad) / 4 * height
    # pull each corner a touch towards the centre, so the dark rim of the desk stays out
    points = [(x * width + (centre_x - x * width) * 0.006, y * height + (centre_y - y * height) * 0.006) for x, y in quad]
    (ax, ay), (bx, by), (cx, cy), (dx, dy) = points

    def distance(x0, y0, x1, y1) -> float:
        return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    out_w = max(8, int(round((distance(ax, ay, bx, by) + distance(dx, dy, cx, cy)) / 2)))
    out_h = max(8, int(round((distance(ax, ay, dx, dy) + distance(bx, by, cx, cy)) / 2)))
    target = [(0, 0), (out_w, 0), (out_w, out_h), (0, out_h)]
    rows, values = [], []
    for (tx, ty), (sx, sy) in zip(target, points):        # output point -> where it comes from in the photo
        rows.append([tx, ty, 1, 0, 0, 0, -sx * tx, -sx * ty])
        rows.append([0, 0, 0, tx, ty, 1, -sy * tx, -sy * ty])
        values += [sx, sy]
    try:
        coefficients = np.linalg.solve(np.array(rows, dtype=np.float64), np.array(values, dtype=np.float64))
    except np.linalg.LinAlgError:
        return image                                  # corners that do not span a sheet: leave the picture as it is
    return image.transform((out_w, out_h), Image.PERSPECTIVE, tuple(coefficients), Image.BICUBIC, fillcolor=fill)


def _paper(gray):
    """Brightness of the paper across the page, as a small image: print is erased with a
    local maximum, then the result is blurred."""
    from PIL import Image, ImageFilter
    small = gray.resize((max(gray.width // 12, 8), max(gray.height // 12, 8)), Image.BILINEAR)
    return small.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(5))


def uneven_light(gray) -> bool:
    """True when one part of the paper is clearly darker than another (a shadow, a lamp to one side)."""
    import numpy as np
    paper = np.asarray(_paper(gray), dtype=np.float32)
    return float(np.percentile(paper, 5)) < 0.85 * float(np.percentile(paper, 95))


def even_light(gray):
    """The page with the brightness of the paper divided out: paper becomes white everywhere."""
    import numpy as np
    from PIL import Image
    paper = np.asarray(_paper(gray).resize(gray.size, Image.BILINEAR), dtype=np.float32)
    # a shadow dims the paper; a printed dark band is not a shadow, so the estimate never drops far below the typical paper
    paper = np.maximum(paper, max(60.0, 0.6 * float(np.median(paper))))
    flat = np.asarray(gray, dtype=np.float32) * (250.0 / paper)
    return Image.fromarray(np.clip(flat, 0, 255).astype(np.uint8))


def _long_runs(ink, length: int):
    """Pixels that belong to a horizontal run of ink at least `length` pixels long. A line
    that drifts by a pixel still counts: the ink is thickened by one row first."""
    import numpy as np
    thick = ink.copy()
    thick[1:] |= ink[:-1]
    thick[:-1] |= ink[1:]
    height, width = thick.shape
    if width <= length:
        return np.zeros_like(ink)
    sums = np.zeros((height, width + 1), dtype=np.int32)
    np.cumsum(thick, axis=1, out=sums[:, 1:])
    full = (sums[:, length:] - sums[:, :-length]) >= length          # windows [i, i + length) that are all ink
    marks = np.zeros((height, width + 1), dtype=np.int32)
    marks[:, :full.shape[1]] += full
    marks[:, length:length + full.shape[1]] -= full
    covered = np.cumsum(marks, axis=1)[:, :width] > 0
    wide = covered.copy()                                              # the whole stroke of the line, not only its core
    for shift in (1, 2):
        wide[shift:] |= covered[:-shift]
        wide[:-shift] |= covered[shift:]
    return wide


def clean_for_ocr(gray):
    """Take out what an OCR engine reads as noise on an invoice: the ruled lines of tables
    and boxes are whitened (a letter has no stroke as long as a rule, so letters are not
    touched), and a filled dark band with light lettering (a table heading printed white
    on black) is turned into dark lettering on a light band."""
    import numpy as np
    from PIL import Image
    pixels = np.asarray(gray.convert("L")).copy()
    height, width = pixels.shape
    limit = min(_otsu(pixels), 170)
    # ---- filled bands: where most of a 40-pixel neighbourhood is ink
    cell = 8
    rows, cols = height // cell, width // cell
    if rows >= 8 and cols >= 8:
        density = (pixels[:rows * cell, :cols * cell] <= limit).reshape(rows, cell, cols, cell).mean(axis=(1, 3))
        padded = np.pad(density, 2, mode="edge")
        table = np.zeros((padded.shape[0] + 1, padded.shape[1] + 1))
        table[1:, 1:] = padded.cumsum(axis=0).cumsum(axis=1)
        local = (table[5:, 5:] - table[:-5, 5:] - table[5:, :-5] + table[:-5, :-5]) / 25.0
        core = local > 0.6
        # the rim of a band: cells that are mostly ink themselves and touch its core
        near = np.pad(core, 2).astype(np.int32)
        near_table = np.zeros((near.shape[0] + 1, near.shape[1] + 1), dtype=np.int32)
        near_table[1:, 1:] = near.cumsum(axis=0).cumsum(axis=1)
        touches = (near_table[5:, 5:] - near_table[:-5, 5:] - near_table[5:, :-5] + near_table[:-5, :-5]) > 0
        band = np.kron(core | (touches & (density > 0.5)), np.ones((cell, cell), dtype=bool))
        if band.any():
            area = pixels[:rows * cell, :cols * cell]
            area[band] = 255 - area[band]
    # ---- ruled lines, across and down
    length = max(60, int(0.045 * max(width, height)))
    ink = pixels <= limit
    rules = _long_runs(ink, length) | _long_runs(ink.T, length).T
    pixels[rules] = 255
    return Image.fromarray(pixels)


def _row_contrast(ink) -> float:
    """How strongly the ink is arranged in horizontal bands. Rows of text give a profile of
    peaks and gaps. The ruled lines of a table would swamp it, so they are taken out first."""
    import numpy as np
    rows = ink.sum(axis=1).astype(np.float32)
    return float(((rows[1:] - rows[:-1]) ** 2).sum()) / max(len(rows), 1)


def runs_vertically(gray) -> bool:
    """True when the rows of text run up or down the picture: the page is on its side."""
    import numpy as np
    small = gray.copy()
    small.thumbnail((600, 600))
    pixels = np.asarray(small)
    ink = pixels <= min(_otsu(pixels), 170)
    longest = max(24, int(0.12 * max(ink.shape)))            # longer than a word at this size: a rule, a box, a band
    ink = ink & ~(_long_runs(ink, longest) | _long_runs(ink.T, longest).T)
    across, upright = _row_contrast(ink.T), _row_contrast(ink)
    return across > 2.0 * upright


def apply_geometry(image, prep: Optional[dict], fill=255):
    """Bring a freshly rendered page into the position it was read in: the same crop to
    the picture, the same sheet outline, the same quarter turns and the same straightening
    angle that were found at read time (kept with the read, so boxes drawn later land on
    the right words)."""
    from PIL import Image
    prep = prep or {}
    if prep.get("crop"):
        x0, y0, x1, y1 = prep["crop"]
        image = image.crop((int(x0 * image.width), int(y0 * image.height), int(x1 * image.width), int(y1 * image.height)))
    if prep.get("quad"):
        image = warp(image, prep["quad"], fill)
    turns = int(prep.get("turns") or 0) % 4
    if turns:
        image = image.rotate(90 * turns, expand=True, fillcolor=fill)
    angle = float(prep.get("angle") or 0.0)
    if abs(angle) >= 0.1:
        image = image.rotate(angle, resample=Image.BICUBIC, fillcolor=fill)
    return image
