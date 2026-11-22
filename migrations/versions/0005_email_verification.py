"""email verification

  users.email_verified_at     when the user proved they own the email (link
                              clicked, or Google said it is verified); null =
                              not yet: such an account cannot log in
  users.verification_sent_at  last verification email, to limit resends

Accounts that already exist and have a linked Google identity are marked
verified (Google checked their email).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        alter table users
            add column if not exists email_verified_at timestamptz,
            add column if not exists verification_sent_at timestamptz;

        update users u set email_verified_at = u.created_at
         where u.email_verified_at is null
           and exists (select 1 from user_identities i where i.user_id = u.id and i.provider = 'google');
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "alter table users drop column if exists verification_sent_at, drop column if exists email_verified_at;"
    )
