"""Flask application factory.

    flask --app main run --debug      or      python main.py

On startup the database is migrated to the latest revision (set
AUTO_MIGRATE=0 to skip, e.g. when migrations run in a separate deploy step).
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from flask import Flask, current_app, jsonify

import src.models  # noqa: F401  (registers every model before the first query)
from src.db.DB import Db
from src.db.migrate import upgrade_database
from src.player.PlayerStats import PlayerRank

log = logging.getLogger(__name__)


def get_db() -> Db:
    """The app's Db, e.g. `with get_db().session() as s: ...` inside a route."""
    return current_app.extensions["db"]


def create_app() -> Flask:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

    app = Flask(__name__)
    secret = os.getenv("SECRET_KEY")
    if not secret:
        log.warning("SECRET_KEY is not set: using a development key; sessions are not safe")
    app.config["SECRET_KEY"] = secret or "dev-only-change-me"
    # set SESSION_COOKIE_SECURE=1 when the site is served over https
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"

    db = Db.from_env()
    app.extensions["db"] = db

    if os.getenv("AUTO_MIGRATE", "1") != "0":
        upgrade_database(db)

    from src.web import account, admin, appearance, auth, charts, google, learn, mcp, media, pages, security, studio, studio_tests, tests, verify

    security.init_app(app)
    appearance.init_app(app)
    charts.init_app(app)
    app.register_blueprint(pages.bp)
    app.register_blueprint(auth.bp)
    google.init_app(app)
    verify.init_app(app)
    media.init_app(app)
    mcp.init_app(app)
    for blueprint in (learn.bp, studio.bp, studio_tests.bp, tests.bp, account.bp, admin.bp):
        app.register_blueprint(blueprint)
    app.jinja_env.globals.update(
        nav_courses=pages.catalog,
        ranks=[r.value for r in PlayerRank],  # F ... S, lowest first
    )

    @app.get("/health")
    def health():
        ok = get_db().ping()
        return jsonify(status="ok" if ok else "db_unreachable"), 200 if ok else 503

    return app
