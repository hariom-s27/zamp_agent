"""Straightening a photographed page before OCR (invoice_desk/straighten.py).

The pictures here are drawn in the test, so nothing depends on an OCR engine: a sheet with
rows of dark bars for text, laid on a dark desk, seen at an angle, lit from one side.
"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from invoice_desk import straighten

DESK = 62


def sheet(width=600, height=840, rows=28, rule=False, band=False) -> Image.Image:
    """A white page with rows of short dark bars standing in for words. No bar is as long as
    the shortest thing clean_for_ocr treats as a ruled line (60 pixels on a page this size):
    a real word is letters with gaps, never one solid stroke."""
    page = Image.new("L", (width, height), 248)
    draw = ImageDraw.Draw(page)
    rng = np.random.default_rng(5)
    for row in range(rows):
        y = 50 + row * 26
        x = 40
        while x < width - 60:
            w = int(rng.integers(18, 52))
            draw.rectangle([x, y, x + w, y + 9], fill=30)
            x += w + int(rng.integers(8, 16))
    if rule:
        draw.line([30, 300, width - 30, 300], fill=20, width=3)
        draw.line([300, 40, 300, height - 40], fill=20, width=3)
    if band:
        draw.rectangle([30, 400, width - 30, 472], fill=45)          # 6 mm at 300 dpi: the heading band of a table
        for x in range(60, width - 80, 90):
            draw.rectangle([x, 430, x + 50, 442], fill=245)          # light lettering on the dark band
    return page


def photographed(page: Image.Image, corners, size=(800, 1100)) -> Image.Image:
    """The page as a camera sees it on a desk: its four corners land on `corners`."""
    w, h = page.size
    rows, values = [], []
    for (tx, ty), (sx, sy) in zip(corners, [(0, 0), (w, 0), (w, h), (0, h)]):       # photo point -> page point
        rows += [[tx, ty, 1, 0, 0, 0, -sx * tx, -sx * ty], [0, 0, 0, tx, ty, 1, -sy * tx, -sy * ty]]
        values += [sx, sy]
    coefficients = np.linalg.solve(np.array(rows, dtype=float), np.array(values, dtype=float))
    photo = page.transform(size, Image.PERSPECTIVE, tuple(coefficients), Image.BICUBIC, fillcolor=DESK)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(corners, fill=255)
    return Image.composite(photo, Image.new("L", size, DESK), mask)


CORNERS = [(70, 60), (735, 95), (700, 1040), (45, 1000)]


def test_the_sheet_is_found_on_the_desk_and_its_corners_are_where_they_are():
    quad = straighten.find_sheet(photographed(sheet(), CORNERS))
    assert quad is not None
    for (fx, fy), (x, y) in zip(quad, CORNERS):
        assert abs(fx * 800 - x) <= 8 and abs(fy * 1100 - y) <= 8


def test_a_page_that_fills_the_picture_is_left_alone():
    """A flatbed scan, a fax, a tightly cropped photo: no desk to cut away."""
    assert straighten.find_sheet(sheet()) is None
    assert straighten.find_sheet(Image.new("L", (600, 840), 250)) is None


def test_two_sheets_or_a_scrap_of_paper_are_not_taken_for_the_page():
    desk = Image.new("L", (800, 1100), DESK)
    desk.paste(Image.new("L", (300, 400), 245), (40, 40))
    desk.paste(Image.new("L", (300, 400), 245), (460, 640))
    assert straighten.find_sheet(desk) is None                       # two sheets: the outline around both is mostly desk
    scrap = Image.new("L", (800, 1100), DESK)
    scrap.paste(Image.new("L", (200, 200), 245), (300, 400))
    assert straighten.find_sheet(scrap) is None                      # too small to be the page


def test_warping_undoes_the_perspective_so_text_rows_are_level_again():
    photo = photographed(sheet(), CORNERS)
    flat = straighten.warp(photo, straighten.find_sheet(photo))
    assert abs(flat.width - 665) <= 25 and abs(flat.height - 943) <= 25          # the size of the sheet in the photo
    pixels = np.asarray(flat) < 128
    assert pixels[:, :4].mean() < 0.02 and pixels[:4].mean() < 0.02              # no desk left at the rim
    rows = pixels.mean(axis=1)
    # level rows of text: a row of pixels is either inside a text row or between two, rarely half and half
    assert (rows > 0.25).mean() > 0.2 and (rows < 0.03).mean() > 0.45
    crooked = (np.asarray(photo) < 128)[150:950, 150:650].mean(axis=1)
    assert (crooked < 0.03).mean() < (rows < 0.03).mean()


def test_a_shadow_is_noticed_and_divided_out():
    page = sheet()
    assert not straighten.uneven_light(page)
    shade = np.linspace(1.0, 0.55, page.width, dtype=np.float32)[None, :]
    dim = Image.fromarray((np.asarray(page, dtype=np.float32) * shade).astype(np.uint8))
    assert straighten.uneven_light(dim)
    even = np.asarray(straighten.even_light(dim), dtype=np.float32)
    paper_left, paper_right = np.percentile(even[:, :80], 90), np.percentile(even[:, -80:], 90)
    assert abs(paper_left - paper_right) < 12 and paper_right > 225              # the dim side is paper-white again
    assert (even < 110).mean() == pytest.approx((np.asarray(page) < 110).mean(), abs=0.01)   # and the print is still print


def test_a_printed_dark_band_is_not_mistaken_for_a_shadow():
    assert not straighten.uneven_light(sheet(band=True))


def test_a_page_on_its_side_is_recognised():
    page = sheet(rule=True)
    assert not straighten.runs_vertically(page)
    assert straighten.runs_vertically(page.rotate(90, expand=True))
    assert straighten.runs_vertically(page.rotate(270, expand=True))
    assert not straighten.runs_vertically(page.rotate(180))                      # upside down is still rows; OCR settles that


def test_ruled_lines_are_whitened_and_letters_are_not():
    page = sheet(rule=True)
    clean = np.asarray(straighten.clean_for_ocr(page))
    before = np.asarray(page)
    assert (before[299:302, 60:540] < 100).mean() > 0.9 and (clean[299:302, 60:540] < 100).mean() == 0     # the rule is gone
    assert (clean[60:240, 299:302] < 100).mean() == 0
    away = np.ones(before.shape, dtype=bool)
    away[294:308, :] = False
    away[:, 294:308] = False
    assert ((before < 100) & away).sum() == ((clean < 100) & away).sum()          # every bar of 'text' is untouched


def test_light_lettering_on_a_dark_band_becomes_dark_on_light():
    page = sheet(band=True)
    clean = np.asarray(straighten.clean_for_ocr(page))
    assert clean[410, 300] > 180 and clean[462, 300] > 180       # the band is light now, up to its rim
    assert clean[436, 85] < 60                                  # and its lettering dark
    assert clean[60:380].tolist() == np.asarray(page)[60:380].tolist()         # the rest of the page is as it was


def test_a_page_is_brought_back_into_the_position_it_was_read_in():
    """What the review screen does: the same crop, sheet outline, quarter turn and angle as at read time."""
    photo = photographed(sheet(), CORNERS)
    quad = straighten.find_sheet(photo)
    again = straighten.apply_geometry(photo, {"quad": quad, "turns": 1, "angle": 0.0})
    assert again.size == straighten.warp(photo, quad).rotate(90, expand=True).size
    assert straighten.apply_geometry(photo, None).size == photo.size
    cropped = straighten.apply_geometry(photo, {"crop": [0.0, 0.1, 1.0, 0.9]})
    assert cropped.size == (800, 880)


@pytest.mark.parametrize("picture", [
    Image.new("L", (1, 1), 255), Image.new("L", (8, 8), 0), Image.new("L", (600, 800), 255), Image.new("L", (600, 800), 0),
    Image.new("L", (2000, 10), 200), Image.new("L", (6, 3000), 30), Image.new("RGB", (300, 400), (250, 250, 250)),
    Image.fromarray((np.random.default_rng(1).random((500, 400)) * 255).astype("uint8")),
], ids=["one pixel", "tiny black", "blank white", "all black", "wide strip", "tall strip", "colour", "noise"])
def test_no_picture_makes_the_straightening_fail(picture):
    """A blank page, a black page, a sliver, noise: every step answers, none raises."""
    gray = picture.convert("L")
    assert straighten.find_sheet(picture) is None
    assert straighten.uneven_light(gray) in (True, False) and straighten.runs_vertically(gray) in (True, False)
    assert straighten.even_light(gray).size == gray.size and straighten.clean_for_ocr(picture).size == gray.size


def test_corners_that_do_not_span_a_sheet_leave_the_picture_alone():
    page = sheet()
    assert straighten.warp(page, [[0.5, 0.5]] * 4).size == page.size
    assert straighten.apply_geometry(page, {"quad": [[0.2, 0.2]] * 4, "turns": 0}).size == page.size
