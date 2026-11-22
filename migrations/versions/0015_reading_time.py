"""reading time, for the Reading axis of the skill graph

  topic_notes.reading_seconds   estimated time to read the note once (computed
                                from its text when it is saved; null = not yet)
  student_note_time             seconds a student really spent on each note
                                (the note page reports active time only)

Reading is at its maximum when a student spent 3x the estimate on a note.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        alter table topic_notes add column reading_seconds integer check (reading_seconds > 0);
        create table student_note_time (
            student_id  integer references users(id) on delete cascade,
            note_id     integer references topic_notes(id) on delete cascade,
            seconds     integer not null default 0 check (seconds >= 0),
            updated_at  timestamptz not null default now(),
            primary key (student_id, note_id)
        );
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql("drop table if exists student_note_time; alter table topic_notes drop column if exists reading_seconds;")
