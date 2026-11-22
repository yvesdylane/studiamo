"""DTOs for UserIdentity. ORM model: src/user/UserIdentity.py."""

from __future__ import annotations

from pydantic import Field

from src.base.BaseDto import DTO


class UserIdentityBase(DTO):
    provider: str = Field(min_length=1, max_length=30)
    provider_uid: str = Field(min_length=1)


class UserIdentityCreate(UserIdentityBase):
    pass


class UserIdentityRead(UserIdentityBase):
    id: int
    user_id: int
