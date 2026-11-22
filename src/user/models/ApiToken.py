"""ORM model for table `api_tokens` (migration 0012)."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base, IntPK, NowTS


class ApiToken(Base):
    __tablename__ = "api_tokens"
    __table_args__ = (sa.Index("api_tokens_user_idx", "user_id"),)

    id: Mapped[IntPK]
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(sa.String(60))
    token_hash: Mapped[str] = mapped_column(sa.CHAR(64), unique=True)
    prefix: Mapped[str] = mapped_column(sa.String(12))   # first characters, to recognise a key in the list
    created_at: Mapped[NowTS]
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
