"""DTOs for User. ORM model: src/user/User.py."""

from __future__ import annotations

from datetime import datetime

from typing import Literal

from pydantic import EmailStr, Field

from src.base.BaseDto import DTO
from src.user.models.User import UserRole

RankingVisibility = Literal["public", "anonymous", "hidden"]


class UserBase(DTO):
    name: str = Field(min_length=1, max_length=100)
    player_name: str | None = Field(default=None, min_length=1, max_length=50)
    email: EmailStr


class UserCreate(UserBase):
    # plain password, hashed by the service; None when signing up through an
    # external provider. New users are always students; roles change through
    # UserRolesUpdate (an admin's action).
    password: str | None = Field(default=None, min_length=8, max_length=128)
    class_name: str | None = Field(default=None, min_length=1, max_length=50)
    accepted_terms: bool = False  # True stamps terms_accepted_at


class UserUpdate(DTO):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    player_name: str | None = Field(default=None, min_length=1, max_length=50)
    class_name: str | None = Field(default=None, min_length=1, max_length=50)
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    ranking_visibility: RankingVisibility | None = None
    accent_color: str | None = Field(default=None, pattern=r"^#[0-9a-f]{6}$")   # send None to go back to the brand colour
    theme: Literal["system", "light", "dark"] | None = None
    secondary_color: str | None = Field(default=None, pattern=r"^#[0-9a-f]{6}$")   # None = the default greys
    font: str | None = Field(default=None, max_length=20)
    text_size: Literal["xsmall", "small", "normal", "large", "xlarge"] | None = None
    toast_enabled: bool | None = None
    toast_seconds: int | None = Field(default=None, ge=3, le=60)


class UserRolesUpdate(DTO):
    roles: list[UserRole] = Field(min_length=1)


class UserRead(UserBase):  # never exposes password_hash
    id: int
    roles: list[UserRole]
    class_name: str | None = None
    terms_accepted_at: datetime | None = None
    email_verified_at: datetime | None = None
    ranking_visibility: RankingVisibility = "public"   # how they appear on the ranking
    accent_color: str | None = None                    # '#rrggbb', replaces the brand purple
    theme: str = "system"                              # system | light | dark
    secondary_color: str | None = None
    font: str = "studiamo"
    text_size: str = "normal"
    toast_enabled: bool = True
    toast_seconds: int = 10
    avatar_path: str | None = None

    @property
    def avatar_url(self) -> str | None:
        return f"/media/{self.avatar_path}" if self.avatar_path else None

    @property
    def initials(self) -> str:
        return "".join(w[0] for w in self.name.split()[:2]).upper() or "?"

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None
    created_at: datetime
    updated_at: datetime


class UserPublic(DTO):  # leaderboards, comments, etc.
    id: int
    name: str
    player_name: str | None = None
