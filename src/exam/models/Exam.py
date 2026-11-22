"""ORM model for table `exams`."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.base.Base import Base, IntPK, pg_enum

if TYPE_CHECKING:
    from src.exam.models.ExamQuestion import ExamQuestion
    from src.exam.models.ExamTopic import ExamTopic


class ExamKind(str, enum.Enum):
    SYSTEM = "system"        # generated from the question bank, open any time
    SCHEDULED = "scheduled"


class Exam(Base):
    __tablename__ = "exams"
    __table_args__ = (
        sa.CheckConstraint(
            "kind = 'system' or (opens_at is not null and closes_at is not null)",
            name="exams_window_check",
        ),
        sa.CheckConstraint(
            "kind = 'system' or (created_by is not null and coalesce(max_attempts, 0) = 1)",
            name="exams_scheduled_rules_check",
        ),
        sa.CheckConstraint(
            "opens_at is null or closes_at is null or closes_at > opens_at",
            name="exams_window_order_check",
        ),
        sa.Index("exams_course_idx", "course_id"),
        sa.Index("exams_kind_window_idx", "kind", "opens_at", "closes_at"),
    )

    id: Mapped[IntPK]
    course_id: Mapped[int | None] = mapped_column(sa.ForeignKey("courses.id", ondelete="CASCADE"))   # null: an exam on its own (0018)
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))  # null = system
    title: Mapped[str] = mapped_column(sa.String(200))
    kind: Mapped[ExamKind] = mapped_column(pg_enum(ExamKind, "exam_kind"))
    opens_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    closes_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    duration_minutes: Mapped[int | None]
    questions_per_attempt: Mapped[int | None]  # null = whole pool
    shuffle_questions: Mapped[bool] = mapped_column(server_default=sa.true())
    shuffle_options: Mapped[bool] = mapped_column(server_default=sa.true())
    max_attempts: Mapped[int | None]  # null = unlimited
    max_exp: Mapped[int] = mapped_column(server_default="0")
    published: Mapped[bool] = mapped_column(server_default=sa.false())
    # joining with a code (migration 0018, src/web/join.py)
    join_code: Mapped[str | None] = mapped_column(sa.String(12), unique=True)
    join_enabled: Mapped[bool] = mapped_column(server_default=sa.false())
    join_mode: Mapped[str] = mapped_column(sa.String(10), server_default="anyone")        # anyone | list
    guest_results: Mapped[str] = mapped_column(sa.String(10), server_default="graded")    # graded | manual
    # admin review (migration 0008, src/review/ReviewService.py)
    review_status: Mapped[str] = mapped_column(sa.String(10), server_default="draft")
    review_comment: Mapped[str | None] = mapped_column(sa.Text)
    submitted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    questions: Mapped[list[ExamQuestion]] = relationship(passive_deletes=True)  # scheduled pool
    topics: Mapped[list[ExamTopic]] = relationship(passive_deletes=True)
