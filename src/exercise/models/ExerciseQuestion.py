"""ORM model for table `exercise_questions`."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base


class ExerciseQuestion(Base):
    __tablename__ = "exercise_questions"

    exercise_id: Mapped[int] = mapped_column(
        sa.ForeignKey("exercises.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[int] = mapped_column(
        sa.ForeignKey("questions.id"), primary_key=True
    )
    position: Mapped[int]
