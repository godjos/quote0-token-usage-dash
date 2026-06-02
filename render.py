"""
Render a 296×152 black/white PNG image showing selected token usage providers.

Uses Terminus bitmap font — pure B&W pixels, no anti-aliasing, no grey.
No supersampling needed.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from usage import UsageProviderResult

W, H = 296, 152
PAD  = 6

_HERE        = Path(__file__).parent
FONT_REGULAR = str(_HERE / "fonts" / "terminus-normal.otb")
FONT_BOLD    = str(_HERE / "fonts" / "terminus-bold.otb")

BLACK = 0
WHITE = 255
LA    = ZoneInfo("America/Los_Angeles")

LABEL_W = 22   # px reserved for row label ("5h", "7d", "Wk")
NOTE_W  = 90   # px reserved for right-side text ("87%  2h37m")


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _lsize(font: ImageFont.FreeTypeFont) -> int:
    return font.size


def _lw(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    return int(draw.textlength(text, font=font))


def _text_tracked(draw: ImageDraw.ImageDraw, pos: tuple[int, int], text: str,
                  font: ImageFont.FreeTypeFont, spacing: int = 1) -> None:
    """Draw text with extra letter spacing (tracking)."""
    x, y = pos
    for ch in text:
        draw.text((x, y), ch, font=font, fill=BLACK)
        x += _lw(draw, ch, font) + spacing


DOT_SPACING = 4  # one dot per NxN grid in the empty bar area

def _bar(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, used_pct: float) -> None:
    draw.rectangle([x, y, x + w - 1, y + h - 1], outline=BLACK, width=1)
    # Filled portion
    filled = int((w - 2) * min(used_pct, 100) / 100)
    if filled > 0:
        draw.rectangle([x + 1, y + 1, x + filled, y + h - 2], fill=BLACK)
    # Empty portion: dot grid anchored to bar left so pattern is consistent
    grid_x0 = x + 1         # anchor — same for every bar
    grid_x1 = x + w - 2
    grid_y0 = y + 1
    grid_y1 = y + h - 2
    empty_x0 = grid_x0 + filled  # clip: only draw in empty region
    margin = DOT_SPACING // 2
    for dy in range(grid_y0 + margin, grid_y1 - margin + 1, DOT_SPACING):
        for dx in range(grid_x0 + margin, grid_x1 - margin + 1, DOT_SPACING):
            if dx >= empty_x0:
                draw.point((dx, dy), fill=BLACK)


def _draw_row(
    draw: ImageDraw.ImageDraw,
    y: int,
    row_h: int,
    label: str,
    used_pct: float,
    note: Optional[str],
    fonts: dict,
) -> None:
    lbl_font  = fonts["label"]
    note_font = fonts["note"]

    bar_h = max(8, row_h - 4)
    bar_y = y + (row_h - bar_h) // 2

    lbl_h  = _lsize(lbl_font)
    note_h = _lsize(note_font)

    # Label
    draw.text((PAD, bar_y + (bar_h - lbl_h) // 2), label, font=lbl_font, fill=BLACK)

    # Note (right-aligned)
    remaining = 100.0 - used_pct
    note_text = f"{remaining:.0f}%"
    if note:
        note_text += f"  {note}"
    note_x = W - PAD - NOTE_W
    draw.text((note_x, bar_y + (bar_h - note_h) // 2), note_text, font=note_font, fill=BLACK)

    # Bar
    bar_x = PAD + LABEL_W
    bar_w = note_x - 4 - bar_x
    _bar(draw, bar_x, bar_y, bar_w, bar_h, used_pct)


def _section_label(usage: "UsageProviderResult") -> str:
    label = usage.title
    if usage.subtitle:
        label += f"  ({usage.subtitle})"
    return label


def render_image(usages: list["UsageProviderResult"]) -> bytes:
    img  = Image.new("L", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    fonts = {
        "title":   _font(FONT_BOLD,    14),
        "ts":      _font(FONT_REGULAR, 12),
        "section": _font(FONT_BOLD,    12),
        "label":   _font(FONT_BOLD,    12),
        "note":    _font(FONT_REGULAR, 12),
    }

    # ── Header ────────────────────────────────────────────────────────────
    now      = datetime.now(LA)
    date_str = now.strftime("%b %-d")
    time_str = now.strftime("%-I:%M %p")

    _text_tracked(draw, (PAD, PAD), "Token Usage", fonts["title"], spacing=2)

    ts_w   = _lw(draw, time_str, fonts["ts"])
    date_w = _lw(draw, date_str, fonts["ts"])
    ts_y   = PAD + (_lsize(fonts["title"]) - _lsize(fonts["ts"])) // 2
    draw.text((W - PAD - ts_w, ts_y), time_str, font=fonts["ts"], fill=BLACK)
    draw.text((W - PAD - ts_w - 6 - date_w, ts_y), date_str, font=fonts["ts"], fill=BLACK)

    header_bottom = PAD + _lsize(fonts["title"]) + 4
    draw.line([(0, header_bottom), (W, header_bottom)], fill=BLACK, width=1)

    # ── Collect rows ──────────────────────────────────────────────────────
    from usage import format_time_until

    sections: list[tuple[str, list[tuple[str, float, Optional[str]]]]] = []
    for usage in usages[:2]:
        rows = []
        for row in usage.rows:
            note = row.reset_hint
            if note is None and row.resets_at is not None:
                note = format_time_until(row.resets_at)
            rows.append((row.label, row.used_percent, note))
        if rows:
            sections.append((_section_label(usage), rows))

    # ── Layout ────────────────────────────────────────────────────────────
    SECTION_H = _lsize(fonts["section"]) + 5
    DIVIDER_H = 8

    n_sections = len(sections)
    n_rows = sum(len(rows) for _, rows in sections)
    has_multiple = n_sections > 1

    content_h = H - header_bottom - 2
    fixed_h = n_sections * SECTION_H
    fixed_h += DIVIDER_H * (n_sections - 1) if has_multiple else 0
    row_h     = min((content_h - fixed_h) // n_rows, 28) if n_rows else content_h

    # Vertically center the content block when it doesn't fill the space
    total_h   = fixed_h + row_h * n_rows
    y_offset  = (content_h - total_h) // 2
    y = header_bottom + 2 + y_offset

    for idx, (label, rows) in enumerate(sections):
        draw.text((PAD, y), label, font=fonts["section"], fill=BLACK)
        y += SECTION_H
        for row_label, used_pct, note in rows:
            _draw_row(draw, y, row_h, row_label, used_pct, note, fonts)
            y += row_h
        if idx < len(sections) - 1:
            y += DIVIDER_H // 2
            dash, gap, x = 6, 4, 0
            while x < W:
                draw.line([(x, y), (min(x + dash - 1, W), y)], fill=BLACK, width=1)
                x += dash + gap
            y += DIVIDER_H // 2

    buf = io.BytesIO()
    img.convert("1").save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    from usage import get_provider_usage, parse_usage_providers

    results = []
    for provider in parse_usage_providers():
        try:
            usage = get_provider_usage(provider)
        except Exception as e:
            print(f"{provider} error: {e}")
            continue
        if usage.rows:
            results.append(usage)

    png = render_image(results)
    with open("/tmp/usage_preview.png", "wb") as f:
        f.write(png)
    print(f"Saved /tmp/usage_preview.png ({len(png)} bytes)")
