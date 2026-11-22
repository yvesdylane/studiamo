"""ORM model for table `question_options`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.base.Base import Base, IntPK

if TYPE_CHECKING:
    from src.question.models.Question import Question


class QuestionOption(Base):  # mcq
    __tablename__ = "question_options"
    __table_args__ = (sa.Index("question_options_question_idx", "question_id"),)

    id: Mapped[IntPK]
    question_id: Mapped[int] = mapped_column(
        sa.ForeignKey("questions.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(sa.Text)
    is_correct: Mapped[bool] = mapped_column(server_default=sa.false())

    question: Mapped[Question] = relationship(back_populates="options")
