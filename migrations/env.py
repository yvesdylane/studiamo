"""Alembic environment.

Used both by the CLI (`alembic upgrade head`, `alembic revision --autogenerate -m "..."`)
and by the app at startup (src/db/migrate.py passes its own connection in).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv

import src.models  # noqa: F401  (registers every model on Base.metadata)
from src.base.Base import Base
from src.db.DB import Db

load_dotenv()

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Views created by the SQL migrations. They are not ORM tables, so autogenerate
# must not try to drop them.
VIEWS = {"player_levels", "student_courses"}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name in VIEWS:
        return False
    return True


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    url = Db.from_env().engine.url.render_as_string(hide_password=False)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # called from the app (src/db/migrate.py)
        _configure(connection)
        return

    db = Db.from_env(pool_size=1, max_overflow=0)
    try:
        with db.engine.connect() as conn:
            _configure(conn)
            conn.commit()
    finally:
        db.close()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
