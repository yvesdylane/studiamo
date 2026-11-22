"""Declarative base and column helpers shared by every ORM model."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Annotated

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    pass


def pg_enum(py_enum: type[enum.Enum], name: str) -> pg.ENUM:
    """Postgres enum already created by schema.sql, stored by value not by name."""
    return pg.ENUM(
        py_enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=False,
    )


IntPK = Annotated[int, mapped_column(primary_key=True)]
NowTS = Annotated[
    datetime, mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
]
