"""ORM model for table `note_exercises`."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.base.Base import Base


class NoteExercise(Base):
    __tablename__ = "note_exercises"

    note_id: Mapped[int] = mapped_column(
        sa.ForeignKey("topic_notes.id", ondelete="CASCADE"), primary_key=True
    )
    exercise_id: Mapped[int] = mapped_column(
        sa.ForeignKey("exercises.id", ondelete="CASCADE"), primary_key=True
    )
