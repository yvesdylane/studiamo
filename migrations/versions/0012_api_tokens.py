"""personal access keys for connecting an AI app (MCP) to Studiamo

  api_tokens  one row per key a user created in Settings > AI. Only a SHA-256
              hash of the key is stored; the key is shown once.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        create table api_tokens (
            id            serial primary key,
            user_id       integer not null references users(id) on delete cascade,
            name          varchar(60) not null,
            token_hash    char(64) not null unique,
            prefix        varchar(12) not null,
            created_at    timestamptz not null default now(),
            last_used_at  timestamptz,
            revoked_at    timestamptz
        );
        create index api_tokens_user_idx on api_tokens (user_id);
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql("drop table if exists api_tokens;")
