"""Drawing key images: background, icon and label, then converting to the deck's own format.

Icons are Material Design Icons (the mdi: names Home Assistant uses), drawn from the MDI web
font, which is downloaded once into a cache folder. A look can also name a .png or .svg file
(.png only, for now).
"""

from __future__ import annotations

import io
import logging
import os
import re
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

COLORS = {
    "black": "#000000",
    "grey": "#5f6368",
    "dark": "#1c1c1e",
    "white": "#ffffff",
    "amber": "#ffb300",
    "blue": "#0a84ff",
    "green": "#30d158",
    "red": "#ff453a",
    "purple": "#bf5af2",
}

MDI_VERSION = "7.4.47"
MDI_URL = f"https://cdn.jsdelivr.net/npm/@mdi/font@{MDI_VERSION}/"
CACHE = Path(os.environ.get("STREAMDECK_HA_CACHE", Path.home() / ".cache" / "streamdeck-ha"))

# Output pixel (x, y) comes from source pixel ... (docs/BRIDGE-PROTOCOL.md); PIL's rotations
# are counterclockwise.
TRANSFORMS = {
    "none": None,
    "transpose": Image.Transpose.TRANSPOSE,
    "rotate90": Image.Transpose.ROTATE_270,
    "rotate270": Image.Transpose.ROTATE_90,
    "rotate180": Image.Transpose.ROTATE_180,
}


def color(value: str | None, default: str) -> str:
    value = value or default
    return COLORS.get(value, value)


@dataclass(frozen=True)
class KeyLook:
    """Everything that decides how one key looks. Frozen, so it can key a cache."""

    icon: str | None = None
    background: str = "black"
    icon_color: str = "white"
    text: str = ""
    text_position: str = "bottom"  # bottom, top or none
    text_color: str = "white"
    border: str | None = None  # e.g. to show a key is waiting for a second press
    long_press: bool = False  # draws a dash in a corner
    double_press: bool = False  # draws two dots in a corner
    countdown: str = ""  # a running timer's time left, drawn large in place of the icon
    progress: float | None = None  # how much of a timer is left, 0..1, drawn as a bar along the top


# --- Icons -----------------------------------------------------------------


def _download(name: str) -> Path:
    path = CACHE / "mdi" / MDI_VERSION / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("Downloading %s", MDI_URL + name)
        with urllib.request.urlopen(MDI_URL + name, timeout=30) as response:
            data = response.read()
        path.with_suffix(".tmp").write_bytes(data)
        path.with_suffix(".tmp").replace(path)
    return path


@lru_cache(maxsize=1)
def _mdi_codepoints() -> dict[str, str]:
    css = _download("css/materialdesignicons.css").read_text(encoding="utf-8")
    return {name: chr(int(code, 16)) for name, code in re.findall(r'\.mdi-([a-z0-9-]+)::?before\s*\{\s*content:\s*"\\([0-9A-Fa-f]+)"', css)}


@lru_cache(maxsize=16)
def _mdi_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_download("fonts/materialdesignicons-webfont.ttf")), size)


def _draw_icon(image: Image.Image, icon: str, box: tuple[int, int, int, int], fill: str) -> None:
    left, top, right, bottom = box
    size = min(right - left, bottom - top)
    if size <= 0:
        return
    center = ((left + right) / 2, (top + bottom) / 2)
    if icon.startswith("mdi:"):
        glyph = _mdi_codepoints().get(icon[4:])
        if glyph is None:
            log.warning("Unknown icon %s", icon)
            glyph = _mdi_codepoints().get("help-rhombus-outline", "?")
        ImageDraw.Draw(image).text(center, glyph, font=_mdi_font(size), fill=fill, anchor="mm")
    elif icon.lower().endswith(".png"):
        try:
            picture = Image.open(icon).convert("RGBA")
        except OSError as error:
            log.warning("Can't open %s: %s", icon, error)
            return
        picture.thumbnail((size, size))
        image.paste(picture, (int(center[0] - picture.width / 2), int(center[1] - picture.height / 2)), picture)
    else:
        log.warning("Only mdi: icons and .png files are drawn so far: %s", icon)


# --- Text ------------------------------------------------------------------


@lru_cache(maxsize=32)
def _text_font(size: int) -> ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _fit_text(draw: ImageDraw.ImageDraw, text: str, width: int, largest: int) -> ImageFont.ImageFont:
    size = largest
    while size > 8:
        font = _text_font(size)
        if draw.textlength(text, font=font) <= width:
            return font
        size -= 1
    return _text_font(size)


# --- Keys ------------------------------------------------------------------


def _base(look: KeyLook, s: int, background: Image.Image | None) -> Image.Image:
    """The key's background colour, or the page's picture, dimmed under a button with no colour."""
    if background is None or look.background not in (None, "black"):
        return Image.new("RGB", (s, s), color(look.background, "black"))
    picture = background.convert("RGB").resize((s, s), Image.Resampling.LANCZOS)
    if look.icon or look.text:
        picture = Image.blend(picture, Image.new("RGB", (s, s), "black"), 0.45)
    return picture


def render_key(look: KeyLook, size: int, background: Image.Image | None = None) -> Image.Image:
    """Draws a key at `size` pixels square, as seen from the front, over `background` if given
    (a piece of the page's picture or clock)."""
    # Drawn at 2x and scaled down, for smoother edges at small key sizes.
    scale = 2
    s = size * scale
    image = _base(look, s, background)
    draw = ImageDraw.Draw(image)
    border_width = max(2, s // 16) if look.border else 0
    pad = int(s * 0.06) + border_width

    text = look.text if look.text_position != "none" else ""
    text_height = int(s * 0.24) if text else 0
    icon_box = [pad, pad, s - pad, s - pad]
    if text:
        font = _fit_text(draw, text, s - 2 * pad, int(s * 0.2))
        if look.text_position == "top":
            draw.text((s / 2, pad), text, font=font, fill=color(look.text_color, "white"), anchor="mt")
            icon_box[1] += text_height
        else:
            draw.text((s / 2, s - pad), text, font=font, fill=color(look.text_color, "white"), anchor="mb")
            icon_box[3] -= text_height
    if look.countdown:
        left, top, right, bottom = icon_box
        font = _fit_text(draw, look.countdown, right - left, min(int(s * 0.36), bottom - top))
        draw.text(((left + right) / 2, (top + bottom) / 2), look.countdown, font=font, fill=color(look.icon_color, "white"), anchor="mm")
        if look.progress is not None:
            bar = max(4, s // 24)
            y = border_width + max(2, s // 40)
            draw.rectangle([pad, y, s - pad, y + bar], fill=color("dark", "dark"))
            filled = pad + (s - 2 * pad) * max(0.0, min(1.0, look.progress))
            if filled > pad:
                draw.rectangle([pad, y, filled, y + bar], fill=color(look.icon_color, "white"))
    elif look.icon:
        _draw_icon(image, look.icon, tuple(icon_box), color(look.icon_color, "white"))
    elif text and look.text_position != "none":
        # A label with no icon: draw it large in the middle instead.
        image = _base(look, s, background)
        draw = ImageDraw.Draw(image)
        font = _fit_text(draw, text, s - 2 * pad, int(s * 0.32))
        draw.text((s / 2, s / 2), text, font=font, fill=color(look.text_color, "white"), anchor="mm")
    if look.long_press or look.double_press:
        _draw_marks(image, look, s, border_width, top=look.text_position != "top" or not text)
    if look.border:
        ImageDraw.Draw(image).rectangle([0, 0, s - 1, s - 1], outline=color(look.border, "white"), width=border_width)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _draw_marks(image: Image.Image, look: KeyLook, s: int, inset: int, top: bool) -> None:
    """Small marks in a corner for keys that do more than tap: a dash for a long press, two dots
    for a double press (like Morse code). Top right, or bottom right when the label is on top."""
    draw = ImageDraw.Draw(image)
    dot = max(4, int(s * 0.06))
    gap = dot * 0.6
    margin = int(s * 0.05) + inset
    y0 = margin if top else s - margin - dot
    fill = color(look.text_color, "white")
    x = s - margin
    if look.double_press:
        for _ in range(2):
            draw.ellipse([x - dot, y0, x, y0 + dot], fill=fill)
            x -= dot + gap
        x -= gap
    if look.long_press:
        draw.rounded_rectangle([x - dot * 2.6, y0, x, y0 + dot], radius=dot / 2, fill=fill)


# --- The Stream Deck +'s touch strip ------------------------------------------


@dataclass(frozen=True)
class DialLook:
    """One dial's part of the touch strip: its name, value and level."""

    icon: str | None = None
    text: str = ""
    value: str = ""
    level: int | None = None  # 0-100 for the bar; None for no bar
    color: str = "amber"  # the bar, and the icon while on
    on: bool = True


DIAL_BACKGROUND = "#0b0c0e"
DIAL_TRACK = "#2a2d33"
DIAL_DIM = "#6b7079"


def render_dial(look: DialLook | None, width: int, height: int) -> Image.Image:
    """Draws one dial's part of the strip, as seen from the front: icon on the left, name and value
    on the right, and a bar along the bottom for the level. None draws it blank."""
    scale = 2
    w, h = width * scale, height * scale
    image = Image.new("RGB", (w, h), DIAL_BACKGROUND if look else "black")
    if look is None:
        return image.resize((width, height), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    pad = round(h * 0.1)
    accent = color(look.color, "amber") if look.on else DIAL_DIM
    bar = round(h * 0.09) if look.level is not None else 0
    bottom = h - pad - (bar + pad // 2 if bar else 0)

    left = pad
    if look.icon:
        size = bottom - pad
        _draw_icon(image, look.icon, (pad, pad, pad + size, bottom), accent if look.on else DIAL_DIM)
        left = pad * 2 + size
    room = w - left - pad
    if look.text:
        font = _fit_text(draw, look.text, room, round(h * 0.22))
        draw.text((left, pad), look.text, font=font, fill="#c9cdd3" if look.on else DIAL_DIM, anchor="lt")
    if look.value:
        font = _fit_text(draw, look.value, room, round(h * 0.38))
        draw.text((left, bottom), look.value, font=font, fill="white" if look.on else DIAL_DIM, anchor="lb")
    if bar:
        top = h - pad - bar
        draw.rounded_rectangle([pad, top, w - pad, top + bar], radius=bar // 2, fill=DIAL_TRACK)
        filled = round((w - 2 * pad) * look.level / 100)
        if filled > 0:
            draw.rounded_rectangle([pad, top, pad + max(filled, bar), top + bar], radius=bar // 2, fill=accent)
    return image.resize((width, height), Image.Resampling.LANCZOS)


def to_native(image: Image.Image, fmt: str, transform: str) -> bytes:
    """Converts a key image to the deck's own format (docs/BRIDGE-PROTOCOL.md)."""
    flip = TRANSFORMS.get(transform)
    if flip is not None:
        image = image.transpose(flip)
    out = io.BytesIO()
    if fmt == "bmp":
        image.save(out, "BMP", dpi=(72, 72))
    else:
        image.save(out, "JPEG", quality=92, subsampling=0)
    return out.getvalue()


def split_text(text: str, keys: int) -> list[str]:
    """Spreads a short text over several keys, a few characters each (for a new panel's ID)."""
    per_key = max(1, -(-len(text) // keys))
    return [text[i : i + per_key] for i in range(0, per_key * keys, per_key)]
