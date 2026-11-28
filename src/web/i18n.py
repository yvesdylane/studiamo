"""Languages: English and French (Flask-Babel).

The language is the "lang" cookie the header's EN / FR switch sets, else the
browser's preferred language, else English. Templates write their text as
{{ _('…') }}; Python uses gettext / lazy_gettext from flask_babel. The French
catalogue is src/translations/fr/LC_MESSAGES/messages.po:

    uv run pybabel extract -F babel.cfg -o messages.pot .          # collect the strings
    uv run pybabel update -i messages.pot -d src/translations      # add new ones to messages.po
    (translate the empty msgstr lines in messages.po)
    uv run pybabel compile -d src/translations                     # messages.mo, read at startup

The compiled .mo is committed, so the server needs no build step.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from flask import Blueprint, Flask, abort, redirect, request
from flask_babel import Babel, get_locale

from src.web import js_strings

LANGUAGES = {"en": "English", "fr": "Français"}
COOKIE = "lang"

bp = Blueprint("i18n", __name__)


def select_locale() -> str:
    chosen = request.cookies.get(COOKIE)
    if chosen in LANGUAGES:
        return chosen
    return request.accept_languages.best_match(LANGUAGES) or "en"


@bp.get("/lang/<code>")
def switch(code: str):
    """Set the language and go back to the page the switch was clicked on."""
    if code not in LANGUAGES:
        abort(404)
    back = request.referrer or "/"
    parts = urlsplit(back)
    if parts.netloc and parts.netloc != request.host:  # only back to this site
        back = "/"
    else:
        back = parts.path + (f"?{parts.query}" if parts.query else "") or "/"
    resp = redirect(back)
    resp.set_cookie(COOKIE, code, max_age=60 * 60 * 24 * 365, samesite="Lax", secure=request.is_secure)
    return resp


def init_app(app: Flask) -> None:
    app.config.setdefault("BABEL_TRANSLATION_DIRECTORIES", "translations")  # relative to src/
    Babel(app, locale_selector=select_locale)
    app.register_blueprint(bp)
    app.jinja_env.globals.update(
        current_lang=lambda: str(get_locale() or "en"),
        languages=LANGUAGES,
        js_strings=js_strings.table,
    )
