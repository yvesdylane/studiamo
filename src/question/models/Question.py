"""ORM model for table `questions`."""

from __future__ import annotations

import enum
from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.base.Base import Base, IntPK, NowTS, pg_enum

if TYPE_CHECKING:
    from src.question.models.QuestionOption import QuestionOption
    from src.question.models.QuestionTestCase import QuestionTestCase


class QuestionType(str, enum.Enum):
    MCQ = "mcq"
    SHORT_ANSWER = "short_answer"
    CODE = "code"


class CodeLanguage(str, enum.Enum):
    VBNET = "vbnet"
    C = "c"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    PYTHON = "python"


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        sa.CheckConstraint(
            "difficulty between 1 and 5", name="questions_difficulty_check"
        ),
        sa.CheckConstraint(
            "type <> 'code' or code_language is not null",
            name="questions_code_language_check",
        ),
        sa.Index(
            "questions_topic_idx", "topic_id", postgresql_where=sa.text("is_active")
        ),
    )

    id: Mapped[IntPK]
    topic_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("course_topics.id", ondelete="SET NULL")
    )
    author_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"))
    type: Mapped[QuestionType] = mapped_column(pg_enum(QuestionType, "question_type"))
    body_md: Mapped[str] = mapped_column(sa.Text)
    difficulty: Mapped[int | None] = mapped_column(sa.SmallInteger)
    points: Mapped[Decimal] = mapped_column(sa.Numeric(5, 2), server_default="1")
    explanation_md: Mapped[str | None] = mapped_column(sa.Text)
    is_active: Mapped[bool] = mapped_column(server_default=sa.true())
    allow_multiple: Mapped[bool] = mapped_column(server_default=sa.false())
    code_language: Mapped[CodeLanguage | None] = mapped_column(
        pg_enum(CodeLanguage, "code_language")
    )
    starter_code: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[NowTS]

    options: Mapped[list[QuestionOption]] = relationship(
        back_populates="question", order_by="QuestionOption.id", passive_deletes=True
    )
    test_cases: Mapped[list[QuestionTestCase]] = relationship(
        back_populates="question", order_by="QuestionTestCase.id", passive_deletes=True
    )
