"""DTOs for Topic. ORM model: src/course/Topic.py."""

from __future__ import annotations

from pydantic import Field

from src.base.BaseDto import DTO


class TopicBase(DTO):
    title: str = Field(min_length=1, max_length=200)
    position: int = Field(ge=0)


class TopicCreate(TopicBase):
    course_id: int


class TopicUpdate(DTO):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    position: int | None = Field(default=None, ge=0)


class TopicRead(TopicBase):
    id: int
    course_id: int
