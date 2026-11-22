"""ORM model for table `exercise_attempts`."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.attempt.AttemptStatus import AttemptStatus
from src.base.Base import Base, IntPK, NowTS, pg_enum


class ExerciseAttempt(Base):
    __tablename__ = "exercise_attempts"
    __table_args__ = (
        sa.UniqueConstraint("student_id", "exercise_id", "attempt_no"),
        sa.CheckConstraint(
            "score_pct between 0 and 100", name="exercise_attempts_score_pct_check"
        ),
        sa.CheckConstraint(
            "status = 'in_progress' or (md_path is not null and submitted_at is not null)",
            name="exercise_attempts_record_check",
        ),
        sa.CheckConstraint(
            "status <> 'graded' or score_pct is not null",
            name="exercise_attempts_graded_score_check",
        ),
        sa.CheckConstraint(
            "points_earned is null or (points_earned >= 0 and points_earned <= points_possible)",
            name="exercise_attempts_points_check",
        ),
    )

    id: Mapped[IntPK]
    student_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    exercise_id: Mapped[int] = mapped_column(
        sa.ForeignKey("exercises.id", ondelete="CASCADE")
    )
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
