"""ORM models for joining exams with a code (migration 0018)."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base, IntPK, NowTS


class ExamGuest(Base):
    """Someone who joined an exam with a code, a name and an email: no account."""

    __tablename__ = "exam_guests"
    __table_args__ = (sa.UniqueConstraint("exam_id", "email"),)

    id: Mapped[IntPK]
    exam_id: Mapped[int] = mapped_column(sa.ForeignKey("exams.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(sa.String(255))
    name: Mapped[str] = mapped_column(sa.String(100))
    code_hash: Mapped[str | None] = mapped_column(sa.CHAR(64))         # the emailed 6-digit code, hashed
    code_expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    code_tries: Mapped[int] = mapped_column(server_default="0")
    code_sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    results_sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[NowTS]


class ExamAllowedEmail(Base):
    """The class list of an exam with join_mode = 'list'."""

    __tablename__ = "exam_allowed_emails"

    exam_id: Mapped[int] = mapped_column(sa.ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True)
    email: Mapped[str] = mapped_column(sa.String(255), primary_key=True)
