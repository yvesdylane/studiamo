"""ORM model for table `player_stats`."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.base.Base import Base, pg_enum

if TYPE_CHECKING:
    from src.user.models.User import User


class PlayerRank(str, enum.Enum):
    F = "F"
    E = "E"
    D = "D"
    C = "C"
    B = "B"
    A = "A"
    S = "S"


class PlayerStats(Base):
    __tablename__ = "player_stats"
    __table_args__ = (
        sa.CheckConstraint("total_exp >= 0", name="player_stats_total_exp_check"),
        sa.Index("player_stats_total_exp_idx", sa.text("total_exp DESC")),
        sa.Index("player_stats_total_xexp_idx", sa.text("total_xexp DESC")),
    )

    user_id: Mapped[int] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    total_exp: Mapped[int] = mapped_column(sa.BigInteger, server_default="0")
    rank: Mapped[PlayerRank] = mapped_column(
        pg_enum(PlayerRank, "player_rank"), server_default="F"
    )
    total_xexp: Mapped[Decimal] = mapped_column(sa.Numeric(14, 2), server_default="0")   # drives the rank
    rank_level: Mapped[int] = mapped_column(server_default="0")   # 0..34 = F1..S5; rank is its letter
    rank_updated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="stats")
