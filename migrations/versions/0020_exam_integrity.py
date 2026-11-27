"""what happened during an exam attempt (src/static/js/exam-guard.js)

  exam_attempts.integrity_events  [{type, at}]: left the window or tab, left full
                                  screen, tried to go back, copy / paste… (max 200)
  exam_attempts.auto_submitted    why the page submitted the exam by itself:
                                  'left' (left the exam again after the warning)
                                  | 'back' (chose to go back / leave the page)

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-26
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        alter table exam_attempts
            add column integrity_events jsonb not null default '[]'::jsonb,
            add column auto_submitted varchar(10) check (auto_submitted in ('left', 'back'));
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "alter table exam_attempts drop column if exists integrity_events, drop column if exists auto_submitted;"
    )
