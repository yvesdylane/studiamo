"""Brings the database up to date. Called once when the app starts.

    1. creates the database itself if it does not exist yet (local Postgres)
    2. if the schema was already loaded by hand (studiamo.sql run in Supabase),
       marks it as revision 0001 instead of creating everything twice
    3. runs every pending Alembic migration (`alembic upgrade head`)

A Postgres advisory lock makes this safe when several workers start at once.
"""

from __future__ import annotations

import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from src.db.DB import Db

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
INITIAL_REVISION = "0001"
_LOCK_KEY = 727_001  # any constant; identifies "studiamo migrations"


def ensure_database_exists(db: Db) -> None:
    """CREATE DATABASE <name> when the server is reachable but the database is missing."""
    url = db.engine.url
    try:
        with db.engine.connect():
            return
    except sa.exc.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise  # wrong password, server down... let it surface

    name = url.database
    log.info("Database %r not found, creating it", name)
    admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            quoted = conn.dialect.identifier_preparer.quote(name)
            conn.exec_driver_sql(f"CREATE DATABASE {quoted}")
    finally:
        admin.dispose()


def _alembic_config(connection: sa.Connection) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["connection"] = connection
    cfg.attributes["skip_logging"] = True  # keep Flask's logging setup
    return cfg


def upgrade_database(db: Db) -> None:
    ensure_database_exists(db)

    with db.engine.connect() as conn:
        conn.execute(sa.text("select pg_advisory_lock(:k)"), {"k": _LOCK_KEY})
        try:
            insp = sa.inspect(conn)
            tables = set(insp.get_table_names())
            cfg = _alembic_config(conn)

            if "alembic_version" not in tables and "users" in tables:
                log.info("Existing schema found without migration history: stamping %s", INITIAL_REVISION)
                command.stamp(cfg, INITIAL_REVISION)

            command.upgrade(cfg, "head")
            conn.commit()
        finally:
            conn.execute(sa.text("select pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
            conn.commit()
