"""ORM model for table `exam_attempt_questions`."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base


class ExamAttemptQuestion(Base):  # the subset THIS attempt got
    __tablename__ = "exam_attempt_questions"
    __table_args__ = (
        sa.Index("exam_attempt_questions_question_idx", "question_id"),
    )

    attempt_id: Mapped[int] = mapped_column(
        sa.ForeignKey("exam_attempts.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[int] = mapped_column(
        sa.ForeignKey("questions.id"), primary_key=True
    )
    position: Mapped[int]
