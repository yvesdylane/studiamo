"""ORM model for table `exam_attempts`."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.attempt.AttemptStatus import AttemptStatus
from src.base.Base import Base, IntPK, NowTS, pg_enum

if TYPE_CHECKING:
    from src.attempt.ExamAttemptQuestion import ExamAttemptQuestion


class ExamAttempt(Base):
    __tablename__ = "exam_attempts"
    __table_args__ = (
        sa.UniqueConstraint("student_id", "exam_id", "attempt_no"),
        sa.CheckConstraint(
            "score_pct between 0 and 100", name="exam_attempts_score_pct_check"
        ),
        sa.CheckConstraint(
            "status = 'in_progress' or (md_path is not null and submitted_at is not null)",
            name="exam_attempts_record_check",
        ),
        sa.CheckConstraint(
            "status <> 'graded' or score_pct is not null",
            name="exam_attempts_graded_score_check",
        ),
        sa.CheckConstraint(
            "points_earned is null or (points_earned >= 0 and points_earned <= points_possible)",
            name="exam_attempts_points_check",
        ),
    )

    id: Mapped[IntPK]
    student_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    exam_id: Mapped[int] = mapped_column(sa.ForeignKey("exams.id", ondelete="CASCADE"))
    attempt_no: Mapped[int]
    status: Mapped[AttemptStatus] = mapped_column(
        pg_enum(AttemptStatus, "attempt_status"), server_default="in_progress"
    )
    md_path: Mapped[str | None] = mapped_column(sa.Text)  # questions + student's answers
    points_earned: Mapped[Decimal | None] = mapped_column(sa.Numeric(7, 2))
    points_possible: Mapped[Decimal | None] = mapped_column(sa.Numeric(7, 2))
    score_pct: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    started_at: Mapped[NowTS]
    submitted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(  # computed by the database
        sa.Integer,
        sa.Computed(
            "extract(epoch from (submitted_at - started_at))::integer", persisted=True
        ),
    )

    questions: Mapped[list[ExamAttemptQuestion]] = relationship(
        order_by="ExamAttemptQuestion.position", passive_deletes=True
    )
