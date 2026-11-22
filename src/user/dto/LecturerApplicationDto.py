"""DTOs for LecturerApplication. ORM model: src/user/models/LecturerApplication.py."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from src.base.BaseDto import DTO
from src.user.models.LecturerApplication import LecturerApplicationStatus


class LecturerApplicationCreate(DTO):
    institution: str = Field(min_length=1, max_length=200)
    # free text as typed ("Factual Programming, HCI"); courses are matched by an admin
    courses_taught: str = Field(min_length=1, max_length=2000)

    @field_validator("institution", "courses_taught")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class LecturerApplicationRead(DTO):
    id: int
    user_id: int
    institution: str
    courses_taught: str
    status: LecturerApplicationStatus
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
