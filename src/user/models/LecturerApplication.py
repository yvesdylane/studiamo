"""ORM model for table `lecturer_applications` (migration 0003)."""

from __future__ import annotations

import enum
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base, IntPK, NowTS, pg_enum


class LecturerApplicationStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class LecturerApplication(Base):
    __tablename__ = "lecturer_applications"
    __table_args__ = (sa.Index("lecturer_applications_status_idx", "status", "created_at"),)

    id: Mapped[IntPK]
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    institution: Mapped[str] = mapped_column(sa.String(200))
    courses_taught: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[LecturerApplicationStatus] = mapped_column(
        pg_enum(LecturerApplicationStatus, "lecturer_application_status"),
        server_default=LecturerApplicationStatus.PENDING.value,
    )
    reviewed_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[NowTS]
