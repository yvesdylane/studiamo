"""Personal appearance, saved per user: colours, light / dark theme, font, text size.

Everything is read from the logged-in user (g.user) when a page is rendered,
so one user's choices never change what anyone else sees. Guests get the
defaults (and the theme cookie of the header switch).

The accent colour replaces the brand purple everywhere for this user. White
text sits on it (buttons, tags), so a colour is only accepted when white text
on it has a contrast ratio of at least 4.5:1 (WCAG AA).

The theme is the account's choice when logged in, else the "theme" cookie the
header switch sets; "system" follows the device.
"""

from __future__ import annotations

import re

from flask import Flask, g, request

BRAND = "#722f99"
PALETTE = [  # name, colour: every one passes 4.5:1 with white text
    ("Purple", "#722f99"), ("Indigo", "#4338ca"), ("Blue", "#1d4ed8"), ("Teal", "#0f766e"),
    ("Green", "#15803d"), ("Orange", "#c2410c"), ("Red", "#b91c1c"), ("Pink", "#be185d"),
    ("Brown", "#7c4a1e"), ("Slate", "#334155"),
]
SECONDARY_PALETTE = [  # course strip, footer, rank badge (tinted; any colour reads well there)
    ("Grey", "#8c8585"), ("Amber", "#b45309"), ("Gold", "#a16207"), ("Mint", "#047857"),
    ("Sky", "#0369a1"), ("Lavender", "#6d28d9"), ("Rose", "#be123c"), ("Graphite", "#1f2937"),
]
DEFAULT_SECONDARY = None   # the design's greys
THEMES = ("system", "light", "dark")
FONTS = {  # key: (label, display stack, body stack, Google Fonts families or None)
    "studiamo": ("Studiamo (Tac One + Tagesschrift)", "'Tac One',Arial,sans-serif", "'Tagesschrift',Georgia,serif", "family=Tac+One&family=Tagesschrift"),
    "modern":   ("Modern (Space Grotesk + Inter)", "'Space Grotesk',Arial,sans-serif", "'Inter',Arial,sans-serif", "family=Space+Grotesk:wght@500;700&family=Inter:wght@400;700"),
    "readable": ("Very readable (Atkinson Hyperlegible)", "'Atkinson Hyperlegible',Arial,sans-serif", "'Atkinson Hyperlegible',Arial,sans-serif", "family=Atkinson+Hyperlegible:wght@400;700"),
    "lexend":   ("Lexend (easier for some dyslexic readers)", "'Lexend',Arial,sans-serif", "'Lexend',Arial,sans-serif", "family=Lexend:wght@400;600"),
    "serif":    ("Classic serif", "Georgia,'Times New Roman',serif", "Georgia,'Times New Roman',serif", None),
    "system":   ("Your device's font", "system-ui,-apple-system,'Segoe UI',Roboto,sans-serif", "system-ui,-apple-system,'Segoe UI',Roboto,sans-serif", None),
}
# html font-size; every size on the site is in rem, so this scales all text
TEXT_SIZES = {"xsmall": ("Extra small", "81.25%"), "small": ("Small", "87.5%"), "normal": ("Normal", "93.75%"),
              "large": ("Large", "106.25%"), "xlarge": ("Extra large", "118.75%")}
_HEX = re.compile(r"^#[0-9a-f]{6}$")


def _luminance(hex_colour: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g_, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g_) + 0.0722 * channel(b)


def contrast_with_white(hex_colour: str) -> float:
    return 1.05 / (_luminance(hex_colour) + 0.05)


def clean_secondary(value: str | None) -> str | None:
    """'#rrggbb' (lower case) or None. Any colour works: it is only used as a tint."""
    value = (value or "").strip().lower()
    if len(value) == 4 and value.startswith("#"):
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return value if _HEX.match(value) else None


def clean_colour(value: str | None) -> str | None:
    """'#rrggbb' in lower case if it is a valid, readable accent; None otherwise."""
    value = (value or "").strip().lower()
    if len(value) == 4 and value.startswith("#"):          # #abc -> #aabbcc
        value = "#" + "".join(ch * 2 for ch in value[1:])
    if not _HEX.match(value) or contrast_with_white(value) < 4.5:
        return None
    return value


def accent_color() -> str | None:
    user = g.get("user")
    colour = getattr(user, "accent_color", None) if user else None
    return colour if colour and _HEX.match(colour) else None   # only ever a validated #rrggbb


def theme_pref() -> str:
    """The account's light/dark choice, else the header switch's cookie, else 'system'."""
    user = g.get("user")
    pref = getattr(user, "theme", None) if user else None
    if pref in ("light", "dark"):
        return pref
    cookie = request.cookies.get("theme")
    return cookie if cookie in ("light", "dark") else "system"


def _user_value(name: str):
    user = g.get("user")
    return getattr(user, name, None) if user else None


def page_style() -> dict:
    """What base.html needs: the CSS variables, the font link, the text size."""
    font_key = _user_value("font") if _user_value("font") in FONTS else "studiamo"
    label, display, body, families = FONTS[font_key]
    size_key = _user_value("text_size") if _user_value("text_size") in TEXT_SIZES else "normal"
    variables = {"--font-display": display, "--font-body": body}
    accent = accent_color()
    if accent:
        variables["--purple"] = accent
    themed = {}
    secondary = _user_value("secondary_color")
    if secondary and _HEX.match(secondary):
        # mixed with the page background, so it suits light and dark
        themed = {"--strip": f"color-mix(in srgb, {secondary} 34%, var(--bg))",
                  "--footer": f"color-mix(in srgb, {secondary} 18%, var(--bg))", "--badge": secondary}
    return {
        # every value is from a fixed list or a validated #rrggbb: safe inside <style>
        "css": ";".join(f"{k}:{v}" for k, v in variables.items()),
        "themed_css": ";".join(f"{k}:{v}" for k, v in themed.items()),
        "font_url": f"https://fonts.googleapis.com/css2?{families}&display=swap" if families else None,
        "text_size": TEXT_SIZES[size_key][1],
    }


def toast_settings() -> dict:
    """How this user wants messages: pop-ups (and for how long) or in the page."""
    user = g.get("user")
    enabled = getattr(user, "toast_enabled", True) if user else True
    seconds = getattr(user, "toast_seconds", 10) if user else 10
    return {"enabled": bool(enabled), "seconds": max(3, min(60, int(seconds or 10)))}


def notifications() -> list[dict]:
    """Messages waiting for this user (daily tasks, badges...), marked as seen."""
    user = g.get("user")
    if user is None or request.method != "GET":
        return []
    from src.progress.service.Engagement import take_notifications
    from src.web import deps

    with deps.db().session() as session:
        return take_notifications(session, user.id)


def init_app(app: Flask) -> None:
    app.jinja_env.globals.update(toast_settings=toast_settings, notifications=notifications,
        accent_color=accent_color, theme_pref=theme_pref, page_style=page_style, palette=PALETTE,
        secondary_palette=SECONDARY_PALETTE, fonts=FONTS, text_sizes=TEXT_SIZES, brand_colour=BRAND,
    )
