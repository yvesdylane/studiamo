"""ORM model for table `student_note_time` (migration 0015)."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base


class StudentNoteTime(Base):
    __tablename__ = "student_note_time"

    student_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    note_id: Mapped[int] = mapped_column(sa.ForeignKey("topic_notes.id", ondelete="CASCADE"), primary_key=True)
    seconds: Mapped[int] = mapped_column(server_default="0")
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
