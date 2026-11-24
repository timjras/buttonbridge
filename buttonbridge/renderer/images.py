"""Pictures across a page's keys: the clock and photos.

The clock has two styles (docs/CONFIG.md). `digit_per_key` puts each digit in its own key,
at its natural shape and as large as the key allows, so the gaps between keys never cut through a
number. `across` draws the time once over the whole panel and cuts it into keys. Both match the
setup page's preview.
"""

from __future__ import annotations

import datetime as dt
import logging
import urllib.request
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .draw import CACHE

log = logging.getLogger(__name__)

# Google Fonts, pinned to one commit of github.com/google/fonts (SIL Open Font License).
FONTS_COMMIT = "e7db6ca603fd4862010383ff17ffd6cc4ffac1c0"
FONTS_URL = f"https://cdn.jsdelivr.net/gh/google/fonts@{FONTS_COMMIT}/"
CLOCK_FONTS = {  # name: (file, weight for variable fonts)
    "montserrat": ("ofl/montserrat/Montserrat%5Bwght%5D.ttf", 800),
    "archivo": ("ofl/archivoblack/ArchivoBlack-Regular.ttf", None),
    "roboto": ("ofl/robotocondensed/RobotoCondensed%5Bwght%5D.ttf", 700),
    "oswald": ("ofl/oswald/Oswald%5Bwght%5D.ttf", 600),
    "bebas": ("ofl/bebasneue/BebasNeue-Regular.ttf", None),
}

CLOCK_BACKGROUND = "#0b0c0e"
CLOCK_DIGITS = "#f2a516"
CLOCK_SMALL = "#c9cdd3"
GAP = 0.22  # the gap between keys, as a fraction of a key (for pictures cut across keys)
MEASURE = 200  # px: digits are measured at this size and scaled


def font_path(name: str) -> Path:
    file, _ = CLOCK_FONTS[name]
    path = CACHE / "fonts" / FONTS_COMMIT[:12] / Path(file).name.replace("%5B", "[").replace("%5D", "]")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("Downloading the %s clock font", name)
        with urllib.request.urlopen(FONTS_URL + file, timeout=30) as response:
            data = response.read()
        path.with_suffix(".tmp").write_bytes(data)
        path.with_suffix(".tmp").replace(path)
    return path


@lru_cache(maxsize=64)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    if name not in CLOCK_FONTS:
        name = "montserrat"
    font = ImageFont.truetype(str(font_path(name)), size)
    weight = CLOCK_FONTS[name][1]
    if weight:
        try:
            font.set_variation_by_axes([weight])
        except (OSError, ValueError):
            pass  # not a variable font after all
    return font


@lru_cache(maxsize=8)
def _digit_metrics(name: str) -> tuple[float, float, float]:
    """The widest digit's ink width, the digits' ink top and their ink height, at MEASURE px."""
    font = _font(name, MEASURE)
    boxes = [font.getbbox(d, anchor="ls") for d in "0123456789"]
    width = max(b[2] - b[0] for b in boxes)
    top = min(b[1] for b in boxes)  # negative: above the baseline
    bottom = max(b[3] for b in boxes)
    return width, top, bottom - top


def _draw_digit(image: Image.Image, char: str, cx: float, y: float, scale: float, font_name: str, fill: str) -> None:
    """Draws a digit at a uniform scale, centred on cx, with the digits' ink box starting at y."""
    size = max(4, round(MEASURE * scale))
    font = _font(font_name, size)
    _, top, _ = _digit_metrics(font_name)
    box = font.getbbox(char, anchor="ls")
    ImageDraw.Draw(image).text((cx - (box[0] + box[2]) / 2, y - top * size / MEASURE), char, font=font, fill=fill, anchor="ls")


def _draw_colon(image: Image.Image, cx: float, cy: float, digit_height: float, fill: str) -> None:
    d = max(3, digit_height * 0.11)
    draw = ImageDraw.Draw(image)
    for dy in (-0.2, 0.2):
        y = cy + digit_height * dy
        draw.rectangle([cx - d / 2, y - d / 2, cx + d / 2, y + d / 2], fill=fill)


@lru_cache(maxsize=4)
def _small_font(size: int) -> ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def clock_time(now: dt.datetime, hours: int) -> tuple[str, str, str]:
    """Hours (two characters, a space instead of a leading zero on a 12-hour clock), minutes, and
    AM/PM ("" on a 24-hour clock)."""
    if hours == 12:
        return f"{now.hour % 12 or 12:>2}", now.strftime("%M"), "AM" if now.hour < 12 else "PM"
    return now.strftime("%H"), now.strftime("%M"), ""


def clock_cells(cols: int, rows: int, now: dt.datetime, hours: int = 24) -> dict[int, tuple[str, str]]:
    """What each key (numbered from 1) shows: ("big", digit or ":"), ("small", text) or nothing.
    A small text can have two lines."""
    hh, mm, ampm = clock_time(now, hours)
    day = now.strftime("%a").upper()
    date = f"{now.day} {now.strftime('%b').upper()}"
    cells: dict[int, tuple[str, str]] = {}

    def at(c: int, r: int, value: tuple[str, str]) -> None:
        cells[r * cols + c + 1] = value

    if cols >= 5:
        r, c0 = (rows - 1) // 2, (cols - 5) // 2
        for i, ch in enumerate([hh[0], hh[1], ":", mm[0], mm[1]]):
            if ch != " ":
                at(c0 + i, r, ("big", ch))
        if r + 1 < rows:
            at(c0 + 1, r + 1, ("small", day))
            if ampm:
                at(c0 + 2, r + 1, ("small", ampm))
            at(c0 + 3, r + 1, ("small", date))
        elif ampm and c0 + 5 < cols:
            at(c0 + 5, r, ("small", ampm))
    else:
        for c, ch in ((0, hh[0]), (1, hh[1])):
            if ch != " ":
                at(c, 0, ("big", ch))
        at(0, 1, ("big", mm[0]))
        at(1, 1, ("big", mm[1]))
        if cols > 2:
            at(2, 0, ("small", f"{day}\n{ampm}" if ampm else day))
            at(2, 1, ("small", date))
    return cells


def clock_key(cell: tuple[str, str] | None, size: int, font_name: str) -> Image.Image:
    """One key of a digit_per_key clock."""
    scale_up = 2
    s = size * scale_up
    image = Image.new("RGB", (s, s), CLOCK_BACKGROUND)
    if cell:
        kind, value = cell
        margin = s * 0.05
        width, _, height = _digit_metrics(font_name)
        k = min((s - 2 * margin) / width, (s - 2 * margin) / height)
        if kind == "big" and value == ":":
            _draw_colon(image, s / 2, s / 2, height * k, CLOCK_DIGITS)
        elif kind == "big":
            _draw_digit(image, value, s / 2, (s - height * k) / 2, k, font_name, CLOCK_DIGITS)
        else:
            ImageDraw.Draw(image).multiline_text((s / 2, s / 2), value, font=_small_font(round(s * 0.26)), fill=CLOCK_SMALL,
                                                 anchor="mm", align="center", spacing=round(s * 0.06))
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _panel_size(cols: int, rows: int, size: int) -> tuple[int, int, float]:
    step = size * (1 + GAP)
    return round(cols * step - GAP * size), round(rows * step - GAP * size), step


def _tiles(image: Image.Image, cols: int, rows: int, size: int, step: float) -> dict[int, Image.Image]:
    tiles = {}
    for key in range(1, cols * rows + 1):
        c, r = (key - 1) % cols, (key - 1) // cols
        x, y = round(c * step), round(r * step)
        tiles[key] = image.crop((x, y, x + size, y + size))
    return tiles


def clock_across(cols: int, rows: int, size: int, now: dt.datetime, font_name: str, hours: int = 24) -> dict[int, Image.Image]:
    """A clock drawn once across the panel and cut into keys (AM/PM small, under the minutes)."""
    scale_up = 2
    w, h, step = _panel_size(cols, rows, size * scale_up)
    image = Image.new("RGB", (w, h), CLOCK_BACKGROUND)
    hh, mm, ampm = clock_time(now, hours)
    text = f"{hh.strip()}:{mm}"
    width, _, height = _digit_metrics(font_name)
    margin = size * scale_up * 0.04
    gap = height * 0.06
    colon = height * 0.11 + 2 * gap
    font = _font(font_name, MEASURE)
    widths = [colon if ch == ":" else (lambda b: b[2] - b[0])(font.getbbox(ch, anchor="ls")) for ch in text]
    natural = sum(widths) + gap * (len(text) - 1)
    k = min((w - 2 * margin) / natural, (h - 2 * margin) / height)
    y = (h - height * k) / 2
    x = (w - natural * k) / 2
    for ch, cw in zip(text, widths):
        cw *= k
        if ch == ":":
            _draw_colon(image, x + cw / 2, h / 2, height * k, CLOCK_DIGITS)
        else:
            _draw_digit(image, ch, x + cw / 2, y, k, font_name, CLOCK_DIGITS)
        x += cw + gap * k
    if ampm:
        ImageDraw.Draw(image).text((x - gap * k, y + height * k + h * 0.02), ampm, font=_small_font(max(8, round(h * 0.09))),
                                   fill=CLOCK_SMALL, anchor="ra")
    return {key: tile.resize((size, size), Image.Resampling.LANCZOS) for key, tile in _tiles(image, cols, rows, size * scale_up, step).items()}


def picture_tiles(picture: Image.Image, cols: int, rows: int, size: int, fit: str = "cover") -> dict[int, Image.Image]:
    """A picture over the whole panel, cropped to fill it (cover) or fitted inside it (contain)."""
    w, h, step = _panel_size(cols, rows, size)
    picture = picture.convert("RGB")
    if fit == "contain":
        canvas = Image.new("RGB", (w, h), "black")
        fitted = ImageOps.contain(picture, (w, h), Image.Resampling.LANCZOS)
        canvas.paste(fitted, ((w - fitted.width) // 2, (h - fitted.height) // 2))
    else:
        canvas = ImageOps.fit(picture, (w, h), Image.Resampling.LANCZOS)
    return _tiles(canvas, cols, rows, size, step)
