"""DTOs for NoteExercise. ORM model: src/exercise/NoteExercise.py."""

from __future__ import annotations

from src.base.BaseDto import DTO


class NoteExerciseCreate(DTO):
    note_id: int
    exercise_id: int


class NoteExerciseRead(NoteExerciseCreate):
    pass
