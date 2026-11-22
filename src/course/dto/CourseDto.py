"""DTOs for Course. ORM model: src/course/Course.py."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from src.base.BaseDto import DTO


class CourseBase(DTO):
    code: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


class CourseCreate(CourseBase):  # created_by comes from the session
    pass


class CourseUpdate(DTO):
    code: str | None = Field(default=None, min_length=1, max_length=20)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class CourseRead(CourseBase):
    id: int
    created_by: int
    created_at: datetime


class CourseCatalogItem(CourseBase):
    """A course on the home page, with how much published material it has."""

    id: int
    published_notes: int = 0
    published_exercises: int = 0
    published_exams: int = 0

    @property
    def available(self) -> bool:
        return (self.published_notes + self.published_exercises + self.published_exams) > 0
