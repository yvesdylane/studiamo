"""profile photo: users.avatar_path, the file name in MEDIA_DIR (null = initials)

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql("alter table users add column avatar_path varchar(80);")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("alter table users drop column avatar_path;")
