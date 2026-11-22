"""two smaller text sizes: users.text_size may also be 'xsmall' or 'small'

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        alter table users drop constraint if exists users_text_size_check;
        alter table users add constraint users_text_size_check
            check (text_size in ('xsmall', 'small', 'normal', 'large', 'xlarge'));
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        update users set text_size = 'normal' where text_size in ('xsmall', 'small');
        alter table users drop constraint if exists users_text_size_check;
        alter table users add constraint users_text_size_check check (text_size in ('normal', 'large', 'xlarge'));
        """
    )
