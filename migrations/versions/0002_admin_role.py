"""add the 'admin' value to the user_role enum (UserRole.ADMIN in src/user/models/User.py)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter type user_role add value if not exists 'admin'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum; demote admins so nothing uses it.
    op.execute("update users set roles = array_remove(roles, 'admin'::user_role)")
