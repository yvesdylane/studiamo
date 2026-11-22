"""ORM model for table `courses`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.base.Base import Base, IntPK, NowTS

if TYPE_CHECKING:
    from src.course.modles.Topic import Topic


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[IntPK]
    code: Mapped[str] = mapped_column(sa.String(20), unique=True)
    title: Mapped[str] = mapped_column(sa.String(200))
    description: Mapped[str | None] = mapped_column(sa.Text)
    created_by: Mapped[int] = mapped_column(sa.ForeignKey("users.id"))
    created_at: Mapped[NowTS]

    topics: Mapped[list[Topic]] = relationship(
        back_populates="course", order_by="Topic.position", passive_deletes=True
    )
