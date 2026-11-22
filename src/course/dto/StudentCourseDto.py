"""Read-only DTO for the `student_courses` view (a student is in a course as
soon as they touch anything in it)."""

from __future__ import annotations

from src.base.BaseDto import DTO


class StudentCourseRead(DTO):  # row of the student_courses view
    student_id: int
    course_id: int
