"""ORM model for table `user_ai_keys` (migration 0013)."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base, NowTS


class UserAiKey(Base):
    __tablename__ = "user_ai_keys"

    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    provider: Mapped[str] = mapped_column(sa.String(20), server_default="anthropic")
    key_encrypted: Mapped[str] = mapped_column(sa.Text)      # never sent to a page
    key_hint: Mapped[str] = mapped_column(sa.String(8))       # "…abcd"
    model: Mapped[str] = mapped_column(sa.String(40), server_default="claude-opus-5-5")
    created_at: Mapped[NowTS]
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
