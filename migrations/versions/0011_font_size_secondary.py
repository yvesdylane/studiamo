"""more personal appearance: font, text size, secondary colour (per user)

  users.secondary_color  '#rrggbb' that tints the course strip, footer and rank badge (null = default)
  users.font             a key of src/web/appearance.py FONTS (default 'studiamo')
  users.text_size        'normal' | 'large' | 'xlarge'

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        alter table users
            add column secondary_color varchar(7) check (secondary_color ~ '^#[0-9a-f]{6}$'),
            add column font varchar(20) not null default 'studiamo',
            add column text_size varchar(6) not null default 'normal' check (text_size in ('normal', 'large', 'xlarge'));
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql("alter table users drop column text_size, drop column font, drop column secondary_color;")
