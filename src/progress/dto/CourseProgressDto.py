"""Read-only summary of how far a student is in one course (computed, no table)."""

from __future__ import annotations

from src.base.BaseDto import DTO


class CourseProgressRead(DTO):
    student_id: int
    course_id: int
    notes_completed: int
    notes_total: int              # published notes
    exercises_done: int           # with at least one graded attempt
    exercises_total: int          # published exercises
    exams_done: int
    exams_total: int              # published exams
    exp_earned: int               # exp from this course's notes, exercises and exams
    completion_pct: float         # notes + exercises done / total, 0-100
