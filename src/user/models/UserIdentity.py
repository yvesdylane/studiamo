"""ORM model for table `user_identities`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.base.Base import Base, IntPK

if TYPE_CHECKING:
    from src.user.models.User import User


class UserIdentity(Base):
    __tablename__ = "user_identities"
    __table_args__ = (
        sa.UniqueConstraint("provider", "provider_uid"),
        sa.Index("user_identities_user_idx", "user_id"),
    )

    id: Mapped[IntPK]
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(sa.String(30))  # google, github, ...
    provider_uid: Mapped[str] = mapped_column(sa.Text)

    user: Mapped[User] = relationship(back_populates="identities")
