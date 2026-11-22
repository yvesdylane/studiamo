"""ORM model for table `student_completed_notes`."""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base, NowTS


class StudentCompletedNote(Base):
    __tablename__ = "student_completed_notes"
    __table_args__ = (sa.Index("student_completed_notes_note_idx", "note_id"),)

    student_id: Mapped[int] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    note_id: Mapped[int] = mapped_column(
        sa.ForeignKey("topic_notes.id", ondelete="CASCADE"), primary_key=True
    )
    exp_awarded: Mapped[int] = mapped_column(server_default="0")
    xexp_awarded: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), server_default="0")
    completed_at: Mapped[NowTS]
