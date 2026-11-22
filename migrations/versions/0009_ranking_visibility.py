"""users choose how they appear on the ranking

  users.ranking_visibility   'public'     name (or player name) shown
                             'anonymous'  listed as "Anonymous player", no link to the profile
                             'hidden'     not on the ranking at all

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        alter table users add column ranking_visibility varchar(10) not null default 'public'
            check (ranking_visibility in ('public', 'anonymous', 'hidden'));
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql("alter table users drop column ranking_visibility;")
