"""ORM model for table `exercises`."""

from __future__ import annotations

from datetime import datetime

from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.base.Base import Base, IntPK

if TYPE_CHECKING:
    from src.exercise.models.ExerciseQuestion import ExerciseQuestion


class Exercise(Base):
    __tablename__ = "exercises"
    __table_args__ = (
        sa.CheckConstraint(
            "retake_decay between 0 and 1", name="exercises_retake_decay_check"
        ),
        sa.Index("exercises_topic_idx", "topic_id"),
    )

    id: Mapped[IntPK]
    topic_id: Mapped[int] = mapped_column(
        sa.ForeignKey("course_topics.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(sa.String(200))
    max_exp: Mapped[int] = mapped_column(server_default="0")
    retake_decay: Mapped[Decimal] = mapped_column(sa.Numeric(3, 2), server_default="0.50")
    published: Mapped[bool] = mapped_column(server_default=sa.false())
    # admin review (migration 0008, src/review/ReviewService.py)
    review_status: Mapped[str] = mapped_column(sa.String(10), server_default="draft")
    review_comment: Mapped[str | None] = mapped_column(sa.Text)
    submitted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    questions: Mapped[list[ExerciseQuestion]] = relationship(
        order_by="ExerciseQuestion.position", passive_deletes=True
    )
