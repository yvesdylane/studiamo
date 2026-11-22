"""personal colour and light / dark theme (font, text size, secondary colour: 0011)

  users.accent_color  '#rrggbb' that replaces the brand purple for this user (null = brand)
  users.theme         'system' (follow the device) | 'light' | 'dark'

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        alter table users
            add column accent_color varchar(7) check (accent_color ~ '^#[0-9a-f]{6}$'),
            add column theme varchar(6) not null default 'system' check (theme in ('system', 'light', 'dark'));
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql("alter table users drop column theme, drop column accent_color;")
