"""Db - the one object that owns the connection pool and runs queries.

    db = Db.from_env()                      # DATABASE_URL, or DB_HOST / DB_PORT / ...
    db = Db("postgresql://user:pw@localhost:5432/learning")

    # raw SQL, named parameters (:name)
    rows = db.fetch_all("select * from courses where created_by = :u", {"u": 1})
    one  = db.fetch_one("select * from users where email = :e", {"e": "a@b.com"})
    n    = db.scalar("select count(*) from users")
    db.execute("update users set name = :n where id = :id", {"n": "Shin", "id": 1})

    # several statements in ONE transaction: pass conn= to join it
    with db.transaction() as conn:
        db.execute("insert into courses (code, title, created_by) values (:c, :t, :u)",
                   {"c": "IGL236", "t": "Networks", "u": 1}, conn=conn)
        db.execute("update ...", {...}, conn=conn)       # all or nothing

    # ORM (the models in src/): commit on success, rollback on error
    with db.session() as s:
        s.add(User(name="Shin", email="a@b.com"))
        courses = s.scalars(select(Course).order_by(Course.code)).all()

Every helper opens its own transaction (committed when it returns) unless you
pass conn=. Rows come back as plain dicts.

Requires: sqlalchemy>=2.0, psycopg[binary]>=3.1
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

Params = Mapping[str, Any] | None
Statement = str | sa.sql.Executable  # a raw SQL string, or a SQLAlchemy select()/insert()/...

_DRIVER = "postgresql+psycopg"


def _to_url(url: str | sa.URL) -> sa.URL:
    """Accepts postgres:// and postgresql:// URLs and forces the psycopg 3 driver."""
    parsed = sa.make_url(url)
    if parsed.drivername in ("postgres", "postgresql"):
        parsed = parsed.set(drivername=_DRIVER)
    return parsed


class Db:
    def __init__(
        self,
        url: str | sa.URL,
        *,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 30,
        pool_recycle: int = 1800,
        echo: bool = False,
        **engine_kwargs: Any,
    ) -> None:
        """Creating a Db does not connect yet: the first query does.

        pool_size / max_overflow   how many connections can be open at once
        pool_recycle               seconds before a connection is replaced
        engine_kwargs              forwarded to sqlalchemy.create_engine
                                   (e.g. connect_args={"sslmode": "require"})
        """
        self.engine: Engine = sa.create_engine(
            _to_url(url),
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
            pool_pre_ping=True,  # drops dead connections instead of failing the query
            echo=echo,
            **engine_kwargs,
        )
        # expire_on_commit=False: objects stay readable after commit, so they can
        # be turned into DTOs (XRead.model_validate(obj)) once the session is closed
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    # ------------------------------------------------------------ creation
    @classmethod
    def from_env(cls, **kwargs: Any) -> Db:
        """First match wins:

        1. DATABASE_URL                          full Postgres URI (Supabase: Connect > Session pooler)
        2. SUPABASE_URL + SUPABASE_DB_PASSWORD   direct connection db.<ref>.supabase.co:5432
        3. DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
        """
        url = os.getenv("DATABASE_URL")
        if not url and os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_DB_PASSWORD"):
            # https://<ref>.supabase.co -> postgres@db.<ref>.supabase.co/postgres
            ref = urlparse(os.environ["SUPABASE_URL"]).hostname.split(".")[0]
            url = sa.URL.create(
                _DRIVER,
                username="postgres",
                password=os.environ["SUPABASE_DB_PASSWORD"],
                host=f"db.{ref}.supabase.co",
                port=5432,
                database="postgres",
            )
        if url and "supabase" in str(sa.make_url(url).host or ""):
            kwargs.setdefault("connect_args", {}).setdefault("sslmode", "require")
        if not url:
            url = sa.URL.create(
                _DRIVER,
                username=os.getenv("DB_USER", "postgres"),
                password=os.getenv("DB_PASSWORD"),
                host=os.getenv("DB_HOST", "localhost"),
                port=int(os.getenv("DB_PORT", "5432")),
                database=os.getenv("DB_NAME", "postgres"),
            )
        return cls(url, **kwargs)

    # ------------------------------------------------------ transactions
    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        """One connection, one transaction: commit on success, rollback on error."""
        with self.engine.begin() as conn:
            yield conn

    @contextmanager
    def session(self) -> Iterator[Session]:
        """ORM session: commit on success, rollback on error, always closed."""
        session = self._sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def _conn(self, conn: Connection | None) -> Iterator[Connection]:
        if conn is not None:  # caller owns the transaction
            yield conn
        else:
            with self.engine.begin() as own:
                yield own

    @staticmethod
    def _stmt(sql: Statement) -> sa.sql.Executable:
        return sa.text(sql) if isinstance(sql, str) else sql

    # ------------------------------------------------------------- queries
    def execute(self, sql: Statement, params: Params = None, *, conn: Connection | None = None) -> int:
        """Run INSERT / UPDATE / DELETE / DDL. Returns the number of rows affected."""
        with self._conn(conn) as c:
            return c.execute(self._stmt(sql), params or {}).rowcount

    def execute_many(
        self, sql: Statement, rows: Sequence[Mapping[str, Any]], *, conn: Connection | None = None
    ) -> int:
        """Run one statement once per dict in rows (bulk insert). Returns rows affected."""
        if not rows:
            return 0
        with self._conn(conn) as c:
            return c.execute(self._stmt(sql), list(rows)).rowcount

    def fetch_all(
        self, sql: Statement, params: Params = None, *, conn: Connection | None = None
    ) -> list[dict[str, Any]]:
        with self._conn(conn) as c:
            return [dict(r) for r in c.execute(self._stmt(sql), params or {}).mappings()]

    def fetch_one(
        self, sql: Statement, params: Params = None, *, conn: Connection | None = None
    ) -> dict[str, Any] | None:
        """First row as a dict, or None when nothing matches."""
        with self._conn(conn) as c:
            row = c.execute(self._stmt(sql), params or {}).mappings().first()
            return dict(row) if row is not None else None

    def scalar(self, sql: Statement, params: Params = None, *, conn: Connection | None = None) -> Any:
        """First column of the first row, or None. Handy for count(*), RETURNING id..."""
        with self._conn(conn) as c:
            return c.execute(self._stmt(sql), params or {}).scalar()

    # ------------------------------------------------------------- scripts
    def run_script(self, sql: str, *, conn: Connection | None = None) -> None:
        """Run a multi-statement SQL script as is (functions, triggers, views...).

        No parameters and no :name parsing, so $$ bodies and colons are safe.
        """
        with self._conn(conn) as c:
            c.exec_driver_sql(sql)

    def run_file(self, path: str | Path, *, conn: Connection | None = None) -> None:
        """Run a .sql file, e.g. db.run_file("schema.sql") on an empty database."""
        self.run_script(Path(path).read_text(encoding="utf-8"), conn=conn)

    # ------------------------------------------------------------- housekeeping
    def ping(self) -> bool:
        """True if the database answers, False otherwise (never raises)."""
        try:
            with self.engine.connect() as c:
                c.execute(sa.text("select 1"))
            return True
        except sa.exc.SQLAlchemyError:
            return False

    def close(self) -> None:
        """Closes every pooled connection. The Db can still be used afterwards."""
        self.engine.dispose()

    def __enter__(self) -> Db:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Db({self.engine.url.render_as_string(hide_password=True)})"