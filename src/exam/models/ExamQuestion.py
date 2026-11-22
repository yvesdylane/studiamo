"""ORM model for table `exam_questions`."""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base


class ExamQuestion(Base):
    __tablename__ = "exam_questions"

    exam_id: Mapped[int] = mapped_column(
        sa.ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[int] = mapped_column(
        sa.ForeignKey("questions.id"), primary_key=True
    )
    points: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
