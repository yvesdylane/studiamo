"""ORM model for table `question_test_cases`."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.base.Base import Base, IntPK

if TYPE_CHECKING:
    from src.question.models.Question import Question


class QuestionTestCase(Base):  # code (stdin / stdout)
    __tablename__ = "question_test_cases"
    __table_args__ = (sa.Index("question_test_cases_question_idx", "question_id"),)

    id: Mapped[IntPK]
    question_id: Mapped[int] = mapped_column(
        sa.ForeignKey("questions.id", ondelete="CASCADE")
    )
    input: Mapped[str] = mapped_column(sa.Text, server_default="")
    expected_output: Mapped[str] = mapped_column(sa.Text)
    is_hidden: Mapped[bool] = mapped_column(server_default=sa.true())
    weight: Mapped[Decimal] = mapped_column(sa.Numeric(5, 2), server_default="1")

    question: Mapped[Question] = relationship(back_populates="test_cases")
