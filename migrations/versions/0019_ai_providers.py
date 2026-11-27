"""more than one AI provider per user

  user_ai_keys      one row per user AND provider ('anthropic' | 'google' | 'openai');
                    model is longer (provider model ids), models = the ids the key
                    can use (asked from the provider when the key is saved)
  users.ai_provider the AI the user picked to use by default ('anthropic' = Claude)

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-25
"""

from __future__ import annotations

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        alter table user_ai_keys drop constraint if exists user_ai_keys_provider_check;
        alter table user_ai_keys drop constraint user_ai_keys_pkey;
        alter table user_ai_keys
            alter column provider drop default,
            alter column model type varchar(80),
            add column models jsonb not null default '[]'::jsonb,
            add constraint user_ai_keys_provider_check check (provider in ('anthropic', 'google', 'openai')),
            add primary key (user_id, provider);
        alter table users
            add column ai_provider varchar(20) not null default 'anthropic'
                check (ai_provider in ('anthropic', 'google', 'openai'));
        update user_ai_keys set models = '["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]'::jsonb
         where provider = 'anthropic';
        """
    )


def downgrade() -> None:
    raise NotImplementedError("0019 keeps keys of several providers; restore from a backup instead")
