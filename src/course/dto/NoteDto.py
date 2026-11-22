"""DTOs for Note. ORM model: src/course/Note.py."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from src.base.BaseDto import DTO


class NoteBase(DTO):
    title: str = Field(min_length=1, max_length=200)
    md_path: str = Field(min_length=1)
    position: int = Field(ge=0)
    max_exp: int = Field(default=0, ge=0)
    published: bool = False


class NoteCreate(NoteBase):
    topic_id: int


class NoteUpdate(DTO):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    md_path: str | None = Field(default=None, min_length=1)
    position: int | None = Field(default=None, ge=0)
    max_exp: int | None = Field(default=None, ge=0)
    published: bool | None = None


class NoteRead(NoteBase):
    id: int
    topic_id: int
    created_at: datetime
    reading_seconds: int | None = None   # estimated time to read it once
    review_status: str = "draft"        # draft | pending | approved | rejected
    review_comment: str | None = None
    submitted_at: datetime | None = None
