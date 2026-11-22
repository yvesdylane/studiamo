"""admin review of lecturer content

Notes, exercises and exams get a review state:

  draft     being written; only editors see it
  pending   submitted by its lecturer, waiting for an admin
  approved  checked by an admin (published = true from then on)
  rejected  sent back with review_comment; the lecturer edits and resubmits

Only admins publish notes, exercises and practice (system) exams, and only
admins set exp. A lecturer may publish their own scheduled exams directly.
A lecturer editing approved content sends it back to pending (and unpublished).
Rows that are already published count as approved.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TABLES = ("topic_notes", "exercises", "exams")


def upgrade() -> None:
    sql = []
    for t in TABLES:
        sql.append(f"""
        alter table {t}
            add column review_status varchar(10) not null default 'draft'
                check (review_status in ('draft', 'pending', 'approved', 'rejected')),
            add column review_comment text,
            add column submitted_at timestamptz,
            add column reviewed_by integer references users(id) on delete set null,
            add column reviewed_at timestamptz;
        update {t} set review_status = 'approved' where published;
        create index {t}_review_idx on {t} (review_status) where review_status = 'pending';
        """)
    op.get_bind().exec_driver_sql("\n".join(sql))


def downgrade() -> None:
    op.get_bind().exec_driver_sql("\n".join(
        f"alter table {t} drop column review_status, drop column review_comment, drop column submitted_at, "
        f"drop column reviewed_by, drop column reviewed_at;" for t in TABLES))
