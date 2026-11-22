"""ORM model for table `exam_topics`."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base


class ExamTopic(Base):
    __tablename__ = "exam_topics"

    exam_id: Mapped[int] = mapped_column(
        sa.ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[int] = mapped_column(
        sa.ForeignKey("course_topics.id", ondelete="CASCADE"), primary_key=True
    )
