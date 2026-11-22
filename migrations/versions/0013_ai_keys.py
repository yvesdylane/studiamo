"""a user's own Claude API key, for converting imported notes

  user_ai_keys  one row per user: the provider ('anthropic'), the key encrypted
                with AI_KEYS_SECRET (or SECRET_KEY), its last 4 characters to
                recognise it, the model to use

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        create table user_ai_keys (
            user_id       integer primary key references users(id) on delete cascade,
            provider      varchar(20) not null default 'anthropic' check (provider in ('anthropic')),
            key_encrypted text not null,
            key_hint      varchar(8) not null,
            model         varchar(40) not null default 'claude-opus-5-5',
            created_at    timestamptz not null default now(),
            last_used_at  timestamptz
        );
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql("drop table if exists user_ai_keys;")
