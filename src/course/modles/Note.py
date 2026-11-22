"""ORM model for table `topic_notes`."""

from __future__ import annotations

from datetime import datetime

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.base.Base import Base, IntPK, NowTS

if TYPE_CHECKING:
    from src.course.modles.Topic import Topic


class Note(Base):
    __tablename__ = "topic_notes"
    __table_args__ = (sa.Index("topic_notes_topic_idx", "topic_id", "position"),)

    id: Mapped[IntPK]
    topic_id: Mapped[int] = mapped_column(
        sa.ForeignKey("course_topics.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(sa.String(200))
    md_path: Mapped[str] = mapped_column(sa.Text)  # path/key of the .md file
    position: Mapped[int]
    max_exp: Mapped[int] = mapped_column(server_default="0")
    reading_seconds: Mapped[int | None]   # estimated time to read it once (migration 0015)
    published: Mapped[bool] = mapped_column(server_default=sa.false())
    # admin review (migration 0008, src/review/ReviewService.py)
    review_status: Mapped[str] = mapped_column(sa.String(10), server_default="draft")
    review_comment: Mapped[str | None] = mapped_column(sa.Text)
    submitted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[NowTS]

    topic: Mapped[Topic] = relationship(back_populates="notes")
