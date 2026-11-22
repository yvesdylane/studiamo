"""ORM model for table `student_mark_for_exams`."""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base


class StudentMarkForExam(Base):
    __tablename__ = "student_mark_for_exams"

    student_id: Mapped[int] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    exam_id: Mapped[int] = mapped_column(
        sa.ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True
    )
    best_score_pct: Mapped[Decimal] = mapped_column(sa.Numeric(5, 2))
    exp_earned: Mapped[int] = mapped_column(server_default="0")
    xexp_earned: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), server_default="0")
    attempts_count: Mapped[int] = mapped_column(server_default="1")
