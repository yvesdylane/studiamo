"""ORM model for table `users`."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.base.Base import Base, IntPK, NowTS, pg_enum

if TYPE_CHECKING:
    from src.player.PlayerStats import PlayerStats
    from src.user.models.UserIdentity import UserIdentity


class UserRole(str, enum.Enum):
    """The only roles stored in the database. Admins are not a role: they are
    decided at login from ADMIN_EMAILS in .env (src/web/security.py)."""

    STUDENT = "student"
    LECTURER = "lecturer"


class User(Base):
    __tablename__ = "users"

    id: Mapped[IntPK]
    name: Mapped[str] = mapped_column(sa.String(100))
    player_name: Mapped[str | None] = mapped_column(sa.String(50), unique=True)
    email: Mapped[str] = mapped_column(sa.String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(sa.Text)  # null if only external auth
    class_name: Mapped[str | None] = mapped_column(sa.String(50))  # e.g. "HND 1"
    terms_accepted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    email_verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))  # null: cannot log in
    verification_sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # how they appear on the ranking: public | anonymous | hidden (migration 0009)
    ranking_visibility: Mapped[str] = mapped_column(sa.String(10), server_default="public")
    # appearance, per user (migration 0010, src/web/appearance.py)
    accent_color: Mapped[str | None] = mapped_column(sa.String(7))
    secondary_color: Mapped[str | None] = mapped_column(sa.String(7))
    theme: Mapped[str] = mapped_column(sa.String(6), server_default="system")
    font: Mapped[str] = mapped_column(sa.String(20), server_default="studiamo")
    text_size: Mapped[str] = mapped_column(sa.String(6), server_default="normal")
    # notifications as pop-ups (migration 0016)
    toast_enabled: Mapped[bool] = mapped_column(server_default=sa.true())
    toast_seconds: Mapped[int] = mapped_column(server_default="10")
    avatar_path: Mapped[str | None] = mapped_column(sa.String(80))   # profile photo in MEDIA_DIR (migration 0017)
    ai_provider: Mapped[str] = mapped_column(sa.String(20), server_default="anthropic")   # AI used by default (migration 0019)
    roles: Mapped[list[UserRole]] = mapped_column(
        pg.ARRAY(pg_enum(UserRole, "user_role")),
        server_default=sa.text("'{student}'"),
    )
    created_at: Mapped[NowTS]
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        server_onupdate=sa.FetchedValue(),  # kept fresh by a DB trigger
    )

    identities: Mapped[list[UserIdentity]] = relationship(
        back_populates="user", passive_deletes=True
    )
    stats: Mapped[PlayerStats | None] = relationship(
        back_populates="user", uselist=False, passive_deletes=True
    )
