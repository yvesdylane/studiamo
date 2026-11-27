"""ORM model for table `user_ai_keys` (migrations 0013, 0019): one row per user and provider."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base, NowTS


class UserAiKey(Base):
    __tablename__ = "user_ai_keys"

    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    provider: Mapped[str] = mapped_column(sa.String(20), primary_key=True)   # anthropic | google | openai
    key_encrypted: Mapped[str] = mapped_column(sa.Text)      # never sent to a page
    key_hint: Mapped[str] = mapped_column(sa.String(8))       # "…abcd"
    model: Mapped[str] = mapped_column(sa.String(80))
    models: Mapped[list] = mapped_column(JSONB, server_default="[]")   # ids this key can use
    created_at: Mapped[NowTS]
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
