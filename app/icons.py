"""The app's own icons: simple line drawings on a 24 × 24 grid, in the colour of the text around them
(or gold), so they look the same on every computer, unlike emoji."""

from markupsafe import Markup

PATHS = {
    "trophy": "M8 21h8 M12 17v4 M7 4h10v5a5 5 0 0 1-10 0z M7 6H4.5a2.5 2.5 0 0 0 2.6 4 M17 6h2.5a2.5 2.5 0 0 1-2.6 4",
    "medal": "M8 2.5l3 6 M16 2.5l-3 6 M12 21.5a6 6 0 1 0 0-12 6 6 0 0 0 0 12z M12 12.6l.95 1.9 2.1.3-1.5 1.5.35 2.1-1.9-1-1.9 1 .35-2.1-1.5-1.5 2.1-.3z",
    "crown": "M3 19h18 M4 16L3 7l5 4 4-7 4 7 5-4-1 9z",
    "flame": "M12 22c4.4 0 7-3 7-6.6 0-3.6-2.6-5.4-3.6-8.4-1 2.4-2.4 3.4-3.4 3.4.2-3-.6-5.6-2.8-7.4-.6 3.8-5.2 6.6-5.2 11.6C4 19 7.6 22 12 22z",
    "bolt": "M13.5 2L4.5 13.5h6.5L10 22l9-11.5h-6.5z",
    "moon": "M20 14.6A8.2 8.2 0 1 1 9.4 4a6.6 6.6 0 0 0 10.6 10.6z",
    "castle": "M4 21V8h3V5h2.5v3h1.5V5h2v3h1.5V5H17v3h3v13z M10 21v-3.5a2 2 0 0 1 4 0V21 M4 12h16",
    "broom": "M15.5 2.5l-4 9 M7.5 11.5h8l2.5 10H5z M9 15.5l-1 6 M12 15.5v6 M15 15.5l1 6",
    "users": "M9 11a3.2 3.2 0 1 0 0-6.4A3.2 3.2 0 0 0 9 11z M2.5 20.5a6.5 6.5 0 0 1 13 0 M16 4.8a3.2 3.2 0 0 1 0 6 M21.5 20.5a6.5 6.5 0 0 0-4-6",
    "gem": "M6 3.5h12l4 5.5-10 12.5L2 9z M2 9h20 M9.5 3.5L8 9l4 12.5L16 9l-1.5-5.5",
    "dice": "M7 3.5h10A3.5 3.5 0 0 1 20.5 7v10a3.5 3.5 0 0 1-3.5 3.5H7A3.5 3.5 0 0 1 3.5 17V7A3.5 3.5 0 0 1 7 3.5z M8.5 8.5h.01 M15.5 15.5h.01 M12 12h.01 M15.5 8.5h.01 M8.5 15.5h.01",
    "rainbow": "M2.5 18.5a9.5 9.5 0 0 1 19 0 M6 18.5a6 6 0 0 1 12 0 M9.5 18.5a2.5 2.5 0 0 1 5 0",
    "tophat": "M7 3.5h10v12H7z M2.5 15.5h19 M2.5 15.5c0 3 4.3 4.5 9.5 4.5s9.5-1.5 9.5-4.5 M7 11.5h10",
    "trash": "M4 7h16 M9.5 7V4h5v3 M6 7l1 13.5h10L18 7 M10 11v6 M14 11v6",
    "timer": "M12 21.5a8 8 0 1 0 0-16 8 8 0 0 0 0 16z M12 9.5v4l2.5 2 M9.5 2.5h5 M12 2.5v3",
    "swords": "M14.5 17.5L3 6V3h3l11.5 11.5 M13 19l6-6 M16 16l4 4 M19 21l2-2 M9.5 6.5L14 2h3v3l-4.5 4.5 M5 14l4 4 M7 17l-3 3 M4 19l1 1",
    "star": "M12 2.8l2.8 5.8 6.4.9-4.6 4.5 1.1 6.3L12 17.3l-5.7 3 1.1-6.3-4.6-4.5 6.4-.9z",
    "sparkle": "M12 2.5c.7 4.8 4.7 8.8 9.5 9.5-4.8.7-8.8 4.7-9.5 9.5-.7-4.8-4.7-8.8-9.5-9.5 4.8-.7 8.8-4.7 9.5-9.5z",
}

# Icons drawn with a fill (the dots of the dice are strokes; these are shapes).
FILLED = {"bolt", "star", "sparkle"}


def icon(name: str, cls: str = "") -> Markup:
    """An icon as inline SVG. A name like "count-10" draws the number in a circle."""
    classes = f"icon {cls}".strip()
    if name.startswith("count-"):
        number = name.split("-", 1)[1]
        size = 10 if len(number) <= 2 else 8
        return Markup(
            f'<svg class="{classes}" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9.5" fill="none" '
            f'stroke="currentColor" stroke-width="1.8"/><text x="12" y="12" dy=".35em" text-anchor="middle" '
            f'font-size="{size}" font-weight="800" fill="currentColor" font-family="Inter, sans-serif">{number}</text></svg>'
        )
    path = PATHS.get(name, PATHS["star"])
    fill = 'fill="currentColor" stroke="none"' if name in FILLED else (
        'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"')
    return Markup(f'<svg class="{classes}" viewBox="0 0 24 24" aria-hidden="true"><path d="{path}" {fill}/></svg>')
