"""ORM model for table `course_topics`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.base.Base import Base, IntPK

if TYPE_CHECKING:
    from src.course.modles.Course import Course
    from src.course.modles.Note import Note


class Topic(Base):
    __tablename__ = "course_topics"
    __table_args__ = (sa.UniqueConstraint("course_id", "position"),)

    id: Mapped[IntPK]
    course_id: Mapped[int] = mapped_column(sa.ForeignKey("courses.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(sa.String(200))
    position: Mapped[int]

    course: Mapped[Course] = relationship(back_populates="topics")
    notes: Mapped[list[Note]] = relationship(
        back_populates="topic", order_by="Note.position", passive_deletes=True
    )
