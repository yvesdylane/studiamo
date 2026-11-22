"""DTOs for StudentCompletedNote. ORM model: src/progress/StudentCompletedNote.py."""

from __future__ import annotations

from datetime import datetime

from src.base.BaseDto import DTO


class StudentCompletedNoteCreate(DTO):  # student_id from the session, exp by the server
    note_id: int


class StudentCompletedNoteRead(DTO):
    student_id: int
    note_id: int
    exp_awarded: int
    xexp_awarded: float = 0.0
    completed_at: datetime
